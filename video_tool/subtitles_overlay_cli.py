from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

try:
    if __package__ is None or __package__ == "":
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from video_tool.subtitles_overlay import overlay_ass_subtitles  # type: ignore
        from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env  # type: ignore
    else:
        from .subtitles_overlay import overlay_ass_subtitles  # type: ignore
        from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env  # type: ignore
    bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffmpeg=False)
except Exception:
    pass

def main(argv: Optional[list[str]] = None) -> int:
    """ASS 字幕叠加调试 CLI。

    解析命令行参数，调用 `overlay_ass_subtitles` 将 ASS 字幕叠加到指定视频，
    并在同目录生成带字幕的 `*_sub.mp4` 或使用指定的 `--out` 输出路径。
    返回 0 表示成功，非 0 表示失败。
    """
    parser = argparse.ArgumentParser(description="Overlay ASS subtitles onto a video (debug CLI)")
    parser.add_argument("--video", required=True, help="Input video path")
    parser.add_argument("--ass", required=True, help="ASS subtitle file path")
    parser.add_argument("--out", required=False, default=None, help="Output video path (default: <name>_sub.mp4)")
    parser.add_argument("--gpu", action="store_true", help="Use NVENC for faster encoding")
    parser.add_argument("--crf", type=int, default=23, help="Quality parameter (CRF/CQ)")
    parser.add_argument("--fontsdir", required=False, default=None, help="Optional fonts directory for libass")
    args = parser.parse_args(argv)

    use_nvenc = bool(args.gpu)
    try:
        outp = overlay_ass_subtitles(
            src_path=args.video,
            ass_path=args.ass,
            out_path=args.out,
            use_nvenc=use_nvenc,
            crf=int(args.crf),
            fontsdir=args.fontsdir,
        )
        ok = os.path.isfile(outp) and outp.lower().endswith(('.mp4', '.mkv', '.mov', '.webm'))
        print({
            "phase": "overlay_done",
            "input": args.video,
            "ass": args.ass,
            "out": outp,
            "ok": ok,
        })
        return 0 if ok else 2
    except Exception as e:
        print({"phase": "overlay_error", "error": str(e)})
        return 1

if __name__ == "__main__":
    raise SystemExit(main())

