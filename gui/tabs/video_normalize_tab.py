from __future__ import annotations

from typing import Optional, List, Tuple
import os
import pathlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6 import QtWidgets, QtCore, QtGui

from gui.utils import theme
from gui.precheck import run_preflight_checks
from utils.common_utils import is_video_file
from utils.calcu_video_info import ffprobe_duration
from video_tool.video_normalize import VideoNormalize


class NormalizeWorker(QtCore.QObject):
    """后台执行视频预处理（归一化）工作流，并发处理并回传进度与结果。"""

    progress = QtCore.Signal(int, int)
    result = QtCore.Signal(str, float, float)
    finished = QtCore.Signal(int, int)
    error = QtCore.Signal(str)
    start = QtCore.Signal(list, str, int)

    def __init__(self) -> None:
        super().__init__()
        self._stopping = False

    def stop(self) -> None:
        """请求软停止，正在处理的任务完成后退出。"""
        self._stopping = True

    @QtCore.Slot(list, str, int)
    def run(self, dirs: List[str], mode: str, concurrency: int) -> None:
        """执行多目录下的视频归一化。

        Parameters
        ----------
        dirs : List[str]
            待处理的视频目录列表（非递归）。
        mode : str
            质量模式：high/standard/lite。
        concurrency : int
            并发处理的视频数量。
        """
        try:
            vids: List[pathlib.Path] = []
            for d in dirs:
                try:
                    base = pathlib.Path(d)
                    if not base.exists() or not base.is_dir():
                        continue
                    for name in os.listdir(str(base)):
                        p = base / name
                        if p.is_file() and is_video_file(str(p)):
                            vids.append(p)
                except Exception:
                    continue
            total = len(vids)
            done = 0
            if total <= 0:
                self.error.emit("所选目录下未找到视频文件")
                self.finished.emit(0, 0)
                return
            vn = VideoNormalize(mode=mode)
            ok = 0
            fail = 0
            max_workers = max(1, int(concurrency))
            futures: List[Tuple[pathlib.Path, object]] = []
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for vp in vids:
                    if self._stopping:
                        break
                    fut = ex.submit(self._normalize_one, vn, vp, mode)
                    futures.append((vp, fut))
                for vp, fut in futures:
                    if self._stopping:
                        break
                    try:
                        outp: Optional[pathlib.Path] = fut.result()
                        if outp and outp.exists():
                            d = float(ffprobe_duration(outp))
                            sz_mb = float(outp.stat().st_size) / (1024.0 * 1024.0)
                            self.result.emit(str(outp), d, sz_mb)
                            ok += 1
                        else:
                            fail += 1
                    except Exception as e:
                        self.error.emit(str(e))
                        fail += 1
                    finally:
                        done += 1
                        self.progress.emit(done, total)
            self.finished.emit(ok, fail)
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit(0, 0)

    def _normalize_one(self, vn: VideoNormalize, vp: pathlib.Path, mode: str) -> Optional[pathlib.Path]:
        """单视频归一化并返回输出路径。"""
        try:
            out = vn.normalize(str(vp), mode=mode)
            return pathlib.Path(out)
        except Exception:
            return None


class VideoNormalizeTab(QtWidgets.QWidget):
    """“视频预处理”标签页：左右分栏布局，目录选择与参数设置，结果与进度显示。"""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.root_layout = QtWidgets.QHBoxLayout(self)
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[NormalizeWorker] = None
        self._is_running: bool = False
        self._preflight_passed: bool = False
        self._build_page()

    def is_running(self) -> bool:
        """返回当前是否处于运行状态。"""
        return bool(self._is_running)

    def request_stop(self) -> None:
        """发起软停止请求。"""
        try:
            if self._worker:
                self._worker.stop()
            if self._thread:
                try:
                    self._thread.quit()
                except Exception:
                    pass
            self._is_running = False
            self._apply_action_button_style(False)
        except Exception:
            pass

    def _build_page(self) -> None:
        """构建整页布局（左右面板加入分割器）。"""
        left_panel = self._build_left_panel()
        right_panel = self._build_right_panel()
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)
        try:
            splitter.handle(1).setEnabled(False)
        except Exception:
            pass
        self.root_layout.setContentsMargins(6, 6, 6, 6)
        self.root_layout.addWidget(splitter)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        """构建左侧参数面板（视频目录、质量模式、并发数量）。"""
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(8)

        group1 = QtWidgets.QGroupBox("视频目录")
        g1 = QtWidgets.QVBoxLayout(group1)
        self.video_dirs_list = QtWidgets.QListWidget()
        self.video_dirs_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.video_dirs_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.video_dirs_list.setMinimumHeight(5 * 24)
        g1.addWidget(self.video_dirs_list)
        row_btns = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("添加目录")
        rm_btn = QtWidgets.QPushButton("移除选中")
        add_btn.clicked.connect(self._on_add_dir)
        rm_btn.clicked.connect(self._on_remove_selected_dirs)
        row_btns.addWidget(add_btn)
        row_btns.addWidget(rm_btn)
        g1.addLayout(row_btns)
        vbox.addWidget(group1)

        group2 = QtWidgets.QGroupBox("预处理参数模式")
        g2 = QtWidgets.QFormLayout(group2)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["发布版", "无损版", "预览版"])  # 显示
        self.mode_combo.setCurrentIndex(0)  # 默认发布版
        g2.addRow("质量选择", self.mode_combo)
        self.concurrent_spin = QtWidgets.QSpinBox()
        self.concurrent_spin.setRange(1, 16)
        self.concurrent_spin.setValue(4)
        self.concurrent_spin.setKeyboardTracking(True)
        g2.addRow("并发数量", self.concurrent_spin)
        vbox.addWidget(group2)

        vbox.addStretch(1)
        return panel

    def _build_right_panel(self) -> QtWidgets.QWidget:
        """构建右侧运行与结果面板（进度、开始/停止、结果表）。"""
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(8)

        ctrl_group = QtWidgets.QGroupBox("运行控制")
        ctrl_h = QtWidgets.QHBoxLayout(ctrl_group)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.start_stop_btn = QtWidgets.QPushButton("开始")
        self.start_stop_btn.clicked.connect(self._on_start_stop_clicked)
        ctrl_h.addWidget(self.progress_bar, 1)
        ctrl_h.addWidget(self.start_stop_btn, 0)
        vbox.addWidget(ctrl_group)

        self._apply_progressbar_style()
        self._apply_action_button_style(False)

        result_group = QtWidgets.QGroupBox("结果列表")
        result_vbox = QtWidgets.QVBoxLayout(result_group)
        self.results_table = QtWidgets.QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["文件输出路径", "时长", "文件大小"])
        header = self.results_table.horizontalHeader()
        try:
            header.setStretchLastSection(False)
            header.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        except Exception:
            pass
        self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.results_table.doubleClicked.connect(self._on_open_selected_file)
        try:
            self.results_table.installEventFilter(self)
        except Exception:
            pass
        result_vbox.addWidget(self.results_table, 1)
        vbox.addWidget(result_group)
        self._apply_results_table_column_widths()
        return panel

    def _apply_results_table_column_widths(self) -> None:
        """设置结果表格列宽比例为 70%/15%/15%。"""
        try:
            w = max(200, int(self.results_table.width()))
            self.results_table.setColumnWidth(0, int(w * 0.70))
            self.results_table.setColumnWidth(1, int(w * 0.15))
            self.results_table.setColumnWidth(2, int(w * 0.15))
        except Exception:
            pass

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        """监听结果表尺寸变化以重设列宽。"""
        try:
            if obj is self.results_table and event.type() == QtCore.QEvent.Resize:
                self._apply_results_table_column_widths()
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _apply_progressbar_style(self, chunk_color: str = theme.PRIMARY_BLUE) -> None:
        """统一设置进度条的尺寸与样式。"""
        try:
            if self.progress_bar is None:
                return
            self.progress_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            screen = QtWidgets.QApplication.primaryScreen()
            dpi = screen.logicalDotsPerInch() if screen else 96.0
            scale = max(1.0, dpi / 96.0)
        except Exception:
            scale = 1.0
        base_h = 32
        height = int(max(28, min(52, base_h * scale)))
        try:
            self.progress_bar.setFixedHeight(height)
            self._control_height = height
        except Exception:
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
            style = theme.build_progressbar_stylesheet(height=height, chunk_color=chunk_color)
            self.progress_bar.setStyleSheet(style)
        except Exception:
            pass

    def _apply_action_button_style(self, running: bool) -> None:
        """统一设置开始/停止按钮样式，与生成封面页一致。"""
        try:
            if self.start_stop_btn is None:
                return
            height = int(getattr(self, "_control_height", theme.BUTTON_HEIGHT))
            primary_bg = theme.PRIMARY_BLUE
            primary_bg_hover = theme.PRIMARY_BLUE_HOVER
            danger_bg = theme.DANGER_RED
            danger_bg_hover = theme.DANGER_RED_HOVER
            idle_style = theme.build_button_stylesheet(
                height=height,
                bg_color=primary_bg,
                hover_color=primary_bg_hover,
                disabled_bg=theme.PRIMARY_BLUE_DISABLED,
                radius=theme.BUTTON_RADIUS,
                pad_h=theme.BUTTON_PADDING_HORIZONTAL,
                pad_v=theme.BUTTON_PADDING_VERTICAL,
            )
            running_style = theme.build_button_stylesheet(
                height=height,
                bg_color=danger_bg,
                hover_color=danger_bg_hover,
                disabled_bg=theme.DANGER_RED_DISABLED,
                radius=theme.BUTTON_RADIUS,
                pad_h=theme.BUTTON_PADDING_HORIZONTAL,
                pad_v=theme.BUTTON_PADDING_VERTICAL,
            )
            self.start_stop_btn.setText("停止" if running else "开始")
            self.start_stop_btn.setStyleSheet(running_style if running else idle_style)
        except Exception:
            pass

    def _on_add_dir(self) -> None:
        """添加视频目录条目。"""
        try:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择视频目录")
            if d:
                self.video_dirs_list.addItem(d)
        except Exception:
            pass

    def _on_remove_selected_dirs(self) -> None:
        """移除选中的视频目录条目。"""
        try:
            for item in self.video_dirs_list.selectedItems():
                row = self.video_dirs_list.row(item)
                self.video_dirs_list.takeItem(row)
        except Exception:
            pass

    def _on_open_selected_file(self) -> None:
        """双击打开结果文件。"""
        try:
            items = self.results_table.selectedItems()
            if not items:
                return
            path = items[0].text()
            if path:
                os.startfile(path)
        except Exception:
            pass

    def _on_start_stop_clicked(self) -> None:
        """开始或停止执行视频预处理。"""
        try:
            if not self._is_running:
                if not self._preflight_passed:
                    try:
                        app = QtWidgets.QApplication.instance()
                        if not (bool(run_preflight_checks(app)) if app is not None else False):
                            return
                        self._preflight_passed = True
                    except Exception:
                        import traceback
                        traceback.print_exc()
                        QtWidgets.QMessageBox.warning(self, "环境检查失败", "FFmpeg 或依赖未准备好，请检查环境配置。")
                        return
                dirs = [self.video_dirs_list.item(i).text() for i in range(self.video_dirs_list.count())]
                if not dirs:
                    QtWidgets.QMessageBox.information(self, "提示", "请先添加视频目录")
                    return
                idx = self.mode_combo.currentIndex()
                mode = "standard" if idx == 0 else ("high" if idx == 1 else "lite")
                concurrency = int(self.concurrent_spin.value())
                self._start_worker(dirs, mode, concurrency)
            else:
                self.request_stop()
        except Exception:
            pass

    def _start_worker(self, dirs: List[str], mode: str, concurrency: int) -> None:
        """启动后台线程并连接信号。"""
        try:
            self._reset_results_table()
            self.progress_bar.setValue(0)
            self._thread = QtCore.QThread(self)
            self._worker = NormalizeWorker()
            self._worker.moveToThread(self._thread)
            self._worker.progress.connect(self._on_progress)
            self._worker.result.connect(self._on_result)
            self._worker.finished.connect(self._on_finished)
            self._worker.error.connect(self._on_error)
            self._worker.start.connect(self._worker.run)
            self._thread.started.connect(lambda: self._worker.start.emit(dirs, mode, concurrency))
            self._thread.start()
            self._is_running = True
            self._apply_action_button_style(True)
        except Exception:
            self._is_running = False
            self._apply_action_button_style(False)

    def _reset_results_table(self) -> None:
        """重置结果列表页（清空内容、选择并滚动到顶部）。"""
        try:
            self.results_table.blockSignals(True)
            self.results_table.clearSelection()
            self.results_table.clearContents()
            self.results_table.setRowCount(0)
            try:
                self.results_table.scrollToTop()
            except Exception:
                pass
            self._apply_results_table_column_widths()
        except Exception:
            pass
        finally:
            try:
                self.results_table.blockSignals(False)
            except Exception:
                pass

    def _on_progress(self, done: int, total: int) -> None:
        """更新进度条。"""
        try:
            pct = int(round(0 if total <= 0 else (done / float(total)) * 100.0))
            self.progress_bar.setValue(pct)
        except Exception:
            pass

    def _on_result(self, path: str, duration: float, size_mb: float) -> None:
        """在结果表格中追加一行输出。"""
        try:
            r = self.results_table.rowCount()
            self.results_table.insertRow(r)
            self.results_table.setItem(r, 0, QtWidgets.QTableWidgetItem(path))
            self.results_table.setItem(r, 1, QtWidgets.QTableWidgetItem(f"{duration:.2f}s"))
            self.results_table.setItem(r, 2, QtWidgets.QTableWidgetItem(f"{size_mb:.2f} MB"))
        except Exception:
            pass

    def _on_finished(self, ok: int, fail: int) -> None:
        """任务完成，复位控件状态。"""
        try:
            self._is_running = False
            self._apply_action_button_style(False)
            try:
                if self._thread:
                    self._thread.quit()
                    self._thread.wait()
                    self._thread = None
                self._worker = None
            except Exception:
                pass
            QtWidgets.QMessageBox.information(self, "完成", f"成功: {ok}, 失败: {fail}")
        except Exception:
            pass

    def _on_error(self, msg: str) -> None:
        """显示错误消息。"""
        try:
            QtWidgets.QMessageBox.warning(self, "错误", msg)
        except Exception:
            pass

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        """窗口关闭时确保后台线程已停止并清理资源。"""
        try:
            self.request_stop()
            try:
                if self._thread:
                    self._thread.quit()
                    self._thread.wait()
            except Exception:
                pass
            self._thread = None
            self._worker = None
        except Exception:
            pass
        try:
            super().closeEvent(event)
        except Exception:
            pass
