import argparse
import sys
import traceback

from .video_detect_scenes import VideoDetectScenes, save_scenes_results


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

    args = parser.parse_args()

    print(f"视频文件: {args.video_path}")
    print(f"输出目录: {args.output_dir or '默认（视频同目录的 scenes）'}")
    print("-" * 30)

    try:
        detect_scenes = VideoDetectScenes(device=args.device)
        saved = detect_scenes.save(args.video_path, output_dir=args.output_dir)
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