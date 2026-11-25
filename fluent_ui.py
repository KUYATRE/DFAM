"""QFluentWidgets presentation layer; accounting and log parsing stay independent."""

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QLabel
from qfluentwidgets import (
    ComboBox, DateEdit, DateTimeEdit, DoubleSpinBox, PushButton, PrimaryPushButton,
    LineEdit, CheckBox, TableWidget, TreeView, ListWidget, ProgressBar,
    SegmentedWidget, CardWidget, TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel,
    CalendarPicker, ScrollArea, FluentStyleSheet, setTheme, setThemeColor, setFontFamilies, Theme,
)

APP_VERSION = "3.11"


def configure_fluent(app):
    app.setStyle("Fusion")
    setFontFamilies(["Segoe UI", "Malgun Gothic"])
    setTheme(Theme.LIGHT, save=False)
    setThemeColor("#0067c0", save=False)
    font = QFont()
    font.setFamilies(["Segoe UI", "Malgun Gothic"])
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(app_stylesheet())


def app_stylesheet():
    # Keep selectors narrow so the library's native painting and animations work.
    return """
        QDialog { background: #f9f9f9; }
        QLabel { background: transparent; color: #202020; }
        QGroupBox { background: white; border: 1px solid #e5e5e5;
                    border-radius: 8px; margin-top: 14px; padding: 16px 12px 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; color: #606060; }
        QSplitter::handle { background: transparent; width: 8px; height: 8px; }
        QMenu { background: #fafafa; color: #202020; border: 1px solid #dedede;
                border-radius: 8px; padding: 6px; }
        QMenu::item { padding: 8px 24px; border-radius: 4px; }
        QMenu::item:selected { background: #e9eef5; }
        QDialogButtonBox QPushButton { background: white; color: #202020;
            border: 1px solid #d4d4d4; border-radius: 4px; padding: 7px 22px; }
        QDialogButtonBox QPushButton:default { background: #0067c0; color: white; }
        QScrollArea { border: none; background: transparent; }
    """


class FluentComboBox(ComboBox):
    """Keep Qt's text/userData argument order used by the existing dialogs."""
    def addItem(self, text, userData=None):
        super().addItem(str(text), userData=userData)


class FluentLineEdit(LineEdit):
    def __init__(self, text=None, parent=None):
        super().__init__(parent if isinstance(text, str) or text is None else text)
        if isinstance(text, str):
            self.setText(text)


class FluentDateEdit(QWidget):
    dateChanged = Signal(QDate)

    def __init__(self, date=None, parent=None):
        super().__init__(parent if isinstance(date, QDate) or date is None else date)
        self.picker = CalendarPicker(self)
        self.picker.setDate(date if isinstance(date, QDate) else QDate.currentDate())
        self.picker.dateChanged.connect(self.dateChanged.emit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.picker)
        self.setFixedHeight(33)

    def date(self):
        return self.picker.getDate()

    def setDate(self, date):
        if date != self.date():
            self.picker.setDate(date)

    def setDisplayFormat(self, format):
        self.picker.setDateFormat(format)

    def setCalendarPopup(self, enabled):
        # CalendarPicker always uses the Fluent calendar popup.
        pass


class FluentTabs(QWidget):
    """Segmented navigation with the small QTabWidget API used by the gas view."""
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pivot = SegmentedWidget(self)
        self.stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self.pivot, 0, Qt.AlignLeft)
        layout.addWidget(self.stack, 1)
        self.stack.currentChanged.connect(self._changed)

    def addTab(self, widget, text):
        index = self.stack.addWidget(widget)
        self.pivot.addItem(str(index), text, onClick=lambda: self.setCurrentIndex(index))
        if index == self.currentIndex():
            self.pivot.setCurrentItem(str(index))
        return index

    def _changed(self, index):
        if str(index) in self.pivot.items:
            self.pivot.setCurrentItem(str(index))
        self.currentChanged.emit(index)

    def setCurrentIndex(self, index):
        self.stack.setCurrentIndex(index)

    def currentIndex(self):
        return self.stack.currentIndex()

    def currentWidget(self):
        return self.stack.currentWidget()

    def count(self):
        return self.stack.count()

    def widget(self, index):
        return self.stack.widget(index)

    def setTabText(self, index, text):
        self.pivot.setItemText(str(index), text)

    def tabText(self, index):
        return self.pivot.items[str(index)].text()


def polish_inputs(root):
    """Replace local legacy input CSS without changing any signal connections."""
    for kind, style in ((PushButton, FluentStyleSheet.BUTTON), (LineEdit, FluentStyleSheet.LINE_EDIT),
                        (ComboBox, FluentStyleSheet.COMBO_BOX), (DateEdit, FluentStyleSheet.SPIN_BOX),
                        (DateTimeEdit, FluentStyleSheet.SPIN_BOX), (DoubleSpinBox, FluentStyleSheet.SPIN_BOX),
                        (CheckBox, FluentStyleSheet.CHECK_BOX), (TableWidget, FluentStyleSheet.TABLE_VIEW),
                        (TreeView, FluentStyleSheet.TREE_VIEW), (ListWidget, FluentStyleSheet.LIST_VIEW)):
        for widget in root.findChildren(kind):
            if widget.property("routeKey") is not None:
                continue  # Pivot/segmented buttons own their selected-state style.
            style.apply(widget)


class SettingsPage(ScrollArea):
    def __init__(self, edit_paths, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.content = QWidget()
        self.setWidget(self.content)
        self.setWidgetResizable(True)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(TitleLabel("Settings", self))
        layout.addWidget(BodyLabel("Equipment log paths and application information", self))
        self.path_labels = {}
        for key, title, description in (
            ("root_dir", "Process Logs", "RecipeProcLog files for plotting and comparing runs"),
            ("proc_log_dir", "Gas Usage Logs", "ProcLog files for calculating monthly N2 and O2 usage"),
            ("alarm_dir", "Alarm Logs", "Alarms recorded during process runs"),
            ("history_dir", "Run History", "Run history and statistics by date"),
        ):
            card = CardWidget(self)
            row = QVBoxLayout(card)
            row.setContentsMargins(20, 14, 20, 14)
            row.addWidget(SubtitleLabel(title, card))
            row.addWidget(CaptionLabel(description, card))
            value = BodyLabel("", card)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setWordWrap(True)
            row.addWidget(value)
            self.path_labels[key] = value
            layout.addWidget(card)
        self.edit_button = PrimaryPushButton("Change Log Paths", self)
        self.edit_button.clicked.connect(edit_paths)
        layout.addWidget(self.edit_button, 0, Qt.AlignLeft)
        layout.addStretch()
        layout.addWidget(CaptionLabel(f"Log Plotter {APP_VERSION}  ·  Windows 11 Fluent UI", self))

    def refresh_paths(self, window):
        for key, label in self.path_labels.items():
            label.setText(str(getattr(window, key)))
