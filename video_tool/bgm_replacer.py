import pathlib
import numpy as np
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeAudioClip
from moviepy.audio.fx import all as afx
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)

from utils.gpu_detect import is_nvenc_available
from video_tool.separate_bgm_demucs import separate_bgm_demucs

class BGMReplacer:
    """
    将视频的音轨通过 Demucs 分离后，依据设置与外部 BGM 混音，并生成合成视频。

    参数:
    - video_path: 输入视频文件路径
    - bgm_path: 合成使用的背景音乐文件路径
    - output_dir: 输出目录
    - keep_original_voice: 是否保留原声（默认保留）
    - original_volume: 原声音量系数
    - bgm_volume: 背景音乐音量系数
    - device: 设备选择，"gpu" 或 "cpu"，默认优先使用 "gpu"
    """

    def __init__(
        self,
        video_path: str,
        bgm_path: str,
        output_dir: str | None = None,
        keep_original_voice: bool = True,
        original_volume: float = 1.0,
        bgm_volume: float = 1.0,
        device: str = "gpu",
    ):
        self.video_path = pathlib.Path(video_path)
        self.bgm_path = pathlib.Path(bgm_path)
        self.output_dir = pathlib.Path(output_dir) if output_dir else self.video_path.parent / self.video_path.stem
        self.keep_original_voice = keep_original_voice
        self.original_volume = original_volume
        self.bgm_volume = bgm_volume
        self.device = device

    def replace(self) -> pathlib.Path | None:
        """
        执行分离与合成，并返回最终输出视频路径。
        若失败返回 None。
        """

        if not self.video_path.is_file():
            print(f"错误：视频文件不存在 -> {self.video_path}")
            return None
        if not self.bgm_path.is_file():
            print(f"错误：BGM 文件不存在 -> {self.bgm_path}")
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)

        final_out = self.output_dir / f"{self.video_path.stem}_with_bgm.mp4"
        if final_out.exists():
            print(f"已存在合成视频，跳过处理: {final_out}")
            return final_out

        ret_output_dir_path = separate_bgm_demucs(str(self.video_path), model="htdemucs", use_device=self.device)
        if ret_output_dir_path is None:
            print("错误：BGM 分离失败")
            return None

        silent_video_path = ret_output_dir_path / f"{self.video_path.stem}_no_audio.mp4"
        if not silent_video_path.exists():
            print("错误：未找到无声视频文件，分离步骤可能失败。")
            return None

        video_clip = VideoFileClip(str(silent_video_path))

        bgm_clip = AudioFileClip(str(self.bgm_path))
        bgm_clip = afx.audio_loop(bgm_clip, duration=video_clip.duration)
        bgm_clip = bgm_clip.audio_fadein(0.8).audio_fadeout(0.8)

        if self.keep_original_voice:
            vocals_path = ret_output_dir_path / "vocals.wav"
            voice_clip = None
            if vocals_path.exists():
                voice_clip = AudioFileClip(str(vocals_path)).set_duration(video_clip.duration)
            else:
                try:
                    orig_video = VideoFileClip(str(self.video_path))
                    if orig_video.audio:
                        voice_clip = orig_video.audio.set_duration(video_clip.duration)
                    orig_video.close()
                except Exception:
                    voice_clip = None

            if voice_clip is not None:
                voice_rms = self._estimate_rms(voice_clip, video_clip.duration)
                bgm_rms = self._estimate_rms(bgm_clip, video_clip.duration)
                eps = 1e-9
                target_rel = 0.32
                auto_bgm_scale = (voice_rms * target_rel) / (bgm_rms + eps) if bgm_rms > 0 else self.bgm_volume
                voice_scale = max(0.0, float(self.original_volume))
                bgm_scale = max(0.0, min(float(self.bgm_volume), auto_bgm_scale))
                headroom = 0.95
                total = voice_scale + bgm_scale
                if total > headroom and total > 0:
                    s = headroom / total
                    voice_scale *= s
                    bgm_scale *= s
                voice_clip = voice_clip.volumex(voice_scale)
                bgm_clip = bgm_clip.volumex(bgm_scale)
                mixed_audio = CompositeAudioClip([voice_clip, bgm_clip]).set_duration(video_clip.duration)
            else:
                bgm_clip = bgm_clip.volumex(min(self.bgm_volume, 0.95))
                mixed_audio = bgm_clip.set_duration(video_clip.duration)
        else:
            bgm_clip = bgm_clip.volumex(min(self.bgm_volume, 0.95))
            mixed_audio = bgm_clip.set_duration(video_clip.duration)

        video_clip = video_clip.set_audio(mixed_audio)
        try:
            use_nvenc = is_nvenc_available()
            codec = "h264_nvenc" if use_nvenc else "libx264"
            ffmpeg_params = ["-preset", "p7", "-cq", "33"] if use_nvenc else ["-preset", "slow", "-crf", "28"]
            video_clip.write_videofile(
                str(final_out),
                audio_codec="aac",
                codec=codec,
                ffmpeg_params=ffmpeg_params,
                logger=None,
            )
        except Exception as e:
            print(f"错误：写出合成视频失败: {e}")
            video_clip.close()
            return None
        finally:
            video_clip.close()

        print(f"已输出合成视频: {final_out}")
        return final_out

    def _estimate_rms(self, clip: AudioFileClip, duration: float, segments: int = 5, seg_len: float = 2.0) -> float:
        """
        估算音频片段的 RMS，采样若干等距窗口以避免整段解码。
        返回均值 RMS 振幅（线性）。
        """
        if duration <= 0:
            return 0.0
        seg_len = max(0.2, min(seg_len, max(0.2, duration / segments)))
        starts = np.linspace(0, max(0, duration - seg_len), num=max(1, segments))
        rms_vals = []
        for t0 in starts:
            try:
                sub = clip.subclip(t0, t0 + seg_len)
                arr = sub.to_soundarray(fps=22050)
                if arr.size == 0:
                    continue
                if arr.ndim == 2:
                    arr = arr.mean(axis=1)
                rms = float(np.sqrt(np.mean(np.square(arr))))
                if np.isfinite(rms):
                    rms_vals.append(rms)
            except Exception:
                continue
        if not rms_vals:
            return 0.0
        return float(np.mean(rms_vals))


def bgm_replacer(
    video_path: str,
    bgm_path: str,
    output_dir: str | None = None,
    keep_original_voice: bool = True,
    original_volume: float = 1.0,
    bgm_volume: float = 1.0,
    device: str = "gpu",
):
    """
    统一接口：替换视频背景音乐并导出合成视频。

    参数:
    - video_path: 输入视频文件路径
    - bgm_path: 背景音乐文件路径
    - output_dir: 输出目录（默认视频同名目录）
    - keep_original_voice: 是否保留原声
    - original_volume: 原声音量系数
    - bgm_volume: BGM 音量系数
    - device: 'gpu' 或 'cpu'，默认 'gpu'

    返回:
    - 输出视频路径（pathlib.Path），失败返回 None
    """
    replacer = BGMReplacer(
        video_path=video_path,
        bgm_path=bgm_path,
        output_dir=output_dir,
        keep_original_voice=keep_original_voice,
        original_volume=original_volume,
        bgm_volume=bgm_volume,
        device=device,
    )
    return replacer.replace()