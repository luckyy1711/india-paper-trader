# Open-source trading-system audit and benchmark plan

## Purpose

Borrow the best engineering ideas from established open-source trading projects without assuming that popularity, AI branding, or a large backtest return means profitability. The project remains paper-only.

## What the repository already has

The `feature/open-source-quant-models` baseline already includes:
- an Indian-equity paper-trading engine with deterministic fills and independent portfolio risk lock;
- six rule-based strategy candidates;
- a research-only LightGBM relative-alpha candidate;
- data-quality gates, cost/slippage sensitivity, and chronological walk-forward folds;
- offline unit tests and GitHub Actions test workflow;
- a synthetic simulation, which tests synthetic paths and is not evidence of live-market profitability.

Do not duplicate these components before inspecting them. Do not alter the active strategy based only on synthetic results.

## Upstream projects to study

| Project | Best thing to learn | Current decision |
|---|---|---|
| [Freqtrade](https://github.com/freqtrade/freqtrade) | Crypto strategy lifecycle, backtesting, dry-run and look-ahead checks | Study; do not wholesale copy |
| [Freqtrade strategies](https://github.com/freqtrade/freqtrade-strategies) | Candidate rules to independently reproduce and test | Inspect individual files and provenance before reuse |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | Event-driven simulation, fill models, portfolio/execution semantics | Architecture reference; likely heavier than current needs |
| [LEAN](https://github.com/QuantConnect/Lean) | Multi-asset portfolio abstractions and corporate-action handling | Architecture reference; don't migrate without evidence |
| [Microsoft Qlib](https://github.com/microsoft/qlib) | Experiment workflow, feature/model separation, signal and portfolio evaluation | Use as research-method reference; current LightGBM model is already Qlib-inspired |
| [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | Reinforcement-learning research methods | Defer until simpler baselines show a durable edge |
| [CCXT](https://github.com/ccxt/ccxt) | Unified crypto exchange API surface | Consider for crypto data/exchange adapter after venue and legal review |
| [LightGBM](https://github.com/lightgbm-org/LightGBM) | Lightweight gradient-boosted-tree model | Already used in research-only equity candidate |

See [open_source_research_manifest.json](../research/open_source_research_manifest.json) for the machine-readable project list, licences, intended use, and evaluation rules.

## Benchmark protocol

### Stage A — reproduce the current baseline

1. Run the existing offline test suite.
2. Run the existing historical research workflow on a fixed, recorded dataset snapshot.
3. Save the current active strategy, candidate results, data coverage, costs, and code revision before experiments.
4. Keep the active strategy unchanged.

### Stage B — compare ideas fairly

For every candidate, hold constant:
- symbols and dates;
- starting capital and portfolio constraints;
- next-bar execution convention;
- transaction costs, spreads and slippage assumptions;
- data-quality and stale-data handling;
- benchmark and metric definitions.

Report net return, excess return versus benchmark, maximum drawdown, trade count, turnover, exposure, Sharpe-like metric with its limitations, yearly/subperiod returns, and worst walk-forward fold. A strategy that only wins before costs or only in one fold should not be promoted.

### Stage C — out-of-sample safeguards

- Split data chronologically, never randomly across dates.
- Ensure training labels have fully matured before each test decision; purge overlapping outcomes.
- Use expanding/rolling walk-forward folds and retain an untouched final test interval.
- Run higher-cost/slippage scenarios and realistic fill assumptions.
- Record every strategy/parameter trial to expose selection bias.
- Do not tune on the final test interval.

### Stage D — extend markets separately

The current data adapter and backtest are for daily Indian equities. Do **not** claim the present model has been tested on crypto or forex.

1. Crypto: select an exchange with accessible historical OHLCV, document venue terms/rate limits, and add fees/spread/funding assumptions where applicable. Evaluate CCXT/Freqtrade-style interfaces.
2. Forex: select a permitted provider and instrument, then include bid/ask spread, rollover/financing and session calendars. Confirm lawful access and product availability for an India-based user before any live use.
3. Indian equities: improve point-in-time universe membership and corporate-action treatment; the current constituent snapshot creates survivorship bias. Verify data licensing before relying on third-party feeds.
4. Keep market-specific strategies and ledgers separate. Do not share capital or risk budgets until each has passed its own evaluation.

### Stage E — paper-forward test

A candidate must complete a separate paper-trading trial with real-time or appropriately delayed market data, no real orders, and the same costs/risk rules used in research. Promotion requires a written review of sample size, net performance, drawdown, data gaps, and backtest-to-forward-test differences. No automated strategy promotion.

## Licence and code-reuse policy

- Prefer reimplementing a clearly described idea from scratch when that is enough.
- Before copying source files, record exact upstream URL, commit/tag, licence, copyright notices, and any modifications.
- Keep required notices and comply with the source licence; GPL and LGPL obligations differ from permissive MIT/Apache licences.
- Do not copy a repository wholesale or merge entire frameworks just because they have many stars.
- Third-party strategies are hypotheses, not validated performance claims.

## Hardware and cost constraints

Keep first experiments compatible with a low-end Linux laptop (about 4 GB RAM): pandas/numpy and LightGBM-sized models first; avoid large RL runs, broad parameter grids, tick-level multi-market datasets, or a full multi-asset engine until the results justify their cost. GitHub Actions is scheduled CI, not a guaranteed always-on trading server; check billing limits before expanding workflows.

## Sources

- Freqtrade backtesting and strategy pitfalls: https://www.freqtrade.io/en/stable/backtesting/ and https://www.freqtrade.io/en/stable/strategy-101/
- FreqAI model training/backtesting: https://www.freqtrade.io/en/stable/freqai/
- NautilusTrader backtesting concepts: https://nautilustrader.io/docs/latest/concepts/backtesting/
- Qlib workflow: https://github.com/microsoft/qlib/blob/main/docs/component/workflow.rst
- FinRL: https://github.com/AI4Finance-Foundation/FinRL
- LEAN: https://github.com/QuantConnect/Lean
- CCXT: https://github.com/ccxt/ccxt
- Indian forex policy must be verified against current RBI/exchange rules before use; this document does not authorise live forex trading.
