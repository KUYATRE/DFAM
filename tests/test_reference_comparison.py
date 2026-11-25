import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from step_statistics import calculate_step_statistics, StepStatisticsDialog
from reference_comparison import (
    compare_step_statistics, export_reference_comparison, ReferenceComparisonDialog,
)
from test_step_statistics import sample_log


def statistics(data):
    return calculate_step_statistics(data, "Time", "Step No", "Step Name")


def changed_log():
    data = sample_log()
    data["Temperature"] += 3
    data["Time"] = ["2026-11-18 01:00:00", "2026-11-18 01:00:02", "2026-11-18 01:00:07",
                    "2026-11-18 01:00:11", "2026-11-18 01:00:15"]
    return data


class ReferenceComparisonTests(unittest.TestCase):
    def test_direction_percentage_and_repeated_occurrences(self):
        result = compare_step_statistics(statistics(sample_log()), statistics(changed_log()))
        self.assertEqual(result.steps["Step"].tolist(), ["1", "2", "1"])
        self.assertEqual(result.steps["Occurrence"].tolist(), [1, 1, 2])
        self.assertEqual(result.steps["Delta (s)"].tolist(), [2, 0, 1])
        self.assertEqual(result.steps.iloc[0]["Delta (%)"], 40)
        means = result.sensors.query("Sensor == 'Temperature' and Statistic == 'Mean'")
        self.assertEqual(means["Delta"].tolist(), [3, 3, 3])
        self.assertEqual(means.iloc[0]["Delta (%)"], 25)
        self.assertEqual(result.steps.iloc[-1]["Time comparison"], "Recorded span only")
        std = result.sensors.query("Sensor == 'Temperature' and Statistic == 'Std dev'")
        self.assertEqual(std.iloc[0]["Delta"], 0)
        self.assertTrue(pd.isna(std.iloc[1]["Delta"]))

    def test_same_log_zero_and_swapping_reverses_signed_difference(self):
        ref, target = statistics(sample_log()), statistics(changed_log())
        same = compare_step_statistics(ref, ref)
        self.assertTrue(same.sensors["Delta"].dropna().eq(0).all())
        forward, reverse = compare_step_statistics(ref, target), compare_step_statistics(target, ref)
        pd.testing.assert_series_equal(forward.sensors["Delta"], -reverse.sensors["Delta"])
        pd.testing.assert_series_equal(forward.steps["Delta (s)"], -reverse.steps["Delta (s)"])

    def test_zero_and_negative_reference_values(self):
        ref, target = sample_log(), changed_log()
        ref["Temperature"] = [0, 0, -10, -10, -10]
        target["Temperature"] = [3, 3, -8, -12, -12]
        result = compare_step_statistics(statistics(ref), statistics(target))
        means = result.sensors.query("Sensor == 'Temperature' and Statistic == 'Mean'")
        self.assertEqual(means["Delta"].tolist(), [3, 2, -2])
        self.assertTrue(pd.isna(means.iloc[0]["Delta (%)"]))
        self.assertEqual(means.iloc[1]["Delta (%)"], 20)
        self.assertEqual(means.iloc[2]["Delta (%)"], -20)

    def test_unmatched_steps_sensors_and_missing_values_remain_visible(self):
        ref, target = sample_log(), changed_log()
        target.loc[2, "Step No"] = 3
        target = target.drop(columns="Pressure")
        target["Flow"] = 5
        result = compare_step_statistics(statistics(ref), statistics(target))
        self.assertEqual(result.steps["Match"].tolist(), ["Matched", "Target only", "Matched", "Reference only"])
        self.assertTrue(result.steps.loc[result.steps["Match"] != "Matched", "Delta (s)"].isna().all())
        pressure = result.sensors.query("Sensor == 'Pressure'")
        self.assertTrue(pressure["Delta"].isna().all())
        self.assertTrue(pressure["Match"].eq("Reference only").all())
        flow = result.sensors.query("Sensor == 'Flow'")
        self.assertTrue(flow["Delta"].isna().all())
        self.assertTrue(flow["Match"].eq("Target only").all())

    def test_unknown_steps_are_never_matched(self):
        data = sample_log()
        data.loc[2, "Step No"] = float("nan")
        result = compare_step_statistics(statistics(data), statistics(data))
        unknown = result.steps.loc[result.steps["Step"].eq("(Unknown)")]
        self.assertEqual(len(unknown), 2)
        self.assertTrue(unknown["Delta (s)"].isna().all())
        self.assertTrue(result.sensors.loc[result.sensors["Step"].eq("(Unknown)"), "Delta"].isna().all())

    def test_step_name_mismatch_has_raw_values_but_no_difference(self):
        ref, target = sample_log(), changed_log()
        target.loc[:1, "Step Name"] = "Cool"
        result = compare_step_statistics(statistics(ref), statistics(target))
        self.assertEqual(result.steps.iloc[0]["Match"], "Step names differ")
        self.assertEqual(result.steps.iloc[0]["Target (s)"], 7)
        self.assertTrue(pd.isna(result.steps.iloc[0]["Delta (s)"]))
        metrics = result.sensors.loc[result.sensors["Pair"].eq(1)]
        self.assertTrue(metrics["Delta"].isna().all())
        self.assertTrue(metrics["Reference"].notna().any())

    def test_partial_and_completed_durations_are_not_subtracted(self):
        result = compare_step_statistics(statistics(sample_log()), statistics(changed_log().iloc[:2]))
        self.assertEqual(result.steps.iloc[0]["Time comparison"], "Duration basis differs")
        self.assertTrue(pd.isna(result.steps.iloc[0]["Delta (s)"]))
        means = result.sensors.query("Pair == 1 and Sensor == 'Temperature' and Statistic == 'Mean'")
        self.assertEqual(means.iloc[0]["Delta"], 3)

    def test_excel_contains_sources_all_statistics_and_real_numbers(self):
        result = compare_step_statistics(statistics(sample_log()), statistics(changed_log()))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "comparison.xlsx"
            export_reference_comparison(result, path, "reference.csv", "target.csv")
            sheets = pd.read_excel(path, sheet_name=None)
            self.assertEqual(list(sheets), ["Step comparison", "Sensor comparison", "Notes"])
            self.assertEqual(sheets["Step comparison"].iloc[0]["Delta (s)"], 2)
            self.assertEqual(len(sheets["Sensor comparison"]), len(result.sensors))
            self.assertEqual(sheets["Notes"].iloc[0, 0], "Reference: reference.csv")
            self.assertEqual(sheets["Notes"].iloc[1, 0], "Target: target.csv")


class ReferenceComparisonUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selection_change_filter_export_cancel_and_load_failure(self):
        logs = {"ref.csv": statistics(sample_log()), "same.csv": statistics(changed_log())}
        dialog = ReferenceComparisonDialog(statistics(changed_log()), Path("target.csv"), lambda p: logs[p.name])
        try:
            self.assertFalse(dialog.export_button.isEnabled())
            with patch("reference_comparison.QFileDialog.getOpenFileName", return_value=("ref.csv", "")):
                dialog.choose_reference()
            self.assertEqual(dialog.reference_path, Path("ref.csv"))
            self.assertEqual(dialog.steps_table.rowCount(), 3)
            dialog.steps_table.selectRow(2)
            dialog.sensor_filter.setText("temperature")
            self.assertEqual(dialog.sensors_table.rowCount(), 1)
            self.assertEqual(dialog.sensors_table.item(0, 5).text(), "3")
            dialog.statistic.setCurrentText("All statistics")
            self.assertEqual(dialog.sensors_table.rowCount(), 4)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "comparison.xlsx"
                with patch("reference_comparison.QFileDialog.getSaveFileName", return_value=(str(path), "")), \
                        patch("reference_comparison.QMessageBox.information"):
                    dialog.export_excel()
                self.assertEqual(len(pd.read_excel(path, sheet_name="Sensor comparison")), 24)
            self.assertTrue(dialog.set_reference_path("same.csv"))
            self.assertTrue(dialog.result.sensors["Delta"].dropna().eq(0).all())
            previous_result = dialog.result
            with patch("reference_comparison.QMessageBox.critical") as error:
                self.assertFalse(dialog.set_reference_path("missing.csv"))
                error.assert_called_once()
            self.assertIs(dialog.result, previous_result)
            self.assertEqual(dialog.reference_path, Path("same.csv"))
            with patch("reference_comparison.QFileDialog.getOpenFileName", return_value=("", "")):
                dialog.choose_reference()
            self.assertIs(dialog.result, previous_result)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_entry_button_and_reference_csv_loader(self):
        from main import CsvPlotPanel
        target = statistics(sample_log())
        dialog = StepStatisticsDialog(target, Path("target.csv"))
        signals = []
        dialog.compareRequested.connect(lambda: signals.append(True))
        dialog.compare_button.click()
        self.assertEqual(signals, [True])
        dialog.close()
        dialog.deleteLater()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reference.csv"
            data = sample_log()
            data["Step Name"] = "가열"
            data.to_csv(path, index=False, encoding="cp949")
            loaded = CsvPlotPanel._load_statistics_for_comparison(path)
            self.assertEqual(loaded.summary.iloc[0]["Step name"], "가열")
            data.drop(columns="Step No").to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "step number"):
                CsvPlotPanel._load_statistics_for_comparison(path)

    def test_panel_navigation_opens_comparison_above_statistics_dialog(self):
        from main import CsvPlotPanel
        panel = CsvPlotPanel(Path("."))
        panel.df, panel.csv_path = sample_log(), Path("target.csv")
        panel._detect_step_columns()
        observed = []

        def open_comparison():
            statistics_dialog = self.app.activeModalWidget()

            def close_comparison():
                comparison = self.app.activeModalWidget()
                observed.append(isinstance(comparison, ReferenceComparisonDialog))
                observed.append(comparison.parent() is statistics_dialog)
                comparison.reject()

            QTimer.singleShot(0, close_comparison)
            statistics_dialog.compare_button.click()
            statistics_dialog.reject()

        try:
            QTimer.singleShot(0, open_comparison)
            panel.show_step_statistics()
            self.assertEqual(observed, [True, True])
        finally:
            panel.close()
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
