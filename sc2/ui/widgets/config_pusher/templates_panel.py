"""Templates tab — save/list/use named command blocks tagged by vendor."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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

from sc2.scng.tools.history_db import (
    ConfigHistoryDB,
    DuplicateTemplate,
    TemplateEntry,
    TemplateNotFound,
)

from ...themes import ThemeColors


_DEFAULT_VENDORS = ["generic", "huawei", "cisco", "arista", "juniper"]
_COLS = ["Label", "Vendor", "Created", "First line"]


class TemplatesPanel(QWidget):
    """Save, browse, edit and reuse command-block templates."""

    use_requested = pyqtSignal(str)  # command block as raw string

    def __init__(self, history_db: ConfigHistoryDB, theme_manager=None, parent=None):
        super().__init__(parent)
        self.history_db = history_db
        self.theme_manager = theme_manager
        self._theme: Optional[ThemeColors] = None
        self._entries: List[TemplateEntry] = []

        self._setup_ui()
        if theme_manager is not None:
            self.apply_theme(theme_manager.colors)
        self.refresh()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Vendor:"))
        self.vendor_combo = QComboBox()
        self.vendor_combo.addItem("(all)", None)
        for v in _DEFAULT_VENDORS:
            self.vendor_combo.addItem(v, v)
        self.vendor_combo.currentIndexChanged.connect(lambda _: self.refresh())
        toolbar.addWidget(self.vendor_combo)
        toolbar.addStretch(1)
        self.new_btn = QPushButton("New template…")
        self.new_btn.clicked.connect(self._on_new_clicked)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self.new_btn)
        toolbar.addWidget(self.refresh_btn)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setObjectName("templatesTable")
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        self.use_btn = QPushButton("Use template →")
        self.use_btn.clicked.connect(self._on_use_clicked)
        bottom.addStretch(1)
        bottom.addWidget(self.edit_btn)
        bottom.addWidget(self.delete_btn)
        bottom.addWidget(self.use_btn)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def apply_theme(self, theme: ThemeColors) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"""
            QTableWidget#templatesTable {{
                background-color: {theme.bg_secondary};
                color: {theme.text_primary};
                gridline-color: {theme.border_dim};
                border: 1px solid {theme.border_dim};
                alternate-background-color: {theme.bg_tertiary};
            }}
            QTableWidget#templatesTable::item:selected {{
                background-color: {theme.bg_selected};
                color: {theme.text_primary};
            }}
            QHeaderView::section {{
                background-color: {theme.bg_tertiary};
                color: {theme.text_secondary};
                padding: 4px;
                border: 1px solid {theme.border_dim};
            }}
            QComboBox {{
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
        vendor = self.vendor_combo.currentData()
        self._entries = self.history_db.list_templates(vendor=vendor)
        self._reload_table()

    def _reload_table(self) -> None:
        self.table.setRowCount(len(self._entries))
        mono = QFont("Consolas")
        for row, t in enumerate(self._entries):
            cells = [
                t.label,
                t.vendor,
                t.created[:19].replace("T", " "),
                _first_line(t.command_block),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 3:
                    item.setFont(mono)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )

    def _selected_entry(self) -> Optional[TemplateEntry]:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._entries):
            return None
        return self._entries[row]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_new_clicked(self) -> None:
        dlg = _TemplateEditDialog(self._theme, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.history_db.save_template(
                label=dlg.label, command_block=dlg.command_block,
                vendor=dlg.vendor,
            )
        except DuplicateTemplate:
            QMessageBox.warning(self, "Save template",
                                f"A template named '{dlg.label}' already exists.")
            return
        self.refresh()

    def _on_edit_clicked(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        dlg = _TemplateEditDialog(self._theme, entry=entry, parent=self,
                                  label_editable=False)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.history_db.update_template(
                entry.label,
                command_block=dlg.command_block,
                vendor=dlg.vendor,
            )
        except TemplateNotFound:
            QMessageBox.warning(self, "Save template", "Template no longer exists.")
        self.refresh()

    def _on_delete_clicked(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        answer = QMessageBox.question(
            self, "Delete template", f"Delete '{entry.label}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.history_db.delete_template(entry.label)
        except TemplateNotFound:
            pass
        self.refresh()

    def _on_use_clicked(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "Use template", "Pick a row first.")
            return
        self.use_requested.emit(entry.command_block)

    def _on_row_double_clicked(self, _) -> None:
        self._on_use_clicked()

    # ------------------------------------------------------------------
    # External entry point: "Save current as template" from PushPanel
    # ------------------------------------------------------------------

    def open_save_dialog_with_block(self, command_block: str) -> None:
        """Open the Edit dialog pre-populated with `command_block`."""
        dlg = _TemplateEditDialog(self._theme, parent=self,
                                  preset_block=command_block)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.history_db.save_template(
                label=dlg.label, command_block=dlg.command_block,
                vendor=dlg.vendor,
            )
        except DuplicateTemplate:
            QMessageBox.warning(self, "Save template",
                                f"A template named '{dlg.label}' already exists.")
            return
        self.refresh()


class _TemplateEditDialog(QDialog):
    """Create or edit a template (label, vendor, command block)."""

    def __init__(
        self,
        theme: Optional[ThemeColors],
        entry: Optional[TemplateEntry] = None,
        preset_block: Optional[str] = None,
        label_editable: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit template" if entry else "New template")
        self.resize(620, 480)
        self.label: str = ""
        self.vendor: str = "generic"
        self.command_block: str = ""

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._label_edit = QLineEdit(entry.label if entry else "")
        self._label_edit.setEnabled(label_editable)
        self._label_edit.setPlaceholderText("e.g. huawei-snmp-view-discovery")
        form.addRow("Label:", self._label_edit)

        self._vendor_combo = QComboBox()
        self._vendor_combo.addItems(_DEFAULT_VENDORS)
        if entry:
            idx = self._vendor_combo.findText(entry.vendor)
            if idx >= 0:
                self._vendor_combo.setCurrentIndex(idx)
        form.addRow("Vendor:", self._vendor_combo)

        layout.addLayout(form)

        layout.addWidget(QLabel("Commands (one per line):"))
        self._block_edit = QPlainTextEdit()
        initial_block = entry.command_block if entry else (preset_block or "")
        self._block_edit.setPlainText(initial_block)
        self._block_edit.setFont(QFont("Consolas", 9))
        layout.addWidget(self._block_edit, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if theme is not None:
            self.setStyleSheet(
                f"QPlainTextEdit, QLineEdit, QComboBox {{ "
                f"background-color: {theme.bg_input}; color: {theme.text_primary}; "
                f"border: 1px solid {theme.border_dim}; padding: 4px; "
                f"border-radius: 4px; }}"
            )

    def _on_accept(self) -> None:
        label = self._label_edit.text().strip()
        block = self._block_edit.toPlainText().strip()
        if not label:
            QMessageBox.warning(self, "Validation", "Label is required.")
            return
        if not block:
            QMessageBox.warning(self, "Validation", "Command block is required.")
            return
        self.label = label
        self.vendor = self._vendor_combo.currentText()
        self.command_block = block
        self.accept()


def _first_line(block: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped if len(stripped) <= 60 else stripped[:57] + "…"
    return "(empty)"
