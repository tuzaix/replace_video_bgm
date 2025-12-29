"""
Video Remixed Video Audio Tab

提供“模仿混剪”标签页，左右分栏布局：
- 左侧面板：参数输入（模仿视频目录、素材库目录、输出目录、混剪数量、GPU加速、编码档位、视频类型）
- 右侧面板：运行状态、进度条、结果列表
"""

from __future__ import annotations

import os
import pathlib
from typing import Optional, List, Tuple, Dict

from PySide6 import QtWidgets, QtCore, QtGui

from gui.utils import theme
from utils.xprint import xprint
from utils.common_utils import is_video_file
from video_tool.video_remixed_video_audio import VideoRemixedVideoAudio

class VideoRemixWorker(QtCore.QObject):
    """后台执行模仿混剪的工作器。"""
    
    progress = QtCore.Signal(int, int) # (current, total)
    finished = QtCore.Signal(list)      # 成功的输出文件列表
    error = QtCore.Signal(str)
    
    def __init__(self) -> None:
        super().__init__()
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True

    @QtCore.Slot(dict)
    def run(self, params: dict) -> None:
        """后台执行混剪逻辑。"""
        try:
            imitation_dir = params['imitation_dir']
            segment_dir = params['segment_dir']
            output_dir = params['output_dir']
            count = params['count']
            use_gpu = params['use_gpu']
            profile = params['profile']
            video_type = params['video_type']

            remixer = VideoRemixedVideoAudio(
                imitation_dir=imitation_dir,
                segment_dir=segment_dir,
                output_dir=output_dir,
                use_gpu=use_gpu,
                encode_profile=profile,
                video_type=video_type
            )
            
            # 由于 VideoRemixedVideoAudio.process 内部没有暴露进度回调，
            # 我们这里通过重写或包装来实现进度反馈。
            # 为了不破坏原有逻辑，我们手动模拟 process 的部分核心逻辑以便发送信号。
            
            imitation_videos = [p for p in remixer.imitation_dir.glob("*") if p.is_file() and is_video_file(str(p))]
            if not imitation_videos:
                self.error.emit("模仿视频目录下没有找到视频文件。")
                return

            all_segments = remixer._get_video_segments()
            if not all_segments:
                self.error.emit("素材库中没有找到有效的视频切片。")
                return
            
            total_tasks = len(imitation_videos) * count
            done_tasks = 0
            results = []

            for idx, video_path in enumerate(imitation_videos, 1):
                if self._stopping: break
                
                audio_path = remixer._extract_audio_lossless(video_path)
                if not audio_path: continue
                
                from utils.calcu_video_info import ffprobe_duration
                audio_duration = ffprobe_duration(audio_path)
                
                # 1.1 提取并标准化片头（前3秒）
                intro_path = remixer._extract_and_normalize_intro(video_path)
                if not intro_path:
                    continue
                
                # 调整后续素材需要填补的时长
                remaining_duration = max(0, audio_duration - 3.0)

                for i in range(count):
                    if self._stopping: break
                    
                    selected_data = remixer._select_segments_for_duration(all_segments, remaining_duration)
                    if not selected_data: continue
                    
                    selected_paths = [item[0] for item in selected_data]
                    output_name = f"{video_path.stem}_remix_{i+1:02d}.mp4"
                    output_path = remixer.output_dir / output_name
                    
                    success = remixer._combine_segments_with_audio(
                        selected_paths, audio_path, audio_duration, remixer.target_res, output_path, intro_path=intro_path
                    )
                    
                    if success:
                        results.append(str(output_path))
                    
                    done_tasks += 1
                    self.progress.emit(done_tasks, total_tasks)

            self.finished.emit(results)
            
        except Exception as e:
            self.error.emit(f"发生错误: {str(e)}")

class VideoRemixedVideoAudioTab(QtWidgets.QWidget):
    """“模仿混剪”标签页。"""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._is_running = False
        self._worker: Optional[VideoRemixWorker] = None
        self._thread: Optional[QtCore.QThread] = None
        
        self.root_layout = QtWidgets.QHBoxLayout(self)
        self._build_page()

    def is_running(self) -> bool:
        return self._is_running

    def request_stop(self) -> None:
        if self._worker:
            self._worker.stop()

    def _build_page(self) -> None:
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        self.root_layout.addWidget(splitter)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        
        # 目录选择
        group_dir = QtWidgets.QGroupBox("目录设置")
        dir_lay = QtWidgets.QVBoxLayout(group_dir)
        
        # 模仿视频目录
        self.imitation_edit = self._create_dir_row(dir_lay, "模仿视频目录：")
        # 素材库目录
        self.segment_edit = self._create_dir_row(dir_lay, "视频素材库：")
        # 输出目录
        self.output_edit = self._create_dir_row(dir_lay, "输出目录(可选)：")
        
        layout.addWidget(group_dir)

        # 参数设置
        group_params = QtWidgets.QGroupBox("混剪参数")
        param_lay = QtWidgets.QFormLayout(group_params)
        
        self.count_spin = QtWidgets.QSpinBox()
        self.count_spin.setRange(1, 100)
        self.count_spin.setValue(1)
        param_lay.addRow("生成数量：", self.count_spin)
        
        self.gpu_check = QtWidgets.QCheckBox("启用 GPU 加速")
        self.gpu_check.setChecked(True)
        param_lay.addRow(self.gpu_check)
        
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.addItems(["balanced", "visual", "size"])
        param_lay.addRow("编码档位：", self.profile_combo)
        
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(["shorts", "video"])
        param_lay.addRow("视频类型：", self.type_combo)
        
        layout.addWidget(group_params)
        layout.addStretch()
        
        return widget

    def _create_dir_row(self, layout, label_text):
        layout.addWidget(QtWidgets.QLabel(label_text))
        row = QtWidgets.QHBoxLayout()
        edit = QtWidgets.QLineEdit()
        btn = QtWidgets.QPushButton("浏览...")
        btn.clicked.connect(lambda: self._on_browse_dir(edit))
        row.addWidget(edit)
        row.addWidget(btn)
        layout.addLayout(row)
        return edit

    def _on_browse_dir(self, edit):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            edit.setText(path)

    def _build_right_panel(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # 运行控制
        ctrl_group = QtWidgets.QGroupBox("运行控制")
        ctrl_h = QtWidgets.QHBoxLayout(ctrl_group)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.run_btn = QtWidgets.QPushButton("开始混剪")
        self.run_btn.clicked.connect(self._on_toggle_run)
        ctrl_h.addWidget(self.progress_bar, 1)
        ctrl_h.addWidget(self.run_btn, 0)
        layout.addWidget(ctrl_group)
        
        self._apply_progressbar_style()
        self._apply_action_button_style(False)
        
        # 结果表格
        result_group = QtWidgets.QGroupBox("生成结果")
        result_vbox = QtWidgets.QVBoxLayout(result_group)
        self.result_table = QtWidgets.QTableWidget(0, 1)
        self.result_table.setHorizontalHeaderLabels(["输出文件路径"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.result_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        result_vbox.addWidget(self.result_table)
        layout.addWidget(result_group)
        
        return widget

    def _apply_progressbar_style(self, chunk_color: str = theme.PRIMARY_BLUE) -> None:
        """统一设置进度条的尺寸与样式。"""
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
            style = theme.build_progressbar_stylesheet(height=height, chunk_color=chunk_color)
            self.progress_bar.setStyleSheet(style)
        except Exception:
            pass

    def _on_toggle_run(self):
        if self._is_running:
            self.request_stop()
            return
        
        # 验证输入
        imi = self.imitation_edit.text().strip()
        seg = self.segment_edit.text().strip()
        if not imi or not seg:
            QtWidgets.QMessageBox.warning(self, "错误", "请先选择模仿视频目录和素材库目录！")
            return
        
        self._is_running = True
        self._apply_action_button_style(True)
        self.result_table.setRowCount(0)
        self.progress_bar.setValue(0)
        
        self._thread = QtCore.QThread()
        self._worker = VideoRemixWorker()
        self._worker.moveToThread(self._thread)
        
        params = {
            'imitation_dir': imi,
            'segment_dir': seg,
            'output_dir': self.output_edit.text().strip() or None,
            'count': self.count_spin.value(),
            'use_gpu': self.gpu_check.isChecked(),
            'profile': self.profile_combo.currentText(),
            'video_type': self.type_combo.currentText()
        }
        
        self._thread.started.connect(lambda: self._worker.run(params))
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(lambda e: QtWidgets.QMessageBox.critical(self, "错误", e))
        
        self._thread.start()

    def _on_progress(self, current, total):
        try:
            pct = int(round(0 if total <= 0 else (current / float(total)) * 100.0))
            self.progress_bar.setValue(pct)
        except Exception:
            pass

    def _on_finished(self, results):
        self._is_running = False
        self._apply_action_button_style(False)
        
        for path in results:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self.result_table.setItem(row, 0, QtWidgets.QTableWidgetItem(path))
        
        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
        self._worker = None

    def _apply_action_button_style(self, running: bool) -> None:
        """统一设置开始/停止按钮样式。"""
        try:
            if self.run_btn is None:
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
            self.run_btn.setText("停止运行" if running else "开始混剪")
            self.run_btn.setStyleSheet(running_style if running else idle_style)
        except Exception:
            pass
