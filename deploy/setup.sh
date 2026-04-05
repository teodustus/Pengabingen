#!/usr/bin/env bash
# ============================================================
# Hetzner VPS Setup — Dual Momentum Trading Bot
#
# Rekommenderad server: CAX11 (ARM64, 2 vCPU, 4 GB RAM, ~4 EUR/mån)
# OS: Ubuntu 24.04
#
# Kör som root på nyinstallerad server:
#   curl -sSL https://raw.githubusercontent.com/teodustus/pengabingen/main/deploy/setup.sh | bash
#
# Eller manuellt:
#   scp deploy/setup.sh root@<IP>:/root/
#   ssh root@<IP> bash /root/setup.sh
# ============================================================

set -euo pipefail

APP_USER="trader"
APP_DIR="/opt/trading-bot"
REPO="https://github.com/teodustus/pengabingen.git"
BRANCH="main"

echo "=== [1/6] Systemuppdatering ==="
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq python3 python3-pip python3-venv git sqlite3 curl

echo "=== [2/6] Skapa applikationsanvändare ==="
if ! id "$APP_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$APP_USER"
    echo "Användare '$APP_USER' skapad"
fi

echo "=== [3/6] Klona repository ==="
if [ -d "$APP_DIR" ]; then
    echo "Katalog $APP_DIR finns redan — pull:ar istället"
    cd "$APP_DIR" && sudo -u "$APP_USER" git pull origin "$BRANCH"
else
    git clone -b "$BRANCH" "$REPO" "$APP_DIR"
    chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
fi

echo "=== [4/6] Python virtualenv ==="
cd "$APP_DIR"
sudo -u "$APP_USER" python3 -m venv .venv
sudo -u "$APP_USER" .venv/bin/pip install --upgrade pip -q
sudo -u "$APP_USER" .venv/bin/pip install -q -r requirements.txt

# Verifiera att alla paket installerades korrekt
sudo -u "$APP_USER" .venv/bin/python3 -c "
import yfinance, pandas, numpy, requests
print('  yfinance', yfinance.__version__)
print('  pandas  ', pandas.__version__)
print('  numpy   ', numpy.__version__)
print('  requests', requests.__version__)
print('Alla paket OK')
"

echo "=== [5/6] Skapa kataloger och miljöfil ==="
sudo -u "$APP_USER" mkdir -p "$APP_DIR/logs"

# Skapa .env-mall om den inte redan finns
if [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" << 'ENVEOF'
# Alpaca API (paper trading)
ALPACA_API_KEY=
ALPACA_API_SECRET=
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Telegram-notifieringar
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ENVEOF
    chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "VIKTIGT: Fyll i $APP_DIR/.env med dina API-nycklar!"
fi

echo "=== [6/6] Installera cron-jobb ==="
# US-börsen stänger 16:00 ET = 21:00 UTC (vintertid EST) / 20:00 UTC (sommartid EDT)
# Vi kör 21:10 UTC — täcker båda DST-lägena och ger yfinance tid att uppdatera
CRON_LINE="10 21 * * 1-5 cd $APP_DIR && set -a && source .env && set +a && .venv/bin/python live.py >> logs/live.log 2>&1"

if sudo -u "$APP_USER" crontab -l 2>/dev/null | grep -qF "live.py"; then
    echo "Cron-jobb finns redan — uppdaterar inte"
else
    (sudo -u "$APP_USER" crontab -l 2>/dev/null; echo "$CRON_LINE") | sudo -u "$APP_USER" crontab -
    echo "Cron-jobb installerat: 21:10 UTC vardagar (23:10 CET / 22:10 CEST)"
fi

echo ""
echo "============================================"
echo " Setup klar!"
echo ""
echo " Nästa steg:"
echo "   1. Fyll i API-nycklar: nano $APP_DIR/.env"
echo "   2. Ladda ner historisk data:"
echo "      sudo -u $APP_USER bash -c 'cd $APP_DIR && source .env && .venv/bin/python data_pipeline.py'"
echo "   3. Kör backtest:"
echo "      sudo -u $APP_USER bash -c 'cd $APP_DIR && .venv/bin/python backtest.py'"
echo "   4. Verifiera cron:"
echo "      sudo -u $APP_USER crontab -l"
echo "============================================"
