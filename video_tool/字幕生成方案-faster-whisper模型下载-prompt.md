`faster-whisper` 的模型默认是托管在 Hugging Face 上的。要实现**完全离线模式**，核心思路是：**“先在有网的机器上把模型文件下载到本地文件夹，然后在代码中指定这个文件夹的路径”**。

鉴于你的 **RTX 4060 Ti** 配置，我们直接以最强的 `large-v3` 模型为例。

以下是分步操作方案：

---

### 第一步：下载模型文件到本地

你需要将模型文件从 Hugging Face 的 `Systran` 仓库（faster-whisper 官方转换好的模型库）下载下来。

**推荐方式：使用 Python 脚本自动下载（最稳妥）**

请在一个**有网络**的环境下，创建一个新的 Python 文件（例如 `download_model.py`），运行以下代码。这会把模型下载到当前目录下的 `models` 文件夹中。

```python
# 首先安装 huggingface_hub 库（如果没装的话）
# pip install huggingface_hub

from huggingface_hub import snapshot_download
import os

def download_offline_model():
    # 模型名称：Systran 官方转换的 CTranslate2 格式模型
    # 选项: "Systran/faster-whisper-large-v3" (精度最高，推荐你的显卡使用)
    # 选项: "Systran/faster-whisper-medium" (速度更快)
    repo_id = "Systran/faster-whisper-large-v3"
    
    # 指定下载到本地的路径
    local_dir = "./models/faster-whisper-large-v3"
    
    print(f"开始下载模型 {repo_id} 到 {local_dir} ...")
    
    # snapshot_download 会下载仓库中的所有文件
    snapshot_download(
        repo_id=repo_id,
        local_dir=local_dir,
        local_dir_use_symlinks=False, # 确保下载的是实实在在的文件，而不是软链接
        resume_download=True          # 支持断点续传
    )
    
    print("下载完成！")
    print(f"模型路径为: {os.path.abspath(local_dir)}")

if __name__ == "__main__":
    download_offline_model()
```

**下载完成后，你的目录结构应该是这样的：**
```text
你的项目文件夹/
│
├── models/
│   └── faster-whisper-large-v3/
│       ├── config.json
│       ├── model.bin       <-- 核心模型文件，很大 (约 3GB)
│       ├── tokenizer.json
│       ├── vocabulary.txt
│       └── ...
│
├── main.py (你的字幕生成代码)
└── ...
```

---

### 第二步：修改推理代码调用本地模型

只要在 `WhisperModel` 初始化时，把原来的模型名称字符串（如 `"large-v3"`）替换成**你的本地文件夹路径**即可。

修改之前的代码如下：

```python
from faster_whisper import WhisperModel
import os

# ... (保留之前的 format_timestamp 函数) ...

def generate_subtitles_offline(video_path, output_srt_path, model_path, device="cuda"):
    print(f"正在从本地路径加载模型: {model_path} ...")
    
    # 检查路径是否存在，防止报错
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"找不到模型文件夹: {model_path}")

    # 这里直接传入路径字符串
    # compute_type="float16" 完美适配你的 RTX 4060 Ti
    model = WhisperModel(model_path, device=device, compute_type="float16")

    print(f"开始转录: {video_path}")
    segments, info = model.transcribe(video_path, beam_size=5, vad_filter=True)
    
    # ... (后续写入 SRT 的代码保持不变) ...

if __name__ == "__main__":
    VIDEO_FILE = "test.mp4"
    SRT_FILE = "test.srt"
    
    # 关键点：指向刚才下载的文件夹路径
    # 建议使用绝对路径，或者确保相对路径正确
    LOCAL_MODEL_PATH = "./models/faster-whisper-large-v3"
    
    generate_subtitles_offline(VIDEO_FILE, SRT_FILE, LOCAL_MODEL_PATH)
```

---

### 备用方案：如果你无法运行 Python 下载脚本

如果你的下载环境无法运行 Python，你可以通过浏览器手动下载。

1.  访问 Hugging Face 仓库页面：[Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3/tree/main)
2.  手动下载列表中的所有文件（尤其是 `model.bin`, `config.json`, `vocabulary.txt`, `tokenizer.json`）。
3.  在你的电脑上新建一个文件夹（例如 `D:\AI_Models\large-v3`），把下载的文件全部丢进去。
4.  代码中的路径指向 `D:\\AI_Models\\large-v3` 即可（注意 Windows 路径的双反斜杠）。

### 总结
1.  利用 `huggingface_hub` 下载模型文件到本地文件夹。
2.  代码中 `WhisperModel("./models/xxx")` 指向该文件夹。
3.  **断网测试**：拔掉网线运行一次，如果能跑通，说明离线部署成功。

这样你的 4060 Ti 就可以在完全无网的环境下全速工作了！