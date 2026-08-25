# 状态机模型（State Machine）

| 版本 | v1.0 |
|------|------|
| 依据 | SYSTEM_DESIGN.md §10 |
| 状态 | 已锁定 contract（Gate 0 后变更需 ADR） |

本文细化 SYSTEM_DESIGN.md §10 的状态机：补全所有转移、每条转移的 guard（引用证据族）、以及配置项。每个 symbol 维护独立的状态机实例。

---

## 0. State 枚举

| State | 用户含义 | 是否提醒 |
|-------|----------|----------|
| `SLEEPING` | 无明显异常 | 否 |
| `ANOMALY` | 资金/量价出现异常 | 可选低等级 |
| `SUSPECTED_START` | 疑似启动，尚未通过反证过滤 | 默认不推"进场"提醒 |
| `START_CONFIRMED` | 证据链通过，假启动 veto 未命中 | 高等级提醒 |
| `CONTINUATION` | 资金仍在持续 | 状态更新/排行榜 |
| `EXHAUSTION` | 推动效率下降、背离增加 | 风险提醒 |
| `WITHDRAWAL` | 撤离证据达确认条件 | 高等级撤离提醒 |
| `REJECTED` | 假启动或信号失效 | 记录并用于后续标注 |
| `COOLDOWN` | 被拒绝/撤离后的冷却 | 否 |

---

## 1. 完整转移图

```
                        ┌─────────────── retrace/anomaly 消退 ───────────────┐
                        ▼                                                    │
                     SLEEPING ──── anomaly ────► ANOMALY ──── no_dir/消退 ───► SLEEPING
                        ▲                         │
                        │                         │ direction evidence
                        │                         ▼
                        │                  SUSPECTED_START
                        │                    │       │
                        │         veto hit   │       │ confirmed + confidence_state≠UNKNOWN
                        │            └───────┘       ▼
                        │                  START_CONFIRMED
                        │                         │
            cooldown_expires                       │ sustained flow
                        │                         ▼
                     COOLDOWN ◄── rejected    CONTINUATION
                        ▲                         │
                        │                         │ weakening / divergence
                        │                         ▼
                        │                     EXHAUSTION
                        │                         │
                        │                         │ withdrawal confirmed
                        │                         ▼
                     COOLDOWN ◄── cooldown    WITHDRAWAL
```

`REJECTED` 是一个瞬时记录状态，进入后立即转 `COOLDOWN`（不驻留）。

---

## 2. 转移 guard 详表

> 所有阈值来自配置（见 configs/），AI 不得自行调参。证据族定义见 ANALYSIS_MODEL.md。`confidence_state` 定义见 DATA_MODEL.md §0.3。

| # | from | to | guard（全部满足） |
|---|------|----|-------------------|
| T1 | SLEEPING | ANOMALY | 任一 anomaly 证据 `passed=true`（VolumeZ/TradeCountZ/PriceAccelZ/TakerDeltaZ 超阈值）；`confidence_state ∈ {CONFIDENT, DEGRADED}` |
| T2 | ANOMALY | SLEEPING | anomaly 窗口内无新增证据 `passed` 且持续 `anomaly_decay_window` 无复发 |
| T3 | ANOMALY | SUSPECTED_START | 方向证据 `direction ∈ {LONG, SHORT}`：主动资金同向（TakerDelta/CVD 同向）+ 价格有效位移 +（OI 同向扩张 **或** 可解释 squeeze 例外）；`confidence_state ∈ {CONFIDENT, DEGRADED}` |
| T4 | SUSPECTED_START | REJECTED | 任一 `hard` veto `triggered=true`（rapid_retrace / oi_contraction〔非 squeeze 例外〕 / delta_reversal / no_acceptance / data_stale） |
| T5 | SUSPECTED_START | START_CONFIRMED | 所有启动证据族 A–D `passed=true`；无 `hard` veto 命中；`confidence_state == CONFIDENT`；满足 `confirmation_hold`（证据持续 ≥ 配置时间，防单次 spike 直接确认） |
| T6 | START_CONFIRMED | CONTINUATION | OI 维持/扩张 + CVD 维持同向 + 回踩反向主动成交减弱 + 效率健康；持续 `continuation_hold` |
| T7 | CONTINUATION | EXHAUSTION | 价格创新高 **但** CVD 不创新高/转弱 **或** OI 走平→收缩 **或** FlowImpact 持续下降；满足 `exhaustion_confirm` 窗口 |
| T8 | EXHAUSTION | WITHDRAWAL | OI 收缩 + delta/CVD 反转 + 主动卖出持续增强 + 价格结构失守（跌破近期支撑/不再创新高）；`confidence_state == CONFIDENT`；满足 `withdrawal_confirm` 窗口 |
| T9 | CONTINUATION | EXHAUSTION | （同 T7） |
| T10 | EXHAUSTION | CONTINUATION | 推动效率恢复（悖离消失、OI 重新扩张）— 允许回退，需新证据 |
| T11 | WITHDRAWAL | COOLDOWN | 自动（撤离确认后进入冷却） |
| T12 | REJECTED | COOLDOWN | 自动（瞬时） |
| T13 | COOLDOWN | SLEEPING | `cooldown_seconds` 到期 |

### 配置项（configs/state_machine.yaml）

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `anomaly_decay_window` | `30s` | anomaly 无复发后退回 SLEEPING 的窗口 |
| `confirmation_hold` | `15s` | SUSPECTED→CONFIRMED 证据需持续的最低时间 |
| `continuation_hold` | `30s` | CONFIRMED→CONTINUATION 证据需持续 |
| `exhaustion_confirm` | `1m` | 衰竭证据需持续的窗口 |
| `withdrawal_confirm` | `1m` | 撤离证据需持续的窗口 |
| `cooldown_seconds` | `300` | REJECTED/WITHDRAWAL 后冷却秒数（配置化） |

---

## 3. squeeze 例外判定

T3 中"OI 同向扩张 **或** 可解释 squeeze 例外"：

| 情形 | OI | 方向 | 判定 |
|------|----|------|------|
| 新增多头启动 | ↑ | LONG | 正常启动 |
| 新增空头启动 | ↑ | SHORT | 正常启动 |
| 空头回补（cover） | ↓ | LONG（价涨） | **squeeze 例外**：归类为 cover，不作为新增多头；evidence `family=POSITION` `type=oi_contraction_cover`，direction 标注但 state 不进 START_CONFIRMED |
| 新空头被逼（短挤） | ↑ | LONG（价涨） | 标注 `squeeze_long`；需额外 CVD/接受度证据，降低 confidence |

> 判定依据：direction=LONG 且 OI 收缩 ⇒ cover 类（非新增资金）。direction=LONG 且 OI 扩张但 Funding 极度负（空头拥挤）⇒ 标注 squeeze 风险。具体阈值配置化。

---

## 4. Data Health 对状态机的硬约束（Fail Closed）

| confidence_state | 允许进入的最高状态 |
|------------------|--------------------|
| `CONFIDENT` | 全部（含 START_CONFIRMED / WITHDRAWAL） |
| `DEGRADED` | 最高 SUSPECTED_START；已 CONFIRMED 的冻结不退出 |
| `UNKNOWN` | 禁止任何 CONFIRMED；ANOMALY 可保持但标注 `data_stale`；已有 CONFIRMED 冻结 |

`data_stale` 作为 **hard veto**（见 ANALYSIS_MODEL.md §4）：关键输入 STALE/DRIFT/MISSING 时，T5、T8 禁止触发。

---

## 5. 不变量

1. 每次状态转移必须产出 AnalysisEvent（含 evidence + vetoes），无证据的状态变化非法。
2. 状态转移必须是确定性的：相同 FeatureSnapshot 序列 + 相同配置 ⇒ 相同状态序列（replay 可复现）。
3. 方向必须从数据推断（aggressor_side / delta / OI），不得从 UI 选择或 LLM 文本反向写入。
4. REJECTED/WITHDRAWAL 必须经 COOLDOWN 回 SLEEPING，不得直接跳到 START_CONFIRMED。
