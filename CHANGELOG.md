# CHANGELOG

## [V1.4.1] — 消除「多套真相」修复（首页正式推荐 + 单一门槛链 + 双时钟生命周期）

修复 V1.4 遗留的 7 处问题：首页仍读实时 Top10、双推荐门槛、普通降级过实时、主周期写错、
排名滞回管成员、三层 Supervisor 职责重叠、DecisionSnapshot 承担正式推荐。目标：**只有一条主线**。

### P0 — 消除多套真相
- **首页改读 PublishedRecommendation（§十）**：前端 `renderHome` 主区 = `published_recommendations`
  （0~N 真实存在，独立对象直到 EXITED/INVALIDATED/EXPIRED 才消失），`confirmed_opportunities`
  降为次级「即将确认」区（允许快速变化）；`homeMateriallyChanged` 基于 published 成员（rec_id+status）；
  新增 `renderPublishedCard` 双值（发布时 vs 当前，§三十三 86 不被覆盖成 73）+ 三段 timeframe + 监督时长。
  **不再强行 Top10，0 条即显示 0 条**。删除废弃 `renderHomeCard`/`snapValue`/`liveValue`/`supervisionStage`。
- **删旧 Snapshot Gate + 单一门槛链（§2）**：删 `RecommendationSnapshotService.passes_gate`（第二套门槛）；
  改为发布即冻结快照：`_publish_recommendation → _create_recommendation_snapshot`（绑定 recommendation_id）
  → SimulationQueue。`SimulationQueueItem`/`RecommendationSnapshot` 加 `recommendation_id` 字段；
  **无 recommendation_id 的机会禁止进入模拟系统**。删 `_maybe_create_snapshot` 周期独立判定。
- **双时钟生命周期（§3）**：`RecommendationLifecycleEngine.tick` 拆 `tick_fast`（实时每 tick：
  current_* 更新 + 即时退出 Hard Veto/Withdrawal/Invalidation/Data Critical）/ `tick_slow`（仅 5m 收盘边界：
  正常→减弱/减弱→恢复/减弱→退出/风险池）。**fail_streak 按 5m Decision Window 计数**（连续 N 个 5m 收盘
  失败才退出，非 2 秒 tick），避免驻留期满后 4 秒就退出。runtime `_gate_and_publish` 5m 边界调 slow。
- **主周期拆三段（§4）**：`PublishedRecommendation` 加 `trigger_timeframe=5m` / `confirmation_timeframe=15m`
  / `context_timeframe=1h`（原 `primary_timeframe="15m"` 硬编码错误）。首页显示「5m 主触发 · 15m 确认 · 1h 同向」。
- **首页正式机会不经 RankingHysteresis（§5）**：published 是独立对象，直接 `published_repo.active()`，
  不经排序/滞回；confirmed 次级区才经实时排名。

### P1 — 统一三层监督职责（§6）
- **StatePool Supervisor**（`SupervisorEngine.update`）：管「这个币属于哪个监控池」。
- **Recommendation Lifecycle**（`RecommendationLifecycleEngine.tick_fast/slow`）：管「已发布推荐能否继续存在」
  （唯一决定推荐退出）。
- **Simulation Supervisor**（`SimulationQueueManager`/`PaperPositionManager`）：管「模拟观察/仓位下一步」。
- `SupervisorEngine.evaluate` 不控制推荐退出（加职责边界注释），避免两套退出互相打架。

### 测试与文档
- 更新测试：`test_snapshot.py` 删 `TestGate`（passes_gate 语义迁至 `test_recommendation_gate.py`）+ 加
  recommendation_id 绑定断言；`test_queue.py` `TestNonFormalNotEnqueued`→`TestSnapshotEnqueue`；`test_lifecycle.py`
  `tick`→`tick_slow`（状态转移）；`test_boundary_publish.py` 状态转移测试改 `tick_slow`。
- 全套 **809 passed**（删旧 gate 测试 -13，净增双时钟/绑定用例）。
- 首页实测：正式机会 0 条（非 LIVE 正确空态）、即将确认/正在观察次级区、风险提醒在线。

**最终主线**：实时数据 → Candidate Radar → 等 5m 收盘 → RecommendationGate → PublishedRecommendation
→ 首页正式推荐 → Lifecycle（Monitoring/Weakening/Risk）→ Exit/归档；并行 PublishedRec → Snapshot
→ SimQueue → Revalidation → 入场 → 持仓 → 统计。

---

## [V1.4] — 正式推荐生命周期修复 + 状态监督闭环 + Short Squeeze 专项 Setup

依据《crypto_radar_v1.4_fix_update_plan.md》。本轮目标：彻底解决首页推荐频繁更换/状态抖动，
在稳定推荐生命周期之上新增第一套完整专项 Setup（Short Squeeze）。系统仍只做市场分析/提醒/模拟验证，
**不做自动下单**。全套 **822 passed**（V1.4 新增 119 用例）。

### 正式推荐生命周期（§一–§六、§三十三、§三十四）

- **PublishedRecommendation 独立实体（§二）**：`src/recommendations/models.py` — 独立生命周期枚举
  RecommendationStatus（PUBLISHED/MONITORING/WEAKENING/RISK/EXITED/EXPIRED/INVALIDATED），
  published_* 冻结不可变 / current_* 持续更新（§二十七）；`PublishedRecommendationRepository`
  为首页数据源（§一.2）。
- **RecommendationGate 正式门禁（§三）**：`src/recommendations/gate.py` — 标准确认（状态范围/
  三门槛/5m 收盘/突破类 breakout_confirmed/Hard Veto/Trade Plan/RR/Pump Risk/stale/证据投票 3-3+3-5）
  + 强确认（§3.2 六项：breakout_hold/retest/second_impulse/15m 对齐/1h 不逆向/spot-perp 一致）。
  禁止「score>70 即首页推荐」。
- **5m 决策边界（§四）**：runtime `_gate_and_publish` — 仅新 5m 收盘窗口评估 Gate（`_last_gate_bar`
  去重）；突破生命周期先于 StateMachine 计算（§五 调用顺序修复），breakout_confirmed 成为真实门禁输入
  而非展示字段；Hard Veto/Invalidation/Withdrawal 即时路径不等待 5m 收盘（§四.4）。
- **RecommendationLifecycleEngine（§六）**：`src/recommendations/lifecycle.py` — Supervisor 真正
  接管已发布推荐：PUBLISHED→MONITORING⇄WEAKENING→RISK→终态。即时退出（Hard Veto/Withdrawal/
  Invalidation/Data Critical，绕过滞回+驻留）；普通降级需连续 N 次离开正式范围（`lifecycle_downgrade_streak`
  配置化，§八）；最低驻留 `minimum_published_lifetime`（§三十三）+ score 抖动→WEAKENING 不删除。
- **推荐成员滞回/冷却（§三十三/§三十四）**：同 symbol 活跃期单活跃去重（§九）+ 同 symbol+方向+Setup
  30m 冷却（新 Setup/方向可立即重发）；首页不再强行 Top10，0 条即显示 0 条（§九/§十）。

### 首页与 Drawer（§九–§十二）

- **首页读取 PublishedRecommendationRepository（§十）**：`get_home()` 新增 `published_recommendations`
  字段（0~N 真实存在），卡片字段精简（§十一：双值 published_*/current_* + 计划摘要 + 实时小评分），
  完整评分放 Drawer。confirmed_opportunities 保留为补充。
- **Drawer Setup 专项屏（§十二.3）**：`get_symbol_detail` 新增 `short_squeeze`（生命周期/拥挤度/逼空强度）
  + `published_recommendation`（活跃正式推荐引用），供 Drawer 第三屏动态展示。

### Short Squeeze 专项（§十三–§二十二）

- **ShortSqueezeEngine（§十三/§十四）**：`src/engines/short_squeeze.py` — 完整生命周期
  SHORT_CROWDING→SQUEEZE_BUILDUP→SQUEEZE_TRIGGER→SQUEEZE_ACCELERATION→SQUEEZE_EXHAUSTION→EXIT。
- **空头拥挤度（§十五）**：`short_crowding_score` 0~100（Funding 极值+Premium/Basis+普通户偏空+OI 扩张
  共振），禁止仅 Funding 负触发、禁止翻译成「庄家控盘」。
- **Funding zscore/percentile（§十六）**：`funding_zscore`（robust baseline）+ `funding_percentile_7d/30d`
  字段（更长历史优雅降级），取代固定阈值。
- **OI 专项（§十七）**：`oi_zscore`（robust）+ 既有 `oi_change_abs/pct_{5m,15m,1h}`/velocity/acceleration。
- **Buildup/Trigger/Acceleration/Exhaustion（§十八–§二十二）**：buildup 需「下跌效率衰减」（卖压大但价格
  不再有效下跌，§十八）；trigger 需 5m 收盘突破+Taker Buy+CVD+Price Efficiency 共振（§十九）；
  分类新增多头（Price↑ OI↑）vs 空头回补（Price↑ OI↓，§二十）；acceleration 输出 `squeeze_strength`（§二十一）；
  exhaustion 不再允许新入场（§二十二）。
- **Long/Short Ratio 采集（§二十三）**：`src/collectors/long_short_ratio_collector.py` — 三个指标严格区分
  （global_account_ls / top_trader_account_ls / top_trader_position_ls），禁止混为同一个 long_short_ratio。
- **普通户 vs 大户分歧（§二十四）**：`positioning_divergence_score`，仅作 Evidence，禁止翻译成「庄家做多」。
- **Taker B/S 修复（§二十五）**：`delta_ratio=(buy-sell)/(buy+sell)` 与 `taker_buy_sell_ratio=buy/sell`
  严格区分，FeatureEngine 暴露 `taker_buy_sell_ratio`。

### 配置与测试

- **配置（§三/§八/§三十三/§三十四）**：`configs/recommendation.yaml` + `RecommendationConfig`
  （门禁阈值/强确认/5m 边界/最低驻留/冷却/`lifecycle_downgrade_streak`/容量），无 magic number。
- **测试（§四十二）**：新增 119 用例 — Recommendation 36 门禁单元 + 21 边界/发布/接线 + 18 生命周期 +
  14 Short Squeeze（仅 Funding 不触发 / 强势下跌不误判 / 下跌效率衰减→buildup / 5m 突破→trigger /
  价格↑ OI↓→acceleration / exhaustion / 新增多头 vs 轧空分类）+ 5 L/S ratio parse + OI zscore。
- **跨模块接线**：runtime `_compute_symbol` 调用 ShortSqueezeEngine + `_supervise_published`；
  DeepScanner 集成 LongShortRatioCollector；FeatureEngine 暴露 funding_zscore/L-S ratios/
  taker_buy_sell_ratio/oi_zscore。

**测试：822 passed，12 warnings（均为既有 TestClock 收集 / 弃用告警）。仅影子/纸面信号，禁止自动交易。**

---

## [V1.3] — 状态监督 × 模拟验证 × UI 重构（P0 批次）

依据《资金行为雷达_V1.3_状态监督_模拟验证_UI重构_更新计划》。
本批次完成 P0 修复全部项：数据健康改为覆盖率口径、OI 单位/1h/pct、排名阈值与节奏、Trade Plan 合法状态门控、配置化落地；P1 状态监督 / P2 模拟验证 / P3 UI 重构按计划后续推进。

### 配置层

- **P0 配置化**：新增 `configs/{ranking,supervision,simulation,health_coverage}.yaml` + Pydantic 配置模型（`RankingConfig`/`SupervisionConfig`/`SimulationConfig`/`HealthCoverageConfig`），`AppConfigBundle` 默认装配，UI 刷新节奏与排名阈值统一从配置读取。

### 数据层（数据健康）

- **P0 Data Health 覆盖率（§46）**：新增 `src/health/coverage.py` — 覆盖率 = 健康（OK/WARN）符号×数据流对数 / 总数；≥90% 正常、70~90% 部分降级、<70% 异常；核心 aggTrade 数据流整体断线 → 严重异常（优先于覆盖率阈值）。单币 OI 延迟不再触发「数据异常」。新增 `GET /api/health/coverage` 端点（`/api/health` 行结构保持不变，兼容现有前端）。
- **P0 OI 单位/1h/pct（§12）**：OI 变化统一为 1 小时口径、单位归一、pct 展示；OI momentum / 相对位置特征（`src/features/oi_features.py`、`flow_features.py`、`engine.py` 接线）。

### 评分层

- **P0 排名规则（§13）**：Top10 排除 COOLDOWN、不再强制凑满 10 个；正式榜仅 START_CONFIRMED/CONTINUATION，观察榜 ANOMALY/SUSPECTED_START；阈值（min_opportunity=70 / min_signal_confirmation=75 / min_data_confidence=85）与刷新节奏从 `RankingConfig` 读取。
- **P0 Trade Plan 合法状态门控（§18-19）**：SLEEPING/COOLDOWN/ANOMALY 不生成；SUSPECTED_START/ACCUMULATION/RETEST_PENDING 仅候选预案（UI 标注「候选预案，尚未确认」）；START_CONFIRMED/CONTINUATION 正式计划。冻结版本化：每个符号首次正式冻结为 V1，新 Setup 升级为 V2 并生成新 trade_plan_id，冻结幂等、非 ACTIVE 不冻结、过期保留冻结值。

### 测试

- **P0 Tests**：覆盖率 18 用例（阈值边界 90/70、核心断线优先级、per-stream 分解、Runtime 集成：单 OI 流延迟 → 87.5% 部分降级）、OI 特征、排名阈值/节奏、Trade Plan 21 用例（合法状态门控 + 冻结版本化 + 过期）。全套 **585 passed**。

**测试：585 passed，12 warnings（均为既有弃用告警）。仅影子/纸面信号，禁止自动交易。**

---

## [V1.3] — 状态监督 × 模拟验证 × UI 重构（P1-P3 批次）

依据《资金行为雷达_V1.3_状态监督_模拟验证_UI重构_更新计划》§5-§10（P1 监督）、§33-§39（P2 模拟验证）、§41-§57（P3 UI 与关联 API）。
P0 批次之上完成：状态分池监督 + 生命周期追踪、推荐快照 + 模拟验证 12 状态闭环、5 页 SPA 重构 + 侧滑 Drawer + 监督台 Kanban + 统计与回放。

### 监督层（P1）

- **State Pool Manager（§5-§6）**：`src/state/pool.py` 8 池（anomaly/watch/confirmed/continuation/risk/exit/cooldown/sleeping），每池 PoolSpec 独立监督规则（升级/降级/滞留/撤离入口）、监督级别（low/medium/high）、池级监督问题 `supervision_question` 8 条中文（§41）。
- **SupervisorEngine（§5-§7）**：`src/state/supervisor.py` 状态迁移滞回（单次评分下降不降级 / 连续失去证据才降级 / 明确 Veto 立即失效）、`condition_fail_streak`、`last_action`、进入池/状态时间戳；§8 生命周期自发现→撤离全链路追踪。
- **监督 API（§63-§65）**：`GET /api/supervision`（按池 Kanban 矩阵）、`GET /api/supervision/{symbol}`（详情 + §42 状态日志时间线，`transition_history` 过滤单币）。

### 模拟层（P2）

- **RecommendationSnapshot（§33）**：19 字段 frozen dataclass，immutable，不被后续实时数据覆盖；相同 Setup 版本化；`trade_plan_id`/`snapshot_id` 沉淀。
- **Simulation Queue + Revalidation（§34-§35）**：正式推荐（START_CONFIRMED/CONTINUATION）自动入队，COOLDOWN/SUSPECTED_START 不入正式模拟；12 状态（WATCHING→ENTRY_ZONE_REACHED→REVALIDATING→ARMED→SIMULATED_ENTRY→OPEN→CLOSED/EXPIRED/CANCELLED/INVALIDATED/MISSED）；Entry Zone 到达后二次验证（方向有效→入场 / Withdrawal→取消 / Direction Flip→取消 / stale→不入场）。
- **Position Monitoring + 动态退出（§36-§39）**：MFE/MAE/TP1-3/Stop/Withdrawal Exit；静态跟踪直到原 TP1 或失效（孰先），24h 上限 → TIME_EXPIRED；动态退出与固定计划双轨记录（`static_plan_result`）。
- **统计（§37-§39）**：推荐次数/进入观察区/通过 Revalidation/模拟入场/TP1·TP2/失效/撤离退出 + 平均 MFE/MAE + 分桶（机会分/确认度/Setup/方向/时间框架/市场背景）+ Setup 转化率。
- **模拟 API（§63-§65）**：`GET /api/simulations`、`GET /api/simulations/{id}`（item+position+events+result）、`GET /api/statistics`。

### UI / 关联 API（P3）

- **5 页 SPA 重构**：`static/js/app.js` 全新重写（~1500 行）+ `static/js/api.js`（15 个 API 方法）+ `static/index.html`（59 行）+ `static/css/style.css` 追加 V1.3 P3 段；首页/全市场/监督台/模拟验证/数据健康，冗余调试页合并或删除（DoD §68）。
- **首页（§15-§16/§69）**：top-stats 六字段、Top Opportunities ≤10 不凑满、COOLDOWN 不入榜、主值取 `decision_snapshot.decision` 冻结值（§55）、实时双值仅入 Drawer（§56）、趋势箭头（§54 基线 + diff>±1）、`homeMateriallyChanged` 节流（§12/§66.8 首页不秒级重排）。
- **Side Drawer A–I（§17/§28-§34/§41-§42）**：当前结论/核心评分双值/当前计划/生命周期/评分明细/资金摘要（OI 5m·15m·1h + Taker B/S + CVD + 现货×合约 + 多空推动）/突破生命周期/Evidence-Veto/模拟状态。
- **监督台（§41-§42/§70）**：6 列 Kanban（anomaly/watch/confirmed/continuation/risk/exit）+ 池级监督问题 + 监督 Drawer（含 §42 时间线）。
- **模拟验证（§33-§39/§71）**：sim-counts + 5 Tab（等待入场/运行中/已结束/统计/历史回放），回放展示动态结果 vs 固定 TP/Stop 对比。
- **数据健康（§46）**：覆盖率大条 + 交易对×流明细 + 核心流中断横幅。
- **轮询节奏（§12）**：priceTimer 4s / dataTimer 10s / slowTimer 30s / topTimer 60s。

### 测试 / 文档

- **P1-P3 Tests**：`tests/api/test_v13_api.py` 9 用例（Kanban 池分类/Top10 空态/监督详情 timeline/模拟列表/统计结构）+ 既有全量回归。全套 **728 passed，12 warnings（均为既有弃用告警）**。
- **UI 冒烟（§66.8/§67）**：`scripts/ui_seed_server.py`（8051 种子服务器，9 币种全状态矩阵）经 Playwright 验证 6 项——首页不秒级重排、Top10 不凑满、COOLDOWN 不入榜、Drawer 内容稳定、Simulation 状态正确、监督台 Pool 正确；另修复 `renderHomeCard` 子评分 tiles 常量拼接崩溃 bug。实盘 30-60 分钟冒烟受本机 Binance 地理封锁（HTTP 451）阻塞，待非封锁环境执行。
- **文档**：新增 `docs/UI.md`（UI 架构：页面清单/轮询节奏/首页/监督台/模拟验证/数据健康/API 对应）。

**测试：728 passed，12 warnings。仅影子/纸面信号，禁止自动交易。**

---

## [V1.2] — 资金生命周期 × 市场背景 × 结构位置 × 置信度 × Trade Plan

依据《资金行为雷达_V1.2》修改方案（25 执行步骤 + 用户追加持久化/恢复层）。
把系统从「实时数据多、分数多、状态变化快」升级为「打开首页就知道最值得看什么、为什么、处于什么阶段、是否适合参与、什么位置失效、资金有没有撤离」。

### 基础层

- **P0 本地持久化 + 停机恢复**：新增 `SqliteRepository`（K线/OI/Funding/信号/TradePlan 持久化）；新增 `RecoveryManager` 三档恢复策略（<5m 快速 / 5m~1h 补历史重算 / >1h 全部失效重建）；新增 `SystemMode`（RECOVERY→WARMUP→LIVE）门控——非 LIVE 不产出强确认 Top10、不发正式推送；OI 重启后第一条作新基准，CVD/Delta 重新预热；Trade Plan 停机超阈值标记 EXPIRED。
- **P1 Confidence 语义修复（§3-4）**：拆分 `data_confidence`（数据可信度，0~100）与 `signal_confirmation`（信号确认度，0~100），保留 `ConfidenceState` 作 fail-closed 门；排名改三因子（机会×确认×可信）；UI 标「确认度」非「胜率」。
- **P2 Missing score 修复（§5）**：评分引擎缺失数据不再默认 50，按可用权重归一化，每个子评分带 `coverage`/`missing`。
- **P3 UI 刷新节奏稳定（§6）**：`RankingHysteresis`（30s 重排，分差>3 或连续 2 轮才交换）；前端分层轮询（价格 1.5s / OI-CVD-Delta 5s / Top10 12s）；子评分 ±3 节流；新增 `/api/prices` 轻量价格端点。

### 分析层

- **P4 Market Regime Engine（§8）**：7 状态市场背景（ALT_RISK_ON/OFF/BTC_DOMINANT/CHOP/DELEVERAGING/PANIC/NEUTRAL）+ 文案。
- **P5 Spot Data（§9）**：`SpotSymbolRegistry` + 现货 aggTrade 采集器 + spot_volume/cvd/taker/delta + `spot_perp_agreement`；无现货标记 unavailable 不伪造。
- **P6 Spot×Perp Confirmation（§9）**：健康启动 vs 杠杆主导分类。
- **P7 Impulse Asymmetry（§10）**：多空推动效率（upside/downside velocity + volume/delta efficiency + impulse_ratio）。
- **P8 Accumulation/Absorption（§11）**：sell absorption / CVD-价格背离 / 低位换手 / OI 渐增 / Spot 确认 / Reclaim。
- **P9 Dormant Revival（§12）**：沉睡复活识别。
- **P10 Distribution（§13）**：派发风险（高量低效/CVD 背离/OI 衰减/突破失败/现货卖压）。
- **P11 Setup Type（§14）**：10 类 Setup 分类 + 中文文案。
- **P12 Breakout Lifecycle（§15）**：突破（5m 收盘站外）→保持→回踩→二次确认→强确认（5m+15m 同向+1h 不逆）。
- **P13 Structure Engine（§16）**：Swing H/L、HH/HL/LH/LL、Support/Resistance、Breakout Level、Retest Zone、Failed Breakout/Breakdown、VWAP、ATR。
- **P14 Volume Profile（§17）**：POC/VAH/VAL/HVN/LVN/High-Low Volume Zone。
- **P15 Location Engine（§19）**：位置偏高不建议追 / 合理回踩承接区。
- **P16 Trend Engine 升级（§20）**：HH/HL/LH/LL + multi-bar slope + ATR-normalized return + VWAP relation + 多周期 agreement。
- **P17 Continuation 真实证据化（§21）**：禁止因在 CONTINUATION 就高分；改为 OI/CVD/Delta persistence + healthy retrace + efficiency，需 min_evidence_count 通过。

### 评分层

- **P18 Score Engine V1.2（§23-24, §41）**：三评分独立（机会/确认/可信）+ setup_type 注入 + Pump Risk 高时 Opportunity 受惩罚；`PumpRiskEngine`（§41）。
- **P19 Trade Plan Engine（§25）**：Entry 来自结构（Breakout/Retest/Support/POC/VWAP/Swing/ATR，非 AI 自由生成）/ Invalidation / TP1-3 / R:R / chase_status；START_CONFIRMED 冻结 snapshot 不漂移；RR 不足输出「不建议追入」。

### UI / Push

- **P20 Top10 V1.2 卡片（§27）**：当前价 / Setup / 机会×确认×可信 / 子评分 / 一句话 / 当前计划；市场背景横幅；无机会明确等待。
- **P21 Side Drawer（§28-34）**：右侧 480px 滑出抽屉，六部分（概要/评分/资金摘要/突破生命周期/证据投票/状态时间轴）。
- **P22 Push Engine（§37-38）**：State Transition Push（SUSPECTED→CONFIRMED / CONFIRMED→CONTINUATION / CONTINUATION→EXHAUSTION / EXHAUSTION→WITHDRAWAL）；§38 完整字段；RECOVERY 期不发；`/api/pushes`。

### 校准

- **P23 Replay Calibration（§42）**：`CalibrationStore` 记录每个 Setup 快照 + future_5m/15m/1h + MFE/MAE；分桶正向率统计（历史胜率待足够样本后输出）。

### 测试 / 文档

- **P24 Tests（§45）**：Accumulation/Distribution/Breakout/Setup/Location/TradePlan/Confidence 全场景回归（528 tests green）。
- **P25 Documentation**：CHANGELOG + ADR-0002 记录 V1.2 大规模重构决策。

**测试：528 passed。权重全部标注 uncalibrated（待 Replay Calibration 校准）。禁止自动交易。**

---

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
