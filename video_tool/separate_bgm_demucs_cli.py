import argparse

# 使用相对导入，因为这个 cli 脚本是 video_tool 包的一部分
from .separate_bgm_demucs import separate_bgm_demucs

def main():
    """
    用于通过命令行调用视频音轨分离功能的函数。
    """
    parser = argparse.ArgumentParser(
        description="使用 Demucs 从视频文件中分离音轨 (人声, 鼓, 贝斯, 其他) 并创建一个无声视频。",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "video_path",
        type=str,
        help="输入视频文件的路径。"
    )

    parser.add_argument(
        "-o", "--output_dir",
        type=str,
        default=None,
        help="输出目录的路径。\n如果未指定，将在视频文件旁边创建一个与视频同名的目录。"
    )

    parser.add_argument(
        "-m", "--model",
        type=str,
        default="htdemucs",
        help="""要使用的 Demucs 模型名称。
可选模型:
- htdemucs (默认): 适用于绝大多数场景。
- htdemucs_ft: 追求极致音质，速度较慢。
- htdemucs_6s: 需要提取吉他或钢琴时使用。
- mdx_extra: 速度快，体积小，但音质可能稍逊。
"""
    )
    parser.add_argument(
        "-d", "--use_device",
        type=str,
        default="cpu",
        help="要使用的设备，'cpu' 或 'gpu'。"
    )

    args = parser.parse_args()

    print(f"视频文件: {args.video_path}")
    print(f"输出目录: {args.output_dir or '默认'}")
    print(f"使用模型: {args.model}")
    print(f"使用设备: {args.use_device}")
    print("-" * 30)

    separate_bgm_demucs(
        video_path=args.video_path,
        output_dir=args.output_dir,
        model=args.model,
        use_device=args.use_device
    )

if __name__ == '__main__':
    # 如何从项目根目录运行此脚本:
    # python -m video_tool.separate_bgm_demucs_cli "path/to/your/video.mp4" -o "output_folder" -m "htdemucs_ft"
    main()