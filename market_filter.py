# ============================================================
# Steg 3: Marknadsfilter
# Dual Momentum Trading System
# ============================================================

import logging

import pandas as pd

from config import MARKET_TICKER, VIX_TICKER

log = logging.getLogger(__name__)

# Parametrar enligt README
SMA_WINDOW   = 200   # Handelsdagar för SPY glidande medelvärde
VIX_THRESHOLD = 30   # VIX > detta → halvera positionsstorlek


def spy_trend(prices: pd.DataFrame, window: int = SMA_WINDOW) -> pd.Series:
    """
    Beräknar om SPY handlas över sitt N-dagars glidande medelvärde.

    Returnerar en boolesk daglig Serie:
      True  = SPY > SMA(N)  →  marknaden i upptrend
      False = SPY <= SMA(N) →  marknaden i nedtrend → gå till cash
    """
    if MARKET_TICKER not in prices.columns:
        raise ValueError(f"{MARKET_TICKER} saknas i prices")

    spy = prices[MARKET_TICKER]
    sma = spy.rolling(window=window, min_periods=window).mean()
    trend = spy > sma
    trend.name = "spy_trend"
    return trend


def vix_multiplier(vix: pd.Series, threshold: int = VIX_THRESHOLD) -> pd.Series:
    """
    Beräknar positionsstorleksmultiplikator baserat på VIX-nivå.

      VIX <= threshold → 1.0  (normal positionsstorlek)
      VIX >  threshold → 0.5  (halverad positionsstorlek)

    Returnerar daglig Serie med float-värden 0.5 eller 1.0.
    """
    multiplier = pd.Series(1.0, index=vix.index, name="vix_multiplier")
    multiplier[vix > threshold] = 0.5
    return multiplier


def market_regime(prices: pd.DataFrame, vix: pd.Series) -> pd.DataFrame:
    """
    Huvud-API. Kombinerar SPY-trend och VIX-multiplikator till ett dagligt regime-läge.

    Returnerar DataFrame med dagligt index och kolumnerna:
      - spy_trend:       bool  — True = upptrend, False = gå till cash
      - vix_multiplier:  float — 1.0 (normal) eller 0.5 (reducerad)
      - in_market:       bool  — alias för spy_trend (läsbarhet)

    OBS: Returnerar osshifted data. Backtest-modulen (backtest.py) gör .shift(1)
    för att undvika look-ahead bias, precis som för momentum-rankingen.
    """
    trend = spy_trend(prices)
    mult  = vix_multiplier(vix)

    regime = pd.DataFrame({
        "spy_trend":      trend,
        "vix_multiplier": mult,
    })
    regime["in_market"] = regime["spy_trend"]

    # Logga täckning
    pct_in = regime["in_market"].mean() * 100
    log.info(
        "Marknadsregim: %.0f%% av dagarna i upptrend (SPY > %d-dagars SMA)",
        pct_in, SMA_WINDOW,
    )
    high_vix_days = (regime["vix_multiplier"] < 1.0).sum()
    log.info("VIX > %d: %d dagar (halverad positionsstorlek)", VIX_THRESHOLD, high_vix_days)

    return regime


if __name__ == "__main__":
    import logging as _logging
    from data_pipeline import init_db, load_prices, load_vix

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    conn = init_db()
    prices = load_prices(conn)
    vix    = load_vix(conn)
    conn.close()

    regime = market_regime(prices, vix)

    print(f"\nMarknadsregim — senaste 5 dagarna:")
    print(regime.tail(5).to_string())

    current = regime.iloc[-1]
    status = "UPPTREND (håll aktier)" if current["in_market"] else "NEDTREND (håll cash)"
    mult   = current["vix_multiplier"]
    print(f"\nAktuellt läge ({regime.index[-1].date()}): {status}")
    print(f"Positionsmultiplikator: {mult:.1f}x")
