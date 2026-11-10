#!/usr/bin/env python3
"""Pull Garmin Connect activities for one region and print normalized JSON."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path

from garminconnect import Garmin

RUN_TYPE_KEYS = {
    "running",
    "trail_running",
    "track_running",
    "treadmill_running",
    "virtual_run",
}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--tokenstore", required=True)
    p.add_argument("--cn", action="store_true", help="use Garmin China (garmin.com.cn)")
    p.add_argument("--label", default=None, help="source label for output records")
    return p.parse_args()


def classify(type_key: str) -> str:
    return "run" if type_key in RUN_TYPE_KEYS else f"other-{type_key}"


def parse_gmt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def normalize(a: dict, label: str) -> dict:
    type_key = (a.get("activityType") or {}).get("typeKey", "unknown")
    start = parse_gmt(a["startTimeGMT"])
    return {
        "source": label,
        "source_id": str(a["activityId"]),
        "sport": classify(type_key),
        "raw_sport": type_key,
        "start_utc": start.isoformat().replace("+00:00", "Z"),
        "duration_s": a.get("duration"),
        "distance_m": a.get("distance"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "load": a.get("trainingLoad"),
        "calories": a.get("calories"),
        "device": a.get("device") or a.get("owner") or None,
        "name": a.get("activityName") or None,
    }


def main() -> int:
    args = parse_args()
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        print(f"garmin[{args.label}]: GARMIN_EMAIL / GARMIN_PASSWORD missing", file=sys.stderr)
        return 1

    client = Garmin(
        email=email,
        password=password,
        is_cn=args.cn,
        prompt_mfa=lambda: getpass("Garmin MFA code: "),
    )
    tokenstore = Path(args.tokenstore)
    tokenstore.mkdir(parents=True, exist_ok=True)

    try:
        client.login(str(tokenstore))
    except Exception as exc:
        print(f"garmin[{args.label}]: token resume failed ({exc}); fresh login", file=sys.stderr)
        client.login()
        client.garth.dump(str(tokenstore))

    start_dt = datetime.fromisoformat(args.start + "T00:00:00").astimezone()
    end_dt = datetime.fromisoformat(args.end + "T23:59:59").astimezone()

    fetched = []
    index = 0
    while True:
        batch = client.get_activities(index, 50)
        if not isinstance(batch, list) or not batch:
            break
        fetched.extend(batch)
        oldest = parse_gmt(batch[-1]["startTimeGMT"])
        if oldest < start_dt.replace(tzinfo=timezone.utc) or len(batch) < 50:
            break
        index += 50

    records = [
        normalize(a, args.label or ("garmin_cn" if args.cn else "garmin_intl"))
        for a in fetched
        if start_dt.replace(tzinfo=timezone.utc)
        <= parse_gmt(a["startTimeGMT"])
        <= end_dt.replace(tzinfo=timezone.utc)
    ]
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
