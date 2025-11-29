"""
直播切片标签页（UI 与逻辑）

布局与 extract_frames_tab.py 一致，分为左右两个面板：

- 左侧面板：
  1) group1（视频目录）
     - 视频目录：QListWidget（默认显示 5 行，支持上下滚动与多选）
     - 按钮：添加目录、移除选中
     - 输出目录：QLineEdit + 浏览（仅目录；为空则为每个视频同名目录）
  2) group2（切片参数）
     - 场景模式：下拉选择（ecommerce/game/entertainment）
     - 模型目录：QLineEdit + 浏览（仅目录；不能为空）
     - 问号提示：从链接下载模型文件到本地（Florence-2 与 faster-whisper）

- 右侧面板：
  - 顶部进度条 + 开始/停止按钮（互斥状态）
  - 下方结果表（文件输出路径、时长、文件大小），支持双击打开文件

运行逻辑：
- 不使用并发，在后台线程顺序处理视频。
- 直接调用 video_tool.broadcast_video_slices.BroadcastVideoSlices，参数按 CLI 默认值传入。
"""

from __future__ import annotations

from typing import Optional, List, Tuple
import os
from PySide6 import QtWidgets, QtCore, QtGui

from gui.utils import theme
from gui.precheck import run_preflight_checks
from video_tool.broadcast_video_slices import BroadcastVideoSlices
from utils.calcu_video_info import ffprobe_duration


def _open_in_os(path: str) -> None:
    """在操作系统中打开指定路径。"""
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
    except Exception:
        pass


class BroadcastVideoSlicesWorker(QtCore.QObject):
    """后台顺序执行直播切片任务的工作器。"""

    phase = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    row_added = QtCore.Signal(str, float, int)
    finished = QtCore.Signal(int)
    error = QtCore.Signal(str)
    start = QtCore.Signal(list, str, str, str, bool, bool, str, int)

    def __init__(self) -> None:
        super().__init__()
        self._stopping: bool = False

    def stop(self) -> None:
        """发起软停止请求。"""
        self._stopping = True

    @QtCore.Slot(list, str, str, str, bool, bool, str, int)
    def run(self, video_dirs: List[str], output_root: str, models_root: str, mode: str, add_subtitles: bool, translate: bool, language: str, max_chars_per_line: int) -> None:
        """顺序执行切片任务。

        参数
        ----
        video_dirs: 输入视频目录列表
        output_root: 输出根目录（为空则每视频同名目录在原位置）
        models_root: 模型基础目录（包含 faster_wishper 与 florence2 子目录）
        mode: 切片场景模式：ecommerce/game/entertainment
        """
        try:
            app = QtWidgets.QApplication.instance()
            ok = bool(run_preflight_checks(app)) if app is not None else False
            if not ok:
                try:
                    QtWidgets.QMessageBox.warning(self, "未授权或环境不满足", "未授权或环境不满足，无法开始")
                except Exception:
                    pass
                return
    
            # 统计所有视频文件
            videos: List[str] = []
            for d in video_dirs:
                try:
                    if not d:
                        continue
                    if os.path.isdir(d):
                        for f in os.listdir(d):
                            if f.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
                                videos.append(os.path.join(d, f))
                    elif os.path.isfile(d) and d.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
                        videos.append(d)
                except Exception:
                    continue

            total = len(videos)
            self.progress.emit(0, total)
            done = 0

            # 实例化切片器
            slicer = BroadcastVideoSlices(model_size="large-v3", device="auto", models_root=models_root)

            for vp in videos:
                if self._stopping:
                    break
                try:
                    base = os.path.splitext(os.path.basename(vp))[0]
                    if output_root:
                        out_dir = os.path.join(output_root, base)
                    else:
                        out_dir = os.path.join(os.path.dirname(os.path.abspath(vp)), base)
                    try:
                        os.makedirs(out_dir, exist_ok=True)
                    except Exception:
                        pass

                    kwargs = {
                        "language": language,
                        "use_nvenc": False,
                        "crf": 23,
                        "add_subtitles": bool(add_subtitles),
                        "translate": bool(translate),
                        "max_chars_per_line": int(max_chars_per_line),
                        "vision_verify": True,
                    }
                    outs = slicer.cut_video(video_path=vp, output_dir=out_dir, mode=mode, **kwargs)
                    for outp in outs:
                        try:
                            dur = float(ffprobe_duration(outp) or 0.0)
                        except Exception:
                            dur = 0.0
                        try:
                            size = int(os.path.getsize(outp))
                        except Exception:
                            size = 0
                        self.row_added.emit(outp, dur, size)
                except Exception as e:
                    self.error.emit(f"处理失败: {e}")
                finally:
                    done += 1
                    self.progress.emit(done, total)

            self.finished.emit(done)
        except Exception as e:
            self.error.emit(str(e))


class BroadcastVideoSlicesTab(QtWidgets.QWidget):
    """“直播切片”标签页。"""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.root_layout = QtWidgets.QHBoxLayout(self)
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[BroadcastVideoSlicesWorker] = None
        self._is_running: bool = False
        # 场景模式中英映射：显示中文，内部传英文键
        self.mode_label_to_key = {"电商": "ecommerce", "游戏": "game", "娱乐": "entertainment"}
        self.mode_key_to_label = {v: k for k, v in self.mode_label_to_key.items()}
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
        """构建整页布局：左右面板组合。"""
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
        """构建左侧参数面板。"""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        group1 = QtWidgets.QGroupBox("视频目录")
        gl1 = QtWidgets.QVBoxLayout(group1)
        gl1.setContentsMargins(10, 8, 10, 8)
        gl1.setSpacing(8)

        gl1.addWidget(QtWidgets.QLabel("输入路径："))
        self.video_list = QtWidgets.QListWidget()
        self.video_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.video_list.setMinimumHeight(5 * 22)
        gl1.addWidget(self.video_list)
        row_btns = QtWidgets.QHBoxLayout()
        btn_add = QtWidgets.QPushButton("添加目录")
        btn_add_file = QtWidgets.QPushButton("添加文件")
        btn_rm = QtWidgets.QPushButton("移除选中")
        btn_add.clicked.connect(self._on_add_dir)
        btn_add_file.clicked.connect(self._on_add_file)
        btn_rm.clicked.connect(self._on_remove_selected)
        row_btns.addWidget(btn_add)
        row_btns.addWidget(btn_add_file)
        row_btns.addWidget(btn_rm)
        gl1.addLayout(row_btns)

        row_out = QtWidgets.QHBoxLayout()
        row_out.addWidget(QtWidgets.QLabel("输出目录："), 0)
        self.output_dir_edit = QtWidgets.QLineEdit()
        self.output_dir_edit.setPlaceholderText("留空则为每视频同名目录")
        btn_browse_out = QtWidgets.QPushButton("浏览…")
        btn_browse_out.clicked.connect(self._on_browse_output_dir)
        row_out.addWidget(self.output_dir_edit, 1)
        row_out.addWidget(btn_browse_out)
        gl1.addLayout(row_out)

        group2 = QtWidgets.QGroupBox("切片参数")
        gl2 = QtWidgets.QVBoxLayout(group2)
        gl2.setContentsMargins(10, 8, 10, 8)
        gl2.setSpacing(8)

        row_mode = QtWidgets.QHBoxLayout()
        row_mode.addWidget(QtWidgets.QLabel("场景模式："), 0)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["电商", "游戏", "娱乐"])
        self.mode_combo.setCurrentText("电商")
        row_mode.addWidget(self.mode_combo, 1)
        gl2.addLayout(row_mode)

        row_model = QtWidgets.QHBoxLayout()
        row_model.addWidget(QtWidgets.QLabel("模型目录："), 0)
        self.model_dir_edit = QtWidgets.QLineEdit()
        self.model_dir_edit.setPlaceholderText("选择模型基础目录（含 faster_wishper 与 florence2）")
        btn_browse_model = QtWidgets.QPushButton("浏览…")
        btn_browse_model.clicked.connect(self._on_browse_model_dir)
        help_btn = QtWidgets.QToolButton()
        help_btn.setText("?")
        help_btn.setFixedSize(22, 22)
        help_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        help_btn.setToolTip("从 https://github.com/SYSTRAN/faster-whisper 与 https://huggingface.co/microsoft/Florence-2-base 下载模型到本地")
        row_model.addWidget(self.model_dir_edit, 1)
        row_model.addWidget(btn_browse_model)
        row_model.addWidget(help_btn)
        gl2.addLayout(row_model)

        row_subs = QtWidgets.QHBoxLayout()
        self.add_subs_chk = QtWidgets.QCheckBox("叠加字幕")
        self.add_subs_chk.setChecked(True)
        self.translate_chk = QtWidgets.QCheckBox("翻译为英文")
        self.translate_chk.setChecked(False)
        row_subs.addWidget(self.add_subs_chk)
        row_subs.addWidget(self.translate_chk)
        gl2.addLayout(row_subs)

        row_lang = QtWidgets.QHBoxLayout()
        row_lang.addWidget(QtWidgets.QLabel("识别语言："), 0)
        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.addItems(["zh", "en"])
        self.lang_combo.setCurrentText("zh")
        row_lang.addWidget(self.lang_combo, 1)
        gl2.addLayout(row_lang)

        row_cpl = QtWidgets.QHBoxLayout()
        row_cpl.addWidget(QtWidgets.QLabel("每行最大字符数："), 0)
        self.cpl_spin = QtWidgets.QSpinBox()
        self.cpl_spin.setRange(8, 28)
        self.cpl_spin.setValue(14)
        row_cpl.addWidget(self.cpl_spin, 1)
        gl2.addLayout(row_cpl)

        layout.addWidget(group1)
        layout.addWidget(group2)
        return container

    def _build_right_panel(self) -> QtWidgets.QWidget:
        """构建右侧运行面板。"""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("0 / 0")
        self.action_btn = QtWidgets.QPushButton("开始")
        self._apply_progressbar_style(chunk_color=theme.PRIMARY_BLUE)
        self._apply_action_button_style(False)
        self.action_btn.clicked.connect(self._on_action_clicked)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.progress_bar)
        row.addWidget(self.action_btn)
        group_run = QtWidgets.QGroupBox("运行状态")
        gpl = QtWidgets.QVBoxLayout(group_run)
        gpl.setContentsMargins(6, 6, 6, 6)
        gpl.addLayout(row)
        layout.addWidget(group_run)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["文件输出路径", "时长(秒)", "文件大小(MB)"])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.cellDoubleClicked.connect(self._on_table_double_clicked)
        layout.addWidget(self.table, 1)
        return container

    def _apply_progressbar_style(self, chunk_color: str = theme.PRIMARY_BLUE) -> None:
        """统一设置进度条样式与尺寸。"""
        try:
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
        """统一设置开始/停止按钮样式。"""
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
        try:
            pb_font = self.progress_bar.font() if getattr(self, "progress_bar", None) is not None else None
            if pb_font is not None:
                self.action_btn.setFont(pb_font)
            self.action_btn.setStyleSheet(running_style if running else idle_style)
            self.action_btn.setFixedHeight(height)
            self.action_btn.setText("停止" if running else "开始")
            self.action_btn.setToolTip("点击停止" if running else "点击开始")
        except Exception:
            pass

    def _on_add_dir(self) -> None:
        """添加视频目录到列表。"""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择视频目录")
        if not d:
            return
        try:
            existing = {self.video_list.item(i).text() for i in range(self.video_list.count())}
            if d not in existing:
                self.video_list.addItem(d)
        except Exception:
            pass

    def _on_add_file(self) -> None:
        """添加具体视频文件到列表（支持多选）。"""
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*)",
        )
        if not files:
            return
        try:
            existing = {self.video_list.item(i).text() for i in range(self.video_list.count())}
            for f in files:
                if not f:
                    continue
                # 基本扩展名判断
                ext = os.path.splitext(f)[1].lower()
                if ext in {".mp4", ".avi", ".mov", ".mkv"}:
                    if f not in existing:
                        self.video_list.addItem(f)
        except Exception:
            pass

    def _on_remove_selected(self) -> None:
        """移除列表中选中的目录。"""
        try:
            rows = self.video_list.selectedIndexes()
            for idx in sorted(rows, key=lambda x: x.row(), reverse=True):
                self.video_list.takeItem(idx.row())
        except Exception:
            pass

    def _on_browse_output_dir(self) -> None:
        """选择输出根目录。"""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.output_dir_edit.setText(d)

    def _on_browse_model_dir(self) -> None:
        """选择模型基础目录。"""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择模型基础目录")
        if d:
            self.model_dir_edit.setText(d)

    def _on_action_clicked(self) -> None:
        """开始或停止任务。"""
        if not self._is_running:
            self._start()
        else:
            self.request_stop()

    def _start(self) -> None:
        """启动后台线程并执行任务。"""
        # 收集参数（目录或视频文件路径）
        inputs = [self.video_list.item(i).text() for i in range(self.video_list.count())]
        if not inputs:
            QtWidgets.QMessageBox.warning(self, "提示", "请先添加至少一个视频目录")
            return
        models_root = self.model_dir_edit.text().strip()
        if not models_root or not os.path.isdir(models_root):
            QtWidgets.QMessageBox.warning(self, "提示", "请指定有效的模型基础目录")
            return
        out_root = self.output_dir_edit.text().strip()
        mode_label = self.mode_combo.currentText().strip()
        mode = self.mode_label_to_key.get(mode_label, "ecommerce")

        self._reset_table()
        self.progress_bar.setFormat("0 / %d" % len(inputs))
        self.progress_bar.setValue(0)
        self._thread = QtCore.QThread(self)
        self._worker = BroadcastVideoSlicesWorker()
        self._worker.moveToThread(self._thread)
        self._worker.progress.connect(self._on_progress)
        self._worker.row_added.connect(self._on_row_added)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start.connect(self._worker.run)
        self._thread.started.connect(lambda: self._worker.start.emit(
            inputs,
            out_root,
            models_root,
            mode,
            bool(self.add_subs_chk.isChecked()),
            bool(self.translate_chk.isChecked()),
            str(self.lang_combo.currentText()),
            int(self.cpl_spin.value()),
        ))
        self._thread.start()
        self._is_running = True
        self._apply_action_button_style(True)

    def _on_progress(self, done: int, total: int) -> None:
        """更新进度显示。"""
        try:
            self.progress_bar.setFormat(f"{done} / {total}")
            pct = int(0 if total <= 0 else (done * 100.0 / total))
            self.progress_bar.setValue(pct)
        except Exception:
            pass

    def _on_row_added(self, path: str, dur: float, size: int) -> None:
        """加入一行结果。"""
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(path))
        self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(f"{dur:.2f}"))
        mb = (size / (1024.0 * 1024.0)) if size > 0 else 0.0
        self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(f"{mb:.2f}"))
        try:
            vw = self.table.viewport().width()
            self.table.setColumnWidth(0, int(vw * 0.70))
            self.table.setColumnWidth(1, int(vw * 0.15))
            self.table.setColumnWidth(2, int(vw * 0.15))
        except Exception:
            pass

    def _on_finished(self, done: int) -> None:
        """任务完成后的复位。"""
        self._cleanup_thread()
        self._is_running = False
        self._apply_action_button_style(False)
        QtWidgets.QMessageBox.information(self, "完成", f"处理完成：{done}")

    def _on_error(self, msg: str) -> None:
        """错误处理并复位按钮。"""
        QtWidgets.QMessageBox.critical(self, "错误", msg)
        self._cleanup_thread()
        self._is_running = False
        self._apply_action_button_style(False)

    def _cleanup_thread(self) -> None:
        """线程收尾。"""
        try:
            if self._thread:
                try:
                    self._worker.stop()
                except Exception:
                    pass
                try:
                    self._thread.quit()
                    self._thread.wait(1500)
                except Exception:
                    pass
        finally:
            self._thread = None
            self._worker = None

    def _reset_table(self) -> None:
        """清空结果表。"""
        try:
            self.table.setRowCount(0)
        except Exception:
            pass

    def _on_table_double_clicked(self, row: int, col: int) -> None:
        """双击打开当前行文件。"""
        try:
            p = self.table.item(row, 0).text()
            if p:
                _open_in_os(p)
        except Exception:
            pass
