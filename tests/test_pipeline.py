"""Tester för data_pipeline.py — fokus på retry-logik."""
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
import pytest

from data_pipeline import fetch_ticker


def _make_df(ticker: str = "AAPL", rows: int = 5) -> pd.DataFrame:
    """Skapar en minimal giltig yfinance-liknande DataFrame."""
    dates = pd.date_range("2024-01-02", periods=rows, freq="B")
    return pd.DataFrame(
        {
            "Open":   [150.0] * rows,
            "High":   [155.0] * rows,
            "Low":    [148.0] * rows,
            "Close":  [152.0] * rows,
            "Volume": [1_000_000] * rows,
        },
        index=dates,
    )


class TestFetchTickerRetry:
    def test_returns_data_on_first_success(self):
        mock_df = _make_df()
        with patch("yfinance.download", return_value=mock_df):
            result = fetch_ticker("AAPL", start="2024-01-01")
        assert not result.empty
        assert "close" in result.columns

    def test_retries_on_empty_and_succeeds(self):
        """Första anropet returnerar tom DF, andra lyckas."""
        mock_df = _make_df()
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return pd.DataFrame() if call_count == 1 else mock_df

        with patch("yfinance.download", side_effect=side_effect):
            with patch("time.sleep"):   # skippa faktisk väntan
                result = fetch_ticker("AAPL", start="2024-01-01", retries=3)

        assert not result.empty
        assert call_count == 2

    def test_returns_empty_after_all_retries_fail(self):
        with patch("yfinance.download", return_value=pd.DataFrame()):
            with patch("time.sleep"):
                result = fetch_ticker("AAPL", start="2024-01-01", retries=3)
        assert result.empty

    def test_retries_on_exception(self):
        """Kastar exception på första anropet, lyckas på andra."""
        mock_df = _make_df()
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("nätverksfel")
            return mock_df

        with patch("yfinance.download", side_effect=side_effect):
            with patch("time.sleep"):
                result = fetch_ticker("AAPL", start="2024-01-01", retries=3)

        assert not result.empty
        assert call_count == 2

    def test_output_columns(self):
        mock_df = _make_df()
        with patch("yfinance.download", return_value=mock_df):
            result = fetch_ticker("AAPL", start="2024-01-01")
        expected_cols = {"ticker", "open", "high", "low", "close", "volume", "adj_close"}
        assert expected_cols.issubset(set(result.columns))

    def test_ticker_column_set_correctly(self):
        mock_df = _make_df()
        with patch("yfinance.download", return_value=mock_df):
            result = fetch_ticker("MSFT", start="2024-01-01")
        assert (result["ticker"] == "MSFT").all()
