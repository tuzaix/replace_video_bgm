"""
Simple FFmpeg concatenation module.

Provides a `VideoConcat` class that mirrors the core logic from
`tests/concat_debug_ffmpeg.py` while packaging it for reuse in
`concat_tool`.

Features:
- Generates a concat list file next to the output.
- Robust re-encode using H.264 (NVENC or libx264) and AAC.
- Optional BGM replacement: maps video from the concat input and audio
  from the BGM input; uses `-shortest` to end at video duration.
- Quality presets: balanced / compact / tiny.

Note:
- This module focuses on the concat step; it does not scan source
  directories or pick random inputs.
- It assumes FFmpeg is available on PATH or initialized by callers.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import tempfile
import subprocess
import shutil
import os

try:
    # Optional environment bootstrap; callers may choose to skip.
    from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env  # type: ignore
    bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)
except Exception:
    pass
from .config import resolve_quality

class VideoConcat:
    """Concatenate multiple video slices into a single output using FFmpeg.

    Parameters
    ----------
    slices : List[Path]
        Ordered list of video slice file paths to concatenate.
    out_path : Path
        Output video file path (e.g., `out.mp4`). The temporary concat list
        will be created in the same directory.
    bgm_path : Optional[Path]
        Optional audio file to use as background music. When provided, the
        original audio from the input is replaced by the BGM (looped) and the
        output ends at the video duration (`-shortest`).
    quality : str
        Quality preset: `balanced` (default), `compact`, or `tiny`. These
        map to NVENC CQ / x264 CRF / AAC bitrate settings.
    use_gpu : bool
        When True and NVENC is available, use `h264_nvenc`; otherwise fall
        back to `libx264`.
    """

    def __init__(
        self,
        slices: List[Path],
        out_path: Path,
        bgm_path: Optional[Path] = None,
        quality: str = "balanced",
        use_gpu: bool = True,
    ) -> None:
        self.slices = slices
        self.out_path = out_path
        self.bgm_path = bgm_path
        self.quality = quality
        self.use_gpu = use_gpu

    def _write_concat_list(self) -> Path:
        """Create a concat list file alongside `self.out_path`.

        Returns
        -------
        Path
            Path to the generated concat list file.
        """
        out_dir = self.out_path.parent
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # If mkdir fails, fall back to system temp dir
            out_dir = Path(tempfile.gettempdir())

        tf = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".txt",
            mode="w",
            encoding="utf-8",
            dir=str(out_dir),
        )
        with tf as f:
            for p in self.slices:
                abspath = os.path.abspath(str(p))
                abspath = abspath.replace("\\", "/")
                f.write(f"file '{abspath}'\n")
        return Path(tf.name)

    def _nvenc_available(self) -> bool:
        """Return True if local FFmpeg supports `h264_nvenc` encoder."""
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            return False
        try:
            res = subprocess.run([ffmpeg_bin, "-hide_banner", "-encoders"], capture_output=True)
            if res.returncode != 0:
                return False
            stdout = ""
            try:
                stdout = (res.stdout or b"").decode("utf-8", errors="ignore")
            except Exception:
                try:
                    stdout = (res.stdout or b"").decode("mbcs", errors="ignore")
                except Exception:
                    stdout = ""
            return "h264_nvenc" in stdout
        except Exception:
            return False

    def _build_ffmpeg_cmd(self, list_path: Path) -> List[str]:
        """Build the FFmpeg command for concat and optional BGM replacement.

        Parameters
        ----------
        list_path : Path
            The concat demuxer list file path.

        Returns
        -------
        List[str]
            The FFmpeg command argument list.
        """
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise FileNotFoundError("未找到 ffmpeg，请先初始化环境或配置 PATH")

        base = [
            ffmpeg_bin,
            "-hide_banner",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
        ]

        if self.bgm_path is not None:
            base += [
                "-stream_loop",
                "-1",
                "-i",
                str(self.bgm_path),
            ]

        # Quality presets: import from centralized config
        
        q_nvenc_cq, q_x264_crf, q_audio_bitrate = resolve_quality(self.quality)

        cmd = base.copy()
        if self.use_gpu and self._nvenc_available():
            cmd += [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p7",
                "-rc",
                "vbr_hq",
                "-rc-lookahead",
                "32",
                "-spatial-aq",
                "1",
                "-temporal-aq",
                "1",
                "-aq-strength",
                "8",
                "-bf",
                "3",
                "-cq",
                q_nvenc_cq,
                "-b:v",
                "0",
            ]
        else:
            cmd += [
                "-c:v",
                "libx264",
                "-crf",
                q_x264_crf,
                "-preset",
                "slow",
                "-bf",
                "3",
                "-profile:v",
                "high",
                "-level",
                "4.1",
            ]

        cmd += [
            "-c:a",
            "aac",
            "-b:a",
            q_audio_bitrate,
            "-ar",
            "44100",
            "-ac",
            "2",
        ]

        if self.bgm_path is not None:
            cmd += [
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-shortest",
            ]

        cmd += [
            "-map_metadata",
            "-1",
            "-movflags",
            "+faststart",
            str(self.out_path),
        ]
        return cmd

    def run(self) -> bool:
        """Execute the concat process and write the output file.

        Returns
        -------
        bool
            True on success; False otherwise.
        """
        try:
            list_path = self._write_concat_list()
            cmd = self._build_ffmpeg_cmd(list_path)
            # Print for debugging/traceability
            print("[concat] ffmpeg cmd:", " ".join(cmd))
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode == 0 and self.out_path.exists():
                return True
            # Dump tail of stderr to aid debugging
            try:
                stderr_text = (res.stderr or b"").decode("utf-8", errors="ignore")
            except Exception:
                try:
                    stderr_text = (res.stderr or b"").decode("mbcs", errors="ignore")
                except Exception:
                    stderr_text = ""
            print("[concat] ffmpeg failed, stderr tail:\n", stderr_text[-800:])
            return False
        except Exception as e:
            print(f"[concat] Exception: {e}")
            return False


__all__ = ["VideoConcat"]