#!/usr/bin/env python3
"""Reconcile uploaded source activities with their final Garmin records."""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from push_cn_history import (
    load_dotenv,
    login,
    parse_gmt,
    record_upload,
)

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "activities.db"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

LINK_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_links (
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  target TEXT NOT NULL,
  target_id TEXT NOT NULL,
  linked_at TEXT NOT NULL,
  evidence TEXT NOT NULL,
  PRIMARY KEY (source, source_id, target)
);
"""

MISSING_SQL = """
WITH representatives AS (
  SELECT group_id, min(source_id) AS source_id
  FROM source_activities
  WHERE source = 'coros'
    AND date(start_utc) >= date(?)
    AND date(start_utc) <= date(?)
  GROUP BY group_id
)
SELECT c.source_id, c.sport, c.raw_sport, c.start_utc, c.duration_s,
       c.distance_m, c.name,
       EXISTS (
         SELECT 1 FROM source_activities g
         WHERE g.group_id = c.group_id AND g.source = ?
       ) AS target_present
FROM representatives r
JOIN source_activities c
  ON c.group_id = r.group_id AND c.source_id = r.source_id
ORDER BY c.start_utc
"""

MANUAL_TYPES = {
    "run": "running",
    "bike": "cycling",
    "walk": "walking",
    "hike": "hiking",
    "swim": "lap_swimming",
    "strength": "strength_training",
    "cardio": "indoor_cardio",
    "jump_rope": "jump_rope",
    "multisport": "multi_sport",
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="YYYY-MM-DD；默认回溯 10 年")
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--end", help="YYYY-MM-DD；默认今天")
    parser.add_argument("--targets", default="garmin_cn,garmin_intl")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manual-fallback", action="store_true")
    parser.add_argument(
        "--link-window-seconds", type=int, default=180,
        help="Garmin upload metadata 与本地上传台账的最大时间差",
    )
    return parser.parse_args(argv)


def window(args):
    today = datetime.now(LOCAL_TZ).date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        try:
            start = today.replace(year=today.year - args.years)
        except ValueError:
            start = today.replace(year=today.year - args.years, day=28)
    return start.isoformat(), end.isoformat()


def parse_targets(value):
    valid = ("garmin_cn", "garmin_intl")
    targets = tuple(item.strip() for item in value.split(",") if item.strip())
    invalid = [target for target in targets if target not in valid]
    if invalid:
        raise ValueError(f"unknown targets: {', '.join(invalid)}")
    if not targets:
        raise ValueError("at least one target is required")
    return targets


def load_ledger(con, target):
    con.execute(LINK_SCHEMA)
    rows = con.execute(
        "SELECT source_id, target_id, uploaded_at FROM push_uploads "
        "WHERE target = ? AND source = 'coros'",
        (target,),
    ).fetchall()
    return {row[0]: {"target_id": row[1], "uploaded_at": row[2]} for row in rows}


def load_missing(con, target, start, end):
    con.execute(LINK_SCHEMA)
    con.row_factory = sqlite3.Row
    rows = con.execute(MISSING_SQL, (start, end, target)).fetchall()
    return [dict(row) for row in rows if not row["target_present"]]


def parse_uploaded(value):
    text = str(value).replace("Z", "+00:00")
    if text.endswith(".0"):
        text = f"{text[:-2]}+00:00"
    elif "." in text and "+" not in text.split(".", 1)[1]:
        text = f"{text}+00:00"
    return datetime.fromisoformat(text)


def choose_upload_candidate(record, candidates, uploaded_at, link_window_seconds=180):
    """Pick the target created by the ledger upload, even if Garmin rewrote it."""
    try:
        uploaded = parse_uploaded(uploaded_at)
        source_start = datetime.fromisoformat(
            record["start_utc"].replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None

    matches = []
    for candidate in candidates:
        metadata = candidate.get("metadataDTO") or {}
        format_key = (metadata.get("fileFormat") or {}).get("formatKey")
        device_id = str((metadata.get("deviceMetaDataDTO") or {}).get("deviceId", ""))
        uploaded_date = metadata.get("uploadedDate")
        if format_key not in {"gpx", "fit", "tcx"} or device_id != "0":
            continue
        try:
            target_uploaded = parse_uploaded(uploaded_date)
            target_start = parse_gmt(candidate["startTimeGMT"])
        except (KeyError, TypeError, ValueError):
            continue
        upload_gap = abs((target_uploaded - uploaded).total_seconds())
        start_gap = abs((target_start - source_start).total_seconds())
        if upload_gap <= link_window_seconds and start_gap <= 86400:
            matches.append((upload_gap, start_gap, candidate))
    return min(matches, default=(None, None, None))[2]


def shortlist_candidates(record, candidates, max_start_seconds=86400):
    source_start = datetime.fromisoformat(record["start_utc"].replace("Z", "+00:00"))
    close = []
    for candidate in candidates:
        try:
            target_start = parse_gmt(candidate["startTimeGMT"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs((target_start - source_start).total_seconds()) <= max_start_seconds:
            close.append(candidate)
    return close


def enrich_candidate(summary, detail):
    merged = dict(summary)
    merged.update(detail)
    if summary.get("startTimeGMT"):
        merged["startTimeGMT"] = summary["startTimeGMT"]
    return merged


def manual_type(sport):
    if str(sport).startswith("other-") or sport in (None, "", "other"):
        return "other"
    return MANUAL_TYPES.get(sport, "other")


def manual_payload(record):
    start = datetime.fromisoformat(record["start_utc"].replace("Z", "+00:00"))
    local_start = start.astimezone(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%S.000")
    return {
        "activityTypeDTO": {"typeKey": manual_type(record.get("sport"))},
        "accessControlRuleDTO": {"typeId": 2, "typeKey": "private"},
        "timeZoneUnitDTO": {"unitKey": "Asia/Shanghai"},
        "activityName": record.get("name") or "Synced activity",
        "metadataDTO": {"autoCalcCalories": False},
        "summaryDTO": {
            "startTimeLocal": local_start,
            "distance": record.get("distance_m") or 0,
            "duration": record.get("duration_s") or 0,
        },
    }


def create_manual_activity(client, record):
    response = client.create_manual_activity_from_json(manual_payload(record))
    activity_id = response.get("activityId") if isinstance(response, dict) else None
    if not activity_id and isinstance(response, list) and response:
        activity_id = response[0].get("activityId")
    if not activity_id:
        raise RuntimeError(f"manual activity returned no id: {response!r}")
    return str(activity_id)


def record_link(con, source, source_id, target, target_id, evidence):
    con.execute(LINK_SCHEMA)
    con.execute(
        "INSERT OR REPLACE INTO activity_links "
        "(source, source_id, target, target_id, linked_at, evidence) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            source, source_id, target, target_id,
            datetime.now(timezone.utc).isoformat(), evidence,
        ),
    )
    con.commit()


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()
    start, end = window(args)
    try:
        targets = parse_targets(args.targets)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if not DB_PATH.exists():
        print(f"missing merged database: {DB_PATH}", file=sys.stderr)
        return 1

    con = sqlite3.connect(DB_PATH)
    con.execute(LINK_SCHEMA)
    con.commit()
    ok = linked = manual = missing_ledger = 0
    for target in targets:
        missing = load_missing(con, target, start, end)
        ledger = load_ledger(con, target)
        print(f"{target}: {len(missing)} coros-only record(s)", flush=True)
        if not missing:
            continue
        client = login(target, target == "garmin_cn")
        if client is None:
            con.close()
            return 1
        for record in missing:
            upload = ledger.get(record["source_id"])
            if upload and not str(upload.get("target_id", "")).startswith("duplicate"):
                day_start = (
                    datetime.fromisoformat(record["start_utc"].replace("Z", "+00:00"))
                    - timedelta(days=2)
                ).date().isoformat()
                day_end = (
                    datetime.fromisoformat(record["start_utc"].replace("Z", "+00:00"))
                    + timedelta(days=2)
                ).date().isoformat()
                candidates = shortlist_candidates(
                    record, client.get_activities_by_date(day_start, day_end)
                )
                ledger_candidate = next(
                    (
                        candidate for candidate in candidates
                        if str(candidate.get("activityId"))
                        == str(upload.get("target_id"))
                    ),
                    None,
                )
                if ledger_candidate:
                    target_id = str(ledger_candidate["activityId"])
                    if args.dry_run:
                        print(f"  would link {record['start_utc']} -> {target_id}", flush=True)
                        linked += 1
                        continue
                    record_link(
                        con, "coros", record["source_id"], target, target_id,
                        "ledger-target-id",
                    )
                    linked += 1
                    ok += 1
                    print(f"  linked {record['start_utc']} -> {target_id}", flush=True)
                    continue
                details = []
                for candidate in candidates:
                    try:
                        details.append(
                            enrich_candidate(
                                candidate, client.get_activity(candidate["activityId"])
                            )
                        )
                    except Exception as exc:
                        print(
                            f"  detail failed {candidate.get('activityId')}: {exc}",
                            file=sys.stderr,
                        )
                chosen = choose_upload_candidate(
                    record, details, upload["uploaded_at"], args.link_window_seconds
                )
                if chosen:
                    target_id = str(chosen["activityId"])
                    if args.dry_run:
                        print(f"  would link {record['start_utc']} -> {target_id}", flush=True)
                        linked += 1
                        continue
                    record_upload(target, record["source_id"], target_id, "coros")
                    record_link(
                        con, "coros", record["source_id"], target, target_id,
                        "upload-metadata",
                    )
                    linked += 1
                    ok += 1
                    print(
                        f"  linked {record['start_utc']} -> {target_id}",
                        flush=True,
                    )
                    continue

            if args.dry_run or not args.manual_fallback:
                missing_ledger += 1
                print(
                    f"  unlinked {record['start_utc']} "
                    f"{record['sport']} ledger={bool(upload)}",
                    flush=True,
                )
                continue
            try:
                if args.dry_run:
                    print(
                        f"  would create manual {record['start_utc']} {record['sport']}",
                        flush=True,
                    )
                    manual += 1
                    continue
                target_id = create_manual_activity(client, record)
                record_upload(target, record["source_id"], target_id, "coros")
                record_link(
                    con, "coros", record["source_id"], target, target_id, "manual"
                )
                manual += 1
                ok += 1
                print(
                    f"  manual {record['start_utc']} {record['sport']} -> {target_id}",
                    flush=True,
                )
            except Exception as exc:
                missing_ledger += 1
                print(
                    f"  manual failed {record['start_utc']} {record['sport']}: {exc}",
                    file=sys.stderr,
                )

    con.close()
    print(
        f"reconcile done: linked={linked} manual={manual} "
        f"unresolved={missing_ledger}",
        flush=True,
    )
    return 1 if missing_ledger else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except json.JSONDecodeError as exc:
        print(f"invalid JSON response: {exc}", file=sys.stderr)
        sys.exit(1)
