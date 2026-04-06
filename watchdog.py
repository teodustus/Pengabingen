# ============================================================
# Watchdog — kontrollerar att live.py kördes idag
# Dual Momentum Trading System
#
# Körs via cron 30 min efter live.py:
#   40 21 * * 1-5 cd /opt/trading-bot && set -a && source .env && set +a \
#     && .venv/bin/python watchdog.py >> logs/live.log 2>&1
# ============================================================

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LIVE_DB_PATH = Path("live_state.db")
MAX_AGE_HOURS = 25


def send_telegram(message: str) -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        log.info("Telegram ej konfigurerat — skriver bara till log")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        log.error("Telegram-fel: %s", e)


def check() -> None:
    if not LIVE_DB_PATH.exists():
        msg = "WATCHDOG: live_state.db saknas — live.py har aldrig körts."
        log.warning(msg)
        send_telegram(msg)
        return

    import sqlite3
    conn = sqlite3.connect(str(LIVE_DB_PATH))
    try:
        row = conn.execute(
            "SELECT timestamp FROM heartbeats ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        row = None
    finally:
        conn.close()

    now = datetime.now(timezone.utc)

    if row is None:
        msg = "WATCHDOG: Inga heartbeats i databasen — live.py har inte körts korrekt."
        log.warning(msg)
        send_telegram(msg)
        return

    last = datetime.fromisoformat(row[0])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    age_hours = (now - last).total_seconds() / 3600

    if age_hours > MAX_AGE_HOURS:
        msg = (
            f"WATCHDOG: live.py kördes senast {last.strftime('%Y-%m-%d %H:%M')} UTC "
            f"({age_hours:.0f}h sedan). "
            f"Kontrollera cron och logs/live.log."
        )
        log.warning(msg)
        send_telegram(msg)
    else:
        log.info("Watchdog OK — live.py kördes %s UTC (%.1fh sedan)", last.strftime("%Y-%m-%d %H:%M"), age_hours)


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    check()
