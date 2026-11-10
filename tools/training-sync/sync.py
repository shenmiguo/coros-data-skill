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
            ["node", str(ROOT / "sources" / "coros_source.mjs"), "--start", start, "--end", end],
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
    signals = []
    da, db = a.get("distance_m"), b.get("distance_m")
    if da and db and min(da, db) > 200:
        signals.append(rel_diff(da, db) <= 0.08)
    ta, tb = a.get("duration_s"), b.get("duration_s")
    if ta and tb and min(ta, tb) > 30:
        signals.append(abs(ta - tb) <= 30 or rel_diff(ta, tb) <= 0.10)
    if not signals:
        return gap <= 60
    return all(signals)


def dedupe(records, max_gap_s):
    records = sorted(records, key=lambda r: r["start_utc"])
    n = len(records)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            gap = parse_ts(records[j]["start_utc"]) - parse_ts(records[i]["start_utc"])
            same_source = records[i]["source"] == records[j]["source"]
            # Same-source duplicates (double upload) share the exact start second;
            # distinct same-device sessions start at least tens of seconds apart.
            if same_source and gap > timedelta(0):
                break
            if not same_source and gap > timedelta(seconds=max_gap_s):
                break
            a, b = records[i], records[j]
            if a["sport"] != b["sport"]:
                continue
            if same_workout(a, b, 0 if same_source else max_gap_s):
                union(i, j)

    groups = {}
    for i, rec in enumerate(records):
        groups.setdefault(find(i), []).append(rec)
    return list(groups.values())


def priority_index(source):
    order = [s.strip() for s in os.environ.get("TRAINING_SYNC_PRIMARY", "coros,garmin_intl,garmin_cn").split(",")]
    return order.index(source) if source in order else len(order)


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
        for field in ("duration_s", "distance_m", "avg_hr", "max_hr", "load", "calories", "device"):
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
    p_args.add_argument("--gap-seconds", type=int, default=180, help="跨设备同场活动最大起始时间差")
    args = p_args.parse_args()

    start, end = window(args)
    records = fetch_sources(args, start, end)
    groups = dedupe(records, args.gap_seconds)
    canonical = build_canonical(groups)

    update_db(records, canonical)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n")

    print(report(canonical, len(records), groups, start, end, args.report_limit))
    print(f"\nSQLite: {DB_PATH}")
    print(f"JSON:   {JSON_PATH}")


if __name__ == "__main__":
    main()
