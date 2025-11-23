from __future__ import annotations

import os
from typing import Optional, Iterable, Tuple, Dict, Any
import tempfile
import subprocess
import shutil

from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env

bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffmpeg=True)
try:
    from faster_whisper import WhisperModel  # type: ignore
except Exception:
    raise RuntimeError("未找到 faster-whisper。请先安装：pip install faster-whisper，并确保 FFmpeg 可用。")


class VideoSubtitles:
    """使用 faster-whisper 为视频生成 SRT 字幕文件。"""

    def __init__(self, model_size: Optional[str] = None, device: str = "auto", model_path: Optional[str] = None) -> None:
        """初始化字幕生成器。

        参数
        ----
        model_size: str
            Whisper 模型大小。支持如 "large-v3"、"medium"。
        device: str
            运行设备："auto"、"cuda"、"cpu"。
        model_path: Optional[str]
            本地模型目录（优先）。
        当未指定 model_size 或指定为 "auto" 时，将根据硬件环境自动选择合适的模型大小。
        """
        self._WhisperModel = WhisperModel  # type: ignore
        self.model_size = model_size or "auto"
        self.device = device
        # 自动选择cpu或cuda
        if self.device == "auto":
            try:
                import torch  # type: ignore
                if torch.cuda.is_available():
                    self.device = "cuda"
                else:
                    self.device = "cpu"
            except Exception:
                self.device = "cpu"

        if self.model_size == "auto":
            self.model_size = self._auto_select_model_size()

        self.model_path = model_path
        # 如果不指定 model_path，则报错
        if not self.model_path:
            raise ValueError("未指定 model_path。请通过 --model-path 或设置环境变量 WHISPER_MODEL_DIR 提供模型目录。")
        compute_type = "float16" if self.device == "cuda" else "int8"

        # 根据模型类型获取模型子目录
        repo_id = self._map_model_to_repo(self.model_size)
        local_model = os.path.join(self.model_path, repo_id)
       
        self.model = self._WhisperModel(local_model, device=self.device, compute_type=compute_type)
           

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """将秒值格式化为 SRT 时间戳。"""
        milliseconds = int(round(seconds * 1000.0))
        hours = milliseconds // 3_600_000
        milliseconds -= hours * 3_600_000
        minutes = milliseconds // 60_000
        milliseconds -= minutes * 60_000
        secs = milliseconds // 1000
        milliseconds -= secs * 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

    def transcribe(self, video_path: str, beam_size: int = 5, translate: bool = False) -> Tuple[Iterable[Any], Dict[str, Any]]:
        """执行语音识别并返回分段与信息。"""
        task = "translate" if translate else None
        try:
            if task:
                segments, info = self.model.transcribe(video_path, task=task, beam_size=beam_size, vad_filter=True)
            else:
                segments, info = self.model.transcribe(video_path, beam_size=beam_size, vad_filter=True)
        except Exception:
            tmpdir = tempfile.mkdtemp(prefix="whisper_audio_")
            tmpwav = os.path.join(tmpdir, "audio.wav")
            ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
            abspath = os.path.abspath(video_path)
            in_arg = f"file:{abspath.replace('\\', '/')}"
            try:
                res = subprocess.run([
                    ffmpeg_bin,
                    "-hide_banner",
                    "-nostdin",
                    "-y",
                    "-i",
                    in_arg,
                    "-vn",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-f",
                    "wav",
                    tmpwav,
                ], capture_output=True)
                if res.returncode != 0:
                    safe_copy = os.path.join(tmpdir, "source.mp4")
                    try:
                        shutil.copyfile(video_path, safe_copy)
                    except Exception:
                        safe_copy = video_path
                    res2 = subprocess.run([
                        ffmpeg_bin,
                        "-hide_banner",
                        "-nostdin",
                        "-y",
                        "-i",
                        safe_copy,
                        "-vn",
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        "-f",
                        "wav",
                        tmpwav,
                    ], capture_output=True)
                    if res2.returncode != 0:
                        err_text = ""
                        try:
                            err_text = (res2.stderr or b"").decode("utf-8", errors="ignore")
                        except Exception:
                            try:
                                err_text = (res2.stderr or b"").decode("mbcs", errors="ignore")
                            except Exception:
                                err_text = ""
                        raise RuntimeError(f"ffmpeg 转音频失败: {err_text.strip()}")
                if task:
                    segments, info = self.model.transcribe(tmpwav, task=task, beam_size=beam_size, vad_filter=True)
                else:
                    segments, info = self.model.transcribe(tmpwav, beam_size=beam_size, vad_filter=True)
            finally:
                try:
                    shutil.rmtree(tmpdir)
                except Exception:
                    pass
        meta = {"language": getattr(info, "language", None), "language_probability": float(getattr(info, "language_probability", 0.0))}
        return segments, meta

    def save_srt(self, video_path: str, output_srt_path: Optional[str] = None, translate: bool = False, max_chars_per_line: Optional[int] = 14, max_lines_per_caption: int = 2) -> str:
        """生成并保存 SRT 文件，返回输出路径。"""
        vp = os.path.abspath(video_path)
        out_path = output_srt_path or os.path.join(os.path.dirname(vp), f"{os.path.splitext(os.path.basename(vp))[0]}.srt")
        segments, _ = self.transcribe(vp, beam_size=5, translate=translate)
        with open(out_path, "w", encoding="utf-8") as f:
            idx = 1
            for seg in segments:
                s = float(getattr(seg, "start", 0.0))
                e = float(getattr(seg, "end", 0.0))
                raw_text = str(getattr(seg, "text", "")).strip()
                chunks: list[tuple[float, float, str]]
                if isinstance(max_chars_per_line, int) and max_chars_per_line > 0:
                    chunks = self._split_segment_text(s, e, raw_text, max_chars_per_line, max_lines_per_caption)
                else:
                    chunks = [(s, e, raw_text)]
                for cs, ce, ctext in chunks:
                    start = self._format_timestamp(cs)
                    end = self._format_timestamp(ce)
                    f.write(f"{idx}\n")
                    f.write(f"{start} --> {end}\n")
                    f.write(f"{ctext}\n\n")
                    idx += 1
        return out_path

    def _wrap_text(self, text: str, max_chars: int) -> list[str]:
        """按最大字数将文本换行，优先在空白或常见标点处分割。"""
        t = (text or "").strip()
        if max_chars <= 0:
            return [t]
        lines: list[str] = []
        buf: list[str] = []
        count = 0
        seps = set(" ，。！？；、,.!?;:—-")
        for ch in t:
            buf.append(ch)
            count += 1
            if count >= max_chars and ch in seps:
                lines.append("".join(buf).strip())
                buf = []
                count = 0
        if buf:
            # 若最后一段超长，仍需按字数硬切
            rem = "".join(buf)
            while len(rem) > max_chars:
                lines.append(rem[:max_chars])
                rem = rem[max_chars:]
            if rem:
                lines.append(rem)
        return [ln for ln in lines if ln]

    def _split_segment_text(self, start: float, end: float, text: str, max_chars_per_line: int, max_lines_per_caption: int) -> list[tuple[float, float, str]]:
        """将一个识别片段按行数与每行字数拆分为多个字幕块。"""
        lines = self._wrap_text(text, max_chars_per_line)
        if not lines:
            return [(start, end, text)]
        groups: list[list[str]] = []
        cur: list[str] = []
        for ln in lines:
            cur.append(ln)
            if len(cur) >= max_lines_per_caption:
                groups.append(cur)
                cur = []
        if cur:
            groups.append(cur)
        total_chars = sum(len("".join(g)) for g in groups) or 1
        dur = max(0.0, float(end) - float(start))
        result: list[tuple[float, float, str]] = []
        cur_s = float(start)
        for g in groups:
            gtext = "\n".join(g)
            frac = float(len("".join(g))) / float(total_chars)
            gd = dur * frac if dur > 0 else 0.0
            ce = cur_s + gd if gd > 0 else float(end)
            result.append((cur_s, ce, gtext))
            cur_s = ce
        if result and result[-1][1] < end:
            last_s, _, last_t = result[-1]
            result[-1] = (last_s, end, last_t)
        return result

    def _gpu_info(self) -> Tuple[bool, float]:
        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                vram_gb = float(getattr(props, "total_memory", 0)) / (1024 ** 3)
                return True, vram_gb
        except Exception:
            pass
        return False, 0.0

    def _auto_select_model_size(self) -> str:
        # 根据机器硬件自动选择模型大小
        gpu, vram = self._gpu_info()
        if gpu:
            if vram >= 8.0:
                prefer = "large-v3"
            elif vram >= 4.0:
                prefer = "medium"
            else:
                prefer = "small"
        else:
            prefer = "medium"
        return prefer

    def _map_model_to_repo(self, size: str) -> str:
        """将模型大小映射为 Hugging Face 仓库标识。"""
        normalized = size.strip().lower()
        mapping = {
            "large-v3": "Systran/faster-whisper-large-v3",
            "medium": "Systran/faster-whisper-medium",
            "small": "Systran/faster-whisper-small",
            "base": "Systran/faster-whisper-base",
            "tiny": "Systran/faster-whisper-tiny",
        }
        return mapping.get(normalized, f"Systran/faster-whisper-{normalized}")
