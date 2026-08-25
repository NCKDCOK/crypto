# CHANGELOG

## [V1.1] — 2026-08-26 — Runtime 修复 + 评分体系 + Top10 大屏 + UI 产品化

依据《资金行为雷达_V1.1》计划（19 步骤）。在后端修复 6 个 P0 问题、引入可解释评分体系、翻译层、Top10 排名，并从 100 行内联 HTML 升级为 6 页面 SPA 大屏。

### Runtime 修复 (P0)

- **P0.1 修复 Candidate≈Universe**：`LightScanner.scan()` 不再使用 `score > 0` 逻辑；改为至少 N 个增量 z-score 超阈值才成候选。
- **P0.2 Stage1 改短时增量**：不再用 24h 累计值；计算相邻采样增量（ΔQuoteVolume/ΔTradeCount/ΔPricePct）+ delta z-score，维护 rolling baseline。
- **P0.3 多周期 Kline**：`KlineCollector` 支持多周期订阅；DeepScanner 订阅 1m/5m/15m/1h 全部周期（之前仅 1m）。
- **P0.4 候选防抖**：BaseWSCollector 新增 `subscribe()`/`unsubscribe()` 增量方法；DeepScanner 集合变化时增量增删而非整组重连；Runtime 加入防抖逻辑（最低驻留 180s + 连续跌出 3 次才移除）。FreshnessWatchdog 新增 `unregister_stream()`。OI/Funding poller 新增 `add_symbol()`/`remove_symbol()`。
- **P0.5 修复"数据 false"**：`get_stats()` 输出人类可读 `data_status`（数据正常/数据降级/数据延迟/数据异常）；main.py 不再显示布尔值。
- **P0.6 统一 Dashboard**：旧 `DashboardService` 标记 DEPRECATED；main.py 直接使用 MarketRadarRuntime。

### 评分引擎 (`src/scoring/`)

- **ScoreEngine**：11 个子评分（7 基础 + 4 风险），每个 0~100，可展开 components。
- OpportunityScore = 加权基础分 - 风险扣分（risk_penalty_scale 缩放）。
- 评分预热：样本不足时不评分（`warmup_min_samples`）。
- 权重全部配置化（`configs/scoring.yaml`），禁止 magic number。
- 修复 `_sigmoid_z` 极端值 `math.exp` 溢出 bug（限制指数参数 [-700, 700]）。

### 置信度引擎 (`src/scoring/confidence.py`)

- **ConfidenceEngine**：独立于机会分，受数据健康/证据完整性/缺失源/多窗口一致性影响。
- 输出数值置信度 0.0~1.0 + penalties 列表。

### 翻译层 (`src/presentation/translator.py`)

- **PresentationTranslator**：内部术语 → 用户中文。
- 状态翻译（ANOMALY→发现异动、START_CONFIRMED→🚀启动确认...）+ emoji。
- 资金行为模块翻译（主动买盘/新增仓位/资金持续性/拥挤程度/撤离迹象）。
- 量价模块翻译（成交量/价格推动效率/回踩承接/突破有效性）。
- 假启动检查翻译（✅/❌ + 中文检查项）。
- 一句话结论生成（规则生成，非大模型）。
- "还缺什么"提示（SUSPECTED_START 状态下）。

### Top10 排名 (`src/presentation/ranking.py`)

- RankingScore = OpportunityScore × ConfidenceFactor。
- 排除规则：UNKNOWN / 评分不可用 / stale 不进入 Top10。
- 系统结论生成（规则生成）。

### UI 大屏 (`static/`)

- **SPA 架构**：原生 HTML/CSS/JS，FastAPI 静态托管，无构建系统。
- **6 页面**：首页 Top10 / 全市场搜索 / 币种详情 / 信号中心 / 数据健康 / 回放验证。
- **首页**：Hero Card (Top1) + 标准卡片 (Top2-10) + 系统结论 + Market Summary + 预览面板 + 空状态 + Loading 预热。
- **全市场**：搜索 + 7 种状态筛选 + 7 种排序 + 表格。
- **详情页**：评分 breakdown + 资金行为 + 量价 + 假启动检查 + 状态时间轴 + 普通/专业模式切换。
- **视觉**：科技感深色主题（#0a0e14）、冷青主色（#2dd4bf）、大留白、柔和圆角、150-300ms 动效、响应式、`/` 快捷键搜索。
- `main.py` 用 `StaticFiles` 替换内联 HTML。

### 配置

- 新增 `configs/hysteresis.yaml`：候选防抖（min_dwell_s/min_consecutive_drops/max_deep_symbols）。
- 新增 `configs/scoring.yaml`：评分权重 + 置信度因子 + 预热参数。
- `DetectorsConfig` 新增 Stage1 增量阈值（light_volume_delta_z/light_trade_count_delta_z/light_price_delta_z/light_min_anomaly_signals）。
- 新增 `HysteresisConfig` + `ScoringConfig` schema。

### API

- 新增 `GET /api/top10`：Top10 排名。
- 新增 `GET /api/market-summary`：系统结论 + Top10 + 统计。
- `GET /api/radar` 增强：opportunity_score / confidence / state_label / direction_label / summary / stale_flag。
- `GET /api/symbol/{symbol}` 增强：完整评分 breakdown / 置信度 breakdown / 翻译模块 / 时间轴 / 子评分标签。
- `GET /api/stats` 增强：data_status（P0.5）。

### 测试

- 全量 `pytest -q`：363 passed（原 306 + 新增 57）。
- 新增 scoring 测试：评分范围 / 预热 / 强启动 / 风险分 / breakdown / 配置权重 / 极端值无溢出。
- 新增 confidence 测试：范围 / stale / 缺失 OI / 低证据 / 预热 / 独立性。
- 新增 presentation 测试：状态/方向/数据翻译 / 资金行为/量价翻译 / 假启动检查 / 结论生成 / 排名排序/排除规则。

### 文档

- 更新 README：V1.1 状态、评分体系、UI 页面、项目结构、配置。
- 新增 `scripts/v11_smoke_test.py`：评分引擎端到端验证。
- 新增 `scripts/verify_static.py`：静态文件验证。

### 不变

- 不自动交易、无 API Key、无账户/仓位/杠杆/订单。
- 未加 RSI/MACD/SMC/OB/FVG/ML/Q-learning（§37 暂禁）。
- 未做自动交易/多交易所。

---

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
