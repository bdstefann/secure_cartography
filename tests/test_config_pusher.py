"""
Tests for sc2.scng.tools.config_pusher.

Covers the vendor-agnostic helpers (load_hosts, build_snmp_view_commands,
build_snmp_view_skip_check) plus the orchestration in
apply_commands_to_device. SSHClient is monkeypatched at the symbol the
module imports so tests never touch the network.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from sc2.scng.tools import config_pusher as cp


# -----------------------------------------------------------------------------
# Pure helpers
# -----------------------------------------------------------------------------

def test_load_hosts_strips_comments_and_blanks(tmp_path: Path):
    p = tmp_path / "hosts.txt"
    p.write_text(
        "10.0.0.1\n"
        "\n"
        "10.0.0.2   # core sw\n"
        "# whole-line comment\n"
        "10.0.0.3,site=floor1\n"   # CSV-style trailing metadata
        "   \n"
        "10.0.0.4\n"
    )
    assert cp.load_hosts(p) == ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]


def test_load_hosts_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        cp.load_hosts(tmp_path / "nope.txt")


def test_build_snmp_view_commands_restricted():
    cmds = cp.build_snmp_view_commands("view-x", "public", restricted=True)
    assert cmds[0] == "system-view"
    assert "snmp-agent mib-view included view-x internet" in cmds
    assert "snmp-agent mib-view included view-x 1.0.8802" in cmds
    assert "snmp-agent mib-view included view-x 1.3.6.1.4.1.2011" in cmds
    assert "snmp-agent community read cipher public mib-view view-x" in cmds
    assert cmds[-1] == "quit"


def test_build_snmp_view_commands_iso():
    cmds = cp.build_snmp_view_commands("view-x", "public", restricted=False)
    assert "snmp-agent mib-view included view-x iso" in cmds
    assert not any("1.0.8802" in c for c in cmds)


def test_build_snmp_view_skip_check_returns_view_name_patterns():
    probe, patterns = cp.build_snmp_view_skip_check("view-discovery")
    assert probe == "display snmp-agent community"
    assert "Mib-view: view-discovery" in patterns
    assert "mib-view view-discovery" in patterns


# -----------------------------------------------------------------------------
# Dry-run never connects
# -----------------------------------------------------------------------------

def test_dry_run_returns_dry_run_status_and_does_not_touch_sshclient(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("dry_run must not instantiate SSHClient")
    monkeypatch.setattr(cp, "SSHClient", fail_if_called)

    r = cp.apply_commands_to_device(
        "10.0.0.1",
        username="admin",
        password="x",
        commands=["system-view", "quit"],
        save=True,
        dry_run=True,
    )
    assert r.status == cp.STATUS_DRY_RUN
    assert r.host == "10.0.0.1"
    # Transcript shows what would be sent, including save+y
    assert "$ system-view" in r.transcript
    assert "$ quit" in r.transcript
    assert "$ save" in r.transcript
    assert "$ y" in r.transcript


# -----------------------------------------------------------------------------
# Mock SSHClient — exercises the real apply_commands_to_device flow
# -----------------------------------------------------------------------------

class FakeSSHClient:
    """Stand-in for SSHClient that records calls and lets tests script replies."""

    instances: List["FakeSSHClient"] = []

    def __init__(self, config):
        self.config = config
        self.calls: List[str] = []
        self.replies = {}            # command -> output
        self.default_reply = ""
        self.disconnected = False
        FakeSSHClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.disconnected = True
        return False

    def find_prompt(self):
        return "<HW>"

    def set_expect_prompt(self, prompt):
        self.expect = prompt

    def disable_pagination(self):
        pass

    def execute_command(self, command, timeout=None):
        self.calls.append(command)
        return self.replies.get(command, self.default_reply)


@pytest.fixture
def fake_ssh(monkeypatch):
    FakeSSHClient.instances.clear()
    monkeypatch.setattr(cp, "SSHClient", FakeSSHClient)
    return FakeSSHClient


def test_apply_commands_happy_path(fake_ssh):
    r = cp.apply_commands_to_device(
        "10.0.0.1",
        username="admin",
        password="x",
        commands=["system-view", "quit"],
    )
    assert r.status == cp.STATUS_OK
    assert fake_ssh.instances[0].calls == ["system-view", "quit"]
    # Each command produces a "$ cmd" + output pair in the transcript
    assert "$ system-view" in r.transcript
    assert "$ quit" in r.transcript


def test_apply_commands_with_save_appends_save_and_y(fake_ssh):
    r = cp.apply_commands_to_device(
        "10.0.0.1",
        username="admin",
        password="x",
        commands=["system-view"],
        save=True,
    )
    assert r.status == cp.STATUS_OK
    assert fake_ssh.instances[0].calls == ["system-view", "save", "y"]


def test_skip_check_matches_returns_skipped_without_sending_commands(fake_ssh):
    # Pre-load the probe reply so the skip pattern matches
    FakeSSHClient.instances.clear()

    # Capture the instance as soon as it's created so we can set replies
    real_init = FakeSSHClient.__init__

    def init_with_reply(self, config):
        real_init(self, config)
        self.replies["display snmp-agent community"] = (
            "Community name:public\n"
            "Group name:public\n"
            "Mib-view: view-discovery\n"
        )

    FakeSSHClient.__init__ = init_with_reply
    try:
        r = cp.apply_commands_to_device(
            "10.0.0.1",
            username="admin",
            password="x",
            commands=["system-view", "snmp-agent ...", "quit"],
            check_command="display snmp-agent community",
            skip_patterns=["Mib-view: view-discovery"],
        )
    finally:
        FakeSSHClient.__init__ = real_init

    assert r.status == cp.STATUS_SKIPPED
    # Only the probe was sent; none of the config commands hit the device
    assert fake_ssh.instances[0].calls == ["display snmp-agent community"]


def test_device_error_marks_failed_at_that_command(fake_ssh):
    # Arm the second command to come back with a VRP-style "% Error" line
    real_init = FakeSSHClient.__init__

    def init_with_reply(self, config):
        real_init(self, config)
        self.replies["snmp-agent mib-view included view-x iso"] = (
            "% Error: Insufficient privilege\n"
        )

    FakeSSHClient.__init__ = init_with_reply
    try:
        r = cp.apply_commands_to_device(
            "10.0.0.1",
            username="admin",
            password="x",
            commands=[
                "system-view",
                "snmp-agent mib-view included view-x iso",
                "quit",
            ],
        )
    finally:
        FakeSSHClient.__init__ = real_init

    assert r.status == cp.STATUS_FAILED
    assert "snmp-agent mib-view included view-x iso" in r.message
    # The third command was never sent — failure stops the run
    assert fake_ssh.instances[0].calls == [
        "system-view",
        "snmp-agent mib-view included view-x iso",
    ]


def test_word_error_alone_without_percent_marker_is_not_a_failure(fake_ssh):
    # Some devices print informational lines containing "error" without "% ".
    # The heuristic must not flag those.
    real_init = FakeSSHClient.__init__

    def init_with_reply(self, config):
        real_init(self, config)
        self.replies["system-view"] = (
            "Info: this command does not modify error counters\n"
        )

    FakeSSHClient.__init__ = init_with_reply
    try:
        r = cp.apply_commands_to_device(
            "10.0.0.1",
            username="admin",
            password="x",
            commands=["system-view", "quit"],
        )
    finally:
        FakeSSHClient.__init__ = real_init

    assert r.status == cp.STATUS_OK


def test_cancel_check_before_connect(fake_ssh):
    r = cp.apply_commands_to_device(
        "10.0.0.1",
        username="admin",
        password="x",
        commands=["system-view"],
        cancel_check=lambda: True,
    )
    assert r.status == cp.STATUS_CANCELLED
    # SSHClient was never instantiated
    assert fake_ssh.instances == []


def test_cancel_check_between_commands(fake_ssh):
    sent = []

    def cancel_after_two():
        # First two checks (before cmd 1 and before cmd 2) return False;
        # the third (before cmd 3) returns True.
        return len(sent) >= 2

    real_exec = FakeSSHClient.execute_command

    def tracking_exec(self, command, timeout=None):
        sent.append(command)
        return real_exec(self, command, timeout)

    FakeSSHClient.execute_command = tracking_exec
    try:
        r = cp.apply_commands_to_device(
            "10.0.0.1",
            username="admin",
            password="x",
            commands=["a", "b", "c"],
            cancel_check=cancel_after_two,
        )
    finally:
        FakeSSHClient.execute_command = real_exec

    assert r.status == cp.STATUS_CANCELLED
    assert sent == ["a", "b"]  # 'c' was skipped because of cancel


def test_on_transcript_streams_each_line(fake_ssh):
    streamed: List[str] = []
    r = cp.apply_commands_to_device(
        "10.0.0.1",
        username="admin",
        password="x",
        commands=["system-view", "quit"],
        on_transcript=streamed.append,
    )
    assert r.status == cp.STATUS_OK
    # Same content as r.transcript, delivered live
    assert streamed == r.transcript


def test_exception_during_connect_marks_failed(monkeypatch):
    def explode(config):
        raise ConnectionRefusedError("boom")
    monkeypatch.setattr(cp, "SSHClient", explode)

    r = cp.apply_commands_to_device(
        "10.0.0.1",
        username="admin",
        password="x",
        commands=["system-view"],
    )
    assert r.status == cp.STATUS_FAILED
    assert "ConnectionRefusedError" in r.message
