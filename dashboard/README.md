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
