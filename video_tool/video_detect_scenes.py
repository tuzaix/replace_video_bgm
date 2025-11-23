from __future__ import annotations

import os
import json
import pathlib
from typing import List, Tuple, Dict, Any, Optional
import shutil
import subprocess

from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
from utils.calcu_video_info import ffprobe_stream_info, ffmpeg_bin
bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffprobe=True)

try:
    from transnetv2_pytorch import TransNetV2  # type: ignore
except Exception:
    raise RuntimeError(
        "未找到 transnetv2-pytorch。请先安装：pip install transnetv2-pytorch ffmpeg-python，"
        "并确保系统已安装 FFmpeg 并配置到 PATH。"
    )
import torch  # type: ignore

class VideoDetectScenes:
    """使用 TransNet V2 进行镜头分割并生成切片与元数据。"""

    def __init__(self, video_path: str, output_dir: str = None, device: str = "auto") -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.device = device
        try:
            
            if torch.cuda.is_available() and self.device == "auto":
                self.device = "cuda"
        except Exception:
            self.device = "auto"
        try:
            self.model = TransNetV2(device=self.device)  # type: ignore[arg-type]   
        except Exception:
            self.model = TransNetV2()  # type: ignore[call-arg]
        
        self.video_path = video_path
        self.output_dir = output_dir or os.path.join(os.path.dirname(video_path), "scenes")
        os.makedirs(self.output_dir, exist_ok=True)

    def _get_fps(self, video_path: str) -> float:
        try:
            sinfo = ffprobe_stream_info(pathlib.Path(video_path))
            fr = str(sinfo.get("r_frame_rate", ""))
            print(fr)
            if fr and "/" in fr:
                a, b = fr.split("/", 1)
                return max(1.0, float(a) / max(1.0, float(b)))
        except Exception:
            pass
        return 30.0

    def detect(self) -> Dict[str, Any]:
        fps = self._get_fps(self.video_path)
        scenes_frames: List[Tuple[int, int]] = []
        scenes_seconds: List[Tuple[float, float]] = []
        try:
            results = self.model.analyze_video(self.video_path)  # type: ignore[attr-defined]
            fps = float(results.get("fps", fps))
            scenes_data = results.get("scenes", [])
            for item in scenes_data:
                try:
                    if isinstance(item, dict):
                        if "start_frame" in item and "end_frame" in item:
                            s = int(item["start_frame"]) 
                            e = int(item["end_frame"]) 
                        elif "start" in item and "end" in item:
                            s = int(item["start"]) 
                            e = int(item["end"]) 
                        elif "start_time" in item and "end_time" in item:
                            st = float(item["start_time"]) 
                            et = float(item["end_time"]) 
                            s = int(round(st * fps))
                            e = int(round(et * fps))
                        else:
                            continue
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        s = int(item[0])
                        e = int(item[1])
                    else:
                        continue
                    scenes_frames.append((s, e))
                    scenes_seconds.append((float(s) / fps, float(e) / fps))
                except Exception:
                    continue
        except Exception:
            try:
                video_frames, single_frame_pred, all_frame_pred = self.model.predict_video(self.video_path)  # type: ignore[attr-defined]
                try:
                    scenes_data = self.model.predictions_to_scenes(single_frame_pred)  # type: ignore[attr-defined]
                except Exception:
                    scenes_data = []
                for item in scenes_data:
                    try:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            s = int(item[0])
                            e = int(item[1])
                        elif isinstance(item, dict):
                            s = int(item.get("start", item.get("start_frame", 0)))
                            e = int(item.get("end", item.get("end_frame", 0)))
                        else:
                            continue
                        scenes_frames.append((s, e))
                        scenes_seconds.append((float(s) / fps, float(e) / fps))
                    except Exception:
                        continue
            except Exception:
                scenes_frames = []
                scenes_seconds = []
        return {
            "fps": float(fps),
            "scenes_frames": scenes_frames,
            "scenes_seconds": scenes_seconds,
        }

    def save(self) -> Dict[str, Any]:
        vp = pathlib.Path(self.video_path)
        out_dir = pathlib.Path(self.output_dir)
    
        data = self.detect()
        json_path = out_dir / (vp.stem + "_scenes.json")
        txt_path = out_dir / (vp.stem + "_scenes.txt")

        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"fps: {data['fps']}\n")
                for i, (s, e) in enumerate(data.get("scenes_seconds", [])):
                    f.write(f"scene {i+1}: {s:.3f} - {e:.3f}\n")
        except Exception:
            pass

        clips: List[str] = []
        clips_meta: List[Dict[str, Any]] = []
        try:
            scenes_frames: List[Tuple[int, int]] = list(data.get("scenes_frames", []))
            fps: float = float(data.get("fps", 30.0))
            scenes_seconds: List[Tuple[float, float]] = list(data.get("scenes_seconds", []))
            if not scenes_seconds and scenes_frames and fps > 0:
                tmp_secs: List[Tuple[float, float]] = []
                for s, e in scenes_frames:
                    try:
                        ss = max(0.0, float(s) / fps)
                        ee = max(ss, float(e) / fps)
                        tmp_secs.append((ss, ee))
                    except Exception:
                        continue
                scenes_seconds = tmp_secs

            for idx, (ss, ee) in enumerate(scenes_seconds):
                try:
                    if ee <= ss:
                        continue
                    out_file = out_dir / f"{vp.stem}_scene_{idx+1:04d}_{ss:.3f}-{ee:.3f}.mp4"
                    cmd = [
                        self.ffmpeg_bin,
                        "-y",
                        "-ss",
                        str(ss),
                        "-to",
                        str(ee),
                        "-i",
                        str(vp),
                        "-map_metadata",
                        "-1",
                        "-movflags",
                        "+faststart",
                        "-c",
                        "copy",
                        str(out_file),
                    ]
                    si = None
                    kwargs: Dict[str, Any] = {}
                    try:
                        if os.name == "nt":
                            si = subprocess.STARTUPINFO()
                            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                            kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
                    except Exception:
                        kwargs = {}
                    r = subprocess.run(cmd, capture_output=True, **kwargs)
                    if r.returncode == 0 and out_file.exists():
                        clips.append(str(out_file))
                        sf = int(round(ss * fps))
                        ef = int(round(ee * fps))
                        clips_meta.append({
                            "index": idx + 1,
                            "start_time": float(ss),
                            "end_time": float(ee),
                            "start_frame": sf,
                            "end_frame": ef,
                            "path": str(out_file),
                        })
                except Exception:
                    continue
        except Exception:
            clips = []
            clips_meta = []

        return {
            "output_dir": str(out_dir),
            "json_path": str(json_path),
            "txt_path": str(txt_path),
            "clips": clips,
            "clips_meta": clips_meta,
        }

def detect_scenes_transnet(video_path: str) -> Dict[str, Any]:
    """函数式封装：返回镜头检测结果。"""
    return VideoDetectScenes(video_path).detect()


def save_scenes_results(video_path: str, output_dir: Optional[str] = None, device: str = "auto") -> Dict[str, Any]:
    """函数式封装：保存镜头检测结果并输出切片。"""
    return VideoDetectScenes(video_path, output_dir=output_dir, device=device).save()
