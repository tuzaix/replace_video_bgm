"""
Common settings dataclass for video concatenation workflow.

This module centralizes configuration so it can be shared by GUI, CLI, and
programmatic usage, keeping the business logic independent from any UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Settings:
    """Configuration settings for video concatenation workflow.

    Attributes
    ----------
    video_dirs : List[str]
        List of directory paths containing input videos.
    bgm_path : str
        Path to a BGM file or a directory containing audio files.
    output : Optional[str]
        Output path (file or directory). When multiple input directories are used, this must be a directory.
    count : int
        Number of random videos per output.
    outputs : int
        Number of output videos to generate.
    gpu : bool
        Whether to enable GPU (NVENC) acceleration if available.
    threads : int
        Number of worker threads to use.
    width : int
        Target output width in pixels.
    height : int
        Target output height in pixels.
    fps : int
        Target output frame rate.
    fill : str
        Fill mode: 'pad' or 'crop'.
    trim_head : float
        Seconds to trim from the start of each clip during TS conversion.
    trim_tail : float
        Seconds to trim from the end of each clip during TS conversion.
    clear_mismatched_cache : bool
        If true, clear TS cache files that do not match the current trim settings.
    group_res : bool
        If true, use grouped-by-resolution mode to produce outputs per resolution group.
    quality_profile : str
        Encoding quality profile: 'visual', 'balanced', or 'size'.
    nvenc_cq : Optional[int]
        Override NVENC CQ value.
    x265_crf : Optional[int]
        Override x265 CRF value.
    preset_gpu : Optional[str]
        Override NVENC preset: 'p4', 'p5', 'p6', or 'p7'.
    preset_cpu : Optional[str]
        Override x265 preset: 'ultrafast', 'medium', 'slow', 'slower', or 'veryslow'.
    """

    video_dirs: List[str]
    bgm_path: str
    output: Optional[str]
    count: int = 5
    outputs: int = 1
    gpu: bool = True
    threads: int = 4
    width: int = 1080
    height: int = 1920
    fps: int = 25
    fill: str = "pad"
    trim_head: float = 0.0
    trim_tail: float = 1.0
    clear_mismatched_cache: bool = False
    group_res: bool = True
    quality_profile: str = "balanced"
    nvenc_cq: Optional[int] = None
    x265_crf: Optional[int] = None
    preset_gpu: Optional[str] = None
    preset_cpu: Optional[str] = None