# ============================================================
# Steg 6: Paper/Live trading-koppling
# Dual Momentum Trading System
#
# Installera: pip install alpaca-py python-telegram-bot
#
# Miljövariabler som krävs:
#   ALPACA_API_KEY        — Alpaca API-nyckel
#   ALPACA_API_SECRET     — Alpaca API-hemlighet
#   ALPACA_BASE_URL       — https://paper-api.alpaca.markets (paper)
#                           https://api.alpaca.markets (live)
#   TELEGRAM_BOT_TOKEN    — Bot-token från @BotFather
#   TELEGRAM_CHAT_ID      — Chat-ID att skicka meddelanden till
#
# Kör dagligen via cron kl 18:00 CET (12:00 ET, efter US-börsen stängt):
#   0 18 * * 1-5 cd /opt/trading-bot && python live.py >> logs/live.log 2>&1
# ============================================================

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from config import CASH_TICKER, DB_PATH, MARKET_TICKER, UNIVERSE
from data_pipeline import init_db, load_prices, load_vix, update_universe
from market_filter import market_regime
from momentum import rank_universe
from portfolio import (
    MAX_DAILY_LOSS,
    MAX_DRAWDOWN,
    STOP_LOSS,
    TOP_N,
    drawdown_exceeded,
    target_weights,
)

log = logging.getLogger(__name__)

# ── KONFIGURATION ─────────────────────────────────────────────

ALPACA_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_API_SECRET", "")
ALPACA_URL    = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
TG_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")

LIVE_DB_PATH  = Path("live_state.db")


# ── TELEGRAM ──────────────────────────────────────────────────

def send_telegram(message: str) -> None:
    """Skickar ett meddelande via Telegram. Loggar fel men kraschar inte."""
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("Telegram ej konfigurerat — hoppar över notifiering")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        log.error("Telegram-fel: %s", e)


# ── ALPACA-KLIENT ─────────────────────────────────────────────

class AlpacaClient:
    """Tunn wrapper mot Alpaca REST API v2."""

    def __init__(self) -> None:
        if not ALPACA_KEY or not ALPACA_SECRET:
            raise EnvironmentError(
                "ALPACA_API_KEY och ALPACA_API_SECRET måste vara satta som miljövariabler"
            )
        self._base = ALPACA_URL.rstrip("/")
        self._headers = {
            "APCA-API-KEY-ID":     ALPACA_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET,
        }

    def _get(self, path: str) -> dict | list:
        resp = requests.get(f"{self._base}{path}", headers=self._headers, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = requests.post(f"{self._base}{path}", headers=self._headers, json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def account(self) -> dict:
        return self._get("/v2/account")

    def positions(self) -> list[dict]:
        return self._get("/v2/positions")

    def portfolio_value(self) -> float:
        return float(self.account()["portfolio_value"])

    def latest_prices(self, tickers: list[str]) -> dict[str, float]:
        """Hämtar senaste handelspris för en lista tickers via Alpaca Data API."""
        symbols = ",".join(tickers)
        data_base = "https://data.alpaca.markets"
        resp = requests.get(
            f"{data_base}/v2/stocks/trades/latest",
            headers=self._headers,
            params={"symbols": symbols, "feed": "iex"},
            timeout=15,
        )
        resp.raise_for_status()
        return {sym: float(info["trade"]["p"]) for sym, info in resp.json()["trades"].items()}

    def place_order(
        self,
        ticker: str,
        qty: float,
        side: str,           # "buy" eller "sell"
        order_type: str = "market",
        time_in_force: str = "day",
    ) -> dict:
        """Lägger en order. Returnerar Alpaca order-objekt."""
        body = {
            "symbol":        ticker,
            "qty":           str(round(qty, 6)),
            "side":          side,
            "type":          order_type,
            "time_in_force": time_in_force,
        }
        log.info("Order: %s %s %.4f", side.upper(), ticker, qty)
        return self._post("/v2/orders", body)

    def close_position(self, ticker: str) -> dict:
        """Stänger hela positionen för en ticker."""
        resp = requests.delete(
            f"{self._base}/v2/positions/{ticker}",
            headers=self._headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


# ── LIVE-DATABAS ──────────────────────────────────────────────

def init_live_db(path: Path = LIVE_DB_PATH) -> sqlite3.Connection:
    """Initierar SQLite-databas för live-tillstånd (entry-priser, P&L, trade-logg)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entry_prices (
            ticker      TEXT PRIMARY KEY,
            entry_price REAL NOT NULL,
            entry_date  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS live_trades (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            date     TEXT NOT NULL,
            ticker   TEXT NOT NULL,
            action   TEXT NOT NULL,
            qty      REAL,
            price    REAL,
            amount   REAL
        );

        CREATE TABLE IF NOT EXISTS daily_pnl (
            date            TEXT PRIMARY KEY,
            portfolio_value REAL NOT NULL,
            daily_return    REAL
        );
    """)
    conn.commit()
    return conn


def load_entry_prices(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute("SELECT ticker, entry_price FROM entry_prices").fetchall()
    return {r[0]: r[1] for r in rows}


def save_entry_price(conn: sqlite3.Connection, ticker: str, price: float) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR REPLACE INTO entry_prices (ticker, entry_price, entry_date) VALUES (?, ?, ?)",
        (ticker, price, today),
    )
    conn.commit()


def remove_entry_price(conn: sqlite3.Connection, ticker: str) -> None:
    conn.execute("DELETE FROM entry_prices WHERE ticker = ?", (ticker,))
    conn.commit()


def log_trade(conn: sqlite3.Connection, ticker: str, action: str,
              qty: float, price: float, amount: float) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO live_trades (date, ticker, action, qty, price, amount) VALUES (?, ?, ?, ?, ?, ?)",
        (today, ticker, action, qty, price, amount),
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


# ── HJÄLPFUNKTIONER ───────────────────────────────────────────

def is_rebalance_day() -> bool:
    """
    Returnerar True om idag är sista handelsdagen i månaden.

    Approximation: om morgondagen tillhör en annan månad är idag sista dagen.
    Alpaca stänger kl 16:00 ET. Vi kör kl 18:00 CET = 12:00 ET, så börsen
    är öppen — men vi handlar vid stängning (market orders, time_in_force=day).
    """
    today = pd.Timestamp.now(tz="America/New_York").normalize()
    # Flytta en dag framåt med USFederalHolidayCalendar om tillgängligt
    try:
        from pandas.tseries.holiday import USFederalHolidayCalendar
        from pandas.tseries.offsets import CustomBusinessDay
        us_bd = CustomBusinessDay(calendar=USFederalHolidayCalendar())
        next_bd = today + us_bd
    except Exception:
        next_bd = today + pd.offsets.BDay(1)

    return today.month != next_bd.month


def compute_target(prices: pd.DataFrame, vix: pd.Series) -> pd.Series:
    """
    Beräknar målvikter för idag baserat på aktuell data.
    Returnerar en Serie: ticker → vikt.
    """
    ranks  = rank_universe(prices).shift(1)
    regime = market_regime(prices, vix).shift(1)
    w = target_weights(ranks, regime, top_n=TOP_N)
    if w.empty:
        return pd.Series({CASH_TICKER: 1.0})
    return w.iloc[-1]


def format_pnl_message(
    portfolio_value: float,
    daily_ret: float | None,
    positions: list[dict],
    rebalanced: bool,
    trade_summary: list[str],
) -> str:
    """Formaterar daglig P&L-rapport för Telegram."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"📊 Trading Bot — {date_str}"]
    lines.append(f"Portföljvärde: ${portfolio_value:,.0f}")

    if daily_ret is not None:
        sign = "+" if daily_ret >= 0 else ""
        lines.append(f"Daglig P&L: {sign}{daily_ret*100:.2f}%")

    if rebalanced:
        lines.append("\n🔄 Rebalansering utförd:")
        lines.extend(f"  {t}" for t in trade_summary)

    if positions:
        lines.append("\n📦 Positioner:")
        for p in positions:
            lines.append(f"  {p['symbol']:<8} {float(p['unrealized_plpc'])*100:+.1f}%")

    return "\n".join(lines)


# ── DAGLIG KÖRNING ────────────────────────────────────────────

def run_daily() -> None:
    """
    Huvudfunktion — körs dagligen via cron efter börsstängning.

    Flöde:
      1. Uppdatera marknadsdata (yfinance)
      2. Kontrollera stop-losses mot aktuella priser
      3. Om rebalanseringsdag: beräkna ny portfölj och exekvera affärer
      4. Logga P&L och skicka Telegram-rapport
    """
    log.info("=== Startar daglig körning %s ===", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    # 1. Uppdatera marknadsdata
    data_conn = init_db()
    update_universe(data_conn)
    prices = load_prices(data_conn)
    vix    = load_vix(data_conn)
    data_conn.close()

    # 2. Initialisera Alpaca och live-databas
    api       = AlpacaClient()
    live_conn = init_live_db()
    pv        = api.portfolio_value()

    log_daily_pnl(live_conn, pv)

    # 3. Hämta aktuella priser och kontrollera stop-losses
    try:
        live_prices = api.latest_prices(UNIVERSE)
    except Exception as e:
        # Om vi inte kan hämta live-priser kan vi inte kontrollera stop-losses.
        # Avbryt körningen och skicka varning — bättre att göra ingenting än fel.
        msg = f"KRITISKT: Kunde inte hamta live-priser fran Alpaca: {e}\nStop-loss ej kontrollerat. Manuell atgard kravs."
        log.error(msg)
        send_telegram(f"⚠️ {msg}")
        live_conn.close()
        return

    entry_prices = load_entry_prices(live_conn)
    stop_triggered: list[str] = []

    for ticker, entry in list(entry_prices.items()):
        curr = live_prices.get(ticker)
        if curr is None:
            log.warning("Inget live-pris for %s — stop-loss ej kontrollerat", ticker)
            continue
        if curr < entry * (1.0 - STOP_LOSS):
            log.warning("Stop-loss triggas för %s (entry=%.2f, nu=%.2f)", ticker, entry, curr)
            try:
                api.close_position(ticker)
                remove_entry_price(live_conn, ticker)
                amount = curr  # approximation utan antal aktier
                log_trade(live_conn, ticker, "STOP", 0, curr, 0)
                stop_triggered.append(ticker)
            except Exception as e:
                log.error("Fel vid stop-loss för %s: %s", ticker, e)

    # 4. Drawdown-kontroll
    pnl_rows = pd.read_sql(
        "SELECT date, portfolio_value FROM daily_pnl ORDER BY date",
        live_conn,
    )
    if not pnl_rows.empty:
        pnl_series = pnl_rows.set_index("date")["portfolio_value"]
        pnl_series.index = pd.to_datetime(pnl_series.index)
        if drawdown_exceeded(pnl_series).iloc[-1]:
            msg = (
                f"⚠️ MAX DRAWDOWN NÅDD — systemet pausat.\n"
                f"Portföljvärde: ${pv:,.0f}\n"
                f"Manuell omstart krävs."
            )
            log.warning(msg)
            send_telegram(msg)
            live_conn.close()
            return

    # 5. Rebalansering (om det är dags)
    rebalanced    = False
    trade_summary = []

    if is_rebalance_day():
        log.info("Rebalanseringsdag — beräknar ny portfölj")
        target = compute_target(prices, vix)
        current_positions = {p["symbol"]: float(p["market_value"]) for p in api.positions()}

        for ticker, weight in target.items():
            desired_value = weight * pv
            current_value = current_positions.get(ticker, 0.0)
            delta = desired_value - current_value

            if abs(delta) < 50:   # Ignorera affärer under $50
                continue

            try:
                curr_price = live_prices.get(ticker)
                if curr_price is None:
                    if ticker in prices.columns:
                        curr_price = float(prices[ticker].iloc[-1])
                        log.warning("Anvander historiskt pris for %s (live-pris saknas): %.2f", ticker, curr_price)
                    else:
                        log.error("Inget pris tillgangligt for %s — hoppar over ordern", ticker)
                        continue
                qty  = abs(delta) / curr_price
                side = "buy" if delta > 0 else "sell"
                api.place_order(ticker, qty, side)

                action = "KOP" if delta > 0 else "SALJ"
                log_trade(live_conn, ticker, action, qty, curr_price, abs(delta))
                trade_summary.append(f"{action} {ticker} ${abs(delta):,.0f}")

                if side == "buy":
                    save_entry_price(live_conn, ticker, curr_price)
                elif desired_value == 0:
                    remove_entry_price(live_conn, ticker)

            except Exception as e:
                log.error("Orderfel för %s: %s", ticker, e)

        rebalanced = bool(trade_summary)

    # 6. Hämta uppdaterade positioner och skicka rapport
    try:
        positions = api.positions()
        pv = api.portfolio_value()
    except Exception as e:
        log.error("Kunde inte hämta slutportfölj: %s", e)
        positions = []

    prev_pnl = live_conn.execute(
        "SELECT portfolio_value FROM daily_pnl ORDER BY date DESC LIMIT 2"
    ).fetchall()
    daily_ret = (prev_pnl[0][0] / prev_pnl[1][0] - 1.0) if len(prev_pnl) >= 2 else None

    msg = format_pnl_message(pv, daily_ret, positions, rebalanced, trade_summary)
    log.info(msg)
    send_telegram(msg)

    if stop_triggered:
        send_telegram(f"🛑 Stop-loss triggas for: {', '.join(stop_triggered)}")

    live_conn.close()
    log.info("=== Daglig körning klar ===")


if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run_daily()
