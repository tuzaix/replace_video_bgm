import os
import argparse
from typing import Optional

from video_tool.broadcast_video_slices import BroadcastVideoSlices


def main() -> None:
    """CLI 入口：直播长视频智能切片（支持传入视频文件或目录）。"""
    p = argparse.ArgumentParser(description="直播长视频智能切片：语义/表演两种模式")
    p.add_argument("input_path", help="输入路径：可为视频文件或视频目录")
    p.add_argument("out_dir", nargs="?", default=None, help="输出根目录：默认在输入所在目录下为每个视频创建同名子目录")
    p.add_argument("--mode", choices=["ecommerce", "game", "entertainment"], default="ecommerce", help="切片模式")
    p.add_argument("--model-size", default="large-v3", help="模型大小，如 large-v3/medium/small；默认自动")
    p.add_argument("--device", default="auto", help="运行设备：auto/cuda/cpu")
    p.add_argument("--model-path", required=True, help="模型根目录，需包含 Systran/faster-whisper-<size>")
    p.add_argument("--vision-model-path", default=None, help="Florence-2 模型本地路径或仓库ID")
    # speech params
    p.add_argument("--min-sec", type=int, default=15, help="语义模式：最短片段秒数")
    p.add_argument("--max-sec", type=int, default=60, help="语义模式：最长片段秒数")
    p.add_argument("--language", default="zh", help="语义模式：ASR语言，默认 zh")
    # performance params
    p.add_argument("--target-duration", type=int, default=30, help="表演模式：高潮截取时长秒")
    p.add_argument("--min-silence-len", type=int, default=1000, help="表演模式：最短静音毫秒阈值")
    p.add_argument("--silence-thresh", type=int, default=-40, help="表演模式：静音阈值 dBFS")
    p.add_argument("--min-segment-sec", type=int, default=5, help="表演模式：最短有效段秒")
    p.add_argument("--max-keep-sec", type=int, default=60, help="表演模式：段落不超过则直接保留秒")

    args = p.parse_args()

    model_path: str = args.model_path
    if not model_path:
        raise SystemExit("未指定模型目录：请使用 --model-path")


    inp = args.input_path
    if os.path.isdir(inp):
        video_dir = inp
        video_files = [
            os.path.join(video_dir, f)
            for f in os.listdir(video_dir)
            if f.lower().endswith((".mp4", ".avi", ".mov", ".mkv"))
        ]
    elif os.path.isfile(inp):
        video_dir = os.path.dirname(os.path.abspath(inp))
        video_files = [inp]
    else:
        raise SystemExit(f"输入路径不可用：{inp}")

    for video_file in video_files:
        base_name = os.path.splitext(os.path.basename(video_file))[0]
        if args.out_dir:
            out_root = args.out_dir
        else:
            out_root = video_dir
        out_dir = os.path.join(out_root, base_name)
        os.makedirs(out_dir, exist_ok=True)
        
        outs = BroadcastVideoSlices(
            model_size=args.model_size,
            device=args.device,
            model_path=model_path,
            vision_model_path_or_id=args.vision_model_path,
        ).cut_video(
            video_path=video_file,
            output_dir=out_dir,
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
