#!/usr/bin/env python3
"""Reconcile a previously submitted COROS ZIP import against live activities.

COROS reports only a batch-level error count, not the names of failed files.
This helper maps a saved ZIP's Garmin activity IDs back to activities that are
visible in COROS, records confirmed successes in the upload ledger, and leaves
unmatched files eligible for a later retry.
"""

import argparse
import json
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import push_cn_history as push  # noqa: E402


EXPORT_FILE_RE = re.compile(r"_(\d+)\.(?:fit|tcx)$", re.IGNORECASE)


def activity_ids_from_zip(zip_path):
    ids = []
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            match = EXPORT_FILE_RE.search(name)
            if match:
                ids.append(match.group(1))
    return ids


def db_activities(ids):
    con = sqlite3.connect(push.DB_PATH)
    con.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in ids)
    rows = con.execute(
        f"""SELECT source_id, raw_sport, start_utc, distance_m, duration_s
            FROM source_activities
            WHERE source = 'garmin_cn' AND source_id IN ({placeholders})""",
        list(ids),
    ).fetchall()
    con.close()
    return {str(row["source_id"]): dict(row) for row in rows}


def db_row_to_garmin_activity(row):
    start = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00"))
    return {
        "activityId": row["source_id"],
        "activityType": {"typeKey": row.get("raw_sport")},
        "startTimeGMT": start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "distance": row.get("distance_m"),
        "duration": row.get("duration_s"),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile a saved COROS import ZIP with live COROS activities"
    )
    parser.add_argument("--zip", required=True, help="saved Garmin export ZIP")
    parser.add_argument("--import-id", required=True, help="COROS import ID")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="write confirmed matches to the COROS upload ledger",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="optional JSON report path",
    )
    args = parser.parse_args()

    zip_path = Path(args.zip).expanduser().resolve()
    ids = activity_ids_from_zip(zip_path)
    if not ids:
        raise SystemExit(f"no Garmin activity IDs found in {zip_path}")

    known = db_activities(ids)
    missing_db = [activity_id for activity_id in ids if activity_id not in known]
    activities = [
        db_row_to_garmin_activity(known[activity_id])
        for activity_id in ids
        if activity_id in known
    ]
    if not activities:
        raise SystemExit(
            "none of the ZIP activities are in the Garmin CN database; "
            "run sync.py first"
        )

    earliest = min(row["start_utc"] for row in known.values())
    latest = max(row["start_utc"] for row in known.values())
    coros = push.fetch_coros_activities(
        earliest[:10],
        latest[:10],
    )
    matches = []
    unresolved = []
    for activity in activities:
        item = {"activity": activity}
        match = push.find_coros_match(item, coros, max_gap_s=120)
        if match is None:
            unresolved.append(str(activity["activityId"]))
        else:
            matches.append(
                {
                    "activity_id": str(activity["activityId"]),
                    "coros_label_id": str(match["source_id"]),
                }
            )

    print(
        f"zip_files={len(ids)} db_matches={len(known)} "
        f"coros_matches={len(matches)} unresolved={len(unresolved)}"
    )
    if missing_db:
        print("not in Garmin CN database: " + ", ".join(missing_db))
    if unresolved:
        print("not visible in COROS: " + ", ".join(unresolved))

    if args.execute:
        for item in matches:
            push.record_upload("coros", item["activity_id"], args.import_id)
        print(f"ledger seeded for {len(matches)} confirmed import(s)")
    else:
        print("dry-run only; rerun with --execute to seed confirmed imports")

    if args.report:
        report_path = Path(args.report).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "zip": str(zip_path),
                    "import_id": args.import_id,
                    "matched": matches,
                    "unresolved_activity_ids": unresolved,
                    "missing_db_activity_ids": missing_db,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    return 1 if unresolved or missing_db else 0


if __name__ == "__main__":
    sys.exit(main())
