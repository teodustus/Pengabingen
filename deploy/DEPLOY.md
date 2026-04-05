# Deployment — Hetzner VPS

## Server

**Hetzner CAX11** (ARM64):
- 2 vCPU Ampere, 4 GB RAM, 40 GB SSD
- ~3.85 EUR/mån
- Mer än tillräckligt — boten använder <100 MB RAM och körs i ~30 sekunder per dag

Skapa server på [console.hetzner.cloud](https://console.hetzner.cloud):
1. Location: Falkenstein (de) eller Helsinki (fi)
2. Image: Ubuntu 24.04
3. Type: CAX11 (ARM64, Shared vCPU)
4. SSH-nyckel: ladda upp din `~/.ssh/id_ed25519.pub`
5. Starta

## Installation

```bash
ssh root@<SERVER-IP>
curl -sSL https://raw.githubusercontent.com/teodustus/pengabingen/main/deploy/setup.sh | bash
```

Scriptet:
1. Installerar Python 3, git, sqlite3
2. Skapar användare `trader` (kör aldrig boten som root)
3. Klonar repot till `/opt/trading-bot`
4. Sätter upp virtualenv + beroenden
5. Skapar `.env`-mall för Telegram (valfritt)
6. Installerar cron-jobb (21:10 UTC vardagar)

## Telegram (valfritt)

Systemet fungerar utan Telegram — all data loggas till `live_state.db` och `logs/live.log`.

Om du vill ha dagliga rapporter, fyll i `/opt/trading-bot/.env`:

```bash
nano /opt/trading-bot/.env
```

| Variabel | Var du hämtar den |
|----------|-------------------|
| `TELEGRAM_BOT_TOKEN` | Skapa bot via [@BotFather](https://t.me/BotFather) i Telegram |
| `TELEGRAM_CHAT_ID` | Skicka `/start` till boten, kör: `curl https://api.telegram.org/bot<TOKEN>/getUpdates` |

Filen är `chmod 600` — bara `trader`-användaren kan läsa den.

## Första körningen

```bash
# Byt till trader-användaren
sudo -u trader bash
cd /opt/trading-bot
source .env
source .venv/bin/activate

# Ladda ner historisk data (~5 min första gången)
python data_pipeline.py

# Kör backtest
python backtest.py

# Testkör live.py manuellt (simulerar paper trading)
python live.py
```

## Tidslinje

| Tid (UTC) | Tid (CET) | Händelse |
|-----------|-----------|----------|
| 21:00 | 23:00 | US-börsen stänger (vintertid) |
| 21:10 | 23:10 | Cron kör `live.py` |

**OBS:** 21:10 UTC = 10 min efter börsens stängning. yfinance-data uppdateras
normalt inom minuter. Om data saknas loggas en varning men systemet avbryter inte.

## Uppdatering

```bash
sudo -u trader bash -c 'cd /opt/trading-bot && git pull origin main'
```

## Övervakning

### Loggar
```bash
# Senaste körningen
tail -50 /opt/trading-bot/logs/live.log

# Alla körningar
less /opt/trading-bot/logs/live.log
```

### Cron-verifiering
```bash
sudo -u trader crontab -l
```

### Databas
```bash
cd /opt/trading-bot
sqlite3 live_state.db "SELECT * FROM daily_pnl ORDER BY date DESC LIMIT 7;"
sqlite3 live_state.db "SELECT * FROM live_trades ORDER BY date DESC LIMIT 10;"
```

## Säkerhet

- **Kör aldrig som root.** Boten körs som `trader`.
- **`.env` är 600.** Bara `trader` kan läsa API-nycklar.
- **SSH-nyckel, inte lösenord.** Stäng av lösenordsinloggning i `/etc/ssh/sshd_config`.
- **Brandvägg:** Hetzner firewall — öppna bara port 22 (SSH).
- **Automatiska säkerhetsuppdateringar:**
  ```bash
  apt-get install -y unattended-upgrades
  dpkg-reconfigure -plow unattended-upgrades
  ```

## Kostnadsöversikt

| Post | Kostnad/mån |
|------|-------------|
| Hetzner CAX11 | ~3.85 EUR |
| Telegram Bot | 0 |
| yfinance | 0 |
| **Totalt** | **~3.85 EUR/mån** |
