目前最**先进（State-of-the-Art, SOTA）**且**高效**的方案是结合 **OpenAI 的 Whisper 模型** 与 **CTranslate2 加速技术**。

具体来说，推荐使用 **`faster-whisper`** 库。相比官方的 `openai-whisper`，它的推理速度通常快 4 倍以上，且显存占用更低，同时保持了相同的识别精度。

以下是一个完整的、工业级的 Python 解决方案。

---

### 方案核心优势
1.  **精度极高**：基于 OpenAI 的 Whisper V3 模型，支持多语言（含中英文混合）识别。
2.  **速度极快**：使用 CTranslate2 引擎，支持 int8 量化推理。
3.  **易于集成**：Python 代码简洁，直接生成 SRT 字幕文件。

---

### 第一步：环境准备

你需要安装 Python 环境，并确保系统中安装了 **FFmpeg**（用于处理音频流）。

**1. 安装 FFmpeg:**
*   **Windows:** 下载 exe 并配置环境变量，或使用 `choco install ffmpeg`。
*   **Mac:** `brew install ffmpeg`
*   **Linux:** `sudo apt install ffmpeg`

**2. 安装 Python 库:**
请在终端执行以下命令：
```bash
pip install faster-whisper
```
*(如果是 NVIDIA 显卡，请确保安装了 CUDA 和 cuDNN 以启用 GPU 加速，速度会有质的飞跃)*

---

### 第二步：Python 代码实现

这段代码封装了一个完整的流程：加载模型 -> 识别音频 -> 格式化时间轴 -> 导出 SRT 字幕文件。

```python
import math
from faster_whisper import WhisperModel

def format_timestamp(seconds: float):
    """
    将秒数转换为 SRT 字幕格式的时间戳 (HH:MM:SS,mmm)
    """
    assert seconds >= 0, "non-negative timestamp expected"
    milliseconds = round(seconds * 1000.0)

    hours = milliseconds // 3600000
    milliseconds -= hours * 3600000

    minutes = milliseconds // 60000
    milliseconds -= minutes * 60000

    seconds = milliseconds // 1000
    milliseconds -= seconds * 1000

    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def generate_subtitles(video_path, output_srt_path, model_size="large-v3", device="auto"):
    """
    生成字幕的主函数
    :param video_path: 视频文件路径
    :param output_srt_path: 输出 srt 文件路径
    :param model_size: 模型大小 (tiny, base, small, medium, large-v3)
    :param device: 运行设备 ('cuda' for GPU, 'cpu' for CPU, 'auto' 自动选择)
    """
    print(f"正在加载模型: {model_size} ...")
    
    # compute_type="float16" 在 GPU 上最快，"int8" 在 CPU 上较快
    compute_type = "float16" if device == "cuda" else "int8"
    
    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as e:
        print(f"GPU加载失败或不支持，切换回CPU: {e}")
        model = WhisperModel(model_size, device="cpu", compute_type="int8")

    print(f"开始转录视频: {video_path}")
    # beam_size=5 增加准确度，vad_filter=True 自动过滤静音片段
    segments, info = model.transcribe(video_path, beam_size=5, vad_filter=True)

    print(f"检测到语言: {info.language} (置信度: {info.language_probability:.2f})")

    # 写入 SRT 文件
    with open(output_srt_path, "w", encoding="utf-8") as f:
        count = 1
        for segment in segments:
            # 格式化时间
            start_time = format_timestamp(segment.start)
            end_time = format_timestamp(segment.end)
            text = segment.text.strip()

            # 写入 SRT 格式块
            f.write(f"{count}\n")
            f.write(f"{start_time} --> {end_time}\n")
            f.write(f"{text}\n\n")
            
            # 打印进度到控制台
            print(f"[{start_time} -> {end_time}] {text}")
            count += 1

    print(f"字幕生成完毕! 已保存至: {output_srt_path}")

if __name__ == "__main__":
    # 配置参数
    VIDEO_FILE = "input_video.mp4"  # 替换你的视频文件路径
    SRT_FILE = "output_subtitle.srt"
    
    # 模型选择建议：
    # 追求速度用 "small" 或 "medium"
    # 追求质量用 "large-v3" (需要约 4GB-8GB 显存)
    generate_subtitles(VIDEO_FILE, SRT_FILE, model_size="large-v3", device="auto")
```

---

### 方案详细解析与优化建议

#### 1. 模型选择策略 (`model_size`)
Whisper 提供了不同大小的模型，你需要根据硬件和需求权衡：
*   **`tiny` / `base`**: 极快，但在嘈杂环境或中文识别上准确率较低。
*   **`small` / `medium`**: **性价比最高**，适合大多数日常视频，速度快且准确率尚可。
*   **`large-v3`**: **SOTA 级别**，准确率极高，能听懂复杂的术语和模糊的发音，但速度较慢，且需要较大显存（建议 8GB 以上显存）。

#### 2. 硬件加速 (`device` & `compute_type`)
*   **GPU (CUDA):** 代码中设置了 `float16` 计算，这是在 NVIDIA 显卡上最高效的模式。
*   **CPU:** 代码会自动回退到 `int8` 量化模式，虽然比 GPU 慢，但比官方原始的 float32 模式快得多，普通笔记本也能跑得动。

#### 3. 语音活动检测 (VAD)
代码中开启了 `vad_filter=True`。这非常重要，它可以自动忽略视频中的背景音乐或静音片段，防止模型在没人说话时产生幻觉（生成奇怪的重复文本）。

#### 4. 进阶功能：翻译
如果你需要将中文视频直接生成**英文字幕**，只需修改 `transcribe` 函数的参数：
```python
segments, info = model.transcribe(video_path, task="translate", beam_size=5)
```
设置 `task="translate"` 后，无论原声是什么语言，Whisper 都会直接输出英文翻译。

### 总结
这个方案利用 `faster-whisper` 实现了工业级的字幕生成。它既能利用 GPU 进行极速推理，也能在 CPU 上通过量化技术流畅运行，是目前 Python 处理视频字幕的最佳实践。