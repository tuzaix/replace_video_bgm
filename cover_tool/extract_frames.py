import os
import sys
import argparse
import subprocess
import shutil
from typing import Optional
from typing import List, Tuple
from typing import Optional
import cv2
import numpy as np
import re
import hashlib
import secrets
import time

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


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """Return a Windows-safe filename (without extension).

    - Strips leading/trailing whitespace.
    - Replaces Windows-invalid characters `<>:"/\\|?*` with `_`.
    - Removes trailing dots/spaces (Windows restriction).
    - Truncates to `max_len` characters and appends an 8-char hash suffix for uniqueness.
    """
    # Normalize whitespace and remove invalid characters
    cleaned = name.strip()
    cleaned = re.sub(r'[<>:"/\\|?*]+', '_', cleaned)
    cleaned = cleaned.rstrip(' .')

    if max_len > 0 and len(cleaned) > max_len:
        suffix = hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]
        base = cleaned[: max(1, max_len - (len(suffix) + 2))]
        cleaned = f"{base}__{suffix}"

    return cleaned


def generate_unique_random_name(parent_dir: str, ext: str, length: int = 12) -> str:
    """Generate a numeric random basename unique within `parent_dir`.

    - Uses cryptographically secure randomness (secrets) to build a digits-only name.
    - Checks existence and retries up to a bounded number of attempts.
    - Falls back to millisecond timestamp if uniqueness cannot be ensured quickly.
    """
    digits = "0123456789"
    max_attempts = 50
    for _ in range(max_attempts):
        name = "".join(secrets.choice(digits) for _ in range(length))
        if not os.path.exists(os.path.join(parent_dir, f"{name}.{ext}")):
            return name
    # Fallback: timestamp-based name (still numeric)
    return str(int(time.time() * 1000))


def build_output_path(base_dir: str, cover_dir: str, dirpath: str, filename: str, fmt: str = "jpg") -> str:
    """Build output JPG path under `cover_dir`, mirroring the input directory structure.

    - Mirrors subdirectories under `cover_dir` to avoid filename collisions for same basenames.
    - Changes extension to `.jpg`.
    """
    rel = os.path.relpath(dirpath, start=base_dir)
    out_dir = os.path.join(cover_dir, rel) if rel != "." else cover_dir
    ensure_dir(out_dir)
    name, _ = os.path.splitext(filename)
    name = sanitize_filename(name)
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


def probe_video_duration_seconds(video_path: str) -> Optional[float]:
    """Probe video duration (seconds) using OpenCV first, then ffprobe as fallback.

    - Tries to open via OpenCV and compute duration as frame_count / fps.
    - If OpenCV yields invalid values, falls back to ffprobe query.

    Returns:
        Duration in seconds on success, or None if unable to determine.
    """
    try:
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
            cap.release()
            if fps > 0 and frame_count > 0:
                return frame_count / fps
    except Exception:
        # Ignore and try ffprobe
        pass

    # Fallback to ffprobe
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


# 旧的 ffmpeg 抽帧逻辑已移除，模块现仅使用 OpenCV 进行清晰度分析。


def compute_sharpest_frame_cv(
    video_path: str,
    start_time_sec: float,
    end_time_sec: float,
) -> Tuple[bool, Optional[np.ndarray], str, float, int]:
    """Compute the sharpest frame within [start_time_sec, end_time_sec] using OpenCV.

    This uses Laplacian variance on grayscale frames as the sharpness score.

    Args:
        video_path: Path to the input video.
        start_time_sec: Start time in seconds to begin analysis.
        end_time_sec: End time in seconds to stop analysis (inclusive).

    Returns:
        (ok, best_frame, msg, best_score, best_frame_num)
        - ok: True if a frame was found; False on error or none found.
        - best_frame: The best frame as a numpy array (BGR), or None if not found.
        - msg: Information or error message.
        - best_score: The Laplacian variance score of the best frame (0.0 if not found).
        - best_frame_num: The frame index of the best frame (-1 if not found).
    """
    if end_time_sec <= start_time_sec:
        return False, None, "Invalid time window: end_time_sec must be greater than start_time_sec", 0.0, -1

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return False, None, f"无法打开视频文件: {video_path}", 0.0, -1

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 25.0  # Fallback FPS

    start_frame = int(max(0, start_time_sec) * fps)
    end_frame = int(max(start_time_sec, end_time_sec) * fps)

    # Clamp to valid range using CAP_PROP_FRAME_COUNT if available
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total_frames > 0:
        end_frame = min(end_frame, total_frames - 1)

    # 采样优化：每秒仅分析 ~2 帧，跳读减少解码和计算负担
    target_analyze_fps = 2.0
    sample_every = max(1, int(round(fps / target_analyze_fps)))

    best_frame: Optional[np.ndarray] = None
    best_frame_score: float = -1.0
    best_frame_num: int = -1

    for pos in range(start_frame, end_frame + 1, sample_every):
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        except Exception:
            # 如果跳读失败，保持连续读取
            pass
        success, frame = cap.read()
        if not success or frame is None:
            continue

        # 中心裁剪 + 下采样以提升速度，同时保持与清晰度相关的边缘信息
        h, w = frame.shape[:2]
        crop_ratio = 0.6
        cw, ch = int(w * crop_ratio), int(h * crop_ratio)
        x0 = max(0, (w - cw) // 2)
        y0 = max(0, (h - ch) // 2)
        roi = frame[y0:y0 + ch, x0:x0 + cw]

        # 将较大分辨率缩放到最大边 640 像素以内
        max_side = 640
        if max(cw, ch) > max_side:
            scale = max_side / float(max(cw, ch))
            roi = cv2.resize(roi, (int(cw * scale), int(ch * scale)), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # 更轻量的拉普拉斯与方差估计
        lap16 = cv2.Laplacian(gray, cv2.CV_16S, ksize=3)
        mean, stddev = cv2.meanStdDev(lap16)
        variance = float((stddev[0][0]) ** 2)

        if variance > best_frame_score:
            best_frame_score = variance
            best_frame = frame
            best_frame_num = pos

    cap.release()

    if best_frame is None:
        return False, None, "未能分析到任何帧", 0.0, -1

    return True, best_frame, "Sharpest frame computed", best_frame_score, best_frame_num


def compute_sharpest_frame_cv_gpu(
    video_path: str,
) -> Tuple[bool, Optional[np.ndarray], str, float, int]:
    """Compute the sharpest frame using GPU-first decode via OpenCV CUDA, fallback to CPU if unavailable.

    - Uses `cv2.cudacodec.createVideoReader` for GPU decode when available.
    - Computes sharpness score via Laplacian variance.
    - Optimizations:
      * Pre-creates and reuses CUDA Laplacian filter to avoid per-frame setup.
      * Dynamically adjusts sampling stride: 1080p+ -> stride=3, else stride=2.
      * Downscales center ROI to max-side 512 to reduce transfer and compute.
      * Downloads only small Laplacian result when computing variance on CPU.

    Returns:
        (ok, best_frame, msg, best_score, best_frame_num)
    """
    try:
        # Check CUDA availability
        has_cuda = hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0
        has_cudacodec = has_cuda and hasattr(cv2, "cudacodec") and hasattr(cv2.cudacodec, "createVideoReader")
    except Exception:
        has_cuda = False
        has_cudacodec = False

    if not has_cudacodec:
        return False, None, "CUDA codec not available", 0.0, -1

    try:
        reader = cv2.cudacodec.createVideoReader(video_path)
    except Exception as e:
        return False, None, f"GPU reader init failed: {e}", 0.0, -1

    best_frame: Optional[np.ndarray] = None
    best_frame_score: float = -1.0
    best_frame_num: int = -1
    frame_idx = 0

    # 预创建 GPU Laplacian 滤镜以减少每帧构建开销
    lap_filter = None

    def _next_gpu_frame(rd):
        # Try different APIs for compatibility across builds
        try:
            if hasattr(rd, "nextFrame"):
                ok, gpu_mat = rd.nextFrame()
                return ok, gpu_mat
            if hasattr(rd, "read"):
                ok, gpu_mat = rd.read()
                return ok, gpu_mat
        except Exception:
            pass
        return False, None

    try:
        sample_every = 2  # 初始采样步长
        dynamic_stride_set = False

        while True:
            ok, gpu_mat = _next_gpu_frame(reader)
            if not ok or gpu_mat is None:
                break

            # 根据第一帧分辨率动态设置采样步长：1080p 及以上用 3，否则 2
            if not dynamic_stride_set:
                try:
                    rows = gpu_mat.rows if hasattr(gpu_mat, "rows") else gpu_mat.size()[0]
                    cols = gpu_mat.cols if hasattr(gpu_mat, "cols") else gpu_mat.size()[1]
                    if (rows * cols) >= (1920 * 1080):
                        sample_every = 3
                    else:
                        sample_every = 2
                except Exception:
                    sample_every = 2
                dynamic_stride_set = True

            # 跳过未采样帧仅做解码推进
            if (frame_idx % sample_every) != 0:
                frame_idx += 1
                continue

            # 中心裁剪并尝试在 GPU 上缩放与计算
            variance = -1.0
            try:
                rows = gpu_mat.rows if hasattr(gpu_mat, "rows") else gpu_mat.size()[0]
                cols = gpu_mat.cols if hasattr(gpu_mat, "cols") else gpu_mat.size()[1]

                crop_ratio = 0.6
                cw, ch = int(cols * crop_ratio), int(rows * crop_ratio)
                x0 = max(0, (cols - cw) // 2)
                y0 = max(0, (rows - ch) // 2)

                roi = gpu_mat.rowRange(y0, y0 + ch).colRange(x0, x0 + cw)
                # 下采样到最大边 512，减少下载与计算量
                max_side = 512
                max_dim = max(cw, ch)
                if max_dim > max_side and hasattr(cv2, "cuda") and hasattr(cv2.cuda, "resize"):
                    scale = max_side / float(max_dim)
                    new_w, new_h = int(cw * scale), int(ch * scale)
                    roi_small = cv2.cuda.resize(roi, (new_w, new_h))
                else:
                    roi_small = roi

                if hasattr(cv2, "cuda") and hasattr(cv2.cuda, "cvtColor"):
                    gpu_gray = cv2.cuda.cvtColor(roi_small, cv2.COLOR_BGR2GRAY)
                else:
                    gpu_gray = None

                if gpu_gray is not None and hasattr(cv2.cuda, "createLaplacianFilter"):
                    if lap_filter is None:
                        lap_filter = cv2.cuda.createLaplacianFilter(srcType=cv2.CV_8U, dstType=cv2.CV_32F, ksize=3)
                    gpu_lap = lap_filter.apply(gpu_gray)
                    lap_small = gpu_lap.download()
                    variance = float(lap_small.var())
                else:
                    # CPU 计算方差（小尺寸，下载开销可接受）
                    small = roi_small.download()
                    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                    lap16 = cv2.Laplacian(gray, cv2.CV_16S, ksize=3)
                    mean, stddev = cv2.meanStdDev(lap16)
                    variance = float((stddev[0][0]) ** 2)
            except Exception:
                # 回退：下载整帧并用 CPU 计算（最后兜底）
                try:
                    frame = gpu_mat.download()
                    h, w = frame.shape[:2]
                    crop_ratio = 0.6
                    cw, ch = int(w * crop_ratio), int(h * crop_ratio)
                    x0 = max(0, (w - cw) // 2)
                    y0 = max(0, (h - ch) // 2)
                    roi = frame[y0:y0 + ch, x0:x0 + cw]
                    max_side = 640
                    if max(cw, ch) > max_side:
                        scale = max_side / float(max(cw, ch))
                    roi = cv2.resize(roi, (int(cw * scale), int(ch * scale)), interpolation=cv2.INTER_AREA)
                    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                    lap16 = cv2.Laplacian(gray, cv2.CV_16S, ksize=3)
                    mean, stddev = cv2.meanStdDev(lap16)
                    variance = float((stddev[0][0]) ** 2)
                except Exception:
                    variance = -1.0

            if variance > best_frame_score:
                best_frame_score = variance
                # 保留 GPU 帧句柄，最后再下载保存，避免反复下载
                try:
                    best_gpu = gpu_mat.clone()
                except Exception:
                    best_gpu = gpu_mat
                best_frame = None
                best_frame_num = frame_idx

            frame_idx += 1
    except Exception as e:
        return False, None, f"GPU decode error: {e}", 0.0, -1

    # 下载最佳帧（如前面未下载）
    if best_frame is None:
        try:
            if 'best_gpu' in locals():
                best_frame = best_gpu.download()  # type: ignore[name-defined]
            else:
                return False, None, "GPU path found no frames", 0.0, -1
        except Exception:
            return False, None, "GPU path found no frames", 0.0, -1

    return True, best_frame, "Sharpest frame computed via GPU", best_frame_score, best_frame_num

def save_frame_cv(
    frame: np.ndarray,
    output_path: str,
    fmt: str = "jpg",
    quality: int = 2,
) -> Tuple[bool, str]:
    """Save a BGR frame using OpenCV imwrite with format-specific options.

    Args:
        frame: Numpy array (BGR) of the image to save.
        output_path: Target path, extension should match fmt.
        fmt: Output format, "jpg" or "png".
        quality: Existing script quality scale (1-31, lower is better).

    Returns:
        (ok, msg): True on success and message; False on error.
    """
    ext = fmt.lower()
    params: List[int] = []
    if ext in ("jpg", "jpeg"):
        # Map 1-31 to 100..60 (lower quality number => higher JPEG quality)
        q = max(1, min(31, quality))
        jpeg_q = max(60, min(100, 100 - (q - 1) * 2))
        params = [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_q)]
    elif ext == "png":
        # Map 1-31 to compression 0..9 inversely
        comp = int(max(0, min(9, round((31 - max(1, min(31, quality))) / 31 * 9))))
        params = [cv2.IMWRITE_PNG_COMPRESSION, comp]

    # First attempt: direct OpenCV write
    try:
        ok = cv2.imwrite(output_path, frame, params) if params else cv2.imwrite(output_path, frame)
        if ok:
            return True, f"Saved: {output_path}"
    except Exception:
        pass

    # Fallback: encode to memory and write via Python I/O (handles Unicode and long paths better)
    try:
        dot_ext = ext if ext in ("jpg", "jpeg") else "png" if ext == "png" else ext
        encode_ext = f".{dot_ext}"
        result, buf = cv2.imencode(encode_ext, frame, params) if params else cv2.imencode(encode_ext, frame)
        if not result:
            return False, f"Failed to encode image for: {output_path}"

        # Ensure directory exists
        try:
            ensure_dir(os.path.dirname(output_path))
        except Exception:
            pass

        # Windows extended path prefix for long paths
        target_path = os.path.abspath(output_path)
        if os.name == "nt":
            # Use extended-length path if near limit
            if len(target_path) >= 240 and not target_path.startswith("\\\\?\\"):
                target_path = "\\\\?\\" + target_path

        with open(target_path, "wb") as f:
            f.write(buf.tobytes())
        return True, f"Saved: {output_path}"
    except Exception as e:
        return False, f"Failed to save image: {output_path}"


# 旧的随机/均匀抽帧辅助函数已移除。


# 旧的均匀时间点计算已移除。


def scan_and_extract(
    base_dir: str,
    overwrite: bool = False,
    recursive: bool = False,
    quality: int = 2,
    fmt: str = "png",
) -> List[str]:
    """Traverse `base_dir` and extract frames for all videos to `cover`.

    - Mirrors input directory structure under `cover` to avoid collisions.
    - Skips files already extracted unless `overwrite=True`.
    - 使用 OpenCV 对视频全片进行清晰度分析（拉普拉斯方差），仅保存最清晰的帧。
    - 默认分析窗口：从 0 秒到视频总时长；若无法探测时长则回退为 0~5 秒。

    Returns:
        A list of status messages for each processed file.
    """
    base_dir = os.path.abspath(base_dir)
    if not os.path.isdir(base_dir):
        return [f"Not a directory: {base_dir}"]

    cover_dir = os.path.join(base_dir, "截图")
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
            # 随机数字命名，避免非法字符与路径过长问题，同时保证目录内唯一性
            ext = "png" if fmt.lower() == "png" else "jpg"
            safe_name = generate_unique_random_name(out_parent_dir, ext, length=12)

            # GPU 优先：尝试使用 CUDA 解码整片并计算最清晰帧
            ok_best, best_img, info_msg, best_score, best_num = compute_sharpest_frame_cv_gpu(in_path)
            if not ok_best:
                # CPU 兜底：计算分析窗口（0 到视频时长；不可探测则 0~5 秒）
                start_sec = 0.0
                dur = probe_video_duration_seconds(in_path)
                if dur is not None and dur > start_sec:
                    window_end = dur
                else:
                    window_end = start_sec + 5.0
                ok_best, best_img, info_msg, best_score, best_num = compute_sharpest_frame_cv(
                    in_path,
                    start_time_sec=0.0,
                    end_time_sec=window_end,
                )
            if ok_best and best_img is not None:
                out_path = os.path.join(out_parent_dir, f"{safe_name}.{ext}")
                if (not overwrite) and os.path.exists(out_path):
                    messages.append(f"Skip existing: {out_path}")
                else:
                    ok_save, msg_save = save_frame_cv(best_img, out_path, fmt=fmt, quality=quality)
                    messages.append(msg_save)
                    messages.append(f"Best frame score={best_score:.2f} @ frame={best_num}")
                    # 标注采用的路径（GPU/CPU）以便调试
                    messages.append("Path: GPU" if "GPU" in info_msg else "Path: CPU")
            else:
                messages.append(f"CV best-frame failed: {info_msg}")

    return messages


def prune_resolution_dirs(
    cover_dir: str,
    min_files: int = 20,
    top_n: int = 2,
    dry_run: bool = False,
) -> Tuple[List[Tuple[str, int]], List[Tuple[str, int]]]:
    """Prune resolution subdirectories under `cover_dir` based on image counts.

    - Counts images recursively within each resolution directory (e.g., "1080x1920").
    - Keeps only the top `top_n` directories where image count > `min_files`.
    - Deletes all other resolution directories.

    Args:
        cover_dir: Root directory containing resolution subfolders (e.g., base/截图).
        min_files: Minimum number of images required to be considered.
        top_n: Number of top directories to keep by image count.
        dry_run: If True, only prints actions and returns results without deleting.

    Returns:
        (kept, removed): Two lists of (dir_path, count) tuples indicating kept and removed dirs.
    """
    if not os.path.isdir(cover_dir):
        return [], []

    # Identify resolution directories (direct children of cover_dir)
    candidates: List[str] = []
    for name in os.listdir(cover_dir):
        full = os.path.join(cover_dir, name)
        if os.path.isdir(full):
            candidates.append(full)

    # Count images recursively within each candidate directory
    def is_image(fname: str) -> bool:
        ext = os.path.splitext(fname)[1].lower()
        return ext in {".jpg", ".jpeg", ".png"}

    counts: List[Tuple[str, int]] = []
    for d in candidates:
        total = 0
        for dirpath, dirnames, filenames in os.walk(d):
            total += sum(1 for f in filenames if is_image(f))
        counts.append((d, total))

    # Filter by min_files and sort descending by count
    eligible = [(d, c) for (d, c) in counts if c > min_files]
    eligible.sort(key=lambda x: x[1], reverse=True)

    kept = eligible[:top_n]
    kept_paths = {d for (d, _) in kept}

    removed: List[Tuple[str, int]] = []
    for (d, c) in counts:
        if d not in kept_paths:
            removed.append((d, c))

    # Perform deletions
    if not dry_run:
        for (d, c) in removed:
            try:
                shutil.rmtree(d, ignore_errors=False)
            except Exception:
                # Best-effort delete; continue
                pass

    return kept, removed


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command-line arguments.

    仅使用 OpenCV 清晰度分析逻辑，不再支持 ffmpeg 抽帧参数。
    """
    parser = argparse.ArgumentParser(
        description=(
            "Traverse a directory and extract the sharpest frame of all video files "
            "into a 'cover' subdirectory (mirrors structure)."
        )
    )
    parser.add_argument("directory", help="Base directory to scan for video files")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing cover images")
    # Default behavior: non-recursive. Use --recursive to enable scanning subdirectories.
    parser.add_argument("--no-recursive", action="store_true", help="Do not scan subdirectories (default)")
    parser.add_argument("--recursive", action="store_true", help="Scan subdirectories (enable recursion)")
    parser.add_argument("--format", choices=["jpg", "png"], default="png", help="Output image format (default: png)")
    parser.add_argument(
        "--quality",
        type=int,
        default=2,
        help="JPEG quality 1-31 (lower is better); for PNG maps to compression level",
    )
    # Prune 控制参数
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help=(
            "Do not prune resolution directories after extraction. By default, only keep top2 "
            "resolutions with more than 20 images and delete others."
        ),
    )
    # Prune resolution directories options
    parser.add_argument(
        "--prune-min-files",
        type=int,
        default=20,
        help="Minimum images in a resolution directory to be eligible for keeping (default: 20)",
    )
    parser.add_argument(
        "--prune-top-n",
        type=int,
        default=2,
        help="Number of top resolution directories to keep by image count (default: 2)",
    )
    parser.add_argument(
        "--prune-dry-run",
        action="store_true",
        help="Show prune plan without deleting directories",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    """CLI entry point: process args and run extraction.

    仅使用 OpenCV 清晰度分析逻辑。
    """
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
    )
    for m in messages:
        print(m)

    # Prune resolution directories based on counts unless disabled
    if not getattr(args, "no_prune", False):
        cover_dir = os.path.join(os.path.abspath(base_dir), "截图")
        kept, removed = prune_resolution_dirs(
            cover_dir,
            min_files=getattr(args, "prune_min_files", 20),
            top_n=getattr(args, "prune_top_n", 2),
            dry_run=getattr(args, "prune_dry_run", False),
        )
        if kept or removed:
            print("Prune summary:")
            if kept:
                for d, c in kept:
                    print(f"  Kept: {d} (images={c})")
            if removed:
                for d, c in removed:
                    print(f"  Removed: {d} (images={c})")
        else:
            print("Prune summary: no resolution directories found.")

    # Indicate success if at least one file processed or no CV errors
    has_error = any(
        m.lower().startswith("cv best-frame failed") or m.lower().startswith("not a directory") for m in messages
    )
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))