from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtWidgets, QtGui

from gui.utils import theme
from gui.precheck import run_preflight_checks

from utils.calcu_video_info import ffprobe_duration
from utils.common_utils import is_video_file

from video_tool.video_detect_scenes import VideoDetectScenes


class VideoDetectScenesWorker(QtCore.QObject):
    progress = QtCore.Signal(int, int)
    row_added = QtCore.Signal(str, float, float)
    finished = QtCore.Signal()
    error = QtCore.Signal(str)
    start = QtCore.Signal(list, str, str, float)

    def __init__(self) -> None:
        super().__init__()
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True

    @QtCore.Slot(list, str, str, float)
    def run(self, video_dirs: List[str], output_root: str, profile: str, threshold: float) -> None:
        try:
            total = len(video_dirs)
            self.progress.emit(0, max(1, total))
            done = 0
            for d in video_dirs:
                if self._stopping:
                    break
                try:
                    files = [name for name in os.listdir(d) if is_video_file(name)]
                except Exception:
                    files = []
                for name in files:
                    if self._stopping:
                        break
                    in_path = os.path.join(d, name)
                    if output_root: # 指定了输出目录，则直接使用即可
                        out_dir = output_root.strip() 
                    else: # 未指定输出目录，则使用视频所在目录
                        out_dir = os.path.join(d, Path(in_path).stem, "切片")

                    try:
                        detector = VideoDetectScenes(device="auto", threshold=float(threshold))
                        saved = detector.save(in_path, output_dir=out_dir, profile=profile)
                        clips = list(saved.get("clips", []))
                        for clip in clips:
                            try:
                                dur = float(ffprobe_duration(Path(clip)) or 0.0)
                            except Exception:
                                dur = 0.0
                            try:
                                sz_mb = float(os.path.getsize(clip)) / (1024 * 1024) if os.path.exists(clip) else 0.0
                            except Exception:
                                sz_mb = 0.0
                            self.row_added.emit(str(clip), dur, sz_mb)
                    except Exception as e:
                        self.error.emit(str(e))
                        continue
                done += 1
                self.progress.emit(done, max(1, total))
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class VideoDetectScenesTab(QtWidgets.QWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.root_layout = QtWidgets.QHBoxLayout(self)
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[VideoDetectScenesWorker] = None
        self._is_running: bool = False
        self.mode_label_to_key = {
            "智能": "general",
            "电商": "ecommerce",
            "游戏": "game",
            "娱乐": "entertainment",
            "mv广告": "mv_ad",
            "访谈": "interview",
            "教程": "tutorial",
        }
        self.mode_key_to_label = {v: k for k, v in self.mode_label_to_key.items()}
        self._build_page()

    def is_running(self) -> bool:
        return bool(self._is_running)

    def request_stop(self) -> None:
        try:
            if self._is_running and self._worker:
                self._worker.stop()
        except Exception:
            pass

    def _build_page(self) -> None:
        left = self._build_left_panel()
        right = self._build_right_panel()
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)
        try:
            splitter.handle(1).setEnabled(False)
        except Exception:
            pass
        self.root_layout.setContentsMargins(6, 6, 6, 6)
        self.root_layout.addWidget(splitter)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(10)

        group1 = QtWidgets.QGroupBox("视频目录")
        g1 = QtWidgets.QVBoxLayout(group1)
        g1.setContentsMargins(10, 8, 10, 8)
        g1.setSpacing(8)

        self.video_list = QtWidgets.QListWidget()
        self.video_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.video_list.setMinimumHeight(5 * 22)
        btns_row = QtWidgets.QHBoxLayout()
        btn_add = QtWidgets.QPushButton("添加目录")
        btn_rm = QtWidgets.QPushButton("移除选中")
        btn_add.clicked.connect(self._on_add_dir)
        btn_rm.clicked.connect(self._on_remove_selected)
        btns_row.addWidget(btn_add)
        btns_row.addWidget(btn_rm)
        g1.addWidget(QtWidgets.QLabel("输入目录："))
        g1.addWidget(self.video_list)
        g1.addLayout(btns_row)

        out_row = QtWidgets.QHBoxLayout()
        self.output_dir_edit = QtWidgets.QLineEdit()
        btn_out = QtWidgets.QPushButton("浏览…")
        btn_out.clicked.connect(self._on_browse_output)
        out_row.addWidget(QtWidgets.QLabel("输出目录"), 0)
        out_row.addWidget(self.output_dir_edit, 1)
        out_row.addWidget(btn_out)
        g1.addLayout(out_row)

        group2 = QtWidgets.QGroupBox("切片参数")
        g2 = QtWidgets.QFormLayout(group2)
        g2.setContentsMargins(10, 8, 10, 8)
        g2.setSpacing(8)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["智能", "电商", "游戏", "娱乐", "mv广告", "访谈", "教程"])
        self.mode_combo.setCurrentText("智能")
        g2.addRow("场景模式", self.mode_combo)

        self.threshold_spin = QtWidgets.QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 1.0)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setValue(0.5)
        g2.addRow("切点阈值", self.threshold_spin)

        vsplit = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        vsplit.addWidget(group1)
        vsplit.addWidget(group2)
        spacer = QtWidgets.QWidget()
        try:
            spacer.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass
        vsplit.addWidget(spacer)
        vsplit.setStretchFactor(0, 0)
        vsplit.setStretchFactor(1, 0)
        vsplit.setStretchFactor(2, 1)
        vbox.addWidget(vsplit)
        return panel

    def _build_right_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(10)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.start_btn = QtWidgets.QPushButton("开始")
        self.start_btn.clicked.connect(self._on_start_clicked)
        self._apply_progressbar_style(chunk_color=theme.PRIMARY_BLUE)
        self._apply_button_style()

        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(self.progress_bar, 1)
        hb.addWidget(self.start_btn)
        vbox.addLayout(hb)

        self.results_table = QtWidgets.QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["文件输出路径", "时长", "文件大小"])
        hh = self.results_table.horizontalHeader()
        try:
            hh.setStretchLastSection(False)
            hh.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        except Exception:
            pass
        try:
            self.results_table.installEventFilter(self)
        except Exception:
            pass
        self._apply_results_table_column_widths()
        self.results_table.itemDoubleClicked.connect(self._on_open_item)
        vbox.addWidget(self.results_table, 1)
        return panel

    def _apply_progressbar_style(self, chunk_color: str = theme.PRIMARY_BLUE) -> None:
        try:
            self._control_height = getattr(self, "_control_height", theme.BUTTON_HEIGHT)
            height = int(getattr(self, "_control_height", theme.BUTTON_HEIGHT))
            style = theme.build_progressbar_stylesheet(height=height, chunk_color=chunk_color)
            self.progress_bar.setStyleSheet(style)
        except Exception:
            pass

    def _apply_button_style(self) -> None:
        try:
            height = int(getattr(self, "_control_height", theme.BUTTON_HEIGHT))
            idle_style = theme.build_button_stylesheet(
                height=height,
                bg_color=theme.PRIMARY_BLUE,
                hover_color=theme.PRIMARY_BLUE_HOVER,
                disabled_bg=theme.PRIMARY_BLUE_DISABLED,
                radius=theme.BUTTON_RADIUS,
                pad_h=theme.BUTTON_PADDING_HORIZONTAL,
                pad_v=theme.BUTTON_PADDING_VERTICAL,
            )
            running_style = theme.build_button_stylesheet(
                height=height,
                bg_color=theme.DANGER_RED,
                hover_color=theme.DANGER_RED_HOVER,
                disabled_bg=theme.DANGER_RED_DISABLED,
                radius=theme.BUTTON_RADIUS,
                pad_h=theme.BUTTON_PADDING_HORIZONTAL,
                pad_v=theme.BUTTON_PADDING_VERTICAL,
            )
            self.start_btn.setStyleSheet(idle_style)
            self.start_btn.setMinimumHeight(height)
            self._idle_btn_style = idle_style
            self._running_btn_style = running_style
        except Exception:
            pass

    def _on_add_dir(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择视频目录")
        if not d:
            return
        existing = {self.video_list.item(i).text() for i in range(self.video_list.count())}
        if d not in existing:
            self.video_list.addItem(d)

    def _on_remove_selected(self) -> None:
        items = self.video_list.selectedItems()
        for it in items:
            row = self.video_list.row(it)
            self.video_list.takeItem(row)

    def _on_browse_output(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.output_dir_edit.setText(d)

    def _reset_table(self) -> None:
        try:
            self.results_table.setRowCount(0)
        except Exception:
            pass

    def _on_open_item(self, item: QtWidgets.QTableWidgetItem) -> None:
        try:
            r = item.row()
            p = self.results_table.item(r, 0).text()
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(p))
        except Exception:
            pass

    def _on_start_clicked(self) -> None:
        try:
            if self._is_running:
                self.request_stop()
                return
            try:
                app = QtWidgets.QApplication.instance()
                if not (bool(run_preflight_checks(app)) if app is not None else False):
                    return
            except Exception:
                return

            dirs = [self.video_list.item(i).text() for i in range(self.video_list.count())]
            if not dirs:
                QtWidgets.QMessageBox.warning(self, "提示", "请添加至少一个视频目录")
                return
            out_root = self.output_dir_edit.text().strip()
            mode_label = self.mode_combo.currentText().strip()
            profile = self.mode_label_to_key.get(mode_label, "general")
            threshold = float(self.threshold_spin.value())

            self._reset_table()
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0 / %d" % len(dirs))
            self._thread = QtCore.QThread(self)
            self._worker = VideoDetectScenesWorker()
            self._worker.moveToThread(self._thread)
            self._worker.progress.connect(self._on_progress)
            self._worker.row_added.connect(self._on_row_added)
            self._worker.finished.connect(self._on_finished)
            self._worker.error.connect(self._on_error)
            self._worker.start.connect(self._worker.run)
            self._thread.started.connect(lambda: self._worker.start.emit(dirs, out_root, profile, threshold))
            self._is_running = True
            try:
                self.start_btn.setText("停止")
                self.start_btn.setStyleSheet(self._running_btn_style)
            except Exception:
                pass
            self._thread.start()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", str(e))

    def _on_progress(self, done: int, total: int) -> None:
        try:
            total = max(1, int(total))
            done = max(0, int(done))
            pct = int(done * 100 / total)
            self.progress_bar.setValue(pct)
            self.progress_bar.setFormat(f"{done} / {total}")
        except Exception:
            pass

    def _on_row_added(self, path: str, duration: float, size_mb: float) -> None:
        try:
            r = self.results_table.rowCount()
            self.results_table.insertRow(r)
            self.results_table.setItem(r, 0, QtWidgets.QTableWidgetItem(str(path)))
            self.results_table.setItem(r, 1, QtWidgets.QTableWidgetItem(f"{duration:.2f}s"))
            self.results_table.setItem(r, 2, QtWidgets.QTableWidgetItem(f"{size_mb:.1f} MB"))
        except Exception:
            pass

    def _on_finished(self) -> None:
        self._is_running = False
        try:
            self.start_btn.setText("开始")
            self.start_btn.setStyleSheet(self._idle_btn_style)
        except Exception:
            pass
        try:
            if self._thread:
                self._thread.quit()
                self._thread.wait()
        except Exception:
            pass

    def _apply_results_table_column_widths(self) -> None:
        try:
            w = max(200, int(self.results_table.width()))
            self.results_table.setColumnWidth(0, int(w * 0.70))
            self.results_table.setColumnWidth(1, int(w * 0.15))
            self.results_table.setColumnWidth(2, int(w * 0.15))
        except Exception:
            pass

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        try:
            if obj is self.results_table and event.type() == QtCore.QEvent.Resize:
                self._apply_results_table_column_widths()
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _on_error(self, text: str) -> None:
        QtWidgets.QMessageBox.critical(self, "错误", str(text))
        self._on_finished()
