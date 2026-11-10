#!/usr/bin/env node
// Pulls activities from the existing coros-data-skill and prints a normalized
// JSON array on stdout. All sports are fetched unless --modes is supplied.

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

const SPORT_TYPE_MAP = new Map([
  [100, "run"],
  [101, "run"],
  [102, "run"],
  [103, "run"],
  [104, "hike"],
  [200, "bike"],
  [400, "cardio"],
  [402, "strength"],
  [900, "walk"],
  [901, "jump_rope"],
  [902, "stair_climb"],
  [9901, "stair_climb"],
  [10001, "multisport"],
]);

function classify(sportType) {
  return SPORT_TYPE_MAP.get(Number(sportType)) || `other-${sportType}`;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function loginWithRetry(client, attempts = 3) {
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await client.login();
      return;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) await sleep(attempt * 1000);
    }
  }
  throw lastError;
}

loadEnvFile(path.join(SKILL_DIR, ".env"));

if (!process.env.COROS_ACCOUNT || !process.env.COROS_PASSWORD) {
  console.error("coros: COROS_ACCOUNT / COROS_PASSWORD not configured");
  process.exit(1);
}

const endArg = arg("end");
const startArg = arg("start");
const modes = arg("modes");

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
await loginWithRetry(client);

const size = 100;
const CHUNK_DAYS = 180;
const rows = [];
const seen = new Set();

function addDays(date, days) {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}

// Wide date ranges make the COROS API intermittently return empty pages.
// Query in <=180-day chunks so each query stays small and pagination is stable.
let chunkStart = new Date(start);
while (chunkStart <= end) {
  const chunkEnd = new Date(
    Math.min(addDays(chunkStart, CHUNK_DAYS - 1).getTime(), end.getTime()),
  );
  let page = 1;
  while (true) {
    let list = null;
    let requestError = null;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const params = new URLSearchParams({
        pageNumber: String(page),
        size: String(size),
        startDay: ymd(chunkStart),
        endDay: ymd(chunkEnd),
      });
      if (modes) params.set("modeList", modes);
      try {
        const res = await client.authedAxios.get(
          `/activity/query?${params.toString()}`,
        );
        if (res.data?.result !== "0000") {
          throw new Error(
            `${res.data?.result || "unknown"} ${res.data?.message || ""}`.trim(),
          );
        }
        list = res.data?.data?.dataList || [];
      } catch (error) {
        requestError = error;
      }
      if (list !== null && list.length > 0) break;
      if (attempt < 3) await sleep(800 * (attempt + 1));
    }
    if (list === null) {
      throw new Error(
        `COROS query failed for ${ymd(chunkStart)}-${ymd(chunkEnd)} ` +
          `page ${page}: ${requestError?.message || "unknown error"}`,
      );
    }
    for (const a of list) {
      const id = String(a.labelId);
      if (!seen.has(id)) {
        seen.add(id);
        rows.push(a);
      }
    }
    if (list.length < size || page >= 50) break;
    page += 1;
  }
  chunkStart = addDays(chunkEnd, 1);
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
