"""QThread that drives sc2.scng.tools.config_pusher.run_push.

The worker is a thin Qt facade: it owns a threading.Event for soft
cancellation, runs run_push on a background thread, and translates the
plain-callback hooks (on_host_status, on_transcript, on_device_finished)
into pyqtSignals that slots on the UI thread can connect to.

Signals are auto-queued across threads because the worker runs in its
own QThread; receiver objects living on the main thread get the slot
invoked safely on the main thread.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from sc2.scng.tools.config_pusher import DeviceResult, run_push


@dataclass
class PushParams:
    """All inputs the worker needs for one push run."""

    hosts: List[str]
    username: str
    commands: List[str]
    password: Optional[str] = None
    key_file: Optional[str] = None
    key_passphrase: Optional[str] = None
    port: int = 22
    save: bool = False
    dry_run: bool = False
    parallel: int = 8
    timeout: int = 30
    legacy_mode: bool = False
    check_command: Optional[str] = None
    skip_patterns: List[str] = field(default_factory=list)
    stop_on_first_failure: bool = False
    transcript_dir: Optional[Path] = None


class PushWorker(QThread):
    """Run a parallel config push in a background thread.

    Connect to signals on the UI thread to drive the host list, transcript
    view, and progress bar. Call request_cancel() to soft-stop: in-flight
    SSH commands finish, then their sessions disconnect cleanly.
    """

    host_status_changed = pyqtSignal(str, str, str)   # host, status, message
    transcript_chunk = pyqtSignal(str, str)           # host, line
    device_finished = pyqtSignal(object)              # DeviceResult
    all_done = pyqtSignal(list)                       # List[DeviceResult]

    def __init__(self, params: PushParams, parent=None):
        super().__init__(parent)
        self._params = params
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        """Soft-cancel: in-flight commands finish, no new ones start."""
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        params = self._params

        results = run_push(
            hosts=params.hosts,
            username=params.username,
            password=params.password,
            key_file=params.key_file,
            key_passphrase=params.key_passphrase,
            port=params.port,
            commands=params.commands,
            save=params.save,
            dry_run=params.dry_run,
            parallel=params.parallel,
            timeout=params.timeout,
            legacy_mode=params.legacy_mode,
            check_command=params.check_command,
            skip_patterns=params.skip_patterns or None,
            stop_on_first_failure=params.stop_on_first_failure,
            transcript_dir=params.transcript_dir,
            on_host_status=self._emit_host_status,
            on_transcript=self._emit_transcript,
            on_device_finished=self._emit_device_finished,
            cancel_check=self._cancel.is_set,
        )
        self.all_done.emit(results)

    def _emit_host_status(self, host: str, status: str, message: str) -> None:
        self.host_status_changed.emit(host, status, message)

    def _emit_transcript(self, host: str, line: str) -> None:
        self.transcript_chunk.emit(host, line)

    def _emit_device_finished(self, result: DeviceResult) -> None:
        self.device_finished.emit(result)
