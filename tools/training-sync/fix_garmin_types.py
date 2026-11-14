#!/usr/bin/env python3
"""Fix imported Garmin activity types from the matching COROS activity."""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from push_cn_history import load_dotenv, login
from reconcile_uploads import manual_type

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "activities.db"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
TARGETS = ("garmin_cn", "garmin_intl")

CANDIDATE_SQL = """
WITH representatives AS (
  SELECT group_id, min(source_id) AS source_id
  FROM source_activities
  WHERE source = 'coros'
    AND date(start_utc, 'localtime') >= date(?)
    AND date(start_utc, 'localtime') <= date(?)
  GROUP BY group_id
)
SELECT c.group_id, c.sport AS canonical_sport, c.name AS canonical_name,
       g.source AS target, g.source_id AS target_id
FROM representatives r
JOIN source_activities c
  ON c.group_id = r.group_id AND c.source_id = r.source_id
JOIN source_activities g
  ON g.group_id = c.group_id AND g.raw_sport = 'other'
WHERE g.source IN (?, ?)
ORDER BY c.start_utc, g.source
"""


def window(args):
    today = datetime.now(LOCAL_TZ).date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start = today.replace(year=today.year - args.years)
    return start.isoformat(), end.isoformat()


def parse_targets(value):
    targets = tuple(item.strip() for item in value.split(",") if item.strip())
    invalid = [target for target in targets if target not in TARGETS]
    if invalid:
        raise ValueError(f"unknown targets: {', '.join(invalid)}")
    if not targets:
        raise ValueError("at least one target is required")
    return targets


def load_candidates(start, end, targets, sports=None):
    if not DB_PATH.exists():
        raise FileNotFoundError(f"missing merged database: {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    sql = CANDIDATE_SQL
    params = [start, end, "garmin_cn", "garmin_intl"]
    if sports:
        sports = tuple(sports)
        sql = sql.replace(
            "WHERE g.source IN (?, ?)",
            "WHERE g.source IN (?, ?) AND "
            f"c.sport IN ({','.join('?' for _ in sports)})",
        )
        params.extend(sports)
    rows = con.execute(sql, params).fetchall()
    con.close()
    return [dict(row) for row in rows if row["target"] in targets]


def type_index(client):
    return {
        item["typeKey"]: (item["typeId"], item["parentTypeId"])
        for item in client.get_activity_types()
    }


def current_type(client, target_id):
    activity = client.get_activity(target_id)
    return (activity.get("activityTypeDTO") or {}).get("typeKey")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="YYYY-MM-DD，本地日期；默认回溯 10 年")
    parser.add_argument("--end", help="YYYY-MM-DD，本地日期；默认今天")
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--targets", default=",".join(TARGETS))
    parser.add_argument(
        "--sports", help="只修正这些 COROS 类型，逗号分隔，例如 stair_climb"
    )
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        targets = parse_targets(args.targets)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        start, end = window(args)
        sports = tuple(
            item.strip() for item in args.sports.split(",") if item.strip()
        ) if args.sports else None
        candidates = load_candidates(start, end, targets, sports)
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"window: {start}..{end}")
    print(f"candidates: {len(candidates)} target record(s)")
    if args.dry_run:
        for row in candidates[:20]:
            print(
                f"  {row['group_id']} {row['target']}:{row['target_id']} "
                        f"{row['canonical_sport']} -> {manual_type(row['canonical_sport'])}"
            )
        return 0

    clients = {}
    indexes = {}
    ok = skipped = failed = 0
    for index, row in enumerate(candidates, 1):
        target = row["target"]
        target_key = manual_type(row["canonical_sport"])
        if target_key == "other":
            skipped += 1
            continue
        try:
            if target not in clients:
                clients[target] = login(target, target == "garmin_cn")
                indexes[target] = type_index(clients[target])
            if target_key not in indexes[target]:
                raise RuntimeError(f"Garmin has no activity type {target_key}")

            current = current_type(clients[target], row["target_id"])
            if current == target_key:
                skipped += 1
                continue
            type_id, parent_type_id = indexes[target][target_key]
            for attempt in range(1, args.retries + 1):
                try:
                    clients[target].set_activity_type(
                        row["target_id"], type_id, target_key, parent_type_id
                    )
                    break
                except Exception:
                    if attempt == args.retries:
                        raise
                    time.sleep(min(30, 5 * attempt))
            ok += 1
            print(
                f"[{index}/{len(candidates)}] {target}:{row['target_id']} "
                f"{current} -> {target_key}",
                flush=True,
            )
            time.sleep(args.delay)
        except Exception as exc:
            failed += 1
            print(
                f"[{index}/{len(candidates)}] FAIL {target}:{row['target_id']}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(args.delay * 2)

    print(f"done: ok={ok} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
