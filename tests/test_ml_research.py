import unittest
from datetime import date, timedelta

from paper_trader.ml_research import (
    FEATURE_WARMUP_SESSIONS,
    HORIZON_SESSIONS,
    _eligible_training_rows,
    _prepare_research_rows,
    backtest_lightgbm_research,
)


def make_bars(count, symbol_index=0):
    start = date(2021, 1, 1)
    rows = []
    for index in range(count):
        # Deterministic movement with enough variation to exercise volatility features.
        close = 100.0 + symbol_index * 3.0 + index * (0.04 + symbol_index * 0.002) + (index % 7) * 0.15
        rows.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close * (1.0 - 0.002),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 100_000.0 + index * 100.0 + symbol_index,
        })
    return rows


class OpenSourceModelResearchTests(unittest.TestCase):
    def test_feature_and_label_rows_are_built_from_five_year_style_daily_bars(self):
        benchmark = make_bars(430, 0)
        stocks = {f"S{index:02d}": make_bars(430, index + 1) for index in range(5)}
        dates, features, labelled, _benchmark_by_date, _benchmark_closes = _prepare_research_rows({
            "__BENCHMARK__": benchmark,
            **stocks,
        })

        self.assertEqual(len(dates), 430)
        self.assertGreater(len(labelled), 0)
        self.assertIn(FEATURE_WARMUP_SESSIONS + 1, features)
        self.assertEqual(len(features[FEATURE_WARMUP_SESSIONS + 1][0][1]), 20)
        self.assertTrue(all(len(sample[3]) == 20 for sample in labelled))
        self.assertTrue(all(sample[1] == sample[0] + HORIZON_SESSIONS for sample in labelled))

    def test_training_filter_purges_labels_that_have_not_matured(self):
        samples = [
            (100, 121, "AAA", (0.0,) * 20, 0.01),
            (101, 122, "BBB", (0.0,) * 20, -0.01),
            (105, 126, "CCC", (0.0,) * 20, 0.02),
        ]
        eligible = _eligible_training_rows(samples, signal_index=122)
        self.assertEqual([sample[0] for sample in eligible], [100, 101])
        self.assertTrue(all(sample[1] <= 122 for sample in eligible))

    def test_features_at_a_signal_date_do_not_change_when_future_prices_change(self):
        benchmark = make_bars(430, 0)
        stock = make_bars(430, 2)
        inputs = {"__BENCHMARK__": benchmark, "AAA": stock}
        dates, features_before, _labels_before, _map, _closes = _prepare_research_rows(inputs)
        cutoff = 320
        mutated_benchmark = [dict(row) for row in benchmark]
        mutated_stock = [dict(row) for row in stock]
        for rows in (mutated_benchmark, mutated_stock):
            for index in range(cutoff + 1, len(rows)):
                rows[index]["close"] *= 1.8
                rows[index]["open"] *= 1.8
                rows[index]["high"] *= 1.8
                rows[index]["low"] *= 1.8
        _dates, features_after, _labels_after, _map_after, _closes_after = _prepare_research_rows({
            "__BENCHMARK__": mutated_benchmark,
            "AAA": mutated_stock,
        })
        self.assertEqual(features_before[cutoff][0][1], features_after[cutoff][0][1])

    def test_short_history_is_blocked_before_model_training(self):
        report = backtest_lightgbm_research({"__BENCHMARK__": make_bars(350), "AAA": make_bars(350, 1)})
        self.assertEqual(report["status"], "insufficient_history")
        self.assertFalse(report["active_strategy"])
        self.assertFalse(report["auto_promoted"])
        self.assertNotIn("total_return_pct", report)


if __name__ == "__main__":
    unittest.main()
