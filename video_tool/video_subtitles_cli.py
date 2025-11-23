import argparse
import sys
import traceback

from .video_subtitles import VideoSubtitles


def main() -> None:
    """命令行入口：生成视频的 SRT 字幕文件。"""
    parser = argparse.ArgumentParser(
        description="使用 faster-whisper 为视频生成 SRT 字幕文件。",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("video_path", type=str, help="输入视频文件路径")
    parser.add_argument("-o", "--output", dest="output", type=str, default=None, help="输出 SRT 路径（默认同目录同名 .srt）")
    parser.add_argument("-m", "--model", dest="model", type=str, default="auto", choices=["auto","tiny","base","small","medium","large-v3"], help="Whisper 模型大小（默认 auto，按硬件自选）")
    parser.add_argument("-d", "--device", dest="device", type=str, default="auto", choices=["auto","cpu","cuda"], help="设备选择：auto/cpu/cuda（默认 auto）")
    parser.add_argument("--model-path", dest="model_path", type=str, default=None, help="本地模型目录（离线环境优先使用该目录）")
    parser.add_argument("--translate", dest="translate", action="store_true", help="直接生成英文字幕")
    parser.add_argument("--line-chars", dest="line_chars", type=int, default=14, help="每行最大字数（不指定则不换行/不拆分）")
    parser.add_argument("--lines-per-caption", dest="lines_per_caption", type=int, default=2, help="每条字幕的最大行数（默认 2）")

    args = parser.parse_args()

    print(f"视频文件: {args.video_path}")
    print(f"输出路径: {args.output or '默认（同目录同名 .srt）'}")
    print(f"模型: {args.model}")
    print(f"设备: {args.device}")
    print(f"本地模型路径: {args.model_path or '未指定'}")
    print(f"翻译模式: {'启用' if args.translate else '关闭'}")
    print(f"每行最大字数: {args.line_chars if args.line_chars is not None else '未限制'}")
    print(f"每条字幕最大行数: {args.lines_per_caption}")
    print("-" * 30)

    try:
        gen = VideoSubtitles(model_size=args.model, device=args.device, model_path=args.model_path)
        try:
            print(f"使用模型目录: {getattr(gen, 'model_path', None) or '在线/缓存'}")
        except Exception:
            pass
        out = gen.save_srt(
            args.video_path,
            args.output,
            translate=args.translate,
            max_chars_per_line=args.line_chars,
            max_lines_per_caption=max(1, int(args.lines_per_caption or 2)),
        )
    except RuntimeError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        traceback.print_exc()
        print(f"错误：生成字幕失败: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"已生成字幕：{out}")


if __name__ == "__main__":
    main()