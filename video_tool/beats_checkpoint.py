"""
Beats Checkpoint

使用 Demucs + Librosa 在音频的鼓点上进行卡点检测，并生成可视化所需的元数据。

功能
----
- 使用 Demucs 仅分离出 `drums.wav`，针对鼓点进行精准检测。
- 基于 Librosa 的 Onset 检测，并提供固定/动态间隔过滤，提升视觉舒适度。
- 生成归一化的波形数据（RMS 0..1），用于前端可视化。
- 扫描整段，给出 30 秒“卡点最密集”建议片段。

默认目录
--------
- 输出 JSON 默认写入 `<音频同目录>/beats_meta/`。
- 临时数据（drums.wav）默认写入 `<音频同目录>/temp/`。
"""

from __future__ import annotations

from typing import List, Tuple, Optional
import os
import json
import pathlib
import numpy as np
import torch
import librosa
import soundfile as sf
from demucs.pretrained import get_model
from demucs.apply import apply_model

     

class BeatsCheckpoint:
    """在音频上生成卡点与可视化元数据。

    参数
    ----
    audio_path: 音频文件路径
    output_dir: 卡点数据输出目录，默认 `<音频目录>/beats_meta`
    temp_dir: 临时目录，默认 `<音频目录>/temp`
    device: 使用设备，`gpu` 或 `cpu`，默认 `gpu`
    """

    def __init__(self, audio_path: str, output_dir: Optional[str] = None, temp_dir: Optional[str] = None, device: str = "gpu") -> None:
        self.audio_path = pathlib.Path(audio_path)
        parent = self.audio_path.parent
        self.output_dir = pathlib.Path(output_dir) if output_dir else parent / "beats_meta"
        self.temp_dir = pathlib.Path(temp_dir) if temp_dir else parent / "temp"
        self.device = device
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _separate_drums(self) -> Optional[pathlib.Path]:
        """使用 Demucs 分离音频，提取 drums.wav 保存到临时目录。

        返回值
        ----
        drums.wav 文件路径；失败返回 None。
        """
        

        if not self.audio_path.is_file():
            return None

        use_cuda = False
        try:
            use_cuda = torch.cuda.is_available() and self.device == "gpu"
        except Exception:
            use_cuda = False

        try:
            model = get_model("htdemucs")
            device = ("cuda" if use_cuda else "cpu")
            import torch
            torch_device = torch.device(device)
            if use_cuda:
                try:
                    torch.backends.cudnn.benchmark = True
                except Exception:
                    pass
            model.to(torch_device)
            model.eval()

            wav_np, sample_rate = sf.read(str(self.audio_path), always_2d=True)
            wav_np = np.transpose(wav_np)  # [channels, samples]
            wav = torch.from_numpy(wav_np).float().to(torch_device)
            inp = wav.unsqueeze(0)  # [1, channels, samples]

            try:
                if use_cuda:
                    with torch.cuda.amp.autocast():
                        stems = apply_model(model, inp, device=torch_device)[0]
                else:
                    stems = apply_model(model, inp, device=torch_device)[0]
            except RuntimeError as re:
                if use_cuda and "out of memory" in str(re).lower():
                    device = "cpu"
                    torch_device = torch.device("cpu")
                    model.to(torch_device)
                    inp = inp.to(torch_device)
                    try:
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                    stems = apply_model(model, inp, device=torch_device)[0]
                else:
                    raise

            stem_names = getattr(model, "sources", [f"stem_{i}" for i in range(stems.shape[0])])
            drums_idx = 0
            try:
                drums_idx = stem_names.index("drums")
            except Exception:
                # 若模型无 drums 名称，则选择能量最大的一轨作为替代
                energies = [float(np.mean(np.abs(stems[i].detach().cpu().numpy()))) for i in range(stems.shape[0])]
                drums_idx = int(max(range(len(energies)), key=lambda i: energies[i]))

            drums = stems[drums_idx].detach().cpu().numpy().T  # [samples, channels]
            out_path = self.temp_dir / "drums.wav"
            sf.write(str(out_path), drums, sample_rate, subtype="PCM_16")
            return out_path
        except Exception:
            return None

    def _detect_beats(self, drums_path: pathlib.Path, mode: str = "default", min_interval: Optional[float] = None) -> List[float]:
        """对 `drums.wav` 使用 Librosa 进行 Onset 检测，并按模式过滤间隔。

        参数
        ----
        drums_path: 鼓点音频路径
        mode: 过滤模式，`default`/`fast`/`slow`/`dynamic`
        min_interval: 覆盖最小间隔（动态模式除外）
        """
   

        try:
            y, sr = librosa.load(str(drums_path), sr=22050)
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, backtrack=True, units="frames")
            times = librosa.frames_to_time(frames, sr=sr).tolist()
        except Exception:
            return []

        def _filter_fixed(beats: List[float], interval_s: float) -> List[float]:
            keep: List[float] = []
            last = -1e9
            for t in beats:
                if t - last >= interval_s:
                    keep.append(t)
                    last = t
            return keep

        def _filter_dynamic(beats: List[float]) -> List[float]:
            # 基于 RMS 能量的分层阈值：强 → 0.3s，中 → 0.65s，弱 → 1.5s
            try:
                rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
            except Exception:
                rms = np.zeros(1, dtype=float)
            q1 = float(np.quantile(rms, 1/3)) if rms.size > 0 else 0.0
            q2 = float(np.quantile(rms, 2/3)) if rms.size > 0 else 0.0

            hop = 512
            keep: List[float] = []
            last = -1e9
            for t in beats:
                idx = max(0, int((t * sr) / hop))
                val = float(rms[min(idx, max(0, rms.size - 1))]) if rms.size > 0 else 0.0
                if val >= q2:
                    interval = 0.30
                elif val >= q1:
                    interval = 0.65
                else:
                    interval = 1.50
                if t - last >= interval:
                    keep.append(t)
                    last = t
            return keep

        if mode not in {"default", "fast", "slow", "dynamic"}:
            mode = "default"

        eff_interval = None
        if mode != "dynamic":
            eff_interval = float(min_interval) if (min_interval is not None) else {
                "default": 0.33,
                "fast": 0.25,
                "slow": 0.60,
            }.get(mode, 0.33)
            filtered = _filter_fixed(sorted(times), max(0.2, eff_interval))
        else:
            filtered = _filter_dynamic(sorted(times))

        return [round(float(t), 3) for t in filtered]

    def _compute_waveform(self, points_per_second: int = 15) -> Tuple[List[float], float, int]:
        """生成归一化的 RMS 波纹数据，用于前端可视化。

        返回值
        ----
        (waveform_values, duration, sample_rate)
        """
        try:
            y, sr = librosa.load(str(self.audio_path), sr=22050)
            duration = float(librosa.get_duration(y=y, sr=sr))
            total = max(1, int(duration * points_per_second))
            hop = max(1, int(len(y) / total))
            rms = librosa.feature.rms(y=y, frame_length=max(2, hop * 2), hop_length=hop)[0]
            values: List[float] = []
            if rms.size > 0:
                rmin = float(rms.min())
                rmax = float(rms.max())
                if rmax > rmin:
                    norm = (rms - rmin) / (rmax - rmin)
                    values = np.round(norm, 3).tolist()
            return values, duration, sr
        except Exception:
            return [], 0.0, 0

    def _find_highlight_segment(self, beats: List[float], duration: float, clip_duration: float = 30.0) -> dict:
        """在整段音频中寻找 30 秒“卡点最密集”片段。"""
        if float(duration) <= float(clip_duration):
            return {"start_time": 0.0, "end_time": float(duration), "beat_count": int(len(beats))}

        arr = np.array(sorted(beats), dtype=float)
        step = 0.5
        best_start = 0.0
        best_cnt = 0
        for start in np.arange(0.0, float(duration) - float(clip_duration), step):
            end = start + float(clip_duration)
            i0 = int(np.searchsorted(arr, start, side="left"))
            i1 = int(np.searchsorted(arr, end, side="right"))
            cnt = int(max(0, i1 - i0))
            if cnt > best_cnt:
                best_cnt = cnt
                best_start = float(start)
        return {"start_time": best_start, "end_time": best_start + float(clip_duration), "beat_count": int(best_cnt)}

    def run(self, mode: str = "default", min_interval: Optional[float] = None) -> Optional[pathlib.Path]:
        """执行分离、检测与 JSON 写出，返回 JSON 路径。"""
        drums_path = self._separate_drums()
        if drums_path is None or not drums_path.exists():
            return None
        beats = self._detect_beats(drums_path, mode=mode, min_interval=min_interval)
       
        wf_vals, duration, sr = self._compute_waveform(points_per_second=15)
        highlight = self._find_highlight_segment(beats, duration, clip_duration=30.0)

        payload = {
            "audio": str(self.audio_path),
            "mode": str(mode),
            "min_interval": None if min_interval is None else float(min_interval),
            "beats": beats,
            "waveform": {
                "points_per_second": 15,
                "values": wf_vals,
            },
            "meta": {
                "duration": float(duration),
                "sample_rate": int(sr),
            },
            "suggestion": {
                "highlight": highlight,
            },
        }

        out_json = self.output_dir / f"{self.audio_path.stem}_beats.json"
        try:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            return None
        return out_json


def beats_checkpoint(audio_path: str, output_dir: Optional[str] = None, temp_dir: Optional[str] = None, mode: str = "default", min_interval: Optional[float] = None, device: str = "gpu") -> Optional[pathlib.Path]:
    """功能函数：生成卡点与可视化元数据并输出 JSON。"""
    runner = BeatsCheckpoint(audio_path=audio_path, output_dir=output_dir, temp_dir=temp_dir, device=device)
    return runner.run(mode=mode, min_interval=min_interval)