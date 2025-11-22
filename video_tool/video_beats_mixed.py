from __future__ import annotations

import os
import json
import random
import pathlib
import traceback
import subprocess
import shutil
import time
from typing import Optional, Tuple, List, Dict, Any
from moviepy.editor import AudioFileClip, VideoFileClip
from utils.gpu_detect import is_nvenc_available
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
from utils.xprint import xprint
from utils.common_utils import is_video_file, is_image_file
from utils.calcu_video_info import ffprobe_duration, ffprobe_stream_info

class VideoBeatsMixed:
    """根据 BGM 卡点元数据与用户选择窗口，使用视频/图片素材合成卡点视频。"""

    def __init__(
        self,
        audio_path: str,
        beats_meta: dict,
        media_files: List[str],
        output_dir: str,
        window: Optional[Tuple[float, float]] = None,
        clip_min_interval: Optional[float] = None,
    ) -> None:
        """初始化混剪器。"""
        if (not isinstance(audio_path, str)) or (not audio_path.strip()):
            raise ValueError("audio_path must not be empty")
        if (isinstance(beats_meta, dict) and not beats_meta):
            raise ValueError("beats_meta dict must not be empty")
        if (not isinstance(media_files, list)) or (len(media_files) == 0):
            raise ValueError("media_files must be a non-empty list")
        if (not isinstance(output_dir, str)) or (not output_dir.strip()):
            raise ValueError("output_dir must not be empty")

        self.audio_path = pathlib.Path(audio_path)
        self.media_files = [pathlib.Path(p) for p in media_files]
        self.output_dir = pathlib.Path(output_dir)
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.window = window
        self.meta = beats_meta
        self.clip_min_interval = clip_min_interval
        self.temp_root = self.output_dir.parent / "beats_mixed_temp"
        try:
            self.temp_root.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        ts = int(time.time() * 1000)
        rid = f"clips_{ts}_{random.randint(1000,9999)}"
        self.run_id = rid
        
        self.temp_dir = self.temp_root / self.run_id
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            os.environ.setdefault("TMPDIR", str(self.temp_dir))
            os.environ.setdefault("TEMP", str(self.temp_dir))
            os.environ.setdefault("TMP", str(self.temp_dir))
        except Exception:
            pass
        env = bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffprobe=False)
        self.ffmpeg_bin = env.get("ffmpeg_path") or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_bin = env.get("ffprobe_path") or shutil.which("ffprobe") or "ffprobe"
        try:
            self._use_nvenc = bool(is_nvenc_available())
        except Exception:
            self._use_nvenc = False
    
    def _resolve_window(self) -> Tuple[float, float]:
        """确定卡点窗口：优先使用用户窗口，否则使用元数据中的建议窗口。"""
        if isinstance(self.window, tuple) and len(self.window) == 2:
            s, e = float(self.window[0]), float(self.window[1])
            if e > s:
                return self._clamp_window_to_audio((s, e))
        # 读取 suggestion.highlight
        try:
            h = (self.meta.get("suggestion", {}) or {}).get("highlight", {}) or {}
            # 兼容 start/end 与 start_time/end_time 两种键
            s = float(h.get("start_time", h.get("start", 0.0)))
            e = float(h.get("end_time", h.get("end", max(s, s + 30.0))))
            if e > s:
                return self._clamp_window_to_audio((s, e))
        except Exception:
            pass
        # 回退：整段音频长度或根据 beats 推断
        try:
            dur = float((self.meta.get("meta", {}) or {}).get("duration", 0.0))
            if dur > 0:
                return self._clamp_window_to_audio((0.0, dur))
        except Exception:
            pass
        try:
            beats = [float(x) for x in (self.meta.get("beats") or [])]
            if beats:
                b0 = float(min(beats))
                b1 = float(max(beats))
                if b1 > b0:
                    return self._clamp_window_to_audio((b0, b1))
        except Exception:
            pass
        return self._clamp_window_to_audio((0.0, 30.0))

    def _get_audio_duration(self) -> float:
        try:
            clip = AudioFileClip(str(self.audio_path))
            dur = float(clip.duration or 0.0)
            try:
                clip.close()
            except Exception:
                pass
            return max(0.0, dur)
        except Exception:
            return 0.0

    def _clamp_window_to_audio(self, window: Tuple[float, float]) -> Tuple[float, float]:
        s, e = float(window[0]), float(window[1])
        dur = self._get_audio_duration()
        if dur <= 0.0:
            s = max(0.0, s)
            e = max(s, e)
            return (s, e)
        s = max(0.0, min(s, dur))
        e = max(0.0, min(e, dur))
        if e <= s:
            s = 0.0
            e = dur
        return (s, e)

    def _extract_beats_info(self, window: Tuple[float, float]) -> List[Dict[str, Any]]:
        s, e = window
        beats_info: List[Dict[str, Any]] = []
        mi = 0.33
        try:
            v = self.clip_min_interval
            if v is not None:
                mi = max(0.2, float(v))
        except Exception:
            mi = max(0.2, mi)
        beats_raw = self.meta.get("beats") or []
        try:
            beats = [float(x) for x in beats_raw]
        except Exception:
            beats = []
        beats = [t for t in beats if s <= t < e]
        beats.sort()
        i = 0
        idx = 0
        while i < len(beats):
            t0 = float(beats[i])
            j = i + 1
            cur_end = t0
            while True:
                next_t = float(beats[j]) if j < len(beats) else float(e)
                total = float(next_t - t0)
                if total >= mi:
                    cur_end = min(float(e), float(next_t))
                    break
                if j >= len(beats):
                    cur_end = min(float(e), float(t0 + mi))
                    break
                j += 1

            dur = float(cur_end - t0)
            if dur < mi:
                dur = mi
                cur_end = t0 + dur
            beats_info.append({
                "idx": idx,
                "start_time": t0,
                "end_time": cur_end,
                "duration": max(0.2, dur),
            })
            idx += 1
            i = j
        return beats_info

    def _pick_random_start(self, vdur: float, seg_dur: float) -> float:
        try:
            return float(random.uniform(0.0, max(0.0, vdur - seg_dur)))
        except Exception:
            return 0.0

    def _slice_video_moviepy(self, in_path: pathlib.Path, start: float, duration: float, idx: int) -> pathlib.Path | None:
        outp = self.temp_dir / f"seg_{self.run_id}_{idx:04d}.mp4"
        try:
            v = VideoFileClip(str(in_path))
            vdur = float(v.duration or 0.0)
            end = min(vdur, float(start + duration))
            if end <= start:
                end = min(vdur, start + max(0.2, duration))
            clip = v.subclip(float(start), float(end))
            fps_val = None
            try:
                if getattr(clip, "fps", None):
                    fps_val = int(round(float(clip.fps)))
                elif getattr(v, "fps", None):
                    fps_val = int(round(float(v.fps)))
                else:
                    sinfo = ffprobe_stream_info(pathlib.Path(in_path))
                    fr = str(sinfo.get("r_frame_rate", ""))
                    if fr and "/" in fr:
                        a, b = fr.split("/", 1)
                        fps_val = int(round(float(a) / float(b)))
            except Exception:
                fps_val = None
            kwargs = {
                "codec": "libx264",
                "audio": False,
                "ffmpeg_params": ["-movflags", "+faststart"],
                "logger": None,
            }
            if fps_val and fps_val > 0:
                kwargs["fps"] = int(fps_val)
            clip.write_videofile(str(outp), **kwargs)
            try:
                clip.close()
            except Exception:
                pass
            try:
                v.close()
            except Exception:
                pass
            if outp.exists():
                return outp
        except Exception:
            traceback.print_exc()
            return None

    def _image_to_segment(self, in_path: pathlib.Path, duration: float, idx: int, base_w: int, base_h: int) -> pathlib.Path | None:
        outp = self.temp_dir / f"imgseg_{self.run_id}_{idx:04d}.mp4"
        try:
            # vf = f"scale={base_w}:{base_h}:force_original_aspect_ratio=decrease,pad={base_w}:{base_h}:(ow-iw)/2:(oh-ih)/2:color=black"
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-loop","1",
                "-i", str(in_path),
                "-t", f"{duration:.3f}",
                # "-vf", vf,
                "-r", 25,
                "-pix_fmt","yuv420p",
                "-c:v", "h264_nvenc" if self._use_nvenc else "libx264",
                *( ["-preset","p4","-cq","28"] if self._use_nvenc else ["-preset","slow","-crf","20"] ),
                "-movflags", "+faststart",
                str(outp),
            ]
            si = None
            kwargs = {}
            xprint(f"_image_to_segment: {cmd}")
            try:
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
            except Exception:
                kwargs = {}
            r = subprocess.run(cmd, capture_output=True, **kwargs)
            if r.returncode == 0 and outp.exists():
                return outp
        except Exception:
            traceback.print_exc()
            pass
        return None

    def _concat_segments_copy(self, segs: List[pathlib.Path]) -> pathlib.Path | None:
        lst = self.temp_dir / f"concat_list_{self.run_id}.txt"
        try:
            with open(lst, "w", encoding="utf-8") as f:
                for p in segs:
                    f.write(f"file '{str(p).replace("'", "\\'")}'\n")
        except Exception:
            return None
        outp = self.temp_dir / f"video_no_audio_{self.run_id}.mp4"
        try:
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-f","concat",
                "-safe","0",
                "-i", str(lst),
                # 保持时间戳并避免 vsync 引入的加速/降速
                "-fflags", "+genpts",
                "-vsync", "passthrough",
                "-c","copy",
                "-movflags", "+faststart",
                str(outp),
            ]
            si = None
            kwargs = {}
            xprint(f"_concat_segments_copy: {cmd}")
            try:
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
            except Exception:
                kwargs = {}
            r = subprocess.run(cmd, capture_output=True, **kwargs)
            if r.returncode == 0 and outp.exists():
                return outp
        except Exception:
            traceback.print_exc()
            pass
        return None

    def _make_bgm_segment(self, s: float, e: float) -> pathlib.Path | None:
        outa = self.temp_dir / f"bgm_{self.run_id}.m4a"
        try:
            dur = max(0.0, float(e - s))
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-ss", f"{s:.3f}",
                "-t", f"{dur:.3f}",
                "-i", str(self.audio_path),
                "-c:a","aac",
                "-b:a","192k",
                "-ar","44100",
                str(outa),
            ]
            si = None
            kwargs = {}
            xprint(f"_make_bgm_segment: {cmd}")
            try:
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
            except Exception:
                kwargs = {}
            r = subprocess.run(cmd, capture_output=True, **kwargs)
            if r.returncode == 0 and outa.exists():
                return outa
        except Exception:
            traceback.print_exc()
            pass
        return None

    def _mux_video_audio_copy(self, video_no_audio: pathlib.Path, bgm_path: pathlib.Path, out_path: pathlib.Path) -> pathlib.Path | None:
        try:
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-i", str(video_no_audio),
                "-i", str(bgm_path),
                "-map","0:v:0",
                "-map","1:a:0",
                "-c:v","copy",
                "-c:a","copy",
                "-shortest",
                "-movflags", "+faststart",
                str(out_path),
            ]
            si = None
            kwargs = {}
            xprint(f"_mux_video_audio_copy: {cmd}")
            try:
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
            except Exception:
                kwargs = {}
            r = subprocess.run(cmd, capture_output=True, **kwargs)
            if r.returncode == 0 and out_path.exists():
                return out_path
        except Exception:
            traceback.print_exc()
            pass
        return None

    def _collect_media(self, count: int) -> List[pathlib.Path]:
        """从传入的媒体文件数组中随机抽取指定数量（不足则循环补齐并打乱）。"""
        candidates: List[pathlib.Path] = []
        try:
            for p in self.media_files:
                if p.is_file():
                    name = p.name
                    if is_video_file(name) or is_image_file(name):
                        candidates.append(p)
        except Exception:
            pass
        if not candidates:
            return []
        try:
            random.shuffle(candidates)
        except Exception:
            pass
        picks: List[pathlib.Path] = []
        while len(picks) < count:
            picks.extend(candidates)
            if not candidates:
                break
        picks = picks[:count]
        try:
            random.shuffle(picks)
        except Exception:
            pass
        return picks

    def run(self) -> pathlib.Path | None:
        # 获取切片的窗口范围
        window = self._resolve_window()
        if window is None:
            return None
        # 获取窗口的鼓点信息
        beats_info = self._extract_beats_info(window)
        if not beats_info:
            return None
        # 根据鼓点个数，随机获取视频切片
        picks = self._collect_media(len(beats_info))
        if not picks:
            return None
      
        # 根据鼓点信息+切片视频/图片，生成视频片段（优化视频内存）  
        segs: List[pathlib.Path] = []
        for idx, (info, path) in enumerate(zip(beats_info, picks)):
            dur = max(0.2, float(info.get("duration", 0.5)))
            if is_video_file(path.name):
                vdur = ffprobe_duration(path)
                start = self._pick_random_start(vdur, dur)
                seg = self._slice_video_moviepy(path, start, dur, idx)
            else:
                seg = self._image_to_segment(path, dur, idx)
            if seg is None:
                continue
            segs.append(seg)
        if not segs:
            return None

        video_no_audio = self._concat_segments_copy(segs)
        if video_no_audio is None:
            return None
        s, e = window
        bgm = self._make_bgm_segment(s, e)
        if bgm is None:
            return None
        rand_id = random.randint(100000, 999999)
        out_path = self.output_dir / f"beats_mixed_{rand_id}.mp4"
        final = self._mux_video_audio_copy(video_no_audio, bgm, out_path)

        try:
            if self.temp_dir.exists():
                # 不管目录是否为空，都直接删除
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                xprint(f"_concat_segments_copy: 临时目录 {self.temp_dir} 已删除")
        except Exception:
            traceback.print_exc()
            pass
        # try:
        #     for p in segs:
        #         try:
        #             if p.exists():
        #                 p.unlink()
        #         except Exception:
        #             pass
        #     try:
        #         if video_no_audio and pathlib.Path(video_no_audio).exists():
        #             pathlib.Path(video_no_audio).unlink()
        #     except Exception:
        #         pass
        #     try:
        #         cl = self.temp_dir / f"concat_list_{self.run_id}.txt"
        #         if cl.exists():
        #             cl.unlink()
        #     except Exception:
        #         pass
        #     try:
        #         if bgm and pathlib.Path(bgm).exists():
        #             pathlib.Path(bgm).unlink()
        #     except Exception:
        #         pass
        #     try:
        #         shutil.rmtree(self.temp_dir, ignore_errors=True)
        #     except Exception:
        #         pass
        # except Exception:
        #     pass
        return final


def video_beats_mixed(
    audio_path: str,
    beats_meta: str | dict,
    media_files: List[str],
    output_dir: str,
    window: Optional[Tuple[float, float]] = None,
    clip_min_interval: Optional[float] = None,
) -> pathlib.Path | None:
    """功能函数：生成卡点混剪视频并返回输出路径。"""
    meta_obj: Dict[str, Any]
    if isinstance(beats_meta, str):
        try:
            with open(beats_meta, "r", encoding="utf-8") as f:
                meta_obj = json.load(f)
        except Exception:
            meta_obj = {}
    else:
        meta_obj = beats_meta
    runner = VideoBeatsMixed(audio_path=audio_path, beats_meta=meta_obj, media_files=media_files, output_dir=output_dir, window=window, clip_min_interval=clip_min_interval)
    return runner.run()