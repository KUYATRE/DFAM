import sys
from pathlib import Path
import re
from dataclasses import dataclass

import pandas as pd

from PySide6.QtGui import (
    QIcon, QAction, QPainter, QColor, QPen, QCursor, QBrush, QPainterPath
)
from PySide6.QtCore import (
    Qt, QDir, QDateTime, QPoint, QPointF, QRect, QRectF,
    QMargins, QTimer, Signal, QSize, QDate, QSortFilterProxyModel
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QTreeView, QFileSystemModel, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QStackedWidget,
    QDateTimeEdit, QDoubleSpinBox, QMessageBox, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QListWidget, QListWidgetItem,
    QAbstractItemView, QGridLayout, QRubberBand,
    QSizePolicy, QLineEdit, QFileDialog, QDateEdit, QMenu,
    QGraphicsLineItem, QGraphicsRectItem, QGraphicsEllipseItem,
    QProxyStyle, QStyle, QGraphicsDropShadowEffect,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCharts import (
    QChart, QChartView, QLineSeries, QScatterSeries,
    QValueAxis, QDateTimeAxis,
    QBarSeries, QStackedBarSeries, QBarSet, QBarCategoryAxis
)

import ctypes
from ctypes import wintypes

# -----------------------------
# logger fallback
# -----------------------------
try:
    from utils.logger import logger  # type: ignore
except Exception:
    class _Dummy:
        def info(self, *a, **k): print(*a)
        def debug(self, *a, **k): print(*a)
        def warning(self, *a, **k): print(*a)
        def error(self, *a, **k): print(*a)
        def exception(self, *a, **k): print(*a)
    logger = _Dummy()

NONE_ITEM = "(None)"


@dataclass(frozen=True)
class LogMeta:
    path: Path
    tube: str
    recipe: str
    job_id: str
    date_str: str   # YYYYMMDD
    time_str: str   # HHMMSS or HHMM
    dt: pd.Timestamp | None


DATE_RE8 = re.compile(r"^\d{8}$")
TIME_RE4_6 = re.compile(r"^\d{4}(\d{2})?$")


@dataclass(frozen=True)
class HistoryLogMeta:
    path: Path
    tube: str
    recipe_from_filename: str
    recipe_name: str
    job_id: str
    date_str: str
    time_str: str
    dt: pd.Timestamp | None
    is_abort: bool


class DateRangeDialog(QDialog):
    """History용 날짜 범위 선택 다이얼로그"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Date Range")

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")

        form = QFormLayout()
        form.addRow("From", self.date_from)
        form.addRow("To", self.date_to)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(btns)

        apply_chrome_input_styles(self)

    def set_values(self, d0: QDate, d1: QDate):
        self.date_from.setDate(d0)
        self.date_to.setDate(d1)

    def values(self):
        return self.date_from.date(), self.date_to.date()


class AppSettingsDialog(QDialog):
    def __init__(self, root_dir: str = "", alarm_dir: str = "", history_dir: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")

        self.root_edit = QLineEdit(root_dir)
        self.alarm_edit = QLineEdit(alarm_dir)
        self.history_edit = QLineEdit(history_dir)

        self.root_btn = QPushButton("Browse...")
        self.alarm_btn = QPushButton("Browse...")
        self.history_btn = QPushButton("Browse...")

        self.root_btn.clicked.connect(lambda: self._pick_dir(self.root_edit))
        self.alarm_btn.clicked.connect(lambda: self._pick_dir(self.alarm_edit))
        self.history_btn.clicked.connect(lambda: self._pick_dir(self.history_edit))

        form = QFormLayout()

        row1 = QHBoxLayout()
        row1.addWidget(self.root_edit, 1)
        row1.addWidget(self.root_btn, 0)
        form.addRow("Root dir", row1)

        row2 = QHBoxLayout()
        row2.addWidget(self.alarm_edit, 1)
        row2.addWidget(self.alarm_btn, 0)
        form.addRow("Alarm dir", row2)

        row3 = QHBoxLayout()
        row3.addWidget(self.history_edit, 1)
        row3.addWidget(self.history_btn, 0)
        form.addRow("History dir", row3)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)

        apply_chrome_input_styles(self)

    def _pick_dir(self, edit: QLineEdit):
        start = edit.text().strip() or str(Path.cwd())
        path = QFileDialog.getExistingDirectory(self, "Select directory", start)
        if path:
            edit.setText(path)

    def _on_accept(self):
        root_dir = self.root_edit.text().strip()
        alarm_dir = self.alarm_edit.text().strip()
        history_dir = self.history_edit.text().strip()

        if not root_dir:
            QMessageBox.warning(self, "Settings", "root_dir를 입력해줘.")
            return
        if not Path(root_dir).exists():
            QMessageBox.warning(self, "Settings", "root_dir 경로가 존재하지 않아.")
            return

        if alarm_dir and not Path(alarm_dir).exists():
            QMessageBox.warning(self, "Settings", "alarm_dir 경로가 존재하지 않아.")
            return

        if history_dir and not Path(history_dir).exists():
            QMessageBox.warning(self, "Settings", "history_dir 경로가 존재하지 않아.")
            return

        self.accept()

    def values(self):
        return (
            self.root_edit.text().strip(),
            self.alarm_edit.text().strip(),
            self.history_edit.text().strip(),
        )


class MenuSelectButton(QPushButton):
    selectionChanged = Signal(str)

    def __init__(self, placeholder="Select", parent=None):
        super().__init__(parent)
        self._items: list[str] = []
        self._current_text: str = ""
        self._placeholder = placeholder

        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)

        # 버튼 기본 폭
        self.setMinimumWidth(140)

        self.setStyleSheet("""
        QPushButton {
            background: white;
            color: #1f2937;
            border: 1px solid #d9dee7;
            border-radius: 10px;
            padding: 6px 34px 6px 14px;
            text-align: center;
        }
        QPushButton:hover {
            background: #f8fafc;
            border: 1px solid #cfd6e2;
        }
        QPushButton:focus {
            border: 1px solid #60a5fa;
        }
        QPushButton::menu-indicator {
            image: none;
            width: 0px;
            height: 0px;
        }
        """)

        self._menu = QMenu(self)
        self._menu.setAttribute(Qt.WA_TranslucentBackground)
        self._menu.setStyleSheet("""
        QMenu {
            background: white;
            color: #1f2937;
            border: 1px solid #d9dee7;
            border-radius: 4px;
            padding: 6px;
        }
        QMenu::item {
            padding: 10px 16px;
            border-radius: 10px;
        }
        QMenu::item:selected {
            background: #eef4ff;
        }
        """)
        self.setMenu(self._menu)
        self._refresh_text()

    def showMenu(self):
        self._menu.setFixedWidth(self.width() + 8)
        pos = self.mapToGlobal(self.rect().bottomLeft())
        pos.setY(pos.y() + 2)
        self._menu.popup(pos)

    def resizeEvent(self, e):
        super().resizeEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        color = QColor("#4b5563") if self.isEnabled() else QColor("#9ca3af")
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        r = self.rect()
        cx = r.right() - 14
        cy = r.center().y()
        w = 8
        h = 5

        path = QPainterPath()
        path.moveTo(cx - w / 2, cy - h / 2)
        path.lineTo(cx, cy + h / 2)
        path.lineTo(cx + w / 2, cy - h / 2)
        p.drawPath(path)
        p.end()

    def clear(self):
        self._items.clear()
        self._menu.clear()
        self._current_text = ""
        self._refresh_text()

    def addItems(self, items: list[str]):
        for t in items:
            self.addItem(t)

    def addItem(self, text: str):
        txt = str(text)
        self._items.append(txt)
        act = QAction(txt, self._menu)
        act.triggered.connect(lambda checked=False, v=txt: self.setCurrentText(v))
        self._menu.addAction(act)

        if not self._current_text:
            self._current_text = txt
            self._refresh_text()

    def currentText(self) -> str:
        return self._current_text

    def setCurrentText(self, text: str):
        txt = str(text)
        if txt not in self._items and txt != "":
            return
        changed = (self._current_text != txt)
        self._current_text = txt
        self._refresh_text()
        if changed:
            self.selectionChanged.emit(self._current_text)

    def setPlaceholderText(self, text: str):
        self._placeholder = str(text)
        self._refresh_text()

    def _refresh_text(self):
        self.setText(self._current_text if self._current_text else self._placeholder)


class ChromeProxyStyle(QProxyStyle):
    """
    ComboBox / DateEdit / DateTimeEdit / DoubleSpinBox 의
    화살표를 크롬 느낌의 chevron 으로 직접 그린다.
    앱 전체에 한 번만 적용해서 사용한다.
    """

    def __init__(self):
        super().__init__()

    def drawPrimitive(self, element, option, painter, widget=None):
        spin_up = getattr(QStyle, "PE_IndicatorSpinUp", None)
        spin_down = getattr(QStyle, "PE_IndicatorSpinDown", None)

        arrow_elements = {
            QStyle.PE_IndicatorArrowDown,
            QStyle.PE_IndicatorArrowUp,
            QStyle.PE_IndicatorArrowLeft,
            QStyle.PE_IndicatorArrowRight,
        }
        if spin_up is not None:
            arrow_elements.add(spin_up)
        if spin_down is not None:
            arrow_elements.add(spin_down)

        if element in arrow_elements:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)

            r = option.rect.adjusted(2, 2, -2, -2)

            color = QColor("#4b5563")
            if option.state & QStyle.State_MouseOver:
                color = QColor("#374151")
            if option.state & QStyle.State_Sunken:
                color = QColor("#111827")
            if not (option.state & QStyle.State_Enabled):
                color = QColor("#9ca3af")

            pen = QPen(color, 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)

            cx = r.center().x()
            cy = r.center().y()

            w = max(6, min(10, r.width() - 4))
            h = max(4, min(7, r.height() - 4))

            path = QPainterPath()

            if element in (QStyle.PE_IndicatorArrowDown, spin_down):
                path.moveTo(cx - w / 2, cy - h / 3)
                path.lineTo(cx, cy + h / 2)
                path.lineTo(cx + w / 2, cy - h / 3)

            elif element in (QStyle.PE_IndicatorArrowUp, spin_up):
                path.moveTo(cx - w / 2, cy + h / 3)
                path.lineTo(cx, cy - h / 2)
                path.lineTo(cx + w / 2, cy + h / 3)

            elif element == QStyle.PE_IndicatorArrowLeft:
                path.moveTo(cx + w / 3, cy - h / 2)
                path.lineTo(cx - w / 2, cy)
                path.lineTo(cx + w / 3, cy + h / 2)

            elif element == QStyle.PE_IndicatorArrowRight:
                path.moveTo(cx - w / 3, cy - h / 2)
                path.lineTo(cx + w / 2, cy)
                path.lineTo(cx - w / 3, cy + h / 2)

            painter.drawPath(path)
            painter.restore()
            return

        super().drawPrimitive(element, option, painter, widget)

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
    date_css = chrome_dateedit_style()
    spin_css = chrome_spinbox_style()

    for w in root.findChildren(QDateEdit):
        w.setStyleSheet(date_css)

    for w in root.findChildren(QDateTimeEdit):
        w.setStyleSheet(date_css)

    for w in root.findChildren(QDoubleSpinBox):
        w.setStyleSheet(spin_css)


def build_app_stylesheet() -> str:
    return """
    QWidget {
        background: #f5f6f8;
        color: #1f2937;
        font-size: 12px;
    }

    QMainWindow, QSplitter, QStackedWidget {
        background: #f5f6f8;
    }

    QLabel {
        background: transparent;
        color: #1f2937;
    }

    QLineEdit, QComboBox, QDateTimeEdit, QDateEdit, QDoubleSpinBox, QListWidget {
        background: white;
        color: #1f2937;
        border: 1px solid #d9dee7;
        border-radius: 10px;
        padding: 6px 8px;
        selection-background-color: #dbeafe;
    }

    QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus, QDateEdit:focus, QDoubleSpinBox:focus, QListWidget:focus {
        border: 1px solid #60a5fa;
    }

    QPushButton {
        background: white;
        color: #1f2937;
        border: 1px solid #d9dee7;
        border-radius: 10px;
        padding: 7px 12px;
        font-weight: 600;
    }

    QPushButton:hover {
        background: #f8fafc;
    }

    QPushButton:pressed {
        background: #eef2f7;
    }

    QPushButton:disabled {
        background: #f3f4f6;
        color: #9ca3af;
        border: 1px solid #e5e7eb;
    }

    QTreeView {
        background: white;
        color: #1f2937;
        border: 1px solid #d9dee7;
        border-radius: 14px;
        alternate-background-color: #f8fafc;
        padding: 4px;
    }

    QTreeView::item {
        padding: 6px 4px;
    }

    QTreeView::item:selected {
        background: #eaf2ff;
        color: #111827;
    }

    QHeaderView::section {
        background: #f8fafc;
        border: none;
        border-bottom: 1px solid #e5e7eb;
        padding: 8px;
        font-weight: 600;
        color: #6b7280;
    }

    QMenu {
        background: white;
        color: #1f2937;
        border: 1px solid #d9dee7;
        border-radius: 12px;
        padding: 6px;
    }

    QMenu::item {
        padding: 8px 14px;
        border-radius: 8px;
    }

    QMenu::item:selected {
        background: #eef4ff;
    }

    QDialog {
        background: #f8fafc;
        color: #1f2937;
    }

    QDialog QLabel {
        background: transparent;
        color: #1f2937;
    }

    QDialog QCheckBox {
        background: transparent;
        color: #1f2937;
    }

    QDialog QComboBox,
    QDialog QLineEdit,
    QDialog QDateTimeEdit,
    QDialog QDateEdit,
    QDialog QDoubleSpinBox,
    QDialog QListWidget {
        background: white;
        color: #1f2937;
        border: 1px solid #d9dee7;
        border-radius: 10px;
        padding: 6px 8px;
    }

    QDialogButtonBox QPushButton {
        min-width: 88px;
    }

    /* ----------------------------- */
    /* Chrome-like ScrollBar */
    /* ----------------------------- */

    QScrollBar:vertical {
        background: transparent;
        width: 12px;
        margin: 4px 2px 4px 2px;
    }

    QScrollBar::handle:vertical {
        background: rgba(120, 130, 145, 0.55);
        min-height: 28px;
        border-radius: 6px;
    }

    QScrollBar::handle:vertical:hover {
        background: rgba(95, 105, 120, 0.78);
    }

    QScrollBar::handle:vertical:pressed {
        background: rgba(70, 80, 95, 0.88);
    }

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        height: 0px;
        background: transparent;
        border: none;
    }

    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {
        background: transparent;
    }

    QScrollBar:horizontal {
        background: transparent;
        height: 12px;
        margin: 2px 4px 2px 4px;
    }

    QScrollBar::handle:horizontal {
        background: rgba(120, 130, 145, 0.55);
        min-width: 28px;
        border-radius: 6px;
    }

    QScrollBar::handle:horizontal:hover {
        background: rgba(95, 105, 120, 0.78);
    }

    QScrollBar::handle:horizontal:pressed {
        background: rgba(70, 80, 95, 0.88);
    }

    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {
        width: 0px;
        background: transparent;
        border: none;
    }

    QScrollBar::add-page:horizontal,
    QScrollBar::sub-page:horizontal {
        background: transparent;
    }
    """

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

def parse_log_filename(p: Path) -> LogMeta | None:
    """
    (튜브)_(레시피명)_(jobID)_(날짜)_(시간).csv
    레시피명은 '_' 포함 가능하므로 뒤에서부터 파싱한다.
    """
    if p.suffix.lower() != ".csv":
        return None

    parts = p.stem.split("_")
    if len(parts) < 5:
        return None

    date_str = parts[-2]
    time_str = parts[-1]
    job_id = parts[-3]
    tube = parts[0]
    recipe = "_".join(parts[1:-3]).strip()

    if not DATE_RE8.match(date_str):
        return None
    if not TIME_RE4_6.match(time_str):
        return None

    dt = None
    try:
        fmt = "%Y%m%d%H%M" if len(time_str) == 4 else "%Y%m%d%H%M%S"
        dt = pd.to_datetime(date_str + time_str, format=fmt, errors="coerce")
        if pd.isna(dt):
            dt = None
    except Exception:
        dt = None

    return LogMeta(path=p, tube=tube, recipe=recipe, job_id=job_id, date_str=date_str, time_str=time_str, dt=dt)


def scan_logs(root_dir: Path) -> pd.DataFrame:
    """
    root_dir 하위 모든 csv를 스캔해서 파일명 기반 메타를 DataFrame으로 반환.
    columns: path, tube, recipe, job_id, date, time, dt
    """
    rows = []
    for p in root_dir.rglob("*.csv"):
        meta = parse_log_filename(p)
        if meta is None:
            continue
        rows.append({
            "path": str(meta.path),
            "tube": meta.tube,
            "recipe": meta.recipe,
            "job_id": meta.job_id,
            "date": meta.date_str,
            "time": meta.time_str,
            "dt": meta.dt,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["date_dt"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df = (
        df.dropna(subset=["date_dt"])
          .sort_values(["date_dt", "tube", "recipe", "job_id"])
          .reset_index(drop=True)
    )
    return df


def parse_history_log_filename(p: Path) -> tuple[str, str, str, bool] | None:
    """
    history_dir 구조:
      history_dir / TUBE01 / (레시피명)_(날짜)_(시간).csv
      history_dir / TUBE01 / (레시피명)_(날짜)_(시간)_AB.csv

    return:
      recipe_from_filename, date_str, time_str, is_abort
    """
    if p.suffix.lower() != ".csv":
        return None

    tube = p.parent.name.strip()
    if not tube:
        return None

    parts = p.stem.split("_")
    if len(parts) < 3:
        return None

    is_abort = False
    if parts[-1].upper() == "AB":
        is_abort = True
        parts = parts[:-1]

    if len(parts) < 3:
        return None

    date_str = parts[-2]
    time_str = parts[-1]
    recipe_from_filename = "_".join(parts[:-2]).strip()

    if not DATE_RE8.match(date_str):
        return None
    if not TIME_RE4_6.match(time_str):
        return None

    return recipe_from_filename, date_str, time_str, is_abort

def extract_history_meta_from_first_row(p: Path) -> dict[str, object]:
    """
    history log CSV 첫 행에서
    'Job ID' ~ 'Recipe Name' 사이(양 끝 포함)의 모든 열을 추출한다.
    """
    try:
        df = pd.read_csv(p, low_memory=False, nrows=1)
        if df.empty:
            return {}
    except Exception:
        return {}

    df = CsvPlotPanel._normalize_columns(df)
    cols = list(df.columns)

    norm_map = {c: re.sub(r"[^a-z0-9]", "", str(c).strip().lower()) for c in cols}

    job_idx = None
    recipe_idx = None

    for i, c in enumerate(cols):
        norm = norm_map[c]
        if job_idx is None and norm in ("jobid", "job"):
            job_idx = i
        if recipe_idx is None and norm in ("recipename", "recipe"):
            recipe_idx = i

    if job_idx is None or recipe_idx is None:
        return {}

    if job_idx > recipe_idx:
        job_idx, recipe_idx = recipe_idx, job_idx

    first = df.iloc[0]
    out = {}

    for c in cols[job_idx:recipe_idx + 1]:
        v = first[c]
        if pd.isna(v):
            out[c] = ""
        else:
            out[c] = str(v).strip()

    return out

def scan_history_logs(
    history_dir: Path,
    start_date: pd.Timestamp | None = None,
    end_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    history_dir 하위 로그를 읽어서 raw/history DataFrame 생성.
    - tube: 상위 폴더명
    - recipe_from_filename: 파일명에서 파싱
    - recipe_name: 파일 내부 첫 행의 'Recipe Name'
    - job_id: 파일 내부 첫 행의 'Job ID'
    - is_abort: 파일명 끝 _AB 여부

    start_date / end_date 가 주어지면
    파일명 날짜(date_str) 기준으로 해당 기간만 스캔한다.
    """
    rows = []

    if not history_dir.exists():
        return pd.DataFrame()

    start_norm = pd.Timestamp(start_date).normalize() if start_date is not None else None
    end_norm = pd.Timestamp(end_date).normalize() if end_date is not None else None

    for p in history_dir.rglob("*.csv"):
        parsed = parse_history_log_filename(p)
        if parsed is None:
            continue

        recipe_from_filename, date_str, time_str, is_abort = parsed
        tube = p.parent.name.strip()

        # ---------- 날짜 범위 선필터 ----------
        try:
            file_date = pd.to_datetime(date_str, format="%Y%m%d", errors="coerce")
        except Exception:
            file_date = pd.NaT

        if pd.isna(file_date):
            continue

        file_date = pd.Timestamp(file_date).normalize()

        if start_norm is not None and file_date < start_norm:
            continue
        if end_norm is not None and file_date > end_norm:
            continue
        # ------------------------------------

        dt = None
        try:
            fmt = "%Y%m%d%H%M" if len(time_str) == 4 else "%Y%m%d%H%M%S"
            dt = pd.to_datetime(date_str + time_str, format=fmt, errors="coerce")
            if pd.isna(dt):
                dt = None
        except Exception:
            dt = None

        meta_cols = extract_history_meta_from_first_row(p)

        job_id = str(meta_cols.get("Job ID", meta_cols.get("JOB ID", meta_cols.get("job_id", "")))).strip()
        recipe_name = str(
            meta_cols.get(
                "Recipe Name",
                meta_cols.get("RECIPE NAME", meta_cols.get("recipe_name", recipe_from_filename))
            )
        ).strip() or recipe_from_filename

        row = {
            "path": str(p),
            "tube": tube,
            "recipe": recipe_name,
            "recipe_from_filename": recipe_from_filename,
            "job_id": job_id,
            "date": date_str,
            "time": time_str,
            "dt": dt,
            "is_abort": bool(is_abort),
            "abort_flag": 1 if is_abort else 0,
        }
        row.update(meta_cols)
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["date_dt"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df = (
        df.dropna(subset=["date_dt"])
          .sort_values(["date_dt", "tube", "recipe", "job_id"])
          .reset_index(drop=True)
    )
    return df


# =========================================================
# Sticky tooltip widget (custom)
# =========================================================
class StickyTip(QWidget):
    """
    QToolTip 대신 사용하는 커스텀 패널.
    - 부모(view) 위에 떠있는 QLabel 패널
    - target_hovering=True 이거나, tip 자체 hover 중이면 유지
    - target_hovering=False 되고 tip도 hover 아님이면 hide(약간 딜레이)
    """
    def __init__(self, parent: QWidget, *, kind: str):
        super().__init__(parent)
        self.kind = kind

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setMouseTracking(True)
        self.setVisible(False)

        self._hovering_tip = False
        self._target_hovering = False

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._maybe_hide)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        self.lbl.setTextFormat(Qt.PlainText)
        self.lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        if kind == "alarm":
            self.lbl.setStyleSheet(
                "QLabel{"
                "background: rgba(90,20,20,230);"
                "color: white;"
                "border: 1px solid rgba(255,255,255,40);"
                "border-radius: 10px;"
                "padding: 8px 10px;"
                "font-size: 11px;"
                "}"
            )
        else:
            self.lbl.setStyleSheet(
                "QLabel{"
                "background: rgba(25,25,25,220);"
                "color: white;"
                "border: 1px solid rgba(255,255,255,40);"
                "border-radius: 10px;"
                "padding: 8px 10px;"
                "font-size: 11px;"
                "}"
            )

        lay.addWidget(self.lbl)

    def set_target_hovering(self, on: bool):
        self._target_hovering = bool(on)
        if on:
            self._hide_timer.stop()
        else:
            self._hide_timer.start(120)

    def show_text_at(self, text: str, pos_in_parent: QPoint, *, offset: QPoint = QPoint(16, 16)):
        if not text:
            self.hide_tip()
            return

        self.lbl.setText(text)
        self.lbl.adjustSize()
        self.adjustSize()

        x = pos_in_parent.x() + offset.x()
        y = pos_in_parent.y() + offset.y()

        pw = self.parentWidget().width()
        ph = self.parentWidget().height()
        w = self.width()
        h = self.height()

        if x + w > pw:
            x = max(0, pw - w)
        if y + h > ph:
            y = max(0, ph - h)
        if x < 0:
            x = 0
        if y < 0:
            y = 0

        self.move(x, y)
        self.setVisible(True)
        self.raise_()

    def hide_tip(self):
        self._hide_timer.stop()
        self.setVisible(False)

    def _maybe_hide(self):
        if (not self._target_hovering) and (not self._hovering_tip):
            self.setVisible(False)

    def enterEvent(self, e):
        self._hovering_tip = True
        self._hide_timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovering_tip = False
        if not self._target_hovering:
            self._hide_timer.start(120)
        super().leaveEvent(e)


# =========================================================
# Dialogs
# =========================================================
class YScaleDialog(QDialog):
    """Left/Right Y 축 스케일 설정 다이얼로그 (Auto/Manual + Min/Max)."""
    def __init__(self, title: str, mode: str, ymin: float, ymax: float, is_log: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)

        self.mode_cb = MenuSelectButton("Mode")
        self.mode_cb.addItems(["Auto", "Manual"])
        self.mode_cb.setCurrentText(mode if mode in ("Auto", "Manual") else "Auto")

        self.ymin_sb = QDoubleSpinBox()
        self.ymin_sb.setDecimals(6)
        self.ymin_sb.setRange(-1e30, 1e30)
        self.ymin_sb.setValue(float(ymin))

        self.ymax_sb = QDoubleSpinBox()
        self.ymax_sb.setDecimals(6)
        self.ymax_sb.setRange(-1e30, 1e30)
        self.ymax_sb.setValue(float(ymax))

        self.log_cb = MenuSelectButton("Scale")
        self.log_cb.addItems(["Linear", "Log"])
        self.log_cb.setCurrentText("Log" if is_log else "Linear")

        form = QFormLayout()
        form.addRow("Mode", self.mode_cb)
        form.addRow("Scale", self.log_cb)
        form.addRow("Min", self.ymin_sb)
        form.addRow("Max", self.ymax_sb)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(btns)

        self.mode_cb.selectionChanged.connect(self._sync_enabled)
        self._sync_enabled()
        apply_chrome_input_styles(self)

    def _sync_enabled(self):
        manual = (self.mode_cb.currentText() == "Manual")
        self.ymin_sb.setEnabled(manual)
        self.ymax_sb.setEnabled(manual)

    def values(self):
        return (
            self.mode_cb.currentText(),
            float(self.ymin_sb.value()),
            float(self.ymax_sb.value()),
            self.log_cb.currentText() == "Log",
        )


class XRangeDialog(QDialog):
    """X 범위 설정 다이얼로그. datetime/numeric 둘 다 지원."""
    def __init__(self, is_datetime: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("X Range")
        self.is_datetime = is_datetime

        self.dt_start = QDateTimeEdit()
        self.dt_start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_start.setCalendarPopup(True)

        self.dt_end = QDateTimeEdit()
        self.dt_end.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_end.setCalendarPopup(True)

        self.num_start = QDoubleSpinBox()
        self.num_start.setDecimals(6)
        self.num_start.setRange(-1e30, 1e30)

        self.num_end = QDoubleSpinBox()
        self.num_end.setDecimals(6)
        self.num_end.setRange(-1e30, 1e30)

        form = QFormLayout()
        if self.is_datetime:
            form.addRow("Start", self.dt_start)
            form.addRow("End", self.dt_end)
        else:
            form.addRow("Start", self.num_start)
            form.addRow("End", self.num_end)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(btns)

        apply_chrome_input_styles(self)

    def set_dt_values(self, qmin: QDateTime, qmax: QDateTime, cur_start: QDateTime, cur_end: QDateTime):
        self.dt_start.setMinimumDateTime(qmin)
        self.dt_start.setMaximumDateTime(qmax)
        self.dt_end.setMinimumDateTime(qmin)
        self.dt_end.setMaximumDateTime(qmax)
        self.dt_start.setDateTime(cur_start)
        self.dt_end.setDateTime(cur_end)

    def set_num_values(self, xmin: float, xmax: float, cur_start: float, cur_end: float):
        self.num_start.setRange(xmin, xmax)
        self.num_end.setRange(xmin, xmax)
        self.num_start.setValue(cur_start)
        self.num_end.setValue(cur_end)

    def values(self):
        if self.is_datetime:
            return self.dt_start.dateTime(), self.dt_end.dateTime()
        return float(self.num_start.value()), float(self.num_end.value())


class RefLineDialog(QDialog):
    """
    기준선 설정:
      - X 기준선(세로선)
      - Y 기준선(가로선) + 어느 Y축(Left/Right) 기준인지 선택
    """
    def __init__(self, *, x_is_datetime: bool, parent=None):
        super().__init__(parent)

        self.setStyleSheet("""
        QDialog {
            background: #f8fafc;
            color: #1f2937;
        }
        QLabel, QCheckBox {
            background: transparent;
            color: #1f2937;
        }
        QCheckBox {
            spacing: 10px;
            padding: 6px 8px;
        }
        QCheckBox::indicator {
            width: 18px;
            height: 18px;
        }
        QCheckBox::indicator:unchecked {
            border: 1px solid #cbd5e1;
            border-radius: 5px;
            background: white;
        }
        QCheckBox::indicator:checked {
            border: 1px solid #2563eb;
            border-radius: 5px;
            background: #2563eb;
        }
        QComboBox, QDateTimeEdit, QDoubleSpinBox {
            background: white;
            color: #1f2937;
            border: 1px solid #d9dee7;
            border-radius: 10px;
            padding: 6px 8px;
        }
        QComboBox:focus, QDateTimeEdit:focus, QDoubleSpinBox:focus {
            border: 1px solid #60a5fa;
        }
        QPushButton {
            background: white;
            color: #1f2937;
            border: 1px solid #d9dee7;
            border-radius: 10px;
            padding: 7px 12px;
            font-weight: 600;
        }
        QPushButton:hover {
            background: #f8fafc;
        }
        """)

        self.setWindowTitle("Reference Lines")

        self.x_is_datetime = x_is_datetime

        self.cb_x = QCheckBox("Show X reference line")
        self.cb_y = QCheckBox("Show Y reference line")
        self.cb_x.setChecked(True)
        self.cb_y.setChecked(False)

        self.x_dt = QDateTimeEdit()
        self.x_dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.x_dt.setCalendarPopup(True)

        self.x_num = QDoubleSpinBox()
        self.x_num.setDecimals(6)
        self.x_num.setRange(-1e30, 1e30)

        self.y_val = QDoubleSpinBox()
        self.y_val.setDecimals(6)
        self.y_val.setRange(-1e30, 1e30)

        self.y_axis_side = MenuSelectButton("Y axis")
        self.y_axis_side.addItems(["Left", "Right"])
        self.y_axis_side.setCurrentText("Left")

        form = QFormLayout()
        form.addRow(self.cb_x)
        if self.x_is_datetime:
            form.addRow("X value", self.x_dt)
        else:
            form.addRow("X value", self.x_num)

        form.addRow(self.cb_y)
        form.addRow("Y axis", self.y_axis_side)
        form.addRow("Y value", self.y_val)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(btns)

        self.cb_x.toggled.connect(self._sync_enabled)
        self.cb_y.toggled.connect(self._sync_enabled)
        self._sync_enabled()
        apply_chrome_input_styles(self)

    def _sync_enabled(self):
        x_on = self.cb_x.isChecked()
        y_on = self.cb_y.isChecked()

        self.x_dt.setEnabled(x_on and self.x_is_datetime)
        self.x_num.setEnabled(x_on and (not self.x_is_datetime))
        self.y_axis_side.setEnabled(y_on)
        self.y_val.setEnabled(y_on)

    def set_initial(
        self,
        *,
        x_value: float | None,
        y_value: float | None,
        y_side: str | None,
        x_dt_min: QDateTime | None = None,
        x_dt_max: QDateTime | None = None
    ):
        if x_value is not None:
            if self.x_is_datetime:
                q = QDateTime.fromMSecsSinceEpoch(int(x_value))
                self.x_dt.setDateTime(q)
                if x_dt_min is not None:
                    self.x_dt.setMinimumDateTime(x_dt_min)
                if x_dt_max is not None:
                    self.x_dt.setMaximumDateTime(x_dt_max)
            else:
                self.x_num.setValue(float(x_value))

        if y_value is not None:
            self.y_val.setValue(float(y_value))

        if y_side:
            self.y_axis_side.setCurrentText("Left" if y_side.lower().startswith("l") else "Right")

    def values(self):
        x_on = self.cb_x.isChecked()
        y_on = self.cb_y.isChecked()

        if self.x_is_datetime:
            x_val = float(self.x_dt.dateTime().toMSecsSinceEpoch())
        else:
            x_val = float(self.x_num.value())

        y_val = float(self.y_val.value())
        side = self.y_axis_side.currentText().lower()
        return x_on, x_val, y_on, side, y_val


class FilterableList(QWidget):
    """
    QListWidget + 검색창(QLineEdit)
    - 타이핑 시 매칭 안되는 항목 숨김
    - 선택 상태는 유지됨(숨겨져도 선택은 유지될 수 있음)
    """
    def __init__(self, items: list[str], *, placeholder: str = "Search...", parent=None):
        super().__init__(parent)

        self.search = QLineEdit()
        self.search.setPlaceholderText(placeholder)
        self.search.setClearButtonEnabled(True)

        self.listw = QListWidget()
        self.listw.setSelectionMode(QAbstractItemView.MultiSelection)

        for t in items:
            self.listw.addItem(QListWidgetItem(t))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.search, 0)
        lay.addWidget(self.listw, 1)

        self.search.textChanged.connect(self._apply_filter)

    def _apply_filter(self, text: str):
        q = (text or "").strip().lower()
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            if not q:
                it.setHidden(False)
            else:
                it.setHidden(q not in it.text().lower())

    def set_selected(self, selected: list[str]):
        sel = set(selected)
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            it.setSelected(it.text() in sel)

    def selected_texts(self) -> list[str]:
        return [i.text() for i in self.listw.selectedItems()]

    def clear_selection(self):
        self.listw.blockSignals(True)
        try:
            for i in range(self.listw.count()):
                it = self.listw.item(i)
                it.setSelected(False)
        finally:
            self.listw.blockSignals(False)


class YColumnsDialog(QDialog):
    """Y 축에 그릴 컬럼을 최대 3개까지 선택 (검색 지원)."""
    def __init__(self, title: str, items: list[str], selected: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)

        self.fl = FilterableList(items, placeholder="Search Y columns...", parent=self)
        self.fl.set_selected(selected)

        hint = QLabel("Type to search. You can choose columns you want to plot on the Y axis.")
        hint.setStyleSheet("color:#6b7280;")

        self.btn_clear = QPushButton("Clear selection")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self.fl.clear_selection)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(self.fl, 1)
        root.addWidget(hint)

        row = QHBoxLayout()
        row.addWidget(self.btn_clear, 0)
        row.addStretch(1)
        row.addWidget(btns, 0)
        root.addLayout(row)

    def selected_items(self) -> list[str]:
        return self.fl.selected_texts()


# =========================================================
# TitleBar
# =========================================================
class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 6)
        lay.setSpacing(8)

        self.lbl = QLabel("—")
        self.lbl.setStyleSheet("font-weight:700; font-size:13px; color:#111827;")
        self.lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        hint = QLabel("X: click=range / drag=zoom · Y band=scale")
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        lay.addWidget(self.lbl, 1)
        lay.addWidget(hint, 0)

    def setText(self, t: str):
        self.lbl.setText(t)


# =========================================================
# Chart View
# =========================================================
class PowerChartView(QChartView):
    BAND = 14

    def __init__(self, area: "PlotArea", parent=None):
        super().__init__(parent)
        self.area = area
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setMouseTracking(True)
        self.setRubberBand(QChartView.NoRubberBand)

        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setMinimumSize(0, 0)

        logger.debug("[UI][PowerChartView] init")

        self._band_bottom = QGraphicsRectItem()
        self._band_left = QGraphicsRectItem()
        self._band_right = QGraphicsRectItem()
        for it in (self._band_bottom, self._band_left, self._band_right):
            it.setZValue(9)
            it.setPen(QPen(QColor(0, 0, 0, 0), 0))
            it.setBrush(QColor(0, 0, 0, 0))
            it.setVisible(False)

        self._vline = QGraphicsLineItem()
        self._hline = QGraphicsLineItem()
        for ln in (self._vline, self._hline):
            ln.setZValue(10)
            ln.setPen(QPen(QColor("gray"), 1))
            ln.setVisible(False)

        self._ref_vline = QGraphicsLineItem()
        self._ref_hline = QGraphicsLineItem()
        ref_pen = QPen(QColor("red"), 1.3, Qt.DashLine)
        for ln in (self._ref_vline, self._ref_hline):
            ln.setZValue(12)
            ln.setPen(ref_pen)
            ln.setVisible(False)

        self._alarm_halo = QGraphicsEllipseItem()
        self._alarm_halo.setZValue(11)
        self._alarm_halo.setPen(QPen(QColor(255, 0, 0, 0), 0))
        self._alarm_halo.setBrush(QBrush(QColor(255, 0, 0, 60)))
        self._alarm_halo.setVisible(False)

        self._rubber = QRubberBand(QRubberBand.Rectangle, self)
        self._dragging = False
        self._drag_start = QPoint()
        self._hover_kind: str | None = None

        self.tip_cross = StickyTip(self, kind="cross")
        self.tip_alarm = StickyTip(self, kind="alarm")

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def sizeHint(self) -> QSize:
        return QSize(200, 120)

    def _ensure_scene_items(self):
        sc = self.chart().scene()
        for it in (
            self._band_bottom, self._band_left, self._band_right,
            self._vline, self._hline, self._alarm_halo,
            self._ref_vline, self._ref_hline
        ):
            if it.scene() is None:
                sc.addItem(it)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._ensure_scene_items()
        self._layout_bands()
        self.area.update_reference_lines()

    def mouseDoubleClickEvent(self, e):
        if not self._plot_contains(e.position().toPoint()):
            return super().mouseDoubleClickEvent(e)

        pa = self.chart().plotArea()
        p_scene = self.mapToScene(e.position().toPoint())

        x_val = None
        ref = self.area._ref_series_for_mapping()
        if ref is not None:
            try:
                v = self.chart().mapToValue(QPointF(p_scene.x(), pa.center().y()), ref)
                x_val = float(v.x())
            except Exception:
                x_val = None

        y_val = None
        y_side = "left"
        ref_y = self.area._ref_series_for_left()
        if ref_y is not None:
            try:
                v2 = self.chart().mapToValue(QPointF(pa.center().x(), p_scene.y()), ref_y)
                y_val = float(v2.y())
            except Exception:
                y_val = None

        x_is_dt = (self.area.parent_panel.x_is_datetime and (not self.area.compare_active))
        dlg = RefLineDialog(x_is_datetime=x_is_dt, parent=self)

        x_dt_min = None
        x_dt_max = None
        if x_is_dt:
            x_dt_min = self.area.parent_panel._full_x_min_dt
            x_dt_max = self.area.parent_panel._full_x_max_dt

        dlg.set_initial(
            x_value=x_val,
            y_value=y_val,
            y_side=y_side,
            x_dt_min=x_dt_min,
            x_dt_max=x_dt_max
        )

        if dlg.exec() != QDialog.Accepted:
            return

        x_on, x_value, y_on, side, y_value = dlg.values()

        self.area.ref_x_on = bool(x_on)
        self.area.ref_x_value = float(x_value)
        self.area.ref_y_on = bool(y_on)
        self.area.ref_y_side = str(side)
        self.area.ref_y_value = float(y_value)
        self.area.update_reference_lines()

    def _layout_bands(self):
        pa: QRectF = self.chart().plotArea()
        b = self.BAND
        self._band_bottom.setRect(QRectF(pa.left(), pa.bottom(), pa.width(), b))
        self._band_left.setRect(QRectF(pa.left() - b, pa.top(), b, pa.height()))
        self._band_right.setRect(QRectF(pa.right(), pa.top(), b, pa.height()))

    def _set_band_visible(self, which: str | None):
        self._ensure_scene_items()

        def show(it: QGraphicsRectItem, on: bool):
            if not on:
                it.setVisible(False)
                return
            it.setVisible(True)
            it.setBrush(QColor(0, 0, 0, 18))
            it.setPen(QPen(QColor(0, 0, 0, 30), 1))

        show(self._band_bottom, which == "x_axis")
        show(self._band_left, which == "y_left")
        show(self._band_right, which == "y_right")

        if which is None:
            self.unsetCursor()
        else:
            self.setCursor(Qt.PointingHandCursor)

    def _hit_kind(self, pos_view: QPoint) -> str | None:
        pa = self.chart().plotArea()
        p_scene = self.mapToScene(pos_view)
        b = self.BAND

        if QRectF(pa.left(), pa.bottom(), pa.width(), b).contains(p_scene):
            return "x_axis"
        if QRectF(pa.left() - b, pa.top(), b, pa.height()).contains(p_scene):
            return "y_left"
        if QRectF(pa.right(), pa.top(), b, pa.height()).contains(p_scene):
            return "y_right"
        return None

    def _plot_contains(self, pos_view: QPoint) -> bool:
        pa = self.chart().plotArea()
        p_scene = self.mapToScene(pos_view)
        return pa.contains(p_scene)

    def _update_crosshair(self, pos_view: QPoint):
        self._ensure_scene_items()
        if not self._plot_contains(pos_view):
            self._vline.setVisible(False)
            self._hline.setVisible(False)
            return

        pa = self.chart().plotArea()
        p_scene = self.mapToScene(pos_view)

        self._vline.setLine(p_scene.x(), pa.top(), p_scene.x(), pa.bottom())
        self._vline.setVisible(True)

        self._hline.setLine(pa.left(), p_scene.y(), pa.right(), p_scene.y())
        self._hline.setVisible(True)

    def show_alarm_halo_at(self, series, point: QPointF, on: bool):
        self._ensure_scene_items()
        if not on or series is None:
            self._alarm_halo.setVisible(False)
            return
        try:
            pos = self.chart().mapToPosition(point, series)
        except Exception:
            self._alarm_halo.setVisible(False)
            return
        r = 14.0
        self._alarm_halo.setRect(QRectF(pos.x() - r, pos.y() - r, r * 2, r * 2))
        self._alarm_halo.setVisible(True)

    def mouseMoveEvent(self, e):
        self._layout_bands()

        if self._dragging:
            rect = QRect(self._drag_start, e.position().toPoint()).normalized()
            self._rubber.setGeometry(rect)
            self._rubber.show()
        else:
            kind = self._hit_kind(e.position().toPoint())
            if kind != self._hover_kind:
                self._hover_kind = kind
                self._set_band_visible(kind)

        self._update_crosshair(e.position().toPoint())

        if self._plot_contains(e.position().toPoint()):
            self.tip_cross.set_target_hovering(True)
            if self.area._alarm_hovering:
                self.tip_cross.hide_tip()
            else:
                ref = self.area._ref_series_for_mapping()
                if ref is not None:
                    pa = self.chart().plotArea()
                    p_scene = self.mapToScene(e.position().toPoint())
                    v = self.chart().mapToValue(QPointF(p_scene.x(), pa.center().y()), ref)
                    x = float(v.x())
                    text = self.area.build_crosshair_text(x)
                    self.tip_cross.show_text_at(text, e.position().toPoint(), offset=QPoint(16, 16))
                else:
                    self.tip_cross.hide_tip()
        else:
            self.tip_cross.set_target_hovering(False)

        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.tip_cross.set_target_hovering(False)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)

        kind = self._hit_kind(e.position().toPoint())
        if kind == "x_axis":
            self.area.parent_panel._open_x_range_dialog()
            return
        if kind == "y_left":
            self.area.parent_panel._open_y_scale_dialog(side="left", area=self.area)
            return
        if kind == "y_right":
            self.area.parent_panel._open_y_scale_dialog(side="right", area=self.area)
            return

        if self._plot_contains(e.position().toPoint()):
            self._dragging = True
            self._drag_start = e.position().toPoint()
            self._rubber.hide()
            return

        return super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self._rubber.hide()

            rect = QRect(self._drag_start, e.position().toPoint()).normalized()
            if rect.width() < 6:
                return

            pa = self.chart().plotArea()
            p0s = self.mapToScene(rect.topLeft())
            p1s = self.mapToScene(rect.bottomRight())

            x0_scene = max(pa.left(), min(pa.right(), p0s.x()))
            x1_scene = max(pa.left(), min(pa.right(), p1s.x()))
            if abs(x1_scene - x0_scene) < 2:
                return

            ref_series = self.area._ref_series_for_mapping()
            if ref_series is None:
                return

            v0 = self.chart().mapToValue(QPointF(x0_scene, pa.center().y()), ref_series)
            v1 = self.chart().mapToValue(QPointF(x1_scene, pa.center().y()), ref_series)

            xmin = float(min(v0.x(), v1.x()))
            xmax = float(max(v0.x(), v1.x()))
            self.area.parent_panel._apply_zoom_from_chart(area=self.area, xmin=xmin, xmax=xmax)
            return

        super().mouseReleaseEvent(e)


# =========================================================
# PlotArea (QtCharts)
# =========================================================
class PlotArea:
    def __init__(self, parent_panel: "CsvPlotPanel"):
        self.parent_panel = parent_panel

        self.chart = QChart()
        self.chart.legend().setAlignment(Qt.AlignBottom)
        self.chart.legend().setVisible(True)
        self.chart.setBackgroundRoundness(10)
        self.chart.setMargins(QMargins(0, 0, 0, 0))
        self.chart.setBackgroundVisible(False)
        self.chart.setPlotAreaBackgroundVisible(False)

        try:
            self.chart.layout().setContentsMargins(0, 0, 0, 0)
        except Exception:
            pass

        self.view = PowerChartView(self)
        self.view.setChart(self.chart)
        self.view.setStyleSheet("background: transparent; border: none;")

        self.container = QWidget()
        self.container.setObjectName("plotShadowWrap")
        self.container.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.container.setMinimumSize(0, 0)
        self.container.setStyleSheet("""
        QWidget#plotShadowWrap {
            background: transparent;
            border: none;
        }
        """)

        self.widget = QWidget()
        self.widget.setObjectName("plotCard")
        self.widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.widget.setMinimumSize(0, 0)
        self.widget.setStyleSheet("""
        QWidget#plotCard {
            background: white;
            border: 1px solid #dde3ec;
            border-radius: 18px;
        }
        """)

        apply_shadow(self.widget, blur=28, y_offset=5)

        container_lay = QVBoxLayout(self.container)
        container_lay.setContentsMargins(14, 14, 14, 14)  # <- shadow 여유 공간
        container_lay.setSpacing(0)
        container_lay.addWidget(self.widget)

        wlay = QVBoxLayout(self.widget)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(0)

        topbar = QHBoxLayout()
        topbar.setContentsMargins(10, 8, 10, 6)
        topbar.setSpacing(8)

        self.btn_delete = QPushButton("✕")
        self.btn_delete.setToolTip("Delete this graph")
        self.btn_delete.setFixedSize(35, 30)
        self.btn_delete.setCursor(Qt.PointingHandCursor)
        self.btn_delete.setStyleSheet(
            "QPushButton{"
            "background:white;"
            "border:1px solid #d9dee7;"
            "border-radius:10px;"
            "font-weight:700;"
            "color:#374151;"
            "}"
            "QPushButton:hover{background:#f8fafc;}"
            "QPushButton:pressed{background:#eef2f7;}"
        )
        self.btn_delete.clicked.connect(lambda: self.parent_panel.delete_graph(self))

        self.btn_menu = QPushButton("⋯")
        self.btn_menu.setToolTip("Graph menu")
        self.btn_menu.setFixedSize(35, 30)
        self.btn_menu.setCursor(Qt.PointingHandCursor)
        self.btn_menu.setStyleSheet(
            "QPushButton{"
            "background:white;"
            "border:1px solid #d9dee7;"
            "border-radius:10px;"
            "font-size:18px;"
            "font-weight:700;"
            "color:#111827;"
            "padding-bottom:2px;"
            "}"
            "QPushButton:hover{background:#f8fafc;}"
            "QPushButton:pressed{background:#eef2f7;}"
            "QPushButton::menu-indicator{image:none;width:0px;}"
        )

        self.menu = QMenu(self.btn_menu)
        self.menu.setStyleSheet("""
        QMenu {
            background: white;
            border: 1px solid #d9dee7;
            border-radius: 2px;
            padding: 6px;
        }
        QMenu::item {
            padding: 8px 14px;
            border-radius: 8px;
            color: #111827;
        }
        QMenu::item:selected {
            background: #eef4ff;
        }
        QMenu::separator {
            height: 1px;
            background: #e5e7eb;
            margin: 6px 8px;
        }
        """)

        self.act_left_cols = QAction("Main Y components", self.menu)
        self.act_right_cols = QAction("Sub Y components", self.menu)
        self.act_compare = QAction("Compare", self.menu)

        self.menu.addAction(self.act_left_cols)
        self.menu.addAction(self.act_right_cols)
        self.menu.addSeparator()
        self.menu.addAction(self.act_compare)

        self.act_left_cols.triggered.connect(
            lambda: self.parent_panel._open_y_columns_dialog(side="left", area=self)
        )
        self.act_right_cols.triggered.connect(
            lambda: self.parent_panel._open_y_columns_dialog(side="right", area=self)
        )
        self.act_compare.triggered.connect(
            lambda: self.parent_panel.request_compare_for_area(self)
        )

        self.btn_menu.setMenu(self.menu)

        self.titlebar = TitleBar()

        topbar.addWidget(self.titlebar, 1)
        topbar.addWidget(self.btn_menu, 0, Qt.AlignRight)
        topbar.addWidget(self.btn_delete, 0, Qt.AlignRight)

        wlay.addLayout(topbar)
        wlay.addWidget(self.view, 1)

        self.left_cols: list[str] = []
        self.right_cols: list[str] = []

        self.axis_x_dt: QDateTimeAxis | None = None
        self.axis_x_num: QValueAxis | None = None
        self.axis_y_left: QValueAxis | None = None
        self.axis_y_right: QValueAxis | None = None

        self.left_series: list[QLineSeries] = []
        self.right_series: list[QLineSeries] = []

        self._alarm_hovering: bool = False
        self.alarm_series: QScatterSeries | None = None
        self._alarm_map: dict[int, list[tuple[str, str, str, str]]] = {}

        self.compare_active: bool = False
        self.compare_full_xmin: float | None = None
        self.compare_full_xmax: float | None = None
        self.compare_x_mode: str = ""

        self.ref_x_on: bool = False
        self.ref_y_on: bool = False
        self.ref_x_value: float = 0.0
        self.ref_y_value: float = 0.0
        self.ref_y_side: str = "left"

    def _ref_series_for_left(self):
        if self.left_series:
            return self.left_series[0]
        if self.right_series:
            return self.right_series[0]
        if self.alarm_series:
            return self.alarm_series
        return None

    def _ref_series_for_right(self):
        if self.right_series:
            return self.right_series[0]
        if self.left_series:
            return self.left_series[0]
        if self.alarm_series:
            return self.alarm_series
        return None

    def update_reference_lines(self):
        v = self.view
        v._ensure_scene_items()

        pa = self.chart.plotArea()

        if self.ref_x_on:
            ref_series = self._ref_series_for_mapping()
            if ref_series is not None:
                try:
                    pos = self.chart.mapToPosition(QPointF(float(self.ref_x_value), 0.0), ref_series)
                    x_scene = pos.x()
                    x_scene = max(pa.left(), min(pa.right(), x_scene))
                    v._ref_vline.setLine(x_scene, pa.top(), x_scene, pa.bottom())
                    v._ref_vline.setVisible(True)
                except Exception:
                    v._ref_vline.setVisible(False)
            else:
                v._ref_vline.setVisible(False)
        else:
            v._ref_vline.setVisible(False)

        if self.ref_y_on:
            ref_series_y = self._ref_series_for_left() if self.ref_y_side == "left" else self._ref_series_for_right()
            if ref_series_y is not None:
                try:
                    pos = self.chart.mapToPosition(QPointF(0.0, float(self.ref_y_value)), ref_series_y)
                    y_scene = pos.y()
                    y_scene = max(pa.top(), min(pa.bottom(), y_scene))
                    v._ref_hline.setLine(pa.left(), y_scene, pa.right(), y_scene)
                    v._ref_hline.setVisible(True)
                except Exception:
                    v._ref_hline.setVisible(False)
            else:
                v._ref_hline.setVisible(False)
        else:
            v._ref_hline.setVisible(False)

    def _find_nearest_y(self, series: QLineSeries, x: float) -> float | None:
        try:
            pts = series.points()
        except Exception:
            return None
        n = len(pts)
        if n == 0:
            return None

        lo, hi = 0, n - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if pts[mid].x() < x:
                lo = mid + 1
            else:
                hi = mid

        i = lo
        best = i
        if i > 0 and abs(pts[i - 1].x() - x) <= abs(pts[i].x() - x):
            best = i - 1
        return float(pts[best].y())

    def build_crosshair_text(self, x: float) -> str:
        if self._alarm_hovering:
            return ""

        lines: list[str] = []

        if self.compare_active:
            xm = (self.compare_x_mode or "").lower()
            if "elapsed" in xm or "Δt" in self.compare_x_mode:
                lines.append(f"Elapsed : {int(x)} s")
            else:
                idx = int(round(x))
                lines.append(f"Index : {idx}")
        else:
            if self.parent_panel.x_is_datetime:
                qdt = QDateTime.fromMSecsSinceEpoch(int(x))
                lines.append(qdt.toString("yyyy-MM-dd HH:mm:ss"))

                sn, sname = self.parent_panel._step_info_at_x_value(x)
                if sn or sname:
                    if sn and sname:
                        lines.append(f"Step: {sn} | {sname}")
                    elif sn:
                        lines.append(f"Step: {sn}")
                    else:
                        lines.append(f"Step: {sname}")
            else:
                lines.append(f"X = {x:.6g}")

        if self.left_series:
            lines.append("")
            lines.append("[Left]")
            for s in self.left_series:
                y = self._find_nearest_y(s, x)
                if y is None:
                    continue
                lines.append(f"{s.name()} = {y:.6g}")

        if self.right_series:
            lines.append("")
            lines.append("[Right]")
            for s in self.right_series:
                y = self._find_nearest_y(s, x)
                if y is None:
                    continue
                lines.append(f"{s.name()} = {y:.6g}")

        return "\n".join(lines).strip()

    def _ref_series_for_mapping(self):
        if self.left_series:
            return self.left_series[0]
        if self.right_series:
            return self.right_series[0]
        if self.alarm_series:
            return self.alarm_series
        return None

    def clear(self):
        try:
            self.view.tip_cross.hide_tip()
            self.view.tip_alarm.hide_tip()
            self.view.tip_cross.set_target_hovering(False)
            self.view.tip_alarm.set_target_hovering(False)
        except Exception:
            pass

        try:
            self.view.show_alarm_halo_at(self.alarm_series, QPointF(), False)
        except Exception:
            pass

        try:
            if self.alarm_series is not None:
                try:
                    self.alarm_series.hovered.disconnect(self.on_alarm_hovered)
                except Exception:
                    pass
        except Exception:
            pass

        self.chart.removeAllSeries()
        self.left_series.clear()
        self.right_series.clear()

        for ax in (self.axis_x_dt, self.axis_x_num, self.axis_y_left, self.axis_y_right):
            if ax is not None:
                try:
                    self.chart.removeAxis(ax)
                except Exception:
                    pass

        self.axis_x_dt = None
        self.axis_x_num = None
        self.axis_y_left = None
        self.axis_y_right = None

        self.alarm_series = None
        self._alarm_map.clear()
        self._alarm_hovering = False

        self.titlebar.setText("—")

        self.compare_active = False
        self.compare_full_xmin = None
        self.compare_full_xmax = None
        self.compare_x_mode = ""

        self.ref_x_on = False
        self.ref_y_on = False
        self.ref_x_value = 0.0
        self.ref_y_value = 0.0
        self.ref_y_side = "left"
        try:
            self.view._ref_vline.setVisible(False)
            self.view._ref_hline.setVisible(False)
        except Exception:
            pass

    def _alarm_text_from_key(self, key_ms: int) -> str:
        infos = self._alarm_map.get(key_ms)
        if not infos:
            return ""

        time_str = infos[0][0]
        step_no = infos[0][2] or ""
        step_name = infos[0][3] or ""

        lines = [time_str]
        if step_no or step_name:
            if step_no and step_name:
                lines.append(f"Step: {step_no} | {step_name}")
            elif step_no:
                lines.append(f"Step: {step_no}")
            else:
                lines.append(f"Step: {step_name}")

        if len(infos) > 1:
            lines.append(f"Alarms: {len(infos)}")
        for i, (_, txt, _, _) in enumerate(infos, start=1):
            t = (txt or "").strip()
            if not t:
                continue
            prefix = f"{i}. " if len(infos) > 1 else ""
            lines.append(prefix + t)

        return "\n".join(lines).strip()

    def on_alarm_hovered(self, point: QPointF, state: bool):
        if self.alarm_series is None:
            return

        self._alarm_hovering = bool(state)
        self.view.show_alarm_halo_at(self.alarm_series, point, state)

        if state:
            self.view.tip_cross.hide_tip()

            key = int(round(point.x()))
            msg = self._alarm_text_from_key(key)
            if msg:
                try:
                    pos_scene = self.view.chart().mapToPosition(point, self.alarm_series)
                    pos_view = self.view.mapFromScene(pos_scene.toPoint())
                except Exception:
                    pos_view = QPoint(self.view.width() // 2, self.view.height() // 2)

                self.view.tip_alarm.set_target_hovering(True)
                self.view.tip_alarm.show_text_at(msg, pos_view, offset=QPoint(18, -10))
            else:
                self.view.tip_alarm.hide_tip()
                self.view.tip_alarm.set_target_hovering(False)
        else:
            self.view.tip_alarm.set_target_hovering(False)


class HistoryChartView(QChartView):
    def __init__(self, chart: QChart, parent=None):
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setMouseTracking(True)
        self.tip = StickyTip(self, kind="cross")

    def hide_tip(self):
        self.tip.hide_tip()
        self.tip.set_target_hovering(False)

    def leaveEvent(self, e):
        self.hide_tip()
        super().leaveEvent(e)


class HistoryDialog(QDialog):
    """
    날짜별 공정 횟수 표시
    - 최초 오픈 시 최근 7일만 스캔
    - 날짜 범위 변경 시 해당 기간만 다시 스캔
    """
    def __init__(self, history_dir: Path, parent=None):
        super().__init__(parent)

        self.setWindowFlag(Qt.Window, True)
        self.setWindowFlag(Qt.WindowMinMaxButtonsHint, True)
        self.setWindowFlag(Qt.WindowCloseButtonHint, True)

        self.setWindowTitle("History (Runs per day)")
        self.resize(980, 560)

        self.history_dir = Path(history_dir)
        self.df_logs = pd.DataFrame()

        self.group_by = MenuSelectButton("Group")
        self.group_by.addItem("Total")
        self.group_by.addItem("tube")
        self.group_by.addItem("recipe")
        self.group_by.setCurrentText("Total")
        self.group_by.selectionChanged.connect(lambda _: self._rebuild_chart())

        self.btn_export = QPushButton("Export raw data (Excel)")
        self.btn_export.setCursor(Qt.PointingHandCursor)

        today = QDate.currentDate()
        default_from = today.addDays(-6)

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        self.date_from.setDate(default_from)

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        self.date_to.setDate(today)

        self.btn_date_range = QPushButton("Select date range")
        self.btn_date_range.setCursor(Qt.PointingHandCursor)
        self.btn_date_range.clicked.connect(self._open_date_range_dialog)

        self.sort_mode = MenuSelectButton("Sort")
        self.sort_mode.addItems(["Date asc", "Count desc"])
        self.sort_mode.setCurrentText("Date asc")
        self.sort_mode.selectionChanged.connect(lambda _: self._rebuild_chart())

        self.abort_only_cb = QCheckBox("Abort only")
        self.abort_only_cb.toggled.connect(lambda _: self._rebuild_chart())

        if not self.df_logs.empty and "date_dt" in self.df_logs.columns:
            dmax = pd.to_datetime(self.df_logs["date_dt"].max()).date()
            qmax = QDate(dmax.year, dmax.month, dmax.day)

            self.date_to.setDate(qmax if qmax <= today else today)

            dmin_limit = self.date_to.date().addDays(-6)
            self.date_from.setDate(dmin_limit)
        else:
            self.date_from.setDate(default_from)
            self.date_to.setDate(today)

        # self.date_from.dateChanged.connect(self._rebuild_chart)
        # self.date_to.dateChanged.connect(self._rebuild_chart)

        apply_chrome_input_styles(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Group by:"), 0)
        top.addWidget(self.group_by, 0)
        top.addSpacing(12)
        top.addWidget(QLabel("Period"), 0)
        top.addWidget(self.btn_date_range, 0)
        top.addStretch(1)
        top.addWidget(self.btn_export, 0)
        top.addSpacing(12)
        top.addWidget(QLabel("Sort"), 0)
        top.addWidget(self.sort_mode, 0)
        top.addSpacing(12)
        top.addWidget(self.abort_only_cb, 0)

        self.chart = QChart()
        self.chart.legend().setVisible(True)
        self.chart.legend().setAlignment(Qt.AlignBottom)
        self.chart.setBackgroundVisible(False)
        self.chart.setPlotAreaBackgroundVisible(False)

        self.view = HistoryChartView(self.chart)
        self.view.setStyleSheet("background:white; border:1px solid #dde3ec; border-radius:16px;")
        apply_shadow(self.view, blur=24, y_offset=4)

        self.raw_table = QTableWidget()
        self.raw_table.setColumnCount(0)
        self.raw_table.setRowCount(0)
        self.raw_table.setAlternatingRowColors(True)
        self.raw_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.raw_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.raw_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.raw_table.setWordWrap(False)
        self.raw_table.verticalHeader().setVisible(False)
        self.raw_table.horizontalHeader().setStretchLastSection(False)
        self.raw_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.raw_table.setStyleSheet("""
        QTableWidget {
            background: white;
            border: 1px solid #dde3ec;
            border-radius: 16px;
            gridline-color: #edf1f5;
            alternate-background-color: #f8fafc;
        }
        QHeaderView::section {
            background: #f8fafc;
            border: none;
            border-bottom: 1px solid #e5e7eb;
            padding: 8px;
            font-weight: 600;
            color: #6b7280;
        }
        QTableWidget::item {
            padding: 6px;
        }
        QTableWidget::item:selected {
            background: #eaf2ff;
            color: #111827;
        }
        """)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.view, 3)
        lay.addWidget(self.raw_table, 2)

        self._series: QBarSeries | None = None
        self._axis_x: QBarCategoryAxis | None = None
        self._axis_y: QValueAxis | None = None

        self._refresh_date_range_button()
        self.reload_data()

    def _refresh_raw_table(self):
        df = self.filtered_logs().copy()

        if df is None or df.empty:
            self.raw_table.clear()
            self.raw_table.setRowCount(0)
            self.raw_table.setColumnCount(0)
            return

        preferred_front = ["date", "time", "tube", "job_id", "recipe", "recipe_from_filename"]
        preferred_back = ["is_abort", "abort_flag", "path", "dt", "date_dt"]

        cols_front = [c for c in preferred_front if c in df.columns]
        cols_back = [c for c in preferred_back if c in df.columns]
        cols_mid = [c for c in df.columns if c not in cols_front + cols_back]

        cols = cols_front + cols_mid + cols_back
        df = df.loc[:, cols].copy()

        df = df.fillna("")

        self.raw_table.clear()
        self.raw_table.setColumnCount(len(cols))
        self.raw_table.setRowCount(len(df))
        self.raw_table.setHorizontalHeaderLabels([str(c) for c in cols])

        for r in range(len(df)):
            row = df.iloc[r]
            for c, col_name in enumerate(cols):
                val = row[col_name]
                text = "" if pd.isna(val) else str(val)
                item = QTableWidgetItem(text)
                self.raw_table.setItem(r, c, item)

        self.raw_table.resizeColumnsToContents()

        hdr = self.raw_table.horizontalHeader()
        for i, col_name in enumerate(cols):
            if col_name in ("path", "recipe_from_filename", "recipe"):
                hdr.setSectionResizeMode(i, QHeaderView.Stretch)

    def reload_data(self):
        start, end = self._selected_date_range()

        self.df_logs = scan_history_logs(
            self.history_dir,
            start_date=start,
            end_date=end,
        )

        self._rebuild_group_candidates()
        self._rebuild_chart()
        self._refresh_raw_table()

    def _rebuild_group_candidates(self):
        current = self.group_by.currentText() if hasattr(self, "group_by") else "Total"

        self.group_by.blockSignals(True)
        try:
            self.group_by.clear()
            self.group_by.addItem("Total")
            self.group_by.addItem("tube")
            self.group_by.addItem("recipe")

            if not self.df_logs.empty:
                reserved = {
                    "path", "tube", "recipe", "recipe_from_filename", "job_id",
                    "date", "time", "dt", "date_dt", "is_abort", "abort_flag"
                }

                extra_group_candidates = []
                for c in self.df_logs.columns:
                    if c in reserved:
                        continue
                    s = self.df_logs[c].astype(str).str.strip()
                    if (s != "").any():
                        extra_group_candidates.append(c)

                for c in extra_group_candidates:
                    self.group_by.addItem(c)

            if current and current in self.group_by._items:
                self.group_by.setCurrentText(current)
            else:
                self.group_by.setCurrentText("Total")
        finally:
            self.group_by.blockSignals(False)

    def _refresh_date_range_button(self):
        s = self.date_from.date().toString("yyyy-MM-dd")
        e = self.date_to.date().toString("yyyy-MM-dd")
        self.btn_date_range.setText(f"{s}  ~  {e}")

    def _open_date_range_dialog(self):
        dlg = DateRangeDialog(self)
        dlg.set_values(self.date_from.date(), self.date_to.date())

        if dlg.exec() != QDialog.Accepted:
            return

        d0, d1 = dlg.values()
        if d0 > d1:
            d0, d1 = d1, d0

        self.date_from.setDate(d0)
        self.date_to.setDate(d1)
        self._refresh_date_range_button()
        self.reload_data()

    def _selected_date_range(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        d0 = self.date_from.date()
        d1 = self.date_to.date()

        if d0 > d1:
            d0, d1 = d1, d0

        start = pd.Timestamp(year=d0.year(), month=d0.month(), day=d0.day())
        end = pd.Timestamp(year=d1.year(), month=d1.month(), day=d1.day())
        return start, end

    def filtered_logs(self) -> pd.DataFrame:
        if self.df_logs.empty or "date_dt" not in self.df_logs.columns:
            return self.df_logs

        start, end = self._selected_date_range()
        df = self.df_logs.copy()
        df = df[df["date_dt"].between(start, end, inclusive="both")].copy()

        if self.abort_only_cb.isChecked() and "is_abort" in df.columns:
            df = df[df["is_abort"] == True].copy()

        return df
    def _unique_colors(self):
        base = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
        i = 0
        while True:
            if i < len(base):
                yield base[i]
            else:
                k = i - len(base)
                hue = (k * 0.61803398875) % 1.0
                yield QColor.fromHsvF(hue, 0.55, 0.85).name()
            i += 1

    def _make_pivot(self) -> tuple[list[str], list[str], dict[str, list[int]]]:
        if self.df_logs.empty:
            return [], [], {}

        df = self.filtered_logs().copy()
        if df.empty:
            return [], [], {}

        df["date_label"] = df["date_dt"].dt.strftime("%Y-%m-%d")

        mode = self.group_by.currentText()
        self.chart.setTitle(f"Runs per day (group by: {mode})")

        if mode == "Total":
            grp = df.groupby("date_label").size().reset_index(name="cnt")
            if self.sort_mode.currentText() == "Count desc":
                grp = grp.sort_values(["cnt", "date_label"], ascending=[False, True])

            dates = grp["date_label"].tolist()
            data = {"Total": grp["cnt"].astype(int).tolist()}
            return dates, ["Total"], data

        gcol = mode
        if gcol not in df.columns:
            return [], [], {}

        df[gcol] = df[gcol].astype(str).fillna("").str.strip()
        df.loc[df[gcol] == "", gcol] = "(blank)"

        pivot = (
            df.pivot_table(
                index="date_label",
                columns=gcol,
                values="path",
                aggfunc="count",
                fill_value=0
            )
            .sort_index()
        )

        if pivot.empty:
            return [], [], {}

        if self.sort_mode.currentText() == "Count desc":
            col_order = pivot.sum(axis=0).sort_values(ascending=False).index.tolist()
            pivot = pivot[col_order]

        dates = pivot.index.tolist()
        groups = list(pivot.columns.astype(str))
        data = {g: pivot[g].astype(int).tolist() for g in groups}
        return dates, groups, data

    def _rebuild_chart(self):
        self.chart.removeAllSeries()
        for ax in list(self.chart.axes()):
            try:
                self.chart.removeAxis(ax)
            except Exception:
                pass

        dates, groups, data = self._make_pivot()
        if not dates:
            self.chart.setTitle("No logs found")
            return

        mode = self.group_by.currentText()

        if mode == "Total":
            series = QBarSeries()
        else:
            series = QStackedBarSeries()

        colors = self._unique_colors()

        for g in groups:
            bs = QBarSet(str(g))
            c = QColor(next(colors))
            bs.setBrush(QBrush(c))
            bs.setColor(c)
            bs.append([int(v) for v in data[g]])

            def make_hover_handler(barset: QBarSet):
                def _on_hovered(status: bool, index: int):
                    if not status:
                        self.view.hide_tip()
                        return
                    if index < 0 or index >= len(dates):
                        self.view.hide_tip()
                        return

                    date_label = dates[index]
                    v = float(barset.at(index))
                    cnt = int(v)

                    y_mid = v
                    if isinstance(series, QStackedBarSeries):
                        below = 0.0
                        for bs2 in series.barSets():
                            if bs2 is barset:
                                break
                            below += float(bs2.at(index))
                        y_mid = below + v / 2.0

                    msg = f"{date_label}\n{barset.label()} : {cnt}"

                    try:
                        pos_scene = self.chart.mapToPosition(QPointF(float(index), float(y_mid)), series)
                        pos_view = self.view.mapFromScene(pos_scene.toPoint())
                    except Exception:
                        pos_view = QPoint(self.view.width() // 2, self.view.height() // 2)

                    self.view.tip.set_target_hovering(True)
                    self.view.tip.show_text_at(msg, pos_view, offset=QPoint(16, -10))

                return _on_hovered

            bs.hovered.connect(make_hover_handler(bs))
            series.append(bs)

        axis_x = QBarCategoryAxis()
        axis_x.append([str(d) for d in dates])

        axis_y = QValueAxis()
        axis_y.setLabelFormat("%d")
        axis_y.setTickCount(6)
        axis_y.setMin(0)

        if isinstance(series, QStackedBarSeries) and groups:
            totals = [0] * len(dates)
            for g in groups:
                vals = data.get(g, [])
                for i, v in enumerate(vals):
                    if i < len(totals):
                        totals[i] += int(v)
            maxv = max(totals) if totals else 0
        else:
            maxv = 0
            for g in groups:
                vals = data.get(g, [])
                if vals:
                    maxv = max(maxv, max(vals))

        axis_y.setMax(max(1, int(maxv * 1.15) if maxv > 0 else 1))

        self.chart.addSeries(series)
        self.chart.addAxis(axis_x, Qt.AlignBottom)
        self.chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_x)
        series.attachAxis(axis_y)

        self._series = series
        self._axis_x = axis_x
        self._axis_y = axis_y

        abort_tag = " | Abort only" if self.abort_only_cb.isChecked() else ""
        self.chart.setTitle(f"Runs per day (group by: {mode}{abort_tag})")


class CompareDialog(QDialog):
    def __init__(self, common_cols: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compare Settings")

        self.x_mode = MenuSelectButton("X axis")
        self.x_mode.addItems(["Elapsed seconds (Δt)", "Index"])
        self.x_mode.setCurrentText("Elapsed seconds (Δt)")

        self.step_no_edit = QLineEdit()
        self.step_no_edit.setPlaceholderText("e.g. 12 (blank = all steps)")

        self.y_pick = FilterableList(common_cols, placeholder="Search compare Y columns...", parent=self)

        hint = QLabel("Select up to 3 Y columns to compare.")
        hint.setStyleSheet("color:#6b7280;")

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("X axis", self.x_mode)
        form.addRow("Step No", self.step_no_edit)
        lay.addLayout(form)

        lay.addWidget(self.y_pick, 1)
        lay.addWidget(hint)
        lay.addWidget(btns)

        apply_chrome_input_styles(self)

    def _on_accept(self):
        if not self.y_pick.selected_texts():
            QMessageBox.information(self, "Compare", "비교할 Y 컬럼을 최소 1개 선택해줘.")
            return
        self.accept()

    def values(self):
        cols = self.y_pick.selected_texts()[:3]
        step_txt = (self.step_no_edit.text() or "").strip()
        return self.x_mode.currentText(), cols, step_txt


class TreeFilterProxyModel(QSortFilterProxyModel):
    """
    QFileSystemModel용 트리 검색/필터
    - 파일명/경로 기준으로 필터
    - 디렉터리는 하위에 매칭 항목이 있으면 표시
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter_text = ""

    def setFilterText(self, text: str):
        self._filter_text = (text or "").strip().lower()
        self.invalidate()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._filter_text:
            return True

        model = self.sourceModel()
        idx = model.index(source_row, 0, source_parent)
        if not idx.isValid():
            return False

        name = str(model.fileName(idx)).lower()
        path = str(model.filePath(idx)).lower()

        if self._filter_text in name or self._filter_text in path:
            return True

        if model.isDir(idx):
            for i in range(model.rowCount(idx)):
                if self.filterAcceptsRow(i, idx):
                    return True

        return False


# =========================================================
# Main Panel
# =========================================================
class CsvPlotPanel(QWidget):
    compareRequested = Signal(object)
    historyRequested = Signal(object)
    exportRequested = Signal(object)

    def __init__(self, alarm_dir: Path, history_dir: Path | None = None, parent=None):
        super().__init__(parent)

        self.root_dir: Path | None = None
        self.history_dir: Path | None = Path(history_dir).resolve() if history_dir else None

        self._history_df_cache: pd.DataFrame | None = None
        self._history_cache_root: Path | None = None
        self._history_cache_history_dir: Path | None = None

        self.alarm_dir = Path(alarm_dir)

        self.df: pd.DataFrame | None = None
        self.csv_path: Path | None = None
        self.x_col: str | None = None
        self.x_is_datetime: bool = False

        self._y_candidates: list[str] = []
        self._step_no_col: str | None = None
        self._step_name_col: str | None = None

        self._areas: list[PlotArea] = []
        self._last_cols: int | None = None

        self._min_zoom_span: float = 0.0
        self._full_x_min_dt: QDateTime | None = None
        self._full_x_max_dt: QDateTime | None = None
        self._full_x_min_num: float | None = None
        self._full_x_max_num: float | None = None

        self._mode: str = "single"

        self._compare_active: bool = False
        self._compare_full_xmin: float | None = None
        self._compare_full_xmax: float | None = None

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 10, 0)
        root.setSpacing(0)

        toolbar_btn_css = (
            "QPushButton{"
            "background:white;"
            "border:1px solid #d9dee7;"
            "border-radius:10px;"
            "padding:8px 14px;"
            "font-weight:600;"
            "}"
            "QPushButton:hover{background:#f8fafc;}"
            "QPushButton:pressed{background:#eef2f7;}"
            "QPushButton:disabled{background:#f3f4f6; color:#9ca3af; border:1px solid #e5e7eb;}"
        )
        accent_btn_css = (
            "QPushButton{"
            "background:#0f6cbd;"
            "color:white;"
            "border:1px solid #0f6cbd;"
            "border-radius:10px;"
            "padding:8px 14px;"
            "font-weight:700;"
            "}"
            "QPushButton:hover{background:#115ea3;}"
            "QPushButton:pressed{background:#0f548c;}"
            "QPushButton:disabled{background:#9ca3af; border:1px solid #9ca3af; color:white;}"
        )

        title_row = QHBoxLayout()
        title_row.setContentsMargins(10, 2, 2, 6)
        title_row.setSpacing(8)

        self.title = QLabel("Choose CSV File to Plot")
        self.title.setStyleSheet("font-weight:700; font-size:14px; color:#111827;")
        title_row.addWidget(self.title, 1)

        self.btn_compare = QPushButton("Compare various logs")
        self.btn_compare.setEnabled(True)
        self.btn_compare.setVisible(False)
        title_row.addWidget(self.btn_compare, 0)

        self.btn_history = QPushButton("History")
        self.btn_history.setEnabled(True)
        self.btn_history.setToolTip("Show runs per day (by date)")
        self.btn_history.clicked.connect(self.show_history_dialog)
        self.btn_history.setStyleSheet(toolbar_btn_css)
        apply_shadow(self.btn_history, blur=18, y_offset=3)
        title_row.addWidget(self.btn_history, 0)

        self.btn_reset_zoom = QPushButton("Reset Zoom")
        self.btn_reset_zoom.setEnabled(False)
        self.btn_reset_zoom.setToolTip("Reset Zoom (X Axis) to full scale")
        self.btn_reset_zoom.clicked.connect(self.reset_zoom)
        self.btn_reset_zoom.setStyleSheet(toolbar_btn_css)
        apply_shadow(self.btn_reset_zoom, blur=18, y_offset=3)
        title_row.addWidget(self.btn_reset_zoom, 0)

        self.btn_add_graph = QPushButton("+ Graph")
        self.btn_add_graph.setEnabled(False)
        self.btn_add_graph.setToolTip("Add one more graph")
        self.btn_add_graph.clicked.connect(self.add_graph)
        self.btn_add_graph.setStyleSheet(accent_btn_css)
        apply_shadow(self.btn_add_graph, blur=20, y_offset=4)
        title_row.addWidget(self.btn_add_graph, 0)

        root.addLayout(title_row)

        self.ctrl_widget = QWidget()
        ctrl_layout = QVBoxLayout(self.ctrl_widget)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("Y(Left):"))
        self.y_combos = [MenuSelectButton("Select Y"), MenuSelectButton("Select Y"), MenuSelectButton("Select Y")]
        for cb in self.y_combos:
            cb.setEnabled(False)
            cb.setMinimumWidth(160)
            top.addWidget(cb, 1)

        self.btn_clear_left = QPushButton("Clear L")
        self.btn_clear_left.setEnabled(False)
        self.btn_clear_left.clicked.connect(self.clear_left)
        top.addWidget(self.btn_clear_left)

        self.plot_btn = QPushButton("Plot")
        self.plot_btn.setEnabled(False)
        self.plot_btn.clicked.connect(self.plot)
        top.addWidget(self.plot_btn)
        ctrl_layout.addLayout(top)

        top2 = QHBoxLayout()
        top2.addWidget(QLabel("Y2(Right):"))
        self.y2_combos = [MenuSelectButton("Select Y2"), MenuSelectButton("Select Y2"), MenuSelectButton("Select Y2")]
        for cb in self.y2_combos:
            cb.setEnabled(False)
            cb.setMinimumWidth(160)
            top2.addWidget(cb, 1)

        self.btn_clear_right = QPushButton("Clear R")
        self.btn_clear_right.setEnabled(False)
        self.btn_clear_right.clicked.connect(self.clear_right)
        top2.addWidget(self.btn_clear_right)
        ctrl_layout.addLayout(top2)

        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("X range:"), 0)
        self.range_stack = QStackedWidget()

        self.dt_widget = QWidget()
        dt_layout = QHBoxLayout(self.dt_widget)
        dt_layout.setContentsMargins(0, 0, 0, 0)

        self.dt_start = QDateTimeEdit()
        self.dt_start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_start.setCalendarPopup(True)

        self.dt_end = QDateTimeEdit()
        self.dt_end.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_end.setCalendarPopup(True)

        self.btn_dt_range = QPushButton("Select datetime range")
        self.btn_dt_range.setCursor(Qt.PointingHandCursor)
        self.btn_dt_range.clicked.connect(self._open_x_range_dialog)

        dt_layout.addWidget(self.btn_dt_range, 1)

        self.num_widget = QWidget()
        num_layout = QHBoxLayout(self.num_widget)
        num_layout.setContentsMargins(0, 0, 0, 0)

        self.num_start = QDoubleSpinBox()
        self.num_start.setDecimals(6)
        self.num_start.setRange(-1e30, 1e30)

        self.num_end = QDoubleSpinBox()
        self.num_end.setDecimals(6)
        self.num_end.setRange(-1e30, 1e30)

        self.btn_num_range = QPushButton("Select numeric range")
        self.btn_num_range.setCursor(Qt.PointingHandCursor)
        self.btn_num_range.clicked.connect(self._open_x_range_dialog)

        num_layout.addWidget(self.btn_num_range, 1)

        self.range_stack.addWidget(self.dt_widget)
        self.range_stack.addWidget(self.num_widget)
        range_row.addWidget(self.range_stack, 1)
        ctrl_layout.addLayout(range_row)

        yscale_row = QHBoxLayout()
        yscale_row.addWidget(QLabel("Left Y Scale:"), 0)
        self.left_scale_mode = MenuSelectButton("Left mode")
        self.left_scale_mode.addItems(["Auto", "Manual"])
        self.left_scale_mode.setCurrentText("Auto")
        self.left_scale_mode.setEnabled(False)
        yscale_row.addWidget(self.left_scale_mode, 0)

        self.left_ymin = QDoubleSpinBox()
        self.left_ymin.setDecimals(6)
        self.left_ymin.setRange(-1e30, 1e30)
        self.left_ymin.setEnabled(False)
        yscale_row.addWidget(QLabel("Min"))
        yscale_row.addWidget(self.left_ymin, 1)

        self.left_ymax = QDoubleSpinBox()
        self.left_ymax.setDecimals(6)
        self.left_ymax.setRange(-1e30, 1e30)
        self.left_ymax.setEnabled(False)
        yscale_row.addWidget(QLabel("Max"))
        yscale_row.addWidget(self.left_ymax, 1)

        self.left_log = QCheckBox("Log")
        self.left_log.setEnabled(False)
        yscale_row.addWidget(self.left_log, 0)

        self.btn_reset_left_scale = QPushButton("Reset L")
        self.btn_reset_left_scale.setEnabled(False)
        self.btn_reset_left_scale.clicked.connect(self.reset_left_scale)
        yscale_row.addWidget(self.btn_reset_left_scale, 0)
        ctrl_layout.addLayout(yscale_row)

        y2scale_row = QHBoxLayout()
        y2scale_row.addWidget(QLabel("Right Y2 Scale:"), 0)
        self.right_scale_mode = MenuSelectButton("Right mode")
        self.right_scale_mode.addItems(["Auto", "Manual"])
        self.right_scale_mode.setCurrentText("Auto")
        self.right_scale_mode.setEnabled(False)
        y2scale_row.addWidget(self.right_scale_mode, 0)

        self.right_ymin = QDoubleSpinBox()
        self.right_ymin.setDecimals(6)
        self.right_ymin.setRange(-1e30, 1e30)
        self.right_ymin.setEnabled(False)
        y2scale_row.addWidget(QLabel("Min"))
        y2scale_row.addWidget(self.right_ymin, 1)

        self.right_ymax = QDoubleSpinBox()
        self.right_ymax.setDecimals(6)
        self.right_ymax.setRange(-1e30, 1e30)
        self.right_ymax.setEnabled(False)
        y2scale_row.addWidget(QLabel("Max"))
        y2scale_row.addWidget(self.right_ymax, 1)

        self.right_log = QCheckBox("Log")
        self.right_log.setEnabled(False)
        y2scale_row.addWidget(self.right_log, 0)

        self.btn_reset_right_scale = QPushButton("Reset R")
        self.btn_reset_right_scale.setEnabled(False)
        self.btn_reset_right_scale.clicked.connect(self.reset_right_scale)
        y2scale_row.addWidget(self.btn_reset_right_scale, 0)
        ctrl_layout.addLayout(y2scale_row)

        self.left_scale_mode.selectionChanged.connect(lambda _: self._update_scale_enable_state())
        self.right_scale_mode.selectionChanged.connect(lambda _: self._update_scale_enable_state())

        root.addWidget(self.ctrl_widget)
        self.ctrl_widget.setVisible(False)

        self.plot_container = QWidget()
        self.plot_container.setStyleSheet("background: transparent;")
        self.plot_container_layout = QVBoxLayout(self.plot_container)
        self.plot_container_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_container_layout.setSpacing(0)

        self.plot_grid = QWidget()
        self.plot_grid.setStyleSheet("background: transparent;")
        self.plot_grid_layout = QGridLayout(self.plot_grid)
        self.plot_grid_layout.setContentsMargins(10, 10, 10, 10)
        self.plot_grid_layout.setHorizontalSpacing(12)
        self.plot_grid_layout.setVerticalSpacing(12)

        self.plot_container_layout.addWidget(self.plot_grid, 1)
        root.addWidget(self.plot_container, 1)

        a0 = PlotArea(self)
        self._areas.append(a0)
        self._rebuild_plot_layout(force=True)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#6b7280; padding:4px 2px 2px 2px;")
        root.addWidget(self.status)

        apply_chrome_input_styles(self)

    def request_history_for_area(self, area: PlotArea):
        self.historyRequested.emit(area)

    def request_export_for_area(self, area: PlotArea):
        self.exportRequested.emit(area)

    def request_compare_for_area(self, area: PlotArea):
        self.compareRequested.emit(area)

    def _get_history_df(self, force: bool = False) -> pd.DataFrame:
        hd = self.history_dir
        if hd is None:
            return pd.DataFrame()

        if (
                (not force)
                and self._history_df_cache is not None
                and self._history_cache_history_dir == hd
        ):
            return self._history_df_cache

        dfh = scan_history_logs(hd)
        self._history_df_cache = dfh
        self._history_cache_history_dir = hd
        return dfh

    def show_history_dialog(self, area: PlotArea | None = None):
        if self.history_dir is None or not Path(self.history_dir).exists():
            QMessageBox.information(self, "History", "history_dir is not set or does not exist.")
            return

        dlg = HistoryDialog(Path(self.history_dir), parent=self)

        def _export_from_dialog():
            self.export_history_to_excel(dlg.filtered_logs())

        dlg.btn_export.clicked.connect(_export_from_dialog)
        dlg.exec()

    def export_history_to_excel(self, dfh: pd.DataFrame | None = None):
        if dfh is None:
            dfh = self._get_history_df()
        if dfh is None or dfh.empty:
            QMessageBox.information(self, "Export", "No history data to export.")
            return

        default_name = "Process_history_data.xlsx"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Excel",
            str((self.history_dir or self.root_dir or Path.cwd()) / default_name),
            "Excel Files (*.xlsx)"
        )
        if not save_path:
            return

        df = dfh.copy()
        df["date_label"] = df["date_dt"].dt.strftime("%Y-%m-%d")

        daily_total = (
            df.groupby("date_label")
            .agg(
                runs=("path", "count"),
                abort_runs=("abort_flag", "sum"),
            )
            .reset_index()
            .sort_values("date_label")
        )

        daily_by_tube = (
            df.pivot_table(index="date_label", columns="tube", values="path", aggfunc="count", fill_value=0)
            .reset_index()
        )

        daily_by_recipe = (
            df.pivot_table(index="date_label", columns="recipe", values="path", aggfunc="count", fill_value=0)
            .reset_index()
        )

        abort_by_tube = (
            df.pivot_table(index="date_label", columns="tube", values="abort_flag", aggfunc="sum", fill_value=0)
            .reset_index()
        )

        abort_by_recipe = (
            df.pivot_table(index="date_label", columns="recipe", values="abort_flag", aggfunc="sum", fill_value=0)
            .reset_index()
        )

        try:
            with pd.ExcelWriter(save_path, engine="openpyxl") as w:
                base_front = ["path", "tube", "recipe", "recipe_from_filename"]
                base_back = ["job_id", "date", "time", "dt", "is_abort", "abort_flag"]

                meta_dynamic = [
                    c for c in df.columns
                    if c not in (base_front + base_back + ["date_label", "date_dt"])
                ]

                out_cols = base_front + meta_dynamic + base_back

                for c in out_cols:
                    if c not in df.columns:
                        df[c] = ""

                df[out_cols].to_excel(w, index=False, sheet_name="raw_files")
                daily_total.to_excel(w, index=False, sheet_name="daily_total")
                daily_by_tube.to_excel(w, index=False, sheet_name="daily_by_tube")
                daily_by_recipe.to_excel(w, index=False, sheet_name="daily_by_recipe")
                abort_by_tube.to_excel(w, index=False, sheet_name="abort_by_tube")
                abort_by_recipe.to_excel(w, index=False, sheet_name="abort_by_recipe")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return

        QMessageBox.information(self, "Export", f"Saved:\n{save_path}")

    @staticmethod
    def _short_compare_label_from_filename(filename: str) -> str:
        stem = Path(filename).stem
        parts = stem.split("_")
        if len(parts) >= 5:
            parts = parts[:-2]
        return "_".join(parts) if parts else stem

    @staticmethod
    def _detect_step_no_column_in_df(df: pd.DataFrame) -> str | None:
        cols = list(df.columns)
        lower_map = {c: str(c).strip().lower() for c in cols}
        candidates = [
            "step no", "stepno", "step number", "stepnumber",
            "step_no", "step-no", "step",
        ]

        for c in cols:
            if lower_map[c] in candidates:
                return c

        norm_map = {c: re.sub(r"[^a-z0-9]", "", lower_map[c]) for c in cols}
        cand_norm = [re.sub(r"[^a-z0-9]", "", s) for s in candidates]

        for c in cols:
            for cn in cand_norm:
                if cn and cn == norm_map[c]:
                    return c

        for c in cols:
            v = norm_map[c]
            for cn in cand_norm:
                if cn and cn in v:
                    return c

        return None

    @staticmethod
    def _detect_step_name_column_in_df(df: pd.DataFrame) -> str | None:
        cols = list(df.columns)
        lower_map = {c: str(c).strip().lower() for c in cols}
        candidates = [
            "step name", "stepname", "step desc", "stepdesc", "step description",
            "recipe step name", "recipestepname",
        ]

        for c in cols:
            if lower_map[c] in candidates:
                return c

        norm_map = {c: re.sub(r"[^a-z0-9]", "", lower_map[c]) for c in cols}
        cand_norm = [re.sub(r"[^a-z0-9]", "", s) for s in candidates]

        for c in cols:
            for cn in cand_norm:
                if cn and cn == norm_map[c]:
                    return c

        for c in cols:
            v = norm_map[c]
            for cn in cand_norm:
                if cn and cn in v:
                    return c

        return None

    @staticmethod
    def _filter_df_by_step_no(df: pd.DataFrame, step_col: str, step_txt: str) -> pd.DataFrame:
        if not step_txt:
            return df

        s = df[step_col]
        try:
            target = int(step_txt)
            sn = pd.to_numeric(s, errors="coerce")
            m = sn.notna() & (sn.astype("int64") == target)
            return df.loc[m].copy()
        except Exception:
            m = s.astype(str).str.strip() == step_txt
            return df.loc[m].copy()

    def _common_numeric_columns(self, paths: list[Path]) -> list[str]:
        common: set[str] | None = None
        for p in paths:
            try:
                df = pd.read_csv(p, low_memory=False, nrows=200)
                df = self._normalize_columns(df)
            except Exception:
                continue

            cols = []
            for c in df.columns[1:]:
                s = pd.to_numeric(df[c], errors="coerce")
                if s.notna().any():
                    cols.append(c)

            if common is None:
                common = set(cols)
            else:
                common &= set(cols)

        return sorted(common) if common else []

    def compare_files(self, paths: list[Path], target_area: PlotArea | None = None):
        if not paths:
            return

        self._mode = "compare"

        common_cols = self._common_numeric_columns(paths)
        if not common_cols:
            QMessageBox.information(self, "Compare", "Can't find common numeric columns.")
            return

        dlg = CompareDialog(common_cols, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        x_mode, y_cols, step_txt = dlg.values()
        if not y_cols:
            QMessageBox.information(self, "Compare", "Select Y columns to compare.")
            return

        if not self._areas:
            a0 = PlotArea(self)
            self._areas.append(a0)
            self._rebuild_plot_layout(force=True)

        area = target_area if target_area is not None else self._areas[0]
        if area not in self._areas:
            area = self._areas[0]
        area.clear()

        area.compare_x_mode = x_mode

        chart = area.chart
        chart.legend().setVisible(True)

        ax_x = QValueAxis()
        ax_x.setTickCount(6)
        ax_x.setTitleText("Elapsed (s)" if "Elapsed" in x_mode else "Index")
        area.axis_x_num = ax_x
        chart.addAxis(ax_x, Qt.AlignBottom)

        ax_l = QValueAxis()
        ax_l.setTickCount(6)
        area.axis_y_left = ax_l
        chart.addAxis(ax_l, Qt.AlignLeft)

        used_colors = set()
        color_gen = self._unique_color_generator()

        def pick_color():
            while True:
                c = next(color_gen)
                if c not in used_colors:
                    used_colors.add(c)
                    return QColor(c)

        global_xmin, global_xmax = None, None
        global_ymin, global_ymax = None, None

        plotted_any = False
        step_names: set[str] = set()

        for p in paths:
            try:
                df = pd.read_csv(p, low_memory=False)
                if df.empty:
                    continue
                df = self._normalize_columns(df)
            except Exception:
                continue

            step_col = self._detect_step_no_column_in_df(df)
            step_name_col = self._detect_step_name_column_in_df(df)

            if step_txt:
                if not step_col or step_col not in df.columns:
                    continue
                df = self._filter_df_by_step_no(df, step_col, step_txt)
                if df.empty:
                    continue

                if step_name_col and step_name_col in df.columns:
                    sn_series = df[step_name_col].astype(str).str.strip()
                    sn_series = sn_series[
                        sn_series.notna() & (sn_series != "") & (sn_series.str.lower() != "nan")
                    ]
                    if not sn_series.empty:
                        step_names.add(sn_series.iloc[0])

            x_raw = df[df.columns[0]]
            x_dt = pd.to_datetime(x_raw, errors="coerce")
            is_dt = x_dt.notna().sum() >= int(len(x_raw) * 0.8)

            if "Elapsed" in x_mode:
                if not is_dt:
                    continue
                tmp = df.copy()
                tmp["_xdt"] = x_dt
                tmp = tmp.dropna(subset=["_xdt"])
                if tmp.empty:
                    continue
                tmp = tmp.sort_values("_xdt").reset_index(drop=True)

                t0 = tmp["_xdt"].iloc[0]
                x_vals = (tmp["_xdt"] - t0).dt.total_seconds().astype("float64")
                df_use = tmp
            else:
                df_use = df.copy()
                if is_dt:
                    df_use["_xdt"] = x_dt
                    df_use = df_use.sort_values("_xdt").reset_index(drop=True)
                else:
                    df_use = df_use.reset_index(drop=True)
                x_vals = pd.Series(range(len(df_use)), dtype="float64")

            for yc in y_cols:
                if yc not in df_use.columns:
                    continue
                y_vals = pd.to_numeric(df_use[yc], errors="coerce")

                m = x_vals.notna() & y_vals.notna()
                if m.sum() == 0:
                    continue

                xs = x_vals[m].astype("float64")
                ys = y_vals[m].astype("float64")

                xmin, xmax = float(xs.min()), float(xs.max())
                ymin, ymax = float(ys.min()), float(ys.max())

                global_xmin = xmin if global_xmin is None else min(global_xmin, xmin)
                global_xmax = xmax if global_xmax is None else max(global_xmax, xmax)
                global_ymin = ymin if global_ymin is None else min(global_ymin, ymin)
                global_ymax = ymax if global_ymax is None else max(global_ymax, ymax)

                s = QLineSeries()
                step_tag = f" | Step={step_txt}" if step_txt else ""
                short_name = self._short_compare_label_from_filename(p.name)
                s.setName(f"{short_name}{step_tag} | {yc}")

                pen = s.pen()
                pen.setWidthF(1.6)
                pen.setColor(pick_color())
                s.setPen(pen)

                for x, y in zip(xs.tolist(), ys.tolist()):
                    s.append(float(x), float(y))

                chart.addSeries(s)
                s.attachAxis(ax_x)
                s.attachAxis(ax_l)
                area.left_series.append(s)

                plotted_any = True

        if not plotted_any or global_xmin is None:
            QMessageBox.information(self, "Compare", "No data to compare")
            return

        ax_x.setRange(global_xmin, global_xmax)
        if global_ymin is None or global_ymax is None:
            global_ymin, global_ymax = 0.0, 1.0
        if global_ymin == global_ymax:
            global_ymax = global_ymin + 1.0
        ax_l.setRange(global_ymin, global_ymax)
        ax_l.applyNiceNumbers()

        step_name_tag = ""
        if step_txt and step_names:
            if len(step_names) == 1:
                step_name_tag = f" | {next(iter(step_names))}"
            else:
                step_name_tag = " | (multiple step names)"

        area.titlebar.setText(
            f"COMPARE ({'Δt' if 'Elapsed' in x_mode else 'Index'})"
            + (f" | Step={step_txt}" if step_txt else "")
            + step_name_tag
        )
        self.status.setText(
            f"Compare: files={len(paths)} | Step={step_txt or 'ALL'} | Y={', '.join(y_cols)}"
        )
        area.view._layout_bands()

        area.compare_active = True
        area.compare_full_xmin = float(global_xmin)
        area.compare_full_xmax = float(global_xmax)

        self.btn_reset_zoom.setEnabled(True)
        self.btn_add_graph.setEnabled(True)

    def reset_zoom(self):
        any_compare = any(a.compare_active for a in self._areas)
        if any_compare:
            did = False
            for a in self._areas:
                if not a.compare_active:
                    continue
                if a.axis_x_num is None:
                    continue
                if a.compare_full_xmin is None or a.compare_full_xmax is None:
                    continue
                a.axis_x_num.setRange(a.compare_full_xmin, a.compare_full_xmax)
                did = True
            if did:
                self.status.setText("Compare zoom reset completed (full scale)")
            return

        if self.df is None or "_x" not in self.df.columns:
            return

        if self.x_is_datetime:
            if self._full_x_min_dt is None or self._full_x_max_dt is None:
                return
            self.dt_start.setDateTime(self._full_x_min_dt)
            self.dt_end.setDateTime(self._full_x_max_dt)
        else:
            if self._full_x_min_num is None or self._full_x_max_num is None:
                return
            self.num_start.setValue(float(self._full_x_min_num))
            self.num_end.setValue(float(self._full_x_max_num))

        self.status.setText("Zoom reset completed (full scale)")
        self._refresh_x_range_buttons()
        self.plot()

    def _desired_cols(self) -> int:
        n = len(self._areas)
        if n <= 1:
            return 1
        return 1 if self.width() < 1100 else 2

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cols = self._desired_cols()
        if cols != self._last_cols:
            self._rebuild_plot_layout(force=True)

    def _clear_grid_layout(self):
        while self.plot_grid_layout.count():
            item = self.plot_grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.plot_grid_layout.removeWidget(w)
                w.setParent(None)

    def _rebuild_plot_layout(self, force: bool = False):
        n = len(self._areas)
        cols = self._desired_cols()

        if not force and self._last_cols == cols:
            return
        self._last_cols = cols

        self._clear_grid_layout()
        if n == 0:
            return

        for r in range(50):
            self.plot_grid_layout.setRowStretch(r, 0)
        for c in range(5):
            self.plot_grid_layout.setColumnStretch(c, 0)

        rows = (n + cols - 1) // cols
        for i, area in enumerate(self._areas):
            r = i // cols
            c = i % cols
            self.plot_grid_layout.addWidget(area.widget, r, c)

        for r in range(rows):
            self.plot_grid_layout.setRowStretch(r, 1)
        for c in range(cols):
            self.plot_grid_layout.setColumnStretch(c, 1)

    def add_graph(self):
        area = PlotArea(self)
        area.left_cols = self._selected_cols(self.y_combos)
        area.right_cols = self._selected_cols(self.y2_combos)
        self._areas.append(area)
        self._rebuild_plot_layout(force=True)
        self.plot()

    def delete_graph(self, area: PlotArea):
        if area not in self._areas:
            return
        if len(self._areas) <= 1:
            QMessageBox.information(self, "Delete aborted", "At least one graph must remain.")
            return
        try:
            area.clear()
        except Exception:
            logger.exception("[UI][Graph] area.clear failed")
        self._areas.remove(area)
        area.widget.setParent(None)
        area.widget.deleteLater()
        self._rebuild_plot_layout(force=True)
        self.plot()

    @staticmethod
    def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = (
            df.columns.astype(str)
            .str.replace("\ufeff", "", regex=False)
            .str.replace("\xa0", " ", regex=False)
            .str.strip()
        )
        return df

    def _set_combos_items(self, combos, items: list[str]):
        for cb in combos:
            cb.clear()
            cb.addItem(NONE_ITEM)
            cb.addItems(items)
            cb.setCurrentText(NONE_ITEM)

    def _selected_cols(self, combos: list[QComboBox]) -> list[str]:
        cols: list[str] = []
        for cb in combos:
            t = cb.currentText().strip()
            if not t or t == NONE_ITEM:
                continue
            cols.append(t)
        seen = set()
        uniq = []
        for c in cols:
            if c not in seen:
                uniq.append(c)
                seen.add(c)
        return uniq

    def _update_scale_enable_state(self):
        left_manual = (self.left_scale_mode.currentText() == "Manual")
        self.left_ymin.setEnabled(left_manual and self.left_scale_mode.isEnabled())
        self.left_ymax.setEnabled(left_manual and self.left_scale_mode.isEnabled())

        right_manual = (self.right_scale_mode.currentText() == "Manual")
        self.right_ymin.setEnabled(right_manual and self.right_scale_mode.isEnabled())
        self.right_ymax.setEnabled(right_manual and self.right_scale_mode.isEnabled())

    def reset_left_scale(self):
        self.left_scale_mode.setCurrentText("Auto")
        self.left_log.setChecked(False)
        self.left_ymin.setValue(0.0)
        self.left_ymax.setValue(0.0)
        self._update_scale_enable_state()
        self.plot()

    def reset_right_scale(self):
        self.right_scale_mode.setCurrentText("Auto")
        self.right_log.setChecked(False)
        self.right_ymin.setValue(0.0)
        self.right_ymax.setValue(0.0)
        self._update_scale_enable_state()
        self.plot()

    def clear_left(self):
        for cb in self.y_combos:
            cb.setCurrentText(NONE_ITEM)
        self.status.setText("Left(Y) deselect")
        self.plot()

    def clear_right(self):
        for cb in self.y2_combos:
            cb.setCurrentText(NONE_ITEM)
        self.status.setText("Right(Y2) deselect")
        self.plot()

    def _detect_step_columns(self):
        self._step_no_col = None
        self._step_name_col = None
        if self.df is None:
            return

        cols = list(self.df.columns)
        lower_map = {c: str(c).strip().lower() for c in cols}

        def pick(candidates: list[str]) -> str | None:
            for c in cols:
                if lower_map[c] in candidates:
                    return c

            norm_map = {c: re.sub(r"[^a-z0-9]", "", lower_map[c]) for c in cols}
            for c in cols:
                for cand in candidates:
                    cand2 = re.sub(r"[^a-z0-9]", "", cand)
                    if cand2 and cand2 == norm_map[c]:
                        return c

            for c in cols:
                v = norm_map[c]
                for cand in candidates:
                    cand2 = re.sub(r"[^a-z0-9]", "", cand)
                    if cand2 and cand2 in v:
                        return c
            return None

        self._step_no_col = pick([
            "step no", "stepno", "step number", "stepnumber",
            "step_no", "step-no", "step",
        ])
        self._step_name_col = pick([
            "step name", "stepname", "step desc", "stepdesc", "step description",
            "recipe step name", "recipestepname",
        ])

    def _step_info_at(self, t: pd.Timestamp) -> tuple[str | None, str | None]:
        if self.df is None or "_x" not in self.df.columns or not self.x_is_datetime:
            return None, None
        if self._step_no_col is None and self._step_name_col is None:
            return None, None

        d = self.df
        cols = ["_x"]
        if self._step_no_col and self._step_no_col in d.columns:
            cols.append(self._step_no_col)
        if self._step_name_col and self._step_name_col in d.columns:
            cols.append(self._step_name_col)
        if len(cols) <= 1:
            return None, None

        dd = d.loc[:, cols].copy()
        dd = dd.dropna(subset=["_x"], how="any")
        if dd.empty:
            return None, None
        dd = dd.sort_values("_x")

        xs = pd.to_datetime(dd["_x"], errors="coerce").dropna()
        if xs.empty:
            return None, None
        dd = dd.loc[xs.index]

        pos = xs.searchsorted(t, side="right") - 1
        if pos < 0:
            return None, None

        row = dd.iloc[int(pos)]
        step_no = None
        step_name = None

        if self._step_no_col and self._step_no_col in row.index:
            v = row[self._step_no_col]
            if pd.notna(v):
                step_no = str(v)

        if self._step_name_col and self._step_name_col in row.index:
            v = row[self._step_name_col]
            if pd.notna(v):
                step_name = str(v)

        return step_no, step_name

    def _step_info_at_x_value(self, x_value: float) -> tuple[str | None, str | None]:
        if not self.x_is_datetime:
            return None, None
        try:
            t = pd.Timestamp.fromtimestamp(float(x_value) / 1000.0)
        except Exception:
            return None, None
        return self._step_info_at(t)

    def load_csv(self, path: str | Path):
        path = Path(path)
        self.csv_path = path
        self._mode = "single"
        for a in self._areas:
            a.compare_active = False
            a.compare_full_xmin = None
            a.compare_full_xmax = None
        self.title.setText(f"Selected CSV: {path.name}")

        try:
            df = pd.read_csv(path, low_memory=False)
            if df.empty:
                raise ValueError("CSV file is empty")
        except Exception as e:
            QMessageBox.critical(self, "CSV file load aborted", f"{e}")
            return

        df = self._normalize_columns(df)
        self.df = df
        self._detect_step_columns()

        self.x_col = df.columns[0]
        x_series = df[self.x_col]
        x_dt = pd.to_datetime(x_series, errors="coerce")
        valid_dt = int(x_dt.notna().sum())

        self._full_x_min_dt = None
        self._full_x_max_dt = None
        self._full_x_min_num = None
        self._full_x_max_num = None

        if valid_dt > 0 and valid_dt >= int(len(x_series) * 0.8):
            self.x_is_datetime = True
            df["_x"] = x_dt
            self._min_zoom_span = 2000.0

            x_valid = df["_x"].dropna()
            if not x_valid.empty:
                xmin = pd.Timestamp(x_valid.min())
                xmax = pd.Timestamp(x_valid.max())
                self._full_x_min_dt = QDateTime.fromString(xmin.strftime("%Y-%m-%d %H:%M:%S"), "yyyy-MM-dd HH:mm:ss")
                self._full_x_max_dt = QDateTime.fromString(xmax.strftime("%Y-%m-%d %H:%M:%S"), "yyyy-MM-dd HH:mm:ss")

            self._setup_x_range_datetime(df["_x"])
            self.range_stack.setCurrentWidget(self.dt_widget)
        else:
            self.x_is_datetime = False
            x_num = pd.to_numeric(x_series, errors="coerce")
            if x_num.notna().sum() == 0:
                df["_x"] = range(len(df))
                self.x_col = "(index)"
            else:
                df["_x"] = x_num

            xv = pd.to_numeric(df["_x"], errors="coerce").dropna().sort_values()
            step = 0.0
            if len(xv) >= 3:
                diffs = xv.diff().dropna()
                diffs = diffs[diffs > 0]
                if not diffs.empty:
                    step = float(diffs.median())
            self._min_zoom_span = float(step * 2.0) if step > 0 else 0.0

            if not xv.empty:
                self._full_x_min_num = float(xv.min())
                self._full_x_max_num = float(xv.max())

            self._setup_x_range_numeric(df["_x"])
            self.range_stack.setCurrentWidget(self.num_widget)

        y_candidates: list[str] = []
        for c in df.columns:
            if c in [self.x_col, "_x"]:
                continue
            s = pd.to_numeric(df[c], errors="coerce")
            if s.notna().any():
                y_candidates.append(c)

        self._y_candidates = y_candidates[:]
        self._set_combos_items(self.y_combos, y_candidates)
        self._set_combos_items(self.y2_combos, y_candidates)

        enabled = len(y_candidates) > 0
        for cb in self.y_combos + self.y2_combos:
            cb.setEnabled(enabled)

        self.plot_btn.setEnabled(enabled)
        self.btn_clear_left.setEnabled(enabled)
        self.btn_clear_right.setEnabled(enabled)

        self.left_scale_mode.setEnabled(enabled)
        self.right_scale_mode.setEnabled(enabled)
        self.left_log.setEnabled(enabled)
        self.right_log.setEnabled(enabled)
        self.btn_reset_left_scale.setEnabled(enabled)
        self.btn_reset_right_scale.setEnabled(enabled)
        self.btn_add_graph.setEnabled(enabled)
        self.btn_reset_zoom.setEnabled(enabled)

        self.left_scale_mode.setCurrentText("Auto")
        self.right_scale_mode.setCurrentText("Auto")
        self._update_scale_enable_state()

        if not enabled:
            self.status.setText("No numeric columns found")
            self._clear_plot_all()
            return

        if len(y_candidates) > 0:
            self.y_combos[0].setCurrentText(y_candidates[0])
        else:
            self.y_combos[0].setCurrentText(NONE_ITEM)

        default_left = self._selected_cols(self.y_combos)
        for a in self._areas:
            if not a.left_cols and not a.right_cols:
                a.left_cols = default_left[:]

        self.plot()

    def _setup_x_range_datetime(self, x_dt: pd.Series):
        x_valid = x_dt.dropna()
        if x_valid.empty:
            now = QDateTime.currentDateTime()
            self.dt_start.setDateTime(now)
            self.dt_end.setDateTime(now)
            return
        xmin = x_valid.min()
        xmax = x_valid.max()
        qmin = QDateTime.fromString(xmin.strftime("%Y-%m-%d %H:%M:%S"), "yyyy-MM-dd HH:mm:ss")
        qmax = QDateTime.fromString(xmax.strftime("%Y-%m-%d %H:%M:%S"), "yyyy-MM-dd HH:mm:ss")
        self.dt_start.setDateTime(qmin)
        self.dt_end.setDateTime(qmax)
        self.dt_start.setMinimumDateTime(qmin)
        self.dt_start.setMaximumDateTime(qmax)
        self.dt_end.setMinimumDateTime(qmin)
        self.dt_end.setMaximumDateTime(qmax)

        self._refresh_x_range_buttons()

    def _setup_x_range_numeric(self, x_num: pd.Series):
        x_valid = pd.to_numeric(x_num, errors="coerce").dropna()
        if x_valid.empty:
            self.num_start.setRange(0, 0)
            self.num_end.setRange(0, 0)
            self.num_start.setValue(0)
            self.num_end.setValue(0)
            return
        xmin = float(x_valid.min())
        xmax = float(x_valid.max())
        self.num_start.setRange(xmin, xmax)
        self.num_end.setRange(xmin, xmax)
        self.num_start.setValue(xmin)
        self.num_end.setValue(xmax)

        self._refresh_x_range_buttons()

    def _refresh_x_range_buttons(self):
        if self.x_is_datetime:
            s = self.dt_start.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            e = self.dt_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            self.btn_dt_range.setText(f"{s}  ~  {e}")
        else:
            s = f"{self.num_start.value():.6g}"
            e = f"{self.num_end.value():.6g}"
            self.btn_num_range.setText(f"{s}  ~  {e}")

    def _clear_plot_all(self):
        for a in self._areas:
            a.clear()

    def _get_alarm_date_yyMMdd(self) -> str | None:
        if self.df is None or "_x" not in self.df.columns:
            return None
        if not self.x_is_datetime:
            return None
        x_valid = self.df["_x"].dropna()
        if x_valid.empty:
            return None
        d = pd.Timestamp(x_valid.min()).date()
        return f"{d.year % 100:02d}{d.month:02d}{d.day:02d}"

    def _get_target_tube_unitid(self) -> str | None:
        if self.df is None:
            return None
        tube_col = None
        if "TubeID" in self.df.columns:
            tube_col = "TubeID"
        elif "Tube ID" in self.df.columns:
            tube_col = "Tube ID"
        else:
            return None
        s = pd.to_numeric(self.df[tube_col], errors="coerce").dropna()
        if s.empty:
            return None
        tube_id = int(s.iloc[0])
        suffix = abs(tube_id) % 10
        return f"TUBE{suffix:02d}"

    def _load_alarm_events(self) -> pd.DataFrame | None:
        yyMMdd = self._get_alarm_date_yyMMdd()
        if yyMMdd is None:
            return None
        alarm_path = self.alarm_dir / f"Alarm_{yyMMdd}.csv"
        if not alarm_path.exists():
            return None

        try:
            adf = pd.read_csv(alarm_path)
            if adf.empty:
                return None
        except Exception:
            return None

        adf = self._normalize_columns(adf)
        required = {"Time", "UnitID", "Set", "Text"}
        if not required.issubset(set(adf.columns)):
            return None

        adf["_t"] = pd.to_datetime(adf["Time"], errors="coerce")
        adf = adf.dropna(subset=["_t"])

        target_tube = self._get_target_tube_unitid()
        unit_s = adf["UnitID"].astype(str).str.strip()
        set_s = adf["Set"].astype(str).str.strip()

        unit_ok = (unit_s == "Main")
        if target_tube:
            unit_ok = unit_ok | (unit_s == target_tube)
        set_ok = (set_s == "Set")

        filtered = adf.loc[unit_ok & set_ok, ["_t", "Text"]].copy()
        if filtered.empty:
            return None
        filtered["Text"] = filtered["Text"].astype(str).fillna("")
        filtered = filtered.sort_values("_t")
        return filtered

    def _open_y_scale_dialog(self, side: str, area: PlotArea | None = None):
        if area is not None and area.compare_active:
            ax = area.axis_y_left if side == "left" else area.axis_y_right
            if ax is None:
                return

            cur_mode = "Auto"
            cur_ymin = float(ax.min())
            cur_ymax = float(ax.max())
            cur_is_log = False

            dlg = YScaleDialog(
                title=("Left Y Scale (Compare)" if side == "left" else "Right Y2 Scale (Compare)"),
                mode=cur_mode,
                ymin=cur_ymin,
                ymax=cur_ymax,
                is_log=cur_is_log,
                parent=self,
            )
            if dlg.exec() != QDialog.Accepted:
                return

            mode, ymin, ymax, _is_log = dlg.values()

            if mode == "Manual":
                if ymin == ymax:
                    return
                if ymin > ymax:
                    ymin, ymax = ymax, ymin
                ax.setRange(float(ymin), float(ymax))
            else:
                ymin2, ymax2 = None, None
                series_list = area.left_series if side == "left" else area.right_series
                for s in series_list:
                    try:
                        pts = s.points()
                    except Exception:
                        continue
                    for p in pts:
                        y = float(p.y())
                        ymin2 = y if ymin2 is None else min(ymin2, y)
                        ymax2 = y if ymax2 is None else max(ymax2, y)

                if ymin2 is None or ymax2 is None:
                    return
                if ymin2 == ymax2:
                    ymax2 = ymin2 + 1.0
                ax.setRange(float(ymin2), float(ymax2))
                ax.applyNiceNumbers()
            return

        if side == "left":
            dlg = YScaleDialog(
                title="Left Y Scale",
                mode=self.left_scale_mode.currentText(),
                ymin=float(self.left_ymin.value()),
                ymax=float(self.left_ymax.value()),
                is_log=self.left_log.isChecked(),
                parent=self,
            )
        else:
            dlg = YScaleDialog(
                title="Right Y2 Scale",
                mode=self.right_scale_mode.currentText(),
                ymin=float(self.right_ymin.value()),
                ymax=float(self.right_ymax.value()),
                is_log=self.right_log.isChecked(),
                parent=self,
            )

        if dlg.exec() != QDialog.Accepted:
            return

        mode, ymin, ymax, is_log = dlg.values()

        if side == "left":
            self.left_scale_mode.setCurrentText(mode)
            self.left_log.setChecked(is_log)
            if mode == "Manual":
                self.left_ymin.setValue(ymin)
                self.left_ymax.setValue(ymax)
        else:
            self.right_scale_mode.setCurrentText(mode)
            self.right_log.setChecked(is_log)
            if mode == "Manual":
                self.right_ymin.setValue(ymin)
                self.right_ymax.setValue(ymax)

        self._update_scale_enable_state()
        self._refresh_x_range_buttons()
        self.plot()

    def _open_x_range_dialog(self):
        if self.df is None or "_x" not in self.df.columns:
            return

        dlg = XRangeDialog(is_datetime=self.x_is_datetime, parent=self)

        if self.x_is_datetime:
            x_valid = self.df["_x"].dropna()
            if x_valid.empty:
                return
            xmin = pd.Timestamp(x_valid.min())
            xmax = pd.Timestamp(x_valid.max())
            qmin = QDateTime.fromString(xmin.strftime("%Y-%m-%d %H:%M:%S"), "yyyy-MM-dd HH:mm:ss")
            qmax = QDateTime.fromString(xmax.strftime("%Y-%m-%d %H:%M:%S"), "yyyy-MM-dd HH:mm:ss")
            dlg.set_dt_values(qmin, qmax, self.dt_start.dateTime(), self.dt_end.dateTime())
        else:
            x_valid = pd.to_numeric(self.df["_x"], errors="coerce").dropna()
            if x_valid.empty:
                return
            xmin = float(x_valid.min())
            xmax = float(x_valid.max())
            dlg.set_num_values(xmin, xmax, float(self.num_start.value()), float(self.num_end.value()))

        if dlg.exec() != QDialog.Accepted:
            return

        v0, v1 = dlg.values()

        if self.x_is_datetime:
            ms0 = float(v0.toMSecsSinceEpoch())
            ms1 = float(v1.toMSecsSinceEpoch())
            if abs(ms1 - ms0) < max(2000.0, self._min_zoom_span):
                self.status.setText("X Axis minimum span is too small(>2000ms)")
                return
            self.dt_start.setDateTime(v0)
            self.dt_end.setDateTime(v1)
        else:
            x0 = float(v0)
            x1 = float(v1)
            if self._min_zoom_span > 0 and abs(x1 - x0) < self._min_zoom_span:
                self.status.setText("Can't zoom in too much(>2000ms)")
                return
            self.num_start.setValue(x0)
            self.num_end.setValue(x1)

        self.plot()

    def _open_y_columns_dialog(self, side: str, area: PlotArea | None = None):
        items = self._y_candidates[:] if self._y_candidates else []
        if not items:
            return

        if area is not None:
            cur = area.left_cols[:] if side == "left" else area.right_cols[:]
        else:
            cur = self._selected_cols(self.y_combos if side == "left" else self.y2_combos)

        dlg = YColumnsDialog(
            title=f"{side.upper()} Y Columns",
            items=items,
            selected=cur,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        selected = dlg.selected_items()

        if area is not None:
            if side == "left":
                area.left_cols = selected
            else:
                area.right_cols = selected
        else:
            combos = self.y_combos if side == "left" else self.y2_combos
            for i, cb in enumerate(combos):
                cb.setCurrentText(selected[i] if i < len(selected) else NONE_ITEM)

        self.plot()

    @staticmethod
    def _unique_color_generator():
        base = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
        i = 0
        while True:
            if i < len(base):
                yield base[i]
            else:
                k = i - len(base)
                hue = (k * 0.61803398875) % 1.0
                yield QColor.fromHsvF(hue, 0.55, 0.85).name()
            i += 1

    def _apply_zoom_from_chart(self, area: PlotArea, xmin: float, xmax: float):
        if xmin > xmax:
            xmin, xmax = xmax, xmin

        if area.compare_active:
            if area.axis_x_num is None:
                return

            min_span = 2.0
            if (xmax - xmin) < min_span:
                self.status.setText("Can't Zoom in more than minimum range (Compare X Axis)")
                return

            area.axis_x_num.setRange(float(xmin), float(xmax))
            self.status.setText("Compare zoom applied")
            return

        if self.x_is_datetime:
            min_span = max(2000.0, self._min_zoom_span)
            if (xmax - xmin) < min_span:
                self.status.setText("Can't Zoom in more than minimum range (X Axis)")
                return
            q0 = QDateTime.fromMSecsSinceEpoch(int(xmin))
            q1 = QDateTime.fromMSecsSinceEpoch(int(xmax))
            self.dt_start.setDateTime(q0)
            self.dt_end.setDateTime(q1)
        else:
            if self._min_zoom_span > 0 and (xmax - xmin) < self._min_zoom_span:
                self.status.setText("Can't Zoom in more than minimum range (X Axis)")
                return
            self.num_start.setValue(float(xmin))
            self.num_end.setValue(float(xmax))

        area.update_reference_lines()
        self.plot()

    @staticmethod
    def _finite_minmax(series: pd.Series) -> tuple[float | None, float | None]:
        s = pd.to_numeric(series, errors="coerce").dropna()
        if s.empty:
            return None, None
        return float(s.min()), float(s.max())

    def plot(self):
        if self.df is None or self.csv_path is None:
            return
        df0 = self.df
        if "_x" not in df0.columns:
            self.status.setText("No internal X Axis columns, please load CSV file again")
            return

        df = df0.copy()
        if self.x_is_datetime:
            start = pd.Timestamp(self.dt_start.dateTime().toPython())
            end = pd.Timestamp(self.dt_end.dateTime().toPython())
            df = df[df["_x"].between(start, end, inclusive="both")]
        else:
            start = float(self.num_start.value())
            end = float(self.num_end.value())
            if start > end:
                start, end = end, start
            df = df[df["_x"].between(start, end, inclusive="both")]

        if df.empty:
            self.status.setText("No data in selected range (X Axis)")
            self._clear_plot_all()
            return

        need_cols = {"_x"}
        for a in self._areas:
            if not a.left_cols and not a.right_cols:
                a.left_cols = self._selected_cols(self.y_combos)
                a.right_cols = self._selected_cols(self.y2_combos)
            for c in a.left_cols + a.right_cols:
                if c:
                    need_cols.add(c)

        use_cols = [c for c in need_cols if c in df.columns]
        df = df.loc[:, use_cols].copy()

        for c in use_cols:
            if c == "_x":
                continue
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["_x"], how="any")
        if df.empty:
            self.status.setText("No sufficient data to plot (NaN deleted)")
            self._clear_plot_all()
            return

        events = self._load_alarm_events()
        if events is not None and not events.empty and self.x_is_datetime:
            start_ev = pd.Timestamp(self.dt_start.dateTime().toPython())
            end_ev = pd.Timestamp(self.dt_end.dateTime().toPython())
            events = events[events["_t"].between(start_ev, end_ev, inclusive="both")]
        else:
            events = None

        for area in self._areas:
            if area.compare_active:
                try:
                    area.view._layout_bands()
                except Exception:
                    pass
                continue

            area.clear()

            left_cols = [c for c in area.left_cols if c in df.columns]
            right_cols = [c for c in area.right_cols if c in df.columns]

            area.titlebar.setText(self.csv_path.name)

            if not left_cols and not right_cols:
                continue

            sub_cols = ["_x"] + left_cols + right_cols
            sub_cols = list(dict.fromkeys(sub_cols))
            dfa = df.loc[:, sub_cols].copy()
            dfa = dfa.dropna(subset=["_x"] + left_cols + right_cols, how="any")
            if dfa.empty:
                continue

            chart = area.chart
            chart.setTitle("")
            chart.legend().setVisible(True)

            if self.x_is_datetime:
                ax_x = QDateTimeAxis()
                ax_x.setFormat("MM-dd HH:mm")
                ax_x.setTitleText("")
                ax_x.setTickCount(6)
                ax_x.setRange(self.dt_start.dateTime(), self.dt_end.dateTime())
                area.axis_x_dt = ax_x
                chart.addAxis(ax_x, Qt.AlignBottom)
            else:
                ax_x = QValueAxis()
                ax_x.setTitleText("")
                x0 = float(self.num_start.value())
                x1 = float(self.num_end.value())
                if x0 > x1:
                    x0, x1 = x1, x0
                ax_x.setRange(x0, x1)
                ax_x.setTickCount(6)
                area.axis_x_num = ax_x
                chart.addAxis(ax_x, Qt.AlignBottom)

            ax_l = QValueAxis()
            ax_l.setTitleText("")
            ax_l.setTickCount(6)
            area.axis_y_left = ax_l
            chart.addAxis(ax_l, Qt.AlignLeft)

            ax_r = QValueAxis()
            ax_r.setTitleText("")
            ax_r.setTickCount(6)
            area.axis_y_right = ax_r
            chart.addAxis(ax_r, Qt.AlignRight)

            used_colors = set()
            color_gen = self._unique_color_generator()

            def pick_color():
                while True:
                    c = next(color_gen)
                    if c not in used_colors:
                        used_colors.add(c)
                        return QColor(c)

            left_min, left_max = None, None
            for c in left_cols:
                s = QLineSeries()
                s.setName(f"L:{c}")
                pen = s.pen()
                pen.setWidthF(1.6)
                pen.setColor(pick_color())
                s.setPen(pen)

                for x, y in zip(dfa["_x"], dfa[c]):
                    if self.x_is_datetime:
                        ms = int(pd.Timestamp(x).to_pydatetime().timestamp() * 1000)
                        s.append(ms, float(y))
                    else:
                        s.append(float(x), float(y))

                chart.addSeries(s)
                if area.axis_x_dt is not None:
                    s.attachAxis(area.axis_x_dt)
                if area.axis_x_num is not None:
                    s.attachAxis(area.axis_x_num)
                s.attachAxis(ax_l)
                area.left_series.append(s)

                mn, mx = self._finite_minmax(dfa[c])
                if mn is not None:
                    left_min = mn if left_min is None else min(left_min, mn)
                    left_max = mx if left_max is None else max(left_max, mx)

            right_min, right_max = None, None
            for c in right_cols:
                s = QLineSeries()
                s.setName(f"R:{c}")
                pen = s.pen()
                pen.setWidthF(1.6)
                pen.setColor(pick_color())
                s.setPen(pen)

                for x, y in zip(dfa["_x"], dfa[c]):
                    if self.x_is_datetime:
                        ms = int(pd.Timestamp(x).to_pydatetime().timestamp() * 1000)
                        s.append(ms, float(y))
                    else:
                        s.append(float(x), float(y))

                chart.addSeries(s)
                if area.axis_x_dt is not None:
                    s.attachAxis(area.axis_x_dt)
                if area.axis_x_num is not None:
                    s.attachAxis(area.axis_x_num)
                s.attachAxis(ax_r)
                area.right_series.append(s)

                mn, mx = self._finite_minmax(dfa[c])
                if mn is not None:
                    right_min = mn if right_min is None else min(right_min, mn)
                    right_max = mx if right_max is None else max(right_max, mx)

            if left_min is not None and left_max is not None:
                if left_min == left_max:
                    left_max = left_min + 1.0
                ax_l.setRange(left_min, left_max)
                ax_l.applyNiceNumbers()

            if right_min is not None and right_max is not None:
                if right_min == right_max:
                    right_max = right_min + 1.0
                ax_r.setRange(right_min, right_max)
                ax_r.applyNiceNumbers()

            if self.left_scale_mode.currentText() == "Manual":
                ymin = float(self.left_ymin.value())
                ymax = float(self.left_ymax.value())
                if ymin != ymax:
                    if ymin > ymax:
                        ymin, ymax = ymax, ymin
                    ax_l.setRange(ymin, ymax)

            if self.right_scale_mode.currentText() == "Manual":
                ymin = float(self.right_ymin.value())
                ymax = float(self.right_ymax.value())
                if ymin != ymax:
                    if ymin > ymax:
                        ymin, ymax = ymax, ymin
                    ax_r.setRange(ymin, ymax)

            if events is not None and not events.empty and self.x_is_datetime:
                y_marker = ax_l.min() + (ax_l.max() - ax_l.min()) * 0.02

                alarm = QScatterSeries()
                alarm.setName("Alarm")
                alarm.setMarkerShape(QScatterSeries.MarkerShapeCircle)
                alarm.setMarkerSize(10.0)
                alarm.setColor(QColor("red"))
                alarm.setBorderColor(QColor("black"))

                chart.addSeries(alarm)
                alarm.attachAxis(area.axis_x_dt)
                alarm.attachAxis(ax_l)

                area.alarm_series = alarm
                area._alarm_map.clear()

                grouped: dict[int, dict[str, object]] = {}

                for t, txt in zip(events["_t"].tolist(), events["Text"].tolist()):
                    ts = pd.Timestamp(t)
                    ms = int(ts.to_pydatetime().timestamp() * 1000)

                    if ms not in grouped:
                        grouped[ms] = {
                            "ts": ts,
                            "time_str": ts.strftime("%Y-%m-%d %H:%M:%S"),
                            "texts": []
                        }
                    grouped[ms]["texts"].append("" if txt is None else str(txt))

                for ms in sorted(grouped.keys()):
                    alarm.append(ms, y_marker)

                    ts_for_step = grouped[ms]["ts"]
                    sn, sname = self._step_info_at(ts_for_step)

                    time_str = str(grouped[ms]["time_str"])
                    texts = grouped[ms]["texts"]

                    area._alarm_map[ms] = []
                    for txt2 in texts:
                        area._alarm_map[ms].append(
                            (
                                time_str,
                                str(txt2).strip(),
                                "" if sn is None else str(sn),
                                "" if sname is None else str(sname),
                            )
                        )

                alarm.hovered.connect(area.on_alarm_hovered)

            area.view._layout_bands()
            area.update_reference_lines()

        self.status.setText(
            f"표시 중: {len(df)} rows | Graphs={len(self._areas)} | "
            f"(ResetZoom=전체복귀 · X click=range · X drag=zoom(>=2s) · Y band=scale)"
        )


# =========================================================
# MainWindow
# =========================================================
class MainWindow(QMainWindow):
    def __init__(self, root_dir: str | Path, alarm_dir: str | Path, history_dir: str | Path):
        super().__init__()
        self.root_dir = Path(root_dir).resolve()
        self.alarm_dir = Path(alarm_dir).resolve()
        self.history_dir = Path(history_dir).resolve()
        self.setWindowTitle("Log Plotter")

        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        w = int(geo.width() * 0.90)
        h = int(geo.height() * 0.85)
        self.resize(w, h)
        self.move(geo.x() + int(geo.width() * 0.05), geo.y() + int(geo.height() * 0.05))

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setHandleWidth(10)

        self.model = QFileSystemModel()
        self.model.setRootPath(str(self.root_dir))
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.model.setNameFilters(["*.csv"])
        self.model.setNameFilterDisables(False)

        self.proxy_model = TreeFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)

        self.tree_search = QLineEdit()
        self.tree_search.setPlaceholderText("Search files/folders...")
        self.tree_search.setClearButtonEnabled(True)
        self.tree_search.textChanged.connect(self.on_tree_search_changed)
        self.tree_search.setStyleSheet(
            "QLineEdit{"
            "background:white;"
            "border:1px solid #d9dee7;"
            "border-radius:12px;"
            "padding:8px 10px;"
            "font-size:12px;"
            "}"
            "QLineEdit:focus{border:1px solid #60a5fa;}"
        )

        self.tree = QTreeView()
        self.tree.setModel(self.proxy_model)
        self.tree.setRootIndex(self.proxy_model.mapFromSource(self.model.index(str(self.root_dir))))
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setStyleSheet(
            "QTreeView{"
            "background:white;"
            "border:1px solid #d9dee7;"
            "padding:6px;"
            "alternate-background-color:#f8fafc;"
            "}"
            "QTreeView::item{padding: 4px 2px;}"
            "QTreeView::item:hover{background: #e6f4f7;}"
            "QTreeView::item:selected{background: #cfe8f0; color: #111827;}"
            "QHeaderView::section{"
            "background:#f8fafc;"
            "border:none;"
            "border-bottom:1px solid #e5e7eb;"
            "padding:8px;"
            "font-weight:600;"
            "color:#6b7280;"
            "}"
        )

        for col in range(self.model.columnCount()):
            if col not in (0, 3):
                self.tree.hideColumn(col)

        self.tree.setAnimated(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(3, Qt.AscendingOrder)
        self.tree.doubleClicked.connect(self.on_tree_double_clicked)
        self.tree.setMinimumWidth(260)
        self.tree.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        left_panel = QWidget()
        left_panel.setStyleSheet("background: transparent;")
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(6, 6, 6, 6)
        left_lay.setSpacing(0)

        self.tree_card = QWidget()
        self.tree_card.setObjectName("treeCard")
        self.tree_card.setStyleSheet("""
        QWidget#treeCard {
            background: white;
            border: 1px solid #d9dee7;
            border-radius: 18px;
        }
        """)
        apply_shadow(self.tree_card, blur=26, y_offset=4)

        tree_card_lay = QVBoxLayout(self.tree_card)
        tree_card_lay.setContentsMargins(10, 10, 10, 10)
        tree_card_lay.setSpacing(0)
        tree_card_lay.addWidget(self.tree_search, 0)
        tree_card_lay.addWidget(self.tree, 1)

        left_lay.addWidget(self.tree_card, 1)
        splitter.addWidget(left_panel)

        self.plot_panel = CsvPlotPanel(
            alarm_dir=self.alarm_dir,
            history_dir=self.history_dir,
        )
        self.plot_panel.root_dir = self.root_dir
        self.plot_panel.root_dir = self.root_dir
        self.plot_panel.historyRequested.connect(self.on_history_requested)
        self.plot_panel.exportRequested.connect(self.on_export_requested)
        self.plot_panel.setMinimumWidth(0)
        self.plot_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.plot_panel.btn_compare.clicked.connect(self.on_compare_selected)
        self.plot_panel.compareRequested.connect(self.on_compare_requested)
        splitter.addWidget(self.plot_panel)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 6)
        self.setCentralWidget(splitter)

        self._install_system_menu()

    def reload_tree_root(self):
        self.model.setRootPath(str(self.root_dir))
        src_root = self.model.index(str(self.root_dir))
        proxy_root = self.proxy_model.mapFromSource(src_root) if hasattr(self, "proxy_model") else src_root

        if hasattr(self, "proxy_model"):
            self.tree.setModel(self.proxy_model)
            self.tree.setRootIndex(proxy_root)
        else:
            self.tree.setModel(self.model)
            self.tree.setRootIndex(src_root)

        self.tree.collapseAll()
        self.plot_panel.root_dir = self.root_dir
        self.plot_panel.alarm_dir = self.alarm_dir
        self.plot_panel.history_dir = self.history_dir

        self.plot_panel._history_df_cache = None
        self.plot_panel._history_cache_root = None
        self.plot_panel._history_cache_history_dir = None

    def open_settings_dialog(self):
        dlg = AppSettingsDialog(
            root_dir=str(self.root_dir),
            alarm_dir=str(self.alarm_dir),
            history_dir=str(self.history_dir),
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        root_dir, alarm_dir, history_dir = dlg.values()

        self.root_dir = Path(root_dir).resolve()
        self.alarm_dir = Path(alarm_dir).resolve() if alarm_dir else Path(".").resolve()
        self.history_dir = Path(history_dir).resolve() if history_dir else Path(".").resolve()

        self.reload_tree_root()

    def _install_system_menu(self):
        if not sys.platform.startswith("win"):
            return

        self._sys_menu_cmd_settings = 0x1FF0

        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        hmenu = user32.GetSystemMenu(hwnd, False)
        if not hmenu:
            return

        MF_SEPARATOR = 0x00000800
        MF_STRING = 0x00000000

        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, self._sys_menu_cmd_settings, "Settings...")

    def nativeEvent(self, eventType, message):
        if sys.platform.startswith("win"):
            msg = wintypes.MSG.from_address(message.__int__())
            WM_SYSCOMMAND = 0x0112

            if msg.message == WM_SYSCOMMAND:
                cmd = int(msg.wParam) & 0xFFF0
                if cmd == self._sys_menu_cmd_settings:
                    self.open_settings_dialog()
                    return True, 0

        return super().nativeEvent(eventType, message)

    def on_tree_search_changed(self, text: str):
        self.proxy_model.setFilterText(text)
        if text.strip():
            self.tree.expandAll()
        else:
            self.tree.collapseAll()
            self.tree.setRootIndex(self.proxy_model.mapFromSource(self.model.index(str(self.root_dir))))

    def on_history_requested(self, area):
        self.plot_panel.show_history_dialog(area)

    def on_export_requested(self, area):
        self.plot_panel.export_history_to_excel()

    def on_compare_requested(self, area):
        idxs = self.tree.selectionModel().selectedRows()
        paths = []
        for idx in idxs:
            src_idx = self.proxy_model.mapToSource(idx)
            p = Path(self.model.filePath(src_idx))
            if p.is_file() and p.suffix.lower() == ".csv":
                paths.append(p)

        if paths:
            self.plot_panel.compare_files(paths, target_area=area)
        else:
            QMessageBox.information(self, "Compare", "Please select CSV files from tree view.")

    def on_compare_selected(self):
        idxs = self.tree.selectionModel().selectedRows()
        paths = []
        for idx in idxs:
            src_idx = self.proxy_model.mapToSource(idx)
            p = Path(self.model.filePath(src_idx))
            if p.is_file() and p.suffix.lower() == ".csv":
                paths.append(p)
        if paths:
            self.plot_panel.compare_files(paths)

    def on_tree_double_clicked(self, index):
        src_index = self.proxy_model.mapToSource(index)
        path = Path(self.model.filePath(src_index))

        if path.is_dir():
            if self.tree.isExpanded(index):
                self.tree.collapse(index)
            else:
                self.tree.expand(index)
            return

        self.plot_panel.load_csv(path)


def resource_path(rel: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / rel)
    return str(Path(__file__).resolve().parent / rel)


def set_appusermodel_id(app_id: str):
    if sys.platform.startswith("win"):
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


def main():
    logger.info("[APP] starting")

    set_appusermodel_id("com.dfam.logplotter")

    icon_path = resource_path("icons/LP_icon_big.ico")

    app = QApplication(sys.argv)
    QApplication.setStyle("Fusion")
    app.setStyle(ChromeProxyStyle())
    app.setWindowIcon(QIcon(icon_path))
    app.setStyleSheet(build_app_stylesheet())

    root_dir = r"D:\01. 업무자료\01. PROJECT\00. 개인PJT\02. 공정로그 및 알람 분석\02. 테스트로그"
    # root_dir = r"C:\hmi\System\RecipeProcLog"
    alarm_dir = r"D:\01. 업무자료\01. PROJECT\00. 개인PJT\02. 공정로그 및 알람 분석\02. 테스트로그\AlarmHistoryLog"
    # alarm_dir = r"C:\hmi\System\AlarmHistoryLog"
    history_dir = r"D:\01. 업무자료\01. PROJECT\00. 개인PJT\02. 공정로그 및 알람 분석\02. 테스트로그\RecipeHistoryLog"
    # history_dir = r"C:\hmi\System\RecipeHistoryLog"

    win = MainWindow(
        root_dir=root_dir,
        alarm_dir=alarm_dir,
        history_dir=history_dir,
    )
    win.setWindowIcon(QIcon(icon_path))
    win.show()

    rc = app.exec()
    logger.info(f"[APP] exit code={rc}")
    sys.exit(rc)


if __name__ == "__main__":
    main()