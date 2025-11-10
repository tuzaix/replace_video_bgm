"""
Business workflow for video concatenation.

This module separates non-GUI logic from gui/main_gui.py, providing a simple
API to orchestrate the end-to-end process using concat_tool.video_concat.

Design goals:
- No Qt dependencies; pure Python, testable.
- Clear function-level docs and simple control flow.
- Report progress via user-provided callbacks.

Author: Your Team
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, List, Tuple, Any

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import video_concat as vc

# 统一启动策略：优先使用内置 FFmpeg，并在开发环境允许系统兜底（通过 FFMPEG_DEV_FALLBACK）。
try:
    from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
    # 工作流层面仅确保环境初始化，不强制要求存在，由 CLI/GUI 决定错误处理。
    bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)
except Exception:
    pass


@dataclass
class WorkflowCallbacks:
    """Callbacks used by the workflow to report status back to the caller.

    Attributes
    ----------
    on_log : Callable[[str], None]
        Called for each log message. Optional but recommended.
    on_phase : Callable[[str], None]
        Called when the workflow phase changes (e.g., 'scan', '预处理视频（mp4转换成ts)', '长视频混合拼接').
    on_progress : Callable[[int, int], None]
        Called to report progress; values are (completed, total). We use a fixed
        scale of total=1000 where 0..300 is the TS stage (30%) and 300..1000 the mix stage (70%).
    on_error : Optional[Callable[[str], None]]
        Called when a non-recoverable error occurs. If not provided, the workflow will raise.
    """

    on_log: Callable[[str], None]
    on_phase: Callable[[str], None]
    on_progress: Callable[[int, int], None]
    on_error: Optional[Callable[[str], None]] = None


def _safe_call(fn: Optional[Callable[..., None]], *args: Any) -> None:
    """Call a callback safely, ignoring any exceptions.

    Parameters
    ----------
    fn : Optional[Callable[..., None]]
        The callback to call.
    *args : Any
        Arguments passed to the callback.
    """
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:
        # Avoid callback errors breaking the workflow.
        pass


def _validate_settings(settings: Any) -> Optional[str]:
    """Validate input settings required for the workflow.

    This function expects an object with at least the following attributes:
    - video_dirs: List[str]
    - bgm_path: str
    - output: Optional[str]
    - threads: int
    - width: int
    - height: int
    - fps: int

    Returns
    -------
    Optional[str]
        Error message if validation fails; otherwise None.
    """
    if not getattr(settings, "video_dirs", None):
        return "请选择至少一个视频目录"
    dirs = [Path(p) for p in settings.video_dirs]
    for d in dirs:
        if not d.exists() or not d.is_dir():
            return f"视频目录不存在或不是目录: {d}"
    bgm = Path(settings.bgm_path)
    if not bgm.exists():
        return f"BGM路径不存在: {bgm}"
    if getattr(settings, "threads", 1) < 1:
        return "线程数必须大于0"
    if getattr(settings, "width", 0) <= 0 or getattr(settings, "height", 0) <= 0:
        return "width/height 必须为正整数"
    if getattr(settings, "fps", 0) <= 0:
        return "fps 必须为正整数"
    if settings.output:
        out_spec = Path(settings.output)
        if out_spec.suffix.lower() == ".mp4" and len(dirs) > 1:
            return "多目录输入时请提供输出目录（不支持单文件路径）"
    return None


def run_video_concat_workflow(settings: Any, cb: WorkflowCallbacks) -> Tuple[int, int, List[str]]:
    """Run the complete video concatenation workflow.

    This function performs:
    1) Global encoding config injection
    2) Validation and environment checks
    3) Scan videos
    4) Optional TS cache cleanup
    5) Preconvert to TS with per-item progress
    6) Execute grouped or random outputs

    Parameters
    ----------
    settings : Any
        Configuration object (dataclass or simple object) with attributes used by the workflow.
    cb : WorkflowCallbacks
        Callbacks used to report logs, phases, and progress.

    Returns
    -------
    Tuple[int, int, List[str]]
        (success_count, fail_count, success_outputs)

    Raises
    ------
    RuntimeError
        If a non-recoverable error occurs and `cb.on_error` is not provided or fails.
    """
    # Inject global encoding config for mapping used by helper functions
    vc.ENCODE_PROFILE = getattr(settings, "quality_profile", "balanced")
    vc.ENCODE_NVENC_CQ = getattr(settings, "nvenc_cq", None)
    vc.ENCODE_X265_CRF = getattr(settings, "x265_crf", None)
    vc.ENCODE_PRESET_GPU = getattr(settings, "preset_gpu", None)
    vc.ENCODE_PRESET_CPU = getattr(settings, "preset_cpu", None)

    # Validate settings
    err = _validate_settings(settings)
    if err:
        _safe_call(cb.on_error, err)
        raise RuntimeError(err)

    # Detect ffmpeg
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        msg = "未找到 ffmpeg，请确保已安装并配置到 PATH"
        _safe_call(cb.on_error, msg)
        raise RuntimeError(msg)

    # Detect NVENC availability
    nvenc_ok = False
    try:
        nvenc_ok = bool(getattr(settings, "gpu", False)) and vc.is_nvenc_available()
    except Exception:
        nvenc_ok = False
    if getattr(settings, "gpu", False) and not nvenc_ok:
        _safe_call(cb.on_log, "⚠️ 未检测到 hevc_nvenc，将使用 CPU (libx265) 进行编码")

    # Prepare output defaults
    video_dirs = [Path(p) for p in settings.video_dirs]
    if len(video_dirs) == 1:
        default_output_dir = video_dirs[0].parent / f"{video_dirs[0].name}_longvideo"
    else:
        base_parent = video_dirs[0].parent
        default_output_dir = base_parent / f"{video_dirs[0].name}_longvideo_combined"

    output_spec = Path(settings.output) if settings.output else None

    # Phase: scan videos
    _safe_call(cb.on_phase, "scan")
    _safe_call(cb.on_log, "📁 扫描视频目录…")
    all_videos: List[Path] = []
    for d in video_dirs:
        _safe_call(cb.on_log, f"  - {d}")
        all_videos.extend(vc.find_videos(d))
    if not all_videos:
        msg = "在输入目录中未找到任何支持的视频文件"
        _safe_call(cb.on_error, msg)
        raise RuntimeError(msg)
    _safe_call(cb.on_log, f"📹 合计找到 {len(all_videos)} 个视频文件")

    # Optional: clear mismatched TS cache
    if getattr(settings, "clear_mismatched_cache", False):
        try:
            removed = vc.clear_mismatched_ts_cache(video_dirs, settings.trim_head, settings.trim_tail)
            _safe_call(cb.on_log, f"🧹 已清理与当前裁剪参数不匹配的 TS 缓存: {removed} 个")
        except Exception as e:
            _safe_call(cb.on_log, f"⚠️ 清理缓存失败: {e}")

    # Phase: preconvert TS（占总进度的 30%）
    _safe_call(cb.on_phase, "预处理视频（mp4转换成ts)")
    _safe_call(cb.on_log, "🚧 正在预转换视频为 TS 以优化拼接…")
    total = len(all_videos)
    done = 0
    # 初始化进度条为固定总量 1000
    _safe_call(cb.on_progress, 0, 1000)

    try:
        with ThreadPoolExecutor(max_workers=max(1, settings.threads)) as executor:
            futures = {}
            for v in all_videos:
                out_ts = vc.get_ts_output_path_with_trim(v, video_dirs, settings.trim_head, settings.trim_tail)
                fut = executor.submit(
                    vc.convert_video_to_ts,
                    v,
                    out_ts,
                    trim_head_seconds=settings.trim_head,
                    trim_tail_seconds=settings.trim_tail,
                    use_gpu=settings.gpu,
                )
                futures[fut] = (v, out_ts)
            for fut in as_completed(futures):
                v, out_ts = futures[fut]
                try:
                    ok = fut.result()
                    done += 1
                    ts_progress = int(done * 300 / max(1, total))
                    _safe_call(cb.on_progress, ts_progress, 1000)
                    if not ok:
                        _safe_call(cb.on_log, f"❌ TS转换失败: {v.name}")
                except Exception as e:
                    done += 1
                    ts_progress = int(done * 300 / max(1, total))
                    _safe_call(cb.on_progress, ts_progress, 1000)
                    _safe_call(cb.on_log, f"❌ TS转换任务异常: {v.name} -> {e}")
    except KeyboardInterrupt:
        msg = "用户中断，停止 TS 预转换…"
        _safe_call(cb.on_error, msg)
        raise RuntimeError(msg)

    _safe_call(cb.on_log, f"📦 TS预转换完成：✅ {done}/{total}（包含失败项统计已在日志中显示）")

    # Create temp dir
    temp_dir = vc.create_temp_dir(video_dirs)

    # Phase: execution (grouped or random)
    _safe_call(cb.on_phase, "长视频混合拼接")
    success_outputs: List[str] = []
    fail_count = 0

    if getattr(settings, "group_res", True):
        # Grouped mode
        _safe_call(cb.on_log, "📐 开启分辨率分组模式：将按分辨率分别拼接输出")
        groups = vc.group_videos_by_resolution(all_videos)
        qualified_groups = {k: v for k, v in groups.items() if len(v) > 20}
        if not qualified_groups:
            _safe_call(cb.on_log, "❌ 没有分辨率分组达到 >20 个视频，自动回退到随机模式")
        else:
            alloc = vc.allocate_outputs_by_group_size(qualified_groups, settings.outputs)
            total_tasks = sum(n for _, n in alloc)
            # 进入混合拼接阶段，将进度条切换到第二阶段的区间，并从 30% 开始累计
            _safe_call(cb.on_progress, 300, 1000)
            mix_done = 0
            _safe_call(cb.on_log, "📦 分配结果（组分辨率 -> 输出数量）：")
            for (w, h), n in alloc:
                _safe_call(cb.on_log, f"  - {w}x{h} -> {n}")
            max_workers = min(settings.threads, max(1, total_tasks))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for (key, count_out) in alloc:
                    vids = qualified_groups[key]
                    for i in range(1, count_out + 1):
                        task_args = (
                            key,
                            vids,
                            i,
                            Path(settings.bgm_path),
                            temp_dir,
                            output_spec,
                            default_output_dir,
                            settings.count,
                            settings.gpu,
                            settings.fps,
                            settings.fill,
                            settings.trim_head,
                            settings.trim_tail,
                            video_dirs,
                        )
                        fut = executor.submit(vc.process_group_single_output, task_args)
                        futures[fut] = key
                for fut in as_completed(futures):
                    key = futures[fut]
                    try:
                        ok, msg = fut.result()
                        if ok:
                            success_outputs.append(msg)
                            _safe_call(cb.on_log, f"✅ [组 {key[0]}x{key[1]}] 完成: {msg}")
                        else:
                            fail_count += 1
                            _safe_call(cb.on_log, f"❌ [组 {key[0]}x{key[1]}] 失败: {msg}")
                        mix_done += 1
                        mix_progress = 300 + int(mix_done * 700 / max(1, total_tasks))
                        _safe_call(cb.on_progress, mix_progress, 1000)
                    except Exception as e:
                        fail_count += 1
                        _safe_call(cb.on_log, f"❌ [组 {key[0]}x{key[1]}] 异常: {e}")
                        mix_done += 1
                        mix_progress = 300 + int(mix_done * 700 / max(1, total_tasks))
                        _safe_call(cb.on_progress, mix_progress, 1000)

    if not getattr(settings, "group_res", True) or not success_outputs:
        # Random mode
        max_workers = max(1, min(settings.threads, settings.outputs))
        _safe_call(cb.on_log, (
            f"🚀 启用并发处理，使用 {max_workers} 个线程" if max_workers > 1 else "🔄 使用线程池顺序处理（workers=1）"
        ))
        tasks = []
        total_tasks = settings.outputs
        _safe_call(cb.on_progress, 300, 1000)
        mix_done = 0
        for idx in range(1, settings.outputs + 1):
            task_args = (
                idx,
                all_videos,
                Path(settings.bgm_path),
                temp_dir,
                output_spec,
                default_output_dir,
                settings.count,
                settings.gpu,
                settings.outputs,
                settings.width,
                settings.height,
                settings.fps,
                settings.fill,
                settings.trim_head,
                settings.trim_tail,
                video_dirs,
            )
            tasks.append(task_args)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {executor.submit(vc.process_single_output, task): task[0] for task in tasks}
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    success, result_idx, message = future.result()
                    if success:
                        success_outputs.append(message)
                        _safe_call(cb.on_log, f"✅ 任务 {result_idx} 完成")
                    else:
                        fail_count += 1
                        _safe_call(cb.on_log, f"❌ 任务 {result_idx} 失败: {message}")
                    mix_done += 1
                    mix_progress = 300 + int(mix_done * 700 / max(1, total_tasks))
                    _safe_call(cb.on_progress, mix_progress, 1000)
                except Exception as e:
                    fail_count += 1
                    _safe_call(cb.on_log, f"❌ 任务 {idx} 异常: {e}")
                    mix_done += 1
                    mix_progress = 300 + int(mix_done * 700 / max(1, total_tasks))
                    _safe_call(cb.on_progress, mix_progress, 1000)

    return len(success_outputs), fail_count, success_outputs