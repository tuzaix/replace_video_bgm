import argparse
import json

from .calcu_video_info import get_resolution_topn


def main() -> None:
    """测试 CLI：统计素材分辨率并输出数量最多的 TopN。"""
    parser = argparse.ArgumentParser(
        description="计算目录下视频/图片分辨率分布并返回数量最多的 TopN",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("dir_path", type=str, help="素材目录路径")
    parser.add_argument("-n", "--top-n", dest="top_n", type=int, default=1, help="返回的分辨率组数量（默认 1）")
    parser.add_argument("-t", "--type", dest="media_type", choices=["all", "video", "image"], default="all", help="素材类型过滤")
    parser.add_argument("-r", "--recursive", dest="recursive", action="store_true", help="递归子目录")

    args = parser.parse_args()

    res = get_resolution_topn(args.dir_path, args.top_n, args.media_type, args.recursive)
    print(json.dumps({"dir": args.dir_path, "top_n": args.top_n, "type": args.media_type, "recursive": args.recursive, "result": res}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
