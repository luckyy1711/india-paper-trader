# India Paper Trader

## Project Goal
Build a cloud-based paper-trading bot for Indian equities that starts with free infrastructure and can later migrate to a Linux mini PC.

## Initial Configuration
- Starting virtual cash: ₹10,000
- Universe: Nifty 50 constituents (versioned symbol snapshot)
- Trading mode: simulation only
- Initial schedule: one run after the market closes on weekdays
- Cloud execution: GitHub Actions
- Future execution: Linux mini PC
- User interface: repository data files first; a private/mobile-friendly dashboard is a later phase
- Real-money trading: disabled

## Core Requirements
- Use available market data and record its source and market-session date.
- Never trade using stale or missing current-session data.
- Simulate whole-share buys and sells, estimated costs and slippage.
- Persist cash, positions, pending target orders and trade history.
- Prevent duplicate daily signals when a workflow retries.
- Enforce position/exposure limits and a portfolio drawdown lock independently of the strategy.
- Log decisions, skipped runs and errors.
- Recover safely after interruptions.
- Compare candidate strategies on historical data without promoting a strategy automatically.
- Require predefined evaluation criteria and a separate paper-trading trial before strategy promotion.

## Safety Rules
- Never place real stock orders.
- Never request broker credentials.
- Never represent simulated profits as real returns.
- Stop safely if data, portfolio state or risk checks fail.
- Keep secrets out of source code and logs.

## Development Stages
1. Define strategy and risk rules.
2. Build the paper-trading engine.
3. Test portfolio accounting and risk controls.
4. Compare candidate strategies against a recent historical sample.
5. Add scheduled GitHub Actions execution and persistence.
6. Verify recovery and no duplicate trades.
7. Build a private/mobile-friendly dashboard.
8. Document migration to a Linux mini PC.

## Hosting Constraints
- Prefer free services.
- Avoid paid usage and unexpected billing.
- Treat GitHub Actions as scheduled execution, not a permanent server.
- Design the application to run independently of its hosting environment.

## Important Limitations
Free market data may be delayed, incomplete or unavailable. Scheduled cloud jobs may be delayed or fail. Historical tests using current constituents can suffer survivorship bias. The bot must record these conditions and avoid unsafe trades.
