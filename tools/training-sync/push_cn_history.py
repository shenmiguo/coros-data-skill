#!/usr/bin/env python3
"""Push Garmin CN history into Garmin International and COROS.

Idempotent: activities that already exist on the target (matched by start time,
type and distance/duration) or that were uploaded in a previous run are skipped.
For COROS, this exports FIT/TCX files and can use the same web upload flow as the
COROS Training Hub (STS -> object storage -> import task).
"""

import argparse
import io
import json
import os
import subprocess
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path

from garminconnect import Garmin
from sports import classify_garmin, sports_compatible

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "activities.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_uploads (
    target TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT,
    uploaded_at TEXT NOT NULL,
    PRIMARY KEY (target, source, source_id)
);
"""


def load_dotenv():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def login(label, cn):
    email = os.environ.get(f"{label.upper()}_EMAIL")
    password = os.environ.get(f"{label.upper()}_PASSWORD")
    if not email or not password:
        print(f"[skip] {label}: credentials missing", file=sys.stderr)
        return None
    client = Garmin(email=email, password=password, is_cn=cn,
                    prompt_mfa=lambda: getpass(f"Garmin {label} MFA: "))
    tokenstore = ROOT / "data" / "tokens" / label
    tokenstore.mkdir(parents=True, exist_ok=True)
    try:
        client.login(str(tokenstore))
    except Exception as exc:
        print(f"{label}: token resume failed ({exc}); fresh login", file=sys.stderr)
        client.login()
        client.garth.dump(str(tokenstore))
    return client


def parse_gmt(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def fetch_all(client, start_dt):
    acts = []
    index = 0
    while True:
        batch = client.get_activities(index, 50)
        if not isinstance(batch, list) or not batch:
            break
        acts.extend(batch)
        oldest = parse_gmt(batch[-1]["startTimeGMT"])
        if oldest < start_dt or len(batch) < 50:
            break
        index += 50
    return [a for a in acts if parse_gmt(a["startTimeGMT"]) >= start_dt]


def years_ago(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def rel_diff(x, y):
    return abs(x - y) / min(x, y)


def metrics_match(a, b, gap, max_gap_s):
    da, db = a.get("distance"), b.get("distance")
    ta, tb = a.get("duration"), b.get("duration")
    has_distance = bool(da and db and min(da, db) > 200)
    has_duration = bool(ta and tb and min(ta, tb) > 30)
    if not has_distance and not has_duration:
        return gap <= 30

    distance_strong = has_distance and rel_diff(da, db) <= 0.02
    if distance_strong:
        return True

    distance_close = has_distance and rel_diff(da, db) <= 0.10
    distance_plausible = has_distance and rel_diff(da, db) <= 0.20
    duration_close = has_duration and (
        abs(ta - tb) <= 30 or rel_diff(ta, tb) <= 0.10
    )
    duration_plausible = has_duration and rel_diff(ta, tb) <= 0.25
    if has_distance and has_duration:
        return (
            (distance_close and duration_plausible)
            or (duration_close and distance_plausible)
        )
    return distance_close or duration_close


def same_activity(a, b, max_gap_s=120):
    if (a.get("activityType") or {}).get("typeKey") != (b.get("activityType") or {}).get("typeKey"):
        return False
    gap = abs((parse_gmt(a["startTimeGMT"]) - parse_gmt(b["startTimeGMT"])).total_seconds())
    if gap > max_gap_s:
        return False
    return metrics_match(a, b, gap, max_gap_s)


def find_match(activity, candidates):
    for c in candidates:
        if same_activity(activity, c):
            return c
    return None


def unique_source_activities(activities):
    """Collapse duplicate uploads from the same Garmin account."""
    by_start = {}
    unique = []
    ordered = sorted(
        activities,
        key=lambda activity: (
            activity["startTimeGMT"],
            0
            if classify_garmin((activity.get("activityType") or {}).get("typeKey"))
            == "run"
            else 1,
        ),
    )
    for activity in ordered:
        key = activity["startTimeGMT"]
        duplicates = by_start.setdefault(key, [])
        if any(metrics_match(activity, current, gap=0, max_gap_s=0) for current in duplicates):
            continue
        duplicates.append(activity)
        unique.append(activity)
    return unique


def already_uploaded(target, source=None):
    con = sqlite3.connect(DB_PATH)
    con.execute(SCHEMA)
    if source:
        rows = con.execute(
            "SELECT source_id FROM push_uploads WHERE target = ? AND source = ?",
            (target, source),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT source_id FROM push_uploads WHERE target = ?", (target,)
        ).fetchall()
    con.close()
    return {r[0] for r in rows}


def record_upload(target, source_id, target_id, source="garmin_cn"):
    con = sqlite3.connect(DB_PATH)
    con.execute(SCHEMA)
    con.execute(
        "INSERT OR REPLACE INTO push_uploads (target, source, source_id, target_id, uploaded_at) VALUES (?, ?, ?, ?, ?)",
        (target, source, source_id, target_id, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    con.close()


def fetch_coros_activities(start, end):
    script = ROOT / "sources" / "coros_source.mjs"
    try:
        proc = subprocess.run(
            ["node", str(script), "--all-sports", "--start", start, "--end", end],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"COROS fetch failed: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"COROS fetch failed: {(proc.stderr or proc.stdout or '').strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("COROS fetch returned invalid JSON") from exc


def matches_coros(activity, rec, max_gap_s=120):
    type_key = (activity.get("activityType") or {}).get("typeKey")
    start_g = parse_gmt(activity["startTimeGMT"])
    start_c = datetime.fromisoformat(rec["start_utc"].replace("Z", "+00:00"))
    start_gap = abs((start_g - start_c).total_seconds())
    if start_gap > max_gap_s:
        return False
    da, db = activity.get("distance"), rec.get("distance_m")
    ta, tb = activity.get("duration"), rec.get("duration_s")
    has_distance = bool(da and db and min(da, db) > 200)
    has_duration = bool(ta and tb and min(ta, tb) > 30)
    if not sports_compatible(
        classify_garmin(type_key),
        rec.get("sport"),
        allow_strength_cardio=not has_distance and start_gap <= 5,
        allow_cardio_run=True,
    ):
        return False
    if not has_distance and not has_duration:
        return True

    distance_strong = has_distance and rel_diff(da, db) <= 0.02
    distance_plausible = has_distance and rel_diff(da, db) <= 0.20
    duration_close = has_duration and (
        abs(ta - tb) <= 30 or rel_diff(ta, tb) <= 0.10
    )
    if start_gap <= 5 and (
        not has_distance or distance_strong or distance_plausible or duration_close
    ):
        return True

    distance_close = has_distance and rel_diff(da, db) <= 0.10
    duration_plausible = has_duration and rel_diff(ta, tb) <= 0.25
    if has_distance and has_duration:
        return (
            (distance_close and duration_plausible)
            or (duration_close and distance_plausible)
        )
    return distance_close or duration_close


def find_coros_missing(cn_acts, start, end):
    coros = fetch_coros_activities(start, end)
    print(f"coros: {len(coros)} activities for match check")
    missing = [a for a in cn_acts if not any(matches_coros(a, r) for r in coros)]
    missing.sort(key=lambda a: a["startTimeGMT"], reverse=True)
    return missing


def export_for_coros(cn, cn_acts, export_dir, start, end, delay=0.5):
    missing = find_coros_missing(cn_acts, start, end)
    print(f"coros export: {len(missing)} activities missing on COROS -> {export_dir}")
    ok = fail = 0
    exported = []
    for i, a in enumerate(missing, 1):
        try:
            name, size = download_export(cn, a, export_dir)
            ok += 1
            exported.append({
                "activity": a,
                "path": str(export_dir / name),
                "size": size,
            })
            print(f"[{i}/{len(missing)}] saved {name} ({size}B)")
            time.sleep(delay)
        except Exception as exc:
            fail += 1
            print(f"[{i}/{len(missing)}] FAIL {a['startTimeLocal']}: {exc}", file=sys.stderr)
    print(f"coros export done: ok={ok} fail={fail}")
    return exported


def download_fit(client, activity, export_dir=None):
    raw = client.download_activity(str(activity["activityId"]), dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
    payload, _inner = extract_fit(raw)
    ext = "fit"
    if not looks_like_fit(payload):
        payload = client.download_activity(
            str(activity["activityId"]), dl_fmt=Garmin.ActivityDownloadFormat.TCX
        )
        ext = "tcx"
        if not looks_like_tcx(payload):
            raise ValueError("Garmin TCX download is not a TrainingCenterDatabase")
    path = Path(tempfile.gettempdir()) / f"cn_{activity['activityId']}.{ext}"
    path.write_bytes(payload)
    if export_dir:
        export_dir.mkdir(parents=True, exist_ok=True)
        start = activity["startTimeLocal"].replace(":", "").replace(" ", "_")
        (export_dir / f"{start}_{activity['activityId']}.{ext}").write_bytes(payload)
    return path, len(payload)


def extract_fit(data):
    # Garmin's ORIGINAL download is a zip archive wrapping the .fit file.
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            fits = [n for n in names if n.lower().endswith(".fit")]
            name = fits[0] if fits else max(names, key=lambda n: z.getinfo(n).file_size)
            return z.read(name), name
    if data[:4] in (b".FIT", b"MFIT"):
        return data, "activity.fit"
    if data.lstrip().startswith(b"<?xml"):
        return data, "activity.xml"
    raise ValueError(f"unexpected download format: {data[:8].hex()}")


def looks_like_fit(data):
    return data[:4] in (b".FIT", b"MFIT")


def looks_like_tcx(data):
    head = data[:4096]
    return b"TrainingCenterDatabase" in head


def download_export(client, activity, export_dir):
    """Save a COROS-compatible FIT, falling back to TCX when needed."""
    export_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(export_dir.glob(f"*_{activity['activityId']}.*"))
    for path in existing:
        data = path.read_bytes()
        if (
            path.suffix.lower() == ".fit"
            and looks_like_fit(data)
            or path.suffix.lower() == ".tcx"
            and looks_like_tcx(data)
        ):
            return path.name, path.stat().st_size

    tmp, _size = download_fit(client, activity)
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    if looks_like_fit(data):
        ext = "fit"
    elif looks_like_tcx(data):
        ext = "tcx"
    else:
        data = client.download_activity(
            str(activity["activityId"]), dl_fmt=Garmin.ActivityDownloadFormat.TCX
        )
        ext = "tcx"
    if ext == "tcx" and not looks_like_tcx(data):
        raise ValueError("Garmin TCX download is not a TrainingCenterDatabase")
    start = activity["startTimeLocal"].replace(":", "").replace(" ", "_")
    name = f"{start}_{activity['activityId']}.{ext}"
    (export_dir / name).write_bytes(data)
    return name, len(data)


def build_batches(exports, batch_size, batch_mb):
    batches = []
    batch = []
    batch_bytes = 0
    limit_bytes = int(batch_mb * 1024 * 1024)
    for item in exports:
        size = item["size"]
        if batch and (len(batch) >= batch_size or batch_bytes + size > limit_bytes):
            batches.append(batch)
            batch = []
            batch_bytes = 0
        batch.append(item)
        batch_bytes += size
    if batch:
        batches.append(batch)
    return batches


def batch_window(batch):
    starts = [parse_gmt(item["activity"]["startTimeGMT"]) for item in batch]
    if not starts:
        return None, None
    first = min(starts).date().isoformat()
    last = max(starts).date().isoformat()
    return first, last


def find_coros_match(item, coros_records, max_gap_s=120):
    activity = item["activity"]
    for record in coros_records:
        if matches_coros(activity, record, max_gap_s=max_gap_s):
            return record
    return None


def partition_batch_matches(batch, coros_records, max_gap_s=120):
    matched = []
    unmatched = []
    for item in batch:
        record = find_coros_match(item, coros_records, max_gap_s=max_gap_s)
        if record is None:
            unmatched.append(item)
        else:
            matched.append((item, record))
    return matched, unmatched


def reconcile_partial_batch(batch):
    """Identify files that became visible in COROS after a partial batch."""
    start, end = batch_window(batch)
    if start is None:
        return [], list(batch), []
    coros = fetch_coros_activities(start, end)
    matched, unmatched = partition_batch_matches(batch, coros)
    return matched, unmatched, coros


def write_batch_report(import_dir, batch_no, import_id, result, matched, unmatched):
    report_path = import_dir / f"batch_{batch_no:03d}_{import_id}.json"
    report_path.write_text(
        json.dumps(
            {
                "import_id": import_id,
                "result": result,
                "matched_activity_ids": [
                    str(item["activity"]["activityId"]) for item, _ in matched
                ],
                "unmatched_activity_ids": [
                    str(item["activity"]["activityId"]) for item in unmatched
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return report_path


def import_coros_exports(exports, import_dir, batch_size, batch_mb, timeout):
    done = already_uploaded("coros")
    pending = [
        item for item in exports
        if str(item["activity"]["activityId"]) not in done
    ]
    if not pending:
        print("coros import: nothing pending")
        return 0

    import_dir.mkdir(parents=True, exist_ok=True)
    importer = ROOT / "import_to_coros.mjs"
    batches = build_batches(pending, batch_size, batch_mb)
    print(f"coros import: {len(pending)} files in {len(batches)} batch(es)")
    ok = 0
    partial = 0
    unresolved = 0
    for batch_no, batch in enumerate(batches, 1):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = import_dir / f"garmin_cn_{stamp}_{batch_no:03d}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in batch:
                path = Path(item["path"])
                archive.write(path, arcname=path.name)
        print(
            f"[{batch_no}/{len(batches)}] upload {len(batch)} files "
            f"({zip_path.stat().st_size}B)"
        )
        try:
            proc = subprocess.run(
                [
                    "node",
                    str(importer),
                    "--zip",
                    str(zip_path),
                    "--timeout-seconds",
                    str(timeout),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout + 120,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    (proc.stderr or proc.stdout or "COROS import failed").strip()
                )
            lines = [line for line in proc.stdout.splitlines() if line.strip()]
            if not lines:
                raise RuntimeError("COROS import returned no result")
            result = json.loads(lines[-1])
        except (OSError, subprocess.TimeoutExpired, RuntimeError, json.JSONDecodeError) as exc:
            unresolved += len(batch)
            print(
                f"[{batch_no}/{len(batches)}] FAILED import batch kept at {zip_path}: {exc}",
                file=sys.stderr,
            )
            continue

        import_id = str(result.get("importId", ""))
        matched = []
        unmatched = []
        if result.get("errorCount"):
            try:
                matched, unmatched, _coros = reconcile_partial_batch(batch)
            except Exception as exc:
                unmatched = list(batch)
                print(
                    f"[{batch_no}/{len(batches)}] partial import {import_id}; "
                    f"could not reconcile successful files: {exc}",
                    file=sys.stderr,
                )
            for item, _record in matched:
                record_upload(
                    "coros",
                    str(item["activity"]["activityId"]),
                    import_id,
                )
            partial += len(matched)
            unresolved += len(unmatched)
            report = write_batch_report(
                import_dir, batch_no, import_id, result, matched, unmatched
            )
            print(
                f"[{batch_no}/{len(batches)}] partial import={import_id} "
                f"matched={len(matched)} unresolved={len(unmatched)} report={report}"
            )
            if unmatched:
                print(
                    f"[{batch_no}/{len(batches)}] unresolved files remain in {zip_path}",
                    file=sys.stderr,
                )
            continue

        for item in batch:
            record_upload(
                "coros",
                str(item["activity"]["activityId"]),
                import_id,
            )
        ok += len(batch)
        print(
            f"[{batch_no}/{len(batches)}] ok import={import_id} "
            f"files={len(batch)}"
        )
        zip_path.unlink(missing_ok=True)
    print(f"coros import done: ok={ok} partial={partial} unresolved={unresolved}")
    return unresolved


def main():
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=None, help="start date, YYYY-MM-DD")
    p.add_argument("--years", type=int, default=None, help="calendar years to backfill")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument("--no-push", action="store_true", help="skip intl push (export only)")
    p.add_argument("--export-fit-dir", default=None, help="save FIT/TCX files here")
    p.add_argument(
        "--push-coros",
        action="store_true",
        help="import exported files into COROS after export",
    )
    p.add_argument("--coros-batch-size", type=int, default=40)
    p.add_argument("--coros-batch-mb", type=float, default=100)
    p.add_argument("--coros-import-timeout", type=int, default=900)
    args = p.parse_args()

    local_today = datetime.now(timezone(timedelta(hours=8))).date()
    start_text = args.start
    if args.years:
        start_text = years_ago(local_today, args.years).isoformat()
    if not start_text:
        start_text = "2023-01-01"
    start_dt = datetime.strptime(start_text, "%Y-%m-%d").replace(
        tzinfo=timezone(timedelta(hours=8))
    )
    cn = login("garmin_cn", cn=True)
    if cn is None:
        return 1
    cn_activities = fetch_all(cn, start_dt)
    cn_acts = unique_source_activities(cn_activities)
    print(
        f"garmin_cn: {len(cn_activities)} activities since {start_text}, "
        f"{len(cn_acts)} unique"
    )

    push = not args.no_push
    intl_acts = []
    if push:
        intl = login("garmin_intl", cn=False)
        if intl is None:
            return 1
        intl_acts = fetch_all(intl, start_dt)
        print(f"garmin_intl: {len(intl_acts)} activities since {start_text}")

        done = already_uploaded("garmin_intl")
        missing = [
            a for a in cn_acts
            if str(a["activityId"]) not in done and find_match(a, intl_acts) is None
        ]
        missing.sort(key=lambda a: a["startTimeGMT"], reverse=True)
        todo = missing[: args.limit] if args.limit else missing
        print(f"intl push: {len(missing)} missing, {len(todo)} to process this run")
        if args.dry_run:
            for a in todo[:15]:
                print(f"  would upload: {a['startTimeLocal']} {a.get('activityName')} {a.get('distance')}m")
            if args.push_coros:
                coros_missing = find_coros_missing(cn_acts, start_text, datetime.now().strftime("%Y-%m-%d"))
                print(f"coros push: {len(coros_missing)} missing")
                for a in coros_missing[:15]:
                    print(
                        f"  would export: {a['startTimeLocal']} "
                        f"{(a.get('activityType') or {}).get('typeKey')} "
                        f"{a.get('distance')}m"
                    )
            return 0

        ok = duplicate = fail = 0
        for i, a in enumerate(todo, 1):
            try:
                path, size = download_fit(cn, a)
                resp = intl.upload_activity(str(path))
                target_id = None
                if isinstance(resp, dict):
                    result = resp.get("detailedImportResult") or {}
                    uuid = (result.get("uploadUuid") or {}).get("uuid") or ""
                    target_id = str(result.get("uploadId") or uuid)
                elif isinstance(resp, list) and resp:
                    target_id = str(resp[0].get("activityId") or resp[0].get("detailedActivityId") or "")
                record_upload("garmin_intl", str(a["activityId"]), target_id)
                ok += 1
                print(f"[{i}/{len(todo)}] ok {a['startTimeLocal']} -> target={target_id} ({size}B)")
                path.unlink(missing_ok=True)
                time.sleep(args.delay)
            except Exception as exc:
                if "409" in str(exc) and "Duplicate Activity" in str(exc):
                    record_upload("garmin_intl", str(a["activityId"]), "duplicate-existing")
                    duplicate += 1
                    print(
                        f"[{i}/{len(todo)}] duplicate "
                        f"{a['startTimeLocal']} {a.get('activityName')}",
                        file=sys.stderr,
                    )
                else:
                    fail += 1
                    print(
                        f"[{i}/{len(todo)}] FAIL "
                        f"{a['startTimeLocal']} {a.get('activityName')}: {exc}",
                        file=sys.stderr,
                    )
                time.sleep(args.delay * 2)
        print(f"intl push done: ok={ok} duplicate={duplicate} fail={fail}")

    if args.dry_run:
        if args.push_coros or args.export_fit_dir:
            coros_missing = find_coros_missing(
                cn_acts, start_text, datetime.now().strftime("%Y-%m-%d")
            )
            print(f"coros push: {len(coros_missing)} missing")
            for a in coros_missing[:15]:
                print(
                    f"  would export: {a['startTimeLocal']} "
                    f"{(a.get('activityType') or {}).get('typeKey')} "
                    f"{a.get('distance')}m"
                )
        return 0

    export_dir = Path(args.export_fit_dir or "data/fit_for_coros")
    if not export_dir.is_absolute():
        export_dir = ROOT / export_dir
    if args.export_fit_dir or args.push_coros:
        end = datetime.now().strftime("%Y-%m-%d")
        exports = export_for_coros(cn, cn_acts, export_dir, start_text, end)
        if args.push_coros:
            unresolved = import_coros_exports(
                exports,
                ROOT / "data" / "coros_import_zips",
                args.coros_batch_size,
                args.coros_batch_mb,
                args.coros_import_timeout,
            )
            if unresolved:
                print(
                    f"COROS import has {unresolved} unresolved file(s); "
                    "rerun after fixing the saved ZIP/report",
                    file=sys.stderr,
                )
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
