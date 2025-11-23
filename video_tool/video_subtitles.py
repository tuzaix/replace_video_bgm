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

    def __init__(self, model_size: str = "medium", device: str = "auto", model_path: Optional[str] = None, proxy: Optional[str] = "127.0.0.1:7897") -> None:
        """初始化字幕生成器。

        参数
        ----
        model_size: str
            Whisper 模型大小。支持如 "large-v3"、"medium"。
        device: str
            运行设备："auto"、"cuda"、"cpu"。
        model_path: Optional[str]
            本地模型目录（优先）。
        proxy: Optional[str]
            下载代理，默认 "127.0.0.1:7897"。
        """

        self._WhisperModel = WhisperModel  # type: ignore
        self.model_size = model_size
        self.device = device
        self.model_path = model_path
        self.proxy = proxy

        compute_type = "float16" if self.device == "cuda" else "int8"

        repo_id = self._map_model_to_repo(self.model_size)
        default_dir = self._default_local_dir(repo_id)
        local_dir = self._resolve_local_model_dir(repo_id)

        if not local_dir:
            try:
                local_dir = self._download_model(repo_id, self.proxy)
            except Exception:
                local_dir = None

        if isinstance(local_dir, str) and os.path.isdir(local_dir):
            try:
                self.model = self._WhisperModel(local_dir, device=self.device, compute_type=compute_type)
                self.model_path = local_dir
                return
            except Exception:
                pass

        try:
            self.model = self._WhisperModel(self.model_size, device=self.device, compute_type=compute_type)
            return
        except Exception:
            candidates: list[str] = []
            env_dir = os.environ.get("WHISPER_MODEL_DIR", "")
            if env_dir:
                candidates.append(os.path.join(env_dir, self.model_size))
                candidates.append(env_dir)
            try:
                repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
                candidates.append(os.path.join(repo_root, "vendor", "whisper", self.model_size))
            except Exception:
                pass
            for p in candidates:
                try:
                    if p and os.path.isdir(p):
                        self.model = self._WhisperModel(p, device=self.device, compute_type=compute_type)
                        return
                except Exception:
                    continue
            try:
                self.model = self._WhisperModel(self.model_size, device="cpu", compute_type="int8")
            except Exception as e:
                raise RuntimeError(
                    "无法加载 Whisper 模型。无网络环境下请准备离线模型目录：\n"
                    "- 通过设置环境变量 WHISPER_MODEL_DIR 指向包含模型文件的目录；\n"
                    "- 或将模型放置到 vendor/whisper/<model_size> 目录；\n"
                    "- 或暂时联网以下载并缓存模型。\n"
                    f"原始错误: {e}"
                )
        try:
            if isinstance(default_dir, str) and os.path.isdir(default_dir):
                self.model_path = default_dir
        except Exception:
            pass

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

    def save_srt(self, video_path: str, output_srt_path: Optional[str] = None, translate: bool = False) -> str:
        """生成并保存 SRT 文件，返回输出路径。"""
        vp = os.path.abspath(video_path)
        out_path = output_srt_path or os.path.join(os.path.dirname(vp), f"{os.path.splitext(os.path.basename(vp))[0]}.srt")
        segments, _ = self.transcribe(vp, beam_size=5, translate=translate)
        with open(out_path, "w", encoding="utf-8") as f:
            idx = 1
            for seg in segments:
                start = self._format_timestamp(float(getattr(seg, "start", 0.0)))
                end = self._format_timestamp(float(getattr(seg, "end", 0.0)))
                text = str(getattr(seg, "text", "")).strip()
                f.write(f"{idx}\n")
                f.write(f"{start} --> {end}\n")
                f.write(f"{text}\n\n")
                idx += 1
        return out_path

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

    def _default_local_dir(self, repo_id: str) -> str:
        """返回默认的本地模型目录（与模块同级的 models 子目录）。"""
        tail = repo_id.split("/")[-1]
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "models", tail))

    def _resolve_local_model_dir(self, repo_id: str) -> Optional[str]:
        """解析并返回可用的本地模型目录路径。"""
        if isinstance(self.model_path, str) and os.path.isdir(self.model_path):
            return self.model_path
        env_dir = os.environ.get("WHISPER_MODEL_DIR", "").strip()
        if env_dir:
            candidate = os.path.join(env_dir, self.model_size)
            if os.path.isdir(candidate):
                return candidate
            if os.path.isdir(env_dir):
                return env_dir
        default_dir = self._default_local_dir(repo_id)
        if os.path.isdir(default_dir):
            return default_dir
        try:
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
            vendor_dir = os.path.join(repo_root, "vendor", "whisper", self.model_size)
            if os.path.isdir(vendor_dir):
                return vendor_dir
        except Exception:
            pass
        return None

    def _download_model(self, repo_id: str, proxy: Optional[str]) -> str:
        """下载模型到默认目录并返回绝对路径。"""
        try:
            from huggingface_hub import snapshot_download  # type: ignore
        except Exception:
            raise RuntimeError("未找到 huggingface_hub。请先安装：pip install huggingface_hub")
        target_dir = self._default_local_dir(repo_id)
        os.makedirs(target_dir, exist_ok=True)
        if proxy:
            url = proxy if proxy.startswith("http") else f"http://{proxy}"
            os.environ.setdefault("HTTP_PROXY", url)
            os.environ.setdefault("HTTPS_PROXY", url)
        snapshot_download(
            repo_id=repo_id,
            local_dir=target_dir,
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        return target_dir