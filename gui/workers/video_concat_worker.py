"""
GUI background worker for running the video concatenation workflow.

This module isolates the business execution from the GUI widgets, so that
the main window and tabs can focus on UI responsibilities. The worker bridges
signals/events to concat_tool.workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6 import QtCore

from concat_tool.workflow import run_video_concat_workflow, WorkflowCallbacks  # type: ignore
from concat_tool.settings import Settings  # type: ignore


class VideoConcatWorker(QtCore.QObject):
    """Background worker to run the video concatenation workflow.

    This worker emits signals to update the GUI without blocking.

    Signals
    -------
    log(str)
        Emitted when there is a new log message.
    phase(str)
        Emitted when the workflow phase changes (e.g., 'scan', 'preconvert').
    progress(int, int)
        Emitted to indicate progress (completed, total) for the current phase.
    finished(int, int)
        Emitted at the end with (success_count, fail_count).
    error(str)
        Emitted when a non-recoverable error occurs.
    results(list)
        Emitted with a list of successful output paths when the workflow completes.
    """

    log = QtCore.Signal(str)
    phase = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    finished = QtCore.Signal(int, int)
    results = QtCore.Signal(list)
    error = QtCore.Signal(str)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings

    def _emit(self, msg: str) -> None:
        """Emit a log message safely.

        Parameters
        ----------
        msg : str
            The message to emit to the GUI log view.
        """
        self.log.emit(msg)

    def _validate(self) -> Optional[str]:
        """Validate the settings.

        Returns
        -------
        Optional[str]
            Error message if validation fails; otherwise None.
        """
        if not self.settings.video_dirs:
            return "请选择至少一个视频目录"
        dirs = [Path(p) for p in self.settings.video_dirs]
        for d in dirs:
            if not d.exists() or not d.is_dir():
                return f"视频目录不存在或不是目录: {d}"
        bgm = Path(self.settings.bgm_path)
        if not bgm.exists():
            return f"BGM路径不存在: {bgm}"
        if self.settings.threads < 1:
            return "线程数必须大于0"
        if self.settings.width <= 0 or self.settings.height <= 0:
            return "width/height 必须为正整数"
        if self.settings.fps <= 0:
            return "fps 必须为正整数"
        if self.settings.output:
            out_spec = Path(self.settings.output)
            if out_spec.suffix.lower() == ".mp4" and len(dirs) > 1:
                return "多目录输入时请提供输出目录（不支持单文件路径）"
        return None

    @QtCore.Slot()
    def run(self) -> None:
        """Run the workflow on the background thread.

        Delegates business logic to concat_tool.workflow.run_video_concat_workflow,
        keeping GUI concerns (signals and stream redirect) isolated.
        """
        try:
            # Redirect prints from workflow to GUI log
            import sys as _sys

            class _StreamRedirect:
                """Redirect sys.stdout/sys.stderr to GUI log.

                Parameters
                ----------
                write_fn : callable
                    Function to call with decoded string chunks.
                """

                def __init__(self, write_fn):
                    self.write_fn = write_fn

                def write(self, s):  # type: ignore[override]
                    try:
                        s = str(s)
                        s = s.replace("\r\n", "\n")
                        for line in s.split("\n"):
                            if line:
                                self.write_fn(line)
                    except Exception:
                        pass

                def flush(self):
                    return

            _orig_out, _orig_err = _sys.stdout, _sys.stderr
            _sys.stdout = _StreamRedirect(self._emit)
            _sys.stderr = _StreamRedirect(self._emit)

            # Bridge callbacks from workflow to GUI signals
            callbacks = WorkflowCallbacks(
                on_log=self._emit,
                on_phase=self.phase.emit,
                on_progress=self.progress.emit,
                on_error=self.error.emit,
            )

            # Execute business workflow
            success_count, fail_count, success_outputs = run_video_concat_workflow(self.settings, callbacks)

            # Emit finished and results back to GUI
            self.finished.emit(success_count, fail_count)
            try:
                self.results.emit(success_outputs)
            except Exception:
                pass
            if success_outputs:
                self._emit("\n🎉 成功生成的文件:")
                for p in success_outputs:
                    try:
                        size_mb = Path(p).stat().st_size / (1024 * 1024)
                        self._emit(f"  - {p} ({size_mb:.1f} MB)")
                    except Exception:
                        self._emit(f"  - {p}")

        except Exception as e:
            self.error.emit(str(e))
        finally:
            # Restore stdout/stderr
            try:
                import sys as _sys2
                _sys2.stdout = _orig_out
                _sys2.stderr = _orig_err
            except Exception:
                pass