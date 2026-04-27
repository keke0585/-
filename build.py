import os
import sys
import subprocess
import shutil
import glob

APP_NAME = "视频研判平台"
ENTRY_POINT = "eyes_modern.py"
DATA_DIR = "web"

def print_step(msg):
    print(f"\n[{msg}]")
    print("-" * 50)

def run_cmd(cmd, suppress_err=False):
    print(f"  >>> {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0 and not suppress_err:
        print("  [ERROR] 上述命令执行失败，已终止。")
        sys.exit(1)

def ensure_env():
    print_step("阶段 1: 环境与架构兼容性检查")
    if sys.platform != "win32":
        print("  [警告] 当前操作系统并不是 Windows，打包可能失败！")
    
    # 彻底杜绝 WinError 193 产生的原因：位宽校验
    try:
        import struct
        import platform
        bits = struct.calcsize("P") * 8
        os_info = f"Windows {platform.release()} (Version: {platform.version()})"
        
        if bits != 64:
            print(f"  [错误] 当前 Python 是 {bits} 位！由于 PyTorch/CUDA 必须 64 位环境，请更换为 64 位 Python 3.8.10。")
            sys.exit(1)
            
        print(f"  ✅ 架构验证通过 (64-bit)")
        print(f"  ✅ 操作系统识别: {os_info}")
    except Exception as e:
        print(f"  [提示] 环境检查略过: {e}")

def install_deps():
    print_step("阶段 2: 清理污染依赖 & 纯净安装 (针对 WinError 193 / 离线断网保障)")
    
    # 毫不留情地卸载系统内所有旧版 PyTorch，防止串台
    run_cmd([sys.executable, "-m", "pip", "uninstall", "-y", "torch", "torchvision", "torchaudio"], suppress_err=True)
    run_cmd([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])

    print("  正在扫描本地离线包并构建安装指令...")
    # 强制本地文件注入逻辑：如果有 whl，直接传文件名给 pip，不给它联网的机会
    targets = {
        "torch": ("torch*2.0.1*cu117*.whl", "torch==2.0.1+cu117"),
        "torchvision": ("torchvision*0.15.2*cu117*.whl", "torchvision==0.15.2+cu117"),
        "torchaudio": ("torchaudio*2.0.2*cu117*.whl", "torchaudio==2.0.2+cu117")
    }

    install_items = []
    for pkg_name, (pattern, fallback) in targets.items():
        found = glob.glob(pattern)
        if found:
            print(f"  ✅ 发现 {pkg_name} 本地包: {found[0]}")
            install_items.append(found[0])
        else:
            print(f"  🌐 [提示] 目录下未发现 {pkg_name} 离线包，将联网获取...")
            install_items.append(fallback)

    run_cmd([
        sys.executable, "-m", "pip", "install", 
        "--no-cache-dir",
        "--index-url", "https://download.pytorch.org/whl/cu117",
    ] + install_items)

    print("\n  安装通用依赖...")
    reqs = []
    if os.path.exists("requirements.txt"):
        with open("requirements.txt", "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip().startswith("#") and "torch" not in line and "ultralytics" not in line:
                    reqs.append(line.strip())
    
    # 增加确保核心识别库的存在
    reqs.append("ultralytics")
    
    if reqs:
        with open("requirements_run.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(reqs))
        run_cmd([sys.executable, "-m", "pip", "install", "-r", "requirements_run.txt"])
        os.remove("requirements_run.txt")

def do_pyinstaller():
    print_step("阶段 3: 调用 Pyinstaller 构建断网免安装包")
    if os.path.exists("build"): shutil.rmtree("build")
    if os.path.exists("dist"): shutil.rmtree("dist")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name", APP_NAME,
        "--add-data", f"{DATA_DIR};{DATA_DIR}",
        "--exclude-module", "tensorboard",
        "--hidden-import", "ultralytics",
        "--hidden-import", "cv2",
        "--hidden-import", "torch",
        "--hidden-import", "torch.utils.data",
        "--collect-data", "cv2",
        # 抓取依赖库。CUDA 和 asmjit 必须被包入 dist 下
        "--collect-binaries", "torch",  
        "--collect-data", "torch",
        "--copy-metadata", "torch",
        "--copy-metadata", "torchvision",
        "--copy-metadata", "torchaudio",
        "--copy-metadata", "ultralytics",
        "--collect-submodules", "ultralytics",
    ]
    if os.path.exists("app.ico"):
        cmd.extend(["--icon", "app.ico"])
    cmd.append(ENTRY_POINT)
    
    run_cmd(cmd)

def collect_extra_dlls_and_models():
    print_step("阶段 4: 追加外围依赖 DLL 与 YOLO 识别模型 (及 Win7 彻底去毒)")
    out_dir = os.path.join("dist", APP_NAME)
    
    import site
    site_packages = site.getsitepackages() if hasattr(site, 'getsitepackages') else []
    
    search_paths = [os.path.dirname(sys.executable)]
    for sp in site_packages:
        search_paths.append(os.path.join(sp, "cv2"))
        search_paths.append(os.path.join(sp, "torch", "lib"))
        
    vc_dlls = ["msvcp140.dll", "vcruntime140_1.dll"]
    good_dlls = {}
    for dll in vc_dlls:
        for sp in search_paths:
            candidate = os.path.join(sp, dll)
            if os.path.exists(candidate):
                good_dlls[dll.lower()] = candidate
                break

    import stat
    print("  开始深层清理与覆盖 Win10/11 专属污染 DLL...")
    
    # 深度遍历所有打包生成的文件
    for root, dirs, files in os.walk(out_dir):
        for file in files:
            file_lower = file.lower()
            target_path = os.path.join(root, file)
            
            # 删除 pyinstaller 自动收集的 Windows 10 API stub及会导致冲突的 vcruntime140.dll
            if file_lower.startswith("api-ms-win-") or file_lower == "vcruntime140.dll":
                try:
                    os.chmod(target_path, stat.S_IWRITE)
                    os.remove(target_path)
                    print(f"  [清理] 删除冲突/不支持的 DLL: {file}")
                except Exception: 
                    pass
                continue
                
            # 若发现了被打包进来的 VC 运行库，立刻强制覆盖
            if file_lower in good_dlls:
                source_path = good_dlls[file_lower]
                try:
                    os.chmod(target_path, stat.S_IWRITE)
                    shutil.copy2(source_path, target_path)
                    print(f"  [修正] 覆盖高兼容防 193 报错运行库 -> {file_lower}")
                except Exception as e:
                    pass
                    
    # 为了保险起见，根目录也显式放一份高兼容的 DLL 兜底 Win7 Loader 加载器
    for dll_name, dll_path in good_dlls.items():
        try:
            shutil.copy2(dll_path, out_dir)
        except:
            pass

    print("\n  复制 AI 模型文件...")
    pt_files = glob.glob("*.pt")
    for pt in pt_files:
        shutil.copy2(pt, out_dir)
        print(f"  -> {pt}")

def main():
    print("=====================================================================")
    print(f"  {APP_NAME} | 纯净版 Python 组合挂载引擎 | GTX 1050Ti")
    print("=====================================================================")
    ensure_env()
    install_deps()
    do_pyinstaller()
    collect_extra_dlls_and_models()
    print_step("阶段 5: 打包全流程完美通过")
    print("\n📦 全部完毕。")
    print("请直接将工程中的 [ dist/视频研判平台 ] 这个文件夹，通过 U盘 拷贝给客户断网的机器使用即可。")

if __name__ == "__main__":
    main()
