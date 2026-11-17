import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication, QDialog
from main import AppSettingsDialog, DEFAULT_PROC_LOG_DIR, MainWindow


class ProcLogSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.IniFormat)
        self.settings_patch = patch("main.QSettings", return_value=self.settings)
        self.settings_patch.start()
        self.menu_patch = patch.object(MainWindow, "_install_system_menu", lambda self: None)
        self.menu_patch.start()
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.menu_patch.stop()
        self.settings_patch.stop()
        self.temp.cleanup()

    def window(self, **kwargs):
        window = MainWindow(self.root, self.root, self.root, **kwargs)
        self.windows.append(window)
        return window

    def test_existing_root_setting_does_not_become_gas_source(self):
        recipes = self.root / "recipes"
        recipes.mkdir()
        self.settings.setValue("paths/root_dir", str(recipes))
        window = self.window()
        self.assertEqual(window.root_dir, recipes.resolve())
        self.assertEqual(window.proc_log_dir, Path(DEFAULT_PROC_LOG_DIR).resolve())
        self.assertEqual(window.plot_panel.proc_log_dir, window.proc_log_dir)
        self.assertEqual(Path(window.model.rootPath()), recipes.resolve())

    def test_settings_change_reaches_dashboard_and_survives_restart_and_cancel(self):
        original = self.root / "original"
        original.mkdir()
        target = self.root / "temporarily_unavailable"
        window = self.window(proc_log_dir=original)

        def accept_new_path(dialog):
            self.assertEqual(Path(dialog.proc_log_edit.text()), original.resolve())
            dialog.proc_log_edit.setText(str(target))
            dialog._on_accept()
            return dialog.result()

        with patch.object(AppSettingsDialog, "exec", accept_new_path):
            window.open_settings_dialog()
        self.assertEqual(window.proc_log_dir, target.resolve())
        self.assertEqual(window.root_dir, self.root.resolve())
        self.assertEqual(window.plot_panel.proc_log_dir, target.resolve())
        self.assertEqual(Path(self.settings.value("paths/proc_log_dir")), target.resolve())
        with patch("main.GasUsageDialog") as dashboard, patch("main.apply_chrome_input_styles"):
            window.plot_panel.show_gas_usage()
            dashboard.assert_called_once_with(target.resolve(), window.plot_panel)
        restored = self.window()
        self.assertEqual(restored.proc_log_dir, target.resolve())
        self.assertEqual(restored.plot_panel.proc_log_dir, target.resolve())

        def cancel_edit(dialog):
            dialog.proc_log_edit.setText(str(original))
            return QDialog.Rejected

        with patch.object(AppSettingsDialog, "exec", cancel_edit):
            restored.open_settings_dialog()
        self.assertEqual(restored.proc_log_dir, target.resolve())
        self.assertEqual(Path(self.settings.value("paths/proc_log_dir")), target.resolve())

    def test_settings_reject_empty_or_file_instead_of_folder(self):
        file = self.root / "a.csv"
        file.write_text("Time,value\n", encoding="utf-8")
        for value in ("", str(file)):
            with self.subTest(value=value):
                dialog = AppSettingsDialog(str(self.root), proc_log_dir=value)
                with patch("main.QMessageBox.warning") as warning:
                    dialog._on_accept()
                    warning.assert_called_once()
                self.assertEqual(dialog.result(), QDialog.Rejected)
                dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
