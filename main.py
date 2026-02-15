import sys
from pathlib import Path

import pandas as pd

from PySide6.QtCore import Qt, QDir, QDateTime, QPoint, QPointF, QRect, QRectF, QMargins, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QCursor, QBrush
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QTreeView, QFileSystemModel, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QStackedWidget,
    QDateTimeEdit, QDoubleSpinBox, QMessageBox, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QListWidget, QListWidgetItem,
    QAbstractItemView, QGridLayout, QRubberBand,
    QSizePolicy
)

from PySide6.QtCharts import (
    QChart, QChartView, QLineSeries, QScatterSeries,
    QValueAxis, QDateTimeAxis
)
from PySide6.QtWidgets import QGraphicsLineItem, QGraphicsRectItem, QGraphicsEllipseItem


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

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # tip 자체 hover 감지
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

        # 스타일 (필요하면 여기만 조절)
        if kind == "alarm":
            self.lbl.setStyleSheet(
                "QLabel{"
                "background: rgba(90,20,20,230);"
                "color: white;"
                "border: 1px solid rgba(255,255,255,40);"
                "border-radius: 8px;"
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
                "border-radius: 8px;"
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
            # target에서 벗어날 때 바로 hide하지 말고 약간 유예(툴팁으로 이동 가능)
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

        # parent(view) 영역 밖으로 나가지 않게 clamp
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
        # target hover도 아니고 tip 위 hover도 아니면 숨김
        if (not self._target_hovering) and (not self._hovering_tip):
            self.setVisible(False)

    def enterEvent(self, e):
        self._hovering_tip = True
        self._hide_timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovering_tip = False
        # tip에서 벗어났더라도 target hover면 유지
        if not self._target_hovering:
            self._hide_timer.start(120)
        super().leaveEvent(e)


# =========================================================
# Dialogs
# =========================================================
class YScaleDialog(QDialog):
    """Left/Right Y 축 스케일 설정 다이얼로그 (Auto/Manual + Min/Max). Log UI는 유지하지만 적용 안함."""
    def __init__(self, title: str, mode: str, ymin: float, ymax: float, is_log: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)

        self.mode_cb = QComboBox()
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

        self.log_cb = QComboBox()
        self.log_cb.addItems(["Linear", "Log"])
        self.log_cb.setCurrentText("Log" if is_log else "Linear")

        form = QFormLayout()
        form.addRow("Mode", self.mode_cb)
        form.addRow("Scale", self.log_cb)  # UI만(미적용)
        form.addRow("Min", self.ymin_sb)
        form.addRow("Max", self.ymax_sb)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(btns)

        self.mode_cb.currentTextChanged.connect(self._sync_enabled)
        self._sync_enabled()

    def _sync_enabled(self):
        manual = (self.mode_cb.currentText() == "Manual")
        self.ymin_sb.setEnabled(manual)
        self.ymax_sb.setEnabled(manual)

    def values(self):
        return (
            self.mode_cb.currentText(),
            float(self.ymin_sb.value()),
            float(self.ymax_sb.value()),
            self.log_cb.currentText() == "Log",  # UI만
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
            return (self.dt_start.dateTime(), self.dt_end.dateTime())
        return (float(self.num_start.value()), float(self.num_end.value()))


class YColumnsDialog(QDialog):
    """Y 축에 그릴 컬럼을 최대 3개까지 선택."""
    def __init__(self, title: str, items: list[str], selected: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)

        self.listw = QListWidget()
        self.listw.setSelectionMode(QAbstractItemView.MultiSelection)

        sel_set = set(selected)
        for it in items:
            item = QListWidgetItem(it)
            item.setSelected(it in sel_set)
            self.listw.addItem(item)

        hint = QLabel("여러개 선택 가능합니다.")
        hint.setStyleSheet("color: gray;")

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(self.listw, 1)
        root.addWidget(hint)
        root.addWidget(btns)

    def selected_items(self) -> list[str]:
        return [i.text() for i in self.listw.selectedItems()]


# =========================================================
# TitleBar
# =========================================================
class TitleBar(QWidget):
    def __init__(self, on_pick_left, on_pick_right, parent=None):
        super().__init__(parent)
        self._on_pick_left = on_pick_left
        self._on_pick_right = on_pick_right

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 4)
        lay.setSpacing(8)

        self.lbl = QLabel("—")
        self.lbl.setStyleSheet("font-weight:700;")
        self.lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        self.btn_left = QPushButton("L 요소…")
        self.btn_right = QPushButton("R 요소…")
        for b in (self.btn_left, self.btn_right):
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(24)
            b.setStyleSheet(
                "QPushButton{padding:2px 10px; border:1px solid rgba(0,0,0,0.18);"
                "border-radius:10px; background:rgba(255,255,255,0.85);}"
                "QPushButton:hover{background:rgba(255,255,255,1.0);}"
                "QPushButton:pressed{background:rgba(235,235,235,1.0);}"
            )

        self.btn_left.clicked.connect(self._on_pick_left)
        self.btn_right.clicked.connect(self._on_pick_right)

        hint = QLabel("X: click=range / drag=zoom · Y band=scale")
        hint.setStyleSheet("color: rgba(0,0,0,0.35); font-size: 11px;")
        hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        lay.addWidget(self.lbl, 1)
        lay.addWidget(self.btn_left, 0)
        lay.addWidget(self.btn_right, 0)
        lay.addWidget(hint, 0)

        self.setCursor(Qt.PointingHandCursor)

    def setText(self, t: str):
        self.lbl.setText(t)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.btn_left.geometry().contains(e.position().toPoint()) or self.btn_right.geometry().contains(e.position().toPoint()):
            return
        if e.position().x() < self.width() * 0.5:
            self._on_pick_left()
        else:
            self._on_pick_right()


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

        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        logger.debug("[UI][PowerChartView] init")

        # hover bands
        self._band_bottom = QGraphicsRectItem()
        self._band_left = QGraphicsRectItem()
        self._band_right = QGraphicsRectItem()
        for it in (self._band_bottom, self._band_left, self._band_right):
            it.setZValue(9)
            it.setPen(QPen(QColor(0, 0, 0, 0), 0))
            it.setBrush(QColor(0, 0, 0, 0))
            it.setVisible(False)

        # crosshair lines
        self._vline = QGraphicsLineItem()
        self._hline = QGraphicsLineItem()
        for ln in (self._vline, self._hline):
            ln.setZValue(10)
            ln.setPen(QPen(QColor("gray"), 1))
            ln.setVisible(False)

        # alarm halo (scene item)
        self._alarm_halo = QGraphicsEllipseItem()
        self._alarm_halo.setZValue(11)
        self._alarm_halo.setPen(QPen(QColor(255, 0, 0, 0), 0))
        self._alarm_halo.setBrush(QBrush(QColor(255, 0, 0, 60)))
        self._alarm_halo.setVisible(False)

        # rubber band zoom
        self._rubber = QRubberBand(QRubberBand.Rectangle, self)
        self._dragging = False
        self._drag_start = QPoint()
        self._hover_kind: str | None = None

        # ✅ custom tips
        self.tip_cross = StickyTip(self, kind="cross")
        self.tip_alarm = StickyTip(self, kind="alarm")

    def _ensure_scene_items(self):
        sc = self.chart().scene()
        for it in (self._band_bottom, self._band_left, self._band_right, self._vline, self._hline, self._alarm_halo):
            if it.scene() is None:
                sc.addItem(it)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._ensure_scene_items()
        self._layout_bands()

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

        # ✅ crosshair tip: plot 위 + 알람 hovering 아닐 때만 표시
        if self._plot_contains(e.position().toPoint()):
            self.tip_cross.set_target_hovering(True)

            if self.area._alarm_hovering:
                # 알람이 우선
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
            self.area.parent_panel._open_y_scale_dialog(side="left")
            return
        if kind == "y_right":
            self.area.parent_panel._open_y_scale_dialog(side="right")
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

        try:
            self.chart.layout().setContentsMargins(0, 0, 0, 0)
        except Exception:
            pass

        self.view = PowerChartView(self)
        self.view.setChart(self.chart)

        # container
        self.widget = QWidget()
        self.widget.setMinimumSize(0, 0)
        self.widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        wlay = QVBoxLayout(self.widget)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(0)

        topbar = QHBoxLayout()
        topbar.setContentsMargins(4, 4, 4, 0)
        topbar.setSpacing(6)

        self.btn_delete = QPushButton("✕")
        self.btn_delete.setToolTip("이 그래프 삭제")
        self.btn_delete.setFixedSize(26, 22)
        self.btn_delete.setStyleSheet(
            "QPushButton{border:1px solid rgba(0,0,0,0.2); border-radius:6px; background:rgba(255,255,255,0.85);}"
            "QPushButton:hover{background:rgba(255,255,255,1.0);}"
            "QPushButton:pressed{background:rgba(230,230,230,1.0);}"
        )
        self.btn_delete.clicked.connect(lambda: self.parent_panel.delete_graph(self))

        self.titlebar = TitleBar(
            on_pick_left=lambda: self.parent_panel._open_y_columns_dialog(side="left", area=self),
            on_pick_right=lambda: self.parent_panel._open_y_columns_dialog(side="right", area=self),
        )

        topbar.addWidget(self.titlebar, 1)
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

        # alarm
        self._alarm_hovering: bool = False
        self.alarm_series: QScatterSeries | None = None
        self._alarm_map: dict[int, tuple[str, str, str, str]] = {}  # ms -> (timeStr, text, stepNo, stepName)

    def _find_nearest_y(self, series: QLineSeries, x: float) -> float | None:
        """series에서 x에 가장 가까운 점의 y를 반환. (x는 ms 또는 numeric)"""
        try:
            pts = series.points()  # ✅ deprecated pointsVector() 안씀
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
        if self.parent_panel.x_is_datetime:
            qdt = QDateTime.fromMSecsSinceEpoch(int(x))
            lines.append(qdt.toString("yyyy-MM-dd HH:mm:ss"))
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
        # ✅ custom tips hide
        try:
            self.view.tip_cross.hide_tip()
            self.view.tip_alarm.hide_tip()
            self.view.tip_cross.set_target_hovering(False)
            self.view.tip_alarm.set_target_hovering(False)
        except Exception:
            pass

        # halo hide
        try:
            self.view.show_alarm_halo_at(self.alarm_series, QPointF(), False)
        except Exception:
            pass

        # hovered disconnect
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

    def _alarm_text_from_key(self, key_ms: int) -> str:
        info = self._alarm_map.get(key_ms)
        if not info:
            return ""
        time_str, txt, step_no, step_name = info
        lines = [time_str]
        if step_no or step_name:
            if step_no and step_name:
                lines.append(f"Step: {step_no} | {step_name}")
            elif step_no:
                lines.append(f"Step: {step_no}")
            else:
                lines.append(f"Step: {step_name}")
        if txt:
            lines.append(txt)
        return "\n".join(lines).strip()

    def on_alarm_hovered(self, point: QPointF, state: bool):
        if self.alarm_series is None:
            return

        self._alarm_hovering = bool(state)

        # halo
        self.view.show_alarm_halo_at(self.alarm_series, point, state)

        if state:
            # crosshair tip은 가림
            self.view.tip_cross.hide_tip()

            # 알람 tip 표시
            key = int(round(point.x()))
            msg = self._alarm_text_from_key(key)
            if msg:
                try:
                    pos_scene = self.view.chart().mapToPosition(point, self.alarm_series)  # chart item coord
                    pos_view = self.view.mapFromScene(pos_scene.toPoint())
                except Exception:
                    pos_view = QPoint(self.view.width() // 2, self.view.height() // 2)

                self.view.tip_alarm.set_target_hovering(True)
                # 알람은 위로 뜨게(겹침 최소화)
                self.view.tip_alarm.show_text_at(msg, pos_view, offset=QPoint(18, -10))
            else:
                self.view.tip_alarm.hide_tip()
                self.view.tip_alarm.set_target_hovering(False)
        else:
            # 알람에서 벗어나면 (툴팁으로 이동 가능하도록) target_hovering false
            self.view.tip_alarm.set_target_hovering(False)


# =========================================================
# Main Panel
# =========================================================
class CsvPlotPanel(QWidget):
    def __init__(self, alarm_dir: Path, parent=None):
        super().__init__(parent)

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

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        title_row = QHBoxLayout()
        self.title = QLabel("CSV를 선택하세요.")
        self.title.setStyleSheet("font-weight: 700;")
        title_row.addWidget(self.title, 1)

        self.btn_add_graph = QPushButton("+ Graph")
        self.btn_add_graph.setEnabled(False)
        self.btn_add_graph.setToolTip("그래프를 하나 더 추가")
        self.btn_add_graph.clicked.connect(self.add_graph)
        title_row.addWidget(self.btn_add_graph, 0)

        root.addLayout(title_row)

        # hidden controls
        self.ctrl_widget = QWidget()
        ctrl_layout = QVBoxLayout(self.ctrl_widget)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("Y(Left):"))
        self.y_combos = [QComboBox(), QComboBox(), QComboBox()]
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
        self.y2_combos = [QComboBox(), QComboBox(), QComboBox()]
        for cb in self.y2_combos:
            cb.setEnabled(False)
            cb.setMinimumWidth(160)
            top2.addWidget(cb, 1)
        self.btn_clear_right = QPushButton("Clear R")
        self.btn_clear_right.setEnabled(False)
        self.btn_clear_right.clicked.connect(self.clear_right)
        top2.addWidget(self.btn_clear_right)
        ctrl_layout.addLayout(top2)

        # X range
        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("X 범위:"), 0)
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
        dt_layout.addWidget(QLabel("Start"))
        dt_layout.addWidget(self.dt_start, 1)
        dt_layout.addWidget(QLabel("End"))
        dt_layout.addWidget(self.dt_end, 1)

        self.num_widget = QWidget()
        num_layout = QHBoxLayout(self.num_widget)
        num_layout.setContentsMargins(0, 0, 0, 0)
        self.num_start = QDoubleSpinBox()
        self.num_start.setDecimals(6)
        self.num_start.setRange(-1e30, 1e30)
        self.num_end = QDoubleSpinBox()
        self.num_end.setDecimals(6)
        self.num_end.setRange(-1e30, 1e30)
        num_layout.addWidget(QLabel("Start"))
        num_layout.addWidget(self.num_start, 1)
        num_layout.addWidget(QLabel("End"))
        num_layout.addWidget(self.num_end, 1)

        self.range_stack.addWidget(self.dt_widget)
        self.range_stack.addWidget(self.num_widget)
        range_row.addWidget(self.range_stack, 1)
        ctrl_layout.addLayout(range_row)

        # Y scale
        yscale_row = QHBoxLayout()
        yscale_row.addWidget(QLabel("Left Y Scale:"), 0)
        self.left_scale_mode = QComboBox()
        self.left_scale_mode.addItems(["Auto", "Manual"])
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
        self.left_log = QCheckBox("Log")  # UI만
        self.left_log.setEnabled(False)
        yscale_row.addWidget(self.left_log, 0)
        self.btn_reset_left_scale = QPushButton("Reset L")
        self.btn_reset_left_scale.setEnabled(False)
        self.btn_reset_left_scale.clicked.connect(self.reset_left_scale)
        yscale_row.addWidget(self.btn_reset_left_scale, 0)
        ctrl_layout.addLayout(yscale_row)

        y2scale_row = QHBoxLayout()
        y2scale_row.addWidget(QLabel("Right Y2 Scale:"), 0)
        self.right_scale_mode = QComboBox()
        self.right_scale_mode.addItems(["Auto", "Manual"])
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
        self.right_log = QCheckBox("Log")  # UI만
        self.right_log.setEnabled(False)
        y2scale_row.addWidget(self.right_log, 0)
        self.btn_reset_right_scale = QPushButton("Reset R")
        self.btn_reset_right_scale.setEnabled(False)
        self.btn_reset_right_scale.clicked.connect(self.reset_right_scale)
        y2scale_row.addWidget(self.btn_reset_right_scale, 0)
        ctrl_layout.addLayout(y2scale_row)

        self.left_scale_mode.currentIndexChanged.connect(self._update_scale_enable_state)
        self.right_scale_mode.currentIndexChanged.connect(self._update_scale_enable_state)

        root.addWidget(self.ctrl_widget)
        self.ctrl_widget.setVisible(False)

        # plot grid
        self.plot_container = QWidget()
        self.plot_container_layout = QVBoxLayout(self.plot_container)
        self.plot_container_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_container_layout.setSpacing(0)

        self.plot_grid = QWidget()
        self.plot_grid_layout = QGridLayout(self.plot_grid)
        self.plot_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_grid_layout.setHorizontalSpacing(6)
        self.plot_grid_layout.setVerticalSpacing(6)

        self.plot_container_layout.addWidget(self.plot_grid, 1)
        root.addWidget(self.plot_container, 1)

        a0 = PlotArea(self)
        self._areas.append(a0)
        self._rebuild_plot_layout(force=True)

        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        root.addWidget(self.status)

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
            QMessageBox.information(self, "삭제 불가", "최소 1개 그래프는 유지됩니다.")
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

    def _set_combos_items(self, combos: list[QComboBox], items: list[str]):
        for cb in combos:
            cb.blockSignals(True)
            cb.clear()
            cb.addItem(NONE_ITEM)
            cb.addItems(items)
            cb.setCurrentIndex(0)
            cb.blockSignals(False)

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
        self.status.setText("Left(Y) 선택 해제")
        self.plot()

    def clear_right(self):
        for cb in self.y2_combos:
            cb.setCurrentText(NONE_ITEM)
        self.status.setText("Right(Y2) 선택 해제")
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
            import re
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

    # -----------------------------
    # CSV load
    # -----------------------------
    def load_csv(self, path: str | Path):
        path = Path(path)
        self.csv_path = path
        self.title.setText(f"선택된 CSV: {path}")

        try:
            df = pd.read_csv(path)
            if df.empty:
                raise ValueError("CSV가 비어 있습니다.")
        except Exception as e:
            QMessageBox.critical(self, "CSV 로드 실패", f"{e}")
            return

        df = self._normalize_columns(df)
        self.df = df
        self._detect_step_columns()

        self.x_col = df.columns[0]
        x_series = df[self.x_col]
        x_dt = pd.to_datetime(x_series, errors="coerce")
        valid_dt = int(x_dt.notna().sum())

        if valid_dt > 0 and valid_dt >= int(len(x_series) * 0.8):
            self.x_is_datetime = True
            df["_x"] = x_dt
            self._setup_x_range_datetime(df["_x"])
            self.range_stack.setCurrentWidget(self.dt_widget)
        else:
            self.x_is_datetime = False
            x_num = pd.to_numeric(x_series, errors="coerce")
            if x_num.notna().sum() == 0:
                df["_x"] = range(len(df))
                self.x_col = "(index)"
                self._setup_x_range_numeric(df["_x"])
            else:
                df["_x"] = x_num
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

        self.left_scale_mode.setCurrentText("Auto")
        self.right_scale_mode.setCurrentText("Auto")
        self._update_scale_enable_state()

        if not enabled:
            self.status.setText("그래프로 그릴 수 있는 숫자형 컬럼이 없습니다.")
            self._clear_plot_all()
            return

        self.y_combos[0].setCurrentIndex(1)

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

    def _clear_plot_all(self):
        for a in self._areas:
            a.clear()

    # Alarm helpers
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

    # Dialog openers
    def _open_y_scale_dialog(self, side: str):
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
            self.dt_start.setDateTime(v0)
            self.dt_end.setDateTime(v1)
        else:
            self.num_start.setValue(float(v0))
            self.num_end.setValue(float(v1))

        self.plot()

    def _open_y_columns_dialog(self, side: str, area: PlotArea | None = None):
        items = self._y_candidates[:] if self._y_candidates else []
        if not items:
            return

        cur = []
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
        if self.x_is_datetime:
            q0 = QDateTime.fromMSecsSinceEpoch(int(xmin))
            q1 = QDateTime.fromMSecsSinceEpoch(int(xmax))
            self.dt_start.setDateTime(q0)
            self.dt_end.setDateTime(q1)
        else:
            self.num_start.setValue(float(xmin))
            self.num_end.setValue(float(xmax))
        self.plot()

    @staticmethod
    def _finite_minmax(series: pd.Series) -> tuple[float | None, float | None]:
        s = pd.to_numeric(series, errors="coerce").dropna()
        if s.empty:
            return None, None
        return float(s.min()), float(s.max())

    # -----------------------------
    # plot
    # -----------------------------
    def plot(self):
        if self.df is None or self.csv_path is None:
            return
        df0 = self.df
        if "_x" not in df0.columns:
            self.status.setText("내부 x축 컬럼(_x)이 없습니다. CSV를 다시 로드해주세요.")
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
            self.status.setText("선택한 X 범위에 데이터가 없습니다.")
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
            self.status.setText("유효한 데이터가 없습니다 (NaN 제거 후)")
            self._clear_plot_all()
            return

        events = self._load_alarm_events()
        if events is not None and not events.empty and self.x_is_datetime:
            start_ev = pd.Timestamp(self.dt_start.dateTime().toPython())
            end_ev = pd.Timestamp(self.dt_end.dateTime().toPython())
            events = events[events["_t"].between(start_ev, end_ev, inclusive="both")]
        else:
            events = None

        for idx, area in enumerate(self._areas):
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

            # axes
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

            # alarms
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

                for t, txt in zip(events["_t"].tolist(), events["Text"].tolist()):
                    ts = pd.Timestamp(t)
                    ms = int(ts.to_pydatetime().timestamp() * 1000)
                    alarm.append(ms, y_marker)

                    sn, sname = self._step_info_at(ts)
                    time_str = ts.strftime("%Y-%m-%d %H:%M:%S")
                    area._alarm_map[int(ms)] = (
                        time_str,
                        str(txt) if txt else "",
                        "" if sn is None else str(sn),
                        "" if sname is None else str(sname),
                    )

                alarm.hovered.connect(area.on_alarm_hovered)

            area.view._layout_bands()

        self.status.setText(
            f"표시 중: {len(df)} rows | Graphs={len(self._areas)} | (Title/L·R 버튼=요소, X click=range, X drag=zoom, Y band=scale)"
        )


# =========================================================
# MainWindow
# =========================================================
class MainWindow(QMainWindow):
    def __init__(self, root_dir: str | Path, alarm_dir: str | Path):
        super().__init__()
        self.root_dir = Path(root_dir).resolve()
        self.alarm_dir = Path(alarm_dir).resolve()
        self.setWindowTitle("병곤이가만듦")

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

        self.model = QFileSystemModel()
        self.model.setRootPath(str(self.root_dir))
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.model.setNameFilters(["*.csv"])
        self.model.setNameFilterDisables(False)

        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.index(str(self.root_dir)))

        for col in range(self.model.columnCount()):
            if col not in (0, 3):  # 0: Name, 3: Date Modified
                self.tree.hideColumn(col)

        self.tree.setAnimated(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(3, Qt.AscendingOrder)  # 시간순
        self.tree.doubleClicked.connect(self.on_tree_double_clicked)
        self.tree.setMinimumWidth(240)
        self.tree.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        splitter.addWidget(self.tree)

        self.plot_panel = CsvPlotPanel(alarm_dir=self.alarm_dir)
        self.plot_panel.setMinimumWidth(0)
        self.plot_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        splitter.addWidget(self.plot_panel)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 6)
        self.setCentralWidget(splitter)

    def on_tree_double_clicked(self, index):
        path = Path(self.model.filePath(index))
        if path.is_dir():
            if self.tree.isExpanded(index):
                self.tree.collapse(index)
            else:
                self.tree.expand(index)
            return
        self.plot_panel.load_csv(path)


def main():
    logger.info("[APP] starting")
    app = QApplication(sys.argv)

    # root_dir = r"D:\01. 업무자료\01. PROJECT\00. 개인PJT\02. 공정로그 및 알람 분석\02. 테스트로그"
    root_dir = r"C:\hmi\System\RecipeProcLog"
    # alarm_dir = r"D:\01. 업무자료\01. PROJECT\00. 개인PJT\02. 공정로그 및 알람 분석\02. 테스트로그\AlarmHistoryLog"
    alarm_dir = r"C:\hmi\System\AlarmHistoryLog"

    logger.info(f"[APP] paths root_dir={root_dir}, alarm_dir={alarm_dir}")

    win = MainWindow(root_dir=root_dir, alarm_dir=alarm_dir)
    win.show()
    rc = app.exec()
    logger.info(f"[APP] exit code={rc}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
