from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional, List
import pathlib

from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
from utils.calcu_video_info import ffprobe_stream_info
from utils.common_utils import get_subprocess_silent_kwargs
from utils.xprint import xprint

env = bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffmpeg=True)
ffmpeg_bin = env.get("ffmpeg_path") or shutil.which("ffmpeg")

def ff_filter_escape_path(path: str) -> str:
    """返回可用于 FFmpeg filter 的安全路径表达。

    将 Windows 路径转换为正斜杠形式，并转义会影响 filter 解析的特殊字符（' 和 :）。
    """
    p = os.path.abspath(path)
    p = p.replace("\\", "/")
    p = p.replace("'", "\\'")
    p = p.replace(":", "\\:")
    return p

def _encode_args(use_nvenc: bool, crf: int) -> List[str]:
    """根据是否启用 NVENC 返回编码参数列表。"""
    if use_nvenc:
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p6",
            "-tune", "hq",
            "-rc", "vbr",
            "-cq", str(int(crf)),
            "-c:a", "aac",
        ]
    return [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", str(int(crf)),
        "-c:a", "aac",
    ]

def overlay_ass_subtitles(
    src_path: str,
    ass_path: str,
    out_path: Optional[str] = None,
    use_nvenc: bool = True,
    crf: int = 23,
    fontsdir: Optional[str] = None,
) -> str:
    """为视频叠加 ASS 字幕并生成带字幕的视频。

    参数
    ----
    src_path: 输入视频路径
    ass_path: ASS 字幕文件路径
    out_path: 输出视频路径；默认在同目录生成 `*_sub` 文件
    use_nvenc: 是否优先使用 NVENC 编码
    crf: 编码质量（NVENC 用作 CQ）
    fontsdir: 可选字体目录（某些系统用于 libass 字体查找）

    返回
    ----
    成功时返回输出视频路径；失败时返回原始 `src_path`。
    """
    try:
        if not ffmpeg_bin:
            return src_path
        name, ext = os.path.splitext(os.path.basename(src_path))
        outd = os.path.join(out_path or os.path.dirname(src_path), "synthesis")
        os.makedirs(outd, exist_ok=True)
        outp = out_path or os.path.join(outd, f"{name}_sub{ext}")
        info = ffprobe_stream_info(pathlib.Path(src_path))
        w = int(info.get("width", 0) or 0)
        h = int(info.get("height", 0) or 0)
        sp = ff_filter_escape_path(ass_path)

        attempts: List[str] = []
        # 优先带 original_size，避免某些构建无法推断分辨率
        if w > 0 and h > 0:
            vf = f"subtitles=filename='{sp}':original_size={w}x{h}"
            if fontsdir:
                fd = ff_filter_escape_path(fontsdir)
                vf += f":fontsdir='{fd}'"
            attempts.append(vf)
        # 简化版本：仅 filename
        vf2 = f"subtitles=filename='{sp}'"
        if fontsdir:
            fd = ff_filter_escape_path(fontsdir)
            vf2 += f":fontsdir='{fd}'"
        attempts.append(vf2)
        # 终极回退：ass 过滤器
        vf3 = f"ass='{sp}'"
        attempts.append(vf3)

        for idx, vf in enumerate(attempts, 1):
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i", src_path,
                "-vf", vf,
            ] + _encode_args(use_nvenc, int(crf)) + [
                "-loglevel", "error",
                outp,
            ]
            r = subprocess.run(cmd, capture_output=True, **get_subprocess_silent_kwargs())
            ok = (r.returncode == 0) and os.path.isfile(outp)
            if ok:
                return outp
            err = ""
            try:
                err = (r.stderr or b"").decode("utf-8", errors="ignore")
            except Exception:
                try:
                    err = (r.stderr or b"").decode("mbcs", errors="ignore")
                except Exception:
                    err = ""
            xprint({"phase": "subtitle_overlay_attempt_failed", "attempt": idx, "vf": vf, "error": err.strip()[:500]})
        return src_path
    except Exception:
        return src_path

