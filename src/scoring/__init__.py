"""Scoring package — 可解释评分引擎 + 独立置信度引擎。

依据：V1.1 计划 §十二~§十六
- OpportunityScore：基础机会分 - 风险扣分
- ConfidenceFactor：独立于机会分，受数据健康/证据完整性/多窗口一致性影响
- RankingScore = OpportunityScore × ConfidenceFactor
- 每个分数可展开 Evidence
"""
