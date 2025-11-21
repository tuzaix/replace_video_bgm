from __future__ import annotations

import json
import math
import os
import pathlib
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf
import torch
from demucs.pretrained import get_model
from demucs.apply import apply_model
import librosa


class BeatsCheckpoint:
    """基于 Demucs + Librosa 的鼓点卡点检测。

    处理流程：
    1) 使用 Demucs 将输入音频分离，生成 `drums.wav` 与 `other.wav` 到临时目录
    2) 使用 Librosa 对 `drums.wav` 进行 Onset 检测，得到时间戳（秒）
    3) 将时间戳保存为 JSON 到输出目录
    """

    def __init__(
        self,
        audio_path: str,
        output_dir: Optional[str] = None,
        temp_dir: Optional[str] = None,
        model: str = "htdemucs",
        device: str = "gpu",
        interval_mode: str = "default",
        min_interval: Optional[float] = None,
    ) -> None:
        """初始化检测器。

        参数：
        - audio_path: 输入音频文件路径
        - output_dir: 卡点数据输出目录（默认：音频同目录下 `beats_meta`）
        - temp_dir: 临时目录（默认：音频同目录下 `temp`）
        - model: Demucs 模型名（默认 `htdemucs`）
        - device: 设备优先级（`gpu` 或 `cpu`）
        """
        self.audio_path = str(audio_path)
        ap = pathlib.Path(self.audio_path)
        self.output_dir = pathlib.Path(output_dir) if output_dir else ap.parent / "beats_meta"
        self.temp_dir = pathlib.Path(temp_dir) if temp_dir else ap.parent / "temp"
        self.model_name = str(model or "htdemucs")
        self.device_pref = str(device or "gpu")
        self.interval_mode = str(interval_mode or "default").lower()
        self.min_interval = float(min_interval) if min_interval is not None else None
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _separate_drums(self) -> Optional[Tuple[pathlib.Path, pathlib.Path]]:
        """使用 Demucs 分离输入音频，仅保存 `drums.wav` 与 `other.wav`。

        返回 `(drums_path, other_path)`；失败时返回 None。
        """
        ap = pathlib.Path(self.audio_path)
        if not ap.is_file():
            return None

        drums_out = self.temp_dir / "drums.wav"
        other_out = self.temp_dir / "other.wav"

        try:
            model = get_model(self.model_name)
            use_cuda = torch.cuda.is_available() and self.device_pref == "gpu"
            device = torch.device("cuda" if use_cuda else "cpu")
            model.to(device)
            model.eval()

            wav_np, sample_rate = sf.read(str(ap), always_2d=True)
            wav_np = np.transpose(wav_np)
            wav = torch.from_numpy(wav_np).float().to(device)
            inp = wav.unsqueeze(0)

            try:
                if use_cuda:
                    with torch.cuda.amp.autocast():
                        stems = apply_model(model, inp, device=device)[0]
                else:
                    stems = apply_model(model, inp, device=device)[0]
            except RuntimeError as re:
                if use_cuda and "out of memory" in str(re).lower():
                    device = torch.device("cpu")
                    model.to(device)
                    inp = inp.to(device)
                    try:
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                    stems = apply_model(model, inp, device=device)[0]
                else:
                    raise

            stem_names = getattr(model, "sources", [f"stem_{i}" for i in range(stems.shape[0])])
            name_to_idx = {n.lower(): i for i, n in enumerate(stem_names)}

            # 保存 drums 与 other，若不存在则回退选择最接近的两个轨
            def _save(idx: int, path: pathlib.Path) -> None:
                audio = stems[idx].detach().cpu().numpy().T
                sf.write(str(path), audio, sample_rate, subtype="PCM_16")

            if "drums" in name_to_idx:
                _save(name_to_idx["drums"], drums_out)
            else:
                _save(0, drums_out)
            if "other" in name_to_idx:
                _save(name_to_idx["other"], other_out)
            else:
                # 选择一个非 drums 的轨作为 other
                idx_other = 1 if stems.shape[0] > 1 else 0
                _save(idx_other, other_out)

            return drums_out, other_out
        except Exception:
            return None

    def _detect_onsets(self, drums_wav: pathlib.Path) -> List[float]:
        """使用 Librosa 对鼓点音轨进行 Onset 检测，返回时间戳（秒）。"""
        y, sr = librosa.load(str(drums_wav), sr=None, mono=True)
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, backtrack=True, units="frames")
        timestamps = librosa.frames_to_time(onset_frames, sr=sr)
        return [float(t) for t in timestamps]

    def _filter_fixed(self, timestamps: List[float], min_interval: float) -> List[float]:
        """按固定最小间隔进行稀疏化过滤。"""
        if not timestamps:
            return []
        out = [float(timestamps[0])]
        for t in timestamps[1:]:
            if float(t) - out[-1] >= float(min_interval):
                out.append(float(t))
        return out

    def _filter_dynamic(self, drums_wav: pathlib.Path, onsets: List[float]) -> List[float]:
        """基于鼓点音轨能量动态调整间隔进行过滤。"""
        if not onsets:
            return []
        y, sr = librosa.load(str(drums_wav), sr=None, mono=True)
        hop_length = 512
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop_length)[0]
        if rms.size == 0:
            return self._filter_fixed(onsets, 0.5)
        thr = float(np.percentile(rms, 70))
        out = [float(onsets[0])]
        for t in onsets[1:]:
            frame_idx = librosa.time_to_frames(float(t), sr=sr, hop_length=hop_length)
            frame_idx = max(0, min(int(frame_idx), int(len(rms) - 1)))
            loud = float(rms[frame_idx])
            interval = 0.3 if loud >= thr else 0.8
            interval = max(0.2, interval)
            if float(t) - out[-1] >= interval:
                out.append(float(t))
        return out

    def run(self) -> Tuple[pathlib.Path, List[float]]:
        """执行完整流程并保存 JSON，返回 `(json_path, timestamps)`。

        输出文件名：`<音频文件名>_beats.json`
        JSON 内容：`{"audio": <原音频路径>, "timestamps": [秒, ...]}`
        """
        ap = pathlib.Path(self.audio_path)
        out_json = self.output_dir / f"{ap.stem}_beats.json"

        sep = self._separate_drums()
        if sep is None:
            return out_json, []
        drums_wav, _ = sep
        ts = self._detect_onsets(drums_wav)

        mode = self.interval_mode
        if mode == "dynamic":
            ts = self._filter_dynamic(drums_wav, ts)
        else:
            if self.min_interval is not None:
                mi = float(self.min_interval)
            else:
                mi = 0.5 if mode == "default" else (0.3 if mode == "fast" else (1.0 if mode == "slow" else 0.5))
            mi = max(0.2, float(mi))
            ts = self._filter_fixed(ts, mi)

        payload = {"audio": str(ap), "timestamps": ts, "mode": mode}
        try:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return out_json, ts


def beats_checkpoint(audio_path: str, output_dir: Optional[str] = None, temp_dir: Optional[str] = None, model: str = "htdemucs", device: str = "gpu", interval_mode: str = "default", min_interval: Optional[float] = None) -> Tuple[pathlib.Path, List[float]]:
    """函数式入口：执行卡点检测并返回 JSON 路径与时间戳列表。"""
    bc = BeatsCheckpoint(audio_path=audio_path, output_dir=output_dir, temp_dir=temp_dir, model=model, device=device, interval_mode=interval_mode, min_interval=min_interval)
    return bc.run()