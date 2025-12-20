from __future__ import annotations

import os
import sys
import random
import subprocess
import shutil
import time
import argparse
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

# 将项目根目录添加到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils.calcu_video_info import ffmpeg_bin, ffprobe_bin, ffprobe_duration, probe_resolution, is_video_file
from utils.common_utils import is_video_file as is_video_check

def _popen_silent_kwargs():
    """获取隐藏控制台窗口的参数（仅限 Windows）。"""
    kwargs = {}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs

class VideoRemixedVideoAudio:
    """
    根据模仿视频的音频时长，从素材库中随机挑选视频切片进行混剪合成。
    """

    def __init__(self, imitation_dir: str, segment_dir: str, output_dir: Optional[str] = None, use_gpu: bool = True, encode_profile: str = "balanced"):
        """
        初始化混剪类。

        :param imitation_dir: 模仿视频目录
        :param segment_dir: 视频切片素材目录
        :param output_dir: 输出目录，如果为 None 则在 imitation_dir 下创建 remixed 目录
        :param use_gpu: 是否使用 GPU 加速
        :param encode_profile: 编码档位 (visual/balanced/size)
        """
        self.imitation_dir = Path(imitation_dir)
        self.segment_dir = Path(segment_dir)
        self.use_gpu = use_gpu
        self.encode_profile = encode_profile

        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = self.imitation_dir / "remixed"
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # 音频剥离的临时目录放到模仿视频目录下
        self.temp_dir = self.imitation_dir / "_temp_audio_extract"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _get_encoding_opts(self) -> List[str]:
        """
        获取编码参数，参考 video_concat.py 的逻辑。
        """
        profile = self.encode_profile.lower()
        if profile not in ('visual', 'balanced', 'size'):
            profile = 'balanced'

        # 默认参数
        if profile == 'visual':
            nvenc_cq, preset_gpu = 28, 'p5'
            x264_crf, preset_cpu = 20, 'medium'
        elif profile == 'size':
            nvenc_cq, preset_gpu = 34, 'p7'
            x264_crf, preset_cpu = 26, 'veryslow'
        else:  # balanced
            nvenc_cq, preset_gpu = 31, 'p6'
            x264_crf, preset_cpu = 23, 'slow'

        if self.use_gpu:
            # 尝试使用 h264_nvenc (因为目标是 mp4，通常用 h264 兼容性更好)
            # 如果需要 HEVC 可以改为 hevc_nvenc
            return [
                '-c:v', 'h264_nvenc',
                '-preset', preset_gpu,
                '-rc', 'vbr',
                '-cq', str(nvenc_cq),
                '-b:v', '0',
                '-pix_fmt', 'yuv420p'
            ]
        else:
            return [
                '-c:v', 'libx264',
                '-crf', str(x264_crf),
                '-preset', preset_cpu,
                '-pix_fmt', 'yuv420p'
            ]

    def _extract_audio_lossless(self, video_path: Path) -> Optional[Path]:
        """
        无损提取视频中的音频。如果无损提取失败，则回退到重编码为 AAC。

        :param video_path: 视频文件路径
        :return: 提取出的音频文件路径，失败返回 None
        """
        # 先探测音频编码
        cmd_probe = [
            ffprobe_bin, "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ]
        try:
            res = subprocess.run(cmd_probe, capture_output=True, text=True, **_popen_silent_kwargs())
            codec = res.stdout.strip()
            if not codec:
                print(f"⚠️ 视频没有音频流: {video_path.name}")
                return None
            
            # 映射常见编码到扩展名
            ext_map = {
                "aac": "m4a",
                "mp3": "mp3",
                "flac": "flac",
                "opus": "opus",
                "vorbis": "ogg",
                "ac3": "ac3",
                "pcm_s16le": "wav",
                "pcm_s24le": "wav"
            }
            ext = ext_map.get(codec, "m4a")
            audio_out = self.temp_dir / f"{video_path.stem}_audio.{ext}"
            
            # 尝试无损提取
            cmd_extract = [
                ffmpeg_bin, "-y",
                "-i", str(video_path),
                "-vn",
                "-c:a", "copy",
                str(audio_out)
            ]
            proc = subprocess.run(cmd_extract, capture_output=True, **_popen_silent_kwargs())
            
            if proc.returncode != 0:
                # 无损提取失败，可能是容器不支持 copy。回退到重编码为 aac
                print(f"⚠️ 无损提取音频失败，正在尝试重编码为 AAC: {video_path.name}")
                audio_out = self.temp_dir / f"{video_path.stem}_audio.m4a"
                cmd_fallback = [
                    ffmpeg_bin, "-y",
                    "-i", str(video_path),
                    "-vn",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    str(audio_out)
                ]
                subprocess.run(cmd_fallback, check=True, **_popen_silent_kwargs())
                
            return audio_out
        except Exception as e:
            print(f"❌ 提取音频失败 {video_path.name}: {e}")
            return None

    def _get_video_segments(self) -> List[Tuple[Path, float, Tuple[int, int]]]:
        """
        获取素材库中所有视频及其时长、分辨率。只查找一级目录，不进行递归。
        """
        segments = []
        for p in self.segment_dir.glob("*"):
            if p.is_file() and is_video_check(p):
                duration = ffprobe_duration(p)
                res = probe_resolution(p)
                if duration > 0 and res:
                    segments.append((p, duration, res))
        return segments

    def _select_segments_for_duration(self, segments: List[Tuple[Path, float, Tuple[int, int]]], target_duration: float) -> List[Tuple[Path, Tuple[int, int]]]:
        """
        挑选总时长达到目标时长的随机素材。
        """
        random.shuffle(segments)
        selected = []
        current_duration = 0.0
        
        # 尝试挑选素材直到满足时长
        for p, d, res in segments:
            selected.append((p, res))
            current_duration += d
            if current_duration >= target_duration:
                break
        
        # 如果素材库不够长，循环利用
        while current_duration < target_duration and segments:
            p, d, res = random.choice(segments)
            selected.append((p, res))
            current_duration += d
            
        return selected

    def process(self, count_per_video: int = 1):
        """
        开始处理混剪任务。
        """
        imitation_videos = [p for p in self.imitation_dir.glob("*") if p.is_file() and is_video_check(p)]
        if not imitation_videos:
            print("⚠️ 模仿视频目录下没有找到视频文件。")
            return

        print(f"🔍 正在扫描素材库: {self.segment_dir}")
        all_segments = self._get_video_segments()
        if not all_segments:
            print("❌ 素材库中没有找到有效的视频切片。")
            return
        print(f"✅ 找到 {len(all_segments)} 个视频素材。")

        for idx, video_path in enumerate(imitation_videos, 1):
            print(f"\n🎬 [{idx}/{len(imitation_videos)}] 正在处理: {video_path.name}")
            
            # 1. 提取音频
            audio_path = self._extract_audio_lossless(video_path)
            if not audio_path:
                continue
            
            audio_duration = ffprobe_duration(audio_path)
            if audio_duration <= 0:
                print(f"⚠️ 无法获取音频时长: {audio_path}")
                continue
            
            print(f"🎵 音频时长: {audio_duration:.2f}s")

            for i in range(count_per_video):
                print(f"  ✨ 正在生成第 {i+1}/{count_per_video} 份混剪...")
                
                # 2. 挑选素材
                selected_data = self._select_segments_for_duration(all_segments, audio_duration)
                if not selected_data:
                    print("  ❌ 未能挑选到有效的素材。")
                    continue

                # 提取路径列表
                selected_paths = [item[0] for item in selected_data]
                # 以挑选出的第一个素材的分辨率作为混剪视频的目标分辨率
                target_res = selected_data[0][1]
                
                print(f"  📺 混剪目标分辨率: {target_res[0]}x{target_res[1]}")

                # 3. 合成视频
                output_name = f"{video_path.stem}_remix_{i+1:02d}.mp4"
                output_path = self.output_dir / output_name
                
                success = self._combine_segments_with_audio(
                    selected_paths, audio_path, audio_duration, target_res, output_path
                )
                
                if success:
                    print(f"  ✅ 已生成: {output_path.name}")
                else:
                    print(f"  ❌ 生成失败: {output_name}")

        # 清理临时目录
        if self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass
        print(f"\n🎉 处理完成！输出目录: {self.output_dir}")

    def _combine_segments_with_audio(
        self, 
        video_segments: List[Path], 
        audio_path: Path, 
        target_duration: float,
        resolution: Tuple[int, int],
        output_path: Path
    ) -> bool:
        """
        拼接视频素材并合成提取出的音频。
        使用 filter_complex_script 避免命令行过长。

        :param video_segments: 挑选出的视频切片列表
        :param audio_path: 提取出的音频文件路径
        :param target_duration: 目标时长（音频时长）
        :param resolution: 目标分辨率 (width, height)
        :param output_path: 最终合成视频的输出路径
        :return: 是否合成成功
        """
        width, height = resolution
        
        # 构造 FFmpeg concat 滤镜脚本
        filter_script_path = self.temp_dir / f"filter_script_{int(time.time())}_{random.randint(1000, 9999)}.txt"
        
        filter_lines = []
        for i, p in enumerate(video_segments):
            # 对每个片段进行缩放、填充、统一帧率和采样率
            # force_original_aspect_ratio=decrease 保持比例缩放，不足部分 pad 补齐
            line = (
                f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}];"
            )
            filter_lines.append(line)
        
        # 拼接视频流
        concat_v_inputs = "".join([f"[v{i}]" for i in range(len(video_segments))])
        filter_lines.append(f"{concat_v_inputs}concat=n={len(video_segments)}:v=1:a=0[outv]")
        
        with open(filter_script_path, "w", encoding="utf-8") as f:
            f.write("\n".join(filter_lines))
        
        cmd = [
            ffmpeg_bin, "-y"
        ]
        # 添加所有视频切片输入
        for p in video_segments:
            cmd.extend(["-i", str(p)])
            
        # 添加音频输入
        cmd.extend(["-i", str(audio_path)])
        
        # 最后一个输入是音频，索引为 len(video_segments)
        audio_index = len(video_segments)
        
        cmd.extend([
            "-filter_complex_script", str(filter_script_path),
            "-map", "[outv]",
            "-map", f"{audio_index}:a",
        ])

        # 使用动态生成的编码参数
        cmd.extend(self._get_encoding_opts())

        cmd.extend([
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", f"{target_duration:.3f}", # 确保时长对齐
            "-movflags", "+faststart",
            str(output_path)
        ])
        
        try:
            # 运行 FFmpeg
            subprocess.run(cmd, check=True, capture_output=True, **_popen_silent_kwargs())
            return True
        except subprocess.CalledProcessError as e:
            err = (e.stderr or b"").decode("utf-8", errors="ignore")
            print(f"❌ FFmpeg 合成失败: {err[:500]}...")
            return False
        except Exception as e:
            print(f"❌ 合成过程中出现错误: {e}")
            return False
        finally:
            # 删除滤镜脚本
            if filter_script_path.exists():
                try:
                    filter_script_path.unlink()
                except Exception:
                    pass

if __name__ == "__main__":
    # 该模块现在建议通过 video_remixed_video_audio_cli.py 调用
    print("请使用 video_remixed_video_audio_cli.py 运行该工具。")
