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

    # 选中变化信号：携带当前选中块索引（-1 表示未选中）
    selection_changed = QtCore.Signal(int)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._align: str = "left"
        # 多字幕块支持：每个块包含文本与位置；字体与对齐使用控件全局设置
        self._blocks: list[dict] = [
            # 默认块不设置字体字段，继承控件当前字体，便于后续全局变更自动生效
            {"text": "示例字幕", "pos": QtCore.QPointF(40.0, 40.0)}
        ]
        # 首次显示时居中一次，之后不再自动调整位置（针对第一个块）
        self._pos_centered_once: bool = False
        # 当前拖拽的块索引；-1 表示无拖拽
        self._dragging_idx: int = -1
        self._drag_offset: QtCore.QPointF = QtCore.QPointF(0.0, 0.0)
        # 当前选中的块索引；-1 表示未选中，用于高对比边框渲染
        self._selected_idx: int = -1
        # 编辑器与状态：双击进入编辑，点击外部完成编辑
        self._editor: Optional[QtWidgets.QTextEdit] = None
        self._editing_idx: int = -1
        self.setMinimumSize(200, 120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setStyleSheet("border: 1px solid #ccc; background: #fafafa;")

    def set_text(self, text: str) -> None:
        """设置第一个字幕块的文本，并请求重绘（多块时建议使用右键菜单）。"""
        if not self._blocks:
            self._blocks.append({"text": text or "", "pos": QtCore.QPointF(40.0, 40.0)})
        else:
            self._blocks[0]["text"] = text or ""
        self.update()

    def get_text(self) -> str:
        """返回所有字幕块文本的合并（换行连接）；无块返回空字符串。"""
        if not self._blocks:
            return ""
        return "\n".join([str(b.get("text", "")) for b in self._blocks]).strip()

    def set_alignment(self, align: str) -> None:
        """设置字幕文本的水平对齐方式（left/center/right）。"""
        self._align = align if align in {"left", "center", "right"} else "left"
        self.update()

    def get_default_alignment(self) -> str:
        """返回控件的默认对齐方式（未设置块对齐时的回退）。"""
        return str(self._align)

    def get_position(self) -> Tuple[float, float]:
        """返回第一个字幕块的归一化坐标 (x_ratio, y_ratio)，原点为左上角。"""
        w = max(1, self.width())
        h = max(1, self.height())
        if not self._blocks:
            return 0.0, 0.0
        pos = self._blocks[0]["pos"]
        x_ratio = max(0.0, min(1.0, float(pos.x()) / float(w)))
        y_ratio = max(0.0, min(1.0, float(pos.y()) / float(h)))
        return x_ratio, y_ratio

    def get_positions(self) -> list[Tuple[float, float]]:
        """返回所有字幕块的归一化坐标列表。"""
        w = max(1, self.width())
        h = max(1, self.height())
        out: list[Tuple[float, float]] = []
        for b in self._blocks:
            p = b["pos"]
            out.append(
                (
                    max(0.0, min(1.0, float(p.x()) / float(w))),
                    max(0.0, min(1.0, float(p.y()) / float(h)))
                )
            )
        return out

    def get_blocks(self) -> list[dict]:
        """返回所有字幕块的文本与归一化位置。

        结构：[{"text": str, "position": (x_ratio, y_ratio), "font_family": str, "font_size": int}]
        用于封面生成时将多个字幕块叠加到最终图像。
        """
        w = max(1, self.width())
        h = max(1, self.height())
        blocks: list[dict] = []
        for b in self._blocks:
            p = b.get("pos", QtCore.QPointF(0.0, 0.0))
            xr = max(0.0, min(1.0, float(p.x()) / float(w)))
            yr = max(0.0, min(1.0, float(p.y()) / float(h)))
            bf: QtGui.QFont = b.get("font", self.font())
            al: str = b.get("align", self._align)
            blocks.append({
                "text": str(b.get("text", "")),
                "position": (xr, yr),
                "font_family": bf.family(),
                "font_size": bf.pointSize() if bf.pointSize() > 0 else bf.pixelSize() or 12,
                "align": al if al in {"left", "center", "right"} else "left",
            })
        return blocks

    def get_selected_index(self) -> int:
        """返回当前选中的字幕块索引，未选中返回 -1。"""
        return int(self._selected_idx)

    def set_block_text(self, idx: int, text: str) -> None:
        """设置指定字幕块的文本并重绘。若索引无效则忽略。"""
        try:
            if idx < 0 or idx >= len(self._blocks):
                return
            self._blocks[idx]["text"] = str(text or "")
            # 若正在编辑该块，同步编辑器内容
            if self._editor is not None and self._editing_idx == idx:
                try:
                    self._editor.setPlainText(self._blocks[idx]["text"])  # type: ignore[attr-defined]
                except Exception:
                    pass
            self.update()
        except Exception:
            pass

    def set_block_font(self, idx: int, font: QtGui.QFont) -> None:
        """设置指定字幕块的字体并重绘。若索引无效则忽略。"""
        try:
            if idx < 0 or idx >= len(self._blocks):
                return
            self._blocks[idx]["font"] = font
            if self._editor is not None and self._editing_idx == idx:
                try:
                    self._editor.setFont(font)  # type: ignore[attr-defined]
                except Exception:
                    pass
            self.update()
        except Exception:
            pass

    def set_block_alignment(self, idx: int, align: str) -> None:
        """设置指定字幕块的水平对齐（left/center/right），若索引无效则忽略。"""
        try:
            if idx < 0 or idx >= len(self._blocks):
                return
            a = align if align in {"left", "center", "right"} else "left"
            self._blocks[idx]["align"] = a
            self.update()
        except Exception:
            pass

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        """开始拖拽：命中任一字幕块时记录对应偏移。"""
        # 若正在编辑，仅当左键点击字幕块外部时结束编辑并提交；回车作为换行由 QTextEdit 默认处理
        if self._editor is not None and self._editing_idx >= 0:
            edit_bbox = self._text_bbox(self._blocks[self._editing_idx])
            if event.button() == QtCore.Qt.LeftButton and not edit_bbox.contains(event.position()):
                self._finish_edit(commit=True)
                # 继续处理选择/拖拽逻辑
        if event.button() == QtCore.Qt.LeftButton:
            hit = -1
            if self._blocks:
                # 从上至下命中，优先最近添加的块
                for idx in range(len(self._blocks) - 1, -1, -1):
                    b = self._blocks[idx]
                    bbox = self._text_bbox(b)
                    if bbox.contains(event.position()):
                        hit = idx
                        break
            if hit >= 0:
                self._dragging_idx = hit
                b = self._blocks[hit]
                self._drag_offset = event.position() - b["pos"]
                self._selected_idx = hit
                try:
                    self.selection_changed.emit(hit)
                except Exception:
                    pass
                self.update()
            else:
                # 点击空白处则取消选中
                self._selected_idx = -1
                self._dragging_idx = -1
                try:
                    self.selection_changed.emit(-1)
                except Exception:
                    pass
                self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        """拖拽时更新命中的字幕块位置，并夹紧到控件范围。"""
        # 编辑时不允许拖拽移动
        if self._editor is not None:
            super().mouseMoveEvent(event)
            return
        if self._dragging_idx >= 0 and self._dragging_idx < len(self._blocks):
            b = self._blocks[self._dragging_idx]
            new_pos = event.position() - self._drag_offset
            # 夹紧：避免文本框超出控件区域
            bbox = self._text_bbox(b, override_pos=new_pos)
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
            b["pos"] = QtCore.QPointF(new_pos.x() + dx, new_pos.y() + dy)
            self.update()
            try:
                rx, ry = self.get_position()
                print(f"drag idx={self._dragging_idx}, pos=({b['pos'].x():.1f},{b['pos'].y():.1f}), ratio=({rx:.3f},{ry:.3f}) [origin top-left]")
            except Exception:
                pass
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        """结束拖拽。"""
        self._dragging_idx = -1
        super().mouseReleaseEvent(event)

    def _text_bbox(self, block: Optional[dict] = None, override_pos: Optional[QtCore.QPointF] = None) -> QtCore.QRectF:
        """计算当前字幕文本的包围框，用于拖拽命中测试与绘制背景。

        行为说明：
        - 随字幕内容（包括换行）动态增高，保证能容纳所有行内容；
        - 文本框最大宽度不超过控件宽度的 90%，超过则进行自动换行；
        - 文本框的定位锚点固定为左上角（origin top-left），切换对齐不会改变块位置。
        """
        b = block or (self._blocks[0] if self._blocks else {"text": "", "pos": QtCore.QPointF(0.0, 0.0)})
        pos = override_pos or b["pos"]
        # 每个块可独立设置字体；未设置则回退为控件字体
        font: QtGui.QFont = (block or {}).get("font", self.font())  # type: ignore[arg-type]
        fm = QtGui.QFontMetricsF(font)

        # 约束文本框最大宽度，避免过长一行溢出控件；多行或超宽会自动换行
        max_box_w = max(50.0, float(self.width()) * 0.9)
        text_rect_wrapped = fm.boundingRect(
            QtCore.QRectF(0.0, 0.0, max_box_w, 1e9),
            QtCore.Qt.TextWordWrap,
            (str(b.get("text", "")) or "")
        )

        tw = text_rect_wrapped.width() + 12.0
        th = text_rect_wrapped.height() + 12.0

        # 固定锚点为左上角：拖拽块位置不随对齐变化而移动
        x = pos.x()
        y = pos.y()
        return QtCore.QRectF(x, y, tw, th)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        """绘制背景与字幕文本框。"""
        # 首次显示时，将第一个文本框居中一次（固定左上角锚点）
        if not self._pos_centered_once and self.width() > 0 and self.height() > 0 and self._blocks:
            bbox0 = self._text_bbox(self._blocks[0], QtCore.QPointF(0.0, 0.0))
            nx = max(0.0, (float(self.width()) - bbox0.width()) / 2.0)
            ny = max(0.0, (float(self.height()) - bbox0.height()) / 2.0)
            self._blocks[0]["pos"] = QtCore.QPointF(nx, ny)
            self._pos_centered_once = True

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        # 背景指示文字
        painter.setPen(QtGui.QPen(QtGui.QColor("#888")))
        painter.drawText(self.rect(), QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft, "右键添加/删除字幕块；拖拽字幕进行定位")
        # 绘制所有字幕块
        for idx, b in enumerate(self._blocks):
            bbox = self._text_bbox(b)
            bg = QtGui.QColor("#000")
            # 选中块使用更高不透明度的背景以增强对比
            bg.setAlpha(110 if idx == self._selected_idx else 90)
            painter.fillRect(bbox, bg)
            # 文本边框：选中时使用主色并加粗
            if idx == self._selected_idx:
                sel_color = QtGui.QColor(str(getattr(theme, "PRIMARY_BLUE", "#2563eb")))
                painter.setPen(QtGui.QPen(sel_color, 2))
            else:
                painter.setPen(QtGui.QPen(QtGui.QColor("#000"), 1))
            painter.drawRect(bbox)
            # 文本颜色
            color = QtGui.QColor(str(getattr(theme, "PRIMARY_BLUE", "#409eff")))
            painter.setPen(QtGui.QPen(color))
            # 文本位置：在 bbox 内侧留 6 像素内边距
            font: QtGui.QFont = b.get("font", self.font())
            painter.setFont(font)
            text_rect = QtCore.QRectF(bbox.left() + 6.0, bbox.top() + 6.0, bbox.width() - 12.0, bbox.height() - 12.0)
            b_align = b.get("align", self._align)
            align_flag = QtCore.Qt.AlignLeft if b_align == "left" else (QtCore.Qt.AlignCenter if b_align == "center" else QtCore.Qt.AlignRight)
            # 支持自动换行与多行文本绘制；水平对齐由用户选择的单选框控制
            painter.drawText(text_rect, align_flag | QtCore.Qt.AlignVCenter | QtCore.Qt.TextWordWrap, str(b.get("text", "")))
        painter.end()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        """双击命中字幕块时进入编辑状态，创建内嵌 QTextEdit。"""
        hit_idx = -1
        for idx in range(len(self._blocks) - 1, -1, -1):
            if self._text_bbox(self._blocks[idx]).contains(event.position()):
                hit_idx = idx
                break
        if hit_idx >= 0:
            self._begin_edit_block(hit_idx)
        else:
            # 双击空白不做处理
            pass
        super().mouseDoubleClickEvent(event)

    def _begin_edit_block(self, idx: int) -> None:
        """开始编辑指定字幕块：

        - 在该块的文本区域创建无边框的 QTextEdit；
        - 载入块文本与字体；
        - 提供右键菜单调整字体与字号；
        - 聚焦并选中全部文本便于替换。
        """
        try:
            block = self._blocks[idx]
        except Exception:
            return
        self._editing_idx = idx
        self._selected_idx = idx
        self._dragging_idx = -1

        bbox = self._text_bbox(block)
        inner = QtCore.QRect(int(bbox.left() + 4), int(bbox.top() + 4), int(bbox.width() - 8), int(bbox.height() - 8))
        editor = QtWidgets.QTextEdit(self)
        editor.setFrameStyle(QtWidgets.QFrame.NoFrame)
        editor.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        editor.setText(str(block.get("text", "")))
        editor.setFont(block.get("font", self.font()))
        editor.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        editor.customContextMenuRequested.connect(lambda pos: self._show_editor_menu(editor))
        editor.setGeometry(inner)
        editor.show()
        editor.raise_()
        editor.setFocus()
        editor.selectAll()
        # 焦点丢失时自动提交编辑
        editor.installEventFilter(self)
        self._editor = editor
        self.update()

    def _finish_edit(self, commit: bool) -> None:
        """结束编辑状态。

        参数
        ----
        commit: 是否提交编辑内容与字体到字幕块。
        """
        if self._editor is None:
            return
        try:
            if commit and self._editing_idx >= 0 and self._editing_idx < len(self._blocks):
                text = self._editor.toPlainText()
                font = self._editor.font()
                self._blocks[self._editing_idx]["text"] = text
                self._blocks[self._editing_idx]["font"] = font
        except Exception:
            pass
        try:
            self._editor.deleteLater()
        except Exception:
            pass
        self._editor = None
        self._editing_idx = -1
        try:
            self.selection_changed.emit(self._selected_idx)
        except Exception:
            pass
        self.update()

    def _show_editor_menu(self, editor: QtWidgets.QTextEdit) -> None:
        """编辑器右键菜单：设置字体、增大/减小字号。"""
        menu = QtWidgets.QMenu(self)
        try:
            bg = getattr(theme, "GRAY_BG", "#e5e7eb")
            fg = getattr(theme, "GRAY_TEXT", "#374151")
            primary = getattr(theme, "PRIMARY_BLUE", "#2563eb")
            menu.setStyleSheet(
                f"QMenu{{background-color:{bg}; color:{fg}; border:1px solid #cfcfcf;}}"
                "QMenu::item{padding:6px 12px;}"
                f"QMenu::item:selected{{background-color:{primary}; color:#ffffff;}}"
            )
        except Exception:
            pass
        act_font = menu.addAction("设置字体…")
        act_inc = menu.addAction("增大字号")
        act_dec = menu.addAction("减小字号")
        chosen = menu.exec(editor.mapToGlobal(QtCore.QPoint(0, editor.cursorRect().bottom())))
        if chosen is None:
            return
        if chosen == act_font:
            cur_font = editor.font()
            font, ok = QtWidgets.QFontDialog.getFont(cur_font, self, "选择字体")
            if ok:
                editor.setFont(font)
        elif chosen == act_inc:
            f = editor.font()
            sz = f.pointSize()
            if sz <= 0:
                sz = 12
            f.setPointSize(min(sz + 2, 96))
            editor.setFont(f)
        elif chosen == act_dec:
            f = editor.font()
            sz = f.pointSize()
            if sz <= 0:
                sz = 12
            f.setPointSize(max(sz - 2, 6))
            editor.setFont(f)

    def eventFilter(self, obj: QtCore.QObject, ev: QtCore.QEvent) -> bool:
        """拦截编辑器事件：允许回车换行；不因失焦或按键结束编辑。"""
        if obj is self._editor:
            # 保持默认行为：
            # - KeyPress: 交由 QTextEdit 处理（回车换行等）
            # - FocusOut: 不结束编辑，等待左键点击块外再提交
            return False
        return super().eventFilter(obj, ev)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:  # type: ignore[override]
        """右键菜单：添加或删除拖拽字幕块（支持多块）。"""
        # 命中检查：是否点击在某个块上
        hit_idx = -1
        for idx in range(len(self._blocks) - 1, -1, -1):
            if self._text_bbox(self._blocks[idx]).contains(event.pos()):
                hit_idx = idx
                break

        menu = QtWidgets.QMenu(self)
        # 使用灰色背景以提高在浅色界面上的可读性
        try:
            bg = getattr(theme, "GRAY_BG", "#e5e7eb")
            fg = getattr(theme, "GRAY_TEXT", "#374151")
            primary = getattr(theme, "PRIMARY_BLUE", "#2563eb")
            menu.setStyleSheet(
                f"QMenu{{background-color:{bg}; color:{fg}; border:1px solid #cfcfcf;}}"
                "QMenu::item{padding:6px 12px;}"
                f"QMenu::item:selected{{background-color:{primary}; color:#ffffff;}}"
            )
        except Exception:
            menu.setStyleSheet(
                "QMenu{background-color:#e5e7eb; color:#374151; border:1px solid #cfcfcf;}"
                "QMenu::item{padding:6px 12px;}"
                "QMenu::item:selected{background-color:#2563eb; color:#ffffff;}"
            )
        # 右键命中块则选中该块；命中空白则取消选中
        self._selected_idx = hit_idx
        try:
            self.selection_changed.emit(hit_idx)
        except Exception:
            pass
        self.update()
        if hit_idx >= 0:
            act_del = menu.addAction("删除该字幕块")
        else:
            act_add = menu.addAction("添加字幕块")
        act_del_all = menu.addAction("删除所有字幕块")

        chosen = menu.exec(event.globalPos())
        try:
            if chosen is not None:
                text = chosen.text()
                if text == "删除该字幕块" and hit_idx >= 0:
                    del self._blocks[hit_idx]
                    self._dragging_idx = -1
                    self._selected_idx = -1
                    try:
                        self.selection_changed.emit(-1)
                    except Exception:
                        pass
                    self.update()
                elif text == "添加字幕块" and hit_idx < 0:
                    # 在点击位置添加新块：不设置字体字段，默认继承控件当前字体与字号
                    pos = event.pos()
                    self._blocks.append({
                        "text": "字幕",
                        "pos": QtCore.QPointF(pos.x(), pos.y()),
                    })
                    self.update()
                elif text == "删除所有字幕块":
                    self._blocks.clear()
                    self._dragging_idx = -1
                    self._selected_idx = -1
                    try:
                        self.selection_changed.emit(-1)
                    except Exception:
                        pass
                    self.update()
        except Exception:
            pass

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        """首次可用尺寸时将文本框居中一次，后续尺寸变化不自动移动。"""
        if not self._pos_centered_once and self.width() > 0 and self.height() > 0 and self._blocks:
            bbox0 = self._text_bbox(self._blocks[0], QtCore.QPointF(0.0, 0.0))
            nx = max(0.0, (float(self.width()) - bbox0.width()) / 2.0)
            ny = max(0.0, (float(self.height()) - bbox0.height()) / 2.0)
            self._blocks[0]["pos"] = QtCore.QPointF(nx, ny)
            self._pos_centered_once = True
            self.update()
        super().resizeEvent(event)


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
                captions=getattr(self, "_captions", None),
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

        # 字幕文本输入：仅作用于下方选中的拖拽块；未选中则提示
        text_row = QtWidgets.QHBoxLayout()
        text_row.addWidget(QtWidgets.QLabel("字幕文本"), 0)
        self.caption_text_edit = QtWidgets.QLineEdit()
        self.caption_text_edit.setPlaceholderText("请输入字幕文本（仅作用于选中块）")
        try:
            self.caption_text_edit.editingFinished.connect(self._on_caption_text_commit)
        except Exception:
            pass
        text_row.addWidget(self.caption_text_edit, 1)
        gl2.addLayout(text_row)

        # 去除独立的“封面字幕”输入框；拖拽控件内通过右键菜单添加/删除字幕块

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
        try:
            # 选中变化时同步左侧输入与对齐/字体控件界面
            self.pos_widget.selection_changed.connect(self._on_selection_changed)
        except Exception:
            pass
        # QTextEdit.textChanged() 不携带文本参数，需主动读取并传给位置控件
        # 字幕文本联动改为由右键菜单管理；不再与外部输入框联动
        # 初始化预览控件字体，使用选择的字体与字号
        # 初始化界面与状态
        self._syncing_controls = False
        try:
            self._refresh_controls_for_selection(-1)
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
        """对齐方式变更：仅更新当前选中的字幕块，无选中则提示。"""
        if getattr(self, "_syncing_controls", False):
            return
        align = "left"
        if self.align_center.isChecked():
            align = "center"
        elif self.align_right.isChecked():
            align = "right"
        idx = self.pos_widget.get_selected_index() if hasattr(self.pos_widget, "get_selected_index") else getattr(self.pos_widget, "_selected_idx", -1)
        if idx is None or int(idx) < 0:
            QtWidgets.QMessageBox.information(self, "提示", "请先选择一个字幕块后再调整对齐方式。")
            self._refresh_controls_for_selection(-1)
            return
        if hasattr(self.pos_widget, "set_block_alignment"):
            self.pos_widget.set_block_alignment(int(idx), align)
        self.pos_widget.update()

    def _on_font_changed(self) -> None:
        """字体或字号变更：仅更新当前选中的字幕块，无选中则提示。"""
        if getattr(self, "_syncing_controls", False):
            return
        try:
            idx = self.pos_widget.get_selected_index() if hasattr(self.pos_widget, "get_selected_index") else getattr(self.pos_widget, "_selected_idx", -1)
            if idx is None or int(idx) < 0:
                QtWidgets.QMessageBox.information(self, "提示", "请先选择一个字幕块后再调整字体或字号。")
                self._refresh_controls_for_selection(-1)
                return
            qf = self.font_combo.currentFont() if hasattr(self, "font_combo") else self.font()
            size = self.font_size_spin.value() if hasattr(self, "font_size_spin") else 20
            qf.setPointSize(int(size))
            if hasattr(self.pos_widget, "set_block_font"):
                self.pos_widget.set_block_font(int(idx), qf)
            self.pos_widget.update()
        except Exception:
            pass

    def _refresh_controls_for_selection(self, idx: int) -> None:
        """根据选中的块刷新左侧控件显示（文本、字体、字号、对齐）。"""
        try:
            self._syncing_controls = True
            if idx is None or int(idx) < 0:
                # 未选中：显示默认/控件字体与当前单选状态，不做应用
                if hasattr(self, "caption_text_edit"):
                    self.caption_text_edit.setText("")
                # 保持现有单选状态与字体控件，不强制重置
                self._syncing_controls = False
                return
            blocks = getattr(self.pos_widget, "_blocks", [])
            if int(idx) >= len(blocks):
                self._syncing_controls = False
                return
            b = blocks[int(idx)]
            # 文本
            if hasattr(self, "caption_text_edit"):
                self.caption_text_edit.setText(str(b.get("text", "")))
            # 字体与字号
            f: QtGui.QFont = b.get("font", self.pos_widget.font())
            if hasattr(self, "font_combo"):
                try:
                    self.font_combo.setCurrentFont(f)
                except Exception:
                    pass
            if hasattr(self, "font_size_spin"):
                try:
                    sz = f.pointSize() if f.pointSize() > 0 else (f.pixelSize() or 12)
                    self.font_size_spin.setValue(int(sz))
                except Exception:
                    pass
            # 对齐
            a = b.get("align", getattr(self.pos_widget, "_align", "left"))
            try:
                self.align_left.setChecked(a == "left")
                self.align_center.setChecked(a == "center")
                self.align_right.setChecked(a == "right")
            except Exception:
                pass
        finally:
            self._syncing_controls = False

    def _on_selection_changed(self, idx: int) -> None:
        """选中拖拽块变化时，更新左侧“字幕文本”输入框内容。"""
        try:
            if idx is None or int(idx) < 0:
                self.caption_text_edit.setText("")
                return
            blocks = getattr(self.pos_widget, "_blocks", [])
            if int(idx) < len(blocks):
                text = str(blocks[int(idx)].get("text", ""))
                self.caption_text_edit.setText(text)
        except Exception:
            pass

    def _on_caption_text_commit(self) -> None:
        """提交左侧“字幕文本”：只作用于当前选中的拖拽块；未选中则弹窗提示。"""
        try:
            idx = self.pos_widget.get_selected_index() if hasattr(self.pos_widget, "get_selected_index") else getattr(self.pos_widget, "_selected_idx", -1)
            if idx is None or int(idx) < 0:
                QtWidgets.QMessageBox.information(self, "提示", "请先在下方选择一个字幕块再修改文本。")
                return
            text = self.caption_text_edit.text()
            if hasattr(self.pos_widget, "set_block_text"):
                self.pos_widget.set_block_text(int(idx), text)
            else:
                # 兼容路径：直接写入内部结构
                blocks = getattr(self.pos_widget, "_blocks", [])
                if int(idx) < len(blocks):
                    blocks[int(idx)]["text"] = text
                    self.pos_widget.update()
        except Exception:
            pass

    def _on_selection_changed(self, idx: int) -> None:
        """选中拖拽块变化时，刷新左侧控件数据以匹配该块。"""
        try:
            self._refresh_controls_for_selection(idx)
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
            caption = self.pos_widget.get_text().strip()
        except Exception:
            caption = ""
        align = "left"
        if self.align_center.isChecked():
            align = "center"
        elif self.align_right.isChecked():
            align = "right"
        pos_x, pos_y = self.pos_widget.get_position()
        # 收集所有字幕块以支持多字幕块生成
        captions_blocks: list[tuple[str, tuple[float, float]]] = []
        try:
            for b in self.pos_widget.get_blocks():
                t = str(b.get("text", "")).strip()
                p = b.get("position", (0.0, 0.0))
                if t:
                    captions_blocks.append((t, (float(p[0]), float(p[1]))))
        except Exception:
            captions_blocks = []

        # 线程与工作者
        self._thread = QtCore.QThread(self)
        self._worker = GenerateCoverWorker()
        # 将多字幕块传给工作者（避免更改信号签名）
        try:
            setattr(self._worker, "_captions", captions_blocks)
        except Exception:
            pass
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
