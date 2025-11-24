import argparse
from .video_normalize import VideoNormalize


def main() -> int:
    parser = argparse.ArgumentParser(description="保持原分辨率的视频归一化处理")
    parser.add_argument("video", type=str, help="输入视频路径")
    parser.add_argument("--mode", "-m", type=str, choices=["standard", "high", "lite"], default="standard", help="处理模式")
    args = parser.parse_args()

    vn = VideoNormalize(mode=args.mode)
    out = vn.normalize(args.video, mode=args.mode)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

