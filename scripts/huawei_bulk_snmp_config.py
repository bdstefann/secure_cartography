"""
Bulk SNMP-view configurator for Huawei VRP / YunShan switches.

Thin CLI wrapper over sc2.scng.tools.config_pusher. The actual SSH push
logic lives in that shared module so the GUI Config Push widget can
reuse it without duplication.

Reads a list of IPs from a hosts file and applies — over SSH, in parallel —
the SNMP view configuration that exposes LLDP-MIB and HUAWEI-NDP-MIB to a
given community, so Secure Cartography can discover neighbors.

The script is idempotent: it checks for an existing view binding before
making changes and skips devices that are already configured. It can also
run in dry-run mode (prints the exact CLI block without sending it).

Usage example:
    python scripts/huawei_bulk_snmp_config.py \\
        --hosts hosts.txt \\
        --ssh-username admin \\
        --ssh-password-env HW_PASS \\
        --community public \\
        --view-name view-discovery \\
        --restricted \\
        --parallel 10 \\
        --save \\
        --log bulk.log

hosts.txt format (one device per line):
    10.0.0.1
    10.0.0.2
    10.0.0.3        # inline comments allowed
    # blank lines and comment-only lines are ignored
"""

from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

# Make the sc2 package importable when running from a fresh checkout
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sc2.scng.tools.config_pusher import (  # noqa: E402
    DeviceResult,
    apply_commands_to_device,
    build_snmp_view_commands,
    build_snmp_view_skip_check,
    load_hosts,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Bulk SNMP view configurator for Huawei switches",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--hosts", required=True, type=Path,
                   help="Path to file with one IP per line")
    p.add_argument("--ssh-username", required=True,
                   help="SSH username (must exist on every device)")
    auth = p.add_mutually_exclusive_group()
    auth.add_argument("--ssh-password-env",
                      help="Name of env var holding the SSH password")
    auth.add_argument("--ssh-password-prompt", action="store_true",
                      help="Prompt for SSH password interactively")
    auth.add_argument("--ssh-key-file", type=Path,
                      help="Path to SSH private key")
    p.add_argument("--ssh-key-passphrase-env",
                   help="Env var with passphrase for the SSH key")
    p.add_argument("--community", required=True,
                   help="SNMP read community to bind to the view")
    p.add_argument("--view-name", default="view-discovery",
                   help="SNMP view name to create (default: view-discovery)")
    p.add_argument("--restricted", action="store_true",
                   help="Restrict view to internet + LLDP + Huawei (default: iso)")
    p.add_argument("--save", action="store_true",
                   help="Run 'save' after applying config")
    p.add_argument("--dry-run", action="store_true",
                   help="Print commands but don't connect")
    p.add_argument("--parallel", type=int, default=8,
                   help="Concurrent SSH sessions (default: 8)")
    p.add_argument("--timeout", type=int, default=30,
                   help="SSH connect timeout per device (default: 30s)")
    p.add_argument("--legacy", action="store_true",
                   help="Enable legacy SSH algorithms for older VRP")
    p.add_argument("--log", type=Path,
                   help="Write per-device transcript to this file")
    p.add_argument("--verbose", "-v", action="count", default=0,
                   help="Increase log level (-v, -vv)")
    return p.parse_args()


def resolve_password(args: argparse.Namespace) -> Optional[str]:
    if args.ssh_password_env:
        pw = os.environ.get(args.ssh_password_env)
        if not pw:
            raise SystemExit(f"env var {args.ssh_password_env} is empty")
        return pw
    if args.ssh_password_prompt:
        return getpass.getpass("SSH password: ")
    return None


def main() -> int:
    args = parse_args()

    level = logging.WARNING - 10 * args.verbose
    logging.basicConfig(level=max(level, logging.DEBUG),
                        format="%(asctime)s %(levelname)s %(message)s")

    hosts = load_hosts(args.hosts)
    if not hosts:
        print("hosts file is empty", file=sys.stderr)
        return 1
    print(f"Loaded {len(hosts)} hosts from {args.hosts}")

    commands = build_snmp_view_commands(args.view_name, args.community, args.restricted)
    check_command, skip_patterns = build_snmp_view_skip_check(args.view_name)

    if args.dry_run:
        print("\n=== DRY RUN: commands that would be sent ===")
        for cmd in commands:
            print(f"  {cmd}")
        if args.save:
            print("  save")
            print("  y")
        print()

    password = None if args.dry_run else resolve_password(args)
    key_passphrase = None
    if args.ssh_key_passphrase_env:
        key_passphrase = os.environ.get(args.ssh_key_passphrase_env)

    results: List[DeviceResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        future_to_host = {
            pool.submit(
                apply_commands_to_device,
                host,
                username=args.ssh_username,
                password=password,
                key_file=str(args.ssh_key_file) if args.ssh_key_file else None,
                key_passphrase=key_passphrase,
                commands=commands,
                save=args.save,
                dry_run=args.dry_run,
                timeout=args.timeout,
                legacy_mode=args.legacy,
                check_command=check_command,
                skip_patterns=skip_patterns,
            ): host
            for host in hosts
        }
        for fut in concurrent.futures.as_completed(future_to_host):
            res = fut.result()
            results.append(res)
            badge = {
                "ok": "OK     ",
                "skipped": "SKIP   ",
                "failed": "FAILED ",
                "dry-run": "DRY-RUN",
                "cancelled": "CANCEL ",
            }.get(res.status, "?      ")
            print(f"  [{badge}] {res.host:<18}  ({res.duration_s:5.1f}s)  {res.message}")

    print()
    print("Summary:")
    for status in ("ok", "skipped", "failed", "dry-run", "cancelled"):
        count = sum(1 for r in results if r.status == status)
        if count:
            print(f"  {status:8s}: {count}")

    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(f"=== {r.host} [{r.status}] ({r.duration_s:.2f}s) ===\n")
                fh.write(f"{r.message}\n")
                for line in r.transcript:
                    fh.write(line)
                    if not line.endswith("\n"):
                        fh.write("\n")
                fh.write("\n")
        print(f"\nTranscript written to {args.log}")

    return 0 if all(r.status != "failed" for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
