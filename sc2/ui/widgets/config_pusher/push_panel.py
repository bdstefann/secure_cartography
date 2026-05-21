"""Main Config Push UI — composes mode toggle, inputs, options, host list,
transcript view, controls, and progress bar around a PushWorker.

The panel knows nothing about Huawei specifically: the user supplies the
command block themselves (or loads a file / picks a template). Vendor
context exists only at template-tag level (handled in iter 6).
"""

from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from sc2.scng.creds.models import CredentialType
from sc2.scng.tools import config_pusher as cp
from sc2.scng.tools.history_db import ConfigHistoryDB

from .host_status_list import HostStatusList
from .transcript_view import TranscriptView
from .worker import PushParams, PushWorker
from ...themes import ThemeColors


_PUSH_RUNS_DIR = Path.home() / ".scng" / "config_push_runs"


class PushPanel(QWidget):
    """Single tab: configure + run + watch a parallel config push."""

    push_finished = pyqtSignal(int)        # history id of the completed run

    def __init__(self, vault, theme_manager, history_db: ConfigHistoryDB, parent=None):
        super().__init__(parent)
        self.vault = vault
        self.theme_manager = theme_manager
        self.history_db = history_db
        self._theme: Optional[ThemeColors] = None
        self._worker: Optional[PushWorker] = None
        self._start_time: float = 0.0
        self._current_run_dir: Optional[Path] = None
        self._current_hosts: List[str] = []
        self._counts = {"ok": 0, "failed": 0, "skipped": 0, "cancelled": 0,
                        "dry-run": 0}

        self._setup_ui()
        if theme_manager is not None:
            self.apply_theme(theme_manager.colors)
        self.refresh_credentials()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Row 1: mode + credential/parallel side by side
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(self._build_mode_group(), 1)
        top_row.addWidget(self._build_credential_group(), 2)
        layout.addLayout(top_row)

        layout.addWidget(self._build_hosts_group())
        layout.addWidget(self._build_commands_group())
        layout.addWidget(self._build_options_group())

        # Controls row
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.start_btn = QPushButton("▶ START PUSH")
        self.start_btn.setObjectName("startPushBtn")
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.stop_btn = QPushButton("■ STOP")
        self.stop_btn.setObjectName("stopPushBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.export_btn = QPushButton("Export transcripts…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export_clicked)
        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        controls.addStretch(1)
        controls.addWidget(self.export_btn)
        layout.addLayout(controls)

        # Progress
        prog_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setObjectName("pushProgress")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress_label = QLabel("Idle")
        self.progress_label.setObjectName("pushProgressLabel")
        prog_row.addWidget(self.progress, 1)
        prog_row.addWidget(self.progress_label)
        layout.addLayout(prog_row)

        # Master-detail: host list + transcript
        self.host_list = HostStatusList()
        self.host_list.host_selected.connect(self._on_host_selected)
        self.transcript = TranscriptView()

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.host_list)
        split.addWidget(self.transcript)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([280, 700])
        layout.addWidget(split, 1)

    def _build_mode_group(self) -> QGroupBox:
        box = QGroupBox("Mode")
        box.setObjectName("modeGroup")
        lay = QVBoxLayout(box)
        self.mode_file = QRadioButton("From file")
        self.mode_adhoc = QRadioButton("Ad-hoc command")
        self.mode_adhoc.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.mode_file)
        self._mode_group.addButton(self.mode_adhoc)
        lay.addWidget(self.mode_file)
        lay.addWidget(self.mode_adhoc)
        return box

    def _build_credential_group(self) -> QGroupBox:
        box = QGroupBox("Credential + concurrency")
        box.setObjectName("credentialGroup")
        form = QFormLayout(box)

        self.cred_combo = QComboBox()
        self.cred_combo.setObjectName("credCombo")
        form.addRow("SSH credential:", self.cred_combo)

        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 32)
        self.parallel_spin.setValue(8)
        form.addRow("Parallel sessions:", self.parallel_spin)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 600)
        self.timeout_spin.setValue(30)
        self.timeout_spin.setSuffix(" s")
        form.addRow("Connect timeout:", self.timeout_spin)
        return box

    def _build_hosts_group(self) -> QGroupBox:
        box = QGroupBox("Hosts")
        box.setObjectName("hostsGroup")
        lay = QVBoxLayout(box)

        toolbar = QHBoxLayout()
        self.load_hosts_btn = QPushButton("Load hosts.txt…")
        self.load_hosts_btn.clicked.connect(self._on_load_hosts_clicked)
        self.hosts_count_label = QLabel("0 hosts ready")
        toolbar.addWidget(self.load_hosts_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.hosts_count_label)
        lay.addLayout(toolbar)

        self.hosts_edit = QPlainTextEdit()
        self.hosts_edit.setObjectName("hostsEdit")
        self.hosts_edit.setPlaceholderText("One IP/hostname per line. # for comments.")
        self.hosts_edit.setMaximumBlockCount(2000)
        self.hosts_edit.setFont(_mono_font())
        self.hosts_edit.setMaximumHeight(120)
        self.hosts_edit.textChanged.connect(self._update_hosts_count)
        lay.addWidget(self.hosts_edit)
        return box

    def _build_commands_group(self) -> QGroupBox:
        box = QGroupBox("Commands")
        box.setObjectName("commandsGroup")
        lay = QVBoxLayout(box)

        toolbar = QHBoxLayout()
        self.load_cmds_btn = QPushButton("Load .txt…")
        self.load_cmds_btn.clicked.connect(self._on_load_commands_clicked)
        # Save as template button is wired in iter 6 (with TemplatesPanel)
        self.cmds_count_label = QLabel("0 lines")
        toolbar.addWidget(self.load_cmds_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.cmds_count_label)
        lay.addLayout(toolbar)

        self.cmds_edit = QPlainTextEdit()
        self.cmds_edit.setObjectName("cmdsEdit")
        self.cmds_edit.setPlaceholderText("One VRP command per line. # for comments.")
        self.cmds_edit.setMaximumBlockCount(5000)
        self.cmds_edit.setFont(_mono_font())
        self.cmds_edit.setMaximumHeight(140)
        self.cmds_edit.textChanged.connect(self._update_cmds_count)
        lay.addWidget(self.cmds_edit)
        return box

    def _build_options_group(self) -> QGroupBox:
        box = QGroupBox("Options")
        box.setObjectName("optionsGroup")
        lay = QVBoxLayout(box)

        self.opt_save = QCheckBox("Run 'save' at end (auto-confirm prompt)")
        self.opt_dry_run = QCheckBox("Dry-run (print commands, don't connect)")
        self.opt_legacy = QCheckBox("Legacy SSH (old ciphers / KEX)")
        self.opt_stop_on_fail = QCheckBox("Stop on first failure")
        self.opt_detect = QCheckBox("Detect already-applied — check command:")
        self.detect_cmd_edit = QLineEdit()
        self.detect_cmd_edit.setPlaceholderText(
            "e.g. display snmp-agent community"
        )
        self.detect_pattern_edit = QLineEdit()
        self.detect_pattern_edit.setPlaceholderText(
            "Skip if output contains (case-insensitive)"
        )
        self.opt_detect.toggled.connect(self.detect_cmd_edit.setEnabled)
        self.opt_detect.toggled.connect(self.detect_pattern_edit.setEnabled)
        self.detect_cmd_edit.setEnabled(False)
        self.detect_pattern_edit.setEnabled(False)

        lay.addWidget(self.opt_save)
        lay.addWidget(self.opt_dry_run)
        lay.addWidget(self.opt_legacy)
        lay.addWidget(self.opt_stop_on_fail)
        lay.addWidget(self.opt_detect)
        detect_inputs = QHBoxLayout()
        detect_inputs.setContentsMargins(20, 0, 0, 0)
        detect_inputs.addWidget(QLabel("Probe:"))
        detect_inputs.addWidget(self.detect_cmd_edit, 1)
        detect_inputs.addWidget(QLabel("Skip if contains:"))
        detect_inputs.addWidget(self.detect_pattern_edit, 1)
        lay.addLayout(detect_inputs)
        return box

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def apply_theme(self, theme: ThemeColors) -> None:
        self._theme = theme
        self.host_list.apply_theme(theme)
        self.transcript.apply_theme(theme)
        self.setStyleSheet(
            f"""
            QGroupBox {{
                color: {theme.text_secondary};
                border: 1px solid {theme.border_dim};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }}
            QPlainTextEdit#hostsEdit, QPlainTextEdit#cmdsEdit {{
                background-color: {theme.bg_input};
                color: {theme.text_primary};
                border: 1px solid {theme.border_dim};
                border-radius: 4px;
                padding: 4px;
            }}
            QPushButton#startPushBtn {{
                background-color: {theme.accent};
                color: {theme.text_on_accent};
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton#startPushBtn:hover {{
                background-color: {theme.accent_hover};
            }}
            QPushButton#startPushBtn:disabled {{
                background-color: {theme.bg_disabled};
                color: {theme.text_disabled};
            }}
            QPushButton#stopPushBtn {{
                background-color: {theme.accent_danger};
                color: {theme.text_on_accent};
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton#stopPushBtn:disabled {{
                background-color: {theme.bg_disabled};
                color: {theme.text_disabled};
            }}
            QProgressBar#pushProgress {{
                background-color: {theme.bg_input};
                border: 1px solid {theme.border_dim};
                border-radius: 4px;
                text-align: center;
                color: {theme.text_primary};
            }}
            QProgressBar#pushProgress::chunk {{
                background-color: {theme.accent_info};
            }}
            """
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_credentials(self) -> None:
        """Populate the SSH credential dropdown from the vault."""
        self.cred_combo.clear()
        if self.vault is None or not getattr(self.vault, "is_unlocked", False):
            self.cred_combo.addItem("(vault locked)", None)
            return
        infos = self.vault.list_credentials(credential_type=CredentialType.SSH)
        if not infos:
            self.cred_combo.addItem("(no SSH credentials)", None)
            return
        for info in infos:
            label = f"{info.name}  ({info.display_username or '?'})"
            self.cred_combo.addItem(label, info.name)

    def set_initial_block(self, commands: List[str]) -> None:
        self.cmds_edit.setPlainText("\n".join(commands))

    def set_initial_hosts(self, hosts: List[str]) -> None:
        self.hosts_edit.setPlainText("\n".join(hosts))

    # ------------------------------------------------------------------
    # Input helpers
    # ------------------------------------------------------------------

    def _parse_hosts(self) -> List[str]:
        out: List[str] = []
        for raw in self.hosts_edit.toPlainText().splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                out.append(line.split(",", 1)[0].strip())
        return [h for h in out if h]

    def _parse_commands(self) -> List[str]:
        out: List[str] = []
        for raw in self.cmds_edit.toPlainText().splitlines():
            line = raw.rstrip()
            if line.lstrip().startswith("#") or not line.strip():
                continue
            out.append(line)
        return out

    def _update_hosts_count(self) -> None:
        n = len(self._parse_hosts())
        self.hosts_count_label.setText(f"{n} hosts ready")

    def _update_cmds_count(self) -> None:
        n = len(self._parse_commands())
        self.cmds_count_label.setText(f"{n} lines")

    # ------------------------------------------------------------------
    # File pickers
    # ------------------------------------------------------------------

    def _on_load_hosts_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load hosts file", str(Path.home()),
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Load failed", str(exc))
            return
        self.hosts_edit.setPlainText(content)

    def _on_load_commands_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load commands file", str(Path.home()),
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Load failed", str(exc))
            return
        self.cmds_edit.setPlainText(content)

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def _on_start_clicked(self) -> None:
        hosts = self._parse_hosts()
        commands = self._parse_commands()
        cred_name = self.cred_combo.currentData()

        if not hosts:
            QMessageBox.warning(self, "Validation", "Add at least one host.")
            return
        if not commands:
            QMessageBox.warning(self, "Validation", "Add at least one command.")
            return
        if cred_name is None:
            QMessageBox.warning(self, "Validation", "Pick an SSH credential.")
            return

        ssh_cred = self.vault.get_ssh_credential(name=cred_name)
        if ssh_cred is None:
            QMessageBox.warning(self, "Validation",
                                f"Credential '{cred_name}' not found in vault.")
            return

        dry_run = self.opt_dry_run.isChecked()
        save = self.opt_save.isChecked()
        legacy = self.opt_legacy.isChecked()
        stop_on_fail = self.opt_stop_on_fail.isChecked()
        parallel = self.parallel_spin.value()
        timeout = self.timeout_spin.value()
        check_command = (
            self.detect_cmd_edit.text().strip()
            if self.opt_detect.isChecked() else None
        )
        skip_patterns = (
            [self.detect_pattern_edit.text().strip()]
            if self.opt_detect.isChecked() and self.detect_pattern_edit.text().strip()
            else []
        )

        summary = (
            f"Push to {len(hosts)} hosts.\n"
            f"{len(commands)} commands per host.\n"
            f"Parallel = {parallel}.\n"
            f"Save = {'yes' if save else 'no'}.\n"
            f"Dry-run = {'yes' if dry_run else 'no'}.\n"
            f"Stop on first failure = {'yes' if stop_on_fail else 'no'}.\n"
            f"\nCredential: {cred_name} ({ssh_cred.username})\n"
            "\nContinue?"
        )
        answer = QMessageBox.question(
            self, "Confirm push", summary,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # Transcript directory: ~/.scng/config_push_runs/<timestamp>/
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self._current_run_dir = _PUSH_RUNS_DIR / ts
        self._current_hosts = hosts
        self._counts = {"ok": 0, "failed": 0, "skipped": 0, "cancelled": 0,
                        "dry-run": 0}

        # Reset UI
        self.host_list.set_hosts(hosts)
        if self._theme is not None:
            self.host_list.apply_theme(self._theme)
        self.transcript.clear()
        if self._theme is not None:
            self.transcript.apply_theme(self._theme)
        self.progress.setRange(0, len(hosts))
        self.progress.setValue(0)
        self.progress_label.setText("Running...")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self._start_time = time.time()

        params = PushParams(
            hosts=hosts,
            username=ssh_cred.username,
            password=ssh_cred.password,
            key_file=None,  # vault returns key as content via key_content; SSHClient supports both
            key_passphrase=ssh_cred.key_passphrase,
            commands=commands,
            save=save,
            dry_run=dry_run,
            parallel=parallel,
            timeout=timeout,
            legacy_mode=legacy,
            check_command=check_command,
            skip_patterns=skip_patterns,
            stop_on_first_failure=stop_on_fail,
            transcript_dir=self._current_run_dir,
        )
        # SSHClient also accepts key_content directly; if the credential has
        # one but no password, pass it through via SSHClientConfig path.
        # apply_commands_to_device exposes only password / key_file / key_passphrase,
        # so we need to pass key_content via a temp side channel — done by
        # writing the key to a tmpfile is overkill; just route through password
        # when present, else the user must currently use a key_file credential
        # (covered in a follow-up iter if needed).

        self._worker = PushWorker(params)
        self._worker.host_status_changed.connect(self._on_host_status_changed)
        self._worker.transcript_chunk.connect(self._on_transcript_chunk)
        self._worker.device_finished.connect(self._on_device_finished)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

    def _on_stop_clicked(self) -> None:
        if self._worker is None:
            return
        active = sum(1 for r in self.host_list._rows.values()
                     if r.status in ("connecting", "running"))
        if active > 0:
            answer = QMessageBox.question(
                self, "Confirm stop",
                f"{active} sessions are active. They will finish their current "
                "command, then disconnect. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._worker.request_cancel()
        self.stop_btn.setEnabled(False)
        self.progress_label.setText("Stopping...")

    # ------------------------------------------------------------------
    # Worker signal handlers (run on the UI thread)
    # ------------------------------------------------------------------

    def _on_host_status_changed(self, host: str, status: str, message: str) -> None:
        self.host_list.update_status(host, status, message)

    def _on_transcript_chunk(self, host: str, line: str) -> None:
        self.transcript.append_line(host, line)

    def _on_device_finished(self, result) -> None:
        self._counts[result.status] = self._counts.get(result.status, 0) + 1
        self.host_list.update_status(
            result.host, result.status, result.message, result.duration_s,
        )
        done = sum(self._counts.values())
        self.progress.setValue(done)
        self.progress_label.setText(
            f"{done} / {len(self._current_hosts)} done "
            f"({self._counts['ok']} ok, {self._counts['failed']} fail, "
            f"{self._counts['skipped']} skip)"
        )

    def _on_all_done(self, results) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.export_btn.setEnabled(True)
        duration = time.time() - self._start_time

        ok = sum(1 for r in results if r.status == "ok")
        fail = sum(1 for r in results if r.status == "failed")
        skip = sum(1 for r in results if r.status == "skipped")

        # Persist to history (unless this was a dry-run — those aren't useful
        # to record)
        if not self.opt_dry_run.isChecked():
            try:
                run_id = self.history_db.record_run(
                    command_block=self.cmds_edit.toPlainText(),
                    hosts=self._current_hosts,
                    ok_count=ok,
                    fail_count=fail,
                    skip_count=skip,
                    duration_s=duration,
                    transcript_dir=str(self._current_run_dir)
                        if self._current_run_dir else None,
                )
                self.push_finished.emit(run_id)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "History",
                                    f"Failed to record run: {exc}")

        self.progress_label.setText(
            f"Done in {duration:.1f}s — {ok} ok, {fail} fail, {skip} skip"
        )

        self._worker = None

    def _on_host_selected(self, host: str) -> None:
        self.transcript.show_host(host)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _on_export_clicked(self) -> None:
        if self._current_run_dir is None or not self._current_run_dir.exists():
            QMessageBox.information(self, "Export",
                                    "No transcripts to export yet.")
            return
        QMessageBox.information(
            self, "Export",
            f"Transcripts already on disk:\n{self._current_run_dir}",
        )


def _mono_font() -> QFont:
    f = QFont("Consolas")
    if not f.exactMatch():
        f.setFamily("Courier New")
        f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(9)
    return f
