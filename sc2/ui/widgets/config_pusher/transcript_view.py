"""Read-only monospace transcript view with auto-scroll.

Holds per-host buffers internally so the parent panel can switch which
host is displayed by calling show_host(host); transcript_chunk events
arriving for a non-displayed host are queued but not painted until the
user selects that host.

Lines starting with "$ " (the convention emitted by config_pusher) are
treated as commands and rendered slightly emphasised so the eye can
separate input from device output.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from ...themes import ThemeColors


_PROMPT_PREFIX = "$ "


class TranscriptView(QWidget):
    """Per-host transcript buffer + a single QPlainTextEdit display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme: Optional[ThemeColors] = None
        self._buffers: Dict[str, List[str]] = defaultdict(list)
        self._current_host: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._edit = QPlainTextEdit()
        self._edit.setObjectName("transcriptEdit")
        self._edit.setReadOnly(True)
        self._edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._edit.setUndoRedoEnabled(False)
        self._edit.setMaximumBlockCount(50000)  # avoid runaway memory on huge runs

        mono = QFont("Consolas")
        if not mono.exactMatch():
            mono.setFamily("Courier New")
            mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)
        self._edit.setFont(mono)

        layout.addWidget(self._edit)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append_line(self, host: str, line: str) -> None:
        """Add one line to `host`'s buffer; repaints only if host is shown."""
        self._buffers[host].append(line)
        if host == self._current_host:
            self._append_to_view(line)

    def show_host(self, host: Optional[str]) -> None:
        """Switch the view to render `host`'s buffer. None clears."""
        self._current_host = host
        self._edit.clear()
        if host is None:
            return
        for line in self._buffers.get(host, []):
            self._append_to_view(line)

    def clear(self) -> None:
        self._buffers.clear()
        self._current_host = None
        self._edit.clear()

    def text_for(self, host: str) -> str:
        return "\n".join(self._buffers.get(host, []))

    def all_buffers(self) -> Dict[str, List[str]]:
        """Snapshot of per-host transcripts (for export)."""
        return {h: list(lines) for h, lines in self._buffers.items()}

    def apply_theme(self, theme: ThemeColors) -> None:
        self._theme = theme
        self._edit.setStyleSheet(
            f"""
            QPlainTextEdit#transcriptEdit {{
                background-color: {theme.bg_input};
                color: {theme.text_primary};
                border: 1px solid {theme.border_dim};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {theme.bg_selected};
                selection-color: {theme.text_primary};
            }}
            """
        )
        # Re-render so command-line formatting picks up the new theme colours
        if self._current_host is not None:
            self.show_host(self._current_host)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _append_to_view(self, line: str) -> None:
        cursor = self._edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if self._theme is not None and line.startswith(_PROMPT_PREFIX):
            cmd_fmt = QTextCharFormat()
            cmd_fmt.setForeground(QColor(self._theme.accent))
            cmd_fmt.setFontWeight(QFont.Weight.Bold)
            cursor.insertText(line + "\n", cmd_fmt)
        else:
            cursor.insertText(line + "\n")

        scrollbar = self._edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
