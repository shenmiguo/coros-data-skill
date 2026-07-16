---
name: coros-data-skill
description: 查询高驰（COROS）运动手表的完整训练数据，包括跑步活动、每日生理指标（HRV/RHR/训练负荷/疲劳度）、心率配速区间、恢复状态等。当需要分析运动员训练状态、查看跑步记录或获取恢复指标时触发。
---

## 功能说明

本 skill 通过调用 COROS 官方 Training Hub API，实现以下功能：

1. **账号登录** — 账号 + MD5 密码登录获取 accessToken
2. **活动查询** — 按日期范围查询跑步活动（距离/配速/心率/负荷/功率）
3. **仪表盘数据** — LTHR/MaxHR/RHR、心率区间、配速区间、恢复百分比、HRV
4. **每日指标** — ATI/CTI/训练负荷(7d/28d)、疲劳度、表现指数、耐力水平
5. **账号档案** — 身高/体重/生日/最大心率/静息心率
6. **训练计划** — 查询已排的训练计划
7. **运动员快照** — 一次调用获取所有关键指标的结构化报告

## 环境变量配置

在 `scripts/.env` 文件中配置：

```env
COROS_ACCOUNT=<COROS 账号（手机号或邮箱）>
COROS_PASSWORD=<账号密码的 MD5 加密值>
```

## API 说明

### CorosClient 方法

| 方法 | 参数 | 说明 |
|------|------|------|
| `login()` | — | 登录并初始化鉴权 |
| `fetchActivity(startDay, endDay, size)` | YYYYMMDD | 跑步活动列表 |
| `fetchRecentActivities(days, endDate)` | 天数, YYYY-MM-DD | 最近 N 天活动 |
| `fetchDashboard()` | — | LTHR/MaxHR/RHR/HRV/恢复/区间/PR |
| `fetchDashboardDetail()` | — | 每日 ATI/CTI/负荷/疲劳/本周距离 |
| `fetchAccount()` | — | 身高/体重/生日/MaxHR/RHR |
| `fetchProfile()` | — | 显示偏好和运动模式配置 |
| `fetchTrainingPlans()` | — | 训练计划列表 |
| `fetchAthleteSnapshot()` | — | 一次性综合状态报告 |
| `fetchActivityDetail(labelId, sportType)` | 活动 ID, 运动类型 | 活动详情（含原始 lapList/summary/zoneList） |
| `fetchActivityLaps(labelId, sportType)` | 活动 ID, 运动类型 | 圈速解析（自动分类 effort/rest，含间歇汇总） |

### 工具函数 (util.js)

| 方法 | 说明 |
|------|------|
| `genHashedPassword(plainPassword)` | 明文密码转 MD5 |
| `computeDistance(activities)` | 活动列表总距离（米） |

### 格式化函数 (coros.js 导出)

| 函数 | 说明 |
|------|------|
| `fmtPace(secPerKm)` | 秒/km 转 `m'ss"` |
| `fmtDuration(sec)` | 秒转 `Xm XXs` |
| `fmtDate(yyyymmdd)` | `20260715` 转 `07/15` |
| `ymd(date)` | Date 对象转 `YYYYMMDD` |

## 使用方式

```js
import { CorosClient, fmtPace } from "./coros.js";

const client = new CorosClient(process.env.COROS_ACCOUNT, process.env.COROS_PASSWORD);
await client.login();

// 查询最近 7 天活动
const activities = await client.fetchRecentActivities(7);

// 获取完整运动员快照（推荐用于教练分析）
const snapshot = await client.fetchAthleteSnapshot();
console.log(snapshot.current.hrvToday, snapshot.trainingStatus.fatigue);
```

## 脚本

| 脚本 | 用途 |
|------|------|
| `coach_dashboard.js` | 教练仪表盘：完整运动员状态报告 |
| `fetch_recent.js <days> [endDate]` | 查询近期活动 |
| `coros.js` | API 客户端库 |
| `util.js` | 工具函数 |
