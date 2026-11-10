# training-sync

打通 COROS、Garmin 国际（garmin.com）、Garmin 国行（garmin.com.cn）三个账号的运动活动数据，
跨设备去重后输出统一的活动列表。COROS 复用现有 `coros-data-skill`（不修改 skill 本身），
Garmin 走社区库 `garminconnect`（`is_cn=True/False` 切换国行/国际）。

## 配置

```bash
cd training-sync
cp .env.example .env
# 填入两个 Garmin 账号；COROS 自动读取 skill 自带的 scripts/.env
```

首次运行 Garmin 登录后，OAuth token 会缓存在 `data/tokens/<source>/`，
之后自动复用；只有 token 过期才需要重新登录（账号开了 2FA 时会提示输入验证码）。

## 使用

```bash
# 合并最近 30 天（默认窗口），输出去重报告 + SQLite + JSON
python3 sync.py

# 回溯历史数据：最近 N 年（首次运行会分页拉取全部历史）
python3 sync.py --years 3

# 或指定精确窗口（COROS 按日期范围直查；Garmin 从最新往回翻页到窗口起点）
python3 sync.py --start 2023-01-01 --end 2024-12-31

# 指定窗口与起始时间差阈值
python3 sync.py --days 90 --end 2026-11-10 --gap-seconds 180
```

只有配置了账号的源才会拉取；例如暂时只填 COROS 也能跑通，Garmin 会提示跳过。
历史回填与增量同步写的是同一张 SQLite 表：同一活动的重复拉取会按
`(source, source_id)` upsert，不会产生重复行；换窗口重跑也只是更新对应数据。

## 去重规则

两条不同源的活动会被判为同一场训练，需要同时满足：

1. 运动类型一致（run 对 run），起始时间差不超过 `--gap-seconds`（默认 180s）；
2. 距离都存在时相对误差 ≤ 8%，或时长都存在时差值 ≤ 30s / 相对误差 ≤ 10%。

同源（同一账号）记录只在起始时间完全相同、且距离/时长匹配时合并，
对应"同一活动被重复上传"的情况；同一手表相邻的独立训练（例如田径场
连续几组间歇，间隔几十秒）不会被误并。

合并时按 `TRAINING_SYNC_PRIMARY` 优先级选主记录（默认 COROS 优先），
缺失字段从其他源补齐，`sources` 字段记录该场训练的所有来源。

## 输出

- `out/merged_activities.json`：去重后的规范活动列表（含 `source_refs` 溯源）
- `data/activities.db`：SQLite 明细表，`group_id` 关联同一场训练的多条源记录
- stdout：终端报告（日期/距离/时长/配速/心率/负荷/来源）

## 后续扩展

- 生理指标（HRV/RHR/睡眠/恢复）可按同样的"源适配器 + 按日期去重"模式扩展；
- 若需要上传/回写（例如 COROS 活动导入 Garmin），可在源适配器上加 FIT 导出 +
  `garminconnect` 的 upload 接口，但上传属于写操作，建议默认关闭。
