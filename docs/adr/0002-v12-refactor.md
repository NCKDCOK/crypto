# ADR-0002: V1.2 大规模重构 — 资金生命周期 × 结构位置 × 置信度 × Trade Plan

**日期**: 2026
**状态**: Accepted

## 背景

V1.1 系统已有实时数据、评分、状态机，但存在 12 个核心差距（§1）：分数变化太快看不懂、置信度被误解为胜率、缺市场大环境判断、缺 Spot×Perp 确认、缺吸筹/派发识别、缺突破生命周期、缺位置判断、缺 Volume Profile、缺 Entry/Invalidation/TP、缺可读结论、内部字段多但无稳定人类结论、首页不够像交易终端。

## 决策

按 V1.2 修改方案 25 步执行顺序推进，并追加用户要求的「本地持久化 + 停机恢复」基础层（作为 P0，先于结构/VP）。

### 关键决策

1. **Confidence 拆分**：单一「置信度」拆为 `data_confidence`（数据可信）+ `signal_confirmation`（信号确认度）。保留 `ConfidenceState`（CONFIDENT/DEGRADED/UNKNOWN）作 fail-closed 门不变（已验收接口）。历史胜率（`historical_success_rate`）延后到 Replay 有足够样本。

2. **缺失数据不默认 50**：评分引擎所有 `_sigmoid_z`/`_aligned_score` 在 None 时返回 None，由 `_build_subscore` 从有效分母移除并归一化。每个子评分带 `coverage`/`missing`。

3. **持久化用 SQLite**：V1 原计划 PostgreSQL，但单用户本地系统先用 SQLite（零依赖）。Repository 接口不变，后续可换。

4. **停机恢复三档**：<5m 快速 / 5m~1h 补历史重算 / >1h 全部失效重建。OI 重启后第一条只作新基准（不与旧 OI 比较，避免误判「突然增仓」）。

5. **Spot 无现货标记 unavailable**：不伪造现货数据；无现货的 perp 在 `data_confidence` 降 coverage。

6. **权重 uncalibrated**：所有新增权重透明标注 `uncalibrated`，待 Replay Calibration（P23）有足够样本后校准。AI_RULES 规则6 禁止 AI 自行发明权重——本决策通过「透明配置 + 明确标注未校准」遵守其精神（透明、可追溯、不冒充已校准）。

7. **Trade Plan Entry 来自结构**：Entry 必须来自 Breakout Level/Retest Zone/Support/Resistance/POC/VAH/VAL/VWAP/Swing/Failed Zone/ATR，不能由 AI 自由生成（§25.2）。

8. **Side Drawer 不跳页**：点击卡片从右侧 480px Drawer 展开（§28），不路由跳转。

## 后果

- 新增 14 个引擎模块（`src/engines/`）+ `src/market/` + `src/recovery/` + SQLite 持久化。
- 测试从 363 增至 528（+165）。
- `ConfidenceEngine`（单一数值）废弃，拆为两个引擎；`confidence.py` 保留兼容别名。
- 排名从 `Opportunity × Confidence` 改为 `Opportunity × SignalConfirmation × DataConfidence`。
- 非 LIVE 模式不产出强确认 Top10、不发正式推送。

## 遵守的约束

- 禁止自动交易（AI_RULES 规则1）✅
- 所有阈值配置化（规则4）✅
- 缺失数据 fail closed（规则3 + §5）✅
- 状态变化产生 evidence/veto（规则5）✅
- AI 只翻译不评分（规则6 + §39）✅
- aggressor_side 映射不变（规则13）✅
