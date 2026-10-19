# Dashboard

The dashboard is a lightweight, no-build web page. It reads the saved simulation state, trade ledger, and strategy report from `../data/`.

## Run locally

From the repository root:

```bash
python -m http.server 8000
```

Open **http://localhost:8000/dashboard/** in your browser. Stop the server with Ctrl+C.

The page refreshes data every 60 seconds. The **Compounding lab** tab is a scenario calculator for staged milestones and protected reserves; it is not a forecast or a backtest and does not change portfolio state. It also labels future asset-universe expansion as a research plan, not as currently enabled trading. It uses Chart.js and a TradingView chart from their hosted scripts/widgets, so an internet connection is required. Portfolio and strategy report files remain in this repository; do not publish them publicly unless you deliberately want those records exposed.

## Run and simulate

Open the **Run & Simulate** tab to request one paper-only session or configure a historical simulation through GitHub Actions. You must be signed in to GitHub and press **Run workflow**; the static dashboard never stores a token or executes jobs itself.

The historical simulator offers the six configured rule strategies and the research-only LightGBM model. Configure starting virtual capital, evaluation window, and cost/slippage stress. Its report is saved as `data/simulation.json` and loaded on dashboard refresh. Simulation runs use historical data and are isolated from the live paper portfolio files. The LightGBM path performs chronological model retraining with matured-label purging. Results show total return, benchmark comparison, drawdown, trade count and model diagnostics when available. A data-quality pass is only a coverage check and does not guarantee profitability.

The paper-trader workflow also has a `validate_only` mode that runs tests without changing the portfolio. The regular weekday schedule remains enabled.

## Important data warning

The page reads the latest `data/research.json` and displays data-coverage warnings when the downloaded universe or benchmark history is incomplete. Always check the report's exact date coverage, because a scheduled run can be blocked by provider delays or missing tickers. The TradingView chart is an external market widget and is not the data feed used by the bot. This dashboard is read-only and cannot place orders.

## Local cached minute chart

Run `python -m paper_trader.paper_chart`, then `python -m paper_trader.intraday_feed --port 8080` from the repository root and open `http://127.0.0.1:8080/dashboard/paper.html`. The 1-minute tab reads a localhost-only endpoint once per second, backed by at most one unofficial Yahoo fetch per symbol per minute. Candles and displayed timestamps are never fabricated. Yahoo data can be delayed/unavailable; the feed is **not** true NSE real time. Daily paper signal generation and next-open fills are unchanged. The static host can show only stored daily candles.

## BTC/ETH moving chart

The crypto page `/dashboard/crypto.html` can now load actual 1-minute Binance USDT (Coinbase USD fallback) market candles from the localhost-only preview server:

```bash
python -m paper_trader.intraday_feed --port 8080
```

Open `http://127.0.0.1:8080/dashboard/crypto.html`; select **1m public market preview** or **1h saved paper candles**. Client polling is once per second; upstream is at most once per 10 seconds per symbol, never a guaranteed real-time exchange subscription. Stale/failed data is labeled and previous actual candles can be shown without fabricating updates. The paper-trading bot remains an independent hourly process; virtual cash and fill history refresh from its saved snapshot every 15 seconds and are not modified by this chart.

## Minute crypto paper fills (new)

Unlike the preview-only 1-minute market graph, `paper_trader.crypto_minute --loop` now **advances the BTC/ETH virtual strategy on completed 1-minute candles**. Start it separately, once, on a Linux host after stopping the original hourly crypto runner. It checks for the next confirmed candle every 15 seconds; there is at most one portfolio decision per candle and there is no obligation to trade on every candle. Existing BTC/ETH cash, holdings and completed fills are retained; the legacy unfilled hourly target is cancelled at migration and a private backup is made.

The crypto screen's **Saved paper fills** timeframe adapts to 1 minute once the engine writes its first minute snapshot. Recorded virtual BUY/SELL markers appear on matching saved/preview minute candles, and the fill table updates every 15 seconds from the independent runner's saved file. This page does not submit trading orders, control the runner or claim that its public minute preview is an execution-grade feed.

Open `http://127.0.0.1:8081/dashboard/crypto.html` when the preview server is on port 8081. `python -m paper_trader.crypto_minute --status` displays the portfolio. A laptop in sleep or power-off cannot run minute trades.

## Crypto trading-performance account (paper money)

The BTC/ETH dashboard now provides a virtual account ledger above the chart:

- **Starting virtual money** from `state.initial_cash` (normally $1,000), remaining cash, market value currently invested, cost of the still-open positions, and total marked-to-market account equity.
- **Total net profit/loss** = saved cash + latest **completed bot candle** value of open positions − original virtual capital; it includes exchange-simulation slippage, fees and unrealized gains/losses, not merely wins on closed trades. This assumes there are no deposits or withdrawals (the current paper engine has neither).
- **Realized profit/loss** on completed virtual sells from FIFO journal matching (buy cost + buy fee, net sale proceeds − sale fee). Unrealized profit is the current invested market value less the remaining FIFO buy cost. If fill records are incomplete or cannot reconcile with holdings/cash, the realized/unrealized split is explicitly **unavailable** rather than guessed; overall cash/equity P&L is still shown.
- **Lifetime investment** is the cumulative net cash spent on BUYs (including purchase fees). It is not the same thing as the cost of positions held today or their latest market value. The dashboard also shows total cash received from SELLs, total recorded fees, and BUY/SELL counts.
- A **last recorded virtual BUY/SELL** indicator (including size, virtual price, fee and net cash effect), separate **last processed bot candle** BUY/SELL/HOLD status, and a scrollable fill ledger with per-SELL realized profits when verified.
- Profits and portfolio balances refresh on the dashboard every 15 seconds **after the minute runner writes a new paper snapshot**. The moving 1-minute price preview is separate and does not mark the balance to a forming/unconfirmed candle. All currency amounts are **USD virtual funds**, not real gains.

After pulling the dashboard and trading-engine changes, restart the locally running minute bot so its exporter includes the new `performance` section in `data/crypto_chart.json`. The price preview server can continue running and serve the updated HTML immediately; a hard browser refresh may help. The new accounting module reads saved data only; it never changes positions or triggers an order.

Use:

```bash
cd ~/india-paper-trader
source .venv/bin/activate
python -m unittest discover -s tests -p 'test_crypto_*.py' -v
python -m paper_trader.crypto_minute --loop
```

The runner and the chart server are separate terminals; the browser only observes what the runner has saved.


## Fresh $1,000 crypto paper-account reset + chart terminal

The latest BTC/ETH terminal uses the **official open-source TradingView Lightweight Charts™ v5.2.1** (Apache 2.0); it does **not** use TradingView's proprietary Advanced Charts website code. The chart loads the library from a public JS CDN, so the browser must have internet access. No package install, Node.js or build process is needed on this low-memory laptop.

The chart adds mouse-wheel/pinch **zoom**, click-drag panning, a visible crosshair with OHLC in IST, interactive price and time scales, candlestick/line/area views, EMA20/50 overlays, optional RSI(14) lower pane, real exchange **base-asset** volume when available, interval aggregation (1, 3, 5, 15, 30, 60 minutes), virtual BUY/SELL event markers, 2-point trend lines, horizontal price lines, 30m/1h/2h/all visible ranges, live follow, fit, dark/light, PNG screenshot and fullscreen. A chart interval selection **does not change the bot's trading clock**, which remains completed 1-minute candles. Displayed intervals beyond 1m derive only from the 180 real minute candles fetched by the feed; they are not an expanded provider history. Exchange preview candles may be forming and Binance USDT is only a proxy for USD. Saved paper fills come from the bot's own CSV and are real recorded simulated executions, not forecasts.

**Reset the local virtual paper account only if you want to discard the current active performance:**

1. In the running BTC/ETH bot terminal, press `Ctrl+C` to stop the **minute bot**. Check for other bot processes and stop those first. Keep the stock trading process separate and unchanged. The reset refuses to proceed if a minute trader is holding its lock.
2. Pull the updated files listed below. Run the offline crypto tests. Do not reset the account if tests fail.
3. Explicitly reset under the existing virtual-bot file lock:

```bash
python -m paper_trader.crypto_minute --reset-paper-account --confirm-reset
```

This archives the pre-reset `crypto_state.json`, `crypto_trades.csv`, and `crypto_chart.json` under a timestamped `data/crypto_reset_backups/` directory (local, ignored by Git); it clears all old active paper fills/positions/pending orders, resets USD cash and peak equity to **$1,000**, sets minute mode, and writes a **$0 P&L** dashboard snapshot immediately. Resetting does **not** change any stock portfolio files, fetch exchange data, or execute live orders. Backups are retained locally so you can inspect the previous experiment.

Then launch `python -m paper_trader.crypto_minute --loop` again. Restart the separate chart server with `python -m paper_trader.intraday_feed --port 8081` to load the new volume parser, and refresh `http://127.0.0.1:8081/dashboard/crypto.html` with Ctrl+Shift+R. The public market candlestick chart still displays genuine market history after an account reset, but all your **old virtual trade markers and P&L** disappear from the active account.

Because the account lives on your **laptop**, merging these GitHub commits does not itself reset your laptop or restart your locally running Python processes.

### Updating only the relevant files

```bash
cd ~/india-paper-trader
git fetch origin feature/open-source-quant-models
git restore --source=FETCH_HEAD -- \
  .gitignore \
  paper_trader/crypto_minute.py \
  paper_trader/crypto_preview.py \
  dashboard/crypto.html \
  dashboard/crypto-chart.js \
  tests/test_crypto_minute.py \
  tests/test_crypto_preview.py \
  tests/test_crypto_chart_ui.py
source .venv/bin/activate
python -m unittest discover -s tests -p 'test_crypto_*.py' -v
```

If you still have not pulled the earlier accounting upgrade, also restore `paper_trader/crypto_metrics.py`, `paper_trader/crypto_paper.py` and `tests/test_crypto_metrics.py` from that same branch.

The TradingView website's proprietary code, Pine Script, multi-chart platform, built-in order routing, and other restricted capabilities are **not** present. The graph offers an inspired layout using an authorized open-source core.
