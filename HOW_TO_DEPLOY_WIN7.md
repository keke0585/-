# Windows 10/11 打包与 Win7 交付说明

目标故障是 Win7 上启动后报：

- `PyTorch 运行库加载失败`
- `WinError 127`
- `Error loading ... torch\lib\asmjit.dll or one of its dependencies`

这类问题本质上不是业务代码错误，而是**打包机环境失控**导致的原生运行库不兼容。当前项目已经收敛为一条唯一可交付路径：

- 打包主机必须是 `Windows 10/11`
- 打包解释器必须是 `Python 3.8.10 x64`
- 打包时强制重装 `torch==2.0.1 / torchvision==0.15.2 / torchaudio==2.0.2`
- 交付包按 `CPU 兼容优先` 构建，不打 CUDA 依赖

## 1. 准备打包主机

在 Windows 10/11 上创建全新环境：

```cmd
conda create -n win_build_py38 python=3.8.10 -y
conda activate win_build_py38
```

然后把整个项目目录拷到这台机器。

## 2. 一键构建

直接运行：

```cmd
build_win_py38.bat
```

脚本会自动完成以下动作：

1. 校验当前是否为 `Windows 10/11 + Python 3.8.10 x64`
2. 升级 `pip / setuptools / wheel`
3. 强制卸载并重装固定 CPU 版 `torch` 三件套
4. 安装 `requirements.txt` 中的其他依赖
5. 执行 `check_env.py` 做硬校验
6. 调用 `package_tool.py` 生成交付包

## 3. 打包产物

成功后交付目录为：

```text
dist\视频研判平台\
```

该目录中会额外生成两份关键文件：

- `构建环境指纹.json`
- `运行前必读.txt`

其中 `构建环境指纹.json` 记录了本次打包机的 Windows build、Python 路径、Python 版本、Torch 三件套版本。以后只要客户再报 `asmjit.dll / WinError 127`，先看这份文件，能立刻判断交付包是不是从错误环境打出来的。

## 4. 本次修复点

当前版本已经补上以下缺口：

- 打包脚本只允许在 `Windows 10/11 + Python 3.8.10 x64` 下执行
- `torch` 三件套从“只检查”改为“构建前自动强制重装”
- PyInstaller 改为由 `当前解释器` 执行，避免吃到系统里别的 Python 环境
- 交付包自动落地 `构建环境指纹.json`
- 交付包尽量补拷 `msvcp140.dll / vcruntime140.dll` 等 VC 运行库
- 程序运行时会主动把 `torch/lib` 加入 DLL 搜索路径，降低 `asmjit.dll` 依赖链加载失败概率

## 5. 交付要求

不要只拿 `exe` 单文件给客户，必须交付整个：

```text
dist\视频研判平台\
```

同时把所需 `.pt` 模型文件保留在该目录根部，与 `视频研判平台.exe` 同级。

## 6. 若仍出现 WinError 127

先按顺序核对：

1. `构建环境指纹.json` 中的 Python 是否精确为 `3.8.10`
2. `构建环境指纹.json` 中的 `torch/torchvision/torchaudio` 是否精确为 `2.0.1 / 0.15.2 / 2.0.2`
3. 交付目录中是否存在 `msvcp140.dll`、`vcruntime140.dll`
4. 是否把整个 `dist\视频研判平台\` 完整拷贝到了 Win7，而不是只拷 `exe`

只要这 4 项成立，Win7 上再出现同类 `asmjit.dll` 报错的概率会大幅下降；若仍复现，优先怀疑目标机系统补丁或 VC 运行库自身损坏。
