# Overnight synthetic Monte Carlo stress test

This local experiment generates synthetic daily OHLCV paths with market-correlated moves, changing volatility regimes, and stock-specific noise. It reuses the existing rule-strategy backtest engine and evaluates one synthetic trading year after a 260-session indicator warm-up.

**This is not a historical backtest or a forecast.** It does not download real market data, train the LightGBM model, modify the active portfolio, or place broker orders. Its conclusions apply only to the synthetic data generator.

## First, smoke-test it

From the repository root, with the project virtual environment active:

```bash
python -m paper_trader.synthetic_simulation --hours 0.02 --max-paths 2 --report /tmp/synthetic-smoke.json
```

This caps the test at about 72 seconds and two paths. Check that the command prints a saved-report message and that the report exists:

```bash
python -c "import json; print(json.load(open('/tmp/synthetic-smoke.json'))['progress'])"
```

## Overnight run

```bash
python -m paper_trader.synthetic_simulation --hours 10 --max-paths 10000 --checkpoint-every 10
```

The default output is `data/synthetic_simulation.json`. The program checkpoints every 10 paths, so if interrupted, the latest checkpoint remains. It starts a new run rather than resuming the old one. Use `--seed 20261020` to change the random path sequence.

Useful options:

- `--hours 8` or `--hours 12`: wall-clock limit, maximum 12 hours.
- `--strategy trend_momentum_126`: choose a supported rule strategy.
- `--initial-cash 25000`: report the chosen virtual starting capital.
- `--sessions 252`: about one trading year; this is the default.
- `--symbols 10`: number of synthetic stocks; keep it modest on a low-RAM machine.
- `--max-paths 10000`: upper limit, not a guarantee that all paths finish.
- `--report /path/to/report.json`: choose a different report file.

Start with the smoke test, then run overnight only while the laptop has good ventilation, is plugged in, and is configured not to sleep. Check CPU temperature and memory usage periodically. The process uses one synthetic path at a time, but the existing backtest engine can still be CPU-intensive. Stop with Ctrl+C if the machine gets too hot or unstable; the last periodic checkpoint should remain on disk.

## What the report means

It aggregates strategy return percentiles, the fraction of successful paths with positive returns, synthetic benchmark return percentiles, drawdown, Sharpe approximation, and trade counts. Failed paths are counted separately. The seed makes each path reproducible.

Do not use these numbers to claim the bot has an edge in Indian equities. Synthetic prices cannot reproduce all market mechanics, constituent changes, liquidity, corporate actions, gaps, circuits, taxes, or real execution slippage. Use historical out-of-sample tests and forward paper trading to assess real-market robustness.
