import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from openpyxl import load_workbook
from PySide6.QtWidgets import QApplication

from step_statistics import calculate_step_statistics, export_step_statistics, StepStatisticsDialog


def sample_log():
    return pd.DataFrame({
        "Time": ["2026-11-17 00:00:00", "2026-11-17 00:00:02", "2026-11-17 00:00:05",
                 "2026-11-17 00:00:09", "2026-11-17 00:00:12"],
        "Step No": [1, 1, 2, 1, 1],
        "Step Name": ["Heat", "Heat", "Hold", "Heat", "Heat"],
        "Temperature": [10, 14, 20, 30, 34],
        "Pressure": [1, None, 3, float("inf"), "bad"],
    })


class StepStatisticsTests(unittest.TestCase):
    def calculate(self, data=None):
        return calculate_step_statistics(sample_log() if data is None else data,
                                         "Time", "Step No", "Step Name")

    def test_repeated_steps_are_separate_and_duration_uses_transition(self):
        original = sample_log()
        result = self.calculate(original)
        pd.testing.assert_frame_equal(original, sample_log())
        self.assertEqual(result.summary["Step"].tolist(), ["1", "2", "1"])
        self.assertEqual(result.summary["Occurrence"].tolist(), [1, 1, 2])
        self.assertEqual(result.summary["Duration (s)"].tolist(), [5, 4, 3])
        self.assertEqual(result.summary["Samples"].tolist(), [2, 1, 2])
        self.assertEqual(result.summary.iloc[-1]["Duration basis"], "Last sample (partial)")
        temperature = result.metrics.query("Sensor == 'Temperature'").iloc[0]
        self.assertEqual(temperature["Mean"], 12)
        self.assertEqual(temperature["Min"], 10)
        self.assertEqual(temperature["Max"], 14)
        self.assertAlmostEqual(temperature["Std dev"], 8 ** 0.5)

    def test_missing_and_nonfinite_values_do_not_become_zero(self):
        result = self.calculate()
        pressure = result.metrics.query("Sensor == 'Pressure'")
        self.assertEqual(pressure["Valid"].tolist(), [1, 1, 0])
        self.assertEqual(pressure["Missing"].tolist(), [1, 0, 2])
        self.assertTrue(pressure["Std dev"].isna().all())
        self.assertTrue(pd.isna(pressure.iloc[-1]["Mean"]))

    def test_numeric_axis_is_not_interpreted_as_nanosecond_dates(self):
        for values in ([0, 2, 5, 9, 12], ["0", "2", "5", "9", "12"]):
            with self.subTest(values=values):
                data = sample_log()
                data["Time"] = values
                result = self.calculate(data)
                self.assertTrue(result.summary["Duration (s)"].isna().all())
                self.assertEqual(result.metrics.iloc[0]["Mean"], 12)

    def test_missing_or_reversed_times_invalidate_only_affected_durations(self):
        data = sample_log()
        data.loc[1, "Time"] = "bad"
        result = self.calculate(data)
        self.assertTrue(pd.isna(result.summary.iloc[0]["Duration (s)"]))
        self.assertEqual(result.summary.iloc[1]["Duration (s)"], 4)
        data.loc[1, "Time"] = "2026-11-16 23:59:59"
        self.assertTrue(pd.isna(self.calculate(data).summary.iloc[0]["Duration (s)"]))

    def test_missing_steps_and_single_row(self):
        data = sample_log()
        data.loc[1, "Step No"] = float("nan")
        result = self.calculate(data)
        self.assertEqual(result.summary["Step"].tolist(), ["1", "(Unknown)", "2", "1"])
        single = self.calculate(data.iloc[:1])
        self.assertEqual(single.summary.iloc[0]["Duration (s)"], 0)
        self.assertEqual(single.summary.iloc[0]["Duration basis"], "Last sample (partial)")
        self.assertTrue(single.metrics["Std dev"].isna().all())

    def test_empty_or_missing_step_column(self):
        with self.assertRaises(ValueError):
            self.calculate(sample_log().iloc[:0])
        with self.assertRaises(ValueError):
            self.calculate(sample_log().drop(columns="Step No"))

    def test_midnight_and_duplicate_timestamp(self):
        data = sample_log().iloc[:3].copy()
        data["Time"] = ["2026-11-16 23:59:59", "2026-11-16 23:59:59", "2026-11-17 00:00:02"]
        self.assertEqual(self.calculate(data).summary["Duration (s)"].tolist(), [3, 0])

    def test_excel_round_trip_keeps_numbers_and_notes(self):
        data = sample_log()
        data["Step Name"] = "=1+1"
        result = self.calculate(data)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "steps.xlsx"
            export_step_statistics(result, path, "sample.csv")
            workbook = load_workbook(path)
            self.assertEqual(workbook.sheetnames, ["Steps", "Sensors", "Notes"])
            self.assertEqual(workbook["Steps"]["G2"].value, 5)
            self.assertEqual(workbook["Steps"]["D2"].value, "=1+1")
            self.assertEqual(workbook["Steps"]["D2"].data_type, "s")
            self.assertEqual(workbook["Sensors"]["G2"].value, 12)
            self.assertEqual(workbook["Notes"]["A2"].value, "Source: sample.csv")
            workbook.close()


class StepStatisticsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_step_switch_measures_cells_in_a_batch(self):
        data = sample_log()
        for index in range(8):
            data[f"Temperature sensor on channel {index}"] = data["Temperature"]
        result = calculate_step_statistics(data, "Time", "Step No", "Step Name")
        dialog = StepStatisticsDialog(result, Path("sample.csv"))
        try:
            dialog.show()
            self.app.processEvents()
            table = dialog.sensors_table
            delegate = table.itemDelegate()
            with patch.object(delegate, "sizeHint", wraps=delegate.sizeHint) as measure:
                dialog.steps_table.selectRow(2)
                self.app.processEvents()
            cells = table.rowCount() * table.columnCount()
            # Allow an extra layout pass, but not a full scan per replaced cell.
            self.assertLessEqual(measure.call_count, 2 * cells)
            self.assertEqual(table.item(table.rowCount() - 1, 3).text(), "32")
            self.assertGreaterEqual(table.columnWidth(0), table.sizeHintForColumn(0))
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_step_selection_sensor_search_and_export(self):
        result = calculate_step_statistics(sample_log(), "Time", "Step No", "Step Name")
        dialog = StepStatisticsDialog(result, Path("sample.csv"))
        try:
            self.assertEqual(dialog.steps_table.rowCount(), 3)
            self.assertEqual(dialog.sensors_table.rowCount(), 2)
            dialog.steps_table.selectRow(2)
            dialog.sensor_filter.setText("temperature")
            self.assertEqual(dialog.sensors_table.rowCount(), 1)
            self.assertEqual(dialog.sensors_table.item(0, 3).text(), "32")
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "ui_export.xlsx"
                with patch("step_statistics.QFileDialog.getSaveFileName", return_value=(str(path), "")), \
                        patch("step_statistics.QMessageBox.information"):
                    dialog.export_excel()
                exported = pd.read_excel(path, sheet_name="Steps")
                self.assertEqual(len(exported), 3)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_panel_button_load_and_failed_load(self):
        from main import CsvPlotPanel
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            panel = CsvPlotPanel(root)
            try:
                self.assertFalse(panel.btn_step_stats.isEnabled())
                path = root / "sample.csv"
                sample_log().to_csv(path, index=False)
                with patch.object(panel, "plot"):
                    panel.load_csv(path)
                self.assertTrue(panel.btn_step_stats.isEnabled())
                with patch("main.StepStatisticsDialog") as dialog:
                    panel.show_step_statistics()
                    self.assertEqual(len(dialog.call_args.args[0].summary), 3)
                    dialog.return_value.exec.assert_called_once()
                with patch("main.QMessageBox.critical"):
                    panel.load_csv(root / "missing.csv")
                self.assertFalse(panel.btn_step_stats.isEnabled())
                # v2.17 records the selected path before attempting to read it.
                self.assertEqual(panel.csv_path, root / "missing.csv")
            finally:
                panel.close()
                panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
