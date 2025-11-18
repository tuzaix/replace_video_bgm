"""
Standalone CLI for video normalization.

This CLI wraps `concat_tool.normalize_video.normalize_videos` and provides
arguments for source/output directories, threads, and GPU usage.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List

# Ensure project root is on sys.path when running as a script
# This allows imports like `from utils.bootstrap_ffmpeg import ...` to work
# even when invoking `python concat_tool/normalize_cli.py ...`.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
from concat_tool.normalize_video import normalize_videos


def build_parser() -> argparse.ArgumentParser:
    """Build an ArgumentParser for normalize CLI."""
    p = argparse.ArgumentParser(description="Normalize videos for later concatenation")
    p.add_argument("--src", required=True, help="源视频目录（仅当前目录，不递归）")
    p.add_argument("--dst", required=True, help="归一化后的输出目录")
    p.add_argument("--threads", type=int, default=4, help="并发线程数（默认4）")
    p.add_argument("--no-gpu", dest="use_gpu", action="store_false", default=True, help="禁用NVENC，强制使用CPU编码")
    return p


def main(argv: List[str] | None = None) -> int:
    """CLI entry point for normalization.

    Parameters
    ----------
    argv : List[str] | None
        Optional argument list; if None, argparse uses sys.argv.

    Returns
    -------
    int
        Exit code: 0 on success, non-zero on failure.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # Initialize ffmpeg environment consistently with other tools
    try:
        bootstrap_ffmpeg_env(
            prefer_bundled=True,
            dev_fallback_env=True,
            modify_env=True,
            require_ffmpeg=True,
            require_ffprobe=True,
        )
    except FileNotFoundError as e:
        print(f"[fatal] {e}")
        return 2

    try:
        normalize_videos(args.src, args.dst, args.threads, use_gpu=args.use_gpu)
        return 0
    except Exception as e:
        print(f"[fatal] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())