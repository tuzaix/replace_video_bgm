"""
Video BGM Replace Tab

提供“BGM替换”标签页，左/右面板布局：
- 左侧：视频列表管理、BGM选择、输出目录、参数设置
- 右侧：进度条与开始/停止按钮、结果列表

后台并发调用 video_tool.bgm_replacer.bgm_replacer 完成替换。
"""

from __future__ import annotations

from typing import Optional, List, Tuple
import os
import threading
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6 import QtWidgets, QtCore, QtGui
from gui.utils import theme
from gui.precheck import run_preflight_checks

def _is_video_file(path: str) -> bool:
    """判断是否为常见视频文件。"""
    ext = os.path.splitext(path)[1].lower()
    return ext in {".mp4", ".mov", ".mkv", ".avi", ".webm"}


def _is_audio_file(path: str) -> bool:
    """判断是否为常见音频文件。"""
    ext = os.path.splitext(path)[1].lower()
    return ext in {".mp3", ".aac", ".wav", ".flac", ".m4a", ".ogg"}


def _open_file_in_os(path: str) -> None:
    """双击打开生成的文件。"""
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
    except Exception:
        pass


class BgmReplaceWorker(QtCore.QObject):
    """后台并发执行 BGM 替换任务。"""

    phase = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    finished = QtCore.Signal()
    error = QtCore.Signal(str)
    row_added = QtCore.Signal(str, float, int)
    start = QtCore.Signal(list, str, str, bool, float, float, int)

    def __init__(self) -> None:
        super().__init__()
        self._stopping = False

    def stop(self) -> None:
        """请求停止，已在执行的任务自然结束。"""
        self._stopping = True

    @QtCore.Slot(list, str, str, bool, float, float, int)
    def run(self, videos: List[str], bgm_path_or_dir: str, output_dir: str, keep_voice: bool, voice_gain: float, bgm_gain: float, threads: int) -> None:
        """执行替换任务。"""
        try:
            from video_tool.bgm_replacer import bgm_replacer
        except Exception as e:
            self.error.emit(f"导入后端失败: {e}")
            return

        self.phase.emit("准备任务…")
        videos = [v for v in videos if _is_video_file(v) and os.path.isfile(v)]
        total = len(videos)
        self.progress.emit(0, total)
        done_lock = threading.Lock()
        done = 0

        def resolve_bgm(bgm: str) -> Optional[str]:
            if not bgm:
                return None
            if os.path.isdir(bgm):
                try:
                    for name in os.listdir(bgm):
                        p = os.path.join(bgm, name)
                        if os.path.isfile(p) and _is_audio_file(p):
                            return p
                except Exception:
                    return None
                return None
            if os.path.isfile(bgm) and _is_audio_file(bgm):
                return bgm
            return None

        bgm_resolved = resolve_bgm(bgm_path_or_dir)
        if bgm_resolved is None:
            self.error.emit("未选择有效的背景音乐文件或目录")
            return

        def _emit_progress():
            with done_lock:
                self.progress.emit(done, total)

        def process_one(vpath: str) -> Tuple[bool, str, float, int]:
            try:
                parent = os.path.dirname(vpath)
                default_out = os.path.join(parent, "BGM替换")
                user_out = str(output_dir or "").strip()
                use_out = user_out if user_out else default_out
                try:
                    os.makedirs(use_out, exist_ok=True)
                except Exception:
                    pass
                out_path = bgm_replacer(
                    video_path=vpath,
                    bgm_path=bgm_resolved,
                    output_dir=use_out,
                    keep_original_voice=keep_voice,
                    original_volume=voice_gain,
                    bgm_volume=bgm_gain,
                    device="gpu",
                )
                if out_path and os.path.isfile(str(out_path)):
                    # 轻量获取时长与大小
                    try:
                        from moviepy.editor import VideoFileClip
                        clip = VideoFileClip(str(out_path))
                        dur = float(clip.duration or 0.0)
                        clip.close()
                    except Exception:
                        dur = 0.0
                    size = 0
                    try:
                        size = int(os.path.getsize(str(out_path)))
                    except Exception:
                        pass
                    return True, str(out_path), dur, size
            except Exception as e:
                self.error.emit(f"处理失败: {e}")
            return False, "", 0.0, 0

        self.phase.emit("执行替换…")
        try:
            with ThreadPoolExecutor(max_workers=max(1, int(threads))) as ex:
                futures = [ex.submit(process_one, v) for v in videos]
                for f in as_completed(futures):
                    if self._stopping:
                        break
                    ok, path, dur, size = f.result()
                    if ok:
                        try:
                            self.row_added.emit(path, dur, size)
                        except Exception:
                            pass
                    with done_lock:
                        done += 1
                    _emit_progress()
        except Exception as e:
            self.error.emit(f"并发执行失败: {e}")
            return

        self.progress.emit(total, total)
        self.finished.emit()


class VideoBgmReplaceTab(QtWidgets.QWidget):
    """“BGM替换”标签页。"""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.root_layout = QtWidgets.QHBoxLayout(self)
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[BgmReplaceWorker] = None
        self._is_running: bool = False
        self._build_page()

    def _build_page(self) -> None:
        """构建左右面板并放入 splitter。"""
        left = self._build_left_panel()
        right = self._build_right_panel()
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)
        self.root_layout.setContentsMargins(6, 6, 6, 6)
        self.root_layout.addWidget(splitter)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        """左侧参数面板。"""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)

        group1 = QtWidgets.QGroupBox("视频目录")
        g1 = QtWidgets.QVBoxLayout(group1)
        # a.1 视频目录：列表 + 按钮
        self.video_list = QtWidgets.QListWidget()
        self.video_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        row_btns = QtWidgets.QHBoxLayout()
        btn_add_dir = QtWidgets.QPushButton("添加目录…")
        btn_remove_sel = QtWidgets.QPushButton("移除选中")
        btn_add_dir.clicked.connect(self._on_add_dir)
        btn_remove_sel.clicked.connect(self._on_remove_selected)
        row_btns.addWidget(btn_add_dir)
        row_btns.addWidget(btn_remove_sel)
        g1.addWidget(self.video_list)
        g1.addLayout(row_btns)

        # a.2 背景音乐：行编辑 + 浏览
        row_bgm = QtWidgets.QHBoxLayout()
        lbl_bgm = QtWidgets.QLabel("背景音乐")
        self.bgm_edit = QtWidgets.QLineEdit()
        self.bgm_edit.setPlaceholderText("选择音频文件或包含音频的目录…")
        btn_browse_bgm = QtWidgets.QPushButton("浏览…")
        btn_browse_bgm.clicked.connect(self._on_browse_bgm)
        row_bgm.addWidget(lbl_bgm, 0)
        row_bgm.addWidget(self.bgm_edit, 1)
        row_bgm.addWidget(btn_browse_bgm)
        g1.addLayout(row_bgm)

        # a.3 合成输出：行编辑 + 浏览（目录，可为空）
        row_out = QtWidgets.QHBoxLayout()
        lbl_out = QtWidgets.QLabel("合成输出")
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText("为空：输出到每个视频父目录下的 BGM替换")
        btn_browse_out = QtWidgets.QPushButton("浏览…")
        btn_browse_out.clicked.connect(self._on_browse_output)
        row_out.addWidget(lbl_out, 0)
        row_out.addWidget(self.output_edit, 1)
        row_out.addWidget(btn_browse_out)
        g1.addLayout(row_out)

        layout.addWidget(group1)

        # b. 参数组
        group2 = QtWidgets.QGroupBox("去除BGM参数")
        g2 = QtWidgets.QVBoxLayout(group2)

        self.keep_voice_cb = QtWidgets.QCheckBox("保留原声")
        self.keep_voice_cb.setChecked(True)
        g2.addWidget(self.keep_voice_cb)

        row_style = QtWidgets.QHBoxLayout()
        row_style.addWidget(QtWidgets.QLabel("风格"), 0)
        self.style_combo = QtWidgets.QComboBox()
        self.style_combo.addItems(["视频解说/纪录片", "音乐陪音(歌曲)", "KTV/直播演唱", "广播稿合播音", "自定义"])
        self.style_hint = QtWidgets.QLabel("")
        try:
            self.style_combo.currentIndexChanged.connect(self._on_style_changed)
        except Exception:
            pass
        row_style.addWidget(self.style_combo, 1)
        row_style.addWidget(self.style_hint)
        g2.addLayout(row_style)

        row_voice = QtWidgets.QHBoxLayout()
        row_voice.addWidget(QtWidgets.QLabel("原声音量(dB)"), 0)
        self.voice_min_label = QtWidgets.QLabel("-12")
        self.voice_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.voice_slider.setRange(-12, 6)
        self.voice_slider.setValue(0)
        self.voice_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.voice_slider.setTickInterval(1)
        self.voice_max_label = QtWidgets.QLabel("+6")
        self.voice_val_label = QtWidgets.QLabel("0 dB")
        try:
            self.voice_slider.valueChanged.connect(self._on_voice_slider_changed)
        except Exception:
            pass
        row_voice.addWidget(self.voice_min_label)
        row_voice.addWidget(self.voice_slider, 1)
        row_voice.addWidget(self.voice_max_label)
        row_voice.addWidget(self.voice_val_label)
        g2.addLayout(row_voice)

        row_bgmv = QtWidgets.QHBoxLayout()
        row_bgmv.addWidget(QtWidgets.QLabel("BGM音量(dB)"), 0)
        self.bgm_min_label = QtWidgets.QLabel("10%")
        self.bgm_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.bgm_slider.setRange(10, 60)
        self.bgm_slider.setValue(25)
        self.bgm_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.bgm_slider.setTickInterval(5)
        self.bgm_max_label = QtWidgets.QLabel("60%")
        self.bgm_val_label = QtWidgets.QLabel("0.25")
        try:
            self.bgm_slider.valueChanged.connect(self._on_bgm_slider_changed)
        except Exception:
            pass
        row_bgmv.addWidget(self.bgm_min_label)
        row_bgmv.addWidget(self.bgm_slider, 1)
        row_bgmv.addWidget(self.bgm_max_label)
        row_bgmv.addWidget(self.bgm_val_label)
        g2.addLayout(row_bgmv)

        row_threads = QtWidgets.QHBoxLayout()
        row_threads.addWidget(QtWidgets.QLabel("并发数量"), 0)
        self.threads_spin = QtWidgets.QSpinBox()
        self.threads_spin.setRange(1, 32)
        self.threads_spin.setValue(2)
        row_threads.addWidget(self.threads_spin, 1)
        g2.addLayout(row_threads)

        layout.addWidget(group2)
        try:
            self._on_style_changed()
        except Exception:
            pass
        layout.addStretch(1)
        return container

    def _build_right_panel(self) -> QtWidgets.QWidget:
        """右侧结果面板。"""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)

        # a. 进度与开始/停止
        status_group = QtWidgets.QGroupBox("运行状态")
        status_vbox = QtWidgets.QVBoxLayout(status_group)
        status_vbox.setContentsMargins(8, 8, 8, 8)
        status_vbox.setSpacing(8)

        row_ctl = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        try:
            self.progress.setTextVisible(True)
            self.progress.setFormat("0 | 0")
        except Exception:
            pass
        self.btn_start = QtWidgets.QPushButton("开始")
        self.btn_start.clicked.connect(self._on_toggle_start)
        try:
            self._apply_progressbar_style(chunk_color=theme.PRIMARY_BLUE)
            self._apply_action_button_style(running=False)
        except Exception:
            pass
        row_ctl.addWidget(self.progress, 1)
        row_ctl.addWidget(self.btn_start)
        status_vbox.addLayout(row_ctl)
        layout.addWidget(status_group)

        # b. 结果列表
        result_group = QtWidgets.QGroupBox("执行结果")
        result_vbox = QtWidgets.QVBoxLayout(result_group)
        result_vbox.setContentsMargins(8, 8, 8, 8)
        result_vbox.setSpacing(8)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["输出路径", "时长(秒)", "大小(MB)"])
        try:
            hdr = self.table.horizontalHeader()
            hdr.setStretchLastSection(False)
            hdr.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
            self._adjust_table_columns()
        except Exception:
            pass
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self._on_open_selected)
        result_vbox.addWidget(self.table, 1)
        layout.addWidget(result_group)

        return container

    def _on_add_dir(self) -> None:
        """添加视频目录到列表，仅展示目录路径。"""
        dir_ = QtWidgets.QFileDialog.getExistingDirectory(self, "选择视频目录")
        if not dir_:
            return
        try:
            # 去重：避免重复添加同一目录
            existing = {self.video_list.item(i).text() for i in range(self.video_list.count())}
            if dir_ not in existing:
                self.video_list.addItem(dir_)
        except Exception:
            pass

    def _on_remove_selected(self) -> None:
        """移除选中的视频条目。"""
        for it in self.video_list.selectedItems():
            row = self.video_list.row(it)
            self.video_list.takeItem(row)

    def _on_browse_bgm(self) -> None:
        """选择 BGM 文件或目录（弹出菜单，交互与混剪页一致）。"""
        menu = QtWidgets.QMenu(self)
        act_file = menu.addAction("选择音频文件…")
        act_dir = menu.addAction("选择目录…")
        action = menu.exec(QtGui.QCursor.pos())
        if action == act_file:
            fname, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "选择音频文件",
                "",
                "音频文件 (*.mp3 *.aac *.m4a *.wav *.flac);;所有文件 (*.*)",
            )
            if fname:
                self.bgm_edit.setText(fname)
        elif action == act_dir:
            dname = QtWidgets.QFileDialog.getExistingDirectory(self, "选择包含音频的目录")
            if dname:
                self.bgm_edit.setText(dname)

    def _on_browse_output(self) -> None:
        """选择输出目录（可为空）。"""
        dir_ = QtWidgets.QFileDialog.getExistingDirectory(self, "选择输出目录")
        if dir_:
            self.output_edit.setText(dir_)

    def _on_toggle_start(self) -> None:
        """开始/停止按钮。"""
        if not self._is_running:
            self._start_tasks()
        else:
            self._stop_tasks()

    def _start_tasks(self) -> None:
        """启动后台任务。"""
        try:
            app = QtWidgets.QApplication.instance()
            if not (bool(run_preflight_checks(app)) if app is not None else False):
                return
        except Exception:
            return
        dirs = [self.video_list.item(i).text() for i in range(self.video_list.count())]
        if not dirs:
            QtWidgets.QMessageBox.warning(self, "提示", "请先添加至少一个视频目录")
            return
        # 从目录收集视频文件（不递归）
        videos: list[str] = []
        try:
            for d in dirs:
                if os.path.isdir(d):
                    for name in os.listdir(d):
                        p = os.path.join(d, name)
                        if os.path.isfile(p) and _is_video_file(p):
                            videos.append(p)
        except Exception:
            pass
        if not videos:
            QtWidgets.QMessageBox.warning(self, "提示", "所选目录中未找到视频文件")
            return
        try:
            self.table.setRowCount(0)
            self.progress.setMaximum(100)
            self.progress.setValue(0)
            self.progress.setFormat("0 | 0")
        except Exception:
            pass
        bgm = self.bgm_edit.text().strip()
        if not bgm:
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择背景音乐（文件或目录）")
            return
        keep_voice = self.keep_voice_cb.isChecked()
        voice_gain_db = float(self.voice_slider.value())
        voice_gain = 10 ** (voice_gain_db / 20.0)
        bgm_gain = float(self.bgm_slider.value()) / 100.0
        threads = int(self.threads_spin.value())

        self._worker = BgmReplaceWorker()
        self._thread = QtCore.QThread(self)
        self._worker.moveToThread(self._thread)
        self._worker.start.connect(self._worker.run)
        out_dir = self.output_edit.text().strip()
        self._thread.started.connect(lambda: self._worker.start.emit(videos, bgm, out_dir, keep_voice, voice_gain, bgm_gain, threads))
        self._worker.progress.connect(self._on_progress)
        self._worker.row_added.connect(self._on_row_added)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._thread.start()
        self._is_running = True
        self.btn_start.setText("停止")
        try:
            self._apply_action_button_style(running=True)
        except Exception:
            pass

    def _stop_tasks(self) -> None:
        """停止后台任务。"""
        if self._worker:
            try:
                self._worker.stop()
            except Exception:
                pass
        if self._thread:
            try:
                self._thread.quit()
                self._thread.wait(2000)
            except Exception:
                pass
        self._is_running = False
        self.btn_start.setText("开始")
        try:
            self._apply_action_button_style(running=False)
        except Exception:
            pass

    def _on_progress(self, done: int, total: int) -> None:
        """更新进度条。"""
        try:
            t = max(0, int(total))
            d = max(0, int(done))
            self.progress.setMaximum(t)
            self.progress.setValue(min(d, t))
            self.progress.setFormat(f"{d} | {t}")
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

    def _on_finished(self) -> None:
        """任务完成。"""
        self._stop_tasks()
        try:
            out_dir = self.output_edit.text().strip()
        except Exception:
            out_dir = ""
        if not out_dir:
            try:
                if self.table.rowCount() > 0:
                    pitem = self.table.item(0, 0)
                    if pitem:
                        out_dir = os.path.dirname(pitem.text())
            except Exception:
                pass
        try:
            if out_dir and os.path.isdir(out_dir):
                ret = QtWidgets.QMessageBox.question(
                    self,
                    "完成",
                    f"BGM替换已完成。是否打开输出目录？\n{out_dir}",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.Yes,
                )
                if ret == QtWidgets.QMessageBox.Yes:
                    _open_file_in_os(out_dir)
            else:
                QtWidgets.QMessageBox.information(self, "完成", "BGM替换已完成")
        except Exception:
            QtWidgets.QMessageBox.information(self, "完成", "BGM替换已完成")

    def _on_error(self, msg: str) -> None:
        """错误提示。"""
        QtWidgets.QMessageBox.warning(self, "错误", msg)

    def _on_open_selected(self, item: QtWidgets.QTableWidgetItem) -> None:
        """双击打开选中文件。"""
        r = item.row()
        pitem = self.table.item(r, 0)
        if not pitem:
            return
        _open_file_in_os(pitem.text())

    def _on_voice_slider_changed(self, v: int) -> None:
        """更新原声音量显示。"""
        try:
            self.voice_val_label.setText(f"{int(v)} dB")
            self._maybe_set_style_custom_due_to_slider()
        except Exception:
            pass

    def _on_bgm_slider_changed(self, v: int) -> None:
        """更新 BGM 音量显示。"""
        try:
            vv = max(0, min(100, int(v)))
            self.bgm_val_label.setText(f"{vv/100:.2f}")
            self._maybe_set_style_custom_due_to_slider()
        except Exception:
            pass

    def _on_style_changed(self) -> None:
        try:
            name = self.style_combo.currentText().strip()
        except Exception:
            name = ""
        presets = {
            "视频解说/纪录片": (0.90, 0.25),
            "音乐陪音(歌曲)": (0.60, 0.40),
            "KTV/直播演唱": (0.60, 0.40),
            "广播稿合播音": (0.80, 0.20),
        }
        try:
            if name in presets:
                vr, br = presets[name]
                db = int(round(20.0 * math.log10(max(0.0001, float(vr)))))
                db = max(-5, min(5, db))
                bv = int(round(float(br) * 100.0))
                bv = max(0, min(100, bv))
                self._syncing_style = True
                try:
                    self.voice_slider.setValue(db)
                    self.bgm_slider.setValue(bv)
                    self.voice_val_label.setText(f"{db} dB")
                    self.bgm_val_label.setText(f"{br:.2f}")
                finally:
                    self._syncing_style = False
                try:
                    self.style_hint.setText(f"人声≈{int(vr*100)}%  BGM≈{int(br*100)}%")
                except Exception:
                    pass
            else:
                try:
                    self.style_hint.setText("自定义")
                except Exception:
                    pass
        except Exception:
            pass

    def _maybe_set_style_custom_due_to_slider(self) -> None:
        try:
            if getattr(self, "_syncing_style", False):
                return
            name = self.style_combo.currentText().strip()
            presets = {
                "视频解说/纪录片": (0.90, 0.25),
                "音乐陪音(歌曲)": (0.60, 0.40),
                "KTV/直播演唱": (0.60, 0.40),
                "广播稿合播音": (0.80, 0.20),
            }
            if name in presets:
                vr, br = presets[name]
                db_expect = int(round(20.0 * math.log10(max(0.0001, float(vr)))))
                bv_expect = int(round(float(br) * 100.0))
                if int(self.voice_slider.value()) != db_expect or int(self.bgm_slider.value()) != bv_expect:
                    idx = self.style_combo.findText("自定义")
                    if idx >= 0:
                        self.style_combo.setCurrentIndex(idx)
        except Exception:
            pass

    def _adjust_table_columns(self) -> None:
        """按比例调整结果表三列的宽度。"""
        try:
            total = max(1, int(self.table.viewport().width()))
            w0 = int(total * 0.70)
            w1 = int(total * 0.15)
            w2 = int(total * 0.15)
            self.table.setColumnWidth(0, w0)
            self.table.setColumnWidth(1, w1)
            self.table.setColumnWidth(2, w2)
        except Exception:
            pass

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        """窗口尺寸变化时同步调整表格列宽比例。"""
        try:
            self._adjust_table_columns()
        except Exception:
            pass
        super().resizeEvent(event)

    # --- 样式与尺寸（与生成封面页保持一致） ---
    def _apply_progressbar_style(self, chunk_color: str = theme.PRIMARY_BLUE) -> None:
        """统一设置进度条的尺寸与样式。"""
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
        """根据运行状态统一设置单按钮的高度与样式。"""
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
            pb_font = self.progress.font() if getattr(self, "progress", None) is not None else None
            if pb_font is not None:
                self.btn_start.setFont(pb_font)
            self.btn_start.setStyleSheet(running_style if running else idle_style)
            self.btn_start.setFixedHeight(height)
        except Exception:
            pass