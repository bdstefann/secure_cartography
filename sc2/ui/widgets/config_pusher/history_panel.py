"""History tab — list of past Config Push runs with reuse / inspect actions."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sc2.scng.tools.history_db import ConfigHistoryDB, HistoryEntry

from ...themes import ThemeColors


_COLS = ["When", "Label", "Hosts", "ok", "fail", "skip", "Duration"]


class HistoryPanel(QWidget):
    """Browse / re-use past Config Push runs."""

    reuse_requested = pyqtSignal(list, list)   # hosts, commands (as lists)

    def __init__(self, history_db: ConfigHistoryDB, theme_manager=None, parent=None):
        super().__init__(parent)
        self.history_db = history_db
        self.theme_manager = theme_manager
        self._theme: Optional[ThemeColors] = None
        self._entries: list[HistoryEntry] = []

        self._setup_ui()
        if theme_manager is not None:
            self.apply_theme(theme_manager.theme)
        self.refresh()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search label or command block…")
        self.search_edit.textChanged.connect(self._on_search_changed)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.refresh_btn)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setObjectName("historyTable")
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        self.table.itemDoubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.reuse_btn = QPushButton("Re-use selected run →")
        self.reuse_btn.clicked.connect(self._on_reuse_clicked)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        bottom.addStretch(1)
        bottom.addWidget(self.delete_btn)
        bottom.addWidget(self.reuse_btn)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def apply_theme(self, theme: ThemeColors) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"""
            QTableWidget#historyTable {{
                background-color: {theme.bg_secondary};
                color: {theme.text_primary};
                gridline-color: {theme.border_dim};
                border: 1px solid {theme.border_dim};
                alternate-background-color: {theme.bg_tertiary};
            }}
            QTableWidget#historyTable::item:selected {{
                background-color: {theme.bg_selected};
                color: {theme.text_primary};
            }}
            QHeaderView::section {{
                background-color: {theme.bg_tertiary};
                color: {theme.text_secondary};
                padding: 4px;
                border: 1px solid {theme.border_dim};
            }}
            QLineEdit {{
                background-color: {theme.bg_input};
                color: {theme.text_primary};
                border: 1px solid {theme.border_dim};
                border-radius: 4px;
                padding: 4px;
            }}
            """
        )

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        query = self.search_edit.text().strip()
        if query:
            self._entries = self.history_db.search_runs(query, limit=50)
        else:
            self._entries = self.history_db.list_runs(limit=50)
        self._reload_table()

    def _reload_table(self) -> None:
        self.table.setRowCount(len(self._entries))
        mono = QFont("Consolas")
        for row, entry in enumerate(self._entries):
            cells = [
                entry.timestamp[:19].replace("T", " "),
                entry.label or _first_line(entry.command_block),
                str(len(entry.hosts)),
                str(entry.ok_count),
                str(entry.fail_count),
                str(entry.skip_count),
                f"{entry.duration_s:.1f}s",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (2, 3, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 1:
                    item.setFont(mono)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        if self.table.horizontalHeader().sectionSize(1) > 50:
            self.table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Stretch
            )

    def _on_search_changed(self, _: str) -> None:
        self.refresh()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _selected_entry(self) -> Optional[HistoryEntry]:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._entries):
            return None
        return self._entries[row]

    def _on_reuse_clicked(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "Re-use", "Pick a row first.")
            return
        commands = [
            line for line in entry.command_block.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.reuse_requested.emit(entry.hosts, commands)

    def _on_delete_clicked(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        answer = QMessageBox.question(
            self, "Delete history entry",
            f"Delete run #{entry.id} ({entry.timestamp[:19]})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.history_db.delete_run(entry.id)
        self.refresh()

    def _on_row_double_clicked(self, _) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        dlg = _HistoryDetailDialog(entry, self._theme, self)
        dlg.exec()


class _HistoryDetailDialog(QDialog):
    """Read-only view of a single history entry: block + hosts + transcript dir."""

    def __init__(self, entry: HistoryEntry, theme: Optional[ThemeColors], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Run #{entry.id} — {entry.timestamp[:19]}")
        self.resize(720, 540)

        layout = QVBoxLayout(self)

        meta = QLabel(
            f"<b>Hosts:</b> {len(entry.hosts)} &nbsp; "
            f"<b>OK:</b> {entry.ok_count} &nbsp; "
            f"<b>Failed:</b> {entry.fail_count} &nbsp; "
            f"<b>Skipped:</b> {entry.skip_count} &nbsp; "
            f"<b>Duration:</b> {entry.duration_s:.1f}s"
        )
        layout.addWidget(meta)
        if entry.transcript_dir:
            layout.addWidget(QLabel(f"Transcripts: {entry.transcript_dir}"))

        layout.addWidget(QLabel("<b>Commands:</b>"))
        cmds_view = QPlainTextEdit(entry.command_block)
        cmds_view.setReadOnly(True)
        cmds_view.setFont(QFont("Consolas", 9))
        layout.addWidget(cmds_view, 1)

        layout.addWidget(QLabel("<b>Hosts:</b>"))
        hosts_view = QPlainTextEdit("\n".join(entry.hosts))
        hosts_view.setReadOnly(True)
        hosts_view.setFont(QFont("Consolas", 9))
        hosts_view.setMaximumHeight(160)
        layout.addWidget(hosts_view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        if theme is not None:
            self.setStyleSheet(
                f"QPlainTextEdit {{ background-color: {theme.bg_input}; "
                f"color: {theme.text_primary}; "
                f"border: 1px solid {theme.border_dim}; padding: 4px; }}"
            )


def _first_line(block: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped if len(stripped) <= 60 else stripped[:57] + "…"
    return "(empty)"
