#!/usr/bin/env node
// Pulls running activities from the existing coros-data-skill and prints
// a normalized JSON array on stdout. No skill files are modified.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SKILL_DIR =
  process.env.COROS_SKILL_DIR ||
  fileURLToPath(new URL("../../../scripts", import.meta.url));

function arg(name, fallback = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

function loadEnvFile(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2];
  }
}

function ymd(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}${m}${d}`;
}

const RUN_MODES = new Set([100, 101, 102, 103]);

function classify(sportType) {
  return RUN_MODES.has(sportType) ? "run" : `other-${sportType}`;
}

loadEnvFile(path.join(SKILL_DIR, ".env"));

if (!process.env.COROS_ACCOUNT || !process.env.COROS_PASSWORD) {
  console.error("coros: COROS_ACCOUNT / COROS_PASSWORD not configured");
  process.exit(1);
}

const endArg = arg("end");
const startArg = arg("start");
const modes = arg("modes", "100,101,102,103");

const end = endArg ? new Date(`${endArg}T23:59:59`) : new Date();
const start = startArg ? new Date(`${startArg}T00:00:00`) : new Date(end);
if (!startArg) start.setDate(start.getDate() - 29);

const { CorosClient } = await import(
  pathToFileURL(path.join(SKILL_DIR, "coros.js")).href
);

const client = new CorosClient(
  process.env.COROS_ACCOUNT,
  process.env.COROS_PASSWORD,
);
await client.login();

const size = 100;
let page = 1;
let emptyRetries = 0;
const rows = [];
while (true) {
  let list = [];
  try {
    const res = await client.authedAxios.get(
      `/activity/query?modeList=${modes}&pageNumber=${page}&size=${size}` +
        `&startDay=${ymd(start)}&endDay=${ymd(end)}`,
    );
    list = res.data?.data?.dataList || [];
  } catch {
    // transient API failure: fall through to the empty-page retry below
  }
  if (list.length === 0 && emptyRetries < 2) {
    emptyRetries += 1;
    await new Promise((r) => setTimeout(r, 1000));
    continue;
  }
  rows.push(...list);
  if (list.length < size || page >= 200) break;
  page += 1;
}

const records = rows.map((a) => ({
  source: "coros",
  source_id: String(a.labelId),
  sport: classify(a.sportType),
  raw_sport: a.sportType,
  start_utc: new Date(a.startTime * 1000).toISOString(),
  duration_s: a.totalTime ?? null,
  distance_m: a.distance ?? null,
  avg_hr: a.avgHr ?? null,
  max_hr: null,
  load: a.trainingLoad ?? null,
  // COROS stores calorie in cal; normalize to kcal for cross-source compare.
  calories: a.calorie ? Math.round(a.calorie / 1000) : null,
  device: a.device ?? null,
  name: a.name ?? null,
}));

process.stdout.write(JSON.stringify(records, null, 2) + "\n");
