# Epic 03 — Feature Engine（Gate 3）

| 字段 | 值 |
|------|----|
| Gate | 3 |
| 依赖 | epic-02 |
| 关联文档 | ANALYSIS_MODEL.md §1,§2; DATA_MODEL.md §6 |
| 禁止 | 生成 START_CONFIRMED / 机会分 / UI |

## Goal

把"原始行情"变成可验证的资金行为特征；仍不生成 START_CONFIRMED。

## Context

epic-02 保证了数据可信与健康标注。本 Gate 把标准化事件经滚动窗口计算为特征，输出 FeatureSnapshot。每个特征须可手算复现。

## Inputs

- ANALYSIS_MODEL.md §2 特征清单（量/价/资金流/仓位/效率/上下文）
- ANALYSIS_MODEL.md §1 滚动窗口层级
- DATA_MODEL.md §6 FeatureSnapshot / FeatureValue

## Outputs

1. `src/windows/`：rolling buffer（5s/15s/30s/1m/3m/5m/15m/1h/4h/24h），时间对齐淘汰。
2. `src/features/`：
   - 量类：rvol / volume_z / trade_count_z
   - 价类：return / price_accel_z
   - 资金流：taker_delta / cvd / cvd_slope_z / cvd_accel_z
   - 仓位：oi_change / oi_velocity / oi_accel
   - 效率：directional_efficiency / flow_impact / retrace_ratio
   - 上下文：funding_percentile / premium_percentile
3. `src/features/baseline.py`：robust baseline（rolling median/MAD）。
4. FeatureSnapshot 产出（含 provenance）。

## Invariants

- 所有特征缺数据 → null / unavailable，不抛异常、不取旧值。
- CVD 方向依赖 aggressor_side 正确性；重连重复 trade 不污染（已在 health 去重）。
- 价格变动但 open_interest 不变 ⇒ oi_change=0（不得用美元名义）。
- 上下文类只能 context，不产生 signal。
- 特征必须可追溯 provenance。
- 所有阈值来自 configs/features.yaml。

## Out of Scope

Detector / 状态机 / 信号判定 / 机会分 / UI。

## Tests

每个特征手算 fixture 一致：

1. 边界：空窗口、单条事件、窗口淘汰。
2. CVD：重连/重复事件不污染。
3. OI：价格变动 contracts 不变 → oi_change=0。
4. flow_impact：ε 默认值，net_taker_notional 极小时行为。
5. directional_efficiency：合成路径手工验证。
6. 缺数据 → null/unavailable。
7. robust baseline：median/MAD 在已知样本上精确。
8. FeatureSnapshot provenance 完整。

## Observability

- metrics: feature_compute_lag_ms / feature_available_ratio{feature}
- log: baseline 样本不足警告

## Definition of Done

| 项 | 验收 |
|----|------|
| CVD/OI/Volume/Delta/efficiency 可 fixture 复算 | 手算一致 |
| 缺数据行为正确 | null/unavailable 测试 |
| provenance 可追溯 | 审查 |
| 无 Detector/信号 | 代码审查 |

## Files Allowed

`src/windows/`, `src/features/`, `fixtures/`, `tests/`, `configs/features.yaml`

## 推荐任务拆分

- 03-A: rolling windows + 时间淘汰
- 03-B: volume/trades 特征 + robust baseline
- 03-C: taker delta + windowed CVD
- 03-D: OI 特征（contracts 单位）
- 03-E: price efficiency + flow impact + retrace
- 03-F: context（funding/premium percentile）
- 03-G: FeatureSnapshot 组装 + provenance
