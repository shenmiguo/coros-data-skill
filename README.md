# coros-data-skill

个人运动数据工具箱：COROS Training Hub SDK + 一组独立工具。

## 结构

```
coros-data-skill/
├── SKILL.md            # Codex skill 入口（COROS 查询能力说明）
├── scripts/            # SDK：CorosClient / util / fetch_recent CLI / send_email
└── tools/              # 独立工具（每个工具一个目录）
    ├── training-sync/  # COROS × Garmin(国行/国际) 数据打通 + 跨设备去重
    ├── briefing/       # 每日教练简报（HTML / 邮件）
    ├── dashboard/      # 教练仪表盘（运动员状态报告）
    └── plan-creator/   # 周计划创建并同步手表
```

## 快速开始

```bash
npm install

# COROS SDK 冒烟测试（需 scripts/.env）
node scripts/fetch_recent.js 7

# 数据同步（需 tools/training-sync/.env 配置 Garmin 账号）
tools/training-sync/.venv/bin/python tools/training-sync/sync.py --days 30
```

各工具的详细用法见 `tools/README.md` 与各目录内的 README。
