"""
Normalize Videos Module (OOP API)

Provides a class-based interface to normalize a set of videos under a
directory, making them consistent for later concatenation.

Normalization defaults:
- Container: mp4
- Video: H.264 (auto NVENC if available, fallback libx264), pixel format yuv420p
- Resolution: keep original (no scaling or padding)
- Frame rate: 25 fps (constant frame rate)
- Audio: AAC, 44.1 kHz, stereo

Concurrency is supported via ThreadPoolExecutor.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional

# Initialize ffmpeg environment if bundled (keeps behavior consistent with other tools)
try:
    from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
    bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)
except Exception:
    pass

# Hardware encoder detection utilities
from utils.gpu_detect import is_nvenc_available
from utils.xprint import xprint

SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v"}


class VideoNormalizer:
    """Normalize a batch of videos to consistent parameters.

    Parameters
    ----------
    fps : int, default 25
        Target constant frame rate.
    use_gpu : bool, default True
        Attempt to use NVENC (`h264_nvenc`) if available, otherwise fallback to CPU.
    threads : int, default 4
        Number of threads for concurrent processing.
    pix_fmt : str, default "yuv420p"
        Pixel format used for output videos.

    Notes
    -----
    - Resolution is preserved (no scaling or padding).
    - Audio normalized to AAC stereo 44.1 kHz.
    - Outputs are MP4 files with `_normalized` suffix.
    """

    def __init__(self, fps: int = 25, use_gpu: bool = True, threads: int = 4, pix_fmt: str = "yuv420p") -> None:
        self.fps = int(fps)
        self.use_gpu = bool(use_gpu)
        self.threads = max(1, int(threads))
        self.pix_fmt = pix_fmt

    @staticmethod
    def _popen_silent_kwargs() -> dict:
        """Return kwargs to suppress console windows for subprocess on Windows."""
        try:
            if os.name == "nt":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                return {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
        except Exception:
            pass
        return {}

    @staticmethod
    def find_videos(directory: Path) -> List[Path]:
        """Find supported video files under `directory` (non-recursive, current dir only)."""
        videos: List[Path] = []
        if not directory.exists() or not directory.is_dir():
            return videos
        for file_path in directory.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_VIDEO_EXTS:
                videos.append(file_path)
        return sorted(videos)

    @staticmethod
    def ensure_dir(path: Path) -> None:
        """Ensure directory exists."""
        path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _format_size(num_bytes: int) -> str:
        """Format bytes to a human-readable string in MB with two decimals."""
        try:
            mb = num_bytes / (1024 * 1024)
            return f"{mb:.2f} MB"
        except Exception:
            return f"{num_bytes} B"

    @staticmethod
    def _percent_change(original_bytes: int, new_bytes: int) -> Optional[float]:
        """Return percentage change from original to new size (positive means reduction).

        Examples
        --------
        - original=100, new=80 → 20% reduction → returns 20.0
        - original=100, new=120 → -20% increase → returns -20.0
        """
        try:
            if original_bytes <= 0:
                return None
            return (original_bytes - new_bytes) * 100.0 / original_bytes
        except Exception:
            return None

    @staticmethod
    def _format_bitrate(bit_rate_bits: Optional[int]) -> Optional[str]:
        """Format bit rate in kbps with no decimals, return None if unknown."""
        try:
            if bit_rate_bits is None:
                return None
            kbps = bit_rate_bits / 1000.0
            return f"{kbps:.0f} kbps"
        except Exception:
            return None

    @staticmethod
    def _parse_fps(raw: Optional[str]) -> Optional[float]:
        """Parse ffprobe frame rate string (e.g., '30000/1001') to float."""
        if not raw:
            return None
        try:
            if "/" in raw:
                num, den = raw.split("/", 1)
                n = float(num)
                d = float(den)
                if d == 0:
                    return None
                return n / d
            return float(raw)
        except Exception:
            return None

    @staticmethod
    def _probe_media(path: Path) -> Optional[dict]:
        """Run ffprobe and return parsed basic media attributes for debug.

        Returns a dictionary with keys:
        - container: str | None
        - duration: float | None
        - bit_rate: int | None (bits per second)
        - video: {codec, width, height, pix_fmt, fps}
        - audio: {codec, sample_rate, channels}
        """
        ffprobe_bin = shutil.which("ffprobe")
        if not ffprobe_bin:
            return None
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate,format_name:stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,r_frame_rate,sample_rate,channels",
            "-print_format",
            "json",
            str(path),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, **VideoNormalizer._popen_silent_kwargs())
            if res.returncode != 0:
                return None
            data = json.loads((res.stdout or b"{}").decode("utf-8", errors="ignore") or "{}")
        except Exception:
            return None

        fmt = data.get("format", {})
        streams = data.get("streams", []) or []
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)

        container = fmt.get("format_name")
        try:
            duration = float(fmt.get("duration")) if fmt.get("duration") is not None else None
        except Exception:
            duration = None
        try:
            bit_rate = int(fmt.get("bit_rate")) if fmt.get("bit_rate") is not None else None
        except Exception:
            bit_rate = None

        video = None
        if v:
            fps = VideoNormalizer._parse_fps(v.get("avg_frame_rate") or v.get("r_frame_rate"))
            video = {
                "codec": v.get("codec_name"),
                "width": v.get("width"),
                "height": v.get("height"),
                "pix_fmt": v.get("pix_fmt"),
                "fps": fps,
            }

        audio = None
        if a:
            try:
                sr = int(a.get("sample_rate")) if a.get("sample_rate") is not None else None
            except Exception:
                sr = None
            audio = {
                "codec": a.get("codec_name"),
                "sample_rate": sr,
                "channels": a.get("channels"),
            }

        return {
            "container": container,
            "duration": duration,
            "bit_rate": bit_rate,
            "video": video,
            "audio": audio,
        }

    @staticmethod
    def _print_attr_diff(name: str, src_attrs: Optional[dict], out_attrs: Optional[dict]) -> None:
        """Print attribute mapping lines for debug comparing original vs normalized output."""
        xprint(f"🔍 属性对比 {name}:")
        if not src_attrs or not out_attrs:
            xprint("  (ffprobe 不可用或属性获取失败)\n")
            return

        sv = src_attrs.get("video") or {}
        sa = src_attrs.get("audio") or {}
        so = src_attrs.get("container")
        sb = VideoNormalizer._format_bitrate(src_attrs.get("bit_rate"))

        ov = out_attrs.get("video") or {}
        oa = out_attrs.get("audio") or {}
        oo = out_attrs.get("container")
        ob = VideoNormalizer._format_bitrate(out_attrs.get("bit_rate"))

        def fmt_res(x):
            w = x.get("width")
            h = x.get("height")
            return f"{w}x{h}" if w and h else "?"

        def fmt_fps(x):
            fps = x.get("fps")
            return f"{fps:.2f}" if isinstance(fps, (float, int)) and fps else "?"

        xprint(
            "  视频: "
            f"分辨率 {fmt_res(sv)} → {fmt_res(ov)}; "
            f"帧率 {fmt_fps(sv)} → {fmt_fps(ov)}; "
            f"像素 {sv.get('pix_fmt') or '?'} → {ov.get('pix_fmt') or '?'}; "
            f"编码 {sv.get('codec') or '?'} → {ov.get('codec') or '?'}"
        )
        xprint(
            "  音频: "
            f"编码 {sa.get('codec') or '?'} → {oa.get('codec') or '?'}; "
            f"采样率 {sa.get('sample_rate') or '?'} → {oa.get('sample_rate') or '?'}; "
            f"声道 {sa.get('channels') or '?'} → {oa.get('channels') or '?'}"
        )
        xprint(
            "  容器/码率: "
            f"容器 {so or '?'} → {oo or '?'}; "
            f"总体码率 {sb or '?'} → {ob or '?'}\n"
        )

    # @staticmethod
    # def _normalized_output_path(in_path: Path, out_dir: Path) -> Path:
    #     """Return an available output path for normalized video.

    #     This method preserves the previous behavior of generating a unique
    #     filename by appending a numeric suffix when the base name already
    #     exists. It is used where we explicitly want distinct outputs.
    #     """
    #     base = out_dir / f"{in_path.stem}_normalized.mp4"
    #     if not base.exists():
    #         return base
    #     idx = 1
    #     while True:
    #         candidate = out_dir / f"{in_path.stem}_normalized_{idx}.mp4"
    #         if not candidate.exists():
    #             return candidate
    #         idx += 1

    @staticmethod
    def _base_output_path(in_path: Path, out_dir: Path) -> Path:
        """Return base output path `<stem>_normalized.mp4` without suffix.

        Used by `normalize()` when `skip_existing=True` to check whether the
        intended normalized output already exists and skip reprocessing.
        """
        return out_dir / f"{in_path.stem}_normalized.mp4"

    def _build_ffmpeg_cmd(
        self,
        in_path: Path,
        out_path: Path,
        start_s: float = 0.0,
        end_s: Optional[float] = None,
    ) -> List[str]:
        """Construct ffmpeg command to normalize one video while keeping original resolution.

        Encoding parameter mapping for better compression without visible quality loss:
        - CPU (libx264): use `preset=slow` with `crf=23` to improve compression vs `medium` at similar perceptual quality.
        - GPU (h264_nvenc): use `preset=p7` (HQ), `rc=vbr_hq`, enable lookahead and AQ to enhance efficiency
          while keeping quality, and align B-frames with CPU settings.

        NVENC options used:
        - `-preset p7`        → maps to libx264 `-preset slow`
        - `-rc vbr_hq`        → higher-quality VBR mode
        - `-rc-lookahead 32`  → better rate control decisions
        - `-spatial-aq 1` / `-temporal-aq 1` with `-aq-strength 8` → adaptive quantization
        - `-bf 3`             → use 3 B-frames similar to libx264 defaults
        - `-cq 30` & `-b:v 0` → constant-quality target (adjust CQ if needed)
        """
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise RuntimeError("未找到 ffmpeg，请确保已安装并配置到 PATH")

        vf = f"fps={self.fps},format={self.pix_fmt}"

        cmd: List[str] = [ffmpeg_bin, "-y"]

        # Apply head trim with `-ss` before input for faster seeking when re-encoding
        if start_s and start_s > 0:
            cmd += ["-ss", f"{float(start_s):.3f}"]

        # Input specification
        cmd += ["-i", str(in_path)]

        # If end trim is specified, compute duration and use `-t` to avoid ambiguity of `-to`
        if end_s is not None and end_s > (start_s or 0.0):
            duration = float(end_s) - float(start_s or 0.0)
            cmd += ["-t", f"{duration:.3f}"]

        # Video normalization filters and common flags
        cmd += [
            "-filter:v", vf,
            "-pix_fmt", self.pix_fmt,
            "-fps_mode", "cfr",
            "-movflags",
            "+faststart",
            "-map_metadata", "-1",
        ]

        if self.use_gpu and is_nvenc_available():
            # gpu参数
            cmd += [
                "-c:v", "h264_nvenc",
                "-preset", "p7",
                "-rc", "vbr_hq",
                "-rc-lookahead", "32",
                "-spatial-aq", "1",
                "-temporal-aq", "1",
                "-aq-strength", "8",
                "-bf", "3",
                "-cq", "33",
                "-b:v", "0",
            ]
        else: # cpu参数
            cmd += [
                "-c:v", "libx264",
                "-crf", "26",
                "-preset", "slow",
                "-profile:v", "high",
                "-level", "4.1",
                "-bf", "3", 
            ]

        # 音频参数
        cmd += [
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-ac", "2",
            str(out_path),
        ]
        xprint(f"ffmpeg 命令: {' '.join(cmd)}")
        return cmd

    def normalize(
        self,
        src_dir: str,
        dst_dir: str,
        trim_head_s: float = 0.0,
        trim_tail_s: float = 0.0,
        skip_existing: bool = True,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """Normalize all videos under `src_dir` and write outputs to `dst_dir`.

        Parameters
        ----------
        src_dir : str
            Source directory containing input videos (non-recursive, current directory only).
        dst_dir : str
            Destination directory to store normalized videos (.mp4).
        skip_existing : bool, default True
            If True, skip processing when the target normalized output file already
            exists at `<stem>_normalized.mp4` in `dst_dir`.
        on_progress : Optional[Callable[[int, int], None]]
            Optional callback receiving (done, total) during processing.
        trim_head_s : float, default 0.0
            可选的开头裁剪秒数。如果为 0，则不应用。
        trim_tail_s : float, default 0.0
            可选的结尾裁剪秒数。如果为 0，则不应用。

        Returns
        -------
        int
            Number of videos successfully normalized.
        """
        src = Path(src_dir)
        out = Path(dst_dir)
        VideoNormalizer.ensure_dir(out)

        videos = VideoNormalizer.find_videos(src)
        total = len(videos)
        if total == 0:
            xprint("❌ 未在输入目录找到可处理的视频")
            return 0

        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise RuntimeError("未找到 ffmpeg，请确保已安装并配置到 PATH")

        ok_count = 0
        original_total = 0
        new_total = 0

        def _process_one(v: Path) -> tuple[bool, int, int, str, Optional[dict], Optional[dict]]:
            try:
                # Prefer base path for skip-existing logic
                out_path = VideoNormalizer._base_output_path(v, out)
                if skip_existing and out_path.exists():
                    xprint(f"⏭️ 目标已存在，跳过 {v.name} → {out_path.name}")
                    return (True, 0, 0, v.name, None, None)
                # Probe duration if tail trimming requested, to compute end time
                start_s = float(trim_head_s or 0.0)
                end_s: Optional[float] = None
                if trim_tail_s and float(trim_tail_s) > 0:
                    attrs = VideoNormalizer._probe_media(v)
                    dur = None
                    try:
                        dur = float(attrs.get("duration")) if attrs and attrs.get("duration") is not None else None
                    except Exception:
                        dur = None
                    if dur is not None:
                        end_s = max(0.0, dur - float(trim_tail_s))
                cmd = self._build_ffmpeg_cmd(v, out_path, start_s=start_s, end_s=end_s)
                res = subprocess.run(cmd, capture_output=True, **VideoNormalizer._popen_silent_kwargs())
                if res.returncode == 0 and out_path.exists():
                    try:
                        orig_sz = v.stat().st_size
                        new_sz = out_path.stat().st_size
                    except Exception:
                        orig_sz = 0
                        new_sz = 0
                    # Probe media attributes for debug mapping
                    src_attrs = VideoNormalizer._probe_media(v)
                    out_attrs = VideoNormalizer._probe_media(out_path)
                    return (True, orig_sz, new_sz, v.name, src_attrs, out_attrs)
                try:
                    stderr_text = (res.stderr or b"").decode("utf-8", errors="ignore")
                except Exception:
                    try:
                        stderr_text = (res.stderr or b"").decode("mbcs", errors="ignore")
                    except Exception:
                        stderr_text = ""
                xprint(f"❌ 归一化失败 {v.name}: {stderr_text[-500:]}")
                return (False, 0, 0, v.name, None, None)
            except Exception as e:
                xprint(f"❌ 归一化异常 {v.name}: {e}")
                return (False, 0, 0, v.name, None, None)

        done = 0
        with ThreadPoolExecutor(max_workers=self.threads) as ex:
            futures = [ex.submit(_process_one, v) for v in videos]
            for f in as_completed(futures):
                try:
                    ok, orig_sz, new_sz, name, src_attrs, out_attrs = f.result()
                    if ok:
                        ok_count += 1
                        original_total += orig_sz
                        new_total += new_sz
                        fmt_orig = VideoNormalizer._format_size(orig_sz)
                        fmt_new = VideoNormalizer._format_size(new_sz)
                        pct = VideoNormalizer._percent_change(orig_sz, new_sz)
                        if pct is None:
                            xprint(f"✅ {name} 大小: 原始 {fmt_orig} → 新 {fmt_new}")
                        else:
                            sign = "-" if pct >= 0 else "+"
                            xprint(f"✅ {name} 大小: 原始 {fmt_orig} → 新 {fmt_new} 变化 {sign}{abs(pct):.2f}%")
                        # Print attribute mapping for debug
                        # VideoNormalizer._print_attr_diff(name, src_attrs, out_attrs)
                except Exception:
                    pass
                done += 1
                if on_progress:
                    try:
                        on_progress(done, total)
                    except Exception:
                        pass

        # Print overall size summary if available
        if original_total > 0:
            fmt_o = VideoNormalizer._format_size(original_total)
            fmt_n = VideoNormalizer._format_size(new_total)
            overall_pct = VideoNormalizer._percent_change(original_total, new_total)
            if overall_pct is None:
                xprint(f"📦 总体大小: 原始 {fmt_o} → 新 {fmt_n}")
            else:
                sign = "-" if overall_pct >= 0 else "+"
                xprint(f"📦 总体大小: 原始 {fmt_o} → 新 {fmt_n} 变化 {sign}{abs(overall_pct):.2f}%")

        xprint(f"✅ 已归一化 {ok_count}/{total} 个视频 → {out}")
        return ok_count


def normalize_videos(src_dir: str, dst_dir: str, threads: int = 4, use_gpu: bool = True) -> int:
    """Convenience wrapper for backward compatibility using VideoNormalizer.

    Parameters
    ----------
    src_dir : str
        Source directory of videos.
    dst_dir : str
        Destination directory for normalized videos.
    threads : int, default 4
        Number of worker threads.
    use_gpu : bool, default True
        Enable NVENC if available.

    Returns
    -------
    int
        Number of videos successfully normalized.
    """
    return VideoNormalizer(threads=threads, use_gpu=use_gpu).normalize(src_dir, dst_dir)


# Note: This module is library-only. Use `concat_tool.normalize_cli` for CLI.