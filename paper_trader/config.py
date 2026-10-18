"""Central configuration. Keep all execution paper-only."""

INITIAL_CASH = 10_000.00
MAX_POSITIONS = 3
MAX_POSITION_WEIGHT = 0.25
MAX_TOTAL_EXPOSURE = 0.75
TRANSACTION_COST_RATE = 0.0020  # 0.20% per side, simplified estimate
SLIPPAGE_RATE = 0.0010          # 0.10% adverse slippage per side
MAX_DRAWDOWN = 0.10             # lock new buys after 10% peak-to-trough drawdown
MIN_CURRENT_SYMBOL_COVERAGE = 45
HISTORY_PERIOD = "5y"
ACTIVE_STRATEGY = "trend_momentum_126"
STRATEGIES = {
    "trend_momentum_126": {"trend_window": 200, "momentum_window": 126, "filter": "trend"},
    "medium_momentum_63": {"trend_window": 100, "momentum_window": 63, "filter": "trend"},
    "ma_crossover_50_200": {"trend_window": 200, "momentum_window": 126, "filter": "crossover"},
}
