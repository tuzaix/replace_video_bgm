"""
Command-line interface for simple FFmpeg concatenation.

This CLI wraps concat_tool.concat.VideoConcat, allowing you to provide a
list of video slice files, an output path, optional BGM, quality preset,
and a GPU toggle.

Usage examples (Windows):
- Basic:
  python -m concat_tool.concat_cli --slices a.mp4 b.mp4 c.mp4 --out out.mp4
- With BGM and GPU:
  python -m concat_tool.concat_cli --slices a.mp4 b.mp4 \
    --bgm music.mp3 --quality compact --gpu --out out.mp4
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

try:
    # Support running as a script or as a package module.
    if __package__ is None or __package__ == "":
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from concat_tool.concat import VideoConcat  # type: ignore
        from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env  # type: ignore
    else:
        from .concat import VideoConcat  # type: ignore
        from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env  # type: ignore
    # Initialize FFmpeg environment when available.
    bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffmpeg=False)
except Exception:
    # Non-fatal; the VideoConcat run will surface errors if ffmpeg is missing.
    pass


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the concat CLI."""
    p = argparse.ArgumentParser(description="Concatenate video slices with optional BGM and quality presets")
    p.add_argument(
        "--slices",
        nargs="+",
        required=True,
        help="视频切片列表（按顺序拼接），示例：--slices a.mp4 b.mp4 c.mp4",
    )
    p.add_argument("--out", required=True, help="合成输出视频文件路径，如 out.mp4")
    p.add_argument("--bgm", help="可选：BGM音频文件路径（替换原音频）")
    p.add_argument(
        "--quality",
        choices=["balanced", "compact", "tiny"],
        default="balanced",
        help="质量档位：balanced/compact/tiny（影响视频与音频码率）",
    )
    p.add_argument("--gpu", action="store_true", help="启用NVENC进行视频编码（支持时生效）")
    return p


def parse_paths(paths: List[str]) -> List[Path]:
    """Convert string paths to Path objects, filtering out non-existent files.

    Parameters
    ----------
    paths : List[str]
        Input file paths provided by CLI.

    Returns
    -------
    List[Path]
        Existing file paths as Path objects, in original order.
    """
    out: List[Path] = []
    for s in paths:
        p = Path(s)
        if p.exists() and p.is_file():
            out.append(p)
        else:
            print(f"[warn] 跳过不存在或非文件路径: {s}")
    return out


def main(argv: List[str] | None = None) -> int:
    """Entry point for the concat CLI.

    Steps
    -----
    1) Parse CLI arguments and validate inputs
    2) Construct VideoConcat with provided parameters
    3) Run the concatenation process and report status
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    slices = parse_paths(args.slices)
    if not slices:
        print("[fatal] 切片列表为空或全部无效")
        return 2

    out_path = Path(args.out)
    bgm_path = Path(args.bgm) if args.bgm else None

    vc = VideoConcat(
        slices=slices,
        out_path=out_path,
        bgm_path=bgm_path,
        quality=args.quality,
        use_gpu=args.gpu,
    )
    ok = vc.run()
    if ok:
        print(f"[ok] 输出成功 → {out_path}")
        return 0
    print("[error] 处理失败，请查看上述 ffmpeg 输出以定位问题")
    return 5


if __name__ == "__main__":
    raise SystemExit(main())