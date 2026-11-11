#!/usr/bin/env python3
"""Merge COROS + Garmin (CN / International) activities with cross-device dedupe."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sports import sports_compatible

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
DB_PATH = DATA_DIR / "activities.db"
JSON_PATH = OUT_DIR / "merged_activities.json"

SOURCE_LABELS = {
    "coros": "C",
    "garmin_intl": "G国际",
    "garmin_cn": "G国行",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_activities (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    sport TEXT,
    start_utc TEXT NOT NULL,
    duration_s REAL,
    distance_m REAL,
    avg_hr REAL,
    max_hr REAL,
    load REAL,
    calories REAL,
    device TEXT,
    name TEXT,
    raw_sport TEXT,
    group_id TEXT,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_activities_start ON source_activities(start_utc);
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


def window(args):
    end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else datetime.now()
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d")
    elif args.years:
        start = end - timedelta(days=round(args.years * 365.25))
    else:
        start = end - timedelta(days=args.days - 1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def run_source(cmd, env_extra, label):
    env = dict(os.environ)
    env.update(env_extra)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[warn] {label}: 启动失败: {exc}", file=sys.stderr)
        return []
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else "unknown error"
        print(f"[warn] {label}: {tail}", file=sys.stderr)
        return []
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"[warn] {label}: 输出不是合法 JSON，已跳过", file=sys.stderr)
        return []


def fetch_sources(args, start, end):
    records = []
    venv_python = ROOT / ".venv" / "bin" / "python"
    python_bin = str(venv_python) if venv_python.exists() else sys.executable
    if not args.no_coros:
        records += run_source(
            [
                "node",
                str(ROOT / "sources" / "coros_source.mjs"),
                "--all-sports",
                "--start",
                start,
                "--end",
                end,
            ],
            {},
            "coros",
        )
    for label, cn_flag in (("garmin_intl", False), ("garmin_cn", True)):
        if args.no_garmin:
            continue
        email_key = f"{label.upper()}_EMAIL"
        if not os.environ.get(email_key):
            print(f"[skip] {label}: 未配置 {email_key}", file=sys.stderr)
            continue
        cmd = [
            python_bin,
            str(ROOT / "sources" / "garmin_source.py"),
            "--start", start,
            "--end", end,
            "--tokenstore", str(DATA_DIR / "tokens" / label),
            "--label", label,
        ]
        if cn_flag:
            cmd.append("--cn")
        records += run_source(
            cmd,
            {
                "GARMIN_EMAIL": os.environ.get(email_key, ""),
                "GARMIN_PASSWORD": os.environ.get(f"{label.upper()}_PASSWORD", ""),
            },
            label,
        )
    return records


def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def rel_diff(x, y):
    return abs(x - y) / min(x, y)


def same_workout(a, b, max_gap_s):
    gap = abs((parse_ts(a["start_utc"]) - parse_ts(b["start_utc"])).total_seconds())
    if gap > max_gap_s:
        return False
    da, db = a.get("distance_m"), b.get("distance_m")
    ta, tb = a.get("duration_s"), b.get("duration_s")
    has_distance = bool(da and db and min(da, db) > 200)
    has_duration = bool(ta and tb and min(ta, tb) > 30)
    if not sports_compatible(
        a.get("sport"),
        b.get("sport"),
        allow_strength_cardio=not has_distance and gap <= 5,
        allow_cardio_run=True,
    ):
        return False
    if not has_distance and not has_duration:
        return gap <= 60

    distance_strong = has_distance and rel_diff(da, db) <= 0.02
    distance_plausible = has_distance and rel_diff(da, db) <= 0.20
    # COROS can retain a different elapsed duration for the same imported
    # workout. An exact start plus distance/no-distance is stronger evidence
    # than duration and is also needed for strength/cardio activities.
    duration_close = has_duration and (
        abs(ta - tb) <= 30 or rel_diff(ta, tb) <= 0.10
    )
    if gap <= 5 and (
        not has_distance or distance_strong or distance_plausible or duration_close
    ):
        return True

    distance_close = has_distance and rel_diff(da, db) <= 0.08
    duration_plausible = has_duration and rel_diff(ta, tb) <= 0.25
    if has_distance and has_duration:
        return (
            (distance_close and duration_plausible)
            or (duration_close and distance_plausible)
        )
    return distance_close or duration_close


def load_activity_links():
    if not DB_PATH.exists():
        return set()
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """CREATE TABLE IF NOT EXISTS activity_links (
             source TEXT NOT NULL,
             source_id TEXT NOT NULL,
             target TEXT NOT NULL,
             target_id TEXT NOT NULL,
             linked_at TEXT NOT NULL,
             evidence TEXT NOT NULL,
             PRIMARY KEY (source, source_id, target)
           )"""
    )
    links = {
        tuple(str(value) for value in row)
        for row in con.execute(
            "SELECT source, source_id, target, target_id FROM activity_links"
        )
    }
    con.close()
    return links


def records_are_linked(left, right, links):
    keys = {
        (
            str(left["source"]), str(left["source_id"]),
            str(right["source"]), str(right["source_id"]),
        ),
        (
            str(right["source"]), str(right["source_id"]),
            str(left["source"]), str(left["source_id"]),
        ),
    }
    return bool(links.intersection(keys))


def record_is_linked(record, links):
    identity = (str(record["source"]), str(record["source_id"]))
    return any(key[:2] == identity or key[2:] == identity for key in links)


def dedupe(records, max_gap_s, links=None):
    links = links or set()
    records = sorted(
        records,
        key=lambda r: (not record_is_linked(r, links), r["start_utc"]),
    )
    ts = [parse_ts(r["start_utc"]) for r in records]
    groups = []
    max_gap = timedelta(seconds=max_gap_s)
    for i, rec in enumerate(records):
        placed = False
        for g in groups:
            linked_member = next(
                (
                    m for m in g["members"]
                    if records_are_linked(m, rec, links)
                ),
                None,
            )
            if linked_member is not None:
                g["members"].append(rec)
                g["linked"] = True
                placed = True
                break
            # Anchor rule: every member must sit within max_gap of the group's
            # earliest start, so chains of consecutive reps never fuse together.
            # Garmin can preserve the upload link but move an imported activity
            # by many hours. A group containing a forced link may therefore be
            # extended only through the normal gap and metric checks below.
            group_is_linked = g.get("linked", False)
            if ts[i] - g["anchor"] > max_gap and not group_is_linked:
                continue
            for m in g["members"]:
                gap = abs(ts[i] - parse_ts(m["start_utc"]))
                if m["source"] == rec["source"]:
                    # Same-source duplicates (double upload) share the exact start.
                    tiny_orphan = (
                        gap <= max_gap
                        and not (rec.get("distance_m") or m.get("distance_m"))
                        and min(rec.get("duration_s") or 0, m.get("duration_s") or 0) <= 5
                    )
                    if (gap == timedelta(0) and same_workout(m, rec, 0)) or tiny_orphan:
                        g["members"].append(rec)
                        placed = True
                        break
                elif gap <= max_gap and same_workout(m, rec, max_gap_s):
                    g["members"].append(rec)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            groups.append({"anchor": ts[i], "members": [rec], "linked": False})
    return [g["members"] for g in groups]


def priority_index(source):
    order = [s.strip() for s in os.environ.get("TRAINING_SYNC_PRIMARY", "coros,garmin_intl,garmin_cn").split(",")]
    return order.index(source) if source in order else len(order)


METRIC_TOLERANCES = {
    "duration_s": 0.02,
    "distance_m": 0.01,
    "avg_hr": 2,
    "max_hr": 2,
    "load": 0.01,
    "calories": 0.05,
}


def metrics_agree(field, left, right):
    if left is None or right is None:
        return False
    tolerance = METRIC_TOLERANCES[field]
    if field in ("avg_hr", "max_hr"):
        return abs(left - right) <= tolerance
    if min(left, right) <= 0:
        return False
    return rel_diff(left, right) <= tolerance


def consensus_value(members, field):
    """Prefer a cross-source consensus over a single-source outlier."""
    values = []
    seen_sources = set()
    for member in sorted(members, key=lambda item: priority_index(item["source"])):
        source = member["source"]
        value = member.get(field)
        if value is None or source in seen_sources:
            continue
        seen_sources.add(source)
        values.append((source, value))

    if not values:
        return None

    clusters = []
    for source, value in values:
        cluster = next(
            (item for item in clusters if metrics_agree(field, item[0][1], value)),
            None,
        )
        if cluster is None:
            clusters.append([(source, value)])
        else:
            cluster.append((source, value))

    largest = max(len(cluster) for cluster in clusters)
    if largest == 1:
        return values[0][1]
    return min(
        (cluster for cluster in clusters if len(cluster) == largest),
        key=lambda cluster: priority_index(cluster[0][0]),
    )[0][1]


def build_canonical(groups):
    canonical = []
    for members in groups:
        members = sorted(members, key=lambda r: priority_index(r["source"]))
        primary = members[0]
        merged = dict(primary)
        merged["primary_source"] = primary["source"]
        merged["sources"] = sorted(m["source"] for m in members)
        merged["source_refs"] = [
            {"source": m["source"], "source_id": m["source_id"], "start_utc": m["start_utc"]}
            for m in sorted(members, key=lambda r: r["start_utc"])
        ]
        for field in ("duration_s", "distance_m", "avg_hr", "max_hr", "load", "calories"):
            merged[field] = consensus_value(members, field)
        for field in ("device",):
            if merged.get(field) is None:
                for m in members[1:]:
                    if m.get(field) is not None:
                        merged[field] = m[field]
                        break
        canonical.append(merged)
    canonical.sort(key=lambda r: r["start_utc"], reverse=True)
    return canonical


def update_db(records, canonical):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)

    for rec in records:
        group_id = None
        for c in canonical:
            if any(
                ref["source"] == rec["source"] and ref["source_id"] == rec["source_id"]
                for ref in c["source_refs"]
            ):
                group_id = f'{c["start_utc"]}-{c["sport"]}'
                break
        con.execute(
            """INSERT INTO source_activities
               (source, source_id, sport, start_utc, duration_s, distance_m,
                avg_hr, max_hr, load, calories, device, name, raw_sport, group_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, source_id) DO UPDATE SET
                 sport=excluded.sport, start_utc=excluded.start_utc,
                 duration_s=excluded.duration_s, distance_m=excluded.distance_m,
                 avg_hr=excluded.avg_hr, max_hr=excluded.max_hr, load=excluded.load,
                 calories=excluded.calories, device=excluded.device, name=excluded.name,
                 raw_sport=excluded.raw_sport, group_id=excluded.group_id""",
            (
                rec["source"], rec["source_id"], rec.get("sport"), rec["start_utc"],
                rec.get("duration_s"), rec.get("distance_m"), rec.get("avg_hr"),
                rec.get("max_hr"), rec.get("load"), rec.get("calories"),
                rec.get("device"), rec.get("name"), rec.get("raw_sport"), group_id,
            ),
        )
    con.commit()
    con.close()


def fmt_duration(seconds):
    if not seconds:
        return "--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def fmt_pace(distance_m, duration_s):
    if not distance_m or not duration_s or distance_m < 100:
        return "--"
    spk = duration_s / (distance_m / 1000)
    return f"{int(spk // 60)}'{int(spk % 60):02d}\""


def report(canonical, total_records, groups, start, end, report_limit):
    dup_groups = sum(1 for g in groups if len(g) > 1)
    lines = [
        f"=== 训练数据合并 {start} ~ {end} ===",
        f"{'日期':<10} {'时间':<6} {'类型':<4} {'距离':>7} {'时长':>8} {'配速':>7} {'均HR':>5} {'负荷':>5} 来源",
    ]
    shown = canonical[:report_limit]
    for c in shown:
        local = parse_ts(c["start_utc"]).astimezone()
        src = "+".join(SOURCE_LABELS.get(s, s) for s in c["sources"])
        weekday = local.strftime("%a")
        lines.append(
            f"{local:%m-%d} {weekday:<5} {local:%H:%M} {c['sport']:<4} "
            f"{(c.get('distance_m') or 0) / 1000:>6.2f}k {fmt_duration(c.get('duration_s')):>8} "
            f"{fmt_pace(c.get('distance_m'), c.get('duration_s')):>7} "
            f"{c.get('avg_hr') or '--':>5} {c.get('load') or '--':>5} {src}"
        )
    km = sum(c.get("distance_m") or 0 for c in canonical) / 1000
    hidden = len(canonical) - len(shown)
    if hidden > 0:
        lines.append(f"(表格仅显示最近 {len(shown)} 组，其余 {hidden} 组见 JSON / SQLite)")
    lines.append(
        f"\n汇总: {len(canonical)} 组 / {total_records} 条源记录 | 总距离 {km:.2f}km | 去重合并 {dup_groups} 组"
    )
    return "\n".join(lines)


def main():
    load_dotenv()
    p_args = argparse.ArgumentParser()
    p_args.add_argument("--days", type=int, default=30)
    p_args.add_argument("--years", type=float, help="回溯 N 年历史数据")
    p_args.add_argument("--start", help="YYYY-MM-DD，历史窗口起点（优先级高于 --years/--days）")
    p_args.add_argument("--end", help="YYYY-MM-DD")
    p_args.add_argument("--report-limit", type=int, default=50)
    p_args.add_argument("--no-coros", action="store_true")
    p_args.add_argument("--no-garmin", action="store_true")
    p_args.add_argument("--gap-seconds", type=int, default=60, help="跨设备同场活动最大起始时间差")
    args = p_args.parse_args()

    start, end = window(args)
    records = fetch_sources(args, start, end)
    groups = dedupe(records, args.gap_seconds, links=load_activity_links())
    canonical = build_canonical(groups)

    update_db(records, canonical)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n")

    print(report(canonical, len(records), groups, start, end, args.report_limit))
    print(f"\nSQLite: {DB_PATH}")
    print(f"JSON:   {JSON_PATH}")


if __name__ == "__main__":
    main()
