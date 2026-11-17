import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PySide6.QtCore import QCoreApplication, QDate, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from fluent_ui import configure_fluent
from gas_usage import CalculationCancelled, CM3_PER_FT3, DEFAULT_N2, DEFAULT_O2
from main import MainWindow


class FluentNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        configure_fluent(cls.app)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / "ui.ini"), QSettings.IniFormat)
        with patch("main.QSettings", return_value=self.settings), patch("gas_usage_dialog.QSettings", return_value=self.settings):
            self.window = MainWindow(self.root, self.root, self.root, proc_log_dir=self.root)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        gas = self.window.gas_page
        if gas.worker is not None:
            gas.worker.requestInterruption()
            gas.worker.wait(5000)
            self.app.processEvents()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_navigation_preserves_calculated_gas_and_graph_selection(self):
        data = pd.DataFrame({"Time": ["2026-10-01 00:00:00", "2026-10-01 00:01:00"]})
        for columns in (DEFAULT_N2, DEFAULT_O2):
            for column in columns:
                data[column] = CM3_PER_FT3 / len(columns)
        path = self.root / "TUBE01_261001_0.csv"
        data.to_csv(path, index=False)
        panel = self.window.plot_panel
        panel.load_csv(path)
        self.assertEqual(panel.csv_path, path)
        area = panel._areas[0]
        self.assertTrue(area.left_series)
        nav = self.window.navigationInterface
        QTest.mouseClick(nav.widget("gasUsageDialog").itemWidget, Qt.LeftButton)
        gas = self.window.gas_page
        self.assertIs(self.window.stackedWidget.currentWidget(), gas)
        gas.selected_month.picker.setDate(QDate(2026, 10, 1))
        gas.calculate_button.click()
        self.wait_until(lambda: gas.worker is None)
        self.assertAlmostEqual(gas.month_result.monthly.iloc[0]["N2 (ft³)"], 1)
        result = gas.month_result
        QTest.mouseClick(nav.widget("settingsPage").itemWidget, Qt.LeftButton)
        self.assertIs(self.window.stackedWidget.currentWidget(), self.window.settings_page)
        QTest.mouseClick(nav.widget("logWorkspace").itemWidget, Qt.LeftButton)
        self.assertIs(panel._areas[0], area)
        self.assertEqual(panel.csv_path, path)
        QTest.mouseClick(nav.widget("gasUsageDialog").itemWidget, Qt.LeftButton)
        self.assertIs(gas.month_result, result)
        QTest.keyClick(gas, Qt.Key_Escape)
        self.assertTrue(gas.isVisible())

    def test_closing_main_window_cancels_worker_and_blocks_path_edits_until_finished(self):
        def slow_scan(root, config, progress, cancelled, **kwargs):
            while not cancelled():
                time.sleep(.001)
            raise CalculationCancelled()
        gas = self.window.gas_page
        with patch("gas_usage_dialog.calculate_monthly_usage", side_effect=slow_scan):
            gas.start_calculation()
            with patch("main.AppSettingsDialog") as settings, patch("main.QMessageBox.information") as notice:
                self.window.open_settings_dialog()
                settings.assert_not_called()
                notice.assert_called_once()
            self.window.close()
            self.wait_until(lambda: gas.worker is None and not self.window.isVisible())
        self.assertIsNone(gas.result)

    def test_segmented_pages_update_export_scope_and_calendar_does_not_repeat_unchanged_date(self):
        gas = self.window.gas_page
        calls = []
        gas.selected_month.dateChanged.connect(calls.append)
        gas.selected_month.setDate(QDate(2026, 10, 1))
        gas.selected_month.setDate(QDate(2026, 10, 1))
        self.assertEqual(calls, [QDate(2026, 10, 1)])
        gas.tabs.pivot.items["1"].click()
        self.assertEqual(gas.tabs.currentIndex(), 1)
        self.assertEqual(gas._display_scope, "trend")
        gas.tabs.pivot.items["0"].click()
        self.assertEqual(gas._display_scope, "month")
        self.assertFalse(gas.export_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
