#!/usr/bin/env python3
"""Dump recent EZVIZ alarm records for a device.

Calls both `client.get_alarminfo(serial, limit=N)` and
`client.get_device_messages_list(serial, limit=N)` and prints the raw
JSON so we can compare them and hunt for the RFID card id (or any other
metadata) that the basic `last_alarm_type_name` field strips.

Usage:

    python3 tools/hp7_alarm_dump.py \
        --account YOUR_EZVIZ_EMAIL \
        --password YOUR_PASSWORD \
        --region eu \
        --serial BE7062577-BE6963574 \
        --limit 5

Credentials can come from EZVIZ_ACCOUNT / EZVIZ_PASSWORD / EZVIZ_REGION
/ EZVIZ_SERIAL env vars.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "ezviz_hp7"))

from pylocalapi.client import EzvizClient  # noqa: E402

REGION_URLS = {
    "eu": "apiieu.ezvizlife.com",
    "us": "apiisa.ezvizlife.com",
    "cn": "apiicn.ezvizlife.com",
    "as": "apiias.ezvizlife.com",
    "sa": "apiisa.ezvizlife.com",
    "ru": "apirus.ezvizru.com",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--account", default=os.environ.get("EZVIZ_ACCOUNT"))
    p.add_argument("--password", default=os.environ.get("EZVIZ_PASSWORD"))
    p.add_argument(
        "--region",
        default=os.environ.get("EZVIZ_REGION", "eu"),
        choices=sorted(REGION_URLS),
    )
    p.add_argument("--serial", default=os.environ.get("EZVIZ_SERIAL"))
    p.add_argument("--limit", type=int, default=5)
    p.add_argument(
        "--stype",
        default="92",
        help="unifiedmsg stype (default 92 = all alarms; try -1, 2701, 9904)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    missing = [
        n for n, v in (
            ("account", args.account),
            ("password", args.password),
            ("serial", args.serial),
        ) if not v
    ]
    if missing:
        print(f"Missing: {', '.join(missing)}", file=sys.stderr)
        return 2

    host = REGION_URLS.get(args.region) or REGION_URLS["eu"]
    print(f"[hp7_alarm_dump] login → {host}")
    c = EzvizClient(account=args.account, password=args.password, url=host)
    c.login()
    print(f"[hp7_alarm_dump] login OK")

    print()
    print(f"=== get_alarminfo(serial={args.serial}, limit={args.limit}) ===")
    try:
        ai = c.get_alarminfo(args.serial, limit=args.limit)
        print(json.dumps(ai, indent=2, ensure_ascii=False))
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)

    print()
    print(f"=== get_device_messages_list(serial={args.serial}, stype={args.stype}, limit={args.limit}) ===")
    try:
        msgs = c.get_device_messages_list(
            serials=args.serial,
            s_type=args.stype,
            limit=args.limit,
        )
        print(json.dumps(msgs, indent=2, ensure_ascii=False))
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)

    try:
        c.logout()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
