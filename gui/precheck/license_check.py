"""License verification and dialog utilities.

This module isolates license-related logic from preflight checks, including:

- default_license_path: Runtime-aware default path to license.dat
- license_is_ok: Wrapper to validate license and timestamp
- show_license_failure_dialog: UI dialog guiding user to copy machine fingerprint

It is designed to be imported by other modules (e.g., preflight.py) without
introducing circular imports. Minimal runtime path helpers are implemented
locally to locate bundled resources in both development and PyInstaller-frozen
environments.
"""

from __future__ import annotations

import sys
import random
from pathlib import Path

from PySide6 import QtCore, QtWidgets

# Ensure imports work both in development and PyInstaller-frozen runtime.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if not getattr(sys, "frozen", False):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

from gui.crypto_tool import verify_license, machine_code  # type: ignore
from .runtime_paths import resource_path, PROJECT_ROOT




def default_license_path() -> Path:
    """Return the default location of license.dat for current runtime.

    Behavior
    --------
    - PyInstaller (frozen) mode: use the directory of the running .exe
      (Path(sys.executable).parent / 'license.dat').
    - Development (non-frozen) mode: use the project root directory
      (PROJECT_ROOT / 'license.dat').

    Returns
    -------
    Path
        Path to the expected license.dat location (may or may not exist).
    """
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "license.dat"
        return PROJECT_ROOT / "license.dat"
    except Exception:
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
                            ", ".join(sorted(names))[:2000]
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
            "未获得授权<br><br>"
            "请点击按钮生成<font color='red'>【专属口令】</font>并发送给管理员 <br>"
            + qr_html + debug_html + "<br>" +
            "微信扫码添加管理员"
        )

        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle("授权校验失败")
        dialog.setWindowModality(QtCore.Qt.ApplicationModal)
        dialog.setWindowFlags(
            dialog.windowFlags()
            | QtCore.Qt.WindowStaysOnTopHint
        )
        dialog.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, True)
        dialog.setWindowFlag(QtCore.Qt.WindowMinimizeButtonHint, False)
        dialog.setWindowFlag(QtCore.Qt.WindowMaximizeButtonHint, False)
        dialog.activateWindow()
        dialog.raise_()

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

        vbox = QtWidgets.QVBoxLayout(dialog)
        label = QtWidgets.QLabel()
        label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setText(msg_html)
        vbox.addWidget(label)

        copy_btn = QtWidgets.QPushButton("生成【专属口令】")
        vbox.addWidget(copy_btn)

        def _on_copy_clicked() -> None:
            copy_btn.setEnabled(False)
            try:
                fp = machine_code.get_stable_hardware_id()
                if not fp:
                    QtWidgets.QMessageBox(
                        QtWidgets.QMessageBox.Critical,
                        "生成失败",
                        "生成【专属口令】失败，请联系管理员。",
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
                    "【专属口令】已复制到剪贴板。<br>请添加管理员好友，并粘贴【专属口令】发给 Ta",
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
                    "生成失败",
                    f"生成【专属口令】失败：{e}",
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


__all__ = [
    "default_license_path",
    "license_is_ok",
    "show_license_failure_dialog",
]