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
from moviepy.editor import VideoFileClip

from gui.utils import theme
from utils.xprint import xprint
from gui.precheck import run_preflight_checks
from utils.calcu_video_info import get_resolution_dir_topn, confirm_resolution_dir
from utils.common_utils import is_audio_file, is_video_file, is_image_file
from video_tool.beats_checkpoint import beats_checkpoint
from video_tool.video_beats_mixed import video_beats_mixed

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
            pass
        except Exception as e:
            self.error.emit(f"导入逻辑失败: {e}")
            return

        self.phase.emit("检测音频文件")
        audio_files: List[pathlib.Path] = []
        for d in audio_dirs:
            try:
                if os.path.isfile(d) and is_audio_file(os.path.basename(d)):
                    audio_files.append(pathlib.Path(d))
                elif os.path.isdir(d):
                    for name in os.listdir(d):
                        if is_audio_file(name):
                            p = pathlib.Path(d) / name
                            if p.is_file():
                                audio_files.append(p)
            except Exception:
                continue
        
        self.phase.emit("检测视频/图片素材")
        media_files: List[pathlib.Path] = []
        # 确认每个视频目录下是否有归一化完成的素材
        confirm_normalized_dirs = {}
        for d in video_dirs:
            try:
                confirm_normalized_dirs[d] = confirm_resolution_dir(d) # 检查是否有预处理的内容
                if not confirm_normalized_dirs[d]:
                    continue
                media_data = get_resolution_dir_topn(d, top_n=1, media_type="all", recursive=False)
                files = media_data.get("files", []) if isinstance(media_data, dict) else []
                for p in files:
                    if isinstance(p, pathlib.Path) and p.is_file():
                        media_files.append(p)
            except Exception:
                # 回退：若筛分失败则尝试直接收集目录下文件
                try:
                    for name in os.listdir(d):
                        if  is_video_file(name) or is_image_file(name):
                            p = pathlib.Path(d) / name
                            if p.is_file():
                                media_files.append(p)
                except Exception:
                    continue

        # 检查是否有目录下没有归一化完成的素材
        if not any(confirm_normalized_dirs.values()):
            # 确认归一化目录下是否有视频/图片素材
            normalized_dirs = [d for d, confirm in confirm_normalized_dirs.items() if not confirm]
            not_normalized_dirs_str = "\n".join(normalized_dirs)
            self.error.emit(f"下面视频目录未找到预处理的素材\n\n{not_normalized_dirs_str}\n\n请先点击【视频预处理】进行处理")
            return

        if not audio_files:
            self.error.emit("未在音频输入中找到音频文件")
            return
        if not media_files:
            self.error.emit("未在视频目录中找到可用素材文件")
            return

        total_tasks = max(1, int(out_count))
        self.progress.emit(0, total_tasks)

        results: List[str] = []
        done = 0

        def _emit_progress() -> None:
            self.progress.emit(done, total_tasks)

        self.phase.emit("生成音频卡点")
        metas: Dict[str, Dict] = {}
        meta_done = 0
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
            meta_done += 1
            try:
                self.progress.emit(meta_done, len(audio_files))
            except Exception:
                pass

        self.phase.emit("混剪视频")

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
                out_dir_final = str(audio_path.parent / "卡点")
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
                assigned: List[pathlib.Path] = []
                for idx in range(total_tasks):
                    a = audio_files[idx % len(audio_files)]
                    assigned.append(a)
                for a in assigned:
                    meta_obj = metas.get(str(a), {})
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
        self.mode_label_to_key: Dict[str, str] = {
            "默认": "default",
            "快速": "fast",
            "缓慢": "slow",
            "动态": "dynamic",
        }
        self.mode_key_to_label: Dict[str, str] = {v: k for k, v in self.mode_label_to_key.items()}
        self.mode_interval_map: Dict[str, float] = {
            "default": 0.33,
            "fast": 0.25,
            "slow": 0.60,
        }
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
        self.output_dir_edit.setPlaceholderText("不能为空")
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
        gl2.addWidget(QtWidgets.QLabel("音频输入："))
        gl2.addWidget(self.audio_dirs_list)

        row_a_btns = QtWidgets.QHBoxLayout()
        btn_add_a_dir = QtWidgets.QPushButton("添加目录")
        btn_add_a_file = QtWidgets.QPushButton("添加文件")
        btn_rm_a = QtWidgets.QPushButton("移除选中")
        btn_add_a_dir.clicked.connect(self._on_add_audio_dir)
        btn_add_a_file.clicked.connect(self._on_add_audio_file)
        btn_rm_a.clicked.connect(self._on_remove_selected_audio_dirs)
        row_a_btns.addWidget(btn_add_a_dir)
        row_a_btns.addWidget(btn_add_a_file)
        row_a_btns.addWidget(btn_rm_a)
        gl2.addLayout(row_a_btns)

        row_high = QtWidgets.QHBoxLayout()
        row_high.addWidget(QtWidgets.QLabel("窗口时长(秒)："), 0)
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
        self.highlight_slider.valueChanged.connect(self._on_highlight_changed)
        row_high.addWidget(self.highlight_min_label)
        row_high.addWidget(self.highlight_slider, 1)
        row_high.addWidget(self.highlight_max_label)
        row_high.addWidget(self.highlight_value)
        gl2.addLayout(row_high)

        row_mode = QtWidgets.QHBoxLayout()
        label_mode = QtWidgets.QLabel("采点模式：")
        row_mode.addWidget(label_mode, 0)
        self.mode_combo = QtWidgets.QComboBox()
        try:
            for lbl, key in self.mode_label_to_key.items():
                self.mode_combo.addItem(lbl, key)
        except Exception:
            self.mode_combo.addItems(list(self.mode_label_to_key.keys()))
        self.interval_spin = QtWidgets.QDoubleSpinBox()
        self.interval_spin.setRange(0.25, 3.0)
        self.interval_spin.setSingleStep(0.05)
        self.interval_spin.setValue(0.33)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        row_mode.addWidget(self.mode_combo, 0)
        row_mode.addSpacing(12)
        label_interval = QtWidgets.QLabel("鼓点间隔(秒)：")
        row_mode.addWidget(label_interval, 0)
        row_mode.addWidget(self.interval_spin, 0)
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
        try:
            self.count_spin.setKeyboardTracking(True)
            self.count_spin.setAccelerated(True)
            self.count_spin.setFocusPolicy(QtCore.Qt.StrongFocus)
        except Exception:
            pass
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


         # a. 进度与开始/停止
        status_group = QtWidgets.QGroupBox("运行状态")
        status_vbox = QtWidgets.QVBoxLayout(status_group)
        status_vbox.setContentsMargins(8, 8, 8, 8)
        status_vbox.setSpacing(8)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        try:
            self.progress.setTextVisible(True)
            self.progress.setFormat("0 | 0")
        except Exception:
            pass

        self.run_btn = QtWidgets.QPushButton("开始")
        self.run_btn.clicked.connect(self._on_toggle_run)

        row_top = QtWidgets.QHBoxLayout()
        row_top.addWidget(self.progress, 1)
        row_top.addWidget(self.run_btn)
        status_vbox.addLayout(row_top)
        vl.addWidget(status_group)
        try:
            self._apply_progressbar_style(chunk_color=theme.PRIMARY_BLUE)
            self._apply_action_button_style(running=False)
        except Exception:
            pass

         # b. 结果列表
        result_group = QtWidgets.QGroupBox("执行结果")
        result_vbox = QtWidgets.QVBoxLayout(result_group)
        result_vbox.setContentsMargins(8, 8, 8, 8)
        result_vbox.setSpacing(8)

        self.result_table = QtWidgets.QTableWidget(0, 3)
        self.result_table.setHorizontalHeaderLabels(["输出文件", "时长(s)", "大小(MB)"])
        try:
            hdr = self.result_table.horizontalHeader()
            hdr.setStretchLastSection(False)
            hdr.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
            self._apply_results_table_column_widths()
            self.result_table.installEventFilter(self)
        except Exception:
            pass
        self.result_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.result_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.result_table.doubleClicked.connect(self._on_open_selected_result)
        result_vbox.addWidget(self.result_table, 1)
        vl.addWidget(result_group)
        return panel

    def _current_mode_key(self) -> str:
        try:
            data = self.mode_combo.currentData()
            if isinstance(data, str) and data:
                return data
        except Exception:
            pass
        t = self.mode_combo.currentText().strip()
        return self.mode_label_to_key.get(t, "default")

    def _on_mode_changed(self, text: str) -> None:
        """间隔模式联动：dynamic 模式禁用间隔框，并设置默认值。"""
        mapping = self.mode_interval_map
        key = self._current_mode_key()
        if key == "dynamic":
            self.interval_spin.setEnabled(False)
            try:
                self.interval_spin.setValue(mapping["default"])  # 仅展示，不参与提交
            except Exception:
                pass
        else:
            self.interval_spin.setEnabled(True)
            try:
                self.interval_spin.setValue(mapping.get(key, 0.33))
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
        """添加一个音频目录到列表（展开目录中的音频文件）。"""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择音频目录")
        if not d:
            return
        try:
            for name in os.listdir(d):
                if is_audio_file(name):
                    p = os.path.join(d, name)
                    if os.path.isfile(p):
                        self.audio_dirs_list.addItem(p)
        except Exception:
            pass

    def _on_add_audio_file(self) -> None:
        """添加一个或多个音频文件到列表。"""
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "选择音频文件", filter="音频文件 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)")
        try:
            for f in files:
                if f and os.path.isfile(f) and is_audio_file(os.path.basename(f)):
                    self.audio_dirs_list.addItem(f)
        except Exception:
            pass

    def _on_highlight_changed(self, v: int) -> None:
        """滑块变更时同步标签并在滑块上显示提示。"""
        try:
            self.highlight_value.setText(str(v))
            pos = self.highlight_slider.mapToGlobal(self.highlight_slider.rect().center())
            QtWidgets.QToolTip.showText(pos, str(v), self.highlight_slider)
        except Exception:
            pass

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
        mode = self._current_mode_key()
        min_interval = float(self.interval_spin.value())
        clip_seconds = float(self.highlight_slider.value())

        try:
            self.count_spin.interpretText()
            out_count = int(self.count_spin.value())
        except Exception:
            pass
        try:
            self.threads_spin.interpretText()
            threads = int(self.threads_spin.value())
        except Exception:
            pass

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
            xprint("[xprint] video_beats_mixed_tab submit:", json.dumps(params, ensure_ascii=False))
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
        self._worker.phase.connect(self._on_phase)
        self._worker.progress.connect(self._on_progress)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        """根据完成数量更新进度条，分阶段加权显示“阶段：完成数 | 总数”。"""
        try:
            start = int(getattr(self, "_phase_start", 0))
            span = int(getattr(self, "_phase_span", 100))
            label = str(getattr(self, "_phase_label", "")) or ""
            if total <= 0:
                self.progress.setValue(start)
                self.progress.setFormat(f"{label}: 0 | 0" if label else "0 | 0")
                return
            ratio = max(0.0, min(1.0, float(done) / float(total)))
            weighted = int(start + span * ratio)
            self.progress.setValue(max(0, min(100, weighted)))
            fmt = f"{int(done)} | {int(total)}"
            self.progress.setFormat(f"{label}: {fmt}" if label else fmt)
        except Exception:
            try:
                self.progress.setValue(0)
                self.progress.setFormat("0 | 0")
            except Exception:
                pass

    def _on_phase(self, name: str) -> None:
        """阶段切换：设置分段权重与初始文本。"""
        try:
            if name in {"meta", "Generating beats meta…"}:
                self._phase_start = 0
                self._phase_span = 30
                self._phase_label = "解析BGM"
                self.progress.setFormat(f"{self._phase_label}: 0 | 0")
            elif name in {"mix", "Mixing videos…"}:
                self._phase_start = 30
                self._phase_span = 70
                self._phase_label = "混剪视频"
                self.progress.setFormat(f"{self._phase_label}: 0 | 0")
            else:
                self._phase_start = 0
                self._phase_span = 100
                self._phase_label = str(name or "")
                self.progress.setFormat(f"{self._phase_label}: 0 | 0" if self._phase_label else "0 | 0")
        except Exception:
            pass

    def _apply_progressbar_style(self, chunk_color: str = theme.PRIMARY_BLUE) -> None:
        """统一设置进度条样式与高度（参考 generate_cover_tab）。"""
        try:
            if self.progress is None:
                return
            self.progress.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            screen = QtWidgets.QApplication.primaryScreen()
            dpi = screen.logicalDotsPerInch() if screen else 96.0
            scale = max(1.0, dpi / 96.0)
        except Exception:
            scale = 1.0
        base_h = 32
        height = int(max(28, min(52, base_h * scale)))
        try:
            self.progress.setFixedHeight(height)
            self._control_height = height
        except Exception:
            try:
                self._control_height = getattr(self, "_control_height", getattr(theme, "BUTTON_HEIGHT", height))
            except Exception:
                self._control_height = height
        try:
            font = self.progress.font()
            base_pt = 11
            pt_size = int(max(base_pt, min(16, base_pt * scale)))
            font.setPointSize(pt_size)
            self.progress.setFont(font)
        except Exception:
            pass
        try:
            style = theme.build_progressbar_stylesheet(height=height, chunk_color=chunk_color)
            self.progress.setStyleSheet(style)
        except Exception:
            pass

    def _apply_action_button_style(self, running: bool) -> None:
        """统一设置开始/停止按钮样式与高度（参考 generate_cover_tab）。"""
        try:
            if self.run_btn is None:
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
            try:
                if self.progress is not None:
                    self.run_btn.setFont(self.progress.font())
            except Exception:
                pass
            self.run_btn.setStyleSheet(running_style if running else idle_style)
            self.run_btn.setFixedHeight(height)
        except Exception:
            pass

    def _apply_results_table_column_widths(self) -> None:
        """按 70%/15%/15% 比例设置结果表列宽。"""
        if not getattr(self, "result_table", None):
            return
        try:
            total = self.result_table.viewport().width() or self.result_table.width()
            w0 = int(total * 0.70)
            w1 = int(total * 0.15)
            w2 = int(total * 0.15)
            self.result_table.setColumnWidth(0, w0)
            self.result_table.setColumnWidth(1, w1)
            self.result_table.setColumnWidth(2, w2)
        except Exception:
            pass

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        try:
            if obj is getattr(self, "result_table", None) and event.type() == QtCore.QEvent.Resize:
                QtCore.QTimer.singleShot(0, self._apply_results_table_column_widths)
        except Exception:
            pass
        try:
            return super().eventFilter(obj, event)
        except Exception:
            return False

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