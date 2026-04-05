# ============================================================
# Steg 6: Lokal Paper Trading-simulator
# Dual Momentum Trading System
#
# Simulerar ordrar lokalt med yfinance-priser — ingen mäklare krävs.
# All portföljdata lagras i live_state.db (SQLite).
#
# Valfria miljövariabler (för Telegram-notifieringar):
#   TELEGRAM_BOT_TOKEN    — Bot-token från @BotFather
#   TELEGRAM_CHAT_ID      — Chat-ID att skicka meddelanden till
#
# Kör dagligen via cron (se deploy/setup.sh):
#   10 21 * * 1-5 cd /opt/trading-bot && .venv/bin/python live.py >> logs/live.log 2>&1
# ============================================================

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from config import (
    CASH_TICKER,
    DB_PATH,
    MARKET_TICKER,
    PAPER_INITIAL_CAPITAL,
    TRANSACTION_COST,
    UNIVERSE,
)
from data_pipeline import init_db, load_prices, load_vix, update_universe
from market_filter import market_regime
from momentum import rank_universe
from portfolio import (
    MAX_DRAWDOWN,
    STOP_LOSS,
    TOP_N,
    drawdown_exceeded,
    target_weights,
)

log = logging.getLogger(__name__)

# ── KONFIGURATION ─────────────────────────────────────────────

TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

LIVE_DB_PATH = Path("live_state.db")


# ── TELEGRAM (valfritt) ──────────────────────────────────────

def send_telegram(message: str) -> None:
    """Skickar ett meddelande via Telegram. Loggar fel men kraschar inte."""
    if not TG_TOKEN or not TG_CHAT_ID:
        log.info("Telegram ej konfigurerat — hoppar över notifiering")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        log.error("Telegram-fel: %s", e)


# ── LIVE-DATABAS ──────────────────────────────────────────────

def init_live_db(path: Path = LIVE_DB_PATH) -> sqlite3.Connection:
    """Initierar SQLite-databas för paper trading-tillstånd."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            key   TEXT PRIMARY KEY,
            value REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS positions (
            ticker      TEXT PRIMARY KEY,
            qty         REAL NOT NULL,
            avg_price   REAL NOT NULL,
            entry_date  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS live_trades (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            date     TEXT NOT NULL,
            ticker   TEXT NOT NULL,
            action   TEXT NOT NULL,
            qty      REAL,
            price    REAL,
            amount   REAL,
            cost     REAL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS daily_pnl (
            date            TEXT PRIMARY KEY,
            portfolio_value REAL NOT NULL,
            daily_return    REAL
        );
    """)
    conn.commit()
    return conn


def _get_cash(conn: sqlite3.Connection) -> float:
    """Hämtar kontantsaldo. Initierar med PAPER_INITIAL_CAPITAL om det saknas."""
    row = conn.execute("SELECT value FROM account WHERE key = 'cash'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO account (key, value) VALUES ('cash', ?)",
            (PAPER_INITIAL_CAPITAL,),
        )
        conn.commit()
        log.info("Nytt paper trading-konto initierat med $%.0f", PAPER_INITIAL_CAPITAL)
        return PAPER_INITIAL_CAPITAL
    return row[0]


def _set_cash(conn: sqlite3.Connection, amount: float) -> None:
    conn.execute("UPDATE account SET value = ? WHERE key = 'cash'", (amount,))
    conn.commit()


def load_positions(conn: sqlite3.Connection) -> dict[str, dict]:
    """Returnerar {ticker: {qty, avg_price, entry_date}}."""
    rows = conn.execute("SELECT ticker, qty, avg_price, entry_date FROM positions").fetchall()
    return {r[0]: {"qty": r[1], "avg_price": r[2], "entry_date": r[3]} for r in rows}


def save_position(conn: sqlite3.Connection, ticker: str, qty: float, avg_price: float) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR REPLACE INTO positions (ticker, qty, avg_price, entry_date) VALUES (?, ?, ?, ?)",
        (ticker, qty, avg_price, today),
    )
    conn.commit()


def remove_position(conn: sqlite3.Connection, ticker: str) -> None:
    conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
    conn.commit()


def log_trade(conn: sqlite3.Connection, ticker: str, action: str,
              qty: float, price: float, amount: float, cost: float = 0.0) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO live_trades (date, ticker, action, qty, price, amount, cost) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (today, ticker, action, qty, price, amount, cost),
    )
    conn.commit()


def log_daily_pnl(conn: sqlite3.Connection, portfolio_value: float) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prev = conn.execute(
        "SELECT portfolio_value FROM daily_pnl ORDER BY date DESC LIMIT 1"
    ).fetchone()
    daily_ret = (portfolio_value / prev[0] - 1.0) if prev else None
    conn.execute(
        "INSERT OR REPLACE INTO daily_pnl (date, portfolio_value, daily_return) VALUES (?, ?, ?)",
        (today, portfolio_value, daily_ret),
    )
    conn.commit()


def portfolio_value(conn: sqlite3.Connection, current_prices: dict[str, float]) -> float:
    """Beräknar totalt portföljvärde: cash + marknadsvärde på alla positioner."""
    cash = _get_cash(conn)
    positions = load_positions(conn)
    market_val = sum(
        pos["qty"] * current_prices.get(ticker, pos["avg_price"])
        for ticker, pos in positions.items()
    )
    return cash + market_val


# ── SIMULERADE ORDRAR ────────────────────────────────────────

def sim_buy(conn: sqlite3.Connection, ticker: str, amount: float, price: float) -> float:
    """
    Simulerar köp. Returnerar antal köpta aktier.
    Drar av transaktionskostnad från beloppet.
    """
    cost = amount * TRANSACTION_COST
    net_amount = amount - cost
    qty = net_amount / price

    # Uppdatera kassa
    cash = _get_cash(conn)
    _set_cash(conn, cash - amount)

    # Uppdatera position (viktat genomsnitt om vi redan har)
    positions = load_positions(conn)
    if ticker in positions:
        old = positions[ticker]
        new_qty = old["qty"] + qty
        new_avg = (old["qty"] * old["avg_price"] + qty * price) / new_qty
        save_position(conn, ticker, new_qty, new_avg)
    else:
        save_position(conn, ticker, qty, price)

    log_trade(conn, ticker, "KOP", qty, price, amount, cost)
    return qty


def sim_sell(conn: sqlite3.Connection, ticker: str, qty: float, price: float) -> float:
    """
    Simulerar sälj. Returnerar nettobelopp (efter kostnad).
    """
    gross = qty * price
    cost = gross * TRANSACTION_COST
    net = gross - cost

    # Uppdatera kassa
    cash = _get_cash(conn)
    _set_cash(conn, cash + net)

    # Uppdatera eller ta bort position
    positions = load_positions(conn)
    if ticker in positions:
        old_qty = positions[ticker]["qty"]
        remaining = old_qty - qty
        if remaining < 0.001:  # Avrundning — stäng helt
            remove_position(conn, ticker)
        else:
            save_position(conn, ticker, remaining, positions[ticker]["avg_price"])

    log_trade(conn, ticker, "SALJ", qty, price, gross, cost)
    return net


def sim_close(conn: sqlite3.Connection, ticker: str, price: float, action: str = "SALJ") -> float:
    """Stänger hela positionen för en ticker."""
    positions = load_positions(conn)
    if ticker not in positions:
        return 0.0
    qty = positions[ticker]["qty"]
    gross = qty * price
    cost = gross * TRANSACTION_COST
    net = gross - cost

    cash = _get_cash(conn)
    _set_cash(conn, cash + net)
    remove_position(conn, ticker)
    log_trade(conn, ticker, action, qty, price, gross, cost)
    return net


# ── HJÄLPFUNKTIONER ───────────────────────────────────────────

def is_rebalance_day() -> bool:
    """Returnerar True om idag är sista handelsdagen i månaden."""
    today = pd.Timestamp.now(tz="America/New_York").normalize()
    try:
        from pandas.tseries.holiday import USFederalHolidayCalendar
        from pandas.tseries.offsets import CustomBusinessDay
        us_bd = CustomBusinessDay(calendar=USFederalHolidayCalendar())
        next_bd = today + us_bd
    except Exception:
        next_bd = today + pd.offsets.BDay(1)
    return today.month != next_bd.month


def get_latest_prices(prices: pd.DataFrame) -> dict[str, float]:
    """Hämtar senaste stängningskurs per ticker från databasdata."""
    latest = prices.iloc[-1]
    return {ticker: float(latest[ticker]) for ticker in prices.columns if pd.notna(latest[ticker])}


def compute_target(prices: pd.DataFrame, vix: pd.Series) -> pd.Series:
    """Beräknar målvikter för idag. Returnerar Serie: ticker -> vikt."""
    ranks  = rank_universe(prices).shift(1)
    regime = market_regime(prices, vix).shift(1)
    w = target_weights(ranks, regime, top_n=TOP_N)
    if w.empty:
        return pd.Series({CASH_TICKER: 1.0})
    return w.iloc[-1]


def format_pnl_message(
    pv: float,
    daily_ret: float | None,
    positions: dict[str, dict],
    current_prices: dict[str, float],
    rebalanced: bool,
    trade_summary: list[str],
    stop_triggered: list[str],
) -> str:
    """Formaterar daglig P&L-rapport."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"[PAPER] Trading Bot — {date_str}"]
    lines.append(f"Portfoljvarde: ${pv:,.0f}")

    if daily_ret is not None:
        sign = "+" if daily_ret >= 0 else ""
        lines.append(f"Daglig P&L: {sign}{daily_ret*100:.2f}%")

    if stop_triggered:
        lines.append(f"\nStop-loss: {', '.join(stop_triggered)}")

    if rebalanced:
        lines.append("\nRebalansering utford:")
        lines.extend(f"  {t}" for t in trade_summary)

    if positions:
        lines.append("\nPositioner:")
        for ticker, pos in sorted(positions.items()):
            price = current_prices.get(ticker, pos["avg_price"])
            pnl_pct = (price / pos["avg_price"] - 1.0) * 100
            lines.append(f"  {ticker:<8} {pos['qty']:.1f} st  {pnl_pct:+.1f}%")

    return "\n".join(lines)


# ── DAGLIG KÖRNING ────────────────────────────────────────────

def run_daily() -> None:
    """
    Huvudfunktion — körs dagligen via cron efter börsstängning.

    Flöde:
      1. Uppdatera marknadsdata (yfinance)
      2. Beräkna portföljvärde med senaste priser
      3. Kontrollera stop-losses
      4. Drawdown-kontroll
      5. Om rebalanseringsdag: beräkna ny portfölj och simulera affärer
      6. Logga P&L och skicka rapport
    """
    log.info("=== Startar daglig korning %s ===", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    # 1. Uppdatera marknadsdata
    data_conn = init_db()
    update_universe(data_conn)
    prices = load_prices(data_conn)
    vix    = load_vix(data_conn)
    data_conn.close()

    # Kontrollera VIX-ålder
    vix_age_days = (pd.Timestamp.now() - vix.index[-1]).days
    if vix_age_days > 3:
        msg = f"VIX-data ar {vix_age_days} dagar gammal"
        log.warning(msg)
        send_telegram(f"Varning: {msg}")

    # 2. Hämta senaste priser och beräkna portföljvärde
    live_conn = init_live_db()
    current_prices = get_latest_prices(prices)

    if not current_prices:
        msg = "Inga priser tillgangliga — avbryter"
        log.error(msg)
        send_telegram(msg)
        live_conn.close()
        return

    pv = portfolio_value(live_conn, current_prices)
    log_daily_pnl(live_conn, pv)

    # 3. Kontrollera stop-losses
    positions = load_positions(live_conn)
    stop_triggered: list[str] = []

    for ticker, pos in list(positions.items()):
        price = current_prices.get(ticker)
        if price is None:
            log.warning("Inget pris for %s — stop-loss ej kontrollerat", ticker)
            continue
        if price < pos["avg_price"] * (1.0 - STOP_LOSS):
            log.warning(
                "Stop-loss for %s (entry=%.2f, nu=%.2f, -%s%%)",
                ticker, pos["avg_price"], price, f"{STOP_LOSS*100:.0f}",
            )
            sim_close(live_conn, ticker, price, action="STOP")
            stop_triggered.append(ticker)

    # 4. Drawdown-kontroll
    pnl_rows = pd.read_sql(
        "SELECT date, portfolio_value FROM daily_pnl ORDER BY date", live_conn,
    )
    if not pnl_rows.empty:
        pnl_series = pnl_rows.set_index("date")["portfolio_value"]
        pnl_series.index = pd.to_datetime(pnl_series.index)
        if drawdown_exceeded(pnl_series).iloc[-1]:
            msg = (
                f"MAX DRAWDOWN NADD — systemet pausat.\n"
                f"Portfoljvarde: ${pv:,.0f}\n"
                f"Manuell omstart kravs."
            )
            log.warning(msg)
            send_telegram(msg)
            live_conn.close()
            return

    # 5. Rebalansering (om det är dags)
    rebalanced    = False
    trade_summary: list[str] = []

    if is_rebalance_day():
        log.info("Rebalanseringsdag — beraknar ny portfolj")
        target = compute_target(prices, vix)

        # Uppdatera portföljvärde efter eventuella stop-loss-sälj
        pv = portfolio_value(live_conn, current_prices)
        positions = load_positions(live_conn)
        current_holdings = {
            t: p["qty"] * current_prices.get(t, p["avg_price"])
            for t, p in positions.items()
        }

        # Sälj först (frigör kapital)
        for ticker in list(positions.keys()):
            desired_weight = target.get(ticker, 0.0)
            desired_value  = desired_weight * pv
            current_value  = current_holdings.get(ticker, 0.0)
            delta = desired_value - current_value

            if delta < -50:  # Sälj
                price = current_prices.get(ticker)
                if price is None:
                    continue
                sell_value = abs(delta)
                sell_qty   = min(sell_value / price, positions[ticker]["qty"])
                sim_sell(live_conn, ticker, sell_qty, price)
                trade_summary.append(f"SALJ {ticker} ${abs(delta):,.0f}")

        # Köp sedan
        for ticker, weight in target.items():
            if ticker == CASH_TICKER or weight <= 0:
                continue
            desired_value = weight * pv
            positions = load_positions(live_conn)
            current_value = 0.0
            if ticker in positions:
                current_value = positions[ticker]["qty"] * current_prices.get(ticker, positions[ticker]["avg_price"])
            delta = desired_value - current_value

            if delta > 50:  # Köp
                price = current_prices.get(ticker)
                if price is None:
                    log.warning("Inget pris for %s — hoppar over kop", ticker)
                    continue
                cash = _get_cash(live_conn)
                buy_amount = min(delta, cash)
                if buy_amount < 50:
                    continue
                sim_buy(live_conn, ticker, buy_amount, price)
                trade_summary.append(f"KOP {ticker} ${buy_amount:,.0f}")

        rebalanced = bool(trade_summary)

    # 6. Slutrapport
    pv = portfolio_value(live_conn, current_prices)
    positions = load_positions(live_conn)

    prev_pnl = live_conn.execute(
        "SELECT portfolio_value FROM daily_pnl ORDER BY date DESC LIMIT 2"
    ).fetchall()
    daily_ret = (prev_pnl[0][0] / prev_pnl[1][0] - 1.0) if len(prev_pnl) >= 2 else None

    msg = format_pnl_message(pv, daily_ret, positions, current_prices, rebalanced, trade_summary, stop_triggered)
    log.info(msg)
    send_telegram(msg)

    live_conn.close()
    log.info("=== Daglig korning klar ===")


if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run_daily()
