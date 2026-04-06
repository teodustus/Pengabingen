"""Delade fixtures för alla tester."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# Lägg till projektroten i sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Mocka yfinance innan några projektmoduler importeras
# (yfinance kan saknas i CI/testmiljö utan venv)
if "yfinance" not in sys.modules:
    sys.modules["yfinance"] = MagicMock()


@pytest.fixture
def live_db():
    """In-memory live_state.db med rätt schema."""
    from live import init_live_db
    conn = init_live_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def sample_prices():
    """
    Liten DataFrame med dagliga priser för 5 tickers + SPY + BIL.
    300 handelsdagar (~1.2 år), deterministisk med np.random.seed.
    """
    np.random.seed(42)
    dates = pd.date_range("2022-01-03", periods=300, freq="B")
    tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "SPY", "BIL"]
    data = {}
    for t in tickers:
        start = 100.0 if t != "BIL" else 91.0
        vol   = 0.015 if t != "BIL" else 0.0002
        drift = 0.0003 if t != "BIL" else 0.00015
        ret   = np.random.normal(drift, vol, len(dates))
        data[t] = start * np.cumprod(1 + ret)
    return pd.DataFrame(data, index=dates)


@pytest.fixture
def sample_prices_long():
    """
    Längre prisserie (1500 dagar, ~6 år) för att testa momentum-beräkningar
    som kräver minst 12 månaders historik.
    """
    np.random.seed(99)
    dates = pd.date_range("2018-01-02", periods=1500, freq="B")
    from config import UNIVERSE, CASH_TICKER, MARKET_TICKER
    tickers = UNIVERSE[:10] + [MARKET_TICKER, CASH_TICKER]
    data = {}
    for t in tickers:
        start = 100.0 if t != CASH_TICKER else 91.0
        vol   = 0.015 if t != CASH_TICKER else 0.0002
        drift = 0.0003 if t != CASH_TICKER else 0.00015
        ret   = np.random.normal(drift, vol, len(dates))
        data[t] = start * np.cumprod(1 + ret)
    return pd.DataFrame(data, index=dates)
