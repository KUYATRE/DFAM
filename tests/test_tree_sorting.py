import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDir, Qt
from PySide6.QtWidgets import QApplication, QFileSystemModel

from main import TreeFilterProxyModel


class DisplayDateModel(QFileSystemModel):
    """Simulate an equipment PC displaying unpadded day/month/year dates."""

    def data(self, index, role=Qt.DisplayRole):
        if index.column() == 3 and role == Qt.DisplayRole:
            return self.fileInfo(index).lastModified().toString("d/M/yyyy H:mm:ss")
        return super().data(index, role)


class TreeSortingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        # Names intentionally disagree with chronological order.
        dates = {
            "log10.csv": datetime(2025, 12, 31, 12),
            "log21.csv": datetime(2026, 1, 1, 12),
            "log11.csv": datetime(2026, 1, 2, 12),
            "log2.csv": datetime(2026, 1, 10, 12),
            "log20.csv": datetime(2026, 1, 11, 12),
            "log1.csv": datetime(2026, 2, 1, 12),
        }
        for name, date in dates.items():
            path = self.root / name
            path.write_text("time,value\n", encoding="utf-8")
            os.utime(path, (date.timestamp(), date.timestamp()))
        self.chronological = list(dates)
        self.model = DisplayDateModel()
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.model.setRootPath(str(self.root))
        self.proxy = TreeFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.source_root = self.model.index(str(self.root))
        deadline = time.monotonic() + 5
        while self.model.rowCount(self.source_root) != len(dates):
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("File system model did not finish loading")
            time.sleep(0.01)

    def tearDown(self):
        self.proxy.setSourceModel(None)
        self.model.deleteLater()
        self.proxy.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def names(self, column, order):
        self.proxy.sort(column, order)
        root = self.proxy.mapFromSource(self.source_root)
        return [self.proxy.index(row, 0, root).data()
                for row in range(self.proxy.rowCount(root))]

    def test_date_sort_uses_timestamps_across_days_months_and_years(self):
        self.assertEqual(self.names(3, Qt.AscendingOrder), self.chronological)
        self.assertEqual(self.names(3, Qt.DescendingOrder), self.chronological[::-1])

    def test_filename_sort_uses_numeric_parts(self):
        expected = [f"log{n}.csv" for n in (1, 2, 10, 11, 20, 21)]
        self.assertEqual(self.names(0, Qt.AscendingOrder), expected)
        self.assertEqual(self.names(0, Qt.DescendingOrder), expected[::-1])

    def test_search_keeps_date_order_and_source_mapping(self):
        self.proxy.setFilterText("log1")
        self.assertEqual(self.names(3, Qt.AscendingOrder),
                         ["log10.csv", "log11.csv", "log1.csv"])
        root = self.proxy.mapFromSource(self.source_root)
        index = self.proxy.index(0, 0, root)
        source = self.proxy.mapToSource(index)
        self.assertEqual(Path(self.model.filePath(source)), self.root / "log10.csv")
        self.proxy.setFilterText("")
        self.assertEqual(self.names(3, Qt.AscendingOrder), self.chronological)


if __name__ == "__main__":
    unittest.main()
