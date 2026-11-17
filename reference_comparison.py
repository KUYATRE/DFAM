"""Compare a target log with a user-selected reference, without judging quality."""

from dataclasses import dataclass
from pathlib import Path
import math

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from step_statistics import StepStatistics, StepStatisticsDialog, export_statistics_tables
from qfluentwidgets import PushButton as QPushButton, LineEdit as QLineEdit, TableWidget as QTableWidget
from fluent_ui import FluentLineEdit as QLineEdit
from fluent_ui import FluentComboBox as QComboBox



STEP_COLUMNS = ["Pair", "Step", "Occurrence", "Match", "Reference (s)", "Target (s)",
                "Delta (s)", "Delta (%)", "Time comparison", "Reference name", "Target name",
                "Reference samples", "Target samples", "Reference duration basis", "Target duration basis"]
SENSOR_COLUMNS = ["Pair", "Step", "Occurrence", "Sensor", "Match", "Statistic", "Reference", "Target",
                  "Delta", "Delta (%)", "Reference valid", "Target valid", "Reference missing", "Target missing"]
STATISTICS = ["Mean", "Min", "Max", "Std dev"]


@dataclass
class ReferenceComparison:
    steps: pd.DataFrame
    sensors: pd.DataFrame
    notes: list[str]


def _difference(reference, target, allowed=True):
    if not allowed or pd.isna(reference) or pd.isna(target):
        return float("nan"), float("nan")
    delta = float(target) - float(reference)
    if not math.isfinite(delta):
        return float("nan"), float("nan")
    percent = delta / abs(float(reference)) * 100 if reference != 0 else float("nan")
    return delta, percent if math.isfinite(percent) else float("nan")


def compare_step_statistics(reference: StepStatistics, target: StepStatistics):
    def step_map(result, side):
        mapped = {}
        for row in result.summary.to_dict("records"):
            # Missing step numbers cannot establish correspondence between files.
            key = (row["Step"], row["Occurrence"])
            if row["Step"] == "(Unknown)":
                key += (side, row["Segment"])
            mapped[key] = row
        return mapped

    def sensor_map(result):
        mapped = {}
        for row in result.metrics.to_dict("records"):
            mapped.setdefault(row["Segment"], {})[row["Sensor"]] = row
        return mapped

    ref_steps, target_steps = step_map(reference, "reference"), step_map(target, "target")
    ref_sensors, target_sensors = sensor_map(reference), sensor_map(target)
    keys = list(target_steps) + [key for key in ref_steps if key not in target_steps]
    step_rows, sensor_rows = [], []
    for pair, key in enumerate(keys, start=1):
        ref, cur = ref_steps.get(key, {}), target_steps.get(key, {})
        step, occurrence = key[:2]
        matched = bool(ref and cur)
        status = "Matched" if matched else ("Target only" if cur else "Reference only")
        if step == "(Unknown)":
            status += " (unknown step)"
        name_differs = matched and bool(ref["Step name"] and cur["Step name"]) and ref["Step name"] != cur["Step name"]
        if name_differs:
            status = "Step names differ"
        comparable = matched and not name_differs
        ref_time, cur_time = ref.get("Duration (s)", float("nan")), cur.get("Duration (s)", float("nan"))
        time_allowed = comparable
        if not comparable:
            time_status = "Not matched"
        elif pd.isna(ref_time) or pd.isna(cur_time):
            time_status, time_allowed = "Time unavailable", False
        elif ref["Duration basis"] != cur["Duration basis"]:
            time_status, time_allowed = "Duration basis differs", False
        elif ref["Duration basis"] == "Last sample (partial)":
            time_status = "Recorded span only"
        else:
            time_status = "Step duration"
        delta, percent = _difference(ref_time, cur_time, time_allowed)
        step_rows.append([pair, step, occurrence, status, ref_time, cur_time, delta, percent, time_status,
                          ref.get("Step name", ""), cur.get("Step name", ""), ref.get("Samples"), cur.get("Samples"),
                          ref.get("Duration basis", ""), cur.get("Duration basis", "")])
        ref_metrics = ref_sensors.get(ref.get("Segment"), {})
        cur_metrics = target_sensors.get(cur.get("Segment"), {})
        sensors = list(cur_metrics) + [sensor for sensor in ref_metrics if sensor not in cur_metrics]
        for sensor in sensors:
            r, t = ref_metrics.get(sensor, {}), cur_metrics.get(sensor, {})
            sensor_status = status
            if comparable:
                sensor_status = "Matched" if r and t else ("Target only" if t else "Reference only")
            for statistic in STATISTICS:
                rv, tv = r.get(statistic, float("nan")), t.get(statistic, float("nan"))
                metric_status = sensor_status
                if sensor_status == "Matched" and (pd.isna(rv) or pd.isna(tv)):
                    metric_status = "Value unavailable"
                delta, percent = _difference(rv, tv, comparable and bool(r and t))
                sensor_rows.append([pair, step, occurrence, sensor, metric_status, statistic, rv, tv, delta, percent,
                                    r.get("Valid"), t.get("Valid"), r.get("Missing"), t.get("Missing")])
    notes = [
        "사용자가 선택한 로그를 비교 기준으로 사용합니다. 정상·이상 판정은 수행하지 않습니다.",
        "편차(Delta) = 분석 대상(Target) - 기준(Reference). 편차율 = 편차 / |기준값| × 100. 기준값이 0이면 편차율은 비워 둡니다.",
        "Step 번호와 발생 회차, 센서 컬럼 이름으로 대응합니다. Step 번호 누락 또는 양쪽 Step 이름 불일치 시 편차를 계산하지 않습니다.",
        "한쪽에만 있는 Step·센서는 별도 표시합니다. 결측 통계값은 0으로 대체하지 않습니다.",
        "시간 계산 기준이 서로 다르면 시간 편차를 계산하지 않습니다. 마지막 Step끼리의 비교는 기록 구간 길이의 차이이며 완료 시간 차이가 아닙니다.",
        "센서 편차는 각 Step의 전체 기록 통계값 차이입니다. 같은 경과 시각의 파형 차이 또는 같은 길이로 잘라낸 구간 비교가 아닙니다.",
        "Recipe와 센서 단위가 같은 조건인지 확인하세요. 같은 Step 번호·회차만으로 동일한 공정 조건임을 판단할 수 없습니다.",
        "[Reference calculation notes]", *reference.notes, "[Target calculation notes]", *target.notes,
    ]
    return ReferenceComparison(pd.DataFrame(step_rows, columns=STEP_COLUMNS),
                               pd.DataFrame(sensor_rows, columns=SENSOR_COLUMNS), notes)


def export_reference_comparison(result, path, reference_path, target_path):
    metadata = pd.DataFrame({"Notes": [f"Reference: {reference_path}", f"Target: {target_path}", *result.notes]})
    export_statistics_tables(path, (("Step comparison", result.steps), ("Sensor comparison", result.sensors),
                                   ("Notes", metadata)))


class ReferenceComparisonDialog(QDialog):
    def __init__(self, target, target_path, reference_loader, parent=None):
        super().__init__(parent)
        self.target, self.target_path = target, Path(target_path)
        self.reference_loader = reference_loader
        self.reference_path = None
        self.result = None
        self.setWindowTitle("Reference Comparison")
        self.resize(1200, 780)
        layout = QVBoxLayout(self)
        target_label = QLabel(f"Target (분석 대상): {self.target_path}")
        target_label.setWordWrap(True)
        layout.addWidget(target_label)
        row = QHBoxLayout()
        self.reference_label = QLabel("Reference (기준): 비교할 기준 로그를 선택하세요.")
        self.reference_label.setWordWrap(True)
        row.addWidget(self.reference_label, 1)
        self.choose_button = QPushButton("Select reference CSV...")
        self.choose_button.clicked.connect(self.choose_reference)
        row.addWidget(self.choose_button)
        layout.addLayout(row)
        description = QLabel("편차 = 분석 대상 - 기준 로그  |  편차율 = 편차 / |기준값| × 100 (기준값 0: 표시 안 함)\n"
                             "Step 번호·발생 회차로 대응하며, 각 Step 전체의 통계값을 비교합니다. 정상·이상 판정은 하지 않습니다.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.status = QLabel("기준 로그를 선택하면 Step별 시간과 센서 통계의 편차가 표시됩니다.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        splitter = QSplitter(Qt.Vertical)
        self.steps_table = StepStatisticsDialog._table()
        splitter.addWidget(self.steps_table)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self.sensor_filter = QLineEdit()
        self.sensor_filter.setPlaceholderText("Search sensors in selected step...")
        self.sensor_filter.setClearButtonEnabled(True)
        filters.addWidget(self.sensor_filter, 1)
        self.statistic = QComboBox()
        self.statistic.addItems(STATISTICS + ["All statistics"])
        filters.addWidget(self.statistic)
        detail_layout.addLayout(filters)
        self.sensors_table = StepStatisticsDialog._table()
        detail_layout.addWidget(self.sensors_table)
        splitter.addWidget(detail)
        splitter.setSizes([280, 280])
        layout.addWidget(splitter, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.notes_button = QPushButton("Calculation notes")
        self.notes_button.setEnabled(False)
        self.notes_button.clicked.connect(self.show_notes)
        buttons.addButton(self.notes_button, QDialogButtonBox.ActionRole)
        self.export_button = QPushButton("Export comparison (Excel)")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_excel)
        buttons.addButton(self.export_button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.steps_table.itemSelectionChanged.connect(self.refresh_sensors)
        self.sensor_filter.textChanged.connect(self.refresh_sensors)
        self.statistic.currentTextChanged.connect(self.refresh_sensors)

    def choose_reference(self):
        start = self.reference_path or self.target_path
        path, _ = QFileDialog.getOpenFileName(self, "Select reference log", str(start.parent), "CSV files (*.csv)")
        if path:
            self.set_reference_path(path)

    def set_reference_path(self, path):
        try:
            reference = self.reference_loader(Path(path))
            result = compare_step_statistics(reference, self.target)
        except Exception as exc:
            QMessageBox.critical(self, "Reference load failed", f"기준 로그를 읽을 수 없습니다.\n{exc}")
            return False
        self.reference_path, self.result = Path(path), result
        self.reference_label.setText(f"Reference (기준): {self.reference_path}")
        self.choose_button.setText("Change reference CSV...")
        self.steps_table.blockSignals(True)
        StepStatisticsDialog._fill(self.steps_table, result.steps.drop(
            columns=["Pair", "Reference duration basis", "Target duration basis"]))
        self.steps_table.clearSelection()
        self.steps_table.blockSignals(False)
        count = int(result.steps["Match"].eq("Matched").sum())
        self.status.setText(f"대응 Step: {count} / 표시 구간: {len(result.steps)}. "
                            "Time comparison과 Match 열에서 누락·계산 조건 차이를 확인하세요.")
        self.export_button.setEnabled(True)
        self.notes_button.setEnabled(True)
        if not result.steps.empty:
            self.steps_table.selectRow(0)
        self.refresh_sensors()
        return True

    def refresh_sensors(self):
        if self.result is None:
            return
        row = self.steps_table.currentRow()
        metrics = self.result.sensors
        if row < 0 or row >= len(self.result.steps):
            filtered = metrics.iloc[:0]
        else:
            pair = self.result.steps.iloc[row]["Pair"]
            filtered = metrics.loc[metrics["Pair"].eq(pair)]
        text = self.sensor_filter.text().strip()
        if text:
            filtered = filtered.loc[filtered["Sensor"].str.contains(text, case=False, regex=False)]
        if self.statistic.currentText() != "All statistics":
            filtered = filtered.loc[filtered["Statistic"].eq(self.statistic.currentText())]
        StepStatisticsDialog._fill(self.sensors_table, filtered.drop(columns=["Pair", "Step", "Occurrence"]))

    def show_notes(self):
        if self.result is not None:
            QMessageBox.information(self, "Calculation notes", "\n\n".join(self.result.notes))

    def export_excel(self):
        if self.result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Reference Comparison",
                                             str(self.target_path.with_name(self.target_path.stem + "_comparison.xlsx")),
                                             "Excel files (*.xlsx)")
        if not path:
            return
        try:
            export_reference_comparison(self.result, path, self.reference_path, self.target_path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", f"Excel 저장에 실패했습니다.\n{exc}")
            return
        QMessageBox.information(self, "Export", f"저장했습니다.\n{path}")
