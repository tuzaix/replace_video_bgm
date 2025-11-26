from __future__ import annotations

import os
from typing import Dict, Any, Optional, List
import pathlib

from utils.calcu_video_info import ffprobe_stream_info

def _ass_color(hex_rgb: str) -> str:
    """将 #RRGGBB 转为 ASS 颜色格式 &HBBGGRR&。"""
    try:
        h = hex_rgb.strip().lstrip("#")
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        return f"&H{b:02X}{g:02X}{r:02X}&"
    except Exception:
        return "&H00FFFFFF&"

def _srt_time_to_ass(t: str) -> str:
    """SRT 时间戳转 ASS 时间戳。"""
    try:
        hh, mm, rest = t.split(":")
        ss, ms = rest.split(",")
        cs = int(round(int(ms) / 10.0))
        return f"{int(hh)}:{int(mm):02d}:{int(ss):02d}.{int(cs):02d}"
    except Exception:
        return "0:00:00.00"

def _ass_escape(s: str) -> str:
    """转义文本中的换行与回车。"""
    t = str(s or "")
    t = t.replace("\r", "")
    t = t.replace("\n", "\\N")
    return t

def _compute_font_size(width: int, max_chars_per_line: Optional[int], reserved_lr_percent: float = 0.05, char_scale: float = 0.6, min_size: int = 18, max_size: int = 96) -> int:
    """根据视频宽度与每行最大字符数估算合适的字体大小。"""
    try:
        n = int(max_chars_per_line or 14)
        n = max(n, 6)
        eff_w = int(width * (1.0 - 2.0 * reserved_lr_percent))
        size = int(eff_w / float(n) / float(char_scale))
        if size < min_size:
            size = min_size
        if size > max_size:
            size = max_size
        return size
    except Exception:
        return 42

def srt_to_ass_with_highlight(
    srt_path: str,
    ass_path: str,
    video_path: str,
    mode: str,
    style_cfg: Dict[str, Any],
    keywords_cfg: Dict[str, Any],
    max_chars_per_line: Optional[int] = None,
) -> str:
    """将 SRT 转为 ASS，按关键词高亮，并根据分辨率与每行字符数推算字体大小与位置。"""
    info = ffprobe_stream_info(pathlib.Path(video_path))
    w = int(info.get("width", 1920) or 1920)
    h = int(info.get("height", 1080) or 1080)

    font = str(style_cfg.get("font_name", "Microsoft YaHei"))
    primary = _ass_color(str(style_cfg.get("primary_color", "#FFFFFF")))
    secondary = primary
    outlinec = _ass_color(str(style_cfg.get("outline_color", "#000000")))
    backc = _ass_color(str(style_cfg.get("back_color", "#000000")))
    outline = int(style_cfg.get("outline", 2))
    shadow = int(style_cfg.get("shadow", 0))
    align = int(style_cfg.get("alignment", 2))
    margin_v = int(style_cfg.get("margin_v", 30))
    enc = int(style_cfg.get("encoding", 1))
    hi_color = _ass_color(str(style_cfg.get("highlight_color", "#FFE400")))
    bold_flag = -1 if bool(style_cfg.get("bold", True)) else 0
    reserved_lr_percent = float(style_cfg.get("reserved_lr_percent", 0.05))
    pos_y_percent = float(style_cfg.get("pos_y_percent", 0.92))
    pos_x = style_cfg.get("pos_x", None)
    if pos_x is None:
        pos_x = int(w * 0.5)
    pos_y = int(h * pos_y_percent)

    fsize = _compute_font_size(w, max_chars_per_line, reserved_lr_percent=reserved_lr_percent)

    kws: List[str] = list(keywords_cfg.get("high", [])) + list(keywords_cfg.get("mid", []))
    with open(srt_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    header: List[str] = []
    header.append("[Script Info]")
    header.append("Script Type: v4.00+")
    header.append(f"PlayResX: {w}")
    header.append(f"PlayResY: {h}")
    header.append("ScaledBorderAndShadow: yes")
    header.append("")
    header.append("[V4+ Styles]")
    header.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
    header.append(f"Style: Default,{font},{fsize},{primary},{secondary},{outlinec},{backc},{bold_flag},0,0,0,100,100,0,0,1,{outline},{shadow},{align},20,20,{margin_v},{enc}")
    header.append("")
    header.append("[Events]")
    header.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    events: List[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        try:
            _idx = lines[i].strip()
            i += 1
            if i >= len(lines):
                break
            ts_line = lines[i].strip()
            i += 1
            if "-->" not in ts_line:
                continue
            t1, t2 = [s.strip() for s in ts_line.split("-->")]
            text_buf: List[str] = []
            while i < len(lines) and lines[i].strip():
                text_buf.append(lines[i])
                i += 1
            raw_text = "\n".join(text_buf)
            ass_text = _ass_escape(raw_text)
            for kw in sorted(kws, key=lambda x: len(x), reverse=True):
                k = str(kw)
                if not k:
                    continue
                ass_text = ass_text.replace(k, f"{'{\\c'}{hi_color}{'}'}{k}{'{\\c'}{primary}{'}'}")
            ass_text = f"{{\\pos({int(pos_x)},{int(pos_y)})}}{ass_text}"
            ev = f"Dialogue: 0,{_srt_time_to_ass(t1)},{_srt_time_to_ass(t2)},Default,,0,0,0,,{ass_text}"
            events.append(ev)
        except Exception:
            i += 1
            continue

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + events))
    return ass_path
