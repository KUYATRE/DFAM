"""Monthly gas dashboard; CSV scanning runs outside the GUI thread."""

from pathlib import Path

import pandas as pd
from PySide6.QtCore import QDate, QMargins, QSettings, QThread, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtCharts import QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QValueAxis
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from gas_usage import (
    CalculationCancelled, DEFAULT_N2, DEFAULT_O2, GasUsageConfig,
    VOLUME_COLUMNS, calculate_monthly_usage, export_gas_usage,
)


class GasUsageWorker(QThread):
    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, root, config, parent=None):
        super().__init__(parent)
        self.root, self.config = root, config

    def run(self):
        try:
            result = calculate_monthly_usage(self.root, self.config, self.progress.emit,
                                             self.isInterruptionRequested)
            if self.isInterruptionRequested():
                raise CalculationCancelled()
            self.succeeded.emit(result)
        except CalculationCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class GasUsageDialog(QDialog):
    def __init__(self, root_dir, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Monthly Gas Usage · v3.00")
        self.resize(1250, 900)
        self.settings = settings if settings is not None else QSettings("DFAM", "GasUsage")
        self.result = None
        self.worker = None
        self._close_when_finished = False
        layout = QVBoxLayout(self)
        heading = QLabel("월별 가스 사용량")
        heading.setStyleSheet("font-size:22px; font-weight:700; color:#123149; padding:4px;")
        layout.addWidget(heading)
        note = QLabel("Tube별 N2 · O2 실측 유량을 적산합니다. 단위: ft³ (원본 sccm과 동일한 표준상태).\n"
                      "설비 전체 = Tube 합계 · 로그가 없는 대기/퍼지 사용량은 포함되지 않습니다.")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.controls = QWidget()
        controls = QVBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("공정 로그 폴더"))
        self.root_edit = QLineEdit(str(root_dir or ""))
        self.root_edit.setPlaceholderText(r"C:\hmi\System\RecipeProcLog")
        path_row.addWidget(self.root_edit, 1)
        browse = QPushButton("폴더 선택")
        browse.clicked.connect(self.browse)
        path_row.addWidget(browse)
        controls.addLayout(path_row)

        options = QHBoxLayout()
        today = QDate.currentDate()
        self.month_from, self.month_to = QDateEdit(), QDateEdit()
        for edit, date in ((self.month_from, QDate(today.year(), 1, 1)),
                           (self.month_to, QDate(today.year(), today.month(), 1))):
            edit.setDisplayFormat("yyyy-MM")
            edit.setCalendarPopup(True)
            edit.setDate(date)
        options.addWidget(QLabel("집계 월"))
        options.addWidget(self.month_from)
        options.addWidget(QLabel("~"))
        options.addWidget(self.month_to)
        options.addStretch()
        self.calculate_button = QPushButton("집계 / 새로고침")
        self.calculate_button.setStyleSheet("background:#0f6cbd; color:white; padding:8px 20px; font-weight:700;")
        self.calculate_button.clicked.connect(self.start_calculation)
        options.addWidget(self.calculate_button)
        controls.addLayout(options)

        settings_toggle = QPushButton("유량 컬럼 · 계산 설정 보기")
        settings_toggle.setCheckable(True)
        controls.addWidget(settings_toggle)
        group = QGroupBox("실측 sccm 컬럼을 확인하세요")
        group.setVisible(False)
        settings_toggle.toggled.connect(group.setVisible)
        form = QFormLayout(group)
        self.n2_edit = QLineEdit(self.settings.value("n2", ", ".join(DEFAULT_N2), type=str))
        self.o2_edit = QLineEdit(self.settings.value("o2", ", ".join(DEFAULT_O2), type=str))
        for edit in (self.n2_edit, self.o2_edit):
            edit.setToolTip("실측 sccm 컬럼 이름을 쉼표로 구분하세요. SP, 압력, Total 중복 합산을 피하세요.")
        form.addRow("N2 실측 sccm", self.n2_edit)
        form.addRow("O2 실측 sccm", self.o2_edit)
        extra = QHBoxLayout()
        self.time_edit = QLineEdit(self.settings.value("time", "Time", type=str))
        self.time_edit.setMaximumWidth(160)
        extra.addWidget(QLabel("시간 컬럼"))
        extra.addWidget(self.time_edit)
        extra.addWidget(QLabel("허용 기록 간격"))
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0.001, 86400)
        self.gap.setDecimals(3)
        self.gap.setSuffix(" s")
        self.gap.setValue(self.settings.value("gap", 60.0, type=float))
        self.gap.setToolTip("이를 초과하는 인접 기록 간격은 추정하지 않고 제외합니다.")
        extra.addWidget(self.gap)
        extra.addStretch()
        defaults = QPushButton("기본값 복원")
        defaults.clicked.connect(self.restore_defaults)
        extra.addWidget(defaults)
        form.addRow(extra)
        controls.addWidget(group)
        layout.addWidget(self.controls)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        progress_row.addWidget(self.progress, 1)
        self.cancel_button = QPushButton("집계 취소")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_calculation)
        progress_row.addWidget(self.cancel_button)
        layout.addLayout(progress_row)
        self.status = QLabel("월과 컬럼을 확인한 뒤 집계를 시작하세요. 하위 폴더의 CSV도 포함합니다.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        filters = QHBoxLayout()
        self.equipment_filter, self.tube_filter = QComboBox(), QComboBox()
        self.equipment_filter.addItem("전체 설비", None)
        self.tube_filter.addItem("설비 전체", None)
        filters.addWidget(QLabel("설비"))
        filters.addWidget(self.equipment_filter)
        filters.addWidget(QLabel("그래프 / Tube 표"))
        filters.addWidget(self.tube_filter)
        filters.addStretch()
        self.export_button = QPushButton("Excel 내보내기")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export)
        filters.addWidget(self.export_button)
        layout.addLayout(filters)
        self.summary = QLabel("N2: —    O2: —    합계: —")
        self.summary.setStyleSheet("font-size:18px; font-weight:700; color:#0f6cbd; padding:6px;")
        layout.addWidget(self.summary)

        splitter = QSplitter(Qt.Vertical)
        self.chart = QChart()
        self.chart.setMargins(QMargins(8, 4, 8, 4))
        self.chart.setTitle("월별 N2 · O2 사용량 (ft³)")
        self.chart_view = QChartView(self.chart)
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        self.chart_view.setMinimumHeight(240)
        splitter.addWidget(self.chart_view)
        self.tabs = QTabWidget()
        self.equipment_table, self.tube_table, self.issues_table = (QTableWidget() for _ in range(3))
        for title, table in (("설비별 월 합계", self.equipment_table),
                             ("Tube별 월 사용량", self.tube_table), ("검토 내역", self.issues_table)):
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.verticalHeader().hide()
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            self.tabs.addTab(table, title)
        splitter.addWidget(self.tabs)
        splitter.setSizes([300, 250])
        layout.addWidget(splitter, 1)
        footnote = QLabel("— = 유효 기록 없음 (0 사용량과 구분) · 그래프 * = 가스 기록 누락 · h = 집계 시간, 설비 표는 Tube 시간 합계\n"
                          "사다리꼴 적산: Σ[(앞 유량+뒤 유량)/2 × 경과초/60] ÷ 28,316.846592 · "
                          "마지막 기록 이후와 파일 사이 공백은 추정하지 않습니다.")
        footnote.setWordWrap(True)
        layout.addWidget(footnote)

        for edit in (self.root_edit, self.n2_edit, self.o2_edit, self.time_edit):
            edit.textChanged.connect(self.invalidate)
        self.month_from.dateChanged.connect(self.invalidate)
        self.month_to.dateChanged.connect(self.invalidate)
        self.gap.valueChanged.connect(self.invalidate)
        self.equipment_filter.currentIndexChanged.connect(self.refresh_views)
        self.tube_filter.currentIndexChanged.connect(self.refresh_views)

    def restore_defaults(self):
        self.n2_edit.setText(", ".join(DEFAULT_N2))
        self.o2_edit.setText(", ".join(DEFAULT_O2))
        self.time_edit.setText("Time")
        self.gap.setValue(60)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "공정 로그 폴더", self.root_edit.text())
        if path:
            self.root_edit.setText(path)

    def invalidate(self, *_):
        self.result = None
        self.export_button.setEnabled(False)
        self.summary.setText("N2: —    O2: —    합계: —")
        self.status.setText("설정이 변경되었습니다. 집계 / 새로고침을 눌러 다시 계산하세요.")
        for table in (self.equipment_table, self.tube_table, self.issues_table):
            table.setRowCount(0)
        self.chart.removeAllSeries()
        for axis in self.chart.axes():
            self.chart.removeAxis(axis)
            axis.deleteLater()

    def start_calculation(self):
        if self.worker is not None:
            return
        config = GasUsageConfig(
            self.month_from.date().toString("yyyy-MM"), self.month_to.date().toString("yyyy-MM"),
            tuple(s.strip() for s in self.n2_edit.text().split(",") if s.strip()),
            tuple(s.strip() for s in self.o2_edit.text().split(",") if s.strip()),
            self.time_edit.text().strip(), self.gap.value())
        try:
            config.validate()
            if not Path(self.root_edit.text().strip()).is_dir() or not self.root_edit.text().strip():
                raise ValueError("공정 로그 폴더가 존재하지 않습니다.")
        except ValueError as exc:
            QMessageBox.warning(self, "월별 가스 사용량", str(exc))
            return
        self.invalidate()
        self.controls.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText("공정 로그를 찾고 있습니다…")
        for key, value in (("n2", self.n2_edit.text()), ("o2", self.o2_edit.text()),
                           ("time", self.time_edit.text()), ("gap", self.gap.value())):
            self.settings.setValue(key, value)
        self.worker = GasUsageWorker(self.root_edit.text().strip(), config, self)
        self.worker.progress.connect(self.update_progress)
        self.worker.succeeded.connect(self.show_result)
        self.worker.failed.connect(self.show_error)
        self.worker.cancelled.connect(lambda: self.status.setText("집계를 취소했습니다. 결과는 저장되지 않았습니다."))
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def update_progress(self, done, total, name):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.status.setText(f"{done:,} / {total:,} 파일 · {name}")

    def cancel_calculation(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.status.setText("현재 파일 처리를 마치고 취소합니다…")

    def worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.controls.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if self.result is not None else 0)
        if self._close_when_finished:
            super().reject()

    def show_error(self, message):
        self.status.setText(f"집계 실패: {message}")

    def show_result(self, result):
        self.result = result
        for combo, title, column in ((self.equipment_filter, "전체 설비", "Equipment"),
                                      (self.tube_filter, "설비 전체", "Tube")):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(title, None)
            for value in sorted(result.tubes[column].unique()):
                combo.addItem(value, value)
            combo.blockSignals(False)
        self.export_button.setEnabled(True)
        has_volume = result.tubes[VOLUME_COLUMNS[:2]].notna().any().any()
        prefix = "집계 완료" if has_volume else "선택 기간에 집계 가능한 기록이 없습니다"
        self.status.setText(f"{prefix} · 읽은 파일 {len(result.files):,}개 · 검토 항목 {len(result.issues):,}개. "
                            "검토 내역의 제외 구간과 누락 파일을 확인하세요.")
        self.fill_table(self.issues_table, result.issues)
        self.tabs.setTabText(2, f"검토 내역 ({len(result.issues):,})")
        self.refresh_views()

    @staticmethod
    def fill_table(table, frame):
        table.setColumnCount(len(frame.columns))
        table.setHorizontalHeaderLabels([str(c) for c in frame.columns])
        table.setRowCount(len(frame))
        for row, values in enumerate(frame.itertuples(index=False, name=None)):
            for col, value in enumerate(values):
                text = "—" if pd.isna(value) else (f"{value:,.6f}" if isinstance(value, float) else str(value))
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if isinstance(value, (float, int)):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, col, item)

    def refresh_views(self, *_):
        if self.result is None:
            return
        equipment, tubes = self.result.equipment, self.result.tubes
        eq, tube = self.equipment_filter.currentData(), self.tube_filter.currentData()
        if eq is not None:
            equipment = equipment[equipment["Equipment"] == eq]
            tubes = tubes[tubes["Equipment"] == eq]
        if tube is not None:
            tubes = tubes[tubes["Tube"] == tube]
        self.fill_table(self.equipment_table, equipment)
        self.fill_table(self.tube_table, tubes)
        shown = tubes if tube is not None else equipment
        monthly = shown.groupby("Month")[VOLUME_COLUMNS[:2]].sum(min_count=1)
        totals = monthly.sum(min_count=1)
        total = totals.iloc[0] + totals.iloc[1]
        def display(value):
            return "—" if pd.isna(value) else f"{value:,.4f} ft³"
        self.summary.setText(f"선택 기간 · N2: {display(totals.iloc[0])}    O2: {display(totals.iloc[1])}    합계: {display(total)}")
        self.chart.removeAllSeries()
        for axis in self.chart.axes():
            self.chart.removeAxis(axis)
            axis.deleteLater()
        series = QBarSeries()
        for gas, color in (("N2", "#138b97"), ("O2", "#e59829")):
            bars = QBarSet(gas)
            bars.setColor(QColor(color))
            bars.append(monthly[f"{gas} (ft³)"].fillna(0).tolist())
            series.append(bars)
        self.chart.addSeries(series)
        x, y = QBarCategoryAxis(), QValueAxis()
        labels = [m + ("*" if row.isna().any() else "")
                  for m, row in monthly.iterrows()]
        if not labels:
            labels = ["기록 없음"]
        x.append(labels)
        if len(labels) > 8:
            x.setLabelsAngle(-30)
        y.setTitleText("ft³")
        y.setRange(0, max(1.0, monthly.max().max() * 1.15) if monthly.notna().any().any() else 1.0)
        y.setLabelFormat("%.2f")
        self.chart.addAxis(x, Qt.AlignBottom)
        self.chart.addAxis(y, Qt.AlignLeft)
        series.attachAxis(x)
        series.attachAxis(y)
        self.chart.setTitle(f"{eq or '전체 설비'} · {tube or '모든 Tube'} · 월별 기록 사용량")

    def export(self):
        if self.result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "월별 가스 사용량 내보내기",
            str(Path(self.root_edit.text()) / "monthly_gas_usage.xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            export_gas_usage(self.result, path)
        except Exception as exc:
            QMessageBox.warning(self, "내보내기 실패", str(exc))
            return
        QMessageBox.information(self, "내보내기 완료", "전체 집계 결과와 계산 설정을 저장했습니다. 화면 필터는 내보내기에 적용되지 않습니다.")

    def reject(self):
        if self.worker is not None:
            self._close_when_finished = True
            self.cancel_calculation()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self._close_when_finished = True
            self.cancel_calculation()
            event.ignore()
        else:
            event.accept()
