import sys
from pathlib import Path

import pandas as pd

from PySide6.QtCore import Qt, QDir, QDateTime
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QTreeView, QFileSystemModel, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QStackedWidget,
    QDateTimeEdit, QDoubleSpinBox, QMessageBox, QCheckBox
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from utils.logger import logger

NONE_ITEM = "(None)"


class CsvPlotPanel(QWidget):
    def __init__(self, alarm_dir: Path, parent=None):
        super().__init__(parent)

        self.alarm_dir = Path(alarm_dir)

        self.df: pd.DataFrame | None = None
        self.csv_path: Path | None = None
        self.x_col: str | None = None
        self.x_is_datetime: bool = False

        # hover용(알람)
        self._alarm_scatter = None
        self._alarm_texts: list[str] = []
        self._alarm_times: list[pd.Timestamp] = []
        self._hover_annot = None

        # ✅ 십자선용
        self._plot_ax = None
        self._vline = None
        self._hline = None

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        self.title = QLabel("CSV를 선택하세요.")
        self.title.setStyleSheet("font-weight: 700;")
        root.addWidget(self.title)

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

        root.addLayout(top)

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

        root.addLayout(top2)

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
        root.addLayout(range_row)

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

        root.addLayout(yscale_row)

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

        root.addLayout(y2scale_row)

        self.left_scale_mode.currentIndexChanged.connect(self._update_scale_enable_state)
        self.right_scale_mode.currentIndexChanged.connect(self._update_scale_enable_state)

        # =========================
        # Plot canvas
        # =========================
        self.fig = Figure()
        self.canvas = FigureCanvas(self.fig)
        root.addWidget(self.canvas, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        root.addWidget(self.status)

        # ✅ hover 이벤트 연결(알람툴팁 + 십자선)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)

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

        self.left_scale_mode.setCurrentText("Auto")
        self.right_scale_mode.setCurrentText("Auto")
        self._update_scale_enable_state()

        if not enabled:
            self.status.setText("그래프로 그릴 수 있는 숫자형 컬럼이 없습니다.")
            self._clear_plot()
            return

        self.y_combos[0].setCurrentIndex(1)
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

    def _clear_plot(self):
        self.fig.clear()
        self._alarm_scatter = None
        self._alarm_texts = []
        self._alarm_times = []
        self._hover_annot = None
        self._plot_ax = None
        self._vline = None
        self._hline = None
        self.canvas.draw_idle()

    def _apply_y_scale(self, ax, mode: str, ymin_spin: QDoubleSpinBox, ymax_spin: QDoubleSpinBox, log_chk: QCheckBox):
        if log_chk.isChecked():
            ax.set_yscale("log")
        else:
            ax.set_yscale("linear")

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
        if "TubeID" not in self.df.columns:
            return None
        s = pd.to_numeric(self.df["TubeID"], errors="coerce").dropna()
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

    def _add_alarm_markers(self, ax):
        events = self._load_alarm_events()
        if events is None or events.empty:
            return

        if self.x_is_datetime:
            start = pd.Timestamp(self.dt_start.dateTime().toPython())
            end = pd.Timestamp(self.dt_end.dateTime().toPython())
            events = events[events["_t"].between(start, end, inclusive="both")]
        if events.empty:
            return

        ymin, ymax = ax.get_ylim()
        if ax.get_yscale() == "log":
            y_marker = (ymin * 1.05) if (ymin and ymin > 0) else 1.0
        else:
            y_marker = ymin + (ymax - ymin) * 0.05

        xs = events["_t"].tolist()
        ys = [y_marker] * len(xs)

        self._alarm_texts = events["Text"].tolist()
        self._alarm_times = [pd.Timestamp(t) for t in events["_t"].tolist()]

        # ✅ hover 잘 되게 picker=8
        self._alarm_scatter = ax.scatter(xs, ys, picker=8)

        # ✅ annotate는 매 plot마다 새로 생성
        self._hover_annot = ax.annotate(
            "",
            xy=(0, 0),
            xytext=(12, 12),
            textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w", alpha=0.95),
            arrowprops=dict(arrowstyle="->"),
        )
        self._hover_annot.set_visible(False)

    # -----------------------------
    # ✅ 마우스 이동: 십자선 + 알람툴팁
    # -----------------------------
    def _on_motion(self, event):
        # 1) 십자선
        self._update_crosshair(event)

        # 2) 알람 툴팁
        self._update_alarm_tooltip(event)

    def _update_crosshair(self, event):
        if self._plot_ax is None or self._vline is None or self._hline is None:
            return

        if event.inaxes != self._plot_ax:
            # 그래프 밖으로 나가면 숨김
            if self._vline.get_visible() or self._hline.get_visible():
                self._vline.set_visible(False)
                self._hline.set_visible(False)
                self.canvas.draw_idle()
            return

        if event.xdata is None or event.ydata is None:
            return

        # 십자선 위치 업데이트
        self._vline.set_xdata([event.xdata, event.xdata])
        self._hline.set_ydata([event.ydata, event.ydata])
        self._vline.set_visible(True)
        self._hline.set_visible(True)
        self.canvas.draw_idle()

    def _update_alarm_tooltip(self, event):
        if self._alarm_scatter is None or self._hover_annot is None:
            return

        if event.inaxes is None:
            if self._hover_annot.get_visible():
                self._hover_annot.set_visible(False)
                self.canvas.draw_idle()
            return

        cont, ind = self._alarm_scatter.contains(event)
        if not cont or "ind" not in ind or len(ind["ind"]) == 0:
            if self._hover_annot.get_visible():
                self._hover_annot.set_visible(False)
                self.canvas.draw_idle()
            return

        i = int(ind["ind"][0])

        try:
            offsets = self._alarm_scatter.get_offsets()
            x_pt, y_pt = offsets[i]
            t = self._alarm_times[i]
            txt = self._alarm_texts[i]
        except Exception:
            return

        self._hover_annot.xy = (x_pt, y_pt)
        msg = f"{pd.Timestamp(t).strftime('%Y-%m-%d %H:%M:%S')}\n{txt}"
        self._hover_annot.set_text(msg)
        self._hover_annot.set_visible(True)
        self.canvas.draw_idle()

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

        left_cols = self._selected_cols(self.y_combos)
        right_cols = self._selected_cols(self.y2_combos)

        if not left_cols and not right_cols:
            self._clear_plot()
            return

        left_cols = [c for c in left_cols if c in df0.columns]
        right_cols = [c for c in right_cols if c in df0.columns]

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
            self._clear_plot()
            return

        use_cols = ["_x"] + left_cols + right_cols
        use_cols = list(dict.fromkeys(use_cols))
        df = df.loc[:, use_cols].copy()

        for c in left_cols + right_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        df = df.dropna(subset=["_x"] + left_cols + right_cols, how="any")
        if df.empty:
            self.status.setText("유효한 데이터가 없습니다 (빈칸/NaN 제거 후)")
            self._clear_plot()
            return

        self.fig.clear()

        # reset hover/crosshair state
        self._alarm_scatter = None
        self._alarm_texts = []
        self._alarm_times = []
        self._hover_annot = None

        self._plot_ax = self.fig.add_subplot(111)
        ax = self._plot_ax

        for c in left_cols:
            ax.plot(df["_x"], df[c], label=f"L:{c}")
        ax.set_xlabel(self.x_col if self.x_col else "X")
        ax.set_ylabel("Left Y")

        ax2 = None
        if right_cols:
            ax2 = ax.twinx()
            for c in right_cols:
                ax2.plot(df["_x"], df[c], label=f"R:{c}")
            ax2.set_ylabel("Right Y2")

        ax.set_title(self.csv_path.name)

        handles, labels = ax.get_legend_handles_labels()
        if ax2 is not None:
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
        if ax2 is not None:
            self._apply_y_scale(ax2, self.right_scale_mode.currentText(), self.right_ymin, self.right_ymax, self.right_log)

        # ✅ Alarm 마커 + 툴팁
        self._add_alarm_markers(ax)

        # ✅ 십자선 생성 (처음엔 숨김)
        #   linewidth/color 지정 안 해도 되지만, 너무 얇거나 안 보일 수 있어서 최소 linewidth만 줌.
        self._vline = ax.axvline(x=df["_x"].iloc[0], visible=False, linewidth=0.8)
        self._hline = ax.axhline(y=0, visible=False, linewidth=0.8)

        self.fig.tight_layout()
        self.canvas.draw_idle()

        self.status.setText(
            f"표시 중: {len(df)} rows | Left={left_cols if left_cols else '-'} | Right={right_cols if right_cols else '-'}"
        )


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

    root_dir = r"D:\01. 업무자료\01. PROJECT\00. 개인PJT\02. 공정로그 및 알람 분석\02. 테스트로그"
    alarm_dir = r"D:\01. 업무자료\01. PROJECT\00. 개인PJT\02. 공정로그 및 알람 분석\02. 테스트로그\AlarmHistoryLog"

    win = MainWindow(root_dir=root_dir, alarm_dir=alarm_dir)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
