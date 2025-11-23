from __future__ import annotations

import os
import uuid
import shutil
import subprocess
from typing import Optional, List, Dict, Any, Tuple

from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env

env = bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffmpeg=True)
ffprobe_bin = env.get("ffprobe_path") or shutil.which("ffprobe")
ffmpeg_bin = env.get("ffmpeg_path") or shutil.which("ffmpeg")

try:
    from faster_whisper import WhisperModel  # type: ignore
except Exception:
    raise RuntimeError("未找到 faster-whisper。请先安装：pip install faster-whisper，并确保 FFmpeg 可用。")
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

class BroadcastVideoSlices:
    """直播长视频智能切片，支持语音语义与表演能量两种模式。"""

    _MODEL_CACHE: Dict[str, Any] = {}

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: str = "auto",
        model_path: Optional[str] = None,
    ) -> None:
        """初始化切片器。

        参数
        ----
        model_size: 模型大小，支持 "large-v3"、"medium"、"small" 等；可为 None 使用自动选择
        device: 运行设备，"auto"/"cuda"/"cpu"
        model_path: 模型根目录，要求包含仓库子目录，例如 Systran/faster-whisper-medium
        """
        self._WhisperModel = WhisperModel  # type: ignore
        self.model_size = model_size or self._auto_select_model_size()
        self.device = self._auto_pick_device(device)
        if not model_path:
            raise ValueError("未指定模型目录。请通过 --model-path 或设置环境变量 WHISPER_MODEL_DIR 提供模型目录。")
        self.model_path = self._pick_model_dir(model_path, self._map_model_to_repo(self.model_size))
        self.compute_type = "float16" if self.device == "cuda" else "int8"
        self.model = self._get_or_create_model(self.model_path, self.device, self.compute_type)

    def _auto_pick_device(self, device: str) -> str:
        """自动选择运行设备。"""
        if device != "auto":
            return device
        try:
            import torch  # type: ignore
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def _gpu_info(self) -> Tuple[bool, float]:
        """返回 GPU 是否可用及显存大小（GB）。"""
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
        """根据硬件环境选择模型大小。"""
        gpu, vram = self._gpu_info()
        if gpu:
            if vram >= 8.0:
                return "large-v3"
            if vram >= 4.0:
                return "medium"
            return "small"
        return "medium"

    def _map_model_to_repo(self, size: str) -> str:
        """将模型大小映射为仓库标识。"""
        mapping = {
            "large-v3": "Systran/faster-whisper-large-v3",
            "medium": "Systran/faster-whisper-medium",
            "small": "Systran/faster-whisper-small",
            "base": "Systran/faster-whisper-base",
            "tiny": "Systran/faster-whisper-tiny",
        }
        normalized = (size or "medium").strip().lower()
        return mapping.get(normalized, f"Systran/faster-whisper-{normalized}")

    def _pick_model_dir(self, base_dir: str, repo_id: str) -> str:
        """在根目录下寻找仓库子目录，或直接验证根目录为模型目录。"""
        head, tail = repo_id.split("/")
        cand = os.path.join(base_dir, head, tail)
        if os.path.isdir(cand):
            return cand
        if os.path.isdir(base_dir):
            has_bin = os.path.isfile(os.path.join(base_dir, "model.bin"))
            has_cfg = os.path.isfile(os.path.join(base_dir, "config.json"))
            if has_bin or has_cfg:
                return base_dir
        raise FileNotFoundError(f"未找到模型目录: {base_dir}（期望包含 {head}/{tail} 或直接为模型目录）")

    def _get_or_create_model(self, model_dir: str, device: str, compute_type: str) -> Any:
        """获取或创建 Whisper 模型实例，内部维护单例缓存。"""
        key = f"{os.path.abspath(model_dir)}|{device}|{compute_type}"
        m = self._MODEL_CACHE.get(key)
        if m is not None:
            return m
        inst = self._WhisperModel(model_dir, device=device, compute_type=compute_type)
        self._MODEL_CACHE[key] = inst
        return inst

    def _extract_audio(self, video_path: str) -> Tuple[str, str]:
        """从视频提取临时音频 MP3，返回 (音频路径, 临时目录)。"""
        base_dir = os.path.dirname(os.path.abspath(video_path))
        tmpdir = os.path.join(base_dir, "temp_slices", uuid.uuid4().hex[:8])
        os.makedirs(tmpdir, exist_ok=True)
        audio_path = os.path.join(tmpdir, "audio.mp3")
        in_arg = f"file:{os.path.abspath(video_path).replace('\\', '/')}"
        r = subprocess.run([
            ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            in_arg,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-ab",
            "64k",
            "-loglevel",
            "error",
            audio_path,
        ], capture_output=True)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="ignore")
            raise RuntimeError(f"提取音频失败: {err}")
        return audio_path, tmpdir

    def analyze_speech(self, video_path: str, min_sec: int = 20, max_sec: int = 60, language: Optional[str] = "zh") -> List[Dict[str, Any]]:
        """基于语音语义的切片策略，保证“话没说完不能断”。"""
        segments, _ = self.model.transcribe(video_path, beam_size=5, vad_filter=True, language=language)
        segs = list(segments)
        if not segs:
            return []
        clips: List[Dict[str, Any]] = []
        cur = {"start": float(segs[0].start or 0.0), "end": float(segs[0].start or 0.0), "text": ""}
        for seg in segs:
            cur["end"] = float(seg.end or cur["end"]) 
            cur["text"] += str(getattr(seg, "text", ""))
            duration = float(cur["end"] - cur["start"]) if cur["end"] >= cur["start"] else 0.0
            txt = str(getattr(seg, "text", "")).strip()
            last = txt[-1:] if txt else ""
            is_sentence_end = last in ["。", "！", "？", "!", "?", "."]
            if duration >= max_sec or (duration >= min_sec and is_sentence_end):
                clips.append({"start": cur["start"], "end": cur["end"], "type": "speech", "text": cur["text"]})
                cur = {"start": float(seg.end or cur["end"]), "end": float(seg.end or cur["end"]), "text": ""}
        if cur["end"] > cur["start"]:
            clips.append({"start": cur["start"], "end": cur["end"], "type": "speech", "text": cur["text"]})
        return clips

    def analyze_performance(
        self,
        video_path: str,
        target_duration: int = 30,
        min_silence_len: int = 2000,
        silence_thresh: int = -40,
        min_segment_sec: int = 10,
        max_keep_sec: int = 60,
    ) -> List[Dict[str, Any]]:
        """基于音频能量的切片策略，提取完整段落或高潮 highlight。"""
        audio_path, tmpdir = self._extract_audio(video_path)
        try:
            
            audio = AudioSegment.from_file(audio_path)
            ranges = detect_nonsilent(audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh)
            clips: List[Dict[str, Any]] = []
            for start_ms, end_ms in ranges:
                dur_sec = (end_ms - start_ms) / 1000.0
                if dur_sec < float(min_segment_sec):
                    continue
                if dur_sec <= float(max_keep_sec):
                    clips.append({"start": start_ms / 1000.0, "end": end_ms / 1000.0, "type": "full_performance"})
                    continue
                clip_audio = audio[start_ms:end_ms]
                window_ms = int(target_duration * 1000)
                step_ms = 1000
                best_energy = -1
                best_offset = 0
                for offset in range(0, max(0, len(clip_audio) - window_ms), step_ms):
                    chunk = clip_audio[offset: offset + window_ms]
                    energy = int(chunk.rms or 0)
                    if energy > best_energy:
                        best_energy = energy
                        best_offset = offset
                final_start = (start_ms + best_offset) / 1000.0
                final_end = final_start + float(target_duration)
                clips.append({"start": final_start, "end": final_end, "type": "highlight"})
            return clips
        finally:
            try:
                shutil.rmtree(tmpdir)
            except Exception:
                pass

    def cut_video(self, video_path: str, output_dir: str, mode: str = "speech", **kwargs: Any) -> List[str]:
        """执行切片并返回输出文件路径列表。"""
        os.makedirs(output_dir, exist_ok=True)
        name = os.path.splitext(os.path.basename(video_path))[0]
        if mode == "speech":
            clips = self.analyze_speech(
                video_path,
                min_sec=int(kwargs.get("min_sec", 20)),
                max_sec=int(kwargs.get("max_sec", 60)),
                language=kwargs.get("language", "zh"),
            )
        elif mode == "performance":
            clips = self.analyze_performance(
                video_path,
                target_duration=int(kwargs.get("target_duration", 30)),
                min_silence_len=int(kwargs.get("min_silence_len", 2000)),
                silence_thresh=int(kwargs.get("silence_thresh", -40)),
                min_segment_sec=int(kwargs.get("min_segment_sec", 10)),
                max_keep_sec=int(kwargs.get("max_keep_sec", 60)),
            )
        else:
            raise ValueError("mode 需为 'speech' 或 'performance'")
        outs: List[str] = []
        for idx, c in enumerate(clips):
            start = float(c["start"]) if c else 0.0
            end = float(c["end"]) if c else start
            duration = max(0.0, end - start)
            out_name = f"{name}_{mode}_{idx + 1:03d}.mp4"
            out_path = os.path.join(output_dir, out_name)
            cmd = [
                ffmpeg_bin,
                "-y",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{duration:.3f}",
                "-i",
                video_path,
                "-c",
                "copy",
                "-avoid_negative_ts",
                "1",
                "-loglevel",
                "error",
                out_path,
            ]
            subprocess.run(cmd)
            outs.append(out_path)
        return outs