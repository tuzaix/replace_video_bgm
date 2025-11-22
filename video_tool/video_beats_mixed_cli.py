import argparse

import pathlib
from .video_beats_mixed import video_beats_mixed
from utils.calcu_video_info import get_resolution_topn

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
    # 增加clip_min_interval参数
    parser.add_argument(
        "--clip_min_interval",
        dest="clip_min_interval",
        type=float,
        default=None,
        help="最小clip间隔（秒），默认None",
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

    output_dir = str(args.output_dir or pathlib.Path(args.media_dir) / "beats_mixed")

    print(f"音频文件: {args.audio_path}")
    print(f"元数据: {args.beats_meta}")
    print(f"素材目录: {args.media_dir}")
    print(f"输出目录: {output_dir}")
    print(f"窗口: {window or '使用建议窗口'}")
    print(f"最小clip间隔: {args.clip_min_interval}")
    print("-" * 30)

    media_data = get_resolution_topn(args.media_dir, top_n=1)
    media_resolution, media_count, media_files = media_data["resolution"], media_data["count"], media_data["files"]
    if media_files:
        print(f"素材文件中最高分辨率: {media_resolution}，共 {media_count} 个文件")

    out = video_beats_mixed(
        audio_path=args.audio_path,
        beats_meta=args.beats_meta,
        media_files=media_files,
        output_dir=output_dir,
        window=window,
        clip_min_interval=args.clip_min_interval,
    )
    if out is None:
        print("生成失败")
    else:
        print(f"已生成: {out}")


if __name__ == "__main__":
    main()