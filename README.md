# India Paper Trader

A free-first, scheduled **paper-trading experiment** for Indian equities. The first version analyzes a version-controlled Nifty 50 universe after the market closes, writes simulated trades and portfolio state to this private repository, and can later be moved to a Linux mini PC.

**Paper trading only.** There is no broker integration, no real-money order code, no leverage, and no broker credentials. Do not add broker secrets to this repository.

## What it does

- Starts with ₹10,000 in virtual cash.
- Uses end-of-day adjusted daily OHLC data retrieved through `yfinance` from Yahoo Finance.
- Uses the `trend_momentum_126` baseline: only consider stocks above their 200-session simple moving average and with positive 126-session momentum; rank eligible stocks by momentum divided by annualized volatility; target up to three positions.
- Simulates whole-share orders at the next available session's open, with a simplified 0.20% transaction cost and 0.10% adverse slippage per side.
- Caps intended position sizing at 25% of equity per stock and total equity exposure at 75%.
- Locks new buys and queues an exit-to-cash target after a 10% peak-to-trough drawdown. The lock remains set for safety; resetting it should require an explicit review.
- Avoids duplicate daily signals, keeps pending orders in state, rejects missing/stale current-session data, and refuses to value a holding without a price.
- Compares three candidate strategies on a recent walk-forward historical sample in `data/research.json`. Research never changes the active strategy. A candidate must be evaluated in a separate paper-trading trial before any promotion.
- Saves portfolio state, trade history, and run logs in `data/` using Git commits so they survive ephemeral GitHub Actions runners.

## Schedule

`.github/workflows/paper-trader.yml` schedules one run at 17:15 India Standard Time, Monday through Friday. This is after market close, but GitHub may delay scheduled runs. The bot requires a benchmark candle dated today; if no current-session data is available (market holiday, provider delay, etc.), it skips trading safely. It does not maintain a process between runs.

A scheduled run creates a target after the close; virtual fills are simulated at the next available session's open. The first successful run creates a pending target rather than instantly filling at a close that was used to calculate the signal.

## Data-source limits

Yahoo Finance through `yfinance` is an unofficial interface intended for personal research and education. Data can be delayed, incomplete, adjusted, rate-limited, changed, or unavailable. This is not exchange-grade real-time data and this project must not be used to place real orders. Read the `yfinance` disclaimer and Yahoo's terms before relying on the feed: https://github.com/ranaroussi/yfinance

The constituent list is a maintained snapshot and must be updated after Nifty 50 reviews. Historical tests use the current constituents, creating **survivorship bias**. The Nifty 50 benchmark series is a price index, not a total-return index. Backtest metrics are research estimates, not forecasts or evidence of future profits.

## Cloud execution and cost controls

The workflow runs on GitHub-hosted Linux runners. GitHub Free currently includes 2,000 Actions minutes per month for private repositories; usage beyond included quotas may be billed if paid usage is enabled. Review GitHub's billing/Actions settings and ensure paid usage is disabled or capped at ₹0 before relying on scheduled runs: https://docs.github.com/en/billing/concepts/product-billing/github-actions

GitHub Actions is a scheduler, not a 24/7 server. Runs can be delayed, fail, or be skipped. The bot should not be considered unattended financial infrastructure.

## Project files

- `paper_trader/core.py` — deterministic strategy, portfolio sizing, fills, valuation, and risk lock
- `paper_trader/market_data.py` — Yahoo Finance data adapter
- `paper_trader/research.py` — historical strategy comparison (informational only)
- `paper_trader/main.py` — one scheduled run
- `tests/` — offline unit tests
- `data/state.json` — virtual cash, holdings, pending targets, and risk lock
- `data/trades.csv` — simulated fills
- `data/runs.jsonl` — execution and skip logs
- `data/research.json` — recent strategy-comparison results
- `.github/workflows/paper-trader.yml` — scheduled execution and persistence

## Local checks only (does not start the bot)

```bash
python -m unittest discover -s tests -v
```

### Interactive dashboard\n\nA dark, responsive dashboard with strategy-return graphs, a TradingView Nifty chart, portfolio metrics, research coverage warnings, and the trade ledger is in `dashboard/`. From the repository root, run `python -m http.server 8000`, then open `http://localhost:8000/dashboard/`. It refreshes saved data every 60 seconds. See `dashboard/README.md`. This is read-only and requires internet access for chart libraries. Keep the repository private: the dashboard reads the private simulation data and should not be published to a public host without deliberately reviewing what would be exposed.\n\nTo inspect the automated run, open the repository's **Actions** tab.

## Future mini-PC migration

The core engine is independent of GitHub Actions. A future Linux mini PC can run the same package from a persistent local folder with a system scheduler. Migrate the state and trade ledger together; do not start the cloud and mini-PC schedulers at the same time, or they could process the same signals twice.
