import os
import argparse
from typing import Optional

from video_tool.broadcast_video_slices import BroadcastVideoSlices


def main() -> None:
    """CLI 入口：直播长视频智能切片（仅场景化模式与跳剪）。"""
    p = argparse.ArgumentParser(description="直播长视频智能切片：场景化(ecommerce/game/entertainment)与 jumpcut")
    p.add_argument("input_path", help="输入路径：可为视频文件或视频目录")
    p.add_argument("out_dir", nargs="?", default=None, help="输出根目录：默认在输入所在目录下为每个视频创建同名子目录")
    p.add_argument("--mode", choices=["ecommerce", "game", "entertainment", "jumpcut"], default="ecommerce", help="切片模式")
    p.add_argument("--profile", default="ecommerce", help="jumpcut 基于的场景化配置：ecommerce/game/entertainment")
    p.add_argument("--model-size", default="large-v3", help="Whisper 模型大小，如 large-v3/medium/small；默认自动")
    p.add_argument("--device", default="auto", help="运行设备：auto/cuda/cpu")
    p.add_argument("--whisper-model-dir", required=True, help="faster-whisper 本地模型根目录（包含 Systran/faster-whisper-<size>）")
    p.add_argument("--vision-model", default=None, help="Florence-2 模型本地路径或仓库ID")
    p.add_argument("--language", default="zh", help="ASR语言，默认 zh")
    p.add_argument("--use-nvenc", action="store_true", help="使用 NVENC 进行视频编码")
    p.add_argument("--crf", type=int, default=23, help="编码质量参数（CRF/CQ）")
    p.add_argument("--add-subtitles", action="store_true", help="为输出片段生成并叠加字幕")
    p.add_argument("--translate", action="store_true", help="字幕翻译为英文")
    p.add_argument("--max-chars-per-line", type=int, default=14, help="字幕每行最大字符数（影响字体大小估算）")

    args = p.parse_args()

    whisper_dir: str = args.whisper_model_dir
    if not whisper_dir:
        raise SystemExit("未指定 faster-whisper 模型根目录：请使用 --whisper-model-dir")

    inp = args.input_path
    if os.path.isdir(inp):
        video_dir = inp
        video_files = [
            os.path.join(video_dir, f)
            for f in os.listdir(video_dir)
            if f.lower().endswith((".mp4", ".avi", ".mov", ".mkv"))
        ]
    elif os.path.isfile(inp):
        video_dir = os.path.dirname(os.path.abspath(inp))
        video_files = [inp]
    else:
        raise SystemExit(f"输入路径不可用：{inp}")

    for video_file in video_files:
        base_name = os.path.splitext(os.path.basename(video_file))[0]
        out_root = args.out_dir or video_dir
        out_dir = os.path.join(out_root, base_name)
        os.makedirs(out_dir, exist_ok=True)

        slicer = BroadcastVideoSlices(
            model_size=args.model_size,
            device=args.device,
            model_path=whisper_dir,
            vision_model_path_or_id=args.vision_model,
        )

        kwargs = {
            "language": args.language,
            "use_nvenc": bool(args.use_nvenc),
            "crf": int(args.crf),
            "add_subtitles": bool(args.add_subtitles),
            "translate": bool(args.translate),
            "max_chars_per_line": int(args.max_chars_per_line),
        }
        if args.mode == "jumpcut":
            kwargs["profile"] = args.profile

        outs = slicer.cut_video(
            video_path=video_file,
            output_dir=out_dir,
            mode=args.mode,
            **kwargs,
        )
        print("导出完成：")
        for pth in outs:
            print(pth)


if __name__ == "__main__":
    main()
