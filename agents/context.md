# Session Context — Pengabingen Trading Bot

## Senaste session: 2026-04-05

### Vad projektet är
Automatiserat dual momentum-system som handlar US S&P 100-aktier.
Månadsvis rebalansering, Python, SQLite, yfinance. Ingen mäklare krävs för paper trading.

### Nuläge
- Alla 6 steg implementerade och pushade till `main`
- Server: Hetzner CAX11, IP `178.104.9.107`, user `trader`, dir `/opt/trading-bot`
- Paper trading körs sedan denna session
- Data pipeline körs (bugfix för `method="ignore"` pushad i senaste commit)

### Filer

| Fil | Syfte |
|-----|-------|
| `config.py` | Konstanter: UNIVERSE, DB_PATH, PAPER_INITIAL_CAPITAL=100_000, TRANSACTION_COST=0.0015 |
| `data_pipeline.py` | yfinance → SQLite (trading_data.db). Kör manuellt eller via cron |
| `momentum.py` | 3/6/12-månaders composite momentum, rank_universe() |
| `market_filter.py` | SPY 200d SMA + VIX < 30 |
| `portfolio.py` | Målvikter, stop-loss 8%, max drawdown 20%, TOP_N=5 |
| `backtest.py` | Simulering med delade datamängder (train/val/test), Sharpe med BIL-riskfri ränta |
| `live.py` | Lokal paper trading-simulator, cron 21:10 UTC, Telegram valfritt |
| `deploy/setup.sh` | Automatisk Hetzner-setup |
| `deploy/DEPLOY.md` | Steg-för-steg deploy-guide |
| `docs/architecture.md` | 5 Mermaid-diagram |

### Databaser
- `trading_data.db` — historisk marknadsdata (~20-50 MB), kan återskapas med `data_pipeline.py`
- `live_state.db` — paper trading-state (kassa, positioner, trades, P&L), bör backas upp dagligen

### live_state.db-schema
```
account(key, value)               -- 'cash' → saldo
positions(ticker, qty, avg_price, entry_date)
live_trades(id, date, ticker, action, qty, price, amount, cost)
daily_pnl(date, portfolio_value, daily_return)
```

### Cron (trader@server)
```
10 21 * * 1-5  cd /opt/trading-bot && .venv/bin/python live.py >> logs/live.log 2>&1
```

### Kända begränsningar / designval
- Survivorship bias i yfinance (~1-2% CAGR överdriven, odokumenterbart)
- Sharpe-ratio beräknas med BIL CAGR som riskfri ränta (inte 0%)
- Drawdown-paus kräver manuell återstart (ingen auto-recovery)
- Cron 21:10 UTC = 5 min efter börsens stängning (16:00 ET = 21:00 UTC)

### Nästa rimliga steg
1. **Kör backtest.py** — validera strategin mot historisk data
2. **Följ upp live.py** — kolla `logs/live.log` och `live_state.db` efter första körning
3. **Telegram** — valfritt, sätt upp @BotFather + chat-ID i `.env` om du vill ha rapporter
4. **Live-handel** — om paper trading visar bra resultat efter 3-6 månader: Saxo Bank OpenAPI (EU-reglerat, MiFID II, bra för svenska användare)

### Beslut tagna
- **Ingen Alpaca** — för mycket KYC, ersatt med lokal simulator
- **Ingen swing trading** — inkompatibelt med dual momentum (för höga kostnader, fel signal-typ)
- **Hetzner CAX11** — ~3.85 EUR/mån, ARM64, Ubuntu 24.04
- **Saxo Bank** — rekommenderat alternativ för eventuell live-handel (inte Alpaca, inte IBKR)

### Buggar fixade denna session
- `save_to_db`: `method="ignore"` i pandas `to_sql` är ogiltigt → ersatt med `executemany` + `INSERT OR IGNORE`
- Hela Alpaca-kopplingen ersatt med lokal simulator (inga externa API-beroenden)
