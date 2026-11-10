#!/usr/bin/env node
// Download COROS originals in one authenticated session and cache them for
// a Python uploader. Input is a JSON array read from stdin; output is JSON.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SKILL_DIR =
  process.env.COROS_SKILL_DIR ||
  fileURLToPath(new URL("../../scripts", import.meta.url));

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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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

function looksLikeFit(data) {
  // Standard FIT files put .FIT at offset 8 after the 14-byte header.
  const direct = data.subarray(0, 4);
  const afterHeader = data.subarray(8, 12);
  const magic = Buffer.from(".FIT");
  const magicOld = Buffer.from("MFIT");
  return direct.equals(magic) ||
    direct.equals(magicOld) ||
    afterHeader.equals(magic) ||
    afterHeader.equals(magicOld);
}

function cacheName(record) {
  const start = String(record.start_utc).replace(/[-:]/g, "").replace(/\..*Z$/, "");
  return `${start}_${String(record.source_id).replace(/[^A-Za-z0-9._-]/g, "_")}.fit`;
}

loadEnvFile(path.join(SKILL_DIR, ".env"));

if (!process.env.COROS_ACCOUNT || !process.env.COROS_PASSWORD) {
  console.error("coros: COROS_ACCOUNT / COROS_PASSWORD not configured");
  process.exit(1);
}

const cacheDir = path.resolve(arg("cache-dir", "data/coros_originals"));
const delayMs = Number(arg("delay-ms", "500"));
let manifest;
try {
  manifest = JSON.parse(fs.readFileSync(0, "utf8"));
} catch (error) {
  console.error(`coros download: invalid stdin manifest: ${error.message}`);
  process.exit(1);
}
if (!Array.isArray(manifest)) {
  console.error("coros download: stdin manifest must be a JSON array");
  process.exit(1);
}

fs.mkdirSync(cacheDir, { recursive: true });
const { CorosClient } = await import(
  pathToFileURL(path.join(SKILL_DIR, "coros.js")).href
);
const client = new CorosClient(process.env.COROS_ACCOUNT, process.env.COROS_PASSWORD);
await loginWithRetry(client);

const results = [];
for (const [index, record] of manifest.entries()) {
  const name = cacheName(record);
  const cachePath = path.join(cacheDir, name);
  const existing = fs.existsSync(cachePath) ? fs.readFileSync(cachePath) : null;
  try {
    if (existing && looksLikeFit(existing)) {
      results.push({
        source_id: String(record.source_id),
        path: cachePath,
        size: existing.length,
        cached: true,
      });
      console.error(`coros download: cached ${index + 1}/${manifest.length} ${name}`);
      continue;
    }

    const { buffer, fileUrl } = await client.downloadActivityFile(
      String(record.source_id),
      record.raw_sport,
      4,
    );
    if (!looksLikeFit(buffer)) {
      throw new Error(`download is not a FIT file (${buffer.subarray(0, 12).toString("hex")})`);
    }
    const tempPath = `${cachePath}.tmp-${process.pid}-${index}`;
    fs.writeFileSync(tempPath, buffer);
    fs.renameSync(tempPath, cachePath);
    results.push({
      source_id: String(record.source_id),
      path: cachePath,
      size: buffer.length,
      cached: false,
      fileUrl,
    });
    console.error(`coros download: ok ${index + 1}/${manifest.length} ${name}`);
  } catch (error) {
    results.push({
      source_id: String(record.source_id),
      path: null,
      error: error.message,
    });
    console.error(`coros download: FAIL ${index + 1}/${manifest.length} ${name}: ${error.message}`);
  }
  if (delayMs > 0) await sleep(delayMs);
}

process.stdout.write(JSON.stringify(results, null, 2) + "\n");
