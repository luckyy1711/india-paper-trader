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
