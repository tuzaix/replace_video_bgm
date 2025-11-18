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
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

try:
    from gui.utils import theme  # 仅用于配色（可选）
except Exception:
    class theme:  # 回退，避免导入失败阻塞
        PRIMARY_BLUE = "#409eff"
        SUCCESS_GREEN = "#67C23A"
        DANGER_RED = "#F56C6C"

from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env  # type: ignore
bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)

from concat_tool.normalize_video import VideoNormalizer  # type: ignore
from concat_tool.concat import VideoConcat  # type: ignore


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
        trim_head_s: float,
        trim_tail_s: float,
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
        trim_head_s : float
            对素材裁剪的头部秒数。
        trim_tail_s : float
            对素材裁剪的尾部秒数。
        """
        super().__init__()
        self.video_dirs = [str(Path(p)) for p in video_dirs]
        self.bgm_path = str(bgm_path or "").strip()
        self.output_dir = str(output_dir or "").strip()
        self.outputs = int(outputs)
        self.slices_per_output = int(slices_per_output)
        self.quality_profile = str(quality_profile or "balanced")
        self.concurrency = max(1, int(concurrency))
        self.trim_head_s = max(0.0, float(trim_head_s))
        self.trim_tail_s = max(0.0, float(trim_tail_s))
        self._stopping = False

    def stop(self) -> None:
        """请求软停止。正在进行的任务会尽快结束。"""
        self._stopping = True

    # ----------------------------- 内部辅助方法 ----------------------------- #
    def _emit(self, text: str) -> None:
        """安全发射日志文本。"""
        try:
            self.log.emit(str(text))
        except Exception:
            pass

    @staticmethod
    def _probe_resolution(path: Path) -> Optional[Tuple[int, int]]:
        """使用 ffprobe 探测视频分辨率 (width, height)。"""
        ffprobe_bin = shutil.which("ffprobe")
        if not ffprobe_bin:
            return None
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode != 0:
                return None
            out = (res.stdout or b"").decode("utf-8", errors="ignore").strip()
            if "x" in out:
                w, h = out.split("x", 1)
                return (int(float(w)), int(float(h)))
        except Exception:
            return None
        return None

    # ----------------------------- 运行主流程 ----------------------------- #
    @QtCore.Slot()
    def run(self) -> None:
        """执行完整流程：归一化 → 选择分辨率组 → 随机拼接输出。

        流程
        ----
        1) 校验参数与输出目录
        2) 对每个源目录执行归一化，输出到该目录下 `临时` 子目录
        3) 汇总全部归一化视频，按分辨率分组，选视频数量最多的组
        4) 并发生成 `outputs` 个混剪视频，每个随机选取 `slices_per_output` 个素材
        5) 裁剪仅在归一化阶段应用；随后调用 `concat_tool.concat.VideoConcat` 拼接
        6) 发射进度与结果
        """
        if self._stopping:
            self.error.emit("任务已取消")
            return

        # 参数校验与输出目录准备
        if not self.video_dirs:
            self.error.emit("请选择至少一个视频目录")
            return
        out_dir = Path(self.output_dir) if self.output_dir else Path(self.video_dirs[0]).parent / "longvideo_outputs"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # 归一化阶段
        try:
            self.phase.emit("normalize")
        except Exception:
            pass
        self._emit("🔧 正在归一化素材…（裁剪仅在该阶段应用）")

       

        normalized_dirs: List[Path] = []
        total_dirs = len(self.video_dirs)
        done_dirs = 0
        for src in self.video_dirs:
            if self._stopping:
                self.error.emit("任务已取消")
                return
            src_p = Path(src)
            if not src_p.exists() or not src_p.is_dir():
                self.error.emit(f"目录不存在或不可用: {src}")
                return
            tmp_out = src_p / "临时"
            try:
                tmp_out.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            normalizer = VideoNormalizer(fps=25, use_gpu=True, threads=self.concurrency)
            ok_count = normalizer.normalize(
                str(src_p),
                str(tmp_out),
                on_progress=lambda d, t: self.progress.emit(d, t),
                trim_head_s=self.trim_head_s,
                trim_tail_s=self.trim_tail_s,
            )
            if ok_count > 0:
                normalized_dirs.append(tmp_out)
            done_dirs += 1
            try:
                self.progress.emit(done_dirs, total_dirs)
            except Exception:
                pass
        self._emit(f"✅ 归一化完成，处理目录 {done_dirs}/{total_dirs}")

        # 收集归一化素材并按分辨率分组
        all_videos: List[Path] = []
        for nd in normalized_dirs:
            try:
                for p in nd.iterdir():
                    if p.is_file() and p.suffix.lower() == ".mp4" and "_normalized" in p.stem:
                        all_videos.append(p)
            except Exception:
                pass
        if not all_videos:
            self.error.emit("归一化后未发现可用素材")
            return

        groups: dict[Tuple[int, int], List[Path]] = {}
        for v in all_videos:
            res = self._probe_resolution(v) or (0, 0)
            groups.setdefault(res, []).append(v)
        # 选择视频数量最多的分辨率组，若并列则取面积更大的分辨率
        best_res = max(groups.keys(), key=lambda r: (len(groups[r]), r[0] * r[1]))
        candidates = groups.get(best_res, [])
        self._emit(f"📹 选择分辨率组 {best_res[0]}x{best_res[1]}，素材数 {len(candidates)}")
        if not candidates:
            self.error.emit("分辨率分组失败：候选为空")
            return

        # 混剪阶段
        try:
            self.phase.emit("concat")
        except Exception:
            pass
        self._emit("🎬 开始生成混剪视频…（不再额外裁剪）")

     

        success: List[str] = []
        fail = 0
        total_outputs = self.outputs

        from concurrent.futures import ThreadPoolExecutor, as_completed
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
            out_path = out_dir / f"concat_{idx}.mp4"
            vc = VideoConcat(
                slices=slices,
                out_path=out_path,
                bgm_path=Path(self.bgm_path) if self.bgm_path else None,
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
                    self.progress.emit(done, total_outputs)
                except Exception:
                    pass

        # 无需清理临时切片目录（未创建临时切片）

        # 完成信号
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
        self.trim_head_dbl: Optional[QtWidgets.QDoubleSpinBox] = None
        self.trim_tail_dbl: Optional[QtWidgets.QDoubleSpinBox] = None

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
        self.video_list.addItems([r"E:\Download\社媒助手\抖音\潮汕菲宝"])
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
        self.output_edit.setText(os.path.join(self.video_list.item(0).text(), "临时"))
        btn_out = QtWidgets.QPushButton("浏览…")
        btn_out.clicked.connect(self._on_browse_output_dir)
        out_row.addWidget(QtWidgets.QLabel("合成输出"), 0)
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
        self.outputs_spin.setRange(1, 100)
        self.outputs_spin.setValue(3)
        self.outputs_spin.setKeyboardTracking(False)
        self.slices_spin = QtWidgets.QSpinBox()
        self.slices_spin.setRange(1, 50)
        self.slices_spin.setValue(8)
        self.slices_spin.setKeyboardTracking(False)
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
        self.concurrency_spin.setKeyboardTracking(False)
        g2.addRow("合成质量档位", self.quality_combo)
        g2.addRow("并发数量", self.concurrency_spin)

        # b.3 素材裁剪头尾
        self.trim_head_dbl = QtWidgets.QDoubleSpinBox()
        self.trim_head_dbl.setRange(0.0, 600.0)
        self.trim_head_dbl.setDecimals(1)
        self.trim_head_dbl.setSingleStep(0.5)
        self.trim_head_dbl.setValue(0.0)
        self.trim_tail_dbl = QtWidgets.QDoubleSpinBox()
        self.trim_tail_dbl.setRange(0.0, 600.0)
        self.trim_tail_dbl.setDecimals(1)
        self.trim_tail_dbl.setSingleStep(0.5)
        self.trim_tail_dbl.setValue(0.0)
        g2.addRow("剪裁头部(秒)", self.trim_head_dbl)
        g2.addRow("剪裁尾部(秒)", self.trim_tail_dbl)

        # 放入垂直 Splitter 以获得更好的伸缩控制
        vsplit = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        try:
            group1.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            group2.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        except Exception:
            pass
        vsplit.addWidget(group1)
        vsplit.addWidget(group2)
        vsplit.setStretchFactor(0, 0)
        vsplit.setStretchFactor(1, 1)

        vbox.addWidget(vsplit)
        return panel

    def _build_right_panel(self) -> QtWidgets.QWidget:
        """构建右侧面板：进度 + 开始/停止按钮 + 结果表。"""
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(10)

        # 顶部控制区
        ctl_row = QtWidgets.QHBoxLayout()
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.start_stop_btn = QtWidgets.QPushButton("开始")
        self.start_stop_btn.clicked.connect(self._on_start_stop_clicked)
        ctl_row.addWidget(self.progress_bar, 1)
        ctl_row.addWidget(self.start_stop_btn)
        vbox.addLayout(ctl_row)

        # 结果表
        self.results_table = QtWidgets.QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["文件输出路径", "文件分辨率", "文件大小"])
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.results_table.doubleClicked.connect(self._on_open_selected_file)
        vbox.addWidget(self.results_table, 1)

        return panel

    # ----------------------------- 交互逻辑 ----------------------------- #
    def _on_add_video_dir(self) -> None:
        """添加一个视频目录到列表。"""
        dlg = QtWidgets.QFileDialog(self, "选择视频目录")
        dlg.setFileMode(QtWidgets.QFileDialog.Directory)
        dlg.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
        if dlg.exec():
            dirs = dlg.selectedFiles()
            if not self.video_list:
                return
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
        trim_head = float(self.trim_head_dbl.value()) if self.trim_head_dbl else 0.0
        trim_tail = float(self.trim_tail_dbl.value()) if self.trim_tail_dbl else 0.0

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
        if trim_head < 0 or trim_tail < 0:
            QtWidgets.QMessageBox.warning(self, "提示", "裁剪秒数不能为负")
            return None

        return {
            "video_dirs": dirs,
            "bgm_path": bgm,
            "output_dir": out_dir,
            "outputs": outputs,
            "slices": slices,
            "quality": quality,
            "concurrency": concurrency,
            "trim_head": trim_head,
            "trim_tail": trim_tail,
        }

    def _on_start_stop_clicked(self) -> None:
        """开始或停止任务：按钮在“开始/停止”两种状态互斥切换。"""
        if not self._is_running:
            settings = self._collect_settings()
            if not settings:
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
                trim_head_s=settings["trim_head"],
                trim_tail_s=settings["trim_tail"],
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
        """阶段变更时的简单提示。"""
        if name == "normalize":
            self.progress_bar.setFormat("归一化：%p%")
        elif name == "concat":
            self.progress_bar.setFormat("混剪：%p%")
        else:
            self.progress_bar.setFormat("%p%")

    def _on_progress(self, done: int, total: int) -> None:
        """更新进度条（0..100）。"""
        if total <= 0:
            self.progress_bar.setValue(0)
            return
        pct = int(done * 100 / total)
        self.progress_bar.setValue(max(0, min(100, pct)))

    def _on_error(self, msg: str) -> None:
        """显示错误并复位按钮状态。"""
        QtWidgets.QMessageBox.critical(self, "错误", msg)
        self._reset_run_state()

    def _on_finished(self, success_count: int, fail_count: int) -> None:
        """任务完成后的状态更新。"""
        QtWidgets.QMessageBox.information(self, "完成", f"成功 {success_count}，失败 {fail_count}")
        self._reset_run_state()

    def _on_results(self, paths: List[str]) -> None:
        """将结果填充到表格（路径、分辨率、大小），支持双击打开。"""
        self.results_table.setRowCount(0)
        for p in paths:
            try:
                res = self._probe_resolution(Path(p))
            except Exception:
                res = None
            try:
                size_mb = Path(p).stat().st_size / (1024 * 1024)
                size_text = f"{size_mb:.1f} MB"
            except Exception:
                size_text = "?"
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self.results_table.setItem(row, 0, QtWidgets.QTableWidgetItem(p))
            self.results_table.setItem(row, 1, QtWidgets.QTableWidgetItem(
                f"{res[0]}x{res[1]}" if res else "?"))
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
        except Exception:
            pass
        try:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(3000)
        except Exception:
            pass


def create_concat_tab(parent: Optional[QtWidgets.QWidget] = None) -> Tuple[QtWidgets.QWidget, QtWidgets.QHBoxLayout]:
    """工厂方法：创建“视频混剪”标签页容器与其根布局。

    Parameters
    ----------
    parent : Optional[QtWidgets.QWidget]
        父控件。

    Returns
    -------
    Tuple[QtWidgets.QWidget, QtWidgets.QHBoxLayout]
        (tab_widget, root_layout)

    Notes
    -----
    该方法用于与旧版 MainWindow 行为兼容，便于以一致方式注册标签页。
    """
    tab = VideoConcatTab(parent)
    return tab, tab.root_layout


__all__ = ["VideoConcatTab", "create_concat_tab"]