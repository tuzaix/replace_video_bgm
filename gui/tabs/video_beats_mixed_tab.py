"""
Video Beats Mixed Tab

提供“卡点混剪”标签页，左右分栏布局与 `extract_frames_tab.py` 保持一致：

- 左侧面板：
  1) group1（视频目录）
     - 视频目录：QListWidget（5 行可滚动，多选）及“添加目录/移除选中”按钮
     - 合成输出：QLineEdit + 浏览（可为空，空则默认使用 `<音频文件父目录>/BGM替换`）
  2) group2（卡点采集参数，用于 `video_tool/beats_checkpoint.py` 生成元数据）
     - 音频目录：QListWidget（5 行可滚动，多选）及“添加目录/移除选中”按钮
     - 卡点窗口时长(秒)：QSlider（默认 15，范围 10–45）
     - 卡点采集间隔模式：QComboBox（default/fast/slow/dynamic）+ QDoubleSpinBox（默认 0.25，范围 0.25–3）
       dynamic 模式禁用间隔数值框
  3) group3（混剪参数）
     - 混剪视频数：QSpinBox（默认 10）
     - 并发数量：QSpinBox（默认 4）

- 右侧面板：
  - 进度条 + 开始/停止按钮（互斥，样式使用项目统一主题）
  - 结果表：文件输出路径、时长、文件大小（双击打开）

业务流程：
1) 扫描“音频目录”中的音频文件，为每个音频调用 `beats_checkpoint` 生成/更新卡点元数据；
2) 根据选择的“视频目录”汇总素材文件（视频/图片），调用 `video_beats_mixed` 生成卡点混剪视频；
3) 支持并发执行与软停止，结果列表实时更新。
"""

from __future__ import annotations

from typing import Optional, List, Tuple, Dict
import os
import json
import pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6 import QtWidgets, QtCore, QtGui

from gui.utils import theme
from gui.precheck import run_preflight_checks


def _is_audio_file(name: str) -> bool:
    """判断是否为常见音频文件。"""
    ext = os.path.splitext(name)[1].lower()
    return ext in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def _is_video_file(name: str) -> bool:
    """判断是否为常见视频文件。"""
    ext = os.path.splitext(name)[1].lower()
    return ext in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v"}


def _is_image_file(name: str) -> bool:
    """判断是否为常见图片文件。"""
    ext = os.path.splitext(name)[1].lower()
    return ext in {".jpg", ".jpeg", ".png", ".bmp"}


class VideoBeatsMixedWorker(QtCore.QObject):
    """后台执行卡点采集与混剪生成的工作器。"""

    phase = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    finished = QtCore.Signal(list)
    error = QtCore.Signal(str)
    start = QtCore.Signal(list, list, str, int, int, str, float, float)

    def __init__(self) -> None:
        super().__init__()
        self._stopping = False

    def stop(self) -> None:
        """请求软停止。"""
        self._stopping = True

    @QtCore.Slot(list, list, str, int, int, str, float, float)
    def run(self, video_dirs: List[str], audio_dirs: List[str], output_dir: str, out_count: int, threads: int, mode: str, min_interval: float, clip_seconds: float) -> None:
        """后台线程执行：采集卡点并生成混剪视频。"""
        try:
            from video_tool.beats_checkpoint import beats_checkpoint
            from video_tool.video_beats_mixed import video_beats_mixed
        except Exception as e:
            self.error.emit(f"导入逻辑失败: {e}")
            return

        self.phase.emit("Scanning media…")
        audio_files: List[pathlib.Path] = []
        for d in audio_dirs:
            try:
                for name in os.listdir(d):
                    if _is_audio_file(name):
                        p = pathlib.Path(d) / name
                        if p.is_file():
                            audio_files.append(p)
            except Exception:
                continue

        media_files: List[pathlib.Path] = []
        for d in video_dirs:
            try:
                for name in os.listdir(d):
                    if _is_video_file(name) or _is_image_file(name):
                        p = pathlib.Path(d) / name
                        if p.is_file():
                            media_files.append(p)
            except Exception:
                continue

        if not audio_files:
            self.error.emit("未在音频目录中找到音频文件")
            return
        if not media_files:
            self.error.emit("未在视频目录中找到可用素材文件")
            return

        total_tasks = len(audio_files) * max(1, int(out_count))
        self.progress.emit(0, total_tasks)

        results: List[str] = []
        done = 0

        def _emit_progress() -> None:
            self.progress.emit(done, total_tasks)

        self.phase.emit("Generating beats meta…")
        metas: Dict[str, Dict] = {}
        for a in audio_files:
            if self._stopping:
                self.error.emit("任务已取消")
                return
            try:
                json_path = beats_checkpoint(
                    str(a),
                    output_dir=None,
                    temp_dir=None,
                    mode=mode,
                    min_interval=(None if mode == "dynamic" else float(min_interval)),
                    clip_duration=float(clip_seconds),
                )
            except Exception as e:
                self.error.emit(f"卡点采集失败: {e}")
                return
            meta_obj: Dict = {}
            try:
                if json_path and pathlib.Path(json_path).exists():
                    with open(json_path, "r", encoding="utf-8") as f:
                        meta_obj = json.load(f)
            except Exception:
                meta_obj = {}
            metas[str(a)] = meta_obj

        self.phase.emit("Mixing videos…")

        def process_one(audio_path: pathlib.Path, meta_obj: Dict) -> Optional[str]:
            nonlocal done
            if self._stopping:
                return None
            picks = [str(p) for p in media_files]
            try:
                h = (meta_obj.get("suggestion", {}) or {}).get("highlight", {}) or {}
                s = float(h.get("start_time", 0.0))
                e_raw = h.get("end_time", None)
                e = float(e_raw) if (e_raw is not None) else float(s + max(10.0, clip_seconds))
                if e <= s:
                    e = float(s + max(10.0, clip_seconds))
            except Exception:
                s, e = 0.0, max(clip_seconds, 10.0)
            out_dir_final = output_dir
            if not out_dir_final:
                out_dir_final = str(audio_path.parent / "BGM替换")
            try:
                os.makedirs(out_dir_final, exist_ok=True)
            except Exception:
                pass
            res = video_beats_mixed(
                audio_path=str(audio_path),
                beats_meta=meta_obj,
                media_files=picks,
                output_dir=out_dir_final,
                window=(s, e),
                clip_min_interval=(None if mode == "dynamic" else float(min_interval)),
            )
            done += 1
            _emit_progress()
            return str(res) if res else None

        max_workers = max(1, int(threads))
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = []
                for a in audio_files:
                    meta_obj = metas.get(str(a), {})
                    for k in range(max(1, int(out_count))):
                        futures.append(ex.submit(process_one, a, meta_obj))
                for f in as_completed(futures):
                    if self._stopping:
                        break
                    try:
                        p = f.result()
                        if p:
                            results.append(p)
                    except Exception:
                        continue
        except Exception as e:
            self.error.emit(f"并发混剪失败: {e}")
            return

        self.phase.emit("完成")
        self.progress.emit(total_tasks, total_tasks)
        self.finished.emit(results)


class VideoBeatsMixedTab(QtWidgets.QWidget):
    """“卡点混剪”标签页。"""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.root_layout = QtWidgets.QHBoxLayout(self)
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[VideoBeatsMixedWorker] = None
        self._is_running: bool = False
        self._preflight_passed: bool = False
        self._build_page()

    def is_running(self) -> bool:
        """返回当前是否处于运行态。"""
        return bool(self._is_running)

    def request_stop(self) -> None:
        """请求停止当前任务（软停止）。"""
        try:
            if self._worker:
                self._worker.stop()
        except Exception:
            pass

    def _build_page(self) -> None:
        """构建整页布局：左右面板通过 splitter 组织。"""
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
        """构建左侧参数面板。"""
        # group1：视频目录与输出
        group1 = QtWidgets.QGroupBox("视频目录")
        gl1 = QtWidgets.QVBoxLayout(group1)
        gl1.setContentsMargins(10, 8, 10, 8)
        gl1.setSpacing(8)

        self.video_dirs_list = QtWidgets.QListWidget()
        self.video_dirs_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.video_dirs_list.setMinimumHeight(5 * 22)
        gl1.addWidget(QtWidgets.QLabel("视频目录："))
        gl1.addWidget(self.video_dirs_list)

        row_v_btns = QtWidgets.QHBoxLayout()
        btn_add_v = QtWidgets.QPushButton("添加目录")
        btn_rm_v = QtWidgets.QPushButton("移除选中")
        btn_add_v.clicked.connect(self._on_add_video_dir)
        btn_rm_v.clicked.connect(self._on_remove_selected_video_dirs)
        row_v_btns.addWidget(btn_add_v)
        row_v_btns.addWidget(btn_rm_v)
        gl1.addLayout(row_v_btns)

        row_out = QtWidgets.QHBoxLayout()
        row_out.addWidget(QtWidgets.QLabel("合成输出："), 0)
        self.output_dir_edit = QtWidgets.QLineEdit()
        self.output_dir_edit.setPlaceholderText("为空则默认 <音频文件父目录>/BGM替换")
        btn_browse_out = QtWidgets.QPushButton("浏览…")
        btn_browse_out.clicked.connect(self._on_browse_output_dir)
        row_out.addWidget(self.output_dir_edit, 1)
        row_out.addWidget(btn_browse_out)
        gl1.addLayout(row_out)

        # group2：卡点采集参数
        group2 = QtWidgets.QGroupBox("卡点采集参数")
        gl2 = QtWidgets.QVBoxLayout(group2)
        gl2.setContentsMargins(10, 8, 10, 8)
        gl2.setSpacing(8)

        self.audio_dirs_list = QtWidgets.QListWidget()
        self.audio_dirs_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.audio_dirs_list.setMinimumHeight(5 * 22)
        gl2.addWidget(QtWidgets.QLabel("音频目录："))
        gl2.addWidget(self.audio_dirs_list)

        row_a_btns = QtWidgets.QHBoxLayout()
        btn_add_a = QtWidgets.QPushButton("添加目录")
        btn_rm_a = QtWidgets.QPushButton("移除选中")
        btn_add_a.clicked.connect(self._on_add_audio_dir)
        btn_rm_a.clicked.connect(self._on_remove_selected_audio_dirs)
        row_a_btns.addWidget(btn_add_a)
        row_a_btns.addWidget(btn_rm_a)
        gl2.addLayout(row_a_btns)

        row_high = QtWidgets.QHBoxLayout()
        row_high.addWidget(QtWidgets.QLabel("卡点窗口时长(秒)："), 0)
        self.highlight_min_label = QtWidgets.QLabel("10")
        self.highlight_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.highlight_slider.setRange(10, 45)
        self.highlight_slider.setValue(15)
        # 刻度显示与间隔
        try:
            self.highlight_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
            self.highlight_slider.setTickInterval(5)
        except Exception:
            pass
        self.highlight_max_label = QtWidgets.QLabel("45")
        self.highlight_value = QtWidgets.QLabel("15")
        self.highlight_slider.valueChanged.connect(lambda v: self.highlight_value.setText(str(v)))
        row_high.addWidget(self.highlight_min_label)
        row_high.addWidget(self.highlight_slider, 1)
        row_high.addWidget(self.highlight_max_label)
        row_high.addWidget(self.highlight_value)
        gl2.addLayout(row_high)

        row_mode = QtWidgets.QHBoxLayout()
        row_mode.addWidget(QtWidgets.QLabel("间隔模式："), 0)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["default", "fast", "slow", "dynamic"])
        self.interval_spin = QtWidgets.QDoubleSpinBox()
        self.interval_spin.setRange(0.25, 3.0)
        self.interval_spin.setSingleStep(0.05)
        self.interval_spin.setValue(0.25)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        row_mode.addWidget(self.mode_combo)
        row_mode.addWidget(QtWidgets.QLabel("间隔(秒)："))
        row_mode.addWidget(self.interval_spin)
        gl2.addLayout(row_mode)

        # group3：混剪参数
        group3 = QtWidgets.QGroupBox("混剪参数")
        gl3 = QtWidgets.QVBoxLayout(group3)
        gl3.setContentsMargins(10, 8, 10, 8)
        gl3.setSpacing(8)

        row_cnt = QtWidgets.QHBoxLayout()
        row_cnt.addWidget(QtWidgets.QLabel("混剪视频数："), 0)
        self.count_spin = QtWidgets.QSpinBox()
        self.count_spin.setRange(1, 100)
        self.count_spin.setValue(10)
        row_cnt.addWidget(self.count_spin, 1)
        gl3.addLayout(row_cnt)

        row_thr = QtWidgets.QHBoxLayout()
        row_thr.addWidget(QtWidgets.QLabel("并发数量："), 0)
        self.threads_spin = QtWidgets.QSpinBox()
        self.threads_spin.setRange(1, 32)
        self.threads_spin.setValue(4)
        row_thr.addWidget(self.threads_spin, 1)
        gl3.addLayout(row_thr)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        try:
            group1.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            group2.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            group3.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        except Exception:
            pass
        splitter.addWidget(group1)
        splitter.addWidget(group2)
        splitter.addWidget(group3)
        widget = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(splitter)
        return widget

    def _build_right_panel(self) -> QtWidgets.QWidget:
        """构建右侧状态与结果面板。"""
        panel = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(panel)
        vl.setContentsMargins(10, 8, 10, 8)
        vl.setSpacing(8)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setStyleSheet(theme.build_progressbar_stylesheet(height=18))

        self.run_btn = QtWidgets.QPushButton("开始")
        self.run_btn.setStyleSheet(theme.build_button_stylesheet(height=theme.BUTTON_HEIGHT, bg_color=theme.PRIMARY_BLUE, hover_color=theme.PRIMARY_BLUE_HOVER))
        self.run_btn.clicked.connect(self._on_toggle_run)

        row_top = QtWidgets.QHBoxLayout()
        row_top.addWidget(self.progress, 1)
        row_top.addWidget(self.run_btn)
        vl.addLayout(row_top)

        self.result_table = QtWidgets.QTableWidget(0, 3)
        self.result_table.setHorizontalHeaderLabels(["输出文件", "时长(s)", "大小(MB)"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.result_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.result_table.doubleClicked.connect(self._on_open_selected_result)

        vl.addWidget(self.result_table, 1)
        return panel

    def _on_mode_changed(self, text: str) -> None:
        """间隔模式联动：dynamic 模式禁用间隔框，并设置默认值。"""
        mapping = {"default": 0.33, "fast": 0.25, "slow": 0.60}
        if text == "dynamic":
            self.interval_spin.setEnabled(False)
            try:
                self.interval_spin.setValue(mapping["default"])  # 仅展示，不参与提交
            except Exception:
                pass
        else:
            self.interval_spin.setEnabled(True)
            try:
                self.interval_spin.setValue(mapping.get(text, 0.33))
            except Exception:
                pass

    def _on_add_video_dir(self) -> None:
        """添加一个视频目录到列表。"""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择视频目录")
        if d:
            self.video_dirs_list.addItem(d)

    def _on_remove_selected_video_dirs(self) -> None:
        """移除视频目录列表中选中的项。"""
        for item in self.video_dirs_list.selectedItems():
            row = self.video_dirs_list.row(item)
            self.video_dirs_list.takeItem(row)

    def _on_add_audio_dir(self) -> None:
        """添加一个音频目录到列表。"""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择音频目录")
        if d:
            self.audio_dirs_list.addItem(d)

    def _on_remove_selected_audio_dirs(self) -> None:
        """移除音频目录列表中选中的项。"""
        for item in self.audio_dirs_list.selectedItems():
            row = self.audio_dirs_list.row(item)
            self.audio_dirs_list.takeItem(row)

    def _on_browse_output_dir(self) -> None:
        """选择输出目录。"""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.output_dir_edit.setText(d)

    def _on_open_selected_result(self) -> None:
        """双击打开选中的结果文件。"""
        try:
            items = self.result_table.selectedItems()
            if not items:
                return
            path = items[0].text()
            if path:
                os.startfile(path)
        except Exception:
            pass

    def _on_toggle_run(self) -> None:
        """开始或停止任务。"""
        if not self._is_running:
            try:
                ok, msg = run_preflight_checks()
            except Exception:
                ok, msg = True, ""
            if not ok:
                QtWidgets.QMessageBox.warning(self, "环境检查失败", str(msg or "请检查运行环境"))
                return
            self._start()
        else:
            self.request_stop()
            self._is_running = False
            self.run_btn.setText("开始")

    def _start(self) -> None:
        """启动后台任务线程并绑定信号。"""
        v_dirs = [self.video_dirs_list.item(i).text() for i in range(self.video_dirs_list.count())]
        a_dirs = [self.audio_dirs_list.item(i).text() for i in range(self.audio_dirs_list.count())]
        out_dir = self.output_dir_edit.text().strip()
        out_count = int(self.count_spin.value())
        threads = int(self.threads_spin.value())
        mode = self.mode_combo.currentText().strip()
        min_interval = float(self.interval_spin.value())
        clip_seconds = float(self.highlight_slider.value())

        if not v_dirs or not a_dirs:
            QtWidgets.QMessageBox.warning(self, "提示", "请至少选择一个视频目录和一个音频目录")
            return
        if not out_dir:
            QtWidgets.QMessageBox.warning(self, "提示", "合成输出目录不能为空")
            return

        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            pass

        # xprint 提交参数，便于调试
        try:
            params = {
                "video_dirs": v_dirs,
                "audio_dirs": a_dirs,
                "output_dir": out_dir,
                "out_count": out_count,
                "threads": threads,
                "mode": mode,
                "min_interval": (None if mode == "dynamic" else float(min_interval)),
                "clip_seconds": clip_seconds,
            }
            print("[xprint] video_beats_mixed_tab submit:", json.dumps(params, ensure_ascii=False))
        except Exception:
            pass

        self.result_table.setRowCount(0)
        self.progress.setValue(0)
        self.run_btn.setText("停止")
        self._is_running = True

        self._thread = QtCore.QThread(self)
        self._worker = VideoBeatsMixedWorker()
        self._worker.moveToThread(self._thread)
        self._worker.start.connect(self._worker.run)
        self._thread.started.connect(lambda: self._worker.start.emit(v_dirs, a_dirs, out_dir, out_count, threads, mode, min_interval, clip_seconds))
        self._worker.phase.connect(lambda t: self.progress.setFormat(f"{t} %p%"))
        self._worker.progress.connect(self._on_progress)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        """根据完成数量更新进度条。"""
        val = 0
        try:
            if total > 0:
                val = int(round(100.0 * done / total))
        except Exception:
            val = 0
        self.progress.setValue(val)

    def _on_error(self, msg: str) -> None:
        """错误处理：弹窗提示并复位 UI。"""
        QtWidgets.QMessageBox.critical(self, "错误", str(msg or "未知错误"))
        self._reset_state()

    def _on_finished(self, paths: List[str]) -> None:
        """生成完成：填充结果表并复位。"""
        for p in paths:
            try:
                dur = 0.0
                try:
                    from moviepy.editor import VideoFileClip
                    c = VideoFileClip(p)
                    dur = float(c.duration or 0.0)
                    try:
                        c.close()
                    except Exception:
                        pass
                except Exception:
                    dur = 0.0
                size_mb = 0.0
                try:
                    size_mb = float(os.path.getsize(p)) / (1024.0 * 1024.0)
                except Exception:
                    size_mb = 0.0
                row = self.result_table.rowCount()
                self.result_table.insertRow(row)
                self.result_table.setItem(row, 0, QtWidgets.QTableWidgetItem(p))
                self.result_table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{dur:.1f}"))
                self.result_table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{size_mb:.2f}"))
            except Exception:
                continue
        self._reset_state()

    def _reset_state(self) -> None:
        """复位运行状态与按钮文本，清理线程。"""
        self._is_running = False
        self.run_btn.setText("开始")
        try:
            if self._worker:
                self._worker.stop()
        except Exception:
            pass
        try:
            if self._thread:
                self._thread.quit()
                self._thread.wait(1000)
        except Exception:
            pass