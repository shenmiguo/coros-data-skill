#!/usr/bin/env node
// Delete explicit COROS activities through the Training Hub web API.
// Callers must supply an audited allow-list; this script never discovers records.

import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import dotenv from "dotenv";

import { CorosClient } from "../../scripts/coros.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");

dotenv.config({ path: resolve(ROOT, "scripts/.env"), quiet: true });

function argValue(name, fallback = null) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

function sleep(ms) {
  return new Promise((done) => setTimeout(done, ms));
}

function progress(message) {
  console.error(`[coros] ${message}`);
}

async function main() {
  const idsPath = argValue("--label-ids-file");
  if (!idsPath) {
    throw new Error("usage: delete_coros_activities.mjs --label-ids-file <ids.json>");
  }
  const delayMs = Number(argValue("--delay", "0.5")) * 1000;
  const labelIds = JSON.parse(await readFile(resolve(idsPath), "utf8"));
  if (!Array.isArray(labelIds) || labelIds.length === 0) {
    throw new Error("label ID file must contain a non-empty JSON array");
  }

  const client = new CorosClient(
    process.env.COROS_ACCOUNT,
    process.env.COROS_PASSWORD,
  );
  progress("logging in");
  await client.login();

  const deleted = [];
  const failed = [];
  for (const [index, rawId] of labelIds.entries()) {
    const labelId = String(rawId);
    try {
      // The Training Hub request helper defaults to GET when method is omitted.
      const response = await client.authedAxios.get("/activity/delete", {
        params: { labelId },
        timeout: 30_000,
      });
      if (response.data?.result !== "0000") {
        throw new Error(
          `result=${response.data?.result || "unknown"} ` +
            `${response.data?.message || ""}`.trim(),
        );
      }
      deleted.push(labelId);
      progress(`[${index + 1}/${labelIds.length}] deleted ${labelId}`);
    } catch (error) {
      failed.push({ label_id: labelId, error: error.message });
      progress(`[${index + 1}/${labelIds.length}] FAILED ${labelId}: ${error.message}`);
    }
    if (index + 1 < labelIds.length) await sleep(delayMs);
  }

  console.log(JSON.stringify({ deleted, failed }));
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
