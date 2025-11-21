import argparse

from .beats_checkpoint import beats_checkpoint


def main() -> None:
    """命令行入口：生成音频卡点与可视化 JSON。"""
    parser = argparse.ArgumentParser(
        description="使用 Demucs+Librosa 在鼓点上检测卡点并生成可视化元数据。",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("audio_path", type=str, help="输入音频文件路径")
    parser.add_argument("-o", "--output_dir", dest="output_dir", type=str, default=None, help="输出目录（默认：<音频目录>/beats_meta）")
    parser.add_argument("-t", "--temp_dir", dest="temp_dir", type=str, default=None, help="临时目录（默认：<音频目录>/temp）")

    parser.add_argument(
        "--mode",
        dest="mode",
        default="default",
        choices=["default", "fast", "slow", "dynamic"],
        help="间隔过滤模式：default/fast/slow/dynamic",
    )
    parser.add_argument(
        "--min-interval",
        dest="min_interval",
        type=float,
        default=None,
        help="自定义最小间隔秒（动态模式除外）",
    )
    parser.add_argument(
        "-d",
        "--device",
        dest="device",
        type=str,
        default="gpu",
        choices=["cpu", "gpu"],
        help="设备选择：cpu/gpu（默认 gpu）",
    )

    args = parser.parse_args()

    print(f"音频文件: {args.audio_path}")
    print(f"输出目录: {args.output_dir or '默认'}")
    print(f"临时目录: {args.temp_dir or '默认'}")
    print(f"模式: {args.mode}")
    print(f"最小间隔: {args.min_interval if args.min_interval is not None else '默认'}")
    print(f"设备: {args.device}")
    print("-" * 30)

    out = beats_checkpoint(
        audio_path=args.audio_path,
        output_dir=args.output_dir,
        temp_dir=args.temp_dir,
        mode=args.mode,
        min_interval=args.min_interval,
        device=args.device,
    )
    if out is None:
        print("生成失败")
    else:
        print(f"已生成: {out}")


if __name__ == "__main__":
    main()