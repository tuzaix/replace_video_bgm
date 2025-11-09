"""Preflight checks module for GUI startup.

This module abstracts the startup runtime checks and helper functions
previously defined in main_gui.py. It provides:

- detect_nvidia_gpu: Detect presence of NVIDIA GPU
- runtime_base_dir/resource_path: Runtime-aware resource location helpers
- default_license_path/license_is_ok: License file path inference and check
- show_no_nvidia_dialog/show_license_failure_dialog: User prompts
- run_preflight_checks: Orchestrate the above checks

All functions include docstrings and are designed to work in both
development and PyInstaller-frozen runtime.
"""

from __future__ import annotations

import sys
import random
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtWidgets

# Ensure imports work both in development and PyInstaller-frozen runtime.
# In frozen mode, bundled packages are available without modifying sys.path.
# In development mode, add project root so `crypto_tool` can be imported.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if not getattr(sys, "frozen", False):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

from gui.crypto_tool import verify_license, machine_code  # type: ignore
from .gpu_detect import detect_nvidia_gpu


# GPU 检测逻辑已抽出至 gpu_detect 模块。


def runtime_base_dir() -> Path:
    """Return base directory for resource lookup depending on runtime.

    - Frozen (PyInstaller onefile/onedir): use sys._MEIPASS as base.
    - Development (non-frozen): use project root (PROJECT_ROOT).

    Returns
    -------
    Path
        The directory from which bundled resources should be read.
    """
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(getattr(sys, "_MEIPASS")).resolve()
    except Exception:
        pass
    return PROJECT_ROOT


def resource_path(*parts: str) -> Path:
    """Compose a resource path that works in both dev and frozen runtime.

    Parameters
    ----------
    parts : str
        Path components under the base directory. For example:
        resource_path("gui", "wechat", "admin1.png").

    Returns
    -------
    Path
        The resolved path to the resource.
    """
    return runtime_base_dir().joinpath(*parts)


def default_license_path() -> Path:
    """Return the default location of license.dat for current runtime.

    Behavior
    --------
    - PyInstaller (frozen) mode: use the directory of the running .exe
      (Path(sys.executable).parent / 'license.dat'). This matches the
      typical deployment where the license file sits next to the executable.
    - Development (non-frozen) mode: use the project root directory
      (PROJECT_ROOT / 'license.dat'). This matches the repository layout
      seen during local development.

    Returns
    -------
    Path
        Path to the expected license.dat location (may or may not exist).
    """
    try:
        if getattr(sys, "frozen", False):
            # Running from a PyInstaller-built executable
            return Path(sys.executable).resolve().parent / "license.dat"
        # Running from source
        return PROJECT_ROOT / "license.dat"
    except Exception:
        # Fallback to argv path if anything goes wrong
        return Path(sys.argv[0]).resolve().parent / "license.dat"


def license_is_ok() -> bool:
    """Check whether license is valid and acceptable for startup.

    Runtime-aware defaults:
    - Frozen (PyInstaller) exe: license.dat and last_run.dat next to the exe
    - Non-frozen dev run: files under project root

    Returns
    -------
    bool
        True if license verification succeeds; False otherwise.
    """
    try:
        lic_path = default_license_path()
        # Place timestamp file alongside license for consistency
        if getattr(sys, "frozen", False):
            ts_file = Path(sys.executable).resolve().parent / "last_run.dat"
        else:
            ts_file = PROJECT_ROOT / "last_run.dat"
        ok = verify_license.verify_license(
            license_path=lic_path,
            timestamp_file=ts_file,
        )
        return bool(ok)
    except Exception:
        return False


def show_no_nvidia_dialog(app: QtWidgets.QApplication) -> None:
    """Show a blocking dialog informing no NVIDIA GPU was detected and quit.

    Parameters
    ----------
    app : QtWidgets.QApplication
        The Qt application instance to quit after user acknowledges.
    """
    try:
        msg = (
            "该程序是使用navida显卡来处理视频，请升级显卡，cpu渲染效率太低"
        )
        QtWidgets.QMessageBox.critical(
            None,
            "硬件要求",
            msg,
            QtWidgets.QMessageBox.StandardButton.Ok,
        )
    except Exception:
        print("[启动检查] 未检测到NVIDIA显卡：该程序是使用navida显卡来处理视频，请升级显卡，cpu渲染效率太低")
    try:
        app.quit()
    except Exception:
        pass


def show_license_failure_dialog(app: QtWidgets.QApplication) -> None:
    """Show the authorization failure dialog and quit the application.

    The dialog provides a button to copy the machine fingerprint and shows a
    QR image if available, then exits the application after interaction.

    Parameters
    ----------
    app : QtWidgets.QApplication
        The Qt application instance.
    """
    try:
        # 构造富文本消息并随机挑选二维码图片
        # In PyInstaller frozen runtime, resources are under sys._MEIPASS
        # because we added them via --add-data "gui\wechat;gui\wechat".
        wechat_dir = resource_path("gui", "wechat")
        qr_candidates = []
        try:
            if wechat_dir.exists():
                for p in wechat_dir.iterdir():
                    if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".gif"}:
                        qr_candidates.append(p)
        except Exception:
            pass
        qr_html = ""
        debug_html = ""
        if qr_candidates:
            qr_path = random.choice(qr_candidates)
            qr_html = f"<div style='margin:8px; text-align:center;'>\n<img src='file:///{qr_path.as_posix()}' style='max-width:160px;'/>\n</div>"
        else:
            # 当没有找到任何图片时，在弹窗中加入诊断信息：显示目录路径和现有文件列表
            try:
                from html import escape as _html_escape
                wdir_str = str(wechat_dir.resolve())
                if not wechat_dir.exists():
                    debug_html = (
                        "<div style='margin:8px; color:#666; font-size:12px;'>"
                        f"诊断：未找到任何二维码图片。资源目录不存在：{_html_escape(wdir_str)}"
                        "</div>"
                    )
                else:
                    names: list[str] = []
                    for p in wechat_dir.iterdir():
                        try:
                            names.append(p.name)
                        except Exception:
                            pass
                    if names:
                        list_html = _html_escape(
                            ", ".join(sorted(names))[:2000]  # 限制长度，避免超长输出
                        )
                    else:
                        list_html = "(空目录)"
                    debug_html = (
                        "<div style='margin:8px; color:#666; font-size:12px;'>"
                        f"诊断：未找到任何二维码图片。资源目录：{_html_escape(wdir_str)}；目录中文件：{list_html}"
                        "</div>"
                    )
            except Exception:
                pass

        msg_html = (
            "未获得授权，程序将退出。<br><br>"
            "请点击下方按钮复制机器指纹并发送给管理员 <br>"
            + qr_html + debug_html + "<br>" +
            "微信添加下面管理员"
        )

        # 使用自定义 QDialog，确保点击“复制机器指纹”后第一弹窗不立即关闭，
        # 等第二个确认弹窗点击 OK 后同时退出两个窗口。
        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle("授权校验失败")
        # 应用级模态，阻止与应用内其他窗口交互
        dialog.setWindowModality(QtCore.Qt.ApplicationModal)
        # 置顶显示，避免被其他窗口遮挡；允许右上角关闭按钮可用
        dialog.setWindowFlags(
            dialog.windowFlags()
            | QtCore.Qt.WindowStaysOnTopHint
        )
        # 显示关闭按钮，用户可通过右上角关闭弹窗
        dialog.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, True)
        dialog.setWindowFlag(QtCore.Qt.WindowMinimizeButtonHint, False)
        dialog.setWindowFlag(QtCore.Qt.WindowMaximizeButtonHint, False)
        # 强制获取焦点
        dialog.activateWindow()
        dialog.raise_()
        # 屏蔽 ESC（保留右上角关闭按钮可用）
        class _BlockCloseEsc(QtCore.QObject):
            def eventFilter(self, obj, event):  # type: ignore[override]
                et = event.type()
                if et == QtCore.QEvent.KeyPress:
                    try:
                        if event.key() == QtCore.Qt.Key_Escape:
                            return True
                    except Exception:
                        pass
                return False

        _guard = _BlockCloseEsc(dialog)
        dialog.installEventFilter(_guard)

        # 首次显示弹窗时不改变鼠标指针；仅在点击“复制机器指纹”期间显示忙碌光标
        vbox = QtWidgets.QVBoxLayout(dialog)
        label = QtWidgets.QLabel()
        label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setText(msg_html)
        vbox.addWidget(label)

        copy_btn = QtWidgets.QPushButton("复制机器指纹")
        vbox.addWidget(copy_btn)

        def _on_copy_clicked() -> None:
            # 点击后立刻禁用按钮，防止重复点击
            copy_btn.setEnabled(False)
            try:
                fp = machine_code.get_stable_hardware_id()
                if not fp:
                    QtWidgets.QMessageBox(
                        QtWidgets.QMessageBox.Critical,
                        "获取失败",
                        "获取机器指纹失败，请联系管理员。",
                        QtWidgets.QMessageBox.StandardButton.Ok,
                        dialog,
                    ).exec()
                    app.quit()
                    return

                QtWidgets.QApplication.clipboard().setText(fp)
                try:
                    QtWidgets.QApplication.restoreOverrideCursor()
                except Exception:
                    pass
                info_box = QtWidgets.QMessageBox(
                    QtWidgets.QMessageBox.Information,
                    "已复制",
                    "机器指纹已复制到剪贴板。<br>请添加管理员好友，并粘贴指纹发给 Ta",
                    QtWidgets.QMessageBox.StandardButton.Ok,
                    dialog,
                )
                info_box.setTextFormat(QtCore.Qt.TextFormat.RichText)
                info_box.exec()
                dialog.accept()
            except Exception as e:
                try:
                    QtWidgets.QApplication.restoreOverrideCursor()
                except Exception:
                    pass
                warn_box = QtWidgets.QMessageBox(
                    QtWidgets.QMessageBox.Warning,
                    "复制失败",
                    f"复制机器指纹失败：{e}",
                    QtWidgets.QMessageBox.StandardButton.Ok,
                    dialog,
                )
                warn_box.exec()
                copy_btn.setEnabled(True)

        copy_btn.clicked.connect(_on_copy_clicked)
        dialog.exec()
        try:
            QtWidgets.QApplication.restoreOverrideCursor()
        except Exception:
            pass
    except Exception:
        # 控制台回退
        try:
            fp = machine_code.get_stable_hardware_id()
            if not fp:
                print("[授权失败] 未找到 license.dat。")
                app.quit()
                return
            print(f"[授权失败] 未找到 license.dat。机器指纹：{fp}")
        except Exception:
            print("[授权失败] 未找到 license.dat。")
    try:
        app.quit()
    except Exception:
        pass


def run_preflight_checks(app: QtWidgets.QApplication) -> bool:
    """Run startup preflight checks: GPU requirement and license check.

    This function orchestrates two independent checks and their UI prompts:
    1) NVIDIA GPU presence. If missing, show a blocking dialog and quit.
    2) License/authorization check. If it fails, show a dialog with a
       "copy machine fingerprint" helper and quit.

    Parameters
    ----------
    app : QtWidgets.QApplication
        The Qt application instance.

    Returns
    -------
    bool
        True to continue launching; False to terminate the app.
    """
    # 1) NVIDIA GPU check
    try:
        has_nv = detect_nvidia_gpu()
    except Exception:
        has_nv = False
    if not has_nv:
        show_no_nvidia_dialog(app)
        return False

    # 2) License/authorization check
    try:
        lic_ok = license_is_ok()
    except Exception:
        lic_ok = False
    if not lic_ok:
        show_license_failure_dialog(app)
        return False

    return True


__all__ = [
    "detect_nvidia_gpu",
    "runtime_base_dir",
    "resource_path",
    "default_license_path",
    "license_is_ok",
    "show_no_nvidia_dialog",
    "show_license_failure_dialog",
    "run_preflight_checks",
]