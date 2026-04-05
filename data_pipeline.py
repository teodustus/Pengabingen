# ============================================================

# Steg 1: Datapipeline

# Dual Momentum Trading System

# 

# Installera: pip install yfinance pandas numpy

# ============================================================

import yfinance as yf
import pandas as pd
import numpy as np
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(
level=logging.INFO,
format=”%(asctime)s  %(levelname)s  %(message)s”,
datefmt=”%Y-%m-%d %H:%M:%S”,
)
log = logging.getLogger(**name**)

# ── KONFIGURATION ────────────────────────────────────────────

# Universe: S&P 100-aktier + SPY (marknadsindex) + BIL (cash-proxy)

# SPY används för marknadsfilter, BIL är vår “cash”-position

UNIVERSE = [
“AAPL”, “MSFT”, “AMZN”, “NVDA”, “GOOGL”, “META”, “TSLA”, “BRK-B”,
“UNH”,  “JNJ”,  “JPM”,  “V”,    “XOM”,   “PG”,   “MA”,   “HD”,
“CVX”,  “MRK”,  “ABBV”, “PEP”,  “KO”,    “AVGO”, “COST”, “MCD”,
“WMT”,  “BAC”,  “LLY”,  “TMO”,  “CSCO”,  “ACN”,  “ABT”,  “CRM”,
“NKE”,  “DHR”,  “TXN”,  “NEE”,  “PM”,    “RTX”,  “HON”,  “AMGN”,
“IBM”,  “GE”,   “CAT”,  “BA”,   “GS”,    “MS”,   “BLK”,  “SPGI”,
]

MARKET_TICKER = “SPY”   # Marknadsindex för marknadsfilter
CASH_TICKER   = “BIL”   # Kortränta ETF — vår “cash”-position
VIX_TICKER    = “^VIX”  # Volatilitetsindex för marknadsfilter

DB_PATH = Path(“trading_data.db”)

# ── DATABASINIT ───────────────────────────────────────────────

def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
“”“Skapar databasen och tabellerna om de inte finns.”””
conn = sqlite3.connect(db_path)
conn.execute(”””
CREATE TABLE IF NOT EXISTS prices (
ticker      TEXT    NOT NULL,
date        TEXT    NOT NULL,
open        REAL,
high        REAL,
low         REAL,
close       REAL,
volume      INTEGER,
adj_close   REAL,
PRIMARY KEY (ticker, date)
)
“””)
conn.execute(”””
CREATE TABLE IF NOT EXISTS downloads (
ticker      TEXT PRIMARY KEY,
last_update TEXT NOT NULL
)
“””)
conn.commit()
log.info(“Databas initierad: %s”, db_path)
return conn

# ── DATAHÄMTNING ──────────────────────────────────────────────

def fetch_ticker(
ticker: str,
start: str = “2010-01-01”,
end: str | None = None,
) -> pd.DataFrame:
“”“Hämtar daglig OHLCV-data för en ticker via yfinance.”””
end = end or datetime.today().strftime(”%Y-%m-%d”)
try:
df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
if df.empty:
log.warning(“Ingen data för %s”, ticker)
return pd.DataFrame()

```
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df["adj_close"] = df["close"]   # auto_adjust=True justerar redan
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    df.index.name = "date"
    df["ticker"] = ticker
    df.dropna(subset=["close"], inplace=True)
    return df

except Exception as e:
    log.error("Fel vid hämtning av %s: %s", ticker, e)
    return pd.DataFrame()
```

def save_to_db(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> int:
“”“Sparar prisdata till databasen. Returnerar antal nya rader.”””
if df.empty:
return 0

```
rows = df.reset_index()[["ticker", "date", "open", "high", "low", "close", "volume", "adj_close"]]
rows.to_sql("prices", conn, if_exists="append", index=False, method="ignore")

conn.execute(
    "INSERT OR REPLACE INTO downloads (ticker, last_update) VALUES (?, ?)",
    (ticker, datetime.today().strftime("%Y-%m-%d"))
)
conn.commit()
return len(rows)
```

def update_universe(
conn: sqlite3.Connection,
tickers: list[str] | None = None,
start: str = “2010-01-01”,
) -> None:
“”“Uppdaterar all data i universe. Hämtar bara ny data om ticker redan finns.”””
tickers = tickers or (UNIVERSE + [MARKET_TICKER, CASH_TICKER, VIX_TICKER])

```
existing = pd.read_sql(
    "SELECT ticker, last_update FROM downloads", conn
).set_index("ticker")["last_update"].to_dict()

for ticker in tickers:
    if ticker in existing:
        # Hämta bara data sedan senaste uppdatering
        last = existing[ticker]
        next_day = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        df = fetch_ticker(ticker, start=next_day)
        label = "uppdaterar"
    else:
        df = fetch_ticker(ticker, start=start)
        label = "laddar ner"

    n = save_to_db(conn, ticker, df)
    log.info("%s %s — %d nya rader", label.ljust(12), ticker, n)
```

# ── DATAINLÄSNING ─────────────────────────────────────────────

def load_prices(
conn: sqlite3.Connection,
tickers: list[str] | None = None,
start: str = “2010-01-01”,
end: str | None = None,
) -> pd.DataFrame:
“””
Läser in justerade stängningspriser som en bred DataFrame.
Returnerar: DataFrame med datum som index, tickers som kolumner.
“””
end = end or datetime.today().strftime(”%Y-%m-%d”)
tickers = tickers or (UNIVERSE + [MARKET_TICKER, CASH_TICKER])

```
placeholders = ",".join("?" * len(tickers))
query = f"""
    SELECT date, ticker, adj_close
    FROM prices
    WHERE ticker IN ({placeholders})
      AND date BETWEEN ? AND ?
    ORDER BY date
"""
df = pd.read_sql(query, conn, params=tickers + [start, end])
prices = df.pivot(index="date", columns="ticker", values="adj_close")
prices.index = pd.to_datetime(prices.index)
prices.sort_index(inplace=True)

# Ffill för helgdagar/handelsuppehåll, droppa rader med all NaN
prices.ffill(inplace=True)
prices.dropna(how="all", inplace=True)

return prices
```

def load_vix(
conn: sqlite3.Connection,
start: str = “2010-01-01”,
end: str | None = None,
) -> pd.Series:
“”“Läser in VIX-stängningspriser som en Serie.”””
end = end or datetime.today().strftime(”%Y-%m-%d”)
query = “””
SELECT date, adj_close
FROM prices
WHERE ticker = ?
AND date BETWEEN ? AND ?
ORDER BY date
“””
df = pd.read_sql(query, conn, params=[VIX_TICKER, start, end])
s = df.set_index(“date”)[“adj_close”]
s.index = pd.to_datetime(s.index)
return s

# ── VALIDERING ────────────────────────────────────────────────

def validate_data(prices: pd.DataFrame) -> None:
“”“Enkel datakvalitetskontroll — loggar varningar vid problem.”””
log.info(“Validerar data: %d datum, %d tickers”, len(prices), len(prices.columns))

```
# Kolla saknade kolumner
expected = set(UNIVERSE + [MARKET_TICKER, CASH_TICKER])
missing = expected - set(prices.columns)
if missing:
    log.warning("Saknar data för: %s", sorted(missing))

# Kolla extrema dagliga rörelser (sannolikt datafel)
daily_returns = prices.pct_change()
extreme = (daily_returns.abs() > 0.5).any()
if extreme.any():
    log.warning("Extrema dagliga rörelser (>50%%) för: %s", list(extreme[extreme].index))

# Kolla täckning
coverage = prices.notna().mean()
low_coverage = coverage[coverage < 0.8]
if not low_coverage.empty:
    log.warning("Låg datatäckning (<80%%) för: %s", list(low_coverage.index))

log.info("Validering klar.")
```

# ── HUVUDPROGRAM ──────────────────────────────────────────────

if **name** == “**main**”:
log.info(”=== Startar datapipeline ===”)

```
conn = init_db()

log.info("Hämtar marknadsdata (kan ta några minuter första gången)...")
update_universe(conn)

log.info("Läser in priser...")
prices = load_prices(conn)
vix    = load_vix(conn)

validate_data(prices)

log.info("Klart! Priser: %s till %s", prices.index[0].date(), prices.index[-1].date())
log.info("Exempel — sista 3 dagarna för SPY:\n%s", prices[["SPY"]].tail(3))
log.info("VIX senaste värde: %.1f", vix.iloc[-1])

conn.close()
```
