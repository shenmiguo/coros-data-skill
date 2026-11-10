import "dotenv/config";
import dotenv from "dotenv";
import { fileURLToPath } from "node:url";

dotenv.config({ path: fileURLToPath(new URL("../../scripts/.env", import.meta.url)) });

import { CorosClient } from "../../scripts/coros.js";

const client = new CorosClient(process.env.COROS_ACCOUNT, process.env.COROS_PASSWORD);
await client.login();

console.log("=== 创建 W10 训练计划 ===\n");

// ---- 周一 8/3: E 12km + 力量A ----
const mon = await client.createProgram("周一 E12km+力量A", [
  { type: "train", distanceMeters: 12000 },
], "E 配速 4:25-4:45/km，心率<148。跑后做力量A（深蹲/硬拉/单腿/核心）。");
console.log("✅ 周一 E12km+力量A");

// ---- 周二 8/4: E 10km + strides ----
const tue = await client.createProgram("周二 E10km+strides", [
  { type: "train", distanceMeters: 10000 },
], "E 配速 4:25-4:45/km，心率<148。末尾6×100m strides @ 3'01-3'05，组间慢走恢复。");
console.log("✅ 周二 E10km+strides");

// ---- 周三 8/5: T-I 阶梯课 ----
// 3km(3:50-3:40) 休3min → 2×2km(3:40-3:30) 休3min → 3×1km(3:25-3:20) 休2min
const wed = await client.createProgram("周三 T-I阶梯课", [
  { type: "warmup", distanceMeters: 2000 },
  { type: "train", distanceMeters: 3000 },
  { type: "rest", durationSec: 180 },
  { type: "train", distanceMeters: 2000 },
  { type: "rest", durationSec: 180 },
  { type: "train", distanceMeters: 2000 },
  { type: "rest", durationSec: 180 },
  { type: "train", distanceMeters: 1000 },
  { type: "rest", durationSec: 120 },
  { type: "train", distanceMeters: 1000 },
  { type: "rest", durationSec: 120 },
  { type: "train", distanceMeters: 1000 },
  { type: "rest", durationSec: 120 },
  { type: "relax", distanceMeters: 2000 },
], "3km@3:50-3:40 休3min → 2×2km@3:40-3:30 休3min → 3×1km@3:25-3:20 休2min。2km热身+2km冷身。总计~14km。");
console.log("✅ 周三 T-I阶梯课");

// ---- 周四 8/6: E 10km ----
const thu = await client.createProgram("周四 E10km", [
  { type: "train", distanceMeters: 10000 },
], "E 配速 4:25-4:45/km，心率<148。纯恢复跑。");
console.log("✅ 周四 E10km");

// ---- 周五 8/7: E 12km + 力量B ----
const fri = await client.createProgram("周五 E12km+力量B", [
  { type: "train", distanceMeters: 12000 },
], "E 配速 4:25-4:45/km，心率<148。跑后做力量B（臀桥/提踵/弹跳/核心）。");
console.log("✅ 周五 E12km+力量B");

// ---- 周六 8/8: E 8km + strides ----
const sat = await client.createProgram("周六 E8km+strides", [
  { type: "train", distanceMeters: 8000 },
], "E 配速 4:25-4:45/km，心率<148。末尾6×100m strides。明日LSD前轻量。");
console.log("✅ 周六 E8km+strides");

// ---- 周日 8/9: L 25km 含末4km@M ----
const sun = await client.createProgram("周日 L25km+4km@M", [
  { type: "train", distanceMeters: 21000 },
  { type: "train", distanceMeters: 4000 },
], "E 21km → M 4km(3:40-3:44, HR148-156)。长距离，前21km纯E，末4km提到M配速。");
console.log("✅ 周日 L25km+4km@M");

// ---- 创建周计划并同步到手表 ----
console.log("\n--- 同步到手表 ---");
const planId = await client.createPlan(
  "W10 速度底座第4周 (8/3-8/9)",
  7,
  [
    { dayNo: 0, program: mon },
    { dayNo: 1, program: tue },
    { dayNo: 2, program: wed },
    { dayNo: 3, program: thu },
    { dayNo: 4, program: fri },
    { dayNo: 5, program: sat },
    { dayNo: 6, program: sun },
  ],
  "W10: 周三T-I阶梯课(3km+2×2km+3×1km)+周日LSD25km。周目标~93km。力量A(周一)/力量B(周五)。"
);

console.log(`\n🎉 W10 计划已同步到手表! planId: ${planId}`);
console.log("周计划: 7天，周三T-I阶梯课 + 周日LSD25km");
console.log("力量: 周一力量A + 周五力量B");
