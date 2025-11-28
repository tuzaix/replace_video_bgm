from __future__ import annotations

import os
import uuid
import shutil
import subprocess
from typing import Optional, List, Dict, Any, Tuple
import threading

import traceback
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
from utils.xprint import xprint
from video_tool.slice_config import SliceConfig
import pathlib
from utils.calcu_video_info import ffprobe_duration
from utils.calcu_video_info import ffprobe_stream_info
from video_tool.video_subtitles import VideoSubtitles
from video_tool.subtitles_overlay import overlay_ass_subtitles
from video_tool.ass_builder import srt_to_ass_with_highlight
import torch  # type: ignore
import cv2  # type: ignore
from PIL import Image  # type: ignore
from transformers import AutoProcessor, AutoModelForCausalLM  # type: ignore
from faster_whisper import WhisperModel  # type: ignore
from pydub import AudioSegment
from pydub.utils import make_chunks

env = bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffmpeg=True)
ffprobe_bin = env.get("ffprobe_path") or shutil.which("ffprobe")
ffmpeg_bin = env.get("ffmpeg_path") or shutil.which("ffmpeg")

class BroadcastVideoSlices:
    """直播长视频智能切片，支持场景化模式与基础模式。

    提供三类场景化模式：`ecommerce`、`game`、`entertainment`，以及兼容的基础模式：`speech`、`performance`。
    - 场景化模式融合语音语义、音频能量、关键词打分与动态前后摇。
    - 基础模式保持原有语义驱动或能量驱动逻辑以兼容旧用法。
    """

    _MODEL_CACHE: Dict[str, Any] = {}
    _VISION_CACHE: Dict[str, Tuple[Any, Any, str]] = {}
    _CACHE_LOCK: threading.Lock = threading.Lock()

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: str = "auto",
        models_root: Optional[str] = None,
    ) -> None:
        """初始化切片器并加载 Whisper 模型。

        参数
        ----
        model_size: Whisper 模型大小（例如 "large-v3"、"medium"、"small"），缺省自动选择
        device: 运行设备（"auto"/"cuda"/"cpu"），默认自动选择
        models_root: 模型基础目录，需包含子目录 faster_wishper 与 florence2
        """
        self._WhisperModel = WhisperModel  # type: ignore
        self.model_size = model_size or self._auto_select_model_size()
        self.device = self._auto_pick_device(device)
        models_root
        if not models_root:
            raise ValueError("未指定模型基础目录。请通过 --models-root 提供。")
        self.models_root = os.path.abspath(models_root)
        self.whisper_model_dir_base = os.path.join(self.models_root, "faster_wishper")
        whisper_model_path = self._pick_model_dir(self.whisper_model_dir_base, self._map_model_to_repo(self.model_size))
        self.compute_type = "float16" if self.device == "cuda" else "int8"

        # wishper 模型
        self.model = self._get_or_create_model(whisper_model_path, self.device, self.compute_type)

        # 加载 Florence-2 模型 路径
        self.vision_model_id = os.path.join(self.models_root, "florence2")

        self.keywords_config = SliceConfig.KEYWORDS_CONFIG
        xprint({
            "phase": "init",
            "model_size": self.model_size,
            "device": self.device,
            "compute_type": self.compute_type,
            "models_root": self.models_root,
            "whisper_root": self.whisper_model_dir_base,
            "vision_model": self.vision_model_id,
        })

    def _auto_pick_device(self, device: str) -> str:
        """自动选择运行设备。"""
        if device != "auto":
            return device
        try:
            return "cuda" if (torch and torch.cuda.is_available()) else "cpu"
        except Exception:
            return "cpu"

    def _gpu_info(self) -> Tuple[bool, float]:
        """返回 GPU 是否可用及显存大小（GB）。"""
        try:
            if torch and torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                vram_gb = float(getattr(props, "total_memory", 0)) / (1024 ** 3)
                return True, vram_gb
        except Exception:
            pass
        return False, 0.0

    def _vision_available(self) -> bool:
        """检测视觉验证依赖是否可用。"""
        return bool(torch) and bool(cv2) and bool(Image) and bool(AutoProcessor) and bool(AutoModelForCausalLM)

    def _build_vision_models(self) -> Tuple[Any, Any, str]:
        """构建或复用 Florence-2 模型与处理器并做单例缓存。"""
        if AutoProcessor is None or AutoModelForCausalLM is None:
            raise ImportError("transformers 未安装或不可用")
        device = "cuda" if (torch and torch.cuda.is_available()) else "cpu"
        model_id = str(self.vision_model_id)
        key = f"{os.path.abspath(model_id)}|{device}"
        cached = self._VISION_CACHE.get(key)
        if cached:
            return cached
        with self._CACHE_LOCK:
            cached2 = self._VISION_CACHE.get(key)
            if cached2:
                return cached2
            kwargs: Dict[str, Any] = {"trust_remote_code": True, "attn_implementation": "eager"}
            if torch and torch.cuda.is_available():
                try:
                    kwargs["dtype"] = torch.float16
                except Exception:
                    pass
            model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs).to(device).eval()
            processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
            tup = (model, processor, device)
            self._VISION_CACHE[key] = tup
            return tup

    def _analyze_frame_caption(self, processor: Any, model: Any, device: str, pil_image: Any) -> str:
        """使用 Florence-2 生成详细画面描述。"""
        task_prompt = "<MORE_DETAILED_CAPTION>"
        inputs = processor(text=task_prompt, images=pil_image, return_tensors="pt").to(device)
        with torch.no_grad():
            ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=512,
                do_sample=False,
                num_beams=2,
            )
        text = processor.batch_decode(ids, skip_special_tokens=False)[0]
        out = processor.post_process_generation(text, task=task_prompt, image_size=(pil_image.width, pil_image.height))
        return str(out.get(task_prompt, ""))

    def filter_clips_by_vision(self, video_path: str, clips: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
        """利用视觉模型过滤片段：抽取中帧，生成描述并按模式视觉关键词匹配。若依赖缺失，直接返回原片段。"""
        xprint({"==phase": "vision_filter", "video": video_path, "clips": len(clips), "mode": mode, "vision_available": self._vision_available()})
        if not clips:
            return clips
        if not self._vision_available():
            return clips
        try:
            model, processor, device = self._build_vision_models()
            cfg = self.keywords_config.get(mode, self.keywords_config["ecommerce"])
            keys = [str(k).lower() for k in cfg.get("visual_keywords", [])]
            cap = cv2.VideoCapture(video_path)
            filtered: List[Dict[str, Any]] = []
            xprint({"phase": "vision_filter_start", "clips": len(clips), "mode": mode})
            for c in clips:
                mid = (float(c.get("start", 0.0)) + float(c.get("end", 0.0))) / 2.0
                cap.set(cv2.CAP_PROP_POS_MSEC, mid * 1000.0)
                ok, frame = cap.read()
                if not ok:
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb)
                desc = self._analyze_frame_caption(processor, model, device, pil)
                hit = any(k in desc.lower() for k in keys) if keys else True
                if hit:
                    c["visual_desc"] = desc
                    filtered.append(c)
            cap.release()
            xprint({"phase": "vision_filter_done", "kept": len(filtered)})
            return filtered
        except Exception as e:
            xprint({"phase": "vision_filter_error", "error": str(e)})
            return clips

    def _use_nvenc(self, prefer: bool = True) -> bool:
        """是否使用 NVENC。若 prefer 为 True 且检测到 CUDA 可用则返回 True。"""
        if not prefer:
            return False
        try:
            import torch  # type: ignore
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def _ffmpeg_encode_args(self, use_nvenc: bool, crf: int) -> List[str]:
        """根据是否启用 NVENC 生成编码参数。"""
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

    def _probe_media_duration(self, path: str) -> float:
        try:
            return float(ffprobe_duration(pathlib.Path(path)) or 0.0)
        except Exception:
            return 0.0

    def _ass_color(self, hex_rgb: str) -> str:
        try:
            h = hex_rgb.strip().lstrip("#")
            r = int(h[0:2], 16)
            g = int(h[2:4], 16)
            b = int(h[4:6], 16)
            return f"&H{b:02X}{g:02X}{r:02X}&"
        except Exception:
            return "&H00FFFFFF&"

    def _srt_time_to_ass(self, t: str) -> str:
        try:
            hh, mm, rest = t.split(":")
            ss, ms = rest.split(",")
            cs = int(round(int(ms) / 10.0))
            return f"{int(hh)}:{int(mm):02d}:{int(ss):02d}.{int(cs):02d}"
        except Exception:
            return "0:00:00.00"

    def _ass_escape(self, s: str) -> str:
        t = str(s or "")
        t = t.replace("\r", "")
        t = t.replace("\n", "\\N")
        return t

    def _ff_filter_escape_path(self, path: str) -> str:
        p = os.path.abspath(path)
        p = p.replace("\\", "/")
        p = p.replace("'", "\\'")
        p = p.replace(":", "\\:")
        return p

    def _srt_to_ass_with_highlight(self, srt_path: str, ass_path: str, video_path: str, mode: str, style_cfg: Dict[str, Any]) -> str:
        try:
            kw_cfg = self.keywords_config.get(mode, self.keywords_config.get("ecommerce", {}))
            kws = list(kw_cfg.get("high", [])) + list(kw_cfg.get("mid", []))
            info = ffprobe_stream_info(pathlib.Path(video_path))
            w = int(info.get("width", 1920) or 1920)
            h = int(info.get("height", 1080) or 1080)
            font = str(style_cfg.get("font_name", "Microsoft YaHei"))
            fsize = int(style_cfg.get("font_size", 42))
            primary = self._ass_color(str(style_cfg.get("primary_color", "#FFFFFF")))
            secondary = primary
            outlinec = self._ass_color(str(style_cfg.get("outline_color", "#000000")))
            backc = self._ass_color(str(style_cfg.get("back_color", "#000000")))
            outline = int(style_cfg.get("outline", 2))
            shadow = int(style_cfg.get("shadow", 0))
            align = int(style_cfg.get("alignment", 2))
            margin_v = int(style_cfg.get("margin_v", 30))
            enc = int(style_cfg.get("encoding", 1))
            hi_color = self._ass_color(str(style_cfg.get("highlight_color", "#FFE400")))

            with open(srt_path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            header = []
            header.append("[Script Info]")
            header.append("Script Type: v4.00+")
            header.append(f"PlayResX: {w}")
            header.append(f"PlayResY: {h}")
            header.append("ScaledBorderAndShadow: yes")
            header.append("")
            header.append("[V4+ Styles]")
            header.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
            header.append(f"Style: Default,{font},{fsize},{primary},{secondary},{outlinec},{backc},0,0,0,0,100,100,0,0,1,{outline},{shadow},{align},20,20,{margin_v},{enc}")
            header.append("")
            header.append("[Events]")
            header.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

            events = []
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
                    text_buf = []
                    while i < len(lines) and lines[i].strip():
                        text_buf.append(lines[i])
                        i += 1
                    text = "\n".join(text_buf)
                    ass_text = self._ass_escape(text)
                    for kw in sorted(kws, key=lambda x: len(x), reverse=True):
                        k = str(kw)
                        if not k:
                            continue
                        ass_text = ass_text.replace(k, f"{'{\\c'}{hi_color}{'}'}{k}{'{\\c'}{primary}{'}'}")
                    ev = f"Dialogue: 0,{self._srt_time_to_ass(t1)},{self._srt_time_to_ass(t2)},Default,,0,0,0,,{ass_text}"
                    events.append(ev)
                except Exception:
                    i += 1
                    continue
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write("\n".join(header + events))
            return ass_path
        except Exception:
            return ass_path

    def _overlay_subtitles(self, src_path: str, ass_path: str, use_nvenc: bool, crf: int) -> str:
        return overlay_ass_subtitles(src_path=src_path, ass_path=ass_path, out_path=None, use_nvenc=use_nvenc, crf=crf)

    def _auto_select_model_size(self) -> str:
        """根据硬件环境选择模型大小。"""
        gpu, vram = self._gpu_info()
        if gpu:
            if vram >= 8.0:
                return "large-v3"
            if vram >= 4.0:
                return "medium"
            return "small"
        return "medium"

    def _map_model_to_repo(self, size: str) -> str:
        """将模型大小映射为仓库标识。"""
        mapping = {
            "large-v3": "Systran/faster-whisper-large-v3",
            "medium": "Systran/faster-whisper-medium",
            "small": "Systran/faster-whisper-small",
            "base": "Systran/faster-whisper-base",
            "tiny": "Systran/faster-whisper-tiny",
        }
        normalized = (size or "medium").strip().lower()
        return mapping.get(normalized, f"Systran/faster-whisper-{normalized}")

    def _pick_model_dir(self, base_dir: str, repo_id: str) -> str:
        """在根目录下寻找仓库子目录，或直接验证根目录为模型目录。"""
        head, tail = repo_id.split("/")
        cand = os.path.join(base_dir, head, tail)
        if os.path.isdir(cand):
            return cand
        if os.path.isdir(base_dir):
            has_bin = os.path.isfile(os.path.join(base_dir, "model.bin"))
            has_cfg = os.path.isfile(os.path.join(base_dir, "config.json"))
            if has_bin or has_cfg:
                return base_dir
        raise FileNotFoundError(f"未找到模型目录: {base_dir}（期望包含 {head}/{tail} 或直接为模型目录）")

    def _get_or_create_model(self, model_dir: str, device: str, compute_type: str) -> Any:
        """获取或创建 Whisper 模型实例，内部维护单例缓存。"""
        key = f"{os.path.abspath(model_dir)}|{device}|{compute_type}"
        m = self._MODEL_CACHE.get(key)
        if m is not None:
            return m
        with self._CACHE_LOCK:
            m2 = self._MODEL_CACHE.get(key)
            if m2 is not None:
                return m2
            inst = self._WhisperModel(model_dir, device=device, compute_type=compute_type)
            self._MODEL_CACHE[key] = inst
            return inst

    def _extract_audio(self, video_path: str, temp_root: Optional[str] = None) -> Tuple[str, str]:
        """从视频提取临时音频 MP3，返回 (音频路径, 临时目录)。"""
        base_dir = os.path.dirname(os.path.abspath(video_path))
        root = temp_root or base_dir
        tmpdir = os.path.join(root, "temp_slices", uuid.uuid4().hex[:8])
        os.makedirs(tmpdir, exist_ok=True)
        audio_path = os.path.join(tmpdir, "audio.mp3")
        in_arg = f"file:{os.path.abspath(video_path).replace('\\', '/')}"
        xprint({"phase": "extract_audio", "video": video_path, "audio_out": audio_path})
        r = subprocess.run([
            ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            in_arg,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-ab",
            "64k",
            "-loglevel",
            "error",
            audio_path,
        ], capture_output=True)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="ignore")
            raise RuntimeError(f"提取音频失败: {err}")
        xprint({"phase": "extract_audio_done", "audio_path": audio_path})
        return audio_path, tmpdir

    def _get_audio_peaks(self, audio: AudioSegment, chunk_len_ms: int = 500, threshold_ratio: float = 1.8) -> List[float]:
        """计算音频 RMS 高能片段时间点（秒）。

        参数
        ----
        audio: 音频对象
        chunk_len_ms: 分块长度（毫秒）
        threshold_ratio: 阈值系数（高能阈值 = 平均 RMS × 系数）
        """
        chunks = make_chunks(audio, chunk_len_ms)
        rms_list = [float(c.rms or 0) for c in chunks] if chunks else []
        avg = (sum(rms_list) / float(len(rms_list))) if rms_list else 0.0
        threshold = avg * float(threshold_ratio)
        peaks: List[float] = []
        for i, rms in enumerate(rms_list):
            if rms > threshold:
                peaks.append(i * (chunk_len_ms / 1000.0))
        xprint({
            "phase": "audio_peaks",
            "avg_rms": round(avg, 3),
            "threshold": round(threshold, 3),
            "peaks": len(peaks),
        })
        return peaks

    

    

    

    def _merge_overlapping_clips(self, clips: List[Dict[str, Any]], gap_tol: float = 2.0) -> List[Dict[str, Any]]:
        """合并时间重叠或相邻的片段。"""
        if not clips:
            return []
        xprint({"phase": "merge", "input": len(clips), "gap_tol": gap_tol})
        sorted_clips = sorted(clips, key=lambda x: float(x.get("start", 0.0)))
        merged: List[Dict[str, Any]] = []
        cur = dict(sorted_clips[0])
        for nx in sorted_clips[1:]:
            if float(nx.get("start", 0.0)) < float(cur.get("end", 0.0)) + float(gap_tol):
                cur["end"] = max(float(cur.get("end", 0.0)), float(nx.get("end", 0.0)))
                cur["duration"] = float(cur["end"]) - float(cur.get("start", 0.0))
                cur["text"] = str(cur.get("text", "")) + " | " + str(nx.get("text", ""))
            else:
                merged.append(cur)
                cur = dict(nx)
        merged.append(cur)
        xprint({"phase": "merge_done", "output": len(merged)})
        return merged

    def analyze_content(self, video_path: str, mode: str = "ecommerce", language: str = "zh") -> List[Dict[str, Any]]:
        """场景化综合分析：采用“锚点扩张”策略并应用强制时长上限与密度过滤。"""
        config = self.keywords_config.get(mode, self.keywords_config["ecommerce"])
        xprint({"phase": "analyze_content_start", "mode": mode})
        segments, _ = self.model.transcribe(video_path, beam_size=5, language=language, vad_filter=True)
        seg_list = list(segments)
        xprint({"phase": "asr_segments", "count": len(seg_list), "language": language})
        if not seg_list:
            return []

        peaks: List[float] = []
        if mode == "game":
            audio_path, tmpdir = self._extract_audio(video_path)
            try:
                audio = AudioSegment.from_file(audio_path)
                peaks = self._get_audio_peaks(audio, chunk_len_ms=500, threshold_ratio=1.8)
            finally:
                try:
                    shutil.rmtree(tmpdir)
                except Exception:
                    pass
            xprint({"phase": "energy_peaks", "count": len(peaks)})

        def _nearest_seg_index(ts: float) -> Optional[int]:
            best_i = None
            best_diff = 1e9
            for i, s in enumerate(seg_list):
                st = float(getattr(s, "start", 0.0) or 0.0)
                ed = float(getattr(s, "end", st) or st)
                mid = (st + ed) / 2.0
                diff = abs(mid - ts)
                if diff < best_diff:
                    best_diff = diff
                    best_i = i
            return best_i

        anchors: List[int] = []
        for i, seg in enumerate(seg_list):
            text = str(getattr(seg, "text", ""))
            if any((kw in text) for kw in config["high"]):
                anchors.append(i)
        if mode == "game" and peaks:
            for t in peaks:
                idx = _nearest_seg_index(t)
                if idx is not None:
                    anchors.append(idx)
        anchors = sorted(set(anchors))
        xprint({"phase": "anchors", "count": len(anchors)})
        if not anchors:
            return []

        pre = float(config.get("pre_roll", config.get("lookback", 1.0)))
        post = float(config.get("post_roll", config.get("padding", 0.5)))
        raw_windows: List[Dict[str, Any]] = []
        for idx in anchors:
            s = seg_list[idx]
            st = float(getattr(s, "start", 0.0) or 0.0)
            ed = float(getattr(s, "end", st) or st)
            win = {"start": max(0.0, st - pre), "end": ed + post, "anchor_text": str(getattr(s, "text", ""))}
            raw_windows.append(win)

        def _merge_overlapping_windows(windows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if not windows:
                return []
            ws = sorted(windows, key=lambda x: float(x["start"]))
            merged: List[Dict[str, Any]] = []
            cur = dict(ws[0])
            for nx in ws[1:]:
                if float(nx["start"]) < float(cur["end"]):
                    cur["end"] = max(float(cur["end"]), float(nx["end"]))
                    cur["anchor_text"] = str(cur.get("anchor_text", "")) + " | " + str(nx.get("anchor_text", ""))
                else:
                    merged.append(cur)
                    cur = dict(nx)
            merged.append(cur)
            return merged

        merged = _merge_overlapping_windows(raw_windows)
        xprint({"phase": "merged_windows", "count": len(merged)})

        max_hard = float(config.get("max_hard_limit", 60.0))
        min_dur = float(config.get("min_duration", 10.0))
        min_hits = int(config.get("min_keyword_hits", 1))

        # 计算密度（关键词/能量）与硬上限
        def _density_hits(w: Dict[str, Any]) -> int:
            s = float(w["start"]); e = float(w["end"]) 
            hits = 0
            for seg in seg_list:
                st = float(getattr(seg, "start", 0.0) or 0.0)
                ed = float(getattr(seg, "end", st) or st)
                if st >= s and ed <= e:
                    txt = str(getattr(seg, "text", ""))
                    if any(kw in txt for kw in config["high"]):
                        hits += 1
                    elif any(kw in txt for kw in config["mid"]):
                        hits += 1
            if mode == "game" and hits < min_hits:
                for t in peaks:
                    if s <= t <= e:
                        hits += 1
                        break
            return hits

        clips: List[Dict[str, Any]] = []
        for w in merged:
            dur = float(w["end"]) - float(w["start"]) 
            if dur > max_hard:
                w["end"] = float(w["start"]) + max_hard
                dur = max_hard
            if dur < min_dur:
                continue
            if _density_hits(w) < min_hits:
                continue
            clips.append({
                "start": float(w["start"]),
                "end": float(w["end"]),
                "duration": float(w["end"]) - float(w["start"]),
                "text": str(w.get("anchor_text", "")),
                "type": "highlight",
            })
        xprint({"phase": "clips_built", "count": len(clips)})
        return self._merge_overlapping_clips(clips, gap_tol=2.0)

    def analyze_jumpcut(self, video_path: str, mode: str = "ecommerce", language: str = "zh") -> List[List[Any]]:
        """离散聚合跳剪：根据关键词筛选并保留上下文，按时间邻近聚类。

        返回多个聚类，每个聚类由若干 ASR 段构成，将被拼接为一个输出短视频。
        """
        config = self.keywords_config.get(mode, self.keywords_config["ecommerce"])
        xprint({"phase": "jumpcut_start", "mode": mode})
        segments, _ = self.model.transcribe(video_path, beam_size=5, language=language, vad_filter=True)
        seg_list = list(segments)
        xprint({"phase": "jumpcut_asr", "segments": len(seg_list)})
        if not seg_list:
            return []

        keywords = list(config.get("high", [])) + list(config.get("mid", []))
        valuable: set[int] = set()
        for i, seg in enumerate(seg_list):
            txt = str(getattr(seg, "text", ""))
            if any(k in txt for k in keywords):
                valuable.add(i)
                if i > 0:
                    valuable.add(i - 1)
                if i < len(seg_list) - 1:
                    valuable.add(i + 1)
        idxs = sorted(list(valuable))
        xprint({"phase": "jumpcut_selected_indices", "count": len(idxs)})
        if not idxs:
            return []

        max_gap = float(config.get("max_cluster_gap", 60.0))
        max_out = float(config.get("max_output_duration", 60.0))
        min_out = float(config.get("min_output_duration", 10.0))

        clusters: List[List[Any]] = []
        cur: List[Any] = [seg_list[idxs[0]]]
        cur_dur = float((cur[0].end or cur[0].start) - (cur[0].start or 0.0))
        last_idx = idxs[0]
        for i in idxs[1:]:
            seg = seg_list[i]
            prev = seg_list[last_idx]
            gap = float((seg.start or 0.0) - (prev.end or 0.0))
            new_dur = float(cur_dur + ((seg.end or seg.start) - (seg.start or 0.0)))
            if gap < max_gap and new_dur < max_out:
                cur.append(seg)
                cur_dur = new_dur
            else:
                if cur and cur_dur >= min_out:
                    clusters.append(cur)
                cur = [seg]
                cur_dur = float((seg.end or seg.start) - (seg.start or 0.0))
            last_idx = i

        if cur and cur_dur >= min_out:
            clusters.append(cur)
        xprint({"phase": "jumpcut_clusters", "count": len(clusters)})
        return clusters

    def _render_jump_cuts(self, video_path: str, output_dir: str, clusters: List[List[Any]], crf: int = 23, use_nvenc: bool = False) -> List[str]:
        """渲染跳剪输出：将离散句子片段重编码后用 concat 合并为短视频。"""
        os.makedirs(output_dir, exist_ok=True)
        name = os.path.splitext(os.path.basename(video_path))[0]
        temp_dir = os.path.join(output_dir, "temp_jump_chunks")
        if os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
        os.makedirs(temp_dir, exist_ok=True)
        outs: List[str] = []
        original_duration = self._probe_media_duration(video_path)
        total_export_duration = 0.0
        xprint({"phase": "jumpcut_render_start", "clusters": len(clusters)})
        for i, cluster in enumerate(clusters):
            concat_list_path = os.path.join(temp_dir, f"concat_list_{i}.txt")
            chunk_paths: List[str] = []
            with open(concat_list_path, "w", encoding="utf-8") as f_list:
                for j, seg in enumerate(cluster):
                    start = max(0.0, float(seg.start or 0.0) - 0.1)
                    duration = float((seg.end or start) - start) + 0.1
                    chunk_name = f"chunk_{i}_{j}.mp4"
                    chunk_path = os.path.join(temp_dir, chunk_name)
                    cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-ss", f"{start:.3f}",
                        "-t", f"{duration:.3f}",
                        "-i", video_path,
                    ] + self._ffmpeg_encode_args(use_nvenc, int(crf)) + [
                        "-loglevel", "error",
                        chunk_path,
                    ]
                    subprocess.run(cmd)
                    abs_chunk = os.path.abspath(chunk_path).replace("\\", "/")
                    f_list.write(f"file '{abs_chunk}'\n")
                    chunk_paths.append(chunk_path)
            out_name = f"{name}_jumpcut_{i + 1:03d}.mp4"
            out_path = os.path.join(output_dir, out_name)
            cmd_concat = [
                ffmpeg_bin,
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                "-loglevel", "error",
                out_path,
            ]
            subprocess.run(cmd_concat)
            seg_dur = 0.0
            try:
                seg_dur = float(ffprobe_duration(pathlib.Path(out_path)) or 0.0)
            except Exception:
                seg_dur = 0.0
            xprint({"phase": "jumpcut_render_done", "index": i + 1, "out": out_path, "chunks": len(chunk_paths), "duration": round(seg_dur, 3)})
            outs.append(out_path)
            total_export_duration += seg_dur
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass
        coverage = (total_export_duration / original_duration) if original_duration > 0 else 0.0
        xprint({
            "phase": "jumpcut_duration_compare",
            "original_sec": round(original_duration, 3),
            "export_total_sec": round(total_export_duration, 3),
            "diff_sec": round(original_duration - total_export_duration, 3),
            "coverage": round(coverage, 4),
        })
        xprint({"phase": "jumpcut_all_done", "outputs": len(outs)})
        return outs

    def cut_video(self, video_path: str, output_dir: Optional[str] = None, mode: str = "ecommerce", **kwargs: Any) -> List[str]:
        """执行切片并返回输出文件路径列表。默认输出目录为视频同名目录。

        当 `mode` 为场景化模式（`ecommerce`/`game`/`entertainment`）时，使用融合算法生成高光片段并采用 `libx264+aac` 重编码导出，以保证切割精确与兼容性。
        另支持场景化聚合模式 `jumpcut`。
        """
        name = os.path.splitext(os.path.basename(video_path))[0]
        if not output_dir:
            output_dir = os.path.join(os.path.dirname(os.path.abspath(video_path)), name)
        os.makedirs(output_dir, exist_ok=True)
        if mode in {"ecommerce", "game", "entertainment"}:
            language = str(kwargs.get("language", "zh"))
            clips = self.analyze_content(video_path, mode=mode, language=language)
            if bool(kwargs.get("vision_verify", False)):
                clips = self.filter_clips_by_vision(video_path, clips, mode)
        elif mode == "jumpcut":
            language = str(kwargs.get("language", "zh"))
            profile = str(kwargs.get("profile", "ecommerce"))
            clusters = self.analyze_jumpcut(video_path, mode=profile, language=language)
            return self._render_jump_cuts(
                video_path,
                output_dir,
                clusters,
                crf=int(kwargs.get("crf", 23)),
                use_nvenc=self._use_nvenc(bool(kwargs.get("use_nvenc", True))),
            )
        else:
            raise ValueError("mode 需为 'ecommerce'、'game'、'entertainment' 或 'jumpcut'")
        outs: List[str] = []
        original_duration = self._probe_media_duration(video_path)
        total_export_duration = 0.0
        xprint({
            "phase": "cut_start",
            "mode": mode,
            "clips": len(clips),
            "video": video_path,
            "output_dir": output_dir,
            "original_duration": round(original_duration, 3),
        })
        for idx, c in enumerate(clips):
            start = float(c["start"]) if c else 0.0
            end = float(c["end"]) if c else start
            duration = max(0.0, end - start)
            if duration < float(kwargs.get("min_export_sec", 0.0)):
                continue
            xprint({
                "phase": "slice_debug",
                "index": idx + 1,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(duration, 3),
            })
            out_name = f"{name}_{mode}_{idx + 1:03d}.mp4"
            out_path = os.path.join(output_dir, out_name)
            use_nvenc = self._use_nvenc(bool(kwargs.get("use_nvenc", True)))
            crf = int(kwargs.get("crf", 23))
            cmd = [
                ffmpeg_bin,
                "-y",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{duration:.3f}",
                "-i",
                video_path,
            ] + self._ffmpeg_encode_args(use_nvenc, crf) + [
                "-loglevel",
                "error",
                out_path,
            ]
            xprint({"cmd": cmd})
            subprocess.run(cmd)
            xprint({"phase": "export_done", "index": idx + 1, "out": out_path, "duration": round(duration, 3)})
            final_path = out_path
            # 增加字幕
            try:
                if bool(kwargs.get("add_subtitles", True)):
                    vs = VideoSubtitles(model_size=self.model_size, device=self.device, model_path=self.whisper_model_dir_base, existing_model=self.model)
                    kw_cfg = self.keywords_config.get(mode, self.keywords_config.get("ecommerce", {}))
                    domain_words = [str(w) for w in (list(kw_cfg.get("high", [])) + list(kw_cfg.get("mid", [])))]
                    initial_prompt = " ".join(domain_words) if domain_words else None
                    srt_path = vs.save_srt(
                        final_path,
                        output_srt_path=os.path.join(output_dir, "subtitles"),
                        translate=bool(kwargs.get("translate", False)),
                        simplify_chinese=True,
                        language=str(kwargs.get("language", "zh")),
                        beam_size=int(kwargs.get("beam_size", 7)),
                        best_of=int(kwargs.get("best_of", 5)),
                        temperature=float(kwargs.get("temperature", 0.0)),
                        initial_prompt=initial_prompt,
                    )
                    style_cfg = SliceConfig.SUBTITLE_STYLE
                    ass_path = os.path.splitext(srt_path)[0] + ".ass"
                    max_cpl = kwargs.get("max_chars_per_line", 14)
                    srt_to_ass_with_highlight(
                        srt_path=srt_path,
                        ass_path=ass_path,
                        video_path=final_path,
                        mode=mode,
                        style_cfg=style_cfg,
                        keywords_cfg=self.keywords_config.get(mode, self.keywords_config.get("ecommerce", {})),
                        max_chars_per_line=max_cpl,
                    )
                    use_nvenc = self._use_nvenc(bool(kwargs.get("use_nvenc", True)))
                    crf = int(kwargs.get("crf", 23))
                    subbed = self._overlay_subtitles(final_path, ass_path, use_nvenc, crf)
                    if os.path.isfile(subbed):
                        final_path = subbed
            except Exception as e:
                traceback.print_exc()
                xprint({"phase": "subtitle_error", "index": idx + 1, "error": str(e)})
            outs.append(final_path)
            total_export_duration += duration
        coverage = (total_export_duration / original_duration) if original_duration > 0 else 0.0
        xprint({
            "phase": "duration_compare",
            "original_sec": round(original_duration, 3),
            "export_total_sec": round(total_export_duration, 3),
            "diff_sec": round(original_duration - total_export_duration, 3),
            "coverage": round(coverage, 4),
        })
        xprint({"phase": "cut_done", "outputs": len(outs)})
        return outs

def slice_broadcast_video(
    video_path: str,
    out_dir: Optional[str] = None,
    mode: str = "ecommerce",
    model_size: Optional[str] = None,
    device: str = "auto",
    models_root: Optional[str] = None,
    **kwargs: Any,
) -> List[str]:
    """统一接口：执行直播长视频智能切片。

    参数
    ----
    video_path: 输入视频文件路径
    out_dir: 输出目录，默认在视频同目录创建同名子目录
    mode: 切片模式：`ecommerce`、`game`、`entertainment` 或 `jumpcut`
    model_size: Whisper 模型大小（默认自动）
    device: 运行设备（auto/cuda/cpu）
    models_root: 模型基础目录（包含 faster_wishper 与 florence2 子目录）
    其余参数与模式相关
    """
    slicer = BroadcastVideoSlices(
        model_size=model_size,
        device=device,
        models_root=models_root,
    )
    return slicer.cut_video(video_path=video_path, output_dir=out_dir, mode=mode, **kwargs)
