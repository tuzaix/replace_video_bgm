"""
Convert WEBM/MKV videos to MP4 (lossless remux) with concurrency.

This CLI scans a specified directory, finds `.webm` and `.mkv` files,
and converts them to `.mp4` using stream copy (`-c copy`) without re-encoding.

Notes
-----
- Lossless remux succeeds only when codecs are compatible with MP4 container.
  For incompatible inputs (e.g., VP9/Opus in WEBM), the conversion will fail
  with `-c copy`. This tool reports failures but does not re-encode.
- Concurrency is supported via ThreadPoolExecutor; thread count is configurable.

Usage
-----
python -m video_tool.convert_video2mp4_cli <dir> --threads 4 --out-dir <out>
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import argparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not getattr(sys, "frozen", False):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

from utils.calcu_video_info import ffmpeg_bin


def _popen_silent_kwargs() -> dict:
    """Return subprocess kwargs that suppress console windows on Windows.

    Returns
    -------
    dict
        Keyword arguments suitable for `subprocess.run` to hide windows.
    """
    try:
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            return {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
    except Exception:
        pass
    return {}


def list_target_videos(dir_path: Path) -> List[Path]:
    """List `.webm` and `.mkv` files directly under the given directory.

    Parameters
    ----------
    dir_path : Path
        Directory to scan (non-recursive).

    Returns
    -------
    List[Path]
        Absolute paths to candidate videos.
    """
    exts = {".webm", ".mkv"}
    out: List[Path] = []
    try:
        for name in os.listdir(dir_path):
            p = dir_path / name
            if p.is_file() and p.suffix.lower() in exts:
                out.append(p)
    except Exception:
        pass
    return out


def build_ffmpeg_copy_cmd(ffmpeg_path: str, input_path: Path, output_path: Path) -> List[str]:
    """Build an ffmpeg command that remuxes to MP4 using stream copy.

    Parameters
    ----------
    ffmpeg_path : str
        ffmpeg executable path.
    input_path : Path
        Source video file (.webm or .mkv).
    output_path : Path
        Target .mp4 file.

    Returns
    -------
    List[str]
        The ffmpeg command arguments.
    """
    return [
        ffmpeg_path,
        "-y",
        "-i",
        str(input_path),
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        "-c",
        "copy",
        str(output_path),
    ]


def convert_one(ffmpeg_path: str, in_path: Path, out_dir: Path) -> Tuple[bool, Path | None, str | None]:
    """Convert a single video to MP4 via stream copy.

    Parameters
    ----------
    ffmpeg_path : str
        ffmpeg executable path.
    in_path : Path
        Input video path (.webm or .mkv).
    out_dir : Path
        Output directory.

    Returns
    -------
    Tuple[bool, Path | None, str | None]
        (success, output_path_or_none, error_message_or_none)
    """
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    out_path = out_dir / f"{in_path.stem}.mp4"
    if out_path.exists():
        return True, out_path, None
    cmd = build_ffmpeg_copy_cmd(ffmpeg_path, in_path, out_path)
    kwargs = _popen_silent_kwargs()
    try:
        proc = subprocess.run(cmd, capture_output=True, **kwargs)
        if proc.returncode == 0 and out_path.exists():
            return True, out_path, None
        # Prefer decoding std err safely
        err = ""
        try:
            err = (proc.stderr or b"").decode("utf-8", errors="ignore")
        except Exception:
            try:
                err = (proc.stderr or b"").decode("mbcs", errors="ignore")
            except Exception:
                err = ""
        return False, None, err
    except Exception as e:
        return False, None, str(e)


def main() -> None:
    """CLI entry: convert WEBM/MKV files in a directory to MP4 concurrently.

    Arguments
    ---------
    dir : str
        Input directory containing `.webm` / `.mkv` files (non-recursive).
    --out-dir : str, optional
        Output directory (default: `<dir>/mp4_converted`).
    --threads : int, optional
        Concurrent workers (default: 4).
    """
    parser = argparse.ArgumentParser(
        description=(
            "无损转换（容器封装）：将目录下所有 .webm/.mkv 视频通过 -c copy 重封装为 .mp4\n"
            "注意：若输入编码与 MP4 容器不兼容，转换会失败（不重编码）。"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("dir", type=str, help="输入目录（非递归）")
    parser.add_argument("--out-dir", dest="out_dir", type=str, default=None, help="输出目录（默认 <dir>/mp4_converted）")
    parser.add_argument("--threads", dest="threads", type=int, default=4, help="并发线程数（默认 4）")
    args = parser.parse_args()

    src_dir = Path(args.dir).resolve()
    if not src_dir.exists() or not src_dir.is_dir():
        print(f"错误：目录不存在 -> {src_dir}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else (src_dir / "mp4_converted")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    videos = list_target_videos(src_dir)
    if not videos:
        print("提示：未找到 .webm/.mkv 文件，已退出。")
        sys.exit(0)

    print(f"ffmpeg: {ffmpeg_bin}")
    print(f"输入目录: {src_dir}")
    print(f"输出目录: {out_dir}")
    print(f"文件数量: {len(videos)}")
    print(f"并发线程: {max(1, int(args.threads))}")
    print("-" * 32)

    successes = 0
    failures: List[Tuple[Path, str]] = []

    def task(p: Path) -> Tuple[bool, Path | None, str | None]:
        return convert_one(ffmpeg_bin, p, out_dir)

    try:
        with ThreadPoolExecutor(max_workers=max(1, int(args.threads))) as ex:
            futures = {ex.submit(task, p): p for p in videos}
            for fut in as_completed(futures):
                src = futures[fut]
                ok, outp, err = fut.result()
                if ok and outp is not None:
                    successes += 1
                    print(f"✅ {src.name} -> {outp.name}")
                else:
                    msg = (err or "未知错误").strip()
                    failures.append((src, msg))
                    print(f"❌ 失败 {src.name}: {msg[:500]}...")
    except KeyboardInterrupt:
        print("中断：已停止并发处理")
    except Exception as e:
        print(f"错误：并发处理失败: {e}", file=sys.stderr)

    print("-" * 32)
    print(f"成功: {successes} / {len(videos)}")
    if failures:
        print(f"失败: {len(failures)}")
        for src, msg in failures[:10]:
            print(f" - {src.name}: {msg[:200]}...")


if __name__ == "__main__":
    main()
