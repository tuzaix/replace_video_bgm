"""
Video Concat Tab factory

This module provides a lightweight factory function to create the
"视频混剪" tab container and its top-level layout, so the main GUI can
attach all existing controls into this tab without rewriting business
logic.

Design goals
- Keep the current MainWindow code unchanged in terms of variable names
  and event handlers; only move the tab container creation out.
- Enable future refactor to fully encapsulate the concat tab into a
  dedicated class without breaking existing behavior.
"""

from typing import Optional, Tuple, List, Literal
import re
from pathlib import Path
from PySide6 import QtWidgets, QtCore, QtGui
from gui.utils import theme
from gui.utils.table_helpers import ensure_table_headers, resolve_display_name, set_table_row_colors
from gui.utils.overlay import BusyOverlay
# 在当前阶段，逐步迁移右侧结果面板的构建到 Tab 内部


def create_concat_tab(parent: Optional[QtWidgets.QWidget] = None) -> Tuple[QtWidgets.QWidget, QtWidgets.QHBoxLayout]:
    """Create the "视频混剪" tab container and its root horizontal layout.

    Parameters
    ----------
    parent : Optional[QtWidgets.QWidget]
        The parent widget, typically the QTabWidget or MainWindow.

    Returns
    -------
    Tuple[QtWidgets.QWidget, QtWidgets.QHBoxLayout]
        A tuple of (tab_widget, root_layout). The caller should use
        the returned layout as the top-level layout to assemble the
        existing controls. The tab_widget should be added to a
        QTabWidget via addTab(tab_widget, "视频混剪").

    Notes
    -----
    This function intentionally does not add the tab into any QTabWidget.
    The caller (main_gui) is responsible for registering the tab with
    its title. This keeps the factory pure and avoids hidden side effects.
    """
    tab = QtWidgets.QWidget(parent)
    root_layout = QtWidgets.QHBoxLayout(tab)
    return tab, root_layout


class VideoConcatTab(QtWidgets.QWidget):
    """
    Encapsulated "视频混剪" tab widget.

    This class provides a dedicated container for the concat page so that
    page-specific UI and event wiring can be gradually migrated from
    MainWindow into this module without breaking existing behavior.

    Attributes
    ----------
    root_layout : QtWidgets.QHBoxLayout
        The top-level layout used to assemble left/right panels.

    Notes
    -----
    - The class is initially a thin wrapper with a single root layout.
      Over time, controls and handlers can be moved inside.
    - The existing factory `create_concat_tab` remains for backward
      compatibility. MainWindow can choose either approach.
    """

    # Signals exposed to MainWindow for worker orchestration
    start_requested = QtCore.Signal(object)
    """
    Emitted when the user requests to start the concat task.

    Payload: a Settings-like object (or dict) that represents the
    current form values. To keep compatibility during migration,
    this signal can carry an opaque object. MainWindow remains
    responsible for creating the VideoConcatWorker and wiring
    thread lifecycle.
    """

    stop_requested = QtCore.Signal()
    """
    Emitted when the user requests to stop the running task.

    MainWindow should handle soft-stop or cleanup behavior.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        """
        Initialize the concat tab container with a root horizontal layout.

        Parameters
        ----------
        parent : Optional[QtWidgets.QWidget]
            The parent widget, typically the QTabWidget or MainWindow.
        """
        super().__init__(parent)
        self.root_layout = QtWidgets.QHBoxLayout(self)
        # 在当前迁移阶段，Tab 仅作为容器，具体控件仍由 MainWindow 构建并加入到此 root_layout。
        # 后续将逐步把控件与事件处理迁移到 Tab 内部。
        # 右侧面板控件占位：由 MainWindow 构建后注入（attach），以便本类的 update_* 方法直接控制
        self.phase_label: Optional[QtWidgets.QLabel] = None
        self.progress_bar: Optional[QtWidgets.QProgressBar] = None
        self.results_table: Optional[QtWidgets.QTableWidget] = None
        # 结果蒙层（BusyOverlay）在本 Tab 内构建与持有，避免 MainWindow 直接管理该细节
        self._results_overlay: Optional[QtWidgets.QWidget] = None
        # 运行控制按钮引用（开始/停止），以便在 Tab 内部应用样式与尺寸自适应
        self.start_btn: Optional[QtWidgets.QPushButton] = None
        self.stop_btn: Optional[QtWidgets.QPushButton] = None
        # 左侧输出路径自动填充开关（默认启用，用户手动编辑后关闭）
        self._output_autofill: bool = True

        # 质量档位与填充模式映射（在本 Tab 内维护一份，便于构建控件与展示）
        self._profile_display_to_code = {
            "均衡": "balanced",
            "观感优先": "visual",
            "压缩优先": "size",
        }
        self._profile_code_to_display = {v: k for k, v in self._profile_display_to_code.items()}
        self._fill_display_to_code = {
            "居中黑边": "pad",
            "裁剪满屏": "crop",
        }
        self._fill_code_to_display = {v: k for k, v in self._fill_display_to_code.items()}

    def get_root_layout(self) -> QtWidgets.QHBoxLayout:
        """
        Return the root layout so that callers can attach existing panels.

        Returns
        -------
        QtWidgets.QHBoxLayout
            The top-level layout of this tab.
        """
        return self.root_layout

    def _build_ui(self) -> None:
        """
        当前阶段不在 Tab 内部构建具体控件，保持为空。
        MainWindow 仍负责创建并将控件加入到本 Tab 的 root_layout。
        后续迭代将逐步将 UI 构建迁移至此方法。
        """
        return

    def build_left_panel(self) -> QtWidgets.QWidget:
        """
        构建并返回左侧面板（输入与参数区域，普通面板）。

        结构
        ----
        - left_container 使用 QVBoxLayout(left_layout) 依次加入：
          1) 输入与路径分组（视频目录、BGM路径、输出路径）
          2) 基本流程参数分组
          3) 编码参数分组

        间距策略
        ----
        - 左侧容器的内边距保持为 0，以贴合窗口顶部；
        - 左侧容器的布局间距设置为 8，确保分组之间有清晰的层次空间；
        - 表单分组内部采用适度的边距与行距，提升可读性。

        返回
        ----
        QtWidgets.QWidget
            左侧普通面板（包含输入与参数分组），可直接加入主分割器。
        """
        # 先构建各控件/分组（沿用现有方法，保持引用与行为兼容）
        _inputs = self.build_input_widgets()
        _flow = self.build_flow_params_group()
        _enc = self.build_encoding_params_group()

        # 左侧容器与布局（普通 QWidget，不使用滚动面板）
        left_container = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_container)
        try:
            # 左侧容器边距为 0，布局间距为 8，体现层次但不浪费顶部空间
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.setSpacing(8)
        except Exception:
            pass

        # 输入与路径分组（表单）
        input_group = QtWidgets.QGroupBox("输入与路径")
        try:
            input_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass
        input_form = QtWidgets.QFormLayout()
        try:
            input_form.setContentsMargins(10, 8, 10, 8)
            input_form.setHorizontalSpacing(14)
            input_form.setVerticalSpacing(8)
        except Exception:
            pass
        input_form.addRow(_inputs["dir_group"])
        input_form.addRow("BGM路径", _inputs["bgm_hbox"])
        input_form.addRow("输出路径", _inputs["out_hbox"]) 
        input_group.setLayout(input_form)
        left_layout.addWidget(input_group)

        # 将“基本流程参数”与“编码参数”分组加入左侧布局
        left_layout.addWidget(_flow["group"])  # 基本流程参数
        left_layout.addWidget(_enc["group"])   # 编码参数分组

        try:
            left_container.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
            # 更贴近 35:65 比例，同时兼顾较小窗口的显示
            left_container.setMinimumWidth(500)
        except Exception:
            pass

        # ---- 左侧控件行为与菜单绑定（迁移自 MainWindow） ----
        try:
            # 视频目录添加/移除
            if hasattr(self, "btn_add_dir") and hasattr(self, "btn_rm_dir"):
                self.btn_add_dir.clicked.connect(self.on_add_dir)  # type: ignore[attr-defined]
                self.btn_rm_dir.clicked.connect(self.on_rm_dir)    # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            # BGM 输入提示与行为
            if hasattr(self, "bgm_path_edit") and self.bgm_path_edit is not None:  # type: ignore[attr-defined]
                self.bgm_path_edit.setPlaceholderText("支持选择音频文件或目录")  # type: ignore[attr-defined]
                self.bgm_path_edit.setClearButtonEnabled(True)  # type: ignore[attr-defined]
                self.bgm_path_edit.setToolTip("选择单个音频文件（mp3/wav/aac/flac/m4a/ogg等）或包含多个音频的目录")  # type: ignore[attr-defined]
                self.bgm_path_edit.textChanged.connect(self.validate_bgm_path)  # type: ignore[attr-defined]
            if hasattr(self, "bgm_browse_btn") and self.bgm_browse_btn is not None:  # type: ignore[attr-defined]
                self.bgm_browse_btn.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)  # type: ignore[attr-defined]
                _bgm_menu = QtWidgets.QMenu(self)
                _bgm_act_file = _bgm_menu.addAction("选择音频文件…")
                _bgm_act_dir = _bgm_menu.addAction("选择目录…")
                self.bgm_browse_btn.setMenu(_bgm_menu)  # type: ignore[attr-defined]
                # 默认点击选择文件，目录选择通过下拉菜单触发
                self.bgm_browse_btn.clicked.connect(self.on_browse_bgm_file)  # type: ignore[attr-defined]
                _bgm_act_file.triggered.connect(self.on_browse_bgm_file)
                _bgm_act_dir.triggered.connect(self.on_browse_bgm_dir)
        except Exception:
            pass
        try:
            # 输出路径提示与行为
            if hasattr(self, "output_edit") and self.output_edit is not None:  # type: ignore[attr-defined]
                self.output_edit.setPlaceholderText("默认：第一个视频目录同级的 ‘<目录名>_longvideo’")  # type: ignore[attr-defined]
                self.output_edit.setClearButtonEnabled(True)  # type: ignore[attr-defined]
                self.output_edit.textEdited.connect(self.on_output_text_edited)  # type: ignore[attr-defined]
            if hasattr(self, "output_browse_btn") and self.output_browse_btn is not None:  # type: ignore[attr-defined]
                self.output_browse_btn.clicked.connect(self.on_browse_output)  # type: ignore[attr-defined]
            # 初始化自动填充默认输出目录
            QtCore.QTimer.singleShot(0, self.update_output_default)
        except Exception:
            pass
        try:
            # 质量档位与填充模式的联动（迁移自 MainWindow 的连接）
            if hasattr(self, "profile_combo") and self.profile_combo is not None:  # type: ignore[attr-defined]
                self.profile_combo.currentTextChanged.connect(self.on_profile_changed)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            left_container.setFont(QtWidgets.QApplication.font())
            left_container.setStyleSheet("")
        except Exception:
            pass

        return left_container

    def apply_compact_field_sizes(self) -> None:
        """
        统一将左侧的数值输入控件(QSpinBox/QDoubleSpinBox)宽度缩小为更紧凑的尺寸（Tab 内部执行）。

        目的
        ----
        - 减少水平占用，让标签和值排版更紧凑，避免左侧布局过宽；
        - 使用 Fixed 宽度策略避免在表单布局中被拉伸。

        规则
        ----
        - QSpinBox：最大宽度 80 像素；
        - QDoubleSpinBox：最大宽度 100 像素（保留小数显示空间）。
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

    def build_right_panel(self) -> QtWidgets.QWidget:
        """构建并返回右侧面板（包含运行状态与结果面板）。

        内容
        ----
        - 运行状态分组：阶段标签、进度条、开始/停止按钮（统一样式与 DPI 自适应）。
        - 结果分组：结果表格、动作栏（打开文件 / 复制路径）、BusyOverlay（淡入淡出）。
        - 垂直分割器：上（运行状态）/下（结果）。

        返回
        ----
        QtWidgets.QWidget
            右侧容器小部件，内部包含分割器与各分组。

        说明
        ----
        - 本方法将右侧面板的控件构建与信号连接集中在 Tab 内部，
          以便逐步减少 MainWindow 对具体控件的直接管理。
        - 构建完成后，Tab 成员变量（phase_label、progress_bar、start_btn、stop_btn、results_table）
          均会被设置，以便 MainWindow 继续引用而不破坏现有逻辑。
        """
        # 容器与布局
        right_container = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_container)
          # 左侧容器边距为 0，布局间距为 4，更紧凑以匹配左侧整体高度
        try:
            # 缩小右侧容器的内边距与间距，以减少整体占用高度
            right_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.setSpacing(4)
        except Exception:
            pass

        # ---- 运行状态分组 ----
        progress_group = QtWidgets.QGroupBox("运行状态")
        try:
            progress_group.setStyleSheet("QGroupBox::title { font-weight: 600; }")
        except Exception:
            pass
        _top_v = QtWidgets.QVBoxLayout(progress_group)
        try:
            # 缩小运行状态分组的内边距与间距，压缩垂直高度
            _top_v.setContentsMargins(2, 2, 2, 2)
            _top_v.setSpacing(6)
        except Exception:
            pass

        # 阶段与进度条 + 单一动作按钮（右侧）
        try:
            # 构建进度控件；阶段标签不再加入布局，改为将阶段文本展示到进度条上
            self.phase_label, self.progress_bar = self.build_progress_widgets()
            # 进度行：进度条 + 动作按钮（开始/结束切换）
            bar_row = QtWidgets.QHBoxLayout()
            bar_row.setContentsMargins(0, 0, 0, 0)
            # 收紧行内间距，使整体更紧凑
            bar_row.setSpacing(4)
            bar_row.addWidget(self.progress_bar, 1)
            # 单一动作按钮，默认“开始”，点击后切换为“结束”
            self.action_btn = QtWidgets.QPushButton("开始")
            try:
                # 固定宽度，避免与进度条竞争空间
                self.action_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                # self.action_btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                # 根据 DPI 自适应宽度，保证在高分辨率下也易点
                screen = QtWidgets.QApplication.primaryScreen()
                dpi = screen.logicalDotsPerInch() if screen else 96.0
                scale = max(1.0, dpi / 96.0)
                # min_w = int(max(100, min(140, 110 * scale)))
                # self.action_btn.setMinimumWidth(min_w)
                # 初始提示：空闲态下提示
                self.action_btn.setToolTip("点击开始")
            except Exception:
                pass
            # 初始运行态标记
            self._is_running = False
            try:
                self.action_btn.clicked.connect(self._on_action_clicked)
            except Exception:
                pass
            # 无障碍：为关键控件设置可访问名称，便于读屏与自动化测试识别
            try:
                self.action_btn.setAccessibleName("concat_action_button")
                if getattr(self, "progress_bar", None) is not None:
                    self.progress_bar.setAccessibleName("concat_progress_bar")
            except Exception:
                pass
            
            bar_row.addWidget(self.action_btn)
            _top_v.addLayout(bar_row)
            # 应用进度条自适应样式
            self.apply_progress_style(chunk_color="#3b82f6")
            # 样式统一（适配单按钮）
            self.apply_action_buttons_style(self.action_btn, None, base_h=28, base_pt=11)
            # 初始化为空闲态样式，使“开始”按钮外观与任务完成后保持一致
            try:
                self.set_running_ui_state(False)
            except Exception:
                pass
        except Exception:
            pass

        # ---- 结果分组 ----
        results_group, self.results_table = self.build_results_panel()
        _rg_layout = results_group.layout()
        if isinstance(_rg_layout, QtWidgets.QVBoxLayout):
            try:
                # 统一为更紧凑的边距与间距，匹配左侧分组的视觉密度
                _rg_layout.setContentsMargins(6, 4, 6, 4)
                _rg_layout.setSpacing(6)
            except Exception:
                pass
        try:
            # 双击打开文件
            self.results_table.itemDoubleClicked.connect(self.on_results_table_double_clicked)
        except Exception:
            pass

        # 结果动作栏
        try:
            actions_bar = QtWidgets.QHBoxLayout()
            actions_bar.setContentsMargins(0, 0, 0, 0)
            actions_bar.setSpacing(4)
            open_selected_btn = QtWidgets.QPushButton("打开文件")
            copy_selected_path_btn = QtWidgets.QPushButton("复制路径")
            actions_bar.addWidget(open_selected_btn)
            actions_bar.addWidget(copy_selected_path_btn)
            actions_bar.addStretch(1)
            if isinstance(_rg_layout, QtWidgets.QVBoxLayout):
                _rg_layout.addLayout(actions_bar)
            try:
                open_selected_btn.clicked.connect(self.on_open_selected_files)
            except Exception:
                pass
            try:
                copy_selected_path_btn.clicked.connect(self.copy_selected_paths)
            except Exception:
                pass
            # 可选：保存引用（不强依赖）
            self._open_selected_btn = open_selected_btn  # type: ignore[attr-defined]
            self._copy_selected_path_btn = copy_selected_path_btn  # type: ignore[attr-defined]
        except Exception:
            pass

        # 尺寸策略
        try:
            progress_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            results_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass

        # 垂直分割器
        right_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        try:
            right_splitter.setChildrenCollapsible(False)
        except Exception:
            pass
        right_splitter.addWidget(progress_group)
        right_splitter.addWidget(results_group)
        try:
            # 根据用户要求调整为 1:9（上:下），强调下半部分结果区域
            right_splitter.setStretchFactor(0, 1)
            right_splitter.setStretchFactor(1, 9)
            # 可选：提供初始高度，便于默认布局体现 1:9 的视觉倾向
            # right_splitter.setSizes([160, 640])
        except Exception:
            pass
        right_layout.addWidget(right_splitter)
        try:
            right_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass

        # 最后，确保 Tab 拥有这些控件的引用（供 MainWindow 继续使用）
        try:
            self.attach_right_panel_controls(
                progress_bar=self.progress_bar,
                results_table=self.results_table,
                results_overlay=self._results_overlay,
                start_btn=self.start_btn,
                stop_btn=self.stop_btn,
            )
        except Exception:
            pass

        return right_container

    def build_page(self) -> None:
        """
        在标签页内部完成“左/右面板 + 分割器”的整体页面构建，并挂载到 root_layout。

        设计原则
        --------
        - 遵循 MainWindow -> Tab -> 左Panel + 右Panel 的嵌套层次；
        - 左Panel 内部包含输入与参数的多个分组（输入与路径、基本流程参数、编码参数）；
        - 右Panel 内部包含运行状态与结果分组，并通过垂直分割器组织；
        - Tab 自己持有分割器与所有控件引用，MainWindow 只订阅信号与进行生命周期管理。

        使用方法
        --------
        - MainWindow 在注册 Tab 后调用 `concat_tab.build_page()` 即可完成页面搭建；
        - 控件引用（如 self.count_spin 等）在前面的构建过程中已写入到 Tab 实例属性中。
        """
        # 左右面板
        left_panel = self.build_left_panel()
        right_container = self.build_right_panel()

        # 分割器（水平）
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_container)
        try:
            # 优化左右比例为 30:70（左:右）。
            # 说明：
            # - setStretchFactor 控制调整大小时的权重；
            # - setSizes 仅作为初始尺寸的建议，后续以 stretch 因子为主；
            # - 若窗口总宽度较小，左侧的最小宽度（600）可能导致初始实际比例略有偏差。
            splitter.setStretchFactor(0, 30)
            splitter.setStretchFactor(1, 70)
            splitter.setSizes([300, 700])
        except Exception:
            pass
        # 将分割器加入 Tab 的根布局
        try:
            # 保持较小的外边距，防止内容贴边过紧
            self.root_layout.setContentsMargins(6, 6, 6, 6)
        except Exception:
            pass
        self.root_layout.addWidget(splitter)
        # 在页面构建完成后，统一收紧左侧数值控件的宽度
        try:
            self.apply_compact_field_sizes()
        except Exception:
            pass

    def build_results_panel(self) -> tuple[QtWidgets.QGroupBox, QtWidgets.QTableWidget]:
        """
        Build the right-side results panel (group box and table) inside the tab.

        Returns
        -------
        (QtWidgets.QGroupBox, QtWidgets.QTableWidget)
            The results group container and the table widget.

        Notes
        -----
        - This method is idempotent; if the table already exists, it will
          return the existing widgets.
        - BusyOverlay 在本方法中统一创建并持有，避免在 MainWindow 中直接管理，
          迁移阶段仍支持通过 `attach_right_panel_controls` 注入外部已有实例。
        """
        # If already built, return existing
        if self.results_table is not None:
            # Find the parent group via the table's parent if possible
            parent = self.results_table.parent()
            if isinstance(parent, QtWidgets.QGroupBox):
                return parent, self.results_table
            # Fallback: create a new group and re-parent the table
        results_group = QtWidgets.QGroupBox("混剪长视频的结果")
        _rg_layout = QtWidgets.QVBoxLayout(results_group)
        try:
            # 缩小结果分组的内边距与间距，压缩垂直高度
            _rg_layout.setContentsMargins(6, 6, 6, 6)
            _rg_layout.setSpacing(6)
        except Exception:
            pass
        self.results_table = QtWidgets.QTableWidget(0, 4, results_group)
        ensure_table_headers(self.results_table, ["序号", "文件名", "大小(MB)", "输出路径"])  # 列头与现有逻辑保持一致
        # 缩小文件列表的默认高度，以匹配上侧分组高度
        # 原为 180，后调 140；进一步按用户要求压缩到 120
        try:
            self.results_table.setMinimumHeight(120)
        except Exception:
            pass
        # 选择与编辑行为保持与现有一致
        try:
            self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.results_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            header = self.results_table.horizontalHeader()
            self.results_table.verticalHeader().setVisible(False)
            # 列宽策略迁移至 Tab 内部，保持与 MainWindow 原有设置一致
            header.setMinimumSectionSize(60)
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        except Exception:
            pass
        _rg_layout.addWidget(self.results_table)
        # 结果组的尺寸策略与现有一致
        try:
            # 垂直方向使用 Preferred，避免在 splitter 中过度扩展
            results_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        except Exception:
            pass
        # 在 Tab 内部统一创建并持有 BusyOverlay，避免 MainWindow 直接管理该细节
        try:
            self.build_results_overlay(results_group)
        except Exception:
            pass
        return results_group, self.results_table

    def build_progress_widgets(self) -> tuple[Optional[QtWidgets.QLabel], QtWidgets.QProgressBar]:
        """
        Build phase label and progress bar widgets for the top-right progress area.

        Returns
        -------
        (QtWidgets.QLabel, QtWidgets.QProgressBar)
            The phase label and the progress bar.

        Notes
        -----
        - The caller (MainWindow) may further adjust DPI-aware styling
          and layout placement. This method ensures sensible defaults.
        """
        # 阶段标签仅保留引用，不再显示在界面；阶段文本改为显示在进度条上
        # 不再创建阶段标签（统一在进度条文字中展示阶段信息）
        self.phase_label = None
        self.progress_bar = QtWidgets.QProgressBar()
        try:
            self.progress_bar.setMinimum(0)
            self.progress_bar.setMaximum(100)
            self.progress_bar.setValue(0)
            self.progress_bar.setTextVisible(True)
            self.progress_bar.setAlignment(QtCore.Qt.AlignCenter)
            # 默认展示“状态: 空闲 | 进度: %p%”，运行时通过 update_phase() 动态更新阶段文本
            self.progress_bar.setFormat("状态: 空闲 | 进度: %p%")
            self.progress_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass
        return self.phase_label, self.progress_bar

    def build_results_overlay(self, parent_group: QtWidgets.QGroupBox) -> BusyOverlay:
        """
        Create and attach a BusyOverlay onto the given results group.

        Parameters
        ----------
        parent_group : QtWidgets.QGroupBox
            The results group box that the overlay should cover.

        Returns
        -------
        BusyOverlay
            The created overlay instance, hidden by default.

        Notes
        -----
        - The overlay listens to parent resize/move to stay fitted.
        - This method sets initial geometry to the parent's rect.
        """
        # 统一主题参数：Backdrop 与 Spinner 颜色
        primary_blue = QtGui.QColor("#2563eb")  # Tailwind blue-600
        backdrop = "rgba(17, 24, 39, 160)"  # gray-900 with ~63% opacity
        overlay = BusyOverlay(
            parent_group,
            spinner_color=primary_blue,
            backdrop_rgba=backdrop,
            label_text="处理中…",
            label_color="#ffffff",
            label_font_px=13,
            label_weight=600,
        )
        try:
            overlay.setGeometry(parent_group.rect())
        except Exception:
            pass
        try:
            overlay.hide()
        except Exception:
            pass
        self._results_overlay = overlay
        return overlay

    def attach_right_panel_controls(
        self,
        progress_bar: QtWidgets.QProgressBar,
        results_table: QtWidgets.QTableWidget,
        results_overlay: Optional[QtWidgets.QWidget] = None,
        start_btn: Optional[QtWidgets.QPushButton] = None,
        stop_btn: Optional[QtWidgets.QPushButton] = None,
    ) -> None:
        """
        Attach existing right-panel controls built by MainWindow to this tab.

        This transitional method allows the tab to own references to UI
        elements without changing the current layout composition, enabling
        update_* methods to manipulate them directly.

        Parameters
        ----------
        phase_label : QtWidgets.QLabel
            Deprecated. 阶段文本已并入进度条文字，不再使用独立标签。
        progress_bar : QtWidgets.QProgressBar
            The progress bar widget.
        results_table : QtWidgets.QTableWidget
            The results table widget.
        results_overlay : Optional[QtWidgets.QWidget]
            The busy overlay widget (if available).
        start_btn : Optional[QtWidgets.QPushButton]
            The "Start" action button.
        stop_btn : Optional[QtWidgets.QPushButton]
            The "Stop" action button.
        """
        self.progress_bar = progress_bar
        self.results_table = results_table
        self._results_overlay = results_overlay
        self.start_btn = start_btn
        self.stop_btn = stop_btn

        # 在迁移阶段，将开始/停止按钮的点击信号接入到 Tab 的请求信号上，
        # 由 MainWindow 统一处理线程生命周期。
        try:
            if self.start_btn is not None:
                self.start_btn.clicked.connect(self._on_start_clicked)
            if self.stop_btn is not None:
                self.stop_btn.clicked.connect(self._on_stop_clicked)
        except Exception:
            # 保持迁移安全，连接失败不抛出到上层
            pass

    def _on_start_clicked(self) -> None:
        """
        处理“开始”按钮点击事件。

        职责：采集当前表单设置并通过 start_requested 信号通知 MainWindow。

        注意：实际的工作线程创建与运行由 MainWindow 负责，本方法不直接
        启动任何耗时任务，确保 UI 模块与业务逻辑解耦。
        """
        try:
            settings_obj = self.collect_settings()
        except Exception:
            settings_obj = None
        # 发出开始请求信号
        try:
            self.start_requested.emit(settings_obj)
        except Exception:
            pass

    def _on_stop_clicked(self) -> None:
        """
        处理“结束”按钮点击事件。

        职责：通知 MainWindow 执行软停止或清理线程资源。
        """
        try:
            self.stop_requested.emit()
        except Exception:
            pass

    def _on_action_clicked(self) -> None:
        """单一动作按钮点击事件处理。

        逻辑互斥：
        - 若当前为空闲（未运行），则触发“开始”，并将按钮文案切换为“结束”；
        - 若当前为运行中，则触发“结束”，并将按钮文案切换为“开始”。

        注意：实际运行态以 MainWindow 的生命周期控制为准；本方法仅发出请求信号，
        UI 的最终状态由 set_running_ui_state 同步更新，确保一致性。
        """
        try:
            running = bool(getattr(self, "_is_running", False))
        except Exception:
            running = False
        if not running:
            # 触发开始
            try:
                # 临时禁用，防止快速连击造成重复请求；最终由 set_running_ui_state 统一恢复
                if getattr(self, "action_btn", None) is not None:
                    self.action_btn.setEnabled(False)
                self._on_start_clicked()
            except Exception:
                pass
            # 先行切换文案，最终状态由 MainWindow 回调 set_running_ui_state 确认
            try:
                self._is_running = True
                if getattr(self, "action_btn", None) is not None:
                    self.action_btn.setText("结束")
            except Exception:
                pass
            # 兜底：若 2.5 秒内未收到运行态更新，则重新启用按钮
            try:
                timer = QtCore.QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda: self.action_btn.setEnabled(True) if getattr(self, "action_btn", None) is not None else None)
                timer.start(2500)
            except Exception:
                pass
        else:
            # 触发停止
            try:
                if getattr(self, "action_btn", None) is not None:
                    self.action_btn.setEnabled(False)
                self._on_stop_clicked()
            except Exception:
                pass
            try:
                self._is_running = False
                if getattr(self, "action_btn", None) is not None:
                    self.action_btn.setText("开始")
            except Exception:
                pass
            # 兜底：若 2.5 秒内未收到空闲态更新，则重新启用按钮
            try:
                timer = QtCore.QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda: self.action_btn.setEnabled(True) if getattr(self, "action_btn", None) is not None else None)
                timer.start(2500)
            except Exception:
                pass

    def set_running_ui_state(self, running: bool) -> None:
        """
        切换运行时 UI 按钮可用状态。

        参数
        ------
        running : bool
            True 表示任务运行中：禁用开始、启用停止；
            False 表示空闲：启用开始、禁用停止。

        说明
        ----
        该方法集中管理开始/停止按钮的互斥逻辑，避免散落在 MainWindow，
        便于后续统一应用样式或动画反馈。
        """
        try:
            # 同步内部运行态标记
            self._is_running = bool(running)
            # 传统双按钮：互斥启停
            if self.start_btn is not None:
                self.start_btn.setEnabled(not running)
            if self.stop_btn is not None:
                self.stop_btn.setEnabled(running)
            # 单一动作按钮：切换文案与提示
            if getattr(self, "action_btn", None) is not None:
                try:
                    self.action_btn.setText("结束" if running else "开始")
                    # 单按钮始终可点，由主线程生命周期保证互斥，不在此禁用
                    self.action_btn.setEnabled(True)
                    # 根据状态更新提示（移除快捷键说明）
                    if running:
                        self.action_btn.setToolTip("点击结束")
                    else:
                        self.action_btn.setToolTip("点击开始")
                except Exception:
                    pass
            # 进度条颜色反馈：运行中为蓝色；非运行时若未完成则置灰
            try:
                if getattr(self, "progress_bar", None) is not None:
                    if running:
                        self.apply_progress_style(chunk_color="#3b82f6")
                    else:
                        # 若已达成最大值（完成），保留当前样式（通常为绿色）；否则置灰
                        val = self.progress_bar.value()
                        mx = self.progress_bar.maximum()
                        if isinstance(val, int) and isinstance(mx, int) and val < mx:
                            self.apply_progress_style(chunk_color="#9ca3af")  # gray-400
            except Exception:
                pass
            # 同步应用样式反馈
            self._apply_start_stop_styles(running)
        except Exception:
            pass

    def _apply_start_stop_styles(self, running: bool) -> None:
        """
        根据运行状态为开始/停止按钮应用统一的样式反馈。

        样式约定（类 Tailwind）：
        - 空闲（running=False）：
            - 开始按钮：主色背景、白色文字；
            - 停止按钮：浅灰背景、深色文字；
        - 运行中（running=True）：
            - 开始按钮：禁用外观（浅灰背景，深灰文字）；
            - 停止按钮：强调色背景、白色文字。
        """
        try:
            # 高度与进度条保持一致，若不可用则回退到主题高度
            try:
                pb_h = self.progress_bar.height() if getattr(self, "progress_bar", None) is not None else 0
            except Exception:
                pb_h = 0
            height = pb_h if isinstance(pb_h, int) and pb_h > 0 else theme.BUTTON_HEIGHT
            # 颜色配置（来自统一主题）
            primary_bg = theme.PRIMARY_BLUE
            primary_bg_hover = theme.PRIMARY_BLUE_HOVER
            danger_bg = theme.DANGER_RED
            danger_bg_hover = theme.DANGER_RED_HOVER
            gray_bg = theme.GRAY_BG
            gray_text = theme.GRAY_TEXT

            # 空闲态样式
            idle_start = (
                f"QPushButton{{min-height:{height}px;max-height:{height}px;padding:{theme.BUTTON_PADDING_VERTICAL}px {theme.BUTTON_PADDING_HORIZONTAL}px;"
                f"border:none;border-radius:{theme.BUTTON_RADIUS}px;color:#ffffff;background-color:{primary_bg};}}"
                f"QPushButton:hover{{background-color:{primary_bg_hover};}}"
                f"QPushButton:pressed{{background-color:{primary_bg_hover};}}"
                f"QPushButton:disabled{{color: rgba(255,255,255,0.8);background-color:#93c5fd;}}"  # blue-300
            )
            idle_stop = (
                f"QPushButton{{min-height:{height}px;max-height:{height}px;padding:{theme.BUTTON_PADDING_VERTICAL}px {theme.BUTTON_PADDING_HORIZONTAL}px;"
                f"border:1px solid #d1d5db;border-radius:{theme.BUTTON_RADIUS}px;color:{gray_text};background-color:{gray_bg};}}"
                f"QPushButton:hover{{background-color:#d1d5db;}}"
                f"QPushButton:pressed{{background-color:#d1d5db;}}"
                f"QPushButton:disabled{{color: rgba(55,65,81,0.6);background-color:#f3f4f6;border:1px solid #e5e7eb;}}"
            )

            # 运行态样式
            running_start = (
                f"QPushButton{{min-height:{height}px;max-height:{height}px;padding:{theme.BUTTON_PADDING_VERTICAL}px {theme.BUTTON_PADDING_HORIZONTAL}px;"
                f"border:1px solid #e5e7eb;border-radius:{theme.BUTTON_RADIUS}px;color: rgba(55,65,81,0.7);background-color:#f3f4f6;}}"
                f"QPushButton:hover{{background-color:#e5e7eb;}}"
                f"QPushButton:pressed{{background-color:#d1d5db;}}"
                f"QPushButton:disabled{{color: rgba(55,65,81,0.6);background-color:#f9fafb;border:1px solid #e5e7eb;}}"
            )
            running_stop = (
                f"QPushButton{{min-height:{height}px;max-height:{height}px;padding:{theme.BUTTON_PADDING_VERTICAL}px {theme.BUTTON_PADDING_HORIZONTAL}px;"
                f"border:none;border-radius:{theme.BUTTON_RADIUS}px;color:#ffffff;background-color:{danger_bg};}}"
                f"QPushButton:hover{{background-color:{danger_bg_hover};}}"
                f"QPushButton:pressed{{background-color:{danger_bg_hover};}}"
                f"QPushButton:disabled{{color: rgba(255,255,255,0.8);background-color:#fca5a5;}}"  # red-300
            )

            if self.start_btn is not None:
                self.start_btn.setStyleSheet(running_start if running else idle_start)
            if self.stop_btn is not None:
                self.stop_btn.setStyleSheet(running_stop if running else idle_stop)
            # 单按钮样式：根据运行态选择开始或停止样式
            if getattr(self, "action_btn", None) is not None:
                self.action_btn.setStyleSheet(running_stop if running else idle_start)
                # 同步按钮的固定高度以匹配进度条
                try:
                    self.action_btn.setFixedHeight(height)
                except Exception:
                    pass
        except Exception:
            # 样式失败不影响功能
            pass

    # ---- Transitional UI update helpers (delegating to MainWindow) ----
    def update_progress(self, done: int, total: int) -> None:
        """
        Update progress on the tab.

        During the migration phase, delegate to MainWindow's
        existing handler if available. This keeps behavior unchanged
        while providing a clear tab-level API.
        """
        # 优先更新注入到 Tab 的控件
        if self.progress_bar is not None:
            try:
                self.progress_bar.setMaximum(total)
                self.progress_bar.setValue(done)
            except Exception:
                pass
            return
        # 迁移阶段：回退到 MainWindow 的处理器
        try:
            mw = self._get_main_window()
            if mw and hasattr(mw, "_on_progress"):
                mw._on_progress(done, total)
        except Exception:
            pass

    def update_phase(self, phase_text: str) -> None:
        """
        更新进度条文字中的阶段文本，并应用对应的配色样式。

        说明：
        - 原先显示在独立 QLabel 的阶段文字，现统一并入进度条的文字中；
        - 颜色样式依旧根据阶段键应用，保证运行态的视觉反馈一致；
        - 若进度条不可用，则回退到 MainWindow 的处理器。
        """
        # 归一化阶段键与展示文本
        try:
            stage_key = self._normalize_stage_key(phase_text)
        except Exception:
            stage_key = "idle"
        try:
            display_text = theme.STAGE_TEXT_MAP.get(stage_key, phase_text)
        except Exception:
            display_text = phase_text
        # 优先更新注入到 Tab 的控件
        if self.progress_bar is not None:
            try:
                # 将阶段文本合并到进度条的显示文字中
                self.progress_bar.setFormat(f"状态: {display_text} | 进度: %p%")
                # 颜色样式迁移至 Tab：根据阶段键选择颜色并应用
                color = theme.STAGE_COLOR_MAP.get(stage_key, "#3b82f6")
                self.apply_progress_style(chunk_color=color)
            except Exception:
                pass
            return
        # 迁移阶段：回退到 MainWindow 的处理器
        try:
            mw = self._get_main_window()
            if mw and hasattr(mw, "_on_phase"):
                mw._on_phase(display_text)
        except Exception:
            pass

    def apply_progress_style(self, chunk_color: str = "#3b82f6") -> None:
        """
        根据当前屏幕 DPI 自适应地设置进度条高度与字体大小，并应用指定块颜色。

        参数
        ----
        chunk_color : str
            进度条填充块颜色（如 #3b82f6 蓝色、#f59e0b 橙色、#22c55e 绿色）。

        行为
        ----
        - 按屏幕 DPI 计算缩放因子，设置进度条的高度与字体大小。
        - 设置进度条的样式表，其中块颜色根据参数决定。

        兼容性
        ----
        - 若 progress_bar 尚未注入，则静默返回。
        """
        try:
            if self.progress_bar is None:
                return
            # 尺寸策略：横向扩展，纵向固定
            self.progress_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            # 计算 DPI 缩放
            screen = QtWidgets.QApplication.primaryScreen()
            dpi = screen.logicalDotsPerInch() if screen else 96.0
            scale = max(1.0, dpi / 96.0)
        except Exception:
            scale = 1.0
        # 自适应高度与字号（设上下限防止过大/过小）
        # 缩小进度条的基础高度与上限，压缩右侧上半部分的占用空间
        base_h = 32
        height = int(max(28, min(52, base_h * scale)))
        try:
            self.progress_bar.setFixedHeight(height)
        except Exception:
            pass
        try:
            font = self.progress_bar.font()
            base_pt = 11
            pt_size = int(max(base_pt, min(16, base_pt * scale)))
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

    def _color_for_stage(self, phase_text: str) -> str:
        """
        Map phase text to a progress chunk color.

        Parameters
        ----------
        phase_text : str
            阶段文本（来自工作者信号或主窗口路由）。

        Returns
        -------
        str
            十六进制颜色字符串，用于进度条块颜色。

        Rules
        -----
        - 预处理/扫描：橙色 (#f59e0b)
        - 合并/混合：蓝色 (#3b82f6)
        - 完成/结束：绿色 (#22c55e)
        - 默认：蓝色 (#3b82f6)
        """
        try:
            pt = (phase_text or "").lower()
            # Normalize to stage keys based on keywords
            if "预处理" in phase_text or "pre" in pt or "scan" in pt:
                return theme.STAGE_COLOR_MAP.get("preprocess", "#f59e0b")
            if "混合" in phase_text or "concat" in pt or "merge" in pt:
                return theme.STAGE_COLOR_MAP.get("concat", "#3b82f6")
            if "完成" in phase_text or "finish" in pt or "done" in pt:
                return theme.STAGE_COLOR_MAP.get("finished", "#22c55e")
        except Exception:
            pass
        return theme.STAGE_COLOR_MAP.get("idle", "#3b82f6")

    def _normalize_stage_key(self, phase_text: str) -> Literal["idle", "preprocess", "concat", "finished"]:
        """
        Normalize free-form phase text to a constrained stage key.

        Parameters
        ----------
        phase_text : str
            文本描述的阶段信息，例如 "预处理"、"拼接"、"完成"，或包含英文关键词。

        Returns
        -------
        Literal["idle", "preprocess", "concat", "finished"]
            归一化后的阶段键，用于统一映射展示文本与颜色。

        Rules
        -----
        - 包含 "预处理"/"pre"/"scan" 归一化为 "preprocess"
        - 包含 "混合"/"拼接"/"concat"/"merge" 归一化为 "concat"
        - 包含 "完成"/"finish"/"done" 归一化为 "finished"
        - 其他情况归一化为 "idle"
        """
        try:
            pt = (phase_text or "").lower()
            # 中文优先匹配，英文关键词兜底
            if "预处理" in phase_text or "pre" in pt or "scan" in pt:
                return "preprocess"
            if "混合" in phase_text or "拼接" in phase_text or "concat" in pt or "merge" in pt:
                return "concat"
            if "完成" in phase_text or "finish" in pt or "done" in pt:
                return "finished"
        except Exception:
            pass
        return "idle"

    def set_progress_value(self, value: int, total: int = 1000) -> None:
        """
        Alias for update_progress to provide a more semantic API name.

        Parameters
        ----------
        value : int
            Current progress on a fixed or dynamic scale.
        total : int, default 1000
            The total units of progress. Defaults to 1000 to match worker emissions.

        Notes
        -----
        - This method simply forwards to update_progress(value, total).
        - Keeping both names allows MainWindow and future code to use
          a unified, semantic interface.
        """
        try:
            self.update_progress(value, total)
        except Exception:
            pass

    def set_progress_stage(self, stage_text: str) -> None:
        """
        Alias for update_phase to provide a more semantic API name.

        Parameters
        ----------
        stage_text : str
            The user-visible phase text, e.g., "预处理", "拼接", "完成".

        Notes
        -----
        - This method simply forwards to update_phase(stage_text).
        - Keeping both names allows callers to express intent more clearly.
        """
        try:
            self.update_phase(stage_text)
        except Exception:
            pass

    def set_stage(self, stage: Literal["idle", "preprocess", "concat", "finished"]) -> None:
        """
        Set the current stage using a constrained vocabulary and update UI.

        Parameters
        ----------
        stage : Literal["idle", "preprocess", "concat", "finished"]
            Normalized stage identifier.

        Behavior
        --------
        - Maps the stage identifier to a localized phase text and forwards to
          update_phase().
        - Centralizes stage management for better type-safety and maintainability.
        """
        try:
            self.update_phase(theme.STAGE_TEXT_MAP.get(stage, str(stage)))
        except Exception:
            pass

    def update_results(self, paths: List[str]) -> None:
        """
        Populate result items in the tab.
        在迁移阶段，优先由 Tab 直接填充 results_table；若表格尚未注入，回退到
        MainWindow 的处理器以保持行为。
        """
        # 优先更新注入到 Tab 的控件
        if self.results_table is not None:
            try:
                self.results_table.setRowCount(0)
            except Exception:
                pass
            for idx, p in enumerate(paths, start=1):
                try:
                    # 兼容：可能包含尾随的"(xx MB)"展示信息，这里规范化为纯路径
                    normalized_p = self._normalize_result_path(str(p))
                    exists_flag = Path(normalized_p).exists()
                    st_size = Path(normalized_p).stat().st_size if exists_flag else 0
                    size_mb = st_size / (1024 * 1024) if st_size else 0.0
                except Exception:
                    normalized_p = str(p)
                    size_mb = 0.0
                    exists_flag = False
                try:
                    row = self.results_table.rowCount()
                    self.results_table.insertRow(row)
                    # 序号
                    idx_item = QtWidgets.QTableWidgetItem(str(idx))
                    idx_item.setTextAlignment(QtCore.Qt.AlignCenter)
                    # 文件名
                    name_item = QtWidgets.QTableWidgetItem(resolve_display_name(normalized_p))
                    # 大小(MB)
                    size_item = QtWidgets.QTableWidgetItem(f"{size_mb:.1f}")
                    size_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                    # 输出路径
                    path_item = QtWidgets.QTableWidgetItem(normalized_p)
                    # 写入 UserRole 以便稳健地获取路径
                    for _it in (idx_item, name_item, size_item, path_item):
                        try:
                            _it.setData(QtCore.Qt.UserRole, normalized_p)
                        except Exception:
                            pass
                    self.results_table.setItem(row, 0, idx_item)
                    self.results_table.setItem(row, 1, name_item)
                    # 列位置：第2列为大小(MB)，第3列为输出路径
                    self.results_table.setItem(row, 2, size_item)
                    self.results_table.setItem(row, 3, path_item)
                    # 行颜色
                    try:
                        set_table_row_colors(self.results_table, row, ok=bool(exists_flag))
                    except Exception:
                        pass
                except Exception:
                    pass
            # 迁移：填充完成后统一调整列宽
            try:
                self._adjust_results_columns()
            except Exception:
                pass
            return
        # 迁移阶段：完全回退到 MainWindow 的处理器
        try:
            mw = self._get_main_window()
            if mw and hasattr(mw, "_on_results_ready"):
                mw._on_results_ready(paths)
        except Exception:
            pass

    def get_selected_paths(self) -> List[Path]:
        """
        Return the list of selected output file paths from the results table.

        Returns
        -------
        List[pathlib.Path]
            A list of file paths corresponding to currently selected rows.

        Notes
        -----
        - If the results table is not available, returns an empty list.
        - Uses the tab's internal row-to-path resolver to ensure robust
          path retrieval even when display text is sanitized.
        """
        out: List[Path] = []
        try:
            if self.results_table is None:
                return out
            sel = self.results_table.selectionModel().selectedRows()
            for mi in sel:
                p = self._get_result_path_by_row(mi.row())
                if p:
                    out.append(p)
        except Exception:
            return out
        return out

    def _normalize_result_path(self, s: str) -> str:
        """Normalize a result string to a pure file path.

        Some outputs may carry a trailing size hint like "(12.3 MB)".
        This method strips such trailing bracketed hints (both halfwidth
        () and fullwidth（）) only when they contain "MB" to avoid
        removing legitimate parentheses in file names.

        Parameters
        ----------
        s : str
            The result string which may include a trailing size hint.

        Returns
        -------
        str
            The normalized pure path string.
        """
        try:
            text = s.strip()
            tail_pattern = re.compile(r"\s*[（(][^（）()]*MB[^（）()]*[）)]\s*$")
            if tail_pattern.search(text):
                text = tail_pattern.sub("", text).strip()
            return text
        except Exception:
            return s

    def apply_action_buttons_style(
        self,
        start_btn: Optional[QtWidgets.QPushButton] = None,
        stop_btn: Optional[QtWidgets.QPushButton] = None,
        base_h: int = 28,
        base_pt: int = 11,
    ) -> None:
        """Apply DPI-aware height, font size and lightweight styles to action buttons.

        Parameters
        ----------
        start_btn : Optional[QtWidgets.QPushButton]
            The start button. If omitted, uses the attached reference.
        stop_btn : Optional[QtWidgets.QPushButton]
            The stop button. If omitted, uses the attached reference.
        base_h : int
            Base height in pixels. Will be scaled by screen DPI with sane limits.
        base_pt : int
            Base font size in points. Will be scaled by screen DPI with limits.

        Notes
        -----
        - This method mirrors the previous MainWindow._apply_action_buttons_style
          logic, but scopes styling to the provided buttons or the tab-attached
          references to avoid affecting other buttons in the application.
        - If neither provided nor attached, this method is a no-op.
        """
        # Resolve buttons: prefer parameters, fallback to attached ones
        start = start_btn or getattr(self, "start_btn", None) or getattr(self, "action_btn", None)
        stop = stop_btn or getattr(self, "stop_btn", None)
        if start is None and stop is None:
            return

        # Compute DPI scale
        try:
            screen = QtWidgets.QApplication.primaryScreen()
            dpi = screen.logicalDotsPerInch() if screen else 96.0
            scale = max(1.0, dpi / 96.0)
        except Exception:
            scale = 1.0

        # Target height: match progress bar height if available; otherwise fallback to DPI-scaled base
        try:
            pb_h = self.progress_bar.height() if getattr(self, "progress_bar", None) is not None else 0
        except Exception:
            pb_h = 0
        if isinstance(pb_h, int) and pb_h > 0:
            height = pb_h
        else:
            height = int(max(28, min(52, base_h * scale)))

        # Font size: align with progress bar font if possible
        try:
            pb_font_pt = self.progress_bar.font().pointSize() if getattr(self, "progress_bar", None) is not None else 0
        except Exception:
            pb_font_pt = 0
        pt_size = pb_font_pt if isinstance(pb_font_pt, int) and pb_font_pt > 0 else int(max(base_pt, min(16, base_pt * scale)))

        # Fix height and size policy
        try:
            if start is not None:
                start.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                start.setFixedHeight(height)
            if stop is not None:
                stop.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                stop.setFixedHeight(height)
        except Exception:
            pass

        # Apply font size
        try:
            if start is not None:
                bf = start.font(); bf.setPointSize(pt_size); start.setFont(bf)
            if stop is not None:
                bf2 = stop.font(); bf2.setPointSize(pt_size); stop.setFont(bf2)
        except Exception:
            pass

        # Lightweight styles with hover/pressed/disabled feedback
        try:
            style = (
                f"QPushButton{{min-height:{height}px;max-height:{height}px;padding:6px 14px;border:1px solid #bfbfbf;border-radius:6px;}}"
                f"QPushButton:hover{{border:1px solid #999999;}}"
                f"QPushButton:pressed{{border:1px solid #888888;background-color: rgba(0,0,0,0.04);}}"
                f"QPushButton:disabled{{color: rgba(0,0,0,0.4);border:1px solid #dddddd;background-color: rgba(0,0,0,0.02);}}"
            )
            if start is not None:
                start.setStyleSheet(style)
            if stop is not None:
                stop.setStyleSheet(style)
        except Exception:
            pass

    def _adjust_results_columns(self) -> None:
        """
        调整结果表格各列的宽度以提升可读性。

        规则与 MainWindow 原有逻辑保持一致：
        - 第0列（序号）：根据内容自适应宽度
        - 第1列（文件名）：至少160px，约占总宽度的25%
        - 第2列（大小MB）：根据内容自适应宽度
        - 第3列（输出路径）：至少240px，约占总宽度的45%

        该方法在填充结果之后调用，保证列宽自适应。
        """
        try:
            if self.results_table is None:
                return
            # 保持与 headers 定义一致的列索引
            size_col = 2
            path_col = 3
            self.results_table.resizeColumnToContents(0)
            self.results_table.setColumnWidth(1, max(160, int(self.results_table.width() * 0.25)))
            self.results_table.resizeColumnToContents(size_col)
            self.results_table.setColumnWidth(path_col, max(240, int(self.results_table.width() * 0.45)))
        except Exception:
            pass

    def show_results_overlay(self) -> None:
        """
        Show the busy overlay over the results region.
        在标签页内部管理 BusyOverlay：若不存在则尝试创建，
        然后显示并置顶，并禁用结果表交互。
        """
        # 若尚未持有蒙层，尝试通过结果表查找父 QGroupBox 并创建
        if self._results_overlay is None:
            try:
                parent_group = self._find_results_group()
                if parent_group is not None:
                    self.build_results_overlay(parent_group)
            except Exception:
                pass
        # 显示并置顶蒙层（支持淡入）
        try:
            if self._results_overlay is not None:
                if hasattr(self._results_overlay, "show_with_fade"):
                    self._results_overlay.show_with_fade(180)
                else:
                    parent_widget = self._results_overlay.parentWidget()
                    if parent_widget is not None:
                        self._results_overlay.setGeometry(parent_widget.rect())
                    self._results_overlay.show()
                    self._results_overlay.raise_()
        except Exception:
            pass
        # 禁用结果交互
        try:
            if self.results_table is not None:
                self.results_table.setEnabled(False)
        except Exception:
            pass

    def hide_results_overlay(self) -> None:
        """
        Hide the busy overlay over the results region.
        标签页内部管理 BusyOverlay：隐藏蒙层并恢复结果表交互。
        """
        try:
            if self._results_overlay is not None:
                if hasattr(self._results_overlay, "hide_with_fade"):
                    self._results_overlay.hide_with_fade(180)
                else:
                    self._results_overlay.hide()
        except Exception:
            pass
        # 恢复结果交互
        try:
            if self.results_table is not None:
                self.results_table.setEnabled(True)
        except Exception:
            pass

    def _find_results_group(self) -> Optional[QtWidgets.QGroupBox]:
        """
        沿父链查找包含结果表的 QGroupBox。

        返回
        -----
        Optional[QtWidgets.QGroupBox]
            若找到则返回该分组框，否则返回 None。
        """
        try:
            w: Optional[QtWidgets.QWidget] = self.results_table
            while w is not None:
                if isinstance(w, QtWidgets.QGroupBox):
                    return w
                w = w.parentWidget()
        except Exception:
            pass
        return None

    def _get_main_window(self) -> Optional[QtWidgets.QWidget]:
        """
        Return the top-level MainWindow if available.

        This helper navigates the parent chain to find the hosting
        main window. It is used to delegate updates during the
        transition period.
        """
        try:
            p = self.parent()
            while p is not None:
                if isinstance(p, QtWidgets.QMainWindow):
                    return p
                p = p.parent()
        except Exception:
            pass
        return None

    def on_results_table_double_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        """Handle double-click on a results table row: reveal the file in the system explorer."""
        try:
            row = item.row()
            path = self._get_result_path_by_row(row)
            if not path:
                QtWidgets.QMessageBox.warning(self, "提示", "无法读取该行的输出路径")
                return
            if not path.exists():
                QtWidgets.QMessageBox.warning(self, "提示", f"文件不存在: {path}")
                return
            self._reveal_in_file_manager([path])
        except Exception:
            pass

    def collect_settings(self) -> object:
        """
        采集当前表单设置用于混剪任务（完整 Settings）。

        迁移策略：
        - 优先从 MainWindow 的控件读取数值型与选择项（count/outputs/gpu/threads/width/height/fps/trim等），
          以兼容当前控件仍在 MainWindow 的阶段；
        - 左侧输入（video_dirs、bgm_path、output）直接使用 Tab 自身的控件值；
        - 若任意控件缺失则回退到 Settings 定义的默认值。

        Returns
        -------
        object
            Settings 实例或兼容对象，包含当前表单参数。
        """
        # 读取左侧控件值
        try:
            video_dirs: List[str] = []
            if hasattr(self, "video_dirs_list") and self.video_dirs_list is not None:  # type: ignore[attr-defined]
                video_dirs = [self.video_dirs_list.item(i).text() for i in range(self.video_dirs_list.count())]  # type: ignore[attr-defined]
            bgm_path: str = ""
            if hasattr(self, "bgm_path_edit") and self.bgm_path_edit is not None:  # type: ignore[attr-defined]
                bgm_path = self.bgm_path_edit.text().strip()  # type: ignore[attr-defined]
            output: Optional[str] = None
            if hasattr(self, "output_edit") and self.output_edit is not None:  # type: ignore[attr-defined]
                out_val = self.output_edit.text().strip()  # type: ignore[attr-defined]
                output = out_val or None
        except Exception:
            # 若采集失败，保持空值并继续尝试委托
            video_dirs = []
            bgm_path = ""
            output = None

        # 从 MainWindow 读取其余参数（若存在），否则使用默认值
        count = 5
        outputs = 1
        gpu = True
        threads = 4
        width = 1080
        height = 1920
        fps = 25
        fill = "pad"
        trim_head = 0.0
        trim_tail = 1.0
        clear_mismatched_cache = False
        group_res = True
        quality_profile = "balanced"
        nvenc_cq = None
        x265_crf = None
        preset_gpu = None
        preset_cpu = None
        mw = self._get_main_window()
        if mw is not None:
            try:
                count = int(getattr(mw, "count_spin").value())
                outputs = int(getattr(mw, "outputs_spin").value())
                gpu = bool(getattr(mw, "gpu_chk").isChecked())
                threads = int(getattr(mw, "threads_spin").value())
                width = int(getattr(mw, "width_spin").value())
                height = int(getattr(mw, "height_spin").value())
                fps = int(getattr(mw, "fps_spin").value())
                # fill code：优先读取 UserRole 数据，否则回退中文/英文文本映射
                fill = self._get_fill_code_from_mw(mw)
                trim_head = float(getattr(mw, "trim_head_dbl").value())
                trim_tail = float(getattr(mw, "trim_tail_dbl").value())
                clear_mismatched_cache = bool(getattr(mw, "clear_cache_chk").isChecked())
                group_res = bool(getattr(mw, "group_res_chk").isChecked())
                quality_profile = self._get_profile_code_from_mw(mw)
                # 覆盖项与预设
                nvenc_val = int(getattr(mw, "nvenc_cq_spin").value())
                x265_val = int(getattr(mw, "x265_crf_spin").value())
                nvenc_cq = nvenc_val if nvenc_val > 0 else None
                x265_crf = x265_val if x265_val > 0 else None
                preset_gpu_txt = str(getattr(mw, "preset_gpu_combo").currentText()).strip()
                preset_cpu_txt = str(getattr(mw, "preset_cpu_combo").currentText()).strip()
                preset_gpu = preset_gpu_txt or None
                preset_cpu = preset_cpu_txt or None
            except Exception:
                pass
        # 构造完整 Settings
        try:
            from concat_tool.settings import Settings  # type: ignore
            return Settings(
                video_dirs=video_dirs,
                bgm_path=bgm_path,
                output=output,
                count=count,
                outputs=outputs,
                gpu=gpu,
                threads=threads,
                width=width,
                height=height,
                fps=fps,
                fill=fill,
                trim_head=trim_head,
                trim_tail=trim_tail,
                clear_mismatched_cache=clear_mismatched_cache,
                group_res=group_res,
                quality_profile=quality_profile,
                nvenc_cq=nvenc_cq,
                x265_crf=x265_crf,
                preset_gpu=preset_gpu,
                preset_cpu=preset_cpu,
            )
        except Exception:
            # 兜底字典结构，保持键一致
            return {
                "video_dirs": video_dirs,
                "bgm_path": bgm_path,
                "output": output,
                "count": count,
                "outputs": outputs,
                "gpu": gpu,
                "threads": threads,
                "width": width,
                "height": height,
                "fps": fps,
                "fill": fill,
                "trim_head": trim_head,
                "trim_tail": trim_tail,
                "clear_mismatched_cache": clear_mismatched_cache,
                "group_res": group_res,
                "quality_profile": quality_profile,
                "nvenc_cq": nvenc_cq,
                "x265_crf": x265_crf,
                "preset_gpu": preset_gpu,
                "preset_cpu": preset_cpu,
            }

    def build_input_widgets(self) -> dict:
        """
        Build left-side input widgets and containers used by the concat task.

        This method creates the widgets for:
        - Video directories list with Add/Remove buttons (group box)
        - BGM path editor with browse tool button (horizontal layout)
        - Output path editor with browse button (horizontal layout)

        Returns
        -------
        dict
            A mapping containing the created widgets and containers:
            {
              "dir_group": QGroupBox,
              "video_dirs_list": QListWidget,
              "btn_add_dir": QPushButton,
              "btn_rm_dir": QPushButton,
              "bgm_hbox": QHBoxLayout,
              "bgm_path_edit": QLineEdit,
              "bgm_browse_btn": QToolButton,
              "out_hbox": QHBoxLayout,
              "output_edit": QLineEdit,
              "output_browse_btn": QPushButton,
            }

        Notes
        -----
        - Signal connections and behavior wiring are intentionally left to
          MainWindow during the migration phase to avoid breaking existing
          logic. Callers should connect the buttons to their handlers.
        - 本方法仅负责构建控件，不绑定事件。为保持现有行为，信号连接仍由
          MainWindow 负责；后续可逐步迁移到 Tab 内部。
        """
        # --- 视频目录（可多选） ---
        video_dirs_list = QtWidgets.QListWidget()
        btn_add_dir = QtWidgets.QPushButton("添加目录")
        btn_rm_dir = QtWidgets.QPushButton("移除选中")
        dir_btns = QtWidgets.QHBoxLayout()
        dir_btns.addWidget(btn_add_dir)
        dir_btns.addWidget(btn_rm_dir)
        dir_container = QtWidgets.QVBoxLayout()
        dir_container.addWidget(video_dirs_list)
        dir_container.addLayout(dir_btns)
        dir_group = QtWidgets.QGroupBox("视频目录（可多选）")
        dir_group.setLayout(dir_container)
        # 视频目录默认值设置为E:\Download\社媒助手\抖音\潮汕菲宝，方便调试
        video_dirs_list.addItem(r"E:\Download\社媒助手\抖音\潮汕菲宝")

        # --- BGM 路径（文件或目录） ---
        bgm_path_edit = QtWidgets.QLineEdit()
        bgm_browse_btn = QtWidgets.QToolButton()
        bgm_browse_btn.setText("浏览…")
        bgm_hbox = QtWidgets.QHBoxLayout()
        bgm_hbox.addWidget(bgm_path_edit)
        bgm_hbox.addWidget(bgm_browse_btn)
        # bgm目录设置为E:\Download\社媒助手\ytb-bgm，方便调试
        bgm_path_edit.setText(r"E:\Download\社媒助手\ytb-bgm")

        # --- 输出路径 ---
        output_edit = QtWidgets.QLineEdit()
        output_browse_btn = QtWidgets.QPushButton("浏览…")
        out_hbox = QtWidgets.QHBoxLayout()
        out_hbox.addWidget(output_edit)
        out_hbox.addWidget(output_browse_btn)

        # 保存引用到 Tab，以便后续迁移时直接在类内部访问
        self.video_dirs_list = video_dirs_list  # type: ignore[attr-defined]
        self.btn_add_dir = btn_add_dir          # type: ignore[attr-defined]
        self.btn_rm_dir = btn_rm_dir            # type: ignore[attr-defined]
        self.bgm_path_edit = bgm_path_edit     # type: ignore[attr-defined]
        self.bgm_browse_btn = bgm_browse_btn   # type: ignore[attr-defined]
        self.output_edit = output_edit         # type: ignore[attr-defined]
        self.output_browse_btn = output_browse_btn  # type: ignore[attr-defined]

        return {
            "dir_group": dir_group,
            "video_dirs_list": video_dirs_list,
            "btn_add_dir": btn_add_dir,
            "btn_rm_dir": btn_rm_dir,
            "bgm_hbox": bgm_hbox,
            "bgm_path_edit": bgm_path_edit,
            "bgm_browse_btn": bgm_browse_btn,
            "out_hbox": out_hbox,
            "output_edit": output_edit,
            "output_browse_btn": output_browse_btn,
        }

    def build_flow_params_group(self) -> dict:
        """
        构建“基本流程参数”分组及其内部控件，并返回引用字典。

        包含控件
        - count_spin: 混剪视频切片数量(n)
        - outputs_spin: 生成混剪长视频数量(m)
        - threads_spin: 线程数
        - group_res_chk: 分辨率分组模式

        返回
        dict: {
            "group": QGroupBox,
            "count_spin": QSpinBox,
            "outputs_spin": QSpinBox,
            "threads_spin": QSpinBox,
            "group_res_chk": QCheckBox,
        }

        说明
        - 与 MainWindow 原有布局保持一致，便于迁移与复用。
        """
        # 数值控件
        count_spin = QtWidgets.QSpinBox(); count_spin.setRange(1, 9999); count_spin.setValue(10)
        outputs_spin = QtWidgets.QSpinBox(); outputs_spin.setRange(1, 9999); outputs_spin.setValue(5)
        threads_spin = QtWidgets.QSpinBox(); threads_spin.setRange(1, 64); threads_spin.setValue(4)
        group_res_chk = QtWidgets.QCheckBox("同分辨率视频拼接（默认即可）"); group_res_chk.setChecked(True)

        # 分组与布局
        flow_group = QtWidgets.QGroupBox("基本流程参数")
        flow_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        flow_grid = QtWidgets.QGridLayout()
        flow_grid.setContentsMargins(10, 8, 10, 8)
        flow_grid.setHorizontalSpacing(16)
        flow_grid.setVerticalSpacing(10)

        lbl_outputs = QtWidgets.QLabel("生成混剪长视频数量(m)")
        lbl_count = QtWidgets.QLabel("混剪视频切片数量(n)")
        lbl_threads = QtWidgets.QLabel("线程数")
        lbl_groupres = QtWidgets.QLabel("同分辨率视频拼接")
        for _lbl in (lbl_count, lbl_outputs, lbl_threads, lbl_groupres):
            _lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        flow_grid.addWidget(lbl_count,   0, 0)
        flow_grid.addWidget(count_spin,   0, 1)
        flow_grid.addWidget(lbl_outputs, 0, 2)
        flow_grid.addWidget(outputs_spin, 0, 3)
        flow_grid.addWidget(lbl_threads, 1, 0)
        flow_grid.addWidget(threads_spin, 1, 1)
        # flow_grid.addWidget(group_res_chk, 1, 2)

        flow_grid.setColumnStretch(0, 0)
        flow_grid.setColumnStretch(1, 1)
        flow_grid.setColumnStretch(2, 0)
        flow_grid.setColumnStretch(3, 1)

        flow_group.setLayout(flow_grid)

        # 保存引用到 Tab（便于后续完全迁移时直接访问）
        self.count_spin = count_spin  # type: ignore[attr-defined]
        self.outputs_spin = outputs_spin  # type: ignore[attr-defined]
        self.threads_spin = threads_spin  # type: ignore[attr-defined]
        self.group_res_chk = group_res_chk  # type: ignore[attr-defined]

        return {
            "group": flow_group,
            "count_spin": count_spin,
            "outputs_spin": outputs_spin,
            "threads_spin": threads_spin,
            "group_res_chk": group_res_chk,
        }

    def build_encoding_params_group(self) -> dict:
        """
        构建“编码参数”分组及其内部控件，并返回引用字典。

        包含控件
        - profile_combo: 质量档位（均衡/观感优先/压缩优先）
        - width_spin, height_spin: 分辨率（宽/高）
        - fill_combo: 填充模式（居中黑边/裁剪满屏）
        - trim_head_dbl, trim_tail_dbl: TS裁剪（头/尾, 秒）
        - fps_spin: 帧率
        - nvenc_cq_spin, x265_crf_spin: 编码器覆盖值（0 表示未覆盖）
        - preset_gpu_combo, preset_cpu_combo: 预设（空字符串表示使用推荐值）

        返回
        dict: {
            "group": QGroupBox,
            "profile_combo": QComboBox,
            "width_spin": QSpinBox,
            "height_spin": QSpinBox,
            "fill_combo": QComboBox,
            "trim_head_dbl": QDoubleSpinBox,
            "trim_tail_dbl": QDoubleSpinBox,
            "fps_spin": QSpinBox,
            "nvenc_cq_spin": QSpinBox,
            "x265_crf_spin": QSpinBox,
            "preset_gpu_combo": QComboBox,
            "preset_cpu_combo": QComboBox,
            "profile_display_to_code": dict,
            "profile_code_to_display": dict,
            "fill_display_to_code": dict,
            "fill_code_to_display": dict,
        }
        """
        # 小工具：水平容器
        def _h(*widgets: QtWidgets.QWidget) -> QtWidgets.QWidget:
            w = QtWidgets.QWidget()
            hb = QtWidgets.QHBoxLayout(w)
            hb.setContentsMargins(0, 0, 0, 0)
            for x in widgets:
                hb.addWidget(x)
            return w

        # 质量档位
        profile_combo = QtWidgets.QComboBox()
        for display, code in self._profile_display_to_code.items():
            profile_combo.addItem(display)
            idx = profile_combo.count() - 1
            profile_combo.setItemData(idx, code, QtCore.Qt.UserRole)
        # 默认均衡
        for i in range(profile_combo.count()):
            if profile_combo.itemData(i, QtCore.Qt.UserRole) == "balanced":
                profile_combo.setCurrentIndex(i)
                break

        # 分辨率与帧率
        width_spin = QtWidgets.QSpinBox(); width_spin.setRange(16, 20000); width_spin.setValue(1080)
        height_spin = QtWidgets.QSpinBox(); height_spin.setRange(16, 20000); height_spin.setValue(1920)
        fps_spin = QtWidgets.QSpinBox(); fps_spin.setRange(1, 240); fps_spin.setValue(25)

        # 填充模式
        fill_combo = QtWidgets.QComboBox()
        for display, code in self._fill_display_to_code.items():
            fill_combo.addItem(display)
            idx = fill_combo.count() - 1
            fill_combo.setItemData(idx, code, QtCore.Qt.UserRole)
        # 默认 pad
        for i in range(fill_combo.count()):
            if fill_combo.itemData(i, QtCore.Qt.UserRole) == "pad":
                fill_combo.setCurrentIndex(i)
                break

        # TS 裁剪
        trim_head_dbl = QtWidgets.QDoubleSpinBox(); trim_head_dbl.setRange(0.0, 3600.0); trim_head_dbl.setDecimals(2); trim_head_dbl.setValue(0.0)
        trim_tail_dbl = QtWidgets.QDoubleSpinBox(); trim_tail_dbl.setRange(0.0, 3600.0); trim_tail_dbl.setDecimals(2); trim_tail_dbl.setValue(1.0)

        # 编码器覆盖与预设（不加入表单，但保持创建以供逻辑使用）
        nvenc_cq_spin = QtWidgets.QSpinBox(); nvenc_cq_spin.setRange(0, 51); nvenc_cq_spin.setSpecialValueText("(默认)"); nvenc_cq_spin.setValue(0)
        x265_crf_spin = QtWidgets.QSpinBox(); x265_crf_spin.setRange(0, 51); x265_crf_spin.setSpecialValueText("(默认)"); x265_crf_spin.setValue(0)
        preset_gpu_combo = QtWidgets.QComboBox(); preset_gpu_combo.addItems(["", "p4", "p5", "p6", "p7"])  # 空表示使用推荐
        preset_cpu_combo = QtWidgets.QComboBox(); preset_cpu_combo.addItems(["", "ultrafast", "medium", "slow", "slower", "veryslow"])  # 空表示使用推荐

        # 分组与布局
        encode_group = QtWidgets.QGroupBox("编码参数")
        encode_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        encode_grid = QtWidgets.QGridLayout()
        encode_grid.setContentsMargins(10, 8, 10, 8)
        encode_grid.setHorizontalSpacing(16)
        encode_grid.setVerticalSpacing(10)

        lbl_profile = QtWidgets.QLabel("质量档位")
        lbl_res = QtWidgets.QLabel("分辨率 (宽/高)")
        lbl_fill = QtWidgets.QLabel("填充模式")
        lbl_trim = QtWidgets.QLabel("TS裁剪(头/尾, 秒)")
        for _lbl in (lbl_profile, lbl_res, lbl_fill, lbl_trim):
            _lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        # 第 0 行：质量档位
        encode_grid.addWidget(lbl_profile, 0, 0)
        encode_grid.addWidget(profile_combo, 0, 1)
        # # 第 1 行：分辨率
        # encode_grid.addWidget(lbl_res, 1, 0)
        # encode_grid.addWidget(_h(width_spin, height_spin), 1, 1)
        # # 第 2 行：填充模式
        # encode_grid.addWidget(lbl_fill, 2, 0)
        # encode_grid.addWidget(fill_combo, 2, 1)
        # 第 3 行：TS裁剪（头/尾）
        encode_grid.addWidget(lbl_trim, 3, 0)
        encode_grid.addWidget(_h(trim_head_dbl, trim_tail_dbl), 3, 1)

        # 第 4 行：编码概览标签暂时移除（根据需求：不显示“编码概况”信息）
        enc_summary_label = None

        encode_grid.setColumnStretch(0, 0)
        encode_grid.setColumnStretch(1, 1)
        encode_grid.setColumnStretch(2, 0)
        encode_grid.setColumnStretch(3, 1)

        encode_group.setLayout(encode_grid)

        # 保存引用到 Tab
        self.profile_combo = profile_combo  # type: ignore[attr-defined]
        self.width_spin = width_spin  # type: ignore[attr-defined]
        self.height_spin = height_spin  # type: ignore[attr-defined]
        self.fill_combo = fill_combo  # type: ignore[attr-defined]
        self.trim_head_dbl = trim_head_dbl  # type: ignore[attr-defined]
        self.trim_tail_dbl = trim_tail_dbl  # type: ignore[attr-defined]
        self.fps_spin = fps_spin  # type: ignore[attr-defined]
        self.nvenc_cq_spin = nvenc_cq_spin  # type: ignore[attr-defined]
        self.x265_crf_spin = x265_crf_spin  # type: ignore[attr-defined]
        self.preset_gpu_combo = preset_gpu_combo  # type: ignore[attr-defined]
        self.preset_cpu_combo = preset_cpu_combo  # type: ignore[attr-defined]
        self.enc_summary_label = enc_summary_label  # type: ignore[attr-defined]

        return {
            "group": encode_group,
            "profile_combo": profile_combo,
            "width_spin": width_spin,
            "height_spin": height_spin,
            "fill_combo": fill_combo,
            "trim_head_dbl": trim_head_dbl,
            "trim_tail_dbl": trim_tail_dbl,
            "fps_spin": fps_spin,
            "nvenc_cq_spin": nvenc_cq_spin,
            "x265_crf_spin": x265_crf_spin,
            "preset_gpu_combo": preset_gpu_combo,
            "preset_cpu_combo": preset_cpu_combo,
            "profile_display_to_code": dict(self._profile_display_to_code),
            "profile_code_to_display": dict(self._profile_code_to_display),
            "fill_display_to_code": dict(self._fill_display_to_code),
            "fill_code_to_display": dict(self._fill_code_to_display),
        }

    # ---- Left panel event handlers (migrating from MainWindow) ----
    def default_output_dir(self) -> Optional[Path]:
        """
        计算默认输出目录，规则与 MainWindow._default_output_dir 完全一致。

        规则
        ----
        - 若未添加任何视频目录，返回 None。
        - 若仅有一个视频目录：默认输出位于其父目录下，名称为 "<目录名>_longvideo"。
          例如：C:/videos/input1 -> C:/videos/input1_longvideo
        - 若有多个视频目录：以第一个目录为基准，其父目录下的
          "<第一个目录名>_longvideo_combined"。

        Returns
        -------
        Optional[Path]
            计算出的默认输出目录路径；若无法计算则返回 None。
        """
       
        if not hasattr(self, "video_dirs_list") or self.video_dirs_list.count() == 0:  # type: ignore[attr-defined]
            return None
        # 收集所有目录文本
        video_dirs = [self.video_dirs_list.item(i).text() for i in range(self.video_dirs_list.count())]  # type: ignore[attr-defined]
        if not video_dirs:
            return None
        if len(video_dirs) == 1:
            d = Path(video_dirs[0])
            return d.parent / f"{d.name}_longvideo"
        base_parent = Path(video_dirs[0]).parent
        return base_parent / f"{Path(video_dirs[0]).name}_longvideo_combined"
        
        
    def _get_result_path_by_row(self, row: int) -> Optional[Path]:
        """Safely retrieve the output path from the results table by row.

        Tries to read from the "输出路径" column; falls back to any column's
        Qt.UserRole if the display text is empty.
        """
        try:
            if self.results_table is None:
                return None
            path_col = 3  # "输出路径" column index
            p_item = self.results_table.item(row, path_col)
            if p_item and p_item.text():
                return Path(p_item.text().strip())
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

    def on_open_selected_files(self) -> None:
        """
        Open and select all selected output files in the system file manager.

        行为
        ----
        - 使用 get_selected_paths() 获取选中行对应的路径列表。
        - 统一委托给 open_paths(paths)，集中处理存在性检查与文件管理器显示。
        - 如果无选中项，则提示用户进行选择。
        """
        try:
            paths = self.get_selected_paths()
        except Exception:
            paths = []
        if not paths:
            QtWidgets.QMessageBox.information(self, "提示", "请先选择一个或多个输出文件")
            return
        try:
            self.open_paths(paths)
        except Exception:
            pass

    def open_selected_paths(self) -> None:
        """
        Public alias to open selected output file paths.

        外部模块或 MainWindow 若需触发“打开并在文件管理器中选中”动作，
        推荐调用此方法以获得更语义化的接口。
        """
        try:
            self.on_open_selected_files()
        except Exception:
            pass

    def copy_selected_paths(self) -> None:
        """Copy selected output file paths to clipboard."""
        try:
            paths = self.get_selected_paths()
        except Exception:
            paths = []
        self.copy_paths(paths)

    def _reveal_in_file_manager(self, paths: List[Path]) -> None:
        """Reveal and select files in the system file manager across platforms."""
        if not paths:
            return
        try:
            import sys
            import subprocess
            plat = sys.platform.lower()
        except Exception:
            plat = ""
        for p in paths:
            try:
                if not p or not isinstance(p, Path):
                    continue
                if plat.startswith("win"):
                    try:
                        subprocess.run(["explorer", "/select,", str(p)], check=False)
                    except Exception:
                        QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p.parent)))
                elif plat == "darwin":
                    try:
                        subprocess.run(["open", "-R", str(p)], check=False)
                    except Exception:
                        QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p.parent)))
                else:
                    QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p.parent)))
            except Exception:
                try:
                    QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p.parent)))
                except Exception:
                    pass

    def open_paths(self, paths: List[Path]) -> None:
        """
        Open and select specified output file paths in the system file manager.

        Parameters
        ----------
        paths : List[pathlib.Path]
            The file paths to reveal in the system file manager.

        Notes
        -----
        - Non-existent paths will be summarized in a single warning dialog and skipped.
        - This method is UI-agnostic and does not depend on table selection.
        """
        try:
            if not paths:
                QtWidgets.QMessageBox.information(self, "提示", "请先选择一个或多个输出文件")
                return
            existing: List[Path] = []
            missing: List[Path] = []
            for p in paths:
                try:
                    if p and Path(p).exists():
                        existing.append(Path(p))
                    else:
                        missing.append(Path(p))
                except Exception:
                    pass
            if missing:
                try:
                    msg = theme.format_missing_paths_warning(missing)
                    QtWidgets.QMessageBox.warning(self, theme.MISSING_PATHS_WARNING_TITLE, msg)
                except Exception:
                    pass
            if existing:
                self._reveal_in_file_manager(existing)
        except Exception:
            pass

    def copy_paths(self, paths: List[Path]) -> None:
        """
        Copy specified output file paths to clipboard.

        Parameters
        ----------
        paths : List[pathlib.Path]
            The file paths to copy.

        Notes
        -----
        - This method is UI-agnostic and does not depend on table selection.
        - Paths will be copied as newline-separated absolute strings.
        """
        try:
            if not paths:
                QtWidgets.QMessageBox.information(self, "提示", "请先选择一个或多个输出文件")
                return
            QtWidgets.QApplication.clipboard().setText("\n".join(str(p) for p in paths))
            QtWidgets.QMessageBox.information(self, "提示", f"已复制 {len(paths)} 个路径到剪贴板")
        except Exception:
            pass
            
    def update_output_default(self) -> None:
        """
        根据第一个视频目录自动生成输出路径默认值并填充到输入框。

        规则与 MainWindow 保持一致：
        - 若列表中存在至少一个目录，默认值为：
          单目录：第一个目录的同级目录下的 “<目录名>_longvideo”。
          多目录：第一个目录的同级目录下的 “<目录名>_longvideo_combined”。
        - 仅在输出框为空或仍处于自动填充模式时更新，避免覆盖用户手动输入。
        """
        try:
            # 若用户已经手动编辑过，则不再自动填充
            if not self._output_autofill and hasattr(self, "output_edit") and self.output_edit.text().strip():  # type: ignore[attr-defined]
                return
            # 计算默认输出目录（与 MainWindow 逻辑一致）
            default_out = self.default_output_dir()
            if default_out is None:
                return
            # 仅在当前为空或仍在自动模式下填充
            if hasattr(self, "output_edit"):
                current = self.output_edit.text().strip()  # type: ignore[attr-defined]
                if self._output_autofill or not current:
                    self.output_edit.setText(str(default_out))  # type: ignore[attr-defined]
        except Exception:
            # 容错，不影响主流程
            pass

    def on_add_dir(self) -> None:
        """
        Open a directory selection dialog and add to the list.

        Notes
        -----
        - Mirrors MainWindow._on_add_dir behavior.
        - Calls update_output_default() after changes.
        """
        try:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择视频目录")
            if d and hasattr(self, "video_dirs_list") and self.video_dirs_list is not None:  # type: ignore[attr-defined]
                self.video_dirs_list.addItem(d)  # type: ignore[attr-defined]
                self.update_output_default()
        except Exception:
            pass

    def on_rm_dir(self) -> None:
        """
        Remove selected directory entries from the list.

        Notes
        -----
        - Mirrors MainWindow._on_rm_dir behavior.
        - Calls update_output_default() after changes.
        """
        try:
            if hasattr(self, "video_dirs_list") and self.video_dirs_list is not None:  # type: ignore[attr-defined]
                for item in self.video_dirs_list.selectedItems():  # type: ignore[attr-defined]
                    self.video_dirs_list.takeItem(self.video_dirs_list.row(item))  # type: ignore[attr-defined]
                self.update_output_default()
        except Exception:
            pass

    def on_browse_bgm_file(self) -> None:
        """
        Select a single BGM audio file and populate the input field.

        Mirrors MainWindow._on_browse_bgm_file behavior during migration.

        Filters common audio formats (mp3/wav/aac/flac/m4a/ogg etc.). If
        the current input has a path, use its directory as the starting dir.
        """
        try:
            from os import path
            current = getattr(self, "bgm_path_edit", None)
            start_dir = str(Path.home())
            if current is not None:
                text = current.text().strip()  # type: ignore[attr-defined]
                if path.exists(text):
                    start_dir = path.dirname(text)
            filters = (
                "音频文件 (*.mp3 *.wav *.aac *.flac *.m4a *.ogg *.wma *.alac *.aiff *.ape);;所有文件 (*)"
            )
            file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择BGM音频文件", start_dir, filters)
            if file_path and current is not None:
                current.setText(file_path)  # type: ignore[attr-defined]
        except Exception:
            pass

    def on_browse_bgm_dir(self) -> None:
        """
        Select a directory containing BGM audio and populate the input field.

        Mirrors MainWindow._on_browse_bgm_dir behavior during migration.
        """
        try:
            from os import path
            current = getattr(self, "bgm_path_edit", None)
            start_dir = str(Path.home())
            if current is not None:
                text = current.text().strip()  # type: ignore[attr-defined]
                if path.isdir(text):
                    start_dir = text
            dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择BGM目录", start_dir)
            if dir_path and current is not None:
                current.setText(dir_path)  # type: ignore[attr-defined]
        except Exception:
            pass

    def validate_bgm_path(self, p: str) -> None:
        """
        Validate the BGM path (file or directory) and apply a visual hint.

        Applies green border for valid file/dir, red for invalid, and resets
        style when empty. Mirrors MainWindow._validate_bgm_path.
        """
        try:
            current = getattr(self, "bgm_path_edit", None)
            if current is None:
                return
            if not p:
                current.setStyleSheet("")  # type: ignore[attr-defined]
                return
            import os
            valid = os.path.isfile(p) or os.path.isdir(p)
            if valid:
                current.setStyleSheet("QLineEdit{border:1px solid #4CAF50}")  # type: ignore[attr-defined]
            else:
                current.setStyleSheet("QLineEdit{border:1px solid #F44336}")  # type: ignore[attr-defined]
        except Exception:
            pass

    def on_browse_output(self) -> None:
        """
        Choose an output file or directory and populate the output edit field.

        Mirrors MainWindow._on_browse_output behavior during migration.
        """
        try:
            dlg = QtWidgets.QFileDialog(self)
            dlg.setFileMode(QtWidgets.QFileDialog.AnyFile)
            if dlg.exec():
                files = dlg.selectedFiles()
                if files and hasattr(self, "output_edit") and self.output_edit is not None:  # type: ignore[attr-defined]
                    # 用户通过对话框选择路径，视为手动设置，关闭自动填充
                    self.output_edit.setText(files[0])  # type: ignore[attr-defined]
                    self._output_autofill = False
        except Exception:
            pass

    def open_default_output_dir(self) -> None:
        """
        打开（并创建）默认输出目录。

        行为
        ----
        - 调用 default_output_dir() 计算默认输出目录；若为空，弹出提示。
        - 若目录不存在，则先创建；随后通过系统默认文件管理器打开。
        """
        try:
            target = self.default_output_dir()
            if not target:
                QtWidgets.QMessageBox.warning(self, "提示", "请先添加视频目录")
                return
            target.mkdir(parents=True, exist_ok=True)
            QtCore.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target)))
        except Exception:
            pass

    def on_output_text_edited(self, _text: str) -> None:
        """
        当用户编辑输出路径时，关闭自动填充默认值。

        迁移后由 Tab 自身维护 _output_autofill 状态，避免覆盖手动输入。
        """
        try:
            self._output_autofill = False
        except Exception:
            pass

    def populate_from_config(self, data: dict) -> None:
        """
        Populate the tab's left-side inputs from a configuration dict.

        This centralizes form updates within the tab to avoid MainWindow
        directly manipulating widgets. It also preserves the autofill
        behavior for the output path.

        Args:
            data (dict): A settings-like dictionary possibly containing
                keys: "video_dirs" (list[str]), "bgm_path" (str),
                and "output" (str).

        Behavior:
            - 左侧：清空并填充视频目录，设置 BGM 路径与校验；根据 output 是否存在决定是否启用默认输出自动填充。
            - 数值/选择项（仍在 MainWindow 的控件）：设置 count/outputs/gpu/threads/width/height/fps/fill/trim与缓存清理、分组模式、质量档位、编码器覆盖与预设；最后刷新编码概览。
        """
        try:
            # video dirs
            if hasattr(self, "video_dirs_list") and self.video_dirs_list is not None:  # type: ignore[attr-defined]
                self.video_dirs_list.clear()  # type: ignore[attr-defined]
                for d in data.get("video_dirs", []) or []:
                    self.video_dirs_list.addItem(str(d))  # type: ignore[attr-defined]

            # bgm path
            bgm_val = str(data.get("bgm_path", ""))
            if hasattr(self, "bgm_path_edit") and self.bgm_path_edit is not None:  # type: ignore[attr-defined]
                self.bgm_path_edit.setText(bgm_val)  # type: ignore[attr-defined]
                # apply validation styling
                try:
                    self.validate_bgm_path(bgm_val)
                except Exception:
                    pass

            # output path & autofill
            out_val = str(data.get("output", ""))
            if hasattr(self, "output_edit") and self.output_edit is not None:  # type: ignore[attr-defined]
                if out_val:
                    self.output_edit.setText(out_val)  # type: ignore[attr-defined]
                    self._output_autofill = False
                else:
                    self._output_autofill = True
                    self.update_output_default()
            else:
                # even without output_edit, attempt to compute default directory
                # to keep internal state consistent for later use
                self._output_autofill = True
            # 其余参数设置（通过 MainWindow 控件）
            mw = self._get_main_window()
            if mw is not None:
                try:
                    getattr(mw, "count_spin").setValue(int(data.get("count", 5)))
                    getattr(mw, "outputs_spin").setValue(int(data.get("outputs", 1)))
                    getattr(mw, "gpu_chk").setChecked(bool(data.get("gpu", True)))
                    getattr(mw, "threads_spin").setValue(int(data.get("threads", 4)))
                    getattr(mw, "width_spin").setValue(int(data.get("width", 1080)))
                    getattr(mw, "height_spin").setValue(int(data.get("height", 1920)))
                    getattr(mw, "fps_spin").setValue(int(data.get("fps", 25)))
                except Exception:
                    pass
                # 填充模式与质量档位支持内部代码或中文展示
                try:
                    self._set_fill_in_mw(mw, str(data.get("fill", "pad")))
                except Exception:
                    pass
                try:
                    self._set_profile_in_mw(mw, str(data.get("quality_profile", "balanced")))
                except Exception:
                    pass
                # 裁剪与缓存/分组
                try:
                    getattr(mw, "trim_head_dbl").setValue(float(data.get("trim_head", 0.0)))
                    getattr(mw, "trim_tail_dbl").setValue(float(data.get("trim_tail", 1.0)))
                    getattr(mw, "clear_cache_chk").setChecked(bool(data.get("clear_mismatched_cache", False)))
                    getattr(mw, "group_res_chk").setChecked(bool(data.get("group_res", True)))
                except Exception:
                    pass
                # 编码器覆盖与预设
                try:
                    nvenc_cq = data.get("nvenc_cq", None)
                    x265_crf = data.get("x265_crf", None)
                    getattr(mw, "nvenc_cq_spin").setValue(int(nvenc_cq) if nvenc_cq is not None else 0)
                    getattr(mw, "x265_crf_spin").setValue(int(x265_crf) if x265_crf is not None else 0)
                    getattr(mw, "preset_gpu_combo").setCurrentText(str(data.get("preset_gpu", "")) or "")
                    getattr(mw, "preset_cpu_combo").setCurrentText(str(data.get("preset_cpu", "")) or "")
                except Exception:
                    pass
                # 刷新编码概览
                try:
                    self.update_enc_summary()
                except Exception:
                    pass
        except Exception:
            pass

    # ---------- 编码参数概览与质量档位联动（迁移至 Tab） ----------
    def _get_profile_code_from_mw(self, mw: QtWidgets.QWidget) -> str:
        """从 MainWindow 的 profile_combo 读取内部代码（visual/balanced/size）。"""
        try:
            combo = getattr(mw, "profile_combo")
            idx = combo.currentIndex()
            code = combo.itemData(idx, QtCore.Qt.UserRole)
            if code:
                return str(code)
            t = (combo.currentText() or "").strip().lower()
            if t in {"visual", "balanced", "size"}:
                return t
        except Exception:
            pass
        return "balanced"

    def _set_profile_in_mw(self, mw: QtWidgets.QWidget, code_or_display: str) -> None:
        """设置 MainWindow 的 profile_combo，支持内部代码或中文展示。"""
        try:
            combo = getattr(mw, "profile_combo")
            target_code = None
            code = code_or_display
            # 预置映射来自 MainWindow
            mapping = getattr(mw, "_profile_display_to_code", None)
            if code in {"visual", "balanced", "size"}:
                target_code = code
            elif mapping and code in mapping:
                target_code = mapping[code]
            else:
                target_code = "balanced"
            for i in range(combo.count()):
                if combo.itemData(i, QtCore.Qt.UserRole) == target_code:
                    combo.setCurrentIndex(i)
                    return
            # 回退设置文本
            display_map = getattr(mw, "_profile_code_to_display", {})
            combo.setCurrentText(display_map.get(target_code, target_code))
        except Exception:
            pass

    def _get_fill_code_from_mw(self, mw: QtWidgets.QWidget) -> str:
        """从 MainWindow 的 fill_combo 读取内部代码（pad/crop）。"""
        try:
            combo = getattr(mw, "fill_combo")
            idx = combo.currentIndex()
            code = combo.itemData(idx, QtCore.Qt.UserRole)
            if code:
                return str(code)
            t = (combo.currentText() or "").strip().lower()
            if t in {"pad", "crop"}:
                return t
        except Exception:
            pass
        return "pad"

    def _set_fill_in_mw(self, mw: QtWidgets.QWidget, code_or_display: str) -> None:
        """设置 MainWindow 的 fill_combo，支持内部代码或中文展示。"""
        try:
            combo = getattr(mw, "fill_combo")
            code = code_or_display
            target_code = None
            mapping = getattr(mw, "_fill_display_to_code", None)
            if code in {"pad", "crop"}:
                target_code = code
            elif mapping and code in mapping:
                target_code = mapping[code]
            else:
                target_code = "pad"
            for i in range(combo.count()):
                if combo.itemData(i, QtCore.Qt.UserRole) == target_code:
                    combo.setCurrentIndex(i)
                    return
            # 回退设置文本
            display_map = getattr(mw, "_fill_code_to_display", {})
            combo.setCurrentText(display_map.get(target_code, target_code))
        except Exception:
            pass

    def _get_profile_code(self) -> str:
        """读取本 Tab 内部的质量档位代码（visual/balanced/size）。"""
        try:
            combo = getattr(self, "profile_combo")
            idx = combo.currentIndex()
            code = combo.itemData(idx, QtCore.Qt.UserRole)
            if code:
                return str(code)
            t = (combo.currentText() or "").strip().lower()
            if t in {"visual", "balanced", "size"}:
                return t
        except Exception:
            pass
        return "balanced"

    def _get_fill_code(self) -> str:
        """读取本 Tab 内部的填充模式代码（pad/crop）。"""
        try:
            combo = getattr(self, "fill_combo")
            idx = combo.currentIndex()
            code = combo.itemData(idx, QtCore.Qt.UserRole)
            if code:
                return str(code)
            t = (combo.currentText() or "").strip().lower()
            if t in {"pad", "crop"}:
                return t
        except Exception:
            pass
        return "pad"

    def _compute_effective_enc_params(self) -> dict:
        """根据当前 Tab 控件计算有效编码参数（含推荐与用户覆盖）。"""
        profile = self._get_profile_code()
        if profile == "visual":
            d_nvenc_cq, d_preset_gpu = 30, "p5"
            d_x265_crf, d_preset_cpu = 28, "medium"
            d_fps = 30
        elif profile == "size":
            d_nvenc_cq, d_preset_gpu = 34, "p7"
            d_x265_crf, d_preset_cpu = 32, "veryslow"
            d_fps = 24
        else:
            d_nvenc_cq, d_preset_gpu = 32, "p6"
            d_x265_crf, d_preset_cpu = 30, "slow"
            d_fps = 25
        nvenc_cq = d_nvenc_cq
        x265_crf = d_x265_crf
        preset_gpu = d_preset_gpu
        preset_cpu = d_preset_cpu
        fps_val = d_fps
        try:
            nvenc_cq = getattr(self, "nvenc_cq_spin").value() or d_nvenc_cq
            x265_crf = getattr(self, "x265_crf_spin").value() or d_x265_crf
            preset_gpu = getattr(self, "preset_gpu_combo").currentText() or d_preset_gpu
            preset_cpu = getattr(self, "preset_cpu_combo").currentText() or d_preset_cpu
            fps_val = int(getattr(self, "fps_spin").value())
        except Exception:
            pass
        return {
            "profile": profile,
            "nvenc_cq": int(nvenc_cq),
            "x265_crf": int(x265_crf),
            "preset_gpu": str(preset_gpu),
            "preset_cpu": str(preset_cpu),
            "fps": int(fps_val),
        }

    def update_enc_summary(self) -> None:
        """
        刷新编码概览标签文本（位于本 Tab 内部的 enc_summary_label）。

        展示信息包含：质量档位、分辨率、帧率、填充模式、NVENC cq 与预设、x265 crf 与预设。
        该方法仅依赖于当前 Tab 上的控件与内部映射，不再访问 MainWindow。
        """
        try:
            lbl = getattr(self, "enc_summary_label", None)
            if lbl is None:
                return
        except Exception:
            return
        try:
            p = self._compute_effective_enc_params()
            # 映射展示
            prof_display = self._profile_code_to_display.get(p["profile"], p["profile"])  # type: ignore
            # 分辨率与填充模式展示
            try:
                w, h = int(getattr(self, "width_spin").value()), int(getattr(self, "height_spin").value())
            except Exception:
                w, h = 1080, 1920
            fill_code = self._get_fill_code()
            fill_display = self._fill_code_to_display.get(fill_code, fill_code)
            lbl.setText(
                f"编码概览：质量档位={prof_display} | 分辨率={w}x{h} | 帧率={p['fps']} | 填充={fill_display} | NVENC cq={p['nvenc_cq']}/{p['preset_gpu']} | x265 crf={p['x265_crf']}/{p['preset_cpu']}"
            )
        except Exception:
            pass

    def on_profile_changed(self, text: str) -> None:
        """当质量档位变化时，自动设置推荐的编码参数（直接更新本 Tab 控件）。"""
        profile = self._get_profile_code()
        if profile == "visual":
            d_nvenc_cq, d_preset_gpu = 30, "p5"
            d_x265_crf, d_preset_cpu = 28, "medium"
            d_fps = 30
        elif profile == "size":
            d_nvenc_cq, d_preset_gpu = 34, "p7"
            d_x265_crf, d_preset_cpu = 32, "veryslow"
            d_fps = 24
        else:
            d_nvenc_cq, d_preset_gpu = 32, "p6"
            d_x265_crf, d_preset_cpu = 30, "slow"
            d_fps = 25
        widgets = []
        try:
            widgets = [
                getattr(self, "nvenc_cq_spin"),
                getattr(self, "x265_crf_spin"),
                getattr(self, "preset_gpu_combo"),
                getattr(self, "preset_cpu_combo"),
                getattr(self, "fps_spin"),
            ]
        except Exception:
            widgets = []
        prev_states = []
        for w in widgets:
            try:
                prev_states.append(w.blockSignals(True))
            except Exception:
                prev_states.append(False)
        try:
            try:
                getattr(self, "nvenc_cq_spin").setValue(int(d_nvenc_cq))
                getattr(self, "x265_crf_spin").setValue(int(d_x265_crf))
                getattr(self, "preset_gpu_combo").setCurrentText(d_preset_gpu)
                getattr(self, "preset_cpu_combo").setCurrentText(d_preset_cpu)
                getattr(self, "fps_spin").setValue(int(d_fps))
            finally:
                for w, prev in zip(widgets, prev_states):
                    try:
                        w.blockSignals(bool(prev))
                    except Exception:
                        pass
            # 不再更新编码概览标签（已移除）
        except Exception:
            pass