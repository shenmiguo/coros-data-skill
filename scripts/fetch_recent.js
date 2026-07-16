import { CorosClient } from "./coros.js";
import { computeDistance } from "./util.js";

const days = process.argv[2] || 7;
const endDate = process.argv[3] || null;

function formatDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}

function fmtDate(yyyymmdd) {
  const s = yyyymmdd.toString();
  return `${s.slice(4, 6)}/${s.slice(6, 8)}`;
}

function fmtPace(secPerKm) {
  if (!isFinite(secPerKm) || secPerKm <= 0) return "--";
  const m = Math.floor(secPerKm / 60);
  const s = Math.floor(secPerKm % 60);
  return `${m}'${String(s).padStart(2, "0")}"`;
}

function fmtDuration(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}m${String(s).padStart(2, "0")}s`;
}

const sportMap = { 100: "户外跑", 101: "室内跑", 102: "越野", 103: "跑步机" };

const client = new CorosClient(process.env.COROS_ACCOUNT, process.env.COROS_PASSWORD);
await client.login();

const end = endDate ? new Date(endDate) : new Date();
const start = new Date(end);
start.setDate(start.getDate() - days + 1);

const activities = await client.fetchActivity(formatDate(start), formatDate(end));

if (!activities || activities.length === 0) {
  console.log(`查询区间 ${fmtDate(formatDate(start))}-${fmtDate(formatDate(end))} 无跑步记录`);
  process.exit(0);
}

activities.sort((a, b) => a.date - b.date || a.startTime - b.startTime);

let totalDist = 0;
let totalDur = 0;

// group by date
const byDate = {};
for (const a of activities) {
  const key = a.date;
  if (!byDate[key]) byDate[key] = [];
  byDate[key].push(a);
}

console.log(`=== ${fmtDate(formatDate(start))} - ${fmtDate(formatDate(end))} 训练数据 ===\n`);

for (const key of Object.keys(byDate).sort((a, b) => a - b)) {
  const acts = byDate[key];
  let dayDist = 0;
  let dayLoad = 0;
  console.log(`📅 ${fmtDate(key)}`);
  for (const a of acts) {
    const km = (a.distance / 1000).toFixed(2);
    const pace = fmtPace(a.totalTime / (a.distance / 1000));
    const sport = sportMap[a.sportType] || `mode${a.sportType}`;
    console.log(`  ${sport} | ${a.name || ""} | ${km}km | ${fmtDuration(a.totalTime)} | ${pace}/km | HR:${a.avgHr} cad:${a.avgCadence} load:${a.trainingLoad} pow:${a.avgPower}W`);
    dayDist += a.distance;
    dayLoad += a.trainingLoad || 0;
  }
  console.log(`  日计: ${(dayDist / 1000).toFixed(2)}km | 负荷:${dayLoad}\n`);
  totalDist += dayDist;
  totalDur += acts.reduce((s, a) => s + a.totalTime, 0);
}

console.log(`📊 区间汇总: ${activities.length}次 | ${(totalDist / 1000).toFixed(2)}km | ${fmtDuration(totalDur)} | 均配速${fmtPace(totalDur / (totalDist / 1000))}/km`);
