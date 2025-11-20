"""
Video Concat GUI (PySide6)
Windows desktop GUI to orchestrate the workflow in concat_tool/video_concat.py.

Features:
- Map CLI options to GUI controls
- Run tasks on a background thread (QThread) with progress and logs
- Validate inputs; environment checks (ffmpeg/NVENC) handled by gui.precheck
- Prepare for building a Windows .exe via PyInstaller

Author: Your Team
"""

from __future__ import annotations

import sys
# from dataclasses import dataclass, asdict  # no longer used in this module
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtWidgets, QtGui

# Ensure imports work both in development and PyInstaller-frozen runtime.
# In frozen mode, bundled packages are available without modifying sys.path.
# In development mode, add project root so `concat_tool` can be imported.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not getattr(sys, "frozen", False):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

# 线程与设置的生命周期已迁移到各自的 Tab 内部，MainWindow 不再直接导入
from gui.tabs.extract_frames_tab import ExtractFramesTab
from gui.tabs.video_concat_tab import VideoConcatTab
from gui.tabs.generate_cover_tab import GenerateCoverTab
from gui.tabs.video_bgm_replace_tab import VideoBgmReplaceTab

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
            # 缩小整体窗口初始高度，默认约 900x560
            w, h = 900, 560
            if screen:
                r = screen.availableGeometry()
                # 初始尺寸按屏幕宽度50%、高度45% 计算，整体更紧凑
                w = max(w, int(r.width() * 0.50))
                h = max(h, int(r.height() * 0.45))
                self.resize(w, h)
            else:
                self.resize(w, h)
        except Exception:
            self.resize(w, h)
        # 下调最小尺寸，允许用户将窗口缩到更小高度
        try:
            self.setMinimumSize(720, 480)
            # self.setMaximumSize(720, 480)
        except Exception:
            pass

        # Widgets（改为基于 QTabWidget 的架构）
        self.tabs = QtWidgets.QTabWidget(self)
        self.setCentralWidget(self.tabs)
        # 注册tab页
        self._register_feature_tabs(self.tabs)
    
    def _register_feature_tabs(self, tabs: QtWidgets.QTabWidget) -> None:
        """ 批量注册功能标签页到主窗口的 QTabWidget 中。"""
        tabs_mapping = [
            {
                "tab_name": "视频混剪",
                "tab_widget": VideoConcatTab(self),
            },
            {
                "tab_name": "视频截图",
                "tab_widget": ExtractFramesTab(self),
            },
            {
                "tab_name": "合成封面",
                "tab_widget": GenerateCoverTab(self),
            },
            {
                "tab_name": "BGM替换",
                "tab_widget": VideoBgmReplaceTab(self),
            },
        ]
        # 注册到
        for tab_item in tabs_mapping:
            tab_name = tab_item["tab_name"]
            tab_widget = tab_item["tab_widget"]

            tabs.addTab(tab_widget, tab_name)
            try:
                tab_widget.setContentsMargins(6, 6, 6, 6)
            except Exception:
                pass

    # ==== 统一运行态检测与停止请求 ====
    def _is_tab_running(self, tab: Optional[QtWidgets.QWidget]) -> bool:
        """Return whether the given tab currently has a running background task.

        统一通过各标签页公开的 `is_running()` 方法判断运行态。
        若标签页未实现该方法或调用失败，则视为不在运行（返回 False）。
        """
        try:
            if tab is None:
                return False
            if hasattr(tab, "is_running") and callable(getattr(tab, "is_running")):
                return bool(getattr(tab, "is_running")())
            # 未提供公共接口则按不在运行处理
            return False
        except Exception:
            return False

    def _request_tab_stop(self, tab: Optional[QtWidgets.QWidget]) -> None:
        """Politely request the given tab to stop its running task.

        统一调用各标签页公开的 `request_stop()` 方法发起软停止。
        若标签页未实现该方法或调用失败，则不进行额外的内部属性/方法回退。
        """
        try:
            if tab is None:
                return
            if hasattr(tab, "request_stop") and callable(getattr(tab, "request_stop")):
                try:
                    getattr(tab, "request_stop")()
                except Exception:
                    pass
        except Exception:
            pass

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
            # 检测所有标签页是否存在运行中的任务
            tabs = [getattr(self, "concat_tab", None), getattr(self, "extract_tab", None), getattr(self, "cover_tab", None)]
            running_tabs = [t for t in tabs if self._is_tab_running(t)]
            if running_tabs:
                ret = QtWidgets.QMessageBox.question(
                    self,
                    "确认退出",
                    "检测到有任务在后台运行，退出将尝试停止所有任务并关闭程序。是否继续？",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                if ret != QtWidgets.QMessageBox.Yes:
                    return
                # 软停止所有运行中的任务（委托给各 Tab）
                for t in running_tabs:
                    try:
                        self._request_tab_stop(t)
                    except Exception:
                        pass
            QtWidgets.QApplication.quit()
        except Exception:
            QtWidgets.QApplication.quit()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        """Intercept window close.

        当有后台任务运行时，关闭窗口不会直接退出应用，而是将窗口隐藏到系统托盘，
        并在托盘中继续运行任务。用户可通过托盘菜单选择"退出"来结束程序。
        """
        try:
            # 统一检测各标签页运行态
            tabs = [getattr(self, "concat_tab", None), getattr(self, "extract_tab", None), getattr(self, "cover_tab", None)]
            running_tabs = [t for t in tabs if self._is_tab_running(t)]
            if running_tabs:
                # 隐藏到托盘
                self._ensure_tray()
                if getattr(self, "tray_icon", None):
                    try:
                        self.tray_icon.show()
                        # 提示继续后台运行
                        self.tray_icon.showMessage(
                            "后台运行",
                            "检测到任务未完成，窗口已隐藏到系统托盘。",
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
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
