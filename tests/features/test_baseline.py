"""Robust baseline 测试 — median/MAD/z-score 手算验证。"""

from __future__ import annotations

import statistics

from src.features.baseline import (
    BaselineResult,
    compute_baseline,
    compute_mad,
    compute_median,
    percentile,
    robust_z_score,
)


class TestComputeMedian:
    def test_odd_count(self):
        assert compute_median([1, 3, 5]) == 3

    def test_even_count(self):
        assert compute_median([1, 2, 3, 4]) == 2.5

    def test_empty(self):
        assert compute_median([]) == 0.0


class TestComputeMAD:
    def test_known_values(self):
        """MAD = median(|x - median(x)|)。

        数据 [1, 2, 3, 4, 5]，median=3
        偏差 [|1-3|, |2-3|, |3-3|, |4-3|, |5-3|] = [2, 1, 0, 1, 2]
        MAD = median([2, 1, 0, 1, 2]) = 1
        """
        mad = compute_mad([1, 2, 3, 4, 5])
        assert mad == 1

    def test_empty(self):
        assert compute_mad([]) == 0.0


class TestComputeBaseline:
    def test_known_values(self):
        """手算验证 baseline。

        数据 [1, 2, 3, 4, 5]
        median = 3, MAD = 1, robust_std = 1.4826
        """
        result = compute_baseline([1, 2, 3, 4, 5])
        assert result.median == 3
        assert result.mad == 1
        assert abs(result.robust_std - 1.4826) < 0.001
        assert result.sample_count == 5
        assert result.is_valid is True

    def test_empty(self):
        result = compute_baseline([])
        assert result.sample_count == 0
        assert result.is_valid is False

    def test_insufficient_samples(self):
        """样本 < 3 → is_valid=False。"""
        result = compute_baseline([1, 2])
        assert result.is_valid is False

    def test_zero_variance(self):
        """所有值相同 → robust_std=0 → is_valid=False。"""
        result = compute_baseline([5, 5, 5])
        assert result.robust_std == 0
        assert result.is_valid is False


class TestRobustZScore:
    def test_known_value(self):
        """z = (value - median) / robust_std。

        数据 [1,2,3,4,5]，median=3, robust_std=1.4826
        z(5) = (5-3)/1.4826 ≈ 1.349
        """
        baseline = compute_baseline([1, 2, 3, 4, 5])
        z = robust_z_score(5.0, baseline)
        assert z is not None
        assert abs(z - 1.349) < 0.01

    def test_insufficient_returns_none(self):
        baseline = BaselineResult(median=0, mad=0, robust_std=0, sample_count=2)
        assert robust_z_score(5.0, baseline) is None

    def test_zero_std_returns_none(self):
        baseline = BaselineResult(median=5, mad=0, robust_std=0, sample_count=5)
        assert robust_z_score(10.0, baseline) is None


class TestPercentile:
    def test_basic(self):
        """数据 [1,2,3,4,5]，值 3 在第 60 百分位。"""
        sorted_vals = [1, 2, 3, 4, 5]
        # 3 的百分位：<=3 的有 3 个，共 5 个 → 60%
        p = percentile(3.0, sorted_vals)
        assert p == 60.0

    def test_min_value(self):
        sorted_vals = [1, 2, 3, 4, 5]
        p = percentile(1.0, sorted_vals)
        assert p == 20.0  # 1 个 <= 1，5 个总共 → 20%

    def test_max_value(self):
        sorted_vals = [1, 2, 3, 4, 5]
        p = percentile(5.0, sorted_vals)
        assert p == 100.0

    def test_insufficient(self):
        assert percentile(5.0, [1, 2]) is None
