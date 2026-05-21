"""
Tests for PBKDF2 password-hash iteration alignment and legacy vault migration.

Covers:
- new vaults record `password_hash_iterations` = PASSWORD_HASH_ITERATIONS
- a synthesized legacy vault (hash generated with 100k, no iterations key)
  unlocks correctly
- after a successful unlock, a legacy vault is transparently re-hashed with
  the current iteration count and the metadata key is added
- subsequent unlocks on the same vault use the new hash and iterations
- change_password rewrites the iteration metadata
- schema version is bumped to 2 on new init and on lazy migrate
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from sc2.scng.creds.encryption import (
    PASSWORD_HASH_ITERATIONS,
    LEGACY_PASSWORD_HASH_ITERATIONS,
    compute_password_hash,
)
from sc2.scng.creds.schema import (
    SCHEMA_VERSION,
    get_schema_version,
    migrate_schema,
)
from sc2.scng.creds.vault import CredentialVault


PASSWORD = "correct-horse-battery-staple"


def _make_legacy_vault(tmp_path: Path) -> CredentialVault:
    """Build a vault, then rewrite its password hash with the legacy iteration
    count and remove the iterations metadata key, so the on-disk shape matches
    a vault created before iteration alignment."""
    db = tmp_path / "legacy.db"
    v = CredentialVault(db)
    v.initialize(PASSWORD)
    v.lock()

    salt = base64.b64decode(v._db.get_vault_metadata("salt"))
    legacy_hash = compute_password_hash(PASSWORD, salt, LEGACY_PASSWORD_HASH_ITERATIONS)
    v._db.set_vault_metadata("password_hash", base64.b64encode(legacy_hash).decode())

    # Simulate "v1 vault" — no iterations key recorded.
    conn = v._db.connection()
    try:
        conn.execute(
            "DELETE FROM vault_metadata WHERE key = 'password_hash_iterations'"
        )
        conn.commit()
    finally:
        conn.close()
    return v


def test_new_vault_records_current_iteration_count(tmp_path: Path):
    v = CredentialVault(tmp_path / "fresh.db")
    v.initialize(PASSWORD)
    v.lock()
    assert (
        int(v._db.get_vault_metadata("password_hash_iterations"))
        == PASSWORD_HASH_ITERATIONS
    )


def test_legacy_vault_unlocks(tmp_path: Path):
    v = _make_legacy_vault(tmp_path)
    assert v._db.get_vault_metadata("password_hash_iterations") is None
    v.unlock(PASSWORD)
    v.lock()


def test_legacy_vault_is_rehashed_on_unlock(tmp_path: Path):
    v = _make_legacy_vault(tmp_path)
    salt_before = v._db.get_vault_metadata("salt")
    hash_before = v._db.get_vault_metadata("password_hash")

    v.unlock(PASSWORD)
    v.lock()

    iters_after = int(v._db.get_vault_metadata("password_hash_iterations"))
    salt_after = v._db.get_vault_metadata("salt")
    hash_after = v._db.get_vault_metadata("password_hash")

    assert iters_after == PASSWORD_HASH_ITERATIONS
    # Salt is preserved (we only re-hashed, no reinit).
    assert salt_after == salt_before
    # Hash changed because the iteration count changed.
    assert hash_after != hash_before


def test_legacy_vault_second_unlock_uses_new_iterations(tmp_path: Path):
    v = _make_legacy_vault(tmp_path)
    v.unlock(PASSWORD)
    v.lock()

    # Now the vault should look v2-shaped; unlock again with the same password
    # to prove the upgraded hash works under the new iteration count.
    v2 = CredentialVault(v.db_path)
    v2.unlock(PASSWORD)
    assert (
        int(v2._db.get_vault_metadata("password_hash_iterations"))
        == PASSWORD_HASH_ITERATIONS
    )


def test_change_password_writes_current_iterations(tmp_path: Path):
    v = CredentialVault(tmp_path / "c.db")
    v.initialize(PASSWORD)
    v.change_password(PASSWORD, "another-strong-password-1")
    assert (
        int(v._db.get_vault_metadata("password_hash_iterations"))
        == PASSWORD_HASH_ITERATIONS
    )


def test_new_db_schema_version_is_two(tmp_path: Path):
    v = CredentialVault(tmp_path / "n.db")
    v.initialize(PASSWORD)
    conn = v._db.connection()
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION == 2
    finally:
        conn.close()


def test_migrate_schema_v1_to_v2_bumps_version_tag(tmp_path: Path):
    v = CredentialVault(tmp_path / "m.db")
    v.initialize(PASSWORD)
    conn = v._db.connection()
    try:
        # Rewind the recorded version, then run migration
        conn.execute(
            "UPDATE vault_metadata SET value = '1' WHERE key = 'schema_version'"
        )
        conn.commit()
        assert get_schema_version(conn) == 1
        migrate_schema(conn, 1)
        assert get_schema_version(conn) == 2
    finally:
        conn.close()
