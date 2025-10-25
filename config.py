#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频分离模块配置文件
"""

import os
from pathlib import Path

# FFmpeg配置
FFMPEG_CONFIG = {
    'path': 'ffmpeg',  # FFmpeg可执行文件路径
    'timeout': 3600,   # 命令执行超时时间（秒）
}

# 视频处理配置
VIDEO_CONFIG = {
    'preserve_quality': True,  # 提取无声视频时是否保持原始质量
    'video_codec': 'libx264',  # 重新编码时使用的视频编码器
    'crf': 23,                 # 视频质量参数（0-51，越小质量越好）
    'preset': 'medium',        # 编码预设（ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow）
}

# 音频处理配置
AUDIO_CONFIG = {
    'sample_rate': 44100,      # 音频采样率
    'channels': 2,             # 音频声道数
    'audio_codec': 'pcm_s16le', # 音频编码器
    'audio_format': 'wav',     # 输出音频格式
}

# Spleeter配置
SPLEETER_CONFIG = {
    'model': '2stems',         # 默认模型（2stems, 4stems, 5stems）
    'sample_rate': 44100,      # 音频采样率
    'models': {
        '2stems': 'spleeter:2stems-16kHz',    # 人声 + 伴奏
        '4stems': 'spleeter:4stems-16kHz',    # 人声 + 鼓 + 贝斯 + 其他
        '5stems': 'spleeter:5stems-16kHz',    # 人声 + 鼓 + 贝斯 + 钢琴 + 其他
    },
    'output_format': 'wav',    # 输出音频格式
}

# 并发处理配置
CONCURRENCY_CONFIG = {
    'max_workers': 2,          # 默认最大并发数
    'chunk_size': 10,          # 批处理块大小
}

# 输出配置
OUTPUT_CONFIG = {
    'create_subdirs': True,    # 是否为每个输入文件创建子目录
    'preserve_structure': True, # 是否保持目录结构
    'overwrite_existing': False, # 是否覆盖已存在的文件
    'file_naming': {
        'silent_video_suffix': '_silent',
        'vocals_suffix': '_vocals',
        'accompaniment_suffix': '_accompaniment',
        'drums_suffix': '_drums',
        'bass_suffix': '_bass',
        'piano_suffix': '_piano',
        'other_suffix': '_other',
    }
}

# 临时文件配置
TEMP_CONFIG = {
    'temp_dir': None,          # 临时目录，None表示使用系统默认
    'cleanup_temp': True,      # 是否自动清理临时文件
    'temp_prefix': 'video_sep_', # 临时文件前缀
}

# 日志配置
LOG_CONFIG = {
    'level': 'INFO',           # 日志级别
    'format': '%(asctime)s - %(levelname)s - %(message)s',
    'file': 'video_separator.log',
    'encoding': 'utf-8',
    'max_size': 10 * 1024 * 1024,  # 10MB
    'backup_count': 5,
}

# 支持的文件格式
SUPPORTED_FORMATS = {
    'video': {
        '.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv',
        '.webm', '.m4v', '.3gp', '.ts', '.mts', '.m2ts'
    },
    'audio': {
        '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a'
    }
}

# 质量预设
QUALITY_PRESETS = {
    'high': {
        'video_codec': 'libx264',
        'crf': 18,
        'preset': 'slow',
        'preserve_quality': True
    },
    'medium': {
        'video_codec': 'libx264',
        'crf': 23,
        'preset': 'medium',
        'preserve_quality': False
    },
    'low': {
        'video_codec': 'libx264',
        'crf': 28,
        'preset': 'fast',
        'preserve_quality': False
    },
    'ultrafast': {
        'video_codec': 'libx264',
        'crf': 23,
        'preset': 'ultrafast',
        'preserve_quality': False
    }
}

def get_config():
    """
    获取完整配置字典
    
    Returns:
        配置字典
    """
    return {
        'ffmpeg': FFMPEG_CONFIG,
        'video': VIDEO_CONFIG,
        'audio': AUDIO_CONFIG,
        'spleeter': SPLEETER_CONFIG,
        'concurrency': CONCURRENCY_CONFIG,
        'output': OUTPUT_CONFIG,
        'temp': TEMP_CONFIG,
        'log': LOG_CONFIG,
        'supported_formats': SUPPORTED_FORMATS,
        'quality_presets': QUALITY_PRESETS
    }

def get_quality_preset(preset_name: str):
    """
    获取质量预设配置
    
    Args:
        preset_name: 预设名称 ('high', 'medium', 'low', 'ultrafast')
        
    Returns:
        预设配置字典
    """
    return QUALITY_PRESETS.get(preset_name, QUALITY_PRESETS['medium'])

def update_config_from_env():
    """
    从环境变量更新配置
    """
    # FFmpeg路径
    if 'FFMPEG_PATH' in os.environ:
        FFMPEG_CONFIG['path'] = os.environ['FFMPEG_PATH']
    
    # 临时目录
    if 'VIDEO_SEP_TEMP_DIR' in os.environ:
        TEMP_CONFIG['temp_dir'] = os.environ['VIDEO_SEP_TEMP_DIR']
    
    # 最大并发数
    if 'VIDEO_SEP_MAX_WORKERS' in os.environ:
        try:
            CONCURRENCY_CONFIG['max_workers'] = int(os.environ['VIDEO_SEP_MAX_WORKERS'])
        except ValueError:
            pass
    
    # 日志级别
    if 'VIDEO_SEP_LOG_LEVEL' in os.environ:
        LOG_CONFIG['level'] = os.environ['VIDEO_SEP_LOG_LEVEL']

# 自动从环境变量更新配置
update_config_from_env()