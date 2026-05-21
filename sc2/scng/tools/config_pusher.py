"""Vendor-agnostic SSH config push.

Provides one entry point — apply_commands_to_device — that connects to a
single device, optionally runs a check-command to detect "already
applied", sends an ordered list of commands, optionally fires `save` +
`y`, and returns a DeviceResult with a structured transcript. Reused by
both scripts/huawei_bulk_snmp_config.py (the CLI) and the PyQt6
Config Push widget (the GUI).

The function takes a `cancel_check` callable that is consulted between
each command and immediately before the connect, so the GUI can implement
soft cancellation without killing the SSH session mid-command. The
`on_transcript` callable is invoked for every transcript line as it
happens so the GUI can stream output without waiting for the device to
finish.

Backwards compatibility: the Huawei SNMP-view CLI used a fixed command
builder and a fixed `display snmp-agent community` probe. Both still
live here (build_snmp_view_commands, build_snmp_view_skip_pattern) so
the CLI script can stay a thin wrapper.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from sc2.scng.discovery.ssh.client import SSHClient, SSHClientConfig


STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_DRY_RUN = "dry-run"
STATUS_CANCELLED = "cancelled"


@dataclass
class DeviceResult:
    """Outcome of one config-push attempt against one device."""

    host: str
    status: str
    message: str = ""
    duration_s: float = 0.0
    transcript: List[str] = field(default_factory=list)


def load_hosts(path: Path) -> List[str]:
    """Read a hosts file (one IP/hostname per line, # for comments).

    Lines may carry inline comments and a trailing CSV-style metadata
    field is tolerated (only the first comma-separated token is kept) so
    files exported from spreadsheets work without preprocessing.
    """
    if not path.exists():
        raise FileNotFoundError(f"hosts file not found: {path}")
    hosts: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        host = line.split(",", 1)[0].strip()
        if host:
            hosts.append(host)
    return hosts


# ---------------------------------------------------------------------------
# Huawei SNMP-view helpers — kept here so the existing CLI wrapper stays thin
# ---------------------------------------------------------------------------

def build_snmp_view_commands(
    view_name: str,
    community: str,
    restricted: bool,
) -> List[str]:
    """Return the ordered VRP commands that build the SNMP view + binding.

    With `restricted=True` the view only exposes the three subtrees needed
    for discovery (internet, LLDP-MIB, Huawei enterprise). Otherwise the
    full iso subtree is included.
    """
    if restricted:
        subtree_cmds = [
            f"snmp-agent mib-view included {view_name} internet",
            f"snmp-agent mib-view included {view_name} 1.0.8802",
            f"snmp-agent mib-view included {view_name} 1.3.6.1.4.1.2011",
        ]
    else:
        subtree_cmds = [f"snmp-agent mib-view included {view_name} iso"]

    return [
        "system-view",
        *subtree_cmds,
        f"snmp-agent community read cipher {community} mib-view {view_name}",
        "quit",
    ]


def build_snmp_view_skip_check(view_name: str) -> tuple[str, List[str]]:
    """Return (probe_command, skip_patterns) for the SNMP-view idempotency check.

    The probe runs once per device before the config block; if any of the
    skip_patterns appear (case-insensitive) in the output, the device is
    already configured and apply_commands_to_device returns SKIPPED.
    The cipher community is masked in `display snmp-agent community`
    output, so we match on the view-name binding instead — which is the
    line "Mib-view: <name>" in VRP output.
    """
    return (
        "display snmp-agent community",
        [f"Mib-view: {view_name}", f"mib-view {view_name}"],
    )


# ---------------------------------------------------------------------------
# Generic push
# ---------------------------------------------------------------------------

def apply_commands_to_device(
    host: str,
    *,
    username: str,
    password: Optional[str] = None,
    key_file: Optional[str] = None,
    key_passphrase: Optional[str] = None,
    port: int = 22,
    commands: List[str],
    save: bool = False,
    dry_run: bool = False,
    timeout: int = 30,
    legacy_mode: bool = False,
    check_command: Optional[str] = None,
    skip_patterns: Optional[List[str]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    on_transcript: Optional[Callable[[str], None]] = None,
) -> DeviceResult:
    """Push `commands` to a single device.

    Args:
        host, username, password/key_file/key_passphrase, port,
        timeout, legacy_mode: forwarded to SSHClientConfig.
        commands: ordered list of CLI commands to send.
        save: if True, send `save` then `y` after the block to persist.
        dry_run: if True, do not connect; transcript = the would-be commands.
        check_command: optional probe run before the block.
        skip_patterns: if any pattern appears (case-insensitive) in the
            check_command output, the device is reported as SKIPPED.
        cancel_check: callable returning True to abort. Consulted before
            connect and between commands; never interrupts an in-flight
            command (soft cancellation).
        on_transcript: callable invoked with each transcript line as it
            is appended, so a GUI can stream output live.

    Returns a DeviceResult describing the outcome and the full transcript.
    """
    started = time.time()
    transcript: List[str] = []

    def _record(line: str) -> None:
        transcript.append(line)
        if on_transcript is not None:
            on_transcript(line)

    def _result(status: str, message: str) -> DeviceResult:
        return DeviceResult(
            host=host,
            status=status,
            message=message,
            duration_s=time.time() - started,
            transcript=transcript,
        )

    if cancel_check and cancel_check():
        return _result(STATUS_CANCELLED, "Cancelled before connect")

    if dry_run:
        for cmd in commands:
            _record(f"$ {cmd}")
        if save:
            _record("$ save")
            _record("$ y")
        return _result(
            STATUS_DRY_RUN,
            f"Would send {len(commands) + (2 if save else 0)} commands",
        )

    cfg = SSHClientConfig(
        host=host,
        username=username,
        password=password,
        key_file=key_file,
        key_passphrase=key_passphrase,
        port=port,
        timeout=timeout,
        legacy_mode=legacy_mode,
    )

    try:
        with SSHClient(cfg) as client:
            prompt = client.find_prompt()
            client.set_expect_prompt(prompt)
            client.disable_pagination()

            if check_command and skip_patterns:
                probe_out = client.execute_command(check_command)
                _record(f"$ {check_command}")
                _record(probe_out)
                lowered = probe_out.lower()
                if any(p.lower() in lowered for p in skip_patterns):
                    return _result(
                        STATUS_SKIPPED,
                        "Already configured (skip pattern matched)",
                    )

            for cmd in commands:
                if cancel_check and cancel_check():
                    return _result(STATUS_CANCELLED, "Cancelled between commands")

                out = client.execute_command(cmd)
                _record(f"$ {cmd}")
                _record(out)
                if _looks_like_device_error(out):
                    return _result(STATUS_FAILED, f"Device rejected '{cmd}'")

            if save:
                save_out = client.execute_command("save")
                _record("$ save")
                _record(save_out)
                confirm_out = client.execute_command("y")
                _record("$ y")
                _record(confirm_out)

            return _result(STATUS_OK, "Commands applied")

    except Exception as exc:
        return _result(STATUS_FAILED, f"{type(exc).__name__}: {exc}")


def _looks_like_device_error(output: str) -> bool:
    """Heuristic for "the device printed an error in response to our command".

    VRP and most other vendors prefix errors with a "% " marker; the word
    'error' on its own is not enough (it appears in benign output too).
    """
    lowered = output.lower()
    return "error" in lowered and "% " in lowered
