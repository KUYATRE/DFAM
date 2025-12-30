import sys
from pathlib import Path

import pandas as pd

from PySide6.QtCore import Qt, QDir, QDateTime, QPoint, QPointF, QRect, QRectF, QMargins
from PySide6.QtGui import QPainter, QColor, QPen, QCursor, QBrush
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QTreeView, QFileSystemModel, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QStackedWidget,
    QDateTimeEdit, QDoubleSpinBox, QMessageBox, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QListWidget, QListWidgetItem,
    QAbstractItemView, QGridLayout, QToolTip, QRubberBand
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
# Dialogs
# =========================================================
class YScaleDialog(QDialog):
    """Left/Right Y 축 스케일 설정 다이얼로그 (Auto/Manual + Min/Max). Log UI는 유지하지만 적용 안함."""

    def __init__(self, title: str, mode: str, ymin: float, ymax: float, is_log: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)

        logger.debug(f"[UI][YScaleDialog] init title={title}, mode={mode}, ymin={ymin}, ymax={ymax}, is_log={is_log}")

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
        logger.debug(f"[UI][YScaleDialog] sync_enabled manual={manual}")
        self.ymin_sb.setEnabled(manual)
        self.ymax_sb.setEnabled(manual)

    def values(self):
        v = (
            self.mode_cb.currentText(),
            float(self.ymin_sb.value()),
            float(self.ymax_sb.value()),
            self.log_cb.currentText() == "Log",  # UI만
        )
        logger.debug(f"[UI][YScaleDialog] values={v}")
        return v


class XRangeDialog(QDialog):
    """X 범위 설정 다이얼로그. datetime/numeric 둘 다 지원."""

    def __init__(self, is_datetime: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("X Range")
        self.is_datetime = is_datetime
        logger.debug(f"[UI][XRangeDialog] init is_datetime={is_datetime}")

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
        logger.debug(
            f"[UI][XRangeDialog] set_dt_values qmin={qmin.toString('yyyy-MM-dd HH:mm:ss')}, "
            f"qmax={qmax.toString('yyyy-MM-dd HH:mm:ss')}, "
            f"cur_start={cur_start.toString('yyyy-MM-dd HH:mm:ss')}, "
            f"cur_end={cur_end.toString('yyyy-MM-dd HH:mm:ss')}"
        )
        self.dt_start.setMinimumDateTime(qmin)
        self.dt_start.setMaximumDateTime(qmax)
        self.dt_end.setMinimumDateTime(qmin)
        self.dt_end.setMaximumDateTime(qmax)
        self.dt_start.setDateTime(cur_start)
        self.dt_end.setDateTime(cur_end)

    def set_num_values(self, xmin: float, xmax: float, cur_start: float, cur_end: float):
        logger.debug(f"[UI][XRangeDialog] set_num_values xmin={xmin}, xmax={xmax}, cur_start={cur_start}, cur_end={cur_end}")
        self.num_start.setRange(xmin, xmax)
        self.num_end.setRange(xmin, xmax)
        self.num_start.setValue(cur_start)
        self.num_end.setValue(cur_end)

    def values(self):
        if self.is_datetime:
            v = (self.dt_start.dateTime(), self.dt_end.dateTime())
            logger.debug(
                f"[UI][XRangeDialog] values datetime start={v[0].toString('yyyy-MM-dd HH:mm:ss')}, "
                f"end={v[1].toString('yyyy-MM-dd HH:mm:ss')}"
            )
            return v
        v = (float(self.num_start.value()), float(self.num_end.value()))
        logger.debug(f"[UI][XRangeDialog] values numeric start={v[0]}, end={v[1]}")
        return v


class YColumnsDialog(QDialog):
    """Y 축에 그릴 컬럼을 최대 3개까지 선택."""

    def __init__(self, title: str, items: list[str], selected: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        logger.debug(f"[UI][YColumnsDialog] init title={title}, items={len(items)}, selected={selected}")

        self.listw = QListWidget()
        self.listw.setSelectionMode(QAbstractItemView.MultiSelection)

        sel_set = set(selected)
        for it in items:
            item = QListWidgetItem(it)
            item.setSelected(it in sel_set)
            self.listw.addItem(item)

        hint = QLabel("최대 3개까지 선택 가능합니다.")
        hint.setStyleSheet("color: gray;")

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept_checked)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(self.listw, 1)
        root.addWidget(hint)
        root.addWidget(btns)

    def _accept_checked(self):
        picked = self.selected_items()
        logger.debug(f"[UI][YColumnsDialog] accept_checked picked={picked}")
        if len(picked) > 3:
            QMessageBox.warning(self, "선택 오류", "최대 3개까지만 선택할 수 있습니다.")
            return
        self.accept()

    def selected_items(self) -> list[str]:
        return [i.text() for i in self.listw.selectedItems()]


# =========================================================
# TitleBar: 타이틀 클릭(좌/우) + 버튼(직관 UI)
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

        # ✅ 직관 버튼
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
        logger.debug(f"[UI][TitleBar] setText='{t}'")
        self.lbl.setText(t)

    def mousePressEvent(self, e):
        # 타이틀 영역 클릭: 좌/우 반으로 판단해도 되고, 버튼도 있으니 유지
        if e.button() != Qt.LeftButton:
            return
        # 버튼 위 클릭은 버튼이 처리
        if self.btn_left.geometry().contains(e.position().toPoint()) or self.btn_right.geometry().contains(e.position().toPoint()):
            return
        if e.position().x() < self.width() * 0.5:
            logger.debug("[UI][TitleBar] click left-half -> pick_left")
            self._on_pick_left()
        else:
            logger.debug("[UI][TitleBar] click right-half -> pick_right")
            self._on_pick_right()


# =========================================================
# Chart View: 밴드 하이라이트 + 축 클릭 다이얼로그 + 드래그줌 + crosshair
# + 알람 halo(QGraphicsEllipseItem)  -> (C++ crash 회피)
# =========================================================
class PowerChartView(QChartView):
    BAND = 14

    def __init__(self, area: "PlotArea", parent=None):
        super().__init__(parent)
        self.area = area
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setMouseTracking(True)
        self.setRubberBand(QChartView.NoRubberBand)

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

        # crosshair
        self._vline = QGraphicsLineItem()
        self._hline = QGraphicsLineItem()
        for ln in (self._vline, self._hline):
            ln.setZValue(10)
            ln.setPen(QPen(QColor("gray"), 1))
            ln.setVisible(False)

        # ✅ alarm halo (scene item)  -> series 조작 금지(크래시 회피)
        self._alarm_halo = QGraphicsEllipseItem()
        self._alarm_halo.setZValue(11)
        self._alarm_halo.setPen(QPen(QColor(255, 0, 0, 0), 0))
        self._alarm_halo.setBrush(QBrush(QColor(255, 0, 0, 60)))
        self._alarm_halo.setVisible(False)

        self._rubber = QRubberBand(QRubberBand.Rectangle, self)
        self._dragging = False
        self._drag_start = QPoint()
        self._hover_kind: str | None = None

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

    # ✅ 외부에서 halo 표시/숨김 호출
    def show_alarm_halo_at(self, series, point: QPointF, on: bool):
        self._ensure_scene_items()
        if not on or series is None:
            self._alarm_halo.setVisible(False)
            return
        try:
            pos = self.chart().mapToPosition(point, series)  # QPointF (chart item coord)
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
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)

        kind = self._hit_kind(e.position().toPoint())
        if kind == "x_axis":
            logger.debug("[UI][PowerChartView] x_axis band click -> open x range dialog")
            self.area.parent_panel._open_x_range_dialog()
            return
        if kind == "y_left":
            logger.debug("[UI][PowerChartView] y_left band click -> open y scale dialog (left)")
            self.area.parent_panel._open_y_scale_dialog(side="left")
            return
        if kind == "y_right":
            logger.debug("[UI][PowerChartView] y_right band click -> open y scale dialog (right)")
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
                logger.debug("[UI][PowerChartView] zoom drag ignored (no ref series)")
                return

            v0 = self.chart().mapToValue(QPointF(x0_scene, pa.center().y()), ref_series)
            v1 = self.chart().mapToValue(QPointF(x1_scene, pa.center().y()), ref_series)

            xmin = float(min(v0.x(), v1.x()))
            xmax = float(max(v0.x(), v1.x()))
            logger.debug(f"[UI][PowerChartView] zoom drag apply xmin={xmin}, xmax={xmax}")
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
        self.chart.legend().setVisible(True)
        self.chart.setBackgroundRoundness(10)
        self.chart.setMargins(QMargins(0, 0, 0, 0))

        self.view = PowerChartView(self)
        self.view.setChart(self.chart)

        # container
        self.widget = QWidget()
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
        self.alarm_series: QScatterSeries | None = None
        self._alarm_map: dict[int, tuple[str, str, str, str]] = {}  # ms -> (timeStr, text, stepNo, stepName)

        logger.debug("[PLOT][PlotArea] created")

    def _ref_series_for_mapping(self):
        if self.left_series:
            return self.left_series[0]
        if self.right_series:
            return self.right_series[0]
        if self.alarm_series:
            return self.alarm_series
        return None

    def clear(self):
        logger.debug("[PLOT][PlotArea] clear")
        # ✅ tooltip/halo 숨김 (hover 이벤트가 남아있어도 안전)
        try:
            QToolTip.hideText()
        except Exception:
            pass
        try:
            self.view.show_alarm_halo_at(self.alarm_series, QPointF(), False)
        except Exception:
            pass

        # ✅ hovered disconnect (C++ 크래시 방지)
        try:
            if self.alarm_series is not None:
                try:
                    self.alarm_series.hovered.disconnect(self.on_alarm_hovered)
                    logger.debug("[PLOT][Alarm] hovered disconnected")
                except Exception:
                    pass
        except Exception:
            pass

        self.chart.removeAllSeries()
        self.left_series.clear()
        self.right_series.clear()

        # axes 제거/초기화
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

        self.titlebar.setText("—")

    # ✅ 고정 메서드 슬롯 (closure 지양)
    def on_alarm_hovered(self, point: QPointF, state: bool):
        # series가 사라졌는데 신호가 들어오는 케이스 가드
        if self.alarm_series is None:
            return

        # halo(씬 아이템) 표시/숨김
        self.view.show_alarm_halo_at(self.alarm_series, point, state)

        if not state:
            QToolTip.hideText()
            return

        key = int(round(point.x()))
        info = self._alarm_map.get(key)
        if not info:
            return

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

        msg = "\n".join(lines)
        QToolTip.showText(QCursor.pos(), msg, self.view)


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

        logger.info(f"[MAIN] CsvPlotPanel init alarm_dir={self.alarm_dir}")
        self._build_ui()

    def _build_ui(self):
        logger.debug("[UI][CsvPlotPanel] build_ui")
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

        # 숨김 컨트롤(상태 유지용)
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
        self._rebuild_plot_layout()

        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        root.addWidget(self.status)

    # -----------------------------
    # layout
    # -----------------------------
    def _clear_grid_layout(self):
        logger.debug("[UI][Layout] clear_grid_layout")
        while self.plot_grid_layout.count():
            item = self.plot_grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.plot_grid_layout.removeWidget(w)
                w.setParent(None)

    def _rebuild_plot_layout(self):
        n = len(self._areas)
        logger.debug(f"[UI][Layout] rebuild_plot_layout graphs={n}")
        self._clear_grid_layout()
        if n == 0:
            return

        for r in range(20):
            self.plot_grid_layout.setRowStretch(r, 0)
        self.plot_grid_layout.setColumnStretch(0, 0)
        self.plot_grid_layout.setColumnStretch(1, 0)

        if n == 1:
            self.plot_grid_layout.addWidget(self._areas[0].widget, 0, 0, 1, 2)
            self.plot_grid_layout.setRowStretch(0, 1)
            self.plot_grid_layout.setColumnStretch(0, 1)
            self.plot_grid_layout.setColumnStretch(1, 1)
            return

        rows = (n + 1) // 2
        for i, area in enumerate(self._areas):
            r = i // 2
            c = i % 2
            self.plot_grid_layout.addWidget(area.widget, r, c)

        for r in range(rows):
            self.plot_grid_layout.setRowStretch(r, 1)
        self.plot_grid_layout.setColumnStretch(0, 1)
        self.plot_grid_layout.setColumnStretch(1, 1)

    # -----------------------------
    # add/delete
    # -----------------------------
    def add_graph(self):
        logger.info("[UI][Graph] add_graph")
        area = PlotArea(self)
        area.left_cols = self._selected_cols(self.y_combos)
        area.right_cols = self._selected_cols(self.y2_combos)
        logger.debug(f"[UI][Graph] new graph left_cols={area.left_cols}, right_cols={area.right_cols}")
        self._areas.append(area)
        self._rebuild_plot_layout()
        self.plot()

    def delete_graph(self, area: PlotArea):
        logger.info("[UI][Graph] delete_graph")
        if area not in self._areas:
            logger.debug("[UI][Graph] delete_graph ignored (not in list)")
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
        self._rebuild_plot_layout()
        self.plot()

    # -----------------------------
    # utils
    # -----------------------------
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
        logger.debug(f"[UI][Combos] set items count={len(items)}")
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

        logger.debug(
            f"[UI][Scale] enable_state left_mode={self.left_scale_mode.currentText()}, "
            f"right_mode={self.right_scale_mode.currentText()}"
        )

    def reset_left_scale(self):
        logger.info("[UI][Scale] reset_left_scale")
        self.left_scale_mode.setCurrentText("Auto")
        self.left_log.setChecked(False)
        self.left_ymin.setValue(0.0)
        self.left_ymax.setValue(0.0)
        self._update_scale_enable_state()
        self.plot()

    def reset_right_scale(self):
        logger.info("[UI][Scale] reset_right_scale")
        self.right_scale_mode.setCurrentText("Auto")
        self.right_log.setChecked(False)
        self.right_ymin.setValue(0.0)
        self.right_ymax.setValue(0.0)
        self._update_scale_enable_state()
        self.plot()

    def clear_left(self):
        logger.info("[UI][Y] clear_left")
        for cb in self.y_combos:
            cb.setCurrentText(NONE_ITEM)
        self.status.setText("Left(Y) 선택 해제")
        self.plot()

    def clear_right(self):
        logger.info("[UI][Y] clear_right")
        for cb in self.y2_combos:
            cb.setCurrentText(NONE_ITEM)
        self.status.setText("Right(Y2) 선택 해제")
        self.plot()

    # -----------------------------
    # Step detect
    # -----------------------------
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
        logger.info(f"[STEP] step_no_col={self._step_no_col}, step_name_col={self._step_name_col}")

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
        logger.info(f"[MAIN] Load CSV: {path}")

        try:
            df = pd.read_csv(path)
            if df.empty:
                raise ValueError("CSV가 비어 있습니다.")
        except Exception as e:
            logger.exception(f"[MAIN] CSV 로드 실패: {path}")
            QMessageBox.critical(self, "CSV 로드 실패", f"{e}")
            return

        df = self._normalize_columns(df)
        self.df = df
        logger.debug(f"[MAIN] columns={list(df.columns)}")

        self._detect_step_columns()

        self.x_col = df.columns[0]
        x_series = df[self.x_col]
        x_dt = pd.to_datetime(x_series, errors="coerce")
        valid_dt = int(x_dt.notna().sum())
        logger.debug(f"[MAIN] X col='{self.x_col}', datetime_valid={valid_dt}/{len(x_series)}")

        if valid_dt > 0 and valid_dt >= int(len(x_series) * 0.8):
            self.x_is_datetime = True
            df["_x"] = x_dt
            self._setup_x_range_datetime(df["_x"])
            self.range_stack.setCurrentWidget(self.dt_widget)
            logger.info("[MAIN] X axis = datetime")
        else:
            self.x_is_datetime = False
            x_num = pd.to_numeric(x_series, errors="coerce")
            if x_num.notna().sum() == 0:
                df["_x"] = range(len(df))
                self.x_col = "(index)"
                self._setup_x_range_numeric(df["_x"])
                logger.info("[MAIN] X axis = index")
            else:
                df["_x"] = x_num
                self._setup_x_range_numeric(df["_x"])
                logger.info("[MAIN] X axis = numeric")
            self.range_stack.setCurrentWidget(self.num_widget)

        y_candidates: list[str] = []
        for c in df.columns:
            if c in [self.x_col, "_x"]:
                continue
            s = pd.to_numeric(df[c], errors="coerce")
            if s.notna().any():
                y_candidates.append(c)

        self._y_candidates = y_candidates[:]
        logger.info(f"[MAIN] y_candidates={len(self._y_candidates)}")
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

        # 전역 기본값
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
            logger.warning("[MAIN] x_dt empty -> set now")
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
        logger.debug(
            f"[MAIN] x_range datetime qmin={qmin.toString('yyyy-MM-dd HH:mm:ss')}, "
            f"qmax={qmax.toString('yyyy-MM-dd HH:mm:ss')}"
        )

    def _setup_x_range_numeric(self, x_num: pd.Series):
        x_valid = pd.to_numeric(x_num, errors="coerce").dropna()
        if x_valid.empty:
            self.num_start.setRange(0, 0)
            self.num_end.setRange(0, 0)
            self.num_start.setValue(0)
            self.num_end.setValue(0)
            logger.warning("[MAIN] x_num empty -> set 0..0")
            return
        xmin = float(x_valid.min())
        xmax = float(x_valid.max())
        self.num_start.setRange(xmin, xmax)
        self.num_end.setRange(xmin, xmax)
        self.num_start.setValue(xmin)
        self.num_end.setValue(xmax)
        logger.debug(f"[MAIN] x_range numeric xmin={xmin}, xmax={xmax}")

    def _clear_plot_all(self):
        logger.debug("[PLOT] clear_plot_all")
        for a in self._areas:
            a.clear()

    # -----------------------------
    # Alarm helpers
    # -----------------------------
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
        unit = f"TUBE{suffix:02d}"
        logger.debug(f"[ALARM] target_tube from {tube_col}={tube_id} -> {unit}")
        return unit

    def _load_alarm_events(self) -> pd.DataFrame | None:
        yyMMdd = self._get_alarm_date_yyMMdd()
        if yyMMdd is None:
            logger.debug("[ALARM] yyMMdd not available")
            return None
        alarm_path = self.alarm_dir / f"Alarm_{yyMMdd}.csv"
        if not alarm_path.exists():
            logger.debug(f"[ALARM] file not found: {alarm_path}")
            return None

        logger.info(f"[ALARM] load {alarm_path}")
        try:
            adf = pd.read_csv(alarm_path)
            if adf.empty:
                return None
        except Exception:
            logger.exception(f"[ALARM] Alarm load failed: {alarm_path}")
            return None

        adf = self._normalize_columns(adf)
        required = {"Time", "UnitID", "Set", "Text"}
        if not required.issubset(set(adf.columns)):
            logger.error(f"[ALARM] Missing columns. need={required}, got={set(adf.columns)}")
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
            logger.debug("[ALARM] filtered empty")
            return None
        filtered["Text"] = filtered["Text"].astype(str).fillna("")
        filtered = filtered.sort_values("_t")
        logger.info(f"[ALARM] filtered events={len(filtered)}")
        return filtered

    # -----------------------------
    # Dialog openers
    # -----------------------------
    def _open_y_scale_dialog(self, side: str):
        logger.debug(f"[UI][Dialog] open_y_scale_dialog side={side}")
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
            logger.debug("[UI][Dialog] y_scale canceled")
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
        logger.debug("[UI][Dialog] open_x_range_dialog")
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
            logger.debug("[UI][Dialog] x_range canceled")
            return

        v0, v1 = dlg.values()
        if self.x_is_datetime:
            self.dt_start.setDateTime(v0)
            self.dt_end.setDateTime(v1)
        else:
            self.num_start.setValue(float(v0))
            self.num_end.setValue(float(v1))

        logger.info("[UI][Dialog] x_range applied -> replot")
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

        logger.debug(f"[UI][Dialog] open_y_columns_dialog side={side}, cur={cur}")
        dlg = YColumnsDialog(
            title=f"{side.upper()} Y Columns",
            items=items,
            selected=cur,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            logger.debug("[UI][Dialog] y_columns canceled")
            return

        selected = dlg.selected_items()[:3]
        logger.info(f"[UI][Dialog] y_columns selected side={side}: {selected}")

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

    # -----------------------------
    # color helper
    # -----------------------------
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

    # -----------------------------
    # zoom apply
    # -----------------------------
    def _apply_zoom_from_chart(self, area: PlotArea, xmin: float, xmax: float):
        if xmin > xmax:
            xmin, xmax = xmax, xmin
        logger.info(f"[PLOT][Zoom] apply xmin={xmin}, xmax={xmax}, datetime={self.x_is_datetime}")
        if self.x_is_datetime:
            q0 = QDateTime.fromMSecsSinceEpoch(int(xmin))
            q1 = QDateTime.fromMSecsSinceEpoch(int(xmax))
            self.dt_start.setDateTime(q0)
            self.dt_end.setDateTime(q1)
        else:
            self.num_start.setValue(float(xmin))
            self.num_end.setValue(float(xmax))
        self.plot()

    # -----------------------------
    # minmax helper
    # -----------------------------
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
            logger.debug("[PLOT] plot ignored (no df/csv)")
            return
        df0 = self.df
        if "_x" not in df0.columns:
            self.status.setText("내부 x축 컬럼(_x)이 없습니다. CSV를 다시 로드해주세요.")
            logger.error("[PLOT] missing _x")
            return

        logger.debug(f"[PLOT] start graphs={len(self._areas)}")

        df = df0.copy()
        if self.x_is_datetime:
            start = pd.Timestamp(self.dt_start.dateTime().toPython())
            end = pd.Timestamp(self.dt_end.dateTime().toPython())
            df = df[df["_x"].between(start, end, inclusive="both")]
            logger.debug(f"[PLOT] x_filter datetime start={start}, end={end}, rows={len(df)}")
        else:
            start = float(self.num_start.value())
            end = float(self.num_end.value())
            if start > end:
                start, end = end, start
            df = df[df["_x"].between(start, end, inclusive="both")]
            logger.debug(f"[PLOT] x_filter numeric start={start}, end={end}, rows={len(df)}")

        if df.empty:
            self.status.setText("선택한 X 범위에 데이터가 없습니다.")
            logger.warning("[PLOT] empty after x filter")
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
            logger.warning("[PLOT] empty after _x dropna")
            self._clear_plot_all()
            return

        events = self._load_alarm_events()
        if events is not None and not events.empty and self.x_is_datetime:
            start_ev = pd.Timestamp(self.dt_start.dateTime().toPython())
            end_ev = pd.Timestamp(self.dt_end.dateTime().toPython())
            events = events[events["_t"].between(start_ev, end_ev, inclusive="both")]
            logger.debug(f"[ALARM] range-filtered events={len(events)}")
        else:
            events = None

        for idx, area in enumerate(self._areas):
            logger.debug(f"[PLOT] area[{idx}] render")
            area.clear()

            left_cols = [c for c in area.left_cols if c in df.columns]
            right_cols = [c for c in area.right_cols if c in df.columns]

            logger.debug(f"[PLOT] area[{idx}] left_cols={left_cols}, right_cols={right_cols}")

            area.titlebar.setText(self.csv_path.name)

            if not left_cols and not right_cols:
                continue

            sub_cols = ["_x"] + left_cols + right_cols
            sub_cols = list(dict.fromkeys(sub_cols))
            dfa = df.loc[:, sub_cols].copy()
            dfa = dfa.dropna(subset=["_x"] + left_cols + right_cols, how="any")
            if dfa.empty:
                logger.debug(f"[PLOT] area[{idx}] skip (dfa empty)")
                continue

            chart = area.chart
            chart.setTitle("")
            chart.legend().setVisible(True)

            # axes
            if self.x_is_datetime:
                ax_x = QDateTimeAxis()
                ax_x.setFormat("MM-dd HH:mm")
                ax_x.setTitleText("")  # ✅ 축 타이틀 숨김
                ax_x.setTickCount(6)
                ax_x.setRange(self.dt_start.dateTime(), self.dt_end.dateTime())
                area.axis_x_dt = ax_x
                chart.addAxis(ax_x, Qt.AlignBottom)
            else:
                ax_x = QValueAxis()
                ax_x.setTitleText("")  # ✅ 축 타이틀 숨김
                x0 = float(self.num_start.value())
                x1 = float(self.num_end.value())
                if x0 > x1:
                    x0, x1 = x1, x0
                ax_x.setRange(x0, x1)
                ax_x.setTickCount(6)
                area.axis_x_num = ax_x
                chart.addAxis(ax_x, Qt.AlignBottom)

            ax_l = QValueAxis()
            ax_l.setTitleText("")  # ✅ Left 타이틀 숨김
            ax_l.setTickCount(6)
            area.axis_y_left = ax_l
            chart.addAxis(ax_l, Qt.AlignLeft)

            ax_r = QValueAxis()
            ax_r.setTitleText("")  # ✅ Right 타이틀 숨김
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
                    logger.debug(f"[PLOT] manual left y range={ymin}..{ymax}")

            if self.right_scale_mode.currentText() == "Manual":
                ymin = float(self.right_ymin.value())
                ymax = float(self.right_ymax.value())
                if ymin != ymax:
                    if ymin > ymax:
                        ymin, ymax = ymax, ymin
                    ax_r.setRange(ymin, ymax)
                    logger.debug(f"[PLOT] manual right y range={ymin}..{ymax}")

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
                logger.debug(f"[ALARM] area[{idx}] markers={len(area._alarm_map)}")

            area.view._layout_bands()

        self.status.setText(
            f"표시 중: {len(df)} rows | Graphs={len(self._areas)} | (Title/L·R 버튼=요소, X click=range, X drag=zoom, Y band=scale)"
        )
        logger.debug("[PLOT] done")


# =========================================================
# MainWindow
# =========================================================
class MainWindow(QMainWindow):
    def __init__(self, root_dir: str | Path, alarm_dir: str | Path):
        super().__init__()
        self.root_dir = Path(root_dir).resolve()
        self.alarm_dir = Path(alarm_dir).resolve()
        self.setWindowTitle("병곤이가만듦")

        logger.info(f"[APP] MainWindow init root_dir={self.root_dir}, alarm_dir={self.alarm_dir}")

        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        w = int(geo.width() * 0.90)
        h = int(geo.height() * 0.85)
        self.resize(w, h)
        self.move(geo.x() + int(geo.width() * 0.05), geo.y() + int(geo.height() * 0.05))

        splitter = QSplitter(Qt.Horizontal)

        self.model = QFileSystemModel()
        self.model.setRootPath(str(self.root_dir))
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.model.setNameFilters(["*.csv"])
        self.model.setNameFilterDisables(False)

        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.index(str(self.root_dir)))
        for col in range(1, self.model.columnCount()):
            self.tree.hideColumn(col)
        self.tree.setAnimated(True)
        self.tree.doubleClicked.connect(self.on_tree_double_clicked)
        splitter.addWidget(self.tree)

        self.plot_panel = CsvPlotPanel(alarm_dir=self.alarm_dir)
        splitter.addWidget(self.plot_panel)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 6)
        self.setCentralWidget(splitter)

        logger.info(f"[APP] ready")

    def on_tree_double_clicked(self, index):
        path = Path(self.model.filePath(index))
        logger.debug(f"[UI][Tree] double_clicked path={path}")
        if path.is_dir():
            if self.tree.isExpanded(index):
                self.tree.collapse(index)
                logger.debug("[UI][Tree] collapse")
            else:
                self.tree.expand(index)
                logger.debug("[UI][Tree] expand")
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
