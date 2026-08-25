"""Impulse Asymmetry Features — 多空推动效率（V1.2 §10）。

量化「缓涨急跌 / 急涨缓跌」：
- upside_velocity / downside_velocity：上涨/下跌过程的位移幅度
- upside/downside_volume_efficiency：单位成交额产生的位移
- upside/downside_delta_efficiency：单位主动资金产生的位移
- impulse_ratio：空头 vs 多头推动效率（>1 空头占优）
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from src.domain import AggressorSide, TradeEvent


@dataclass
class ImpulseFeatures:
    upside_velocity: float | None
    downside_velocity: float | None
    upside_volume_efficiency: float | None
    downside_volume_efficiency: float | None
    upside_delta_efficiency: float | None
    downside_delta_efficiency: float | None
    impulse_ratio: float | None


def compute_impulse_asymmetry(trades: Sequence[TradeEvent]) -> ImpulseFeatures:
    """从主窗口成交计算多空推动效率。

    按相邻成交价格变化分解为上涨段 / 下跌段：
    - upside_velocity = Σ|正向价格位移|
    - downside_velocity = Σ|负向价格位移|
    - volume_efficiency = velocity / 成交额（越大越高效）
    - delta_efficiency = velocity / |主动资金|（同向主动资金推动效率）
    """
    if len(trades) < 2:
        return ImpulseFeatures(None, None, None, None, None, None, None)

    up_vel = 0.0
    down_vel = 0.0
    up_vol = 0.0  # 上涨段成交额
    down_vol = 0.0  # 下跌段成交额
    up_delta = 0.0  # 上涨段主动买
    down_delta = 0.0  # 下跌段主动卖

    sorted_trades = sorted(trades, key=lambda t: t.receive_time)
    for i in range(1, len(sorted_trades)):
        prev = sorted_trades[i - 1]
        cur = sorted_trades[i]
        p_prev = float(prev.price)
        p_cur = float(cur.price)
        move = p_cur - p_prev
        notional = float(cur.quote_notional)
        # 主动资金（按当前成交方向）
        if cur.aggressor_side == AggressorSide.BUY:
            taker = notional
        elif cur.aggressor_side == AggressorSide.SELL:
            taker = -notional
        else:
            taker = 0.0

        if move > 0:
            up_vel += abs(move)
            up_vol += notional
            if taker > 0:
                up_delta += taker
        elif move < 0:
            down_vel += abs(move)
            down_vol += notional
            if taker < 0:
                down_delta += abs(taker)

    up_vol_eff = (up_vel / up_vol) if up_vol > 0 else None
    down_vol_eff = (down_vel / down_vol) if down_vol > 0 else None
    up_delta_eff = (up_vel / up_delta) if up_delta > 0 else None
    down_delta_eff = (down_vel / down_delta) if down_delta > 0 else None
    impulse_ratio = (down_vel / up_vel) if up_vel > 0 else (None if down_vel == 0 else float("inf"))

    return ImpulseFeatures(
        upside_velocity=up_vel if up_vel > 0 else None,
        downside_velocity=down_vel if down_vel > 0 else None,
        upside_volume_efficiency=up_vol_eff,
        downside_volume_efficiency=down_vol_eff,
        upside_delta_efficiency=up_delta_eff,
        downside_delta_efficiency=down_delta_eff,
        impulse_ratio=impulse_ratio if (impulse_ratio is not None and impulse_ratio != float("inf")) else None,
    )
