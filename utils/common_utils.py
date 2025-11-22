from __future__ import annotations

import os
import time
import random
import string
from typing import Optional
from pathlib import Path
import uuid


def get_random_int(min_value: int = 0, max_value: int = 999999) -> int:
    """返回指定范围内的随机整数。

    参数
    ----
    min_value: int
        最小值（含），默认 0。
    max_value: int
        最大值（含），默认 999999。

    返回
    ----
    int
        随机整数。
    """
    if max_value < min_value:
        min_value, max_value = max_value, min_value
    return random.randint(int(min_value), int(max_value))

def get_random_uuid(len: int = 16) -> str:
    """返回随机 UUID 字符串。

    返回
    ----
    str
        随机 UUID 字符串，格式如 "123e4567-e89b-12d3-a456-426614174000"。
    """
    return str(uuid.uuid4())[:len]

def make_random_suffix_name(
    filename: str,
    *args: any,
) -> str:
    """基于传入文件名生成带随机后缀的新名称。

    参数
    ----
    filename: str
        原始文件名或路径（仅使用名与后缀部分）。
    sep: str
        后缀连接符，默认 "_"。
    rand_len: int
        随机串长度，默认 6。
    add_timestamp: bool
        是否在随机串前附加毫秒时间戳，默认 False。
    preserve_ext: bool
        是否保留原始扩展名，默认 True。
    ext: Optional[str]
        指定新扩展名（覆盖原扩展名）。

    返回
    ----
    str
        新的文件名（不包含目录）。
    """
    parts = filename.split('.')
    ext = ext if ext else parts[-1]
    prefix = '.'.join(parts[:-1])

    random_suffix = get_random_uuid()
    p = Path(filename)
    stem = p.stem
    orig_ext = p.suffix
    use_ext = ext if isinstance(ext, str) and ext else (orig_ext if preserve_ext else "")

    alphabet = string.ascii_lowercase + string.digits
    rnd = "".join(random.choice(alphabet) for _ in range(max(1, int(rand_len))))
    parts = [stem]
    if add_timestamp:
        parts.append(str(int(time.time() * 1000)))
    parts.append(rnd)
    new_stem = sep.join(parts)
    return f"{new_stem}{use_ext}"