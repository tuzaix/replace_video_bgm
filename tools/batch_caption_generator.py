#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Batch Video Caption Generator
串行调用 video_caption_generator.py 处理多个视频目录。
"""

import os
import sys
import subprocess
from pathlib import Path

def main():
    # --- 用户配置区：在此处添加需要处理的视频目录 ---
    target_directories = [
        # r"D:\Videos\Batch1",
        # r"D:\Videos\Batch2",
        r"E:\项目\视频号鸡汤\音频素材\Q同学",
        r"E:\项目\视频号鸡汤\音频素材\藏峰-",
        r"E:\项目\视频号鸡汤\音频素材\寂寞红尘",
        r"E:\项目\视频号鸡汤\音频素材\江虹"
    ]
    # -------------------------------------------

    # 如果通过命令行传入了路径，优先使用命令行参数
    if len(sys.argv) > 1:
        target_directories = sys.argv[1:]

    if not target_directories:
        print("[-] 未指定目标目录。")
        print("    使用方式 1: 直接在脚本 target_directories 列表中添加路径。")
        print("    使用方式 2: python batch_caption_generator.py <dir1> <dir2> ...")
        return

    # 获取 video_caption_generator.py 的绝对路径
    script_dir = Path(__file__).parent
    generator_script = script_dir / "video_caption_generator.py"

    if not generator_script.exists():
        print(f"[-] 找不到生成脚本: {generator_script}")
        return

    total_dirs = len(target_directories)
    print(f"[*] 开始批量处理 {total_dirs} 个目录...")

    for i, dir_path in enumerate(target_directories):
        dir_path = Path(dir_path).resolve()
        print(f"\n{'='*60}")
        print(f"[{i+1}/{total_dirs}] 正在处理目录: {dir_path}")
        print(f"{'='*60}")

        if not dir_path.exists():
            print(f"[-] 目录不存在，跳过: {dir_path}")
            continue

        # 构造命令
        # 可以根据需要添加 --workers 或 --model 等参数
        cmd = [
            sys.executable,
            str(generator_script),
            str(dir_path),
            "--workers", "3"  # 目录内部依然可以保持小规模并发，或者设为 1 彻底串行
        ]

        try:
            # 使用 subprocess.run 串行执行
            # capture_output=False 允许直接在当前终端看到输出
            result = subprocess.run(cmd, check=False)
            
            if result.returncode == 0:
                print(f"[+] 目录处理完成: {dir_path}")
            else:
                print(f"[-] 目录处理失败 (退出码 {result.returncode}): {dir_path}")
                
        except Exception as e:
            print(f"[-] 执行过程中发生异常: {e}")

    print(f"\n[+] 全部 {total_dirs} 个目录批量处理任务结束。")

if __name__ == "__main__":
    main()
