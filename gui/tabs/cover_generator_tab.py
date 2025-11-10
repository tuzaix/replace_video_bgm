"""
CoverGeneratorTab GUI skeleton.

This module provides a tab widget for generating stitched cover images
(screen_cover). It focuses on UI and event wiring, leaving business logic
integration (cover_tool.generate_cover) for later steps.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtWidgets


class CoverGeneratorTab(QtWidgets.QWidget):
    """封面生成（screen_cover）功能的GUI骨架标签页。

    该标签页提供基础的参数输入与占位交互，后续可接入
    cover_tool.generate_cover.generate_covers_concurrently 完成真实业务逻辑。

    当前包含的控件：
    - 图片目录选择（images_dir）
    - 字幕文本（caption）
    - 每个封面使用的图片数量（per_cover）
    - 生成封面数量（count）
    - 线程数（workers）
    - 字幕颜色（color）
    - 生成/停止按钮与简易日志区域
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._bind_events()

    def _build_ui(self) -> None:
        """构建封面生成的表单与布局。"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 顶部说明
        title = QtWidgets.QLabel("封面生成（screen_cover）")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        subtitle = QtWidgets.QLabel(
            "从图片目录随机抽取若干图片，横向拼接生成封面，可叠加字幕。\n"
            "后续将接入 cover_tool.generate_cover 的并发生成逻辑。"
        )
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)

        # 图片目录
        self.images_dir_edit = QtWidgets.QLineEdit()
        self.images_dir_edit.setPlaceholderText("请选择包含图片的目录（jpg/jpeg/png/webp/bmp）")
        self.images_dir_edit.setClearButtonEnabled(True)
        btn_browse_dir = QtWidgets.QPushButton("浏览…")
        dir_row = QtWidgets.QHBoxLayout()
        dir_row.addWidget(self.images_dir_edit)
        dir_row.addWidget(btn_browse_dir)
        dir_container = QtWidgets.QWidget()
        dir_container.setLayout(dir_row)
        form.addRow("图片目录", dir_container)

        # 字幕文本
        self.caption_edit = QtWidgets.QLineEdit()
        self.caption_edit.setPlaceholderText("可选：叠加到封面上的字幕文本")
        form.addRow("字幕", self.caption_edit)

        # 每个封面的图片数量
        self.per_cover_spin = QtWidgets.QSpinBox()
        self.per_cover_spin.setRange(1, 12)
        self.per_cover_spin.setValue(4)
        form.addRow("每个封面图片数", self.per_cover_spin)

        # 生成封面数量
        self.count_spin = QtWidgets.QSpinBox()
        self.count_spin.setRange(1, 9999)
        self.count_spin.setValue(10)
        form.addRow("生成数量", self.count_spin)

        # 线程数（workers）
        self.workers_spin = QtWidgets.QSpinBox()
        try:
            _cpu = max(1, int(os.cpu_count() or 1))
        except Exception:
            _cpu = 4
        self.workers_spin.setRange(1, 32)
        self.workers_spin.setValue(min(8, _cpu))
        form.addRow("并发线程数", self.workers_spin)

        # 字幕颜色
        self.color_combo = QtWidgets.QComboBox()
        self.color_combo.addItems(["yellow", "white", "black", "red", "green", "blue"])
        self.color_combo.setCurrentText("yellow")
        form.addRow("字幕颜色", self.color_combo)

        layout.addLayout(form)

        # 操作按钮
        btns = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("生成封面")
        self.stop_btn = QtWidgets.QPushButton("停止")
        self.stop_btn.setEnabled(False)
        btns.addStretch(1)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        layout.addLayout(btns)

        # 简易日志区域
        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("生成日志将显示在此处…")
        layout.addWidget(self.log_view)

        # 记录浏览按钮引用，供绑定事件使用
        self._btn_browse_dir = btn_browse_dir

    def _bind_events(self) -> None:
        """连接基本事件处理。"""
        self._btn_browse_dir.clicked.connect(self._on_browse_images_dir)
        self.start_btn.clicked.connect(self._on_start_generate)
        self.stop_btn.clicked.connect(self._on_stop_generate)

    def _on_browse_images_dir(self) -> None:
        """弹出目录选择对话框并写入输入框。"""
        try:
            dlg = QtWidgets.QFileDialog(self)
            dlg.setFileMode(QtWidgets.QFileDialog.Directory)
            dlg.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
            if dlg.exec() == QtWidgets.QDialog.Accepted:
                selected = dlg.selectedFiles()
                if selected:
                    self.images_dir_edit.setText(selected[0])
        except Exception as e:
            self._log(f"选择目录失败: {e}")

    def _validate_inputs(self) -> Optional[str]:
        """校验输入参数。

        Returns
        -------
        Optional[str]
            返回错误消息；若无错误返回 None。
        """
        images_dir = self.images_dir_edit.text().strip()
        if not images_dir:
            return "请先选择图片目录"
        try:
            p = Path(images_dir)
            if not p.exists() or not p.is_dir():
                return "图片目录不存在或不是目录"
        except Exception:
            return "图片目录路径无效"
        return None

    def _collect_params(self) -> dict:
        """收集参数为后续接入业务逻辑做准备。"""
        return {
            "images_dir": self.images_dir_edit.text().strip(),
            "caption": self.caption_edit.text().strip() or None,
            "count": int(self.count_spin.value()),
            "per_cover": int(self.per_cover_spin.value()),
            "workers": int(self.workers_spin.value()),
            "color": self.color_combo.currentText().strip() or "yellow",
        }

    def _on_start_generate(self) -> None:
        """占位的“开始生成”事件处理。

        当前为骨架：仅进行输入校验与参数收集，然后输出到日志。
        后续将替换为线程化的真正生成逻辑与结果列表展示。
        """
        err = self._validate_inputs()
        if err:
            self._log(err)
            return
        params = self._collect_params()
        self._log("[准备生成] 参数如下：")
        for k, v in params.items():
            self._log(f"- {k}: {v}")
        self._log("提示：当前为骨架实现，后续会接入并发生成逻辑与结果预览。")

    def _on_stop_generate(self) -> None:
        """占位的“停止生成”事件处理。"""
        self._log("停止请求：当前为骨架实现，无正在运行的任务。")

    def _log(self, msg: str) -> None:
        """向日志视图追加一行文本。"""
        try:
            self.log_view.append(msg)
        except Exception:
            pass