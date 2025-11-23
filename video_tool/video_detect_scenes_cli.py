import argparse
import sys
import traceback

from .video_detect_scenes import detect_scenes_transnet, save_scenes_results


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

    args = parser.parse_args()

    print(f"视频文件: {args.video_path}")
    print(f"输出目录: {args.output_dir or '默认（视频同目录的 scenes）'}")
    print("-" * 30)

    try:
        result = detect_scenes_transnet(args.video_path)
        fps = float(result.get("fps", 30.0))
        scenes_frames = list(result.get("scenes_frames", []))
        scenes_seconds = list(result.get("scenes_seconds", []))

        print("AI检测完成，前3个镜头：")
        preview_count = min(3, len(scenes_frames))
        for i in range(preview_count):
            sf, ef = scenes_frames[i]
            if i < len(scenes_seconds):
                s, e = scenes_seconds[i]
            else:
                s, e = (sf / fps), (ef / fps)
            item = {
                "start_frame": sf,
                "end_frame": ef,
                "start_time": f"{s:.2f}s",
                "end_time": f"{e:.2f}s",
            }
            print(item)

        saved = save_scenes_results(args.video_path, args.output_dir, result=result)
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


if __name__ == "__main__":
    main()