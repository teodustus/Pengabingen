"""Tester för riskhanteringsfunktioner i portfolio.py."""
import pandas as pd
import numpy as np
import pytest

from portfolio import stop_loss_hit, daily_loss_exceeded, drawdown_exceeded, STOP_LOSS, MAX_DRAWDOWN


class TestStopLossHit:
    def test_not_triggered_at_entry(self):
        assert stop_loss_hit(100.0, 100.0) is False

    def test_not_triggered_just_above_threshold(self):
        # 7.9% fall → under 8% → ej triggas
        assert stop_loss_hit(100.0, 92.1) is False

    def test_triggered_at_threshold(self):
        # 8% fall: 100 * 0.92 = 92.0 → NOT triggered (strict <)
        # 8.01% fall: 91.99 → triggered
        assert stop_loss_hit(100.0, 91.99) is True
        assert stop_loss_hit(100.0, 92.0) is False  # exakt på gränsen — ej triggas

    def test_triggered_well_below_threshold(self):
        assert stop_loss_hit(100.0, 80.0) is True

    def test_price_above_entry_never_triggers(self):
        assert stop_loss_hit(100.0, 150.0) is False

    def test_custom_threshold(self):
        assert stop_loss_hit(100.0, 94.9, threshold=0.05) is True
        assert stop_loss_hit(100.0, 95.0, threshold=0.05) is False  # exakt på gränsen


class TestDailyLossExceeded:
    def test_no_loss_returns_false(self):
        pv = pd.Series([100.0, 101.0, 102.0])
        result = daily_loss_exceeded(pv)
        assert not result.any()

    def test_small_loss_not_exceeded(self):
        pv = pd.Series([100.0, 98.0])   # 2% fall — under 3%
        result = daily_loss_exceeded(pv)
        assert not result.iloc[-1]

    def test_large_loss_exceeded(self):
        pv = pd.Series([100.0, 96.0])   # 4% fall — över 3%
        result = daily_loss_exceeded(pv)
        assert result.iloc[-1]

    def test_returns_series(self):
        pv = pd.Series([100.0, 99.0, 101.0])
        result = daily_loss_exceeded(pv)
        assert isinstance(result, pd.Series)
        assert len(result) == len(pv)


class TestDrawdownExceeded:
    def test_no_drawdown(self):
        pv = pd.Series([100.0, 101.0, 102.0, 103.0])
        assert not drawdown_exceeded(pv).any()

    def test_small_drawdown_not_exceeded(self):
        pv = pd.Series([100.0, 110.0, 95.0])   # 13.6% DD — under 20%
        assert not drawdown_exceeded(pv).any()

    def test_large_drawdown_exceeded(self):
        pv = pd.Series([100.0, 120.0, 90.0])   # 25% DD — över 20%
        result = drawdown_exceeded(pv)
        assert result.iloc[-1]

    def test_exactly_at_threshold(self):
        # drawdown_exceeded använder strikt <, exakt -20% triggas ej
        pv_exact = pd.Series([100.0, 80.0])    # exakt -20% → ej triggas
        assert not drawdown_exceeded(pv_exact).iloc[-1]
        pv_over  = pd.Series([100.0, 79.9])    # -20.1% → triggas
        assert drawdown_exceeded(pv_over).iloc[-1]

    def test_recovery_after_drawdown(self):
        # Drawdown triggas men värdet återhämtar sig
        pv = pd.Series([100.0, 120.0, 80.0, 125.0])
        result = drawdown_exceeded(pv)
        # Drawdown triggas vid index 2 (80/120 = -33%)
        assert result.iloc[2]
        # Vid index 3 (125) är vi fortfarande i drawdown relativt peak (125 > 120)
        assert not result.iloc[3]
