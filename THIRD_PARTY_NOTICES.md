# Open-source research references and attribution

This project uses the following open-source model ecosystem and documentation as references. The main bot remains an independent paper-trading implementation; it does not vendor an entire external trading bot.

## LightGBM

- Project: https://github.com/lightgbm-org/LightGBM
- License: MIT
- Use: the `lightgbm` Python package is installed as a dependency and used to train the research-only gradient-boosted-tree alpha model. No LightGBM source files are copied into this repository.

## Microsoft Qlib

- Project: https://github.com/microsoft/qlib
- License: MIT
- Use: its public LightGBM quantitative-research workflow is a design reference for training an ML model from historical features and evaluating its predictions. Qlib itself is not installed or vendored, and no Qlib source code is copied into this repository.

## Freqtrade / FreqAI

- Project: https://github.com/freqtrade/freqtrade
- Documentation: https://www.freqtrade.io/en/stable/freqai/
- License: GPL-3.0
- Use: documentation is a design reference for rolling model retraining, feature engineering, and treating a model as part of a repeatable research pipeline. No Freqtrade or FreqAI source code is copied or linked into the runtime.

## Important note

The LightGBM candidate is a research experiment, not a proven profitable model or financial advice. Results depend on data quality, current-universe survivorship bias, execution assumptions, and market regime. It cannot place real orders and is not allowed to replace the active strategy automatically.
