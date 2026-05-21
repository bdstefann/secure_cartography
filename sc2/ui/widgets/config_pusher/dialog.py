"""ConfigPusherDialog — container with Push / History / Templates tabs.

Owns the ConfigHistoryDB instance and wires the three panels together:

  - PushPanel.push_finished       -> HistoryPanel.refresh()
  - PushPanel.save_template_requested(block)
                                  -> jump to Templates tab + open dialog
  - HistoryPanel.reuse_requested(hosts, commands)
                                  -> PushPanel.set_initial_*() + Push tab
  - TemplatesPanel.use_requested(block)
                                  -> PushPanel.set_initial_block + Push tab
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QDialog, QScrollArea, QTabWidget, QVBoxLayout, QWidget

from sc2.scng.tools.history_db import ConfigHistoryDB

from .history_panel import HistoryPanel
from .push_panel import PushPanel
from .templates_panel import TemplatesPanel
from ...themes import ThemeColors


class ConfigPusherDialog(QDialog):
    """Non-modal Config Push window."""

    def __init__(
        self,
        vault,
        theme_manager,
        history_db: Optional[ConfigHistoryDB] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Config Push  (F11 fullscreen, Esc exits)")
        # Add minimize + maximize + close to the title bar (QDialog has only
        # close by default on Windows).
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        # Smaller default so it fits a 1366x768 laptop screen with room to
        # spare; user can maximize / fullscreen if they want more.
        self.resize(1100, 680)
        self.setMinimumSize(720, 480)

        self.vault = vault
        self.theme_manager = theme_manager
        self.history_db = history_db or ConfigHistoryDB()

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("configPusherTabs")

        self.push_panel = PushPanel(vault, theme_manager, self.history_db)
        self.history_panel = HistoryPanel(self.history_db, theme_manager)
        self.templates_panel = TemplatesPanel(self.history_db, theme_manager)

        # Each tab gets its own scroll area so the inner widget can be larger
        # than the visible viewport (laptop screens / restored window sizes).
        self.tabs.addTab(_in_scroll(self.push_panel), "Push")
        self.tabs.addTab(_in_scroll(self.history_panel), "History")
        self.tabs.addTab(_in_scroll(self.templates_panel), "Templates")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

        # F11 toggles fullscreen; Esc leaves fullscreen.
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("Escape"), self, activated=self._exit_fullscreen)

        # Wire cross-panel signals
        self.push_panel.push_finished.connect(lambda _id: self.history_panel.refresh())
        self.push_panel.save_template_requested.connect(self._on_save_template_requested)
        self.history_panel.reuse_requested.connect(self._on_reuse_run)
        self.templates_panel.use_requested.connect(self._on_use_template)

        if theme_manager is not None:
            self.apply_theme(theme_manager.theme)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def apply_theme(self, theme: ThemeColors) -> None:
        self.push_panel.apply_theme(theme)
        self.history_panel.apply_theme(theme)
        self.templates_panel.apply_theme(theme)
        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {theme.bg_primary};
                color: {theme.text_primary};
            }}
            QTabWidget#configPusherTabs::pane {{
                border: 1px solid {theme.border_dim};
                background-color: {theme.bg_secondary};
            }}
            QTabBar::tab {{
                background-color: {theme.bg_tertiary};
                color: {theme.text_secondary};
                padding: 8px 16px;
                border: 1px solid {theme.border_dim};
                border-bottom: none;
            }}
            QTabBar::tab:selected {{
                background-color: {theme.bg_secondary};
                color: {theme.text_primary};
                font-weight: bold;
            }}
            QPushButton {{
                background-color: {theme.bg_tertiary};
                color: {theme.text_primary};
                border: 1px solid {theme.border_dim};
                padding: 6px 12px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: {theme.bg_hover};
            }}
            """
        )

    # ------------------------------------------------------------------
    # Cross-panel wiring
    # ------------------------------------------------------------------

    def _on_save_template_requested(self, block: str) -> None:
        self.tabs.setCurrentIndex(2)
        self.templates_panel.open_save_dialog_with_block(block)

    def _on_reuse_run(self, hosts, commands) -> None:
        self.push_panel.set_initial_hosts(hosts)
        self.push_panel.set_initial_block(commands)
        self.tabs.setCurrentIndex(0)

    def _on_use_template(self, block: str) -> None:
        commands = [
            line for line in block.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.push_panel.set_initial_block(commands)
        self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # Fullscreen
    # ------------------------------------------------------------------

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _exit_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _in_scroll(widget: QWidget) -> QScrollArea:
    """Wrap `widget` in a frameless QScrollArea so the dialog scrolls
    instead of clipping content when the window is smaller than the panel."""
    scroll = QScrollArea()
    scroll.setObjectName("configPusherScroll")
    scroll.setWidget(widget)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    return scroll
