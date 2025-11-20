import argparse


from .bgm_replacer import bgm_replacer


def main():
    """
    命令行入口：替换视频背景音乐并导出合成视频。
    """
    parser = argparse.ArgumentParser(
        description="替换视频背景音乐，支持保留原声并调节两者音量。",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "video_path",
        type=str,
        help="输入视频文件路径",
    )

    parser.add_argument(
        "bgm_path",
        type=str,
        help="用于合成的背景音乐音频文件路径",
    )

    parser.add_argument(
        "-o",
        "--output_dir",
        type=str,
        default=None,
        help="输出目录（默认在视频同名目录下输出）",
    )

    parser.add_argument(
        "--keep_original_voice",
        dest="keep_original_voice",
        action="store_true",
        default=True,
        help="保留原声（默认开启）",
    )
    parser.add_argument(
        "--no_keep_original_voice",
        dest="keep_original_voice",
        action="store_false",
        help="不保留原声",
    )

    parser.add_argument(
        "--original_volume",
        type=float,
        default=1,
        help="原声音量系数（默认 1）",
    )

    parser.add_argument(
        "--bgm_volume",
        type=float,
        default=0.1,
        help="BGM 音量系数（默认 0.7）",
    )

    parser.add_argument(
        "-d",
        "--device",
        type=str,
        default="gpu",
        help="设备选择：'gpu' 或 'cpu'（默认 'gpu'）",
    )

    args = parser.parse_args()

    out = bgm_replacer(
        video_path=args.video_path,
        bgm_path=args.bgm_path,
        output_dir=args.output_dir,
        keep_original_voice=args.keep_original_voice,
        original_volume=args.original_volume,
        bgm_volume=args.bgm_volume,
        device=args.device,
    )

    if out is not None:
        print(f"输出: {out}")


if __name__ == "__main__":
    # python -m video_tool.bgm_replacer_cli ./input.mp4 ./bgm.mp3 -o ./out --original_volume 0.9 --bgm_volume 0.7
    main()