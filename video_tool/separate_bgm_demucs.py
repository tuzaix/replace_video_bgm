import os
import pathlib
import shutil
from moviepy.editor import VideoFileClip
# 懒加载所需模块，避免全局导入带来启动开销
from demucs.pretrained import get_model
from demucs.apply import apply_model
import soundfile as sf
import numpy as np
import torch

from utils.xprint import xprint
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)

class SeparateBgmDemucs:
    """
    使用 Demucs 从视频中分离音轨。
    将视频分离为：一个无声的视频文件，以及人声、鼓、贝斯、其他等多个音频文件。
    """

    def __init__(self, model: str = "htdemucs", use_device = "gpu"):
        """
        初始化分离器。

        :param model: 要使用的 Demucs 模型名称，默认为 'htdemucs'。
        可选模型： htdemucs(绝大多数场景，默认), 
                 htdemucs_ft（追求极致画质/音质）, 
                 htdemucs_6s（需要提取**吉他**或**钢琴**独奏时使用）, 
                 mdx_extra（专为 MDX 挑战赛量化的模型，体积小，速度快，但音质有压缩。）
        """
        self.model = model
        self.use_device = use_device

        xprint(f"正在使用设备: {self.use_device}")

    def separate(self, video_path: str, output_dir: str = None):
        """
        执行分离操作。

        :param video_path: 输入视频文件的路径。
        :param output_dir: 输出目录。如果为 None，则默认为视频文件所在目录下，一个与视频同名的新目录。
        返回:
        - 输出目录路径（pathlib.Path），失败返回 None
        """
        video_p = pathlib.Path(video_path)
        if not video_p.is_file():
            xprint(f"错误：视频文件不存在 -> {video_path}")
            return

        if output_dir is None:
            # 默认输出目录为 "视频文件名"
            output_dir_path = video_p.parent / "替换bgm临时" / video_p.stem
        else:
            output_dir_path = pathlib.Path(output_dir)

        output_dir_path.mkdir(parents=True, exist_ok=True)
        xprint(f"输出目录: {output_dir_path}")

        # --- 预检查：如果已有生成文件则直接返回 ---
        try:
            silent_video_candidate = output_dir_path / f"{video_p.stem}_no_audio.mp4"
            existing_wavs = [p for p in output_dir_path.glob("*.wav") if p.name != "temp_audio_for_demucs.wav"]
            if silent_video_candidate.exists() and len(existing_wavs) > 0:
                xprint("检测到已生成的无声视频及至少一个音轨文件，跳过分离。")
                return output_dir_path
        except Exception:
            pass

        # --- 1. 加载视频 ---
        xprint("正在加载视频...")
        try:
            video = VideoFileClip(str(video_path))
        except Exception as e:
            xprint(f"错误：无法加载视频文件 '{video_path}': {e}")
            return

        # --- 2. 创建无声视频 ---
        silent_video_path = output_dir_path / f"{video_p.stem}_no_audio.mp4"
        xprint("正在创建无声视频...")
        try:
            video.write_videofile(str(silent_video_path), audio=False, logger=None)
            xprint(f"已生成无声视频: {silent_video_path}")
        except Exception as e:
            xprint(f"错误：创建无声视频失败: {e}")
            video.close()
            return 

        # --- 3. 提取音频 ---
        if not video.audio:
            xprint(f"警告: 视频 '{video_path}' 不包含音轨，仅生成无声视频。")
            video.close()
            return
            
        temp_audio_path = output_dir_path / f"temp_audio_for_demucs.wav"
        xprint("正在从视频提取音频...")
        try:
            video.audio.write_audiofile(str(temp_audio_path))
        except Exception as e:
            xprint(f"错误：提取音频失败: {e}")
            video.close()
            return
        finally:
            video.close()

        # --- 4. 使用 Demucs Python API 进行分离并以 WAV 保存 ---
        # 通过 Python API 直接获得分离的音轨，避免 torchcodec 依赖导致的保存失败。
        xprint(f"正在使用 Demucs ({self.model}) 分离音轨 (这可能需要很长时间)...")

        try:
            # 加载模型
            model = get_model(self.model)
            use_cuda = torch.cuda.is_available() and self.use_device == "gpu"
            device = torch.device("cuda" if use_cuda else "cpu")
            xprint(f"正在使用设备: {device}, {use_cuda}")
            if use_cuda:
                xprint("支持 CUDA，将使用 GPU 加速。")
                try:
                    torch.backends.cudnn.benchmark = True
                except Exception:
                    pass
            model.to(device)
            model.eval()

            # 读取音频：形状 [channels, samples]
            # 使用 soundfile 加载 WAV，返回 [samples, channels]
            wav_np, sample_rate = sf.read(str(temp_audio_path), always_2d=True)
            # 转换为 [channels, samples]
            wav_np = np.transpose(wav_np)
            # 转换为 torch.Tensor 并放到设备
            wav = torch.from_numpy(wav_np).float().to(device)
            # 增加 batch 维度 -> [1, channels, samples]
            inp = wav.unsqueeze(0)

            # 应用模型，得到分离后的音轨：形状 [num_stems, channels, samples]
            # GPU 优先，显存不足自动回退到 CPU
            try:
                if use_cuda:
                    with torch.cuda.amp.autocast():
                        stems = apply_model(model, inp, device=device)[0]
                else:
                    stems = apply_model(model, inp, device=device)[0]
            except RuntimeError as re:
                if use_cuda and "out of memory" in str(re).lower():
                    xprint("GPU 显存不足，正在回退到 CPU 处理...")
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

            # 获取音轨名称（例如 ['vocals', 'drums', 'bass', 'other'] 或 6 stems）
            stem_names = getattr(model, 'sources', [f'stem_{i}' for i in range(stems.shape[0])])

            xprint("正在保存分离后的音轨为 WAV...")
            for i, name in enumerate(stem_names):
                out_path = output_dir_path / f"{name}.wav"
                # soundfile 期望 [samples, channels]
                audio = stems[i].detach().cpu().numpy().T
                # 保存为 16-bit PCM，便于通用播放器兼容
                sf.write(str(out_path), audio, sample_rate, subtype='PCM_16')
                xprint(f"已保存: {out_path}")

            xprint(f"分离完成！音轨文件已保存到: {output_dir_path}")

        except Exception as e:
            import traceback
            xprint(f"Demucs 分离失败: {e}")
            traceback.print_exc()
        finally:
            # --- 5. 清理临时文件 ---
            if temp_audio_path.exists():
                os.remove(temp_audio_path)
                xprint(f"已删除临时音频文件: {temp_audio_path}")

        xprint("所有操作完成。")
        return output_dir_path


def separate_bgm_demucs(video_path, output_dir=None, model: str = "htdemucs", use_device: str = "cpu"):
    """
    功能函数，用于调用 SeparateBgmDemucs 类。
    分离出对应的无声视频，人声，鼓，贝斯，其他等音频文件。

    :param video_path: 输入视频文件的路径。
    :param output_dir: 输出目录。如果为 None，则默认为视频文件名（不含扩展名）的目录。
    :param model: 要使用的 Demucs 模型名称。
    :param use_device: 要使用的设备，"cpu" 或 "gpu"。   

    返回:
    - 输出目录路径（pathlib.Path），失败返回 None
    """
    separator = SeparateBgmDemucs(model=model, use_device=use_device)
    return separator.separate(video_path, output_dir)
