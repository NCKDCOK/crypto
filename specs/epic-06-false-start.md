# Epic 06 — False Start Filter（Gate 6，最重要）

| 字段 | 值 |
|------|----|
| Gate | 6 |
| 依赖 | epic-05 |
| 关联文档 | ANALYSIS_MODEL.md §4; STATE_MACHINE.md T4,T12 |
| 禁止 | 为"优化命中率"改阈值 / 现场发明评分 |

## Goal

把"异动很多"变成"只留下有持续性证据的候选"。这是系统 V1 的核心 Edge。

## Context

epic-05 产出 SUSPECTED_START 候选。本 Gate 用 Veto 机制过滤假启动。完成后不要急着优化命中率——先持续记录 REJECTED 与 CONFIRMED 样本，建立真实标签集。

## Inputs

- ANALYSIS_MODEL.md §4 Veto 清单（data_stale/rapid_retrace/oi_contraction/delta_reversal/no_acceptance/low_efficiency_absorption/crowding_extreme）
- STATE_MACHINE.md T4（→REJECTED）、T12（→COOLDOWN）

## Outputs

1. `src/detectors/false_start.py`：FalseStartFilter。
2. 7 个 Veto 实现（hard/soft 区分）。
3. Veto 输出（type/triggered/severity/detail）。
4. REJECTED → COOLDOWN → SLEEPING 流转。

## Invariants

- hard veto 命中 ⇒ 禁止 START_CONFIRMED，转 REJECTED。
- data_stale 为 hard veto。
- oi_contraction：direction=LONG 且 OI 收缩 且非 squeeze 例外 → hard veto。
- soft veto（low_efficiency_absorption/crowding_extreme）→ 风险提示，不直接拒绝。
- 所有阈值配置化，AI 不得自行调优。
- 不得改测试标准来"修复命中率"。

## Out of Scope

Continuation / Withdrawal / 机会分 / 评分 / UI。

## Tests

| Veto | fixture |
|------|---------|
| data_stale | 冻结 aggTrade 后尝试确认 |
| rapid_retrace | 先拉升后完整回吐 |
| oi_contraction | price↑ volume↑ OI↓ |
| delta_reversal | 正 delta → 强负 delta |
| no_acceptance | 刺穿后立即回区间 |
| low_efficiency_absorption | delta 高、flow_impact≈0 |
| crowding_extreme | funding 极正 + 追涨 |

每个 veto：正例触发 + 负例不误触。

## Observability

- metrics: veto_triggered_total{type,severity} / rejected_total / confirmed_total
- log: veto 命中详情

## Definition of Done

| 项 | 验收 |
|----|------|
| 7 veto 全实现 + 测试 | fixture 绿 |
| hard/soft 区分正确 | 审查 |
| REJECTED→COOLDOWN 流转 | 状态机测试 |
| 无命中率调参 | 阈值未改 |

## Files Allowed

`src/detectors/false_start.py`, `tests/`, `fixtures/`, `configs/detectors.yaml`, `configs/state_machine.yaml`

## 推荐任务拆分

- 06-A: data_stale + rapid_retrace + no_acceptance（hard）
- 06-B: oi_contraction + delta_reversal（hard）
- 06-C: low_efficiency_absorption + crowding_extreme（soft）
- 06-D: REJECTED→COOLDOWN 流转 + 测试
