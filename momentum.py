# ============================================================
# Steg 2: Momentumrankning
# Dual Momentum Trading System
# ============================================================

import logging

import pandas as pd

from config import UNIVERSE, CASH_TICKER

log = logging.getLogger(__name__)

# Lookback-fönster i månader
LOOKBACK_MONTHS = [3, 6, 12]


def monthly_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Resamplar dagliga priser till sista handelsdagen per månad."""
    return prices.resample("ME").last()


def compute_returns(prices_monthly: pd.DataFrame, months: int) -> pd.DataFrame:
    """
    Procentuell avkastning per ticker under senaste N månader.
    Input:  månadsslut-DataFrame (datum som index, tickers som kolumner).
    Output: DataFrame med samma struktur.
    Tickers med otillräcklig historik returnerar NaN för de perioderna.
    """
    return prices_monthly.pct_change(periods=months)


def composite_momentum(prices_monthly: pd.DataFrame) -> pd.DataFrame:
    """
    Sammansatt momentum-score: ovägt genomsnitt av 3-, 6- och 12-månaders avkastning.

    OBS: Antonaccis originalmetod — 3-månadersfönstret är implicit med i alla tre
    beräkningar. Det är avsiktligt och ger något mer vikt åt det senaste kvartalets rörelse.

    Returnerar DataFrame: månadsslut-datum som index, tickers som kolumner.
    """
    returns = [compute_returns(prices_monthly, m) for m in LOOKBACK_MONTHS]
    # Elementvis medelvärde — alla DataFrames har samma index och kolumner
    return sum(returns) / len(returns)


def rank_universe(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Huvud-API. Tar dagliga priser och returnerar en historisk ranknings-DataFrame.

    Output:
      - Index:   månadsslut-datum
      - Kolumner: tickers i UNIVERSE (ej CASH_TICKER/BIL)
      - Värden:  rank (1 = högst momentum), NaN = failade absolut momentum-filter

    Absolut momentum-filter: ticker exkluderas om dess composite-score
    understiger CASH_TICKER (BIL):s composite-score för samma period.
    BIL måste finnas i `prices`.

    OBS: Returnerar osshifted ranks. Backtest-modulen (backtest.py) ansvarar
    för att göra .shift(1) för att undvika look-ahead bias.
    """
    if CASH_TICKER not in prices.columns:
        raise ValueError(f"{CASH_TICKER} saknas i prices — krävs för absolut momentum-filter")

    monthly = monthly_prices(prices)
    scores = composite_momentum(monthly)

    # Absolut filter: exkludera tickers som underpresterar BIL
    bil_scores = scores[CASH_TICKER]
    universe_cols = [t for t in UNIVERSE if t in scores.columns]
    universe_scores = scores[universe_cols]

    # Sätt NaN för tickers som failar absolut filter
    passes_filter = universe_scores.gt(bil_scores, axis=0)
    filtered = universe_scores.where(passes_filter)

    # Rank: 1 = bäst (högst momentum), NaN för tickers utan tillräcklig data eller under BIL
    ranks = filtered.rank(axis=1, ascending=False, na_option="keep")

    missing = set(UNIVERSE) - set(universe_cols)
    if missing:
        log.warning("Saknar prisdata för: %s", sorted(missing))

    return ranks


if __name__ == "__main__":
    import sqlite3
    import logging as _logging
    from data_pipeline import init_db, load_prices

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    conn = init_db()
    prices = load_prices(conn)
    conn.close()

    ranks = rank_universe(prices)

    latest = ranks.iloc[-1].dropna().sort_values()
    print(f"\nMomentumrankning — {ranks.index[-1].date()}")
    print(f"{'Rank':<6} {'Ticker'}")
    print("-" * 20)
    for ticker, rank in latest.head(10).items():
        print(f"{int(rank):<6} {ticker}")

    n_passed = latest.notna().sum()
    n_total = len([t for t in UNIVERSE if t in ranks.columns])
    print(f"\n{n_passed}/{n_total} aktier passerade absolut momentum-filter")
