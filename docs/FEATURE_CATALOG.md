# FEATURE_CATALOG — 特征目录

| 版本 | v1.0 |
|------|------|
| 依据 | 改造任务文档 §11/§12, ANALYSIS_MODEL.md §2 |

每个 Feature 可追溯：`raw data → aggregation → feature`（provenance）。
缺数据 → `null` / `unavailable`，不抛异常、不取旧值。
主窗口（z-score / efficiency）= 30s；robust baseline = rolling median/MAD。

## 1. Price（价类）

| Feature | 公式 | 窗口 | 缺数据 |
|---|---|---|---|
| `price_return_{5s/15s/30s/1m/5m}` | (P_end−P_start)/P_start | 各窗口 | null |
| `price_acceleration` | 后半段 return − 前半段 return | 30s | null |
| `high_break` | 当前价 > 5m 窗口高（1/0/None） | 30s vs 5m | null |
| `low_break` | 当前价 < 5m 窗口低（1/0/None） | 30s vs 5m | null |
| `retrace_ratio` | 突破后回吐位移 / 突破位移 | 30s | null |
| `acceptance` | 突破后后段维持方向侧占比 | 30s | null |

## 2. Volume（量类）

| Feature | 公式 | 窗口 | 缺数据 |
|---|---|---|---|
| `volume_{5s/15s/30s/1m}` | Σ qty | 各窗口 | null |
| `trade_count_{5s/15s/30s/1m}` | len(trades) | 各窗口 | null |
| `relative_volume` / `rvol` | 窗口量 / baseline median | 30s | null |
| `volume_zscore` / `volume_z` | robust Z of volume | 30s | null |
| `trade_count_zscore` / `trade_count_z` | robust Z of trade count | 30s | null |
| `volume_acceleration` | (后半量−前半量)/总量 | 30s | null |
| `average_trade_size` | 窗口量 / 笔数 | 30s | null |
| `large_trade_ratio` | > 3×median 笔数 / 总笔数 | 30s | null |

## 3. Flow（资金流类）

| Feature | 公式 | 窗口 | 缺数据 |
|---|---|---|---|
| `taker_buy_volume` / `taker_buy_volume_{w}` | Σ BUY notional | 30s/各窗口 | null |
| `taker_sell_volume` / `taker_sell_volume_{w}` | Σ SELL notional | 30s/各窗口 | null |
| `signed_delta` / `taker_delta` / `taker_delta_{w}` | Σbuy − Σsell | 30s/各窗口 | null |
| `delta_ratio` | (buy−sell)/(buy+sell) | 30s | null |
| `CVD` / `cvd` | 累积 signed_delta（每笔更新） | 全程 | 0.0 |
| `CVD_slope` / `cvd_slope_z` | robust Z of CVD 斜率 | 30s | null |
| `CVD_acceleration` / `cvd_accel_z` | robust Z of CVD 二阶差分 | 30s | null |

> CVD 方向完全依赖 aggressor_side 正确性（m=true→SELL）。重连重复 trade 在 collector 内 trade_id 去重后才进入 CVD。

## 4. OI（仓位类，基础资产数量）

| Feature | 公式 | 窗口 | 缺数据 |
|---|---|---|---|
| `oi_contracts` | 最近快照 open_interest | — | null |
| `oi_change_30s` | now − asof(30s, 容差内) | 30s | unavailable |
| `oi_change_1m` | now − asof(1m) | 1m | unavailable |
| `oi_change_5m` | now − asof(5m) | 5m | unavailable |
| `oi_velocity` | Δoi / Δt | — | null |
| `oi_acceleration` | OI 二阶差分 | — | null |

> OI 单位 = 基础资产数量（非美元名义）。价格涨但 open_interest 不变 → oi_change=0。
> as-of lookup 容差外无数据 → unavailable，不回退取更旧数据。

## 5. Efficiency（效率类，§12）

| Feature | 公式 | 窗口 | 缺数据 |
|---|---|---|---|
| `directional_efficiency` | \|P_end−P_start\| / Σ\|ΔP_i\| | 30s | null |
| `flow_impact` | signed_return / max(\|net_taker\|, ε) | 30s | null |
| `price_efficiency` | abs(price_return) / normalized_aggressive_flow | 30s | null |

> `price_efficiency`：资金越来越大但价格推不动 → efficiency ↓ → absorption/distribution/exhaustion。
> 归一化用 baseline median（跨 symbol 可比），防除零。它只是 Evidence，不直接定义卖出。

## 6. Context（上下文类，只 soft veto）

| Feature | 公式 | 窗口 | 缺数据 |
|---|---|---|---|
| `funding` | last_funding_rate | — | null |
| `premium` | mark − index | — | null |
| `funding_percentile` | 当前 funding 在 24h 基线百分位 | 24h | null |
| `premium_percentile` | 当前 premium 百分位 | 24h | null |
| `context_{1m/5m/15m/1h}` | closed bar return | 各周期 | null |

## 7. Quality（数据质量类）

| Feature | 公式 | 缺数据 |
|---|---|---|
| `source_age` | now − last_receive_time | null |
| `stale_flag` | 任一关键流 STALE/DRIFT/FAIL → 1.0 | 0.0 |

## 8. Provenance

每个 FeatureSnapshot.provenance 记录来源 stream：
`{volume: {source_streams:[aggTrade]}, flow:[aggTrade], oi:[oi_poller], context:[funding_premium], kline:[kline], quality:[所有流]}`。
