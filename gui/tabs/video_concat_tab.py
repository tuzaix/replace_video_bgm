"""
视频混剪标签页（UI 与逻辑）

本模块实现一个新的“视频混剪”标签页，布局与 extract_frames_tab.py 一致，分为左右两个面板：

- 左侧面板：
  1) group1（视频目录）
     - 视频目录：QListWidget（支持多行选中）、添加目录、移除选中
     - 背景音乐：QLineEdit + 浏览（支持选择音频文件或目录）
     - 合成输出：QLineEdit + 浏览（仅目录）
  2) group2（混剪参数）
     - 混剪视频数量（输出数量）：QSpinBox（支持手动编辑）
     - 每个混剪切片数：QSpinBox（支持手动编辑）
     - 合成质量档位：QComboBox（观感均衡→balanced，更高压缩→compact，极限压缩→tiny）
     - 并发数量：QSpinBox（支持手动编辑）
     - 素材裁剪头尾：QDoubleSpinBox(裁剪头部秒) → QDoubleSpinBox(裁剪尾部秒)

- 右侧面板：
  - 顶部进度条与“开始/停止”互斥按钮（单按钮，点击后在两个状态间切换）
  - 下方混剪结果表（文件输出路径、文件分辨率、文件大小），支持双击打开文件

视频混剪逻辑分两步：
1) 对每个“视频目录”进行素材归一化，输出到该目录下的临时子目录（使用 concat_tool.normalize_video.VideoNormalizer）
2) 汇总所有临时目录下的归一化视频，按分辨率分组，选取视频数量最多的分辨率组作为候选；
   每个输出随机选择“每个混剪切片数”个素材（可选裁剪头尾），通过 concat_tool.concat.VideoConcat 完成拼接。

注意：
- 本模块为 GUI 组件，不启动 Web 预览。需在桌面环境运行 PySide6 应用手动验证 UI 效果。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import random
import time
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env  # type: ignore
bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)

from concat_tool.normalize_video import VideoNormalizer  # type: ignore
from concat_tool.concat import VideoConcat  # type: ignore
from gui.utils import theme
from gui.precheck import run_preflight_checks
from utils.calcu_video_info import probe_resolution, get_resolution_dir_topn, confirm_resolution_dir, ffprobe_duration
from utils.common_utils import is_video_file, is_image_file

class ConcatWorker(QtCore.QObject):
    """后台混剪工作者：先归一化素材，再按分辨率分组进行拼接。

    此类在 QThread 中运行，避免阻塞主线程。通过信号向 UI 报告阶段、进度、错误与结果。

    Signals
    -------
    phase(str): 当前阶段（"normalize" / "concat" 等）
    progress(int, int): 进度值 (done, total)
    finished(int, int): 完成信号 (success_count, fail_count)
    results(list[str]): 成功输出的视频文件路径列表
    error(str): 错误信息
    log(str): 文本日志，用于右侧日志/状态展示
    """

    phase = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    finished = QtCore.Signal(int, int)
    results = QtCore.Signal(list)
    error = QtCore.Signal(str)
    log = QtCore.Signal(str)

    def __init__(
        self,
        video_dirs: List[str],
        bgm_path: str,
        output_dir: str,
        outputs: int,
        slices_per_output: int,
        quality_profile: str,
        concurrency: int,
    ) -> None:
        """初始化工作者并快照所有运行参数。

        Parameters
        ----------
        video_dirs : List[str]
            用户选择的多个视频根目录。
        bgm_path : str
            背景音乐路径（可为空或指定音频文件；若是目录则按需选择其中文件）。
        output_dir : str
            合成输出的目录。
        outputs : int
            需要生成的混剪视频数量。
        slices_per_output : int
            每个混剪视频包含的切片数量（从候选素材中随机抽取）。
        quality_profile : str
            质量档位代码："balanced" | "compact" | "tiny"。
        concurrency : int
            并发数量（用于归一化与拼接并行）。
        
        """
        super().__init__()
        self.video_dirs = [str(Path(p)) for p in video_dirs]
        self.bgm_path = str(bgm_path or "").strip()
        self.output_dir = str(output_dir or "").strip()
        self.outputs = int(outputs)
        self.slices_per_output = int(slices_per_output)
        self.quality_profile = str(quality_profile or "balanced")
        self.concurrency = max(1, int(concurrency))
        self._stopping = False

    def stop(self) -> None:
        """请求软停止。正在进行的任务会尽快结束。"""
        self._stopping = True

    # ----------------------------- 内部辅助方法 ----------------------------- #
    def _emit(self, text: str) -> None:
        """安全发射日志文本。"""
        # try:
        #     self.log.emit(str(text))
        # except Exception:
        #     pass
        pass

    def _choose_bgm_path(self) -> Optional[Path]:
        """选择用于混剪的背景音乐文件路径。

        逻辑
        ----
        - 若 `self.bgm_path` 指向一个文件，直接返回该文件路径。
        - 若 `self.bgm_path` 指向一个目录，在其中随机选择一个匹配的音频文件（mp3 / wav）。
        - 若未指定或未找到匹配项，返回 None。

        Returns
        -------
        Optional[Path]
            选中的 BGM 文件路径；若不可用则为 None。
        """
        try:
            if not self.bgm_path:
                return None
            bgm_p = Path(self.bgm_path)
            if bgm_p.is_file():
                return bgm_p
            if bgm_p.is_dir():
                try:
                    bgm_files = list(bgm_p.glob("*.mp3")) + list(bgm_p.glob("*.wav"))
                    if bgm_files:
                        return random.choice(bgm_files)
                except Exception:
                    return None
        except Exception:
            return None
        return None

    def _concat_videos(self, normalized_dirs: List[Path], out_dir: Path) -> Optional[Tuple[List[str], int]]:
        """执行混剪阶段，基于归一化素材生成目标输出。

        逻辑
        ----
        - 收集归一化素材并按分辨率分组，选择数量最多的分辨率组。
        - 发射阶段 `concat` 与日志。
        - 并发生成输出，每完成一个就发射 `progress(done, total_outputs)`。

        Parameters
        ----------
        normalized_dirs : List[Path]
            归一化素材所在的临时目录列表。
        out_dir : Path
            混剪输出目录。

        Returns
        -------
        Optional[Tuple[List[str], int]]
            成功时返回 (success_paths, fail_count)；若候选为空或被取消则返回 None。
        """
        # 收集候选素材（支持传入分辨率目录或具体文件列表）并按分辨率分组
        all_videos: List[Path] = []
        for nd in normalized_dirs:
            try:
                if nd.is_dir():
                    for p in nd.iterdir():
                        if is_video_file(p.name):
                            all_videos.append(p)
                elif is_video_file(nd.name):
                    all_videos.append(nd)
            except Exception:
                pass
        if not all_videos:
            self.error.emit("归一化后未发现可用素材")
            return None

        groups: dict[Tuple[int, int], List[Path]] = {}
        for v in all_videos:
            try:
                res = probe_resolution(v) or (0, 0)
            except Exception:
                res = (0, 0)
            groups.setdefault(res, []).append(v)
        # 选择视频数量最多的分辨率组，若并列则取面积更大的分辨率
        best_res = max(groups.keys(), key=lambda r: (len(groups[r]), r[0] * r[1]))
        candidates = groups.get(best_res, [])
        self._emit(f"📹 选择分辨率组 {best_res[0]}x{best_res[1]}，素材数 {len(candidates)}")
        if not candidates:
            self.error.emit("分辨率分组失败：候选为空")
            return None

        try:
            self.phase.emit("concat")
        except Exception:
            pass
        self._emit("🎬 开始生成混剪视频…（不再额外裁剪）")

        success: List[str] = []
        fail = 0
        total_outputs = self.outputs
        done = 0

        def build_one(idx: int) -> bool:
            if self._stopping:
                return False
            try:
                pick = random.sample(candidates, k=min(self.slices_per_output, len(candidates)))
            except Exception:
                pick = candidates[:min(self.slices_per_output, len(candidates))]
            # 仅使用归一化后的素材作为拼接切片，不再做头尾裁剪
            slices: List[Path] = list(pick)
            # 增加随机字符串
            random_str = random.randint(100000, 999999)
            out_path = out_dir / f"concat_{idx}_{random_str}_{best_res[0]}x{best_res[1]}.mp4"

            # 根据设置选择合适的 BGM 文件（文件或目录随机）
            bgm_path = self._choose_bgm_path()

            vc = VideoConcat(
                slices=slices,
                out_path=out_path,
                bgm_path=bgm_path,
                quality=self.quality_profile,
                use_gpu=True,
            )
            ok = vc.run()
            if ok:
                success.append(str(out_path))
                return True
            return False

        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            futures = [ex.submit(build_one, i) for i in range(1, total_outputs + 1)]
            for f in as_completed(futures):
                try:
                    ok = f.result()
                    if not ok:
                        fail += 1
                except Exception:
                    fail += 1
                done += 1
                try:
                    self.progress.emit(int(done), int(total_outputs))
                except Exception:
                    pass

        return success, fail

    # ----------------------------- 运行主流程 ----------------------------- #
    @QtCore.Slot()
    def run(self) -> None:
        """执行完整流程：先归一化，再合成混剪输出。

        流程
        ----
        1) 校验参数与输出目录
        2) 调用 `_normalize_sources()` 执行归一化并发射进度
        3) 调用 `_concat_videos()` 执行混剪并发射进度
        4) 汇总结果并发射完成信号
        """
        if self._stopping:
            self.error.emit("任务已取消")
            return

        # 参数校验与输出目录准备
        if not self.video_dirs:
            self.error.emit("请选择至少一个视频目录")
            return
        
        if not self.output_dir:
            self.error.emit("请选择输出目录")
            return
        out_dir = Path(self.output_dir)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # 阶段一：素材收集
        candidates: List[Path] = []
        confirm_normalized_dirs: dict[str, bool] = {}
        for d in self.video_dirs:
            try:
                confirm_normalized_dirs[d] = confirm_resolution_dir(d)
                if not confirm_normalized_dirs[d]:
                    continue
                media_data = get_resolution_dir_topn(d, top_n=1, recursive=False)
                files = media_data.get("files", []) if isinstance(media_data, dict) else []
                for p in files:
                    if isinstance(p, Path) and p.is_file() and is_video_file(p):
                        candidates.append(p)
            except Exception:
                try:
                    for name in os.listdir(d):
                        p = Path(d) / name
                        if p.is_file() and is_video_file(p):
                            candidates.append(p)
                except Exception:
                    continue
        if not candidates:
            self.error.emit("未发现可用素材，请先在【视频预处理】中归一化")
            return

        # 阶段二：混剪
        result = self._concat_videos(candidates, out_dir)
        if result is None:
            return
        success, fail = result

        # 完成信号与日志
        try:
            self.finished.emit(len(success), fail)
        except Exception:
            pass
        try:
            self.results.emit(success)
        except Exception:
            pass
        if success:
            self._emit("\n🎉 成功生成的文件:")
            for p in success:
                try:
                    size_mb = Path(p).stat().st_size / (1024 * 1024)
                    self._emit(f"  - {p} ({size_mb:.1f} MB)")
                except Exception:
                    self._emit(f"  - {p}")


class VideoConcatTab(QtWidgets.QWidget):
    """“视频混剪”标签页。

    提供与 extract_frames_tab 相似的左右分栏布局：左侧为输入与参数，右侧为进度与结果。
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.root_layout = QtWidgets.QHBoxLayout(self)
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[ConcatWorker] = None
        self._is_running: bool = False

        # 左侧控件引用
        self.video_list: Optional[QtWidgets.QListWidget] = None
        self.bgm_edit: Optional[QtWidgets.QLineEdit] = None
        self.output_edit: Optional[QtWidgets.QLineEdit] = None
        self.outputs_spin: Optional[QtWidgets.QSpinBox] = None
        self.slices_spin: Optional[QtWidgets.QSpinBox] = None
        self.quality_combo: Optional[QtWidgets.QComboBox] = None
        self.concurrency_spin: Optional[QtWidgets.QSpinBox] = None

        # 右侧控件引用
        self.progress_bar: Optional[QtWidgets.QProgressBar] = None
        self.start_stop_btn: Optional[QtWidgets.QPushButton] = None
        self.results_table: Optional[QtWidgets.QTableWidget] = None

        self._build_page()

    # ----------------------------- 页面构建 ----------------------------- #
    def _build_page(self) -> None:
        """构建整页：左右面板通过 Splitter 组合。"""
        left = self._build_left_panel()
        right = self._build_right_panel()

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)
        # 固定右侧不可拖动
        try:
            splitter.handle(1).setEnabled(False)
        except Exception:
            pass
        self.root_layout.setContentsMargins(6, 6, 6, 6)
        self.root_layout.addWidget(splitter)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        """构建左侧面板，包含 group1（目录与路径）与 group2（混剪参数）。"""
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(10)

        # group1：视频目录与路径配置
        group1 = QtWidgets.QGroupBox("视频目录")
        g1 = QtWidgets.QVBoxLayout(group1)
        g1.setContentsMargins(10, 8, 10, 8)
        g1.setSpacing(8)

        # a.1 视频目录列表 + 添加/移除按钮
        self.video_list = QtWidgets.QListWidget()
        self.video_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.video_list.setMinimumHeight(120)
        # self.video_list 增加默认值
        # self.video_list.addItems([r"E:\Download\社媒助手\抖音\潮汕菲宝"])
        btns_row = QtWidgets.QHBoxLayout()
        btn_add = QtWidgets.QPushButton("添加目录…")
        btn_del = QtWidgets.QPushButton("移除选中")
        btn_add.clicked.connect(self._on_add_video_dir)
        btn_del.clicked.connect(self._on_remove_selected_dirs)
        btns_row.addWidget(btn_add)
        btns_row.addWidget(btn_del)
        g1.addWidget(QtWidgets.QLabel("视频目录（可多选）"))
        g1.addWidget(self.video_list)
        g1.addLayout(btns_row)

        # a.2 背景音乐：QLineEdit + 浏览（文件或目录）
        bgm_row = QtWidgets.QHBoxLayout()
        self.bgm_edit = QtWidgets.QLineEdit()
        self.bgm_edit.setPlaceholderText("选择音频文件或包含音频的目录…")
        btn_bgm = QtWidgets.QPushButton("浏览…")
        btn_bgm.clicked.connect(self._on_browse_bgm)
        bgm_row.addWidget(QtWidgets.QLabel("背景音乐"), 0)
        bgm_row.addWidget(self.bgm_edit, 1)
        bgm_row.addWidget(btn_bgm)
        g1.addLayout(bgm_row)

        # a.3 合成输出：QLineEdit + 浏览（仅目录）
        out_row = QtWidgets.QHBoxLayout()
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText("选择输出目录…")
        self.output_edit.setText("默认是：视频最后一个目录/混剪")

        btn_out = QtWidgets.QPushButton("浏览…")
        btn_out.clicked.connect(self._on_browse_output_dir)
        out_row.addWidget(QtWidgets.QLabel("混剪输出"), 0)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(btn_out)
        g1.addLayout(out_row)

        # group2：混剪参数
        group2 = QtWidgets.QGroupBox("混剪参数")
        g2 = QtWidgets.QFormLayout(group2)
        g2.setContentsMargins(10, 8, 10, 8)
        g2.setSpacing(8)

        # b.1 输出数量 & 每个混剪切片数
        self.outputs_spin = QtWidgets.QSpinBox()
        self.outputs_spin.setRange(1, 1000)
        self.outputs_spin.setValue(3)
        # 支持手动输入并即时解析
        self.outputs_spin.setKeyboardTracking(True)
        self.outputs_spin.setAccelerated(True)
        self.outputs_spin.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.slices_spin = QtWidgets.QSpinBox()
        self.slices_spin.setRange(1, 100)
        self.slices_spin.setValue(8)
        # 支持手动输入并即时解析
        self.slices_spin.setKeyboardTracking(True)
        self.slices_spin.setAccelerated(True)
        self.slices_spin.setFocusPolicy(QtCore.Qt.StrongFocus)
        g2.addRow("混剪视频数量", self.outputs_spin)
        g2.addRow("每个混剪切片数", self.slices_spin)

        # b.2 质量档位 & 并发数量
        self.quality_combo = QtWidgets.QComboBox()
        quality_label_mapping = [
            ("均衡", "balanced"),
            ("更高压缩", "compact"),
            ("极限压缩", "tiny"),
        ]
        for label, value in quality_label_mapping:
            self.quality_combo.addItem(label, value)

        self.concurrency_spin = QtWidgets.QSpinBox()
        self.concurrency_spin.setRange(1, 32)
        self.concurrency_spin.setValue(4)
        # 支持手动输入并即时解析
        self.concurrency_spin.setKeyboardTracking(True)
        self.concurrency_spin.setAccelerated(True)
        self.concurrency_spin.setFocusPolicy(QtCore.Qt.StrongFocus)
        g2.addRow("合成质量档位", self.quality_combo)
        g2.addRow("并发数量", self.concurrency_spin)


        # 放入垂直 Splitter 以获得更好的伸缩控制
        vsplit = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        try:
            group1.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            group2.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        except Exception:
            pass
        vsplit.addWidget(group1)
        vsplit.addWidget(group2)
        spacer = QtWidgets.QWidget()
        try:
            spacer.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass
        vsplit.addWidget(spacer)
        vsplit.setStretchFactor(0, 0)
        vsplit.setStretchFactor(1, 0)
        vsplit.setStretchFactor(2, 1)

        vbox.addWidget(vsplit)
        return panel

    def _build_right_panel(self) -> QtWidgets.QWidget:
        """构建右侧面板：进度 + 开始/停止按钮 + 结果表。"""
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(10)

        # 顶部控制区（分组：执行状态）
        status_group = QtWidgets.QGroupBox("运行状态")
        status_vbox = QtWidgets.QVBoxLayout(status_group)
        status_vbox.setContentsMargins(8, 8, 8, 8)
        status_vbox.setSpacing(8)

        ctl_row = QtWidgets.QHBoxLayout()
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        # 进度条文本与样式（与 generate_cover_tab 保持一致）
        try:
            self.progress_bar.setTextVisible(True)
        except Exception:
            pass
        self.start_stop_btn = QtWidgets.QPushButton("开始")
        self.start_stop_btn.clicked.connect(self._on_start_stop_clicked)
        ctl_row.addWidget(self.progress_bar, 1)
        ctl_row.addWidget(self.start_stop_btn)
        status_vbox.addLayout(ctl_row)
        vbox.addWidget(status_group)

        self._apply_progressbar_style(theme.PRIMARY_BLUE)
        self._apply_action_button_style(running=False)
       
        # 结果表
        result_group = QtWidgets.QGroupBox("执行结果")
        result_vbox = QtWidgets.QVBoxLayout(result_group)
        result_vbox.setContentsMargins(8, 8, 8, 8)
        result_vbox.setSpacing(8)

        self.results_table = QtWidgets.QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["文件输出路径", "时长", "文件大小"])
        # 列宽比例：输出路径 80%，分辨率 10%，大小 10%
        header = self.results_table.horizontalHeader()
        try:
            header.setStretchLastSection(False)
            header.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        except Exception:
            pass
        self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.results_table.doubleClicked.connect(self._on_open_selected_file)
        # 初始应用列宽，并在尺寸变化时自适应
        try:
            self._apply_results_table_column_widths()
            self.results_table.installEventFilter(self)
        except Exception:
            pass
        result_vbox.addWidget(self.results_table, 1)
        vbox.addWidget(result_group)

        return panel

    def _confirm_cleanup_output_dir(self, out_dir: str) -> Optional[bool]:
        """在开始执行前确认是否清理（删除）合成输出目录。

        逻辑
        ----
        - 若 `out_dir` 为空或不是有效目录，直接返回 False（不清理）。
        - 若目录存在且包含文件/子目录，则弹窗确认：
          - 按钮选项：
            1) 删除后开始
            2) 保留并开始
            3) 取消
        - 返回值：
          - True  → 用户选择“删除后开始”，调用方应清理该目录
          - False → 用户选择“保留并开始”，继续执行但不清理
          - None  → 用户选择“取消”，应中止开始流程

        Parameters
        ----------
        out_dir : str
            合成输出目录路径。

        Returns
        -------
        Optional[bool]
            用户选择的操作，如上所述。
        """
        try:
            if not out_dir or not os.path.isdir(out_dir):
                return False
            # 统计目录条目数，用于提示
            try:
                entries = list(Path(out_dir).iterdir())
                entry_count = len(entries)
            except Exception:
                entry_count = 0
            if entry_count <= 0:
                return False

            msg = QtWidgets.QMessageBox(self)
            msg.setIcon(QtWidgets.QMessageBox.Question)
            msg.setWindowTitle("确认清理输出目录")
            msg.setText(
                f"检测到合成输出目录已存在且包含 {entry_count} 个条目:\n\n{out_dir}\n\n是否删除该目录内的所有文件后再开始？"
            )
            btn_delete = msg.addButton("删除后开始", QtWidgets.QMessageBox.AcceptRole)
            btn_keep = msg.addButton("保留并开始", QtWidgets.QMessageBox.ActionRole)
            btn_cancel = msg.addButton("取消", QtWidgets.QMessageBox.RejectRole)
            msg.exec()

            clicked = msg.clickedButton()
            if clicked == btn_delete:
                return True
            if clicked == btn_keep:
                return False
            return None
        except Exception:
            # 若弹窗失败，保守策略：不清理，继续执行
            return False

    # --- 样式与尺寸（与截图/封面页保持一致） ---
    def _apply_progressbar_style(self, chunk_color: str = theme.PRIMARY_BLUE) -> None:
        """统一设置进度条的尺寸与样式，使其与 generate_cover_tab 一致。

        - 横向扩展、纵向固定高度；高度依据屏幕 DPI 自适应
        - 文本居中显示；进度块颜色可配置
        """
        try:
            if self.progress_bar is None:
                return
            # 尺寸策略：横向扩展、纵向固定
            self.progress_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            # 计算 DPI 缩放
            screen = QtWidgets.QApplication.primaryScreen()
            dpi = screen.logicalDotsPerInch() if screen else 96.0
            scale = max(1.0, dpi / 96.0)
        except Exception:
            scale = 1.0

        # 高度与字号
        base_h = 32
        height = int(max(28, min(52, base_h * scale)))
        try:
            self.progress_bar.setFixedHeight(height)
            # 缓存统一控件高度，供按钮样式使用（若后续需要）
            self._control_height = height
        except Exception:
            # 回退：无缓存则使用主题默认高度（若存在）
            try:
                
                self._control_height = getattr(self, "_control_height", getattr(theme, "BUTTON_HEIGHT", height))
            except Exception:
                self._control_height = height

        try:
            font = self.progress_bar.font()
            base_pt = 11
            pt_size = int(max(base_pt, min(16, base_pt * scale)))
            font.setPointSize(pt_size)
            self.progress_bar.setFont(font)
        except Exception:
            pass

        # 样式表：统一从主题构造样式字符串
        try:
            style = theme.build_progressbar_stylesheet(height=height, chunk_color=chunk_color)
            self.progress_bar.setStyleSheet(style)
        except Exception:
            pass

    def _apply_action_button_style(self, running: bool) -> None:
        """统一设置开始/停止按钮的高度与样式，使其与 generate_cover_tab 一致。

        - 空闲态使用主题主色（蓝色），运行态使用危险色（红色）
        - 按钮高度与进度条一致（使用缓存的 `_control_height`）
        """
        try:
            if self.start_stop_btn is None:
                return
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

            # 采用与进度条一致的字体大小
            try:
                if self.progress_bar is not None:
                    self.start_stop_btn.setFont(self.progress_bar.font())
            except Exception:
                pass
            self.start_stop_btn.setStyleSheet(running_style if running else idle_style)
            self.start_stop_btn.setFixedHeight(height)
        except Exception:
            pass

    def _apply_results_table_column_widths(self) -> None:
        """按照 80%/10%/10% 比例设置结果表的三列宽度，并在不同 DPI 下保持可读性。"""
        if not getattr(self, "results_table", None):
            return
        try:
            total = self.results_table.viewport().width()
            if not total or total <= 0:
                total = self.results_table.width()

            w0 = int(total * 0.70)  # 输出路径
            w1 = int(total * 0.15)   # 分辨率
            w2 = int(total * 0.15)   # 文件大小
            self.results_table.setColumnWidth(0, w0)
            self.results_table.setColumnWidth(1, w1)
            self.results_table.setColumnWidth(2, w2)
        except Exception:
            pass

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        """监听结果表尺寸变化，实时按比例调整列宽。"""
        try:
            if obj is getattr(self, "results_table", None) and event.type() == QtCore.QEvent.Resize:
                # 使用单次定时器，避免频繁重算引发抖动
                QtCore.QTimer.singleShot(0, self._apply_results_table_column_widths)
        except Exception:
            pass
        try:
            return super().eventFilter(obj, event)
        except Exception:
            return False

    # ----------------------------- 交互逻辑 ----------------------------- #
    def _on_add_video_dir(self) -> None:
        """添加一个视频目录到列表，并动态更新“混剪输出”目录。

        逻辑
        ----
        - 支持一次选择多个目录，逐一去重后添加到列表。
        - 成功添加后，将下方“混剪输出”设置为“最后一个新增目录/混剪”。
        """
        dlg = QtWidgets.QFileDialog(self, "选择视频目录")
        dlg.setFileMode(QtWidgets.QFileDialog.Directory)
        dlg.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
        if dlg.exec():
            dirs = dlg.selectedFiles()
            if not self.video_list:
                return
            last_added: Optional[str] = None
            for d in dirs:
                if d and os.path.isdir(d):
                    # 避免重复
                    exists = False
                    for i in range(self.video_list.count()):
                        if self.video_list.item(i).text() == d:
                            exists = True
                            break
                    if not exists:
                        self.video_list.addItem(d)
                        last_added = d

            # 动态更新“混剪输出”为“最后一个新增目录/混剪”
            try:
                if last_added and self.output_edit:
                    self.output_edit.setText(os.path.join(last_added, "混剪"))
            except Exception:
                pass

    def _on_remove_selected_dirs(self) -> None:
        """移除列表中选中的目录。"""
        if not self.video_list:
            return
        for item in self.video_list.selectedItems():
            row = self.video_list.row(item)
            self.video_list.takeItem(row)

    def _on_browse_bgm(self) -> None:
        """浏览选择背景音乐：支持选择音频文件或目录。"""
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

    def _on_browse_output_dir(self) -> None:
        """浏览选择输出目录（仅目录）。"""
        dname = QtWidgets.QFileDialog.getExistingDirectory(self, "选择输出目录")
        if dname:
            self.output_edit.setText(dname)

    def _collect_settings(self) -> Optional[dict]:
        """采集并校验左侧面板参数，返回设置字典。"""
        if not self.video_list:
            return None
        dirs = [self.video_list.item(i).text() for i in range(self.video_list.count())]
        if len(dirs) == 0:
            QtWidgets.QMessageBox.warning(self, "提示", "请选择至少一个视频目录")
            return None
        bgm = self.bgm_edit.text().strip() if self.bgm_edit else ""
        out_dir = self.output_edit.text().strip() if self.output_edit else ""
        outputs = int(self.outputs_spin.value()) if self.outputs_spin else 1
        slices = int(self.slices_spin.value()) if self.slices_spin else 1
        quality = self.quality_combo.currentData() if self.quality_combo else "balanced"
        concurrency = int(self.concurrency_spin.value()) if self.concurrency_spin else 1

        # 基本校验
        for d in dirs:
            if not os.path.isdir(d):
                QtWidgets.QMessageBox.warning(self, "提示", f"目录不可用：{d}")
                return None
        if out_dir and not os.path.isdir(out_dir):
            try:
                Path(out_dir).mkdir(parents=True, exist_ok=True)
            except Exception:
                QtWidgets.QMessageBox.warning(self, "提示", f"无法创建输出目录：{out_dir}")
                return None
        if outputs < 1 or slices < 1 or concurrency < 1:
            QtWidgets.QMessageBox.warning(self, "提示", "混剪视频数量、切片数、并发数均需 ≥ 1")
            return None

        return {
            "video_dirs": dirs,
            "bgm_path": bgm,
            "output_dir": out_dir,
            "outputs": outputs,
            "slices": slices,
            "quality": quality,
            "concurrency": concurrency,
        }

    def _on_start_stop_clicked(self) -> None:
        """开始或停止任务：按钮在“开始/停止”两种状态互斥切换。"""
        if not self._is_running:
            try:
                app = QtWidgets.QApplication.instance()
                if not (bool(run_preflight_checks(app)) if app is not None else False):
                    return
            except Exception:
                return
            settings = self._collect_settings()
            if not settings:
                return
            # 在启动前确认是否清理合成输出目录
            try:
                decision = self._confirm_cleanup_output_dir(settings.get("output_dir", ""))
            except Exception:
                decision = False
            if decision is None:
                # 用户取消开始
                return
            if decision is True:
                # 用户选择删除后开始：清空目录
                out_dir = settings.get("output_dir", "")
                if out_dir:
                    try:
                        shutil.rmtree(out_dir, ignore_errors=False)
                        os.makedirs(out_dir, exist_ok=True)
                    except Exception as e:
                        QtWidgets.QMessageBox.critical(self, "错误", f"清理输出目录失败：{e}")
                        return
            # 启动线程与工作者
            self._thread = QtCore.QThread(self)
            self._worker = ConcatWorker(
                video_dirs=settings["video_dirs"],
                bgm_path=settings["bgm_path"],
                output_dir=settings["output_dir"],
                outputs=settings["outputs"],
                slices_per_output=settings["slices"],
                quality_profile=settings["quality"],
                concurrency=settings["concurrency"],
            )
            self._worker.moveToThread(self._thread)
            # 信号连接
            self._thread.started.connect(self._worker.run)
            self._worker.phase.connect(self._on_phase)
            self._worker.progress.connect(self._on_progress)
            self._worker.error.connect(self._on_error)
            self._worker.finished.connect(self._on_finished)
            self._worker.results.connect(self._on_results)
            # 线程结束清理
            self._thread.finished.connect(self._thread.deleteLater)
            # 更新 UI 状态
            self._is_running = True
            self.start_stop_btn.setText("停止")
            try:
                self._apply_action_button_style(running=True)
            except Exception:
                pass
            self.progress_bar.setValue(0)
            # 清空旧结果
            self.results_table.setRowCount(0)
            # 启动
            self._thread.start()
        else:
            # 请求停止
            if self._worker:
                self._worker.stop()
            self.start_stop_btn.setEnabled(False)

    def _on_phase(self, name: str) -> None:
        """阶段变更时的提示，并设置分段权重与初始文本。

        - 归一化阶段占 30%，文本显示为“归一化：完成数 | 待转换总数”。
        - 合成阶段占 70%，文本显示为“混合视频：完成数 | 待合成总数”。
        """
        try:
            self._phase_name = str(name)
            if name == "normalize":
                self._phase_start = 0
                self._phase_span = 30
                self.progress_bar.setFormat("归一化：0 | 0")
            elif name == "concat":
                self._phase_start = 30
                self._phase_span = 70
                self.progress_bar.setFormat("混合视频：0 | 0")
            else:
                self._phase_start = 0
                self._phase_span = 100
                self.progress_bar.setFormat("进度：0 | 0")
        except Exception:
            pass

    def _on_progress(self, done: int, total: int) -> None:
        """更新进度条的分段进度与文本，显示“完成数 | 总数”。

        逻辑
        ----
        - 使用当前阶段的起始与跨度，将实际完成比例映射到 0..100 分段。
        - 文本根据阶段显示：
          归一化 → “归一化：完成数 | 待转换总数”；
          合成 → “混合视频：完成数 | 待合成总数”；
          其他 → “进度：完成数 | 总数”。
        """
        try:
            start = int(getattr(self, "_phase_start", 0))
            span = int(getattr(self, "_phase_span", 100))
            label = "进度"
            phase = str(getattr(self, "_phase_name", ""))
            if phase == "normalize":
                label = "归一化"
            elif phase == "concat":
                label = "混合视频"

            if total <= 0:
                self.progress_bar.setValue(start)
                self.progress_bar.setFormat(f"{label}：0 | 0")
                return

            ratio = max(0.0, min(1.0, float(done) / float(total)))
            weighted = int(start + span * ratio)
            self.progress_bar.setValue(max(0, min(100, weighted)))
            self.progress_bar.setFormat(f"{label}：{int(done)} | {int(total)}")
        except Exception:
            try:
                self.progress_bar.setValue(0)
                self.progress_bar.setFormat("进度：0 | 0")
            except Exception:
                pass

    def _on_error(self, msg: str) -> None:
        """显示错误并复位按钮状态。"""
        QtWidgets.QMessageBox.critical(self, "错误", msg)
        self._reset_run_state()

    def _on_finished(self, success_count: int, fail_count: int) -> None:
        """任务完成后的状态更新，并提供打开输出目录的操作。"""
        try:
            dlg = QtWidgets.QMessageBox(self)
            dlg.setWindowTitle("完成")
            dlg.setIcon(QtWidgets.QMessageBox.Information)
            dlg.setText(f"成功 {success_count}，失败 {fail_count}")
            open_btn = dlg.addButton("打开目录", QtWidgets.QMessageBox.AcceptRole)
            close_btn = dlg.addButton("关闭", QtWidgets.QMessageBox.RejectRole)
            dlg.exec()

            if dlg.clickedButton() == open_btn:
                out_dir = self._get_effective_output_dir()
                if out_dir and out_dir.exists():
                    try:
                        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(out_dir)))
                    except Exception:
                        try:
                            if os.name == "nt":
                                os.startfile(str(out_dir))  # type: ignore[attr-defined]
                            else:
                                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(out_dir)))
                        except Exception:
                            QtWidgets.QMessageBox.warning(self, "提示", f"无法打开目录：{out_dir}")
                else:
                    QtWidgets.QMessageBox.warning(self, "提示", "输出目录不存在或不可用")
        except Exception:
            QtWidgets.QMessageBox.information(self, "完成", f"成功 {success_count}，失败 {fail_count}")
        finally:
            self._reset_run_state()

    def _get_effective_output_dir(self) -> Optional[Path]:
        """计算当前任务使用的输出目录路径。

        优先级
        ------
        1) 读取正在运行或刚完成的 worker 的 `output_dir`
        2) 若 UI 中的输出编辑框有值，使用该值
        3) 若无值，则以首个视频目录的上级目录下的 `混剪` 作为默认

        Returns
        -------
        Optional[Path]
            有效的输出目录路径；若无法计算则返回 None。
        """
        try:
            if self._worker and getattr(self._worker, "output_dir", ""):
                return Path(self._worker.output_dir)
        except Exception:
            pass

        try:
            if self.output_edit:
                text = self.output_edit.text().strip()
                if text:
                    return Path(text)
        except Exception:
            pass

        return None

    def _on_results(self, paths: List[str]) -> None:
        """将结果填充到表格（路径、分辨率、大小），支持双击打开。"""
        self.results_table.setRowCount(0)
        for p in paths:
            pt = Path(p)
            dur = ffprobe_duration(pt)
            # 秒转换成 HH:MM:SS
            if dur:
                dur = time.strftime("%H:%M:%S", time.gmtime(dur))
            
            try:
                size_mb = pt.stat().st_size / (1024 * 1024)
                size_text = f"{size_mb:.1f} MB"
            except Exception:
                size_text = "?"
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self.results_table.setItem(row, 0, QtWidgets.QTableWidgetItem(p))
            self.results_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(dur) if dur else "?"))
            self.results_table.setItem(row, 2, QtWidgets.QTableWidgetItem(size_text))

    def _on_open_selected_file(self) -> None:
        """双击打开选中文件。"""
        idxs = self.results_table.selectedIndexes()
        if not idxs:
            return
        row = idxs[0].row()
        item = self.results_table.item(row, 0)
        if not item:
            return
        path = item.text()
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        except Exception:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def _reset_run_state(self) -> None:
        """复位运行状态与按钮文本，安全清理线程。"""
        self._is_running = False
        try:
            self.start_stop_btn.setText("开始")
            self.start_stop_btn.setEnabled(True)
            try:
                self._apply_action_button_style(running=False)
            except Exception:
                pass
        except Exception:
            pass
        try:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(3000)
        except Exception:
            pass

__all__ = ["VideoConcatTab"]
