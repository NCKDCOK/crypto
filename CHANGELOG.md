# CHANGELOG

## [V1.0] — 2026-08-25 — Runtime 集成审计与实盘改造

依据《资金行为雷达_仓库集成审计与V1实盘改造》文档。从"骨架完整但 runtime 仅接 aggTrade"改造为"全数据源 live + 两阶段 Radar + 真实 Health + Evidence 不擦除"。

### Audit
- 新增 `docs/RUNTIME_INTEGRATION_AUDIT.md`：21 模块逐项审计，定位 orphan 模块（Kline/OI/Funding/SymbolRegistry/Watchdog/RateLimiter/OILookup/QueueLagMonitor）、demo confidence override、Dashboard evidence 覆盖、单一 30s 窗口、硬编码 universe。

### P0 — Runtime 真实性
- **P0.1 删除演示 confidence override**：`main.py` 不再写 `CONFIDENT`；confidence 由 `FreshnessWatchdog` + `ConfidenceTracker` 真实派生。stale → UNKNOWN → 禁止 CONFIRMED（fail closed）。
- **P0.2 Evidence 不被 refresh 擦除**：分离 `latest_state`（CurrentState）与 `last_evidence_transition`（含证据 TransitionEvent）。自动 COOLDOWN 迁移不擦除证据。新增集成测试验证。
- **P0.3 全部采集器接入 runtime**：新建 `src/runtime.py` 编排层，实例化 AggTrade/Kline/OI/Funding 全部 collector + SymbolRegistry。

### Data Health
- `FreshnessWatchdog` 接入 runtime，每流注册 + record_event + mark_connected。connected≠healthy 生效。
- `ConfidenceTracker` 从关键流（aggTrade/kline/OI）派生 CONFIDENT/DEGRADED/UNKNOWN。
- `OILookup` 带容差 as-of lookup 接入。
- `QueueLagMonitor` + bounded trade queue（maxsize=50000）。
- `RateLimiter` 统一 REST 限频（权重预算滚动重置 + 429/418/熔断），权重预算每分钟重置。

### 多窗口 + Feature Catalog
- `FeatureEngine` 重写为状态化多窗口：统一 `WindowManager`（5s/15s/30s/1m/5m trade flow + 1m/5m/15m/1h kline context）。
- CVD 按每笔成交更新（修复旧编排层重叠窗口重复计数 bug）。
- 新增 Feature：price_return_{w} / price_acceleration / high_break / low_break / acceptance / volume_acceleration / average_trade_size / large_trade_ratio / taker_buy_volume / taker_sell_volume / delta_ratio / oi_change_30s / oi_contracts / price_efficiency / context_{w} / source_age / stale_flag。
- `FeatureSnapshot.data_health` 由 runtime 注入。

### Detector
- `StartupDetector` 增加 subtype（new_long_build / short_squeeze / new_short_build / long_liquidation）+ price_efficiency/acceptance/cvd 证据。
- `FalseStartFilter` 增至 8 个 Veto（新增 `ONE_BAR_SPIKE`），全部带解释性 detail（值/阈值/窗口/原因），data_stale 读真实 stale_flag/data_health。
- `VetoType` 枚举新增 `ONE_BAR_SPIKE`。

### 两阶段 Radar + 动态 Universe
- `SymbolUniverse`：exchangeInfo + 24h ticker，top-N by quote volume，blacklist/whitelist/liquidity floor/max_symbols。
- `LightScanner`（Stage1）：低成本全 universe 扫描 → 候选排名。
- `DeepScanner`（Stage2）：候选 ≤deep_max_symbols 跑完整 pipeline；候选集 set 变化才重建 WS（防抖动）。

### Dashboard
- 重写 `main.py`：Radar Cards + 顶部状态汇总 + /api/health + /api/symbol/{symbol} 详情（完整 Evidence/Veto）+ 排序（状态优先级 + evidence + recency，无神秘评分）。

### Replay
- `ReplayEngine` 重写：同一套核心逻辑（FeatureEngine + StateMachine + FreshnessWatchdog + ConfidenceTracker），health-driven confidence（不再 confidence_overrides）。支持多流输入（trades/oi/kline/funding）+ `simulate_healthy_streams`。deterministic。

### Smoke Test
- 新增 `scripts/live_smoke_test.py`：BTC/ETH/SOL，输出 trade/dup/age/reconnect/queue lag/feature sample/transitions，打印 PASS/FAIL。

### 配置化
- 扩展 `configs/`（app/symbols/features/data_health/detectors/state_machine）：proxy / universe / window / poll interval / 全部 detector+veto 阈值 / light scanner 阈值。`src/config` Pydantic schema 校验。
- WS collector + RateLimiter 支持 proxy。

### 代理
- WS（websockets 17）与 REST（httpx）均支持 `proxy` 配置，受限环境可用。

### 测试
- 全量 `pytest -q`：306 passed（原 289 + 新增 15 V1 集成/feature/detector 测试）。
- 新增：price_efficiency / retrace / RVOL / 短启动 / squeeze / long_liquidation / one_bar_spike / delta_reversal / absorption / evidence 保留 / 自动 COOLDOWN 不擦除证据。

### 文档
- 新增 RUNTIME_INTEGRATION_AUDIT / RUNTIME_ARCHITECTURE / LIVE_DATA_FLOW / FEATURE_CATALOG / DETECTOR_LOGIC / OPERATIONS / KNOWN_LIMITATIONS。
- 更新 README 项目状态为 V1 Runtime 已落地。

### 不变
- 不自动交易、无 API Key、无账户/仓位/杠杆/订单。
- 未加 RSI/MACD 等传统指标；未发明机会分；未做 SMC/OB/FVG/ML（§35 暂禁）。
