"""
Command-line interface for the video concatenation workflow.

This CLI uses concat_tool.workflow.run_video_concat_workflow with the shared
Settings dataclass, printing phases, progress and logs to the console.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from .settings import Settings
from .workflow import run_video_concat_workflow, WorkflowCallbacks
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env


def _print_log(msg: str) -> None:
    """Print a log message.

    Parameters
    ----------
    msg : str
        The message to print.
    """
    print(msg)


def _print_phase(name: str) -> None:
    """Print phase change information.

    Parameters
    ----------
    name : str
        The new phase name.
    """
    print(f"[phase] {name}")


def _print_progress(done: int, total: int) -> None:
    """Print progress in a simple fixed-scale manner.

    Parameters
    ----------
    done : int
        Completed units.
    total : int
        Total units (fixed to 1000 by the workflow).
    """
    print(f"[progress] {done}/{total}")


def build_parser() -> argparse.ArgumentParser:
    """Build an ArgumentParser for the workflow CLI.

    Returns
    -------
    argparse.ArgumentParser
        Configured parser.
    """
    p = argparse.ArgumentParser(description="Video concat workflow CLI")
    p.add_argument("--video-dirs", nargs="+", required=True, help="输入视频目录，一个或多个")
    p.add_argument("--bgm-path", required=True, help="BGM 文件或目录路径")
    p.add_argument("--output", default=None, help="输出路径（文件或目录）。多目录输入时应为目录")
    p.add_argument("--count", type=int, default=5, help="每个输出随机视频的数量")
    p.add_argument("--outputs", type=int, default=1, help="输出视频数量")
    p.add_argument("--gpu", action="store_true", help="启用 GPU (NVENC) 加速（若可用）")
    p.add_argument("--threads", type=int, default=4, help="工作线程数")
    p.add_argument("--width", type=int, default=1080, help="输出宽度")
    p.add_argument("--height", type=int, default=1920, help="输出高度")
    p.add_argument("--fps", type=int, default=25, help="输出帧率")
    p.add_argument("--fill", choices=["pad", "crop"], default="pad", help="填充模式")
    p.add_argument("--trim-head", type=float, default=0.0, help="每段视频开头裁剪秒数")
    p.add_argument("--trim-tail", type=float, default=1.0, help="每段视频结尾裁剪秒数")
    p.add_argument("--clear-mismatched-cache", action="store_true", help="清理与裁剪参数不匹配的 TS 缓存")
    p.add_argument("--group-res", action="store_true", help="按分辨率分组输出")
    p.add_argument("--quality-profile", choices=["visual", "balanced", "size"], default="balanced", help="编码质量档位")
    p.add_argument("--nvenc-cq", type=int, default=None, help="NVENC CQ 覆盖值")
    p.add_argument("--x265-crf", type=int, default=None, help="x265 CRF 覆盖值")
    p.add_argument("--preset-gpu", choices=["p4", "p5", "p6", "p7"], default=None, help="NVENC 预设")
    p.add_argument("--preset-cpu", choices=[
        "ultrafast", "medium", "slow", "slower", "veryslow"
    ], default=None, help="x265 预设")
    return p


def main(argv: List[str] | None = None) -> int:
    """CLI entry point.

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

    # 统一启动策略：优先使用内置 FFmpeg，并在开发环境允许系统兜底（通过 FFMPEG_DEV_FALLBACK）。
    try:
        bootstrap_ffmpeg_env(
            prefer_bundled=True,
            dev_fallback_env=True,
            modify_env=True,
            logger=lambda m: _print_log(f"[ffmpeg] {m}"),
            require_ffmpeg=True,
            require_ffprobe=True,
        )
    except FileNotFoundError as e:
        print(f"[fatal] {e}")
        return 2

    # Build shared settings
    settings = Settings(
        video_dirs=args.video_dirs,
        bgm_path=args.bgm_path,
        output=args.output,
        count=args.count,
        outputs=args.outputs,
        gpu=bool(args.gpu),
        threads=args.threads,
        width=args.width,
        height=args.height,
        fps=args.fps,
        fill=args.fill,
        trim_head=args.trim_head,
        trim_tail=args.trim_tail,
        clear_mismatched_cache=bool(args.clear_mismatched_cache),
        group_res=bool(args.group_res),
        quality_profile=args.quality_profile,
        nvenc_cq=args.nvenc_cq,
        x265_crf=args.x265_crf,
        preset_gpu=args.preset_gpu,
        preset_cpu=args.preset_cpu,
    )

    # Setup callbacks to print status
    cb = WorkflowCallbacks(
        on_log=_print_log,
        on_phase=_print_phase,
        on_progress=_print_progress,
        on_error=lambda m: print(f"[error] {m}"),
    )

    try:
        success_count, fail_count, success_outputs = run_video_concat_workflow(settings, cb)
        print(f"[done] success={success_count}, failed={fail_count}")
        if success_outputs:
            print("\n[results]")
            for p in success_outputs:
                try:
                    size_mb = Path(p).stat().st_size / (1024 * 1024)
                    print(f"  - {p} ({size_mb:.1f} MB)")
                except Exception:
                    print(f"  - {p}")
        return 0
    except Exception as e:
        print(f"[fatal] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())