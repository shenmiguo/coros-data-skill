# training-sync

打通 COROS、Garmin 国际（garmin.com）、Garmin 国行（garmin.com.cn）三个账号的运动活动数据，
跨设备去重后输出统一的活动列表。COROS 复用现有 `coros-data-skill`（不修改 skill 本身），
Garmin 走社区库 `garminconnect`（`is_cn=True/False` 切换国行/国际）。当前默认拉取
全部运动类型，不只跑步。COROS 源脚本另外支持 `--modes 100,101,102,103`，
可按 COROS 模式编号缩小查询范围；`sync.py` 默认拉取全部模式。

本目录包含以下独立工具：

- `sync.py`：从三个账号拉取历史/增量数据并去重，输出 SQLite/JSON；
- `push_cn_history.py`：把 Garmin 国行历史回传到 Garmin 国际和 COROS；
- `push_coros_history.py`：把缺少 Garmin 副本的 COROS 原始 FIT 反向回传到
  Garmin 国行和 Garmin 国际；
- `download_coros_history.mjs`：一次登录后批量下载并缓存 COROS 原始 FIT；
- `reconcile_coros_import.py`：对已提交但部分失败的 COROS ZIP 导入做现场核对和补记账；
- `fix_garmin_types.py`：按同组 COROS 运动类型修正 Garmin 导入误归为 other 的活动；
- `cleanup_coros_duplicates.py`：审计并清理本工具造成的 COROS 重复活动；
- `import_to_coros.mjs` / `delete_coros_activities.mjs`：底层网页接口工具，不建议手动直接调用。

## 配置

```bash
cd /path/to/coros-data-skill
cp tools/training-sync/.env.example tools/training-sync/.env
# 填入两个 Garmin 账号；COROS 自动读取 skill 自带的 scripts/.env
python3 -m venv tools/training-sync/.venv
tools/training-sync/.venv/bin/pip install -r tools/training-sync/requirements.txt
npm install
```

首次运行 Garmin 登录后，OAuth token 会缓存在 `data/tokens/<source>/`，
之后自动复用；只有 token 过期才需要重新登录（账号开了 2FA 时会提示输入验证码）。

## 使用

```bash
# 合并最近 30 天（默认窗口），输出去重报告 + SQLite + JSON
tools/training-sync/.venv/bin/python tools/training-sync/sync.py

# 回溯历史数据：最近 N 年（首次运行会分页拉取全部历史）
tools/training-sync/.venv/bin/python tools/training-sync/sync.py --years 3

# 回溯固定历史窗口（建议三源都使用同一起点）
tools/training-sync/.venv/bin/python tools/training-sync/sync.py \
  --start 2023-01-01 --end 2026-11-10

# 或指定精确窗口（COROS 按日期范围直查；Garmin 从最新往回翻页到窗口起点）
tools/training-sync/.venv/bin/python tools/training-sync/sync.py \
  --start 2023-01-01 --end 2024-12-31

# 指定窗口与起始时间差阈值
tools/training-sync/.venv/bin/python tools/training-sync/sync.py \
  --days 90 --end 2026-11-10 --gap-seconds 180
```

只有配置了账号的源才会拉取；例如暂时只填 COROS 也能跑通，Garmin 会提示跳过。
历史回填与增量同步写的是同一张 SQLite 表：同一活动的重复拉取会按
`(source, source_id)` upsert，不会产生重复行；换窗口重跑也只是更新对应数据。

## Garmin 国行回传

```bash
# 先预演：只显示 Garmin 国际缺少多少条，不写远端
tools/training-sync/.venv/bin/python tools/training-sync/push_cn_history.py \
  --years 3 --dry-run

# 同时检查国际区和 COROS；两边都为 0 才表示历史回填完成
tools/training-sync/.venv/bin/python tools/training-sync/push_cn_history.py \
  --start 2023-01-01 --dry-run --push-coros

# 国行 -> 国际；同时导出 COROS 缺少的 FIT/TCX 并自动批量导入 COROS
tools/training-sync/.venv/bin/python tools/training-sync/push_cn_history.py \
  --years 3 \
  --delay 1.5 \
  --push-coros \
  --export-fit-dir data/fit_for_coros

# 只导出 COROS 缺少的文件，不自动导入
tools/training-sync/.venv/bin/python tools/training-sync/push_cn_history.py \
  --years 3 \
  --no-push \
  --export-fit-dir data/fit_for_coros
```

回传是幂等的：

- 国行活动如果已经出现在国际区，或已记录在 `push_uploads` 表，会跳过；
- COROS 侧先按全运动类型、开始时间、距离/时长重新匹配，已存在的活动不会导出；
- 一个 ZIP 批量导入成功后，批次内每个国行 `activityId` 都写入 `push_uploads`，
  再次运行不会重复导入。

COROS 导入接口只返回批次级 `errorSize`，不返回失败文件名。脚本在部分失败时会重新拉取
COROS 现场数据，把确认出现的文件写入台账；未出现的文件保留在该 ZIP 和批报告中，
不会盲目把整批文件再次上传。若需要恢复已经中断的历史批次：

```bash
# 只读核对；--import-id 使用 COROS 返回的批次 ID
tools/training-sync/.venv/bin/python tools/training-sync/reconcile_coros_import.py \
  --zip data/coros_import_zips/<batch>.zip \
  --import-id <coros-import-id>

# 确认无误后，把已出现在 COROS 的文件写入台账
tools/training-sync/.venv/bin/python tools/training-sync/reconcile_coros_import.py \
  --zip data/coros_import_zips/<batch>.zip \
  --import-id <coros-import-id> \
  --execute
```

国行 FIT 通过 `download_activity(..., ORIGINAL)` 获取时实际可能是 ZIP 包装，也可能
直接返回 GPX。脚本会校验文件真实格式：有 FIT 时保留 FIT，否则自动改下 TCX；不再
按文件大小推断格式，因为 COROS 当前已接受小体积 TCX。

## COROS 反向回传 Garmin

`push_coros_history.py` 处理合并结果中缺少国行和/或国际记录的 COROS 组。它按组
只下载一次 COROS 原始 FIT，再分别上传到缺失目标。下载接口使用 Training Hub 的
`/activity/detail/download`（`fileType=4` 表示 FIT），并校验标准 FIT 文件头。

```bash
# 预演十年队列；这里只读 SQLite，不登录远端
tools/training-sync/.venv/bin/python tools/training-sync/push_coros_history.py \
  --years 10 --db-only

# 只下载 1 个原始 FIT，验证文件格式，不上传 Garmin
tools/training-sync/.venv/bin/python tools/training-sync/push_coros_history.py \
  --years 10 --limit 1 --manifest-only

# 小批量真实回传；--limit 是活动组数，不是目标上传次数
tools/training-sync/.venv/bin/python tools/training-sync/push_coros_history.py \
  --years 10 --limit 5

# 确认后跑十年全量，两个目标一次执行
tools/training-sync/.venv/bin/python tools/training-sync/push_coros_history.py \
  --years 10
```

回传是幂等的：

- 本地合并库判断缺失目标；COROS-only 补国行 + 国际，COROS + 国行只补国际；
- `push_uploads` 使用 `(target, source, source_id)` 记账，这里的 `source=coros`；
- 上传前还会重新拉取目标账号 10 年活动，按起点、运动类型、距离/时长匹配；
  如果远端已有活动，则补台账并跳过；
- Garmin 返回 `409 Duplicate Activity` 时也会写入台账；
- 同一组包含多条 COROS 记录时，只取一个代表记录，避免把重复导入再次扩散。

原始 FIT 缓存在 `data/coros_originals/`，文件名包含 COROS `start_utc` 和
`source_id`。命令完成后重新运行 `sync.py` 才会把新的目标端副本合并进本地库。

## Garmin 类型修正

如果 Garmin 在导入 COROS GPX 备份时把活动归为 other，可先按合并库预演：

```bash
tools/training-sync/.venv/bin/python tools/training-sync/fix_garmin_types.py \
  --start 2026-08-11 --end 2026-11-14 --dry-run
```

确认候选后去掉 `--dry-run`。工具只处理“Garmin 当前仍是 other、同组 COROS 有明确
canonical 类型”的记录，并用 Garmin 活动类型表解析 `typeId`。执行后重新运行
`sync.py` 刷新本地 `raw_sport`。

COROS 没有公开的上传 API。`import_to_coros.mjs` 复用了 COROS 网页当前的
STS -> 阿里云 OSS -> `/activity/fit/import` 流程，按 ZIP 批量导入并轮询任务状态。
该流程属于网页接口，COROS 更新后可能需要调整。自动导入失败时，可继续使用已经
导出的文件，在 COROS 网页或 App 中批量选择 `.fit` / `.tcx` 手动导入。

## 去重规则

两条不同源的活动会被判为同一场训练，需要同时满足：

1. 运动类型兼容；跑步的户外跑、操场跑、越野跑、室内跑统一归为 `run`，
   骑行、力量、室内有氧、徒步、步行、爬楼等也使用统一规范标签。Garmin 的泛化
   `other` 只有在起点和距离/时长给出强证据时才允许匹配具体 COROS 运动；
   起始时间差不超过 `--gap-seconds`（默认 60s，
   且必须落在该组最早记录的 60s 锚点窗口内，避免连续间歇的传递性误并）；
2. 起点完全一致（≤ 5s）时，距离误差 ≤ 20%、时长差 ≤ 30s/10%，或无距离指标，
   即可视为强匹配；这可处理 COROS 对同一场训练保存的 elapsed duration 与 Garmin
   不同的情况，以及部分力量训练被 COROS 归类为“室内有氧”的情况；
3. 起点略有偏差时，距离和时长都存在时采用双信号容错：距离误差 ≤ 8% 且时长误差
   ≤ 25%，或时长差 ≤ 30s/10% 且距离误差 ≤ 20%；
4. 只有一个指标可比时，距离要求误差 ≤ 8%，时长要求差 ≤ 30s/10%。

同源（同一账号）记录只在起始时间完全相同、且距离/时长匹配时合并，
对应"同一活动被重复上传"的情况；同一手表相邻的独立训练（例如田径场
连续几组间歇，间隔几十秒）不会被误并。

合并时按 `TRAINING_SYNC_PRIMARY` 优先级选主记录（默认 COROS 优先）。
对时长、距离、心率、负荷和卡路里等指标，若至少两个不同源一致而主记录是离群值，
规范记录采用一致值；主记录仅缺失字段时仍从其他源补齐。`sources` 和 `source_refs`
保留该场训练的全部来源与原始 ID。

## 输出

- `out/merged_activities.json`：去重后的规范活动列表（含 `source_refs` 溯源）
- `data/activities.db`：SQLite 明细表，`group_id` 关联同一场训练的多条源记录
- `data/activities.db` 的 `push_uploads`：跨目标上传台账，避免重复写远端
- `data/fit_for_coros/`：COROS 缺少活动的 FIT/TCX 中间文件
- stdout：终端报告（日期/距离/时长/配速/心率/负荷/来源）

## COROS 重复清理

COROS 的批量导入接口不会自动去重。若历史批次曾经导入过同一活动，先只读审计：

```bash
tools/training-sync/.venv/bin/python tools/training-sync/cleanup_coros_duplicates.py \
  --start 2023-01-01 --end 2026-11-10
```

默认会查找“新 ID、无设备、且能找到更早的设备记录”的高置信度重复项，同时支持
跑步距离匹配、不带距离的力量/有氧时长匹配，以及力量训练导入后变成 COROS
“室内有氧”的类型转换重复。确认报告中的候选后再执行：

```bash
tools/training-sync/.venv/bin/python tools/training-sync/cleanup_coros_duplicates.py \
  --start 2023-01-01 --end 2026-11-10 \
  --execute --max-delete 200
```

工具默认最多删除 200 条，并会同时清理对应上传台账；删除属于远端写操作，先看
`data/coros_duplicate_cleanup_*.json` 报告。

## 后续扩展

- 生理指标（HRV/RHR/睡眠/恢复）可按同样的"源适配器 + 按日期去重"模式扩展；
- 可继续增加 COROS -> Garmin 的反向 FIT 导出与上传；
- 可在同步任务末尾加入自动增量回传，但首次历史回填仍建议人工观察一次日志。
