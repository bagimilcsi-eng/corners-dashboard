#!/usr/bin/env python3
"""
BTTS Bot – Both Teams to Score (Mindkét csapat gól)
- SofaScore kaparás (ingyenes, nincs API kulcs)
- Hazai csapat utolsó 10 hazai meccse + vendég csapat utolsó 10 vendég meccse
- BTTS YES szűrő: min. 62% kombinált arány
- Min. szorzó: 1.55 | Max. napi tipp: 4
- Token: COUPON_BOT_TOKEN | Chat: COUPON_CHAT_ID (ugyanaz mint Football 25)
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
from datetime import datetime, date, timedelta
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

try:
    from zoneinfo import ZoneInfo
except ImportError:
    try:
        from backports.zoneinfo import ZoneInfo
    except ImportError:
        import pytz
        class ZoneInfo:
            def __new__(cls, key):
                return pytz.timezone(key)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    format="%(asctime)s - btts_bot - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("btts_bot")

# ─── Konfiguráció ─────────────────────────────────────────────────────────────

BOT_TOKEN    = os.environ["COUPON_BOT_TOKEN"]
ADMIN_CHAT   = os.environ.get("COUPON_CHAT_ID", "")
DATABASE_URL = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
HU_TZ        = ZoneInfo("Europe/Budapest")

SOFASCORE_BASE = "https://www.sofascore.com/api/v1"

SOFA_HEADERS = {
    "User-Agent":     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":         "application/json",
    "Referer":        "https://www.sofascore.com/",
    "Accept-Language":"en-US,en;q=0.9",
    "Origin":         "https://www.sofascore.com",
}

CHAT_IDS         = [6617439213, -1003802326194, -1003835559510]

MIN_ODDS         = 1.55
BTTS_THRESHOLD   = 0.62   # min. kombinált BTTS arány
MIN_FORM_MATCHES = 6      # legalább ennyi befejezett mérkőzés kell
MAX_DAILY_TIPS   = 4      # napi maximum tipp

# Ligák amikre figyelünk (SofaScore tournament ID-k — 0 = minden liga)
LEAGUE_WHITELIST: set[int] = set()   # üres = minden liga engedélyezett


# ─── DB ───────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)


def init_db():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS btts_tips (
                fixture_id       BIGINT PRIMARY KEY,
                home             TEXT NOT NULL,
                away             TEXT NOT NULL,
                league           TEXT NOT NULL,
                league_id        INTEGER,
                country          TEXT,
                match_time       TIMESTAMPTZ NOT NULL,
                odds             FLOAT NOT NULL,
                home_btts_rate   FLOAT,
                away_btts_rate   FLOAT,
                confidence       FLOAT,
                result           TEXT,
                actual_home_goals INTEGER,
                actual_away_goals INTEGER,
                sent_at          TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    conn.commit()
    conn.close()
    logger.info("btts_tips tábla kész")


def save_tip(data: dict) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO btts_tips
                    (fixture_id, home, away, league, league_id, country,
                     match_time, odds, home_btts_rate, away_btts_rate, confidence)
                VALUES
                    (%(fixture_id)s, %(home)s, %(away)s, %(league)s, %(league_id)s, %(country)s,
                     %(match_time)s, %(odds)s, %(home_btts_rate)s, %(away_btts_rate)s, %(confidence)s)
                ON CONFLICT (fixture_id) DO NOTHING
            """, data)
            saved = cur.rowcount > 0
        conn.commit()
        return saved
    finally:
        conn.close()


def load_sent_ids() -> set:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT fixture_id FROM btts_tips")
        return {r[0] for r in cur.fetchall()}
    conn.close()


def load_pending_tips() -> list:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM btts_tips WHERE result IS NULL ORDER BY match_time"
        )
        return [dict(r) for r in cur.fetchall()]
    conn.close()


def update_result(fixture_id: int, result: str, home_goals: int, away_goals: int):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE btts_tips SET result=%s, actual_home_goals=%s, actual_away_goals=%s WHERE fixture_id=%s",
            (result, home_goals, away_goals, fixture_id),
        )
    conn.commit()
    conn.close()


def count_today_tips() -> int:
    today = date.today()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM btts_tips WHERE sent_at::date = %s",
            (today,),
        )
        return cur.fetchone()[0]
    conn.close()


# ─── SofaScore API ────────────────────────────────────────────────────────────

def sofa_get(url: str) -> dict:
    try:
        r = requests.get(url, headers=SOFA_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"sofa_get hiba: {url} → {e}")
    return {}


def _parse_odds(val) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val))
    except Exception:
        return None


def fetch_upcoming_fixtures() -> list:
    events = []
    for delta in range(0, 2):   # ma + holnap
        d = (date.today() + timedelta(days=delta)).strftime("%Y-%m-%d")
        data = sofa_get(f"{SOFASCORE_BASE}/sport/football/scheduled-events/{d}")
        for ev in data.get("events", []):
            st = (ev.get("status") or {}).get("type", "")
            if st not in ("notstarted",):
                continue
            ts = ev.get("startTimestamp", 0)
            if ts - time.time() < 3600:        # legalább 1 óra van még
                continue
            if ts - time.time() > 36 * 3600:   # max 36 óra előre
                continue
            events.append(ev)
    return events


def fetch_team_form(team_id: int, is_home: bool, last: int = 10) -> list:
    data = sofa_get(f"{SOFASCORE_BASE}/team/{team_id}/events/last/0")
    finished = [
        e for e in data.get("events", [])
        if (e.get("status") or {}).get("type") == "finished"
    ]
    filtered = [
        e for e in finished
        if (is_home     and (e.get("homeTeam") or {}).get("id") == team_id)
        or (not is_home and (e.get("awayTeam") or {}).get("id") == team_id)
    ]
    pool = filtered if len(filtered) >= MIN_FORM_MATCHES else finished
    return pool[:last]


def fetch_btts_odds(event_id: int) -> float | None:
    """BTTS YES szorzó SofaScore-ból."""
    data    = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/odds/1/all")
    markets = data.get("markets") or []
    for mkt in markets:
        name = (mkt.get("marketName") or mkt.get("name") or "").lower()
        if not any(kw in name for kw in [
            "both teams to score", "btts", "gg/ng", "goal/goal",
            "both teams score", "mindkét csapat", "gg"
        ]):
            continue
        choices = mkt.get("choices") or []
        yes_ch  = next(
            (c for c in choices if (c.get("name") or "").lower() in ("yes", "igen", "gg", "si")),
            None,
        )
        if not yes_ch:
            # ha csak 2 choice van, az első általában a YES
            if len(choices) == 2:
                yes_ch = choices[0]
        if yes_ch:
            odds = _parse_odds(yes_ch.get("fractionalValue"))
            if odds:
                return odds
    return None


def fetch_fixture_result(event_id: int) -> dict | None:
    data  = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}")
    event = data.get("event", {})
    if (event.get("status") or {}).get("type") != "finished":
        return None
    hs  = (event.get("homeScore") or {}).get("current")
    as_ = (event.get("awayScore") or {}).get("current")
    if hs is None or as_ is None:
        return None
    return {"home_goals": hs, "away_goals": as_}


# ─── BTTS statisztika ─────────────────────────────────────────────────────────

def _parse_btts_stats(matches: list, team_id: int) -> dict:
    """
    Megadja, hogy a csapat meccsein hány %-ban lőtt mindkét csapat legalább 1 gólt.
    """
    btts = count = 0
    for m in matches:
        hs  = (m.get("homeScore") or {}).get("current")
        as_ = (m.get("awayScore") or {}).get("current")
        if hs is None or as_ is None:
            continue
        count += 1
        if hs >= 1 and as_ >= 1:
            btts += 1
    if count == 0:
        return {"btts_rate": None, "count": 0}
    return {"btts_rate": btts / count, "count": count}


def calculate_btts(home_stats: dict, away_stats: dict, odds: float) -> dict | None:
    home_rate = home_stats.get("btts_rate")
    away_rate = away_stats.get("btts_rate")

    if home_rate is None or away_rate is None:
        return None
    if home_stats["count"] < MIN_FORM_MATCHES or away_stats["count"] < MIN_FORM_MATCHES:
        return None

    combined = home_rate * 0.5 + away_rate * 0.5

    if combined < BTTS_THRESHOLD:
        return None

    if odds < MIN_ODDS:
        return None

    confidence = round(combined * 100, 1)
    return {
        "odds":           odds,
        "home_btts_rate": round(home_rate * 100, 1),
        "away_btts_rate": round(away_rate * 100, 1),
        "confidence":     confidence,
    }


# ─── Tipp gyűjtés ─────────────────────────────────────────────────────────────

def _collect_tips_sync(sent_ids: set) -> list:
    today_count = count_today_tips()
    remaining   = MAX_DAILY_TIPS - today_count
    if remaining <= 0:
        logger.info(f"Napi limit elérve ({MAX_DAILY_TIPS} tipp) — scan kihagyva")
        return []

    fixtures = fetch_upcoming_fixtures()
    logger.info(f"Közelgő meccsek: {len(fixtures)}")

    candidates = []
    for ev in fixtures:
        event_id = ev.get("id")
        if not event_id or event_id in sent_ids:
            continue

        tourn     = ev.get("tournament") or {}
        league_id = tourn.get("id")
        if LEAGUE_WHITELIST and league_id not in LEAGUE_WHITELIST:
            continue

        home_team = ev.get("homeTeam") or {}
        away_team = ev.get("awayTeam") or {}
        home_id   = home_team.get("id")
        away_id   = away_team.get("id")
        if not home_id or not away_id:
            continue

        odds = fetch_btts_odds(event_id)
        if not odds:
            logger.debug(f"Nincs BTTS odds: {home_team.get('name')} vs {away_team.get('name')}")
            time.sleep(0.3)
            continue

        home_form  = fetch_team_form(home_id, is_home=True)
        away_form  = fetch_team_form(away_id, is_home=False)
        home_stats = _parse_btts_stats(home_form, home_id)
        away_stats = _parse_btts_stats(away_form, away_id)

        tip = calculate_btts(home_stats, away_stats, odds)

        if not tip:
            logger.info(
                f"Kizárva: {home_team.get('name')} vs {away_team.get('name')} "
                f"(hazai BTTS={home_stats.get('btts_rate', 'N/A'):.0%} "
                f"vendég={away_stats.get('btts_rate', 'N/A'):.0%} odds={odds})"
                if home_stats.get("btts_rate") is not None and away_stats.get("btts_rate") is not None
                else f"Kizárva: {home_team.get('name')} vs {away_team.get('name')} (kevés adat)"
            )
            time.sleep(0.3)
            continue

        ts = ev.get("startTimestamp", 0)
        candidates.append({
            "event_id":       event_id,
            "home":           home_team.get("name", "?"),
            "away":           away_team.get("name", "?"),
            "league":         tourn.get("name", "?"),
            "league_id":      league_id,
            "country":        (tourn.get("category") or {}).get("name", ""),
            "match_time":     datetime.utcfromtimestamp(ts).replace(tzinfo=None),
            **tip,
        })
        time.sleep(0.3)

    # Confidence szerint sorba rendezve, top N
    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    return candidates[:remaining]


# ─── Telegram üzenetek ────────────────────────────────────────────────────────

def build_message(tip: dict) -> str:
    mt = datetime.utcfromtimestamp(
        tip["match_time"].timestamp() if hasattr(tip["match_time"], "timestamp")
        else tip["match_time"]
    ) + timedelta(hours=2)   # UTC → CET
    mt_str = mt.strftime("%Y.%m.%d %H:%M")

    return (
        f"⚽ *BTTS – Mindkét csapat gól (IGEN)*\n\n"
        f"🏆 {tip['league']}"
        + (f" ({tip['country']})" if tip.get("country") else "") + "\n"
        f"🆚 *{tip['home']} – {tip['away']}*\n"
        f"🕐 {mt_str}\n\n"
        f"📊 Hazai BTTS: {tip['home_btts_rate']}% | Vendég BTTS: {tip['away_btts_rate']}%\n"
        f"💡 Kombinált: {tip['confidence']}%\n"
        f"💰 Szorzó: *{tip['odds']:.2f}*\n\n"
        f"✅ _Tipp: Mindkét csapat szerez legalább 1 gólt_"
    )


async def send_to_all_chats(bot, text: str):
    for chat_id in CHAT_IDS:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"Küldési hiba (chat_id={chat_id}): {e}")


async def send_admin(bot, text: str):
    if not ADMIN_CHAT:
        return
    try:
        await bot.send_message(chat_id=ADMIN_CHAT, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Admin üzenet hiba: {e}")


# ─── Scan és eredmény ─────────────────────────────────────────────────────────

async def scan_and_send(context):
    app  = context.application
    loop = asyncio.get_event_loop()

    sent_ids   = await loop.run_in_executor(None, load_sent_ids)
    candidates = await loop.run_in_executor(None, _collect_tips_sync, sent_ids)

    if not candidates:
        logger.info("Nincs BTTS tipp ebben a körben")
        return

    for tip in candidates:
        db_data = {
            "fixture_id":      tip["event_id"],
            "home":            tip["home"],
            "away":            tip["away"],
            "league":          tip["league"],
            "league_id":       tip.get("league_id"),
            "country":         tip.get("country", ""),
            "match_time":      tip["match_time"],
            "odds":            tip["odds"],
            "home_btts_rate":  tip["home_btts_rate"] / 100,
            "away_btts_rate":  tip["away_btts_rate"] / 100,
            "confidence":      tip["confidence"],
        }
        saved = await loop.run_in_executor(None, save_tip, db_data)
        if not saved:
            continue
        msg = build_message(tip)
        await send_to_all_chats(app.bot, msg)
        logger.info(f"Tipp elküldve: {tip['home']} vs {tip['away']} | odds={tip['odds']} | conf={tip['confidence']}%")


def _check_results_sync() -> list:
    pending = load_pending_tips()
    updates = []
    for tip in pending:
        mt = tip["match_time"]
        if hasattr(mt, "timestamp"):
            ts = mt.timestamp()
        else:
            ts = mt
        if time.time() < ts + 105 * 60:   # 105 perccel a meccs után
            continue
        res = fetch_fixture_result(tip["fixture_id"])
        if not res:
            continue
        hg = res["home_goals"]
        ag = res["away_goals"]
        result = "WIN" if hg >= 1 and ag >= 1 else "LOSS"
        update_result(tip["fixture_id"], result, hg, ag)
        updates.append({**tip, "result": result, "hg": hg, "ag": ag})
        time.sleep(0.3)
    return updates


async def check_results(context):
    app  = context.application
    loop = asyncio.get_event_loop()
    updates = await loop.run_in_executor(None, _check_results_sync)
    for upd in updates:
        icon = "✅" if upd["result"] == "WIN" else "❌"
        msg  = (
            f"{icon} *BTTS Eredmény*\n\n"
            f"🆚 {upd['home']} – {upd['away']}\n"
            f"🏆 {upd['league']}\n"
            f"⚽ Végeredmény: {upd['hg']} – {upd['ag']}\n"
            f"{'✅ Mindkét csapat szerzett gólt – NYERT' if upd['result'] == 'WIN' else '❌ Nem szerezett mindkét csapat gólt – VESZÍTETT'}\n"
            f"💰 Szorzó volt: {upd['odds']:.2f}"
        )
        await send_to_all_chats(app.bot, msg)
        logger.info(f"Eredmény: {upd['home']} vs {upd['away']} → {upd['result']} ({upd['hg']}-{upd['ag']})")


# ─── Telegram parancsok ───────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⚽ *BTTS Bot – Mindkét csapat gól*\n\n"
        "Automatikusan keres BTTS YES tippeket futball meccsekre.\n\n"
        "📌 *Stratégia:* hazai és vendég BTTS arány elemzés\n"
        f"🎯 *Küszöb:* ≥{int(BTTS_THRESHOLD*100)}% kombinált arány\n"
        f"💰 *Min. odds:* {MIN_ODDS}\n"
        f"📊 *Max napi tipp:* {MAX_DAILY_TIPS}\n\n"
        "Parancsok: /tippek /stat"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_tippek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = load_pending_tips()
    if not pending:
        await update.message.reply_text("Nincs aktív BTTS tipp.")
        return
    lines = ["⚽ *Aktív BTTS tippek:*\n"]
    for t in pending:
        mt = t["match_time"]
        if hasattr(mt, "strftime"):
            mt_str = (mt + timedelta(hours=2)).strftime("%m.%d %H:%M")
        else:
            mt_str = "?"
        lines.append(f"• {t['home']} – {t['away']} | {mt_str} | @{t['odds']:.2f}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE result IS NOT NULL) AS total,
                COUNT(*) FILTER (WHERE result = 'WIN')     AS wins,
                COUNT(*) FILTER (WHERE result = 'LOSS')    AS losses,
                COUNT(*) FILTER (WHERE result IS NULL)     AS pending,
                ROUND(AVG(odds)::numeric, 2)               AS avg_odds
            FROM btts_tips
        """)
        row = cur.fetchone()
    conn.close()

    total, wins, losses, pending, avg_odds = row
    roi = 0.0
    if total and total > 0:
        roi = ((wins * (avg_odds or 0) - total) / total) * 100

    text = (
        f"📊 *BTTS Bot Statisztika*\n\n"
        f"Lezárt tippek: {total}\n"
        f"✅ Nyert: {wins} | ❌ Veszített: {losses}\n"
        f"⏳ Függőben: {pending}\n"
        f"Win%: {wins/total*100:.1f}%" if total else "Nincs adat még."
    )
    if total:
        text += f"\nÁtlag szorzó: {avg_odds}\nROI: {roi:+.1f}%"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── Main ─────────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    init_db()

    # Startup üzenet
    try:
        await app.bot.send_message(
            chat_id=ADMIN_CHAT,
            text=(
                "⚽ *BTTS Bot aktív*\n\n"
                f"Mindkét csapat gól tippek | min. odds {MIN_ODDS} | max. {MAX_DAILY_TIPS} tipp/nap\n"
                "Scan: 08:05, 10:05, 12:05, 14:05, 16:05, 18:05, 20:05"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(f"Startup üzenet hiba: {e}")

    scheduler = AsyncIOScheduler(timezone="Europe/Budapest")

    # Scan: naponta 7×, páratlan órákon (mint a többi bot)
    scheduler.add_job(
        scan_and_send, "cron",
        hour="8,10,12,14,16,18,20",
        minute=5,
        args=[app],
        id="scan",
    )

    # Eredmény ellenőrzés: 20 percenként
    scheduler.add_job(
        check_results, "interval",
        minutes=20,
        args=[app],
        id="results",
    )

    # Startup scan 3 perc múlva
    from zoneinfo import ZoneInfo as _ZI
    _tz = _ZI("Europe/Budapest")
    scheduler.add_job(
        scan_and_send, "date",
        run_date=datetime.now(_tz) + timedelta(minutes=3),
        args=[app],
        id="startup_scan",
    )

    scheduler.start()
    logger.info(f"BTTS Bot elindult ✅ (min_odds={MIN_ODDS}, threshold={BTTS_THRESHOLD:.0%}, max_daily={MAX_DAILY_TIPS})")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("tippek",  cmd_tippek))
    app.add_handler(CommandHandler("stat",    cmd_stat))
    app.add_handler(CommandHandler("help",    cmd_start))

    logger.info("BTTS Bot polling indul...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
