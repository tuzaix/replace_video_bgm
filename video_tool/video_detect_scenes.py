from __future__ import annotations

import os
import json
import pathlib
from typing import List, Tuple, Dict, Any, Optional

from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
from utils.calcu_video_info import ffprobe_stream_info
bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffprobe=True)

try:
    from transnetv2_pytorch import TransNetV2  # type: ignore
except Exception:
    raise RuntimeError(
        "未找到 transnetv2-pytorch。请先安装：pip install transnetv2-pytorch ffmpeg-python，"
        "并确保系统已安装 FFmpeg 并配置到 PATH。"
    )

def _get_fps(video_path: str) -> float:
    """返回视频的帧率（FPS）。

    参数
    ----
    video_path: str
        视频文件路径。

    返回
    ----
    float
        帧率，无法解析时回退为 30.0。
    """
    try:
        sinfo = ffprobe_stream_info(pathlib.Path(video_path))
        fr = str(sinfo.get("r_frame_rate", ""))
        if fr and "/" in fr:
            a, b = fr.split("/", 1)
            return max(1.0, float(a) / max(1.0, float(b)))
    except Exception:
        pass
    return 30.0


def detect_scenes_transnet(video_path: str) -> Dict[str, Any]:
    """使用 TransNet V2 进行镜头分割，返回帧索引与秒单位的区间信息。

    参数
    ----
    video_path: str
        输入视频路径。

    返回
    ----
    Dict[str, Any]
        结构：{
          "fps": float,
          "scenes_frames": List[Tuple[int, int]],
          "scenes_seconds": List[Tuple[float, float]],
        }

    说明
    ----
    需要在项目中准备好 TransNet V2 的推理代码与权重，并可通过 `from transnetv2 import TransNetV2` 导入。
    若环境未配置，将抛出 RuntimeError，提示用户按照指南安装。
    """
    model = TransNetV2()
    predictions, scenes = model.predict_video(video_path)
    fps = _get_fps(video_path)
    scenes_seconds: List[Tuple[float, float]] = []
    try:
        for s, e in scenes:
            scenes_seconds.append((float(s) / fps, float(e) / fps))
    except Exception:
        scenes_seconds = []
    return {
        "fps": float(fps),
        "scenes_frames": [(int(s), int(e)) for s, e in scenes],
        "scenes_seconds": scenes_seconds,
    }


def save_scenes_results(video_path: str, output_dir: Optional[str] = None, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """执行镜头分割并将结果保存到输出目录，返回写入的文件路径信息。

    参数
    ----
    video_path: str
        输入视频路径。
    output_dir: Optional[str]
        输出目录；为空则默认视频同目录下的 `scenes` 子目录。

    返回
    ----
    Dict[str, Any]
        结构：{
          "output_dir": str,
          "json_path": str,
          "txt_path": str,
        }
    """
    vp = pathlib.Path(video_path)
    out_dir = pathlib.Path(output_dir or (vp.parent / "scenes"))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    result = result or detect_scenes_transnet(video_path)
    json_path = out_dir / (vp.stem + "_scenes.json")
    txt_path = out_dir / (vp.stem + "_scenes.txt")

    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"fps: {result['fps']}\n")
            for i, (s, e) in enumerate(result.get("scenes_seconds", [])):
                f.write(f"scene {i+1}: {s:.3f} - {e:.3f}\n")
    except Exception:
        pass

    return {
        "output_dir": str(out_dir),
        "json_path": str(json_path),
        "txt_path": str(txt_path),
    }