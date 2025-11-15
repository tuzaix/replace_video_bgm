"""
Generate Cover Tab

本模块实现“合成封面”标签页，采用与“生成截图”标签页类似的左右分栏布局：

- 左侧面板：
  1) 必填参数（截图目录；合成封面数、每次拼接截图数、并行数）
  2) 字幕参数（字幕文本；对齐方式：左/中/右）
  3) 字幕位置（可拖拽控件，显示字幕文本并记录归一化 x/y 坐标）

- 右侧面板：
  1) 进度条 + 开始/停止按钮（互斥）
  2) 结果表（序号、封面路径、分辨率）

业务逻辑：
通过调用 `cover_tool.generate_cover` 中的并发封面生成方法来完成实际合成；
本标签页在后台线程中运行生成逻辑，并通过信号更新 UI。
"""

from __future__ import annotations

from typing import Optional, Tuple
import os
from PySide6 import QtWidgets, QtCore, QtGui
from gui.utils import theme


class CaptionPositionWidget(QtWidgets.QWidget):
    """一个可拖拽的字幕位置选择控件。

    该控件用于在一个空白画布上展示当前的字幕文本，并允许用户拖拽其位置。
    控件记录归一化坐标（x_ratio, y_ratio），用于生成封面时定位字幕。

    公共方法
    --------
    set_text(text: str) -> None
        设置显示的字幕文本。
    set_alignment(align: str) -> None
        设置水平对齐方式：'left'、'center' 或 'right'。
    get_position() -> Tuple[float, float]
        返回当前归一化坐标 (x_ratio, y_ratio)，范围 [0, 1]。
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._text: str = "示例字幕"
        self._align: str = "left"
        # 使用像素位置存储锚点，绘制时转换为文本框左上角或居中点
        self._pos: QtCore.QPointF = QtCore.QPointF(40.0, 40.0)
        self._dragging: bool = False
        self._drag_offset: QtCore.QPointF = QtCore.QPointF(0.0, 0.0)
        self.setMinimumSize(200, 120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setStyleSheet("border: 1px solid #ccc; background: #fafafa;")

    def set_text(self, text: str) -> None:
        """设置控件内部显示的字幕文本，并请求重绘。"""
        self._text = text or ""
        self.update()

    def set_alignment(self, align: str) -> None:
        """设置字幕文本的水平对齐方式（left/center/right）。"""
        self._align = align if align in {"left", "center", "right"} else "left"
        self.update()

    def get_position(self) -> Tuple[float, float]:
        """返回当前位置的归一化坐标 (x_ratio, y_ratio)，原点为左上角。"""
        w = max(1, self.width())
        h = max(1, self.height())
        x_ratio = max(0.0, min(1.0, float(self._pos.x()) / float(w)))
        y_ratio = max(0.0, min(1.0, float(self._pos.y()) / float(h)))
        return x_ratio, y_ratio

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        """开始拖拽：记录按下位置与当前文本框位置的偏移。"""
        if event.button() == QtCore.Qt.LeftButton:
            bbox = self._text_bbox()
            if bbox.contains(event.position()):
                self._dragging = True
                self._drag_offset = event.position() - self._pos
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        """拖拽时更新位置，并夹紧到控件范围。"""
        if self._dragging:
            new_pos = event.position() - self._drag_offset
            # 夹紧：避免文本框超出控件区域
            bbox = self._text_bbox(override_pos=new_pos)
            dx = 0.0
            dy = 0.0
            if bbox.left() < 0:
                dx = -bbox.left()
            if bbox.right() > self.width():
                dx = self.width() - bbox.right()
            if bbox.top() < 0:
                dy = -bbox.top()
            if bbox.bottom() > self.height():
                dy = self.height() - bbox.bottom()
            self._pos = QtCore.QPointF(new_pos.x() + dx, new_pos.y() + dy)
            self.update()
            try:
                rx, ry = self.get_position()
                print(f"pos=({self._pos.x():.1f},{self._pos.y():.1f}), ratio=({rx:.3f},{ry:.3f}) [origin top-left]")
            except Exception:
                pass
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        """结束拖拽。"""
        self._dragging = False
        super().mouseReleaseEvent(event)

    def _text_bbox(self, override_pos: Optional[QtCore.QPointF] = None) -> QtCore.QRectF:
        """计算当前字幕文本的包围框，用于拖拽命中测试与绘制背景。"""
        pos = override_pos or self._pos
        painter = QtGui.QPainter()
        font = self.font()
        fm = QtGui.QFontMetricsF(font)
        rect = fm.boundingRect(self._text or "")
        tw = rect.width() + 12.0
        th = rect.height() + 12.0
        # 根据对齐方式调整参考点：left=左上角，center=中心点，right=右上角
        if self._align == "center":
            x = pos.x() - tw / 2.0
            y = pos.y() - th / 2.0
        elif self._align == "right":
            x = pos.x() - tw
            y = pos.y()
        else:  # left
            x = pos.x()
            y = pos.y()
        return QtCore.QRectF(x, y, tw, th)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        """绘制背景与字幕文本框。"""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        # 背景指示文字
        painter.setPen(QtGui.QPen(QtGui.QColor("#888")))
        painter.drawText(self.rect(), QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft, "拖拽字幕进行定位")
        # 文本背景
        bbox = self._text_bbox()
        bg = QtGui.QColor("#000")
        bg.setAlpha(90)
        painter.fillRect(bbox, bg)
        # 文本边框：增加黑色边框便于观察
        painter.setPen(QtGui.QPen(QtGui.QColor("#000"), 1))
        painter.drawRect(bbox)
        # 文本颜色
        color = QtGui.QColor(str(getattr(theme, "PRIMARY_BLUE", "#409eff")))
        painter.setPen(QtGui.QPen(color))
        # 文本位置：在 bbox 内侧留 6 像素内边距
        font = self.font()
        painter.setFont(font)
        text_rect = QtCore.QRectF(bbox.left() + 6.0, bbox.top() + 6.0, bbox.width() - 12.0, bbox.height() - 12.0)
        align_flag = QtCore.Qt.AlignLeft if self._align == "left" else (QtCore.Qt.AlignCenter if self._align == "center" else QtCore.Qt.AlignRight)
        painter.drawText(text_rect, align_flag | QtCore.Qt.AlignVCenter, self._text)
        painter.end()


class GenerateCoverWorker(QtCore.QObject):
    """后台工作者：并发生成封面图片。

    通过调用 `cover_tool.generate_cover.generate_covers_concurrently` 完成封面合成，
    并在每次生成成功后发射 `cover_generated` 信号更新右侧结果表。

    信号
    ----
    phase(str): 阶段描述。
    progress(int, int): 已完成/总数。
    cover_generated(int, str, Tuple[int, int]): (序号, 封面路径, 分辨率)
    finished(str, int): (输出目录, 成功数量)
    error(str): 错误描述。

    槽
    ---
    run(images_dir, output_dir, count, per_cover, workers, caption, align, pos_x, pos_y) -> None
        启动封面生成任务。
    """

    phase = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    cover_generated = QtCore.Signal(int, str, tuple)
    finished = QtCore.Signal(str, int)
    error = QtCore.Signal(str)
    start = QtCore.Signal(str, int, int, int, str, str, float, float)

    def __init__(self) -> None:
        super().__init__()
        self._stopping: bool = False

    def stop(self) -> None:
        """请求停止：当前轮次结束后退出。"""
        self._stopping = True

    @QtCore.Slot(str, str, int, int, int, str, str, float, float)
    def run(self, images_dir: str, output_dir: str, count: int, per_cover: int, workers: int, caption: str, align: str, pos_x: float, pos_y: float) -> None:
        """执行封面生成任务（后台线程）。"""
        try:
            from cover_tool import generate_cover as gen
        except Exception as e:
            self.error.emit(f"导入封面逻辑失败: {e}")
            return

        try:
            if not os.path.isdir(images_dir):
                self.error.emit("截图目录不存在或不可访问")
                return
            images = gen.list_images(images_dir)
            if not images:
                self.error.emit("截图目录中未找到图片文件")
                return
        except Exception as e:
            self.error.emit(f"扫描图片失败: {e}")
            return

        # 输出目录校验与准备
        try:
            if not output_dir:
                # 默认：截图目录的上一层目录/封面
                parent = os.path.dirname(os.path.abspath(images_dir))
                output_dir = os.path.join(parent, "封面")
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            self.error.emit(f"创建输出目录失败: {e}")
            return

        total = max(1, int(count))
        self.progress.emit(0, total)
        self.phase.emit("Generating covers…")

        # 构建回调以接收每个封面生成完成事件
        def on_cover(idx: int, path: str, wh: Tuple[int, int]) -> None:
            if self._stopping:
                return
            try:
                self.cover_generated.emit(idx, path, wh)
            except Exception:
                pass
            # 进度推进
            try:
                self.progress.emit(idx, total)
            except Exception:
                pass

        try:
            ok = gen.generate_covers_concurrently(
                images_dir=images_dir,
                all_images=images,
                count=total,
                per_cover=max(1, int(per_cover)),
                caption=caption or None,
                color="yellow",
                workers=max(1, int(workers)),
                caption_position=(float(pos_x), float(pos_y)),
                caption_align=align if align in {"left", "center", "right"} else "left",
                output_dir=output_dir,
                progress_cb=on_cover,
            )
        except Exception as e:
            self.error.emit(f"并发生成封面失败: {e}")
            return

        # 完成统计：在 output_dir 下统计生成的图片数
        try:
            total_images = 0
            for _, _, files in os.walk(output_dir):
                for f in files:
                    if os.path.splitext(f)[1].lower() in {".jpg", ".jpeg", ".png"}:
                        total_images += 1
            self.progress.emit(total, total)
            self.finished.emit(output_dir, total_images)
        except Exception as e:
            self.error.emit(f"统计结果失败: {e}")


class GenerateCoverTab(QtWidgets.QWidget):
    """“合成封面”标签页，实现左右面板布局与运行控制。"""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.root_layout = QtWidgets.QHBoxLayout(self)
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[GenerateCoverWorker] = None
        self._is_running: bool = False
        self._build_page()

    def _build_page(self) -> None:
        """构建整页：左右两面板，经分割器组织并加入根布局。"""
        left_panel = self._build_left_panel()
        right_panel = self._build_right_panel()

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 30)
        splitter.setStretchFactor(1, 70)

        handle = splitter.handle(1)
        try:
            handle.setEnabled(False)
        except Exception:
            pass

        self.root_layout.setContentsMargins(6, 6, 6, 6)
        self.root_layout.addWidget(splitter)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        """构建左侧参数面板，包括三组参数与一个可拖拽控件。"""
        # group1：必填参数
        group1 = QtWidgets.QGroupBox("必填参数")
        gl1 = QtWidgets.QVBoxLayout(group1)
        gl1.setContentsMargins(10, 8, 10, 8)
        gl1.setSpacing(10)

        lbl_images = QtWidgets.QLabel("截图目录")
        self.images_dir_edit = QtWidgets.QLineEdit()
        self.images_dir_edit.setPlaceholderText("选择截图目录…")
        browse_images_btn = QtWidgets.QPushButton("浏览…")
        browse_images_btn.clicked.connect(self._on_browse_images_dir)
        row_img = QtWidgets.QHBoxLayout()
        row_img.addWidget(lbl_images, 0)
        row_img.addWidget(self.images_dir_edit, 1)
        row_img.addWidget(browse_images_btn)
        gl1.addLayout(row_img)

        # 新增：合成封面目录（默认：截图目录的上一层目录/封面）
        lbl_output = QtWidgets.QLabel("合成目录")
        self.output_dir_edit = QtWidgets.QLineEdit()
        self.output_dir_edit.setPlaceholderText("默认：<截图目录> 的上一层目录/封面")
        browse_output_btn = QtWidgets.QPushButton("浏览")
        browse_output_btn.clicked.connect(self._on_browse_output_dir)
        row_out = QtWidgets.QHBoxLayout()
        row_out.addWidget(lbl_output, 0)
        row_out.addWidget(self.output_dir_edit, 1)
        row_out.addWidget(browse_output_btn)
        gl1.addLayout(row_out)
        try:
            self.images_dir_edit.textChanged.connect(self._sync_default_output_dir)
        except Exception:
            pass

        # 数值参数：合成封面数、每次拼接截图数、并行数
        row_nums = QtWidgets.QHBoxLayout()
        self.count_spin = QtWidgets.QSpinBox(); self.count_spin.setRange(1, 500); self.count_spin.setValue(10)
        self.per_cover_spin = QtWidgets.QSpinBox(); self.per_cover_spin.setRange(1, 10); self.per_cover_spin.setValue(4)
        self.workers_spin = QtWidgets.QSpinBox(); self.workers_spin.setRange(1, 32); self.workers_spin.setValue(4)
        row_nums.addWidget(QtWidgets.QLabel("合成封面数"), 0)
        row_nums.addWidget(self.count_spin, 1)
        row_nums.addWidget(QtWidgets.QLabel("每次拼接截图数"), 0)
        row_nums.addWidget(self.per_cover_spin, 1)
        row_nums.addWidget(QtWidgets.QLabel("执行并行数"), 0)
        row_nums.addWidget(self.workers_spin, 1)
        gl1.addLayout(row_nums)

        # group2：字幕参数
        group2 = QtWidgets.QGroupBox("字幕参数")
        gl2 = QtWidgets.QVBoxLayout(group2)
        gl2.setContentsMargins(10, 8, 10, 8)
        gl2.setSpacing(10)

        self.caption_edit = QtWidgets.QTextEdit()  # 多行文本框，默认约 5 行高度
        self.caption_edit.setFixedHeight(5 * self.caption_edit.fontMetrics().height())
        self.caption_edit.setPlaceholderText("封面字幕…")
        row_cap = QtWidgets.QHBoxLayout()
        row_cap.addWidget(QtWidgets.QLabel("封面字幕"), 0)
        row_cap.addWidget(self.caption_edit, 1)
        gl2.addLayout(row_cap)

        align_row = QtWidgets.QHBoxLayout()
        align_row.addWidget(QtWidgets.QLabel("字幕对齐方式"), 0)
        self.align_left = QtWidgets.QRadioButton("靠左"); 
        self.align_center = QtWidgets.QRadioButton("居中"); 
        self.align_right = QtWidgets.QRadioButton("靠右")
        self.align_center.setChecked(True)

        for rb in (self.align_left, self.align_center, self.align_right):
            rb.toggled.connect(self._on_align_changed)
        align_row.addWidget(self.align_left)
        align_row.addWidget(self.align_center)
        align_row.addWidget(self.align_right)
        # 字体选择与大小（横向追加控件）
        align_row.addWidget(QtWidgets.QLabel("字幕字体"), 0)
        self.font_combo = QtWidgets.QFontComboBox()
        try:
            self.font_combo.setCurrentFont(self.font())
        except Exception:
            pass
        try:
            self.font_combo.currentFontChanged.connect(self._on_font_changed)
        except Exception:
            pass
        align_row.addWidget(self.font_combo)

        align_row.addWidget(QtWidgets.QLabel("字体大小"), 0)
        self.font_size_spin = QtWidgets.QSpinBox()
        self.font_size_spin.setRange(8, 72)
        self.font_size_spin.setValue(15)
        try:
            self.font_size_spin.valueChanged.connect(self._on_font_changed)
        except Exception:
            pass
        align_row.addWidget(self.font_size_spin)
        gl2.addLayout(align_row)

        # group3：字幕位置（横屏比例）
        group3 = QtWidgets.QGroupBox("字幕在封面的位置（可拖拽）")
        gl3 = QtWidgets.QVBoxLayout(group3)
        gl3.setContentsMargins(10, 8, 10, 8)
        gl3.setSpacing(10)

        self.pos_widget = CaptionPositionWidget()
        # QTextEdit.textChanged() 不携带文本参数，需主动读取并传给位置控件
        try:
            self.caption_edit.textChanged.connect(lambda: self.pos_widget.set_text(self.caption_edit.toPlainText()))
        except Exception:
            pass
        # 初始化预览控件字体，使用选择的字体与字号
        try:
            self._on_font_changed()
        except Exception:
            pass
        
        gl3.addWidget(self.pos_widget, 1)

        # 组装到垂直分割器
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        try:
            group1.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            group2.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        except Exception:
            pass
        splitter.addWidget(group1)
        splitter.addWidget(group2)
        splitter.addWidget(group3)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        return splitter

    def _build_right_panel(self) -> QtWidgets.QWidget:
        """构建右侧运行与结果面板。"""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        # 进度 + 开始/停止（互斥单按钮）
        group_progress = QtWidgets.QGroupBox("运行状态")
        gpl = QtWidgets.QVBoxLayout(group_progress)
        gpl.setContentsMargins(6, 6, 6, 6)

        phase_row = QtWidgets.QHBoxLayout()
        self.phase_label = QtWidgets.QLabel("准备就绪")
        phase_row.addWidget(self.phase_label, 1)
        gpl.addLayout(phase_row)

        row_progress = QtWidgets.QHBoxLayout()
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        # 显示“已完成/总数”格式
        try:
            self.progress_bar.setTextVisible(True)
            self.progress_bar.setFormat("0 / 0")
        except Exception:
            pass

        # 统一进度条样式（与截图页一致）
        self._apply_progressbar_style(chunk_color=theme.PRIMARY_BLUE)

        self.action_btn = QtWidgets.QPushButton("开始")
        self._apply_action_button_style(running=False)
        self.action_btn.clicked.connect(self._on_action_clicked)

        row_progress.addWidget(self.progress_bar)
        row_progress.addWidget(self.action_btn)
        gpl.addLayout(row_progress)
        layout.addWidget(group_progress)

        # 结果表
        self.results_table = QtWidgets.QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["序号", "封面路径", "分辨率"])
        self.results_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.results_table, 1)
        return container

    def _on_align_changed(self) -> None:
        """对齐方式单选框变更时更新拖拽控件的文本对齐。"""
        align = "left"
        if self.align_center.isChecked():
            align = "center"
        elif self.align_right.isChecked():
            align = "right"
        self.pos_widget.set_alignment(align)

    def _on_font_changed(self) -> None:
        """字体或字号变化时，更新拖拽预览控件的字体。"""
        try:
            qf = self.font_combo.currentFont() if hasattr(self, "font_combo") else self.font()
            size = self.font_size_spin.value() if hasattr(self, "font_size_spin") else 20
            qf.setPointSize(int(size))
            self.pos_widget.setFont(qf)
            self.pos_widget.update()
        except Exception:
            pass

    def _on_browse_images_dir(self) -> None:
        """选择截图目录路径。"""
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "选择截图目录")
        if p:
            self.images_dir_edit.setText(p)
            # 同步默认输出目录为其上一层目录/封面
            try:
                parent = os.path.dirname(os.path.abspath(p))
                self.output_dir_edit.setText(os.path.join(parent, "封面"))
            except Exception:
                pass

    def _on_browse_output_dir(self) -> None:
        """选择合成封面输出目录。"""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择合成封面目录")
        if d:
            self.output_dir_edit.setText(d)

    def _sync_default_output_dir(self, _: str) -> None:
        """当截图目录变化时，将输出目录默认设为其上一层目录的“封面”。"""
        try:
            vd = self.images_dir_edit.text().strip()
            if not vd:
                return
            parent = os.path.dirname(os.path.abspath(vd))
            if parent:
                self.output_dir_edit.setText(os.path.join(parent, "封面"))
        except Exception:
            pass

    def _on_start_clicked(self) -> None:
        """开始执行封面生成任务：启动线程与工作者。"""
        if self._is_running:
            return
        images_dir = self.images_dir_edit.text().strip()
        if not images_dir:
            QtWidgets.QMessageBox.warning(self, "提示", "请先填写截图目录")
            return
        output_dir = self.output_dir_edit.text().strip()
        if not output_dir:
            QtWidgets.QMessageBox.warning(self, "提示", "请先填写合成封面目录")
            return
        try:
            caption = self.caption_edit.toPlainText().strip()
        except Exception:
            caption = ""
        align = "left"
        if self.align_center.isChecked():
            align = "center"
        elif self.align_right.isChecked():
            align = "right"
        pos_x, pos_y = self.pos_widget.get_position()

        # 线程与工作者
        self._thread = QtCore.QThread(self)
        self._worker = GenerateCoverWorker()
        self._worker.moveToThread(self._thread)
        # 连接信号
        self._thread.started.connect(lambda: self._worker.run(
            images_dir,
            output_dir,
            int(self.count_spin.value()),
            int(self.per_cover_spin.value()),
            int(self.workers_spin.value()),
            caption,
            align,
            float(pos_x),
            float(pos_y),
        ))
        self._worker.phase.connect(self._on_phase)
        self._worker.progress.connect(self._on_progress)
        self._worker.cover_generated.connect(self._on_cover_generated)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()
        self._set_running_ui(True)

    def _on_stop_clicked(self) -> None:
        """请求停止当前任务。"""
        if not self._is_running:
            return
        try:
            if self._worker:
                self._worker.stop()
        except Exception:
            pass

    def _on_phase(self, text: str) -> None:
        """更新阶段描述标签。"""
        try:
            self.phase_label.setText(text)
        except Exception:
            pass

    def _on_progress(self, done: int, total: int) -> None:
        """更新进度条最大值与当前值。"""
        try:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(done)
            # 同步“已完成/总数”文本
            self.progress_bar.setFormat(f"{done} / {total}")
        except Exception:
            pass

    def _on_cover_generated(self, idx: int, path: str, wh: Tuple[int, int]) -> None:
        """在结果表中插入一行，展示生成的封面路径与分辨率。"""
        try:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self.results_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(idx)))
            self.results_table.setItem(row, 1, QtWidgets.QTableWidgetItem(path))
            self.results_table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{wh[0]}x{wh[1]}"))
        except Exception:
            pass

    def _on_finished(self, out_dir: str, count: int) -> None:
        """任务完成后更新阶段文本并复位按钮状态。"""
        try:
            self.phase_label.setText(f"完成：输出目录 {out_dir}（{count} 张）")
        except Exception:
            pass
        self._set_running_ui(False)

    def _on_error(self, msg: str) -> None:
        """错误时提示并复位控件。"""
        try:
            QtWidgets.QMessageBox.critical(self, "错误", msg)
        except Exception:
            pass
        self._set_running_ui(False)

    def _cleanup_thread(self) -> None:
        """线程收尾：断开引用与标志。"""
        try:
            self._thread = None
            self._worker = None
        except Exception:
            pass

    def _set_running_ui(self, running: bool) -> None:
        """根据运行状态更新互斥按钮的文案与样式。"""
        self._is_running = running
        try:
            if hasattr(self, "action_btn") and self.action_btn is not None:
                self.action_btn.setText("停止" if running else "开始")
                self.action_btn.setToolTip("点击停止" if running else "点击开始")
                self._apply_action_button_style(running=running)
        except Exception:
            pass

    def _on_action_clicked(self) -> None:
        """互斥按钮：未运行则开始，运行中则停止。"""
        if not self._is_running:
            self._on_start_clicked()
        else:
            self._on_stop_clicked()

    # --- 样式与尺寸（与截图页保持一致） ---
    def _apply_progressbar_style(self, chunk_color: str = theme.PRIMARY_BLUE) -> None:
        """统一设置进度条的尺寸与样式，使其与“生成截图”页一致。"""
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
            style = (
                f"QProgressBar{{min-height:{height}px;max-height:{height}px;border:1px solid #bbb;border-radius:4px;text-align:center;}}"
                f"QProgressBar::chunk{{background-color:{chunk_color};margin:0px;}}"
            )
            self.progress_bar.setStyleSheet(style)
        except Exception:
            pass

    def _apply_action_button_style(self, running: bool) -> None:
        """根据运行状态统一设置单按钮的高度与样式。"""
        height = int(getattr(self, "_control_height", theme.BUTTON_HEIGHT))
        primary_bg = theme.PRIMARY_BLUE
        primary_bg_hover = theme.PRIMARY_BLUE_HOVER
        danger_bg = theme.DANGER_RED
        danger_bg_hover = theme.DANGER_RED_HOVER

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
            pb_font = self.progress_bar.font() if getattr(self, "progress_bar", None) is not None else None
            if pb_font is not None:
                self.action_btn.setFont(pb_font)
            self.action_btn.setStyleSheet(running_style if running else idle_style)
            self.action_btn.setFixedHeight(height)
        except Exception:
            pass