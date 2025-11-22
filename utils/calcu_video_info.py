from __future__ import annotations

import os
import pathlib
import shutil
from typing import List, Tuple, Dict, Literal
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
env = bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffprobe=False)

ffprobe_bin = env.get("ffprobe_path") or shutil.which("ffprobe")
ffmpeg_bin = env.get("ffmpeg_path") or shutil.which("ffmpeg")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}

def list_media(
    dir_path: str,
    media_type: Literal["all", "video", "image"] = "all",
    recursive: bool = False,
) -> List[pathlib.Path]:
    """列出目录下的视频/图片素材文件列表。

    参数
    ----
    dir_path: 素材目录路径
    media_type: 过滤类型，"all"/"video"/"image"
    recursive: 是否递归子目录
    """
    root = pathlib.Path(dir_path)
    out: List[pathlib.Path] = []
    try:
        if recursive:
            for r, _, files in os.walk(str(root)):
                base = pathlib.Path(r)
                for name in files:
                    ext = os.path.splitext(name)[1].lower()
                    if media_type in {"all", "video"} and ext in VIDEO_EXTS:
                        out.append(base / name)
                    elif media_type in {"all", "image"} and ext in IMAGE_EXTS:
                        out.append(base / name)
        else:
            for name in os.listdir(str(root)):
                p = root / name
                if p.is_file():
                    ext = os.path.splitext(name)[1].lower()
                    if media_type in {"all", "video"} and ext in VIDEO_EXTS:
                        out.append(p)
                    elif media_type in {"all", "image"} and ext in IMAGE_EXTS:
                        out.append(p)
    except Exception:
        pass
    return out


def probe_resolution(path: pathlib.Path) -> Tuple[int, int] | None:
    """使用 ffprobe 优先探测分辨率，失败时回退 moviepy。"""
    try:
        ext = os.path.splitext(path.name)[1].lower()
        if ext in VIDEO_EXTS:
            try:
                
                if ffprobe_bin:
                    import subprocess
                    si = None
                    kwargs = {}
                    try:
                        if os.name == "nt":
                            si = subprocess.STARTUPINFO()
                            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                            kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
                    except Exception:
                        kwargs = {}
                    cmd = [
                        ffprobe_bin,
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=width,height",
                        "-of",
                        "csv=p=0:s=x",
                        str(path),
                    ]
                    res = subprocess.run(cmd, capture_output=True, **kwargs)
                    if res.returncode == 0:
                        text = (res.stdout or b"").decode("utf-8", errors="ignore").strip()
                        if "x" in text:
                            w, h = text.split("x", 1)
                            return (int(float(w)), int(float(h)))
            except Exception:
                pass
            try:
                from moviepy.editor import VideoFileClip
                with VideoFileClip(str(path)) as clip:
                    return (int(clip.w or 0), int(clip.h or 0))
            except Exception:
                return None
        else:
            try:
                from moviepy.editor import ImageClip
                clip = ImageClip(str(path))
                w = int(getattr(clip, "w", 0) or 0)
                h = int(getattr(clip, "h", 0) or 0)
                try:
                    clip.close()
                except Exception:
                    pass
                return (w, h) if (w > 0 and h > 0) else (0, 0)
            except Exception:
                return None
    except Exception:
        return None

def ffprobe_duration(path: pathlib.Path) -> float:
    try:
        cmd = [ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
        si = None
        kwargs = {}
        try:
            if os.name == "nt":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
        except Exception:
            kwargs = {}
        r = subprocess.run(cmd, capture_output=True, **kwargs)
        if r.returncode == 0:
            txt = (r.stdout or b"").decode("utf-8", errors="ignore").strip()
            return float(txt)
    except Exception:
        pass
    try:
        v = VideoFileClip(str(path))
        d = float(v.duration or 0.0)
        v.close()
        return d
    except Exception:
        return 0.0

def ffprobe_stream_info(path: pathlib.Path) -> Dict[str, Any]:
    try:
        cmd = [
            ffprobe_bin,
            "-v","error",
            "-select_streams","v:0",
            "-show_entries","stream=width,height,codec_name,r_frame_rate,pix_fmt",
            "-of","json",
            str(path),
        ]
        si = None
        kwargs = {}
        try:
            if os.name == "nt":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
        except Exception:
            kwargs = {}
        r = subprocess.run(cmd, capture_output=True, **kwargs)
        if r.returncode == 0:
            import json as _json
            data = _json.loads((r.stdout or b"{}").decode("utf-8", errors="ignore") or "{}")
            st = (data.get("streams") or [{}])[0]
            return {
                "width": int(st.get("width" or 0) or 0),
                "height": int(st.get("height" or 0) or 0),
                "codec": str(st.get("codec_name" or "") or ""),
                "pix_fmt": str(st.get("pix_fmt" or "") or ""),
                "r_frame_rate": str(st.get("r_frame_rate" or "") or ""),
            }
    except Exception:
        pass
    return {"width": 0, "height": 0, "codec": "", "pix_fmt": "", "r_frame_rate": ""}

def group_by_resolution(paths: List[pathlib.Path]) -> Dict[Tuple[int, int], List[pathlib.Path]]:
    """按分辨率对素材进行分组。"""
    groups: Dict[Tuple[int, int], List[pathlib.Path]] = {}
    for p in paths:
        try:
            res = probe_resolution(p) or (0, 0)
        except Exception:
            res = (0, 0)
        groups.setdefault(res, []).append(p)
    return groups


def get_resolution_topn(
    dir_path: str,
    top_n: int = 1,
    media_type: Literal["all", "video", "image"] = "all",
    recursive: bool = False,
) -> List[Tuple[Tuple[int, int], int]]:
    """计算目录下视频/图片分辨率分布并返回数量最多的 TopN。

    参数
    ----
    dir_path: 素材目录路径
    top_n: 返回的分辨率组数量（默认 1）
    media_type: 过滤类型，"all"/"video"/"image"
    recursive: 是否递归子目录

    返回
    ----
    列表，元素为 ((width, height), count)，按 count 从大到小排序。
    """
    paths = list_media(dir_path, media_type=media_type, recursive=recursive)
    if not paths:
        return []
    groups = group_by_resolution(paths)
    counts: Dict[Tuple[int, int], int] = {k: len(v) for k, v in groups.items()}
    items = sorted(counts.items(), key=lambda kv: (-kv[1], -(kv[0][0] * kv[0][1])))
    topn_data = [
        {
            "resolution": k, 
            "count": v, 
            "files": groups[k]
        } for k, v in items[: max(1, int(top_n))]
    ]
    return topn_data[0] if top_n == 1 else topn_data
