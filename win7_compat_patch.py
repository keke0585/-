import sys
import os

def apply():
    """
    Win7 兼容性环境挂载点
    由于在新版 PyTorch / Python 下频繁触发 WinError 127 或 193，
    通过此系统钩子强制控制工作路径和弹窗。
    """
    if sys.platform != "win32":
        return
        
    try:
        import platform
        # 使用松散条件判断：只要不是 Win10/Win11 (一般 major/release == 10)
        # 我们就假定它需要兼容性补丁控制 (例如 Win7/Win8)
        if platform.release() == "7" or platform.version().startswith("6."):
            
            # 1. 确保将执行文件所在目录注册为顶级 DLL 搜寻目标，防止去加载 System32 中旧版本冲突 DLL
            app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(app_dir)
                except Exception:
                    pass
            
            # 手工兜底 PATH 环境变量
            os.environ["PATH"] = app_dir + os.pathsep + os.environ.get("PATH", "")
            
            # 2. 调用内核功能抑制找不到 DLL 的弹窗报错 (WinError 127/WinError 193 等严重错误)
            # 这可以让程序在尝试优雅降级（如退回 CPU）时避免阻断
            import ctypes
            # SEM_FAILCRITICALERRORS (0x0001) | SEM_NOGPFAULTERRORBOX (0x0002) | SEM_NOOPENFILEERRORBOX (0x8000)
            ctypes.windll.kernel32.SetErrorMode(0x8003)
            
    except Exception as e:
        # 这个级别的失败只能记录，不能崩溃
        print(f"Warning: Failed to apply Win7 compatibility patch: {e}")

if __name__ == "__main__":
    apply()
    print("Win7 patch test successfully.")
