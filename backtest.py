# ============================================================
# Steg 5: Backtesting
# Dual Momentum Trading System
# ============================================================

import logging

import numpy as np
import pandas as pd

from config import CASH_TICKER, DB_PATH, MARKET_TICKER
from portfolio import MAX_DAILY_LOSS, MAX_DRAWDOWN, STOP_LOSS, TOP_N, target_weights

log = logging.getLogger(__name__)

# Transaktionskostnader per affär (en riktning)
COMMISSION     = 0.001    # 0.1% courtage
SLIPPAGE       = 0.0005   # 0.05% slippage
COST_PER_TRADE = COMMISSION + SLIPPAGE

# Datadelning (kronologisk — testdatan är helig)
TRAIN_END      = "2017-12-31"
VALIDATION_END = "2020-12-31"


# ── SIMULERING ────────────────────────────────────────────────

def simulate(
    prices: pd.DataFrame,
    weights: pd.DataFrame,
    initial_capital: float = 100_000.0,
) -> tuple[pd.Series, pd.DataFrame]:
    """
    Simulerar portföljutveckling dag-för-dag baserat på månadsvis målvikter.

    Representerar varje position i antal aktier (shares) för att undvika
    att behöva justera dollar-belopp dagligen.

    Input:
      prices:           dagliga justerade stängningspriser (alla tickers + BIL)
      weights:          månadsvis målvikts-DataFrame från portfolio.target_weights(),
                        redan shiftad med .shift(1)
      initial_capital:  startkapital

    Output:
      portfolio_values: daglig Serie med portföljvärde
      trades:           DataFrame med alla affärer

    Simulerar:
      - Månadsvis rebalansering med transaktionskostnader (0.15% per affär)
      - Daglig stop-loss (8% under inköpspris → sälj till BIL)
      - Daglig förlustgräns (3%) → allt till BIL resten av dagen
      - Drawdown-paus (>20%) → cash till nästa rebalansering
    """
    rebalance_dates = set(weights.index)
    shares: dict[str, float] = {}          # ticker → antal aktier
    entry_prices: dict[str, float] = {}    # ticker → inköpspris för stop-loss
    peak_value = initial_capital
    paused = False
    trades: list[dict] = []

    # Starta med allt i cash (BIL)
    if CASH_TICKER in prices.columns:
        shares[CASH_TICKER] = initial_capital / float(prices[CASH_TICKER].iloc[0])
    else:
        shares["_cash"] = initial_capital  # fallback: håll som skalär

    portfolio_values: dict[pd.Timestamp, float] = {}
    prev_value = initial_capital

    def current_value(date: pd.Timestamp) -> float:
        """Beräknar nuvarande portföljvärde baserat på dagens priser."""
        total = 0.0
        for t, sh in shares.items():
            if t == "_cash":
                total += sh
            elif t in prices.columns:
                total += sh * float(prices.loc[date, t])
        return total

    def liquidate_to_cash(date: pd.Timestamp, reason: str) -> None:
        """Likviderar alla positioner till BIL. Avdrar transaktionskostnader."""
        nonlocal shares, entry_prices
        total = current_value(date)
        cost = sum(
            abs(shares.get(t, 0.0) * float(prices.loc[date, t])) * COST_PER_TRADE
            for t in shares
            if t not in (CASH_TICKER, "_cash") and t in prices.columns
        )
        net = total - cost
        shares = {}
        entry_prices = {}
        if CASH_TICKER in prices.columns:
            shares[CASH_TICKER] = net / float(prices.loc[date, CASH_TICKER])
        else:
            shares["_cash"] = net
        log.debug("%s: Likviderat till cash (%s) — netto %.0f", date.date(), reason, net)

    for date in prices.index:
        val = current_value(date)

        # ── Daglig förlustgräns ──
        daily_ret = (val - prev_value) / prev_value if prev_value > 0 else 0.0
        halted_today = daily_ret < -MAX_DAILY_LOSS
        if halted_today:
            log.debug("Daglig förlust %.1f%% nådd %s", daily_ret * 100, date.date())
            liquidate_to_cash(date, "daglig förlustgräns")
            val = current_value(date)

        # ── Drawdown-kontroll ──
        peak_value = max(peak_value, val)
        dd = (val - peak_value) / peak_value if peak_value > 0 else 0.0
        if dd < -MAX_DRAWDOWN and not paused:
            log.warning("Max drawdown %.1f%% nådd %s — pausar", dd * 100, date.date())
            paused = True
            liquidate_to_cash(date, "max drawdown")
            val = current_value(date)

        # ── Stop-loss ──
        if not halted_today and not paused:
            for ticker in list(entry_prices):
                if ticker not in prices.columns:
                    continue
                curr_price = float(prices.loc[date, ticker])
                if curr_price < entry_prices[ticker] * (1.0 - STOP_LOSS):
                    amount = shares.pop(ticker, 0.0) * curr_price
                    cost = amount * COST_PER_TRADE
                    trades.append({"date": date, "ticker": ticker, "action": "STOP",
                                   "amount": amount, "cost": cost})
                    net = amount - cost
                    if CASH_TICKER in prices.columns:
                        shares[CASH_TICKER] = shares.get(CASH_TICKER, 0.0) + net / float(prices.loc[date, CASH_TICKER])
                    else:
                        shares["_cash"] = shares.get("_cash", 0.0) + net
                    entry_prices.pop(ticker, None)

        # ── Rebalansering ──
        if date in rebalance_dates and not halted_today:
            # OBS: drawdown-paus häver sig INTE automatiskt vid rebalansering.
            # I en riktig krasch vill man inte återengagera vid nästa månadsslut.
            # Pausen kvarstår tills datan visar att marknaden återhämtat sig
            # (hanteras i live.py med manuell återstart, eller via drawdown_exceeded()).
            if paused:
                portfolio_values[date] = current_value(date)
                prev_value = portfolio_values[date]
                continue
            val = current_value(date)
            target = weights.loc[date]
            target_dollars = {t: w * val for t, w in target.items() if w > 0}

            all_tickers = set(shares) | set(target_dollars)
            total_cost = 0.0

            for ticker in all_tickers:
                price = float(prices.loc[date, ticker]) if ticker in prices.columns else 1.0
                curr_dollars = shares.get(ticker, 0.0) * price
                want_dollars = target_dollars.get(ticker, 0.0)
                delta = want_dollars - curr_dollars

                if abs(delta) < 1.0:
                    continue

                cost = abs(delta) * COST_PER_TRADE
                total_cost += cost
                action = "KÖP" if delta > 0 else "SÄLJ"
                trades.append({"date": date, "ticker": ticker, "action": action,
                               "amount": abs(delta), "cost": cost})

                if want_dollars > 0:
                    shares[ticker] = want_dollars / price
                    if delta > 0:
                        # Köp eller ökning — viktat genomsnitt av gammalt och nytt inköpspris
                        old_dollars = curr_dollars
                        old_entry  = entry_prices.get(ticker, price)
                        total_new  = old_dollars + delta
                        if total_new > 0:
                            entry_prices[ticker] = (old_dollars * old_entry + delta * price) / total_new
                        else:
                            entry_prices[ticker] = price
                    # Partiell minskning: behåll ursprungligt entry-pris (stop-loss mäts från originalköp)
                else:
                    shares.pop(ticker, None)
                    entry_prices.pop(ticker, None)

            # Dra transaktionskostnader från BIL-positionen
            if CASH_TICKER in shares and CASH_TICKER in prices.columns:
                bil_price = float(prices.loc[date, CASH_TICKER])
                shares[CASH_TICKER] = max(0.0, shares.get(CASH_TICKER, 0.0) - total_cost / bil_price)

        val = current_value(date)
        portfolio_values[date] = val
        prev_value = val

    pv = pd.Series(portfolio_values, name="portfolio_value")
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["date", "ticker", "action", "amount", "cost"]
    )
    total_cost = float(trades_df["cost"].sum()) if not trades_df.empty else 0.0
    return pv, trades_df, total_cost


# ── NYCKELTAL ─────────────────────────────────────────────────

def performance_metrics(
    portfolio_values: pd.Series,
    benchmark_prices: pd.Series,
    label: str = "Strategi",
    risk_free_rate: float = 0.0,
) -> dict:
    """
    Beräknar nyckeltal för en portföljvärdeserie.

    Nyckeltal:
      total_return, cagr, annualized_vol, sharpe,
      max_drawdown, pct_positive_months, n_months

    Krav för att gå vidare till paper trading (se README):
      sharpe > 1.0, max_drawdown > -20%, pct_positive_months > 60%,
      sharpe > benchmark sharpe
    """
    daily_ret = portfolio_values.pct_change().dropna()
    years = len(daily_ret) / 252.0

    total_ret = (portfolio_values.iloc[-1] / portfolio_values.iloc[0]) - 1.0
    cagr = (1.0 + total_ret) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    ann_vol = daily_ret.std() * np.sqrt(252)

    # risk_free_rate skickas in av anroparen (typiskt BIL CAGR för perioden).
    # Kritiskt: utan detta överskattas Sharpe med 0.3–0.5 enheter
    # när räntor är 4–5% (2022–2024).
    sharpe = (cagr - risk_free_rate) / ann_vol if ann_vol > 0 else 0.0

    rolling_max = portfolio_values.cummax()
    max_dd = ((portfolio_values - rolling_max) / rolling_max).min()

    monthly_ret = portfolio_values.resample("ME").last().pct_change().dropna()
    pct_pos = float((monthly_ret > 0).mean())
    n_months = int(len(monthly_ret))

    # Benchmark (SPY buy-and-hold) — samma riskfria ränta för rättvis jämförelse
    bench_ret = (benchmark_prices.iloc[-1] / benchmark_prices.iloc[0]) - 1.0
    bench_cagr = (1.0 + bench_ret) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    bench_daily = benchmark_prices.pct_change().dropna()
    bench_vol = bench_daily.std() * np.sqrt(252)
    bench_sharpe = (bench_cagr - risk_free_rate) / bench_vol if bench_vol > 0 else 0.0

    return {
        "label":               label,
        "total_return":        total_ret,
        "cagr":                cagr,
        "annualized_vol":      ann_vol,
        "sharpe":              sharpe,
        "max_drawdown":        max_dd,
        "pct_positive_months": pct_pos,
        "n_months":            n_months,
        "bench_cagr":          bench_cagr,
        "bench_sharpe":        bench_sharpe,
        "total_cost":          0.0,   # fylls i av anroparen
        "cost_drag_ann":       0.0,   # fylls i av anroparen
    }


def print_metrics(m: dict) -> None:
    """Skriver ut nyckeltal på ett läsbart sätt med pass/fail mot README-kraven."""
    passes = (
        m["sharpe"] > 1.0
        and m["max_drawdown"] > -MAX_DRAWDOWN
        and m["pct_positive_months"] > 0.60
        and m["sharpe"] > m["bench_sharpe"]
    )
    status = "GODKAND" if passes else "EJ GODKAND"

    print(f"\n{'─'*46}")
    print(f"  {m['label']}")
    print(f"{'─'*46}")
    print(f"  Total avkastning:    {m['total_return']*100:>8.1f}%")
    print(f"  CAGR:                {m['cagr']*100:>8.1f}%")
    print(f"  Annualiserad vol:    {m['annualized_vol']*100:>8.1f}%")
    print(f"  Sharpe-ratio:        {m['sharpe']:>8.2f}  (krav > 1.0)")
    print(f"  Max drawdown:        {m['max_drawdown']*100:>8.1f}%  (krav > -20%)")
    print(f"  Positiva manader:    {m['pct_positive_months']*100:>8.1f}%  (krav > 60%)")
    print(f"  Antal manader:       {m['n_months']:>8d}")
    if m.get("total_cost", 0) > 0:
        print(f"  Transaktionskostn:  ${m['total_cost']:>8,.0f}  ({m['cost_drag_ann']*100:.2f}%/år)")
    print(f"  {'─'*40}")
    print(f"  SPY CAGR:            {m['bench_cagr']*100:>8.1f}%")
    print(f"  SPY Sharpe:          {m['bench_sharpe']:>8.2f}")
    print(f"  {'─'*40}")
    print(f"  Resultat: {status}")
    print(f"{'─'*46}\n")


# ── DATADELNING ───────────────────────────────────────────────

def split_data(
    prices: pd.DataFrame,
    weights: pd.DataFrame,
) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    """
    Delar data i tre kronologiska perioder.

    OBS: Testdatan (2021–) är helig — titta BARA EN GÅNG efter all
    parameterjustering är klar på tränings- och valideringsdata.

      train:      2010–2017  (parameterjustering och strategutveckling)
      validation: 2018–2020  (mellankalibrering, inga slutsatser)
      test:       2021–slutet (slutlig validering)
    """
    def _slice(df: pd.DataFrame, start: str, end: str | None) -> pd.DataFrame:
        idx = df.index
        mask = idx >= start
        if end:
            mask &= idx <= end
        return df.loc[mask]

    return {
        "train":      (_slice(prices, "2010-01-01", TRAIN_END),
                       _slice(weights, "2010-01-01", TRAIN_END)),
        "validation": (_slice(prices, "2018-01-01", VALIDATION_END),
                       _slice(weights, "2018-01-01", VALIDATION_END)),
        "test":       (_slice(prices, "2021-01-01", None),
                       _slice(weights, "2021-01-01", None)),
    }


# ── PARAMETER GRID ────────────────────────────────────────────

def parameter_grid(
    prices: pd.DataFrame,
    regime: pd.DataFrame,
    lookback_combos: list[tuple] | None = None,
    top_n_values: list[int] | None = None,
) -> pd.DataFrame:
    """
    Testar kombinationer av TOP_N och momentum-lookbacks på träningsdata (2010-2017).

    Returnerar DataFrame sorterad efter Sharpe — en rad per kombination.
    Kör BARA på träningsdata (fram till TRAIN_END). Testdata är helig.

    Args:
        prices:          dagliga justerade stängningspriser
        regime:          marknadsregim (redan shiftad med .shift(1))
        lookback_combos: lista av tuples, t.ex. [(3,6,12), (1,3,6)]
        top_n_values:    lista av heltal, t.ex. [3, 4, 5]
    """
    from momentum import rank_universe

    top_n_values    = top_n_values    or [3, 4, 5]
    lookback_combos = lookback_combos or [
        (3, 6, 12),   # standard (Antonacci)
        (1, 3, 6),    # kortsiktigare
        (6, 12, 24),  # långsiktigare
    ]

    train_prices = prices.loc["2010-01-01":TRAIN_END]
    bench_train  = prices[MARKET_TICKER].loc["2010-01-01":TRAIN_END]
    bil_period   = prices[CASH_TICKER].loc["2010-01-01":TRAIN_END].dropna()
    bil_yrs      = len(bil_period) / 252.0
    rfr = float(
        (bil_period.iloc[-1] / bil_period.iloc[0]) ** (1.0 / bil_yrs) - 1.0
    ) if bil_yrs > 0 else 0.0

    results = []
    for lookbacks in lookback_combos:
        try:
            ranks = rank_universe(prices, lookbacks=lookbacks).shift(1)
        except TypeError:
            log.warning("rank_universe stodjer inte lookbacks-parametern — hoppar over %s", lookbacks)
            continue

        for top_n in top_n_values:
            try:
                w       = target_weights(ranks, regime, top_n=top_n)
                w_train = w.loc[w.index <= TRAIN_END]
                if w_train.empty:
                    continue

                pv, _, total_cost = simulate(train_prices, w_train)
                bench = bench_train / bench_train.iloc[0] * pv.iloc[0]
                years = len(pv) / 252.0
                m     = performance_metrics(pv, bench, risk_free_rate=rfr)

                results.append({
                    "lookbacks":  str(lookbacks),
                    "top_n":      top_n,
                    "cagr_%":     round(m["cagr"] * 100, 1),
                    "sharpe":     round(m["sharpe"], 2),
                    "max_dd_%":   round(m["max_drawdown"] * 100, 1),
                    "pos_mon_%":  round(m["pct_positive_months"] * 100, 1),
                    "cost_%/år":  round(total_cost / 100_000 / years * 100, 2) if years > 0 else 0,
                })
            except Exception as e:
                log.warning("Grid-fel top_n=%d lookbacks=%s: %s", top_n, lookbacks, e)

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results).sort_values("sharpe", ascending=False).reset_index(drop=True)


# ── HUVUDPROGRAM ──────────────────────────────────────────────

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

    log.info("Laddar data...")
    conn = init_db()
    prices = load_prices(conn)
    vix    = load_vix(conn)
    conn.close()

    log.info("Beraknar signaler...")
    ranks   = rank_universe(prices).shift(1)
    regime  = market_regime(prices, vix).shift(1)
    weights = target_weights(ranks, regime, top_n=TOP_N)

    periods = split_data(prices, weights)
    benchmark_all = prices[MARKET_TICKER]

    # Riskfri ränta: annualiserad BIL-avkastning över hela perioden
    bil_full = prices[CASH_TICKER].dropna()
    bil_years = len(bil_full) / 252.0
    risk_free_rate = float(
        (bil_full.iloc[-1] / bil_full.iloc[0]) ** (1.0 / bil_years) - 1.0
    ) if bil_years > 0 else 0.0
    log.info("Riskfri ranta (BIL CAGR): %.2f%%", risk_free_rate * 100)

    for period_name, (p_prices, p_weights) in periods.items():
        if p_weights.empty or p_prices.empty:
            log.warning("Tom datamanged for period: %s", period_name)
            continue

        start_yr = p_prices.index[0].year
        end_yr   = p_prices.index[-1].year
        label    = f"{period_name.upper()} ({start_yr}-{end_yr})"
        log.info("Kör backtest: %s", label)

        pv, trades, total_cost = simulate(p_prices, p_weights)

        bench = benchmark_all.loc[p_prices.index[0]:p_prices.index[-1]]
        bench = bench / bench.iloc[0] * pv.iloc[0]

        # Periodspecifik riskfri ränta
        bil_period = prices[CASH_TICKER].loc[p_prices.index[0]:p_prices.index[-1]].dropna()
        bil_yrs = len(bil_period) / 252.0
        rfr = float((bil_period.iloc[-1] / bil_period.iloc[0]) ** (1.0 / bil_yrs) - 1.0) if bil_yrs > 0 else 0.0

        years = len(pv) / 252.0
        m = performance_metrics(pv, bench, label=label, risk_free_rate=rfr)
        m["total_cost"]    = total_cost
        m["cost_drag_ann"] = total_cost / 100_000.0 / years if years > 0 else 0.0
        print_metrics(m)
        log.info("Antal affarer: %d", len(trades))

    log.info("Backtest klar.")

    # ── PARAMETER GRID (enbart träningsdata) ──────────────────
    print("\n" + "═" * 70)
    print("  PARAMETER GRID — träningsdata (2010-2017)")
    print("  OBS: Optimera aldrig mot validerings- eller testdata")
    print("═" * 70)
    regime_shifted = market_regime(prices, vix).shift(1)
    grid = parameter_grid(prices, regime_shifted)
    if not grid.empty:
        print(grid.to_string(index=False))
    else:
        print("  Inga resultat (saknar lookbacks-stöd i momentum.py?)")
    print()
