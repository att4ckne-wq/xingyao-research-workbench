"""Paper design tokens: mist violet, night violet and native fallback."""
import os
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase

# Retain the persisted key so existing preferences and integrations stay compatible.
THEME_SCIENTIFIC = "科研蓝"
THEME_DARK = "夜航紫"
THEME_CLASSIC = "默认经典"
NAMES = {THEME_SCIENTIFIC:"雾紫纸页", THEME_DARK:"夜航紫", THEME_CLASSIC:"默认经典"}
LIGHT = dict(bg="#F7F5FA", surface="#FFFFFF", subtle="#EFEBF5", ink="#292535", muted="#716A7D", line="#DDD7E7", accent="#665A9C", hover="#584B8C", selected="#E6DFF5", white="#FFFFFF")
DARK = dict(bg="#1E1B29", surface="#292534", subtle="#343042", ink="#F3EEF9", muted="#B6ADC5", line="#50475F", accent="#BCA9ED", hover="#D2C1FC", selected="#4D406A", white="#211932")

def palette():
    app = QApplication.instance()
    return DARK if app and app.property("paperTheme") == THEME_DARK else LIGHT

def theme_label(name):
    return NAMES.get(name,name)

def stylesheet(p):
    return """
QMainWindow, QDialog { background: %(bg)s; }
QWidget { font-family: "Microsoft YaHei"; font-size: 13px; color: %(ink)s; }
QToolBar { background: %(surface)s; border: none; border-bottom: 1px solid %(line)s; spacing: 5px; padding: 7px; }
QToolBar QToolButton { background: %(surface)s; border: 1px solid transparent; border-radius: 7px; padding: 8px 11px; color: %(ink)s; }
QToolBar QToolButton:hover { background: %(subtle)s; border-color: %(line)s; }
QToolBar QToolButton[primary="true"], QPushButton[primary="true"] { background: %(accent)s; color: %(white)s; border-color: %(accent)s; font-weight: 600; }
QLabel#brandTitle { color: %(accent)s; font-size: 21px; font-weight: 700; padding: 3px 0; }
QLabel#pageTitle { color: %(ink)s; font-size: 20px; font-weight: 700; }
QLabel#muted, QLabel#helper { color: %(muted)s; }
QLabel#sectionTitle { color: %(accent)s; font-weight: 700; padding-top: 8px; }
QWidget#sidebar, QWidget#card { background: %(surface)s; border: 1px solid %(line)s; border-radius: 12px; }
QWidget#recordBody { background: %(bg)s; }
QTabWidget::pane { background: %(surface)s; border: 1px solid %(line)s; border-radius: 10px; top: -1px; }
QTabBar::tab { background: %(bg)s; color: %(muted)s; padding: 11px 20px; border: none; border-bottom: 3px solid transparent; }
QTabBar::tab:selected { color: %(accent)s; border-bottom-color: %(accent)s; font-weight: 700; }
QTabBar::tab:hover { background: %(subtle)s; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateTimeEdit, QSpinBox { background: %(surface)s; color: %(ink)s; border: 1px solid %(line)s; border-radius: 7px; padding: 7px; selection-background-color: %(selected)s; selection-color: %(ink)s; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateTimeEdit:focus { border: 2px solid %(accent)s; padding: 6px; }
QPushButton { background: %(surface)s; color: %(ink)s; border: 1px solid %(line)s; border-radius: 7px; padding: 8px 13px; min-height: 18px; }
QPushButton:hover { background: %(subtle)s; border-color: %(accent)s; }
QPushButton:pressed { background: %(selected)s; }
QPushButton:focus, QToolButton:focus { border: 2px solid %(accent)s; }
QPushButton:disabled, QToolButton:disabled { color: %(muted)s; background: %(subtle)s; }
QPushButton[danger="true"] { color: #BD555F; }
QListWidget, QTreeWidget { background: %(surface)s; alternate-background-color: %(bg)s; color: %(ink)s; border: none; border-radius: 8px; outline: none; }
QListWidget::item { padding: 9px 8px; border-left: 3px solid transparent; }
QTreeWidget::item { padding: 7px 4px; }
QListWidget::item:selected { border-left-color: %(accent)s; background: %(selected)s; color: %(ink)s; }
QTreeWidget::item:selected { background: %(selected)s; color: %(ink)s; }
QListWidget::item:hover, QTreeWidget::item:hover { background: %(subtle)s; }
QHeaderView::section { background: %(subtle)s; color: %(muted)s; border: none; padding: 10px 8px; font-weight: 600; }
QStatusBar { background: %(surface)s; color: %(muted)s; border-top: 1px solid %(line)s; padding: 4px; }
QStatusBar::item { border: none; }
QSplitter::handle { background: %(bg)s; width: 10px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: %(bg)s; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: %(line)s; min-height: 28px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QComboBox QAbstractItemView { background: %(surface)s; color: %(ink)s; selection-background-color: %(selected)s; selection-color: %(ink)s; }
QMenu { background: %(surface)s; color: %(ink)s; border: 1px solid %(line)s; padding: 5px; }
QMenu::item { padding: 7px 18px; border-radius: 4px; }
QMenu::item:selected { background: %(selected)s; color: %(ink)s; }
QMenu::item:disabled { color: %(muted)s; }
QCalendarWidget { background: %(surface)s; border: 1px solid %(line)s; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: %(accent)s; }
QCalendarWidget QToolButton { background: %(accent)s; color: %(white)s; border: none; padding: 5px; }
QCalendarWidget QToolButton:hover { background: %(hover)s; }
QCalendarWidget QAbstractItemView { background: %(surface)s; color: %(ink)s; alternate-background-color: %(subtle)s; selection-background-color: %(accent)s; selection-color: %(white)s; outline: none; border: none; }
QCalendarWidget QAbstractItemView::item { color: %(ink)s; }
QCalendarWidget QAbstractItemView::item:selected { background: %(accent)s; color: %(white)s; }
QCalendarWidget QAbstractItemView:disabled { color: %(muted)s; }
QToolBar#mainToolbar { padding: 7px; min-height: 26px; }
QToolBar#mainToolbar QToolButton { padding: 2px 11px; }
QListWidget#projectList::item { padding: 3px 8px; }
QPushButton[compact="true"] { padding: 3px 8px; min-height: 18px; }
QLabel#validationError { color: #BD555F; }
QToolTip { background: %(ink)s; color: %(surface)s; border: none; padding: 7px; }
QCheckBox { spacing: 7px; padding: 3px; }
QProgressBar { background: %(subtle)s; border: none; border-radius: 4px; height: 6px; }
QProgressBar::chunk { background: %(accent)s; border-radius: 4px; }
""" % p

SCIENTIFIC_QSS = stylesheet(LIGHT)

def apply_theme(name):
    app = QApplication.instance()
    if app is None: return
    if not app.property("paperFontsLoaded"):
        # Register existing system fonts for this process only, including offscreen Qt.
        directory = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            path = directory / filename
            if path.is_file(): QFontDatabase.addApplicationFont(str(path))
        app.setFont(QFont("Microsoft YaHei", 10))
        app.setProperty("paperFontsLoaded", True)
    app.setProperty("paperTheme", name)
    app.setStyleSheet("" if name == THEME_CLASSIC or name not in NAMES else stylesheet(DARK if name == THEME_DARK else LIGHT))

def available_themes():
    return (THEME_SCIENTIFIC, THEME_DARK, THEME_CLASSIC)
