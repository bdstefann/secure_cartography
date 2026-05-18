"""
Install Huawei TextFSM templates into tfsm_templates.db.

Idempotent: deletes existing huawei_* rows before inserting fresh ones.

Usage:
    python data/textfsm/huawei/install_templates.py [path/to/tfsm_templates.db]

If no path is given, both the bundled fallback location
(`tfsm_backup/tfsm_templates.db`) and the runtime location
(`sc2/scng/utils/tfsm_templates.db`) are updated when present.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = Path(__file__).resolve().parent

TEMPLATES = [
    ("huawei_vrp_display_lldp_neighbor", "huawei_vrp_display_lldp_neighbor.textfsm"),
    ("huawei_vrp_display_ndp", "huawei_vrp_display_ndp.textfsm"),
    ("huawei_vrp_display_version", "huawei_vrp_display_version.textfsm"),
]


def install_into(db_path: Path) -> int:
    """Install all Huawei templates into the given SQLite DB. Returns count installed."""
    if not db_path.exists():
        print(f"  [skip] {db_path} (does not exist)")
        return 0

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='templates'")
        if not cur.fetchone():
            print(f"  [skip] {db_path} (no 'templates' table)")
            return 0

        # Remove any prior huawei rows so this is idempotent
        cur.execute("DELETE FROM templates WHERE cli_command LIKE 'huawei_%'")
        deleted = cur.rowcount

        now = datetime.now().isoformat(timespec="seconds")
        installed = 0
        for cli_command, filename in TEMPLATES:
            content = (TEMPLATE_DIR / filename).read_text(encoding="utf-8")
            tfsm_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            cur.execute(
                "INSERT INTO templates (cli_command, cli_content, textfsm_content, "
                "textfsm_hash, source, created) VALUES (?, ?, ?, ?, ?, ?)",
                (cli_command, "", content, tfsm_hash, "secure_cartography_huawei", now),
            )
            installed += 1
        conn.commit()
        print(f"  [ok]   {db_path} (deleted {deleted}, installed {installed})")
        return installed
    finally:
        conn.close()


def main() -> int:
    targets: list[Path] = []
    if len(sys.argv) > 1:
        targets.append(Path(sys.argv[1]).resolve())
    else:
        candidates = [
            ROOT / "tfsm_backup" / "tfsm_templates.db",
            ROOT / "sc2" / "scng" / "utils" / "tfsm_templates.db",
        ]
        targets.extend(p for p in candidates if p.exists())
        if not targets:
            print("No tfsm_templates.db found; pass a path explicitly.")
            return 1

    print("Installing Huawei TextFSM templates:")
    total = 0
    for t in targets:
        total += install_into(t)
    print(f"Done. {total} template insertions across {len(targets)} database(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
