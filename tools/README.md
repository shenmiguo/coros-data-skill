# Tools

每个工具独立成目录，共享 `scripts/` 里的 COROS SDK 和根部 `node_modules`。

| 工具 | 说明 | 用法 |
|------|------|------|
| `training-sync/` | COROS × Garmin(国行/国际) 活动数据打通，跨设备去重合并，支持历史回填 | 见 `training-sync/README.md` |
| `briefing/` | 每日教练简报生成（HTML 预览 / 邮件） | `node tools/briefing/generate_briefing.mjs --preview` |
| `dashboard/` | 教练仪表盘：运动员完整状态报告 | `node tools/dashboard/coach_dashboard.js` |
| `plan-creator/` | 周训练计划创建并同步 COROS 手表 | `node tools/plan-creator/create_w10_plan.mjs` |

依赖安装：仓库根目录 `npm install`（`scripts/` 自带一套供 SDK 自用）。
