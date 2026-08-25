"""[DEPRECATED] V1.2 §3-4：Confidence 已拆分为 data_confidence + signal_confirmation。

- 数据可信度 → src.scoring.data_confidence.DataConfidenceEngine
- 信号确认度 → src.scoring.signal_confirmation.SignalConfirmationEngine

本文件仅保留 ConfidenceBreakdown 别名以兼容历史导入，新代码请直接使用新模块。
旧的「单一数值置信度」语义已废弃：它混淆了数据完整度与证据确认度。
"""

from __future__ import annotations

from src.scoring.data_confidence import DataConfidenceBreakdown as ConfidenceBreakdown

__all__ = ["ConfidenceBreakdown"]
