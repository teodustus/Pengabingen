# ============================================================
# Rapport: Summerar marknadsdata och paper trading-status
# Dual Momentum Trading System
#
# Genererar en Markdown-rapport i reports/YYYY-MM-DD.md
# och pushar den till git.
#
# Kör manuellt:
#   python report.py
#
# Eller lägg till i cron (efter live.py):
#   15 21 * * 1-5 cd /opt/trading-bot && .venv/bin/python report.py >> logs/live.log 2>&1
# ============================================================

import logging
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from config import CASH_TICKER, DB_PATH, MARKET_TICKER, UNIVERSE

log = logging.getLogger(__name__)

LIVE_DB_PATH  = Path("live_state.db")
REPORTS_DIR   = Path("reports")


# ── DATAINLÄSNING ─────────────────────────────────────────────

def _open(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    return sqlite3.connect(path)


def load_market_summary(conn: sqlite3.Connection) -> dict:
    """Sammanfattning av trading_data.db — täckning, färskhet, marknadsläge."""
    # Antal rader per ticker + senaste datum
    coverage = pd.read_sql(
        "SELECT ticker, COUNT(*) as rows, MAX(date) as last_date FROM prices GROUP BY ticker",
        conn,
    ).sort_values("ticker")

    # SPY för marknadsfilter
    spy = pd.read_sql(
        f"SELECT date, adj_close FROM prices WHERE ticker = '{MARKET_TICKER}' ORDER BY date",
        conn,
    )
    spy["date"] = pd.to_datetime(spy["date"])
    spy = spy.set_index("date")["adj_close"]

    sma200     = spy.rolling(200).mean().iloc[-1]
    spy_latest = spy.iloc[-1]
    in_market  = spy_latest > sma200

    # VIX
    vix = pd.read_sql(
        "SELECT adj_close FROM prices WHERE ticker = '^VIX' ORDER BY date DESC LIMIT 1",
        conn,
    )
    vix_val = float(vix.iloc[0, 0]) if not vix.empty else None

    # BIL (cash proxy)
    bil = pd.read_sql(
        "SELECT date, adj_close FROM prices WHERE ticker = 'BIL' ORDER BY date",
        conn,
    )
    bil["date"] = pd.to_datetime(bil["date"])
    bil = bil.set_index("date")["adj_close"]
    bil_cagr = None
    if len(bil) > 1:
        years = (bil.index[-1] - bil.index[0]).days / 365.25
        bil_cagr = float((bil.iloc[-1] / bil.iloc[0]) ** (1.0 / years) - 1.0) if years > 0 else 0.0

    return {
        "coverage":      coverage,
        "spy_latest":    float(spy_latest),
        "sma200":        float(sma200),
        "in_market":     in_market,
        "spy_pct_above": float((spy_latest - sma200) / sma200 * 100),
        "vix":           vix_val,
        "bil_cagr":      bil_cagr,
        "data_from":     spy.index[0].date(),
        "data_to":       spy.index[-1].date(),
        "total_rows":    int(coverage["rows"].sum()),
    }


def load_portfolio_summary(conn: sqlite3.Connection) -> dict:
    """Sammanfattning av live_state.db — portfölj, positioner, trades, P&L."""
    # Kassa
    cash_row = conn.execute("SELECT value FROM account WHERE key = 'cash'").fetchone()
    cash = float(cash_row[0]) if cash_row else None

    # Positioner
    positions = pd.read_sql(
        "SELECT ticker, qty, avg_price, entry_date FROM positions ORDER BY ticker", conn
    )

    # P&L-historik
    pnl = pd.read_sql(
        "SELECT date, portfolio_value, daily_return FROM daily_pnl ORDER BY date", conn
    )
    pnl["date"] = pd.to_datetime(pnl["date"])

    # Trades
    trades = pd.read_sql(
        "SELECT date, ticker, action, qty, price, amount, cost FROM live_trades ORDER BY date DESC LIMIT 20",
        conn,
    )

    # Beräkna nyckeltal om vi har tillräcklig historik
    metrics = {}
    if len(pnl) >= 2:
        pv = pnl.set_index("date")["portfolio_value"]
        total_ret  = (pv.iloc[-1] / pv.iloc[0]) - 1.0
        years      = (pv.index[-1] - pv.index[0]).days / 365.25
        cagr       = float((1 + total_ret) ** (1.0 / years) - 1.0) if years > 0 else 0.0
        daily_ret  = pv.pct_change().dropna()
        ann_vol    = float(daily_ret.std() * np.sqrt(252)) if len(daily_ret) > 1 else 0.0
        rolling_max = pv.cummax()
        max_dd     = float(((pv - rolling_max) / rolling_max).min())
        metrics    = {
            "total_return": total_ret,
            "cagr":         cagr,
            "ann_vol":      ann_vol,
            "max_drawdown": max_dd,
            "days_running": (pv.index[-1] - pv.index[0]).days,
            "start_value":  float(pv.iloc[0]),
            "latest_value": float(pv.iloc[-1]),
        }

    # Health check: senaste heartbeat
    hb_row = conn.execute(
        "SELECT timestamp FROM heartbeats ORDER BY id DESC LIMIT 1"
    ).fetchone()
    last_heartbeat = datetime.fromisoformat(hb_row[0]) if hb_row else None

    return {
        "cash":           cash,
        "positions":      positions,
        "pnl":            pnl,
        "trades":         trades,
        "metrics":        metrics,
        "last_heartbeat": last_heartbeat,
    }


# ── RAPPORT-BYGGARE ───────────────────────────────────────────

def _sign(v: float) -> str:
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"


def build_report(market: dict, portfolio: dict | None) -> str:
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: list[str] = []

    lines.append(f"# Daglig rapport — {today}")
    lines.append("")
    lines.append(
        "> Automatiskt genererad av `report.py`. "
        "Visar marknadsläge och paper trading-status."
    )
    lines.append("")

    # ── 1. MARKNADSLÄGE ──────────────────────────────────────
    lines.append("## 1. Marknadsläge")
    lines.append("")

    regime_str = "**I MARKNADEN** (SPY > 200d SMA)" if market["in_market"] \
                 else "**UTANFÖR MARKNADEN** (SPY < 200d SMA)"
    vix_str    = f"{market['vix']:.1f}" if market["vix"] else "–"
    vix_regime = "Normal (< 30)" if market["vix"] and market["vix"] < 30 else "Hög (≥ 30) — halverad positionsstorlek"

    lines.append(f"| Indikator | Värde | Status |")
    lines.append(f"|-----------|-------|--------|")
    lines.append(
        f"| SPY senaste kurs | ${market['spy_latest']:.2f} | {regime_str} |"
    )
    lines.append(
        f"| SPY 200d SMA | ${market['sma200']:.2f} | "
        f"{market['spy_pct_above']:+.1f}% {'över' if market['in_market'] else 'under'} SMA |"
    )
    lines.append(f"| VIX | {vix_str} | {vix_regime} |")
    if market["bil_cagr"] is not None:
        lines.append(f"| BIL CAGR (riskfri ränta) | {market['bil_cagr']*100:.2f}% | Används i Sharpe-beräkning |")
    lines.append("")

    # ── 2. DATAKVALITET ──────────────────────────────────────
    lines.append("## 2. Datakvalitet")
    lines.append("")
    lines.append(
        f"Historisk data från **{market['data_from']}** till **{market['data_to']}** "
        f"— totalt **{market['total_rows']:,} rader** i databasen."
    )
    lines.append("")

    cov = market["coverage"]
    # Visa bara tickers med avvikande radantal eller gammal data
    latest_date = cov["last_date"].max()
    stale = cov[cov["last_date"] < latest_date]
    short = cov[cov["rows"] < cov["rows"].max() * 0.8]
    flagged = pd.concat([stale, short]).drop_duplicates("ticker")

    if flagged.empty:
        lines.append("Alla tickers har fullständig och färsk data. ✓")
    else:
        lines.append(
            f"**{len(flagged)} ticker(s) med avvikande data** "
            f"(kortare historik eller gammal data):"
        )
        lines.append("")
        lines.append("| Ticker | Rader | Senaste datum |")
        lines.append("|--------|-------|---------------|")
        for _, row in flagged.iterrows():
            lines.append(f"| {row['ticker']} | {row['rows']:,} | {row['last_date']} |")
    lines.append("")

    # ── 3. PAPER TRADING ─────────────────────────────────────
    lines.append("## 3. Paper Trading")
    lines.append("")

    if portfolio is None:
        lines.append(
            "> `live_state.db` saknas — paper trading har inte startat ännu. "
            "Kör `python live.py` för att initialisera."
        )
        lines.append("")
        return "\n".join(lines)

    metrics   = portfolio["metrics"]
    positions = portfolio["positions"]
    pnl       = portfolio["pnl"]
    trades    = portfolio["trades"]
    cash      = portfolio["cash"]

    # 3a. Portföljöversikt
    lines.append("### Portföljöversikt")
    lines.append("")

    if metrics:
        start_val = metrics["start_value"]
        latest_val = metrics["latest_value"]
        total_ret  = metrics["total_return"]
        lines.append(f"| Nyckeltal | Värde |")
        lines.append(f"|-----------|-------|")
        lines.append(f"| Startkapital | ${start_val:,.0f} |")
        lines.append(f"| Nuvarande värde | ${latest_val:,.0f} |")
        lines.append(f"| Total avkastning | {_sign(total_ret*100)} |")
        if metrics["days_running"] > 30:
            lines.append(f"| CAGR (annualiserad) | {metrics['cagr']*100:.1f}% |")
        lines.append(f"| Max drawdown | {metrics['max_drawdown']*100:.1f}% |")
        if metrics["ann_vol"] > 0:
            lines.append(f"| Annualiserad volatilitet | {metrics['ann_vol']*100:.1f}% |")
        lines.append(f"| Dagar i drift | {metrics['days_running']} |")
    elif cash is not None:
        lines.append(f"Konto initierat. Kassa: **${cash:,.0f}**. Inga affärer ännu.")
    lines.append("")

    # 3b. Positioner
    lines.append("### Positioner")
    lines.append("")

    if positions.empty:
        lines.append("Inga öppna positioner (100% i kassa).")
    else:
        lines.append("| Ticker | Antal | Inköpspris | In sedan |")
        lines.append("|--------|-------|-----------|----------|")
        for _, pos in positions.iterrows():
            lines.append(
                f"| {pos['ticker']} | {pos['qty']:.2f} | ${pos['avg_price']:.2f} | {pos['entry_date']} |"
            )
        if cash is not None:
            lines.append("")
            lines.append(f"**Kassa:** ${cash:,.0f}")
    lines.append("")

    # 3c. P&L-historik (senaste 30 dagarna)
    lines.append("### P&L-historik (senaste 30 dagarna)")
    lines.append("")

    if pnl.empty:
        lines.append("Ingen P&L-historik ännu.")
    else:
        recent = pnl.tail(30).copy()
        lines.append("| Datum | Portföljvärde | Daglig avkastning |")
        lines.append("|-------|--------------|-------------------|")
        for _, row in recent.iterrows():
            ret_str = _sign(row["daily_return"] * 100) if pd.notna(row["daily_return"]) else "–"
            lines.append(
                f"| {row['date'].strftime('%Y-%m-%d')} | "
                f"${row['portfolio_value']:,.0f} | {ret_str} |"
            )
    lines.append("")

    # 3d. Senaste affärer
    lines.append("### Senaste affärer (max 20)")
    lines.append("")

    if trades.empty:
        lines.append("Inga affärer loggade ännu.")
    else:
        lines.append("| Datum | Ticker | Åtgärd | Antal | Pris | Belopp | Kostnad |")
        lines.append("|-------|--------|--------|-------|------|--------|---------|")
        for _, t in trades.iterrows():
            lines.append(
                f"| {t['date']} | {t['ticker']} | {t['action']} "
                f"| {t['qty']:.2f} | ${t['price']:.2f} "
                f"| ${t['amount']:,.0f} | ${t['cost']:.2f} |"
            )
    lines.append("")

    # ── 4. HÄLSOSTATUS ───────────────────────────────────────
    lines.append("## 4. Systemhälsa")
    lines.append("")
    hb = portfolio.get("last_heartbeat")
    now_utc = datetime.now(timezone.utc)
    if hb is None:
        lines.append("live.py har inte körts ännu.")
    else:
        hb_utc = hb if hb.tzinfo else hb.replace(tzinfo=timezone.utc)
        age_h  = (now_utc - hb_utc).total_seconds() / 3600
        if age_h > 25:
            lines.append(
                f"**VARNING:** live.py kördes senast för {age_h:.0f} timmar sedan "
                f"({hb_utc.strftime('%Y-%m-%d %H:%M')} UTC). "
                f"Kontrollera cron och logs/live.log."
            )
        else:
            lines.append(
                f"live.py kördes {hb_utc.strftime('%Y-%m-%d %H:%M')} UTC "
                f"({age_h:.1f}h sedan). ✓"
            )
    lines.append("")

    # ── 5. FÖRKLARINGAR ──────────────────────────────────────
    lines.append("## 5. Förklaringar")
    lines.append("")
    lines.append("| Term | Förklaring |")
    lines.append("|------|-----------|")
    lines.append("| SPY 200d SMA | Rullande 200-dagarsmedelvärde — marknadens långsiktiga trend |")
    lines.append("| VIX ≥ 30 | Hög volatilitet → positionsstorlek halveras automatiskt |")
    lines.append("| BIL CAGR | Annualiserad avkastning på kortränta ETF — används som riskfri ränta i Sharpe |")
    lines.append("| CAGR | Compound Annual Growth Rate — annualiserad tillväxttakt |")
    lines.append("| Max drawdown | Största toppet-till-botten-fall under perioden |")
    lines.append("| STOP | Stop-loss triggrad — position stängd 8% under inköpspris |")
    lines.append("")

    return "\n".join(lines)


# ── GIT-PUSH ──────────────────────────────────────────────────

def git_push_report(report_path: Path) -> bool:
    """
    Committar och pushar rapporten till git.
    Returnerar True om lyckades.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        files_to_add = [str(report_path)]
        if Path(LIVE_DB_PATH).exists():
            files_to_add.append(str(LIVE_DB_PATH))
        subprocess.run(["git", "add"] + files_to_add, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True,
        )
        if result.returncode == 0:
            log.info("Ingen förändring — hoppar över commit")
            return True

        subprocess.run(
            ["git", "commit", "-m", f"Rapport {today}"],
            check=True, capture_output=True,
        )

        # Synka med remote innan push för att undvika konflikter
        pull = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            capture_output=True,
        )
        if pull.returncode != 0:
            log.warning("git pull --rebase misslyckades: %s", pull.stderr.decode())

        # Retry med exponential backoff vid nätverksfel
        import time as _time
        for attempt, wait in enumerate([0, 2, 4, 8, 16]):
            if wait:
                log.warning("git push misslyckades (forsok %d) — väntar %ds", attempt, wait)
                _time.sleep(wait)
            try:
                subprocess.run(
                    ["git", "push", "-u", "origin", "main"],
                    check=True, capture_output=True,
                )
                log.info("Rapport pushad: %s", report_path)
                return True
            except subprocess.CalledProcessError:
                continue

        log.error("git push misslyckades efter fem forsok")
        return False

    except subprocess.CalledProcessError as e:
        log.error("Git-fel: %s", e.stderr.decode() if e.stderr else e)
        return False


# ── HUVUDPROGRAM ──────────────────────────────────────────────

def run_report(push: bool = True) -> Path:
    """
    Genererar rapport och pushar till git.
    Returnerar sökvägen till den genererade filen.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Läs databaser
    data_conn = _open(DB_PATH)
    if data_conn is None:
        raise FileNotFoundError(
            f"{DB_PATH} saknas — kör `python data_pipeline.py` först"
        )

    live_conn = _open(LIVE_DB_PATH)

    market    = load_market_summary(data_conn)
    portfolio = load_portfolio_summary(live_conn) if live_conn else None

    data_conn.close()
    if live_conn:
        live_conn.close()

    # Bygg rapport
    report_md = build_report(market, portfolio)

    # Spara
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"{today}.md"
    report_path.write_text(report_md, encoding="utf-8")
    log.info("Rapport sparad: %s", report_path)

    if push:
        git_push_report(report_path)

    return report_path


if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run_report()
