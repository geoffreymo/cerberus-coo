"""Qt theme (palette + stylesheet) for the Cerberus GUI."""

from PyQt6.QtGui import QColor, QPalette, QFont
from PyQt6.QtWidgets import QApplication

# Accent colours used by widgets (kept here so panels don't hard-code them)
COLOR_OK = "#4caf50"
COLOR_WARN = "#ff9800"
COLOR_ERR = "#f44336"
COLOR_INFO = "#64b5f6"
COLOR_MUTED = "#9e9e9e"
COLOR_START = "#2e7d32"
COLOR_STOP = "#c62828"

_DARK = {
    'window': "#2b2b2b", 'base': "#1f1f1f", 'alt': "#2f2f2f", 'text': "#e6e6e6",
    'button': "#3c3c3c", 'highlight': "#3d7bd9", 'border': "#555555", 'title': "#90caf9",
    'disabled': "#7a7a7a",
}
_LIGHT = {
    'window': "#f3f3f3", 'base': "#ffffff", 'alt': "#f7f7f7", 'text': "#1e1e1e",
    'button': "#e6e6e6", 'highlight': "#3d7bd9", 'border': "#b9b9b9", 'title': "#1a5fb4",
    'disabled': "#9a9a9a",
}


def _stylesheet(c: dict) -> str:
    return f"""
    QGroupBox {{
        font-weight: bold;
        border: 1px solid {c['border']};
        border-radius: 5px;
        margin-top: 12px;
        padding-top: 8px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 8px;
        padding: 0 4px;
        color: {c['title']};
    }}
    QPushButton {{ padding: 4px 10px; min-height: 22px; }}
    QPushButton#startStop {{ font-weight: bold; color: white; }}
    QPushButton#startStop[streaming="false"] {{ background-color: {COLOR_START}; }}
    QPushButton#startStop[streaming="true"] {{ background-color: {COLOR_STOP}; }}
    QPushButton#startStop:disabled {{ background-color: {c['button']}; color: {c['disabled']}; }}
    QLabel#hint {{ color: {COLOR_MUTED}; font-size: 10pt; }}
    QLabel#status_ok {{ color: {COLOR_OK}; font-weight: bold; }}
    QLabel#status_warn {{ color: {COLOR_WARN}; font-weight: bold; }}
    QLabel#status_err {{ color: {COLOR_ERR}; font-weight: bold; }}
    QLabel#status_muted {{ color: {COLOR_MUTED}; }}
    QLabel#statusCell {{
        border: 1px solid {c['border']};
        border-radius: 3px;
        padding: 2px 6px;
    }}
    QCheckBox#saving[active="true"] {{ color: {COLOR_WARN}; font-weight: bold; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; }}
    QPlainTextEdit#log {{ font-family: monospace; font-size: 9pt; }}
    QToolTip {{ color: {c['text']}; background-color: {c['alt']}; border: 1px solid {c['border']}; }}
    """


def apply_theme(app: QApplication, dark: bool = True, base_point_size: int = 11):
    """Apply Fusion style with a dark or light palette and the shared stylesheet."""
    c = _DARK if dark else _LIGHT
    app.setStyle("Fusion")

    pal = QPalette()
    roles = QPalette.ColorRole
    pal.setColor(roles.Window, QColor(c['window']))
    pal.setColor(roles.WindowText, QColor(c['text']))
    pal.setColor(roles.Base, QColor(c['base']))
    pal.setColor(roles.AlternateBase, QColor(c['alt']))
    pal.setColor(roles.ToolTipBase, QColor(c['alt']))
    pal.setColor(roles.ToolTipText, QColor(c['text']))
    pal.setColor(roles.Text, QColor(c['text']))
    pal.setColor(roles.Button, QColor(c['button']))
    pal.setColor(roles.ButtonText, QColor(c['text']))
    pal.setColor(roles.BrightText, QColor("#ff5252"))
    pal.setColor(roles.Link, QColor(c['highlight']))
    pal.setColor(roles.Highlight, QColor(c['highlight']))
    pal.setColor(roles.HighlightedText, QColor("#ffffff"))
    pal.setColor(roles.PlaceholderText, QColor(c['disabled']))
    dis = QPalette.ColorGroup.Disabled
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        pal.setColor(dis, role, QColor(c['disabled']))
    app.setPalette(pal)

    font = app.font()
    font.setPointSize(base_point_size)
    app.setFont(font)

    app.setStyleSheet(_stylesheet(c))
    app.setProperty("cerberus_dark", dark)


def set_status_label(label, text: str, kind: str = "muted"):
    """Set a QLabel's text and colour class (ok/warn/err/muted/info)."""
    label.setText(text)
    label.setObjectName({"ok": "status_ok", "warn": "status_warn", "err": "status_err",
                         "info": "status_ok"}.get(kind, "status_muted"))
    label.style().unpolish(label)
    label.style().polish(label)
