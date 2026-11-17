"""Statistics for contiguous recipe steps, preserving repeated occurrences."""

from dataclasses import dataclass
from pathlib import Path
import re

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


SUMMARY_COLUMNS = ["Segment", "Step", "Occurrence", "Step name", "Start", "End",
                   "Duration (s)", "Duration basis", "Samples"]
METRIC_COLUMNS = ["Segment", "Step", "Occurrence", "Sensor", "Valid", "Missing",
                  "Mean", "Min", "Max", "Std dev"]


@dataclass
class StepStatistics:
    summary: pd.DataFrame
    metrics: pd.DataFrame
    notes: list[str]


def _step_label(value):
    if pd.isna(value) or not str(value).strip():
        return "(Unknown)"
    return re.sub(r"^([+-]?\d+)\.0+$", r"\1", str(value).strip())


def calculate_step_statistics(df, time_col, step_col, name_col=None, sensor_cols=None):
    if df.empty:
        raise ValueError("분석할 로그 데이터가 없습니다.")
    if step_col not in df.columns:
        raise ValueError("Step 번호 컬럼을 찾을 수 없습니다.")
    data = df.reset_index(drop=True)
    labels = data[step_col].map(_step_label)
    segments = labels.ne(labels.shift()).cumsum()
    notes = [
        "전체 로그 기준입니다. 반복 Step은 연속 구간별로 회차를 구분합니다.",
        "시간은 파일 기록 순서로 계산합니다. 다음 Step의 첫 기록을 전환 시점으로 사용하며, "
        "마지막 Step은 마지막 기록까지만 계산합니다(완료 시간 아님).",
        "평균은 유효 샘플의 산술평균, 표준편차는 표본 표준편차(n-1)입니다. "
        "결측·비수치·무한대는 제외하며, 유효 샘플이 1개이면 표준편차는 비워 둡니다.",
    ]
    times = pd.Series(pd.NaT, index=data.index, dtype="datetime64[ns]")
    if time_col in data.columns:
        raw = data[time_col]
        numeric_time = pd.api.types.is_numeric_dtype(raw) or (
            raw.notna().any() and pd.to_numeric(raw.dropna(), errors="coerce").notna().all()
        )
        if not numeric_time:
            times = pd.to_datetime(raw, errors="coerce", format="mixed")
    if times.isna().all():
        notes.append("날짜/시간 컬럼을 해석할 수 없어 소요 시간을 계산하지 않았습니다. 숫자 축은 초로 가정하지 않습니다.")
    elif times.isna().any():
        notes.append(f"시각이 없거나 잘못된 기록 {int(times.isna().sum())}개: 해당 구간의 소요 시간은 비워 둡니다.")
    if (times.diff().dt.total_seconds() < 0).any():
        notes.append("시각이 역행하는 기록이 있습니다. 해당 구간의 소요 시간은 비워 둡니다.")
    if labels.eq("(Unknown)").any():
        notes.append("Step 번호가 없는 기록은 (Unknown) 구간으로 별도 표시합니다.")

    excluded = {time_col, step_col, name_col, "_x"}
    candidates = data.columns if sensor_cols is None else sensor_cols
    numeric = {}
    for col in dict.fromkeys(candidates):
        if col in excluded or col not in data.columns:
            continue
        values = pd.to_numeric(data[col], errors="coerce").replace([float("inf"), -float("inf")], float("nan"))
        # Explicitly selected sensors remain visible even if all samples are missing.
        if values.notna().any() or sensor_cols is not None or pd.api.types.is_numeric_dtype(data[col]):
            numeric[col] = values

    summaries, metrics, occurrences = [], [], {}
    for segment, group in data.groupby(segments, sort=False):
        first, last = int(group.index[0]), int(group.index[-1])
        step = labels.iloc[first]
        occurrences[step] = occurrences.get(step, 0) + 1
        occurrence = occurrences[step]
        has_next = last + 1 < len(data)
        end_index = last + 1 if has_next else last
        interval = times.iloc[first:end_index + 1]
        start, end = times.iloc[first], times.iloc[end_index]
        valid_time = interval.notna().all() and not (interval.diff().dt.total_seconds() < 0).any()
        duration = (end - start).total_seconds() if valid_time else float("nan")
        basis = ("Next step start" if has_next else "Last sample (partial)") if valid_time else "Time unavailable"
        names = [] if name_col not in data.columns else [str(v).strip() for v in group[name_col].dropna() if str(v).strip()]
        summaries.append([int(segment), step, occurrence, " / ".join(dict.fromkeys(names)),
                          "" if pd.isna(start) else start.isoformat(sep=" "),
                          "" if pd.isna(end) else end.isoformat(sep=" "),
                          duration, basis, len(group)])
        for sensor, values in numeric.items():
            sample = values.iloc[first:last + 1].dropna()
            metrics.append([int(segment), step, occurrence, sensor, len(sample), len(group) - len(sample),
                            sample.mean(), sample.min(), sample.max(), sample.std(ddof=1)])
    return StepStatistics(pd.DataFrame(summaries, columns=SUMMARY_COLUMNS),
                          pd.DataFrame(metrics, columns=METRIC_COLUMNS), notes)


def export_step_statistics(result, path, source):
    metadata = pd.DataFrame({"Notes": [f"Source: {source}", *result.notes]})
    export_statistics_tables(path, (("Steps", result.summary), ("Sensors", result.metrics), ("Notes", metadata)))


def export_statistics_tables(path, tables):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in tables:
            frame.to_excel(writer, index=False, sheet_name=name)
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for cells in sheet.columns:
                sheet.column_dimensions[cells[0].column_letter].width = min(
                    60, max(12, max(len(str(cell.value or "")) for cell in cells) + 2)
                )
                for cell in cells:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
                    elif isinstance(cell.value, float):
                        cell.number_format = "0.000"


class StepStatisticsDialog(QDialog):
    compareRequested = Signal()

    def __init__(self, result: StepStatistics, source: Path, parent=None):
        super().__init__(parent)
        self.result, self.source = result, Path(source)
        self.setWindowTitle("Step Statistics")
        self.resize(1100, 740)
        layout = QVBoxLayout(self)
        title = QLabel(f"{self.source.name}  |  {len(result.summary)} step segments")
        title.setWordWrap(True)
        title.setStyleSheet("font-size:15px; font-weight:600; padding:6px;")
        layout.addWidget(title)
        notes = QLabel("\n".join(result.notes))
        notes.setWordWrap(True)
        layout.addWidget(notes)
        splitter = QSplitter(Qt.Vertical)
        self.steps_table = self._table()
        self._fill(self.steps_table, result.summary)
        splitter.addWidget(self.steps_table)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.sensor_filter = QLineEdit()
        self.sensor_filter.setPlaceholderText("Search sensors in selected step...")
        self.sensor_filter.setClearButtonEnabled(True)
        detail_layout.addWidget(self.sensor_filter)
        self.sensors_table = self._table()
        detail_layout.addWidget(self.sensors_table)
        splitter.addWidget(detail)
        splitter.setSizes([280, 280])
        layout.addWidget(splitter, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.compare_button = QPushButton("Compare to reference...")
        buttons.addButton(self.compare_button, QDialogButtonBox.ActionRole)
        self.compare_button.clicked.connect(self.compareRequested.emit)
        export = QPushButton("Export all steps (Excel)")
        buttons.addButton(export, QDialogButtonBox.ActionRole)
        export.clicked.connect(self.export_excel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.steps_table.itemSelectionChanged.connect(self.refresh_sensors)
        self.sensor_filter.textChanged.connect(self.refresh_sensors)
        if not result.summary.empty:
            self.steps_table.selectRow(0)

    @staticmethod
    def _table():
        table = QTableWidget()
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        return table

    @staticmethod
    def _fill(table, frame):
        table.setColumnCount(len(frame.columns))
        table.setHorizontalHeaderLabels(list(frame.columns))
        table.setRowCount(len(frame))
        for row, values in enumerate(frame.itertuples(index=False, name=None)):
            for col, value in enumerate(values):
                text = "—" if pd.isna(value) else (f"{value:.6g}" if isinstance(value, float) else str(value))
                item = QTableWidgetItem(text)
                item.setToolTip("" if pd.isna(value) else str(value))
                table.setItem(row, col, item)

    def refresh_sensors(self):
        row = self.steps_table.currentRow()
        metrics = self.result.metrics
        if row < 0:
            self._fill(self.sensors_table, metrics.iloc[:0])
            return
        segment = self.result.summary.iloc[row]["Segment"]
        filtered = metrics.loc[metrics["Segment"].eq(segment)]
        text = self.sensor_filter.text().strip()
        if text:
            filtered = filtered.loc[filtered["Sensor"].str.contains(text, case=False, regex=False)]
        self._fill(self.sensors_table, filtered.drop(columns=["Segment", "Step", "Occurrence"]))

    def export_excel(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Step Statistics",
                                             str(self.source.with_name(self.source.stem + "_steps.xlsx")),
                                             "Excel files (*.xlsx)")
        if not path:
            return
        try:
            export_step_statistics(self.result, path, self.source)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", f"Excel 저장에 실패했습니다.\n{exc}")
            return
        QMessageBox.information(self, "Export", f"저장했습니다.\n{path}")
