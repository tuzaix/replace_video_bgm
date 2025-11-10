from __future__ import annotations

from typing import Optional
from PySide6 import QtCore, QtWidgets, QtGui
from . import theme


class SpinnerIndicator(QtWidgets.QWidget):
    """
    A lightweight, timer-driven spinner indicator widget.

    Parameters
    ----------
    parent : Optional[QtWidgets.QWidget]
        Parent widget.
    size : int
        Diameter of the spinner in pixels.
    color : Optional[QtGui.QColor]
        Color for the spinner lines. Defaults to QtGui.QColor(0, 122, 204).
    """
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, size: int = 64, color: QtGui.QColor | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._angle = 0
        # Default spinner color uses theme primary blue
        self._color = color or QtGui.QColor(theme.PRIMARY_BLUE)
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(80)
        try:
            self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        except Exception:
            pass
        self.setFixedSize(size, size)

    def _tick(self) -> None:
        """Advance the spinner angle and schedule a repaint."""
        self._angle = (self._angle + 1) % 12
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        """Custom painting of the spinner with 12 lines fading by angle."""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        try:
            rect = self.rect()
            cx, cy = rect.center().x(), rect.center().y()
            radius = min(rect.width(), rect.height()) // 2 - 4
            line_len = max(6, radius // 3)
            line_w = max(2, radius // 8)
            for i in range(12):
                # alpha decreases with relative angle
                rel = (i - self._angle) % 12
                alpha = int(60 + (195 * (1 - rel / 12.0)))  # 60..255
                color = QtGui.QColor(self._color)
                color.setAlpha(alpha)
                pen = QtGui.QPen(color)
                pen.setWidth(line_w)
                try:
                    pen.setCapStyle(QtCore.Qt.RoundCap)
                except Exception:
                    pass
                painter.setPen(pen)
                from math import cos, sin, pi
                theta = (i / 12.0) * 2.0 * pi
                sx = cx + (radius - line_len) * cos(theta)
                sy = cy + (radius - line_len) * sin(theta)
                ex = cx + radius * cos(theta)
                ey = cy + radius * sin(theta)
                painter.drawLine(QtCore.QPointF(sx, sy), QtCore.QPointF(ex, ey))
        finally:
            try:
                painter.end()
            except Exception:
                pass


class BusyOverlay(QtWidgets.QWidget):
    """
    A semi-transparent overlay that centers a spinner and a hint text.

    Behavior
    --------
    - Intercepts parent resize/move to adjust geometry
    - No scroll bars and no border; purely visual overlay
    - Hidden by default
    """
    def __init__(
        self,
        parent: QtWidgets.QWidget,
        spinner_color: QtGui.QColor | None = None,
        backdrop_rgba: str | None = None,
        label_text: str = "处理中…",
        label_color: str = theme.OVERLAY_LABEL_COLOR,
        label_font_px: int = theme.OVERLAY_LABEL_FONT_PX,
        label_weight: int = theme.OVERLAY_LABEL_WEIGHT,
    ) -> None:
        """
        Initialize the BusyOverlay with optional theming parameters.

        Parameters
        ----------
        parent : QtWidgets.QWidget
            The widget to cover with the overlay.
        spinner_color : Optional[QtGui.QColor]
            Color of the spinner indicator lines. Defaults to blue.
        backdrop_rgba : Optional[str]
            Background color in CSS rgba() format. Defaults to semi-transparent black.
        label_text : str
            Text to display below the spinner.
        label_color : str
            CSS color for the label text.
        label_font_px : int
            Label font size in pixels.
        label_weight : int
            Label font weight.
        """
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        # Apply themed backdrop by default
        self.setStyleSheet(f"background-color: {backdrop_rgba or theme.OVERLAY_BACKDROP};")
        self.setVisible(False)
        # Opacity effect for fade animation
        self._opacity_effect = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(0.0)
        self._fade_anim = QtCore.QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_anim.setEasingCurve(QtCore.QEasingCurve.InOutQuad)
        vbox = QtWidgets.QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)
        vbox.setAlignment(QtCore.Qt.AlignCenter)
        self.spinner = SpinnerIndicator(self, size=64, color=spinner_color)
        self.label = QtWidgets.QLabel(label_text)
        self.label.setStyleSheet(
            f"color: {label_color}; font-size: {label_font_px}px; font-weight: {label_weight};"
        )
        vbox.addWidget(self.spinner, 0, QtCore.Qt.AlignCenter)
        vbox.addWidget(self.label, 0, QtCore.Qt.AlignCenter)
        parent.installEventFilter(self)

    def show_with_fade(self, duration_ms: int = 200) -> None:
        """
        Show the overlay with a fade-in animation.

        Parameters
        ----------
        duration_ms : int
            Duration of the fade-in in milliseconds.
        """
        try:
            parent_widget = self.parentWidget()
            if parent_widget is not None:
                self.setGeometry(parent_widget.rect())
        except Exception:
            pass
        try:
            self._fade_anim.stop()
            self._opacity_effect.setOpacity(0.0)
            self.setVisible(True)
            self.raise_()
            self._fade_anim.setDuration(max(80, duration_ms))
            self._fade_anim.setStartValue(0.0)
            self._fade_anim.setEndValue(1.0)
            self._fade_anim.start()
        except Exception:
            # Fallback to immediate show
            try:
                self.show()
                self.raise_()
            except Exception:
                pass

    def hide_with_fade(self, duration_ms: int = 200) -> None:
        """
        Hide the overlay with a fade-out animation.

        Parameters
        ----------
        duration_ms : int
            Duration of the fade-out in milliseconds.
        """
        try:
            self._fade_anim.stop()
            self._fade_anim.setDuration(max(80, duration_ms))
            self._fade_anim.setStartValue(self._opacity_effect.opacity())
            self._fade_anim.setEndValue(0.0)
            def _on_finished() -> None:
                try:
                    self.setVisible(False)
                except Exception:
                    pass
            self._fade_anim.finished.connect(_on_finished)
            self._fade_anim.start()
        except Exception:
            # Fallback to immediate hide
            try:
                self.hide()
            except Exception:
                pass

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        """Track parent geometry changes and fill its rect."""
        if watched is self.parent() and event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Move):
            try:
                parent_widget = self.parentWidget()
                if parent_widget is not None:
                    self.setGeometry(parent_widget.rect())
            except Exception:
                pass
        return super().eventFilter(watched, event)