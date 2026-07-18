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
| `fetchTrainingPrograms()` | — | 训练课表列表（含 exerciseBarChart） |
| `fetchTrainingProgramDetail(programId)` | program ID | 单个课表完整详情（含 exercises 分段结构） |
| `formatWeeklyPlanForCoros(weekPlan)` | 周计划数组 | 生成 COROS 导入格式文本（手动导入用） |
| `createPlan(name, totalDay, dayEntries, overview)` | 名称, 天数, 每天条目, 概述 | 创建 COROS 训练计划并同步到手表 |
| `createProgram(name, segments, overview)` | 名称, 分段数组, 描述 | 创建训练课程（返回完整 program 对象） |

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

## COROS 训练计划数据模型

训练课表 (program) 的 exercises 数组结构：

| 字段 | 说明 | 示例 |
|------|------|------|
| exerciseType | 1=热身 2=主体(跑) 3=冷身 4=休息 | 2 |
| targetType | 2=时间(秒) 5=距离(厘米) | 5 |
| targetValue | 距离(cm)或时间(秒) | 200000=2km 150=150s |
| isGroup | true 时为重复组容器 | true |
| sets | 重复组数 | 9 |
| groupId | 子段归属的组 ID | "xxx" |

### 计划创建 API

`createPlan()` 方法已成功实现自动创建训练计划（通过逆向 COROS Training Hub 前端代码）。
关键参数结构:
- entities: 天映射条目 {happenDay, idInPlan, sortNoInSchedule, dayNo, exerciseBarChart}
- programs: 完整训练课程数据数组（每个含 exercises + idInPlan）
- versionObjects: 版本对象 {id, status: 1}

示例:
```js
const prog = await client.createProgram('E 14km', [
  { type: 'warmup', distanceMeters: 2000 },
  { type: 'effort', distanceMeters: 10000 },
  { type: 'cooldown', distanceMeters: 2000 },
]);
const planId = await client.createPlan('周计划', 7, [
  { dayNo: 0, programId: prog.id },
  { dayNo: 2, programId: anotherProg.id },
]);
```
