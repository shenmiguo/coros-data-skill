#!/usr/bin/env node
// Download COROS originals in one authenticated session and cache them for
// a Python uploader. Input is a JSON array read from stdin; output is JSON.

import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
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

function looksLikeGpx(data) {
  return data.subarray(0, 256).includes("<gpx");
}

function cacheName(record, extension) {
  const start = String(record.start_utc).replace(/[-:]/g, "").replace(/\..*Z$/, "");
  return `${start}_${String(record.source_id).replace(/[^A-Za-z0-9._-]/g, "_")}.${extension}`;
}

function extractFitFromZip(zipData, cacheDir, token) {
  const zipPath = path.join(cacheDir, `.coros-${token}.zip`);
  fs.writeFileSync(zipPath, zipData);
  try {
    const listing = execFileSync("unzip", ["-Z1", zipPath], {
      encoding: "utf8",
      maxBuffer: 8 * 1024 * 1024,
    });
    const fitName = listing
      .split(/\r?\n/)
      .find((name) => name.trim().toLowerCase().endsWith(".fit"));
    if (!fitName) throw new Error("COROS ZIP contains no FIT entry");
    const fit = execFileSync("unzip", ["-p", zipPath, fitName.trim()], {
      maxBuffer: 128 * 1024 * 1024,
    });
    if (!looksLikeFit(fit)) {
      throw new Error("COROS ZIP FIT entry failed validation");
    }
    return fit;
  } finally {
    fs.unlinkSync(zipPath);
  }
}

async function ensureDownload(client, record, cacheDir, extension, fileType, index, total) {
  const cachePath = path.join(cacheDir, cacheName(record, extension));
  const existing = fs.existsSync(cachePath) ? fs.readFileSync(cachePath) : null;
  if (extension === "fit" && existing && looksLikeFit(existing)) {
    return { path: cachePath, size: existing.length, cached: true };
  }
  if (extension === "gpx" && existing && looksLikeGpx(existing)) {
    return { path: cachePath, size: existing.length, cached: true };
  }

  const download = await client.downloadActivityFile(
    String(record.source_id),
    record.raw_sport,
    fileType,
  );
  let { buffer, fileUrl } = download;
  if (extension === "fit" && buffer.subarray(0, 2).toString("ascii") === "PK") {
    buffer = extractFitFromZip(buffer, cacheDir, String(record.source_id));
  }
  if (extension === "fit" && !looksLikeFit(buffer)) {
    throw new Error(`download is not a FIT file (${buffer.subarray(0, 12).toString("hex")})`);
  }
  if (extension === "gpx" && !looksLikeGpx(buffer)) {
    throw new Error("download is not a GPX file");
  }
  const tempPath = `${cachePath}.tmp-${process.pid}-${index}`;
  fs.writeFileSync(tempPath, buffer);
  fs.renameSync(tempPath, cachePath);
  console.error(`coros download: ok ${extension} ${index + 1}/${total} ${path.basename(cachePath)}`);
  return { path: cachePath, size: buffer.length, cached: false, fileUrl };
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
  const fitName = cacheName(record, "fit");
  const gpxName = cacheName(record, "gpx");
  const item = {
    source_id: String(record.source_id),
    path: null,
    size: null,
    gpx_path: null,
    gpx_size: null,
    errors: [],
  };
  let fit = null;
  let gpx = null;
  try {
    fit = await ensureDownload(
      client,
      record,
      cacheDir,
      "fit",
      4,
      index,
      manifest.length,
    );
    item.path = fit.path;
    item.size = fit.size;
  } catch (error) {
    item.errors.push(`fit: ${error.message}`);
    console.error(`coros download: FAIL fit ${index + 1}/${manifest.length} ${fitName}: ${error.message}`);
  }

  try {
    gpx = await ensureDownload(
      client,
      record,
      cacheDir,
      "gpx",
      1,
      index,
      manifest.length,
    );
    item.gpx_path = gpx.path;
    item.gpx_size = gpx.size;
  } catch (error) {
    item.errors.push(`gpx: ${error.message}`);
    console.error(`coros download: FAIL gpx ${index + 1}/${manifest.length} ${gpxName}: ${error.message}`);
  }

  if (fit?.cached || gpx?.cached) item.cached = true;
  results.push(item);
  if (delayMs > 0) await sleep(delayMs);
}

process.stdout.write(JSON.stringify(results, null, 2) + "\n");
