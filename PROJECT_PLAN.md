# India Paper Trader

## Project Goal
Build a cloud-based paper-trading bot for Indian equities that starts with free infrastructure and can later migrate to a Linux mini PC.

## Initial Configuration
- Starting virtual cash: ₹10,000
- Market: NSE-listed Indian equities
- Trading mode: simulation only
- Initial schedule: one run after the market closes on weekdays
- Cloud execution: GitHub Actions
- Future execution: Linux mini PC
- User interface: mobile-friendly dashboard
- Real-money trading: disabled

## Core Requirements
- Use available market data and record its source and timestamp.
- Never trade using stale or missing data.
- Simulate buys, sells, fees and portfolio value.
- Persist cash, positions, orders and trade history.
- Prevent duplicate trades when jobs retry.
- Enforce risk limits independently of the strategy.
- Log every decision and execution result.
- Recover safely after interruptions.
- Test strategies on historical data before considering them for paper trading.
- Require predefined evaluation criteria before changing the active strategy.

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
4. Test strategies against historical data.
5. Add scheduled GitHub Actions execution.
6. Verify persistent state and recovery.
7. Build a mobile-friendly dashboard.
8. Document migration to a Linux mini PC.

## Hosting Constraints
- Prefer free services.
- Avoid paid usage and unexpected billing.
- Treat GitHub Actions as scheduled execution, not a permanent server.
- Design the application to run independently of its hosting environment.

## Important Limitations
Free market data may be delayed, incomplete or unavailable. Scheduled cloud jobs may be delayed or fail. The bot must record these conditions and avoid unsafe trades.
