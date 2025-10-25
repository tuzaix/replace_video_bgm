# 视频BGM分离和替换工具

这是一个基于Python的视频背景音乐(BGM)分离和替换工具，使用深度学习模型demucs来分离音频，并支持GPU/CPU自适应处理和多线程并发操作。

## 功能特性

- 🎵 **智能音频分离**: 使用demucs深度学习模型分离视频中的BGM
- 🔄 **BGM随机替换**: 从指定目录随机选择新的BGM进行替换
- 🔁 **自动循环**: 当BGM长度小于视频长度时自动循环播放
- ⚡ **GPU加速**: 自动检测并优先使用GPU，CPU作为备选
- 🚀 **多线程处理**: 支持并发处理多个视频文件
- 📁 **批量处理**: 自动遍历目录下的所有视频文件
- 🎬 **视频优化**: 输出24fps，在保证质量的前提下压缩文件大小

## 系统要求

- Python 3.8+
- Windows/macOS/Linux
- 推荐: NVIDIA GPU (支持CUDA)
- 内存: 建议8GB以上

## 安装

1. 克隆或下载项目到本地
2. 安装依赖:

```bash
pip install -r requirements.txt
```

### GPU支持 (可选)

如果您有NVIDIA GPU，可以安装CUDA版本的PyTorch以获得更好的性能：

```bash
# 对于CUDA 11.6
pip install torch torchaudio --extra-index-url https://download.pytorch.org/whl/cu116
```

## 使用方法

### 基本用法

```bash
python video_bgm_replacer.py <视频目录> <BGM目录>
```

### 高级用法

```bash
python video_bgm_replacer.py <视频目录> <BGM目录> --workers 8
```

### 参数说明

- `视频目录`: 包含待处理视频文件的目录
- `BGM目录`: 包含新BGM音频文件的目录
- `--workers`: 并发线程数 (默认: 4)

### 示例

```bash
# 处理videos目录下的视频，使用bgm目录下的音频作为新BGM
python video_bgm_replacer.py ./videos ./bgm

# 使用8个线程并发处理
python video_bgm_replacer.py ./videos ./bgm --workers 8
```

## 支持的文件格式

### 视频格式
- MP4, AVI, MOV, MKV, WMV, FLV, WebM

### 音频格式
- MP3, WAV, FLAC, AAC, OGG, M4A

## 输出结构

运行后会在视频目录下创建以下目录：

```
视频目录/
├── tmp/                    # 临时文件目录
├── mixed_bgm_video/        # 输出视频目录
├── 原视频文件...
└── video_bgm_replacer.log  # 日志文件
```

## 工作流程

1. **目录扫描**: 扫描视频目录和BGM目录，获取所有支持的文件
2. **音频分离**: 使用demucs模型分离视频中的人声、鼓声、贝斯和其他音频
3. **BGM选择**: 为每个视频随机选择一个新的BGM
4. **音频处理**: 根据视频长度调整BGM长度（循环或截取）
5. **视频合成**: 将处理后的音频与原视频合成，输出新视频

## 性能优化

### GPU加速
- 自动检测CUDA支持
- GPU优先，CPU备选
- 针对不同设备优化编码参数

### 多线程处理
- 支持自定义并发线程数
- 合理分配系统资源
- 实时进度监控

### 内存管理
- 及时清理临时文件
- 优化内存使用
- 防止内存泄漏

## 注意事项

1. **首次运行**: 第一次运行时会下载demucs模型，需要网络连接
2. **磁盘空间**: 确保有足够的磁盘空间存储临时文件和输出文件
3. **处理时间**: 处理时间取决于视频长度、数量和硬件性能
4. **音频质量**: 分离效果可能因原视频音频质量而异

## 故障排除

### 常见问题

1. **CUDA错误**: 如果遇到CUDA相关错误，程序会自动切换到CPU模式
2. **内存不足**: 减少并发线程数或处理较短的视频
3. **模型下载失败**: 检查网络连接，或手动下载模型文件

### 日志文件

程序运行时会生成详细的日志文件 `video_bgm_replacer.log`，包含：
- 处理进度
- 错误信息
- 性能统计

## 技术架构

- **深度学习**: demucs (Facebook Research)
- **视频处理**: MoviePy
- **音频处理**: torchaudio, librosa
- **并发处理**: ThreadPoolExecutor
- **设备管理**: PyTorch CUDA

## 许可证

本项目仅供学习和研究使用。请确保您有权处理相关的视频和音频文件。

## 更新日志

### v1.0.0
- 初始版本发布
- 支持基本的BGM分离和替换功能
- GPU/CPU自适应处理
- 多线程并发支持