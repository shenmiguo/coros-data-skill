import dotenv from "dotenv";
import { fileURLToPath } from "node:url";

dotenv.config({ path: fileURLToPath(new URL("../../scripts/.env", import.meta.url)) });
import { CorosClient, fmtPace, fmtDate, fmtDuration } from "../../scripts/coros.js";
import { generateBriefingHTML, assessLight } from "./briefing_html.js";
import { sendBriefingEmail } from "../../scripts/send_email.js";

// ---- Config ----
// 支持 --preview 参数：只生成 HTML 预览文件，不发邮件
const PREVIEW_ONLY = process.argv.includes("--preview");

const WEEK_LABEL = process.argv[2] || "W10 第1天";
const WEEK_GOAL_KM = parseInt(process.argv[3] || "93");

// ---- Fetch data ----
const client = new CorosClient(process.env.COROS_ACCOUNT, process.env.COROS_PASSWORD);
await client.login();

const activities = await client.fetchRecentActivities(2);
const sorted = (activities || []).sort((a, b) => a.date - b.date || a.startTime - b.startTime);
const snap = await client.fetchAthleteSnapshot();

// ---- Filter today's activities ----
const now = new Date();
const todayStr = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
const todayActs = sorted.filter((a) => String(a.date) === todayStr);
const yesterdayActs = sorted.filter((a) => String(a.date) !== todayStr);

// ---- Build today summary ----
let todayDist = 0, todayLoad = 0, todayDur = 0;
const records = [];
for (const a of todayActs) {
  const km = (a.distance / 1000).toFixed(2);
  const pace = fmtPace(a.totalTime / (a.distance / 1000));
  const sportName = { 100: "户外跑", 101: "室内跑", 102: "越野跑", 103: "操场跑" }[a.sportType] || "跑步";
  todayDist += a.distance;
  todayLoad += a.trainingLoad || 0;
  todayDur += a.totalTime;
  records.push({
    name: a.name || sportName,
    dist: `${km}km`,
    pace,
    hr: `${a.avgHr || "--"}${a.maxHr ? `(峰${a.maxHr})` : ""}`,
    load: String(a.trainingLoad || 0),
  });
}
const todayKm = (todayDist / 1000).toFixed(2);

// ---- Fetch lap details for today ----
let segments = [];
let segmentNote = "";
for (const a of todayActs) {
  const laps = await client.fetchActivityLaps(a.labelId, a.sportType);
  const intervals = laps?.intervals || [];
  for (const iv of intervals) {
    // intervals is nested (groups of reps) for interval sessions but flat
    // for track/race sessions; normalize before iterating.
    const groups = Array.isArray(iv) ? iv : [iv];
    for (const grp of groups) {
      if (grp.count && grp.count > 1) {
        segments.push({
          zone: "I",
          group: `${grp.count}×${grp.distanceRange}`,
          pace: `${grp.paceRange} (均${grp.avgPace})`,
          hr: `${grp.avgHrRange} 峰${grp.maxHrPeak}`,
          note: grp.paceRange.includes("3'1") ? "I 区间达标 ✓" : "偏慢，未达 I 区间",
          cls: "pace-fast",
        });
      }
    }
  }
}

// ---- Status assessment ----
const cur = snap.current || {};
const ts = snap.trainingStatus || {};
const hrvBase = cur.hrvBase || 56;
const hrvToday = cur.hrvToday || 0;
const hrvRatio = hrvToday / hrvBase;
const { light, label: lightLabel } = assessLight(
  cur.recoveryPct || 0, hrvRatio, cur.rhr || 43, ts.fatigue || 0, ts.trainingLoadRatio || 1,
);
const lightZh = { green: "绿灯", yellow: "黄灯", red: "红灯" }[light];

// HRV trend string
const hrvList = snap.recentHrv || [];
const hrvTrendStr = hrvList.length
  ? `7天HRV: ${hrvList.map(h => h.hrv).join("→")}。`
  : "";

// Load ratio assessment
const lr = ts.trainingLoadRatio || 0;
const lrStatus = lr > 1.3 ? "偏高 ⚠️" : lr >= 0.8 && lr <= 1.2 ? "生产区 ✓" : "偏低";
const lrCls = lr > 1.3 ? "warn" : "ok";
const rhrCls = (cur.rhr || 43) <= 45 ? "ok" : "warn";
const hrvDelta = hrvToday >= hrvBase ? `+${(hrvToday - hrvBase).toFixed(0)}` : `${(hrvToday - hrvBase).toFixed(0)}`;

// ---- Weekly progress ----
// We get weeklyDistance from snapshot (7 days). Map to plan.
const wd = snap.weeklyDistance || [];
const wdMap = {};
for (const w of wd) wdMap[w.date] = parseFloat(w.distance);
const weekTotal = Object.values(wdMap).reduce((a, b) => a + b, 0);

// W10 周计划：周三 T-I 阶梯课 + 周日LSD（已同步COROS手表）
const weekPlan = [
  { day: "周一 8/3", plan: "E 12km + 力量A", actual: `${todayKm}km E ✓`, status: "done" },
  { day: "周二 8/4", plan: "E 10km + strides", actual: "待执行", status: "pending" },
  { day: "周三 8/5", plan: "T-I阶梯 3km+2×2km+3×1km", actual: "待执行", status: "pending" },
  { day: "周四 8/6", plan: "E 10km", actual: "待执行", status: "pending" },
  { day: "周五 8/7", plan: "E 12km + 力量B", actual: "待执行", status: "pending" },
  { day: "周六 8/8", plan: "E 8km + strides", actual: "待执行", status: "pending" },
  { day: "周日 8/9", plan: "L 25km + 4km@M", actual: "待执行", status: "pending" },
];

// ---- Build data structure ----
const data = {
  dateStr: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`,
  weekday: ["周日","周一","周二","周三","周四","周五","周六"][now.getDay()],
  weekLabel: WEEK_LABEL,
  vdot: 63,
  today: {
    dist: todayKm,
    records,
    pills: [
      `负荷 ${todayLoad}`,
      `${records.length} 次记录`,
      `时长 ${fmtDuration(todayDur)}`,
    ],
  },
  todayNote: todayActs.length === 0 ? "今天还没有跑步记录。" : `均配速 ${fmtPace(todayDur / (todayDist / 1000))}/km`,
  segments,
  segmentNote,
  status: {
    recovery: cur.recoveryPct || "--",
    recoveryArrow: cur.recoveryPct >= 80 ? "↑" : "↓",
    fullHours: cur.fullRecoveryHours || "--",
    hrv: hrvToday,
    hrvBase,
    hrvDelta,
    rhr: cur.rhr || "--",
    rhrNote: "(基线 ✓)",
    rhrCls,
    fatigue: ts.fatigue || "--",
    fatiguePct: ts.fatiguePct ? `${ts.fatiguePct}%` : "",
    loadRatio: lr.toFixed(2),
    loadRatioStatus: lrStatus,
    loadRatioCls: lrCls,
    light,
    label: lightZh,
  },
  hrvTrend: hrvTrendStr,
  weekly: weekPlan,
  weekSummary: `<p style="font-size:13px;margin:8px 0 0;">7天已跑 <b>${weekTotal.toFixed(1)}km</b>，本周目标 <b>~${WEEK_GOAL_KM}km</b></p>`,
  alertBox: light === "yellow" || light === "red"
    ? `⚠️ <b>负荷提醒：</b>负荷比 ${lr.toFixed(2)} 偏高，E 日严格控心率 <148，让身体消化。`
    : "",
  goal: `东营马拉松 <b>2:32:00</b>（VDOT 62→65）。速度底座阶段 W10，VDOT 63。有氧 ${cur.aerobicEnduranceScore || "--"} / 无氧 ${cur.anaerobicCapacityScore || "--"} / 阈值 ${cur.lactateThresholdScore || "--"}。`,
  tomorrow: {
    date: "周二 8/4",
    type: "🏃‍♂️ E 10km + 6×100m strides",
    details: [
      { label: "配速区间", val: "4'25-4'45 /km", cls: "" },
      { label: "心率上限", val: "严格 <148 bpm", cls: "warn" },
      { label: "strides", val: "6×100m @ 3'01-3'05", cls: "pace-fast" },
      { label: "距离", val: "~10km", cls: "" },
    ],
    notes: [
      "周三才是重头戏：T-I阶梯课（3km+2×2km+3×1km）",
      "周二把腿溜开，strides 重在跑姿不冲心率",
      "力量A今天做了吗？没做的话明天补不了",
      "E日控距，别再超量，把配额留给周三",
    ],
  },
  quote: "今天E恢复跑做对了。周三的T-I阶梯课是本周硬骨头——3km入T、2km压T冲I、1km全力I。计划已同步手表，照着跑。",
};

// ---- Generate & send ----
const html = generateBriefingHTML(data);

// Text fallback for non-HTML clients
const textBriefing = `${data.dateStr} ${data.weekday} 训练简报\n\n今日: ${todayKm}km 负荷${todayLoad}\n恢复: ${cur.recoveryPct}% HRV ${hrvToday} RHR ${cur.rhr}\n明日: I 6×1km @ 3'13-3'17\n详见HTML邮件`;

// 保存预览（无论是否发邮件都保存，方便随时查看）
import { writeFileSync } from "node:fs";
writeFileSync("/Users/jedyi/Documents/Coach/.briefing_preview.html", html);

if (PREVIEW_ONLY) {
  console.log("Preview saved (no email sent).");
  process.exit(0);
}

import { sendMail } from "../../scripts/send_email.js";
try {
  const id2 = await sendMail({
    to: process.env.BRIEFING_MAIL_TO,
    subject: `Wesley 训练简报 · ${data.dateStr} ${data.weekday}`,
    text: textBriefing,
    html,
  });
  console.log("EMAIL_HTML_OK:", id2);
} catch (e) {
  console.log("EMAIL_HTML_SKIPPED:", e.message);
}
