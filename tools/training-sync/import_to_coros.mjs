#!/usr/bin/env node
// Import one ZIP of Garmin FIT/TCX files into COROS.
//
// The COROS web app does not expose a public activity-upload API. This follows
// the same STS -> Aliyun OSS -> /activity/fit/import flow used by its web client.

import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import axios from "axios";
import OSS from "ali-oss";
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

function decodeSts(credentials) {
  const encoded = credentials.replace("9y78gpoERW4lBNYL", "");
  return JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
}

async function fetchSts(client) {
  const response = await axios.get("https://t.coros.com/api/proxy/oss/sts", {
    headers: {
      accept: "application/json, text/plain, */*",
      cookie: `CPL-coros-region=2; CPL-coros-token=${client.accessToken}`,
      origin: "https://t.coros.com",
      referer: "https://t.coros.com/",
    },
    timeout: 30_000,
  });
  const payload = response.data || {};
  if (payload.code !== 200 || !payload.data?.credentials) {
    throw new Error(`COROS STS failed: ${payload.message || payload.code || "unknown error"}`);
  }
  return {
    service: payload.data.service,
    ...decodeSts(payload.data.credentials),
  };
}

function uploadToAliyun(sts, objectName, filePath) {
  const client = new OSS({
    region: sts.Region,
    accessKeyId: sts.AccessKeyId,
    accessKeySecret: sts.AccessKeySecret,
    stsToken: sts.SecurityToken,
    bucket: sts.Bucket,
    secure: true,
  });
  return client.multipartUpload(objectName, filePath, {
    parallel: 4,
    partSize: 1024 * 1024,
    mime: "application/zip",
  });
}

async function requestImport(client, sts, filePath, md5, size) {
  const fileName = basename(filePath);
  const objectName = `fit_zip/${client.userId}/${md5}.zip`;
  if (sts.Service !== "aliyun" && sts.service !== "aliyun") {
    throw new Error(`unsupported COROS object storage service: ${sts.Service || sts.service}`);
  }
  progress(`uploading ${fileName} (${size}B)`);
  await uploadToAliyun(sts, objectName, filePath);
  progress(`upload complete: ${fileName}`);

  const parameter = {
    source: 1,
    timezone: (-new Date().getTimezoneOffset() / 60) * 4,
    bucket: sts.Bucket,
    md5,
    size,
    object: objectName,
    serviceName: sts.service,
    oriFileName: fileName,
  };
  const form = new FormData();
  form.append("jsonParameter", JSON.stringify(parameter));
  const response = await client.authedAxios.post("/activity/fit/import", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 60_000,
  });
  const importId = String(
    response.data?.id ||
      response.data?.data?.id ||
      response.data?.data ||
      "",
  );
  if (response.data?.result !== "0000" || !importId) {
    throw new Error(
      `COROS import request failed: ${response.data?.result || "unknown"} ` +
        `${response.data?.message || ""}`.trim(),
    );
  }
  progress(`import submitted: id=${importId}`);
  return importId;
}

async function waitForImport(client, importId, timeoutMs, intervalMs) {
  const deadline = Date.now() + timeoutMs;
  let lastStatus = null;
  while (Date.now() < deadline) {
    const response = await client.authedAxios.post("/activity/fit/getImportSportList", {
      size: 100,
    });
    const tasks = response.data?.data || [];
    const task = tasks.find((item) => String(item.id) === importId);
    if (task) {
      lastStatus = task.status;
      if (task.status === 2) {
        return {
          importId,
          status: task.status,
          fileCount: task.size,
          errorCount: task.errorSize || 0,
          originalFilename: task.originalFilename,
        };
      }
      if (task.status < 0) {
        throw new Error(
          `COROS import ${importId} failed: status=${task.status} ` +
            `errorCount=${task.errorSize || 0}`,
        );
      }
    }
    await sleep(intervalMs);
  }
  throw new Error(`COROS import ${importId} timed out; last status=${lastStatus}`);
}

async function main() {
  const zipPath = argValue("--zip");
  if (!zipPath) throw new Error("usage: import_to_coros.mjs --zip <activities.zip>");
  const filePath = resolve(zipPath);
  const timeoutMs = Number(argValue("--timeout-seconds", "900")) * 1000;
  const intervalMs = Number(argValue("--poll-seconds", "3")) * 1000;

  const client = new CorosClient(
    process.env.COROS_ACCOUNT,
    process.env.COROS_PASSWORD,
  );
  progress("logging in");
  await client.login();
  const account = await client.fetchAccount();
  client.userId = String(account.userId);

  progress("requesting upload credentials");
  const sts = await fetchSts(client);
  const bytes = await readFile(filePath);
  const md5 = createHash("md5").update(bytes).digest("hex");
  const importId = await requestImport(client, sts, filePath, md5, bytes.length);
  progress(`waiting for import ${importId}`);
  const result = await waitForImport(client, importId, timeoutMs, intervalMs);
  console.log(JSON.stringify(result));
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
