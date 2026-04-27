import importlib
import struct
import sys

TARGET_PYTHON_VERSION = (3, 8, 10)
TARGET_RUNTIME_VERSIONS = {
    "torch": "2.0.1",
    "torchvision": "0.15.2",
    "torchaudio": "2.0.2",
}


def load_module(module_name):
    """安全导入模块，返回模块对象和异常。"""
    try:
        return importlib.import_module(module_name), None
    except Exception as exc:
        return None, exc


def print_separator():
    print("-" * 50)


print_separator()
print(f"Python 版本: {sys.version}")
print(f"Python 路径: {sys.executable}")
print(f"Python 位数: {struct.calcsize('P') * 8} bit")
if hasattr(sys, "getwindowsversion"):
    win_ver = sys.getwindowsversion()
    print(f"Windows 版本: major={win_ver.major}, minor={win_ver.minor}, build={win_ver.build}")
else:
    print("Windows 版本: 当前不是 Windows 主机")
print_separator()

runtime_failed = False

# 1. 检查打包主机硬约束
if sys.platform != "win32":
    print("宿主系统兼容性: ❌ 当前不是 Windows，禁止用于交付打包")
    runtime_failed = True
elif not hasattr(sys, "getwindowsversion") or sys.getwindowsversion().major < 10:
    print("宿主系统兼容性: ❌ 当前不是 Windows 10/11")
    runtime_failed = True
else:
    print("宿主系统兼容性: ✅ 当前为 Windows 10/11")

if sys.version_info[:3] != TARGET_PYTHON_VERSION:
    print("Python 兼容性: ❌ 当前不是 Python 3.8.10")
    runtime_failed = True
else:
    print("Python 兼容性: ✅ 当前为 Python 3.8.10")

if struct.calcsize("P") * 8 != 64:
    print("Python 位数兼容性: ❌ 当前不是 64 位解释器")
    runtime_failed = True
else:
    print("Python 位数兼容性: ✅ 当前为 64 位解释器")

# 2. 检查 PyTorch 三件套必须为精确固定版本
runtime_modules = {}
for module_name, expected_version in TARGET_RUNTIME_VERSIONS.items():
    module, error = load_module(module_name)
    if error is not None:
        print(f"{module_name} 导入: ❌ 失败 ({error})")
        runtime_failed = True
        continue

    runtime_modules[module_name] = module
    version_text = getattr(module, "__version__", "unknown")
    print(f"{module_name} 版本: {version_text}")

    if version_text.startswith(expected_version):
        print(f"{module_name} 兼容性: ✅ 已锁定为 {expected_version} (实际加载: {version_text})")
    else:
        print(f"{module_name} 兼容性: ❌ 当前版本不符合要求，必须以 {expected_version} 开头")
        runtime_failed = True

if runtime_failed:
    print_separator()
    print("建议修复命令:")
    print("python -m pip install --upgrade --force-reinstall --no-cache-dir torch==2.0.1 torchvision==0.15.2 torchaudio==2.0.2 --index-url https://download.pytorch.org/whl/cpu")
    sys.exit(1)

torch = runtime_modules["torch"]
cuda_ok = torch.cuda.is_available()
print(f"CUDA 是否可用: {'✅ 是 (YES)' if cuda_ok else '❌ 否 (NO)'}")
print("提示: 交付包采用 CPU 兼容优先策略，CUDA 状态仅做参考，不参与打包基线。")

print_separator()

# 3. 检查其他核心依赖
cv2_module, cv2_error = load_module("cv2")
if cv2_error is not None:
    print(f"OpenCV: ❌ 未安装 ({cv2_error})")
    sys.exit(1)
print(f"OpenCV 版本: {cv2_module.__version__}")

mp_module, mp_error = load_module("mediapipe")
if mp_error is not None:
    print(f"MediaPipe: ⚠️ 未安装或不可用 ({mp_error})")
else:
    print(f"MediaPipe 版本: {mp_module.__version__}")

ultralytics_module, ultralytics_error = load_module("ultralytics")
if ultralytics_error is not None:
    print(f"YOLO (Ultralytics) 库: ❌ 未安装 ({ultralytics_error})")
    sys.exit(1)

print(f"YOLO (Ultralytics) 版本: {getattr(ultralytics_module, '__version__', 'unknown')}")
print("YOLO (Ultralytics) 库: ✅ 已就绪")

print_separator()
print("检查完成：当前环境可用于兼容交付打包。")
