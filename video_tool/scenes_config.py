from typing import Dict, Any, Optional


SCENE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "ecommerce": {
        "threshold": 0.55,
        "similarity_threshold": 0.85,
        "hist_sample_offset": 5,
        "min_duration": 0.6,
        "min_segment_sec": 0.5,
        "enable_audio_snap": False,
        "snap_tolerance": 0.2,
        "enable_silence_split": False,
        "window_s": 0.5,
    },
    "game": {
        "threshold": 0.6,
        "similarity_threshold": 0.88,
        "hist_sample_offset": 6,
        "min_duration": 0.5,
        "min_segment_sec": 0.5,
        "enable_audio_snap": False,
        "snap_tolerance": 0.2,
        "enable_silence_split": False,
        "window_s": 0.6,
    },
    "entertainment": {
        "threshold": 0.65,
        "similarity_threshold": 0.88,
        "hist_sample_offset": 6,
        "min_duration": 0.6,
        "min_segment_sec": 0.6,
        "enable_audio_snap": False,
        "snap_tolerance": 0.2,
        "enable_silence_split": False,
        "window_s": 0.7,
    },
    "mv_ad": {
        "threshold": 0.8,
        "similarity_threshold": 0.9,
        "hist_sample_offset": 7,
        "min_duration": 0.6,
        "min_segment_sec": 0.7,
        "enable_audio_snap": False,
        "snap_tolerance": 0.2,
        "enable_silence_split": False,
        "window_s": 0.9,
    },
    "interview": {
        "threshold": 0.5,
        "similarity_threshold": 0.85,
        "hist_sample_offset": 5,
        "min_duration": 0.6,
        "min_segment_sec": 0.6,
        "enable_audio_snap": True,
        "snap_tolerance": 0.25,
        "enable_silence_split": False,
        "window_s": 0.6,
    },
    "tutorial": {
        "threshold": 0.45,
        "similarity_threshold": 0.83,
        "hist_sample_offset": 5,
        "min_duration": 0.7,
        "min_segment_sec": 0.7,
        "enable_audio_snap": False,
        "snap_tolerance": 0.3,
        "enable_silence_split": True,
        "window_s": 0.5,
    },
}


def get_scene_config(profile: Optional[str]) -> Dict[str, Any]:
    """返回场景配置字典，当未匹配时返回空字典。"""
    if not profile:
        return {}
    return SCENE_CONFIGS.get(str(profile)) or {}
