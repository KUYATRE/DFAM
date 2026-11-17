import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtCore import QDate, QSettings, QEventLoop, QTimer, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from gas_usage import (
    CM3_PER_FT3, DEFAULT_N2, DEFAULT_O2, CalculationCancelled, GasUsageConfig,
    calculate_monthly_usage, export_gas_usage,
)
from gas_usage_dialog import GasUsageDialog


def log(times, n2=CM3_PER_FT3, o2=CM3_PER_FT3, tube=1, equipment=1):
    frame = pd.DataFrame({"Time": times, "EQP ID": equipment, "Tube ID": tube})
    for columns, values in ((DEFAULT_N2, n2), (DEFAULT_O2, o2)):
        for col in columns:
            frame[col] = np.asarray(values) / len(columns)
    return frame


class GasUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = GasUsageConfig("2026-01", "2026-02")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, frame, name="TUBE01_recipe_1_20260101_000000.csv", **kwargs):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, **kwargs)
        return path

    def result(self, config=None):
        return calculate_monthly_usage(self.root, config or self.config)

    def test_one_cubic_foot_per_minute_and_only_actual_channels(self):
        frame = log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"])
        for name in ["Low N2(SP)", "UTIL-N2(Psi)", "N2 Total [sccm]", "OffSet Low N2"]:
            frame[name] = 1e9
        self.write(frame)
        result = self.result()
        row = result.tubes.iloc[0]
        self.assertAlmostEqual(row["N2 (ft³)"], 1)
        self.assertAlmostEqual(row["O2 (ft³)"], 1)
        self.assertAlmostEqual(row["N2+O2 (ft³)"], 2)
        self.assertAlmostEqual(row["N2 recorded (h)"], 1 / 60)
        self.assertTrue(pd.isna(result.tubes.iloc[1]["N2 (ft³)"]))
        self.assertEqual(result.tubes.iloc[1]["Status"], "기록 없음")

    def test_irregular_sampling_uses_trapezoids_not_row_count(self):
        self.write(log(["2026-01-01 00:00:00", "2026-01-01 00:00:15", "2026-01-01 00:01:00"],
                       n2=[0, CM3_PER_FT3, CM3_PER_FT3], o2=0))
        self.assertAlmostEqual(self.result().tubes.iloc[0]["N2 (ft³)"], 0.875)

    def test_month_boundary_interpolates_rate_and_filters_partial_interval(self):
        self.write(log(["2026-01-31 23:59:30", "2026-02-01 00:00:30"],
                       n2=[0, CM3_PER_FT3 * 2]))
        result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 0.25)
        self.assertAlmostEqual(result.tubes.iloc[1]["N2 (ft³)"], 0.75)
        feb = self.result(GasUsageConfig("2026-02", "2026-02"))
        self.assertAlmostEqual(feb.tubes.iloc[0]["N2 (ft³)"], 0.75)

    def test_leap_year_and_year_boundary(self):
        self.write(log(["2028-02-29 23:59:30", "2028-03-01 00:00:30"]))
        result = self.result(GasUsageConfig("2028-02", "2028-03"))
        self.assertEqual(result.tubes["Month"].tolist(), ["2028-02", "2028-03"])
        self.assertTrue(np.allclose(result.tubes["N2 (ft³)"], [0.5, 0.5]))
        self.write(log(["2028-12-31 23:59:30", "2029-01-01 00:00:30"]), name="TUBE01_year.csv")
        result = self.result(GasUsageConfig("2028-12", "2029-01"))
        self.assertTrue(np.allclose(result.tubes["N2 (ft³)"], [0.5, 0.5]))

    def test_equipment_sum_and_equipment_ids_remain_separate(self):
        times = ["2026-01-01 00:00:00", "2026-01-01 00:01:00"]
        self.write(log(times))
        self.write(log(times, tube=2, n2=CM3_PER_FT3 * 3), name="TUBE02_run.csv")
        self.write(log(times, equipment=2), name="second/TUBE01_run.csv")
        jan = self.result().equipment.query("Month == '2026-01'").set_index("Equipment")
        self.assertAlmostEqual(jan.loc["EQP 1", "N2 (ft³)"], 4)
        self.assertAlmostEqual(jan.loc["EQP 1", "O2 (ft³)"], 2)
        self.assertAlmostEqual(jan.loc["EQP 2", "N2 (ft³)"], 1)

    def test_duplicate_and_partial_overlaps_count_only_uncovered_tails(self):
        first = log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"])
        self.write(first, name="TUBE01_a.csv")
        self.write(first, name="copy/TUBE01_a.csv")
        self.write(log(["2026-01-01 00:00:30", "2026-01-01 00:01:30"],
                       n2=CM3_PER_FT3 * 2), name="TUBE01_b.csv")
        result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 2)
        self.assertAlmostEqual(result.tubes.iloc[0]["O2 (ft³)"], 1.5)
        self.assertTrue(result.issues["Issue"].str.contains("중복/겹침").any())

    def test_invalid_gas_endpoints_are_excluded_not_clamped_or_bridged(self):
        frame = log(pd.date_range("2026-01-01", periods=4, freq="1min").astype(str))
        frame.loc[1, DEFAULT_N2[0]] = -1
        self.write(frame)
        result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 1)
        self.assertAlmostEqual(result.tubes.iloc[0]["O2 (ft³)"], 3)
        self.assertTrue(result.issues["Issue"].str.contains("음수").any())

    def test_nan_infinity_and_missing_channel_are_unavailable_not_zero(self):
        frame = log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"])
        frame.loc[0, DEFAULT_N2[0]] = np.inf
        frame = frame.drop(columns=DEFAULT_O2[0])
        self.write(frame)
        result = self.result()
        self.assertTrue(result.tubes[["N2 (ft³)", "O2 (ft³)"]].isna().all().all())
        self.assertEqual(len(result.issues), 2)

    def test_timestamp_gaps_bad_rows_and_files_are_not_bridged(self):
        self.write(log(["2026-01-01 00:00:00", "bad", "2026-01-01 00:01:00", "2026-01-01 00:03:00"]))
        self.write(log(["2026-01-01 00:04:00", "2026-01-01 00:05:00"]), name="TUBE01_second.csv")
        limited = self.result(GasUsageConfig("2026-01", "2026-02", max_gap_seconds=60))
        self.assertAlmostEqual(limited.tubes.iloc[0]["N2 (ft³)"], 1)
        self.assertTrue(limited.issues["Issue"].str.contains("60초 초과").any())

    def test_same_duration_at_one_thirty_and_120_second_cadences(self):
        for interval in (1, 30, 120):
            with self.subTest(interval=interval):
                times = pd.date_range("2026-01-01", periods=240 // interval + 1, freq=f"{interval}s")
                self.write(log(times.astype(str)))
                result = self.result()
                self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 4)
                self.assertAlmostEqual(result.tubes.iloc[0]["N2 recorded (h)"], 240 / 3600)
                self.assertEqual(result.settings.set_index("Setting").loc["Maximum gap (s)", "Value"], "Unlimited")

    def test_mixed_cadences_integrate_a_linear_ramp_using_actual_seconds(self):
        elapsed = np.array([0, 1, 31, 121, 240])
        times = pd.Timestamp("2026-01-01") + pd.to_timedelta(elapsed, unit="s")
        self.write(log(times.astype(str), n2=CM3_PER_FT3 * elapsed / 240))
        row = self.result().tubes.iloc[0]
        self.assertAlmostEqual(row["N2 (ft³)"], 2)
        self.assertAlmostEqual(row["O2 (ft³)"], 4)

    def test_long_interval_crossing_month_boundary_is_split_by_elapsed_time(self):
        self.write(log(["2026-01-31 23:59:00", "2026-02-01 00:02:00"],
                       n2=[0, 3 * CM3_PER_FT3]))
        result = self.result()
        self.assertAlmostEqual(result.tubes.iloc[0]["N2 (ft³)"], 0.5)
        self.assertAlmostEqual(result.tubes.iloc[1]["N2 (ft³)"], 4)
        self.assertAlmostEqual(result.tubes["N2 recorded (h)"].sum(), 180 / 3600)

    def test_numeric_time_and_backwards_time_are_not_calendar_intervals(self):
        self.write(log([0, 60]))
        self.assertTrue(self.result().tubes["N2 (ft³)"].isna().all())
        self.write(log(["2026-01-01 00:01:00", "2026-01-01 00:00:00"]))
        self.assertTrue(self.result().tubes["N2 (ft³)"].isna().all())

    def test_compact_time_cp949_uppercase_and_tube_directory(self):
        frame = log(["20260101-00:00:00", "20260101-00:01:00"])
        frame["설명"] = "가열"
        self.write(frame, name="TUBE01/run.CSV", encoding="cp949")
        self.assertAlmostEqual(self.result().tubes.iloc[0]["N2 (ft³)"], 1)

    def test_conflicting_tube_and_unknown_file_report_errors(self):
        self.write(log(["2026-01-01", "2026-01-01 00:01:00"], tube=2))
        self.write(log(["2026-01-01", "2026-01-01 00:01:00"]), name="unknown.csv")
        result = self.result()
        self.assertTrue(result.tubes.empty)
        self.assertEqual(len(result.issues), 2)

    def test_empty_folder_and_cancellation(self):
        result = self.result()
        self.assertTrue(result.tubes.empty)
        self.assertTrue(result.equipment.empty)
        with self.assertRaises(CalculationCancelled):
            calculate_monthly_usage(self.root, self.config, cancelled=lambda: True)

    def test_config_validation(self):
        for config in [GasUsageConfig("2026-02", "2026-01"),
                       GasUsageConfig("2026-01", "2026-02", max_gap_seconds=0),
                       GasUsageConfig("2026-01", "2026-02", n2_columns=("x",), o2_columns=("x",))]:
            with self.assertRaises(ValueError):
                config.validate()

    def test_excel_has_numeric_volumes_settings_and_review(self):
        self.write(log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"]))
        result = self.result()
        path = self.root / "result.xlsx"
        export_gas_usage(result, path)
        with pd.ExcelFile(path) as book:
            self.assertEqual(len(book.sheet_names), 6)
            frame = pd.read_excel(book, sheet_name="Tube monthly")
            self.assertAlmostEqual(frame.iloc[0]["N2 (ft³)"], 1)
            self.assertTrue(pd.isna(frame.iloc[1]["N2 (ft³)"]))
            self.assertIn("Formula", pd.read_excel(book, sheet_name="Settings")["Setting"].tolist())
            channels = pd.read_excel(book, sheet_name="Channel monthly")
            self.assertAlmostEqual(channels.query("Gas == 'N2'")["Volume (ft³)"].sum(), 1)

    def test_channel_values_share_overlap_and_month_clipping_with_totals(self):
        self.write(log(["2026-01-31 23:59:30", "2026-02-01 00:00:30"],
                       n2=[0, 2 * CM3_PER_FT3]), name="TUBE01_a.csv")
        later = log(["2026-02-01 00:00:00", "2026-02-01 00:01:00"])
        for column in DEFAULT_N2:
            later[column] = 0
        later[DEFAULT_N2[0]] = CM3_PER_FT3
        self.write(later, name="TUBE01_b.csv")
        result = self.result()
        jan = result.channels.query("Month == '2026-01' and Gas == 'N2'")
        feb = result.channels.query("Month == '2026-02' and Gas == 'N2'")
        self.assertTrue(np.allclose(jan["Volume (ft³)"], 0.0625))
        self.assertAlmostEqual(feb.set_index("Channel").loc[DEFAULT_N2[0], "Volume (ft³)"], 0.6875)
        self.assertAlmostEqual(feb["Volume (ft³)"].sum(), result.tubes.iloc[1]["N2 (ft³)"])
        self.assertAlmostEqual(feb["Volume (ft³)"].sum(), 1.25)


class GasUsageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_close_waits_for_cancellation_before_destroying_worker(self):
        def slow_scan(root, config, progress, cancelled):
            while not cancelled():
                time.sleep(0.001)
            raise CalculationCancelled()

        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "ui.ini"), QSettings.IniFormat)
            dialog = GasUsageDialog(folder, settings=settings)
            with patch("gas_usage_dialog.calculate_monthly_usage", side_effect=slow_scan):
                dialog.start_calculation()
                loop = QEventLoop()
                timeout = QTimer()
                timeout.setSingleShot(True)
                timeout.timeout.connect(loop.quit)
                dialog.worker.finished.connect(loop.quit)
                dialog.reject()
                timeout.start(10000)
                loop.exec()
                timeout.stop()
                try:
                    self.assertIsNone(dialog.worker)
                    self.assertIsNone(dialog.result)
                    self.assertIn("취소", dialog.status.text())
                finally:
                    if dialog.worker is not None:
                        dialog.worker.requestInterruption()
                        dialog.worker.wait()
                        self.app.processEvents()
                    dialog.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_worker_calculates_filters_and_invalidates_export_after_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log(["2026-01-01 00:00:00", "2026-01-01 00:01:00"]).to_csv(root / "TUBE01_run.csv", index=False)
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            dialog = GasUsageDialog(root, settings=settings)
            dialog.month_from.setDate(QDate(2026, 1, 1))
            dialog.month_to.setDate(QDate(2026, 2, 1))
            dialog.start_calculation()
            loop = QEventLoop()
            dialog.worker.finished.connect(loop.quit)
            timeout = QTimer()
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)
            timeout.start(10000)
            loop.exec()
            timeout.stop()
            try:
                self.assertIsNone(dialog.worker)
                self.assertIsNotNone(dialog.result)
                self.assertEqual(dialog.tube_table.rowCount(), 2)
                self.assertTrue(dialog.export_button.isEnabled())
                dialog.tube_filter.setCurrentIndex(1)
                self.assertIn("1.0000", dialog.summary.text())
                dialog.n2_edit.setText("other")
                self.assertFalse(dialog.export_button.isEnabled())
                self.assertIsNone(dialog.result)
                self.assertEqual(dialog.tube_table.rowCount(), 0)
            finally:
                if dialog.worker is not None:
                    dialog.worker.requestInterruption()
                    dialog.worker.wait()
                    self.app.processEvents()
                dialog.close()
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_dashboard_uses_proc_log_directory_independent_of_root(self):
        from main import CsvPlotPanel
        panel = CsvPlotPanel(Path("."))
        panel.root_dir = Path("chosen_process_logs")
        panel.proc_log_dir = Path("separate_gas_logs")
        try:
            with patch("main.GasUsageDialog") as dialog, patch("main.apply_chrome_input_styles"):
                panel.btn_gas_usage.click()
                dialog.assert_called_once_with(panel.proc_log_dir, panel)
                dialog.return_value.exec.assert_called_once()
        finally:
            panel.close()
            panel.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_legacy_default_gap_is_removed_but_new_explicit_limit_is_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "ui.ini"), QSettings.IniFormat)
            settings.setValue("gap", 60)
            dialog = GasUsageDialog(folder, settings=settings)
            self.assertEqual(dialog.gap.value(), 0)
            dialog.deleteLater()
            settings.setValue("gap_limit_seconds", 120)
            restored = GasUsageDialog(folder, settings=settings)
            self.assertEqual(restored.gap.value(), 120)
            restored.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_selected_month_cards_do_not_narrow_the_period_trend(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for month, multiplier in (("01", 1), ("02", 4)):
                for tube in (1, 2):
                    times = [f"2026-{month}-01 00:00:00", f"2026-{month}-01 00:01:00"]
                    log(times, n2=CM3_PER_FT3 * multiplier * tube, tube=tube).to_csv(
                        root / f"TUBE0{tube}_{month}.csv", index=False)
            result = calculate_monthly_usage(root, GasUsageConfig("2026-01", "2026-02"))
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            dialog = GasUsageDialog(root, settings=settings)
            try:
                dialog.show_result(result)
                dialog.selected_month.setDate(QDate(2026, 2, 1))
                self.assertIn("12.000000", dialog.monthly_totals["N2"].text())
                self.assertIn("1.000000", dialog.tube_cards["TUBE01"].values[DEFAULT_N2[0]].text())
                dialog.selected_month.setDate(QDate(2026, 1, 1))
                self.assertIn("3.000000", dialog.monthly_totals["N2"].text())
                self.assertIn("0.250000", dialog.tube_cards["TUBE01"].values[DEFAULT_N2[0]].text())
                dialog.tube_filter.setCurrentIndex(1)
                self.assertIn("5.0000", dialog.summary.text())
                self.assertIn("3.000000", dialog.monthly_totals["N2"].text())
                bars = dialog.chart.series()[0].barSets()[0]
                self.assertEqual([bars.at(0), bars.at(1)], [1, 4])
                dialog.n2_edit.setText("replacement")
                self.assertIn("—", dialog.monthly_totals["N2"].text())
                self.assertIn("—", dialog.tube_cards["TUBE01"].values["replacement"].text())
            finally:
                dialog.close()
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
