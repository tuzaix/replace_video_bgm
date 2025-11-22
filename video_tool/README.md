# 视频工具依赖与安装指南

本目录提供视频/音频相关的命令行与工具，包括：卡点采集（Demucs+Librosa）、卡点混剪（MoviePy/FFmpeg）、BGM替换等。以下为依赖与安装说明。

## 依赖列表

- moviepy==1.0.3（视频/音频处理）
- demucs>=4.0.0（音频分离）
- librosa>=0.10.0（音频分析）
- soundfile>=0.12.1（音频读写）
- numpy>=1.26.0（数值计算）
- torch（Demucs 依赖，需按环境选择版本）

完整列表见 `requirements.txt`。

## PyTorch 版本选择

- CPU 环境（默认）：使用 `torch==2.3.1`
- CUDA 11.8：需添加额外源并安装 `torch==2.3.1`
- CUDA 12.1：需添加额外源并安装 `torch==2.3.1`

示例（PowerShell）：

```powershell
# 进入虚拟环境后在项目根目录执行
pip install -r video_tool/requirements.txt

# 若使用 CUDA 11.8（任选其一）
pip install --extra-index-url https://download.pytorch.org/whl/cu118 torch==2.3.1

# 若使用 CUDA 12.1（任选其一）
pip install --extra-index-url https://download.pytorch.org/whl/cu121 torch==2.3.1
```

注意：若当前 Python 版本为 3.13，可能存在官方未发布的 PyTorch 轮子，建议使用 Python 3.12 安装以获得更稳定支持。

## FFmpeg 环境

- 工具默认优先使用捆绑 FFmpeg；开发环境下可通过 PATH 的系统 FFmpeg 兜底。
- 相关逻辑见 `utils/bootstrap_ffmpeg.py` 与 `gui.precheck.ffmpeg_paths`。

## 常见问题

- 安装失败：优先确认 Python 版本与 torch 轮子兼容；必要时降级到 Python 3.12。
- GPU 编码：在支持 NVENC 时会优先使用 GPU，否则使用 CPU 编码。