# LIVE_DATA_FLOW — 实盘数据流

| 版本 | v1.0 |
|------|------|
| 依据 | 改造任务文档 §33.3, ARCHITECTURE.md §3 |

## 1. 端到端数据流（ASCII）

```
┌──────────────── Binance USDT-M Public Data ────────────────┐
│  aggTrade(WS)  Kline 1m(WS)  OI(REST)  Funding/Premium(REST)│
│                    + 24h ticker(REST, universe 发现)        │
└────────────┬──────────────────────────────────┬────────────┘
             │                                  │
             ▼                                  ▼
   [Stage1 LightScanner]                [SymbolUniverse]
   全 universe 低成本 REST 扫描          exchangeInfo + 24h ticker
   relative_volume_z / trade_count_z    → top-N by quote volume
   price_change → ANOMALY_CANDIDATE     liquidity floor / blacklist
             │                          / whitelist / max_symbols
             ▼                                  │
   候选集 (top deep_max_symbols) ◄──────────────┘
             │
             ▼
   [Stage2 DeepScanner]  ← 仅候选 symbol
   ├─ AggTradeCollector(WS) ──► bounded Queue ──► FeatureEngine.add_trade
   ├─ KlineCollector(WS) ─────────────────────► FeatureEngine.add_kline
   ├─ OIPoller(REST, RateLimiter) ────────────► FeatureEngine.add_oi + OILookup
   └─ FundingCollector(REST, RateLimiter) ────► FeatureEngine.add_funding
             │
             ▼
   [FreshnessWatchdog]  每流独立 freshness budget (connected≠healthy)
             │  record_event / mark_connected / check_health
             ▼
   [ConfidenceTracker]  关键流(aggTrade/kline/OI) → CONFIDENT/DEGRADED/UNKNOWN
             │
             ▼
   [FeatureEngine]  WindowManager(5s/15s/30s/1m/5m) + OI + Funding + Kline
   │  CVD 按每笔成交更新（每笔只计一次）→ 多窗口 FeatureSnapshot
   │  data_health 注入；stale → fail closed
             ▼
   [Detectors]  Anomaly → Startup → FalseStart → Continuation/Exhaustion/Withdrawal
             ▼
   [StateMachine]  12 条转移 guard + confidence 约束
             ▼
   AnalysisEvent(transition)  ──► latest_state / last_evidence_transition
             │                       (CurrentState 与 TransitionEvent 分离)
             ├─► Dashboard API (/api/radar /health /symbol /signals)
             ├─► AlertManager (START_CONFIRMED / EXHAUSTION / WITHDRAWAL)
             └─► transition_history (Replay/Labeling 用)
```

## 2. 关键单向流

`Collector → Health → Feature → Detector → StateMachine → Consumer`

- Detector 不回写 Collector；StateMachine 不回写 Feature。
- UI/Alerts 只消费 AnalysisEvent / CurrentState，不订阅原始行情自行计算。
- 任何 Detector 不允许直接请求 Binance。

## 3. 两阶段 Radar 数据流

| 阶段 | 覆盖 | 数据源 | 成本 | 输出 |
|------|------|--------|------|------|
| Stage1 LightScanner | 全 universe (≤100) | 24h ticker (1 REST, weight~40) | 低 | ANOMALY_CANDIDATE 排名 |
| Stage2 DeepScanner | 候选 (≤40) | aggTrade WS + Kline WS + OI REST + Funding REST | 中 | FeatureSnapshot + State 转移 |

候选集每 `light_scan_interval_s` 刷新；集合变化（set 比较）才重建 WS 连接，避免抖动。

## 4. Fail Closed 路径

```
WS 半死 (connected=true 但无数据)
  → FreshnessWatchdog: now - last_event_time > budget → STALE
  → ConfidenceTracker: 关键流 STALE → UNKNOWN
  → StateMachine: UNKNOWN 禁止 → START_CONFIRMED / WITHDRAWAL
  → FalseStartFilter: data_stale hard veto → REJECTED
```
