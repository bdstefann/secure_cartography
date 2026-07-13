# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Secure Cartography v2 — SSH & SNMP-based network discovery and topology mapping: a PyQt6 desktop app over a headless discovery engine, plus a separately packaged FastAPI SNMP proxy. Python ≥3.10, GPL-3.0. `setup.py` version is **2.5.2**.

This repo is the fork `bdstefann/secure_cartography` (remote `origin`), which carries features the upstream `scottpeterman/secure_cartography` (remote `upstream`) does not: Config Push (GUI + CLI), the FastAPI SNMP proxy, vault hardening, Huawei NDP/VRP support, and the Nord theme + login redesign. The two lines diverged from a near-2.5 base rather than the fork simply lagging — a plain `git pull upstream` is **not** a clean upgrade. See the "Merge notes" section under Architecture.

The repo root is `E:\Secure Cartography\secure_cartography\` — the path contains a **space**, always quote it. A stale copy exists at `E:\Secure_Cartography\` (underscore, no `.git/`) — never work there.

`RECAP.md` in the repo root holds the last session's state (pending local commits, manual-test checklist, deferred follow-ups). Read it when resuming work. Git identity is set locally in this repo (`user.email` / `user.name`); if a commit errors with "Author identity unknown", re-set them with `git config user.email …`.

## Commands

Everything runs from the repo root using the project venv (`.venv\`, Python 3.11):

```powershell
cd "E:\Secure Cartography\secure_cartography"
.\.venv\Scripts\python.exe -m pytest tests/ -q                        # full suite
.\.venv\Scripts\python.exe -m pytest tests/test_engine_topology.py -q # one file
.\.venv\Scripts\python.exe -m pytest tests/ -k huawei -q              # by keyword
.\.venv\Scripts\python.exe -m sc2.ui                                  # launch GUI
.\.venv\Scripts\python.exe -u debug_launch.py                         # GUI with global excepthook → debug_crash.log
```

Tests import `sc2.*` directly, so pytest must run from the repo root. No lint config exists (`black`/`ruff` appear only as dev extras in setup.py). Run pytest with `PYTHONIOENCODING=utf-8` — some tests/log lines emit non-ASCII.

**Launching the GUI programmatically (agents):** launching `-m sc2.ui` detached from Git Bash (background or via `timeout`) fails with exit 127 or hangs at `QApplication` — a session/window-station isolation issue, not a code bug. Use PowerShell `Start-Process` with `-RedirectStandardOutput/-RedirectStandardError` to launch it and capture any crash, then confirm it's a real window (a live GUI is ~150 MB+ RSS with `MainWindowTitle = "Secure Cartography"`; ~4 MB means it's still bootstrapping/hung). The `QtWebEngineProcess` helper only spawns after the vault is unlocked and the main window opens.

**Offscreen rendering caveat:** `QT_QPA_PLATFORM=offscreen` renders layout/colors but with tofu (□) text — no font faces. For screenshots with real fonts, render on the native platform and grab widgets that have no `QWebEngineView` (e.g. `LoginDialog`); `MainWindow` embeds the Cytoscape webview and hangs/needs a hard exit when grabbed headless.

Console entry points (setup.py): `sc2` (GUI), `sc2-creds` (vault CLI), `sc2-discover` (discovery CLI). The audit CLI runs as `python -m sc2.scng.audit.cli`. `snmp_proxy/` is its own package (separate pyproject.toml, MIT, entry point `snmp-proxy`) — an async ticket-based SNMP proxy deployed on remote hosts, consumed by the GUI's device-poll proxy mode.

## Architecture

Two layers with a hard boundary:

- `sc2/scng/` — headless engine, no Qt imports: `discovery/` (SNMP collectors + SSH fallback), `creds/` (encrypted vault), `audit/` (config/inventory collection + PDF reports), `tools/` (config-push business logic shared by CLI and GUI), `utils/` (TextFSM auto-engine + `tfsm_templates.db` SQLite)
- `sc2/ui/` — PyQt6 GUI (`widgets/`, `themes.py`); `sc2/export/` — GraphML and Draw.io exporters

### Credential vault is the single gateway to secrets

`CredentialVault` (`sc2/scng/creds/vault.py`) is the only code that touches `encryption.py` and `schema.py`. Every entry point — GUI login, `sc2-creds`, `sc2-discover`, audit — unlocks the vault and passes it down; `CredentialResolver` (`creds/resolver.py`) relays credentials into discovery, testing them in per-credential `priority` order and persisting test results back into the vault. Unlock is rate-limited (exponential cooldown after 5 failures, persisted in `vault_metadata`).

Encryption is **Fernet (AES-128-CBC + HMAC-SHA256) with PBKDF2-HMAC-SHA256 at 480k iterations**. The main README and requirements.txt claim "AES-256-GCM" — that is wrong; `README_Creds.md` is correct. Legacy v1 vaults migrate lazily on first unlock (see `tests/test_vault_pbkdf2_migration.py`).

### Discovery: SNMP-first with SSH fallback

`DiscoveryEngine` (`sc2/scng/discovery/engine.py`) crawls breadth-first. Per-device credential resolution (`_get_working_credential`, ~L548) tries: per-IP cache → /24 subnet preference (learned from prior success) → all SNMP credentials (v2c, then v3) → SSH fallback. The SSH path (`discovery/ssh/`) parses CLI output through TextFSM templates. Vendor support: Cisco, Arista, Juniper, Huawei (NDP via HUAWEI-NDP-MIB; templates in `data/textfsm/huawei/` with an installer script).

Note: credential-testing logic exists both in `CredentialResolver` and in the engine's `_test_snmp_credential`/`_test_ssh_credential` — parallel implementations; the engine's in-memory learning is not persisted.

### Topology map generation — duplicated code, already diverged

The devices→map logic exists **twice**: standalone `sc2/scng/discovery/discovery_to_map.py` and `DiscoveryEngine._generate_topology_map()` (engine.py ~L1380), including duplicate copies of `has_reverse_claim` and interface normalization. They have diverged (the engine has the `peer_is_leaf` exception and richer normalization — Port-channel, Vlan, IOS-XR forms; the script does not). **Until consolidated, any fix must be applied in both places.** See `PROPOSAL_Topology_Map_Consolidation.md` for documented findings and the phased consolidation plan.

Invariants to preserve (regression tests in `tests/test_engine_topology.py`):
- **Bidirectional validation**: a link enters the map only if both ends claim it, unless the peer is a leaf (discovered, zero neighbors) or was never discovered. The dead-`return True` bug that disabled this entirely was fixed in commit `686f3ba`.
- **Two-pass LLDP resolution** (`discovery/snmp/collectors/lldp.py`): `lldpLocPortNum` ≠ SNMP `ifIndex`. Pass 1 walks `lldpLocPortTable` to build the port→name map; pass 2 resolves the remote table through it. Breaking this makes bidirectional validation silently drop real links (mismatched interface names).

### Events: engine → GUI decoupling

The engine emits structured events through `EventEmitter` (`discovery/events.py`). Consumers: `ConsoleEventPrinter`, `JsonEventPrinter` (JSON-lines, for subprocess consumption), and `DiscoverySignalBridge` (Qt signals, throttled). `README_Events.md` and `README_Progress_events.md` document the flow.

### Topology viewer: Python → JS via Base64

`TopologyViewer` (`sc2/ui/widgets/topology_viewer.py`) embeds Cytoscape.js inside `topology_viewer.html` in a QWebEngine view. Topology data crosses as base64-encoded JSON (`loadTopologyB64`); node clicks/drags come back through a QWebChannel bridge. Theme colors are mapped into viewer CSS variables. Platform icons resolve through `platform_icon_map.json` + `sc2/ui/assets/icons_lib/` (584 SVG/JPG device icons).

### Theme system

QSS-first with palette fallback (`README_Style_Guide.md`). Four themes: Cyber, Dark, Light, Nord (`sc2/ui/themes.py`, `ThemeName` enum → `THEMES` dict → `generate_stylesheet`). Everything is data-driven — a new theme in the enum + `THEMES` propagates automatically (selector, settings round-trip in `settings.py`'s `theme_map`, login combo). The theme object is `theme_manager.theme` — **`theme_manager.colors` does not exist** (past crash source, fixed across 4 files).

The **login screen** (`sc2/ui/login.py`) does NOT use the global theme colors. It has its own high-fidelity design-token table `LOGIN_THEMES` (via `login_tokens(theme_name)`) taken verbatim from `../Prototip după poză/design_handoff_login_screen` — login-card-specific hex per theme (e.g. Nord uses a light input pill on a dark card). Edit those tokens, not the global palette, to restyle the login. The TUIASI crest logo lives at `sc2/ui/assets/tuiasi_logo.png` in a white 188×108 slot on all themes.

### Windows console encoding (crash source)

The app's debug code prints emoji/checkmarks (✓, 🚫, …). Windows consoles default to **cp1252**, which cannot encode them → `UnicodeEncodeError`. This crashed the app right after vault unlock (in `TopologyPreviewPanel`'s debug print). Two guards now exist: `sc2/ui/__main__.py` reconfigures `stdout`/`stderr` to UTF-8 (`errors="replace"`) at startup, and `topology_preview_panel.py`'s `debug_print` falls back to ascii-replace. **A `print()` in GUI-path code must never be able to crash the app** — keep it non-ASCII-safe.

### Merge notes (fork ↔ upstream)

`upstream` is configured. Upstream ships occasional large squash commits that mix real changes with junk (`.DS_Store`, `sc2/scng.zip`, `client_original.py` backups, screenshots, a root `pyproject.toml` that would override the fork's `setup.py`). When merging: reject that junk, keep the fork's `README.md` (superset), and for the **binary** `tfsm_templates.db` conflict rebuild the **union** — upstream's NX-OS templates + the fork's Huawei templates (currently 299 total: 83 `cisco_nxos_*` + 3 `huawei_vrp_*`). See `PROPOSAL_Topology_Map_Consolidation.md` and the `41dce16` merge commit message for the resolution pattern.

The engine's topology builder has a **ghost-node filter** (`engine.py` ~L1720/L1834) that drops peer names <2 chars (NX-OS command-echo artifacts). Topology regression tests must therefore use realistic device names (`R1`/`R2`, not single-char `A`/`B`), or links get silently dropped and tests fail for the wrong reason.

## Pitfalls

- The repo root accumulates uncommitted debug artifacts (`debug_*.log`, `sc2_run.*`, `*.png` screenshots, `debug_launch.py`/`debug_open_dialog.py` crash-isolation wrappers). Don't commit them; `debug_launch.py` is the tool of choice for diagnosing Qt-slot crashes.
- `scripts/huawei_bulk_snmp_config.py` is a thin CLI wrapper over `sc2.scng.tools.config_pusher` with a byte-identical `--dry-run` output contract — don't change its output format.
- TextFSM templates live in two DBs (`sc2/scng/utils/tfsm_templates.db` packaged, `tfsm_backup/` backup); Huawei template changes must be installed into both (`data/textfsm/huawei/install_templates.py`). The packaged DB is a binary artifact and a union of upstream + fork templates — never resolve a merge conflict on it by picking one side (see Merge notes).
- `sc2/scng/audit/collector.py` calls `enable_emulation("ip_lookup.json")` at **module top level**, so it runs at import time and raises `FileNotFoundError` when the file is absent — importing the audit module bare crashes. Pre-existing (present in both fork and upstream), not yet fixed.
