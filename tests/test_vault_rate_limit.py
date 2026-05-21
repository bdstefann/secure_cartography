"""
Tests for vault unlock rate limiting and the removal of the --password CLI flag.

Covers:
- successful unlock resets failure counters
- N "free" failures (below threshold) raise InvalidPassword without cooldown
- failures past the threshold arm an exponential cooldown that raises VaultLockedOut
- cooldown persists across new CredentialVault instances on the same DB
- correct unlock after a lockout expires (without waiting in real time)
- argparse rejects the removed `--password` / `-p` global flag
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sc2.scng.creds.encryption import InvalidPassword
from sc2.scng.creds.vault import (
    CredentialVault,
    VaultLockedOut,
    UNLOCK_FAIL_THRESHOLD,
    UNLOCK_LOCKOUT_BASE_SECONDS,
)


PASSWORD = "correct-horse-battery-staple"
WRONG = "wrong"


@pytest.fixture
def vault(tmp_path: Path) -> CredentialVault:
    db = tmp_path / "v.db"
    v = CredentialVault(db)
    v.initialize(PASSWORD)
    v.lock()
    return v


def _fail_count(vault: CredentialVault) -> int:
    raw = vault._db.get_vault_metadata("unlock_fail_count")
    return int(raw) if raw else 0


def test_unlock_success_resets_counters(vault: CredentialVault):
    # Pre-fill some failures
    for _ in range(2):
        with pytest.raises(InvalidPassword):
            vault.unlock(WRONG)
    assert _fail_count(vault) == 2

    # Correct password resets counters
    vault.unlock(PASSWORD)
    assert _fail_count(vault) == 0
    assert vault._db.get_vault_metadata("unlock_locked_until") == ""
    vault.lock()


def test_failures_below_threshold_do_not_lock(vault: CredentialVault):
    for _ in range(UNLOCK_FAIL_THRESHOLD):
        with pytest.raises(InvalidPassword):
            vault.unlock(WRONG)
    # Right at the threshold the next attempt should still get InvalidPassword,
    # but the one *after* that should be locked.
    assert _fail_count(vault) == UNLOCK_FAIL_THRESHOLD


def test_failure_past_threshold_arms_lockout(vault: CredentialVault):
    for _ in range(UNLOCK_FAIL_THRESHOLD + 1):
        with pytest.raises(InvalidPassword):
            vault.unlock(WRONG)
    # Next attempt — even with correct password — must hit the lockout
    with pytest.raises(VaultLockedOut) as exc:
        vault.unlock(PASSWORD)
    assert exc.value.fail_count == UNLOCK_FAIL_THRESHOLD + 1
    assert exc.value.seconds_remaining > 0
    assert exc.value.seconds_remaining <= UNLOCK_LOCKOUT_BASE_SECONDS


def test_lockout_persists_across_vault_instances(tmp_path: Path):
    db = tmp_path / "v.db"
    v = CredentialVault(db)
    v.initialize(PASSWORD)
    v.lock()

    # Trip the lockout
    for _ in range(UNLOCK_FAIL_THRESHOLD + 1):
        with pytest.raises(InvalidPassword):
            v.unlock(WRONG)

    # New CredentialVault on the same DB should still be locked
    v2 = CredentialVault(db)
    with pytest.raises(VaultLockedOut):
        v2.unlock(PASSWORD)


def test_correct_password_after_expired_lockout(vault: CredentialVault, monkeypatch):
    # Force a lockout, then expire it by rewinding the locked_until timestamp.
    for _ in range(UNLOCK_FAIL_THRESHOLD + 1):
        with pytest.raises(InvalidPassword):
            vault.unlock(WRONG)
    with pytest.raises(VaultLockedOut):
        vault.unlock(PASSWORD)

    past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    vault._db.set_vault_metadata("unlock_locked_until", past)

    vault.unlock(PASSWORD)
    assert _fail_count(vault) == 0


def test_cli_rejects_removed_password_flag(tmp_path: Path):
    # `--password` / `-p` should be gone from the global parser entirely
    proc = subprocess.run(
        [sys.executable, "-m", "sc2.scng.creds", "--password", "x", "list"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    combined = (proc.stderr + proc.stdout).lower()
    assert "unrecognized" in combined or "unknown" in combined or "invalid" in combined


def test_vault_locked_out_message_contains_seconds():
    exc = VaultLockedOut(seconds_remaining=42, fail_count=7)
    assert "42s" in str(exc)
    assert "7" in str(exc)
