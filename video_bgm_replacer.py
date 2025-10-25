#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频BGM分离和替换工具 v3.0

改进版本，参考video_separator实现模式：
1. 增强的音频分离算法，支持多种分离策略
2. 分离质量控制和评估功能
3. 音频预处理功能（降噪、标准化等）
4. 可配置的分离参数
5. 多模型支持和自动选择

作者: AI Assistant
版本: 3.0.0
"""

import os
import sys
import gc
import random
import logging
import argparse
import subprocess
import threading
import signal
import atexit
import numpy as np
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Optional, Dict, Any
import time
from dataclasses import dataclass
from enum import Enum

try:
    import torch
    import torchaudio
    import librosa
    import soundfile as sf
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    import moviepy.editor as mp
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.audio.io.AudioFileClip import AudioFileClip
    from scipy import signal
    from scipy.signal import butter, filtfilt
    # 导入video_separator模块用于批量分离
    from video_separator import VideoSeparator
    from config import get_config, SUPPORTED_FORMATS
except ImportError as e:
    print(f"缺少必要的依赖库: {e}")
    print("请运行: pip install -r requirements.txt")
    sys.exit(1)


class SeparationStrategy(Enum):
    """音频分离策略枚举"""
    VOCALS_ONLY = "vocals_only"           # 只保留人声
    VOCALS_AND_OTHER = "vocals_and_other" # 保留人声和其他音频
    CUSTOM_MIX = "custom_mix"             # 自定义混合比例
    ADAPTIVE = "adaptive"                 # 自适应策略


@dataclass
class SeparationConfig:
    """音频分离配置"""
    strategy: SeparationStrategy = SeparationStrategy.VOCALS_ONLY  # 默认仅保留人声
    model_name: str = "htdemucs"
    overlap: float = 0.25
    split: bool = True
    vocals_volume: float = 2         # 人声音量增强30%，确保清晰突出
    drums_volume: float = 0.0           # 鼓声默认不保留
    bass_volume: float = 0.0            # 低音默认不保留
    other_volume: float = 0.2           # 其他音频（环境音等）适中保留，与新BGM平衡
    enable_preprocessing: bool = True
    enable_quality_check: bool = True
    quality_threshold: float = 0.7
    # 新增：批量分离配置
    use_batch_separation: bool = False  # 是否使用video_separator的批量分离功能
    batch_max_workers: int = 2          # 批量分离的最大并发数


@dataclass
class AudioQualityMetrics:
    """音频质量评估指标"""
    snr: float = 0.0          # 信噪比
    spectral_centroid: float = 0.0  # 频谱质心
    zero_crossing_rate: float = 0.0  # 过零率
    rms_energy: float = 0.0   # RMS能量
    quality_score: float = 0.0  # 综合质量分数


class AudioPreprocessor:
    """音频预处理器"""
    
    def __init__(self, sample_rate: int = 44100):
        """
        初始化音频预处理器
        
        Args:
            sample_rate: 采样率
        """
        self.sample_rate = sample_rate
    
    def normalize_audio(self, audio: np.ndarray) -> np.ndarray:
        """
        音频标准化
        
        Args:
            audio: 输入音频数组
            
        Returns:
            标准化后的音频
        """
        # 防止除零
        max_val = np.max(np.abs(audio))
        if max_val > 0:
            return audio / max_val * 0.95
        return audio
    
    def apply_highpass_filter(self, audio: np.ndarray, cutoff: float = 80.0) -> np.ndarray:
        """
        应用高通滤波器去除低频噪声
        
        Args:
            audio: 输入音频
            cutoff: 截止频率
            
        Returns:
            滤波后的音频
        """
        nyquist = self.sample_rate * 0.5
        normal_cutoff = cutoff / nyquist
        b, a = butter(5, normal_cutoff, btype='high', analog=False)
        return filtfilt(b, a, audio)
    
    def reduce_noise(self, audio: np.ndarray, noise_factor: float = 0.1) -> np.ndarray:
        """
        简单的噪声抑制
        
        Args:
            audio: 输入音频
            noise_factor: 噪声抑制因子
            
        Returns:
            降噪后的音频
        """
        # 计算音频的RMS
        rms = np.sqrt(np.mean(audio**2))
        
        # 如果RMS太小，认为是噪声
        threshold = rms * noise_factor
        mask = np.abs(audio) > threshold
        
        # 应用软阈值
        return audio * mask + audio * (1 - mask) * 0.1
    
    def preprocess(self, audio: np.ndarray) -> np.ndarray:
        """
        完整的音频预处理流程
        
        Args:
            audio: 输入音频
            
        Returns:
            预处理后的音频
        """
        # 标准化
        audio = self.normalize_audio(audio)
        
        # 高通滤波
        audio = self.apply_highpass_filter(audio)
        
        # 降噪
        audio = self.reduce_noise(audio)
        
        return audio


class AudioQualityAnalyzer:
    """音频质量分析器"""
    
    def __init__(self, sample_rate: int = 44100):
        """
        初始化音频质量分析器
        
        Args:
            sample_rate: 采样率
        """
        self.sample_rate = sample_rate
    
    def calculate_snr(self, signal_audio: np.ndarray, noise_audio: np.ndarray) -> float:
        """
        计算信噪比
        
        Args:
            signal_audio: 信号音频
            noise_audio: 噪声音频
            
        Returns:
            信噪比 (dB)
        """
        signal_power = np.mean(signal_audio**2)
        noise_power = np.mean(noise_audio**2)
        
        if noise_power == 0:
            return float('inf')
        
        snr = 10 * np.log10(signal_power / noise_power)
        return snr
    
    def calculate_spectral_centroid(self, audio: np.ndarray) -> float:
        """
        计算频谱质心
        
        Args:
            audio: 输入音频
            
        Returns:
            频谱质心
        """
        centroid = librosa.feature.spectral_centroid(y=audio, sr=self.sample_rate)[0]
        return np.mean(centroid)
    
    def calculate_zero_crossing_rate(self, audio: np.ndarray) -> float:
        """
        计算过零率
        
        Args:
            audio: 输入音频
            
        Returns:
            过零率
        """
        zcr = librosa.feature.zero_crossing_rate(audio)[0]
        return np.mean(zcr)
    
    def calculate_rms_energy(self, audio: np.ndarray) -> float:
        """
        计算RMS能量
        
        Args:
            audio: 输入音频
            
        Returns:
            RMS能量
        """
        rms = librosa.feature.rms(y=audio)[0]
        return np.mean(rms)
    
    def analyze_quality(self, audio: np.ndarray, original_audio: np.ndarray = None) -> AudioQualityMetrics:
        """
        分析音频质量
        
        Args:
            audio: 待分析音频
            original_audio: 原始音频（用于计算SNR）
            
        Returns:
            音频质量指标
        """
        metrics = AudioQualityMetrics()
        
        # 计算各项指标
        metrics.spectral_centroid = self.calculate_spectral_centroid(audio)
        metrics.zero_crossing_rate = self.calculate_zero_crossing_rate(audio)
        metrics.rms_energy = self.calculate_rms_energy(audio)
        
        if original_audio is not None:
            # 计算残差作为噪声
            residual = original_audio - audio
            metrics.snr = self.calculate_snr(audio, residual)
        
        # 计算综合质量分数 (0-1)
        # 这是一个简化的评分算法，可以根据需要调整
        centroid_score = min(metrics.spectral_centroid / 2000, 1.0)  # 标准化到0-1
        zcr_score = 1.0 - min(metrics.zero_crossing_rate * 10, 1.0)  # 过零率越低越好
        rms_score = min(metrics.rms_energy * 10, 1.0)  # RMS能量
        
        metrics.quality_score = (centroid_score + zcr_score + rms_score) / 3
        
        return metrics


class AdvancedAudioSeparator:
    """高级音频分离器"""
    
    def __init__(self, config: SeparationConfig, device: str = 'cpu'):
        """
        初始化高级音频分离器
        
        Args:
            config: 分离配置
            device: 计算设备
        """
        self.config = config
        self.device = device
        self.model = None
        self.preprocessor = AudioPreprocessor()
        self.quality_analyzer = AudioQualityAnalyzer()
        self.logger = logging.getLogger(__name__)
        
        self._load_model()
    
    def _load_model(self):
        """加载demucs模型"""
        try:
            self.model = get_model(self.config.model_name)
            self.model.to(self.device)
            self.model.eval()
            self.logger.info(f"已加载模型: {self.config.model_name}")
        except Exception as e:
            self.logger.error(f"模型加载失败: {e}")
            raise
    
    def _apply_separation_strategy(self, sources: torch.Tensor) -> torch.Tensor:
        """
        应用分离策略
        
        Args:
            sources: demucs分离的音频源 [vocals, drums, bass, other]
            
        Returns:
            处理后的音频
        """
        vocals, drums, bass, other = sources[0], sources[1], sources[2], sources[3]
        
        if self.config.strategy == SeparationStrategy.VOCALS_ONLY:
            # 只保留人声，完全去除背景音乐
            return vocals * self.config.vocals_volume
        
        elif self.config.strategy == SeparationStrategy.VOCALS_AND_OTHER:
            # 保留人声和环境音，去除原BGM（最佳默认策略）
            # 这是最符合BGM替换需求的策略：
            # - 完全保留人声对话/歌唱
            # - 保留环境音效（脚步声、风声、机械声等）
            # - 去除原始背景音乐，为新BGM让路
            # - 与新BGM混合后效果最自然
            self.logger.debug(f"应用VOCALS_AND_OTHER策略: 人声音量={self.config.vocals_volume}, 环境音音量={self.config.other_volume}")
            return (vocals * self.config.vocals_volume + 
                   other * self.config.other_volume)
        
        elif self.config.strategy == SeparationStrategy.CUSTOM_MIX:
            # 自定义混合比例，完全可控
            return (vocals * self.config.vocals_volume +
                   drums * self.config.drums_volume +
                   bass * self.config.bass_volume +
                   other * self.config.other_volume)
        
        elif self.config.strategy == SeparationStrategy.ADAPTIVE:
            # 自适应策略：根据音频特征动态调整
            return self._adaptive_mix(vocals, drums, bass, other)
        
        else:
            # 默认策略：保留人声和适量环境音
            self.logger.warning("使用默认分离策略")
            return vocals * 1.0 + other * 0.4
    
    def _adaptive_mix(self, vocals: torch.Tensor, drums: torch.Tensor, 
                     bass: torch.Tensor, other: torch.Tensor) -> torch.Tensor:
        """
        自适应混合策略 - 智能分析音频特征并优化混合比例
        
        Args:
            vocals, drums, bass, other: 分离的音频源
            
        Returns:
            自适应混合后的音频
        """
        # 计算各个源的能量
        vocals_energy = torch.mean(vocals**2)
        drums_energy = torch.mean(drums**2)
        bass_energy = torch.mean(bass**2)
        other_energy = torch.mean(other**2)
        
        total_energy = vocals_energy + drums_energy + bass_energy + other_energy
        
        if total_energy == 0:
            return vocals
        
        # 根据能量比例调整混合权重
        vocals_ratio = vocals_energy / total_energy
        other_ratio = other_energy / total_energy
        
        self.logger.debug(f"自适应混合分析: 人声比例={vocals_ratio:.3f}, 其他比例={other_ratio:.3f}")
        
        # 优化的自适应策略：更好地保留人声，适度保留环境音
        if vocals_ratio > 0.6:  # 人声占主导
            other_volume = 0.2
            self.logger.debug("检测到人声主导，降低环境音")
        elif vocals_ratio > 0.3:  # 人声中等
            other_volume = 0.3
            self.logger.debug("检测到人声中等，保持适中环境音")
        else:  # 人声较少
            other_volume = 0.5
            self.logger.debug("检测到人声较少，保留更多环境音")
            
        return vocals * 1.0 + other * other_volume
    
    def _optimize_vocal_separation(self, separated_audio: torch.Tensor) -> torch.Tensor:
        """
        优化人声分离效果
        
        Args:
            separated_audio: 分离后的音频
            
        Returns:
            优化后的音频
        """
        # 应用轻微的高通滤波，增强人声清晰度
        if separated_audio.dim() == 2:
            # 转换为numpy进行处理
            audio_np = separated_audio.cpu().numpy()
            
            # 简单的高通滤波（去除低频噪音）
            try:
                # 设计高通滤波器（截止频率80Hz，适合去除低频噪音但保留人声）
                sos = signal.butter(2, 80, btype='high', fs=44100, output='sos')
                filtered_audio = signal.sosfilt(sos, audio_np, axis=1)
                
                # 转换回tensor
                optimized_audio = torch.from_numpy(filtered_audio).to(separated_audio.device)
                self.logger.debug("应用高通滤波优化人声")
                return optimized_audio
            except Exception as e:
                self.logger.warning(f"音频优化失败，返回原始音频: {e}")
                return separated_audio
        
        return separated_audio
    
    def separate_audio(self, audio_path: Path, tmp_dir: Path) -> Tuple[Optional[Path], Optional[AudioQualityMetrics]]:
        """
        分离音频
        
        Args:
            audio_path: 音频文件路径
            tmp_dir: 临时文件目录
            
        Returns:
            (分离后的音频路径, 质量指标)
        """
        try:
            # 确保audio_path是Path对象
            if isinstance(audio_path, str):
                audio_path = Path(audio_path)
            
            self.logger.info(f"开始分离音频: {audio_path.name}")
            
            # 加载音频并确保数据类型为float32
            try:
                waveform, sample_rate = torchaudio.load(str(audio_path))
                # 强制转换为float32类型，避免double/float类型不匹配
                waveform = waveform.float()
                original_audio = waveform.clone()
                self.logger.debug(f"音频加载成功，数据类型: {waveform.dtype}, 形状: {waveform.shape}")
            except Exception as load_error:
                self.logger.error(f"音频加载失败: {load_error}")
                return None, None
            
            # 确保音频为立体声格式（demucs要求2通道输入）
            if waveform.shape[0] == 1:
                # 单声道转立体声：复制通道
                waveform = waveform.repeat(2, 1)
                self.logger.debug("单声道音频已转换为立体声")
            elif waveform.shape[0] > 2:
                # 多通道音频转立体声：取前两个通道
                waveform = waveform[:2, :]
                self.logger.debug("多通道音频已转换为立体声")
            
            # 预处理（保持立体声格式）
            if self.config.enable_preprocessing:
                # 对每个通道分别进行预处理
                processed_channels = []
                for channel_idx in range(waveform.shape[0]):
                    channel_audio = waveform[channel_idx].numpy()
                    processed_channel = self.preprocessor.preprocess(channel_audio)
                    processed_channels.append(processed_channel)
                
                # 重新组合为立体声tensor
                processed_audio = np.stack(processed_channels, axis=0)
                waveform = torch.from_numpy(processed_audio).float()
            
            # 确保tensor在正确设备上且为float32类型
            waveform = waveform.to(self.device).float()
            
            # 应用demucs模型
            with torch.no_grad():
                # 确保输入tensor为float32类型，并添加batch维度
                model_input = waveform.unsqueeze(0).float()
                self.logger.debug(f"模型输入数据类型: {model_input.dtype}, 形状: {model_input.shape}")
                
                # 验证输入形状：应该是 [batch_size, channels, samples]
                if model_input.dim() != 3 or model_input.shape[1] != 2:
                    raise ValueError(f"模型输入形状错误: {model_input.shape}, 期望: [1, 2, samples]")
                
                sources = apply_model(
                    self.model, 
                    model_input,
                    device=self.device,
                    split=self.config.split,
                    overlap=self.config.overlap
                )[0]
            
            # 应用分离策略
            separated_audio = self._apply_separation_strategy(sources)
            
            # 优化人声分离效果（仅对VOCALS_AND_OTHER和VOCALS_ONLY策略应用）
            if self.config.strategy in [SeparationStrategy.VOCALS_AND_OTHER, SeparationStrategy.VOCALS_ONLY]:
                separated_audio = self._optimize_vocal_separation(separated_audio)
                self.logger.debug("应用人声分离优化")
            
            # 保存分离后的音频到临时目录
            output_path = tmp_dir / f"{audio_path.stem}_separated.wav"
            torchaudio.save(str(output_path), separated_audio.cpu(), sample_rate)
            
            # 质量评估
            quality_metrics = None
            if self.config.enable_quality_check:
                separated_np = separated_audio.cpu().numpy()
                if separated_np.ndim > 1:
                    separated_np = np.mean(separated_np, axis=0)
                
                original_np = original_audio.numpy()
                if original_np.ndim > 1:
                    original_np = np.mean(original_np, axis=0)
                
                quality_metrics = self.quality_analyzer.analyze_quality(
                    separated_np, original_np
                )
                
                self.logger.info(f"分离质量分数: {quality_metrics.quality_score:.3f}")
                
                # 如果质量不达标，记录警告
                if quality_metrics.quality_score < self.config.quality_threshold:
                    self.logger.warning(f"分离质量较低: {quality_metrics.quality_score:.3f}")
            
            # 清理GPU内存
            if self.device == 'cuda':
                torch.cuda.empty_cache()
            
            self.logger.info(f"音频分离完成: {output_path.name}")
            return output_path, quality_metrics
            
        except Exception as e:
            self.logger.error(f"音频分离失败 {audio_path.name}: {e}")
            return None, None


class VideoBGMReplacer:
    """视频BGM分离和替换处理器 v3.0"""
    
    def __init__(self, video_dir: str, bgm_dir: str, max_workers: int = 4, 
                 separation_config: Optional[SeparationConfig] = None):
        """
        初始化BGM替换器
        
        Args:
            video_dir: 视频文件目录
            bgm_dir: BGM音频文件目录
            max_workers: 最大并发线程数
            separation_config: 分离配置
        """
        self.video_dir = Path(video_dir)
        self.bgm_dir = Path(bgm_dir)
        self.max_workers = max_workers
        self.separation_config = separation_config or SeparationConfig()
        
        # 创建临时目录
        self.tmp_dir = self.video_dir / "tmp"
        self.output_dir = self.video_dir / "mixed_bgm_video"
        
        # 支持的文件格式
        self.video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm'}
        self.audio_extensions = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a'}
        
        # 检测GPU支持
        self.device = self._detect_device()
        
        # 设置日志
        self._setup_logging()
        
        # 创建高级音频分离器
        self.audio_separator = AdvancedAudioSeparator(self.separation_config, self.device)
        
        # 创建video_separator实例（用于批量分离）
        if self.separation_config.use_batch_separation:
            self.video_separator = VideoSeparator()
            self.logger.info("✅ 已启用批量分离模式")
        else:
            self.video_separator = None
            self.logger.info("✅ 使用传统分离模式")
        
        # 创建必要目录
        self._create_directories()
        
        # 统计信息
        self.stats = {
            'total_videos': 0,
            'successful': 0,
            'failed': 0,
            'avg_quality_score': 0.0
        }
    
    def _detect_device(self) -> str:
        """检测可用的计算设备（GPU优先）"""
        if torch.cuda.is_available():
            device = 'cuda'
            gpu_name = torch.cuda.get_device_name(0)
            print(f"检测到GPU: {gpu_name}，将使用GPU加速")
        else:
            device = 'cpu'
            print("未检测到可用GPU，将使用CPU处理")
        return device
    
    def _setup_logging(self):
        """设置日志系统"""
        log_file = self.video_dir / "bgm_replacement.log"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def _create_directories(self):
        """创建必要的目录"""
        self.tmp_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        
        # 设置Demucs临时目录环境变量
        os.environ['TMPDIR'] = str(self.tmp_dir)
        os.environ['TEMP'] = str(self.tmp_dir)
        os.environ['TMP'] = str(self.tmp_dir)
        
        self.logger.info(f"工作目录已创建: {self.tmp_dir}")
        self.logger.info(f"输出目录已创建: {self.output_dir}")
        self.logger.info(f"Demucs临时目录已设置: {self.tmp_dir}")
    
    def get_video_files(self) -> List[Path]:
        """获取所有视频文件"""
        video_files = []
        for ext in self.video_extensions:
            video_files.extend(self.video_dir.glob(f"*{ext}"))
        return sorted(video_files)
    
    def get_bgm_files(self) -> List[Path]:
        """获取所有BGM文件"""
        bgm_files = []
        for ext in self.audio_extensions:
            bgm_files.extend(self.bgm_dir.glob(f"*{ext}"))
        return sorted(bgm_files)
    
    def cleanup_temp_files(self, keep_recent: bool = False):
        """
        清理临时文件
        
        Args:
            keep_recent: 是否保留最近的临时文件（用于调试）
        """
        try:
            if not self.tmp_dir.exists():
                return
            
            temp_files = list(self.tmp_dir.glob("*"))
            if not temp_files:
                self.logger.debug("临时目录为空，无需清理")
                return
            
            if keep_recent:
                # 保留最近1小时内创建的文件
                import time
                current_time = time.time()
                one_hour_ago = current_time - 3600
                
                for temp_file in temp_files:
                    if temp_file.is_file() and temp_file.stat().st_mtime < one_hour_ago:
                        try:
                            temp_file.unlink()
                            self.logger.debug(f"已删除旧临时文件: {temp_file}")
                        except Exception as e:
                            self.logger.warning(f"删除临时文件失败 {temp_file}: {e}")
            else:
                # 删除所有临时文件
                for temp_file in temp_files:
                    try:
                        if temp_file.is_file():
                            temp_file.unlink()
                        elif temp_file.is_dir():
                            import shutil
                            shutil.rmtree(temp_file)
                        self.logger.debug(f"已删除临时文件: {temp_file}")
                    except Exception as e:
                        self.logger.warning(f"删除临时文件失败 {temp_file}: {e}")
            
            self.logger.info(f"临时文件清理完成，清理了 {len(temp_files)} 个文件/目录")
            
        except Exception as e:
            self.logger.error(f"临时文件清理失败: {e}")
    
    def create_looped_bgm(self, bgm_path: Path, target_duration: float) -> Path:
        """创建循环BGM以匹配视频长度"""
        try:
            bgm_clip = AudioFileClip(str(bgm_path))
            bgm_duration = bgm_clip.duration
            
            if bgm_duration >= target_duration:
                # BGM比视频长，直接截取
                looped_bgm = bgm_clip.subclip(0, target_duration)
            else:
                # BGM比视频短，需要循环
                loop_count = int(target_duration / bgm_duration) + 1
                clips = [bgm_clip] * loop_count
                looped_bgm = mp.concatenate_audioclips(clips).subclip(0, target_duration)
            
            # 保存循环BGM
            looped_path = self.tmp_dir / f"{bgm_path.stem}_looped.wav"
            looped_bgm.write_audiofile(str(looped_path), verbose=False, logger=None)
            
            bgm_clip.close()
            looped_bgm.close()
            
            return looped_path
            
        except Exception as e:
            self.logger.error(f"创建循环BGM失败: {e}")
            return bgm_path
    
    def _get_codec_params(self) -> Dict[str, Any]:
        """
        获取视频编码参数，针对不同设备优化
        
        Returns:
            Dict[str, Any]: 编码参数字典
        """
        if self.device == 'cuda':
            # GPU加速编码参数
            return {
                'codec': 'h264_nvenc',  # NVIDIA GPU编码器
                'preset': 'fast',       # 快速预设
                'bitrate': '2000k',     # 较高比特率保证质量
                'threads': 1,
                'fps': 24,
                'audio_codec': 'aac',
                'audio_bitrate': '128k'
            }
        else:
            # CPU编码参数
            return {
                'codec': 'libx264',     # CPU编码器
                'preset': 'medium',     # 平衡质量和速度
                'bitrate': '1500k',     # 适中比特率
                'threads': 1,
                'fps': 24,
                'audio_codec': 'aac',
                'audio_bitrate': '128k'
            }
    
    def combine_video_with_new_bgm(self, video_path: Path, separated_audio_path: Path, 
                                 bgm_path: Path) -> bool:
        """
        将分离的音频与新BGM合成
        
        Args:
            video_path: 视频文件路径
            separated_audio_path: 分离后的音频文件路径
            bgm_path: 新BGM文件路径
            
        Returns:
            bool: 合成是否成功
        """
        video_clip = None
        separated_audio = None
        new_bgm = None
        mixed_audio = None
        final_video = None
        looped_bgm_path = None
        
        try:
            self.logger.info(f"🎬 开始视频合成: {video_path.name}")
            self.logger.info(f"📁 输入文件:")
            self.logger.info(f"   - 视频: {video_path.name}")
            self.logger.info(f"   - 分离音频: {separated_audio_path.name}")
            self.logger.info(f"   - BGM: {bgm_path.name}")
            
            # 加载视频（不包含音频）
            self.logger.info("📹 加载视频文件...")
            video_clip = VideoFileClip(str(video_path)).without_audio()
            video_duration = video_clip.duration
            self.logger.info(f"   - 视频时长: {video_duration:.2f}秒")
            self.logger.info(f"   - 视频分辨率: {video_clip.size}")
            self.logger.info(f"   - 视频帧率: {video_clip.fps}")
            
            # 创建循环BGM
            self.logger.info("🎵 处理BGM音频...")
            looped_bgm_path = self.create_looped_bgm(bgm_path, video_duration)
            self.logger.info(f"   - BGM循环文件已创建: {looped_bgm_path.name}")
            
            # 加载音频
            self.logger.info("🔊 加载音频文件...")
            separated_audio = AudioFileClip(str(separated_audio_path))
            new_bgm = AudioFileClip(str(looped_bgm_path))
            self.logger.info(f"   - 分离音频时长: {separated_audio.duration:.2f}秒")
            self.logger.info(f"   - BGM音频时长: {new_bgm.duration:.2f}秒")
            
            # 智能音频混合：根据人声强度动态调整BGM音量
            self.logger.info("🎛️ 智能混合音频...")
            
            # 分析人声强度，动态调整BGM音量
            try:
                separated_audio_array = separated_audio.to_soundarray()
                vocal_rms = np.sqrt(np.mean(separated_audio_array**2))
            except Exception as e:
                self.logger.warning(f"无法分析人声强度，使用默认BGM音量: {e}")
                vocal_rms = 0.05  # 默认中等强度
            
            # 优化的BGM音量调整算法：确保人声清晰突出，BGM适中
            # 基于人声强度的智能音量调整，人声优先策略
            if vocal_rms > 0.15:  # 人声很强（如演讲、歌唱）
                bgm_volume_factor = 0.12  # BGM很轻，完全突出人声
                vocal_volume_factor = 1.4  # 进一步增强强人声
            elif vocal_rms > 0.08:  # 人声较强（正常对话）
                bgm_volume_factor = 0.18  # BGM适中，保持平衡
                vocal_volume_factor = 1.3  # 增强正常对话
            elif vocal_rms > 0.03:  # 人声中等（轻声对话）
                bgm_volume_factor = 0.25  # BGM稍强，营造氛围
                vocal_volume_factor = 1.5  # 显著增强轻声对话
            else:  # 人声较弱或无人声段落
                bgm_volume_factor = 0.35  # BGM较强，填充空白
                vocal_volume_factor = 1.6  # 大幅增强微弱人声
                
            self.logger.info(f"   - 人声RMS强度: {vocal_rms:.4f}")
            self.logger.info(f"   - 分离音频音量: {vocal_volume_factor} (人声增强)")
            self.logger.info(f"   - BGM音量: {bgm_volume_factor}")
            
            # 计算总音量因子（在混合后计算）
            if vocal_volume_factor >= 1.5:
                total_volume_factor = 0.75
            elif vocal_volume_factor >= 1.3:
                total_volume_factor = 0.80
            else:
                total_volume_factor = 0.85
            
            self.logger.info(f"   - 总音量调整: {total_volume_factor} (防止削波)")
            
            # 应用优化的智能音频混合
            mixed_audio = mp.CompositeAudioClip([
                separated_audio.volumex(vocal_volume_factor),  # 根据人声强度调整分离音频音量
                new_bgm.volumex(bgm_volume_factor)  # 动态调整BGM音量
            ])
            
            # 智能音频标准化：根据混合后的音频特性调整总音量
            # 考虑到人声增强，适当降低总音量避免削波，同时保持清晰度
            mixed_audio = mixed_audio.volumex(total_volume_factor)  # 动态调整总音量
            
            # 合成最终视频
            self.logger.info("🎞️ 合成最终视频...")
            final_video = video_clip.set_audio(mixed_audio)
            
            # 输出文件路径
            output_path = self.output_dir / f"{video_path.stem}_with_new_bgm.mp4"
            self.logger.info(f"📤 输出路径: {output_path}")
            
            # 获取编码参数（针对GPU优化）
            codec_params = self._get_codec_params()
            self.logger.info(f"⚙️ 编码参数: {codec_params}")
            
            # 如果有GPU，使用GPU加速编码
            if self.device == 'cuda':
                self.logger.info("🚀 使用GPU加速视频编码...")
                # 为GPU优化编码参数
                codec_params.update({
                    'codec': 'h264_nvenc',  # 使用NVIDIA GPU编码器
                    'preset': 'fast',
                    'bitrate': '2000k'
                })
                self.logger.info(f"   - GPU编码器: h264_nvenc")
            else:
                self.logger.info("💻 使用CPU进行视频编码...")
            
            # 写入视频文件
            self.logger.info("💾 开始写入视频文件...")
            start_time = time.time()
            
            final_video.write_videofile(
                str(output_path),
                verbose=False,
                logger=None,
                **codec_params
            )
            
            encode_time = time.time() - start_time
            self.logger.info(f"✅ 视频编码完成，耗时: {encode_time:.2f}秒")
            
            # 获取输出文件信息
            if output_path.exists():
                file_size = output_path.stat().st_size / (1024 * 1024)  # MB
                self.logger.info(f"📊 输出文件信息:")
                self.logger.info(f"   - 文件大小: {file_size:.2f} MB")
                self.logger.info(f"   - 文件路径: {output_path}")
            
            self.logger.info(f"🎉 视频合成完成: {output_path.name}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ 视频合成失败 {video_path.name}: {e}")
            self.logger.error(f"   - 错误类型: {type(e).__name__}")
            self.logger.error(f"   - 错误详情: {str(e)}")
            return False
        finally:
            # 资源清理
            self.logger.info("🧹 清理资源...")
            clips = [video_clip, separated_audio, new_bgm, mixed_audio, final_video]
            for i, clip in enumerate(clips):
                if clip:
                    try:
                        clip.close()
                        self.logger.debug(f"   - 已释放资源 {i+1}/5")
                    except Exception as cleanup_error:
                        self.logger.warning(f"   - 资源释放警告: {cleanup_error}")
            
            # 清理临时文件
            if looped_bgm_path and looped_bgm_path.exists():
                try:
                    time.sleep(1.0)
                    looped_bgm_path.unlink()
                    self.logger.info(f"   - 已删除临时BGM文件: {looped_bgm_path.name}")
                except Exception as cleanup_error:
                    self.logger.warning(f"   - 清理循环BGM文件失败: {cleanup_error}")
            
            # 强制垃圾回收和GPU内存清理
            gc.collect()
            if self.device == 'cuda':
                torch.cuda.empty_cache()
                self.logger.info("   - GPU内存已清理")
            self.logger.info("✨ 资源清理完成")
    
    def process_single_video(self, video_path: Path, bgm_files: List[Path]) -> bool:
        """
        处理单个视频文件
        
        Args:
            video_path: 视频文件路径
            bgm_files: BGM文件列表
            
        Returns:
            bool: 处理是否成功
        """
        separated_audio_path = None
        try:
            self.logger.info("=" * 60)
            self.logger.info(f"🎯 开始处理视频: {video_path.name}")
            self.logger.info(f"📂 文件路径: {video_path}")
            
            # 随机选择BGM
            selected_bgm = random.choice(bgm_files)
            self.logger.info(f"🎵 为 {video_path.name} 选择BGM: {selected_bgm.name}")
            self.logger.info(f"📂 BGM路径: {selected_bgm}")
            
            # 提取视频音频
            self.logger.info("🎬 提取视频音频...")
            video_clip = VideoFileClip(str(video_path))
            if video_clip.audio is None:
                self.logger.warning(f"⚠️ 视频文件没有音频轨道: {video_path.name}")
                video_clip.close()
                return False
            
            self.logger.info(f"   - 视频时长: {video_clip.duration:.2f}秒")
            self.logger.info(f"   - 音频采样率: {video_clip.audio.fps}Hz")
            
            audio_path = self.tmp_dir / f"{video_path.stem}_original.wav"
            self.logger.info(f"💾 保存原始音频到: {audio_path.name}")
            
            video_clip.audio.write_audiofile(str(audio_path), verbose=False, logger=None)
            video_clip.close()
            
            # 使用高级分离器分离音频
            self.logger.info("🔧 开始音频分离...")
            self.logger.info(f"   - 分离策略: {self.separation_config.strategy.value}")
            self.logger.info(f"   - 使用模型: {self.separation_config.model_name}")
            self.logger.info(f"   - 计算设备: {self.device}")
            
            separated_audio_path, quality_metrics = self.audio_separator.separate_audio(audio_path, self.tmp_dir)
            
            if not separated_audio_path:
                self.logger.error("❌ 音频分离失败")
                return False
            
            self.logger.info(f"✅ 音频分离完成: {separated_audio_path.name}")
            
            # 更新统计信息
            if quality_metrics:
                self.stats['avg_quality_score'] += quality_metrics.quality_score
                self.logger.info(f"📊 分离质量评估:")
                self.logger.info(f"   - 质量分数: {quality_metrics.quality_score:.3f}")
                self.logger.info(f"   - 信噪比: {quality_metrics.snr:.2f} dB")
                self.logger.info(f"   - 频谱质心: {quality_metrics.spectral_centroid:.2f} Hz")
                self.logger.info(f"   - RMS能量: {quality_metrics.rms_energy:.4f}")
            
            # 合成新视频
            self.logger.info("🎞️ 开始视频合成...")
            success = self.combine_video_with_new_bgm(video_path, separated_audio_path, selected_bgm)
            
            # 清理原始音频文件
            if audio_path.exists():
                audio_path.unlink()
                self.logger.info(f"🗑️ 已清理原始音频文件: {audio_path.name}")
            
            if success:
                self.logger.info(f"🎉 视频处理成功: {video_path.name}")
            else:
                self.logger.error(f"❌ 视频处理失败: {video_path.name}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"❌ 处理视频异常 {video_path.name}: {e}")
            self.logger.error(f"   - 错误类型: {type(e).__name__}")
            self.logger.error(f"   - 错误详情: {str(e)}")
            return False
        finally:
            # 清理临时文件
            if separated_audio_path and separated_audio_path.exists():
                try:
                    time.sleep(1.0)
                    separated_audio_path.unlink()
                    self.logger.info(f"🗑️ 已清理分离音频文件: {separated_audio_path.name}")
                except Exception as cleanup_error:
                    self.logger.warning(f"⚠️ 清理分离音频文件失败: {cleanup_error}")
            
            self.logger.info("=" * 60)
    
    def process_videos_with_batch_separation(self, video_files: List[Path], bgm_files: List[Path]) -> bool:
        """
        使用video_separator的批量分离功能处理视频
        
        Args:
            video_files: 视频文件列表
            bgm_files: BGM文件列表
            
        Returns:
            bool: 处理是否成功
        """
        try:
            self.logger.info("🔧 使用批量分离模式处理视频...")
            
            # 创建临时分离目录
            batch_separation_dir = self.tmp_dir / "batch_separated"
            batch_separation_dir.mkdir(exist_ok=True)
            
            # 使用video_separator进行批量分离
            self.logger.info(f"📂 批量分离输入目录: {self.video_dir}")
            self.logger.info(f"📂 批量分离输出目录: {batch_separation_dir}")
            
            # 调用batch_separate_videos函数
            separation_results = self.video_separator.batch_separate_videos(
                input_dir=str(self.video_dir),
                output_dir=str(batch_separation_dir),
                max_workers=self.separation_config.batch_max_workers,
                extract_silent=False,  # 我们不需要无声视频
                separate_audio=True    # 需要分离音频
            )
            
            self.logger.info(f"✅ 批量分离完成，成功处理 {separation_results['successful']} 个文件")
            
            if separation_results['failed'] > 0:
                self.logger.warning(f"⚠️ {separation_results['failed']} 个文件分离失败")
            
            # 处理分离后的音频文件，与BGM合成
            success_count = 0
            for video_file in video_files:
                try:
                    # 查找对应的分离音频文件
                    separated_audio_dir = batch_separation_dir / video_file.stem
                    vocals_file = separated_audio_dir / "vocals.wav"
                    other_file = separated_audio_dir / "other.wav"
                    
                    if not vocals_file.exists():
                        self.logger.error(f"❌ 未找到分离的人声文件: {vocals_file}")
                        continue
                    
                    # 根据分离策略合成音频
                    combined_audio_path = self._combine_separated_audio(
                        vocals_file, other_file, video_file.stem
                    )
                    
                    if combined_audio_path:
                        # 随机选择BGM并合成视频
                        selected_bgm = random.choice(bgm_files)
                        self.logger.info(f"🎵 为 {video_file.name} 选择BGM: {selected_bgm.name}")
                        
                        if self.combine_video_with_new_bgm(video_file, combined_audio_path, selected_bgm):
                            success_count += 1
                            self.logger.info(f"✅ 视频处理成功: {video_file.name}")
                        else:
                            self.logger.error(f"❌ 视频合成失败: {video_file.name}")
                    
                except Exception as e:
                    self.logger.error(f"❌ 处理视频异常 {video_file.name}: {e}")
            
            # 清理临时分离文件
            try:
                import shutil
                shutil.rmtree(batch_separation_dir)
                self.logger.info("🗑️ 已清理批量分离临时文件")
            except Exception as cleanup_error:
                self.logger.warning(f"⚠️ 清理批量分离临时文件失败: {cleanup_error}")
            
            self.stats['successful_videos'] = success_count
            self.stats['failed_videos'] = len(video_files) - success_count
            
            return success_count > 0
            
        except Exception as e:
            self.logger.error(f"❌ 批量分离处理异常: {e}")
            return False
    
    def _combine_separated_audio(self, vocals_file: Path, other_file: Path, video_stem: str) -> Optional[Path]:
        """
        根据分离策略合成分离后的音频
        
        Args:
            vocals_file: 人声文件路径
            other_file: 其他音频文件路径
            video_stem: 视频文件名（不含扩展名）
            
        Returns:
            合成后的音频文件路径
        """
        try:
            # 加载音频文件
            vocals_audio, sr = librosa.load(str(vocals_file), sr=None)
            
            # 根据策略处理
            if self.separation_config.strategy == SeparationStrategy.VOCALS_ONLY:
                # 只保留人声
                combined_audio = vocals_audio * self.separation_config.vocals_volume
            elif self.separation_config.strategy == SeparationStrategy.VOCALS_AND_OTHER:
                # 保留人声和其他音频
                if other_file.exists():
                    other_audio, _ = librosa.load(str(other_file), sr=sr)
                    # 确保音频长度一致
                    min_length = min(len(vocals_audio), len(other_audio))
                    vocals_audio = vocals_audio[:min_length]
                    other_audio = other_audio[:min_length]
                    
                    combined_audio = (vocals_audio * self.separation_config.vocals_volume + 
                                    other_audio * self.separation_config.other_volume)
                else:
                    combined_audio = vocals_audio * self.separation_config.vocals_volume
            else:
                # 默认只保留人声
                combined_audio = vocals_audio * self.separation_config.vocals_volume
            
            # 保存合成音频
            output_path = self.tmp_dir / f"{video_stem}_combined.wav"
            sf.write(str(output_path), combined_audio, sr)
            
            self.logger.info(f"✅ 音频合成完成: {output_path.name}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"❌ 音频合成失败: {e}")
            return None
    
    def process_videos(self):
        """批量处理视频文件"""
        self.logger.info("🚀 启动视频BGM替换工具 v3.0")
        self.logger.info("=" * 80)
        
        video_files = self.get_video_files()
        bgm_files = self.get_bgm_files()
        
        if not video_files:
            self.logger.error("❌ 未找到视频文件")
            self.logger.error(f"   - 搜索目录: {self.video_dir}")
            self.logger.error(f"   - 支持格式: {', '.join(self.video_extensions)}")
            return
        
        if not bgm_files:
            self.logger.error("❌ 未找到BGM文件")
            self.logger.error(f"   - 搜索目录: {self.bgm_dir}")
            self.logger.error(f"   - 支持格式: {', '.join(self.audio_extensions)}")
            return
        
        self.stats['total_videos'] = len(video_files)
        
        # 显示配置信息
        self.logger.info("📋 处理配置:")
        self.logger.info(f"   - 视频文件数量: {len(video_files)}")
        self.logger.info(f"   - BGM文件数量: {len(bgm_files)}")
        self.logger.info(f"   - 分离策略: {self.separation_config.strategy.value}")
        self.logger.info(f"   - 使用模型: {self.separation_config.model_name}")
        self.logger.info(f"   - 模型重叠率: {self.separation_config.overlap}")
        self.logger.info(f"   - 计算设备: {self.device}")
        self.logger.info(f"   - 并发线程数: {self.max_workers}")
        self.logger.info(f"   - 质量阈值: {self.separation_config.quality_threshold}")
        self.logger.info(f"   - 预处理: {'启用' if self.separation_config.enable_preprocessing else '禁用'}")
        self.logger.info(f"   - 质量检查: {'启用' if self.separation_config.enable_quality_check else '禁用'}")
        
        # 显示目录信息
        self.logger.info("📁 目录信息:")
        self.logger.info(f"   - 视频目录: {self.video_dir}")
        self.logger.info(f"   - BGM目录: {self.bgm_dir}")
        self.logger.info(f"   - 临时目录: {self.tmp_dir}")
        self.logger.info(f"   - 输出目录: {self.output_dir}")
        
        # 显示文件列表
        self.logger.info("📄 视频文件列表:")
        for i, video_file in enumerate(video_files, 1):
            file_size = video_file.stat().st_size / (1024 * 1024)  # MB
            self.logger.info(f"   {i:2d}. {video_file.name} ({file_size:.1f} MB)")
        
        self.logger.info("🎵 BGM文件列表:")
        for i, bgm_file in enumerate(bgm_files, 1):
            file_size = bgm_file.stat().st_size / (1024 * 1024)  # MB
            self.logger.info(f"   {i:2d}. {bgm_file.name} ({file_size:.1f} MB)")
        
        self.logger.info("=" * 80)
        self.logger.info("🎬 开始批量处理...")
        
        start_time = time.time()
        
        # 根据配置选择处理模式
        if self.separation_config.use_batch_separation and self.video_separator:
            self.logger.info("🔧 使用批量分离模式")
            success = self.process_videos_with_batch_separation(video_files, bgm_files)
            
            if not success:
                self.logger.error("❌ 批量分离模式处理失败")
                return
        else:
            self.logger.info("🔧 使用传统分离模式")
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                self.logger.info(f"🔧 创建线程池，最大并发数: {self.max_workers}")
                
                # 提交任务
                future_to_video = {
                    executor.submit(self.process_single_video, video_file, bgm_files): video_file
                    for video_file in video_files
                }
                
                self.logger.info(f"📤 已提交 {len(future_to_video)} 个处理任务")
                
                # 处理结果
                completed_count = 0
                for future in as_completed(future_to_video):
                    video_file = future_to_video[future]
                    completed_count += 1
                    
                    try:
                        success = future.result()
                        if success:
                            self.stats['successful'] += 1
                            self.logger.info(f"✅ [{completed_count}/{len(video_files)}] 成功处理: {video_file.name}")
                        else:
                            self.stats['failed'] += 1
                            self.logger.error(f"❌ [{completed_count}/{len(video_files)}] 处理失败: {video_file.name}")
                    except Exception as e:
                        self.stats['failed'] += 1
                        self.logger.error(f"❌ [{completed_count}/{len(video_files)}] 处理异常 {video_file.name}: {e}")
                    
                    # 显示进度
                    progress = (completed_count / len(video_files)) * 100
                    self.logger.info(f"📊 处理进度: {progress:.1f}% ({completed_count}/{len(video_files)})")
        
        # 计算平均质量分数
        if self.stats['successful'] > 0:
            self.stats['avg_quality_score'] /= self.stats['successful']
        
        end_time = time.time()
        duration = end_time - start_time
        
        # 输出详细统计信息
        self.logger.info("=" * 80)
        self.logger.info("📊 处理完成统计报告:")
        self.logger.info("=" * 80)
        self.logger.info(f"📈 基本统计:")
        self.logger.info(f"   - 总视频数: {self.stats['total_videos']}")
        self.logger.info(f"   - 成功处理: {self.stats['successful']}")
        self.logger.info(f"   - 处理失败: {self.stats['failed']}")
        self.logger.info(f"   - 成功率: {self.stats['successful']/self.stats['total_videos']*100:.1f}%")
        
        self.logger.info(f"🎯 质量统计:")
        self.logger.info(f"   - 平均质量分数: {self.stats['avg_quality_score']:.3f}")
        
        self.logger.info(f"⏱️ 性能统计:")
        self.logger.info(f"   - 总耗时: {duration:.1f} 秒")
        self.logger.info(f"   - 平均每个视频: {duration/self.stats['total_videos']:.1f} 秒")
        if self.stats['successful'] > 0:
            self.logger.info(f"   - 成功视频平均耗时: {duration/self.stats['successful']:.1f} 秒")
        
        self.logger.info(f"📁 输出信息:")
        self.logger.info(f"   - 输出目录: {self.output_dir}")
        
        # 计算输出文件总大小
        total_output_size = 0
        output_files = list(self.output_dir.glob("*.mp4"))
        for output_file in output_files:
            total_output_size += output_file.stat().st_size
        
        if total_output_size > 0:
            total_output_size_mb = total_output_size / (1024 * 1024)
            self.logger.info(f"   - 输出文件数: {len(output_files)}")
            self.logger.info(f"   - 总输出大小: {total_output_size_mb:.1f} MB")
            self.logger.info(f"   - 平均文件大小: {total_output_size_mb/len(output_files):.1f} MB")
        
        self.logger.info(f"🖥️ 系统信息:")
        self.logger.info(f"   - 使用设备: {self.device}")
        self.logger.info(f"   - 并发线程数: {self.max_workers}")
        
        if self.device == 'cuda':
            gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            self.logger.info(f"   - GPU内存: {gpu_memory:.1f} GB")
        
        self.logger.info("=" * 80)
        
        # 全局临时文件清理
        self.logger.info("🧹 开始清理临时文件...")
        self.cleanup_temp_files(keep_recent=False)
        
        if self.stats['successful'] == self.stats['total_videos']:
            self.logger.info("🎉 所有视频处理完成！")
        elif self.stats['successful'] > 0:
            self.logger.info(f"⚠️ 部分视频处理完成，{self.stats['failed']} 个视频处理失败")
        else:
            self.logger.error("❌ 所有视频处理失败")
        
        self.logger.info("=" * 80)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='视频BGM分离和替换工具 v3.0')
    parser.add_argument('video_dir', help='视频文件目录')
    parser.add_argument('bgm_dir', help='BGM音频文件目录')
    parser.add_argument('--workers', type=int, default=4, help='并发线程数 (默认: 4)')
    parser.add_argument('--strategy', choices=['vocals_only', 'vocals_and_other', 'custom_mix', 'adaptive'],
                       default='vocals_and_other', help='分离策略 (默认: vocals_and_other - 保留人声和环境音，去除BGM)')
    parser.add_argument('--model', default='htdemucs', help='demucs模型名称 (默认: htdemucs)')
    parser.add_argument('--overlap', type=float, default=0.25, help='模型重叠参数 (默认: 0.25)')
    parser.add_argument('--vocals-volume', type=float, default=2.0, help='人声音量 (默认: 2.0，增强30%%)')
    parser.add_argument('--other-volume', type=float, default=0.15, help='其他音频音量 (默认: 0.15 - 环境音与新BGM平衡)')
    parser.add_argument('--quality-threshold', type=float, default=0.7, help='质量阈值 (默认: 0.7)')
    parser.add_argument('--disable-preprocessing', action='store_true', help='禁用音频预处理')
    parser.add_argument('--disable-quality-check', action='store_true', help='禁用质量检查')
    parser.add_argument('--use-batch-separation', action='store_true', help='使用video_separator的批量分离功能')
    parser.add_argument('--batch-workers', type=int, default=2, help='批量分离的最大并发数 (默认: 2)')
    
    args = parser.parse_args()
    
    # 全局变量用于信号处理
    global_replacer = None
    
    def cleanup_on_exit():
        """程序退出时的清理函数"""
        if global_replacer:
            try:
                print("🧹 程序退出，正在清理临时文件...")
                global_replacer.cleanup_temp_files(keep_recent=False)
            except Exception as e:
                print(f"⚠️ 退出时临时文件清理失败: {e}")
    
    def signal_handler(signum, frame):
        """信号处理器"""
        print(f"\n⚠️ 接收到信号 {signum}，正在清理并退出...")
        cleanup_on_exit()
        sys.exit(1)
    
    # # 注册信号处理器和退出处理器
    # signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    # if hasattr(signal, 'SIGTERM'):
    #     signal.signal(signal.SIGTERM, signal_handler)  # 终止信号
    # atexit.register(cleanup_on_exit)
    
    # 验证目录
    if not os.path.exists(args.video_dir):
        print(f"错误: 视频目录不存在: {args.video_dir}")
        sys.exit(1)
    
    if not os.path.exists(args.bgm_dir):
        print(f"错误: BGM目录不存在: {args.bgm_dir}")
        sys.exit(1)
    
    # 创建分离配置
    separation_config = SeparationConfig(
        strategy=SeparationStrategy(args.strategy),
        model_name=args.model,
        overlap=args.overlap,
        vocals_volume=args.vocals_volume,
        other_volume=args.other_volume,
        quality_threshold=args.quality_threshold,
        enable_preprocessing=not args.disable_preprocessing,
        enable_quality_check=not args.disable_quality_check,
        use_batch_separation=args.use_batch_separation,
        batch_max_workers=args.batch_workers
    )
    
    # 创建处理器并开始处理
    try:
        global_replacer = VideoBGMReplacer(args.video_dir, args.bgm_dir, args.workers, separation_config)
        global_replacer.process_videos()
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断程序执行")
        if global_replacer:
            print("🧹 正在清理临时文件...")
            global_replacer.cleanup_temp_files(keep_recent=False)
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 程序执行异常: {e}")
        if global_replacer:
            print("🧹 正在清理临时文件...")
            global_replacer.cleanup_temp_files(keep_recent=False)
        sys.exit(1)
    finally:
        # 确保在程序结束时清理临时文件
        if global_replacer:
            try:
                global_replacer.cleanup_temp_files(keep_recent=False)
            except Exception as cleanup_error:
                print(f"⚠️ 临时文件清理失败: {cleanup_error}")


if __name__ == "__main__":
    main()