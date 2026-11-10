#!/usr/bin/env python3
"""Audit and remove COROS activities duplicated by an earlier bulk import.

Default mode is read-only. Remote deletion requires --execute and is limited to
unattributed records that have a near-identical, older COROS record with a
non-empty device field.
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sports import sports_compatible

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "activities.db"
COROS_SOURCE = ROOT / "sources" / "coros_source.mjs"
DELETE_SCRIPT = ROOT / "delete_coros_activities.mjs"
DEFAULT_MIN_LABEL_ID = 481655000000000000
EXPORT_FILE_RE = re.compile(
    r"^(?P<local_start>\d{4}-\d{2}-\d{2}_\d{6})_(?P<activity_id>\d+)\.(?:fit|tcx)$",
    re.IGNORECASE,
)


def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def numeric_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def rel_diff(x, y):
    return abs(x - y) / min(x, y)


def find_duplicate_candidates(
    coros_records,
    *,
    min_label_id=481655000000000000,
    max_gap_s=2,
    distance_tolerance=0.01,
):
    originals = [
        record
        for record in coros_records
        if record.get("device") and numeric_id(record.get("source_id")) is not None
    ]
    candidates = []
    for imported in sorted(
        coros_records,
        key=lambda record: numeric_id(record.get("source_id")) or 0,
    ):
        imported_id = numeric_id(imported.get("source_id"))
        if (
            imported_id is None
            or imported_id < min_label_id
            or imported.get("device")
        ):
            continue

        imported_start = parse_ts(imported["start_utc"])
        imported_distance = imported.get("distance_m")
        matches = []
        for original in originals:
            original_id = numeric_id(original.get("source_id"))
            if original_id is None or original_id >= imported_id:
                continue
            has_distance = bool(
                imported.get("distance_m") and original.get("distance_m")
            )
            if not sports_compatible(
                imported.get("sport"),
                original.get("sport"),
                allow_strength_cardio=not has_distance,
            ):
                continue
            original_start = parse_ts(original["start_utc"])
            if abs((imported_start - original_start).total_seconds()) > max_gap_s:
                continue
            original_distance = original.get("distance_m")
            if imported_distance and original_distance:
                if (
                    min(imported_distance, original_distance) <= 100
                    or rel_diff(imported_distance, original_distance)
                    > distance_tolerance
                ):
                    continue
            else:
                imported_duration = imported.get("duration_s")
                original_duration = original.get("duration_s")
                if not imported_duration or not original_duration:
                    continue
                duration_close = (
                    abs(imported_duration - original_duration) <= 30
                    or rel_diff(imported_duration, original_duration) <= 0.05
                )
                if not duration_close:
                    continue
            matches.append(original)

        if matches:
            candidates.append((imported, matches[-1]))
    return candidates


def fetch_coros_activities(start, end):
    proc = subprocess.run(
        ["node", str(COROS_SOURCE), "--start", start, "--end", end],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "COROS fetch failed").strip())
    return json.loads(proc.stdout)


def load_db_context():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    ledger_ids = {
        row[0]
        for row in con.execute(
            """SELECT source_id FROM push_uploads
               WHERE target = 'coros' AND source = 'garmin_cn'"""
        )
    }
    garmin_records = [
        dict(row)
        for row in con.execute(
            "SELECT * FROM source_activities WHERE source = 'garmin_cn'"
        )
    ]
    con.close()
    return ledger_ids, garmin_records


def find_garmin_source(
    imported,
    garmin_records,
    ledger_ids,
    *,
    max_gap_s=5,
    distance_tolerance=0.02,
):
    imported_start = parse_ts(imported["start_utc"])
    imported_distance = imported.get("distance_m")
    if not imported_distance:
        return None

    matches = []
    for garmin in garmin_records:
        if garmin["source_id"] not in ledger_ids:
            continue
        if garmin.get("sport") != imported.get("sport"):
            continue
        gap = abs((parse_ts(garmin["start_utc"]) - imported_start).total_seconds())
        if gap > max_gap_s or not garmin.get("distance_m"):
            continue
        if rel_diff(imported_distance, garmin["distance_m"]) > distance_tolerance:
            continue
        matches.append((gap, garmin))
    return min(matches, key=lambda item: item[0])[1] if matches else None


def export_file_ids(export_dir, ledger_ids):
    by_local_start = {}
    if not export_dir.exists():
        return by_local_start
    for path in export_dir.iterdir():
        match = EXPORT_FILE_RE.match(path.name)
        if not match or match.group("activity_id") not in ledger_ids:
            continue
        by_local_start.setdefault(match.group("local_start"), set()).add(
            match.group("activity_id")
        )
    return by_local_start


def garmin_id_from_export(imported, files_by_local_start, ledger_ids):
    local_start = parse_ts(imported["start_utc"]).astimezone(
        ZoneInfo("Asia/Shanghai")
    )
    key = local_start.strftime("%Y-%m-%d_%H%M%S")
    matches = files_by_local_start.get(key, set()) & ledger_ids
    return next(iter(matches)) if len(matches) == 1 else None


def build_plan(coros_records, garmin_records, ledger_ids, export_dir, args):
    pairs = find_duplicate_candidates(
        coros_records,
        min_label_id=args.min_label_id,
        max_gap_s=args.max_gap_seconds,
        distance_tolerance=args.distance_tolerance,
    )
    files_by_local_start = export_file_ids(export_dir, ledger_ids)
    plan = []
    for imported, original in pairs:
        garmin = find_garmin_source(
            imported,
            garmin_records,
            ledger_ids,
            max_gap_s=max(args.max_gap_seconds, 5),
        )
        garmin_id = (
            garmin["source_id"]
            if garmin
            else garmin_id_from_export(imported, files_by_local_start, ledger_ids)
        )
        plan.append(
            {
                "label_id": imported["source_id"],
                "start_utc": imported["start_utc"],
                "distance_m": imported.get("distance_m"),
                "original_label_id": original["source_id"],
                "original_device": original.get("device"),
                "garmin_source_id": garmin_id,
            }
        )
    return plan


def write_report(plan, report_path):
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(plan),
        "candidates": plan,
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def delete_remote(label_ids, delay):
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="coros-delete-",
        delete=False,
    ) as handle:
        json.dump(label_ids, handle)
        handle.write("\n")
        temp_path = Path(handle.name)
    try:
        proc = subprocess.run(
            [
                "node",
                str(DELETE_SCRIPT),
                "--label-ids-file",
                str(temp_path),
                "--delay",
                str(delay),
            ],
            capture_output=True,
            text=True,
            timeout=max(120, len(label_ids) * (delay + 3)),
        )
    finally:
        temp_path.unlink(missing_ok=True)
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        detail = (proc.stderr or "COROS delete returned no result").strip()
        raise RuntimeError(detail)
    result = json.loads(lines[-1])
    if proc.returncode != 0 and not result.get("deleted"):
        detail = (proc.stderr or proc.stdout or "COROS delete failed").strip()
        raise RuntimeError(detail)
    return result


def apply_db_cleanup(deleted_ids, plan):
    candidates = {str(item["label_id"]): item for item in plan}
    con = sqlite3.connect(DB_PATH)
    for label_id in map(str, deleted_ids):
        item = candidates.get(label_id)
        if not item:
            continue
        con.execute(
            "DELETE FROM source_activities WHERE source = 'coros' AND source_id = ?",
            (label_id,),
        )
        if item.get("garmin_source_id"):
            con.execute(
                """DELETE FROM push_uploads
                   WHERE target = 'coros' AND source = 'garmin_cn' AND source_id = ?""",
                (str(item["garmin_source_id"]),),
            )
    con.commit()
    con.close()


def default_end():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def main():
    parser = argparse.ArgumentParser(
        description="Audit/remove COROS duplicates created by this tool's bulk import"
    )
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=default_end())
    parser.add_argument("--input-json", help="use a saved COROS source JSON")
    parser.add_argument("--execute", action="store_true", help="delete matched records")
    parser.add_argument("--min-label-id", type=int, default=DEFAULT_MIN_LABEL_ID)
    parser.add_argument("--max-gap-seconds", type=float, default=2)
    parser.add_argument("--distance-tolerance", type=float, default=0.01)
    parser.add_argument("--max-delete", type=int, default=200)
    parser.add_argument(
        "--export-dir",
        default=str(ROOT / "data" / "fit_for_coros"),
    )
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--report", help="report path")
    args = parser.parse_args()

    coros_records = (
        json.loads(Path(args.input_json).read_text())
        if args.input_json
        else fetch_coros_activities(args.start, args.end)
    )
    ledger_ids, garmin_records = load_db_context()
    plan = build_plan(
        coros_records,
        garmin_records,
        ledger_ids,
        Path(args.export_dir),
        args,
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = Path(
        args.report or ROOT / "data" / f"coros_duplicate_cleanup_{stamp}.json"
    )
    write_report(plan, report_path)

    mapped = sum(bool(item.get("garmin_source_id")) for item in plan)
    print(
        f"coros records={len(coros_records)} candidates={len(plan)} "
        f"garmin_mapped={mapped}"
    )
    for item in plan[:20]:
        print(
            f"  {item['label_id']} {item['start_utc']} "
            f"{item['distance_m']:.1f}m -> original={item['original_label_id']} "
            f"garmin={item.get('garmin_source_id') or '--'}"
        )
    if len(plan) > 20:
        print(f"  ... {len(plan) - 20} more")
    print(f"report: {report_path}")

    if not args.execute:
        print("dry-run only; rerun with --execute to delete these records")
        return 0
    if len(plan) > args.max_delete:
        print(
            f"refusing to delete {len(plan)} records; "
            f"--max-delete is {args.max_delete}",
            file=sys.stderr,
        )
        return 1
    if not plan:
        print("nothing to delete")
        return 0

    result = delete_remote([item["label_id"] for item in plan], args.delay)
    deleted = [str(item) for item in result.get("deleted", [])]
    failed = result.get("failed", [])
    apply_db_cleanup(deleted, plan)
    print(f"deleted={len(deleted)} failed={len(failed)}")
    for failure in failed[:20]:
        print(
            f"  FAIL {failure.get('label_id')}: {failure.get('error')}",
            file=sys.stderr,
        )
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
