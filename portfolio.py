# ============================================================
# Steg 4: Portföljlogik
# Dual Momentum Trading System
# ============================================================

import logging

import pandas as pd
import numpy as np

from config import CASH_TICKER

log = logging.getLogger(__name__)

# Parametrar enligt README
TOP_N           = 5      # Antal simultana positioner (3–5, finjusteras i backtesting)
MAX_POSITION    = 0.20   # Max 20% per position
STOP_LOSS       = 0.08   # Stop-loss 8% under inköpspris
MAX_DAILY_LOSS  = 0.03   # Max 3% daglig förlust av portföljvärde
MAX_DRAWDOWN    = 0.20   # Max 20% drawdown — pausa systemet


# ── MÅLVIKTER ─────────────────────────────────────────────────

def position_size(n_positions: int, vix_mult: float, max_pos: float = MAX_POSITION) -> float:
    """
    Beräknar viktning per position.

    Utan VIX-justering: lika viktade positioner, max MAX_POSITION vardera.
    Med VIX-justering: storleken skalas ned med vix_mult (0.5 eller 1.0).
    Resterande kapital parkeras i cash (BIL).
    """
    if n_positions == 0:
        return 0.0
    equal_weight = 1.0 / n_positions
    raw = min(equal_weight, max_pos)
    return raw * vix_mult


def target_weights(
    ranks: pd.DataFrame,
    regime: pd.DataFrame,
    top_n: int = TOP_N,
) -> pd.DataFrame:
    """
    Beräknar målvikter för varje månadsslut-rebalansering.

    Input:
      ranks:  DataFrame från momentum.rank_universe() — redan shiftad med .shift(1)
              (månadsslut-datum som index, tickers som kolumner, rank 1 = bäst, NaN = exkluderad)
      regime: DataFrame från market_filter.market_regime() — redan shiftad med .shift(1)
              (dagligt index, kolumnerna in_market och vix_multiplier)
      top_n:  antal positioner att hålla (default TOP_N)

    Output:
      DataFrame med månadsslut-datum som index, tickers (+ BIL) som kolumner.
      Värden = målvikter (0.0–1.0). Summa per rad = 1.0 (resterande → BIL).

    Logik:
      1. Resampla regime till månadsslut (sista kalenderdagen per månad)
      2. Om in_market = False → 100% BIL
      3. Välj top_n tickers med lägst rank (rank 1 = bäst)
      4. Vikta lika med hänsyn till vix_multiplier och max per position
      5. Resterande → BIL
    """
    # Resampla regime till månadsslut för att matcha ranks-index
    regime_monthly = regime.resample("ME").last()

    # Justera index så att de matchar varandra
    common_dates = ranks.index.intersection(regime_monthly.index)
    if common_dates.empty:
        raise ValueError("Ingen överlappning mellan ranks- och regime-index")

    ranks_m  = ranks.loc[common_dates]
    regime_m = regime_monthly.loc[common_dates]

    all_tickers = list(ranks_m.columns) + [CASH_TICKER]
    weights = pd.DataFrame(0.0, index=common_dates, columns=all_tickers)

    for date in common_dates:
        row_regime = regime_m.loc[date]
        in_market  = bool(row_regime["in_market"])
        vix_mult   = float(row_regime["vix_multiplier"])

        if not in_market or pd.isna(in_market):
            # Marknadsfilter triggar → 100% cash
            weights.loc[date, CASH_TICKER] = 1.0
            continue

        row_ranks = ranks_m.loc[date].dropna()
        if row_ranks.empty:
            weights.loc[date, CASH_TICKER] = 1.0
            continue

        # Välj top_n tickers (rank 1 = bäst)
        selected = row_ranks.nsmallest(top_n).index.tolist()
        n = len(selected)

        pos_size = position_size(n, vix_mult)
        for ticker in selected:
            weights.loc[date, ticker] = pos_size

        # Resterande kapital → BIL
        invested = pos_size * n
        weights.loc[date, CASH_TICKER] = max(0.0, 1.0 - invested)

    log.info(
        "Målvikter beräknade: %d rebalanseringsdatum, %d tickers",
        len(common_dates), len(all_tickers),
    )
    return weights


# ── RISKHANTERING (daglig) ────────────────────────────────────

def stop_loss_hit(entry_price: float, current_price: float, threshold: float = STOP_LOSS) -> bool:
    """
    Kontrollerar om en position har nått sin stop-loss.

    Returnerar True om current_price < entry_price * (1 - threshold).
    """
    return current_price < entry_price * (1.0 - threshold)


def daily_loss_exceeded(portfolio_values: pd.Series, threshold: float = MAX_DAILY_LOSS) -> pd.Series:
    """
    Beräknar om den dagliga förlusten överstiger tröskeln.

    Input:  daglig portföljvärdesserie
    Output: boolesk Serie — True = daglig förlust > threshold, stäng all handel den dagen

    Används av backtest.py för att simulera daglig förlustgräns.
    """
    daily_ret = portfolio_values.pct_change()
    return daily_ret < -threshold


def drawdown_exceeded(portfolio_values: pd.Series, threshold: float = MAX_DRAWDOWN) -> pd.Series:
    """
    Beräknar om max drawdown-gränsen är bruten.

    Input:  daglig portföljvärdesserie
    Output: boolesk Serie — True = nuvarande drawdown > threshold → pausa systemet

    Drawdown mäts från rullande all-time-high.
    """
    rolling_max = portfolio_values.cummax()
    drawdown = (portfolio_values - rolling_max) / rolling_max
    return drawdown < -threshold


if __name__ == "__main__":
    import logging as _logging
    from data_pipeline import init_db, load_prices, load_vix
    from momentum import rank_universe
    from market_filter import market_regime

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    conn = init_db()
    prices = load_prices(conn)
    vix    = load_vix(conn)
    conn.close()

    # Beräkna och shifta signaler (undvika look-ahead bias)
    ranks  = rank_universe(prices).shift(1)
    regime = market_regime(prices, vix).shift(1)

    weights = target_weights(ranks, regime)

    print(f"\nSenaste rebalansering ({weights.index[-1].date()}):")
    latest = weights.iloc[-1]
    held = latest[latest > 0].sort_values(ascending=False)
    for ticker, w in held.items():
        print(f"  {ticker:<8} {w*100:.1f}%")

    # Andel månader i marknaden
    in_market_months = (weights.drop(columns=[CASH_TICKER]).sum(axis=1) > 0).mean() * 100
    print(f"\nAndel månader med aktier (ej 100% cash): {in_market_months:.0f}%")
