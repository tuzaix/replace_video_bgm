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

    def __init__(self, imitation_dir: str, segment_dir: str, output_dir: Optional[str] = None, 
                 use_gpu: bool = True, encode_profile: str = "balanced", video_type: str = "shorts"):
        """
        初始化混剪类。

        :param imitation_dir: 模仿视频目录
        :param segment_dir: 视频切片素材目录
        :param output_dir: 输出目录，如果为 None 则在 imitation_dir 下创建 remixed 目录
        :param use_gpu: 是否使用 GPU 加速
        :param encode_profile: 编码档位 (visual/balanced/size)
        :param video_type: 视频类型 (shorts: 1080x1920, video: 1920x1080)
        """
        self.imitation_dir = Path(imitation_dir)
        self.segment_dir = Path(segment_dir)
        self.use_gpu = use_gpu
        self.encode_profile = encode_profile
        self.video_type = video_type.lower()

        # 根据视频类型确定目标分辨率
        if self.video_type == "video":
            self.target_res = (1920, 1080)
        else:
            self.target_res = (1080, 1920)

        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = self.imitation_dir / "remixed"
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # 音频剥离和视频标准化的临时目录
        self.temp_dir = self.imitation_dir / "_temp_remix_work"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.norm_dir = self.temp_dir / "normalized_segments"
        self.norm_dir.mkdir(parents=True, exist_ok=True)
        self.intro_dir = self.temp_dir / "intros"
        self.intro_dir.mkdir(parents=True, exist_ok=True)
        
        # 缓存已标准化的分片路径 { (path, resolution): norm_path }
        self._norm_cache: Dict[Tuple[str, Tuple[int, int]], Path] = {}

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

            # 1.1 提取并标准化片头（前3秒）
            print(f"  🎬 正在生成片头预处理 ({self.video_type})...")
            intro_path = self._extract_and_normalize_intro(video_path)
            if not intro_path:
                print(f"  ⚠️ 无法生成片头，将跳过当前视频: {video_path.name}")
                continue
            
            print(f"  ✅ 片头预处理完成: {intro_path.name}")
            
            # 调整后续素材需要填补的时长
            remaining_duration = max(0, audio_duration - 3.0)

            for i in range(count_per_video):
                print(f"  ✨ 正在生成第 {i+1}/{count_per_video} 份混剪...")
                
                # 2. 挑选素材 (挑选时长为总时长减去片头时长)
                selected_data = self._select_segments_for_duration(all_segments, remaining_duration)
                if not selected_data:
                    print("  ❌ 未能挑选到有效的素材。")
                    continue

                # 提取路径列表
                selected_paths = [item[0] for item in selected_data]
                
                print(f"  📺 混剪目标分辨率: {self.target_res[0]}x{self.target_res[1]} ({self.video_type})")

                # 3. 合成视频
                output_name = f"{video_path.stem}_remix_{i+1:02d}.mp4"
                output_path = self.output_dir / output_name
                
                success = self._combine_segments_with_audio(
                    selected_paths, audio_path, audio_duration, self.target_res, output_path, intro_path=intro_path
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

    def _extract_and_normalize_intro(self, video_path: Path) -> Optional[Path]:
        """
        截取模仿视频的前3秒作为片头，并根据 self.video_type 标准化为 TS 格式。

        :param video_path: 模仿视频路径
        :return: 标准化后的片头 TS 文件路径
        """
        if not video_path.exists():
            print(f"❌ 模仿视频文件不存在: {video_path}")
            return None

        width, height = self.target_res
        # 在文件名中加入 video_type，以便区分不同类型的缓存
        intro_filename = f"intro_{video_path.stem}_{self.video_type}_{width}x{height}.ts"
        intro_path = self.intro_dir / intro_filename

        if intro_path.exists():
            return intro_path

        # 提取前3秒并标准化的命令
        # 将 -ss 和 -t 放在 -i 之后作为输出参数，通常更稳定
        cmd = [
            ffmpeg_bin, "-y",
            "-i", str(video_path),
            "-ss", "0",
            "-t", "3",
        ]

        # 视频滤镜：缩放、填充、统一帧率
        vf_chain = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
        cmd.extend(["-vf", vf_chain])
        cmd.append("-an") # 片头不需要音频

        if self.use_gpu:
            cmd.extend([
                "-c:v", "h264_nvenc",
                "-preset", "p4",
                "-cq", "20",
                "-rc", "vbr",
                "-pix_fmt", "yuv420p"
            ])
        else:
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18"
            ])

        cmd.extend([
            "-f", "mpegts",
            str(intro_path)
        ])

        try:
            # 增加详细日志
            # print(f"  DEBUG: 执行 FFmpeg 命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, check=True, capture_output=True, **_popen_silent_kwargs())
            if intro_path.exists():
                return intro_path
            else:
                print(f"❌ FFmpeg 执行成功但未生成片头文件: {intro_path}")
                return None
        except subprocess.CalledProcessError as e:
            err = (e.stderr or b"").decode("utf-8", errors="ignore")
            print(f"❌ 提取片头失败 {video_path.name}: {err[:500]}")
            return None
        except Exception as e:
            print(f"❌ 提取片头过程中出现未知错误 {video_path.name}: {e}")
            return None

    def _normalize_segment(self, segment_path: Path, target_res: Tuple[int, int]) -> Optional[Path]:
        """
        将单个视频片段标准化为统一的分辨率、帧率和格式（MPEG-TS），以减少最终合成时的内存占用。
        
        :param segment_path: 原始视频路径
        :param target_res: 目标分辨率 (width, height)
        :return: 标准化后的 TS 文件路径
        """
        cache_key = (str(segment_path.resolve()), target_res)
        if cache_key in self._norm_cache:
            norm_path = self._norm_cache[cache_key]
            if norm_path.exists():
                return norm_path

        width, height = target_res
        # 使用稳定的文件名以便在同一次运行中复用
        # 移除了时间戳，改用简单的 stem + resolution
        norm_filename = f"norm_{segment_path.stem}_{width}x{height}.ts"
        norm_path = self.norm_dir / norm_filename

        # 标准化命令：缩放、填充、统一帧率(30)、去除音频
        # 使用较快的预设以节省时间，TS 格式对拼接非常友好
        cmd = [
            ffmpeg_bin, "-y",
        ]

        # 如果启用 GPU，在输入前尝试添加硬件加速解码（可选，但编码加速更关键）
        if self.use_gpu:
            # 注意：某些格式硬件解码可能失败，这里主要加速编码
            pass

        cmd.extend(["-i", str(segment_path)])
        
        # 视频滤镜：缩放、填充、统一帧率
        vf_chain = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
        cmd.extend(["-vf", vf_chain])
        
        cmd.append("-an") # 标准化过程不需要音频

        if self.use_gpu:
            # 使用 NVIDIA GPU 加速编码
            cmd.extend([
                "-c:v", "h264_nvenc",
                "-preset", "p4", # p4 是较快的平衡档位
                "-cq", "20",     # 保持高质量
                "-rc", "vbr",
                "-pix_fmt", "yuv420p"
            ])
        else:
            # 使用 CPU 编码
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18"
            ])

        cmd.extend([
            "-f", "mpegts",
            str(norm_path)
        ])

        try:
            subprocess.run(cmd, check=True, capture_output=True, **_popen_silent_kwargs())
            self._norm_cache[cache_key] = norm_path
            return norm_path
        except Exception as e:
            print(f"❌ 标准化分片失败 {segment_path.name}: {e}")
            return None

    def _combine_segments_with_audio(
        self, 
        video_segments: List[Path], 
        audio_path: Path, 
        target_duration: float,
        resolution: Tuple[int, int],
        output_path: Path,
        intro_path: Optional[Path] = None
    ) -> bool:
        """
        优化后的视频合成逻辑：先逐个标准化分片，再使用 concat demuxer 合并。
        极大地减少了 FFmpeg 的内存占用。
        """
        # 1. 逐个标准化分片
        normalized_paths = []
        
        # 如果提供了片头，将其放在最前面
        if intro_path and intro_path.exists():
            normalized_paths.append(intro_path)

        for p in video_segments:
            norm_p = self._normalize_segment(p, resolution)
            if norm_p:
                normalized_paths.append(norm_p)
        
        if not normalized_paths:
            return False

        # 2. 创建 concat 列表文件
        concat_list_path = self.temp_dir / f"concat_list_{int(time.time())}.txt"
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for p in normalized_paths:
                # 写入格式: file 'path/to/file'
                # 注意路径中的反斜杠在 FFmpeg concat 协议中需要转义，或者统一用正斜杠
                f.write(f"file '{str(p.absolute()).replace('\\', '/')}'\n")

        # 3. 最终合成
        # 使用 concat demuxer 合并视频，并混入音频
        cmd = [
            ffmpeg_bin, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_path),
            "-i", str(audio_path),
            "-map", "0:v:0", # 使用 concat 后的视频流
            "-map", "1:a:0", # 使用输入音频流
        ]

        # 添加动态编码参数
        cmd.extend(self._get_encoding_opts())

        cmd.extend([
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", f"{target_duration:.3f}",
            "-movflags", "+faststart",
            str(output_path)
        ])

        try:
            subprocess.run(cmd, check=True, capture_output=True, **_popen_silent_kwargs())
            return True
        except subprocess.CalledProcessError as e:
            err = (e.stderr or b"").decode("utf-8", errors="ignore")
            print(f"❌ FFmpeg 最终合成失败: {err[:500]}...")
            return False
        except Exception as e:
            print(f"❌ 合成过程中出现错误: {e}")
            return False
        finally:
            if concat_list_path.exists():
                try:
                    concat_list_path.unlink()
                except Exception:
                    pass

if __name__ == "__main__":
    # 该模块现在建议通过 video_remixed_video_audio_cli.py 调用
    print("请使用 video_remixed_video_audio_cli.py 运行该工具。")
