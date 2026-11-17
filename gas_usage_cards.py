"""Compact per-Tube cards matching the monthly plant overview."""

import re
import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout


def volume_text(value):
    return "— ft³" if pd.isna(value) else f"{value:,.6f} ft³"


class TubeUsageCard(QFrame):
    def __init__(self, tube, channels, parent=None):
        super().__init__(parent)
        self.setObjectName("tubeUsageCard")
        self.setStyleSheet("""
            QFrame#tubeUsageCard {background:white; border:1px solid #aebbc6; border-radius:2px;}
            QFrame#tubeUsageCard QLabel {background:transparent; border:0; padding:0; color:#172b3c;}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 9, 14, 10)
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
        rows.setVerticalSpacing(2)
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
        for index, (gas, color) in enumerate((("N2", "#163ea1"), ("O2", "#167035")), start=len(channels)):
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
