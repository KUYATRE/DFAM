"""Monthly gas dashboard; CSV scanning runs outside the GUI thread."""

from pathlib import Path
from background_work import background_resources

import pandas as pd
from PySide6.QtCore import QDate, QMargins, QSettings, QStandardPaths, QThread, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtCharts import QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QValueAxis
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
    QFrame, QGridLayout, QScrollArea,
)

from ui_theme import (apply_chrome_input_styles, apply_shadow, ChromeComboBox as QComboBox,
                      ChromeDateEdit as QDateEdit, ChromeDoubleSpinBox as QDoubleSpinBox)
from gas_usage_cards import TubeUsageCard, volume_text

from gas_usage import (
    CalculationCancelled, DEFAULT_N2, DEFAULT_O2, GasUsageConfig,
    VOLUME_COLUMNS, calculate_monthly_usage, export_gas_usage, select_usage_month,
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
            cache = (Path(QStandardPaths.writableLocation(QStandardPaths.GenericCacheLocation))
                     / "DFAM" / "GasUsage" / "file_periods.sqlite3")
            with background_resources() as background_enabled:
                result = calculate_monthly_usage(
                    self.root, self.config, self.progress.emit,
                    self.isInterruptionRequested, period_cache=cache,
                    cooperate=lambda: self.msleep(5))
            result.settings.loc[len(result.settings)] = [
                "Scheduling", "Windows background resources" if background_enabled else "Qt low thread priority"]
            if self.isInterruptionRequested():
                raise CalculationCancelled()
            self.succeeded.emit(result)
        except CalculationCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class GasUsageDialog(QDialog):
    def __init__(self, proc_log_dir, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("MONTHLY GAS FLOW · v3.05")
        self.resize(1180, 1000)
        self.settings = settings if settings is not None else QSettings("DFAM", "GasUsage")
        self.result = None
        self.month_result = None
        self.trend_result = None
        self._calculation_scope = "month"
        self._display_scope = "month"
        self.worker = None
        self._close_when_finished = False
        self.tube_cards = {}
        self._card_signature = None
        self.setObjectName("gasUsageDialog")
        self.setStyleSheet("""
            QDialog#gasUsageDialog {background:#f5f6f8; color:#1f2937;}
            QDialog#gasUsageDialog QLabel {background:transparent;}
            QPushButton {background:white; color:#1f2937; border:1px solid #d9dee7;
                         border-radius:10px; padding:8px 14px; font-weight:600;}
            QPushButton:hover {background:#f0f5fc; border-color:#bccbdf;}
            QPushButton:pressed, QPushButton:checked {background:#eaf2ff; color:#0f6cbd;}
            QPushButton:disabled {background:#f3f4f6; color:#9ca3af; border-color:#e5e7eb;}
            QPushButton[primary="true"] {background:#0f6cbd; color:white; border-color:#0f6cbd;}
            QPushButton[primary="true"]:hover {background:#115ea3;}
            QPushButton[primary="true"]:disabled {background:#b6cce1; border-color:#b6cce1;}
            QGroupBox {background:white; border:1px solid #d9dee7; border-radius:14px;
                       margin-top:10px; padding:16px 12px 8px;}
            QGroupBox::title {subcontrol-origin:margin; left:16px; color:#526675;}
            QTableWidget {background:white; alternate-background-color:#f8fafc;
                          border:1px solid #d9dee7; border-radius:12px; padding:6px;
                          gridline-color:#eef2f7; selection-background-color:#eaf2ff;
                          selection-color:#1f2937;}
            QHeaderView::section {background:#f8fafc; color:#64748b; border:0;
                                  border-bottom:1px solid #e5e7eb; padding:9px;}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(10)

        banner = QFrame()
        banner.setObjectName("gasBanner")
        banner.setStyleSheet("QFrame#gasBanner {background:white; border:1px solid #d9dee7; border-radius:18px;} QFrame#gasBanner QLabel {color:#1f2937; background:transparent; border:0;}")
        apply_shadow(banner)
        header = QHBoxLayout(banner)
        header.setContentsMargins(20, 13, 20, 13)
        title = QLabel("MONTHLY GAS FLOW")
        title.setStyleSheet("font-size:23px; font-weight:700; color:#243f54;")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel("집계 월"))
        today = QDate.currentDate()
        self.selected_month = QDateEdit(QDate(today.year(), today.month(), 1))
        self.selected_month.setDisplayFormat("yyyy-MM")
        self.selected_month.setCalendarPopup(True)
        self.selected_month.setMinimumWidth(135)
        header.addWidget(self.selected_month)
        layout.addWidget(banner)

        self.controls = QWidget()
        controls = QVBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        toolbar = QHBoxLayout()
        toolbar.addStretch()
        self.settings_toggle = QPushButton("경로 / 계산 설정")
        self.settings_toggle.setCheckable(True)
        toolbar.addWidget(self.settings_toggle)
        self.calculate_button = QPushButton("선택 월 집계")
        self.calculate_button.setProperty("primary", True)
        self.calculate_button.clicked.connect(self.start_calculation)
        toolbar.addWidget(self.calculate_button)
        self.export_button = QPushButton("Excel 내보내기")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export)
        toolbar.addWidget(self.export_button)
        controls.addLayout(toolbar)

        group = QGroupBox("가스 집계용 ProcLog 경로 및 실측 sccm 컬럼")
        group.setVisible(False)
        self.settings_toggle.toggled.connect(group.setVisible)
        form = QFormLayout(group)
        path_row = QHBoxLayout()
        self.root_edit = QLineEdit(str(proc_log_dir or ""))
        self.root_edit.setToolTip("기본 경로는 메인 Settings의 Proc log dir (Gas usage)에서 저장합니다.")
        path_row.addWidget(self.root_edit, 1)
        browse = QPushButton("폴더 선택")
        browse.clicked.connect(self.browse)
        path_row.addWidget(browse)
        form.addRow("ProcLog 폴더", path_row)
        self.n2_edit = QLineEdit(self.settings.value("n2", ", ".join(DEFAULT_N2), type=str))
        self.o2_edit = QLineEdit(self.settings.value("o2", ", ".join(DEFAULT_O2), type=str))
        for edit in (self.n2_edit, self.o2_edit):
            edit.setToolTip("실측 sccm 컬럼 이름을 쉼표로 구분하세요. SP, 압력, Total 중복 합산을 피하세요.")
        form.addRow("N2 실측 sccm", self.n2_edit)
        form.addRow("O2 실측 sccm", self.o2_edit)
        extra = QHBoxLayout()
        self.time_edit = QLineEdit(self.settings.value("time", "Time", type=str))
        self.time_edit.setMaximumWidth(140)
        extra.addWidget(QLabel("시간 컬럼"))
        extra.addWidget(self.time_edit)
        extra.addWidget(QLabel("기록 간격 상한 (선택)"))
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0, 86400)
        self.gap.setDecimals(3)
        self.gap.setSuffix(" s")
        self.gap.setSpecialValueText("제한 없음")
        self.gap.setToolTip("앞뒤 시각 차이로 적산합니다. 간격 상한을 지정한 경우에만 더 긴 구간을 제외합니다.")
        # v3.01 wrote a default 60-second cap under 'gap'. Do not carry that
        # implicit cap into interval-aware accounting; new explicit limits persist.
        self.gap.setValue(self.settings.value("gap_limit_seconds", 0.0, type=float))
        extra.addWidget(self.gap)
        extra.addStretch()
        defaults = QPushButton("기본값 복원")
        defaults.clicked.connect(self.restore_defaults)
        extra.addWidget(defaults)
        form.addRow(extra)
        controls.addWidget(group)
        layout.addWidget(self.controls)

        self.progress_widget = QWidget()
        progress_row = QHBoxLayout(self.progress_widget)
        progress_row.setContentsMargins(0, 0, 0, 0)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        progress_row.addWidget(self.progress, 1)
        self.cancel_button = QPushButton("집계 취소")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_calculation)
        progress_row.addWidget(self.cancel_button)
        layout.addWidget(self.progress_widget)
        self.progress_widget.hide()
        self.status = QLabel("우측 상단의 월을 선택하고 선택 월 집계를 누르세요. 여러 달은 기간별 사용량 추이 탭에서 집계합니다.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#526675; font-size:11px;")
        layout.addWidget(self.status)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet("""
            QTabWidget::pane {border:0; background:transparent; top:8px;}
            QTabBar {background:transparent;}
            QTabBar::tab {background:#e9edf3; color:#64748b; border:1px solid transparent;
                          border-radius:10px; padding:10px 20px; margin:2px 8px 6px 0;}
            QTabBar::tab:hover {background:#e2ecfa; color:#0f6cbd;}
            QTabBar::tab:selected {background:white; border-color:#cad9ec; color:#0f6cbd; font-weight:700;}
        """)
        self._build_monthly_page()
        self._build_trend_page()
        self.tube_table, self.issues_table = (QTableWidget() for _ in range(2))
        for title, table in (("Tube별 상세", self.tube_table), ("검토 내역", self.issues_table)):
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.verticalHeader().hide()
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            self.tabs.addTab(table, title)
        layout.addWidget(self.tabs, 1)
        note = QLabel("단위 ft³ · 원본 sccm과 동일한 표준상태 · — = 유효 기록 없음 · 미기록 대기/퍼지 사용량 제외")
        note.setStyleSheet("font-size:10px; color:#657582;")
        note.setWordWrap(True)
        layout.addWidget(note)

        for edit in (self.root_edit, self.n2_edit, self.o2_edit, self.time_edit):
            edit.textChanged.connect(self.invalidate)
        self.month_from.dateChanged.connect(self.invalidate_trend)
        self.month_to.dateChanged.connect(self.invalidate_trend)
        self.gap.valueChanged.connect(self.invalidate)
        self.tube_filter.currentIndexChanged.connect(self.refresh_views)
        self.selected_month.dateChanged.connect(self.month_changed)
        self.tabs.currentChanged.connect(self.tab_changed)
        apply_chrome_input_styles(self)
        for button in self.findChildren(QPushButton):
            apply_shadow(button, blur=12, y_offset=2, color=QColor(15, 23, 42, 28))
        self.refresh_monthly()
        self.refresh_views()

    def _build_monthly_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 9, 0, 10)
        layout.setSpacing(10)
        self.month_caption = QLabel()
        self.month_caption.setStyleSheet("color:#526675; font-size:11px;")
        layout.addWidget(self.month_caption)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea {background:transparent; border:0;}")
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        self.cards_layout = QGridLayout(body)
        self.cards_layout.setContentsMargins(12, 8, 12, 16)
        self.cards_layout.setSpacing(18)
        self.cards_layout.setColumnStretch(0, 1)
        self.cards_layout.setColumnStretch(1, 1)
        self.cards_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        footer = QFrame()
        footer.setObjectName("gasTotals")
        footer.setStyleSheet("QFrame#gasTotals {background:white; border:1px solid #d9dee7; border-radius:18px;} QFrame#gasTotals QLabel {background:transparent; border:0;}")
        apply_shadow(footer)
        totals = QHBoxLayout(footer)
        totals.setContentsMargins(20, 17, 20, 17)
        self.monthly_totals = {}
        for gas, color in (("N2", "#0f6cbd"), ("O2", "#11877d")):
            name = QLabel(f"ALL TUBE {gas}")
            name.setStyleSheet("font-size:15px; font-weight:700; color:#526675;")
            value = QLabel("— ft³")
            value.setStyleSheet(f"font-size:20px; font-weight:700; color:{color};")
            totals.addWidget(name)
            totals.addWidget(value, 1, Qt.AlignHCenter)
            self.monthly_totals[gas] = value
        layout.addWidget(footer)
        self.tabs.addTab(page, "월별 Tube 현황")

    def _build_trend_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.trend_controls = QWidget()
        filters = QHBoxLayout(self.trend_controls)
        filters.setContentsMargins(0, 8, 0, 8)
        filters.addWidget(QLabel("집계 기간"))
        today = QDate.currentDate()
        self.month_from, self.month_to = QDateEdit(), QDateEdit()
        for edit, date in ((self.month_from, QDate(today.year(), 1, 1)),
                           (self.month_to, QDate(today.year(), today.month(), 1))):
            edit.setDisplayFormat("yyyy-MM")
            edit.setCalendarPopup(True)
            edit.setDate(date)
            edit.setMinimumWidth(135)
        filters.addWidget(self.month_from)
        filters.addWidget(QLabel("~"))
        filters.addWidget(self.month_to)
        self.trend_calculate_button = QPushButton("기간 집계")
        self.trend_calculate_button.setProperty("primary", True)
        self.trend_calculate_button.clicked.connect(lambda: self.start_calculation(scope="trend"))
        filters.addWidget(self.trend_calculate_button)
        filters.addStretch()
        filters.addWidget(QLabel("Tube"))
        self.tube_filter = QComboBox()
        self.tube_filter.addItem("모든 Tube", None)
        self.tube_filter.setMinimumWidth(130)
        filters.addWidget(self.tube_filter)
        layout.addWidget(self.trend_controls)
        self.summary = QLabel("N2: —    O2: —    합계: —")
        self.summary.setStyleSheet("font-size:18px; font-weight:700; color:#176596; padding:8px;")
        layout.addWidget(self.summary)
        self.chart = QChart()
        self.chart.setMargins(QMargins(16, 12, 16, 12))
        self.chart.setBackgroundRoundness(18)
        self.chart.setBackgroundBrush(QColor("white"))
        self.chart.setDropShadowEnabled(True)
        self.chart.setTitle("조회 기간 내 월별 N2 · O2 사용량 (ft³)")
        self.chart_view = QChartView(self.chart)
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        self.chart_view.setMinimumHeight(250)
        self.chart_view.setStyleSheet("background:transparent; border:0;")
        layout.addWidget(self.chart_view, 1)
        note = QLabel("기간을 선택한 뒤 기간 집계를 누르세요. 월별 카드와 별도로 계산합니다. 그래프 * = 가스 기록 누락.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#657582; font-size:11px;")
        layout.addWidget(note)
        self.tabs.addTab(page, "기간별 사용량 추이")

    def refresh_monthly(self, *_):
        month = self.selected_month.date().toString("yyyy-MM")
        configured = [s.strip() for text in (self.n2_edit.text(), self.o2_edit.text())
                      for s in text.split(",") if s.strip()]
        columns = list(dict.fromkeys(configured))
        tubes = [f"TUBE{i:02d}" for i in range(1, 7)]
        records = pd.DataFrame()
        channels = pd.DataFrame()
        result = self.monthly_source()
        if result is not None:
            records = result.tubes
            channels = result.channels
            tubes = sorted(set(tubes) | set(records["Tube"]))
            records = records[records["Month"] == month]
            channels = channels[channels["Month"] == month]
        signature = (tuple(tubes), tuple(columns))
        if self._card_signature != signature:
            while self.cards_layout.count():
                item = self.cards_layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            self.tube_cards = {}
            for index, tube in enumerate(tubes):
                card = TubeUsageCard(tube, columns)
                self.cards_layout.addWidget(card, index // 2, index % 2)
                self.tube_cards[tube] = card
            self._card_signature = signature
        for tube, card in self.tube_cards.items():
            values, totals, state = {}, {}, "기록 없음" if result is not None else "집계 대기"
            if not records.empty:
                selected = records[records["Tube"] == tube]
                for gas in ("N2", "O2"):
                    totals[gas] = selected[f"{gas} (ft³)"].sum(min_count=1)
                if any(pd.notna(v) for v in totals.values()):
                    state = "부분 집계" if (selected["Status"] != "기록 구간 집계").any() else "기록 구간 집계"
            if not channels.empty:
                values = channels[channels["Tube"] == tube].groupby("Channel")["Volume (ft³)"].sum(min_count=1).to_dict()
            card.set_usage(values, totals, state)
        for gas, label in self.monthly_totals.items():
            total = records[f"{gas} (ft³)"].sum(min_count=1) if not records.empty else float("nan")
            label.setText(volume_text(total))
        hint = "선택 월의 기록된 사용량 · N2 / O2" if result is not None else "선택 월 집계를 눌러 사용량을 확인하세요"
        self.month_caption.setText(f"{month}  |  전체 Tube  |  {hint}")

    def restore_defaults(self):
        self.n2_edit.setText(", ".join(DEFAULT_N2))
        self.o2_edit.setText(", ".join(DEFAULT_O2))
        self.time_edit.setText("Time")
        self.gap.setValue(0)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "공정 로그 폴더", self.root_edit.text())
        if path:
            self.root_edit.setText(path)

    def monthly_source(self):
        month = self.selected_month.date().toString("yyyy-MM")
        for result in (self.month_result, self.trend_result):
            if result is not None:
                start, end = result.settings.set_index("Setting").loc["Months", "Value"].split(" ~ ")
                if start <= month <= end:
                    return result
        return None

    def month_changed(self, *_):
        self.refresh_monthly()
        self.refresh_views()

    def tab_changed(self, index):
        if index in (0, 1):
            self._display_scope = "month" if index == 0 else "trend"
        self.refresh_views()

    def invalidate(self, *_):
        self.month_result = self.trend_result = None
        self.status.setText("설정이 변경되었습니다. 선택 월 또는 기간 집계를 눌러 다시 계산하세요.")
        self.refresh_monthly()
        self.refresh_views()

    def invalidate_trend(self, *_):
        self.trend_result = None
        self.refresh_monthly()
        self.refresh_views()

    def start_calculation(self, checked=False, *, scope="month"):
        if self.worker is not None:
            return
        start, end = ((self.month_from.date(), self.month_to.date()) if scope == "trend"
                      else (self.selected_month.date(), self.selected_month.date()))
        config = GasUsageConfig(
            start.toString("yyyy-MM"), end.toString("yyyy-MM"),
            tuple(s.strip() for s in self.n2_edit.text().split(",") if s.strip()),
            tuple(s.strip() for s in self.o2_edit.text().split(",") if s.strip()),
            self.time_edit.text().strip(), self.gap.value() or None)
        try:
            config.validate()
            if not Path(self.root_edit.text().strip()).is_dir() or not self.root_edit.text().strip():
                raise ValueError("공정 로그 폴더가 존재하지 않습니다.")
        except ValueError as exc:
            QMessageBox.warning(self, "월별 가스 사용량", str(exc))
            return
        self._calculation_scope = scope
        if scope == "month":
            self.month_result = None
        else:
            self.trend_result = None
        self.tabs.setCurrentIndex(0 if scope == "month" else 1)
        self.refresh_monthly()
        self.refresh_views()
        self.controls.setEnabled(False)
        self.selected_month.setEnabled(False)
        self.trend_controls.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_widget.show()
        self.progress.setRange(0, 0)
        self._active_period = (config.start_month if config.start_month == config.end_month
                               else f"{config.start_month} ~ {config.end_month}")
        self.status.setText(f"{self._active_period} · 대상 로그의 기록 기간을 확인합니다…")
        for key, value in (("n2", self.n2_edit.text()), ("o2", self.o2_edit.text()),
                           ("time", self.time_edit.text()), ("gap_limit_seconds", self.gap.value())):
            self.settings.setValue(key, value)
        self.worker = GasUsageWorker(self.root_edit.text().strip(), config, self)
        self.worker.progress.connect(self.update_progress)
        self.worker.succeeded.connect(self.show_result)
        self.worker.failed.connect(self.show_error)
        self.worker.cancelled.connect(lambda: self.status.setText("집계를 취소했습니다. 결과는 저장되지 않았습니다."))
        self.worker.finished.connect(self.worker_finished)
        self.worker.start(QThread.LowPriority)

    def update_progress(self, done, total, name):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.status.setText(f"{self._active_period} · {name} · {done:,} / {total:,} 파일")

    def cancel_calculation(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.status.setText("현재 파일 처리를 마치고 취소합니다…")

    def worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.controls.setEnabled(True)
        self.selected_month.setEnabled(True)
        self.trend_controls.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if self.result is not None else 0)
        self.progress_widget.hide()
        self.refresh_views()
        if self._close_when_finished:
            super().reject()

    def show_error(self, message):
        self.status.setText(f"집계 실패: {message}")

    def show_result(self, result):
        if self._calculation_scope == "trend":
            self.trend_result = result
            combo = self.tube_filter
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("모든 Tube", None)
            for value in sorted(result.tubes["Tube"].unique()):
                combo.addItem(value, value)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        else:
            self.month_result = result
        has_volume = result.tubes[VOLUME_COLUMNS[:2]].notna().any().any()
        prefix = "집계 완료" if has_volume else "선택 기간에 집계 가능한 기록이 없습니다"
        values = result.settings.set_index("Setting")["Value"]
        skipped = int(values.get("Skipped outside period", 0))
        if str(values.get("Result cache", "")).startswith("Reused"):
            prefix += " (변경 없음 · 결과 재사용)"
        period = values["Months"].split(" ~ ")
        label = period[0] if period[0] == period[1] else " ~ ".join(period)
        self.status.setText(f"{label} · {prefix} · 집계 대상 파일 {len(result.files):,}개 · 기간 외 제외 {skipped:,}개 · 검토 {len(result.issues):,}개. "
                            "검토 내역의 제외 구간과 누락 파일을 확인하세요.")
        self.refresh_monthly()
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
        self.result = self.monthly_source() if self._display_scope == "month" else self.trend_result
        scope = "선택 월" if self._display_scope == "month" else "기간"
        self.export_button.setText(f"{scope} Excel 내보내기")
        self.export_button.setEnabled(self.result is not None and self.worker is None)
        if self.result is None:
            self.tube_table.setRowCount(0)
            self.issues_table.setRowCount(0)
            self.tabs.setTabText(3, "검토 내역")
        else:
            tubes = self.result.tubes
            if self._display_scope == "month":
                tubes = tubes[tubes["Month"] == self.selected_month.date().toString("yyyy-MM")]
            self.fill_table(self.tube_table, tubes)
            self.fill_table(self.issues_table, self.result.issues)
            self.tabs.setTabText(3, f"검토 내역 ({len(self.result.issues):,})")
        self.chart.removeAllSeries()
        for axis in self.chart.axes():
            self.chart.removeAxis(axis)
            axis.deleteLater()
        if self.trend_result is None:
            self.summary.setText("기간을 선택하고 기간 집계를 누르세요.")
            self.chart.setTitle("기간 집계 후 월별 N2 · O2 사용량을 표시합니다")
            return
        tube = self.tube_filter.currentData()
        tubes = self.trend_result.tubes
        if tube is not None:
            tubes = tubes[tubes["Tube"] == tube]
        shown = tubes if tube is not None else self.trend_result.monthly
        monthly = shown.groupby("Month")[VOLUME_COLUMNS[:2]].sum(min_count=1)
        totals = monthly.sum(min_count=1)
        total = totals.iloc[0] + totals.iloc[1]
        def display(value):
            return "—" if pd.isna(value) else f"{value:,.4f} ft³"
        self.summary.setText(f"선택 기간 · N2: {display(totals.iloc[0])}    O2: {display(totals.iloc[1])}    합계: {display(total)}")
        series = QBarSeries()
        for gas, color in (("N2", "#0f6cbd"), ("O2", "#11877d")):
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
        self.chart.setTitle(f"{tube or '모든 Tube'} · 월별 기록 사용량")

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
            export_gas_usage(select_usage_month(self.result, self.selected_month.date().toString("yyyy-MM"))
                             if self._display_scope == "month" else self.result, path)
        except Exception as exc:
            QMessageBox.warning(self, "내보내기 실패", str(exc))
            return
        QMessageBox.information(self, "내보내기 완료", "선택한 집계 범위의 모든 Tube 결과와 계산 설정을 저장했습니다.")

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
