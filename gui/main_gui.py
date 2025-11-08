"""
Video Concat GUI (PySide6)
Windows desktop GUI to orchestrate the workflow in concat_tool/video_concat.py.

Features:
- Map CLI options to GUI controls
- Run tasks on a background thread (QThread) with progress and logs
- Validate inputs and detect ffmpeg/NVENC availability
- Prepare for building a Windows .exe via PyInstaller

Author: Your Team
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

from PySide6 import QtCore, QtWidgets, QtGui

# Ensure imports work both in development and PyInstaller-frozen runtime.
# In frozen mode, bundled packages are available without modifying sys.path.
# In development mode, add project root so `concat_tool` can be imported.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not getattr(sys, "frozen", False):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

from concat_tool import video_concat as vc  # type: ignore


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


class VideoConcatWorker(QtCore.QObject):
    """Background worker to run the video concatenation workflow.

    This worker emits signals to update the GUI without blocking.

    Signals
    -------
    log(str)
        Emitted when there is a new log message.
    phase(str)
        Emitted when the workflow phase changes (e.g., 'scan', 'preconvert').
    progress(int, int)
        Emitted to indicate progress (completed, total) for the current phase.
    finished(int, int)
        Emitted at the end with (success_count, fail_count).
    error(str)
        Emitted when a non-recoverable error occurs.
    """

    log = QtCore.Signal(str)
    phase = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    finished = QtCore.Signal(int, int)
    results = QtCore.Signal(list)
    error = QtCore.Signal(str)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings

    def _emit(self, msg: str) -> None:
        """Emit a log message safely.

        Parameters
        ----------
        msg : str
            The message to emit to the GUI log view.
        """
        self.log.emit(msg)

    def _validate(self) -> Optional[str]:
        """Validate the settings.

        Returns
        -------
        Optional[str]
            Error message if validation fails; otherwise None.
        """
        if not self.settings.video_dirs:
            return "请选择至少一个视频目录"
        dirs = [Path(p) for p in self.settings.video_dirs]
        for d in dirs:
            if not d.exists() or not d.is_dir():
                return f"视频目录不存在或不是目录: {d}"
        bgm = Path(self.settings.bgm_path)
        if not bgm.exists():
            return f"BGM路径不存在: {bgm}"
        if self.settings.threads < 1:
            return "线程数必须大于0"
        if self.settings.width <= 0 or self.settings.height <= 0:
            return "width/height 必须为正整数"
        if self.settings.fps <= 0:
            return "fps 必须为正整数"
        if self.settings.output:
            out_spec = Path(self.settings.output)
            if out_spec.suffix.lower() == ".mp4" and len(dirs) > 1:
                return "多目录输入时请提供输出目录（不支持单文件路径）"
        return None

    @QtCore.Slot()
    def run(self) -> None:
        """Run the workflow on the background thread.

        This method performs:
        1) Global encoding config injection
        2) Validation and environment checks
        3) Scan videos
        4) Optional TS cache cleanup
        5) Preconvert to TS with per-item progress
        6) Execute grouped or random outputs
        7) Emit final results
        """
        try:
            # Redirect prints from vc module to GUI log
            import sys as _sys

            class _StreamRedirect:
                """Redirect sys.stdout/sys.stderr to GUI log.

                Parameters
                ----------
                write_fn : callable
                    Function to call with decoded string chunks.
                """

                def __init__(self, write_fn):
                    self.write_fn = write_fn

                def write(self, s):  # type: ignore[override]
                    try:
                        s = str(s)
                        s = s.replace("\r\n", "\n")
                        for line in s.split("\n"):
                            if line:
                                self.write_fn(line)
                    except Exception:
                        pass

                def flush(self):
                    return

            _orig_out, _orig_err = _sys.stdout, _sys.stderr
            _sys.stdout = _StreamRedirect(self._emit)
            _sys.stderr = _StreamRedirect(self._emit)
            # Inject global encoding config for mapping used by helper functions
            vc.ENCODE_PROFILE = self.settings.quality_profile
            vc.ENCODE_NVENC_CQ = self.settings.nvenc_cq
            vc.ENCODE_X265_CRF = self.settings.x265_crf
            vc.ENCODE_PRESET_GPU = self.settings.preset_gpu
            vc.ENCODE_PRESET_CPU = self.settings.preset_cpu

            # Validate settings
            err = self._validate()
            if err:
                self.error.emit(err)
                return

            # Detect ffmpeg
            import shutil

            ffmpeg_bin = shutil.which("ffmpeg")
            if not ffmpeg_bin:
                self.error.emit("未找到 ffmpeg，请确保已安装并配置到 PATH")
                return

            # Detect NVENC availability
            nvenc_ok = False
            try:
                nvenc_ok = self.settings.gpu and vc.is_nvenc_available()
            except Exception:
                nvenc_ok = False
            if self.settings.gpu and not nvenc_ok:
                self._emit("⚠️ 未检测到 hevc_nvenc，将使用 CPU (libx265) 进行编码")

            # Prepare output defaults
            video_dirs = [Path(p) for p in self.settings.video_dirs]
            if len(video_dirs) == 1:
                default_output_dir = video_dirs[0].parent / f"{video_dirs[0].name}_longvideo"
            else:
                base_parent = video_dirs[0].parent
                default_output_dir = base_parent / f"{video_dirs[0].name}_longvideo_combined"

            output_spec = Path(self.settings.output) if self.settings.output else None

            # Phase: scan videos
            self.phase.emit("scan")
            self._emit("📁 扫描视频目录…")
            all_videos: List[Path] = []
            for d in video_dirs:
                self._emit(f"  - {d}")
                all_videos.extend(vc.find_videos(d))
            if not all_videos:
                self.error.emit("在输入目录中未找到任何支持的视频文件")
                return
            self._emit(f"📹 合计找到 {len(all_videos)} 个视频文件")

            # Optional: clear mismatched TS cache
            if self.settings.clear_mismatched_cache:
                try:
                    removed = vc.clear_mismatched_ts_cache(video_dirs, self.settings.trim_head, self.settings.trim_tail)
                    self._emit(f"🧹 已清理与当前裁剪参数不匹配的 TS 缓存: {removed} 个")
                except Exception as e:
                    self._emit(f"⚠️ 清理缓存失败: {e}")

            # Phase: preconvert TS with per-item progress
            self.phase.emit("preconvert")
            self._emit("🚧 正在预转换视频为 TS 以优化拼接…")
            total = len(all_videos)
            done = 0

            from concurrent.futures import ThreadPoolExecutor, as_completed

            try:
                with ThreadPoolExecutor(max_workers=max(1, self.settings.threads)) as executor:
                    futures = {}
                    for v in all_videos:
                        out_ts = vc.get_ts_output_path_with_trim(v, video_dirs, self.settings.trim_head, self.settings.trim_tail)
                        fut = executor.submit(
                            vc.convert_video_to_ts,
                            v,
                            out_ts,
                            trim_head_seconds=self.settings.trim_head,
                            trim_tail_seconds=self.settings.trim_tail,
                            use_gpu=self.settings.gpu,
                        )
                        futures[fut] = (v, out_ts)
                    for fut in as_completed(futures):
                        v, out_ts = futures[fut]
                        try:
                            ok = fut.result()
                            done += 1
                            self.progress.emit(done, total)
                            if not ok:
                                self._emit(f"❌ TS转换失败: {v.name}")
                        except Exception as e:
                            done += 1
                            self.progress.emit(done, total)
                            self._emit(f"❌ TS转换任务异常: {v.name} -> {e}")
            except KeyboardInterrupt:
                self.error.emit("用户中断，停止 TS 预转换…")
                return

            self._emit(f"📦 TS预转换完成：✅ {done}/{total}（包含失败项统计已在日志中显示）")

            # Create temp dir
            temp_dir = vc.create_temp_dir(video_dirs)

            # Phase: execution (grouped or random)
            self.phase.emit("execute")
            success_outputs: List[str] = []
            fail_count = 0

            if self.settings.group_res:
                # Grouped mode
                self._emit("📐 开启分辨率分组模式：将按分辨率分别拼接输出")
                groups = vc.group_videos_by_resolution(all_videos)
                qualified_groups = {k: v for k, v in groups.items() if len(v) > 20}
                if not qualified_groups:
                    self._emit("❌ 没有分辨率分组达到 >20 个视频，自动回退到随机模式")
                else:
                    alloc = vc.allocate_outputs_by_group_size(qualified_groups, self.settings.outputs)
                    total_tasks = sum(n for _, n in alloc)
                    self._emit("📦 分配结果（组分辨率 -> 输出数量）：")
                    for (w, h), n in alloc:
                        self._emit(f"  - {w}x{h} -> {n}")
                    max_workers = min(self.settings.threads, max(1, total_tasks))

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = {}
                        for (key, count_out) in alloc:
                            vids = qualified_groups[key]
                            for i in range(1, count_out + 1):
                                task_args = (
                                    key,
                                    vids,
                                    i,
                                    Path(self.settings.bgm_path),
                                    temp_dir,
                                    output_spec,
                                    default_output_dir,
                                    self.settings.count,
                                    self.settings.gpu,
                                    self.settings.fps,
                                    self.settings.fill,
                                    self.settings.trim_head,
                                    self.settings.trim_tail,
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
                                    self._emit(f"✅ [组 {key[0]}x{key[1]}] 完成: {msg}")
                                else:
                                    fail_count += 1
                                    self._emit(f"❌ [组 {key[0]}x{key[1]}] 失败: {msg}")
                            except Exception as e:
                                fail_count += 1
                                self._emit(f"❌ [组 {key[0]}x{key[1]}] 异常: {e}")

            if not self.settings.group_res or not success_outputs:
                # Random mode
                max_workers = max(1, min(self.settings.threads, self.settings.outputs))
                self._emit(
                    f"🚀 启用并发处理，使用 {max_workers} 个线程" if max_workers > 1 else "🔄 使用线程池顺序处理（workers=1）"
                )
                tasks = []
                for idx in range(1, self.settings.outputs + 1):
                    task_args = (
                        idx,
                        all_videos,
                        Path(self.settings.bgm_path),
                        temp_dir,
                        output_spec,
                        default_output_dir,
                        self.settings.count,
                        self.settings.gpu,
                        self.settings.outputs,
                        self.settings.width,
                        self.settings.height,
                        self.settings.fps,
                        self.settings.fill,
                        self.settings.trim_head,
                        self.settings.trim_tail,
                        video_dirs,
                    )
                    tasks.append(task_args)

                from concurrent.futures import ThreadPoolExecutor, as_completed

                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_idx = {executor.submit(vc.process_single_output, task): task[0] for task in tasks}
                    for future in as_completed(future_to_idx):
                        idx = future_to_idx[future]
                        try:
                            success, result_idx, message = future.result()
                            if success:
                                success_outputs.append(message)
                                self._emit(f"✅ 任务 {result_idx} 完成")
                            else:
                                fail_count += 1
                                self._emit(f"❌ 任务 {result_idx} 失败: {message}")
                        except Exception as e:
                            fail_count += 1
                            self._emit(f"❌ 任务 {idx} 异常: {e}")

            # Emit finished
            self.finished.emit(len(success_outputs), fail_count)
            # Emit results list for GUI consumption
            try:
                self.results.emit(success_outputs)
            except Exception:
                pass
            if success_outputs:
                self._emit("\n🎉 成功生成的文件:")
                for p in success_outputs:
                    try:
                        size_mb = Path(p).stat().st_size / (1024 * 1024)
                        self._emit(f"  - {p} ({size_mb:.1f} MB)")
                    except Exception:
                        self._emit(f"  - {p}")

        except Exception as e:
            self.error.emit(str(e))
        finally:
            # Restore stdout/stderr
            try:
                import sys as _sys2
                _sys2.stdout = _orig_out
                _sys2.stderr = _orig_err
            except Exception:
                pass


class MainWindow(QtWidgets.QMainWindow):
    """Main application window for Video Concat GUI.

    This class sets up the form, wires the worker thread, and manages logs and progress.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("短视频切片拼接+替换bgm工具(NVIDIA GPU版)")
        # 初始窗口尺寸加大，尽量使左侧参数全部可见
        try:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen:
                r = screen.availableGeometry()
                w = max(1200, int(r.width() * 0.6))
                h = max(820, int(r.height() * 0.6))
                self.resize(w, h)
            else:
                self.resize(1280, 840)
        except Exception:
            self.resize(1280, 840)
        # 设定一个较大的最小尺寸，避免窗口过小导致左侧被压缩
        self.setMinimumSize(1280, 840)

        # Widgets
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        # 顶层采用水平布局用于左/右分布
        root_layout = QtWidgets.QHBoxLayout(central)

        # Video directories (multi-select via list + add/remove)
        self.video_dirs_list = QtWidgets.QListWidget()
        btn_add_dir = QtWidgets.QPushButton("添加目录")
        btn_rm_dir = QtWidgets.QPushButton("移除选中")
        dir_btns = QtWidgets.QHBoxLayout()
        dir_btns.addWidget(btn_add_dir)
        dir_btns.addWidget(btn_rm_dir)
        dir_container = QtWidgets.QVBoxLayout()
        dir_container.addWidget(self.video_dirs_list)
        dir_container.addLayout(dir_btns)
        dir_group = QtWidgets.QGroupBox("视频目录（可多选）")
        dir_group.setLayout(dir_container)

        # BGM path (file or directory)
        self.bgm_path_edit = QtWidgets.QLineEdit()
        self.bgm_path_edit.setPlaceholderText("支持选择音频文件或目录")
        self.bgm_path_edit.setClearButtonEnabled(True)
        self.bgm_path_edit.setToolTip("选择单个音频文件（mp3/wav/aac/flac/m4a/ogg等）或包含多个音频的目录")
        self.bgm_browse_btn = QtWidgets.QToolButton()
        self.bgm_browse_btn.setText("浏览…")
        self.bgm_browse_btn.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        _bgm_menu = QtWidgets.QMenu(self)
        _bgm_act_file = _bgm_menu.addAction("选择音频文件…")
        _bgm_act_dir = _bgm_menu.addAction("选择目录…")
        self.bgm_browse_btn.setMenu(_bgm_menu)
        # 默认点击选择文件，菜单可选择目录
        self.bgm_browse_btn.clicked.connect(self._on_browse_bgm_file)
        _bgm_act_file.triggered.connect(self._on_browse_bgm_file)
        _bgm_act_dir.triggered.connect(self._on_browse_bgm_dir)
        # 文本变化时进行路径有效性校验
        self.bgm_path_edit.textChanged.connect(self._validate_bgm_path)
        bgm_hbox = QtWidgets.QHBoxLayout()
        bgm_hbox.addWidget(self.bgm_path_edit)
        bgm_hbox.addWidget(self.bgm_browse_btn)

        # Output path（默认：第一个视频目录的同级目录名 + "_longvideo"）
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText("默认：第一个视频目录同级的 ‘<目录名>_longvideo’")
        self.output_edit.setClearButtonEnabled(True)
        self.output_browse_btn = QtWidgets.QPushButton("浏览…")
        out_hbox = QtWidgets.QHBoxLayout()
        out_hbox.addWidget(self.output_edit)
        out_hbox.addWidget(self.output_browse_btn)

        # Numeric controls
        self.count_spin = QtWidgets.QSpinBox(); self.count_spin.setRange(1, 9999); self.count_spin.setValue(5)
        self.outputs_spin = QtWidgets.QSpinBox(); self.outputs_spin.setRange(1, 9999); self.outputs_spin.setValue(1)
        self.threads_spin = QtWidgets.QSpinBox(); self.threads_spin.setRange(1, 64); self.threads_spin.setValue(4)
        self.width_spin = QtWidgets.QSpinBox(); self.width_spin.setRange(16, 20000); self.width_spin.setValue(1080)
        self.height_spin = QtWidgets.QSpinBox(); self.height_spin.setRange(16, 20000); self.height_spin.setValue(1920)
        self.fps_spin = QtWidgets.QSpinBox(); self.fps_spin.setRange(1, 240); self.fps_spin.setValue(25)
        self.trim_head_dbl = QtWidgets.QDoubleSpinBox(); self.trim_head_dbl.setRange(0.0, 3600.0); self.trim_head_dbl.setDecimals(2); self.trim_head_dbl.setValue(0.0)
        self.trim_tail_dbl = QtWidgets.QDoubleSpinBox(); self.trim_tail_dbl.setRange(0.0, 3600.0); self.trim_tail_dbl.setDecimals(2); self.trim_tail_dbl.setValue(1.0)
        # 左侧 SpinBox 统一收紧宽度
        self._apply_compact_field_sizes()

        # Checkboxes and combos
        self.gpu_chk = QtWidgets.QCheckBox("启用GPU(NVENC)"); self.gpu_chk.setChecked(True)
        self.clear_cache_chk = QtWidgets.QCheckBox("清理不匹配TS缓存"); self.clear_cache_chk.setChecked(False)
        self.group_res_chk = QtWidgets.QCheckBox("分辨率分组模式"); self.group_res_chk.setChecked(True)
        # 填充模式使用中文展示，内部代码沿用 pad/crop 以匹配后端参数
        self.fill_combo = QtWidgets.QComboBox()
        self._fill_display_to_code = {"居中黑边": "pad", "裁剪满屏": "crop"}
        self._fill_code_to_display = {v: k for k, v in self._fill_display_to_code.items()}
        for _display, _code in self._fill_display_to_code.items():
            self.fill_combo.addItem(_display)
            idx = self.fill_combo.count() - 1
            self.fill_combo.setItemData(idx, _code, QtCore.Qt.UserRole)
        # 默认 pad（居中黑边）
        for i in range(self.fill_combo.count()):
            if self.fill_combo.itemData(i, QtCore.Qt.UserRole) == "pad":
                self.fill_combo.setCurrentIndex(i)
                break
        # 质量档位使用中文显示，内部映射为英文代码，便于后端一致性
        self.profile_combo = QtWidgets.QComboBox()
        self._profile_display_to_code = {"均衡": "balanced", "观感优先": "visual", "压缩优先": "size"}
        self._profile_code_to_display = {v: k for k, v in self._profile_display_to_code.items()}
        for _display, _code in self._profile_display_to_code.items():
            self.profile_combo.addItem(_display)
            idx = self.profile_combo.count() - 1
            # 将内部代码存到 UserRole，供逻辑层读取
            self.profile_combo.setItemData(idx, _code, QtCore.Qt.UserRole)
        # 默认选择“均衡”
        for i in range(self.profile_combo.count()):
            if self.profile_combo.itemData(i, QtCore.Qt.UserRole) == "balanced":
                self.profile_combo.setCurrentIndex(i)
                break
        self.preset_gpu_combo = QtWidgets.QComboBox(); self.preset_gpu_combo.addItems(["", "p4", "p5", "p6", "p7"])  # empty for None
        self.preset_cpu_combo = QtWidgets.QComboBox(); self.preset_cpu_combo.addItems(["", "ultrafast", "medium", "slow", "slower", "veryslow"])  # empty for None
        self.nvenc_cq_spin = QtWidgets.QSpinBox(); self.nvenc_cq_spin.setRange(0, 51); self.nvenc_cq_spin.setSpecialValueText("(默认)"); self.nvenc_cq_spin.setValue(0)
        self.x265_crf_spin = QtWidgets.QSpinBox(); self.x265_crf_spin.setRange(0, 51); self.x265_crf_spin.setSpecialValueText("(默认)"); self.x265_crf_spin.setValue(0)

        # Buttons
        self.start_btn = QtWidgets.QPushButton("开始")
        self.stop_btn = QtWidgets.QPushButton("停止")
        self.stop_btn.setEnabled(False)

        # Progress & log
        self.phase_label = QtWidgets.QLabel("阶段: idle")
        self.progress_bar = QtWidgets.QProgressBar(); self.progress_bar.setMinimum(0); self.progress_bar.setMaximum(100); self.progress_bar.setValue(0)
        self.log_view = QtWidgets.QTextEdit(); self.log_view.setReadOnly(True)

        # Layout composition — 左右分布与参数分区
        # 左侧：参数设置（按类型分组）；右侧：进度、日志、结果与动作按钮

        # 左侧使用滚动容器以便在窗口较小时也能浏览完整参数
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_container = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_container)

        # 1) 输入与路径
        input_group = QtWidgets.QGroupBox("输入与路径")
        input_form = QtWidgets.QFormLayout()
        input_form.addRow(dir_group)
        input_form.addRow("BGM路径", bgm_hbox)
        input_form.addRow("输出路径", out_hbox)
        input_group.setLayout(input_form)
        left_layout.addWidget(input_group)

        # 2) 基本流程参数（双列布局）
        flow_group = QtWidgets.QGroupBox("基本流程参数")
        flow_grid = QtWidgets.QGridLayout()
        flow_grid.setContentsMargins(10, 8, 10, 8)
        flow_grid.setHorizontalSpacing(16)
        flow_grid.setVerticalSpacing(10)

        # 左右双列：标签靠右对齐，控件靠左放置
        lbl_outputs = QtWidgets.QLabel("生成混剪长视频数量(m)")
        lbl_count = QtWidgets.QLabel("混剪视频切片数量(n)")
        lbl_threads = QtWidgets.QLabel("线程数")
        lbl_groupres = QtWidgets.QLabel("分辨率分组模式")
        for _lbl in (lbl_count, lbl_outputs, lbl_threads, lbl_groupres):
            _lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        # 第1行：n 与 m
        flow_grid.addWidget(lbl_count,   0, 0)
        flow_grid.addWidget(self.count_spin,   0, 1)
        flow_grid.addWidget(lbl_outputs, 0, 2)
        flow_grid.addWidget(self.outputs_spin, 0, 3)
        # 第2行：线程数 与 分辨率分组模式
        flow_grid.addWidget(lbl_threads, 1, 0)
        flow_grid.addWidget(self.threads_spin, 1, 1)
        # flow_grid.addWidget(lbl_groupres, 1, 2)
        flow_grid.addWidget(self.group_res_chk, 1, 2)

        # 列伸展：标签列较窄，控件列占据可用空间但受控件最大宽度约束
        flow_grid.setColumnStretch(0, 0)
        flow_grid.setColumnStretch(1, 1)
        flow_grid.setColumnStretch(2, 0)
        flow_grid.setColumnStretch(3, 1)

        flow_group.setLayout(flow_grid)
        left_layout.addWidget(flow_group)

        # 3) 编码参数（双列布局）
        encode_group = QtWidgets.QGroupBox("编码参数")
        encode_grid = QtWidgets.QGridLayout()
        encode_grid.setContentsMargins(10, 8, 10, 8)
        encode_grid.setHorizontalSpacing(16)
        encode_grid.setVerticalSpacing(10)

        # 标签（右对齐）
        lbl_res = QtWidgets.QLabel("分辨率 (宽/高)")
        lbl_fps = QtWidgets.QLabel("帧率(fps)")
        lbl_fill = QtWidgets.QLabel("填充模式")
        lbl_profile = QtWidgets.QLabel("质量档位")
        lbl_nvenc = QtWidgets.QLabel("NVENC CQ")
        lbl_x265 = QtWidgets.QLabel("X265 CRF")
        lbl_preset_gpu = QtWidgets.QLabel("GPU预设")
        lbl_preset_cpu = QtWidgets.QLabel("CPU预设")
        for _lbl in (lbl_res, lbl_fps, lbl_fill, lbl_profile, lbl_nvenc, lbl_x265, lbl_preset_gpu, lbl_preset_cpu):
            _lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        # 第1行：分辨率 与 帧率
        encode_grid.addWidget(lbl_res, 1, 0)
        encode_grid.addWidget(self._h(self.width_spin, self.height_spin), 1, 1)
        
        encode_grid.addWidget(lbl_fps, 1, 2)
        encode_grid.addWidget(self.fps_spin, 1, 3)

        # 第2行：仅填充模式（质量档位移至“编码预设”分组）
        encode_grid.addWidget(lbl_fill, 2, 0)
        encode_grid.addWidget(self.fill_combo, 2, 1)

        # 顶部：启用GPU(NVENC) 跨整行显示
        encode_grid.addWidget(self.gpu_chk, 2, 3)

        # 预设项单独成组：编码预设（位于编码参数之上）
        preset_group = QtWidgets.QGroupBox("编码预设(推荐使用<均衡>档位即可)")
        # 标题使用红色以醒目提示“使用默认即可”，仅影响标题不影响内容
        try:
            preset_group.setStyleSheet("QGroupBox::title { color: #d32f2f; font-weight: 600; }")
        except Exception:
            pass
        preset_grid = QtWidgets.QGridLayout()
        preset_grid.setContentsMargins(10, 8, 10, 8)
        preset_grid.setHorizontalSpacing(16)
        preset_grid.setVerticalSpacing(10)

        for _lbl in (lbl_profile, lbl_nvenc, lbl_x265, lbl_preset_gpu, lbl_preset_cpu):
            _lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        # 第1行：质量档位（占左两列）
        preset_grid.addWidget(lbl_profile, 0, 0)
        preset_grid.addWidget(self.profile_combo, 0, 1)
        # 第2行：NVENC CQ 与 X265 CRF
        preset_grid.addWidget(lbl_nvenc, 1, 0)
        preset_grid.addWidget(self.nvenc_cq_spin, 1, 1)
        preset_grid.addWidget(lbl_x265, 1, 2)
        preset_grid.addWidget(self.x265_crf_spin, 1, 3)
        # 第3行：GPU预设 与 CPU预设
        preset_grid.addWidget(lbl_preset_gpu, 2, 0)
        preset_grid.addWidget(self.preset_gpu_combo, 2, 1)
        preset_grid.addWidget(lbl_preset_cpu, 2, 2)
        preset_grid.addWidget(self.preset_cpu_combo, 2, 3)

        # 列伸展：标签列较窄，控件列伸展但受控件大小约束
        preset_grid.setColumnStretch(0, 0)
        preset_grid.setColumnStretch(1, 1)
        preset_grid.setColumnStretch(2, 0)
        preset_grid.setColumnStretch(3, 1)

        preset_group.setLayout(preset_grid)

        # 列伸展：标签列较窄，控件列伸展但受控件大小约束
        encode_grid.setColumnStretch(0, 0)
        encode_grid.setColumnStretch(1, 1)
        encode_grid.setColumnStretch(2, 0)
        encode_grid.setColumnStretch(3, 1)

        encode_group.setLayout(encode_grid)
        # 先添加“编码预设”，再添加“编码参数”，使预设位于编码参数上方
        left_layout.addWidget(preset_group)
        left_layout.addWidget(encode_group)

        # 4) 裁剪与缓存
        trim_group = QtWidgets.QGroupBox("裁剪与缓存(**使用默认即可**)")
        # 标题使用红色以醒目提示“使用默认即可”，仅影响标题不影响内容
        try:
            trim_group.setStyleSheet("QGroupBox::title { color: #d32f2f; font-weight: 600; }")
        except Exception:
            pass
        trim_form = QtWidgets.QFormLayout()
        trim_form.addRow("TS裁剪(头/尾, 秒)", self._h(self.trim_head_dbl, self.trim_tail_dbl))
        trim_form.addRow("", self.clear_cache_chk)
        trim_group.setLayout(trim_form)
        left_layout.addWidget(trim_group)

        # 5) 环境状态与概览
        status_group = QtWidgets.QGroupBox("环境状态")
        status_vbox = QtWidgets.QVBoxLayout()
        status_box = QtWidgets.QHBoxLayout()
        self.ffmpeg_status = QtWidgets.QLabel("ffmpeg: 未检测")
        self.nvenc_status = QtWidgets.QLabel("NVENC: 未检测")
        status_box.addWidget(self.ffmpeg_status)
        status_box.addWidget(self.nvenc_status)
        self.ffmpeg_info_btn = QtWidgets.QPushButton("显示 FFmpeg 版本信息")
        self.use_bundled_ffmpeg_chk = QtWidgets.QCheckBox("优先使用内置 FFmpeg")
        self.use_bundled_ffmpeg_chk.setToolTip("勾选后优先使用打包的 ffmpeg/ffprobe（ffmpeg\\bin），未勾选时优先使用系统 PATH 中的 ffmpeg")
        status_box.addWidget(self.ffmpeg_info_btn)
        status_box.addWidget(self.use_bundled_ffmpeg_chk)
        status_vbox.addLayout(status_box)
        # 概览标签放在状态组下方，便于集中查看有效编码参数
        self.enc_summary_label = QtWidgets.QLabel("编码参数概览：")
        status_vbox.addWidget(self.enc_summary_label)
        status_group.setLayout(status_vbox)
        left_layout.addWidget(status_group)

        left_layout.addStretch(1)
        # 优化左侧滚动区域的尺寸策略，避免被右侧压缩到过窄
        left_scroll.setWidget(left_container)
        left_scroll.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        # 恢复正常字体与样式，避免上下压缩造成可读性下降
        left_container.setFont(QtWidgets.QApplication.font())
        left_container.setStyleSheet("")
        # 恢复更舒适的间距与边距
        try:
            left_layout.setSpacing(10)
            left_layout.setContentsMargins(12, 12, 12, 12)
        except Exception:
            pass
        # 保持较大的最小宽度以避免出现水平滚动条
        left_scroll.setMinimumWidth(600)
        left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        # 右侧运行区：阶段、进度、日志、动作按钮、结果
        right_container = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_container)
        right_layout.addWidget(self.phase_label)
        right_layout.addWidget(self.progress_bar)
        right_layout.addWidget(self.log_view)

        # Toolbar-like action buttons
        btn_box = QtWidgets.QHBoxLayout()
        # self.export_cfg_btn = QtWidgets.QPushButton("导出配置")
        # self.import_cfg_btn = QtWidgets.QPushButton("导入配置")
        # self.export_log_btn = QtWidgets.QPushButton("导出日志")
        self.copy_cfg_btn = QtWidgets.QPushButton("复制配置到剪贴板")
        self.open_out_dir_btn = QtWidgets.QPushButton("打开默认输出目录")
        btn_box.addWidget(self.start_btn)
        btn_box.addWidget(self.stop_btn)
        # btn_box.addWidget(self.export_cfg_btn)
        # btn_box.addWidget(self.import_cfg_btn)
        # btn_box.addWidget(self.export_log_btn)
        btn_box.addWidget(self.copy_cfg_btn)
        btn_box.addWidget(self.open_out_dir_btn)
        right_layout.addLayout(btn_box)

        # Results list group（放在右侧，执行后结果更直观）
        self.results_list = QtWidgets.QListWidget()
        self.results_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        results_group = QtWidgets.QGroupBox("输出结果（双击打开文件）")
        _rg_layout = QtWidgets.QVBoxLayout(results_group)
        _rg_layout.addWidget(self.results_list)
        _rg_btns = QtWidgets.QHBoxLayout()
        self.open_selected_btn = QtWidgets.QPushButton("打开选中输出")
        self.open_selected_dir_btn = QtWidgets.QPushButton("打开选中所在目录")
        _rg_btns.addWidget(self.open_selected_btn)
        _rg_btns.addWidget(self.open_selected_dir_btn)
        _rg_layout.addLayout(_rg_btns)
        right_layout.addWidget(results_group)
        # 右侧扩大显示日志和结果
        right_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        # 使用 QSplitter 提供可拖拽的左右分栏，并设置初始宽度比例
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_scroll)
        splitter.addWidget(right_container)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        try:
            # 设置更大的初始分栏尺寸，让左侧内容尽可能完整展示
            splitter.setSizes([400, 700])
        except Exception:
            pass
        # 在窗口显示后再根据实际宽度微调一次，增强自适应（异步执行避免初始宽度未就绪）
        try:
            QtCore.QTimer.singleShot(0, lambda: splitter.setSizes([int(self.width() * 0.30), int(self.width() * 0.70)]))
        except Exception:
            pass
        root_layout.addWidget(splitter)

        # Signals
        btn_add_dir.clicked.connect(self._on_add_dir)
        btn_rm_dir.clicked.connect(self._on_rm_dir)
        # 用户手动编辑输出路径后，停止自动填充默认值
        self._output_autofill = True
        self.output_edit.textEdited.connect(self._on_output_text_edited)
        # 默认按钮行为为选择音频文件，目录选择通过下拉菜单触发
        # 注意：上方已连接 clicked 到 _on_browse_bgm_file，此处无需重复连接到旧方法
        self.output_browse_btn.clicked.connect(self._on_browse_output)
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)
        self.export_cfg_btn.clicked.connect(self._on_export_config)
        self.import_cfg_btn.clicked.connect(self._on_import_config)
        self.export_log_btn.clicked.connect(self._on_export_log)
        self.copy_cfg_btn.clicked.connect(self._on_copy_config)
        self.open_out_dir_btn.clicked.connect(self._on_open_default_output_dir)
        self.open_selected_btn.clicked.connect(self._on_open_selected_files)
        self.open_selected_dir_btn.clicked.connect(self._on_open_selected_dirs)
        self.results_list.itemDoubleClicked.connect(self._on_results_item_double_clicked)
        self.ffmpeg_info_btn.clicked.connect(self._on_show_ffmpeg_info)
        self.use_bundled_ffmpeg_chk.toggled.connect(self._on_toggle_ffmpeg_priority)

        # Auto-update encoding summary on relevant control changes
        for w in [
            self.profile_combo,
            self.nvenc_cq_spin,
            self.x265_crf_spin,
            self.preset_gpu_combo,
            self.preset_cpu_combo,
        ]:
            try:
                if hasattr(w, "currentIndexChanged"):
                    w.currentIndexChanged.connect(self._update_enc_summary)
                if hasattr(w, "valueChanged"):
                    w.valueChanged.connect(self._update_enc_summary)
            except Exception:
                pass

        # 当质量档位变化时，动态应用推荐的编码参数到 NVENC CQ / x265 CRF / GPU/CPU 预设
        try:
            # 使用文本变化信号即可，内部将通过映射读取代码
            self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        except Exception:
            pass

        # Thread members
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[VideoConcatWorker] = None

        # Detect environment
        self._detect_env()
        # 启动加载时，根据当前质量档位初始化推荐的编码参数
        try:
            self._on_profile_changed(self.profile_combo.currentText())
        except Exception:
            pass
        self._update_enc_summary()

    def _h(self, *widgets: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """Create a horizontal layout wrapper for multiple widgets.

        Parameters
        ----------
        widgets : QtWidgets.QWidget
            Child widgets to be arranged horizontally.

        Returns
        -------
        QtWidgets.QWidget
            A container widget with HBox layout containing the specified widgets.
        """
        w = QtWidgets.QWidget()
        hb = QtWidgets.QHBoxLayout(w)
        hb.setContentsMargins(0, 0, 0, 0)
        for x in widgets:
            hb.addWidget(x)
        return w

    def _append_log(self, text: str) -> None:
        """Append text to the log view and auto-scroll.

        Parameters
        ----------
        text : str
            Log message to append.
        """
        self.log_view.append(text)
        self.log_view.moveCursor(QtGui.QTextCursor.End)

    def _on_export_log(self) -> None:
        """Export current log to a UTF-8 text file."""
        path, ok = QtWidgets.QFileDialog.getSaveFileName(self, "保存日志", "log.txt", "Text Files (*.txt)")
        if ok and path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.log_view.toPlainText())
                QtWidgets.QMessageBox.information(self, "成功", f"已保存: {path}")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "错误", f"保存失败: {e}")

    def _on_add_dir(self) -> None:
        """Open a directory selection dialog and add to the list."""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择视频目录")
        if d:
            self.video_dirs_list.addItem(d)
            # 添加目录后更新输出路径默认值
            self._update_output_default()

    def _on_rm_dir(self) -> None:
        """Remove selected directory entries from the list."""
        for item in self.video_dirs_list.selectedItems():
            self.video_dirs_list.takeItem(self.video_dirs_list.row(item))
        # 删除目录后也更新输出路径默认值
        self._update_output_default()

    def _on_browse_bgm_file(self) -> None:
        """选择单个 BGM 音频文件并填充到输入框。

        过滤常见音频格式（mp3/wav/aac/flac/m4a/ogg 等）。
        若当前输入框已有路径，则以其目录作为起始目录。
        """
        from os import path
        start_dir = path.dirname(self.bgm_path_edit.text().strip()) if path.exists(self.bgm_path_edit.text().strip()) else str(Path.home())
        filters = (
            "音频文件 (*.mp3 *.wav *.aac *.flac *.m4a *.ogg *.wma *.alac *.aiff *.ape);;所有文件 (*)"
        )
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择BGM音频文件", start_dir, filters)
        if file_path:
            self.bgm_path_edit.setText(file_path)

    def _on_browse_bgm_dir(self) -> None:
        """选择包含 BGM 音频的目录并填充到输入框。"""
        from os import path
        start_dir = self.bgm_path_edit.text().strip()
        if not path.isdir(start_dir):
            start_dir = str(Path.home())
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择BGM目录", start_dir)
        if dir_path:
            self.bgm_path_edit.setText(dir_path)

    def _validate_bgm_path(self, p: str) -> None:
        """校验 BGM 路径（文件或目录）。

        根据有效性为输入框添加绿色/红色边框提示：
        - 绿色：路径存在且为文件或目录
        - 红色：无效路径
        空字符串时恢复默认样式。
        """
        import os
        if not p:
            self.bgm_path_edit.setStyleSheet("")
            return
        valid = os.path.isfile(p) or os.path.isdir(p)
        if valid:
            self.bgm_path_edit.setStyleSheet("QLineEdit{border:1px solid #4CAF50}")
        else:
            self.bgm_path_edit.setStyleSheet("QLineEdit{border:1px solid #F44336}")

    def _on_browse_output(self) -> None:
        """Choose an output file or directory."""
        dlg = QtWidgets.QFileDialog(self)
        dlg.setFileMode(QtWidgets.QFileDialog.AnyFile)
        if dlg.exec():
            files = dlg.selectedFiles()
            if files:
                self.output_edit.setText(files[0])

    def _apply_compact_field_sizes(self) -> None:
        """统一将左侧的数值输入控件(QSpinBox/QDoubleSpinBox)宽度缩小为更紧凑的尺寸。

        目的：减少水平占用，让标签和值排版更紧凑，避免左侧布局过宽。

        注意：使用 Fixed 宽度策略以避免在表单布局中被拉伸；宽度按类型适配：
        - QSpinBox：最大宽度 90 像素
        - QDoubleSpinBox：最大宽度 100 像素（保留小数显示空间）
        """
        try:
            spinboxes = [
                self.count_spin,
                self.outputs_spin,
                self.threads_spin,
                self.width_spin,
                self.height_spin,
                self.fps_spin,
                self.nvenc_cq_spin,
                self.x265_crf_spin,
            ]
            for sb in spinboxes:
                try:
                    sb.setMaximumWidth(80)
                    sp = sb.sizePolicy()
                    sp.setHorizontalPolicy(QtWidgets.QSizePolicy.Fixed)
                    sb.setSizePolicy(sp)
                except Exception:
                    pass
            dbl_spinboxes = [self.trim_head_dbl, self.trim_tail_dbl]
            for dsb in dbl_spinboxes:
                try:
                    dsb.setMaximumWidth(100)
                    sp = dsb.sizePolicy()
                    sp.setHorizontalPolicy(QtWidgets.QSizePolicy.Fixed)
                    dsb.setSizePolicy(sp)
                except Exception:
                    pass
        except Exception:
            pass

    def _on_output_text_edited(self, _text: str) -> None:
        """当用户编辑输出路径时，关闭自动填充默认值。"""
        self._output_autofill = False

    def _update_output_default(self) -> None:
        """根据第一个视频目录自动生成输出路径默认值并填充到输入框。

        规则：
        - 若列表中存在至少一个目录，默认值为：第一个目录的同级目录下的 “<目录名>_longvideo”。
          例如：C:/videos/input1 -> C:/videos/input1_longvideo
        - 仅在输出框为空或仍处于自动填充模式时更新，避免覆盖用户手动输入。
        """
        try:
            # 若用户已经手动编辑过，则不再自动填充
            if not self._output_autofill and self.output_edit.text().strip():
                return
            if self.video_dirs_list.count() == 0:
                return
            first_dir = Path(self.video_dirs_list.item(0).text())
            # 生成默认输出路径：同级目录 + “_longvideo”
            default_out = first_dir.parent / f"{first_dir.name}_longvideo"
            # 仅在当前为空或仍在自动模式下填充
            if self._output_autofill or not self.output_edit.text().strip():
                self.output_edit.setText(str(default_out))
        except Exception:
            # 容错，不影响主流程
            pass

    def _collect_settings(self) -> Settings:
        """Collect current form values into a Settings dataclass.

        Returns
        -------
        Settings
            The collected settings from the GUI form.
        """
        video_dirs = [self.video_dirs_list.item(i).text() for i in range(self.video_dirs_list.count())]
        preset_gpu = self.preset_gpu_combo.currentText() or None
        preset_cpu = self.preset_cpu_combo.currentText() or None
        nvenc_cq = self.nvenc_cq_spin.value() or None
        x265_crf = self.x265_crf_spin.value() or None
        # Interpret 0 as None for overrides
        if nvenc_cq == 0:
            nvenc_cq = None
        if x265_crf == 0:
            x265_crf = None
        return Settings(
            video_dirs=video_dirs,
            bgm_path=self.bgm_path_edit.text().strip(),
            output=self.output_edit.text().strip() or None,
            count=int(self.count_spin.value()),
            outputs=int(self.outputs_spin.value()),
            gpu=bool(self.gpu_chk.isChecked()),
            threads=int(self.threads_spin.value()),
            width=int(self.width_spin.value()),
            height=int(self.height_spin.value()),
            fps=int(self.fps_spin.value()),
            # 存内部代码 pad/crop
            fill=str(self._get_fill_code()),
            trim_head=float(self.trim_head_dbl.value()),
            trim_tail=float(self.trim_tail_dbl.value()),
            clear_mismatched_cache=bool(self.clear_cache_chk.isChecked()),
            group_res=bool(self.group_res_chk.isChecked()),
            # 使用内部代码而非中文展示文本，确保配置与逻辑一致
            quality_profile=str(self._get_profile_code()),
            nvenc_cq=nvenc_cq,
            x265_crf=x265_crf,
            preset_gpu=preset_gpu,
            preset_cpu=preset_cpu,
        )

    def _on_export_config(self) -> None:
        """Export current settings to JSON."""
        import json
        path, ok = QtWidgets.QFileDialog.getSaveFileName(self, "保存配置", "settings.json", "JSON Files (*.json)")
        if ok and path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(vars(self._collect_settings()), f, ensure_ascii=False, indent=2)
                QtWidgets.QMessageBox.information(self, "成功", f"已保存: {path}")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "错误", f"保存失败: {e}")

    def _on_import_config(self) -> None:
        """Import settings from JSON and populate the form."""
        import json
        path, ok = QtWidgets.QFileDialog.getOpenFileName(self, "打开配置", "", "JSON Files (*.json)")
        if ok and path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Populate
                self.video_dirs_list.clear()
                for d in data.get("video_dirs", []):
                    self.video_dirs_list.addItem(str(d))
                self.bgm_path_edit.setText(str(data.get("bgm_path", "")))
                out_val = str(data.get("output", ""))
                self.output_edit.setText(out_val)
                # 若导入的配置中未提供输出路径，则根据当前视频目录自动填充默认值
                if not out_val:
                    self._output_autofill = True
                    self._update_output_default()
                self.count_spin.setValue(int(data.get("count", 5)))
                self.outputs_spin.setValue(int(data.get("outputs", 1)))
                self.gpu_chk.setChecked(bool(data.get("gpu", True)))
                self.threads_spin.setValue(int(data.get("threads", 4)))
                self.width_spin.setValue(int(data.get("width", 1080)))
                self.height_spin.setValue(int(data.get("height", 1920)))
                self.fps_spin.setValue(int(data.get("fps", 25)))
                # 支持导入内部代码或中文展示文本
                self._set_fill_by_code(str(data.get("fill", "pad")))
                self.trim_head_dbl.setValue(float(data.get("trim_head", 0.0)))
                self.trim_tail_dbl.setValue(float(data.get("trim_tail", 1.0)))
                self.clear_cache_chk.setChecked(bool(data.get("clear_mismatched_cache", False)))
                self.group_res_chk.setChecked(bool(data.get("group_res", True)))
                # 支持导入内部代码或中文展示文本
                self._set_profile_by_code(str(data.get("quality_profile", "balanced")))
                # Presets and overrides
                nvenc_cq = data.get("nvenc_cq", None)
                x265_crf = data.get("x265_crf", None)
                self.nvenc_cq_spin.setValue(int(nvenc_cq) if nvenc_cq is not None else 0)
                self.x265_crf_spin.setValue(int(x265_crf) if x265_crf is not None else 0)
                self.preset_gpu_combo.setCurrentText(str(data.get("preset_gpu", "")) or "")
                self.preset_cpu_combo.setCurrentText(str(data.get("preset_cpu", "")) or "")
                self._update_enc_summary()
                QtWidgets.QMessageBox.information(self, "成功", f"已加载: {path}")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "错误", f"加载失败: {e}")

    def _on_copy_config(self) -> None:
        """Copy current settings as JSON to clipboard."""
        import json
        cb = QtWidgets.QApplication.clipboard()
        cb.setText(json.dumps(vars(self._collect_settings()), ensure_ascii=False, indent=2))
        QtWidgets.QMessageBox.information(self, "复制", "已复制当前配置到剪贴板")

    def _detect_env(self) -> None:
        """Detect ffmpeg and NVENC availability and update labels.

        根据“优先使用内置 FFmpeg”开关，选择先查内置还是系统 ffmpeg。
        若发现内置 ffmpeg，会把其 bin 目录加入到 PATH 的前端，保证子进程能找到。
        """
        import shutil, os

        settings = QtCore.QSettings("ReplaceVideoBGM", "VideoConcatGUI")
        prefer_bundled = settings.value("prefer_bundled_ffmpeg", True, type=bool)
        # 使复选框状态与设置一致（避免初次加载不同步）
        if hasattr(self, "use_bundled_ffmpeg_chk"):
            block = self.use_bundled_ffmpeg_chk.blockSignals(True)
            self.use_bundled_ffmpeg_chk.setChecked(bool(prefer_bundled))
            self.use_bundled_ffmpeg_chk.blockSignals(block)

        def _bundled_ffmpeg_dir() -> Optional[Path]:
            base = getattr(sys, "_MEIPASS", None)
            if base:
                cand = Path(base) / "ffmpeg" / "bin"
            else:
                cand = PROJECT_ROOT / "vendor" / "ffmpeg" / "bin"
            return cand if cand.exists() else None

        def _ensure_path_front(dir_path: Path) -> None:
            cur = os.environ.get("PATH", "")
            parts = cur.split(os.pathsep) if cur else []
            d = str(dir_path)
            parts = [p for p in parts if os.path.abspath(p) != os.path.abspath(d)]
            os.environ["PATH"] = d + os.pathsep + os.pathsep.join(parts)

        ffmpeg_bin = None
        src = "不可用"
        bdir = _bundled_ffmpeg_dir()

        if prefer_bundled and bdir:
            _ensure_path_front(bdir)
            ffmpeg_bin = shutil.which("ffmpeg")
            src = "内置" if ffmpeg_bin else src
            if not ffmpeg_bin:
                # fallback to system
                ffmpeg_bin = shutil.which("ffmpeg")
                src = "系统" if ffmpeg_bin else src
        else:
            ffmpeg_bin = shutil.which("ffmpeg")
            if ffmpeg_bin:
                src = "系统"
            elif bdir:
                _ensure_path_front(bdir)
                ffmpeg_bin = shutil.which("ffmpeg")
                src = "内置" if ffmpeg_bin else src

        # Update ffmpeg badge
        if ffmpeg_bin:
            self.ffmpeg_status.setText(f"ffmpeg: 可用 ({src})")
        else:
            self.ffmpeg_status.setText("ffmpeg: 不可用")

        # NVENC badge由后续检测来更新，这里仅在 ffmpeg 不可用时重置
        try:
            ok = vc.is_nvenc_available()
            self.nvenc_status.setText("NVENC: 可用" if ok else "NVENC: 不可用")
        except Exception:
            self.nvenc_status.setText("NVENC: 检测失败")

    def _on_toggle_ffmpeg_priority(self, checked: bool) -> None:
        """Toggle preference for using bundled FFmpeg first.

        保存到 QSettings 并重新进行环境检测，以便立即生效。
        """
        settings = QtCore.QSettings("ReplaceVideoBGM", "VideoConcatGUI")
        settings.setValue("prefer_bundled_ffmpeg", bool(checked))
        try:
            self._detect_env()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "环境检测失败", f"切换 FFmpeg 选择策略时发生错误: {e}")

    def _get_profile_code(self) -> str:
        """Get internal profile code based on current selection.

        Returns
        -------
        str
            One of: 'visual', 'balanced', 'size'. Falls back to 'balanced' if unknown.
        """
        try:
            idx = self.profile_combo.currentIndex()
            code = self.profile_combo.itemData(idx, QtCore.Qt.UserRole)
            if code:
                return str(code)
        except Exception:
            pass
        # 兼容历史：如果存储的是中文展示或英文文本
        t = (self.profile_combo.currentText() or "").strip()
        if hasattr(self, "_profile_display_to_code") and t in self._profile_display_to_code:
            return self._profile_display_to_code[t]
        t_lower = t.lower()
        return t_lower if t_lower in {"visual", "balanced", "size"} else "balanced"

    def _set_profile_by_code(self, code: str) -> None:
        """Set the quality profile by internal code or display name.

        Parameters
        ----------
        code : str
            Internal code ('visual'/'balanced'/'size') or Chinese display name ('观感优先'/'均衡'/'压缩优先').
        """
        target_code = None
        if hasattr(self, "_profile_code_to_display") and code in self._profile_code_to_display:
            target_code = code
        elif hasattr(self, "_profile_display_to_code") and code in self._profile_display_to_code:
            target_code = self._profile_display_to_code[code]
        else:
            target_code = (code or "balanced").lower()
        # 根据 UserRole 查找索引
        try:
            for i in range(self.profile_combo.count()):
                if self.profile_combo.itemData(i, QtCore.Qt.UserRole) == target_code:
                    self.profile_combo.setCurrentIndex(i)
                    return
        except Exception:
            pass
        # 兜底：按文本匹配
        try:
            display = self._profile_code_to_display.get(target_code, target_code)
            self.profile_combo.setCurrentText(display)
        except Exception:
            pass

    def _get_fill_code(self) -> str:
        """Get internal fill code ('pad' or 'crop') based on current selection.

        Returns
        -------
        str
            'pad' or 'crop'. Defaults to 'pad' if unknown.
        """
        try:
            idx = self.fill_combo.currentIndex()
            code = self.fill_combo.itemData(idx, QtCore.Qt.UserRole)
            if code:
                return str(code)
        except Exception:
            pass
        t = (self.fill_combo.currentText() or "").strip()
        if hasattr(self, "_fill_display_to_code") and t in self._fill_display_to_code:
            return self._fill_display_to_code[t]
        t_lower = t.lower()
        return t_lower if t_lower in {"pad", "crop"} else "pad"

    def _set_fill_by_code(self, code: str) -> None:
        """Set the fill mode by internal code or Chinese display.

        Parameters
        ----------
        code : str
            'pad'/'crop' or '居中黑边'/'裁剪满屏'.
        """
        target_code = None
        if code in {"pad", "crop"}:
            target_code = code
        elif hasattr(self, "_fill_display_to_code") and code in self._fill_display_to_code:
            target_code = self._fill_display_to_code[code]
        else:
            target_code = "pad"
        try:
            for i in range(self.fill_combo.count()):
                if self.fill_combo.itemData(i, QtCore.Qt.UserRole) == target_code:
                    self.fill_combo.setCurrentIndex(i)
                    return
        except Exception:
            pass
        try:
            display = getattr(self, "_fill_code_to_display", {}).get(target_code, target_code)
            self.fill_combo.setCurrentText(display)
        except Exception:
            pass

    def _compute_effective_enc_params(self) -> dict:
        """Compute effective encoding parameters from current form settings.

        Returns
        -------
        dict
            Dictionary with keys: nvenc_cq, x265_crf, preset_gpu, preset_cpu, profile.
        """
        profile = self._get_profile_code()
        if profile == "visual":
            d_nvenc_cq, d_preset_gpu = 30, "p5"
            d_x265_crf, d_preset_cpu = 28, "medium"
        elif profile == "size":
            d_nvenc_cq, d_preset_gpu = 34, "p7"
            d_x265_crf, d_preset_cpu = 32, "veryslow"
        else:
            d_nvenc_cq, d_preset_gpu = 32, "p6"
            d_x265_crf, d_preset_cpu = 30, "slow"
        nvenc_cq = self.nvenc_cq_spin.value() or d_nvenc_cq
        x265_crf = self.x265_crf_spin.value() or d_x265_crf
        preset_gpu = self.preset_gpu_combo.currentText() or d_preset_gpu
        preset_cpu = self.preset_cpu_combo.currentText() or d_preset_cpu
        return {
            "profile": profile,
            "nvenc_cq": nvenc_cq,
            "x265_crf": x265_crf,
            "preset_gpu": preset_gpu,
            "preset_cpu": preset_cpu,
        }

    def _update_enc_summary(self) -> None:
        """Update label that summarizes effective encoding parameters."""
        p = self._compute_effective_enc_params()
        display = getattr(self, "_profile_code_to_display", {}).get(p["profile"], p["profile"])  # 中文优先
        self.enc_summary_label.setText(
            f"编码参数概览：质量档位={display} | NVENC cq={p['nvenc_cq']} preset={p['preset_gpu']} | x265 crf={p['x265_crf']} preset={p['preset_cpu']}"
        )

    def _on_profile_changed(self, text: str) -> None:
        """当质量档位变化时，自动设置推荐的编码参数。

        该方法会根据质量档位（visual/balanced/size）更新以下字段：
        - NVENC CQ（SpinBox）
        - x265 CRF（SpinBox）
        - GPU 预设（ComboBox）
        - CPU 预设（ComboBox）

        为避免重复信号触发，会在设置值时临时屏蔽相关控件的信号，最后统一刷新汇总标签。
        """
        # 通过映射取得内部代码，忽略中文展示差异
        profile = self._get_profile_code()
        if profile == "visual":
            d_nvenc_cq, d_preset_gpu = 30, "p5"
            d_x265_crf, d_preset_cpu = 28, "medium"
        elif profile == "size":
            d_nvenc_cq, d_preset_gpu = 34, "p7"
            d_x265_crf, d_preset_cpu = 32, "veryslow"
        else:  # balanced 默认
            d_nvenc_cq, d_preset_gpu = 32, "p6"
            d_x265_crf, d_preset_cpu = 30, "slow"

        widgets_to_block = [
            self.nvenc_cq_spin,
            self.x265_crf_spin,
            self.preset_gpu_combo,
            self.preset_cpu_combo,
        ]
        prev_block_states = []
        for w in widgets_to_block:
            try:
                prev_block_states.append(w.blockSignals(True))
            except Exception:
                prev_block_states.append(False)

        try:
            self.nvenc_cq_spin.setValue(int(d_nvenc_cq))
            self.x265_crf_spin.setValue(int(d_x265_crf))
            self.preset_gpu_combo.setCurrentText(d_preset_gpu)
            self.preset_cpu_combo.setCurrentText(d_preset_cpu)
        finally:
            for w, prev in zip(widgets_to_block, prev_block_states):
                try:
                    w.blockSignals(bool(prev))
                except Exception:
                    pass

        # 统一刷新概览
        try:
            self._update_enc_summary()
        except Exception:
            pass

    def _default_output_dir(self) -> Optional[Path]:
        """Compute default output directory based on selected video dirs."""
        video_dirs = [self.video_dirs_list.item(i).text() for i in range(self.video_dirs_list.count())]
        if not video_dirs:
            return None
        if len(video_dirs) == 1:
            d = Path(video_dirs[0])
            return d.parent / f"{d.name}_longvideo"
        base_parent = Path(video_dirs[0]).parent
        return base_parent / f"{Path(video_dirs[0]).name}_longvideo_combined"

    def _on_open_default_output_dir(self) -> None:
        """Open the default output directory in Explorer."""
        target = self._default_output_dir()
        if not target:
            QtWidgets.QMessageBox.warning(self, "提示", "请先添加视频目录")
            return
        target.mkdir(parents=True, exist_ok=True)
        QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target)))

    def _on_start(self) -> None:
        """Start the background worker with current settings."""
        if self._thread is not None:
            QtWidgets.QMessageBox.warning(self, "提示", "已有任务在运行")
            return
        settings = self._collect_settings()
        self._append_log("▶️ 开始任务\n" + str(asdict(settings)))

        self._thread = QtCore.QThread(self)
        self._worker = VideoConcatWorker(settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._append_log)
        self._worker.phase.connect(lambda p: self.phase_label.setText(f"阶段: {p}"))
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.results.connect(self._on_results_ready)
        self._worker.error.connect(self._on_error)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _on_progress(self, done: int, total: int) -> None:
        """Update progress bar with (done, total).

        Parameters
        ----------
        done : int
            Completed items.
        total : int
            Total items in the current phase.
        """
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(done)

    def _on_finished(self, ok_count: int, fail_count: int) -> None:
        """Handle worker completion.

        Parameters
        ----------
        ok_count : int
            Number of successful outputs.
        fail_count : int
            Number of failed outputs.
        """
        self._append_log(f"\n📊 完成：✅ 成功 {ok_count}，❌ 失败 {fail_count}")
        self._cleanup_thread()

    def _on_results_ready(self, paths: List[str]) -> None:
        """Populate the results list with generated output file paths.

        Parameters
        ----------
        paths : List[str]
            List of output file paths.
        """
        self.results_list.clear()
        for p in paths:
            self.results_list.addItem(p)

    def _on_results_item_double_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        """Open selected output file using system default application."""
        path = Path(item.text())
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, "提示", f"文件不存在: {path}")
            return
        QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def _on_open_selected_files(self) -> None:
        """Open all selected output files."""
        items = self.results_list.selectedItems()
        if not items:
            QtWidgets.QMessageBox.information(self, "提示", "请先选择一个或多个输出文件")
            return
        for it in items:
            p = Path(it.text())
            if p.exists():
                QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p)))
            else:
                self._append_log(f"⚠️ 文件不存在: {p}")

    def _on_open_selected_dirs(self) -> None:
        """Open directories for the selected output files."""
        items = self.results_list.selectedItems()
        if not items:
            QtWidgets.QMessageBox.information(self, "提示", "请先选择一个或多个输出文件")
            return
        opened = set()
        for it in items:
            p = Path(it.text())
            d = p.parent
            if d.exists() and str(d) not in opened:
                QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(d)))
                opened.add(str(d))

    def _on_show_ffmpeg_info(self) -> None:
        """Show FFmpeg/FFprobe version details in a dialog.

        This method resolves the ffmpeg path (system or bundled), runs
        `ffmpeg -version` and `ffprobe -version`, and displays outputs
        with the resolved executable path. Helpful to verify whether the
        app is using the bundled FFmpeg or the system one.
        """
        import shutil
        import subprocess

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            QtWidgets.QMessageBox.critical(self, "错误", "未找到 ffmpeg，可在设置中检查环境或打包内置 FFmpeg")
            return

        # Detect type (bundled vs system)
        ffmpeg_type = "系统"
        try:
            base = getattr(sys, "_MEIPASS", None)
            if base and str(Path(base) / "ffmpeg" / "bin") in ffmpeg_path:
                ffmpeg_type = "内置(PyInstaller)"
            elif str(PROJECT_ROOT / "vendor" / "ffmpeg" / "bin") in ffmpeg_path:
                ffmpeg_type = "内置(vendor)"
        except Exception:
            pass

        # Collect version info
        def run_ver(cmd: list[str]) -> str:
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                out = res.stdout.strip() or res.stderr.strip()
                return out or "<无输出>"
            except Exception as e:
                return f"<执行失败: {e}>"

        ffmpeg_ver = run_ver([ffmpeg_path, "-version"])
        ffprobe_path = shutil.which("ffprobe") or "(未找到 ffprobe)"
        ffprobe_ver = run_ver([ffprobe_path, "-version"]) if "ffprobe" in ffprobe_path else "(未找到 ffprobe)"

        # Build and show dialog
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("FFmpeg 版本信息")
        vbox = QtWidgets.QVBoxLayout(dlg)
        info_label = QtWidgets.QLabel(
            f"类型: {ffmpeg_type}\n路径: {ffmpeg_path}"
        )
        text = QtWidgets.QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            "==== ffmpeg -version ===="
            + "\n" + ffmpeg_ver
            + "\n\n==== ffprobe -version ===="
            + "\n" + ffprobe_ver
        )
        # Extra actions: copy and NVENC check
        actions = QtWidgets.QHBoxLayout()
        copy_btn = QtWidgets.QPushButton("复制到剪贴板")
        nvenc_btn = QtWidgets.QPushButton("检测 NVENC")
        actions.addWidget(copy_btn)
        actions.addWidget(nvenc_btn)

        def do_copy() -> None:
            """Copy version info to clipboard for quick sharing."""
            full_text = (
                f"类型: {ffmpeg_type}\n路径: {ffmpeg_path}\n\n" +
                "==== ffmpeg -version ====" + "\n" + ffmpeg_ver + "\n\n" +
                "==== ffprobe -version ====" + "\n" + ffprobe_ver
            )
            QtWidgets.QApplication.clipboard().setText(full_text)
            QtWidgets.QMessageBox.information(dlg, "已复制", "版本信息已复制到剪贴板")

        def check_nvenc() -> None:
            """Run a quick NVENC availability check using ffmpeg output."""
            encoders = run_ver([ffmpeg_path, "-hide_banner", "-encoders"]) if ffmpeg_path else ""
            hwaccels = run_ver([ffmpeg_path, "-hide_banner", "-hwaccels"]) if ffmpeg_path else ""
            has_h264 = "h264_nvenc" in encoders
            has_hevc = "hevc_nvenc" in encoders
            nvenc_available = has_h264 or has_hevc
            summary = (
                f"NVENC: {'可用' if nvenc_available else '不可用'}\n" +
                f"检测到编码器: {', '.join([x for x in ['h264_nvenc' if has_h264 else '', 'hevc_nvenc' if has_hevc else ''] if x]) or '无'}\n" +
                ("\n可用硬件加速:\n" + hwaccels if hwaccels else "")
            )
            # Append details in the text area
            text.append("\n\n==== NVENC 检测 ====\n" + summary + ("\n\n==== encoders 输出 ====\n" + encoders if encoders else ""))
            QtWidgets.QMessageBox.information(dlg, "NVENC 检测", summary)

        copy_btn.clicked.connect(do_copy)
        nvenc_btn.clicked.connect(check_nvenc)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        vbox.addWidget(info_label)
        vbox.addWidget(text)
        vbox.addLayout(actions)
        vbox.addWidget(btns)
        dlg.resize(720, 480)
        dlg.exec()

    def _on_error(self, msg: str) -> None:
        """Display error and stop the worker.

        Parameters
        ----------
        msg : str
            Error message to show.
        """
        QtWidgets.QMessageBox.critical(self, "错误", msg)
        self._append_log("❌ " + msg)
        self._cleanup_thread()

    def _cleanup_thread(self) -> None:
        """Cleanup thread/worker state and re-enable controls."""
        try:
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(2000)
        except Exception:
            pass
        self._thread = None
        self._worker = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.phase_label.setText("阶段: idle")
        self.progress_bar.setValue(0)

    def _on_stop(self) -> None:
        """Attempt to stop the running worker.

        Note: For simplicity, this demo performs a soft stop by quitting the thread.
        Long-running ffmpeg subprocesses will finish their current item.
        """
        self._cleanup_thread()


def main() -> None:
    """Application entry point.

    Creates the Qt application and displays the main window.
    """
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()