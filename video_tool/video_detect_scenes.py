from __future__ import annotations

import os
import json
import pathlib
from typing import List, Tuple, Dict, Any, Optional
import uuid
import subprocess
from transnetv2_pytorch import TransNetV2  # type: ignore
import torch  # type: ignore
import numpy as np  # type: ignore
import librosa  # type: ignore
import cv2  # type: ignore
import traceback
from utils.calcu_video_info import ffprobe_stream_info, ffmpeg_bin, ffprobe_duration
from .scenes_config import SCENE_CONFIGS

class VideoDetectScenes:
    """使用 TransNet V2 进行镜头分割并生成切片与元数据。"""

    

    def __init__(self, device: str = "auto", threshold: float = 0.5) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.device = device
        self.threshold = threshold
        try:
            if torch.cuda.is_available() and self.device == "auto":
                self.device = "cuda"
        except Exception:
            self.device = "auto"
        try:
            self.model = TransNetV2(device=self.device)  # type: ignore[arg-type]   
        except Exception:
            self.model = TransNetV2()  # type: ignore[call-arg]
        
    def _get_fps(self, video_path: str) -> float:
        try:
            sinfo = ffprobe_stream_info(pathlib.Path(video_path))
            fr = str(sinfo.get("r_frame_rate", "") or "")
            if fr and "/" in fr:
                a, b = fr.split("/", 1)
                aa = float(a) if a else 0.0
                bb = float(b) if b else 1.0
                return max(1.0, aa / max(1.0, bb))
        except Exception:
            pass
        return 30.0

    def detect(self, video_path: str, 
                        min_duration: float = 0.6, 
                        similarity_threshold: float = 0.85, 
                        hist_sample_offset: int = 5, 
                        enable_audio_snap: bool = False, 
                        snap_tolerance: float = 0.2, 
                        min_segment_sec: float = 0.5, 
                        enable_silence_split: bool = False,
                        window_s: float = 0.5,
                        profile: Optional[str] = None
        ) -> Dict[str, Any]:
        """检测镜头，使用 TransNet 召回 + HSV 直方图相似度过滤 + 最小时长约束，可选音频吸附对齐。"""
        if not profile:
            profile = "general"
        cfg = SCENE_CONFIGS.get(str(profile)) or {}
        
        min_duration = float(cfg.get("min_duration", min_duration))
        similarity_threshold = float(cfg.get("similarity_threshold", similarity_threshold))
        hist_sample_offset = int(cfg.get("hist_sample_offset", hist_sample_offset))
        enable_audio_snap = bool(cfg.get("enable_audio_snap", enable_audio_snap))
        snap_tolerance = float(cfg.get("snap_tolerance", snap_tolerance))
        min_segment_sec = float(cfg.get("min_segment_sec", min_segment_sec))
        enable_silence_split = bool(cfg.get("enable_silence_split", enable_silence_split))
        window_s = float(cfg.get("window_s", window_s))
        try:
            self.threshold = float(cfg.get("threshold", self.threshold))
        except Exception:
            pass

        if profile == "general":
            try:
                tuned = self._auto_tune_config(video_path, {
                    "threshold": self.threshold,
                    "similarity_threshold": similarity_threshold,
                    "hist_sample_offset": hist_sample_offset,
                    "min_duration": min_duration,
                    "min_segment_sec": min_segment_sec,
                    "enable_audio_snap": enable_audio_snap,
                    "snap_tolerance": snap_tolerance,
                    "enable_silence_split": enable_silence_split,
                    "window_s": window_s,
                })
                self.threshold = float(tuned.get("threshold", self.threshold))
                similarity_threshold = float(tuned.get("similarity_threshold", similarity_threshold))
                hist_sample_offset = int(tuned.get("hist_sample_offset", hist_sample_offset))
                min_duration = float(tuned.get("min_duration", min_duration))
                min_segment_sec = float(tuned.get("min_segment_sec", min_segment_sec))
                enable_audio_snap = bool(tuned.get("enable_audio_snap", enable_audio_snap))
                snap_tolerance = float(tuned.get("snap_tolerance", snap_tolerance))
                enable_silence_split = bool(tuned.get("enable_silence_split", enable_silence_split))
                window_s = float(tuned.get("window_s", window_s))
            except Exception:
                pass

        fps = self._get_fps(video_path)
        raw_frames: List[Tuple[int, int]] = []
        raw_seconds: List[Tuple[float, float]] = []
        try:
            results = self.model.analyze_video(video_path, threshold=self.threshold)  # type: ignore[attr-defined]
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
                    raw_frames.append((s, e))
                    raw_seconds.append((float(s) / fps, float(e) / fps))
                except Exception:
                    continue
        except Exception:
            try:
                video_frames, single_frame_pred, all_frame_pred = self.model.predict_video(video_path)  # type: ignore[attr-defined]
                try:
                    scenes_data = self.model.predictions_to_scenes(single_frame_pred, threshold=self.threshold)  # type: ignore[attr-defined]
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
                        raw_frames.append((s, e))
                        raw_seconds.append((float(s) / fps, float(e) / fps))
                    except Exception:
                        continue
            except Exception:
                raw_frames = []
                raw_seconds = []

        cut_frames: List[int] = [int(round(seg[1] * fps)) for seg in raw_seconds if seg[1] > seg[0]]
        cap = None
        try:
            cap = cv2.VideoCapture(video_path)
            cv_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            if cv_fps > 1.0:
                fps = cv_fps
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        except Exception:
            total_frames = int(max([int(round(seg[1] * fps)) for seg in raw_seconds], default=0))

        final_cut_frames: List[int] = []
        last_cut_frame = 0
        for cf in sorted(set(cut_frames)):
            try:
                if (cf - last_cut_frame) / max(1.0, fps) < float(min_duration):
                    continue
                prev_idx = max(0, cf - int(hist_sample_offset))
                next_idx = min(max(0, total_frames - 1), cf + int(hist_sample_offset))
                sim = None
                if cap:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, prev_idx)
                    ok1, f1 = cap.read()
                    cap.set(cv2.CAP_PROP_POS_FRAMES, next_idx)
                    ok2, f2 = cap.read()
                    if ok1 and ok2 and f1 is not None and f2 is not None:
                        sim = float(self._hist_similarity(f1, f2))
                if sim is not None and sim > float(similarity_threshold):
                    continue
                final_cut_frames.append(cf)
                last_cut_frame = cf
            except Exception:
                continue
        try:
            if cap:
                cap.release()
        except Exception:
            pass

        cut_times: List[float] = [float(cf) / float(fps) for cf in final_cut_frames]
        if enable_audio_snap:
            audio_path: Optional[str] = None
            try:
                audio_path = self._extract_audio_tmp(video_path)
                y, sr = librosa.load(audio_path, sr=None, mono=True)
                onset_frames = librosa.onset.onset_detect(y=y, sr=sr, backtrack=True)
                onset_times = librosa.frames_to_time(onset_frames, sr=sr)
                if enable_silence_split:
                    try:
                        intervals = librosa.effects.split(y, top_db=30)
                        for st, et in intervals:
                            cut_times.append(float(et) / float(sr))
                    except Exception:
                        pass
                cut_times = self._snap_cuts(cut_times, onset_times, snap_tolerance)
            except Exception:
                pass
            finally:
                try:
                    if audio_path and os.path.isfile(audio_path):
                        os.remove(audio_path)
                        d = os.path.dirname(audio_path)
                        if os.path.isdir(d) and not os.listdir(d):
                            os.rmdir(d)
                except Exception:
                    pass

        duration = 0.0
        try:
            duration = float(ffprobe_duration(pathlib.Path(video_path)) or 0.0)
        except Exception:
            duration = max([t for _, t in raw_seconds], default=0.0)
        if duration > 0:
            cut_times = [t for t in cut_times if 0.0 < t < duration]

        cut_times_sorted = sorted(set([round(t, 3) for t in cut_times]))
        segments: List[Tuple[float, float]] = []
        last_t = 0.0
        for ct in cut_times_sorted:
            if ct - last_t >= float(min_segment_sec):
                segments.append((last_t, ct))
                last_t = ct
        if duration > last_t + float(min_segment_sec):
            segments.append((last_t, duration))

        if segments:
            try:
                segments = self._refine_segments(video_path, segments, float(fps), float(min_segment_sec), float(similarity_threshold), window_s)
            except Exception:
                tail_trim_sec = 0.3
                new_segments: List[Tuple[float, float]] = []
                n = len(segments)
                for i, (ss_i, ee_i) in enumerate(segments):
                    if i < n - 1:
                        ee_trim = max(ss_i + float(min_segment_sec), ee_i - float(tail_trim_sec))
                        new_segments.append((ss_i, ee_trim if ee_trim > ss_i else ee_i))
                    else:
                        new_segments.append((ss_i, ee_i))
                segments = new_segments

        scenes_frames: List[Tuple[int, int]] = []
        scenes_seconds: List[Tuple[float, float]] = []
        for ss, ee in segments:
            scenes_seconds.append((ss, ee))
            scenes_frames.append((int(round(ss * fps)), int(round(ee * fps))))

        return {
            "fps": float(fps),
            "scenes_frames": scenes_frames,
            "scenes_seconds": scenes_seconds,
        }

    def _hist_similarity(self, frame1, frame2) -> float:
        """计算两帧画面的 HSV 直方图相关性，相似度越大越相似。"""
        hsv1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2HSV)
        hsv2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2HSV)
        hist1 = cv2.calcHist([hsv1], [0, 1], None, [180, 256], [0, 180, 0, 256])
        hist2 = cv2.calcHist([hsv2], [0, 1], None, [180, 256], [0, 180, 0, 256])
        cv2.normalize(hist1, hist1, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hist2, hist2, 0, 1, cv2.NORM_MINMAX)
        sim = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)
        return float(sim)

    def _auto_tune_config(self, video_path: str, base: Dict[str, Any]) -> Dict[str, Any]:
        """根据视频的画面与音频特征对场景参数进行自适应调整。"""
        cfg = dict(base)
        fps = max(1.0, float(self._get_fps(video_path)))
        cap = None
        total_frames = 0
        try:
            cap = cv2.VideoCapture(video_path)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        except Exception:
            cap = None
            total_frames = 0

        motion_level = 0.0
        try:
            if cap and total_frames > int(fps * 2):
                samples = min(80, max(10, int(total_frames / max(1, int(fps * 3)))))
                idxs = [int(i * total_frames / (samples + 1)) for i in range(1, samples + 1)]
                prev = None
                diffs = []
                for idx in idxs:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                    ok, f = cap.read()
                    if not ok or f is None:
                        continue
                    if prev is not None:
                        sim = self._hist_similarity(prev, f)
                        diffs.append(max(0.0, 1.0 - float(sim)))
                    prev = f
                motion_level = float(np.mean(diffs)) if diffs else 0.0
        except Exception:
            motion_level = 0.0

        onset_density = 0.0
        audio_path = None
        try:
            audio_path = self._extract_audio_tmp(video_path)
            y, sr = librosa.load(audio_path, sr=None, mono=True)
            onset_frames = librosa.onset.onset_detect(y=y, sr=sr, backtrack=True)
            onset_times = librosa.frames_to_time(onset_frames, sr=sr)
            duration = float(ffprobe_duration(pathlib.Path(video_path)) or 0.0) or (float(total_frames) / fps)
            onset_density = float(len(onset_times)) / max(1.0, duration)
        except Exception:
            onset_density = 0.0
        finally:
            try:
                if audio_path and os.path.isfile(audio_path):
                    os.remove(audio_path)
                    d = os.path.dirname(audio_path)
                    if os.path.isdir(d) and not os.listdir(d):
                        os.rmdir(d)
            except Exception:
                pass

        if motion_level >= 0.25:
            cfg["threshold"] = max(float(cfg.get("threshold", 0.6)), 0.65)
            cfg["similarity_threshold"] = max(float(cfg.get("similarity_threshold", 0.87)), 0.9)
            cfg["window_s"] = max(float(cfg.get("window_s", 0.6)), 0.8)
            cfg["min_duration"] = min(float(cfg.get("min_duration", 0.6)), 0.55)
        elif motion_level <= 0.10:
            cfg["threshold"] = min(float(cfg.get("threshold", 0.6)), 0.5)
            cfg["similarity_threshold"] = min(float(cfg.get("similarity_threshold", 0.87)), 0.86)
            cfg["window_s"] = min(float(cfg.get("window_s", 0.6)), 0.5)
            cfg["min_duration"] = max(float(cfg.get("min_duration", 0.6)), 0.65)

        if onset_density >= 0.35:
            cfg["enable_audio_snap"] = True
            cfg["snap_tolerance"] = max(float(cfg.get("snap_tolerance", 0.25)), 0.25)
        else:
            cfg["enable_audio_snap"] = bool(cfg.get("enable_audio_snap", False))

        cfg["min_segment_sec"] = max(0.5, float(cfg.get("min_segment_sec", 0.6)))
        try:
            if cap:
                cap.release()
        except Exception:
            pass
        return cfg

    def _refine_segments(self, video_path: str, segments: List[Tuple[float, float]], fps: float, min_segment_sec: float, sim_threshold: float, window_s: float = 0.5) -> List[Tuple[float, float]]:
        cap = None
        try:
            cap = cv2.VideoCapture(video_path)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        except Exception:
            total_frames = 0

        def read_frame(idx: int):
            try:
                if not cap:
                    return None, False
                idx = max(0, min(int(idx), max(0, total_frames - 1)))
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, f = cap.read()
                return (f if ok else None), ok
            except Exception:
                return None, False

        window_f = int(round(window_s * max(1.0, fps)))
        out: List[Tuple[float, float]] = []
        n = len(segments)
        for i, (ss_i, ee_i) in enumerate(segments):
            start_f = int(round(ss_i * fps))
            end_f = int(round(ee_i * fps))
            prev_f = max(0, start_f - 1)
            next_start_f = end_f + 1
            if i < n - 1:
                next_start_f = int(round(segments[i + 1][0] * fps))

            new_start_f = start_f
            ref_prev, ok_prev = read_frame(prev_f)
            if ok_prev and ref_prev is not None:
                head_limit = min(start_f + window_f, max(start_f, end_f - int(round(min_segment_sec * fps))))
                for c in range(start_f, head_limit + 1):
                    f_c, ok_c = read_frame(c)
                    if ok_c and f_c is not None:
                        sim = self._hist_similarity(ref_prev, f_c)
                        if sim < sim_threshold:
                            new_start_f = c
                            break

            new_end_f = end_f
            if i < n - 1:
                ref_next, ok_next = read_frame(next_start_f)
                if ok_next and ref_next is not None:
                    tail_start = max(start_f + int(round(min_segment_sec * fps)), end_f - window_f)
                    for c in range(end_f, tail_start - 1, -1):
                        f_c, ok_c = read_frame(c)
                        if ok_c and f_c is not None:
                            sim = self._hist_similarity(ref_next, f_c)
                            if sim < sim_threshold:
                                new_end_f = c
                                break

            new_ss = max(ss_i, float(new_start_f) / float(max(1.0, fps)))
            new_ee = min(ee_i, float(new_end_f) / float(max(1.0, fps)))
            if new_ee - new_ss < float(min_segment_sec):
                new_ss = ss_i
                new_ee = ee_i
            out.append((new_ss, new_ee))

        try:
            if cap:
                cap.release()
        except Exception:
            pass
        return out

    def _detect(self, video_path: str) -> Dict[str, Any]:
        fps = self._get_fps(video_path)
        scenes_frames: List[Tuple[int, int]] = []
        scenes_seconds: List[Tuple[float, float]] = []
        try:
            video_frames, single_frame_pred, all_frame_pred = self.model.predict_video(video_path)  # type: ignore[attr-defined]
            try:
                scenes_data = self.model.predictions_to_scenes(single_frame_pred, threshold=self.threshold)  # type: ignore[attr-defined]
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

    def _extract_audio_tmp(self, video_path: str) -> str:
        """提取临时音频 WAV 文件用于音频分析。"""
        base_dir = os.path.dirname(os.path.abspath(video_path))
        tmpdir = os.path.join(base_dir, "temp_detect")
        os.makedirs(tmpdir, exist_ok=True)
        out = os.path.join(tmpdir, f"audio_{uuid.uuid4().hex[:8]}.wav")
        si = None
        kwargs: Dict[str, Any] = {}
        try:
            if os.name == "nt":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
        except Exception:
            kwargs = {}
        cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            video_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "22050",
            out,
        ]
        subprocess.run(cmd, capture_output=True, **kwargs)
        return out

    def _snap_cuts(self, visual_cuts: List[float], audio_cuts: List[float], tolerance: float) -> List[float]:
        """吸附对齐：将视觉切点吸附到附近音频节拍。"""
        if not audio_cuts:
            return visual_cuts
        a = np.sort(np.array(audio_cuts, dtype=float))
        out: List[float] = []
        for v in visual_cuts:
            idx = int(np.searchsorted(a, v))
            candidates: List[float] = []
            if idx < len(a):
                candidates.append(float(a[idx]))
            if idx > 0:
                candidates.append(float(a[idx - 1]))
            best = v
            dist = float("inf")
            for c in candidates:
                d = abs(c - v)
                if d < dist:
                    dist = d
                    best = c
            out.append(best if dist <= tolerance else v)
        return out


    def save(self, video_path: str, output_dir: str = None, **kwargs) -> Dict[str, Any]:
        vp = pathlib.Path(video_path)
        out_dir = pathlib.Path(output_dir or os.path.dirname(video_path)) / "scenes"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        kw = {k: v for k, v in kwargs.items() if v is not None}
        data = self.detect(video_path, **kw)
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
