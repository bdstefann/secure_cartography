"""
Engine tests:
- _normalize_interface: short forms across Cisco/Arista/Juniper conventions.
- _generate_topology_map / has_reverse_claim: bidirectional link validation
  (regression for the dead `return True` bug — both sides must claim a link
  before it appears in the map, unless the peer is a leaf or undiscovered).
"""

from __future__ import annotations

import importlib
import sys

import pytest


# -----------------------------------------------------------------------------
# Lazy import so test collection works even if pysnmp pulls heavy deps.
# -----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine_mod():
    return importlib.import_module("sc2.scng.discovery.engine")


@pytest.fixture(scope="module")
def models_mod():
    return importlib.import_module("sc2.scng.discovery.models")


@pytest.fixture
def engine(engine_mod):
    # No vault, no network — pure CPU-side methods are what we test.
    return engine_mod.DiscoveryEngine()


# -----------------------------------------------------------------------------
# _normalize_interface
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    # Cisco long-form -> short-form
    ("GigabitEthernet0/1", "Gi0/1"),
    ("TenGigabitEthernet1/1", "Te1/1"),
    ("FortyGigabitEthernet1/1/1", "Fo1/1/1"),
    ("HundredGigE1/2", "Hu1/2"),
    ("FastEthernet0/24", "Fa0/24"),
    # "Ethernet" must lose to longer prefixes; standalone -> "Eth"
    ("Ethernet1/3", "Eth1/3"),
    # Port-channel variants (case-insensitive)
    ("Port-channel10", "Po10"),
    ("Port-Channel10", "Po10"),
    ("port-channel10", "Po10"),
    # Vlan variants
    ("Vlan666", "Vl666"),
    ("VLAN-666", "Vl666"),
    # Loopback / Null
    ("Loopback0", "Lo0"),
    ("Null0", "Nu0"),
    # Arista LLDP often shortens Ethernet1 -> Et1; the engine re-lengthens
    # the short Et<digit> form so it lines up with the canonical Eth1.
    ("Et1", "Eth1"),
    ("Et49/1", "Eth49/1"),
    # Juniper: strip the implicit .0 unit, keep non-zero units
    ("xe-0/0/0.0", "xe-0/0/0"),
    ("ge-0/0/3.0", "ge-0/0/3"),
    ("xe-0/0/0.123", "xe-0/0/0.123"),
    # Pass-through: empty + whitespace
    ("", ""),
    ("  Gi0/2  ", "Gi0/2"),
])
def test_normalize_interface(engine, raw, expected):
    assert engine._normalize_interface(raw) == expected


# -----------------------------------------------------------------------------
# Bidirectional link validation (has_reverse_claim regression)
# -----------------------------------------------------------------------------

def _make_device(models, *, hostname, ip, sys_name=None, neighbors=()):
    dev = models.Device(hostname=hostname, ip_address=ip, sys_name=sys_name or hostname)
    dev.neighbors = list(neighbors)
    return dev


def _lldp(models, *, local_if, peer, remote_if, peer_ip=None):
    return models.Neighbor(
        local_interface=local_if,
        remote_device=peer,
        remote_interface=remote_if,
        remote_ip=peer_ip,
        protocol=models.NeighborProtocol.LLDP,
    )


def test_bidirectional_link_is_kept(engine, models_mod):
    a = _make_device(
        models_mod, hostname="A", ip="10.0.0.1",
        neighbors=[_lldp(models_mod, local_if="Gi0/1", peer="B", remote_if="Gi0/2")],
    )
    b = _make_device(
        models_mod, hostname="B", ip="10.0.0.2",
        neighbors=[_lldp(models_mod, local_if="Gi0/2", peer="A", remote_if="Gi0/1")],
    )
    topo = engine._generate_topology_map([a, b])

    assert "B" in topo["A"]["peers"]
    assert ["Gi0/1", "Gi0/2"] in topo["A"]["peers"]["B"]["connections"]
    assert "A" in topo["B"]["peers"]
    assert ["Gi0/2", "Gi0/1"] in topo["B"]["peers"]["A"]["connections"]


def test_unidirectional_link_is_dropped_when_peer_was_discovered(engine, models_mod):
    # A claims a link to B on Gi0/1 <-> Gi0/2.
    # B was discovered (has neighbors) but does NOT claim A back -> drop it.
    a = _make_device(
        models_mod, hostname="A", ip="10.0.0.1",
        neighbors=[_lldp(models_mod, local_if="Gi0/1", peer="B", remote_if="Gi0/2")],
    )
    b = _make_device(
        models_mod, hostname="B", ip="10.0.0.2",
        neighbors=[_lldp(models_mod, local_if="Gi0/9", peer="C", remote_if="Gi0/9")],
    )
    topo = engine._generate_topology_map([a, b])
    assert "B" not in topo["A"]["peers"], (
        "A's claim of B should have been dropped — B was discovered with neighbors "
        "but never claimed A back. If this fails, has_reverse_claim is being "
        "short-circuited (the bug fixed in 686f3ba)."
    )


def test_leaf_peer_keeps_unidirectional_link(engine, models_mod):
    # B was discovered but has NO neighbors (capability-less leaf).
    # A's claim to B is trusted because the leaf can't reciprocate.
    a = _make_device(
        models_mod, hostname="A", ip="10.0.0.1",
        neighbors=[_lldp(models_mod, local_if="Gi0/1", peer="B", remote_if="Gi0/2")],
    )
    b = _make_device(models_mod, hostname="B", ip="10.0.0.2", neighbors=[])
    topo = engine._generate_topology_map([a, b])
    assert "B" in topo["A"]["peers"]
    assert ["Gi0/1", "Gi0/2"] in topo["A"]["peers"]["B"]["connections"]


def test_undiscovered_peer_keeps_link(engine, models_mod):
    # B was never discovered (no Device for it). Trust A's claim.
    a = _make_device(
        models_mod, hostname="A", ip="10.0.0.1",
        neighbors=[_lldp(models_mod, local_if="Gi0/1", peer="ghost", remote_if="Gi0/2")],
    )
    topo = engine._generate_topology_map([a])
    assert "ghost" in topo["A"]["peers"]


def test_normalized_interface_matching_lets_bidirectional_link_pass(engine, models_mod):
    # A reports "GigabitEthernet0/1" and B reports "Gi0/1" — after normalization
    # both sides should agree and the link should be kept.
    a = _make_device(
        models_mod, hostname="A", ip="10.0.0.1",
        neighbors=[_lldp(models_mod, local_if="GigabitEthernet0/1", peer="B",
                         remote_if="Gi0/2")],
    )
    b = _make_device(
        models_mod, hostname="B", ip="10.0.0.2",
        neighbors=[_lldp(models_mod, local_if="Gi0/2", peer="A",
                         remote_if="GigabitEthernet0/1")],
    )
    topo = engine._generate_topology_map([a, b])
    assert "B" in topo["A"]["peers"]
    # Stored using normalized short form
    assert ["Gi0/1", "Gi0/2"] in topo["A"]["peers"]["B"]["connections"]
