from __future__ import annotations

import os
import pathlib
import subprocess
import platform
from typing import Optional, List

from utils.xprint import xprint
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
from utils.calcu_video_info import ffprobe_stream_info, ffmpeg_bin

bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffprobe=True)

class VideoNormalize:
    def __init__(self, mode: str = "standard") -> None:
        self.mode = mode
        self.ffmpeg = ffmpeg_bin or (os.environ.get("FFMPEG_PATH") or "ffmpeg")
        self.hw = self._detect_hardware()

    def _detect_hardware(self) -> str:
        try:
            si = None
            kwargs = {}
            try:
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
            except Exception:
                kwargs = {}
            r = subprocess.run([self.ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, **kwargs)
            out = (r.stdout or "") + (r.stderr or "")
            if "h264_nvenc" in out:
                return "nvidia"
            if "h264_videotoolbox" in out and platform.system() == "Darwin":
                return "mac"
            if "h264_qsv" in out:
                return "intel"
            return "cpu"
        except Exception:
            return "cpu"

    def _build_params(self, mode: str) -> List[str]:
        mbps_audio = "192k" if mode == "high" else ("128k" if mode == "standard" else "96k")
        vf = "pad=ceil(iw/2)*2:ceil(ih/2)*2"
        base = [
            "-c:a",
            "aac",
            "-b:a",
            mbps_audio,
            "-ar",
            "48000",
            "-r",
            "25",
            "-vsync",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-vf",
            vf,
        ]
        if self.hw == "nvidia":
            preset = "p7" if mode == "high" else ("p6" if mode == "standard" else "p3")
            cq = "19" if mode == "high" else ("27" if mode == "standard" else "30")
            profile = "high" if mode == "high" else "main"
            nvenc_adv = [
                "-rc",
                "vbr",
                "-b:v",
                "0",
                "-bf",
                "3",
                "-rc-lookahead",
                "32",
                "-spatial-aq",
                "1",
                "-temporal-aq",
                "1",
                "-aq-strength",
                "6" if mode == "standard" else ("6" if mode == "high" else "8"),
                "-multipass",
                "fullres",
                "-g",
                "50",
            ]
            return ["-c:v", "h264_nvenc", "-preset", preset, "-cq", cq, "-profile:v", profile, *nvenc_adv] + base
        if self.hw == "mac":
            qv = "68" if mode == "high" else ("58" if mode == "standard" else "50")
            return ["-c:v", "h264_videotoolbox", "-q:v", qv, "-g", "50"] + base
        if self.hw == "intel":
            qv = "18" if mode == "high" else ("23" if mode == "standard" else "28")
            return ["-c:v", "h264_qsv", "-global_quality", qv, "-look_ahead", "1", "-preset", "medium", "-g", "50"] + base
        crf = "20" if mode == "high" else ("24" if mode == "standard" else "28")
        preset = "slow" if mode == "high" else ("slower" if mode == "standard" else "fast")
        return [
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            preset,
            "-tune",
            "film",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-g",
            "50",
        ] + base

    def normalize(self, video_path: str, mode: Optional[str] = None) -> str:
        m = (mode or self.mode or "standard").lower()
        vp = pathlib.Path(video_path)
        sinfo = ffprobe_stream_info(vp)
        w = int(sinfo.get("width") or 0)
        h = int(sinfo.get("height") or 0)
        xprint({"phase": "input_stream_info", "info": sinfo})
        xprint({"mode": m, "ffmpeg": self.ffmpeg, "hardware": self.hw})
        xprint(f"视频分辨率: {w}x{h}")
        res_name = f"{w}x{h}" if (w > 0 and h > 0) else "unknown"
        out_dir = vp.parent / "normalized" / res_name
        out_file = out_dir / f"{vp.stem}.mp4"
        if out_file.exists():
            return str(out_file)
        out_dir.mkdir(parents=True, exist_ok=True)
        enc_params = self._build_params(m)
        try:
            # 根据分辨率设置码率上限（防止复杂场景暴涨）
            if w >= 3200 or h >= 1800:
                maxrate = "12M"; bufsize = "24M"
            elif w >= 2500 or h >= 1400:
                maxrate = "10M"; bufsize = "20M"
            elif w >= 1800 or h >= 1000:
                maxrate = "8M"; bufsize = "16M"
            elif w >= 1200 or h >= 700:
                maxrate = "5M"; bufsize = "10M"
            else:
                maxrate = "3M"; bufsize = "6M"
            enc_params = enc_params + ["-maxrate", maxrate, "-bufsize", bufsize]
        except Exception:
            pass
        try:
            ci = enc_params.index("-c:v")
            encoder = enc_params[ci + 1]
        except Exception:
            encoder = "unknown"
        xprint({"encoder": encoder, "encode_params": enc_params})
        cmd = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(vp)] + enc_params + [str(out_file)]
        xprint({"cmd": cmd})
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
        if r.returncode != 0:
            raise RuntimeError(((r.stderr or b"").decode("utf-8", errors="ignore")) if isinstance(r.stderr, (bytes, bytearray)) else str(r.stderr))
        out_info = ffprobe_stream_info(out_file)
        xprint({"phase": "output_stream_info", "info": out_info})
        try:
            xprint_compare_streams(sinfo, out_info)
        except Exception:
            pass
        try:
            ib = int(vp.stat().st_size)
            ob = int(pathlib.Path(out_file).stat().st_size)
            ratio = (float(ob) / float(ib)) if ib > 0 else None
            percent = ((1.0 - float(ratio)) * 100.0) if (ratio is not None) else None
            xprint({
                "phase": "size_compare",
                "input_bytes": ib,
                "output_bytes": ob,
                "input_mb": round(float(ib) / (1024.0 * 1024.0), 3),
                "output_mb": round(float(ob) / (1024.0 * 1024.0), 3),
                "ratio": ratio,
                "change_percent": percent,
            })
        except Exception:
            pass
        return str(out_file)


def video_normalize(video_path: str, mode: str = "standard") -> str:
    return VideoNormalize(mode=mode).normalize(video_path, mode=mode)


def xprint_compare_streams(old_info: dict, new_info: dict) -> None:
    """打印新老视频参数对比（分辨率、编码、像素格式、帧率）。"""
    try:
        ow = int(old_info.get("width") or 0)
        oh = int(old_info.get("height") or 0)
        nw = int(new_info.get("width") or 0)
        nh = int(new_info.get("height") or 0)
        res_old = f"{ow}x{oh}" if (ow > 0 and oh > 0) else "unknown"
        res_new = f"{nw}x{nh}" if (nw > 0 and nh > 0) else "unknown"
        items = [
            {
                "name": "resolution",
                "old": res_old,
                "new": res_new,
                "changed": res_old != res_new,
            },
            {
                "name": "codec",
                "old": str(old_info.get("codec") or ""),
                "new": str(new_info.get("codec") or ""),
                "changed": (old_info.get("codec") or "") != (new_info.get("codec") or ""),
            },
            {
                "name": "pix_fmt",
                "old": str(old_info.get("pix_fmt") or ""),
                "new": str(new_info.get("pix_fmt") or ""),
                "changed": (old_info.get("pix_fmt") or "") != (new_info.get("pix_fmt") or ""),
            },
            {
                "name": "r_frame_rate",
                "old": str(old_info.get("r_frame_rate") or ""),
                "new": str(new_info.get("r_frame_rate") or ""),
                "changed": (old_info.get("r_frame_rate") or "") != (new_info.get("r_frame_rate") or ""),
            },
        ]
        xprint({"phase": "compare_streams", "items": items})
    except Exception:
        pass
