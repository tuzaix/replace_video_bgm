import os
import argparse
from typing import Optional

from video_tool.broadcast_video_slices import BroadcastVideoSlices


def main() -> None:
    """CLI 入口：直播长视频智能切片。"""
    p = argparse.ArgumentParser(description="直播长视频智能切片：语义/表演两种模式")
    p.add_argument("video", help="输入视频文件路径")
    p.add_argument("out_dir", nargs="?", default=None, help="输出目录，默认视频同名目录")
    p.add_argument("--mode", choices=["speech", "performance"], default="speech", help="切片模式")
    p.add_argument("--model-size", default=None, help="模型大小，如 large-v3/medium/small；默认自动")
    p.add_argument("--device", default="auto", help="运行设备：auto/cuda/cpu")
    p.add_argument("--model-path", default=None, help="模型根目录，需包含 Systran/faster-whisper-<size>")
    # speech params
    p.add_argument("--min-sec", type=int, default=20, help="语义模式：最短片段秒数")
    p.add_argument("--max-sec", type=int, default=60, help="语义模式：最长片段秒数")
    p.add_argument("--language", default="zh", help="语义模式：ASR语言，默认 zh")
    # performance params
    p.add_argument("--target-duration", type=int, default=30, help="表演模式：高潮截取时长秒")
    p.add_argument("--min-silence-len", type=int, default=2000, help="表演模式：最短静音毫秒阈值")
    p.add_argument("--silence-thresh", type=int, default=-40, help="表演模式：静音阈值 dBFS")
    p.add_argument("--min-segment-sec", type=int, default=10, help="表演模式：最短有效段秒")
    p.add_argument("--max-keep-sec", type=int, default=60, help="表演模式：段落不超过则直接保留秒")

    args = p.parse_args()

    model_path: Optional[str] = args.model_path or os.environ.get("WHISPER_MODEL_DIR")
    if not model_path:
        raise SystemExit("未指定模型目录：请使用 --model-path 或设置环境变量 WHISPER_MODEL_DIR")

    outs = BroadcastVideoSlices(
        model_size=args.model_size,
        device=args.device,
        model_path=model_path,
    ).cut_video(
        video_path=args.video,
        output_dir=args.out_dir,
        mode=args.mode,
        min_sec=args.min_sec,
        max_sec=args.max_sec,
        language=args.language,
        target_duration=args.target_duration,
        min_silence_len=args.min_silence_len,
        silence_thresh=args.silence_thresh,
        min_segment_sec=args.min_segment_sec,
        max_keep_sec=args.max_keep_sec,
    )
    print("导出完成：")
    for pth in outs:
        print(pth)


if __name__ == "__main__":
    main()