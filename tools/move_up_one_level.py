#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
move_up_one_level.py — 将下一级子目录中的文件移动到当前目录

- 只处理给定目录的“一层子目录”中的文件（不递归更深层）。
- 遇到与目标目录已有文件同名时将跳过并提示（不覆盖、不重命名）。

用法示例：
python tools\move_up_one_level.py C:\path\to\root_dir
"""

import sys
import argparse
from pathlib import Path
import shutil


def move_up_one_level(root: Path) -> tuple[int, int, int]:
    """将 root 的一层子目录中的所有文件移动到 root。

    返回 (moved, skipped, errors)
    - moved: 成功移动的文件数量
    - skipped: 因重名而跳过的数量
    - errors: 发生错误的数量
    """
    moved = 0
    skipped = 0
    errors = 0

    for child in sorted(root.iterdir()):
        if child.is_dir():
            for item in sorted(child.iterdir()):
                if item.is_file():
                    target = root / item.name
                    if target.exists():
                        print(f"⚠️ 跳过重名: {item} -> {target}")
                        skipped += 1
                        continue
                    try:
                        shutil.move(str(item), str(target))
                        print(f"✅ 移动: {item} -> {target}")
                        moved += 1
                    except Exception as e:
                        print(f"❌ 失败: {item} -> {target}: {e}")
                        errors += 1
    return moved, skipped, errors


def main():
    parser = argparse.ArgumentParser(description='把下一级子目录下的文件移动到当前目录，只移动一层子目录内的文件')
    parser.add_argument('dir', help='目标目录路径字符串')
    args = parser.parse_args()

    root = Path(args.dir)
    if not root.exists() or not root.is_dir():
        print(f"错误：目录不存在或不可用：{root}")
        sys.exit(1)

    moved, skipped, errors = move_up_one_level(root)
    print("\n=== 汇总 ===")
    print(f"✅ 已移动：{moved}")
    print(f"⚠️ 重名跳过：{skipped}")
    print(f"❌ 失败：{errors}")

    sys.exit(0 if errors == 0 else 2)


if __name__ == '__main__':
    main()