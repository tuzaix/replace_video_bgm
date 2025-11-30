import argparse
import sys
import traceback

from .video_detect_scenes import VideoDetectScenes
from .scenes_config import SCENE_CONFIGS


def main() -> None:
    """命令行入口：使用 TransNet V2 进行视频镜头分割并保存结果。"""
    parser = argparse.ArgumentParser(
        description="调用 TransNet V2 进行镜头分割，输出 JSON/TXT 结果。",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("video_path", type=str, help="输入视频文件路径")
    parser.add_argument(
        "-o",
        "--output_dir",
        dest="output_dir",
        type=str,
        default=None,
        help="输出目录（默认：视频同目录的 scenes 子目录）",
    )
    parser.add_argument(
        "-d",
        "--device",
        dest="device",
        type=str,
        choices=["auto", "cpu", "cuda"],
        default="auto",
        help="设备选择：auto/cpu/cuda（默认 auto）",
    )
    parser.add_argument(
        "-p",
        "--profile",
        dest="profile",
        type=str,
        choices=list(SCENE_CONFIGS.keys()),
        default="general",
        help="场景配置（默认 general，含智能自适应）",
    )
    parser.add_argument(
        "-t",
        "--threshold",
        dest="threshold",
        type=float,
        default=0.5,
        help="模型切点阈值（若设置 profile 将以场景配置为准）",
    )
    parser.add_argument("--min_duration", dest="min_duration", type=float, default=None)
    parser.add_argument("--similarity", dest="similarity_threshold", type=float, default=None)
    parser.add_argument("--hist_offset", dest="hist_sample_offset", type=int, default=None)
    parser.add_argument("--audio_snap", dest="enable_audio_snap", action="store_true")
    parser.add_argument("--no_audio_snap", dest="enable_audio_snap", action="store_false")
    parser.set_defaults(enable_audio_snap=None)
    parser.add_argument("--snap_tolerance", dest="snap_tolerance", type=float, default=None)
    parser.add_argument("--min_segment", dest="min_segment_sec", type=float, default=None)
    parser.add_argument("--silence_split", dest="enable_silence_split", action="store_true")
    parser.add_argument("--no_silence_split", dest="enable_silence_split", action="store_false")
    parser.set_defaults(enable_silence_split=None)
    parser.add_argument("--window_s", dest="window_s", type=float, default=None)

    args = parser.parse_args()

    print(f"视频文件: {args.video_path}")
    print(f"输出目录: {args.output_dir or '默认（视频同目录的 scenes）'}")
    print(f"设备选择: {args.device}")
    print(f"场景: {args.profile}")
    print(f"阈值: {args.threshold}")
    print("-" * 30)

    try:
        detect_scenes = VideoDetectScenes(device=args.device, threshold=args.threshold)
        saved = detect_scenes.save(
            args.video_path,
            output_dir=args.output_dir,
            profile=args.profile,
            min_duration=args.min_duration,
            similarity_threshold=args.similarity_threshold,
            hist_sample_offset=args.hist_sample_offset,
            enable_audio_snap=args.enable_audio_snap,
            snap_tolerance=args.snap_tolerance,
            min_segment_sec=args.min_segment_sec,
            enable_silence_split=args.enable_silence_split,
            window_s=args.window_s,
        )
        clips_meta = list(saved.get("clips_meta", []))
        print("AI检测完成，前3个镜头：")
        preview_count = min(3, len(clips_meta))
        for i in range(preview_count):
            m = clips_meta[i]
            item = {
                "start_frame": int(m.get("start_frame", 0)),
                "end_frame": int(m.get("end_frame", 0)),
                "start_time": f"{float(m.get('start_time', 0.0)):.2f}s",
                "end_time": f"{float(m.get('end_time', 0.0)):.2f}s",
                "path": str(m.get("path", "")),
            }
            print(item)
    except RuntimeError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        traceback.print_exc()
        print(f"错误：镜头分割执行失败: {e}", file=sys.stderr)
        sys.exit(1)

    print("已保存镜头分割结果：")
    print(f"  - JSON: {saved.get('json_path')}")
    print(f"  - TXT: {saved.get('txt_path')}")
    clips = saved.get("clips") or []
    print(f"  - Clips: {len(clips)} 个")
    if clips:
        print(f"  - Clips 目录: {saved.get('output_dir')}")


if __name__ == "__main__":
    main()
