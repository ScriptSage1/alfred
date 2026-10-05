"""The command palette window: a text box that appears, takes a request,
shows progress, and shows the answer.

The palette knows nothing about agents, tasks or Obsidian. It emits
`submitted(text)` and displays whatever steps and replies it is given; the
controller in alfred.ui.app connects it to the agent.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QKeySequence, QLinearGradient, QPainter, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

WIDTH = 680
SHADOW_MARGIN = 28  # transparent space around the card, so the shadow has room
GOLD = "#d4af6a"    # the accent colour from Alfred's logo

STYLE = f"""
#card {{
    background: rgba(22, 22, 24, 245);
    border: 1px solid rgba(255, 255, 255, 22);
    border-radius: 16px;
}}
QLineEdit {{
    background: transparent;
    border: none;
    color: #f4f4f5;
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 19px;
    selection-background-color: rgba(212, 175, 106, 70);
    selection-color: #f4f4f5;
}}
QLineEdit:read-only {{ color: #a1a1aa; }}
#separator {{ background: rgba(255, 255, 255, 18); }}
#steps {{ color: #a1a1aa; font-family: "Segoe UI", sans-serif; font-size: 13px; }}
#reply {{ color: #f4f4f5; font-family: "Segoe UI", sans-serif; font-size: 15px; }}
#hint {{ color: #71717a; font-family: "Segoe UI", sans-serif; font-size: 11px; }}
"""


@dataclass
class _Step:
    state: str  # "running", "done" or "failed"
    text: str
    tool: str | None


class ActivityBar(QWidget):
    """A thin animated gold line that sweeps across the top while Alfred works."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(2)
        self._phase = 0.0
        self._animation = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=1100)
        self._animation.setLoopCount(-1)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._animation.valueChanged.connect(self._set_phase)

    def start(self) -> None:
        self._animation.start()

    def stop(self) -> None:
        self._animation.stop()
        self._phase = 0.0
        self.update()

    @property
    def running(self) -> bool:
        return self._animation.state() == QVariantAnimation.State.Running

    def _set_phase(self, value: float) -> None:
        self._phase = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if not self.running:
            return
        width = self.width()
        segment = width * 0.35
        x = -segment + (width + segment) * self._phase
        gradient = QLinearGradient(x, 0, x + segment, 0)
        gradient.setColorAt(0.0, QColor(212, 175, 106, 0))
        gradient.setColorAt(0.5, QColor(212, 175, 106, 255))
        gradient.setColorAt(1.0, QColor(212, 175, 106, 0))
        painter = QPainter(self)
        painter.fillRect(self.rect(), gradient)


class CommandPalette(QWidget):
    submitted = Signal(str)
    shown = Signal()  # emitted each time the palette opens

    def __init__(self, icon: QIcon | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,  # no taskbar button
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Alfred")
        self.setStyleSheet(STYLE)
        self.setFixedWidth(WIDTH)

        self._busy = False
        self._steps: list[_Step] = []
        self._animation: QParallelAnimationGroup | None = None

        card = self._card = QFrame(objectName="card")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(48)
        shadow.setOffset(QPointF(0, 12))
        shadow.setColor(QColor(0, 0, 0, 150))
        card.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)
        outer.addWidget(card)

        self._activity = ActivityBar()
        self._input = QLineEdit(placeholderText="Ask Alfred…   e.g. “Remind me to call mom tomorrow”")
        self._input.returnPressed.connect(self._submit)

        logo = QLabel()
        if icon is not None:
            logo.setPixmap(icon.pixmap(30, 30))

        row = QHBoxLayout()
        row.setContentsMargins(18, 14, 18, 14)
        row.setSpacing(14)
        row.addWidget(logo)
        row.addWidget(self._input, 1)

        self._separator = QFrame(objectName="separator")
        self._separator.setFixedHeight(1)
        self._steps_label = QLabel(objectName="steps", textFormat=Qt.TextFormat.RichText, wordWrap=True)
        self._reply_label = QLabel(objectName="reply", wordWrap=True)
        self._reply_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._hint = QLabel("Enter to send  ·  Esc to close", objectName="hint")

        # Everything below the input lives in one container, hidden when empty,
        # so an idle palette is just the input row.
        self._details = QWidget()
        details = QVBoxLayout(self._details)
        details.setContentsMargins(20, 12, 20, 14)
        details.setSpacing(8)
        details.addWidget(self._steps_label)
        details.addWidget(self._reply_label)
        details.addWidget(self._hint, 0, Qt.AlignmentFlag.AlignRight)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._activity)
        layout.addLayout(row)
        layout.addWidget(self._separator)
        layout.addWidget(self._details)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.hide_palette)
        self._render()

    # --- Showing and hiding ------------------------------------------------------

    def toggle(self) -> None:
        if self.isVisible() and self.isActiveWindow():
            self.hide_palette()
        else:
            self.show_palette()

    def show_palette(self) -> None:
        """Fade and slide in near the top of the screen the mouse is on."""
        self.adjustSize()
        target = self._home_position()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.move(target - QPoint(0, 14))
            self.show()
        self._animate(opacity=1.0, position=target, duration=190, curve=QEasingCurve.Type.OutCubic)
        self._take_focus()
        self.shown.emit()

    def _take_focus(self) -> None:
        """Make the palette the active window, with the cursor in the text box."""
        self.raise_()
        self.activateWindow()
        if QGuiApplication.platformName() == "windows":
            from alfred.ui.win32 import bring_to_front

            bring_to_front(int(self.winId()))
        self._input.setFocus()
        self._input.selectAll()

    def hide_palette(self) -> None:
        """Fade out. Work in progress carries on; its answer is kept for next time."""
        if not self.isVisible():
            return
        self._animate(
            opacity=0.0,
            position=self.pos() - QPoint(0, 8),
            duration=130,
            curve=QEasingCurve.Type.InCubic,
            then=self.hide,
        )

    def _home_position(self) -> QPoint:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        return QPoint(area.center().x() - self.width() // 2, area.top() + int(area.height() * 0.18))

    def _animate(self, *, opacity, position, duration, curve, then=None) -> None:
        if self._animation is not None:
            self._animation.stop()
        group = QParallelAnimationGroup(self)
        for prop, end in ((b"windowOpacity", opacity), (b"pos", position)):
            animation = QPropertyAnimation(self, prop, duration=duration, endValue=end)
            animation.setEasingCurve(curve)
            group.addAnimation(animation)
        if then is not None:
            group.finished.connect(then)
        self._animation = group
        group.start()

    # --- Progress and results (called by the controller) ------------------------

    @property
    def busy(self) -> bool:
        return self._busy

    def begin_request(self) -> None:
        self._steps = [_Step("running", "Thinking…", None)]
        self._reply_label.clear()
        self._set_busy(True)
        self._render()

    def add_step(self, kind: str, message: str, tool: str | None = None) -> None:
        """Show agent progress: kind is 'tool_started', 'tool_finished' or 'tool_failed'."""
        self._steps = [s for s in self._steps if s.tool is not None]  # drop "Thinking…"
        if kind == "tool_started":
            self._steps.append(_Step("running", message, tool))
        elif kind in ("tool_finished", "tool_failed"):
            state = "done" if kind == "tool_finished" else "failed"
            for step in reversed(self._steps):
                if step.tool == tool and step.state == "running":
                    step.state, step.text = state, message
                    break
            else:
                self._steps.append(_Step(state, message, tool))
        self._render()

    def show_reply(self, text: str) -> None:
        self._finish(text, color="")

    def show_error(self, text: str) -> None:
        self._finish(text, color="color: #f87171;")

    def _finish(self, text: str, *, color: str) -> None:
        self._steps = [s for s in self._steps if s.tool is not None]
        self._reply_label.setStyleSheet(color)
        self._reply_label.setText(text)
        self._set_busy(False)
        self._render()
        if self.isVisible():
            # Another app (e.g. Obsidian, just started by a tool) may have taken
            # focus meanwhile; take it back so Esc and typing reach the palette.
            self._take_focus()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._input.setReadOnly(busy)
        if busy:
            self._activity.start()
        else:
            self._activity.stop()
            self._input.selectAll()  # typing replaces the old request

    def _render(self) -> None:
        icons = {"running": f'<span style="color:{GOLD}">●</span>',
                 "done": f'<span style="color:{GOLD}">✓</span>',
                 "failed": '<span style="color:#f87171">✕</span>'}
        lines = [f"{icons[s.state]}&nbsp;&nbsp;{html.escape(s.text)}" for s in self._steps]
        self._steps_label.setText("<br>".join(lines))
        self._steps_label.setVisible(bool(lines))
        self._reply_label.setVisible(bool(self._reply_label.text()))
        has_details = bool(lines) or bool(self._reply_label.text())
        self._separator.setVisible(has_details)
        self._details.setVisible(has_details)
        self._fit_height()

    def _fit_height(self) -> None:
        """Grow or shrink smoothly to fit the content."""
        # Qt recalculates layouts lazily, and not at all while the window is
        # hidden; force it now so the size hint reflects what was just shown.
        for widget in (self._details, self._card, self):
            widget.layout().invalidate()
            widget.layout().activate()
        if not self.isVisible():
            self.resize(self.width(), self.sizeHint().height())
            return
        height = self.sizeHint().height()
        if height == self.height():
            return
        animation = QPropertyAnimation(self, b"geometry", self, duration=160)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setEndValue(QRect(self.x(), self.y(), self.width(), height))
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _submit(self) -> None:
        text = self._input.text().strip()
        if text and not self._busy:
            self.submitted.emit(text)
