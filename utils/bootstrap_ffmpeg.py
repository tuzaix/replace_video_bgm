"""
统一 FFmpeg 启动策略封装

目标：
- 默认优先使用内置 FFmpeg（vendor/ffmpeg/bin 或打包产物中的捆绑路径）。
- 在开发环境下可通过环境变量 FFMPEG_DEV_FALLBACK=1 允许系统 PATH 中的 FFmpeg 兜底。
- 可选将捆绑目录插入到 PATH 的最前端，保证后续子进程调用一致性。

使用示例：
    from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
    bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)

注意：
- 若 require_ffmpeg/require_ffprobe 为 True，则在无法找到对应可执行文件时会抛出 FileNotFoundError。
- 若提供 override_bundled_dir，用于测试或自定义捆绑目录，将优先把该目录插入 PATH（无需真实存在 ffmpeg 可执行）。
"""

from typing import Optional, Callable, Dict
import os
import shutil

# 复用已有路径解析逻辑
from gui.precheck.ffmpeg_paths import resolve_ffmpeg_paths, allow_system_fallback_env


def _prepend_path(dir_path: str) -> None:
    """将目录插入到 PATH 最前端。

    Args:
        dir_path: 需要插入 PATH 的目录路径
    """
    if not dir_path:
        return
    current = os.environ.get("PATH", "")
    # 避免重复插入
    parts = current.split(os.pathsep) if current else []
    if dir_path not in parts:
        os.environ["PATH"] = dir_path + (os.pathsep + current if current else "")

def bootstrap_ffmpeg_env(
    prefer_bundled: bool = True,
    dev_fallback_env: bool = True,
    modify_env: bool = True,
    logger: Optional[Callable[[str], None]] = None,
    require_ffmpeg: bool = False,
    require_ffprobe: bool = False,
    override_bundled_dir: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """统一的 FFmpeg/FFprobe 环境引导函数。

    功能：
    - 优先解析并使用内置 FFmpeg；在开发环境允许通过环境变量兜底到系统 PATH。
    - 可选将捆绑目录插入 PATH 前端，保证后续 subprocess/第三方库能找到正确的可执行文件。
    - 支持覆盖捆绑目录（override_bundled_dir），便于测试与自定义。

    Args:
        prefer_bundled: 是否优先使用捆绑的 FFmpeg。
        dev_fallback_env: 是否允许通过环境变量 FFMPEG_DEV_FALLBACK=1 在开发环境兜底到系统 FFmpeg。
        modify_env: 是否将解析到的捆绑目录插入到 PATH 前端。
        logger: 可选日志函数，用于输出调试信息。
        require_ffmpeg: 若为 True，则在无法解析到 ffmpeg 时抛出 FileNotFoundError。
        require_ffprobe: 若为 True，则在无法解析到 ffprobe 时抛出 FileNotFoundError。
        override_bundled_dir: 可选覆盖捆绑目录，用于测试或自定义路径注入。

    Returns:
        包含 ffmpeg_path 与 ffprobe_path 的字典结果，用于调用方参考。

    Raises:
        FileNotFoundError: 当 require_ffmpeg/require_ffprobe 为 True 且未找到对应可执行文件时。
    """

    log = logger or (lambda m: None)

    # 覆盖模式：测试或自定义路径注入
    if override_bundled_dir:
        log(f"使用覆盖捆绑目录：{override_bundled_dir}")
        if modify_env:
            _prepend_path(override_bundled_dir)
            log("已将覆盖目录插入 PATH 前端")
        ffmpeg_found = shutil.which("ffmpeg")
        ffprobe_found = shutil.which("ffprobe")
        if require_ffmpeg and not ffmpeg_found:
            raise FileNotFoundError("未找到 ffmpeg，可执行文件不存在或未在 PATH 中")
        if require_ffprobe and not ffprobe_found:
            raise FileNotFoundError("未找到 ffprobe，可执行文件不存在或未在 PATH 中")
        return {"ffmpeg_path": ffmpeg_found, "ffprobe_path": ffprobe_found}

    # 正常解析模式：优先捆绑，开发可兜底
    allow_fallback = allow_system_fallback_env() if dev_fallback_env else False
    res = resolve_ffmpeg_paths(
        prefer_bundled=prefer_bundled,
        allow_system_fallback=allow_fallback,
        modify_env=modify_env,
        logger=lambda m: log(f"[ffmpeg] {m}")
    )

    ffmpeg_found = res.ffmpeg_path or shutil.which("ffmpeg")
    ffprobe_found = res.ffprobe_path or shutil.which("ffprobe")

    if require_ffmpeg and not ffmpeg_found:
        raise FileNotFoundError("未找到 ffmpeg，请准备 vendor/ffmpeg/bin 或设置 FFMPEG_DEV_FALLBACK=1 进行系统兜底")
    if require_ffprobe and not ffprobe_found:
        raise FileNotFoundError("未找到 ffprobe，请准备 vendor/ffmpeg/bin 或设置 FFMPEG_DEV_FALLBACK=1 进行系统兜底")

    return {"ffmpeg_path": ffmpeg_found, "ffprobe_path": ffprobe_found}