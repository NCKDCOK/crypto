# 资金行为雷达 V1.4：正式推荐生命周期修复 + 状态监督闭环 + Short Squeeze 专项 Setup

仓库：`https://github.com/NCKDCOK/crypto`

本轮不是继续堆指标，也不是单纯调整前端刷新频率。

本轮目标只有两个：

1. **彻底解决首页推荐频繁更换、状态抖动、推荐与实时排名混为一谈的问题。**
2. **在稳定推荐生命周期之上，新增第一套完整专项 Setup：Short Squeeze / 山寨币轧空。**

最终要做到：

> 实时数据可以每秒变化，但正式推荐必须是“发布后被持续监督的一条机会”，而不是每次实时重算后重新生成的排行榜。

系统仍然只做市场分析、提醒、模拟验证，不做自动下单。

---

# 一、本轮最高优先级：修复“实时排名 = 正式推荐”的结构错误

当前首页推荐不应该继续直接来源于实时 `Top10 / rank_symbols()`。

必须明确拆成两套完全不同的数据：

## 1. Live Ranking

用途：

> 内部发现当前市场上哪些币正在变强、变弱、异常、吸筹、启动。

特点：

- 可以频繁变化
- 可以每几秒重新计算
- 不直接代表正式推荐
- 主要给 Stage1 / Stage2 / Supervisor 使用

## 2. Published Recommendation

用途：

> 已经通过正式门禁、发布到首页并进入监督生命周期的机会。

特点：

- 一旦发布，不因短时间分数波动直接消失
- 有唯一 `recommendation_id`
- 有发布时间
- 有发布时快照
- 有当前监督状态
- 有 Trade Plan
- 有退出原因
- 有完整生命周期

首页必须主要读取：

`PublishedRecommendationRepository`

而不是实时排行榜。

---

# 二、新增 PublishedRecommendation 数据模型

建议新增：

`src/recommendations/models.py`

结构至少包括：

```text
recommendation_id
symbol
created_at
published_at
closed_at

side
setup_type
primary_timeframe

published_state
current_state

published_price
current_price

published_opportunity_score
published_signal_confirmation
published_data_confidence

current_opportunity_score
current_signal_confirmation
current_data_confidence

entry_zone_low
entry_zone_high
invalidation_price
tp1
tp2
tp3
rr1
rr2
rr3

status
risk_status
exit_reason

snapshot_id
simulation_id
```

状态建议：

```text
PENDING
PUBLISHED
MONITORING
WEAKENING
RISK
EXITED
EXPIRED
INVALIDATED
```

---

# 三、正式推荐必须使用 Recommendation Gate

新增：

`RecommendationGate`

禁止再用：

> “当前 score > 70 就立刻成为首页推荐”

必须通过正式门禁。

---

## 3.1 标准确认门槛

建议第一版至少要求：

```text
state in {START_CONFIRMED, CONTINUATION}

Opportunity >= 70
Signal Confirmation >= 75
Data Confidence >= 85

5m closed confirmation = true
Hard Veto = false
Trade Plan = ACTIVE
RR >= minimum_rr
Pump Risk < threshold
```

同时至少满足核心证据投票。

例如：

```text
核心证据 >= 3/3
辅助证据 >= 3/5
```

---

## 3.2 强确认

满足标准确认后，如果进一步满足：

```text
breakout_hold = true
retest_confirmed = true
second_impulse_confirmed = true
15m direction aligned = true
1h not strongly opposite = true
spot_perp_agreement = true
```

升级为：

`STRONG_CONFIRMATION`

用户显示：

> 强确认

---

# 四、正式推荐必须绑定 5m Decision Boundary

这是本轮最重要的修复之一。

后台仍然可以：

- 1s 更新价格
- 2s 更新 flow
- 2s 更新 OI
- 实时更新 CVD

但是：

> **正式 Recommendation 发布不能跟随 1~2 秒计算循环。**

推荐首次发布的主要决策节点：

`5m closed candle`

也就是说：

```text
实时异动
↓
候选
↓
实时监督
↓
5m 收盘
↓
正式 Gate
↓
发布 Recommendation
```

15m 收盘用于：

- 强确认
- 趋势升级
- 降低假突破

Hard Veto / Invalidation 可以即时触发退出，不必等待 5m 收盘。

---

# 五、修复 BreakoutLifecycleEngine 与 StateMachine 的调用顺序

必须检查当前 runtime 主流程。

正确顺序应该至少是：

```text
Feature Snapshot
↓
Breakout Lifecycle
↓
Structure / Location
↓
Setup Detection
↓
Evidence / Veto
↓
State Machine
↓
Score
↓
Recommendation Gate
↓
Published Recommendation
↓
Supervisor
```

不能继续出现：

```text
StateMachine 已先决定 START_CONFIRMED
↓
BreakoutLifecycleEngine 后面才计算 5m 是否真的突破
```

这会导致 5m 真突破逻辑只是展示字段，而不是门禁。

本轮必须彻底修正。

---

# 六、Supervisor 必须真正接管“发布后的机会”

当前 Supervisor 不能只做状态标签记录。

必须正式成为：

> Published Recommendation 的生命周期管理器。

每条已发布 Recommendation 必须注册到 Supervisor。

Supervisor 根据状态使用不同检查规则。

---

# 七、状态分区 / State Pool

建立清晰的监督池。

## 7.1 NORMAL_POOL

状态：

```text
SLEEPING
COOLDOWN
```

监督重点：

- Stage1 轻量异常
- 成交额增量
- Trade Count
- Price acceleration

低成本扫描即可。

---

## 7.2 ANOMALY_POOL

状态：

```text
ANOMALY
```

监督重点：

- 快速回吐
- OI 是否响应
- Delta 是否持续
- CVD 是否持续
- Pump Risk

目标：

> 判断它是噪声，还是值得进入重点观察。

---

## 7.3 WATCH_POOL

状态：

```text
SUSPECTED_START
ACCUMULATION candidate
SQUEEZE_BUILDUP
```

监督重点：

- Spot / Perp
- OI
- Funding
- CVD
- Taker
- 5m Breakout
- Retest
- Structure

这是“重点观察池”。

---

## 7.4 CONFIRMED_POOL

状态：

```text
START_CONFIRMED
```

监督重点：

- Trade Plan
- Entry Zone
- 二次确认
- Breakout Hold
- 资金是否持续

满足正式 Gate 后才允许发布首页 Recommendation。

---

## 7.5 CONTINUATION_POOL

状态：

```text
CONTINUATION
```

监督重点不再是“还能不能启动”，而是：

> 资金是否还在。

重点：

- OI persistence
- CVD persistence
- Delta persistence
- Spot confirmation
- Price Efficiency
- Retest health
- Withdrawal Risk

---

## 7.6 RISK_POOL

状态：

```text
EXHAUSTION
DISTRIBUTION
```

监督：

- CVD divergence
- High volume no progress
- OI decay
- Funding normalization
- Price Efficiency collapse
- Failed continuation

---

## 7.7 EXIT_POOL

状态：

```text
WITHDRAWAL
INVALIDATED
EXPIRED
```

动作：

- Recommendation 退出首页活跃区
- Simulation 结算
- 进入历史统计
- 保留完整快照

---

# 八、Supervisor 的状态改变必须有滞回

禁止：

```text
91 → 74 → 89
```

导致：

```text
确认 → 冷却 → 确认
```

应使用：

- minimum residence time
- consecutive confirmation
- consecutive failure
- hard veto override

例如：

```text
START_CONFIRMED 最低驻留：一个完整5m确认周期

普通降级：连续2个Decision Window失败

WITHDRAWAL / Invalidation：允许即时退出
```

具体阈值配置化。

---

# 九、首页不再“强行 Top10”

正式推荐首页只展示真实存在的 Published Recommendations。

如果只有 2 个：

> 就显示 2 个。

如果 0 个：

```text
当前暂无确认机会。

重点观察：4
异动观察：7
```

禁止用：

- COOLDOWN
- 机会分 30
- 无活跃信号

去凑满 Top10。

---

# 十、首页重新定义

首页只回答：

> “现在最值得我看什么？”

建议结构：

## 10.1 市场背景

```text
市场背景
山寨偏强 · BTC稳定 · 资金扩散
```

## 10.2 正式机会

显示：

`0~10 条 Published Recommendation`

## 10.3 重点观察

显示：

- 疑似吸筹
- 轧空蓄势
- 等待5m确认
- 回踩等待二次确认

只展示 3~5 个。

## 10.4 风险提醒

显示：

- 动能衰竭
- 派发增强
- 撤离

---

# 十一、首页卡片字段精简

首页不要继续展示十几个进度条。

正式推荐卡建议：

```text
#1 VOXELUSDT                         LONG

当前价 0.1265               +18.4%

🔥 轧空触发 · 5m标准确认

机会分        86
信号确认      91%
数据可信      96%

发布于        09:35
已跟踪        12m

资金输入      强 ↑
逼空强度      强 ↑
即时续航      良好 →
追涨安全      一般
撤离风险      低

当前阶段：
轧空触发 → 加速观察

系统判断：
空头拥挤明显，突破后 OI 开始回落，
主动买盘增强，存在空头回补推动。

当前计划：
等待首次健康回踩，不直接追价。
```

完整评分放 Drawer。

---

# 十二、Drawer 字段重新整理

删除普通/专业模式。

点击任何币：

> 右侧 Drawer 展开完整分析。

---

## 12.1 第一屏：决策摘要

显示：

```text
Symbol
当前价
24h涨跌
方向
Setup
主周期
当前生命周期

机会分
信号确认
数据可信

当前计划
Reference Entry Zone
Invalidation
TP1 / TP2 / TP3
R:R
```

---

## 12.2 第二屏：资金行为

显示：

```text
资金输入
吸筹迹象
承接强度
现货确认
主动资金偏向
Taker B/S
CVD
OI 5m
OI 15m
OI 1h
Funding
Premium
```

---

## 12.3 第三屏：Setup 专项

根据 Setup 动态显示。

例如 Short Squeeze：

```text
空头拥挤
逼空潜力
Funding 极值
Global L/S
Top Trader Account L/S
Top Trader Position L/S
OI Expansion
下跌效率衰减
突破确认
OI回落
Funding恢复
```

---

## 12.4 第四屏：结构与位置

```text
5m趋势
15m趋势
1h趋势
Swing High / Low
Breakout Level
Retest Zone
POC
VAH
VAL
Support
Resistance
Location Score
```

---

## 12.5 第五屏：证据投票

```text
核心证据 3/3
辅助证据 4/5
Veto 0
```

显示每条证据。

---

## 12.6 第六屏：生命周期

例如：

```text
09:21  空头拥挤
09:28  轧空蓄势
09:35  轧空触发
09:41  加速逼空
```

---

# 十三、Short Squeeze Engine：V1.4 第一套完整专项 Setup

新增：

`ShortSqueezeEngine`

目的：

> 识别“空头拥挤 → 轧空蓄势 → 轧空触发 → 加速 → 尾声”的完整生命周期。

---

# 十四、Short Squeeze 生命周期

```text
SHORT_CROWDING
↓
SQUEEZE_BUILDUP
↓
SQUEEZE_TRIGGER
↓
SQUEEZE_ACCELERATION
↓
SQUEEZE_EXHAUSTION
↓
EXIT
```

用户文案：

```text
空头拥挤
轧空蓄势
轧空触发
加速逼空
逼空尾声
```

---

# 十五、空头拥挤指标

新增：

```text
short_crowding_score
```

至少使用：

- Funding Rate
- Funding Z-score
- Funding percentile
- Premium / Basis
- Global Long/Short Account Ratio
- Top Trader Account L/S
- Top Trader Position L/S
- OI Expansion

禁止直接定义：

> Funding 负 = 庄家控盘。

只能翻译为：

> 永续市场明显偏空 / 空头拥挤增强。

---

# 十六、Funding 不使用固定阈值作为唯一逻辑

不要简单：

```text
funding < -0.1%
```

就触发。

新增：

```text
funding_zscore
funding_percentile_7d
funding_percentile_30d
```

例如：

```text
当前Funding位于过去30天最负的2%
```

这比固定阈值更适合不同山寨币。

---

# 十七、OI 专项升级

禁止再使用错误百分比字段。

明确拆分：

```text
oi_change_abs_5m
oi_change_pct_5m

oi_change_abs_15m
oi_change_pct_15m

oi_change_abs_1h
oi_change_pct_1h

oi_velocity
oi_acceleration
oi_zscore
```

UI 默认显示百分比。

原始绝对数量可在 Drawer 辅助显示。

---

# 十八、Short Squeeze Build-up / 轧空蓄势

不能因为 Funding 很负就认定做多。

至少寻找：

```text
Funding 极负
+
OI 持续扩张
+
Price 不再有效下跌
+
卖方推动效率下降
+
承接增强
```

重要信号：

```text
Sell Delta 很大
但 Price 跌幅越来越小
```

或者：

```text
CVD下降
但 Price横盘或抬高
```

这说明空方正在变拥挤，但价格继续下跌的效率减弱。

---

# 十九、Short Squeeze Trigger / 轧空触发

正式触发需要：

```text
5m关键阻力收盘突破
+
Taker Buy增强
+
CVD转强
+
Price Efficiency转强
+
核心空头结构被破坏
```

不能只看 Funding / OI。

---

# 二十、区分“新增多头上涨”和“空头回补上涨”

必须新增分类逻辑。

## 新增多头型

```text
Price ↑
OI ↑
Delta ↑
CVD ↑
```

## Short Squeeze 型

```text
Price ↑↑
Delta ↑↑
CVD ↑
OI ↓
```

用户必须看到不同 Setup 名称。

---

# 二十一、Squeeze Acceleration / 加速逼空

进入加速阶段时重点监控：

- Price acceleration
- OI contraction
- Taker Buy surge
- CVD acceleration
- breakout continuation
- liquidation proxy（如果无可靠 liquidation feed，则不要伪造）

输出：

```text
squeeze_strength
```

---

# 二十二、Squeeze Exhaustion / 逼空尾声

至少结合：

```text
OI快速下降
Funding恢复中性
Taker Buy衰减
CVD不再创新高
Price Efficiency下降
接近关键压力区
```

用户显示：

```text
逼空强度 91 → 54
顶部风险 23 → 67
撤离风险 18 → 61

状态：逼空尾声
```

此阶段不再允许新 Recommendation Entry。

---

# 二十三、新增 Long/Short Ratio 数据

对 Deep Candidates 获取：

```text
Global Long/Short Account Ratio
Top Trader Account Long/Short Ratio
Top Trader Position Long/Short Ratio
```

必须严格区分三个指标。

禁止混为同一个 `long_short_ratio`。

建议字段：

```text
global_account_ls_ratio
top_trader_account_ls_ratio
top_trader_position_ls_ratio
```

---

# 二十四、新增“普通账户 vs 大额持仓分歧”

新增：

```text
positioning_divergence_score
```

例如：

```text
Global L/S = 0.58
Top Trader Account = 0.94
Top Trader Position = 1.41
```

用户翻译：

> 普通账户明显偏空，但大额持仓整体更偏多，仓位分歧较明显。

这只能作为 Evidence，不得翻译成：

> 庄家一定做多。

---

# 二十五、Taker 字段修复

必须区分：

```text
delta_ratio
```

与：

```text
taker_buy_sell_ratio
```

定义：

```text
delta_ratio = (buy - sell) / (buy + sell)

taker_buy_sell_ratio = buy / sell
```

UI：

```text
主动资金偏向：+0.31
Taker B/S：1.66
```

禁止把负数 `delta_ratio` 显示成 Taker B/S。

---

# 二十六、Data Health 修复

首页禁止简单显示：

```text
数据异常
```

只因为 40 个 symbol 里有 1 个 UNKNOWN。

改成覆盖率：

```text
数据健康 94%
37 / 40 正常
2 个预热
1 个 OI 延迟
```

建议：

```text
>= 90% 正常
70~90% 部分降级
< 70% 异常
```

核心数据源整体断线才显示严重异常。

---

# 二十七、Recommendation Snapshot 必须不可变

正式 Recommendation 发布时生成：

`RecommendationSnapshot`

冻结：

```text
published_price
published_scores
published_features
published_setup
published_entry_zone
published_invalidation
published_tp
published_market_regime
published_evidence
```

后续变化只能新增：

```text
current_xxx
```

禁止覆盖历史发布快照。

---

# 二十八、正式 Recommendation 自动进入 Simulation Queue

每条正式推荐必须自动进入：

`SimulationQueue`

状态：

```text
WATCHING
```

如果当前价格不在关注区：

> 继续观察。

如果价格进入 Entry Zone：

> 执行 Entry Revalidation。

---

# 二十九、Entry Revalidation

到达关注区后禁止机械模拟入场。

必须重新验证：

```text
原方向仍有效
Setup 未 INVALIDATED
Data Health 正常
没有 WITHDRAWAL
没有 Hard Veto
位置仍合理
RR仍合理
关键 Evidence 未崩坏
```

通过：

```text
WATCHING
→ ARMED
→ SIMULATED_ENTRY
```

不通过：

```text
WATCHING
→ CANCELLED
```

并记录原因。

---

# 三十、模拟仓位监督

模拟入场后记录：

```text
entry_price
current_price
pnl_pct
MFE
MAE

distance_to_tp1
distance_to_invalidation

current_funding
current_oi
current_cvd
current_withdrawal_risk
```

退出类型：

```text
TP1_HIT
TP2_HIT
TP3_HIT
INVALIDATION_HIT
SIGNAL_WITHDRAWAL
DIRECTION_FLIP
EXPIRED
MANUAL_CLOSE
```

---

# 三十一、比较“固定退出”和“资金撤离退出”

模拟系统同时记录两套结果：

## A. 固定计划

```text
TP / Stop
```

## B. 资金监督退出

```text
Withdrawal / Exhaustion / Direction Flip
```

以后比较：

> 哪种方式 MFE / MAE / 最终收益更好。

这直接验证：

> “资金进场我跟，资金撤离我撤。”

---

# 三十二、首页刷新频率再次明确

不要依赖单一前端 interval。

必须由 Snapshot 层决定。

建议：

```text
当前价：3~5秒

资金摘要：5秒

当前实时评分：10~15秒

首页正式 Recommendation：事件驱动

正式排名：60秒最多重新计算一次

Recommendation 成员变化：只允许 Gate / Exit Event
```

最关键：

> 即使后台每 2 秒重算，首页正式推荐成员也不能每 2 秒变化。

---

# 三十三、推荐成员滞回

发布后最少跟踪：

建议：

`minimum_published_lifetime = 5m`

除非：

- Hard Veto
- Invalidation
- Withdrawal
- Data Critical Failure

否则不能因为：

```text
Opportunity 72 → 68
```

直接删除。

显示：

```text
推荐时 82
当前 68 ↓
状态：条件减弱，继续监督
```

---

# 三十四、Recommendation 不允许短周期重复发布

同一 symbol + direction + setup：

设置：

```text
recommendation_cooldown
```

例如：

`30m`

只有：

- 新 Setup
- 新方向
- 原 Recommendation 已完全结束并重新满足全新启动条件

才能生成新的 recommendation_id。

---

# 三十五、界面导航精简

建议最终保留：

```text
首页
全市场
监督台
模拟验证
数据健康
```

删除或合并：

- 独立信号中心 → 并入监督台
- 独立回放页 → 并入模拟验证
- 独立详情页 → Drawer
- 普通/专业模式 → 删除

---

# 三十六、监督台页面

监督台不是另一个 Top10。

按池展示：

```text
重点观察
确认机会
趋势跟踪
风险监控
撤离/结束
```

每条显示：

```text
Symbol
Setup
状态
进入该池时间
当前监督重点
距离下次 Decision Boundary
当前风险
```

例如：

```text
VOXELUSDT
轧空蓄势
进入重点观察 11m

下一步：等待5m突破 0.1264
核心Evidence：4/5
```

---

# 三十七、模拟验证页面

分为：

```text
等待入场
模拟持仓
已结束
统计
```

统计至少：

```text
总推荐数
进入关注区比例
二次验证通过比例
模拟成交数
TP1命中率
TP2命中率
Invalidation率
MFE
MAE
平均持有时间
```

按 Setup 分组：

```text
新增多头启动
回踩复燃
Short Squeeze
吸筹后启动
```

---

# 三十八、Historical Bootstrap / Startup Recovery

继续落实：

> 保存历史事实，实时结论重建。

程序启动：

```text
BOOTSTRAP
↓
补K线 / OI历史缺口
↓
恢复 Market Regime / Structure
↓
清理过期实时状态
↓
重新建立短周期 Flow / OI baseline
↓
WARMUP
↓
LIVE
```

只有 LIVE：

- 允许正式 Recommendation
- 允许 Push
- 允许 Simulation Entry

Recovery Replay 禁止发旧通知。

---

# 三十九、AI 职责保持限制

AI 不允许决定：

- State
- Setup
- Score
- Recommendation Gate
- Entry
- Invalidation
- TP
- Exit

AI 只做：

> 将已经确定的 Evidence / Plan 翻译成人话。

---

# 四十、本轮禁止继续扩张

本轮先不要增加：

- 更多复杂传统指标
- ML
- 神经网络
- 多交易所
- 自动下单
- 全套 SMC
- AI 自主交易

优先把：

> 推荐生命周期 + Supervisor + Simulation + Short Squeeze

真正做通。

---

# 四十一、开发执行顺序

严格按顺序执行：

```text
1. Audit 当前 Recommendation 数据流
2. Live Ranking 与 Published Recommendation 分离
3. 新建 PublishedRecommendation model/repository
4. 修正 Breakout → StateMachine 调用顺序
5. Recommendation Gate
6. 5m Decision Boundary
7. Supervisor 真正接管发布后的机会
8. State Pool
9. Recommendation membership hysteresis
10. 首页读取 Published Recommendations
11. 首页字段精简
12. Drawer 重构
13. OI 单位/1h 修复
14. Taker B/S 修复
15. Data Health coverage
16. Long/Short Ratio collectors
17. Short Crowding
18. Short Squeeze Lifecycle
19. Recommendation Snapshot
20. Simulation Queue
21. Entry Revalidation
22. Paper Position
23. MFE / MAE / Exit tracking
24. Supervisor UI
25. Simulation UI
26. Startup Recovery
27. Tests
28. Documentation
```

---

# 四十二、必须新增的测试

## Recommendation

```text
实时 score 抖动不能让已发布 recommendation 消失

同币短时间不得重复发布

COOLDOWN 不得进入正式推荐

无机会时首页允许 0 条

5m 未收盘不得正式发布 breakout recommendation
```

## Supervisor

```text
minimum residence 生效

连续失败才普通降级

Hard Veto 即时退出

Withdrawal 即时退出
```

## Short Squeeze

```text
negative funding only -> 不触发

funding extreme + OI up + price down strongly -> 不应误判轧空

funding extreme + OI up + downside efficiency collapse -> buildup

5m breakout + taker/cvd confirm -> trigger

price up + OI down -> squeeze acceleration evidence

funding normalize + OI down + efficiency collapse -> exhaustion
```

## Simulation

```text
推荐自动入队

Entry Zone 未到 -> WATCHING

进入区域但方向失效 -> CANCELLED

进入区域且重新确认 -> SIMULATED_ENTRY

TP / Invalidation / Withdrawal 正确结算
```

---

# 四十三、完成定义 DoD

只有全部满足才算本轮完成。

## Recommendation

- 首页不再直接展示 realtime top ranking
- Recommendation 有独立实体
- 5m closed gate 生效
- Recommendation 发布后持续跟踪
- score 小幅变化不会删除推荐
- 无机会时允许空首页

## Supervisor

- 每个状态有明确 pool
- 每个 pool 有不同监督目标
- Supervisor 真正控制推荐生命周期
- Hard Veto / Withdrawal 能及时退出

## Short Squeeze

- Funding percentile / zscore
- OI multi-window
- Global L/S
- Top Trader Account L/S
- Top Trader Position L/S
- Short Crowding
- Squeeze Build-up
- Trigger
- Acceleration
- Exhaustion

## Simulation

- Recommendation Snapshot 不可变
- 自动加入 Simulation Queue
- Entry Revalidation
- Paper Entry
- MFE / MAE
- 多种 Exit Reason
- Setup 统计

## UI

- 首页推荐稳定
- 当前价可以实时
- 正式卡片不每秒换成员
- Drawer 无普通/专业模式
- 监督台按状态池展示
- 模拟页可看到全生命周期

---

# 四十四、最终目标

最终首页不能再出现这种体验：

```text
06:31 #1 A
06:32 #1 B
06:33 #1 C
06:34 A消失
```

而应该是：

```text
06:31 VOXEL
进入轧空蓄势观察

06:35
5m确认突破
发布正式 Recommendation

06:35 ~ 06:52
一直留在首页
状态持续更新

机会分：86 → 81 → 78
逼空强度：88 → 92 → 73

但 Recommendation 不消失

06:53
进入逼空尾声

06:56
资金撤离
Recommendation 结束
进入模拟统计
```

系统要做到：

> **不是每秒告诉我谁现在分最高，而是发现一个真正值得看的机会后，从开始一直负责跟踪到结束。**

这才是本轮最重要的产品目标。
