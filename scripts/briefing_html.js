/**
 * 训练简报 HTML 邮件模板生成器
 * 还原 7/22 设计风格：渐变头部、总结卡片、分段质量表、红黄绿灯、明日建议框、教练金句
 */

/**
 * 配速颜色 class
 * @param {number} paceSec s/km
 * @param {string} zone - 'E'|'M'|'T'|'I'|'R'|null
 */
function paceClass(paceSec, zone) {
  if (!paceSec || paceSec <= 0) return "";
  if (zone === "I" || zone === "R") return "pace-fast";
  if (zone === "T" || zone === "M") return "pace-good";
  return "";
}

/**
 * 根据 恢复%/HRV/RHR/疲劳/负荷比 综合给出灯
 * @returns {light:'green'|'yellow'|'red', label:string}
 */
function assessLight(rec, hrvRatio, rhr, fatigue, loadRatio) {
  let score = 0;
  if (rec >= 80) score++;
  else if (rec < 50) score--;
  if (hrvRatio >= 0.95) score++;
  else if (hrvRatio < 0.85) score--;
  if (rhr <= 45) score++;
  else if (rhr > 50) score--;
  if (loadRatio > 1.3) score--;
  else if (loadRatio >= 0.8 && loadRatio <= 1.2) score++;

  if (score >= 2) return { light: "green", label: "绿灯" };
  if (score <= -2) return { light: "red", label: "红灯" };
  return { light: "yellow", label: "yellow" };
}

function lightDesc(light) {
  const map = {
    green: "状态良好，可以执行高质量课",
    yellow: "急性疲劳正常，E 日即可消化",
    red: "恢复不足，降量降强或休息",
  };
  return map[light] || "";
}

/**
 * 生成完整 HTML 简报
 */
export function generateBriefingHTML(data) {
  const { light, label } = data.status;

  const todayRows = (data.today?.records || [])
    .map((r) => {
      const cls = r.cls || "";
      return `<tr><td>${r.name}</td><td class="${cls}">${r.dist}</td><td class="${cls}">${r.pace}</td><td class="${cls}">${r.hr}</td><td class="${cls}">${r.load}</td></tr>`;
    })
    .join("\n");

  const pills = (data.today?.pills || [])
    .map((p) => `<span class="pill">${p}</span>`)
    .join("\n");

  let segmentsHTML = "";
  if (data.segments && data.segments.length) {
    const segRows = data.segments
      .map((s) => `<tr><td><b>${s.zone}</b></td><td>${s.group}</td><td class="${s.cls || ""}">${s.pace}</td><td>${s.hr}</td><td>${s.note}</td></tr>`)
      .join("\n");
    segmentsHTML = `
    <section>
      <h2>🔥 主课分段质量</h2>
      <table>
        <tr><th>段</th><th>组</th><th>配速</th><th>均心率</th><th>评价</th></tr>
        ${segRows}
      </table>
      ${data.segmentNote ? `<div class="${data.segmentNoteCls || "good-box"}">${data.segmentNote}</div>` : ""}
    </section>`;
  }

  const weekRows = (data.weekly || [])
    .map((w) => {
      let color = "";
      if (w.status === "done") color = "";
      else if (w.status === "missed") color = `style="color:#dc3545;"`;
      else color = `style="color:#adb5bd;"`;
      return `<tr><td>${w.day}</td><td>${w.plan}</td><td ${color}>${w.actual}</td></tr>`;
    })
    .join("\n");

  const tomorrowDetails = (data.tomorrow?.details || [])
    .map((d) => `<tr><td>${d.label}</td><td class="val ${d.cls || ""}">${d.val}</td></tr>`)
    .join("\n");
  const tomorrowNotes = (data.tomorrow?.notes || [])
    .map((n) => `<li>${n}</li>`)
    .join("\n");

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body { font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif; margin:0; padding:0; background:#f0f2f5; color:#1a1a1a; }
.container { max-width:600px; margin:0 auto; background:#fff; }
.header { background:linear-gradient(135deg,#0d6efd,#0a58ca); color:#fff; padding:24px 28px; }
.header h1 { margin:0; font-size:22px; font-weight:700; }
.header .sub { margin:4px 0 0; font-size:13px; opacity:0.85; }
.body { padding:24px 28px; }
h2 { font-size:15px; font-weight:700; color:#0a58ca; margin:0 0 10px; padding-bottom:6px; border-bottom:2px solid #0d6efd; }
section { margin-bottom:24px; }
.summary-box { background:#f8f9fa; border-radius:10px; padding:16px; margin-bottom:12px; }
.summary-box .total { font-size:28px; font-weight:800; color:#0d6efd; }
.summary-box .unit { font-size:14px; color:#6c757d; }
.summary-row { display:flex; gap:12px; margin-top:8px; }
.pill { background:#e9ecef; border-radius:20px; padding:4px 12px; font-size:12px; color:#495057; }
table { width:100%; border-collapse:collapse; font-size:13px; margin-bottom:8px; }
th { background:#0a58ca; color:#fff; padding:8px 10px; text-align:left; font-weight:600; font-size:12px; }
td { padding:8px 10px; border-bottom:1px solid #e9ecef; }
tr:nth-child(even) td { background:#f8f9fa; }
.pace-good { color:#198754; font-weight:600; }
.pace-warn { color:#f59f00; font-weight:600; }
.pace-fast { color:#0d6efd; font-weight:600; }
.light { display:inline-block; width:14px; height:14px; border-radius:50%; vertical-align:middle; }
.light-yellow { background:#f59f00; }
.light-green { background:#198754; }
.light-red { background:#dc3545; }
.status-table td { padding:6px 10px; }
.status-table .val { font-weight:700; text-align:right; }
.ok { color:#198754; }
.warn { color:#f59f00; }
.alert-box { background:#fff3cd; border-left:4px solid #f59f00; border-radius:6px; padding:12px 14px; margin:10px 0; font-size:13px; }
.good-box { background:#d1e7dd; border-left:4px solid #198754; border-radius:6px; padding:12px 14px; margin:10px 0; font-size:13px; }
.tomorrow-box { background:#e7f5ff; border-radius:10px; padding:16px; margin-top:10px; }
.tomorrow-box .label { font-size:12px; color:#0a58ca; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; }
.tomorrow-box .type { font-size:18px; font-weight:800; margin:4px 0 8px; }
.quote { background:#f8f9fa; border-radius:10px; padding:14px 16px; font-style:italic; color:#495057; font-size:13px; margin-top:12px; }
.footer { text-align:center; padding:16px; font-size:11px; color:#adb5bd; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🏃 Wesley 训练简报</h1>
    <div class="sub">${data.dateStr} ${data.weekday} · ${data.weekLabel} · VDOT ${data.vdot}</div>
  </div>
  <div class="body">

    <section>
      <h2>📋 今日训练总结</h2>
      <div class="summary-box">
        <span class="total">${data.today?.dist || "--"}</span><span class="unit"> km</span>
        ${pills}
      </div>
      ${todayRows ? `<table>
        <tr><th>记录</th><th>距离</th><th>配速</th><th>均心率</th><th>负荷</th></tr>
        ${todayRows}
      </table>` : ""}
      ${data.todayNote ? `<p style="font-size:13px;margin:8px 0 4px;color:#495057;">${data.todayNote}</p>` : ""}
    </section>

    ${segmentsHTML}

    <section>
      <h2>📊 状态评估</h2>
      <p style="font-size:16px;margin:0 0 8px;"><span class="light light-${light}"></span> &nbsp;<b>${label}</b> — ${lightDesc(light)}</p>
      <table class="status-table">
        <tr><td>恢复%</td><td class="val">${data.status.recovery || "--"}% ${data.status.recoveryArrow || ""}</td></tr>
        <tr><td>完全恢复</td><td class="val">${data.status.fullHours || "--"} 小时</td></tr>
        <tr><td>HRV</td><td class="val">${data.status.hrv || "--"} <span style="color:#6c757d;font-size:11px;">(基线${data.status.hrvBase || "--"}, ${data.status.hrvDelta || ""})</span></td></tr>
        <tr><td>RHR</td><td class="val ${data.status.rhrCls || "ok"}">${data.status.rhr || "--"} ${data.status.rhrNote || ""}</td></tr>
        <tr><td>疲劳度</td><td class="val">${data.status.fatigue || "--"} ${data.status.fatiguePct ? `<span style="color:#6c757d;font-size:11px;">(${data.status.fatiguePct})</span>` : ""}</td></tr>
        <tr><td>负荷比</td><td class="val ${data.status.loadRatioCls || "ok"}">${data.status.loadRatio || "--"} (${data.status.loadRatioStatus || ""})</td></tr>
      </table>
      ${data.hrvTrend ? `<p style="font-size:12px;color:#6c757d;margin:8px 0 0;">${data.hrvTrend}</p>` : ""}
    </section>

    <section>
      <h2>📅 本周进展 · ${data.weekLabel}</h2>
      <table>
        <tr><th>日</th><th>计划</th><th>实际完成</th></tr>
        ${weekRows}
      </table>
      ${data.weekSummary || ""}
      ${data.alertBox ? `<div class="alert-box">${data.alertBox}</div>` : ""}
    </section>

    <section>
      <h2>🎯 目标追踪</h2>
      <p style="font-size:13px;margin:0;">${data.goal || ""}</p>
    </section>

    <section>
      <h2>📌 明日建议</h2>
      <div class="tomorrow-box">
        <div class="label">${data.tomorrow?.date || ""}</div>
        <div class="type">${data.tomorrow?.type || ""}</div>
        ${tomorrowDetails ? `<table class="status-table">${tomorrowDetails}</table>` : ""}
      </div>
      ${tomorrowNotes ? `<ul style="font-size:13px;color:#495057;padding-left:18px;margin:8px 0;">${tomorrowNotes}</ul>` : ""}
    </section>

    ${data.quote ? `<div class="quote">💬 ${data.quote}</div>` : ""}

  </div>
  <div class="footer">COROS Training Hub · 丹尼尔斯训练法 · Codex 教练系统</div>
</div>
</body>
</html>`;
}

export { paceClass, assessLight };
