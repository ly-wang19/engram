from __future__ import annotations

from eval.bench import percentile
from eval.longmemeval import _percentile
from eval.report import _percentile as report_percentile


def test_eval_latency_percentile_helpers_share_round_nearest_rank():
    values = [1.0, 2.0, 3.0, 100.0]
    assert percentile(values, 50) == 3.0
    assert percentile(values, 95) == 100.0
    assert _percentile(values, 50) == percentile(values, 50)
    assert _percentile(values, 95) == percentile(values, 95)
    assert report_percentile(values, 50) == percentile(values, 50)
    assert report_percentile(values, 95) == percentile(values, 95)
