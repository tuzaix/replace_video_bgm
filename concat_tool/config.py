"""
Quality preset configuration for FFmpeg encoding.

This module centralizes the mapping between logical quality presets and
their corresponding encoder parameters. Import from here to keep usage
consistent across modules.
"""

from __future__ import annotations

from typing import Tuple

# NVENC CQ values per preset (lower is higher quality/larger size)
QUALITY_NVENC_CQ = {
    "balanced": "27",
    "compact": "29",
    "tiny": "31",
}

# x264 CRF values per preset (lower is higher quality/larger size)
QUALITY_X264_CRF = {
    "balanced": "22",
    "compact": "24",
    "tiny": "26",
}

# AAC audio bitrate per preset
QUALITY_AAC_BITRATE = {
    "balanced": "128k",
    "compact": "96k",
    "tiny": "80k",
}


def resolve_quality(quality: str) -> Tuple[str, str, str]:
    """Resolve a logical quality preset to encoder parameters.

    Parameters
    ----------
    quality : str
        Preset name. Supported values: "balanced", "compact", "tiny".

    Returns
    -------
    Tuple[str, str, str]
        A tuple of (nvenc_cq, x264_crf, aac_bitrate) strings.

    Notes
    -----
    Defaults to the "balanced" preset when an unknown value is provided.
    """
    q = quality if quality in QUALITY_NVENC_CQ else "balanced"
    return (
        QUALITY_NVENC_CQ[q],
        QUALITY_X264_CRF[q],
        QUALITY_AAC_BITRATE[q],
    )


__all__ = [
    "QUALITY_NVENC_CQ",
    "QUALITY_X264_CRF",
    "QUALITY_AAC_BITRATE",
    "resolve_quality",
]