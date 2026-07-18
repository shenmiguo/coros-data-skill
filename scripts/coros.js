import "dotenv/config";
import axios from "axios";

// ==================== 常量配置 ====================

const BASE = "https://teamcnapi.coros.com";

const DEFAULT_HEADERS = {
  accept: "application/json, text/plain, */*",
  "accept-language": "zh-CN,zh;q=0.9",
  "content-type": "application/json;charset=UTF-8",
  origin: "https://t.coros.com",
  referer: "https://t.coros.com/",
  "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
};

const SPORT_TYPES = { 100: "户外跑", 101: "室内跑", 102: "越野跑", 103: "操场跑" };

// ==================== 工具函数 ====================

function ymd(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}${m}${d}`;
}

function fmtPace(secPerKm) {
  if (!isFinite(secPerKm) || secPerKm <= 0) return "--";
  const m = Math.floor(secPerKm / 60);
  const s = Math.floor(secPerKm % 60);
  return `${m}'${String(s).padStart(2, "0")}"`;
}

function fmtDuration(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h}h${String(m).padStart(2, "0")}m`;
  return `${m}m${String(s).padStart(2, "0")}s`;
}

function fmtDate(yyyymmdd) {
  const s = String(yyyymmdd);
  return `${s.slice(4, 6)}/${s.slice(6, 8)}`;
}

// ==================== CorosClient 类 ====================

export class CorosClient {
  /**
   * @param {string} account COROS 账号（邮箱或手机号）
   * @param {string} password 已 MD5 加密的密码
   */
  constructor(account, password) {
    this.account = account;
    this.password = password;
    this.accessToken = null;
    this.authedAxios = null;
  }

  /** 登录并获取 accessToken */
  async login() {
    const res = await axios.post(
      `${BASE}/account/login`,
      { account: this.account, accountType: 2, pwd: this.password },
      { headers: DEFAULT_HEADERS, timeout: 60000 },
    );
    const token = res.data?.data?.accessToken;
    if (!token) throw new Error("登录失败，请检查 .env 中的 COROS_ACCOUNT 和 COROS_PASSWORD");
    this.accessToken = token;
    this.authedAxios = axios.create({
      baseURL: BASE,
      timeout: 240000,
      headers: {
        ...DEFAULT_HEADERS,
        accesstoken: token,
        cookie: `CPL-coros-region=2; CPL-coros-token=${token}`,
      },
    });
  }

  /** 确保 session 仍然活跃 */
  async ensureLogin() {
    if (!this.accessToken) await this.login();
  }

  // ==================== 活动查询 ====================

  /**
   * 查询跑步活动列表
   * @param {string} startDay YYYYMMDD
   * @param {string} endDay YYYYMMDD
   * @param {number} size 分页大小，默认 100
   */
  async fetchActivity(startDay, endDay, size = 100) {
    await this.ensureLogin();
    const modeList = "100,101,102,103";
    const res = await this.authedAxios.get(
      `/activity/query?modeList=${modeList}&pageNumber=1&size=${size}&startDay=${startDay}&endDay=${endDay}`,
    );
    return res.data?.data?.dataList || [];
  }

  /**
   * 查询最近 N 天的跑步活动
   * @param {number} days 天数
   * @param {string|null} endDate 截止日期 YYYY-MM-DD，默认今天
   */
  async fetchRecentActivities(days = 7, endDate = null) {
    const end = endDate ? new Date(endDate) : new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - days + 1);
    return this.fetchActivity(ymd(start), ymd(end));
  }

 // ==================== Dashboard 仪表盘 ====================

  /**
   * 获取仪表盘核心数据：LTHR/MaxHR/RHR/HRV/恢复/配速区间/心率区间/PR
   */
  async fetchDashboard() {
    await this.ensureLogin();
    const res = await this.authedAxios.get("/dashboard/query");
    return res.data?.data;
  }

  /**
   * 获取每日详情：ATI/CTI/训练负荷/疲劳度/表现（detailList）
   * 以及本周距离分布（currentWeekRecord/record）和今日摘要（summaryInfo）
   */
  async fetchDashboardDetail() {
    await this.ensureLogin();
    const res = await this.authedAxios.get("/dashboard/detail/query");
    return res.data?.data;
  }

  // ==================== 账号与档案 ====================

  /** 获取账号信息（身高/体重/生日/MaxHR/RHR 等） */
  async fetchAccount() {
    await this.ensureLogin();
    const res = await this.authedAxios.get("/account/query");
    return res.data?.data;
  }

  /** 获取用户档案（显示偏好、运动模式配置等） */
  async fetchProfile() {
    await this.ensureLogin();
    const res = await this.authedAxios.get("/profile/private/query");
    return res.data?.data;
  }

  // ==================== 训练计划 ====================

 /** 查询训练计划列表 */
 async fetchTrainingPlans() {
   await this.ensureLogin();
   const res = await this.authedAxios.post("/training/plan/query");
   return res.data?.data;
 }

  /**
   * 查询训练计划中的 program（课表内容）列表
   * 每个 program 包含 exerciseBarChart 和 exercises 详细结构
   */
  async fetchTrainingPrograms() {
    await this.ensureLogin();
    const res = await this.authedAxios.post("/training/program/query", {});
    return res.data?.data || [];
  }

  /**
   * 查询单个 program 的完整详情（含 exercises 分段结构）
   * @param {string} programId program ID
   */
  async fetchTrainingProgramDetail(programId) {
    await this.ensureLogin();
    const res = await this.authedAxios.post("/training/program/query", {
      programId,
    });
    const programs = res.data?.data || [];
    return programs.find((p) => p.id === programId) || programs[0] || null;
  }

  /**
   * 生成 COROS 格式的训练计划文本（供手动导入 COROS Training Hub）
   * 将 Daniels 训练法的周计划转为 COROS 可识别的结构化格式
   * @param {Array} weekPlan - 周计划数组，每天一个对象 {day, type, sessions: [{distance, pace, description}]}
   * @returns {string} 格式化的训练计划文本
   */
  formatWeeklyPlanForCoros(weekPlan) {
    const lines = [];
    lines.push("📋 COROS 训练计划（手动导入格式）\n");

    for (const day of weekPlan) {
      const dayName = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][day.dayOfWeek] || day.day;
      lines.push(`━━━ ${dayName} ${day.date || ""} ━━━`);

      if (day.type === "rest") {
        lines.push("  休息\n");
        continue;
      }

      for (const session of day.sessions || []) {
        const parts = [];
        if (session.warmup) parts.push(`热身 ${session.warmup}`);
        if (session.mainSet) {
          parts.push(`主体: ${session.mainSet}`);
          if (session.intervals) {
            for (const iv of session.intervals) {
              parts.push(`  ${iv.distance} @ ${iv.pace}` + (iv.rest ? ` + ${iv.rest}` : ""));
            }
          }
        }
        if (session.cooldown) parts.push(`冷身 ${session.cooldown}`);
        parts.push(`  预估 ${session.totalDistance || "--"}km`);
        lines.push(`  [${session.type || day.type}] ${parts.join(" → ")}`);
      }
      lines.push("");
    }

    lines.push("💡 导入方法: COROS App → 训练 → 训练计划 → 新建计划 → 按上述内容逐天添加");
    return lines.join("\n");
  }

  /**
   * 创建 COROS 训练计划（含训练课程），自动同步到手表
   *
   * 关键数据结构（通过逆向 COROS Training Hub 前端代码获得）:
   * - entities: 天映射条目 {happenDay, idInPlan, sortNoInSchedule, dayNo, exerciseBarChart}
   * - programs: 完整训练课程数据数组（每个含 exercises + idInPlan）
   * - versionObjects: 版本对象 {id, status: 1}
   *
   * @param {string} name - 计划名称
   * @param {number} totalDay - 总天数
   * @param {Array} dayEntries - 每天的训练数据: {dayNo, programId} 或 {dayNo, program}
   * @param {string} [overview] - 计划概述
   * @returns {Promise<string>} 创建的 planId
   */
  async createPlan(name, totalDay, dayEntries, overview = "") {
    await this.ensureLogin();
    const entities = [];
    const programs = [];
    const versionObjects = [];
    let maxIdInPlan = 0;

    for (const entry of dayEntries) {
      const idInPlan = ++maxIdInPlan;
      let program;
      if (entry.programId) {
        const progRes = await this.authedAxios.post("/training/program/query", {});
        program = (progRes.data?.data || []).find((p) => p.id === entry.programId);
        if (!program) throw new Error(`Program not found: ${entry.programId}`);
      } else if (entry.program) {
        const createRes = await this.authedAxios.post("/training/program/add", entry.program);
        const newProgId = createRes.data?.data;
        const progRes = await this.authedAxios.post("/training/program/query", {});
        program = (progRes.data?.data || []).find((p) => p.id === newProgId);
        if (!program) throw new Error("Failed to create program");
      } else {
        continue;
      }
      const prog = JSON.parse(JSON.stringify(program));
      prog.idInPlan = idInPlan;
      entities.push({
        happenDay: "",
        idInPlan,
        sortNoInSchedule: 0,
        dayNo: entry.dayNo,
        exerciseBarChart: prog.exerciseBarChart || [],
      });
      programs.push(prog);
      versionObjects.push({ id: idInPlan, status: 1 });
    }

    if (entities.length === 0) throw new Error("No valid training entries");
    const maxWeeks = Math.ceil(totalDay / 7);
    const body = {
      name, overview, entities, programs,
      weekStages: [], maxIdInPlan, totalDay, unit: 0,
      sourceId: "425868142590476288",
      sourceUrl: "https://oss.coros.com/source/source_default/0/6097a29cf17a435f88b573c08679280b.jpg",
      minWeeks: 1, maxWeeks, region: 2, pbVersion: 9, versionObjects,
    };
    const res = await this.authedAxios.post("/training/plan/add", body);
    if (res.data.result !== "0000") throw new Error(`Plan creation failed: ${res.data.message}`);
    return res.data.data;
  }

  /**
   * 创建训练课程 program（返回完整 program 对象）
   * @param {string} name - 课程名称
   * @param {Array} segments - 分段 [{type:'effort'|'warmup'|'cooldown'|'rest', distanceMeters?, durationSec?}]
   * @param {string} [overview] - 描述
   * @returns {Promise<object>} 完整 program 对象
   */
  async createProgram(name, segments, overview = "") {
    await this.ensureLogin();
    const exercises = [];
    const exerciseBarChart = [];
    let sortNo = 16777216;
    let idx = 0;
    for (const seg of segments) {
      const exerciseType = { warmup: 1, effort: 2, cooldown: 3, rest: 4 }[seg.type] || 2;
      const hasDist = seg.distanceMeters != null;
      const targetType = hasDist ? 5 : 2;
      const targetValue = hasDist ? Math.round(seg.distanceMeters * 100) : Math.round(seg.durationSec || 0);
      exercises.push({
        exerciseType, targetType, targetValue, targetDisplayUnit: hasDist ? 1 : 0,
        sortNo, sets: 1, name: `T${3000 + idx}`, originId: seg.type === "rest" ? "3" : "2",
        intensityType: 0, restType: 3, restValue: 0, sportType: 1, status: 1,
        groupId: "0", isGroup: false, isDefaultAdd: 0, isIntensityPercent: false,
        intensityPercent: 0, intensityPercentExtend: 0, intensityValue: 0,
        intensityValueExtend: 0, exerciseKind: 0, gradeSystem: 0, hrType: 0,
        subType: 0, animationId: 0, defaultOrder: 0, intensityCustom: 0,
        intensityDisplayUnit: 0, intensityMultiplier: 0, onsightGradeOffset: 24,
        packageTime: 0, sourceId: "0", sourceUrl: "", videoInfos: [], videoUrl: "",
      });
      exerciseBarChart.push({
        exerciseType, targetType, targetValue, value: targetValue,
        name: `T${3000 + idx}`, height: 5, width: Math.max(1, Math.round(targetValue / 20000)), widthFill: 0,
      });
      sortNo += 16777216;
      idx++;
    }
    const body = { name, sportType: 1, subType: 65535, exercises, exerciseBarChart, overview };
    const res = await this.authedAxios.post("/training/program/add", body);
    if (res.data.result !== "0000") throw new Error(`Program creation failed: ${res.data.message}`);
    const progRes = await this.authedAxios.post("/training/program/query", {});
    return (progRes.data?.data || []).find((p) => p.id === res.data.data);
  }

// ==================== 综合教练查询 ====================

/**
   * 查询单个活动的详细信息（含 lap 圈速数据）
   * @param {string} labelId 活动 ID
   * @param {number|string} sportType 运动类型（100/101/102/103）
   * @returns {Promise<object>} 包含 lapList、summary、zoneList、frequencyList 等
   */
  async fetchActivityDetail(labelId, sportType) {
    await this.ensureLogin();
    const res = await this.authedAxios.post(
      `/activity/detail/query?screenW=1024&screenH=1169&labelId=${labelId}&sportType=${sportType}`,
    );
    return res.data?.data;
  }

  /**
   * 查询单个活动的圈速解析（自动分类 effort/rest）
   * @param {string} labelId 活动 ID
   * @param {number|string} sportType 运动类型
   * @returns {Promise<object>} { summary, laps, intervals, autoLaps }
   */
  async fetchActivityLaps(labelId, sportType) {
    const detail = await this.fetchActivityDetail(labelId, sportType);
    if (!detail) return null;

    const summary = detail.summary || {};
    const lapGroups = detail.lapList || [];

    // Pick the most detailed lap view (most items = finest granularity)
    const detailedView =
      lapGroups.reduce((a, b) =>
        (a?.lapItemList?.length || 0) > (b?.lapItemList?.length || 0) ? a : b,
      ) || { lapItemList: [] };

    // Pick the 1km auto-lap view if available
    const autoLapView = lapGroups.find((l) => l.lapDistance === 100000);
    const autoLapItems = autoLapView?.lapItemList || detailedView.lapItemList;

    // Parse each lap item in the detailed view
    const laps = detailedView.lapItemList.map((item, i) => {
      const dist = (item.distance || 0) / 100; // cm -> m
      const time = (item.time || 0) / 100; // centiseconds -> s
      const pace = item.avgPace || 0; // s/km
      const isEffort = pace > 0 && pace < 360 && dist > 250;
      const isRest = pace > 500 || (dist < 150 && !isEffort);
      return {
        index: i + 1,
        distance: dist,
        duration: time,
        paceSec: pace,
        paceStr: pace > 0 ? fmtPace(pace) : "--",
        avgHr: item.avgHr || 0,
        maxHr: item.maxHr || 0,
        minHr: item.minHr || 0,
        avgCadence: item.avgCadence || 0,
        avgPower: item.avgPower || 0,
        type: isEffort ? "effort" : isRest ? "rest" : "other",
      };
    });

    // Auto-lap (1km) view
    const autoLaps = autoLapItems.map((item, i) => {
      const dist = (item.distance || 0) / 100;
      const pace = item.avgPace || 0;
      return {
        index: i + 1,
        distance: dist,
        paceStr: pace > 0 ? fmtPace(pace) : "--",
        avgHr: item.avgHr || 0,
        maxHr: item.maxHr || 0,
        avgCadence: item.avgCadence || 0,
        avgPower: item.avgPower || 0,
      };
    });

    // Group efforts into interval summary
    const efforts = laps.filter((l) => l.type === "effort");
    const intervals = summarizeEfforts(efforts);

    return {
      summary: {
       distance: summary.distance ? summary.distance / 100000 : 0,
       duration: (summary.totalLength || summary.time || summary.totalTime) ? (summary.totalLength || summary.time || summary.totalTime) / 100 : 0,
       avgPace: (summary.avgMoveSpeed || summary.adjustedPace) ? fmtPace(summary.avgMoveSpeed || summary.adjustedPace) : "--",
        avgMoveSpeed: summary.avgMoveSpeed || 0,
        avgHr: summary.avgHr || 0,
        maxHr: summary.maxHr || 0,
        avgCadence: summary.avgCadence || 0,
        avgPower: summary.avgPower || 0,
        trainingLoad: summary.trainingLoad || 0,
        bestKm: summary.bestKm ? fmtPace(summary.bestKm) : "--",
        aerobicEffect: summary.aerobicEffect || 0,
        anaerobicEffect: summary.anaerobicEffect || 0,
        calories: summary.calories || 0,
        sportType: summary.sportType || 0,
        variationIndex: summary.variationIndex || 0,
        efficiencyFactor: summary.efficiencyFactor || 0,
      },
      laps,
      intervals,
      autoLaps,
      zoneList: detail.zoneList || [],
    };
  }

  /**
   * 获取完整的运动员状态快照（一次调用获取所有关键指标）
   */
 async fetchAthleteSnapshot() {
    const [dashboard, detail, account] = await Promise.all([
      this.fetchDashboard(),
      this.fetchDashboardDetail(),
      this.fetchAccount(),
    ]);

    const si = dashboard?.summaryInfo || {};
    const hrv = si.sleepHrvData || {};
    const todaySummary = detail?.summaryInfo || {};
    const dailyMetrics = (detail?.detailList || []).map((d) => ({
      date: fmtDate(d.happenDay),
      happenDay: d.happenDay,
      ati: d.ati,
      cti: d.cti,
      t7d: d.t7d,
      t28d: d.t28d,
      fatigue: d.tiredRate,
      performance: d.performance,
      staminaLevel: d.staminaLevel,
      lthr: d.lthr,
      ltsp: d.ltsp ? fmtPace(d.ltsp) : null,
    }));

    const recentHrv = (hrv.sleepHrvList || []).map((h) => ({
      date: fmtDate(h.happenDay),
      hrv: h.avgSleepHrv,
      base: h.sleepHrvBase,
      sd: h.sleepHrvSd,
    }));

    return {
      athlete: {
        nickname: account?.nickname,
        stature: account?.stature,
        weight: account?.weight,
        sex: account?.sex,
        birthday: account?.birthday,
        maxHr: account?.maxHr,
        rhr: account?.rhr,
      },
      current: {
        lthr: si.lthr,
        ltsp: si.ltsp ? fmtPace(si.ltsp) : null,
        maxHr: si.fitnessMaxHr,
        rhr: si.rhr,
        recoveryPct: si.recoveryPct,
        recoveryState: si.recoveryState,
        fullRecoveryHours: si.fullRecoveryHours,
        hrvToday: hrv.avgSleepHrv,
        hrvBase: hrv.sleepHrvBase,
        aerobicEnduranceScore: si.aerobicEnduranceScore,
        anaerobicCapacityScore: si.anaerobicCapacityScore,
        lactateThresholdScore: si.lactateThresholdCapacityScore,
        staminaLevel: si.staminaLevel,
      },
      trainingStatus: {
        ati: todaySummary.ati,
        cti: todaySummary.cti,
        fatigue: todaySummary.tiredRate,
        fatigueNew: todaySummary.tiredRateNew,
        fatiguePct: todaySummary.tiredRateNewPercentInState,
        trainingLoadRatio: todaySummary.trainingLoadRatio,
      },
      hrZones: si.lthrZone?.map((z) => ({ index: z.index, hr: z.hr, ratio: z.ratio })) || [],
      paceZones: si.ltspZone?.map((z) => ({ index: z.index, pace: fmtPace(z.pace), ratio: z.ratio })) || [],
      recentHrv,
      dailyMetrics,
      weeklyDistance: detail?.record?.distanceRecord?.detailList?.map((d) => ({
        date: fmtDate(d.happenDay),
        distance: (d.value / 1000).toFixed(2),
        count: d.count,
      })) || [],
    };
  }
}

export { SPORT_TYPES, fmtPace, fmtDuration, fmtDate, ymd };

// ==================== Lap 解析工具函数 ====================

/**
 * 将 effort 段汇总为间歇组摘要
 */
function summarizeEfforts(efforts) {
  if (!efforts.length) return [];
  const avg = (arr) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0);
  const mn = (arr) => (arr.length ? Math.min(...arr) : 0);
  const mx = (arr) => (arr.length ? Math.max(...arr) : 0);
  const paces = efforts.map((e) => e.paceSec);
  const hrs = efforts.map((e) => e.avgHr);
  const maxHrs = efforts.map((e) => e.maxHr);
  const cads = efforts.map((e) => e.avgCadence);
  const powers = efforts.map((e) => e.avgPower);
  const dists = efforts.map((e) => e.distance);

  return [
    {
      count: efforts.length,
      distanceRange: `${mn(dists).toFixed(0)}-${mx(dists).toFixed(0)}m`,
      paceRange: `${fmtPace(mn(paces))}-${fmtPace(mx(paces))}`,
      avgPace: fmtPace(avg(paces)),
      avgHrRange: `${mn(hrs).toFixed(0)}-${mx(hrs).toFixed(0)}`,
      maxHrPeak: mx(maxHrs),
      cadenceRange: `${mn(cads).toFixed(0)}-${mx(cads).toFixed(0)}`,
      powerRange: `${mn(powers).toFixed(0)}-${mx(powers).toFixed(0)}W`,
      reps: efforts.map((e) => ({
        distance: e.distance.toFixed(0) + "m",
        pace: e.paceStr,
        avgHr: e.avgHr,
        maxHr: e.maxHr,
        cadence: e.avgCadence,
        power: e.avgPower,
      })),
    },
  ];
}
