"""Shared rounded input styling and soft shadows for application dialogs."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QWidget, QComboBox, QDateEdit, QDateTimeEdit,
                               QDoubleSpinBox, QGraphicsDropShadowEffect)

def chrome_combo_style() -> str:
    return """
    QComboBox {
        background: white;
        color: #1f2937;
        border: 1px solid #d9dee7;
        border-radius: 10px;
        padding: 6px 30px 6px 10px;
        min-height: 18px;
    }

    QComboBox:hover {
        background: #f8fafc;
        border: 1px solid #cfd6e2;
    }

    QComboBox:focus {
        border: 1px solid #60a5fa;
    }

    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border: none;
        margin: 2px 4px 2px 0px;
        border-radius: 8px;
        background: transparent;
    }

    QComboBox::drop-down:hover {
        background: #eef2f7;
    }

    QComboBox::drop-down:pressed {
        background: #e5e7eb;
    }

    QComboBox::down-arrow {
        image: none;
        width: 10px;
        height: 10px;
    }

    QComboBox QAbstractItemView {
        background: white;
        color: #1f2937;
        border: 1px solid #d9dee7;
        border-radius: 10px;
        padding: 4px;
        selection-background-color: #eaf2ff;
        selection-color: #111827;
        outline: 0;
    }
    """

def chrome_dateedit_style() -> str:
    return """
    QDateEdit, QDateTimeEdit {
        background: white;
        color: #1f2937;
        border: 1px solid #d9dee7;
        border-radius: 10px;
        padding: 6px 30px 6px 10px;
        min-height: 18px;
    }

    QDateEdit:hover, QDateTimeEdit:hover {
        background: #f8fafc;
        border: 1px solid #cfd6e2;
    }

    QDateEdit:focus, QDateTimeEdit:focus {
        border: 1px solid #60a5fa;
    }

    QDateEdit::drop-down, QDateTimeEdit::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border: none;
        margin: 2px 4px 2px 0px;
        border-radius: 8px;
        background: transparent;
    }

    QDateEdit::drop-down:hover, QDateTimeEdit::drop-down:hover {
        background: #eef2f7;
    }

    QDateEdit::drop-down:pressed, QDateTimeEdit::drop-down:pressed {
        background: #e5e7eb;
    }

    QDateEdit::down-arrow, QDateTimeEdit::down-arrow {
        image: none;
        width: 10px;
        height: 10px;
    }
    """

def chrome_spinbox_style() -> str:
    return """
    QDoubleSpinBox {
        background: white;
        color: #1f2937;
        border: 1px solid #d9dee7;
        border-radius: 10px;
        padding: 6px 28px 6px 10px;
        min-height: 18px;
    }

    QDoubleSpinBox:hover {
        background: #f8fafc;
        border: 1px solid #cfd6e2;
    }

    QDoubleSpinBox:focus {
        border: 1px solid #60a5fa;
    }

    QDoubleSpinBox::up-button {
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 22px;
        border: none;
        margin: 2px 4px 1px 0px;
        border-top-right-radius: 8px;
        background: transparent;
    }

    QDoubleSpinBox::down-button {
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 22px;
        border: none;
        margin: 1px 4px 2px 0px;
        border-bottom-right-radius: 8px;
        background: transparent;
    }

    QDoubleSpinBox::up-button:hover,
    QDoubleSpinBox::down-button:hover {
        background: #eef2f7;
    }

    QDoubleSpinBox::up-button:pressed,
    QDoubleSpinBox::down-button:pressed {
        background: #e5e7eb;
    }

    QDoubleSpinBox::up-arrow,
    QDoubleSpinBox::down-arrow {
        image: none;
        width: 10px;
        height: 10px;
    }
    """

def apply_chrome_input_styles(root: QWidget):
    for w in root.findChildren(QComboBox):
        w.setStyleSheet(chrome_combo_style())
    date_css = chrome_dateedit_style()
    spin_css = chrome_spinbox_style()

    for w in root.findChildren(QDateEdit):
        w.setStyleSheet(date_css)

    for w in root.findChildren(QDateTimeEdit):
        w.setStyleSheet(date_css)

    for w in root.findChildren(QDoubleSpinBox):
        w.setStyleSheet(spin_css)


def apply_shadow(
    widget: QWidget,
    blur: float = 22.0,
    x_offset: float = 0.0,
    y_offset: float = 4.0,
    color: QColor | None = None,
):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(x_offset, y_offset)
    effect.setColor(color or QColor(15, 23, 42, 45))  # 은은한 회색/남색 그림자
    widget.setGraphicsEffect(effect)
    return effect


def draw_chevron(widget, center_x=None, center_y=None, up=False):
    """Paint the same thin chevron as the main window's menu selectors."""
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor("#4b5563" if widget.isEnabled() else "#9ca3af"), 1.8)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    x = widget.rect().right() - 14 if center_x is None else center_x
    y = widget.rect().center().y() if center_y is None else center_y
    direction = -1 if up else 1
    path = QPainterPath()
    path.moveTo(x - 4, y - 2.5 * direction)
    path.lineTo(x, y + 2.5 * direction)
    path.lineTo(x + 4, y - 2.5 * direction)
    painter.drawPath(path)
    painter.end()


class ChromeComboBox(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        draw_chevron(self)


class ChromeDateEdit(QDateEdit):
    def paintEvent(self, event):
        super().paintEvent(event)
        draw_chevron(self)


class ChromeDoubleSpinBox(QDoubleSpinBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        draw_chevron(self, center_y=self.height() / 4, up=True)
        draw_chevron(self, center_y=3 * self.height() / 4)
