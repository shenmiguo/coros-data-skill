#!/usr/bin/env python3
"""Push missing COROS activities to Garmin CN and Garmin International."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from push_cn_history import (
    already_uploaded,
    fetch_all,
    load_dotenv,
    login,
    matches_coros,
    record_upload,
    years_ago,
)

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "activities.db"
TARGETS = ("garmin_cn", "garmin_intl")

QUEUE_SQL = """
SELECT c.source_id, c.sport, c.raw_sport, c.start_utc, c.duration_s,
       c.distance_m, c.name, c.group_id,
       EXISTS (
         SELECT 1 FROM source_activities cn
         WHERE cn.group_id = c.group_id AND cn.source = 'garmin_cn'
       ) AS cn_present,
       EXISTS (
         SELECT 1 FROM source_activities intl
         WHERE intl.group_id = c.group_id AND intl.source = 'garmin_intl'
       ) AS intl_present
FROM source_activities c
WHERE c.source = 'coros'
  AND date(c.start_utc) >= date(?)
  AND date(c.start_utc) <= date(?)
ORDER BY c.start_utc DESC
"""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="YYYY-MM-DD；默认回溯 10 年")
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--end", help="YYYY-MM-DD；默认今天")
    parser.add_argument("--targets", default="garmin_cn,garmin_intl")
    parser.add_argument("--limit", type=int, help="本次最多处理的 COROS 活动组数")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db-only", action="store_true", help="不登录 Garmin，仅用本地合并库预演")
    parser.add_argument("--manifest-only", action="store_true", help="只下载原始 FIT，不上传")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--download-delay-ms", type=float, default=500)
    parser.add_argument("--download-timeout", type=int, default=1200)
    parser.add_argument("--upload-timeout", type=int, default=90)
    parser.add_argument("--cache-dir", default="data/coros_originals")
    return parser.parse_args(argv)


def local_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def window(args):
    today = local_today()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start = years_ago(today, args.years)
    return start.isoformat(), end.isoformat()


def parse_targets(value):
    targets = tuple(item.strip() for item in value.split(",") if item.strip())
    invalid = [target for target in targets if target not in TARGETS]
    if invalid:
        raise ValueError(f"unknown targets: {', '.join(invalid)}")
    if not targets:
        raise ValueError("at least one target is required")
    return targets


def load_db_groups(start, end):
    """Return one representative COROS record per merged group."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"missing merged database: {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(QUEUE_SQL, (start, end)).fetchall()
    con.close()

    groups = {}
    for row in rows:
        key = row["group_id"] or f'coros:{row["source_id"]}'
        current = groups.get(key)
        if current is None or row["source_id"] < current["source_id"]:
            groups[key] = {
                "source_id": str(row["source_id"]),
                "sport": row["sport"],
                "raw_sport": row["raw_sport"],
                "start_utc": row["start_utc"],
                "duration_s": row["duration_s"],
                "distance_m": row["distance_m"],
                "name": row["name"],
                "group_id": row["group_id"],
                "cn_present": bool(row["cn_present"]),
                "intl_present": bool(row["intl_present"]),
            }
    return list(groups.values())


def build_queue(db_groups, targets, ledger=None):
    ledger = ledger or defaultdict(set)
    queue = []
    for record in db_groups:
        pending = []
        for target in targets:
            present = record[f"{target.removeprefix('garmin_')}_present"]
            if not present and record["source_id"] not in ledger[target]:
                pending.append(target)
        if pending:
            record = dict(record)
            record["pending_targets"] = pending
            queue.append(record)
    return queue


def download_manifest(groups, cache_dir, delay_ms, timeout):
    downloader = ROOT / "download_coros_history.mjs"
    cache_path = Path(cache_dir).expanduser()
    if not cache_path.is_absolute():
        cache_path = ROOT / cache_path
    payload = json.dumps(groups, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [
                "node", str(downloader),
                "--cache-dir", str(cache_path),
                "--delay-ms", str(delay_ms),
            ],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"COROS download failed: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"COROS download failed: {(proc.stderr or proc.stdout or '').strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("COROS download returned invalid JSON") from exc


def target_id_from_response(response):
    if not isinstance(response, dict):
        if isinstance(response, list) and response:
            response = response[0]
        else:
            return None
    result = response.get("detailedImportResult") or {}
    upload = result.get("uploadUuid") or {}
    value = result.get("uploadId") or upload.get("uuid")
    if not value:
        value = response.get("activityId") or response.get("detailedActivityId")
    return str(value) if value else None


def is_duplicate_activity_error(exc):
    text = str(exc)
    return "409" in text and "Duplicate Activity" in text


def upload_file(client, path, timeout=90):
    payload = Path(path)
    with payload.open("rb") as file_handle:
        response = client.client.post(
            "connectapi",
            client.garmin_connect_upload,
            files={"file": (payload.name, file_handle, "application/octet-stream")},
            api=True,
            timeout=timeout,
        )
    target_id = target_id_from_response(response)
    if not target_id:
        raise RuntimeError(f"Garmin upload returned no target id: {response!r}")
    return target_id


def local_time(record):
    stamp = datetime.fromisoformat(record["start_utc"].replace("Z", "+00:00"))
    return stamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()
    start, end = window(args)
    try:
        targets = parse_targets(args.targets)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    db_groups = load_db_groups(start, end)
    ledger = {target: already_uploaded(target, "coros") for target in targets}
    queue = build_queue(db_groups, targets, ledger)
    if args.limit is not None:
        queue = queue[:max(0, args.limit)]

    upload_count = sum(len(record["pending_targets"]) for record in queue)
    print(f"window: {start}..{end}", flush=True)
    print(
            f"coros groups: {len(db_groups)}, missing: {len(queue)}, "
            f"uploads: {upload_count}",
            flush=True,
        )
    for record in queue[:15]:
        targets_text = "+".join(record["pending_targets"])
        distance = f'{(record.get("distance_m") or 0) / 1000:.2f}km'
        print(
            f"  {local_time(record)} {record['sport']} {distance} -> {targets_text}",
            flush=True,
        )
    if args.dry_run or args.db_only:
        return 0

    missing = build_queue(db_groups, targets, ledger)
    if args.limit is not None:
        missing = missing[:max(0, args.limit)]
    downloads = download_manifest(
        missing, args.cache_dir, args.download_delay_ms, args.download_timeout
    )
    files = {item["source_id"]: item for item in downloads}
    failed = [item for item in downloads if not item.get("path") and not item.get("gpx_path")]
    if failed:
        for item in failed:
            print(
                f"download failed: {item['source_id']}: {'; '.join(item.get('errors', []))}",
                file=sys.stderr,
            )
        if not any(
            files.get(record["source_id"], {}).get("path")
            or files.get(record["source_id"], {}).get("gpx_path")
            for record in missing
        ):
            return 1

    if args.manifest_only:
        print(f"download only: ok={len(downloads) - len(failed)} fail={len(failed)}")
        return 1 if failed else 0

    total_ok = total_duplicate = total_fail = 0
    for target in targets:
        client = login(target, cn=target == "garmin_cn")
        if client is None:
            return 1
        target_acts = fetch_all(client, datetime.fromisoformat(start).replace(tzinfo=timezone.utc))
        done = already_uploaded(target, "coros")
        ok = duplicate = fail = 0
        todo = [record for record in missing if record["source_id"] not in done]
        print(f"{target}: {len(todo)} upload(s) after target-side ledger check", flush=True)
        for index, record in enumerate(todo, 1):
            item = files.get(record["source_id"])
            if not item or (not item.get("path") and not item.get("gpx_path")):
                fail += 1
                continue
            existing = next(
                (activity for activity in target_acts if matches_coros(activity, record)),
                None,
            )
            if existing:
                record_upload(target, record["source_id"], str(existing["activityId"]), "coros")
                duplicate += 1
                print(
                    f"[{index}/{len(todo)}] duplicate {local_time(record)} "
                    f"-> target={existing['activityId']}",
                    flush=True,
                )
                continue

            primary_path = item.get("path") or item.get("gpx_path")
            try:
                target_id = upload_file(client, primary_path, args.upload_timeout)
                record_upload(target, record["source_id"], target_id, "coros")
                ok += 1
                total_ok += 1
            except Exception as exc:
                gpx_path = item.get("gpx_path")
                if (
                    not is_duplicate_activity_error(exc)
                    or primary_path == gpx_path
                    or not gpx_path
                ):
                    fail += 1
                    print(
                        f"[{index}/{len(todo)}] FAIL {local_time(record)}: {exc}",
                        file=sys.stderr,
                    )
                    time.sleep(args.delay * 2)
                    continue
                try:
                    target_id = upload_file(client, gpx_path, args.upload_timeout)
                    record_upload(target, record["source_id"], target_id, "coros")
                    ok += 1
                    total_ok += 1
                except Exception as gpx_exc:
                    fail += 1
                    print(
                        f"[{index}/{len(todo)}] FAIL {local_time(record)}: "
                        f"FIT={exc}; GPX={gpx_exc}",
                        file=sys.stderr,
                    )
                    time.sleep(args.delay * 2)
                    continue
            print(
                f"[{index}/{len(todo)}] ok {local_time(record)} -> target={target_id} "
                f"({item['size'] or item['gpx_size']}B)",
                flush=True,
            )
            time.sleep(args.delay)
        total_duplicate += duplicate
        total_fail += fail
        print(f"{target} done: ok={ok} duplicate={duplicate} fail={fail}", flush=True)

    print(
        f"all targets done: ok={total_ok} duplicate={total_duplicate} fail={total_fail}",
        flush=True,
    )
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
