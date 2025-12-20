#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频音频混剪工具 CLI
"""

import argparse
import sys
from pathlib import Path

# 将项目根目录添加到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from video_tool.video_remixed_video_audio import VideoRemixedVideoAudio

def main():
    parser = argparse.ArgumentParser(description="根据模仿视频的音频混剪素材库视频。")
    parser.add_argument("--imitation_dir", "-i", required=True, help="模仿视频目录")
    parser.add_argument("--segment_dir", "-s", required=True, help="视频素材切片目录")
    parser.add_argument("--output_dir", "-o", help="输出目录 (可选，默认为模仿视频目录下的 remixed)")
    parser.add_argument("--count", "-c", type=int, default=1, help="每个模仿视频生成的混剪数量 (默认 1)")
    parser.add_argument("--gpu", action="store_true", default=True, help="是否使用 GPU 加速 (默认开启)")
    parser.add_argument("--no-gpu", action="store_false", dest="gpu", help="禁用 GPU 加速")
    parser.add_argument("--profile", "-p", choices=["visual", "balanced", "size"], default="balanced", 
                        help="编码档位: visual(观感优先), balanced(平衡), size(体积优先)。默认 balanced")

    args = parser.parse_args()
    
    # 初始化混剪器
    remixer = VideoRemixedVideoAudio(
        imitation_dir=args.imitation_dir, 
        segment_dir=args.segment_dir, 
        output_dir=args.output_dir,
        use_gpu=args.gpu,
        encode_profile=args.profile
    )
    
    # 开始处理
    remixer.process(count_per_video=args.count)

if __name__ == "__main__":
    main()
