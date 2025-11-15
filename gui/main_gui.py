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
import re
from pathlib import Path
from typing import List, Optional

from PySide6 import QtCore, QtWidgets, QtGui

# Ensure imports work both in development and PyInstaller-frozen runtime.
# In frozen mode, bundled packages are available without modifying sys.path.
# In development mode, add project root so `concat_tool` can be imported.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not getattr(sys, "frozen", False):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

from gui.workers.video_concat_worker import VideoConcatWorker
from concat_tool.settings import Settings  # type: ignore
from gui.precheck import run_preflight_checks
from gui.tabs.extract_frames_tab import ExtractFramesTab
from gui.tabs.video_concat_tab import VideoConcatTab
from gui.tabs.generate_cover_tab import GenerateCoverTab
from gui.utils.table_helpers import resolve_display_name, set_table_row_colors
# 预检逻辑已抽象到 gui.precheck.preflight 模块，main_gui 保留调用点即可。

# Settings dataclass已迁移至 concat_tool.settings 供 GUI/CLI 复用。
# NOTE: VideoConcatWorker 已迁移至 gui.workers.video_concat_worker 模块，
# 以实现 GUI 与业务逻辑分离。此处不再定义该类。

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
        # 注册“视频混剪”标签页（保持与其他标签页一致的构建/注册模式）
        try:
            self.concat_tab = VideoConcatTab(self)
            self.register_feature_tab("视频混剪", self.concat_tab)
            # 构建“视频混剪”标签页的左/右面板与布局（此前代码误置于 _open_readme_v3 内导致未初始化）
            self._init_concat_tab_ui()
        except Exception:
            # 若初始化失败，不影响其他标签页；用户将看到空白页
            pass
    
         # 注册“生成截图”标签页
        try:
            extract_tab = ExtractFramesTab(self)
            self.register_feature_tab("生成截图", extract_tab)
        except Exception:
            # 若加载失败，不影响其他标签页
            pass

        # 注册“合成封面”标签页
        try:
            cover_tab = GenerateCoverTab(self)
            self.register_feature_tab("合成封面", cover_tab)
        except Exception:
            # 若加载失败，不影响其他标签页
            pass
        
        # # 注册已有的“封面生成”标签页骨架（可选扩展）
        # try:
        #     cover_tab = CoverGeneratorTab(self)
        #     self.register_feature_tab("封面生成", cover_tab)
        # except Exception:
        #     # 若加载失败，不影响主功能页
        #     pass

       

        # # 注册“BGM 合并”占位标签页（规划中）
        # try:
        #     bgm_tab = BgmMergeTab(self)
        #     self.register_feature_tab("BGM 合并", bgm_tab)
        # except Exception:
        #     pass

        # 占位标签页：更多功能（开发中）
        more_tab = QtWidgets.QWidget()
        _more_layout = QtWidgets.QVBoxLayout(more_tab)
        _title_label = QtWidgets.QLabel("更多功能（开发中）")
        _title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        _desc_label = QtWidgets.QLabel(
            "以下功能正在规划或开发中：\n"
            "- 批量封面生成与导出\n"
            "- BGM 智能匹配与自动淡入淡出\n"
            "- 视频剪辑预览与快捷标注\n"
            "- 结果表格导出 CSV/Excel\n\n"
            "更多信息请参见 README_v3.md。"
        )
        _desc_label.setWordWrap(True)
        _btn_open_doc = QtWidgets.QPushButton("查看文档（README_v3.md）")
        _btn_open_doc.clicked.connect(self._open_readme_v3)
        _more_layout.addWidget(_title_label)
        _more_layout.addWidget(_desc_label)
        _more_layout.addWidget(_btn_open_doc)
        _more_layout.addStretch(1)
        self.register_feature_tab("更多功能", more_tab)

        # 线程与工作者初始状态：未运行
        # 避免在首次点击开始时访问未定义属性导致异常
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[VideoConcatWorker] = None

    def register_feature_tab(self, title: str, widget: QtWidgets.QWidget) -> int:
        """
        将功能页统一注册到主窗口的 QTabWidget 中，并返回注册后的索引。

        Args:
            title: 标签页标题，如 "视频混剪"。
            widget: 需要注册的标签页小部件（QWidget 或其子类）。

        Returns:
            新增标签页在 QTabWidget 中的索引位置（int）。

        Notes:
            - 该方法用于统一标签页注册入口，便于后续添加样式或统一行为。
            - 可在此处添加通用的边距、样式或信号连接。
        """
        index = self.tabs.addTab(widget, title)
        try:
            widget.setContentsMargins(6, 6, 6, 6)
        except Exception:
            pass
        return index

    def _init_concat_tab_ui(self) -> None:
        """
        构建“视频混剪”标签页的左侧参数区域与右侧运行/结果区域，并完成信号连接。

        注意：此前由于一次误合并，页面构建代码被错误地放入 _open_readme_v3，
        导致用户启动后该标签页为空白。此方法恢复正确的初始化位置。
        """
        # 交由 Tab 完成“左/右面板 + 分割器”的整体构建（符合面板嵌套原则）
        self.concat_tab.build_page()
        # 左侧控件与分组的引用由 Tab 自行管理；MainWindow 不再进行回填
        # 保持与旧逻辑兼容：创建 GPU/缓存清理复选框（不加入布局，默认隐藏）
        # self.gpu_chk = QtWidgets.QCheckBox("启用GPU(NVENC)"); self.gpu_chk.setChecked(True); self.gpu_chk.setVisible(False)
        # self.clear_cache_chk = QtWidgets.QCheckBox("清理不匹配TS缓存"); self.clear_cache_chk.setChecked(False); self.clear_cache_chk.setVisible(False)
        # 左侧数值控件的紧凑化与质量档位联动已在 Tab 内完成，无需在 MainWindow 连接

        # 右侧运行控件（按钮、进度）迁移至 Tab 内部统一构建与管理

        # 右侧控件由 Tab 管理；MainWindow 不依赖其直接引用，仅订阅事件与路由工作者信号
        # 订阅 Tab 发出的开始/停止请求信号，由 MainWindow 统一路由到现有处理器。
        try:
            self.concat_tab.start_requested.connect(self._on_start_with_settings)
        except Exception:
            pass
        try:
            self.concat_tab.stop_requested.connect(self._on_stop_requested)
        except Exception:
            pass

        # 分割器与左右面板均已在 Tab 内完成构建，无需在 MainWindow 再次添加
        # 如需在启动后调整分配比例，可在此处调用 Tab 内的分割器暴露接口（未来可选）

    def _open_readme_v3(self) -> None:
        """
        Open the README_v3.md in the system's default file manager.

        This helper simply reveals the documentation file so users can
        view plans and usage notes. It does not build or modify any UI.
        """
        import os
        import subprocess
        readme_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "README_v3.md")
        try:
            if os.path.exists(readme_path):
                if os.name == "nt":
                    subprocess.Popen(["explorer", readme_path])
                else:
                    subprocess.Popen(["open", readme_path])
            else:
                QtWidgets.QMessageBox.information(self, "提示", f"未找到文档：{readme_path}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"打开文档失败：{e}")
        return

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

    def _on_start_with_settings(self, settings: Settings) -> None:
        """
        Start the background worker using a Settings object provided by the tab.

        Design
        ------
        This is the single entry point for starting the concat task. The
        VideoConcatTab collects all form values via `collect_settings()` and
        emits them through its `start_requested` signal. MainWindow only
        orchestrates the worker lifecycle and never re-reads UI controls for
        settings, ensuring a clean separation of responsibilities.

        Parameters
        ----------
        settings : Settings
            The collected parameters for the concat task.
        """
        if self._thread is not None:
            QtWidgets.QMessageBox.warning(self, "提示", "已有任务在运行")
            return
        # 基础校验：视频目录与 BGM 路径必须填写
        try:
            if not getattr(settings, "video_dirs", None):
                QtWidgets.QMessageBox.warning(self, "提示", "请先选择至少一个视频目录")
                return
            if not getattr(settings, "bgm_path", None):
                QtWidgets.QMessageBox.warning(self, "提示", "请先选择 BGM 路径（文件或目录）")
                return
        except Exception:
            QtWidgets.QMessageBox.warning(self, "提示", "采集参数失败，请检查表单输入")
            return
        # 显示右下“输出结果”蒙层并禁用列表交互（由 Tab 统一处理）
        self.concat_tab.show_results_overlay()
        # 新任务开始前由 Tab 自行处理进度条样式与初始值（MainWindow 不直接操作控件）
        # 启动线程与工作者
        self._thread = QtCore.QThread(self)
        self._worker = VideoConcatWorker(settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # 路由工作者信号到标签页的更新接口（MainWindow 仅负责生命周期）
        self._worker.phase.connect(self.concat_tab.set_progress_stage)
        self._worker.progress.connect(self.concat_tab.set_progress_value)
        self._worker.finished.connect(self._on_finished)
        self._worker.results.connect(self.concat_tab.update_results)
        self._worker.error.connect(self._on_error)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()
        self.concat_tab.set_running_ui_state(True)

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
        # 迁移后统一委托给 Tab 层接口，避免 MainWindow 直接操作控件
        self.concat_tab.set_progress_value(done, total)

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
            # 完成后以绿色显示块，直到下一次开始（由 Tab 样式驱动）
            self.concat_tab.apply_progress_style(chunk_color="#22c55e")
        except Exception:
            pass
        # 关闭蒙层，恢复交互
        try:
            self.concat_tab.hide_results_overlay()
        except Exception:
            pass
        self._cleanup_thread()

    def _on_results_ready(self, paths: List[str]) -> None:
        """委托 Tab 层填充结果表，保持 MainWindow 仅进行事件路由。"""
        try:
            self.concat_tab.update_results(paths)
        except Exception:
            pass

    # 已迁移：路径规范化与展示名生成逻辑由 Tab 层实现（见 VideoConcatTab._normalize_result_path 与 table_helpers.resolve_display_name）

    def _get_result_path_by_row(self, row: int) -> Optional[Path]:
        """委托 Tab 层解析选中行的输出路径。"""
        try:
            return self.concat_tab._get_result_path_by_row(row)
        except Exception:
            return None

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
        # 局部导入 subprocess，避免在模块顶层保留未必要的依赖
        import subprocess
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

        
    def _on_error(self, msg: str) -> None:
        """Display error and stop the worker.

        Parameters
        ----------
        msg : str
            Error message to show.
        """
        QtWidgets.QMessageBox.critical(self, "错误", msg)
        self.concat_tab.hide_results_overlay()
        self._cleanup_thread()

    def _cleanup_thread(self) -> None:
        """释放线程与工作者资源，并交由 Tab 复位运行态 UI。

        Notes
        -----
        - MainWindow 不再维护控件的兜底启停状态，统一委托给 Tab 层。
        - 仅负责线程的退出与对象引用清理；阶段标签回到 idle，进度值保持不变。
        """
        try:
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(2000)
        except Exception:
            pass
        self._thread = None
        # 交由 Tab 恢复运行态 UI
        self.concat_tab.set_running_ui_state(False)
        # 清理 worker 引用
        self._worker = None
        # 阶段标签回到 idle，进度值保持不变：委托给 Tab 层
        try:
            # 使用类型安全的阶段接口
            self.concat_tab.set_stage("idle")
        except Exception:
            pass



    def _on_phase(self, phase_text: str) -> None:
        """阶段更新槽：委托给 Tab 接口处理阶段标签与进度条配色。"""
        try:
            self.concat_tab.set_progress_stage(phase_text)
        except Exception:
            pass
        # 注意：阶段更新不应更改开始/停止按钮状态或清理线程；这些逻辑由 _cleanup_thread 统一处理。
        # 进度条重置策略：保留在完成后 100%，仅在下次开始任务前重置，在 _on_start_with_settings 中执行。

    def _on_stop_requested(self) -> None:
        """Handle stop requests from the tab or exit flow.

        Performs a soft stop by quitting the worker thread. Any running
        ffmpeg subprocess will finish the current item. This method is the
        single entry point for stop behavior, wired to VideoConcatTab.stop_requested
        and used by exit handling.
        """
        try:
            self.concat_tab.hide_results_overlay()
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
                self._on_stop_requested()
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
    if not run_preflight_checks(app):
        # 用户确认后退出，或授权校验失败
        return
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
