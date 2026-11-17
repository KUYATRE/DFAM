"""Shared Fluent input styling and restrained card shadows."""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QGraphicsDropShadowEffect
from fluent_ui import polish_inputs, FluentComboBox as ChromeComboBox, FluentDateEdit as ChromeDateEdit
from qfluentwidgets import DoubleSpinBox as ChromeDoubleSpinBox


def apply_chrome_input_styles(root: QWidget):
    """Compatibility entry point for the existing dialogs."""
    polish_inputs(root)


def apply_shadow(widget: QWidget, blur=14.0, x_offset=0.0, y_offset=2.0, color=None):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(x_offset, y_offset)
    effect.setColor(color or QColor(0, 0, 0, 24))
    widget.setGraphicsEffect(effect)
    return effect
