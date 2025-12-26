import sys
from pathlib import Path

import pandas as pd

from PySide6.QtCore import Qt, QDir, QDateTime
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QTreeView, QFileSystemModel, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QStackedWidget,
    QDateTimeEdit, QDoubleSpinBox, QMessageBox, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QListWidget, QListWidgetItem,
    QAbstractItemView, QGridLayout
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch
import matplotlib.dates as mdates

from utils.logger import logger

NONE_ITEM = "(None)"


class YScaleDialog(QDialog):
    """Left/Right Y 축 스케일 설정 다이얼로그 (Auto/Manual + Min/Max + Linear/Log)."""

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
        form.addRow("Scale", self.log_cb)
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


class YColumnsDialog(QDialog):
    """Y 축에 그릴 컬럼을 최대 3개까지 선택하는 다이얼로그."""

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
        if len(picked) > 3:
            QMessageBox.warning(self, "선택 오류", "최대 3개까지만 선택할 수 있습니다.")
            return
        self.accept()

    def selected_items(self) -> list[str]:
        return [i.text() for i in self.listw.selectedItems()]


class PlotArea:
    """한 개의 그래프(figure+canvas)와 상태를 묶어서 관리."""

    def __init__(self, parent_panel: "CsvPlotPanel"):
        self.parent_panel = parent_panel

        self.fig = Figure()
        self.canvas = FigureCanvas(self.fig)

        # ✅ 그래프 컨테이너(우측 상단 삭제 버튼 포함)
        self.widget = QWidget()
        wlay = QVBoxLayout(self.widget)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(0)

        topbar = QHBoxLayout()
        topbar.setContentsMargins(4, 4, 4, 0)
        topbar.addStretch(1)

        self.btn_delete = QPushButton("✕")
        self.btn_delete.setToolTip("이 그래프 삭제")
        self.btn_delete.setFixedSize(26, 22)
        self.btn_delete.setStyleSheet(
            "QPushButton{border:1px solid rgba(0,0,0,0.2); border-radius:6px; background:rgba(255,255,255,0.8);}"
            "QPushButton:hover{background:rgba(255,255,255,1.0);}"
            "QPushButton:pressed{background:rgba(230,230,230,1.0);}"
        )
        self.btn_delete.clicked.connect(lambda: self.parent_panel.delete_graph(self))
        topbar.addWidget(self.btn_delete, 0, Qt.AlignRight)

        wlay.addLayout(topbar)
        wlay.addWidget(self.canvas, 1)

        # plot axis refs
        self.ax = None
        self.ax2 = None

        # ✅ per-graph Y 선택
        self.left_cols: list[str] = []
        self.right_cols: list[str] = []

        # alarm hover
        self.alarm_scatter = None
        self.alarm_texts: list[str] = []
        self.alarm_times: list[pd.Timestamp] = []
        # ✅ Step info per alarm point
        self.alarm_step_nos: list[str] = []
        self.alarm_step_names: list[str] = []

        self.hover_annot = None
        self.alarm_hover_bg = None

        # crosshair
        self.vline = None
        self.hline_left = None
        self.hline_right = None

        # clickable hover
        self.hover_kind: str | None = None
        self.label_default = {"x": None, "yl": None, "yr": None}
        self.spine_default = {"bottom": None, "left": None, "right": None}
        self.hover_patch_bottom = None
        self.hover_patch_left = None
        self.hover_patch_right = None

        # ✅ drag로 X 범위 선택
        self.drag_active = False
        self.drag_x0 = None
        self.drag_span = None

        # connect mpl events
        self.canvas.mpl_connect("motion_notify_event", lambda e: self.parent_panel._on_motion(e, self))
        self.canvas.mpl_connect("button_press_event", lambda e: self.parent_panel._on_press(e, self))
        self.canvas.mpl_connect("button_release_event", lambda e: self.parent_panel._on_release(e, self))

    def clear(self):
        self.fig.clear()
        self.ax = None
        self.ax2 = None

        self.alarm_scatter = None
        self.alarm_texts = []
        self.alarm_times = []
        self.alarm_step_nos = []
        self.alarm_step_names = []
        self.hover_annot = None
        self.alarm_hover_bg = None

        self.vline = None
        self.hline_left = None
        self.hline_right = None

        self.hover_kind = None
        self.label_default = {"x": None, "yl": None, "yr": None}
        self.spine_default = {"bottom": None, "left": None, "right": None}
        self.hover_patch_bottom = None
        self.hover_patch_left = None
        self.hover_patch_right = None

        self.drag_active = False
        self.drag_x0 = None
        self.drag_span = None

        self.canvas.draw_idle()


class CsvPlotPanel(QWidget):
    def __init__(self, alarm_dir: Path, parent=None):
        super().__init__(parent)

        self.alarm_dir = Path(alarm_dir)

        self.df: pd.DataFrame | None = None
        self.csv_path: Path | None = None
        self.x_col: str | None = None
        self.x_is_datetime: bool = False

        # Y 후보 저장
        self._y_candidates: list[str] = []

        # ✅ Step 컬럼 캐시
        self._step_no_col: str | None = None
        self._step_name_col: str | None = None

        # ✅ 그래프 영역(여러개)
        self._areas: list[PlotArea] = []

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

        # ✅ 컨트롤 컨테이너
        self.ctrl_widget = QWidget()
        ctrl_layout = QVBoxLayout(self.ctrl_widget)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(6)

        # =========================
        # Y(Left)
        # =========================
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

        # =========================
        # Y2(Right)
        # =========================
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

        # =========================
        # X range
        # =========================
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

        # =========================
        # Y scale
        # =========================
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

        self.right_log = QCheckBox("Log")
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

        # =========================
        # Plot container (✅ GridLayout로 안전하게)
        # =========================
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

        # 기본 1개 그래프 생성
        a0 = PlotArea(self)
        self._areas.append(a0)
        self._rebuild_plot_layout()

        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        root.addWidget(self.status)

        # 컨트롤 숨김
        self.ctrl_widget.setVisible(False)

    # -----------------------------
    # ✅ Layout
    # -----------------------------
    def _clear_grid_layout(self):
        while self.plot_grid_layout.count():
            item = self.plot_grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.plot_grid_layout.removeWidget(w)
                # ✅ deleteLater 금지: Qt ownership 꼬임 방지
                w.setParent(None)

    def _rebuild_plot_layout(self):
        """- 1개: 전체 span
        - 2개 이상: 2열 n행 (row-major)
        """
        self._clear_grid_layout()

        n = len(self._areas)
        if n == 0:
            return

        # stretch 초기화
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
    # ✅ Add/Delete graph
    # -----------------------------
    def add_graph(self):
        area = PlotArea(self)
        # 새 그래프는 현재 전역 콤보값을 초기값으로 "복사"(이후에는 개별 변경)
        area.left_cols = self._selected_cols(self.y_combos)
        area.right_cols = self._selected_cols(self.y2_combos)

        self._areas.append(area)
        self._rebuild_plot_layout()
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
            pass

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
        # ✅ 전역 콤보는 "기본값" 역할만 유지
        for cb in self.y_combos:
            cb.setCurrentText(NONE_ITEM)
        self.status.setText("Left(Y) 선택 해제")
        # 전역 기본값을 모든 그래프에 강제로 적용하지 않음
        self.plot()

    def clear_right(self):
        for cb in self.y2_combos:
            cb.setCurrentText(NONE_ITEM)
        self.status.setText("Right(Y2) 선택 해제")
        self.plot()

    # -----------------------------
    # ✅ Step helpers (NEW)
    # -----------------------------
    def _detect_step_columns(self):
        """공정로그에서 Step No / Step Name 컬럼명을 최대한 유연하게 찾는다."""
        self._step_no_col = None
        self._step_name_col = None

        if self.df is None:
            return

        cols = list(self.df.columns)
        lower_map = {c: str(c).strip().lower() for c in cols}

        def pick(candidates: list[str]) -> str | None:
            # 1) 정확히 일치
            for c in cols:
                if lower_map[c] in candidates:
                    return c

            # 2) 공백/특수문자 제거 후 일치/포함
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
        """알람 발생 시각 t에 해당하는 공정로그의 Step 정보를 반환.

        정책:
        - x가 datetime일 때만 동작
        - _x 기준 정렬 후, t 시각 '이전(<=t) 중 가장 가까운 row' 사용
        """
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

        xs = pd.to_datetime(dd["_x"], errors="coerce")
        xs = xs.dropna()
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

        # ✅ Step 컬럼 탐지(NEW)
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

        # 전역 기본값
        self.y_combos[0].setCurrentIndex(1)

        # 기존 그래프들은 전역 기본값을 "초기값"으로만 맞춰줌 (개별 설정 시작점)
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

    def _apply_y_scale(self, ax, mode: str, ymin_spin: QDoubleSpinBox, ymax_spin: QDoubleSpinBox, log_chk: QCheckBox):
        ax.set_yscale("log" if log_chk.isChecked() else "linear")

        if mode == "Manual":
            ymin = float(ymin_spin.value())
            ymax = float(ymax_spin.value())
            if ymin == ymax:
                return
            if ymin > ymax:
                ymin, ymax = ymax, ymin
            ax.set_ylim(ymin, ymax)
        else:
            ax.relim()
            ax.autoscale(axis="y")

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

        # TubeID / Tube ID 둘 다 지원
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
            return None

        filtered["Text"] = filtered["Text"].astype(str).fillna("")
        return filtered.sort_values("_t")

    def _add_alarm_markers(self, area: PlotArea, ax):
        events = self._load_alarm_events()
        if events is None or events.empty:
            return

        if self.x_is_datetime:
            start = pd.Timestamp(self.dt_start.dateTime().toPython())
            end = pd.Timestamp(self.dt_end.dateTime().toPython())
            events = events[events["_t"].between(start, end, inclusive="both")]
        if events.empty:
            return

        # ✅ 항상 X축 바로 위(하단 2% 부근)
        ymin, ymax = ax.get_ylim()
        y_marker = ymin + (ymax - ymin) * 0.02

        xs = events["_t"].tolist()
        ys = [y_marker] * len(xs)

        area.alarm_texts = events["Text"].tolist()
        area.alarm_times = [pd.Timestamp(t) for t in events["_t"].tolist()]

        # ✅ 알람 발생 시점 Step 정보 채우기 (NEW)
        area.alarm_step_nos = []
        area.alarm_step_names = []
        for t in area.alarm_times:
            sn, sname = self._step_info_at(pd.Timestamp(t))
            area.alarm_step_nos.append("" if sn is None else sn)
            area.alarm_step_names.append("" if sname is None else sname)

        area.alarm_scatter = ax.scatter(
            xs, ys,
            s=60,
            c="red",              # 또는 color="red"
            edgecolors="black",
            linewidths=0.6,
            picker=8,
            zorder=6,
        )

        area.hover_annot = ax.annotate(
            "",
            xy=(0, 0),
            xytext=(12, 12),
            textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w", alpha=0.95),
            arrowprops=dict(arrowstyle="->"),
            alpha=1,
        )
        area.hover_annot.set_visible(False)

        # hover 시 둥근 반투명 강조
        area.alarm_hover_bg = ax.scatter(
            [], [],
            s=300,
            marker="o",
            alpha=0.25,
            linewidths=0,
            zorder=5,
            visible=False,
        )

    # -----------------------------
    # Drag(X zoom): press/move/release
    # -----------------------------
    def _on_press(self, event, area: PlotArea):
        # 1) 축/라벨 클릭 다이얼로그 먼저
        if self._on_click_dialogs(event, area):
            return

        # 좌클릭만
        if getattr(event, "button", None) != 1:
            return

        # plot 내부에서만 시작
        if area.ax is None or event.inaxes not in (area.ax, area.ax2):
            return

        if event.xdata is None:
            return

        area.drag_active = True
        area.drag_x0 = float(event.xdata)

        # span 초기화
        try:
            if area.drag_span is not None:
                area.drag_span.remove()
        except Exception:
            pass

        # ✅ 반투명 선택 영역
        area.drag_span = area.ax.axvspan(
            area.drag_x0,
            area.drag_x0,
            ymin=0.0,
            ymax=1.0,
            alpha=0.22,
            facecolor="gray",
            edgecolor="none",
            zorder=9,
        )
        area.canvas.draw_idle()

    def _on_release(self, event, area: PlotArea):
        if not area.drag_active:
            return

        area.drag_active = False

        if area.drag_x0 is None or event.xdata is None:
            try:
                if area.drag_span is not None:
                    area.drag_span.remove()
            except Exception:
                pass
            area.drag_span = None
            area.drag_x0 = None
            return

        x0 = float(area.drag_x0)
        x1 = float(event.xdata)
        area.drag_x0 = None

        # span 제거
        try:
            if area.drag_span is not None:
                area.drag_span.remove()
        except Exception:
            pass
        area.drag_span = None

        if abs(x1 - x0) < 1e-12:
            return

        xmin, xmax = (x0, x1) if x0 < x1 else (x1, x0)

        # ✅ X 범위 업데이트 (전역)
        if self.x_is_datetime:
            dt0 = mdates.num2date(xmin)
            dt1 = mdates.num2date(xmax)

            p0 = pd.Timestamp(dt0).tz_localize(None)
            p1 = pd.Timestamp(dt1).tz_localize(None)

            q0 = QDateTime.fromString(p0.strftime("%Y-%m-%d %H:%M:%S"), "yyyy-MM-dd HH:mm:ss")
            q1 = QDateTime.fromString(p1.strftime("%Y-%m-%d %H:%M:%S"), "yyyy-MM-dd HH:mm:ss")

            self.dt_start.setDateTime(q0)
            self.dt_end.setDateTime(q1)
        else:
            self.num_start.setValue(float(xmin))
            self.num_end.setValue(float(xmax))

        self.plot()

    def _on_motion(self, event, area: PlotArea):
        # drag span update
        if area.drag_active and area.ax is not None and area.drag_x0 is not None and event.xdata is not None:
            x0 = float(area.drag_x0)
            x1 = float(event.xdata)
            xmin, xmax = (x0, x1) if x0 < x1 else (x1, x0)

            try:
                if area.drag_span is not None:
                    area.drag_span.remove()
            except Exception:
                pass

            area.drag_span = area.ax.axvspan(
                xmin, xmax,
                ymin=0.0, ymax=1.0,
                alpha=0.22,
                facecolor="gray",
                edgecolor="none",
                zorder=9,
            )
            area.canvas.draw_idle()

        self._update_crosshair(event, area)
        self._update_alarm_tooltip(event, area)
        self._update_clickable_hover(event, area)

    # -----------------------------
    # motion: crosshair
    # -----------------------------
    def _update_crosshair(self, event, area: PlotArea):
        if area.ax is None or area.vline is None or area.hline_left is None:
            return

        if event.inaxes != area.ax and event.inaxes != area.ax2:
            changed = False
            for ln in (area.vline, area.hline_left, area.hline_right):
                if ln is not None and ln.get_visible():
                    ln.set_visible(False)
                    changed = True
            if changed:
                area.canvas.draw_idle()
            return

        if event.xdata is None or event.ydata is None:
            return

        area.vline.set_xdata([event.xdata, event.xdata])
        area.vline.set_visible(True)

        if event.inaxes == area.ax:
            area.hline_left.set_ydata([event.ydata, event.ydata])
            area.hline_left.set_visible(True)
            if area.hline_right is not None:
                area.hline_right.set_visible(False)
        elif event.inaxes == area.ax2 and area.hline_right is not None:
            area.hline_right.set_ydata([event.ydata, event.ydata])
            area.hline_right.set_visible(True)
            area.hline_left.set_visible(False)

        area.canvas.draw_idle()

    # -----------------------------
    # motion: alarm tooltip + hover bg
    # -----------------------------
    def _update_alarm_tooltip(self, event, area: PlotArea):
        if area.alarm_scatter is None or area.hover_annot is None:
            return

        if event.inaxes is None:
            changed = False
            if area.hover_annot.get_visible():
                area.hover_annot.set_visible(False)
                changed = True
            if area.alarm_hover_bg is not None and area.alarm_hover_bg.get_visible():
                area.alarm_hover_bg.set_visible(False)
                changed = True
            if changed:
                area.canvas.draw_idle()
            return

        cont, ind = area.alarm_scatter.contains(event)
        if not cont or "ind" not in ind or len(ind["ind"]) == 0:
            changed = False
            if area.hover_annot.get_visible():
                area.hover_annot.set_visible(False)
                changed = True
            if area.alarm_hover_bg is not None and area.alarm_hover_bg.get_visible():
                area.alarm_hover_bg.set_visible(False)
                changed = True
            if changed:
                area.canvas.draw_idle()
            return

        i = int(ind["ind"][0])

        try:
            offsets = area.alarm_scatter.get_offsets()
            x_pt, y_pt = offsets[i]
            t = area.alarm_times[i]
            txt = area.alarm_texts[i]
        except Exception:
            return

        # ✅ Step info (NEW)
        step_no = ""
        step_name = ""
        try:
            step_no = area.alarm_step_nos[i] if i < len(area.alarm_step_nos) else ""
            step_name = area.alarm_step_names[i] if i < len(area.alarm_step_names) else ""
        except Exception:
            pass

        area.hover_annot.xy = (x_pt, y_pt)

        lines = [pd.Timestamp(t).strftime("%Y-%m-%d %H:%M:%S")]
        if step_no or step_name:
            if step_no and step_name:
                lines.append(f"Step: {step_no} | {step_name}")
            elif step_no:
                lines.append(f"Step: {step_no}")
            else:
                lines.append(f"Step: {step_name}")
        if txt:
            lines.append(str(txt))

        msg = "\n".join(lines)

        area.hover_annot.set_text(msg)
        area.hover_annot.set_visible(True)

        if area.alarm_hover_bg is not None:
            area.alarm_hover_bg.set_offsets([[x_pt, y_pt]])
            area.alarm_hover_bg.set_visible(True)

        area.canvas.draw_idle()

    # -----------------------------
    # click: 축/라벨 다이얼로그
    # -----------------------------
    def _on_click_dialogs(self, event, area: PlotArea) -> bool:
        if area.ax is None:
            return False

        ax = area.ax
        ax2 = area.ax2

        # ✅ 라벨 클릭: 해당 그래프만 설정
        try:
            if ax.yaxis.label.contains(event)[0]:
                self._open_y_columns_dialog(side="left", area=area)
                return True
        except Exception:
            pass

        try:
            if ax2 is not None and ax2.yaxis.label.contains(event)[0]:
                self._open_y_columns_dialog(side="right", area=area)
                return True
        except Exception:
            pass

        try:
            if ax.xaxis.label.contains(event)[0]:
                self._open_x_range_dialog()
                return True
        except Exception:
            pass

        bbox = ax.bbox
        pad = 14

        if (bbox.x0 <= event.x <= bbox.x1) and (abs(event.y - bbox.y0) <= pad):
            self._open_x_range_dialog()
            return True

        if (bbox.y0 <= event.y <= bbox.y1) and (abs(event.x - bbox.x0) <= pad):
            self._open_y_scale_dialog(side="left")
            return True

        if ax2 is not None:
            if (bbox.y0 <= event.y <= bbox.y1) and (abs(event.x - bbox.x1) <= pad):
                self._open_y_scale_dialog(side="right")
                return True

        return False

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

        selected = dlg.selected_items()[:3]

        if area is not None:
            if side == "left":
                area.left_cols = selected
            else:
                area.right_cols = selected
        else:
            # 전역 기본 콤보 반영(원하면)
            combos = self.y_combos if side == "left" else self.y2_combos
            for i, cb in enumerate(combos):
                cb.setCurrentText(selected[i] if i < len(selected) else NONE_ITEM)

        self.plot()

    # -----------------------------
    # ✅ 색상: 한 그래프 내에서 이미 사용한 색은 재사용하지 않기
    # -----------------------------
    @staticmethod
    def _unique_color_generator():
        from matplotlib import rcParams

        cyc = rcParams.get("axes.prop_cycle", None)
        if cyc is None:
            base = ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]
        else:
            try:
                base = [d.get("color") for d in cyc]
                base = [c for c in base if c is not None]
            except Exception:
                base = ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]

        i = 0
        while True:
            if i < len(base):
                yield base[i]
            else:
                k = i - len(base)
                hue = (k * 0.61803398875) % 1.0
                yield (hue, 0.55, 0.85)
            i += 1

    # -----------------------------
    # X sync
    # -----------------------------
    def _sync_x_axes(self):
        """여러 그래프가 있을 때 X축 표시 범위를 동일하게 맞춘다."""
        if not self._areas:
            return

        if self.x_is_datetime:
            start = pd.Timestamp(self.dt_start.dateTime().toPython())
            end = pd.Timestamp(self.dt_end.dateTime().toPython())
            x0 = mdates.date2num(start.to_pydatetime())
            x1 = mdates.date2num(end.to_pydatetime())
        else:
            x0 = float(self.num_start.value())
            x1 = float(self.num_end.value())

        if x0 > x1:
            x0, x1 = x1, x0

        for a in self._areas:
            if a.ax is None:
                continue
            try:
                a.ax.set_xlim(x0, x1)
                if a.ax2 is not None:
                    a.ax2.set_xlim(x0, x1)
                a.canvas.draw_idle()
            except Exception:
                pass

    # -----------------------------
    # Plot
    # -----------------------------
    def plot(self):
        if self.df is None or self.csv_path is None:
            return

        df0 = self.df
        if "_x" not in df0.columns:
            self.status.setText("내부 x축 컬럼(_x)이 없습니다. CSV를 다시 로드해주세요.")
            return

        # X 범위로 데이터 슬라이스
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

        # 각 area별로 필요한 컬럼을 모아서 최소 subset
        need_cols = {"_x"}
        for a in self._areas:
            # 비어있으면 전역 기본값으로 한 번만 채움(기본값 역할)
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

        # 그래프마다 dropna 기준이 달라야 하므로(각자 선택 컬럼), 여기서는 _x만 보장
        df = df.dropna(subset=["_x"], how="any")
        if df.empty:
            self.status.setText("유효한 데이터가 없습니다 (NaN 제거 후)")
            self._clear_plot_all()
            return

        for area in self._areas:
            area.clear()

            area.ax = area.fig.add_subplot(111)
            ax = area.ax
            area.ax2 = ax.twinx()
            ax2 = area.ax2

            left_cols = [c for c in area.left_cols if c in df.columns]
            right_cols = [c for c in area.right_cols if c in df.columns]

            if not left_cols and not right_cols:
                # 아무것도 없으면 빈 플롯
                ax.set_title(self.csv_path.name)
                area.canvas.draw_idle()
                continue

            # area별 데이터 정리
            sub_cols = ["_x"] + left_cols + right_cols
            sub_cols = list(dict.fromkeys(sub_cols))
            dfa = df.loc[:, sub_cols].copy()
            dfa = dfa.dropna(subset=["_x"] + left_cols + right_cols, how="any")
            if dfa.empty:
                ax.set_title(self.csv_path.name)
                area.canvas.draw_idle()
                continue

            used_colors = set()
            color_gen = self._unique_color_generator()

            def pick_color():
                while True:
                    c = next(color_gen)
                    if c not in used_colors:
                        used_colors.add(c)
                        return c

            for c in left_cols:
                ax.plot(dfa["_x"], dfa[c], label=f"L:{c}", color=pick_color())
            ax.set_xlabel(self.x_col if self.x_col else "X")
            ax.set_ylabel("Left Y")

            if right_cols:
                for c in right_cols:
                    ax2.plot(dfa["_x"], dfa[c], label=f"R:{c}", color=pick_color())
            ax2.set_ylabel("Right Y2")

            try:
                area.label_default["x"] = (ax.xaxis.label.get_color(), ax.xaxis.label.get_fontweight())
                area.label_default["yl"] = (ax.yaxis.label.get_color(), ax.yaxis.label.get_fontweight())
                area.spine_default["bottom"] = ax.spines["bottom"].get_linewidth()
                area.spine_default["left"] = ax.spines["left"].get_linewidth()
                area.label_default["yr"] = (ax2.yaxis.label.get_color(), ax2.yaxis.label.get_fontweight())
                area.spine_default["right"] = ax2.spines["right"].get_linewidth()
            except Exception:
                pass

            ax.set_title(self.csv_path.name)

            handles, labels = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            handles += h2
            labels += l2
            if labels:
                ax.legend(handles, labels, loc="best")

            if self.x_is_datetime:
                for tick in ax.get_xticklabels():
                    tick.set_rotation(30)
                    tick.set_ha("right")

            self._apply_y_scale(ax, self.left_scale_mode.currentText(), self.left_ymin, self.left_ymax, self.left_log)
            self._apply_y_scale(ax2, self.right_scale_mode.currentText(), self.right_ymin, self.right_ymax, self.right_log)

            self._add_alarm_markers(area, ax)

            area.fig.tight_layout()

            # hover 영역 패치
            pos = ax.get_position()
            pad = 0.008
            band_h = 0.060
            band_w = 0.070

            bx = pos.x0
            by = max(0.0, pos.y0 - band_h - pad)
            bw = pos.width
            bh = min(band_h, pos.y0 - pad) if pos.y0 > pad else band_h

            area.hover_patch_bottom = FancyBboxPatch(
                (bx, by), bw, bh,
                transform=area.fig.transFigure, clip_on=False,
                boxstyle="round,pad=0.006,rounding_size=0.012",
                facecolor=(0, 0, 0, 0.06), edgecolor=(0, 0, 0, 0.12), linewidth=1.0,
                visible=False,
                zorder=0.5,
            )
            area.fig.add_artist(area.hover_patch_bottom)

            lx = max(0.02, pos.x0 - band_w - pad)
            ly = pos.y0
            lw = min(band_w, pos.x0 - pad) if pos.x0 > pad else band_w
            lh = pos.height

            area.hover_patch_left = FancyBboxPatch(
                (lx, ly), lw, lh,
                transform=area.fig.transFigure, clip_on=False,
                boxstyle="round,pad=0.006,rounding_size=0.012",
                facecolor=(0, 0, 0, 0.06), edgecolor=(0, 0, 0, 0.12), linewidth=1.0,
                visible=False,
                zorder=0.5,
            )
            area.fig.add_artist(area.hover_patch_left)

            rx = min(1.0 - band_w, pos.x1 + pad)
            ry = pos.y0
            rw = min(band_w, 1.0 - pos.x1 - pad) if (1.0 - pos.x1) > pad else band_w
            rh = pos.height

            area.hover_patch_right = FancyBboxPatch(
                (rx, ry), rw, rh,
                transform=area.fig.transFigure, clip_on=False,
                boxstyle="round,pad=0.006,rounding_size=0.012",
                facecolor=(0, 0, 0, 0.06), edgecolor=(0, 0, 0, 0.12), linewidth=1.0,
                visible=False,
                zorder=0.5,
            )
            area.fig.add_artist(area.hover_patch_right)

            # ✅ 십자선: 회색
            area.vline = ax.axvline(x=dfa["_x"].iloc[0], visible=False, linewidth=0.8, color="gray")
            area.hline_left = ax.axhline(y=0, visible=False, linewidth=0.8, color="gray")
            area.hline_right = ax2.axhline(y=0, visible=False, linewidth=0.8, color="gray")

            area.canvas.draw_idle()

        # 상태
        self.status.setText(
            f"표시 중: {len(df)} rows | Graphs={len(self._areas)} | (X drag=zoom, label click=dialog)"
        )

        # ✅ 여러 그래프 X축 동기화(표시범위)
        self._sync_x_axes()

    # -----------------------------
    # Hover 강조(축/라벨)
    # -----------------------------
    def _set_hover_kind(self, area: PlotArea, kind: str | None):
        if kind == area.hover_kind:
            return
        area.hover_kind = kind

        if kind is None:
            area.canvas.unsetCursor()
        else:
            area.canvas.setCursor(Qt.PointingHandCursor)

        ax = area.ax
        ax2 = area.ax2
        if ax is None:
            return

        xlab = ax.xaxis.label
        ylab_l = ax.yaxis.label
        ylab_r = ax2.yaxis.label if ax2 is not None else None

        def _restore_label(label, key: str):
            if label is None:
                return
            v = area.label_default.get(key)
            if v and v[0] is not None and v[1] is not None:
                label.set_color(v[0])
                label.set_fontweight(v[1])
                label.set_alpha(1.0)
            label.set_bbox(None)

        def _highlight_label(label):
            if label is None:
                return
            label.set_fontweight("bold")
            label.set_alpha(1.0)
            label.set_bbox(dict(
                boxstyle="round,pad=0.35,rounding_size=0.25",
                facecolor=(0, 0, 0, 0.06),
                edgecolor=(0, 0, 0, 0.12),
                linewidth=1.0,
            ))

        def _restore_spine(which: str):
            lw = area.spine_default.get(which)
            if lw is None:
                return
            if which == "bottom":
                ax.spines["bottom"].set_linewidth(lw)
            elif which == "left":
                ax.spines["left"].set_linewidth(lw)
            elif which == "right" and ax2 is not None:
                ax2.spines["right"].set_linewidth(lw)

        def _highlight_spine(which: str):
            if which == "bottom":
                ax.spines["bottom"].set_linewidth(2.0)
            elif which == "left":
                ax.spines["left"].set_linewidth(2.0)
            elif which == "right" and ax2 is not None:
                ax2.spines["right"].set_linewidth(2.0)

        def _ticks_normal():
            ax.tick_params(axis="x", width=1.0)
            ax.tick_params(axis="y", width=1.0)
            for t in ax.get_xticklabels():
                t.set_fontweight("normal")
                t.set_alpha(1.0)
            for t in ax.get_yticklabels():
                t.set_fontweight("normal")
                t.set_alpha(1.0)
            if ax2 is not None:
                ax2.tick_params(axis="y", width=1.0)
                for t in ax2.get_yticklabels():
                    t.set_fontweight("normal")
                    t.set_alpha(1.0)

        def _ticks_bold_x():
            ax.tick_params(axis="x", width=2.0)
            for t in ax.get_xticklabels():
                t.set_fontweight("bold")
                t.set_alpha(1.0)

        def _ticks_bold_y_left():
            ax.tick_params(axis="y", width=2.0)
            for t in ax.get_yticklabels():
                t.set_fontweight("bold")
                t.set_alpha(1.0)

        def _ticks_bold_y_right():
            if ax2 is None:
                return
            ax2.tick_params(axis="y", width=2.0)
            for t in ax2.get_yticklabels():
                t.set_fontweight("bold")
                t.set_alpha(1.0)

        def _hide_patches():
            for p in (area.hover_patch_bottom, area.hover_patch_left, area.hover_patch_right):
                if p is not None:
                    p.set_visible(False)

        def _show_patch(which: str):
            _hide_patches()
            p = None
            if which == "bottom":
                p = area.hover_patch_bottom
            elif which == "left":
                p = area.hover_patch_left
            elif which == "right":
                p = area.hover_patch_right
            if p is not None:
                p.set_visible(True)

        _restore_label(xlab, "x")
        _restore_label(ylab_l, "yl")
        _restore_label(ylab_r, "yr")
        _restore_spine("bottom")
        _restore_spine("left")
        _restore_spine("right")
        _ticks_normal()
        _hide_patches()

        if kind == "x_label":
            _highlight_label(xlab)
        elif kind == "y_label_left":
            _highlight_label(ylab_l)
        elif kind == "y_label_right":
            _highlight_label(ylab_r)
        elif kind == "x_axis":
            _highlight_spine("bottom")
            _ticks_bold_x()
            _show_patch("bottom")
        elif kind == "y_axis_left":
            _highlight_spine("left")
            _ticks_bold_y_left()
            _show_patch("left")
        elif kind == "y_axis_right":
            _highlight_spine("right")
            _ticks_bold_y_right()
            _show_patch("right")

        area.canvas.draw_idle()

    def _update_clickable_hover(self, event, area: PlotArea):
        ax = area.ax
        if ax is None:
            self._set_hover_kind(area, None)
            return
        ax2 = area.ax2

        try:
            if ax.yaxis.label.contains(event)[0]:
                self._set_hover_kind(area, "y_label_left")
                return
        except Exception:
            pass
        try:
            if ax2 is not None and ax2.yaxis.label.contains(event)[0]:
                self._set_hover_kind(area, "y_label_right")
                return
        except Exception:
            pass
        try:
            if ax.xaxis.label.contains(event)[0]:
                self._set_hover_kind(area, "x_label")
                return
        except Exception:
            pass

        bbox = ax.bbox
        pad = 14

        if (bbox.x0 <= event.x <= bbox.x1) and (abs(event.y - bbox.y0) <= pad):
            self._set_hover_kind(area, "x_axis")
            return

        if (bbox.y0 <= event.y <= bbox.y1) and (abs(event.x - bbox.x0) <= pad):
            self._set_hover_kind(area, "y_axis_left")
            return

        if ax2 is not None:
            if (bbox.y0 <= event.y <= bbox.y1) and (abs(event.x - bbox.x1) <= pad):
                self._set_hover_kind(area, "y_axis_right")
                return

        self._set_hover_kind(area, None)


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

        logger.info(f"[APP] root_dir={self.root_dir}")
        logger.info(f"[APP] alarm_dir={self.alarm_dir}")

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
    app = QApplication(sys.argv)

    root_dir = r"C:\hmi\System\RecipeProcLog"
    alarm_dir = r"C:\hmi\System\AlarmHistoryLog"

    win = MainWindow(root_dir=root_dir, alarm_dir=alarm_dir)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
