from __future__ import annotations

import os
import json
import random
import pathlib
from typing import Optional, Tuple, List, Dict, Any
from moviepy.editor import concatenate_videoclips, AudioFileClip, VideoFileClip, ImageClip
from moviepy.video.fx import all as vfx
from utils.gpu_detect import is_nvenc_available


class VideoBeatsMixed:
    """根据 BGM 卡点元数据与用户选择窗口，使用视频/图片素材合成卡点视频。"""

    def __init__(
        self,
        audio_path: str,
        beats_meta: dict,
        media_files: List[str],
        output_dir: str,
        window: Optional[Tuple[float, float]] = None,
    ) -> None:
        """初始化混剪器。"""
        if (not isinstance(audio_path, str)) or (not audio_path.strip()):
            raise ValueError("audio_path must not be empty")
        if (isinstance(beats_meta, dict) and not beats_meta):
            raise ValueError("beats_meta dict must not be empty")
        if (not isinstance(media_files, list)) or (len(media_files) == 0):
            raise ValueError("media_files must be a non-empty list")
        if not media_files:
            raise ValueError("media_files must not be empty")
        if (not isinstance(output_dir, str)) or (not output_dir.strip()):
            raise ValueError("output_dir must not be empty")

        self.audio_path = pathlib.Path(audio_path)
        self.media_files = [pathlib.Path(p) for p in media_files]
        self.output_dir = pathlib.Path(output_dir)
        self.window = window
        self.meta = beats_meta
        
    @staticmethod
    def _is_video_file(path: str) -> bool:
        """判断是否为视频文件。"""
        ext = os.path.splitext(path)[1].lower()
        return ext in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v"}

    @staticmethod
    def _is_image_file(path: str) -> bool:
        """判断是否为图片文件。"""
        ext = os.path.splitext(path)[1].lower()
        return ext in {".jpg", ".jpeg", ".png", ".bmp"}

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
            from moviepy.editor import AudioFileClip
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
        """从元数据中提取窗口内的卡点信息列表。"""
        s, e = window
        beats_info: List[Dict[str, Any]] = []
        # 若已有预生成的 beats_checkpoint_meta，优先使用
        pre = self.meta.get("beats_checkpoint_meta")
        if isinstance(pre, list) and pre:
            for item in pre:
                try:
                    t0 = float(item.get("start_time", 0.0))
                    dur = float(item.get("duration", 0.0))
                    t1 = float(item.get("end_time", t0 + max(0.2, dur)))
                    if s <= t0 < e:
                        # 若超出窗口，截断到窗口尾
                        t1 = min(t1, e)
                        beats_info.append({
                            "idx": int(item.get("idx", len(beats_info))),
                            "start_time": t0,
                            "end_time": t1,
                            "duration": max(0.2, float(t1 - t0)),
                        })
                except Exception:
                    continue
            if beats_info:
                return beats_info

        # 否则从 beats 列表生成：持续到下一个鼓点或窗口尾
        beats = self.meta.get("beats") or []
        try:
            beats = [float(x) for x in beats]
        except Exception:
            beats = []
        beats = [t for t in beats if s <= t < e]
        beats.sort()
        for i, t in enumerate(beats):
            next_t = beats[i + 1] if (i + 1) < len(beats) else e
            t1 = min(e, float(next_t))
            beats_info.append({
                "idx": i,
                "start_time": float(t),
                "end_time": float(t1),
                "duration": max(0.2, float(t1 - t)),
            })
        return beats_info

    def _collect_media(self, count: int) -> List[pathlib.Path]:
        """从传入的媒体文件数组中随机抽取指定数量（不足则循环补齐并打乱）。"""
        candidates: List[pathlib.Path] = []
        try:
            for p in self.media_files:
                if p.is_file():
                    name = p.name
                    if self._is_video_file(name) or self._is_image_file(name):
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

    def _concat_and_mux(self, clips: List[Any], audio_window: Tuple[float, float], out_path: pathlib.Path) -> pathlib.Path | None:
        """拼接视频片段并与指定窗口的 BGM 写出。"""
        try:
            final = concatenate_videoclips(clips, method="compose")
        except Exception:
            return None

        s, e = audio_window
        try:
            if e > s:
                bgm = AudioFileClip(str(self.audio_path)).subclip(s, e)
                final = final.set_audio(bgm)
        except Exception:
            pass

        try:
            use_nvenc = bool(is_nvenc_available())
        except Exception:
            use_nvenc = False
        codec = "h264_nvenc" if use_nvenc else "libx264"
        ffmpeg_params = ["-preset", "p6", "-cq", "32"] if use_nvenc else ["-preset", "slow", "-crf", "28"]
        try:
            final.write_videofile(
                str(out_path),
                audio_codec="aac",
                codec=codec,
                ffmpeg_params=ffmpeg_params,
                logger=None,
            )
        except Exception:
            return None
        finally:
            try:
                final.close()
            except Exception:
                pass
        return out_path if out_path.exists() else None

    def run(self) -> pathlib.Path | None:
        """执行卡点混剪并输出最终视频路径。"""
        window = self._resolve_window()
        beats_info = self._extract_beats_info(window)
        if not beats_info:
            return None
        picks = self._collect_media(len(beats_info))
        if not picks:
            return None

        clips: List[Any] = []
        for info, path in zip(beats_info, picks):
            dur = max(0.2, float(info.get("duration", 0.5)))
            try:
                if self._is_video_file(path.name):
                    v = VideoFileClip(str(path))
                    vdur = float(v.duration or 0.0)
                    if vdur > dur:
                        start = 0.0
                        try:
                            start = random.uniform(0.0, max(0.0, vdur - dur))
                        except Exception:
                            start = 0.0
                        clip = v.subclip(start, start + dur)
                    else:
                        clip = v.subclip(0, max(0.0, min(dur, vdur)))
                        # 若视频长度不足，循环填充到目标时长
                        try:
                            need = max(0.0, dur - float(clip.duration or 0.0))
                            if need > 0.05:
                                clip = clip.fx(vfx.loop, duration=dur)
                        except Exception:
                            pass
                else:
                    clip = ImageClip(str(path)).set_duration(dur)
                clips.append(clip)
            except Exception:
                continue

        # 对齐总时长到窗口长度：最后一段微调
        try:
            total = sum(float(c.duration or 0.0) for c in clips)
            target = float(window[1] - window[0])
            diff = float(target - total)
            if diff > 0.05:
                last = clips[-1]
                try:
                    from moviepy.video.fx import all as vfx
                    if hasattr(last, "fx"):
                        clips[-1] = last.fx(vfx.loop, duration=float(last.duration or 0.0) + diff)
                    else:
                        clips[-1] = last.set_duration(float(last.duration or 0.0) + diff)
                except Exception:
                    clips[-1] = last.set_duration(float(last.duration or 0.0) + diff)
            elif diff < -0.05:
                i = len(clips) - 1
                remain = -diff
                while i >= 0 and remain > 0.05:
                    cur = clips[i]
                    curd = float(cur.duration or 0.0)
                    shrink = min(curd - 0.2, remain)
                    if shrink > 0.0:
                        try:
                            newd = max(0.2, curd - shrink)
                            clips[i] = cur.subclip(0, newd)
                        except Exception:
                            clips[i] = cur.set_duration(max(0.2, curd - shrink))
                        remain -= shrink
                    i -= 1
        except Exception:
            pass

        # 输出文件名
        rand_id = random.randint(100000, 999999)
        out_path = self.output_dir / f"beats_mixed_{rand_id}.mp4"
        return self._concat_and_mux(clips, window, out_path)


def video_beats_mixed(
    audio_path: str,
    beats_meta: str | dict,
    media_files: List[str],
    output_dir: str,
    window: Optional[Tuple[float, float]] = None,
) -> pathlib.Path | None:
    """功能函数：生成卡点混剪视频并返回输出路径。"""
    runner = VideoBeatsMixed(audio_path=audio_path, beats_meta=beats_meta, media_files=media_files, output_dir=output_dir, window=window)
    return runner.run()