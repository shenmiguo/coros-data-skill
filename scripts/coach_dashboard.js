import { CorosClient, fmtPace, fmtDuration, fmtDate, SPORT_TYPES } from "./coros.js";

const client = new CorosClient(process.env.COROS_ACCOUNT, process.env.COROS_PASSWORD);
await client.login();

// 获取完整快照 + 近期活动
const [snapshot, activities] = await Promise.all([
  client.fetchAthleteSnapshot(),
  client.fetchRecentActivities(7),
]);

const a = snapshot.athlete;
const c = snapshot.current;
const ts = snapshot.trainingStatus;

console.log("═══════════════════════════════════════════");
console.log(`  🏃 ${a.nickname} | ${a.stature}cm / ${a.weight}kg | MaxHR ${a.maxHr} | RHR ${a.rhr}`);
console.log("═══════════════════════════════════════════\n");

// 当前状态
console.log("📊 当前状态");
console.log(`   恢复: ${c.recoveryPct}% (${c.fullRecoveryHours}h 至完全恢复)`);
console.log(`   HRV: ${c.hrvToday} (基线 ${c.hrvBase})`);
console.log(`   RHR: ${c.rhr} | LTHR: ${c.lthr} | LT配速: ${c.ltsp}/km`);
console.log(`   有氧: ${c.aerobicEnduranceScore} | 无氧: ${c.anaerobicCapacityScore} | 乳酸阈: ${c.lactateThresholdScore} | 耐力: ${c.staminaLevel}`);

// 训练负荷
console.log("\n💪 训练负荷");
console.log(`   ATI: ${ts.ati} | CTI: ${ts.cti}`);
console.log(`   疲劳度: ${ts.fatigueNew}% (${ts.fatiguePct}%) | 负荷比: ${ts.trainingLoadRatio}`);

// 近 7 天 HRV 趋势
if (snapshot.recentHrv.length > 0) {
  console.log("\n❤️ 近期 HRV 趋势");
  for (const h of snapshot.recentHrv) {
    const trend = h.hrv >= h.base ? "↑" : "↓";
    console.log(`   ${h.date}: ${h.hrv} ${trend} (base ${h.base}, SD ${h.sd})`);
  }
}

// 近 7 天活动
if (activities.length > 0) {
  activities.sort((x, y) => x.date - y.date || x.startTime - y.startTime);
  console.log("\n📅 近 7 天训练");
  let totalDist = 0;
  let totalDur = 0;
  const byDate = {};
  for (const act of activities) {
    if (!byDate[act.date]) byDate[act.date] = [];
    byDate[act.date].push(act);
  }
  for (const day of Object.keys(byDate).sort((x, y) => x - y)) {
    const acts = byDate[day];
    let dayDist = 0;
    let dayLoad = 0;
    process.stdout.write(`   ${fmtDate(day)}: `);
    const parts = acts.map((act) => {
      const km = (act.distance / 1000).toFixed(2);
      const pace = fmtPace(act.totalTime / (act.distance / 1000));
      dayDist += act.distance;
      dayLoad += act.trainingLoad || 0;
      return `${SPORT_TYPES[act.sportType] || "?"} ${km}km ${pace}/km HR${act.avgHr}`;
    });
    console.log(parts.join(" + "));
    console.log(`         → ${(dayDist / 1000).toFixed(1)}km | 负荷 ${dayLoad}`);
    totalDist += dayDist;
    totalDur += acts.reduce((s, x) => s + x.totalTime, 0);
  }
  console.log(`   📈 周总计: ${(totalDist / 1000).toFixed(1)}km | ${fmtDuration(totalDur)}`);
}

// 心率区间
if (snapshot.hrZones.length > 0) {
  console.log("\n🎯 心率区间 (LTHR)");
  for (const z of snapshot.hrZones) {
    console.log(`   Z${z.index}: ${z.hr}bpm (${z.ratio}%)`);
  }
}

// 配速区间
if (snapshot.paceZones.length > 0) {
  console.log("\n🏃 配速区间 (LT)");
  for (const z of snapshot.paceZones) {
    console.log(`   P${z.index}: ${z.pace}/km (${z.ratio}%)`);
  }
}

console.log("\n═══════════════════════════════════════════");
