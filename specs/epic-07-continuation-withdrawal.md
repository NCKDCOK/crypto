# Epic 07 — Continuation & Withdrawal（Gate 7）

| 字段 | 值 |
|------|----|
| Gate | 7 |
| 依赖 | epic-06 |
| 关联文档 | ANALYSIS_MODEL.md §5.3,§5.4; STATE_MACHINE.md T6-T11 |
| 禁止 | 撤离当"启动条件取反" / 发 SELL 指令 / UI 复制逻辑 |

## Goal

实现"主力进我跟，主力走我知道"的后半段。撤离必须独立建模。

## Context

epic-06 后系统能区分真假启动。本 Gate 实现资金持续、衰竭与撤离检测，覆盖启动后的完整生命周期。

## Inputs

- ANALYSIS_MODEL.md §5.3 Continuation / §5.4 Withdrawal
- STATE_MACHINE.md T6（→CONTINUATION）、T7（→EXHAUSTION）、T8（→WITHDRAWAL）、T10（回退）、T11（→COOLDOWN）

## Outputs

1. `src/detectors/continuation.py`：ContinuationDetector。
2. `src/detectors/exhaustion.py`：ExhaustionDetector。
3. `src/detectors/withdrawal.py`：WithdrawalDetector（**独立模型**，独立 fixture）。
4. CONTINUATION / EXHAUSTION / WITHDRAWAL 状态流转。

## Invariants

- **撤离不是"启动条件取反"。** WithdrawalDetector 必须单独编写 + 独立 fixture。
- Withdrawal 输出首先是"资金撤离风险/确认"，**不是 SELL 指令**。
- WITHDRAWAL 需 confidence_state==CONFIDENT + withdrawal_confirm 窗口。
- 允许 EXHAUSTION→CONTINUATION 回退（需新证据）。
- 方向从数据推断。

## Out of Scope

机会分 / 评分 / UI 业务逻辑。

## Tests

| Detector | fixture |
|----------|---------|
| Continuation | OI 持续 + CVD 同向 + 回踩卖压减弱 → CONTINUATION |
| Continuation 失效 | OI 坍缩 → 不维持 CONTINUATION |
| Exhaustion | 价创新高但 CVD/OI/效率不确认 → EXHAUSTION |
| Withdrawal | OI 收缩 + delta/CVD 反转 + 主动卖出持续 + 价格失守 → WITHDRAWAL |
| 回退 | EXHAUSTION 后效率恢复 → CONTINUATION |

## Observability

- metrics: state_transitions_total{from,to}（含 EXHAUSTION/WITHDRAWAL）
- log: 衰竭/撤离证据

## Definition of Done

| 项 | 验收 |
|----|------|
| 3 detector 独立 fixture | 测试绿 |
| Withdrawal 独立模型 | 不复用 Startup 取反逻辑 |
| 无 SELL 指令 | 代码审查 |
| 回退路径正确 | EXHAUSTION→CONTINUATION |

## Files Allowed

`src/detectors/continuation.py`, `src/detectors/exhaustion.py`, `src/detectors/withdrawal.py`, `tests/`, `fixtures/`, `configs/detectors.yaml`, `configs/state_machine.yaml`

## 推荐任务拆分

- 07-A: ContinuationDetector
- 07-B: ExhaustionDetector
- 07-C: WithdrawalDetector（独立 fixture）
- 07-D: 状态回退 + 测试
