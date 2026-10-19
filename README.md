# India Paper Trader

A free-first, scheduled **paper-trading experiment** for Indian equities. The first version analyzes a version-controlled Nifty 50 universe after the market closes, writes simulated trades and portfolio state to this private repository, and can later be moved to a Linux mini PC.

**Paper trading only.** There is no broker integration, no real-money order code, no leverage, and no broker credentials. Do not add broker secrets to this repository.

## Browser-only GitHub deployment (no localhost required)

All cloud workflows are in this repository. To enable the automated schedules,
merge the `feature/open-source-quant-models` branch into the repository's **default
branch (`main`)** using its pull request. GitHub Actions cron triggers run from
the default branch only, and may be delayed or skipped.

1. Open **Settings → Actions → General** and allow GitHub Actions to run. Under
   Workflow permissions choose **Read and write permissions** so virtual
   portfolio state can be committed back to the repository.
2. Open **Settings → Pages → Build and deployment → Source: GitHub Actions**.
   If Pages is unavailable on your repository plan/visibility, hosting the
   dashboard there is not possible until your GitHub Pages eligibility changes.
3. Open **Actions → Crypto Paper Trader (Cloud) → Run workflow** and choose
   `start_btc_now` (or wait for the next scheduled hourly run). The first
   run explicitly starts a **5% / $50 virtual BTC** position using the latest
   confirmed hourly market close, if a data provider is reachable. It cannot
   place a real crypto order. If a crypto portfolio already exists, use
   `paper_once` instead. Hourly runs automatically bootstrap once if no
   crypto state exists.
4. Open **Actions → Publish Paper Dashboards (GitHub Pages)** to verify
   deployment. The expected URL, *only after successful deployment*, is
   `https://luckyy1711.github.io/india-paper-trader/dashboard/`.
   The sidebar links to both stock and crypto terminals.

The existing **India Paper Trader** workflow continues to run on weekday
sessions and now saves `data/paper_chart.json` for the stock terminal.
The crypto workflow runs hourly at approximately minute 13 UTC and saves
`data/crypto_state.json`, `data/crypto_trades.csv`, and
`data/crypto_chart.json`. A commit triggers a Pages refresh. GitHub Actions
is **not** a real-time server: schedules, market feeds, and deployment can be
delayed or fail. Both trading engines remain paper-only. The crypto model
uses Binance BTC/USDT or ETH/USDT as an approximate USD quote when available,
otherwise public Coinbase USD or Yahoo USD candles. The USD/USDT peg is not
guaranteed.

**Privacy:** The published Pages site is generally accessible publicly, even
if the source repository is private. It includes portfolio snapshots and
simulated trade history; never include passwords, API keys, personal details,
or other secrets in published files. Review GitHub Actions and Pages billing
and visibility before enabling. Do not publish private portfolio data without
understanding this tradeoff.

## What it does

- Starts with ₹10,000 in virtual cash.
- Uses end-of-day adjusted daily OHLC data retrieved through `yfinance` from Yahoo Finance.
- Defaults to `nifty_regime_momentum_126`: require the Nifty 50 benchmark and each stock to close above their 200-session moving averages, require positive 126-session stock momentum, then rank eligible stocks by momentum divided by annualized volatility; target up to three positions.
- Simulates whole-share orders at the next available session's open, with a simplified 0.20% transaction cost and 0.10% adverse slippage per side.
- Caps intended position sizing at 25% of equity per stock and total equity exposure at 75%.
- Locks new buys and queues an exit-to-cash target after a 10% peak-to-trough drawdown. The lock remains set for safety; resetting it should require an explicit review.
- Avoids duplicate daily signals, keeps pending orders in state, rejects missing/stale current-session data, and refuses to value a holding without a price.
- Compares six fixed rule-based strategies plus a separate open-source LightGBM alpha model in `data/research.json`. Research never changes the active strategy. A candidate must be evaluated in a separate paper-trading trial before any promotion.
- Saves portfolio state, trade history, and run logs in `data/` using Git commits so they survive ephemeral GitHub Actions runners.

## Schedule

`.github/workflows/paper-trader.yml` schedules one run at 17:15 India Standard Time, Monday through Friday. This is after market close, but GitHub may delay scheduled runs. The bot requires a benchmark candle dated today; if no current-session data is available (market holiday, provider delay, etc.), it skips trading safely. It does not maintain a process between runs.

A scheduled run creates a target after the close; virtual fills are simulated at the next available session's open. The first successful run creates a pending target rather than instantly filling at a close that was used to calculate the signal.

## Data-source limits

Yahoo Finance through `yfinance` is an unofficial interface intended for personal research and education. Data can be delayed, incomplete, adjusted, rate-limited, changed, or unavailable. This is not exchange-grade real-time data and this project must not be used to place real orders. Read the `yfinance` disclaimer and Yahoo's terms before relying on the feed: https://github.com/ranaroussi/yfinance

The constituent list is a maintained snapshot and must be updated after Nifty 50 reviews. Historical tests use the current constituents, creating **survivorship bias**. The Nifty 50 benchmark series is a price index, not a total-return index. Backtest metrics are research estimates, not forecasts or evidence of future profits.

## Research data-quality gate

Research backtest metrics are withheld unless at least 45 of the 50 stock histories have at least 300 daily bars and the Nifty benchmark has at least 1,000 daily bars. A failed check is recorded explicitly in `data/research.json` and makes the research workflow fail rather than presenting incomplete results as valid. Passing this gate does not remove survivorship bias, validate the external data provider, or make backtests predictive.

## Strategy research lab

The default paper-trading strategy is `nifty_regime_momentum_126`. Changes require explicit model selection; research never promotes a candidate automatically. Research compares six fixed rule sets:

- Trend + six-month momentum, volatility-adjusted (current baseline).
- Medium-term momentum and a 50/200-day moving-average filter.
- **12-1 relative strength**, excluding the most recent 21 sessions from its 12-month momentum signal. Academic background: [Jegadeesh & Titman (1993)](https://doi.org/10.1111/j.1540-6261.1993.tb04702.x).
- **Dual 6/12-month momentum**, requiring both horizons to be positive.
- **Nifty regime filter**, which only considers stock entries when the Nifty 50 closes above its 200-day average.
- The existing moving-average crossover candidate.

The rule-based report evaluates up to 756 recent sessions (roughly three years), compares results with Nifty 50 buy-and-hold, records calendar-year sub-periods from one continuous simulation, and includes configured costs/slippage. Each strategy is also checked over 252-, 504-, and 756-session windows and under 1.5x/2.0x cost-and-slippage stress. In addition, it runs four consecutive 126-session chronological test folds; the fixed strategy is not re-tuned between folds. Fold portfolios restart independently, so fold results are not one compounded equity curve. These checks help expose fragility but do not prove profitability. The period/cost sensitivity windows overlap; the separate walk-forward fold return intervals do not. A large grid of parameter tests is not a substitute for out-of-sample evidence: [research on backtest overfitting](https://escholarship.org/uc/item/4w1110bb) explains why the best historical variant can be a statistical mirage. The active strategy is not changed automatically. A candidate must also survive longer live-data paper trading and manual review before any promotion.

### Open-source ML research candidate

The strategy lab also evaluates a separate LightGBM gradient-boosted-tree regressor inspired by the open-source quantitative-research workflow in [Microsoft Qlib](https://github.com/microsoft/qlib) and the rolling feature/retraining approach documented by [FreqAI](https://www.freqtrade.io/en/stable/freqai/). It is not the Qlib or Freqtrade engine, and it does not contain a wholesale copy of either bot. It uses the upstream [LightGBM library](https://github.com/lightgbm-org/LightGBM) (MIT license) directly, keeping the runtime lighter than installing Qlib or a full crypto trading stack.

The model predicts each stock's next 21-session return relative to Nifty 50 from rolling price and optional OHLCV features. Signals are ranked monthly, model training is refreshed roughly quarterly, and training labels are purged until the full 21-session outcome has ended before each signal date. A 200-session Nifty regime filter and next-session-open fills are used. Transaction-cost/slippage sensitivity is checked at 1.0x, 1.5x, and 2.0x. It remains research-only and cannot change the active rule. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The dashboard also compares two drawdown recovery policies for the current active strategy: the existing permanent 10% lock and a **research-only** overlay that stays in cash until Nifty closes below its 200-session average after the lock and later closes back above it, with at least one eligible stock with a valid next-open fill. On re-entry, the research simulation resets its drawdown reference peak to the re-entry equity. This is a controlled experiment only: it does not change the scheduled paper trader, does not clear its existing lock, and does not automatically promote a strategy or authorize real-money trading.

## Cloud execution and cost controls

The workflow runs on GitHub-hosted Linux runners. GitHub Free currently includes 2,000 Actions minutes per month for private repositories; usage beyond included quotas may be billed if paid usage is enabled. Review GitHub's billing/Actions settings and ensure paid usage is disabled or capped at ₹0 before relying on scheduled runs: https://docs.github.com/en/billing/concepts/product-billing/github-actions

GitHub Actions is a scheduler, not a 24/7 server. Runs can be delayed, fail, or be skipped. The bot should not be considered unattended financial infrastructure.

## Project files

- `paper_trader/core.py` — deterministic strategy, portfolio sizing, fills, valuation, and risk lock
- `paper_trader/market_data.py` — Yahoo Finance data adapter
- `paper_trader/research.py` — historical strategy comparison (informational only)
- `paper_trader/ml_research.py` — expanding-window LightGBM research model with purged labels
- `THIRD_PARTY_NOTICES.md` — open-source references and licence notes
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

## Interactive dashboard

A dark, responsive dashboard with strategy-return graphs, a TradingView Nifty chart, portfolio metrics, research coverage warnings, and the trade ledger is in `dashboard/`. From the repository root, run `python -m http.server 8000`, then open `http://localhost:8000/dashboard/`. It refreshes saved data every 60 seconds. See `dashboard/README.md`. This is read-only and requires internet access for chart libraries. Keep the repository private: the dashboard reads the private simulation data and should not be published to a public host without deliberately reviewing what would be exposed.

To inspect the automated run, open the repository's **Actions** tab.

## On-demand controls and historical simulator

The **Run & Simulate** dashboard tab links to two authenticated GitHub Actions workflows:

- **India Paper Trader**: use `workflow_dispatch` with `paper_once` to request one simulated session, or `validate_only` to run tests without changing portfolio state. The existing weekday schedule remains enabled. The engine is paper-only and refuses to process a session when current data is missing or stale.
- **Historical Market Simulator**: choose a rule strategy or `lightgbm_relative_alpha`, starting virtual capital, evaluation window (252/504/756/1008 sessions), and cost/slippage multiplier (1x/1.5x/2x). It fetches historical bars, applies a coverage gate, replays the selected strategy chronologically, and saves `data/simulation.json`. The LightGBM option retrains at historical points and excludes labels until their full 21-session outcome has matured.

The historical simulator is isolated from `data/state.json` and `data/trades.csv`; it cannot change the active strategy or execute broker orders. When the workflow finishes, refresh the dashboard to read the saved result. The dashboard does not contain a GitHub token: controls open the GitHub Actions page, where you must be signed in and explicitly click **Run workflow**. Research results are historical estimates, not forecasts or guarantees.

## Future mini-PC migration

The core engine is independent of GitHub Actions. A future Linux mini PC can run the same package from a persistent local folder with a system scheduler. Migrate the state and trade ledger together; do not start the cloud and mini-PC schedulers at the same time, or they could process the same signals twice.

## Stock terminal: model switching, reasons, and comparisons

The stock terminal is at `dashboard/paper.html`. It is a **read-only snapshot of actual daily OHLC and completed virtual fills**, not a live broker chart. The stock dropdown displays 6 rule-based strategies. Selecting one shows its candidates and their trend average, momentum, volatility-adjusted ranking score, and sample rejection reasons. Different rules can select the same stocks. Recorded BUY/SELL markers only appear when a virtual order actually fills; model candidates are not filled trades.

Run from the project root with your virtual environment enabled:

```bash
python -m unittest discover -s tests -v
python -m paper_trader.paper_chart
python -m http.server 8080
```

Open `http://localhost:8080/dashboard/paper.html`. The exporter requires a saved paper portfolio and sufficiently current market history to calculate candidate signals. It **withholds** model selections when fewer than 45 Nifty 50 stock histories end on the benchmark's latest available date. The exporter does not advance the portfolio or place orders.

To switch the model that will create *future* signals:

```bash
python -m paper_trader.model_select --set medium_momentum_63
```

If an unfilled pending target exists, the command refuses to change state. An explicit alternate command is:

```bash
python -m paper_trader.model_select --set medium_momentum_63 --cancel-pending
```

**Warning:** `--cancel-pending` discards existing *unfilled* target stocks when actually changing strategy; it never clears holdings, cash, recorded fills, risk lock, or the last processed session date. Switching to the currently active model is a no-op and does not cancel targets. The first new signal follows the next eligible trading session after the already-processed date, with any fill following a later open; there is no same-session backfill. A switch made on a locally running laptop does **not** automatically update a separately running GitHub checkout; the authoritative strategy is stored in `data/state.json` on each running environment. Do not operate two independent paper portfolio copies as though they were a single shared account.

The model chosen for an existing saved portfolio and the last 100 model-switch audit events live **atomically in the portfolio's `data/state.json`**, which the stock cloud workflow already preserves. A pre-first-run choice can still live in the legacy `data/selected_strategy.json`. On Linux, the CLI and daily runner serialize modifications using a local file lock. Avoid editing state files by hand.

The comparison table displays **previously computed research backtests** from `data/research.json` (about 252 and 756 sessions), not current paper-account performance. It shows the source dates and withholds results if the existing research quality gate failed. The exporter does not retrain models or rerun expensive backtests. Historical returns and drawdowns are affected by data limitations, survivor bias, transaction-cost assumptions and the permanent paper risk lock; they cannot establish future profitability. LightGBM remains research-only.
