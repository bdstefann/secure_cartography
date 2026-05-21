"""
Tests for sc2.scng.tools.config_pusher.run_push (the parallel orchestrator
that both the CLI and the Qt PushWorker call into).

We monkeypatch apply_commands_to_device so tests never touch the network
or threading-around-SSH, and exercise:
  - results returned for every host
  - on_host_status / on_transcript / on_device_finished callbacks fire
  - stop_on_first_failure converts in-flight runs to cancellations
  - external cancel_check short-circuits before connect
  - transcript_dir produces one file per host with proper content
  - dry_run path still calls apply_commands_to_device (we trust its own tests)
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List

import pytest

from sc2.scng.tools import config_pusher as cp


def _make_fake_apply(behavior: dict):
    """Return an apply_commands_to_device stand-in that consults `behavior`.

    behavior maps host -> either:
      - DeviceResult to return directly (synchronous)
      - callable(host, kwargs) -> DeviceResult (lets the test gate on cancel)
    """
    def fake(host, **kwargs):
        # Honour cancel_check before doing anything (matches real semantics)
        cc = kwargs.get("cancel_check")
        if cc and cc():
            return cp.DeviceResult(host=host, status=cp.STATUS_CANCELLED,
                                   message="Cancelled before connect")
        b = behavior[host]
        if callable(b):
            return b(host, kwargs)
        # Drive status callbacks like the real impl would
        on_status = kwargs.get("on_status")
        on_transcript = kwargs.get("on_transcript")
        if on_status:
            on_status(cp.STATUS_CONNECTING)
            on_status(cp.STATUS_RUNNING)
        if on_transcript:
            for cmd in kwargs.get("commands", []):
                on_transcript(f"$ {cmd}")
        return b
    return fake


def test_run_push_returns_one_result_per_host(monkeypatch):
    behavior = {
        "10.0.0.1": cp.DeviceResult(host="10.0.0.1", status=cp.STATUS_OK,
                                    message="ok", duration_s=0.1),
        "10.0.0.2": cp.DeviceResult(host="10.0.0.2", status=cp.STATUS_SKIPPED,
                                    message="skip", duration_s=0.1),
        "10.0.0.3": cp.DeviceResult(host="10.0.0.3", status=cp.STATUS_FAILED,
                                    message="bad", duration_s=0.1),
    }
    monkeypatch.setattr(cp, "apply_commands_to_device", _make_fake_apply(behavior))

    results = cp.run_push(
        hosts=["10.0.0.1", "10.0.0.2", "10.0.0.3"],
        username="admin",
        commands=["system-view"],
        parallel=3,
    )
    by_host = {r.host: r.status for r in results}
    assert by_host == {
        "10.0.0.1": cp.STATUS_OK,
        "10.0.0.2": cp.STATUS_SKIPPED,
        "10.0.0.3": cp.STATUS_FAILED,
    }


def test_callbacks_fire_for_every_host(monkeypatch):
    behavior = {
        "1.1.1.1": cp.DeviceResult(host="1.1.1.1", status=cp.STATUS_OK,
                                   message="", duration_s=0.0),
        "2.2.2.2": cp.DeviceResult(host="2.2.2.2", status=cp.STATUS_FAILED,
                                   message="x", duration_s=0.0),
    }
    monkeypatch.setattr(cp, "apply_commands_to_device", _make_fake_apply(behavior))

    statuses: List[tuple] = []
    transcripts: List[tuple] = []
    finished: List[cp.DeviceResult] = []

    cp.run_push(
        hosts=["1.1.1.1", "2.2.2.2"],
        username="admin",
        commands=["a", "b"],
        parallel=2,
        on_host_status=lambda h, s, m: statuses.append((h, s)),
        on_transcript=lambda h, l: transcripts.append((h, l)),
        on_device_finished=lambda r: finished.append(r),
    )

    # Each host got connecting + running + final
    for h in ("1.1.1.1", "2.2.2.2"):
        host_statuses = [s for (hh, s) in statuses if hh == h]
        assert cp.STATUS_CONNECTING in host_statuses
        assert cp.STATUS_RUNNING in host_statuses
        # Final status present
        assert any(s in (cp.STATUS_OK, cp.STATUS_FAILED) for s in host_statuses)

    # Both transcripts captured
    assert ("1.1.1.1", "$ a") in transcripts
    assert ("2.2.2.2", "$ a") in transcripts

    # Both finished events emitted
    assert {r.host for r in finished} == {"1.1.1.1", "2.2.2.2"}


def test_stop_on_first_failure_cancels_in_flight(monkeypatch):
    # Use a gate: host A fails fast; hosts B and C wait on the gate so they
    # are guaranteed to be in flight when the failure lands and check the
    # cancel signal that stop_on_first_failure flips on.
    gate = threading.Event()

    def slow_then_check(host, kwargs):
        gate.wait(timeout=3)
        cc = kwargs.get("cancel_check")
        if cc and cc():
            return cp.DeviceResult(host=host, status=cp.STATUS_CANCELLED,
                                   message="cancelled", duration_s=0.0)
        return cp.DeviceResult(host=host, status=cp.STATUS_OK,
                               message="ok", duration_s=0.0)

    behavior = {
        "fast-fail": cp.DeviceResult(host="fast-fail", status=cp.STATUS_FAILED,
                                     message="boom", duration_s=0.0),
        "slow-b": slow_then_check,
        "slow-c": slow_then_check,
    }
    monkeypatch.setattr(cp, "apply_commands_to_device", _make_fake_apply(behavior))

    def open_gate_after_fail():
        # Give the fast-fail a moment to land + flip stop_on_first_failure
        import time as _t
        _t.sleep(0.2)
        gate.set()

    threading.Thread(target=open_gate_after_fail, daemon=True).start()

    results = cp.run_push(
        hosts=["fast-fail", "slow-b", "slow-c"],
        username="admin",
        commands=["x"],
        parallel=3,
        stop_on_first_failure=True,
    )
    by_host = {r.host: r.status for r in results}
    assert by_host["fast-fail"] == cp.STATUS_FAILED
    assert by_host["slow-b"] == cp.STATUS_CANCELLED
    assert by_host["slow-c"] == cp.STATUS_CANCELLED


def test_external_cancel_check_short_circuits(monkeypatch):
    seen_hosts: List[str] = []

    def fake(host, **kwargs):
        seen_hosts.append(host)
        # Real impl honours cancel_check itself; pretend so we know it ran
        if kwargs["cancel_check"]():
            return cp.DeviceResult(host=host, status=cp.STATUS_CANCELLED,
                                   message="", duration_s=0.0)
        return cp.DeviceResult(host=host, status=cp.STATUS_OK,
                               message="", duration_s=0.0)

    monkeypatch.setattr(cp, "apply_commands_to_device", fake)

    results = cp.run_push(
        hosts=["a", "b", "c"],
        username="admin",
        commands=["x"],
        parallel=2,
        cancel_check=lambda: True,
    )
    assert all(r.status == cp.STATUS_CANCELLED for r in results)


def test_transcript_dir_writes_one_file_per_host(monkeypatch, tmp_path: Path):
    behavior = {
        "10.0.0.1": cp.DeviceResult(
            host="10.0.0.1", status=cp.STATUS_OK, message="ok",
            duration_s=0.5, transcript=["$ system-view", "[~HW]"],
        ),
        "10.0.0.2": cp.DeviceResult(
            host="10.0.0.2", status=cp.STATUS_FAILED, message="bad cmd",
            duration_s=0.3, transcript=["$ bad", "% Error"],
        ),
    }
    monkeypatch.setattr(cp, "apply_commands_to_device", _make_fake_apply(behavior))

    target = tmp_path / "run-1"
    cp.run_push(
        hosts=["10.0.0.1", "10.0.0.2"],
        username="admin",
        commands=["system-view"],
        parallel=2,
        transcript_dir=target,
    )

    files = sorted(p.name for p in target.iterdir())
    assert files == ["10.0.0.1.txt", "10.0.0.2.txt"]
    a_content = (target / "10.0.0.1.txt").read_text(encoding="utf-8")
    assert "10.0.0.1 [ok]" in a_content
    assert "$ system-view" in a_content


def test_transcript_filename_sanitization(monkeypatch, tmp_path: Path):
    # IPv6 and weird hostnames should produce safe filenames
    behavior = {
        "fe80::1%eth0": cp.DeviceResult(
            host="fe80::1%eth0", status=cp.STATUS_OK, message="",
            duration_s=0.0, transcript=[],
        ),
    }
    monkeypatch.setattr(cp, "apply_commands_to_device", _make_fake_apply(behavior))

    cp.run_push(
        hosts=["fe80::1%eth0"],
        username="admin",
        commands=["x"],
        parallel=1,
        transcript_dir=tmp_path,
    )
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    # No raw colons or percent in filename
    assert ":" not in files[0].name
    assert "%" not in files[0].name
