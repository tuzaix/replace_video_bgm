"""
Extract Frames Tab

This module implements the "生成截图" tab with a left/right panel layout
similar to the 视频混剪 tab. It provides:

- Left panel:
  a) 视频目录（路径输入 + 浏览按钮）
  b) 每个视频的截图数量（下拉，默认 1）
  c) 截图目录（路径输入 + 浏览按钮，默认：<视频目录>/截图，动态更新）

- Right panel:
  a) 进度条 + 开始/停止按钮（互斥状态）
  b) 结果表（序号，目录，截图数量）

The tab orchestrates extraction by calling cover_tool.extract_frames.scan_and_extract
in a background thread to avoid blocking the GUI.
"""

from __future__ import annotations

from typing import Container, Optional, List, Tuple
import os
import shutil
from PySide6 import QtWidgets, QtCore, QtGui
from gui.utils import theme
from gui.precheck import run_preflight_checks
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


class BusySpinner(QtWidgets.QWidget):
    """轻量级菊花转圈圈控件。

    在父控件上绘制一个旋转的圆形条纹，作为不确定加载指示器。通过 `start()`/`stop()` 控制动画。
    """

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        inner_radius: int = 12,
        line_length: int = 6,
        line_width: int = 3,
        lines: int = 12,
        color: Optional[QtGui.QColor] = None,
        interval_ms: int = 90,
    ) -> None:
        super().__init__(parent)
        self._inner_radius = inner_radius
        self._line_length = line_length
        self._line_width = line_width
        self._lines = max(6, int(lines))
        base_color = color or QtGui.QColor(str(getattr(theme, "PRIMARY_BLUE", "#409eff")))
        self._color = base_color
        self._angle = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(int(interval_ms))
        self._timer.timeout.connect(self._on_tick)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

    def sizeHint(self) -> QtCore.QSize:  # type: ignore[override]
        """返回控件的推荐尺寸。"""
        d = (self._inner_radius + self._line_length + self._line_width) * 2
        return QtCore.QSize(d, d)

    def start(self) -> None:
        """开始旋转动画。"""
        self._timer.start()
        self.show()

    def stop(self) -> None:
        """停止旋转动画。"""
        self._timer.stop()
        self.hide()

    def _on_tick(self) -> None:
        """定时器回调，更新旋转角度并重绘。"""
        self._angle = (self._angle + 360 // self._lines) % 360
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        """绘制菊花条纹，按照当前角度旋转。"""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        center = self.rect().center()
        radius = self._inner_radius
        for i in range(self._lines):
            # 颜色从淡到浓形成转圈效果
            alpha = int(60 + 195 * (i + 1) / self._lines)
            c = QtGui.QColor(self._color)
            c.setAlpha(alpha)
            pen = QtGui.QPen(c, self._line_width, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
            painter.setPen(pen)
            angle = (self._angle + (360 * i) // self._lines) % 360
            painter.save()
            painter.translate(center)
            painter.rotate(angle)
            painter.drawLine(QtCore.QPoint(0, -radius), QtCore.QPoint(0, -(radius + self._line_length)))
            painter.restore()
        painter.end()


class ExtractFramesWorker(QtCore.QObject):
    """Background worker to run frame extraction.

    Methods
    -------
    run(video_dir: str, output_dir: str, count: int, threads: int, filter_count: int) -> None
        Execute extraction and emit signals for progress and results.

    Signals
    -------
    phase(str):
        Human-readable phase description.
    progress(int, int):
        Progress values (done, total). A simple 0..100 scale is used.
    finished(str, int):
        Emitted with (output_dir, image_count) when done.
    error(str):
        Emitted when extraction fails.
    """

    phase = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    finished = QtCore.Signal(str, int)
    error = QtCore.Signal(str)
    # 每次成功保存一张截图时，发射该图片的绝对路径
    image_saved = QtCore.Signal(str)
    # 使用带参数的启动信号，确保在工作线程中调用 run()
    start = QtCore.Signal(str, str, int, int, int)

    def __init__(self) -> None:
        super().__init__()
        self._stopping = False

    def stop(self) -> None:
        """Request a soft stop. The current extraction will finish and exit."""
        self._stopping = True

    @QtCore.Slot(str, str, int, int, int)
    def run(self, video_dir: str, output_dir: str, count: int, threads: int, filter_count: int) -> None:
        """执行抽帧任务（后台线程）。

        Parameters
        ----------
        video_dir : str
            待扫描的视频根目录。
        output_dir : str
            截图输出根目录。
        count : int
            每个视频生成的截图数量。
        threads : int
            并发执行线程数，用于控制同时处理的视频数量。
        """
        try:
            # 按视频并发、动态更新进度
            try:
                from cover_tool.extract_frames import (
                    is_video_file,
                    ensure_dir,
                    probe_video_resolution,
                    probe_video_duration_seconds,
                    compute_sharpest_frame_cv_gpu,
                    compute_sharpest_frame_cv,
                    save_frame_cv,
                    generate_unique_random_name,
                )
            except Exception as e:
                self.error.emit(f"导入抽帧逻辑失败: {e}")
                return

            self.phase.emit("Scanning videos…")

            # 非递归：仅顶层视频文件
            try:
                entries = os.listdir(video_dir)
            except Exception as e:
                self.error.emit(f"无法读取目录: {e}")
                return
            if self._stopping:
                self.error.emit("任务已取消")
                return
            videos = [f for f in entries if is_video_file(f) and os.path.isfile(os.path.join(video_dir, f))]

            # 按照视频分辨率，统计每种分辨率下的视频个数，对少于20个视频的分辨率，则去掉截取的必要
            res_count = {}
            for video in videos:
                wh = probe_video_resolution(os.path.join(video_dir, video))
                res_count[wh] = res_count.get(wh, 0) + 1
            unknown_resolution_videos = [v for v in videos if res_count.get(probe_video_resolution(os.path.join(video_dir, v)), 0) < filter_count]
            videos = [v for v in videos if v not in unknown_resolution_videos]
           

            shots_for_total = max(1, int(count))
            total_tasks = len(videos) * shots_for_total
            # 初始化计数进度：0 / total_tasks
            self.progress.emit(0, total_tasks)

            # 构建输出根目录
            try:
                ensure_dir(output_dir)
            except Exception:
                pass

            # 线程安全计数器
            done_lock = threading.Lock()
            done_count = 0

            def _emit_progress():
                # 直接发射“已完成/总数”计数，以便 UI 准确显示
                with done_lock:
                    current = done_count
                self.progress.emit(current, total_tasks)

            # 单视频处理函数
            def process_one(video_filename: str) -> int:
                nonlocal done_count
                in_path = os.path.join(video_dir, video_filename)
                # 分辨率分组目录：<output_dir>/<WxH>/<rel>
                # wh = probe_video_resolution(in_path)
                # res_dir = os.path.join(output_dir, f"{wh[0]}x{wh[1]}" if wh else "unknown_resolution")
                res_dir = output_dir # 不在物理上区分分辨率，之通过视频文件属性来区分
                ensure_dir(res_dir)
                out_parent_dir = res_dir  # 非递归，直接使用分辨率层

                # 计算时间窗
                try:
                    total_dur = probe_video_duration_seconds(in_path) or 5.0
                except Exception:
                    total_dur = 5.0
                total_dur = max(0.5, float(total_dur))
                shots = max(1, int(count))
                window_edges: List[Tuple[float, float]] = []
                if shots == 1:
                    window_edges = [(0.0, total_dur)]
                else:
                    seg = total_dur / float(shots)
                    start = 0.0
                    for i in range(shots):
                        end = start + seg
                        if i == shots - 1:
                            end = total_dur
                        window_edges.append((start, end))
                        start = end

                saved = 0
                for (win_start, win_end) in window_edges:
                    if self._stopping:
                        break
                    ok_best, best_img, info_msg, best_score, best_num = compute_sharpest_frame_cv_gpu(in_path)
                    if not ok_best:
                        ok_best, best_img, info_msg, best_score, best_num = compute_sharpest_frame_cv(
                            in_path,
                            start_time_sec=win_start,
                            end_time_sec=win_end,
                        )
                if ok_best and best_img is not None:
                    ext = "png"
                    safe_name = generate_unique_random_name(out_parent_dir, ext, length=12)
                    out_path = os.path.join(out_parent_dir, f"{safe_name}.{ext}")
                    ok_save, msg_save = save_frame_cv(best_img, out_path, fmt=ext, quality=2)
                    if ok_save:
                        saved += 1
                        # 通知 UI 展示最新生成的截图预览
                        try:
                            self.image_saved.emit(out_path)
                        except Exception:
                            pass
                    # 无论窗口是否成功保存，都推进进度
                    with done_lock:
                        done_count += 1
                    _emit_progress()
                return saved

            # 并发执行
            self.phase.emit("Extracting frames…")
            max_workers = max(1, int(threads))
            try:
                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futures = [ex.submit(process_one, v) for v in videos]
                    for f in as_completed(futures):
                        if self._stopping:
                            break
                        # 单帧进度已在 worker 中更新
            except Exception as e:
                self.error.emit(f"并发执行失败: {e}")
                return

            # 完成统计
            self.phase.emit("Counting results…")
            total_images = 0
            for dirpath, dirnames, filenames in os.walk(output_dir):
                total_images += sum(1 for f in filenames if os.path.splitext(f)[1].lower() in {".jpg", ".jpeg", ".png"})
            # 结束时发射 total_tasks / total_tasks
            self.progress.emit(total_tasks, total_tasks)
            self.finished.emit(output_dir, total_images)
        except Exception as e:
            self.error.emit(f"生成截图失败: {e}")


class ExtractFramesTab(QtWidgets.QWidget):
    """"生成截图" 标签页。

    提供左/右面板：左侧用于输入视频目录、选择每视频截图数量、指定截图输出目录；
    右侧包含进度条、开始/停止按钮，以及结果表格显示输出目录与截图数量。
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.root_layout = QtWidgets.QHBoxLayout(self)
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[ExtractFramesWorker] = None
        self._is_running: bool = False
        # 预检授权状态开关（参考 generate_cover_tab 的实现），通过一次预检决定是否允许开始
        self._preflight_passed: bool = False
        self._build_page()

    def _build_page(self) -> None:
        """Build the full page: left and right panels joined via a splitter."""
        left_panel = self._build_left_panel()
        right_panel = self._build_right_panel()

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)

        # 固定splitter不能左右拖动
        splitter_handle = splitter.handle(1)
        splitter_handle.setEnabled(False)

        self.root_layout.setContentsMargins(6, 6, 6, 6)
        self.root_layout.addWidget(splitter)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        """构建左侧参数面板，将输入控件统一置于一个分组并自上而下排列。

        包含四项：
        - 视频目录（路径输入 + 浏览按钮）
        - 截图目录（路径输入 + 浏览按钮）
        - 每个视频截图数量（数值框）
        - 并发执行线程数（数值框）
        """
        # 单一分组容器
        group1 = QtWidgets.QGroupBox("输入/输出路径")
        gl1 = QtWidgets.QVBoxLayout(group1)
        gl1.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
        gl1.setContentsMargins(10, 8, 10, 8)
        gl1.setSpacing(10)

        # 1) 视频目录
        lbl_video = QtWidgets.QLabel("视频目录")
        self.video_dir_edit = QtWidgets.QLineEdit()
        self.video_dir_edit.setPlaceholderText("选择视频目录…")
        # 默认值（可按需调整）
        self.video_dir_edit.setText(r"E:\Download\社媒助手\抖音\潮汕菲宝")

        btn_browse_video = QtWidgets.QPushButton("浏览…")
        btn_browse_video.clicked.connect(self._on_browse_video_dir)
        row_video = QtWidgets.QHBoxLayout()
        row_video.addWidget(lbl_video, 0)
        row_video.addWidget(self.video_dir_edit, 1)
        row_video.addWidget(btn_browse_video)
        gl1.addLayout(row_video)

        # 2) 截图目录（默认随视频目录更新）
        lbl_output = QtWidgets.QLabel("截图目录")
        self.output_dir_edit = QtWidgets.QLineEdit()
        
        self.output_dir_edit.setPlaceholderText("默认：<视频目录>/截图")
        # 默认值（可按需调整）
        self.output_dir_edit.setText(os.path.join(self.video_dir_edit.text(), "截图"))

        btn_browse_output = QtWidgets.QPushButton("浏览…")
        btn_browse_output.clicked.connect(self._on_browse_output_dir)
        row_output = QtWidgets.QHBoxLayout()
        row_output.addWidget(lbl_output, 0)
        row_output.addWidget(self.output_dir_edit, 1)
        row_output.addWidget(btn_browse_output)
        gl1.addLayout(row_output)

         # 单一分组容器
        group2 = QtWidgets.QGroupBox("参数设置")
        gl2 = QtWidgets.QVBoxLayout(group2)
        gl2.setContentsMargins(10, 8, 10, 8)
        gl2.setSpacing(10)

        # 3) 每个视频截图数量（数值框）
        lbl_count = QtWidgets.QLabel("每个视频截图数量")
        self.count_spin = QtWidgets.QSpinBox()
        # self.count_spin 支持手动填写
        self.count_spin.setKeyboardTracking(False)

        self.count_spin.setRange(1, 20)
        self.count_spin.setValue(1)
        btn_help_count = QtWidgets.QPushButton("?")
        btn_help_count.setFixedSize(22, 22)
        btn_help_count.setToolTip("设置每个视频需要生成的截图数量，范围 1–20")
        row_1 = QtWidgets.QHBoxLayout()
        row_1.addWidget(lbl_count, 0)
        row_1.addWidget(self.count_spin, 1)
        row_1.addWidget(btn_help_count, 2)

        gl2.addLayout(row_1)


        # 4) 筛选掉相同视频分辨率的视频数少于x（数值框）
        lbl_filter = QtWidgets.QLabel("过滤同分辨率的视频数少于")
        self.filter_spin = QtWidgets.QSpinBox()
        self.filter_spin.setRange(1, 100)
        self.filter_spin.setValue(20)
        btn_help_filter = QtWidgets.QPushButton("?")
        btn_help_filter.setFixedSize(22, 22)
        btn_help_filter.setToolTip("当某种分辨率的视频数量少于设定值时，将跳过这些视频的截图生成")
        row_3 = QtWidgets.QHBoxLayout()
        row_3.addWidget(lbl_filter, 0)
        row_3.addWidget(self.filter_spin, 1)
        row_3.addWidget(btn_help_filter, 2)
        gl2.addLayout(row_3)

        # 4) 并发执行线程数（数值框）
        lbl_threads = QtWidgets.QLabel("并发线程数")
        self.threads_spin = QtWidgets.QSpinBox()
        self.threads_spin.setRange(1, 32)
        self.threads_spin.setValue(2)
        btn_help_threads = QtWidgets.QPushButton("?")
        btn_help_threads.setFixedSize(22, 22)
        btn_help_threads.setToolTip("控制同时处理视频的文件数量，数值越高占用资源越多")

        row_2 = QtWidgets.QHBoxLayout()
        row_2.addWidget(lbl_threads, 0)
        row_2.addWidget(self.threads_spin, 1)
        row_2.addWidget(btn_help_threads, 2)
        gl2.addLayout(row_2)

        # 视频目录变更时同步默认截图目录
        self.video_dir_edit.textChanged.connect(self._sync_default_output_dir)

        # 使用splitter来把上下的空间包起来
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        # 让两个分组不在垂直方向上扩展：保持其 sizeHint 高度
        try:
            group1.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            group2.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        except Exception:
            pass
        splitter.addWidget(group1)
        splitter.addWidget(group2)

        # # 在下方加入一个可扩展的占位控件，吃掉剩余空间，使 group1/2 靠上排列
        # spacer = QtWidgets.QWidget()
        # spacer.setMinimumSize(0, 0)
        # spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        group3 = QtWidgets.QGroupBox("截图预览")
        gl3 = QtWidgets.QVBoxLayout(group3)
        gl3.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
        gl3.setContentsMargins(10, 8, 10, 8)
        gl3.setSpacing(10)

        # spacer换成可以加载图片预览的窗口
        self.preview_label = QtWidgets.QLabel()
        # 预览区域铺满整个 group3，但不超过其范围；随 group3 尺寸自适应。
        try:
            self.preview_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            self.preview_label.setMinimumSize(0, 0)
            self.preview_label.setMaximumSize(800, 350)
            # 监听尺寸变化，以便对当前预览图进行等比平滑缩放
            self.preview_label.installEventFilter(self)
        except Exception:
            pass
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setStyleSheet("border: 1px solid #ccc;")
        # 使用拉伸因子，让预览标签填满 group3 可用空间
        gl3.addWidget(self.preview_label, 1)

        splitter.addWidget(group3)
        # # 设置初始尺寸：前两项按内容高度，剩余空间给占位控件
        # try:
        #     h1 = group1.sizeHint().height()
        #     h2 = group2.sizeHint().height()
        #     splitter.setSizes([max(1, h1), max(1, h2), 1000])
        # except Exception:
        #     splitter.setSizes([100, 100, 1000])
        # 仅让占位控件参与拉伸，两个分组不拉伸
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        # 整体仍可水平扩展以适配父容器
        splitter.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        return splitter

    def _build_right_panel(self) -> QtWidgets.QWidget:
        """Construct the right panel with progress and results table."""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # a) 进度条 + 开始/停止按钮
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        # 在进度条中显示“已完成/总数”
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("0 / 0")
        self.action_btn = QtWidgets.QPushButton("开始")
        self._apply_progressbar_style(chunk_color=theme.PRIMARY_BLUE)
        self._apply_action_button_style(running=False)
        self.action_btn.clicked.connect(self._on_action_clicked)
        row_progress = QtWidgets.QHBoxLayout()
        row_progress.addWidget(self.progress_bar)
        row_progress.addWidget(self.action_btn)

        group_progress = QtWidgets.QGroupBox("运行状态")
        gpl = QtWidgets.QVBoxLayout(group_progress)
        gpl.setContentsMargins(6, 6, 6, 6)
        gpl.addLayout(row_progress)
        layout.addWidget(group_progress)

        # b) 结果表（序号，目录，截图数量）
        self.results_table = QtWidgets.QTableWidget(0, 2)
        # 表头：序号、文件绝对路径、分辨率
        self.results_table.setHorizontalHeaderLabels(["文件路径", "分辨率"])
        # 列宽按比例调节：第一列10%、第二列70%、第三列20%
        # 使用 Interactive 模式，并在表格尺寸变化时根据比例重新设置列宽
        try:
            header = self.results_table.horizontalHeader()
            header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        except Exception:
            pass
        self._results_col_ratios = (0.70, 0.30)
        try:
            self.results_table.installEventFilter(self)
        except Exception:
            pass
        self._apply_results_table_column_ratio()

        self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        # 双击行打开对应分辨率子目录
        self.results_table.cellDoubleClicked.connect(self._on_results_double_clicked)

        group_results = QtWidgets.QGroupBox("截图文件目录")
        grl = QtWidgets.QVBoxLayout(group_results)
        grl.setContentsMargins(6, 6, 6, 6)
        grl.addWidget(self.results_table)
        # 初始化覆盖在结果表上的加载蒙版（菊花转圈圈）
        self._init_results_overlay()
        layout.addWidget(group_results)

        container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        return container

    def _init_results_overlay(self) -> None:
        """初始化覆盖在结果表上的半透明加载蒙版与菊花转圈圈。"""
        try:
            # 叠加在结果表上的蒙版，默认隐藏
            self._results_overlay = QtWidgets.QWidget(self.results_table)
            self._results_overlay.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            self._results_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 80);")
            ovl = QtWidgets.QVBoxLayout(self._results_overlay)
            ovl.setContentsMargins(24, 24, 24, 24)
            ovl.setSpacing(12)
            ovl.setAlignment(QtCore.Qt.AlignCenter)

            # 菊花转圈圈（去掉居中文案，仅保留转圈圈）
            self._overlay_spinner = BusySpinner(
                parent=self._results_overlay,
                inner_radius=12,
                line_length=6,
                line_width=3,
                lines=12,
                color=QtGui.QColor(str(getattr(theme, "PRIMARY_BLUE", "#409eff"))),
                interval_ms=80,
            )
            ovl.addWidget(self._overlay_spinner, 0, QtCore.Qt.AlignCenter)

            self._results_overlay.hide()

            # 保持蒙版尺寸与结果表一致
            try:
                self.results_table.installEventFilter(self)
            except Exception:
                pass
            self._position_results_overlay()
        except Exception:
            pass

    def _position_results_overlay(self) -> None:
        """将蒙版尺寸设为覆盖整个结果表。"""
        try:
            if hasattr(self, "_results_overlay") and self._results_overlay is not None:
                self._results_overlay.setGeometry(self.results_table.rect())
                self._results_overlay.raise_()
        except Exception:
            pass

    def _show_results_overlay(self, show: bool) -> None:
        """显示或隐藏结果表上的加载蒙版。"""
        try:
            if not hasattr(self, "_results_overlay") or self._results_overlay is None:
                return
            if show:
                self._position_results_overlay()
                self._results_overlay.show()
                self._results_overlay.raise_()
                try:
                    if hasattr(self, "_overlay_spinner") and self._overlay_spinner is not None:
                        self._overlay_spinner.start()
                except Exception:
                    pass
            else:
                self._results_overlay.hide()
                try:
                    if hasattr(self, "_overlay_spinner") and self._overlay_spinner is not None:
                        self._overlay_spinner.stop()
                except Exception:
                    pass
        except Exception:
            pass

    def _sync_default_output_dir(self, _: str) -> None:
        """Update the default output dir to <video_dir>/截图 when the video dir changes."""
        vd = self.video_dir_edit.text().strip()
        if vd:
            self.output_dir_edit.setText(os.path.join(vd, "截图"))

    def _on_browse_video_dir(self) -> None:
        """Open a directory chooser for the video directory and update defaults."""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择视频目录")
        if d:
            self.video_dir_edit.setText(d)
            # 将输出目录同步为默认值
            self.output_dir_edit.setText(os.path.join(d, "截图"))

    def _on_browse_output_dir(self) -> None:
        """Open a directory chooser for the output directory."""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择截图目录")
        if d:
            self.output_dir_edit.setText(d)

    def _on_action_clicked(self) -> None:
        """开始或停止任务；开始前进行一次授权/环境预检，并询问是否清理旧截图目录。

        当当前状态为未运行时：
        - 若截图目录存在且非空，弹出确认弹窗：
          - 选择“删除后开始”：先清理该目录，再开始任务。
          - 选择“保留并开始”：不清理，直接开始任务。
          - 选择“取消”：不开始任务。
        - 若目录不存在或为空：直接开始任务。

        当当前状态为运行中时：调用停止逻辑。
        """
        if not self._is_running:
            # 授权/环境预检（若尚未通过，则执行一次；通过后本次会话内不重复检查）
            try:
                if not self._preflight_passed:
                    app = QtWidgets.QApplication.instance()
                    ok = bool(run_preflight_checks(app)) if app is not None else False
                    self._preflight_passed = ok
                    if not ok:
                        try:
                            QtWidgets.QMessageBox.warning(self, "未授权或环境不满足", "未授权或环境不满足，无法开始")
                        except Exception:
                            pass
                        return
            except Exception:
                # 兜底：出现异常则视为未通过，避免误放行
                try:
                    QtWidgets.QMessageBox.warning(self, "预检失败", "预检失败：未授权或环境不满足")
                except Exception:
                    pass
                return
            # 在开始前确认是否清理旧目录
            self._confirm_and_cleanup_output_dir_before_start()
            self._start_task()
        else:
            self._stop_task()

    def _confirm_and_cleanup_output_dir_before_start(self) -> bool:
        """开始前确认是否清理旧截图目录（精简版）。

        仅在输出目录存在且非空时弹窗，提供两种选择：
        - "删除后开始"：直接尝试清理后继续开始（失败也不打断）。
        - "直接开始"：保留旧内容并开始。

        始终返回 True 以继续开始任务（除非无法访问目录时，视为空处理）。
        """
        output_dir = self.output_dir_edit.text().strip()

        # 目录不存在则直接允许开始
        if not os.path.isdir(output_dir):
            return True

        # 检查目录是否为空
        try:
            entries = list(os.scandir(output_dir))
            is_empty = len(entries) == 0
        except Exception:
            # 无法列出时，视为空，避免多余提示
            is_empty = True

        if is_empty:
            return True

        # 目录非空，弹窗确认（仅两项：删除后开始 / 直接开始）
        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setIcon(QtWidgets.QMessageBox.Question)
        msg_box.setWindowTitle("清理旧截图确认")
        msg_box.setText(
            f"检测到截图目录非空：\n{output_dir}\n\n是否删除旧文件后开始？"
        )
        delete_button = msg_box.addButton("删除后开始", QtWidgets.QMessageBox.YesRole)
        keep_button = msg_box.addButton("直接开始", QtWidgets.QMessageBox.NoRole)
        msg_box.setDefaultButton(delete_button)
        msg_box.exec()

        clicked = msg_box.clickedButton()
        if clicked is delete_button:
            # 尝试清理（失败不打断流程，不额外弹窗）
            try:
                self._safe_cleanup_directory(output_dir)
            except Exception:
                pass
            return True
        # 直接开始
        return True

    def _safe_cleanup_directory(self, dir_path: str) -> Tuple[bool, str]:
        """安全清理指定目录内容。

        尝试删除目录中的所有文件与子目录；若删除整个目录失败，则逐项删除。
        删除后不保证重建目录；后续 `_start_task()` 会负责创建。

        参数
        - dir_path: 待清理目录的绝对路径或相对路径。

        返回
        - (True, "") 表示清理成功；(False, 错误信息) 表示失败。
        """
        try:
            abs_dir = os.path.abspath(dir_path)
            # 优先尝试整体删除
            try:
                shutil.rmtree(abs_dir)
            except Exception:
                # 回退到逐项清理
                try:
                    for entry in os.scandir(abs_dir):
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                shutil.rmtree(entry.path)
                            else:
                                os.remove(entry.path)
                        except Exception:
                            pass
                except Exception as e2:
                    return False, str(e2)
            return True, ""
        except Exception as e:
            return False, str(e)

    def _start_task(self) -> None:
        """启动后台任务，读取当前表单的目录、数量与线程数。"""
        video_dir = self.video_dir_edit.text().strip()
        output_dir = self.output_dir_edit.text().strip()
        count = int(self.count_spin.value() if hasattr(self, "count_spin") else 1)
        threads = int(self.threads_spin.value() if hasattr(self, "threads_spin") else 1)
        filter_count = int(self.filter_spin.value() if hasattr(self, "filter_spin") else 1)

        if not video_dir or not os.path.isdir(video_dir):
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择有效的视频目录")
            return
        if not output_dir:
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择截图目录")
            return
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"无法创建截图目录: {e}")
            return

        # 线程与工作者
        self._thread = QtCore.QThread(self)
        self._worker = ExtractFramesWorker()
        self._worker.moveToThread(self._thread)
        # 使用带参数的启动信号，确保 run() 在工作线程中执行
        self._worker.start.connect(self._worker.run)
        self._worker.phase.connect(self._on_phase)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        # 连接每张图片保存后的预览更新
        self._worker.image_saved.connect(self._on_image_saved)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()
        # 通过信号触发开始，避免直接调用导致在主线程执行
        self._worker.start.emit(video_dir, output_dir, count, threads, filter_count)

        # UI 状态更新
        self._is_running = True
        self.action_btn.setText("停止")
        self.progress_bar.setValue(0)
        # 运行态样式：强调“停止”按钮，禁用“开始”外观
        self._apply_action_button_style(running=True)
        self._set_form_enabled(False)
        # 显示结果列表蒙版（转圈圈）
        self._show_results_overlay(True)

    def _stop_task(self) -> None:
        """Request the worker to stop and restore UI when done."""
        if self._worker is not None:
            self._worker.stop()
        # 立即切换按钮外观，等待线程退出
        self.action_btn.setEnabled(False)
        self.action_btn.setText("停止中…")

    def _on_phase(self, text: str) -> None:
        """Update phase-related visuals. Currently only a no-op placeholder."""
        # 可选：在未来加入阶段标签或日志视图
        return

    def _on_progress(self, done: int, total: int) -> None:
        """Update the progress bar with given values.

        显示当前完成数量/总数，并根据计数计算百分比以更新进度条。
        """
        try:
            pct = 0 if total <= 0 else int(max(0, min(100, round(done / total * 100))))
            self.progress_bar.setValue(pct)
            # 在进度条上显示：已完成/总数
            self.progress_bar.setFormat(f"{done} / {total}")
        except Exception:
            pass

    def _on_finished(self, out_dir: str, count: int) -> None:
        """Handle successful completion: update results table and reset UI."""
        # 根据截图目录下的分辨率子目录填充结果表
        self._populate_results_by_resolution(out_dir)

        # 复位 UI
        self._is_running = False
        self.action_btn.setEnabled(True)
        self.action_btn.setText("开始")
        # 空闲态样式：强调“开始”按钮
        self._apply_action_button_style(running=False)
        self._set_form_enabled(True)
        # 隐藏结果列表蒙版
        self._show_results_overlay(False)
        self._cleanup_thread()

        # 完成后弹窗，提示用户打开目录进行人工筛选
        self._prompt_open_dir_after_finish(out_dir)

    def _on_image_saved(self, path: str) -> None:
        """每次成功保存截图后，将该图片以幻灯片式交替展示。

        - 固定预览区域大小；
        - 新图显示时淡入，旧图淡出并销毁以释放内存。
        """
        try:
            if not path or not os.path.isfile(path):
                return
            pix = QtGui.QPixmap(path)
            if pix.isNull():
                return
            self._show_preview_pixmap(pix)
        except Exception:
            pass

    def _show_preview_pixmap(self, pix: QtGui.QPixmap) -> None:
        """以交替动画显示预览图片，并清理前一张图片引用避免占用内存。

        Parameters
        ----------
        pix : QtGui.QPixmap
            需要显示的新图片。
        """
        try:
            # 创建透明度效果以支持淡入/淡出
            if not hasattr(self, "_preview_opacity") or self._preview_opacity is None:
                self._preview_opacity = QtWidgets.QGraphicsOpacityEffect(self.preview_label)
                self.preview_label.setGraphicsEffect(self._preview_opacity)
                self._preview_opacity.setOpacity(1.0)

            had_prev = hasattr(self, "_current_preview_pixmap") and isinstance(getattr(self, "_current_preview_pixmap"), QtGui.QPixmap) and getattr(self, "_current_preview_pixmap") is not None and not getattr(self, "_current_preview_pixmap").isNull()

            def do_fade_in() -> None:
                try:
                    size = self.preview_label.size()
                    scaled = pix.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                    self.preview_label.setPixmap(scaled)
                    self._current_preview_pixmap = pix
                except Exception:
                    self.preview_label.setPixmap(pix)
                    self._current_preview_pixmap = pix
                anim_in = QtCore.QPropertyAnimation(self._preview_opacity, b"opacity", self)
                anim_in.setDuration(160)
                anim_in.setStartValue(0.0)
                anim_in.setEndValue(1.0)
                anim_in.setEasingCurve(QtCore.QEasingCurve.InOutQuad)
                self._preview_anim = anim_in
                anim_in.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

            if had_prev:
                # 先淡出旧图，然后清理并淡入新图
                anim_out = QtCore.QPropertyAnimation(self._preview_opacity, b"opacity", self)
                anim_out.setDuration(120)
                anim_out.setStartValue(1.0)
                anim_out.setEndValue(0.0)
                anim_out.setEasingCurve(QtCore.QEasingCurve.InOutQuad)

                def on_out_finished() -> None:
                    # 清理旧图，确保销毁引用
                    try:
                        self.preview_label.clear()
                    except Exception:
                        pass
                    self._current_preview_pixmap = None
                    do_fade_in()

                anim_out.finished.connect(on_out_finished)
                self._preview_anim = anim_out
                anim_out.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
            else:
                # 初次显示或无旧图：直接淡入新图
                try:
                    self._preview_opacity.setOpacity(0.0)
                except Exception:
                    pass
                do_fade_in()
        except Exception:
            pass

    def _populate_results_by_resolution(self, out_dir: str) -> None:
        """Populate the results table with all image files and their resolutions.

        遍历传入目录下的所有图片文件（递归）：
        - 第 1 列：序号（从 1 开始）
        - 第 2 列：文件的绝对路径
        - 第 3 列：文件分辨率（如 1920x1080），无法识别时显示 "unknown"

        Parameters
        ----------
        out_dir : str
            要扫描的根目录。将递归遍历其子目录，收集扩展名为 .jpg/.jpeg/.png 的图片文件。
        """
        try:
            self.results_table.setRowCount(0)
            if not out_dir or not os.path.isdir(out_dir):
                return
            exts = {".jpg", ".jpeg", ".png"}

            # 收集所有匹配的图片文件（递归）
            all_images: list[str] = []
            display_lines = 30
            try:
                for dirpath, dirnames, filenames in os.walk(out_dir):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in exts:
                            all_images.append(fp)
                            if len(all_images) > display_lines:
                                break
                    if len(all_images) > display_lines:
                        break
            except Exception:
                pass

            # 稳定排序后填充表格
            
            for fp in sorted(all_images):
                # 读取图片分辨率（WxH）；失败则显示 unknown
                res_text = "unknown"
                try:
                    img = QtGui.QImage(fp)
                    if not img.isNull():
                        res_text = f"{img.width()}x{img.height()}"
                except Exception:
                    pass

                row = self.results_table.rowCount()
                self.results_table.insertRow(row)
                self.results_table.setItem(row, 0, QtWidgets.QTableWidgetItem(fp))
                self.results_table.setItem(row, 1, QtWidgets.QTableWidgetItem(res_text))
            # 增加一行，提示是：查看更多，请到 out_dir 查看
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self.results_table.setItem(row, 0, QtWidgets.QTableWidgetItem(out_dir))
            self.results_table.setItem(row, 1, QtWidgets.QTableWidgetItem("查看更多，双击打开目录..."))
        except Exception:
            pass

    def _on_results_double_clicked(self, row: int, column: int) -> None:
        """双击结果行时打开对应文件或目录。

        从第 `row` 行的第 2 列读取绝对路径：
        - 如果是文件，则尝试用系统默认程序打开该文件；
        - 如果是目录，则尝试用系统文件管理器打开该目录。
        在 Windows 上优先使用 Qt 的 `QDesktopServices`，失败时回退到 `os.startfile`。
        """
        try:
            item = self.results_table.item(row, 0)
            if not item:
                return
            path = item.text().strip()
            if not path:
                return
            # 优先尝试打开文件，其次目录
            target = None
            if os.path.isfile(path):
                target = path
            elif os.path.isdir(path):
                target = path
            else:
                return

            opened = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))
            if not opened:
                try:
                    os.startfile(target)  # Windows 回退
                except Exception:
                    pass
        except Exception:
            pass

    def _on_error(self, msg: str) -> None:
        """Display error and reset UI state."""
        QtWidgets.QMessageBox.critical(self, "错误", msg)
        self._is_running = False
        self.action_btn.setEnabled(True)
        self.action_btn.setText("开始")
        # 空闲态样式：强调“开始”按钮
        self._apply_action_button_style(running=False)
        self._set_form_enabled(True)
        # 隐藏结果列表蒙版
        self._show_results_overlay(False)
        self._cleanup_thread()

    def _prompt_open_dir_after_finish(self, out_dir: str) -> None:
        """在任务完成后弹出提示框，提供“打开目录/取消”选项。

        Parameters
        ----------
        out_dir : str
            截图输出根目录，将在用户点击“打开目录”时用系统文件管理器打开。
        """
        try:
            if not out_dir or not os.path.isdir(out_dir):
                return
            msg = QtWidgets.QMessageBox(self)
            msg.setWindowTitle("提示")
            msg.setIcon(QtWidgets.QMessageBox.Information)
            msg.setText("请打开生成截图的目录，人工筛选删除不符合要求的图片\n便于接下来的封面合成质量！")
            open_btn = msg.addButton("打开目录", QtWidgets.QMessageBox.AcceptRole)
            cancel_btn = msg.addButton("取消", QtWidgets.QMessageBox.RejectRole)
            msg.exec()
            if msg.clickedButton() == open_btn:
                opened = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(out_dir))
                if not opened:
                    try:
                        os.startfile(out_dir)
                    except Exception:
                        pass
        except Exception:
            # 保持安静失败，不影响主流程
            pass

    def _cleanup_thread(self) -> None:
        """Dispose thread and worker references safely."""
        try:
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(1500)
        except Exception:
            pass
        self._thread = None
        self._worker = None

    def is_running(self) -> bool:
        """Return whether a background extraction task is currently running.

        检查线程运行状态与内部 `_is_running` 标志，尽可能给出稳健结果。

        Returns
        -------
        bool
            True 表示任务仍在运行；False 表示空闲或已停止。
        """
        try:
            th_alive = bool(getattr(self, "_thread", None) and self._thread.isRunning())
        except Exception:
            th_alive = False
        return bool(th_alive or getattr(self, "_is_running", False))

    def request_stop(self) -> None:
        """Request a graceful stop of the extraction workflow.

        公开的停止入口，用于窗口关闭或退出时统一调用：
        - 优先复用本页既有的 `_stop_task()` 停止逻辑；
        - 若不可用则直接调用 worker.stop() 并更新按钮状态。
        """
        try:
            if hasattr(self, "_stop_task") and callable(self._stop_task):
                self._stop_task()
                return
        except Exception:
            pass
        # 回退：直接停止 worker 并提示按钮状态
        try:
            if getattr(self, "_worker", None) is not None:
                self._worker.stop()
        except Exception:
            pass
        try:
            if hasattr(self, "action_btn") and self.action_btn is not None:
                self.action_btn.setEnabled(False)
                self.action_btn.setText("停止中…")
        except Exception:
            pass

    def _set_form_enabled(self, enabled: bool) -> None:
        """启用/禁用左侧表单控件。"""
        try:
            self.video_dir_edit.setEnabled(enabled)
            if hasattr(self, "count_spin"):
                self.count_spin.setEnabled(enabled)
            if hasattr(self, "threads_spin"):
                self.threads_spin.setEnabled(enabled)
            self.output_dir_edit.setEnabled(enabled)
        except Exception:
            pass

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        """事件过滤：保持加载蒙版自适应，并在预览标签尺寸变化时重绘缩放。"""
        try:
            if obj is self.results_table and event.type() in (
                QtCore.QEvent.Resize,
                QtCore.QEvent.Move,
                QtCore.QEvent.Show,
            ):
                self._position_results_overlay()
                # 表格尺寸变化时按比例调整列宽
                self._apply_results_table_column_ratio()
            # 预览标签尺寸变化时，若存在当前预览图，则按新尺寸重新缩放显示
            if obj is self.preview_label and event.type() in (
                QtCore.QEvent.Resize,
            ):
                try:
                    pix = getattr(self, "_current_preview_pixmap", None)
                    if pix is not None and isinstance(pix, QtGui.QPixmap) and not pix.isNull():
                        size = self.preview_label.size()
                        if size.width() > 0 and size.height() > 0:
                            scaled = pix.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                            self.preview_label.setPixmap(scaled)
                except Exception:
                    pass
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _apply_results_table_column_ratio(self) -> None:
        """按比例设置结果表的列宽。

        以当前表格视口宽度为基准，按照 `self._results_col_ratios` 依次将
        三列宽度设置为约 10% / 70% / 20%。该方法在表格初始化与尺寸变化时调用。

        注意：Qt 的表头没有原生“列权重”概念，因此使用 `Interactive` 模式并在
        `Resize` 事件中动态应用比例，以获得期望效果。
        """
        try:
            ratios = getattr(self, "_results_col_ratios", (0.70, 0.30))
            if not isinstance(ratios, (tuple, list)) or len(ratios) != 2:
                ratios = (0.70, 0.30)
            total = sum(float(r) for r in ratios)
            if total <= 0:
                return
            vw = self.results_table.viewport().width()
            if vw <= 0:
                # 回退到表格本身宽度
                vw = self.results_table.width()
            if vw <= 0:
                return

            # 计算各列宽度，保证最小可读宽度
            mins = (60, 200)
            widths = []
            for i, r in enumerate(ratios):
                w = int(vw * (float(r) / total))
                w = max(mins[i], w)
                widths.append(w)

            # 应用列宽
            for i, w in enumerate(widths):
                try:
                    self.results_table.setColumnWidth(i, w)
                except Exception:
                    pass
        except Exception:
            pass

    # --- 样式与尺寸同步方法 ---
    def _apply_progressbar_style(self, chunk_color: str = theme.PRIMARY_BLUE) -> None:
        """统一设置进度条的尺寸与样式，使其与“视频混剪”页一致。

        - 纵向固定高度，横向扩展；高度依据屏幕 DPI 自适应。
        - 样式表应用统一的边框与进度块颜色。

        Parameters
        ----------
        chunk_color : str
            进度块的颜色（CSS hex）。
        """
        try:
            if self.progress_bar is None:
                return
            # 尺寸策略：横向扩展，纵向固定
            self.progress_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            # 计算 DPI 缩放
            screen = QtWidgets.QApplication.primaryScreen()
            dpi = screen.logicalDotsPerInch() if screen else 96.0
            scale = max(1.0, dpi / 96.0)
        except Exception:
            scale = 1.0

        # 高度与字号（与视频混剪页保持一致策略）
        base_h = 32
        height = int(max(28, min(52, base_h * scale)))
        try:
            self.progress_bar.setFixedHeight(height)
            # 缓存统一控件高度，供按钮样式使用，确保开始/停止高度一致
            self._control_height = height
        except Exception:
            # 回退：无缓存则使用主题默认高度
            self._control_height = getattr(self, "_control_height", theme.BUTTON_HEIGHT)
        try:
            font = self.progress_bar.font()
            base_pt = 11
            pt_size = int(max(base_pt, min(16, base_pt * scale)))
            font.setPointSize(pt_size)
            self.progress_bar.setFont(font)
        except Exception:
            pass

        try:
            style = (
                f"QProgressBar{{min-height:{height}px;max-height:{height}px;border:1px solid #bbb;border-radius:4px;text-align:center;}}"
                f"QProgressBar::chunk{{background-color:{chunk_color};margin:0px;}}"
            )
            self.progress_bar.setStyleSheet(style)
        except Exception:
            pass

    def _apply_action_button_style(self, running: bool) -> None:
        """根据运行状态统一设置“开始/停止”按钮的高度与样式。

        - 高度与进度条保持一致；
        - 空闲态：主色背景、白色文字；
        - 运行态：强调色背景、白色文字（单按钮等效“停止”）。

        Parameters
        ----------
        running : bool
            当前是否为运行态。
        """
        # 使用统一的控件高度，避免不同状态下按钮高度不一致
        height = int(getattr(self, "_control_height", theme.BUTTON_HEIGHT))

        primary_bg = theme.PRIMARY_BLUE
        primary_bg_hover = theme.PRIMARY_BLUE_HOVER
        danger_bg = theme.DANGER_RED
        danger_bg_hover = theme.DANGER_RED_HOVER
        gray_bg = theme.GRAY_BG
        gray_text = theme.GRAY_TEXT

        idle_style = (
            f"QPushButton{{min-height:{height}px;max-height:{height}px;padding:{theme.BUTTON_PADDING_VERTICAL}px {theme.BUTTON_PADDING_HORIZONTAL}px;"
            f"border:none;border-radius:{theme.BUTTON_RADIUS}px;color:#ffffff;background-color:{primary_bg};}}"
            f"QPushButton:hover{{background-color:{primary_bg_hover};}}"
            f"QPushButton:pressed{{background-color:{primary_bg_hover};}}"
            f"QPushButton:disabled{{color: rgba(255,255,255,0.8);background-color:#93c5fd;}}"
        )
        running_style = (
            f"QPushButton{{min-height:{height}px;max-height:{height}px;padding:{theme.BUTTON_PADDING_VERTICAL}px {theme.BUTTON_PADDING_HORIZONTAL}px;"
            f"border:none;border-radius:{theme.BUTTON_RADIUS}px;color:#ffffff;background-color:{danger_bg};}}"
            f"QPushButton:hover{{background-color:{danger_bg_hover};}}"
            f"QPushButton:pressed{{background-color:{danger_bg_hover};}}"
            f"QPushButton:disabled{{color: rgba(255,255,255,0.8);background-color:#fca5a5;}}"
        )

        try:
            # 同步按钮字体大小与进度条，视觉高度一致
            pb_font = self.progress_bar.font() if getattr(self, "progress_bar", None) is not None else None
            if pb_font is not None:
                self.action_btn.setFont(pb_font)
            self.action_btn.setStyleSheet(running_style if running else idle_style)
            self.action_btn.setFixedHeight(height)
            self.action_btn.setToolTip("点击开始" if not running else "点击停止")
        except Exception:
            pass
