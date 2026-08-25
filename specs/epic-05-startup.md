# Epic 05 — Startup Candidate / Confirm（Gate 5）

| 字段 | 值 |
|------|----|
| Gate | 5 |
| 依赖 | epic-04 |
| 关联文档 | ANALYSIS_MODEL.md §3,§5.2; STATE_MACHINE.md T3,T5,§3 |
| 禁止 | AI 自由发挥加 RSI/MACD/Fibonacci / 发明权重 / 单次 spike 直接确认 |

## Goal

从异常中建立"方向 + 新增资金 + 价格效果"的证据链。

## Context

epic-04 产出 anomaly 候选。本 Gate 用证据族 A–E 建立 SUSPECTED_START，再由确认条件升级 START_CONFIRMED。必须区分"新增多头启动"与"空头回补 squeeze"。

## Inputs

- ANALYSIS_MODEL.md §3 EvidenceFamily（ANOMALY/FLOW/POSITION/PRICE_EFFECT/CONTEXT）
- ANALYSIS_MODEL.md §5.2 Startup 证据链
- STATE_MACHINE.md T3（→SUSPECTED_START）、T5（→START_CONFIRMED）、§3 squeeze 例外
- DATA_MODEL.md §7 AnalysisEvent / Evidence

## Outputs

1. `src/detectors/startup.py`：StartupDetector。
2. SUSPECTED_START → START_CONFIRMED 升级逻辑（含 confirmation_hold 防单次 spike）。
3. squeeze 例外判定：direction=LONG 且 OI 收缩 → cover 分类（非新增多头）。
4. Evidence 输出（family/type/value/reference/threshold/passed）。
5. 方向从数据推断（aggressor_side/delta/OI），不得从 UI/LLM 反向写入。

## Invariants

- 先 SUSPECTED_START，再升级；禁止单次 spike 直接确认。
- START_CONFIRMED 需 confidence_state==CONFIDENT + confirmation_hold。
- 区分新增多头 vs 空头回补 squeeze（证据类别不同）。
- 只实现已定义 evidence family；不得自行加指标。
- 方向必须从数据推断。

## Out of Scope

False Start veto（epic-06）/ Continuation / Withdrawal / 机会分 / UI。

## Tests

1. 干净多头启动 → SUSPECTED → START_CONFIRMED。
2. 单次 spike → 不直接确认（需 hold）。
3. squeeze 场景（price↑ vol↑ OI↓）→ cover 分类，不 START_CONFIRMED。
4. confidence_state=DEGRADED → 最高 SUSPECTED，禁止 CONFIRMED。
5. confidence_state=UNKNOWN → 禁止 CONFIRMED。
6. Evidence 完整可追溯。

## Observability

- metrics: state_transitions_total{from,to} / startup_evidence_pass_ratio{family}
- log: 证据链详情

## Definition of Done

| 项 | 验收 |
|----|------|
| SUSPECTED→CONFIRMED 正确 | fixture |
| squeeze 分类正确 | OI↓ 场景 |
| confidence 联动 | DEGRADED/UNKNOWN 测试 |
| 无传统指标 | 代码审查 |
| 方向从数据推断 | 审查 |

## Files Allowed

`src/detectors/startup.py`, `tests/`, `fixtures/`, `configs/detectors.yaml`, `configs/state_machine.yaml`

## 推荐任务拆分

- 05-A: StartupDetector 主体 + evidence 族 A-D
- 05-B: squeeze 例外判定
- 05-C: confirmation_hold + confidence 联动
- 05-D: 测试 fixture
