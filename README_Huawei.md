# Huawei Switch Support

Secure Cartography v2 supports neighbor discovery and topology mapping on
Huawei switches running VRP V5 / V8 (S5720, S5730, S6720, S6730, S7700,
S9300, AR-series) and YunShan-based devices (S5735, recent CloudEngine
CE-series).

## What gets discovered

| Vector | Protocol | Source |
|---|---|---|
| SNMP fingerprint | sysDescr / sysName / sysObjectID | `SNMPv2-MIB` |
| Interfaces | ifTable + ifXTable | `IF-MIB` |
| ARP table | ipNetToMedia | `IP-MIB` |
| LLDP neighbors | Standard LLDP-MIB (`1.0.8802.1.1.2`) | Vendor-neutral |
| NDP neighbors | HUAWEI-NDP-MIB (`1.3.6.1.4.1.2011.5.25.106`) | Huawei proprietary |

SSH fallback runs the same commands a network engineer would:

```text
display version
display interface description
display lldp neighbor
display ndp
```

Pagination is auto-disabled via `screen-length 0 temporary` (already part
of the global pagination-shotgun in `ssh/client.py`).

## Models confirmed in design

- **S5730** (VRP V5/V8) — `display lldp neighbor`, `display ndp`
- **S6730** (VRP V5/V8) — `display lldp neighbor`, `display ndp`
- **S5735** (YunShan / VRP V8 modern) — `display lldp neighbor` (NDP optional)

Other supported families share the same CLI / MIBs:
**S5720, S6720, S7700, S9300, CloudEngine CE6800/CE12800, AR-series**.

## Required device configuration

The switch must answer at least one of:

```text
# SNMPv2c (read-only is enough for discovery)
[HW] snmp-agent
[HW] snmp-agent community read cipher <community>
[HW] snmp-agent sys-info version v2c

# LLDP (vendor-neutral - preferred)
[HW] lldp enable

# NDP (Huawei proprietary, optional)
[HW] ndp enable
```

For SSH-based discovery, add the user used by Secure Cartography to the
local AAA database with `service-type ssh` and at least `level 1`
read-only access (enough to run `display` commands).

## How vendor detection works

The SNMP collector reads `sysDescr` (`1.3.6.1.2.1.1.1.0`) and matches it
against case-insensitive patterns including `huawei`, `vrp`, `yunshan`,
`quidway`, `cloudengine`, and the specific model codes (`s5730`, `s6730`,
`s5735`, `ce6800`, etc.). See `sc2/scng/discovery/snmp/parsers.py`,
`VENDOR_PATTERNS`.

The SSH collector additionally inspects the output of `display version`
when no vendor hint is provided. See `sc2/scng/discovery/ssh/collector.py`,
`detect_vendor_from_output`.

## TextFSM templates

The following templates are installed in `tfsm_templates.db`:

| Template name | Command parsed |
|---|---|
| `huawei_vrp_display_lldp_neighbor` | `display lldp neighbor` |
| `huawei_vrp_display_ndp` | `display ndp` |
| `huawei_vrp_display_version` | `display version` |

Source `.textfsm` files live under `data/textfsm/huawei/` so they can be
reviewed or edited. After editing, reinstall them with:

```bash
python data/textfsm/huawei/install_templates.py
```

The script is idempotent — it deletes any prior `huawei_*` rows before
inserting the current versions.

## Running unit tests

```bash
pip install textfsm pytest
python -m pytest tests/test_huawei_support.py -v
```

This validates the templates against the sample outputs under
`data/fixtures/huawei/` without needing a real device.

## Validating against a real switch

A quick end-to-end check from a host with SSH reachability:

```bash
sc2-discover --seed 10.1.1.1 \
             --vendor huawei \
             --ssh-username admin \
             --max-depth 1 \
             --output ./hw-test-map
```

If LLDP returns nothing, NDP is automatically attempted (Huawei devices
that use `ndp` without `lldp`). The discovery JSON will include
`ndp.json` alongside `lldp.json` under each device's output folder.

### Common issues

- **`display lldp neighbor` returns "LLDP is not enabled globally"** — run
  `lldp enable` in system view.
- **SNMP walk on HUAWEI-NDP-MIB returns nothing** — your VRP version may
  not expose the proprietary MIB. The SSH `display ndp` fallback will
  still work as long as the CLI command itself returns data.
- **YunShan-based S5735 lacks NDP** — YunShan dropped NDP in favour of
  LLDP. The collector silently skips NDP when the table is empty.

## Field mapping (HW NDP → Neighbor dataclass)

| TextFSM field | `Neighbor` attribute |
|---|---|
| `LOCAL_INTERFACE` | `local_interface` |
| `NEIGHBOR_NAME` | `remote_device` |
| `NEIGHBOR_PORT_ID` | `remote_interface` |
| `CHASSIS_ID` | `chassis_id` (not set for NDP; mapped where present) |
| `VERSION` | `remote_description` |
| `PLATFORM` | `remote_platform` |
| `protocol` | `NeighborProtocol.NDP` |

## What is *not* yet implemented

- Stack member discovery via HUAWEI-STACK-MIB
- VRF-aware ARP collection (single global VRF for now)
- Push of CLI changes — discovery only, read-only access is enough
