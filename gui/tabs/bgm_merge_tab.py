"""
BGM Merge Tab (skeleton)

A placeholder GUI tab for future BGM merge features. This tab provides a
minimal layout and descriptive text, and can be expanded to integrate with
merge_bgm_tool/merge_bgm.py.

Design goals
- Offer a ready-to-register QWidget tab with clear docstrings.
- Keep implementation lightweight until actual business logic is integrated.
"""

from __future__ import annotations

from typing import Optional
from PySide6 import QtWidgets


class BgmMergeTab(QtWidgets.QWidget):
    """
    Skeleton tab for BGM merging features.

    This widget currently displays a title, a short description and a button
    linking users to project documentation. It is intended to be expanded with
    real controls (file pickers, parameters, start/stop) and connected to the
    merge_bgm_tool module.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        """
        Initialize the skeleton tab UI.

        Parameters
        ----------
        parent : Optional[QtWidgets.QWidget]
            The parent widget (QTabWidget or MainWindow).
        """
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)

        title = QtWidgets.QLabel("BGM 合并（规划中）")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        desc = QtWidgets.QLabel(
            "将多个视频的音轨与指定 BGM 进行合并的功能页。\n\n"
            "规划功能：\n"
            "- 选择视频文件或目录\n"
            "- 选择 BGM 文件或目录\n"
            "- 设置淡入/淡出、音量、混合策略\n"
            "- 并发处理与结果展示\n\n"
            "当前为占位页，后续将接入 merge_bgm_tool/merge_bgm.py。"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        btn_doc = QtWidgets.QPushButton("查看文档（README_v3.md）")
        btn_doc.clicked.connect(self._open_readme_v3)
        layout.addWidget(btn_doc)

        layout.addStretch(1)

    def _open_readme_v3(self) -> None:
        """
        Open README_v3.md in the system's default file manager.
        """
        import os
        import subprocess
        from utils.common_utils import get_subprocess_silent_kwargs
        try:
            base = os.path.dirname(os.path.dirname(__file__))
            readme_path = os.path.join(base, "README_v3.md")
            if os.path.exists(readme_path):
                if os.name == "nt":
                    subprocess.Popen(["explorer", readme_path], **get_subprocess_silent_kwargs())
                else:
                    subprocess.Popen(["open", readme_path])
            else:
                QtWidgets.QMessageBox.information(self, "提示", f"未找到文档：{readme_path}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"打开文档失败：{e}")