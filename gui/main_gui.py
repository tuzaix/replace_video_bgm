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
import re
from pathlib import Path
from typing import List, Optional

from PySide6 import QtCore, QtWidgets, QtGui
import subprocess
import shutil
import os

try:
    # PySide6 提供的对象有效性检测工具
    from shiboken6 import isValid as _qt_is_valid  # type: ignore
except Exception:
    _qt_is_valid = None

# Ensure imports work both in development and PyInstaller-frozen runtime.
# In frozen mode, bundled packages are available without modifying sys.path.
# In development mode, add project root so `concat_tool` can be imported.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not getattr(sys, "frozen", False):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

from concat_tool import video_concat as vc  # type: ignore
from concat_tool.workflow import run_video_concat_workflow, WorkflowCallbacks  # type: ignore
from concat_tool.settings import Settings  # type: ignore
from gui.precheck import preflight
from gui.precheck.ffmpeg_paths import (
    resolve_ffmpeg_paths,
    get_ffmpeg_versions,
    detect_nvenc,
)
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
# 预检逻辑已抽象到 gui.precheck.preflight 模块，main_gui 保留调用点即可。

@dataclass
# Settings dataclass moved to concat_tool.settings for reuse by GUI/CLI.


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

        Delegates business logic to concat_tool.workflow.run_video_concat_workflow,
        keeping GUI concerns (signals and stream redirect) isolated.
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
            # Bridge callbacks from workflow to GUI signals
            callbacks = WorkflowCallbacks(
                on_log=self._emit,
                on_phase=self.phase.emit,
                on_progress=self.progress.emit,
                on_error=self.error.emit,
            )

            # Execute business workflow
            success_count, fail_count, success_outputs = run_video_concat_workflow(self.settings, callbacks)

            # Emit finished and results back to GUI
            self.finished.emit(success_count, fail_count)
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


class SpinnerIndicator(QtWidgets.QWidget):
    """
    简易菊花转圈指示器（无第三方资源），用于加载状态提示。

    通过 QTimer 周期性刷新角度，使用 QPainter 绘制 12 段线条，
    根据当前角度设置透明度生成旋转效果。
    """
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, size: int = 64, color: QtGui.QColor | None = None) -> None:
        super().__init__(parent)
        self._size = max(32, size)
        self._angle = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(self._size, self._size)
        self._color = color or QtGui.QColor("#3b82f6")

    def _tick(self) -> None:
        """推进角度并触发重绘。"""
        self._angle = (self._angle + 1) % 12
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        """绘制 12 段旋转线条形成菊花效果。

        注意：确保在 finally 中调用 painter.end()，避免 QBackingStore 错误。
        """
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            cx, cy = self.width() / 2.0, self.height() / 2.0
            radius = self._size / 2.0 - 6.0
            line_len = float(max(6, int(self._size * 0.20)))
            line_w = max(2, int(self._size * 0.06))
            # 使用更健壮的设置方式，避免枚举差异带来的异常
            for i in range(12):
                # 透明度随相对角度递减
                rel = (i - self._angle) % 12
                alpha = int(60 + (195 * (1 - rel / 12.0)))  # 60..255
                color = QtGui.QColor(self._color)
                color.setAlpha(alpha)
                pen = QtGui.QPen(color)
                pen.setWidth(line_w)
                try:
                    pen.setCapStyle(QtCore.Qt.RoundCap)
                except Exception:
                    pass
                painter.setPen(pen)
                # 使用 Python 内置 math 库计算坐标，避免 QtCore.qCos/qSin 不可用
                from math import cos, sin, pi
                theta = (i / 12.0) * 2.0 * pi
                sx = cx + (radius - line_len) * cos(theta)
                sy = cy + (radius - line_len) * sin(theta)
                ex = cx + radius * cos(theta)
                ey = cy + radius * sin(theta)
                painter.drawLine(QtCore.QPointF(sx, sy), QtCore.QPointF(ex, ey))
        finally:
            # 显式结束绘制，避免活动 painter 导致 QBackingStore 报错
            try:
                painter.end()
            except Exception:
                pass


class BusyOverlay(QtWidgets.QWidget):
    """
    半透明蒙层，居中显示菊花转圈与提示文本，用于遮罩“输出结果”区域。

    行为：
    - 显示时阻止底部点击（通过禁用列表实现）；
    - 随父组件大小变化自动调整几何；
    - 无滚动条、无边框，仅视觉遮罩。
    """
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: rgba(0,0,0,96);")
        self.setVisible(False)
        # 居中布局
        vbox = QtWidgets.QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)
        vbox.setAlignment(QtCore.Qt.AlignCenter)
        self.spinner = SpinnerIndicator(self, size=64)
        self.label = QtWidgets.QLabel("处理中…")
        self.label.setStyleSheet("color: white; font-size: 14px; font-weight: 600;")
        vbox.addWidget(self.spinner, 0, QtCore.Qt.AlignCenter)
        vbox.addWidget(self.label, 0, QtCore.Qt.AlignCenter)
        # 拦截父组件 resize 以随动
        parent.installEventFilter(self)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if watched is self.parent() and event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Move):
            try:
                # 填满父组件内容区域
                parent_widget = self.parentWidget()
                if parent_widget is not None:
                    self.setGeometry(parent_widget.rect())
            except Exception:
                pass
        return super().eventFilter(watched, event)


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
        self.start_btn = QtWidgets.QPushButton("开始-混剪")
        self.stop_btn = QtWidgets.QPushButton("停止-混剪")
        self.stop_btn.setEnabled(False)
        # 提高“开始/停止”按钮的高度与字号，采用 DPI 自适应以在高分屏上保持醒目
        try:
            self._apply_action_buttons_style(base_h=38, base_pt=12)
        except Exception:
            # 兜底：固定高度与字号
            try:
                self.start_btn.setFixedHeight(38)
                self.stop_btn.setFixedHeight(38)
                _bf = self.start_btn.font(); _bf.setPointSize(max(12, _bf.pointSize())); self.start_btn.setFont(_bf)
                _bf2 = self.stop_btn.font(); _bf2.setPointSize(max(12, _bf2.pointSize())); self.stop_btn.setFont(_bf2)
            except Exception:
                pass

        # Progress（移除右侧日志框，仅保留阶段与进度条）
        self.phase_label = QtWidgets.QLabel("阶段: ")
        # 进度条：显示百分比文本，并加大高度便于观察
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        # 进度条尺寸策略：横向扩展，纵向固定，避免被压缩成细线
        try:
            self.progress_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass
        # 将文本居中显示，格式为“进度: XX%”
        try:
            self.progress_bar.setAlignment(QtCore.Qt.AlignCenter)
        except Exception:
            pass
        self.progress_bar.setFormat("进度: %p%")
        # 应用 DPI 自适应的进度条样式（高度与字号），默认使用蓝色块
        try:
            self._apply_progress_style(chunk_color="#3b82f6")
        except Exception:
            # 兜底样式（非 DPI 自适应）
            try:
                self.progress_bar.setFixedHeight(40)
                font = self.progress_bar.font()
                font.setPointSize(max(12, font.pointSize()))
                self.progress_bar.setFont(font)
                self.progress_bar.setStyleSheet(
                    "QProgressBar{min-height:40px;max-height:40px;border:1px solid #bbb;border-radius:4px;text-align:center;}"
                    "QProgressBar::chunk{background-color:#3b82f6;margin:0px;}"
                )
            except Exception:
                pass

        # Layout composition — 左右分布与参数分区
        # 左侧：参数设置（按类型分组）；右侧：进度、日志、结果与动作按钮

        # 左侧使用滚动容器以便在窗口较小时也能浏览完整参数
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_container = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_container)
        # 左侧容器在垂直方向可扩展，以便其内部最后一个分组可以“贴底”对齐右侧下方分组
        left_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        # 1) 输入与路径
        input_group = QtWidgets.QGroupBox("输入与路径")
        # 上部分组保持固定高度（根据其内容自适应），不参与剩余空间分配
        input_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        input_form = QtWidgets.QFormLayout()
        input_form.addRow(dir_group)
        input_form.addRow("BGM路径", bgm_hbox)
        input_form.addRow("输出路径", out_hbox)
        input_group.setLayout(input_form)
        left_layout.addWidget(input_group)

        # 2) 基本流程参数（双列布局）
        flow_group = QtWidgets.QGroupBox("基本流程参数")
        flow_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
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
        encode_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
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
        preset_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
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
        # 最下方分组设置为垂直方向可扩展，用于占用剩余空间，从而使其底部与右侧下方分组底部对齐
        trim_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
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

        # 5) 环境状态与概览（按需求移除左侧下方布局，仅保留控件以兼容现有逻辑）
        # 注意：以下控件仍然初始化，以便后台逻辑（环境检测、编码概览更新、按钮回调）不报错，
        # 但不再加入左侧布局，从而在界面上隐藏该区域。
        status_group = QtWidgets.QGroupBox("环境状态")
        status_vbox = QtWidgets.QVBoxLayout()
        status_box = QtWidgets.QHBoxLayout()
        self.ffmpeg_status = QtWidgets.QLabel("ffmpeg: 未检测")
        self.nvenc_status = QtWidgets.QLabel("NVENC: 未检测")
        status_box.addWidget(self.ffmpeg_status)
        status_box.addWidget(self.nvenc_status)
        self.ffmpeg_info_btn = QtWidgets.QPushButton("显示 FFmpeg 版本信息")
        status_box.addWidget(self.ffmpeg_info_btn)
        status_vbox.addLayout(status_box)
        # 概览标签保留但不显示，用于兼容编码参数概览文本更新
        self.enc_summary_label = QtWidgets.QLabel("编码参数概览：")
        status_vbox.addWidget(self.enc_summary_label)
        status_group.setLayout(status_vbox)
        # 不再加入 left_layout，以达到“移除左侧下方的环境状态布局”的视觉效果
        # 注意：为了实现左右两侧“上下分组底部对齐”的视觉效果，这里移除尾部的 addStretch，
        # 通过将 trim_group 设置为垂直 Expanding 来占用剩余空间，从而使其底部贴近容器底部。
        
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

        # 右侧运行区：阶段、进度、动作按钮、结果（移除日志打印框）
        right_container = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_container)

        # 上部：运行状态组（阶段标签 + 进度条），用分组包裹使信息更集中
        progress_group = QtWidgets.QGroupBox("运行状态")
        try:
            # 适度强化标题样式，提升辨识度
            progress_group.setStyleSheet("QGroupBox::title { font-weight: 600; }")
        except Exception:
            pass
        _top_v = QtWidgets.QVBoxLayout(progress_group)
        _top_v.setContentsMargins(10, 8, 10, 8)
        _top_v.setSpacing(8)
        _top_v.addWidget(self.phase_label)
        _top_v.addWidget(self.progress_bar)

        # Toolbar-like action buttons
        btn_box = QtWidgets.QHBoxLayout()
        try:
            btn_box.setContentsMargins(0, 0, 0, 0)
            btn_box.setSpacing(8)
        except Exception:
            pass
        # self.open_out_dir_btn = QtWidgets.QPushButton("打开默认输出目录")
        btn_box.addWidget(self.start_btn)
        btn_box.addWidget(self.stop_btn)
        # 将开始/停止按钮放到上面的进度条分组下方
        _top_v.addLayout(btn_box)
      

        # Results table group（右下：结果以表格形式展示，支持多选与右键菜单）
        results_group = QtWidgets.QGroupBox("混剪长视频的结果")
        _rg_layout = QtWidgets.QVBoxLayout(results_group)
        _rg_layout.setContentsMargins(10, 8, 10, 8)
        _rg_layout.setSpacing(8)

        # 表格：序号、文件名、输出路径、大小(MB)
        self.results_table = QtWidgets.QTableWidget(0, 4, results_group)
        # 列顺序调整：将“大小(MB)”与“输出路径”位置互换为：序号、文件名、大小(MB)、输出路径
        self.results_table.setHorizontalHeaderLabels(["序号", "文件名", "大小(MB)", "输出路径"])
        # 记录列索引，避免后续读写错列
        self._RESULTS_PATH_COL = 3
        self._RESULTS_SIZE_COL = 2
        self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.results_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        try:
            header = self.results_table.horizontalHeader()
            # 对齐头和列显示策略：序号/大小按内容自适应，文件名固定较宽，路径尽量拉伸
            header.setMinimumSectionSize(80)
            self.results_table.verticalHeader().setVisible(False)
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)  # 序号
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)       # 文件名
            header.setSectionResizeMode(self._RESULTS_SIZE_COL, QtWidgets.QHeaderView.ResizeToContents)  # 大小(MB)
            header.setSectionResizeMode(self._RESULTS_PATH_COL, QtWidgets.QHeaderView.Stretch)           # 输出路径
        except Exception:
            pass
        # 双击打开文件
        self.results_table.itemDoubleClicked.connect(self._on_results_table_double_clicked)
        _rg_layout.addWidget(self.results_table)

        # 结果区操作栏（打开文件/目录、复制路径）
        actions_bar = QtWidgets.QHBoxLayout()
        actions_bar.setContentsMargins(0, 0, 0, 0)
        actions_bar.setSpacing(6)
        self.open_selected_btn = QtWidgets.QPushButton("打开文件")
        self.copy_selected_path_btn = QtWidgets.QPushButton("复制路径")
        actions_bar.addWidget(self.open_selected_btn)
        actions_bar.addWidget(self.copy_selected_path_btn)
        actions_bar.addStretch(1)
        _rg_layout.addLayout(actions_bar)
        # 创建“输出结果”蒙层与菊花转圈指示器（默认隐藏）
        try:
            self._results_overlay = BusyOverlay(results_group)
            # 初始化几何尺寸为父组件当前矩形
            try:
                self._results_overlay.setGeometry(results_group.rect())
            except Exception:
                pass
            self._results_overlay.hide()
        except Exception:
            self._results_overlay = None
        # 统一设置两组的尺寸策略为横向扩展，保持同宽
        try:
            progress_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            results_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass

        # 使用垂直分割器控制上下比例为 2:8
        right_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right_splitter.setChildrenCollapsible(False)
        right_splitter.addWidget(progress_group)
        # 直接添加结果分组到分割器，去除中间容器，保证与上方分组同宽
        right_splitter.addWidget(results_group)
        # 设置比例
        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 8)
        try:
            # 设置初始高度比例（以像素估算 2:8 比例）
            right_splitter.setSizes([200, 800])
        except Exception:
            pass
        right_layout.addWidget(right_splitter)
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
      
        # self.open_out_dir_btn.clicked.connect(self._on_open_default_output_dir)
        self.open_selected_btn.clicked.connect(self._on_open_selected_files)
        self.copy_selected_path_btn.clicked.connect(self._copy_selected_paths)
        self.ffmpeg_info_btn.clicked.connect(self._on_show_ffmpeg_info)

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

    # 已移除日志打印框，保留方法以兼容旧代码（不执行任何动作）
    def _append_log(self, text: str) -> None:
        """兼容占位：过去用于日志追加，现已移除日志视图。"""
        return

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

        改进：强制只使用内置打包的 FFmpeg/FFprobe，不再回退到系统安装。
        检测到内置 ffmpeg 后，会将其 bin 目录插入到 PATH 前端，确保所有子进程只调用内置版本。
        若未发现内置 ffmpeg，则标记为不可用并提示，而不是使用系统版本。
        """
        # 统一启动策略：使用封装的引导函数，优先内置并允许开发环境系统兜底，同时修改 PATH。
        try:
            bootstrap_ffmpeg_env(
                prefer_bundled=True,
                dev_fallback_env=True,
                modify_env=True,
                logger=lambda m: self._append_log(f"[FFmpeg探测] {m}") if hasattr(self, "_append_log") else None,
            )
        except Exception:
            # 初始化失败时仍继续，用于更新标签为不可用
            pass

        # 再次解析以获取来源标签（不修改 PATH，仅用于显示）
        res = resolve_ffmpeg_paths(
            prefer_bundled=True,
            allow_system_fallback=True,
            modify_env=False,
            logger=lambda m: self._append_log(f"[FFmpeg探测] {m}") if hasattr(self, "_append_log") else None,
        )

        # Update ffmpeg badge
        if res.ffmpeg_path:
            src_text = "内置" if res.source.startswith("bundled") else res.source
            self.ffmpeg_status.setText(f"ffmpeg: 可用 ({src_text})")
        else:
            self.ffmpeg_status.setText("ffmpeg: 不可用")

        # NVENC badge 由后续检测来更新，这里仅在 ffmpeg 不可用时重置
        try:
            ok = vc.is_nvenc_available()
            self.nvenc_status.setText("NVENC: 可用" if ok else "NVENC: 不可用")
        except Exception:
            self.nvenc_status.setText("NVENC: 检测失败")

    # 取消用户选择项：默认始终使用内置 FFmpeg，无需切换优先级

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
        try:
            # 当窗口已关闭或控件已被销毁时，避免调用已删除的 Qt 对象
            lbl = getattr(self, "enc_summary_label", None)
            if lbl is None:
                return
            if _qt_is_valid is not None and not _qt_is_valid(lbl):
                return
            if hasattr(lbl, "isVisible") and lbl is not None and not lbl.isVisible():
                # 不可见时仍可安全更新，但若对象已被销毁，上面 isValid 会截获
                pass

            p = self._compute_effective_enc_params()
            display = getattr(self, "_profile_code_to_display", {}).get(p["profile"], p["profile"])  # 中文优先
            lbl.setText(
                f"编码参数概览：质量档位={display} | NVENC cq={p['nvenc_cq']} preset={p['preset_gpu']} | x265 crf={p['x265_crf']} preset={p['preset_cpu']}"
            )
        except Exception:
            # 防御性保护：任何异常（含对象已删除）都不影响主流程
            pass

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
        # 预校验：未选择视频目录或关键参数缺失时直接提示并返回，不切换按钮或显示蒙层
        try:
            settings_preview = self._collect_settings()
            if not settings_preview.video_dirs:
                QtWidgets.QMessageBox.warning(self, "提示", "请先选择至少一个视频目录")
                return
            if not settings_preview.bgm_path:
                QtWidgets.QMessageBox.warning(self, "提示", "请先选择 BGM 路径（文件或目录）")
                return
        except Exception:
            # 若采集设置异常则保守返回
            QtWidgets.QMessageBox.warning(self, "提示", "采集参数失败，请检查表单输入")
            return
        # 显示右下“输出结果”蒙层并禁用列表交互
        try:
            self._show_results_overlay()
        except Exception:
            pass
        # 新任务开始前重置进度条到 0%，本次任务期间不再自动重置
        try:
            self.progress_bar.setMaximum(100)
            self.progress_bar.setValue(0)
        except Exception:
            pass
        settings = self._collect_settings()
        # 移除日志输出，仅在状态区显示阶段与进度

        self._thread = QtCore.QThread(self)
        self._worker = VideoConcatWorker(settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.phase.connect(self._on_phase)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.results.connect(self._on_results_ready)
        self._worker.error.connect(self._on_error)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _on_progress(self, done: int, total: int) -> None:
        """Update progress bar with fixed-scale phase percentages.

        The worker emits progress on a fixed scale of 1000 units:
        - Phase 1 (TS 预转换) uses 0..300 units (30%).
        - Phase 2 (混合拼接) uses 300..1000 units (70%).

        Parameters
        ----------
        done : int
            Current progress units on the 0..1000 scale.
        total : int
            Always 1000 in this scheme.
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
        # 任务完成后将进度条显示为 100%，直到下次开始任务前不再重置
        try:
            # 若当前最大值为固定刻度（例如 1000），此处直接置为最大值即可呈现 100%
            self.progress_bar.setValue(self.progress_bar.maximum())
            # 完成后以绿色显示块，直到下一次开始
            self._apply_progress_style(chunk_color="#22c55e")
        except Exception:
            pass
        # 关闭蒙层，恢复交互
        try:
            self._hide_results_overlay()
        except Exception:
            pass
        self._cleanup_thread()

    def _on_results_ready(self, paths: List[str]) -> None:
        """Populate the results table with generated output file paths.

        Parameters
        ----------
        paths : List[str]
            List of output file paths.
        """
        try:
            self.results_table.setRowCount(0)
        except Exception:
            pass
        for idx, p in enumerate(paths, start=1):
            try:
                # 兼容：有些结果字符串可能携带尾随的"(xx MB)"展示信息，这里先规范化为纯路径
                normalized_p = self._normalize_result_path(p)
                from pathlib import Path as _P
                st_size = _P(normalized_p).stat().st_size if _P(normalized_p).exists() else 0
                size_mb = st_size / (1024 * 1024) if st_size else 0.0
            except Exception:
                size_mb = 0.0
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            # 序号
            idx_item = QtWidgets.QTableWidgetItem(str(idx))
            idx_item.setTextAlignment(QtCore.Qt.AlignCenter)
            # 文件名（优化显示：去除后缀与末尾括号内容，如 "abc (123)" -> "abc"；支持全角括号）
            name_item = QtWidgets.QTableWidgetItem(self._display_file_name_from_path(normalized_p))
            # 大小(MB)
            size_item = QtWidgets.QTableWidgetItem(f"{size_mb:.1f}")
            size_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            # 输出路径
            path_item = QtWidgets.QTableWidgetItem(normalized_p)
            # 为每个单元项写入 UserRole 以便稳健地获取路径
            try:
                for _it in (idx_item, name_item, size_item, path_item):
                    _it.setData(QtCore.Qt.UserRole, normalized_p)
            except Exception:
                pass
            self.results_table.setItem(row, 0, idx_item)
            self.results_table.setItem(row, 1, name_item)
            # 列位置调整：第2列为大小(MB)，第3列为输出路径
            self.results_table.setItem(row, self._RESULTS_SIZE_COL, size_item)
            self.results_table.setItem(row, self._RESULTS_PATH_COL, path_item)
        # 自适应列宽（文件名和路径更宽，序号和大小适度；输出路径位于最后一列并可适度拉伸）
        try:
            self.results_table.resizeColumnToContents(0)
            self.results_table.setColumnWidth(1, max(160, int(self.results_table.width() * 0.25)))
            # 大小(MB)列适度
            self.results_table.resizeColumnToContents(self._RESULTS_SIZE_COL)
            # 输出路径列更宽
            self.results_table.setColumnWidth(self._RESULTS_PATH_COL, max(240, int(self.results_table.width() * 0.45)))
        except Exception:
            pass

    def _normalize_result_path(self, s: str) -> str:
        """将可能包含尾随大小展示信息的结果字符串规范化为纯文件路径。

        场景：分辨率分组模式曾返回类似 "C:/path/to/out.mp4 (12.3 MB)" 的字符串，
        这里识别并去掉末尾的括号块（仅当括号内包含 "MB" 关键字时），保留纯路径。

        Parameters
        ----------
        s : str
            结果字符串，可能是纯路径，也可能带有尾随大小信息。

        Returns
        -------
        str
            纯路径字符串。
        """
        try:
            text = s.strip()
            # 仅在尾部存在括号且括号内包含 "MB" 时去除，避免误伤正常带括号的路径
            # 支持半角 () 与全角（）
            tail_pattern = re.compile(r"\s*[（(][^（）()]*MB[^（）()]*[）)]\s*$")
            if tail_pattern.search(text):
                text = tail_pattern.sub("", text).strip()
            return text
        except Exception:
            return s

    def _display_file_name_from_path(self, path_str: str) -> str:
        """根据完整路径生成更干净的文件名用于展示。

        规则：
        - 去掉文件后缀（例如 .mp4）
        - 去掉末尾的括号及其内部内容（支持半角 () 与全角（）），可重复去除
          例如："示例视频 (版本1)" -> "示例视频"

        Parameters
        ----------
        path_str : str
            完整的文件路径字符串。

        Returns
        -------
        str
            优化后的文件名（用于表格“文件名”列展示）。
        """
        try:
            p = Path(path_str)
            stem = p.stem  # 去后缀
            # 反复去除末尾括号及其内部内容（支持半角/全角）
            # 匹配示例："名称 (abc)"、"名称（abc）"、末尾可能有空格
            pattern = re.compile(r"\s*[（(][^）)]*[）)]\s*$")
            sanitized = stem
            # 最多重复 3 次，避免极端情况死循环（一般 1~2 次足够）
            for _ in range(3):
                if not pattern.search(sanitized):
                    break
                sanitized = pattern.sub("", sanitized).strip()
            sanitized = sanitized.strip()
            return sanitized or stem
        except Exception:
            # 回退：异常时返回去后缀的文件名
            try:
                return Path(path_str).stem
            except Exception:
                return path_str

    def _get_result_path_by_row(self, row: int) -> Optional[Path]:
        """根据表格行安全地获取输出路径。

        尝试读取指定行的“输出路径”列文本；若为空则回退到各列的 UserRole 数据。

        Parameters
        ----------
        row : int
            表格的行号。

        Returns
        -------
        Optional[Path]
            若成功获取，返回 Path；否则返回 None。
        """
        try:
            p_item = self.results_table.item(row, self._RESULTS_PATH_COL)
            if p_item and p_item.text():
                return Path(p_item.text().strip())
            # 回退：从任一列的 UserRole 读取路径
            for col in range(self.results_table.columnCount()):
                it = self.results_table.item(row, col)
                if not it:
                    continue
                data = it.data(QtCore.Qt.UserRole)
                if isinstance(data, str) and data.strip():
                    return Path(data.strip())
        except Exception:
            return None
        return None

    def _on_results_table_double_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        """双击表格项时，在文件管理器中打开所在目录并选中该文件。"""
        try:
            row = item.row()
            path = self._get_result_path_by_row(row)
            if not path:
                QtWidgets.QMessageBox.warning(self, "提示", "无法读取该行的输出路径")
                return
            if not path.exists():
                QtWidgets.QMessageBox.warning(self, "提示", f"文件不存在: {path}")
                return
            # 优化：改为在文件管理器中定位并选中文件
            self._reveal_in_file_manager([path])
        except Exception:
            pass

    def _on_open_selected_files(self) -> None:
        """在文件管理器中打开并选中所有选中的输出文件（表格选中行）。"""
        try:
            sel = self.results_table.selectionModel().selectedRows()
        except Exception:
            sel = []
        if not sel:
            QtWidgets.QMessageBox.information(self, "提示", "请先选择一个或多个输出文件")
            return
        paths: list[Path] = []
        for mi in sel:
            try:
                p = self._get_result_path_by_row(mi.row())
                if p and p.exists():
                    paths.append(p)
                else:
                    QtWidgets.QMessageBox.warning(self, "提示", f"文件不存在: {p}")
            except Exception:
                pass
        if paths:
            self._reveal_in_file_manager(paths)

    # 已移除“打开所在目录”按钮。若后续需要恢复，可将此处理函数重新绑定到按钮或菜单。
    # def _on_open_selected_dirs(self) -> None:
    #     """在文件管理器中打开选中文件的所在目录，并选中这些文件（与“打开文件”一致）。"""
    #     try:
    #         sel = self.results_table.selectionModel().selectedRows()
    #     except Exception:
    #         sel = []
    #     if not sel:
    #         QtWidgets.QMessageBox.information(self, "提示", "请先选择一个或多个输出文件")
    #         return
    #     paths: list[Path] = []
    #     for mi in sel:
    #         try:
    #             p = self._get_result_path_by_row(mi.row())
    #             if p and p.exists():
    #                 paths.append(p)
    #             else:
    #                 QtWidgets.QMessageBox.warning(self, "提示", f"文件不存在: {p}")
    #         except Exception:
    #             pass
    #     if paths:
    #         self._reveal_in_file_manager(paths)

    def _copy_selected_paths(self) -> None:
        """复制选中行的输出路径到剪贴板。"""
        try:
            sel = self.results_table.selectionModel().selectedRows()
        except Exception:
            sel = []
        if not sel:
            QtWidgets.QMessageBox.information(self, "提示", "请先选择一个或多个输出文件")
            return
        paths = []
        for mi in sel:
            p = self._get_result_path_by_row(mi.row())
            if p:
                paths.append(str(p))
        if paths:
            QtWidgets.QApplication.clipboard().setText("\n".join(paths))
            QtWidgets.QMessageBox.information(self, "提示", f"已复制 {len(paths)} 个路径到剪贴板")

    def _on_show_ffmpeg_info(self) -> None:
        """Show FFmpeg/FFprobe version details in a dialog.

        This method resolves the ffmpeg path (system or bundled), runs
        `ffmpeg -version` and `ffprobe -version`, and displays outputs
        with the resolved executable path. Helpful to verify whether the
        app is using the bundled FFmpeg or the system one.
        """
        # 使用封装的路径解析，不修改 PATH，仅用于信息展示。
        res = resolve_ffmpeg_paths(
            prefer_bundled=True,
            allow_system_fallback=True,
            modify_env=False,
        )
        ffmpeg_path = res.ffmpeg_path
        ffprobe_path = res.ffprobe_path
        if not ffmpeg_path:
            QtWidgets.QMessageBox.critical(self, "错误", "未找到 ffmpeg，可在设置中检查环境或打包内置 FFmpeg")
            return

        ffmpeg_type = (
            "内置(PyInstaller)" if res.source == "bundled_meipass" else (
                "内置(vendor)" if res.source == "bundled_vendor" else "系统"
            )
        )

    def _reveal_in_file_manager(self, paths: List[Path]) -> None:
        """在系统文件管理器中显示并选中指定文件。

        不同平台的实现：
        - Windows: 使用 `explorer /select,<path>`，逐个文件执行
        - macOS: 使用 `open -R <path>`，逐个文件执行
        - 其他平台: 打开所在目录（不保证选中），使用 QDesktopServices

        Parameters
        ----------
        paths : List[pathlib.Path]
            需要在文件管理器中显示并选中的文件列表。
        """
        if not paths:
            return
        try:
            plat = sys.platform.lower()
        except Exception:
            plat = ""

        for p in paths:
            try:
                if not p or not isinstance(p, Path):
                    continue
                if plat.startswith("win"):
                    # Windows: explorer /select,<path>
                    try:
                        subprocess.run(["explorer", "/select,", str(p)], check=False)
                    except Exception:
                        # 回退：打开所在目录
                        QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p.parent)))
                elif plat == "darwin":
                    # macOS: open -R <path>
                    try:
                        subprocess.run(["open", "-R", str(p)], check=False)
                    except Exception:
                        QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p.parent)))
                else:
                    # 其他平台：打开目录（不保证选中）
                    QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p.parent)))
            except Exception:
                try:
                    QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p.parent)))
                except Exception:
                    pass

        # Collect version info via util
        ffmpeg_ver, ffprobe_ver = get_ffmpeg_versions(ffmpeg_path, ffprobe_path, timeout=8)

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
            nvenc_available, encoders, hwaccels = detect_nvenc(ffmpeg_path, timeout=8)
            has_h264 = "h264_nvenc" in encoders
            has_hevc = "hevc_nvenc" in encoders
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
        try:
            self._hide_results_overlay()
        except Exception:
            pass
        self._cleanup_thread()

    def _cleanup_thread(self) -> None:
        """Cleanup thread/worker state and re-enable controls.

        完成、错误或手动停止后统一在此处恢复按钮互斥逻辑：
        - 启用“开始”按钮，禁用“停止”按钮；
        - 清空 worker 引用，置空线程；
        - 阶段标签回到 idle（不重置进度值，保留到下一次开始任务前）。
        """
        try:
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(2000)
        except Exception:
            pass
        self._thread = None
        # 恢复互斥按钮状态
        try:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        except Exception:
            pass
        # 清理 worker 引用
        self._worker = None
        # 阶段标签回到 idle，进度值保持不变
        try:
            self.phase_label.setText("阶段: idle")
        except Exception:
            pass

    def _apply_action_buttons_style(self, base_h: int = 44, base_pt: int = 12) -> None:
        """
        根据屏幕 DPI 自适应地设置“开始/停止”按钮的高度与字号，并应用轻量样式。

        参数:
            base_h: 基准高度（像素），会随 DPI 线性缩放并在合理范围内裁剪。
            base_pt: 基准字号（pt），会随 DPI 缩放并限制上下限。
        """
        # 计算 DPI 缩放
        try:
            screen = QtWidgets.QApplication.primaryScreen()
            dpi = screen.logicalDotsPerInch() if screen else 96.0
            scale = max(1.0, dpi / 96.0)
        except Exception:
            scale = 1.0

        # 计算自适应高度与字号
        height = int(max(40, min(64, base_h * scale)))
        pt_size = int(max(12, min(18, base_pt * scale)))

        # 固定高度，避免不同平台下被压缩
        try:
            self.start_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.stop_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.start_btn.setFixedHeight(height)
            self.stop_btn.setFixedHeight(height)
        except Exception:
            pass

        # 设置统一字号
        try:
            bf = self.start_btn.font(); bf.setPointSize(pt_size); self.start_btn.setFont(bf)
            bf2 = self.stop_btn.font(); bf2.setPointSize(pt_size); self.stop_btn.setFont(bf2)
        except Exception:
            pass

        # 轻量级样式提升触控面积与美观（圆角与内边距）
        try:
            # 恢复边框，并提供悬停/按下/禁用状态的细微视觉反馈
            style = (
                f"QPushButton{{min-height:{height}px;max-height:{height}px;padding:6px 14px;border:1px solid #bfbfbf;border-radius:6px;}}"
                f"QPushButton:hover{{border:1px solid #999999;}}"
                f"QPushButton:pressed{{border:1px solid #888888;background-color: rgba(0,0,0,0.04);}}"
                f"QPushButton:disabled{{color: rgba(0,0,0,0.4);border:1px solid #dddddd;background-color: rgba(0,0,0,0.02);}}"
            )
            # 分别设置，避免影响其他按钮
            self.start_btn.setStyleSheet(style)
            self.stop_btn.setStyleSheet(style)
        except Exception:
            pass

    def _show_results_overlay(self) -> None:
        """显示右下“输出结果”分组的蒙层与菊花转圈，并禁用列表交互。"""
        if getattr(self, "_results_overlay", None):
            try:
                self._results_overlay.show()
                self._results_overlay.raise_()
            except Exception:
                pass
        # 禁用结果交互（表格优先，兼容旧列表）
        try:
            if hasattr(self, "results_table"):
                self.results_table.setEnabled(False)
            elif hasattr(self, "results_list"):
                self.results_list.setEnabled(False)
        except Exception:
            pass

    def _hide_results_overlay(self) -> None:
        """隐藏右下“输出结果”分组的蒙层，并恢复列表交互。"""
        if getattr(self, "_results_overlay", None):
            try:
                self._results_overlay.hide()
            except Exception:
                pass
        # 恢复结果交互（表格优先，兼容旧列表）
        try:
            if hasattr(self, "results_table"):
                self.results_table.setEnabled(True)
            elif hasattr(self, "results_list"):
                self.results_list.setEnabled(True)
        except Exception:
            pass

    def _apply_progress_style(self, chunk_color: str = "#3b82f6") -> None:
        """
        根据当前屏幕 DPI 自适应地设置进度条高度与字体大小，并应用指定块颜色。

        参数:
            chunk_color: 进度条填充块颜色（如 #3b82f6 蓝色、#f59e0b 橙色、#22c55e 绿色）
        """
        # 尺寸策略：横向扩展，纵向固定
        try:
            self.progress_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass

        # 计算 DPI 缩放
        try:
            screen = QtWidgets.QApplication.primaryScreen()
            dpi = screen.logicalDotsPerInch() if screen else 96.0
            scale = max(1.0, dpi / 96.0)
        except Exception:
            scale = 1.0

        # 自适应高度与字号（设上下限防止过大/过小）
        base_h = 40
        height = int(max(34, min(56, base_h * scale)))
        try:
            self.progress_bar.setFixedHeight(height)
        except Exception:
            pass

        try:
            font = self.progress_bar.font()
            base_pt = 12
            pt_size = int(max(base_pt, min(18, base_pt * scale)))
            font.setPointSize(pt_size)
            self.progress_bar.setFont(font)
        except Exception:
            pass

        # 应用样式表
        try:
            style = (
                f"QProgressBar{{min-height:{height}px;max-height:{height}px;border:1px solid #bbb;border-radius:4px;text-align:center;}}"
                f"QProgressBar::chunk{{background-color:{chunk_color};margin:0px;}}"
            )
            self.progress_bar.setStyleSheet(style)
        except Exception:
            pass

    def _on_phase(self, phase_text: str) -> None:
        """阶段更新槽：更新阶段标签，并按阶段调整进度条配色。"""
        try:
            self.phase_label.setText(f"阶段: {phase_text}")
        except Exception:
            pass

        # 根据阶段关键字选择颜色
        pt = (phase_text or "").lower()
        color = "#3b82f6"  # 默认蓝色
        try:
            if "预处理" in phase_text or "pre" in pt or "scan" in pt:
                color = "#f59e0b"  # 橙色：预处理/扫描
            elif "混合" in phase_text or "concat" in pt or "merge" in pt:
                color = "#3b82f6"  # 蓝色：合并/混合
            elif "完成" in phase_text or "finish" in pt or "done" in pt:
                color = "#22c55e"  # 绿色：完成
        except Exception:
            pass

        # 应用选择的颜色（同时保留 DPI 自适应）
        try:
            self._apply_progress_style(chunk_color=color)
        except Exception:
            pass
        # 注意：阶段更新不应更改开始/停止按钮状态或清理线程；这些逻辑由 _cleanup_thread 统一处理。
        # 进度条重置策略：保留在完成后 100%，仅在下次开始任务前重置，在 _on_start 中执行。

    def _on_stop(self) -> None:
        """Attempt to stop the running worker.

        Note: For simplicity, this demo performs a soft stop by quitting the thread.
        Long-running ffmpeg subprocesses will finish their current item.
        """
        try:
            self._hide_results_overlay()
        except Exception:
            pass
        self._cleanup_thread()

    # ==== 托盘与窗口关闭行为优化 ====
    def _ensure_tray(self) -> None:
        """Ensure system tray icon and menu are initialized."""
        try:
            if getattr(self, "tray_icon", None):
                return
            self.tray_icon = QtWidgets.QSystemTrayIcon(self)
            # 使用窗口图标或一个标准图标
            icon = self.windowIcon()
            try:
                if getattr(icon, 'isNull', lambda: True)():
                    icon = QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
            except Exception:
                pass
            self.tray_icon.setIcon(icon)

            self.tray_menu = QtWidgets.QMenu(self)
            self.tray_act_show = QtGui.QAction("显示窗口", self)
            self.tray_act_exit = QtGui.QAction("退出", self)
            self.tray_menu.addAction(self.tray_act_show)
            self.tray_menu.addSeparator()
            self.tray_menu.addAction(self.tray_act_exit)
            self.tray_icon.setContextMenu(self.tray_menu)

            self.tray_act_show.triggered.connect(self._restore_from_tray)
            self.tray_act_exit.triggered.connect(self._on_exit_requested)
            self.tray_icon.activated.connect(self._on_tray_activated)
        except Exception:
            # 托盘初始化失败不影响主流程
            self.tray_icon = None

    def _restore_from_tray(self) -> None:
        """Restore the main window from the system tray."""
        try:
            self.showNormal()
            self.activateWindow()
        except Exception:
            pass

    def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation to restore window on click/double click."""
        try:
            if reason in (QtWidgets.QSystemTrayIcon.Trigger, QtWidgets.QSystemTrayIcon.DoubleClick):
                self._restore_from_tray()
        except Exception:
            pass

    def _on_exit_requested(self) -> None:
        """Exit the application. If a task is running, ask for confirmation."""
        try:
            if self._thread is not None:
                ret = QtWidgets.QMessageBox.question(
                    self,
                    "确认退出",
                    "当前有任务在后台运行，退出将尝试停止线程并关闭程序。是否继续？",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                if ret != QtWidgets.QMessageBox.Yes:
                    return
                # 软停止当前任务
                self._on_stop()
            QtWidgets.QApplication.quit()
        except Exception:
            QtWidgets.QApplication.quit()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        """Intercept window close.

        当有后台任务运行时，关闭窗口不会直接退出应用，而是将窗口隐藏到系统托盘，
        并在托盘中继续运行任务。用户可通过托盘菜单选择"退出"来结束程序。
        """
        try:
            if self._thread is not None:
                # 隐藏到托盘
                self._ensure_tray()
                if getattr(self, "tray_icon", None):
                    try:
                        self.tray_icon.show()
                        # 提示继续后台运行
                        self.tray_icon.showMessage(
                            "后台运行",
                            "任务未完成，窗口已隐藏到系统托盘。",
                            QtWidgets.QSystemTrayIcon.Information,
                            3000,
                        )
                    except Exception:
                        pass
                self.hide()
                event.ignore()
                return
        except Exception:
            pass
        # 无后台任务，正常退出
        try:
            if getattr(self, "tray_icon", None):
                self.tray_icon.hide()
        except Exception:
            pass
        event.accept()


def main() -> None:
    """Application entry point.

    Creates the Qt application and displays the main window.
    """
    app = QtWidgets.QApplication(sys.argv)
    # 在显示主窗口之前执行启动自检（英伟达显卡与授权切面）
    if not preflight.run_preflight_checks(app):
        # 用户确认后退出，或授权校验失败
        return
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()