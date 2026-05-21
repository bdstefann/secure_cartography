"""
Tests for sc2.scng.tools.history_db.ConfigHistoryDB.

Exercise record/list/get/search/delete on `history`, and save/update/
list/get/delete on `templates`, including duplicate-label and missing-row
error paths and the vendor filter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sc2.scng.tools.history_db import (
    ConfigHistoryDB,
    DuplicateTemplate,
    TemplateNotFound,
)


@pytest.fixture
def db(tmp_path: Path) -> ConfigHistoryDB:
    return ConfigHistoryDB(tmp_path / "h.db")


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------

def test_record_run_returns_id_and_can_be_retrieved(db: ConfigHistoryDB):
    run_id = db.record_run(
        command_block="system-view\nquit",
        hosts=["10.0.0.1", "10.0.0.2"],
        ok_count=2,
        fail_count=0,
        skip_count=0,
        duration_s=1.5,
        label="snmp view rollout",
        transcript_dir="/tmp/run-1",
    )
    assert run_id > 0
    entry = db.get_run(run_id)
    assert entry is not None
    assert entry.hosts == ["10.0.0.1", "10.0.0.2"]
    assert entry.label == "snmp view rollout"
    assert entry.transcript_dir == "/tmp/run-1"
    assert entry.ok_count == 2


def test_list_runs_orders_newest_first(db: ConfigHistoryDB):
    db.record_run(
        command_block="cmd a", hosts=["1.1.1.1"],
        ok_count=1, fail_count=0, skip_count=0, duration_s=0.1,
        timestamp="2026-05-19T10:00:00+00:00",
    )
    db.record_run(
        command_block="cmd b", hosts=["1.1.1.2"],
        ok_count=1, fail_count=0, skip_count=0, duration_s=0.1,
        timestamp="2026-05-20T10:00:00+00:00",
    )
    db.record_run(
        command_block="cmd c", hosts=["1.1.1.3"],
        ok_count=1, fail_count=0, skip_count=0, duration_s=0.1,
        timestamp="2026-05-18T10:00:00+00:00",
    )
    runs = db.list_runs()
    assert [r.command_block for r in runs] == ["cmd b", "cmd a", "cmd c"]


def test_list_runs_respects_limit(db: ConfigHistoryDB):
    for i in range(5):
        db.record_run(
            command_block=f"cmd {i}", hosts=["1.1.1.1"],
            ok_count=1, fail_count=0, skip_count=0, duration_s=0.1,
        )
    assert len(db.list_runs(limit=3)) == 3


def test_search_runs_matches_label_and_block_case_insensitively(db: ConfigHistoryDB):
    db.record_run(
        command_block="snmp-agent mib-view ...", hosts=["1.1.1.1"],
        ok_count=1, fail_count=0, skip_count=0, duration_s=0.1,
        label="SNMP view rollout",
    )
    db.record_run(
        command_block="undo dhcp enable", hosts=["1.1.1.2"],
        ok_count=1, fail_count=0, skip_count=0, duration_s=0.1,
        label="cleanup",
    )
    by_label = db.search_runs("snmp")
    assert len(by_label) == 1
    assert by_label[0].label == "SNMP view rollout"

    by_block = db.search_runs("DHCP")
    assert len(by_block) == 1
    assert "dhcp" in by_block[0].command_block.lower()


def test_get_run_returns_none_for_unknown_id(db: ConfigHistoryDB):
    assert db.get_run(9999) is None


def test_delete_run_removes_it(db: ConfigHistoryDB):
    run_id = db.record_run(
        command_block="x", hosts=["1.1.1.1"],
        ok_count=1, fail_count=0, skip_count=0, duration_s=0.1,
    )
    db.delete_run(run_id)
    assert db.get_run(run_id) is None


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

def test_save_template_returns_id_and_get_returns_it(db: ConfigHistoryDB):
    tid = db.save_template(
        label="hw-snmp-view",
        command_block="system-view\nsnmp-agent mib-view included v iso\nquit",
        vendor="huawei",
    )
    assert tid > 0
    t = db.get_template("hw-snmp-view")
    assert t is not None
    assert t.vendor == "huawei"
    assert "snmp-agent" in t.command_block


def test_save_template_default_vendor_is_generic(db: ConfigHistoryDB):
    db.save_template(label="x", command_block="y")
    assert db.get_template("x").vendor == "generic"


def test_save_template_duplicate_label_raises(db: ConfigHistoryDB):
    db.save_template(label="dup", command_block="cmd")
    with pytest.raises(DuplicateTemplate):
        db.save_template(label="dup", command_block="other")


def test_update_template_changes_block_and_vendor(db: ConfigHistoryDB):
    db.save_template(label="x", command_block="old", vendor="generic")
    db.update_template("x", command_block="new", vendor="cisco")
    t = db.get_template("x")
    assert t.command_block == "new"
    assert t.vendor == "cisco"


def test_update_template_missing_raises(db: ConfigHistoryDB):
    with pytest.raises(TemplateNotFound):
        db.update_template("nope", command_block="x")


def test_update_template_with_no_changes_is_noop(db: ConfigHistoryDB):
    # Don't raise if both kwargs are None
    db.save_template(label="x", command_block="cmd")
    db.update_template("x")  # no-op, no exception


def test_list_templates_filters_by_vendor(db: ConfigHistoryDB):
    db.save_template(label="a", command_block="x", vendor="huawei")
    db.save_template(label="b", command_block="y", vendor="cisco")
    db.save_template(label="c", command_block="z", vendor="huawei")
    huawei = db.list_templates(vendor="huawei")
    assert sorted(t.label for t in huawei) == ["a", "c"]
    cisco = db.list_templates(vendor="cisco")
    assert [t.label for t in cisco] == ["b"]


def test_list_templates_without_filter_returns_all(db: ConfigHistoryDB):
    db.save_template(label="a", command_block="x", vendor="huawei")
    db.save_template(label="b", command_block="y", vendor="cisco")
    assert len(db.list_templates()) == 2


def test_delete_template_removes_it(db: ConfigHistoryDB):
    db.save_template(label="x", command_block="cmd")
    db.delete_template("x")
    assert db.get_template("x") is None


def test_delete_template_missing_raises(db: ConfigHistoryDB):
    with pytest.raises(TemplateNotFound):
        db.delete_template("nope")


# --------------------------------------------------------------------------
# Default DB path
# --------------------------------------------------------------------------

def test_default_db_path_is_under_home_dot_scng(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    db = ConfigHistoryDB()
    assert db.db_path == tmp_path / ".scng" / "config_history.db"
    assert db.db_path.exists()
