# 分析模型（Analysis Model）

| 版本 | v1.0 |
|------|------|
| 依据 | SYSTEM_DESIGN.md §8, §9, §11 |
| 状态 | 已锁定 contract |

本文定义特征、检测器、证据族、Veto 的完整清单。AI 只能实现本文已定义的项，不得自行发明指标、权重或机会分公式。

---

## 0. 总原则

- V1 输出 **Evidence + State**，不输出 0–100 机会分。
- 所有阈值配置化（`configs/features.yaml` / `configs/detectors.yaml`）。
- 优先用 **robust baseline**（rolling median/MAD、robust Z-score），避免固定阈值对不同山寨币失效。
- 方向必须从数据推断（aggressor_side / delta / OI），不得从 UI 或 LLM 反向写入。

---

## 1. 滚动窗口层级

| 层级 | 窗口 | 职责 |
|------|------|------|
| 实时微观层 | 5s / 15s / 30s | 成交量、Taker Delta、CVD、价格加速度异常 |
| 启动层 | 1m / 3m / 5m | 新增仓位、回踩承接、异常是否持续 |
| 趋势上下文 | 15m / 1h | 更大方向、是否过度延伸 |
| 背景层 | 4h / 24h | Funding/OI regime、波动基线 |

**robust baseline 窗口**：默认用过去 `baseline_window`（建议 `1h`，可配置）的滚动数据计算 median/MAD。不同 symbol 不共享基线（"相对自身历史"）。

---

## 2. 特征清单（Feature Engine，Gate 3）

每个特征须能回答：输入是什么、公式是什么、窗口是什么、缺数据时是什么、fixture 怎么复算。

### 2.1 量类

| 特征 | 公式 | 窗口 | 缺数据 |
|------|------|------|--------|
| `rvol` | 当前窗口成交量 / baseline median(window 内) | 30s, 1m, 5m | null |
| `volume_z` | robust Z = (vol − median) / (1.4826 × MAD) | 同上 | null |
| `trade_count_z` | robust Z of trade count | 同上 | null |

> TradeCountZ 与 VolumeZ 同步爆发才算真异动；单笔大单但 trade_count 不变 → 证据体现差异，不直接等同启动。

### 2.2 价类

| 特征 | 公式 | 窗口 | 缺数据 |
|------|------|------|--------|
| `return` | (P_end − P_start) / P_start | 各层 | null |
| `price_accel_z` | robust Z of 价格二阶差分 | 5s/15s/30s | null |

### 2.3 资金流类

| 特征 | 公式 | 窗口 | 缺数据 |
|------|------|------|--------|
| `taker_delta` | Σ(buy_notional) − Σ(sell_notional)；buy/sell 由 aggressor_side 判定 | 各层 | null |
| `cvd` | 累积 taker_delta（按 symbol） | 全程 | null |
| `cvd_slope_z` | robust Z of CVD 斜率 | 30s/1m/5m | null |
| `cvd_accel_z` | robust Z of CVD 二阶差分 | 30s/1m | null |

> CVD 方向完全依赖 aggressor_side 正确性（见 DATA_MODEL.md §0.1）。重连重复 trade 必须在进入 CVD 前去重。

### 2.4 仓位类

| 特征 | 公式 | 窗口 | 缺数据 |
|------|------|------|--------|
| `oi_change` | open_interest_now − open_interest_asof（**基础资产数量**） | 1m/5m/15m | unavailable |
| `oi_velocity` | oi_change / Δt | 同上 | unavailable |
| `oi_accel` | 二阶差分 of open_interest | 同上 | unavailable |

> 价格变动但 open_interest 不变 ⇒ `oi_change = 0`。不得用美元名义 OI。

### 2.5 效率类

| 特征 | 公式 | 窗口 | 缺数据 |
|------|------|------|--------|
| `directional_efficiency` | \|P_end − P_start\| / Σ\|ΔP_i\| | 30s/1m | null |
| `flow_impact` | signed_return / max(\|net_taker_notional\|, ε) | 30s/1m | null |
| `retrace_ratio` | 突破后回吐位移 / 突破位移 | 1m/5m | null |

- `ε` 默认 `1.0`（USDT 名义 1 美元），配置化。
- `AbsorptionCandidate = |delta| 大 AND flow_impact 极低`（需结合后续接受度/OI 再判断）。

### 2.6 上下文类

| 特征 | 公式 | 窗口 | 缺数据 |
|------|------|------|--------|
| `funding_percentile` | 当前 funding 在 24h 基线的百分位 | 4h/24h | null |
| `premium_percentile` | 当前 premium 在基线的百分位 | 同上 | null |

> 上下文类**只能 context / soft veto，不得单独触发信号**。

---

## 3. 证据族（EvidenceFamily）

Evidence 对象结构见 DATA_MODEL.md §7。

| Family | 含义 | 典型 type |
|--------|------|-----------|
| `ANOMALY` | 量价异常 | volume_z, trade_count_z, price_accel_z |
| `FLOW` | 主动资金 | taker_delta, cvd_slope_z, cvd_accel_z |
| `POSITION` | 仓位变化 | oi_expansion, oi_contraction, oi_contraction_cover |
| `PRICE_EFFECT` | 价格效果 | directional_efficiency, flow_impact, breakout_acceptance, retrace |
| `CONTEXT` | 拥挤上下文 | funding_percentile, premium_percentile |

---

## 4. Veto 清单（False Start Filter，Gate 6）

Veto 对象结构见 DATA_MODEL.md §7。

| VetoType | 典型表现 | severity | 最小实现 | 测试 fixture |
|----------|----------|----------|----------|--------------|
| `data_stale` | 关键输入过期 | hard | 任一 hard-required 输入 stale → 禁止确认 | 冻结 aggTrade 后尝试确认 |
| `rapid_retrace` | 异常上涨后短时间回吐大部分位移 | hard | retrace_ratio > 配置阈值 | 先拉升后完整回吐 |
| `oi_contraction` | 上涨但 OI 持续下降 | hard | direction=LONG 且 oi_change<0 且非 squeeze 例外 → squeeze 分类 | price↑ volume↑ OI↓ |
| `delta_reversal` | 第一波主动买入后迅速翻为持续主动卖出 | hard | 同向 delta 后持续反向 | 正 delta → 强负 delta |
| `no_acceptance` | 突破未在新价格区域停留 | hard | 突破后回区间 | 刺穿后立即回区间 |
| `low_efficiency_absorption` | 巨量主动资金但价格推不动 | soft | delta 高且 flow_impact 极低 | delta 高、flow_impact≈0 |
| `crowding_extreme` | Funding/Premium 极端且追涨 | soft | funding/premium 百分位 > 阈值 | funding 极正 + 追涨 |

> **禁止事项**：V1 不允许 AI 写 `score = 0.2*RSI + 0.4*Volume + ...`。所有 veto 与 evidence 必须先有定义、fixture 和历史样本，再决定权重。

---

## 5. 检测器职责

### 5.1 Anomaly Detector（Gate 4）

- 输入：VolumeZ、TradeCountZ、PriceAccelZ、TakerDeltaZ、OI velocity。
- 输出：AnomalyEvidence 列表 + `direction_hint`（可为空）。**不输出 LONG/SHORT，不代表"主力进场"。**
- 目标：高召回，允许误报；把全市场缩小到候选集合。
- 关键数据 stale ⇒ 不得发可升级到 confirmed 的 anomaly。

### 5.2 Startup Detector（Gate 5）

证据链：
```
候选方向 + 量异常
+ 主动成交同向（FLOW）
+ OI 同向扩张（或可解释 squeeze 例外）（POSITION）
+ 价格产生有效位移（PRICE_EFFECT）
+ 突破后未快速全部回吐
─────────────────
→ SUSPECTED_START / START_CONFIRMED
```

- 先 SUSPECTED_START，再由确认条件升级；禁止单次 spike 直接确认。
- 必须区分"新增多头启动"与"空头回补 squeeze"（见 STATE_MACHINE.md §3）。

### 5.3 Continuation Detector（Gate 7）

- OI 持续/保持，而非启动后立即坍缩。
- CVD/Taker Delta 维持方向，回踩反向主动成交减弱。
- 成交量从"爆发"过渡到"健康延续"。
- 价格效率维持；二次突破获 acceptance。

### 5.4 Withdrawal Detector（Gate 7，独立模型）

**撤离不是"启动条件取反"。** 必须单独编写 WithdrawalDetector 和自己的 fixture。

```
Price 继续创新高
BUT CVD 不创新高 / Delta 转弱
+ OI 从扩张 → 走平 → 收缩
+ Volume 仍大但 FlowImpact 越来越低
+ 主动卖出持续增强
─────────────────
→ EXHAUSTION → WITHDRAWAL
```

输出首先是"资金撤离风险/确认"，**不是 SELL 指令**。

---

## 6. Evidence-first 输出示例

```
ONGUSDT
STATE = SUSPECTED_START
DIRECTION = LONG
confidence_state = CONFIDENT

Evidence:
  [ANOMALY]     volume_z(30s)      value=4.72  ref(median=..,mad=..) threshold=3.0  passed=true
  [ANOMALY]     trade_count_z      value=3.81  ...                    threshold=3.0  passed=true
  [ANOMALY]     price_accel_z      value=2.94  ...                    threshold=2.5  passed=true
  [POSITION]    oi_change_1m       value=+1.82% ...                                  passed=true
  [FLOW]        taker_delta        value=+1.92M ...                                  passed=true
  [FLOW]        cvd_slope_z        value=+3.12  ...                    threshold=2.5  passed=true
  [PRICE_EFFECT] flow_impact       value=healthy ...                                  passed=true

Veto:
  rapid_retrace            triggered=false
  oi_contraction           triggered=false
  delta_reversal           triggered=false
  data_stale               triggered=false

结论：疑似新增多头资金启动，等待 acceptance / 二次确认。
```

---

## 7. 评分延后声明

只有积累足够历史样本（Gate 8）后，才进入 v1.5/v2 的 calibrated score：由命中率、条件发生频率和不同币种/regime 后验表现确定，**而不是人为"给 20 分"**。在此之前任何 0–100 分都只是人为装饰。
