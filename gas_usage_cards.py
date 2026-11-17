"""Compact per-Tube cards matching the monthly plant overview."""

from qfluentwidgets import CardWidget
from PySide6.QtGui import QColor

import re
import pandas as pd
from ui_theme import apply_shadow
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout


def volume_text(value):
    return "— ft³" if pd.isna(value) else f"{value:,.6f} ft³"


class TubeUsageCard(CardWidget):
    def __init__(self, tube, channels, parent=None):
        super().__init__(parent)
        self.setObjectName("tubeUsageCard")
        self.setBorderRadius(8)
        apply_shadow(self, blur=12, y_offset=2, color=QColor(0, 0, 0, 18))
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(5)
        header = QHBoxLayout()
        title = QLabel(re.sub(r"^TUBE", "TUBE ", tube))
        title.setStyleSheet("font-size:16px; font-weight:700; color:#243f54;")
        header.addWidget(title)
        self.state = QLabel("기록 없음")
        self.state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.state.setStyleSheet("font-size:10px; color:#7c8996;")
        header.addWidget(self.state)
        root.addLayout(header)
        rows = QGridLayout()
        rows.setHorizontalSpacing(18)
        rows.setVerticalSpacing(1)
        rows.setColumnStretch(0, 1)
        self.values = {}
        for index, name in enumerate(channels):
            label = QLabel(re.sub(r"\s*\(PV\)\s*$", "", name, flags=re.IGNORECASE))
            label.setToolTip(name)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setStyleSheet("font-size:12px; font-weight:600;")
            value = QLabel("— ft³")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setStyleSheet("font-size:12px;")
            rows.addWidget(label, index, 0)
            rows.addWidget(value, index, 1)
            self.values[name] = value
        self.totals = {}
        for index, (gas, color) in enumerate((("N2", "#0f6cbd"), ("O2", "#11877d")), start=len(channels)):
            label, value = QLabel(f"{gas} TOTAL"), QLabel("— ft³")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            for item in (label, value):
                item.setStyleSheet(f"font-size:12px; font-weight:700; color:{color};")
            rows.addWidget(label, index, 0)
            rows.addWidget(value, index, 1)
            self.totals[gas] = value
        root.addLayout(rows)

    def set_usage(self, channels, totals, status):
        for name, label in self.values.items():
            label.setText(volume_text(channels.get(name, float("nan"))))
        for gas, label in self.totals.items():
            label.setText(volume_text(totals.get(gas, float("nan"))))
        self.state.setText(status)
