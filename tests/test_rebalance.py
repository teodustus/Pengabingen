"""Tester för is_rebalance_day() — sista handelsdagen i månaden."""
import pandas as pd
import pytest

from live import is_rebalance_day


def _ts(date_str: str) -> pd.Timestamp:
    return pd.Timestamp(date_str)


class TestIsRebalanceDay:
    def test_last_business_day_of_april(self):
        # 30 april 2026 är en torsdag — sista handelsdagen i april
        assert is_rebalance_day(_ts("2026-04-30")) is True

    def test_not_last_day_of_april(self):
        # 29 april är inte sista
        assert is_rebalance_day(_ts("2026-04-29")) is False

    def test_first_day_of_month(self):
        assert is_rebalance_day(_ts("2026-04-01")) is False

    def test_last_business_day_december(self):
        # 31 dec 2025 är en onsdag
        assert is_rebalance_day(_ts("2025-12-31")) is True

    def test_month_end_on_weekend_uses_friday(self):
        # 31 maj 2025 är en lördag — sista HANDELSDAGEN är fredag 30 maj
        # Cron kör aldrig på lördagar (1-5 i crontab) så lördag testas ej
        assert is_rebalance_day(_ts("2025-05-30")) is True

    def test_new_years_day_pushes_december_end(self):
        # 1 jan är helgdag — om 31 dec är fredag är det ändå sista handelsdagen
        # 31 dec 2021 är en fredag, 3 jan 2022 är nästa handelsdag → annan månad
        assert is_rebalance_day(_ts("2021-12-31")) is True

    def test_mid_month_returns_false(self):
        for day in ["2026-01-15", "2026-06-10", "2026-09-01"]:
            assert is_rebalance_day(_ts(day)) is False, f"Misslyckades för {day}"

    def test_returns_bool(self):
        result = is_rebalance_day(_ts("2026-04-30"))
        assert isinstance(result, bool)
