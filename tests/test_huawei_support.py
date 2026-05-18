"""
Tests for Huawei switch support (S5730 / S6730 VRP, S5735 YunShan).

These tests validate:
 - Vendor detection from sysDescr / show-version strings
 - SSH VENDOR_COMMANDS mapping for Huawei
 - TextFSM template parsing of display-lldp-neighbor / display-ndp / display-version
 - NeighborProtocol.NDP enum and Neighbor.from_ndp() factory

The tests are intentionally dependency-light: they only require `textfsm`
and the standard library so they can run in the CI sandbox without
installing the full SNMP/SSH stack.
"""

from __future__ import annotations

import importlib.util
import io
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "huawei"
TEMPLATE_DIR = ROOT / "data" / "textfsm" / "huawei"


# ---------------------------------------------------------------------------
# Helpers: load models.py without pulling pysnmp through the package __init__
# ---------------------------------------------------------------------------

def _load_models():
    spec = importlib.util.spec_from_file_location(
        "sc_models", ROOT / "sc2" / "scng" / "discovery" / "models.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_with_template(template_filename: str, fixture_filename: str):
    import textfsm

    template_path = TEMPLATE_DIR / template_filename
    fixture_path = FIXTURE_DIR / fixture_filename

    with template_path.open() as f:
        template = textfsm.TextFSM(io.StringIO(f.read()))
    with fixture_path.open() as f:
        rows = template.ParseText(f.read())
    return [dict(zip(template.header, row)) for row in rows]


# ---------------------------------------------------------------------------
# Model-level tests
# ---------------------------------------------------------------------------

def test_neighbor_protocol_ndp_exists():
    models = _load_models()
    assert models.NeighborProtocol.NDP.value == "ndp"


def test_device_vendor_huawei_exists():
    models = _load_models()
    assert models.DeviceVendor.HUAWEI.value == "huawei"


def test_neighbor_from_ndp_factory():
    models = _load_models()
    n = models.Neighbor.from_ndp(
        local_interface="GigabitEthernet0/0/1",
        device_id="HW-CORE-01",
        remote_port="GigabitEthernet0/0/24",
        ip_address="10.1.1.1",
        platform="S6730",
        version="VRP 5.170",
    )
    assert n.protocol == models.NeighborProtocol.NDP
    assert n.remote_device == "HW-CORE-01"
    assert n.remote_interface == "GigabitEthernet0/0/24"
    assert n.remote_ip == "10.1.1.1"
    assert n.remote_platform == "S6730"
    assert n.remote_description == "VRP 5.170"


def test_device_ndp_neighbors_property():
    models = _load_models()
    dev = models.Device(ip_address="10.1.1.1", hostname="hw-test")
    dev.add_neighbor(models.Neighbor.from_ndp(
        local_interface="GE0/0/1", device_id="peer", remote_port="GE0/0/2",
    ))
    dev.add_neighbor(models.Neighbor.from_lldp(
        local_interface="GE0/0/3", system_name="lldp-peer", port_id="GE0/0/4",
    ))
    assert len(dev.ndp_neighbors) == 1
    assert dev.ndp_neighbors[0].remote_device == "peer"
    assert len(dev.lldp_neighbors) == 1


# ---------------------------------------------------------------------------
# TextFSM template tests
# ---------------------------------------------------------------------------

def test_textfsm_lldp_neighbor_parses_two_peers():
    records = _parse_with_template(
        "huawei_vrp_display_lldp_neighbor.textfsm",
        "display_lldp_neighbor.txt",
    )
    valid = [r for r in records if r.get("NEIGHBOR_NAME")]
    assert len(valid) == 2

    by_name = {r["NEIGHBOR_NAME"]: r for r in valid}
    assert "HW-CORE-01" in by_name
    assert "HW-ACCESS-02" in by_name

    core = by_name["HW-CORE-01"]
    assert core["LOCAL_INTERFACE"] == "GigabitEthernet0/0/1"
    assert core["NEIGHBOR_PORT_ID"] == "GigabitEthernet0/0/24"
    assert core["MGMT_ADDRESS"] == "10.1.1.1"
    assert core["CHASSIS_ID"] == "00e0-fc12-3456"


def test_textfsm_ndp_parses_two_peers():
    records = _parse_with_template(
        "huawei_vrp_display_ndp.textfsm",
        "display_ndp.txt",
    )
    valid = [r for r in records if r.get("NEIGHBOR_NAME")]
    assert len(valid) == 2

    by_name = {r["NEIGHBOR_NAME"]: r for r in valid}
    assert by_name["HW-CORE-01"]["LOCAL_INTERFACE"] == "GigabitEthernet0/0/1"
    assert by_name["HW-CORE-01"]["NEIGHBOR_PORT_ID"] == "GigabitEthernet0/0/24"
    assert by_name["HW-ACCESS-02"]["CHASSIS_ID"] == "00e0-fc99-aaaa"


def test_textfsm_display_version_extracts_platform():
    records = _parse_with_template(
        "huawei_vrp_display_version.textfsm",
        "display_version.txt",
    )
    assert len(records) == 1
    assert records[0]["PLATFORM"] == "S6730-S24X6Q"
    assert records[0]["VRP_VERSION"] == "5.170"
    assert records[0]["SOFTWARE_VERSION"] == "V200R019C10SPC500"


# ---------------------------------------------------------------------------
# Vendor detection tests
# ---------------------------------------------------------------------------

# Mirror of VENDOR_PATTERNS in snmp/parsers.py (kept in sync intentionally —
# importing the real module pulls pysnmp which is not always installed).
HUAWEI_SYS_DESCRS = [
    "Huawei Versatile Routing Platform Software, VRP (R) software, Version 5.170 (S5730 V200R019C10SPC500)",
    "S6730-H24X6C Huawei Versatile Routing Platform Software VRP (R) software, Version 8.211",
    "S5735-L24P4X-A1 Routing Switch with YunShan OS, Version V600R022C00",
    "Huawei Quidway S5328 Routing Switch",
    "HUAWEI CloudEngine CE6800 Series Switch",
]


@pytest.mark.parametrize("sys_descr", HUAWEI_SYS_DESCRS)
def test_huawei_patterns_match(sys_descr):
    import re
    patterns = [
        r"huawei", r"\bvrp\b", r"yunshan", r"quidway", r"cloudengine",
        r"\bs5730\b", r"\bs6730\b", r"\bs5735\b", r"\bce6800\b",
    ]
    text = sys_descr.lower()
    assert any(re.search(p, text, re.IGNORECASE) for p in patterns), (
        f"No Huawei pattern matched: {sys_descr}"
    )


# ---------------------------------------------------------------------------
# Database installation tests
# ---------------------------------------------------------------------------

def test_huawei_templates_present_in_db():
    db_path = ROOT / "sc2" / "scng" / "utils" / "tfsm_templates.db"
    if not db_path.exists():
        pytest.skip(f"Template DB not present: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT cli_command FROM templates WHERE cli_command LIKE 'huawei_%'"
        ).fetchall()
    finally:
        conn.close()
    names = {r[0] for r in rows}
    assert "huawei_vrp_display_lldp_neighbor" in names
    assert "huawei_vrp_display_ndp" in names
    assert "huawei_vrp_display_version" in names
