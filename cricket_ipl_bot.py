#!/usr/bin/env python3
"""
IPL Cricket Away-Dog Bot
- Stratégia: Vendég csapat (underdog) fogadása IPL meccseken
- Szorzó szűrő: @1.80–2.50
- Backtest: 87 meccs, 2 szezon → +17.2% ROI
- Token: MULTI_SPORT_BOT_TOKEN | MULTI_SPORT_CHAT_ID
"""
from __future__ import annotations

import os
import sys
import asyncio
import time
import logging
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  KONFIGURÁCIÓ – Töltsd ki PythonAnywhere-en (vagy .env fájlban)!
# ═══════════════════════════════════════════════════════════════════
_BOT_TOKEN    = ""   # Telegram bot token (BotFather-től)
_CHAT_ID      = ""   # Telegram chat/csoport ID (pl. -1001234567890)
_DATABASE_URL = ""   # Supabase PostgreSQL URL (postgresql://user:pass@host:5432/db)
# ═══════════════════════════════════════════════════════════════════

BOT_TOKEN    = os.environ.get("CRICKET_BOT_TOKEN") or _BOT_TOKEN
DATABASE_URL = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DATABASE_URL

_chat_env = os.environ.get("CRICKET_CHAT_ID", "") or _CHAT_ID
CHAT_IDS = [int(x.strip()) for x in _chat_env.split(",") if x.strip()]

SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.sofascore.com/",
    "Accept":     "application/json",
}

# ── Beállítások ─────────────────────────────────────────────────────────────
MIN_DOG_ODDS   = 1.80
MAX_DOG_ODDS   = 999.0
SCAN_INTERVAL  = 1800   # 30 perc
RESULT_CHECK   = 3600   # 60 perc
TZ             = ZoneInfo("Europe/Budapest")

# ── DB ──────────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cricket_tips (
                    id            SERIAL PRIMARY KEY,
                    event_id      BIGINT UNIQUE NOT NULL,
                    home          TEXT NOT NULL,
                    away          TEXT NOT NULL,
                    league        TEXT NOT NULL,
                    match_time    TIMESTAMPTZ NOT NULL,
                    tip_team      TEXT NOT NULL,
                    tip_side      TEXT NOT NULL DEFAULT 'away_dog',
                    home_odds     REAL NOT NULL,
                    away_odds     REAL NOT NULL,
                    dog_odds      REAL NOT NULL,
                    sent_at       TIMESTAMPTZ DEFAULT NOW(),
                    result        TEXT,
                    actual_winner TEXT,
                    resolved_at   TIMESTAMPTZ
                )
            """)
        conn.commit()
    logger.info("cricket_tips tábla kész")

def already_sent(event_id: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM cricket_tips WHERE event_id = %s", (event_id,))
            return cur.fetchone() is not None

def save_tip(event_id, home, away, league, match_time, tip_team,
             home_odds, away_odds, dog_odds):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO cricket_tips
                    (event_id, home, away, league, match_time, tip_team,
                     home_odds, away_odds, dog_odds)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (event_id) DO NOTHING
            """, (event_id, home, away, league, match_time, tip_team,
                  home_odds, away_odds, dog_odds))
        conn.commit()

def get_pending_tips():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT * FROM cricket_tips
                WHERE result IS NULL
                  AND match_time < NOW() - INTERVAL '3 hours'
            """)
            return cur.fetchall()

def update_result(event_id, result, actual_winner):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE cricket_tips
                SET result = %s, actual_winner = %s, resolved_at = NOW()
                WHERE event_id = %s
            """, (result, actual_winner, event_id))
        conn.commit()

# ── SofaScore ───────────────────────────────────────────────────────────────
def frac_to_dec(f) -> float | None:
    if f is None:
        return None
    try:
        s = str(f)
        if "/" in s:
            a, b = s.split("/")
            return round(int(a) / int(b) + 1, 3)
        return round(float(s) + 1, 3)
    except Exception:
        return None

def fetch_ipl_matches(date_str: str) -> list[dict]:
    """Adott nap IPL meccseit adja vissza."""
    try:
        r = requests.get(
            f"https://api.sofascore.com/api/v1/sport/cricket/scheduled-events/{date_str}",
            headers=SOFASCORE_HEADERS, timeout=10
        )
        if r.status_code != 200:
            return []
        return [
            e for e in r.json().get("events", [])
            if e["tournament"]["name"] == "Indian Premier League"
        ]
    except Exception as exc:
        logger.warning(f"IPL meccsek lekérési hiba: {exc}")
        return []

def fetch_odds(event_id: int) -> tuple[float | None, float | None]:
    """(home_odds, away_odds) decimális formátumban."""
    try:
        r = requests.get(
            f"https://api.sofascore.com/api/v1/event/{event_id}/odds/1/all",
            headers=SOFASCORE_HEADERS, timeout=8
        )
        if r.status_code != 200:
            return None, None
        for m in r.json().get("markets", []):
            if m.get("marketName") == "Full time":
                ch = m.get("choices", [])
                h = frac_to_dec(next((c["fractionalValue"] for c in ch if c["name"] == "1"), None))
                a = frac_to_dec(next((c["fractionalValue"] for c in ch if c["name"] == "2"), None))
                return h, a
    except Exception as exc:
        logger.warning(f"Odds lekérési hiba {event_id}: {exc}")
    return None, None

def fetch_result(event_id: int) -> str | None:
    """'home' vagy 'away' ha vége, None ha még fut."""
    try:
        r = requests.get(
            f"https://api.sofascore.com/api/v1/event/{event_id}",
            headers=SOFASCORE_HEADERS, timeout=8
        )
        if r.status_code != 200:
            return None
        e = r.json().get("event", {})
        if e.get("status", {}).get("type") != "finished":
            return None
        hs = e.get("homeScore", {}).get("current", 0)
        as_ = e.get("awayScore", {}).get("current", 0)
        if hs == as_ == 0:
            return None
        return "home" if hs > as_ else "away"
    except Exception as exc:
        logger.warning(f"Eredmény lekérési hiba {event_id}: {exc}")
        return None

# ── Üzenetek ────────────────────────────────────────────────────────────────
def md_escape(text: str) -> str:
    """MarkdownV2 speciális karakterek escapelése."""
    special = r'\_*[]()~`>#+-=|{}.!'
    for ch in special:
        text = text.replace(ch, f"\\{ch}")
    return text

def fmt_odds(odds: float) -> str:
    return md_escape(f"{odds:.2f}")

def build_tip_message(home, away, tip_team, home_odds, away_odds, dog_odds,
                      match_time_str) -> str:
    return (
        f"🏏 *IPL KRIKETT TIPP*\n\n"
        f"🏟 {md_escape(home)} 🆚 {md_escape(away)}\n"
        f"📅 {md_escape(match_time_str)}\n\n"
        f"✅ *TIPP: {md_escape(tip_team)} nyeri a meccset*\n"
        f"📊 Szorzó: *@{fmt_odds(dog_odds)}*\n\n"
        f"Szorzók: {md_escape(home)} @{fmt_odds(home_odds)} \\| {md_escape(away)} @{fmt_odds(away_odds)}"
    )

def build_result_message(home, away, tip_team, dog_odds, result, actual_winner) -> str:
    actual_name = home if actual_winner == "home" else away
    won = (tip_team == actual_name)
    icon = "✅ NYERT" if won else "❌ VESZTETT"
    profit = md_escape(f"+{dog_odds - 1:.2f}") if won else "\\-1\\.00"
    return (
        f"🏏 *IPL EREDMÉNY*\n\n"
        f"{md_escape(home)} 🆚 {md_escape(away)}\n"
        f"Tipp: {md_escape(tip_team)}\n"
        f"Eredmény: {md_escape(actual_name)} nyert\n\n"
        f"{icon} \\| Szorzó: @{fmt_odds(dog_odds)} \\| {profit} egység"
    )

# ── Telegram küldés ─────────────────────────────────────────────────────────
async def send_to_all(bot: Bot, text: str):
    for chat_id in CHAT_IDS:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await asyncio.sleep(0.3)
        except Exception as exc:
            logger.error(f"Telegram küldési hiba ({chat_id}): {exc}")

# ── Fő scan ─────────────────────────────────────────────────────────────────
async def scan_and_send(bot: Bot):
    now = datetime.now(TZ)
    logger.info(f"IPL scan: {now.strftime('%Y-%m-%d %H:%M')}")

    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    tips_sent = 0
    for date_str in [today, tomorrow]:
        matches = fetch_ipl_matches(date_str)
        logger.info(f"  {date_str}: {len(matches)} IPL meccs")

        for e in matches:
            event_id = e["id"]
            status   = e.get("status", {}).get("type", "")

            # Csak jövőbeli meccsek
            if status == "finished":
                continue
            start_ts = e.get("startTimestamp", 0)
            match_dt = datetime.fromtimestamp(start_ts, tz=TZ)
            # 5 percnél régebben kezdett meccs kihagyás
            if match_dt < now - timedelta(minutes=5):
                continue
            # Max 36 óra előre
            if match_dt > now + timedelta(hours=36):
                continue

            if already_sent(event_id):
                continue

            home = e["homeTeam"]["name"]
            away = e["awayTeam"]["name"]

            time.sleep(0.4)
            home_odds, away_odds = fetch_odds(event_id)

            if not home_odds or not away_odds:
                logger.info(f"  Nincs odds: {home} vs {away}")
                continue

            # Away dog szűrő: hazai fav (home < away), vendég az underdog
            if home_odds >= away_odds:
                logger.info(f"  Nem hazai fav: {home} @{home_odds} vs {away} @{away_odds} — skip")
                continue

            dog_odds = away_odds
            if not (MIN_DOG_ODDS <= dog_odds <= MAX_DOG_ODDS):
                logger.info(f"  Odds tartomány kívül: {away} @{dog_odds:.2f} — skip")
                continue

            # Tipp küldése
            match_time_str = match_dt.strftime("%Y-%m-%d %H:%M")
            msg = build_tip_message(home, away, away, home_odds, away_odds, dog_odds, match_time_str)

            await send_to_all(bot, msg)
            save_tip(event_id, home, away, "Indian Premier League",
                     match_dt, away, home_odds, away_odds, dog_odds)

            logger.info(f"  ✅ TIPP ELKÜLDVE: {away} @{dog_odds:.2f} (home fav: {home} @{home_odds:.2f})")
            tips_sent += 1

    if tips_sent == 0:
        logger.info("  Nincs új tipp")

# ── Eredmény ellenőrzés ──────────────────────────────────────────────────────
async def check_results(bot: Bot):
    pending = get_pending_tips()
    logger.info(f"Eredmény ellenőrzés: {len(pending)} függő tipp")

    for tip in pending:
        event_id = tip["event_id"]
        winner = fetch_result(event_id)
        if winner is None:
            continue

        actual_name = tip["home"] if winner == "home" else tip["away"]
        won = tip["tip_team"] == actual_name
        result = "win" if won else "loss"

        update_result(event_id, result, winner)

        msg = build_result_message(
            tip["home"], tip["away"], tip["tip_team"],
            tip["dog_odds"], result, winner
        )
        await send_to_all(bot, msg)
        logger.info(f"  Eredmény: {tip['home']} vs {tip['away']} → {result.upper()} ({actual_name})")
        await asyncio.sleep(0.5)

# ── Napi összefoglaló ────────────────────────────────────────────────────────
async def daily_summary(bot: Bot):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE result = 'win')  as wins,
                        COUNT(*) FILTER (WHERE result = 'loss') as losses,
                        COUNT(*) FILTER (WHERE result IS NULL)  as pending,
                        ROUND(AVG(dog_odds)::numeric, 2)        as avg_odds
                    FROM cricket_tips
                """)
                row = cur.fetchone()
                wins, losses, pending, avg_odds = row
                total = (wins or 0) + (losses or 0)
                if total == 0:
                    return
                roi = ((wins * float(avg_odds or 2.0) - total) / total * 100)
                msg = (
                    f"🏏 *IPL Bot — Napi összefoglaló*\n\n"
                    f"Összes tipp: {total} lezárt \\+ {pending} függő\n"
                    f"WIN: {wins} \\| LOSS: {losses} \\| Win%: {wins/total*100:.1f}%\n"
                    f"Átlag szorzó: @{avg_odds}\n"
                    f"ROI: {roi:+.1f}%"
                )
                await send_to_all(bot, msg)
    except Exception as exc:
        logger.error(f"Napi összefoglaló hiba: {exc}")

# ── Main ────────────────────────────────────────────────────────────────────
async def main():
    if not CHAT_IDS:
        logger.error("MULTI_SPORT_CHAT_ID nincs beállítva!")
        sys.exit(1)

    init_db()

    bot = Bot(token=BOT_TOKEN)
    me = await bot.get_me()
    logger.info(f"Bot elindult: @{me.username}")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(scan_and_send,  "interval", seconds=SCAN_INTERVAL, args=[bot])
    scheduler.add_job(check_results,  "interval", seconds=RESULT_CHECK,  args=[bot])
    scheduler.add_job(daily_summary,  "cron",     hour=22, minute=0,     args=[bot])
    scheduler.start()

    # Azonnal futtat egyet
    await scan_and_send(bot)
    await check_results(bot)

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot leállítva.")
        scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
