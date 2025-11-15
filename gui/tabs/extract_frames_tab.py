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
from PySide6 import QtWidgets, QtCore, QtGui
from gui.utils import theme
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


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
                wh = probe_video_resolution(in_path)
                res_dir = os.path.join(output_dir, f"{wh[0]}x{wh[1]}" if wh else "unknown_resolution")
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
                print(f"目录: {dirpath}")
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
        self.video_dir_edit.setText("E:\\Download\\社媒助手\\抖音\\Miya")

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
        self.count_spin.setRange(1, 20)
        self.count_spin.setValue(1)
        row_1 = QtWidgets.QHBoxLayout()
        row_1.addWidget(lbl_count, 0)
        row_1.addWidget(self.count_spin, 1)
        gl2.addLayout(row_1)

        # 4) 并发执行线程数（数值框）
        lbl_threads = QtWidgets.QLabel("并发线程数")
        self.threads_spin = QtWidgets.QSpinBox()
        self.threads_spin.setRange(1, 32)
        self.threads_spin.setValue(2)
        row_2 = QtWidgets.QHBoxLayout()
        row_2.addWidget(lbl_threads, 0)
        row_2.addWidget(self.threads_spin, 1)
        gl2.addLayout(row_2)

        # 4) 筛选掉相同视频分辨率的视频数少于x（数值框）
        lbl_filter = QtWidgets.QLabel("过滤同分辨率的视频数少于")
        self.filter_spin = QtWidgets.QSpinBox()
        self.filter_spin.setRange(1, 100)
        self.filter_spin.setValue(20)
        row_3 = QtWidgets.QHBoxLayout()
        row_3.addWidget(lbl_filter, 0)
        row_3.addWidget(self.filter_spin, 1)
        gl2.addLayout(row_3)

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
        # 在下方加入一个可扩展的占位控件，吃掉剩余空间，使 group1/2 靠上排列
        spacer = QtWidgets.QWidget()
        spacer.setMinimumSize(0, 0)
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        splitter.addWidget(spacer)
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
        # 与视频混剪页保持一致的尺寸与样式（进度条与按钮同高）
        self._apply_progressbar_style(chunk_color=theme.PRIMARY_BLUE)
        self._apply_action_button_style(running=False)
        self.action_btn.clicked.connect(self._on_action_clicked)
        row_progress = QtWidgets.QHBoxLayout()
        row_progress.addWidget(self.progress_bar, 1)
        row_progress.addWidget(self.action_btn)

        group_progress = QtWidgets.QGroupBox("运行状态")
        gpl = QtWidgets.QVBoxLayout(group_progress)
        gpl.setContentsMargins(6, 6, 6, 6)
        gpl.addLayout(row_progress)
        layout.addWidget(group_progress)

        # b) 结果表（序号，目录，截图数量）
        self.results_table = QtWidgets.QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["序号", "目录", "截图数量"])
        self.results_table.horizontalHeader().setStretchLastSection(True)
    
        self.results_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)

        self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        # 双击行打开对应分辨率子目录
        self.results_table.cellDoubleClicked.connect(self._on_results_double_clicked)

        group_results = QtWidgets.QGroupBox("截图文件目录")
        grl = QtWidgets.QVBoxLayout(group_results)
        grl.setContentsMargins(6, 6, 6, 6)
        grl.addWidget(self.results_table)
        layout.addWidget(group_results)

        container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        return container

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
        """Start or stop the extraction task depending on current state."""
        if not self._is_running:
            self._start_task()
        else:
            self._stop_task()

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
        self._cleanup_thread()

    def _populate_results_by_resolution(self, out_dir: str) -> None:
        """Populate the results table with resolution subfolders and image counts.

        Parameters
        ----------
        out_dir : str
            截图输出根目录。该目录下按分辨率（如 1920x1080、1280x720、unknown_resolution）划分子目录，
            本方法将枚举这些子目录，并统计各自包含的图片数量（非递归）。
        """
        try:
            self.results_table.setRowCount(0)
            if not out_dir or not os.path.isdir(out_dir):
                return
            exts = {".jpg", ".jpeg", ".png"}
            # 遍历分辨率子目录，仅统计该层级的图片数量
            try:
                for name in sorted(os.listdir(out_dir)):
                    sub_path = os.path.join(out_dir, name)
                    if not os.path.isdir(sub_path):
                        continue
                    # 统计图片数量（非递归）
                    try:
                        files = os.listdir(sub_path)
                    except Exception:
                        files = []
                    count = 0
                    for f in files:
                        fp = os.path.join(sub_path, f)
                        if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in exts:
                            count += 1
                    # 填充一行：序号、分辨率子目录路径、数量
                    row = self.results_table.rowCount()
                    self.results_table.insertRow(row)
                    self.results_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(row + 1)))
                    self.results_table.setItem(row, 1, QtWidgets.QTableWidgetItem(sub_path))
                    self.results_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(count)))
            except Exception:
                pass
        except Exception:
            pass

    def _on_results_double_clicked(self, row: int, column: int) -> None:
        """双击结果行时打开对应目录。

        从第 `row` 行的第 2 列（分辨率目录路径）读取目录路径，尝试用系统默认文件管理器打开。
        在 Windows 上优先使用 Qt 的 `QDesktopServices`，失败时回退到 `os.startfile`。
        """
        try:
            item = self.results_table.item(row, 1)
            if not item:
                return
            path = item.text().strip()
            if not path or not os.path.isdir(path):
                return
            # 优先使用 Qt 的 DesktopServices 打开目录
            opened = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
            if not opened:
                try:
                    os.startfile(path)  # Windows 回退
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
        self._cleanup_thread()

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
        except Exception:
            pass
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
        try:
            pb_h = self.progress_bar.height() if getattr(self, "progress_bar", None) is not None else 0
        except Exception:
            pb_h = 0
        height = pb_h if isinstance(pb_h, int) and pb_h > 0 else theme.BUTTON_HEIGHT

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
            self.action_btn.setStyleSheet(running_style if running else idle_style)
            self.action_btn.setFixedHeight(height)
            self.action_btn.setToolTip("点击开始" if not running else "点击停止")
        except Exception:
            pass