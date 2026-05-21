"""List of hosts with a coloured status dot + IP + duration.

Status dot colours map to ThemeColors so the widget stays consistent
across the Cyber / Dark / Light themes. Click on a row emits
host_selected so the parent panel can swap the transcript view.

Status string vocabulary matches sc2.scng.tools.config_pusher:
    pending / connecting / running / ok / failed / skipped / cancelled / dry-run
"""

from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ...themes import ThemeColors


def _status_color(status: str, theme: ThemeColors) -> str:
    """Map a status string to a theme-aware hex colour for the dot."""
    if status == "ok":
        return theme.accent_success
    if status == "failed":
        return theme.accent_danger
    if status == "connecting":
        return theme.accent_warning
    if status in ("running", "dry-run"):
        return theme.accent_info
    # pending / skipped / cancelled / unknown
    return theme.text_muted


def _status_glyph(status: str) -> str:
    """Single-char marker that's still legible if the dot CSS fails."""
    if status == "ok":
        return "●"          # ●
    if status == "failed":
        return "✕"          # ✕
    if status == "skipped":
        return "○"          # ○
    if status == "connecting":
        return "◐"          # ◐
    if status == "running":
        return "◐"          # ◐
    if status == "dry-run":
        return "⋮"          # ⋮
    return "○"              # ○


class HostRow(QWidget):
    """One row: dot + IP + status text + duration."""

    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self.host = host
        self.status = "pending"
        self.message = ""
        self.duration_s: Optional[float] = None
        self._theme: Optional[ThemeColors] = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        self.dot = QLabel(_status_glyph("pending"))
        self.dot.setFixedWidth(14)
        self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.host_lbl = QLabel(host)
        host_font = QFont()
        host_font.setFamily("Consolas, Courier New, monospace")
        host_font.setBold(True)
        self.host_lbl.setFont(host_font)

        self.status_lbl = QLabel("pending")
        self.duration_lbl = QLabel("")
        self.duration_lbl.setMinimumWidth(48)
        self.duration_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        lay.addWidget(self.dot)
        lay.addWidget(self.host_lbl)
        lay.addStretch(1)
        lay.addWidget(self.status_lbl)
        lay.addWidget(self.duration_lbl)

    def update_state(
        self,
        status: str,
        message: str = "",
        duration_s: Optional[float] = None,
    ) -> None:
        self.status = status
        self.message = message
        if duration_s is not None:
            self.duration_s = duration_s

        self.dot.setText(_status_glyph(status))
        self.status_lbl.setText(status)
        if self.duration_s is not None and self.duration_s > 0:
            self.duration_lbl.setText(f"{self.duration_s:5.1f}s")
        else:
            self.duration_lbl.setText("")
        self._restyle()

    def apply_theme(self, theme: ThemeColors) -> None:
        self._theme = theme
        self._restyle()

    def _restyle(self) -> None:
        if self._theme is None:
            return
        dot_color = _status_color(self.status, self._theme)
        self.dot.setStyleSheet(
            f"color: {dot_color}; font-weight: bold; font-size: 14px;"
        )
        self.host_lbl.setStyleSheet(f"color: {self._theme.text_primary};")
        # Secondary status text in muted colour so the dot does the talking
        self.status_lbl.setStyleSheet(f"color: {self._theme.text_secondary};")
        self.duration_lbl.setStyleSheet(f"color: {self._theme.text_muted};")
        if self.message:
            self.setToolTip(f"{self.status}: {self.message}")
        else:
            self.setToolTip(self.status)


class HostStatusList(QWidget):
    """Vertical list of HostRow items with selection signalling."""

    host_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme: Optional[ThemeColors] = None
        self._rows: Dict[str, HostRow] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._list = QListWidget()
        self._list.setObjectName("hostStatusList")
        self._list.setUniformItemSizes(True)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.currentItemChanged.connect(self._on_selection_changed)

        layout.addWidget(self._list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_hosts(self, hosts) -> None:
        """Replace the displayed hosts. All start in 'pending' state."""
        self._list.clear()
        self._rows.clear()
        for host in hosts:
            self._add_row(host)

    def update_status(
        self,
        host: str,
        status: str,
        message: str = "",
        duration_s: Optional[float] = None,
    ) -> None:
        row = self._rows.get(host)
        if row is None:
            return
        row.update_state(status, message, duration_s)

    def clear(self) -> None:
        self._list.clear()
        self._rows.clear()

    def selected_host(self) -> Optional[str]:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def select_host(self, host: str) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == host:
                self._list.setCurrentItem(item)
                return

    def apply_theme(self, theme: ThemeColors) -> None:
        self._theme = theme
        for row in self._rows.values():
            row.apply_theme(theme)
        self._list.setStyleSheet(
            f"""
            QListWidget#hostStatusList {{
                background-color: {theme.bg_secondary};
                border: 1px solid {theme.border_dim};
                border-radius: 4px;
                outline: none;
            }}
            QListWidget#hostStatusList::item:selected {{
                background-color: {theme.bg_selected};
            }}
            QListWidget#hostStatusList::item:hover {{
                background-color: {theme.bg_hover};
            }}
            """
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _add_row(self, host: str) -> None:
        row = HostRow(host)
        if self._theme is not None:
            row.apply_theme(self._theme)
        row.update_state("pending")

        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, host)
        item.setSizeHint(row.sizeHint())
        self._list.addItem(item)
        self._list.setItemWidget(item, row)
        self._rows[host] = row

    def _on_selection_changed(self, current, previous) -> None:
        if current is None:
            return
        host = current.data(Qt.ItemDataRole.UserRole)
        if host is not None:
            self.host_selected.emit(host)
