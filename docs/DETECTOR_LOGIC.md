# DETECTOR_LOGIC — 检测器逻辑

| 版本 | v1.0 |
|------|------|
| 依据 | 改造任务文档 §13-§18, ANALYSIS_MODEL.md §3-§5, STATE_MACHINE.md |

## 总原则

- Detector 输出 **Evidence[]**（含 code/name/direction/raw_value/normalized/threshold/window/source/explanation），不输出"机会分"。
- Veto 输出必须说明：哪条规则 / 用了什么值 / 阈值 / 时间窗口 / 为什么否决（Veto.detail）。
- 方向从数据推断（aggressor_side / delta / OI），不从 UI/LLM 反向写入。

## 1. AnomalyDetector（Stage2，高召回）

- 输入：volume_z / trade_count_z / cvd_slope_z（近似 taker_delta_z）。
- 任一超阈值 → `is_anomaly=True` + `direction_hint`（可为空）。
- **不输出 LONG/SHORT 决策**。
- confidence=UNKNOWN → 不发可升级 anomaly（fail closed）。

## 2. StartupDetector（证据链 + subtype）

证据链：候选方向 + 量异常 + 主动成交同向(FLOW) + OI 同向扩张(POSITION)
+ 价格有效位移(PRICE_EFFECT) + price_efficiency + acceptance + 回吐可控 + CVD 同向。

### subtype 分类（§15）

| subtype | Price | OI | Delta | 含义 |
|---|---|---|---|---|
| `new_long_build` | ↑ | ↑ | ↑ | 新增多头资金 |
| `short_squeeze` | ↑ | ↓ | ↑ | 空头回补/逼空（非新增多头，is_squeeze_cover=True，不 suspected） |
| `new_short_build` | ↓ | ↑ | ↓ | 新增空头资金 |
| `long_liquidation` | ↓ | ↓ | ↓ | 多头被清算（疑似去杠杆） |

- SUSPECTED：方向确定 + FLOW passed + 非 squeeze + confidence≠UNKNOWN。
- CONFIRMED：SUSPECTED + confidence==CONFIDENT + hold≥confirmation_hold + 全证据 passed + 证据数≥min_evidence。
- DEGRADED → 最高 SUSPECTED，禁止 CONFIRMED。

## 3. FalseStartFilter（核心 Edge，8 个 Veto）

| VetoType | severity | 触发 | detail |
|---|---|---|---|
| `data_stale` | hard | 关键流 STALE/DRIFT/FAIL 或 stale_flag=1 | unhealthy_streams / reason |
| `rapid_retrace` | hard | retrace_ratio > 0.7 | retrace / threshold / window |
| `oi_contraction` | hard | LONG 且 oi_change_1m<0（非 squeeze 例外） | oi_change / direction |
| `delta_reversal` | hard | 方向与 taker_delta 相反 | delta / direction |
| `no_acceptance` | hard | directional_efficiency<0.05 或 acceptance<0.3 | eff / acceptance |
| `low_efficiency_absorption` | soft | \|delta\|大 且 flow_impact 极低 | delta / flow_impact / 阈值 |
| `crowding_extreme` | soft | funding/premium 百分位 > 95 | percentiles / 阈值 |
| `one_bar_spike` | hard | 5s 位移占 30s 绝大部分 且 retrace>0.6 | ret5 / ret30 / retrace |

任一 hard veto triggered → REJECTED。Veto 不得只输出 "Rejected"。

## 4. ContinuationDetector

START_CONFIRMED 后跟踪资金是否持续：OI 维持/扩张 + CVD 同向 + 效率健康。
全证据 passed → CONTINUATION；任一 failed → weakening。

## 5. ExhaustionDetector

价格创新高但 CVD 不确认 / OI 走平→收缩 / FlowImpact 持续低。
divergence_count ≥ min_divergence_count → EXHAUSTION。

## 6. WithdrawalDetector（独立模型，非启动条件取反）

撤离 Evidence（§18）：
- OI 收缩（oi_withdrawal）
- delta/CVD 反转（delta_reversal）
- 效率失守（efficiency_broken）
- 回吐大（retrace_large）

confirm_count ≥ min_evidence_count → WITHDRAWAL。
**不简单写"做多条件消失=撤离"**，必须有真正退出 Evidence。

## 7. 状态转移（STATE_MACHINE.md T1-T13）

```
SLEEPING → ANOMALY → SUSPECTED_START → START_CONFIRMED → CONTINUATION
                        ↓(hard veto)                        ↓(divergence)
                     REJECTED                          EXHAUSTION → WITHDRAWAL → COOLDOWN → SLEEPING
```
- 每次转移产出 AnalysisEvent（evidence + vetoes）。
- confidence UNKNOWN → 禁止 CONFIRMED；DEGRADED → 最高 SUSPECTED。
- REJECTED/WITHDRAWAL → COOLDOWN → SLEEPING，不跳过冷却。
