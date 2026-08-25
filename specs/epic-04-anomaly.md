# Epic 04 — Anomaly Detector（Gate 4）

| 字段 | 值 |
|------|----|
| Gate | 4 |
| 依赖 | epic-03 |
| 关联文档 | ANALYSIS_MODEL.md §5.1; STATE_MACHINE.md T1,T2 |
| 禁止 | 输出 LONG/SHORT 决策 / 发明权重 / 加传统指标 |

## Goal

高召回发现"异动候选"，不负责判断真假。

## Context

epic-03 产出 FeatureSnapshot。本 Gate 用 robust z-score / percentile 发现异常，把全市场缩小到候选集合。只回答"有东西不正常"。

## Inputs

- ANALYSIS_MODEL.md §5.1（输入：VolumeZ/TradeCountZ/PriceAccelZ/TakerDeltaZ/OI velocity）
- STATE_MACHINE.md T1（SLEEPING→ANOMALY）、T2（ANOMALY→SLEEPING）
- configs/detectors.yaml（anomaly 阈值）

## Outputs

1. `src/detectors/anomaly.py`：AnomalyDetector。
2. 输出 AnomalyEvidence 列表 + `direction_hint`（可为空）。
3. 触发前后 FeatureSnapshot 保存（供 replay）。
4. confidence_state 联动：关键数据 stale ⇒ 不得发可升级到 confirmed 的 anomaly。

## Invariants

- 基于 robust z-score / percentile，不先硬编码"成交量 3 倍就是启动"。
- 不输出 LONG/SHORT，不代表"主力进场"。
- 所有阈值来自配置；AI 不得自己调优。
- direction_hint 仅为提示，非决策。
- 关键数据 stale ⇒ anomaly 标注 data_stale，不可升级 confirmed。

## Out of Scope

Startup / False Start / 机会分 / 权重 / RSI/MACD。

## Tests

| 场景 | 期望 |
|------|------|
| 正常随机噪声 | 大多数时间不触发 |
| volume+trade count 同步尖峰 | 触发 volume anomaly |
| 单笔超大成交但 trade count 无变化 | 证据体现差异，不直接等同真启动 |
| 关键数据 stale | 不得发可升级到 confirmed 的 anomaly |

## Observability

- metrics: anomaly_triggered_total{feature} / anomaly_false_positive（后续标注）
- log: 触发证据快照

## Definition of Done

| 项 | 验收 |
|----|------|
| 4 测试场景全绿 | fixture |
| 无 LONG/SHORT 决策输出 | 代码审查 |
| 阈值全部配置化 | 无 magic number |
| stale 联动正确 | confidence_state 测试 |

## Files Allowed

`src/detectors/anomaly.py`, `tests/`, `fixtures/`, `configs/detectors.yaml`

## 推荐任务拆分

- 04-A: AnomalyDetector 主体 + robust z 阈值
- 04-B: direction_hint（可为空）+ stale 联动
- 04-C: 4 个测试场景 fixture
