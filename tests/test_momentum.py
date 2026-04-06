"""Tester för momentum-beräkningar i momentum.py."""
import pandas as pd
import numpy as np
import pytest

from momentum import monthly_prices, compute_returns, composite_momentum, rank_universe


class TestMonthlyPrices:
    def test_resamples_to_month_end(self, sample_prices):
        monthly = monthly_prices(sample_prices)
        # Alla datum bör vara månadsslut
        for ts in monthly.index:
            assert ts == ts + pd.offsets.MonthEnd(0), f"{ts} är inte månadsslut"

    def test_preserves_columns(self, sample_prices):
        monthly = monthly_prices(sample_prices)
        assert set(monthly.columns) == set(sample_prices.columns)

    def test_fewer_rows_than_daily(self, sample_prices):
        monthly = monthly_prices(sample_prices)
        assert len(monthly) < len(sample_prices)


class TestComputeReturns:
    def test_1_month_return(self):
        dates = pd.date_range("2022-01-31", periods=4, freq="ME")
        prices = pd.DataFrame({"A": [100.0, 110.0, 121.0, 133.1]}, index=dates)
        ret = compute_returns(prices, months=1)
        assert ret["A"].iloc[1] == pytest.approx(0.10)

    def test_nan_for_insufficient_history(self):
        dates = pd.date_range("2022-01-31", periods=3, freq="ME")
        prices = pd.DataFrame({"A": [100.0, 110.0, 121.0]}, index=dates)
        ret = compute_returns(prices, months=6)
        assert ret["A"].isna().all()

    def test_returns_same_shape(self, sample_prices):
        monthly = monthly_prices(sample_prices)
        ret = compute_returns(monthly, months=3)
        assert ret.shape == monthly.shape


class TestCompositeMomentum:
    def test_returns_dataframe(self, sample_prices):
        monthly = monthly_prices(sample_prices)
        scores = composite_momentum(monthly)
        assert isinstance(scores, pd.DataFrame)

    def test_custom_lookbacks(self, sample_prices):
        monthly = monthly_prices(sample_prices)
        scores_default = composite_momentum(monthly)
        scores_custom  = composite_momentum(monthly, lookbacks=(1, 2))
        # Olika lookbacks ger olika scores
        assert not scores_default.equals(scores_custom)

    def test_average_of_lookbacks(self):
        dates  = pd.date_range("2020-01-31", periods=15, freq="ME")
        prices = pd.DataFrame({"A": [100.0 * (1.01 ** i) for i in range(15)]}, index=dates)
        scores = composite_momentum(prices, lookbacks=(1, 2))
        r1 = compute_returns(prices, 1)
        r2 = compute_returns(prices, 2)
        expected = (r1 + r2) / 2
        pd.testing.assert_frame_equal(scores, expected)


class TestRankUniverse:
    def test_raises_without_bil(self, sample_prices):
        prices_no_bil = sample_prices.drop(columns=["BIL"])
        with pytest.raises(ValueError, match="BIL"):
            rank_universe(prices_no_bil)

    def test_returns_dataframe(self, sample_prices_long):
        ranks = rank_universe(sample_prices_long)
        assert isinstance(ranks, pd.DataFrame)

    def test_rank_starts_at_1(self, sample_prices_long):
        ranks = rank_universe(sample_prices_long)
        valid_rows = ranks.dropna(how="all")
        if not valid_rows.empty:
            min_rank = valid_rows.min(axis=1).dropna()
            assert (min_rank >= 1.0).all()

    def test_custom_lookbacks_accepted(self, sample_prices_long):
        ranks = rank_universe(sample_prices_long, lookbacks=(1, 3))
        assert isinstance(ranks, pd.DataFrame)

    def test_absolute_filter_excludes_underperformers(self, sample_prices_long):
        # Aktier som underpresterar BIL ska ha NaN i ranks
        ranks = rank_universe(sample_prices_long)
        # Minst några rader bör ha NaN (BIL-filter aktivt)
        nan_count = ranks.isna().sum().sum()
        assert nan_count > 0, "Absolut momentum-filter verkar inte fungera"
