import argparse

from .video_beats_mixed import video_beats_mixed


def main() -> None:
    """命令行入口：根据卡点元数据与素材合成卡点视频。"""
    parser = argparse.ArgumentParser(
        description="解析卡点元数据并在指定窗口内使用视频/图片素材合成卡点视频。",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("audio_path", type=str, help="背景音乐音频文件路径")
    parser.add_argument("beats_meta", type=str, help="卡点元数据文件路径（JSON）")
    parser.add_argument("media_dir", type=str, help="视频/图片素材所在目录")
    parser.add_argument("-o", "--output_dir", dest="output_dir", type=str, default=None, help="输出目录（默认：素材目录下 beats_mixed）")
    parser.add_argument(
        "--window",
        dest="window",
        type=str,
        default=None,
        help="卡点窗口，格式 'start,end'（秒）。为空则使用元数据建议窗口",
    )

    args = parser.parse_args()

    window = None
    if args.window:
        try:
            parts = [float(x.strip()) for x in str(args.window).split(",")]
            if len(parts) == 2 and parts[1] > parts[0]:
                window = (parts[0], parts[1])
        except Exception:
            window = None

    print(f"音频文件: {args.audio_path}")
    print(f"元数据: {args.beats_meta}")
    print(f"素材目录: {args.media_dir}")
    print(f"输出目录: {args.output_dir or '默认'}")
    print(f"窗口: {window or '使用建议窗口'}")
    print("-" * 30)

    out = video_beats_mixed(
        audio_path=args.audio_path,
        beats_meta=args.beats_meta,
        media_dir=args.media_dir,
        output_dir=args.output_dir,
        window=window,
    )
    if out is None:
        print("生成失败")
    else:
        print(f"已生成: {out}")


if __name__ == "__main__":
    main()