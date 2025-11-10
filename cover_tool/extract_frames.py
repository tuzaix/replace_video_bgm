import os
import sys
import argparse
import subprocess
import random
from typing import Optional
from typing import List, Tuple
from typing import Optional

# 统一启动策略：优先使用内置 FFmpeg，并在开发环境允许系统兜底（通过 FFMPEG_DEV_FALLBACK）。
try:
    from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
    # 在该工具中不强制要求 ffprobe/ffmpeg 存在，保持与原行为一致（调用失败由下游处理）
    bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)
except Exception:
    # 静默忽略初始化失败，维持原行为
    pass


SUPPORTED_EXTS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts", ".flv", ".wmv", ".3gp"
}


def is_video_file(filename: str) -> bool:
    """Return True if filename ends with a supported video extension."""
    _, ext = os.path.splitext(filename)
    return ext.lower() in SUPPORTED_EXTS


def ensure_dir(path: str) -> None:
    """Create directory path if it does not exist."""
    os.makedirs(path, exist_ok=True)


def build_output_path(base_dir: str, cover_dir: str, dirpath: str, filename: str, fmt: str = "jpg") -> str:
    """Build output JPG path under `cover_dir`, mirroring the input directory structure.

    - Mirrors subdirectories under `cover_dir` to avoid filename collisions for same basenames.
    - Changes extension to `.jpg`.
    """
    rel = os.path.relpath(dirpath, start=base_dir)
    out_dir = os.path.join(cover_dir, rel) if rel != "." else cover_dir
    ensure_dir(out_dir)
    name, _ = os.path.splitext(filename)
    ext = "png" if fmt.lower() == "png" else "jpg"
    return os.path.join(out_dir, f"{name}.{ext}")


def probe_video_resolution(video_path: str) -> Optional[Tuple[int, int]]:
    """Probe video resolution using ffprobe. Returns (width, height) or None on failure."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=s=x:p=0",
        video_path,
    ]
    try:
        res = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        out = res.stdout.strip()
        if not out:
            return None
        parts = out.split('x')
        if len(parts) != 2:
            return None
        width = int(parts[0])
        height = int(parts[1])
        return width, height
    except Exception:
        return None


def extract_first_frame(
    video_path: str,
    output_path: str,
    overwrite: bool = False,
    quality: int = 2,
    fmt: str = "jpg",
    seek: float = 0.0,
) -> Tuple[bool, str]:
    """Extract the very first frame of a video to a JPEG using ffmpeg.

    Args:
        video_path: Input video file path.
        output_path: Target image path (.jpg).
        overwrite: Whether to overwrite existing image.

    Returns:
        (ok, message): ok indicates success; message includes info or error detail.
    """
    if (not overwrite) and os.path.exists(output_path):
        return True, f"Skip existing: {output_path}"

    # Build ffmpeg command. Use -ss before -i for fast seek; small positive seek can avoid black frames.
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if overwrite else "-n",
    ]

    # If seek provided and > 0, use it; otherwise use 0
    cmd += ["-ss", str(seek if seek and seek > 0 else 0)]
    cmd += ["-i", video_path, "-frames:v", "1"]

    # Output format specific options
    if fmt.lower() == "jpg" or fmt.lower() == "jpeg":
        # -q:v: 2 is high quality, 1 is best. Range 1-31 (lower better)
        cmd += ["-q:v", str(max(1, min(31, quality))), output_path]
    elif fmt.lower() == "png":
        # PNG uses -compression_level 0-9 (0 fastest, 9 smallest)
        # Map quality 1-31 to compression 0-9 inversely
        comp = int(max(0, min(9, round((31 - max(1, min(31, quality))) / 31 * 9))))
        cmd += ["-compression_level", str(comp), output_path]
    else:
        # default fallback
        cmd += ["-q:v", str(max(1, min(31, quality))), output_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        return True, f"Saved: {output_path}"
    except subprocess.CalledProcessError as e:
        # Show stderr for troubleshooting
        return False, f"ffmpeg error for {video_path}: {e.stderr or e}"
    except FileNotFoundError:
        return False, "ffmpeg not found. Please install ffmpeg and ensure it is in PATH."


def probe_video_duration(video_path: str) -> Optional[float]:
    """Probe video duration in seconds using ffprobe. Returns None on failure."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        res = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        out = res.stdout.strip()
        if not out:
            return None
        return float(out)
    except Exception:
        return None


def extract_random_frame(
    video_path: str,
    output_path: str,
    overwrite: bool = False,
    quality: int = 2,
    fmt: str = "jpg",
    duration_hint: Optional[float] = None,
) -> Tuple[bool, str]:
    """Extract a random frame from the video.

    - Uses a random seek in [0.2, max(0.5, duration-0.2)] if duration is known.
    - If duration is unknown, picks a random seek in [0.2, 5.0].
    """
    dur = duration_hint if (duration_hint and duration_hint > 0.0) else probe_video_duration(video_path)
    if dur and dur > 0.5:
        # Avoid very end; keep within video bounds
        low = 0.2
        high = max(low + 0.3, dur - 0.2)
        seek = random.uniform(low, high)
    else:
        seek = random.uniform(0.2, 5.0)
    return extract_first_frame(video_path, output_path, overwrite=overwrite, quality=quality, fmt=fmt, seek=seek)


def compute_evenly_spaced_seeks(count: int, duration_hint: Optional[float]) -> List[float]:
    """Compute `count` evenly spaced seek timestamps within the video duration.

    - Avoids the very start and end by reserving 0.2s margin on both sides when duration is known.
    - If duration is unknown or too short, falls back to a small window [0.2, 5.0].
    - Uses interior points only: low + step*k, k=1..count, where step=(high-low)/(count+1).
    """
    if count <= 0:
        return []
    low = 0.2
    if duration_hint and duration_hint > (low + 0.5):
        high = max(low + 0.3, duration_hint - 0.2)
    else:
        high = 5.0
    if high <= low:
        high = low + 0.3
    step = (high - low) / (count + 1)
    return [low + step * k for k in range(1, count + 1)]


def scan_and_extract(
    base_dir: str,
    overwrite: bool = False,
    recursive: bool = False,
    quality: int = 2,
    fmt: str = "jpg",
    seek: float = 0.0,
    frames: int = 1,
) -> List[str]:
    """Traverse `base_dir` and extract first frames for all videos to `cover` subdirectory.

    - Mirrors input directory structure under `cover` to avoid collisions.
    - Skips files already extracted unless `overwrite=True`.

    Returns:
        A list of status messages for each processed file.
    """
    base_dir = os.path.abspath(base_dir)
    if not os.path.isdir(base_dir):
        return [f"Not a directory: {base_dir}"]

    cover_dir = os.path.join(base_dir, "cover")
    ensure_dir(cover_dir)

    messages: List[str] = []

    if recursive:
        walker = os.walk(base_dir)
    else:
        # Non-recursive: only top-level files
        walker = [(base_dir, [], os.listdir(base_dir))]

    for dirpath, dirnames, filenames in walker:
        # Avoid traversing the 'cover' output directory itself
        dirnames[:] = [d for d in dirnames if os.path.join(dirpath, d) != cover_dir]

        for fname in filenames:
            if not is_video_file(fname):
                continue
            in_path = os.path.join(dirpath, fname)
            # Probe resolution to route into subdirectories like cover/1080x1920
            wh = probe_video_resolution(in_path)
            if wh:
                res_dir = os.path.join(cover_dir, f"{wh[0]}x{wh[1]}")
            else:
                res_dir = os.path.join(cover_dir, "unknown_resolution")
            ensure_dir(res_dir)

            # Use res_dir as the base for mirroring relative structure
            rel = os.path.relpath(dirpath, start=base_dir)
            out_parent_dir = os.path.join(res_dir, rel) if rel != "." else res_dir
            ensure_dir(out_parent_dir)
            name, _ = os.path.splitext(fname)
            ext = "png" if fmt.lower() == "png" else "jpg"

            # First frame: keep naming without index to preserve previous behavior
            out_path_first = os.path.join(out_parent_dir, f"{name}.{ext}")
            ok, msg = extract_first_frame(
                in_path, out_path_first, overwrite=overwrite, quality=quality, fmt=fmt, seek=seek
            )
            messages.append(msg)

            # Additional evenly spaced frames (name_2.ext, name_3.ext, ...)
            if frames and frames > 1:
                duration_hint = probe_video_duration(in_path)
                seeks = compute_evenly_spaced_seeks(frames - 1, duration_hint)
                for idx, s in enumerate(seeks, start=2):
                    out_path_i = os.path.join(out_parent_dir, f"{name}_{idx}.{ext}")
                    ok_i, msg_i = extract_first_frame(
                        in_path, out_path_i, overwrite=overwrite, quality=quality, fmt=fmt, seek=s
                    )
                    messages.append(msg_i)

    return messages


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Traverse a directory and extract the first frame of all video files "
            "into a 'cover' subdirectory (mirrors structure)."
        )
    )
    parser.add_argument("directory", help="Base directory to scan for video files")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing cover images")
    # Default behavior: non-recursive. Use --recursive to enable scanning subdirectories.
    parser.add_argument("--no-recursive", action="store_true", help="Do not scan subdirectories (default)")
    parser.add_argument("--recursive", action="store_true", help="Scan subdirectories (enable recursion)")
    parser.add_argument("--format", choices=["jpg", "png"], default="jpg", help="Output image format")
    parser.add_argument(
        "--quality",
        type=int,
        default=2,
        help="JPEG quality 1-31 (lower is better); for PNG maps to compression level",
    )
    parser.add_argument(
        "--seek",
        type=float,
        default=0.2,
        help="Seek seconds before first frame (e.g., 0.2 to avoid black frames)",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=1,
        help=(
            "Number of frames to extract per video. First frame uses the same method as before; "
            "additional frames are captured at random timestamps. Naming: name.ext, name_2.ext, ..."
        ),
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    """CLI entry point: process args and run extraction."""
    args = parse_args(argv)
    base_dir = args.directory
    # Default non-recursive; --recursive enables recursion. --no-recursive keeps it off.
    if getattr(args, "recursive", False):
        recursive = True
    elif getattr(args, "no_recursive", False):
        recursive = False
    else:
        recursive = False
    overwrite = args.overwrite

    print(f"Scanning: {os.path.abspath(base_dir)}")
    print(f"Recursive: {recursive} | Overwrite: {overwrite}")

    messages = scan_and_extract(
        base_dir,
        overwrite=overwrite,
        recursive=recursive,
        quality=args.quality,
        fmt=args.format,
        seek=args.seek,
        frames=args.frames,
    )
    for m in messages:
        print(m)

    # Indicate success if at least one file processed or no errors
    has_error = any(m.lower().startswith("ffmpeg error") or m.lower().startswith("not a directory") for m in messages)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))