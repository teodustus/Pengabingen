from pathlib import Path

# Universe: S&P 100-aktier + SPY (marknadsindex) + BIL (cash-proxy)
UNIVERSE = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B",
    "UNH",  "JNJ",  "JPM",  "V",    "XOM",   "PG",   "MA",   "HD",
    "CVX",  "MRK",  "ABBV", "PEP",  "KO",    "AVGO", "COST", "MCD",
    "WMT",  "BAC",  "LLY",  "TMO",  "CSCO",  "ACN",  "ABT",  "CRM",
    "NKE",  "DHR",  "TXN",  "NEE",  "PM",    "RTX",  "HON",  "AMGN",
    "IBM",  "GE",   "CAT",  "BA",   "GS",    "MS",   "BLK",  "SPGI",
]

MARKET_TICKER = "SPY"   # Marknadsindex för marknadsfilter
CASH_TICKER   = "BIL"   # Kortränta ETF — vår "cash"-position
VIX_TICKER    = "^VIX"  # Volatilitetsindex för marknadsfilter

DB_PATH = Path("trading_data.db")

# Paper trading
PAPER_INITIAL_CAPITAL = 100_000.0   # Startkapital i USD
TRANSACTION_COST      = 0.0015      # 0.15% per affär (courtage + slippage)
