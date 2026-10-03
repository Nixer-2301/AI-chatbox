"""界面主题：统一配色与控件样式（深色工业化风格）。"""

from __future__ import annotations

from PyQt6.QtGui import QFont, QFontDatabase

COLORS = {
    "bg": "#16181c",
    "panel": "#1e2126",
    "panel_alt": "#24282e",
    "border": "#33383f",
    "text": "#d7dbe0",
    "text_dim": "#8b939d",
    "accent": "#4c8bf5",
    "accent_dim": "#2f5ea8",
    "ok": "#39c07f",
    "warn": "#e0b341",
    "error": "#e05561",
    "user": "#6fb3ff",
    "assistant": "#39c07f",
    "reasoning": "#a08fe0",
    "system": "#e0b341",
}

STYLESHEET = """
QWidget {
    background-color: %(bg)s;
    color: %(text)s;
    font-size: 13px;
}
QMainWindow::separator {
    background: %(border)s;
    width: 4px; height: 4px;
}
QMenuBar, QMenu {
    background-color: %(panel)s;
    border: 1px solid %(border)s;
}
QMenuBar::item:selected, QMenu::item:selected {
    background-color: %(accent_dim)s;
}
QToolBar {
    background: %(panel)s;
    border-bottom: 1px solid %(border)s;
    spacing: 4px;
    padding: 4px;
}
QStatusBar {
    background: %(panel)s;
    border-top: 1px solid %(border)s;
    color: %(text_dim)s;
}
QDockWidget {
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    font-weight: 600;
}
QDockWidget::title {
    background: %(panel_alt)s;
    padding: 5px 8px;
    border-bottom: 1px solid %(border)s;
}
QGroupBox {
    border: 1px solid %(border)s;
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 8px;
    background: %(panel)s;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 9px;
    padding: 0 5px;
    color: %(text_dim)s;
    font-weight: 600;
}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: %(panel_alt)s;
    border: 1px solid %(border)s;
    border-radius: 3px;
    padding: 3px 6px;
    selection-background-color: %(accent_dim)s;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QComboBox:focus {
    border: 1px solid %(accent)s;
}
QLineEdit[invalid="true"] {
    border: 1px solid %(error)s;
}
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: %(panel_alt)s;
    border: 1px solid %(border)s;
    selection-background-color: %(accent_dim)s;
}
QPushButton {
    background: %(panel_alt)s;
    border: 1px solid %(border)s;
    border-radius: 3px;
    padding: 5px 12px;
}
QPushButton:hover { border-color: %(accent)s; }
QPushButton:pressed { background: %(accent_dim)s; }
QPushButton:disabled { color: %(text_dim)s; border-color: %(border)s; }
QPushButton[accent="true"] {
    background: %(accent_dim)s;
    border-color: %(accent)s;
    font-weight: 600;
}
QPushButton[danger="true"]:hover { border-color: %(error)s; }
QToolButton[section="true"] {
    background: %(panel_alt)s;
    border: 1px solid %(border)s;
    border-radius: 4px;
    padding: 6px 10px;
    text-align: left;
    font-weight: 600;
    color: %(text)s;
}
QToolButton[section="true"]:hover { border-color: %(accent)s; }
QToolButton[section="true"]:checked { background: %(panel)s; }
QTableWidget, QTableView, QTreeWidget, QListWidget {
    background: %(panel)s;
    alternate-background-color: %(panel_alt)s;
    border: 1px solid %(border)s;
    gridline-color: %(border)s;
    selection-background-color: %(accent_dim)s;
}
QHeaderView::section {
    background: %(panel_alt)s;
    color: %(text_dim)s;
    border: none;
    border-right: 1px solid %(border)s;
    border-bottom: 1px solid %(border)s;
    padding: 4px 6px;
    font-weight: 600;
}
QTabWidget::pane { border: 1px solid %(border)s; background: %(panel)s; }
QTabBar::tab {
    background: %(bg)s;
    color: %(text_dim)s;
    border: 1px solid %(border)s;
    border-bottom: none;
    padding: 5px 12px;
    margin-right: 1px;
}
QTabBar::tab:selected {
    background: %(panel)s;
    color: %(text)s;
    border-top: 2px solid %(accent)s;
}
QScrollBar:vertical { background: %(bg)s; width: 11px; margin: 0; }
QScrollBar::handle:vertical {
    background: %(border)s; border-radius: 5px; min-height: 26px;
}
QScrollBar::handle:vertical:hover { background: %(accent_dim)s; }
QScrollBar:horizontal { background: %(bg)s; height: 11px; margin: 0; }
QScrollBar::handle:horizontal {
    background: %(border)s; border-radius: 5px; min-width: 26px;
}
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QSplitter::handle { background: %(border)s; }
QCheckBox { spacing: 6px; }
QCheckBox::indicator {
    width: 13px; height: 13px;
    border: 1px solid %(border)s;
    border-radius: 2px;
    background: %(panel_alt)s;
}
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }
QLabel[hint="true"] { color: %(text_dim)s; }
QLabel[mono="true"] { font-family: "Consolas", "Cascadia Mono", monospace; }
QLabel[warn="true"] { color: %(warn)s; }
QLabel[error="true"] { color: %(error)s; }
QLabel[ok="true"] { color: %(ok)s; }
"""


def apply_theme(app) -> None:
    families = set(QFontDatabase.families())
    for candidate in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
        if candidate in families:
            font = QFont(candidate, 9)
            break
    else:
        font = QFont()
    app.setFont(font)
    app.setStyleSheet(STYLESHEET % COLORS)


def mono_font(size: int = 9) -> QFont:
    families = set(QFontDatabase.families())
    for candidate in ("Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New"):
        if candidate in families:
            return QFont(candidate, size)
    return QFont("monospace", size)


def colored(text: str, key: str) -> str:
    return f'<span style="color:{COLORS.get(key, COLORS["text"])}">{text}</span>'