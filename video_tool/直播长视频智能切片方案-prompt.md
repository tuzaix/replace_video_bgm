为了兼容**电商带货（强语音逻辑）**和**才艺表演（跳舞/唱歌，强音频/视觉逻辑）**，我们需要将方案升级为**“多模态切片系统”**。

针对不同的直播类型，切片的判定核心逻辑完全不同：
1.  **电商带货（Speech Mode）**：核心是**“话没说完不能断”**。依赖 ASR（语音识别）+ 语义断句。
2.  **跳舞/唱歌（Performance Mode）**：核心是**“音乐/动作的完整性”**和**“高潮部分提取”**。依赖音频能量检测（VAD/RMS）来寻找一段表演的开始与结束，并自动截取“高潮（Chorus）”片段。

---

### 综合方案架构设计

我们需要引入 `pydub` 和 `numpy` 来处理音频能量分析，配合之前的 `faster-whisper`。

#### 安装新增依赖
```bash
pip install faster-whisper ffmpeg-python pydub numpy
```

#### Python 完整代码实现

这个方案包含一个“通用切片器类”，支持 `mode="speech"` (带货) 和 `mode="performance"` (唱跳)。

```python
import os
import subprocess
import numpy as np
from faster_whisper import WhisperModel
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

class UniversalLiveSlicer:
    def __init__(self, model_size="small", device="auto"):
        self.model_size = model_size
        self.device = device
        self.whisper_model = None # 懒加载

    def _load_whisper(self):
        if not self.whisper_model:
            print("正在加载 Whisper 模型...")
            self.whisper_model = WhisperModel(self.model_size, device=self.device, compute_type="int8")

    def extract_audio(self, video_path):
        """从视频提取音频用于分析 (导出为wav/mp3)"""
        audio_path = video_path + ".mp3"
        if not os.path.exists(audio_path):
            print("正在提取临时音频...")
            # 使用 ffmpeg 提取音频，采样率 16k 足够分析
            subprocess.run([
                "ffmpeg", "-y", "-i", video_path, 
                "-vn", "-ac", "1", "-ar", "16000", "-ab", "64k",
                "-loglevel", "error", audio_path
            ])
        return audio_path

    # ===========================
    # 模式 A: 电商带货 (语义切片)
    # ===========================
    def _analyze_speech(self, video_path, min_sec=20, max_sec=60):
        self._load_whisper()
        print("正在进行语义识别...")
        segments, _ = self.whisper_model.transcribe(video_path, beam_size=5, language="zh", vad_filter=True)
        
        clips = []
        current_clip = {"start": 0, "end": 0, "text": ""}
        
        segment_list = list(segments) # 转换为列表
        if not segment_list: return []

        current_clip["start"] = segment_list[0].start

        for i, seg in enumerate(segment_list):
            current_clip["end"] = seg.end
            current_clip["text"] += seg.text
            duration = current_clip["end"] - current_clip["start"]
            
            # 判断断句逻辑：
            # 1. 标点符号结束
            # 2. 时长达标
            is_sentence_end = seg.text.strip()[-1] in ['。', '！', '？', '!', '?', '.']
            
            if (duration >= max_sec) or (duration >= min_sec and is_sentence_end):
                clips.append(current_clip)
                current_clip = {"start": seg.end, "end": seg.end, "text": ""}
        
        return clips

    # ===========================
    # 模式 B: 唱跳表演 (能量切片)
    # ===========================
    def _analyze_performance(self, video_path, target_duration=30):
        """
        针对唱歌/跳舞：
        1. 识别音频中的连续高能量块（一段完整的歌/舞）。
        2. 在该段落中，寻找能量最高的部分（通常是副歌/高潮）。
        3. 截取 target_duration 时长的片段。
        """
        audio_file = self.extract_audio(video_path)
        print("正在加载音频进行能量分析...")
        audio = AudioSegment.from_file(audio_file)
        
        # 1. 检测非静音区间 (假设一段表演中间停顿不超过 2秒)
        # silence_thresh: 这里的阈值根据实际情况调整，-40dBFS 是通用值
        print("正在分析表演片段...")
        nonsilent_ranges = detect_nonsilent(audio, min_silence_len=2000, silence_thresh=-40)
        
        clips = []
        
        # 遍历每一个识别出的“表演段落”
        for start_ms, end_ms in nonsilent_ranges:
            duration_ms = end_ms - start_ms
            duration_sec = duration_ms / 1000
            
            # 过滤掉太短的噪音（小于10秒不算表演）
            if duration_sec < 10:
                continue
                
            # 如果段落本身就符合短视频时长 (比如 15s - 60s)，直接保留
            if duration_sec <= 60:
                clips.append({
                    "start": start_ms / 1000,
                    "end": end_ms / 1000,
                    "type": "full_performance"
                })
                continue

            # 如果段落很长（比如一首歌3分钟），我们需要提取“高潮”
            # 策略：在这一段中，找到音量(RMS)最大的 target_duration 区间
            clip_audio = audio[start_ms:end_ms]
            
            # 滑动窗口寻找最大能量区间
            window_size_ms = target_duration * 1000
            step_ms = 1000 # 步长1秒
            
            max_energy = 0
            best_offset = 0
            
            # 简单的性能优化：只检查中间部分，因为高潮通常不在开头或结尾
            # 这里为了简化，检查全段
            search_range = range(0, len(clip_audio) - window_size_ms, step_ms)
            
            for offset in search_range:
                chunk = clip_audio[offset : offset + window_size_ms]
                energy = chunk.rms # 获取均方根能量
                if energy > max_energy:
                    max_energy = energy
                    best_offset = offset
            
            # 计算最终切片的绝对时间
            final_start = (start_ms + best_offset) / 1000
            final_end = final_start + target_duration
            
            clips.append({
                "start": final_start,
                "end": final_end,
                "type": "highlight"
            })

        # 清理临时文件
        try: os.remove(audio_file)
        except: pass
        
        return clips

    # ===========================
    # 执行切片
    # ===========================
    def cut_video(self, video_path, output_dir, mode="speech"):
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        filename = os.path.basename(video_path).split('.')[0]
        
        if mode == "speech":
            clips = self._analyze_speech(video_path)
        elif mode == "performance":
            clips = self._analyze_performance(video_path)
        else:
            raise ValueError("Mode must be 'speech' or 'performance'")
            
        print(f"分析完成，模式 [{mode}]，共生成 {len(clips)} 个切片方案。开始导出...")

        for idx, clip in enumerate(clips):
            start = clip["start"]
            duration = clip["end"] - start
            
            out_name = f"{filename}_{mode}_{idx+1:03d}.mp4"
            out_path = os.path.join(output_dir, out_name)

            # 极速切片命令
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start:.3f}",
                "-t", f"{duration:.3f}",
                "-i", video_path,
                "-c", "copy", # 视频不重编码
                "-avoid_negative_ts", "1",
                "-loglevel", "error",
                out_path
            ]
            subprocess.run(cmd)
            print(f"完成: {out_name} ({duration:.1f}s)")

# ===========================
# 使用示例
# ===========================
if __name__ == "__main__":
    # 实例化工具
    slicer = UniversalLiveSlicer()
    
    VIDEO_FILE = "live_record.mp4"
    
    # 场景 1: 电商带货
    # 适合：口播、讲解、带货、聊天
    # slicer.cut_video(VIDEO_FILE, "./out_speech", mode="speech")
    
    # 场景 2: 唱跳表演
    # 适合：跳舞、唱歌、乐器演奏
    # 逻辑：寻找非静音段 -> 并在长段落中截取能量最高(最嗨)的30秒
    slicer.cut_video(VIDEO_FILE, "./out_dance", mode="performance")
```

### 不同场景的算法逻辑详解

#### 1. 电商/口播场景 (`mode="speech"`)
*   **难点**：语速快，容易把句子切断。
*   **解决方案**：
    *   利用 Whisper 的 `vad_filter=True` 过滤背景杂音。
    *   识别标点符号（句号、问号、感叹号）。
    *   **滑动窗口策略**：如果当前片段凑够了 30 秒，但句子没结束，继续往后延，直到遇到句号或达到 60 秒上限才强制切断。

#### 2. 跳舞/唱歌场景 (`mode="performance"`)
*   **难点**：没有语音，只有音乐；或者一直在唱歌。如果按时间硬切，可能切在歌曲高潮前一秒，非常难受。
*   **解决方案**：**基于音频能量的高潮提取 (Audio Energy Highlight Extraction)**。
    *   **第一步：段落分割**。使用 `pydub.detect_nonsilent` 识别出“有音乐/声音”的区块。比如主播聊了5分钟天（低能量），然后跳了3分钟舞（连续高能量），这3分钟会被识别出来。
    *   **第二步：高潮定位**。在识别出的3分钟舞蹈中，我们不能把3分钟全发短视频（太长）。脚本会计算这3分钟内，**音量最大（RMS最高）** 的 30秒（可配置）。通常音量最大、鼓点最密集的区域就是副歌/高潮部分。
    *   **第三步：对齐切片**。直接提取这段高潮。

### 进阶优化方向（可行性扩展）

如果需要更精准的“跳舞”切片（有时候跳舞音乐不大，但动作很大），可以结合**视觉分析**（但速度会变慢）：

1.  **画面变化率检测**：
    使用 FFmpeg 的 `scdet` (Scene Change Detect) 滤镜，或者计算帧差法。当画面变化剧烈时，认为是舞蹈部分；画面静止（坐着说话）时，认为是闲聊。
    *   *FFmpeg 命令示例*：`ffmpeg -i video.mp4 -vf "scdet=threshold=10" -f null -`

2.  **混合权重**：
    最终得分 = `0.7 * 音量能量` + `0.3 * 画面动量`。

### 总结
对于大多数直播录像：
*   **卖货**用 `speech` 模式（基于文本语义，保全句子）。
*   **才艺**用 `performance` 模式（基于音量能量，提取高潮）。

这个 Python 脚本无需昂贵的 GPU 视频分析算法，仅靠 CPU 处理音频即可实现极高的处理效率，非常适合批量生产短视频。