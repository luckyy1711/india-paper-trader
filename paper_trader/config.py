"""Central configuration. Keep all execution paper-only."""

INITIAL_CASH = 10_000.00
MAX_POSITIONS = 3
MAX_POSITION_WEIGHT = 0.25
MAX_TOTAL_EXPOSURE = 0.75
TRANSACTION_COST_RATE = 0.0020  # 0.20% per side, simplified estimate
SLIPPAGE_RATE = 0.0010          # 0.10% adverse slippage per side
MAX_DRAWDOWN = 0.10             # lock new buys after 10% peak-to-trough drawdown
MIN_CURRENT_SYMBOL_COVERAGE = 45
MIN_RESEARCH_SYMBOL_COVERAGE = 45  # Require at least 90% of the Nifty 50 universe.
MIN_RESEARCH_SYMBOLS_WITH_260_BARS = 45  # Report this for transparency.
MIN_RESEARCH_SYMBOLS_WITH_300_BARS = 45  # The 12-1 candidate needs about 274 bars plus margin.
MIN_RESEARCH_BENCHMARK_BARS = 1000  # Keep about 3 years for evaluation plus a trend warm-up.
HISTORY_PERIOD = "5y"
ACTIVE_STRATEGY = "nifty_regime_momentum_126"
STRATEGIES = {
    "trend_momentum_126": {"trend_window": 200, "momentum_window": 126, "filter": "trend"},
    "medium_momentum_63": {"trend_window": 100, "momentum_window": 63, "filter": "trend"},
    "ma_crossover_50_200": {"trend_window": 200, "momentum_window": 126, "filter": "crossover"},
    # Classic cross-sectional 12-1 momentum: skip the most recent month, then rank by risk-adjusted return.
    "relative_strength_12_1": {
        "trend_window": 200, "momentum_window": 252, "momentum_windows": [252],
        "skip_recent_days": 21, "volatility_window": 252, "filter": "trend",
    },
    # Multi-horizon momentum: require positive 6- and 12-month returns, skipping the latest month.
    "dual_momentum_6_12": {
        "trend_window": 200, "momentum_window": 252, "momentum_windows": [126, 252],
        "skip_recent_days": 21, "volatility_window": 252, "filter": "trend",
    },
    # Existing stock ranking plus an index trend regime filter; remains a research candidate only.
    "nifty_regime_momentum_126": {
        "trend_window": 200, "momentum_window": 126, "filter": "trend",
        "market_regime_filter": True, "market_regime_window": 200,
    },
    # Optional research/paper model: daily trend + index regime with multiple confirmations.
    # Existing active portfolios do not switch automatically.
    "confirmed_regime_momentum_126": {
        "trend_window": 200, "momentum_window": 126, "filter": "trend",
        "market_regime_filter": True, "market_regime_window": 200,
        "indicator_confirmation": True,
    },
}
