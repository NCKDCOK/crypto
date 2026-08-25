"""V1.1 smoke test — verify all modules import and integrate correctly."""
from pathlib import Path
from src.config import load_config

cfg = load_config(Path("configs"))
print("Config loaded OK")
print(f"  hysteresis.max_deep_symbols = {cfg.hysteresis.max_deep_symbols}")
print(f"  hysteresis.min_dwell_s = {cfg.hysteresis.min_dwell_s}")
print(f"  scoring.w_capital_inflow = {cfg.scoring.w_capital_inflow}")
print(f"  detectors.light_volume_delta_z = {cfg.detectors.light_volume_delta_z}")
print(f"  features.kline_context_intervals = {cfg.features.kline_context_intervals}")

from src.scoring.engine import ScoreEngine
from src.scoring.data_confidence import DataConfidenceEngine
from src.scoring.signal_confirmation import SignalConfirmationEngine, ConfirmationContext
from src.presentation.translator import PresentationTranslator
from src.presentation.ranking import rank_symbols
print("All V1.1 modules imported OK")

from src.runtime import MarketRadarRuntime, LightScanner, DeepScanner
print("Runtime imported OK")

# KlineCollector multi-interval
from src.collectors.kline_collector import KlineCollector
from src.domain import KlineInterval
streams = KlineCollector.build_streams(
    ["BTCUSDT", "ETHUSDT"],
    [KlineInterval.M1, KlineInterval.M5, KlineInterval.M15, KlineInterval.H1],
)
print(f"Kline streams ({len(streams)}): {streams[:4]}...")

# Translation
print(f"START_CONFIRMED -> {PresentationTranslator.state_display('START_CONFIRMED')}")
print(f"LONG -> {PresentationTranslator.direction_label('LONG')}")
print(f"CONFIDENT -> {PresentationTranslator.data_status_label('CONFIDENT')}")

# Score engine end-to-end
from src.domain import FeatureSnapshot, FeatureValue, State
feats = {}
for k, v in {"volume_z": 5.0, "trade_count_z": 4.0, "oi_change_5m": 0.05,
             "cvd_slope_z": 3.5, "signed_delta": 50000, "acceptance": 0.9,
             "price_acceleration": 0.8, "oi_change_1m": 0.04,
             "price_efficiency": 0.7, "retrace_ratio": 0.2,
             "funding": 0.001, "funding_percentile": 30,
             "context_1m": 0.01, "context_5m": 0.02, "context_15m": 0.03, "context_1h": 0.04,
             "price_return_30s": 0.3, "price_return_5m": 0.05,
             "cvd_accel_z": 2.5, "CVD_slope": 0.001, "oi_change_30s": 0.01,
             "taker_delta_1m": 30000, "premium_percentile": 40}.items():
    feats[k] = FeatureValue(value=v, available=True, window="30s")
snap = FeatureSnapshot(symbol="ONGUSDT", asof=0, features=feats)

eng = ScoreEngine(cfg.scoring)
bd = eng.compute(snap, State.START_CONFIRMED, "LONG", 5, 0, 0, sample_count=20)
print(f"\nScore Engine end-to-end:")
print(f"  OpportunityScore = {bd.opportunity_score:.1f}")
print(f"  Base = {bd.base_score:.1f}, Risk penalty = {bd.risk_penalty:.1f}")
for k, ss in bd.subscores.items():
    print(f"  {ss.label}: {ss.score:.1f} {'[risk]' if ss.is_risk else ''}")

# Data Confidence + Signal Confirmation (V1.2 §3-4)
from src.domain import ConfidenceState
dc_eng = DataConfidenceEngine(cfg.scoring)
dc = dc_eng.compute(ConfidenceState.CONFIDENT, snap, sample_count=20)
print(f"\nDataConfidence = {dc.score:.1f}  coverage={dc.coverage:.2f}  missing={dc.missing}")

sc_eng = SignalConfirmationEngine(cfg.scoring)
sctx = ConfirmationContext(direction="LONG", evidence_count=5, veto_count=0,
                           breakout_acceptance=0.9)
sc = sc_eng.compute(snap, sctx, sample_count=20, data_confidence_score=dc.score)
print(f"SignalConfirmation = {sc.score:.1f}  core={sc.core_passed}/{sc.core_total} "
      f"support={sc.supporting_passed}/{sc.supporting_total} strong={sc.strong_confirm}")

# Ranking (V1.2 §26: opportunity × signal × data)
from src.presentation.ranking import compute_ranking_score
rs = compute_ranking_score(bd.opportunity_score, sc.score, dc.score)
print(f"RankingScore = {rs:.1f}")

print("\nAll V1.2 smoke tests passed!")
