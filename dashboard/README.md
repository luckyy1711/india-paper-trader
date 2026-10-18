# Dashboard

The dashboard is a lightweight, no-build web page. It reads the saved simulation state, trade ledger, and strategy report from `../data/`.

## Run locally

From the repository root:

```bash
python -m http.server 8000
```

Open **http://localhost:8000/dashboard/** in your browser. Stop the server with Ctrl+C.

The page refreshes data every 60 seconds. It uses Chart.js and a TradingView chart from their hosted scripts/widgets, so an internet connection is required. Portfolio and strategy report files remain in this repository; do not publish them publicly unless you deliberately want those records exposed.

## Important data warning

The page reads the latest `data/research.json` and displays data-coverage warnings when the downloaded universe or benchmark history is incomplete. Always check the report's exact date coverage, because a scheduled run can be blocked by provider delays or missing tickers. The TradingView chart is an external market widget and is not the data feed used by the bot. This dashboard is read-only and cannot place orders.
