import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
from time import monotonic
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from main import HistoryDialog, scan_history_logs


class RunHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write_log(self, folder, filename):
        directory = self.root / folder
        directory.mkdir(exist_ok=True)
        (directory / filename).write_text(
            "Job ID,tube,Recipe Name\nJ1,1,Recipe_A\n", encoding="utf-8")

    def wait_until(self, predicate):
        deadline = monotonic() + 8
        while not predicate() and monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_folder_names_in_groups_table_and_abort_filter(self):
        today = QDate.currentDate()
        day = today.toString("yyyyMMdd")
        for tube in ("TUBE01", "RPCVD1-1", "ARCVD8-3", "ARCVD8-1"):
            self.write_log(tube, f"Recipe_A_{day}_1200.csv")
        self.write_log("ARCVD8-3", f"Recipe_A_{day}_120100_AB.csv")
        self.write_log("RPCVD1-1", "Old_20000101_1000.csv")
        dialog = HistoryDialog(self.root)
        self.addCleanup(dialog.close)
        dialog.show()
        self.wait_until(lambda: dialog._scan is None)
        counts = {"TUBE01": 1, "RPCVD1-1": 1, "ARCVD8-3": 2, "ARCVD8-1": 1}
        self.assertEqual(dialog._make_pie_data(), ("tube", counts))
        dialog.group_by.setCurrentText("tube")
        self.assertEqual(dialog._make_pivot()[2], {k: [v] for k, v in counts.items()})
        self.assertEqual(dialog.raw_model.rowCount(), 5)
        dialog.abort_only_cb.setChecked(True)
        self.assertEqual(dialog.raw_model.rowCount(), 1)
        self.assertEqual(dialog.raw_model.index(0, 2).data(Qt.DisplayRole), "ARCVD8-3")
        self.assertEqual(dialog.filtered_logs()["tube"].tolist(), ["ARCVD8-3"])
        self.assertTrue(dialog.btn_export.isEnabled())

    def test_slow_scan_can_be_replaced_and_closed(self):
        entered, release, finished = Event(), Event(), Event()
        tokens = []
        worker_ids = []

        def slow_scan(directory, start, end, *, cancelled):
            tokens.append(cancelled)
            worker_ids.append(get_ident())
            entered.set()
            try:
                release.wait(8)
                return pd.DataFrame()
            finally:
                finished.set()

        with patch("main.scan_history_logs", side_effect=slow_scan):
            dialog = HistoryDialog(self.root)
            self.addCleanup(dialog.close)
            dialog.show()
            try:
                self.wait_until(entered.is_set)
                self.assertNotEqual(worker_ids[0], get_ident())
                self.assertFalse(dialog.btn_export.isEnabled())
                dialog.date_from.setDate(QDate(2000, 1, 1))
                dialog.reload_data()
                self.assertTrue(tokens[0].is_set())
                current = dialog._scan
                dialog._scan_completed(tokens[0], pd.DataFrame(), "")
                self.assertIs(dialog._scan, current)
                dialog.close()
                self.assertFalse(dialog.isVisible())
                self.assertTrue(current.cancelled.is_set())
            finally:
                release.set()
                self.wait_until(finished.is_set)

    def test_out_of_range_files_are_skipped_before_reading(self):
        self.write_log("RPCVD1-1", "Old_20261124_0800.csv")
        self.write_log("RPCVD1-1", "New_20261125_0800.csv")
        with patch("main.extract_history_meta_from_first_row", return_value={}) as read:
            runs = scan_history_logs(self.root, pd.Timestamp("2026-11-25"), pd.Timestamp("2026-11-25"))
        self.assertEqual(runs["recipe"].tolist(), ["New"])
        read.assert_called_once_with(self.root / "RPCVD1-1" / "New_20261125_0800.csv")
