#!/usr/bin/env python3
"""
Football 2.5 Over/Under Bot
- SofaScore kaparás (nincs API kulcs, nincs limit)
- H2H utolsó 7 meccs; hazai csapat utolsó 10 hazai; vendég utolsó 10 vendég
- Félidei gól szűrő, konszenzus szűrő, liga whitelist
- Min. szorzó: 1.55
- Token: COUPON_BOT_TOKEN | Chat: COUPON_CHAT_ID
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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Konfiguráció ─────────────────────────────────────────────────────────────

BOT_TOKEN    = os.environ["COUPON_BOT_TOKEN"]
ADMIN_CHAT   = os.environ.get("COUPON_CHAT_ID", "")
DATABASE_URL = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
HU_TZ        = ZoneInfo("Europe/Budapest")

import os as _os
SOFASCORE_BASE = (
    "https://www.sofascore.com/api/v1"
    if _os.environ.get("REPL_ID")
    else "https://814dfd73-d8dd-4560-ab7a-2dea4ca2da33-00-3j0ryo8vfet2i.janeway.replit.dev/api/sofa"
)
SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":    "https://www.sofascore.com/",
    "Accept":     "application/json, text/plain, */*",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin":     "https://www.sofascore.com",
    "sec-ch-ua":  '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Csoportok ahova a tippek mennek
CHAT_IDS = [6617439213, -1003802326194, -1003835559510]

# Szűrő paraméterek
MIN_ODDS         = 1.55
MIN_H2H_MATCHES  = 5       # SofaScore H2H általában rövidebb múltat ad vissza
MIN_FORM_MATCHES = 5
OVER_THRESHOLD   = 0.62
UNDER_THRESHOLD  = 0.38
HT_OVER_MIN      = 0.35
HT_UNDER_MAX     = 0.58
CONSENSUS_MIN    = 2
CONSENSUS_THRESH = 0.55
SCAN_INTERVAL    = 1800
RESULT_CHECK_MIN = 105
API_DELAY        = 0.45
HORIZON_HOURS    = 12

# Engedélyezett kategóriák (SofaScore tournament.category.name)
ALLOWED_CATEGORIES: set[str] = {
    "England", "Spain", "Germany", "France", "Italy",
    "Netherlands", "Portugal", "Belgium", "Scotland", "Turkey",
    "Greece", "Austria", "Switzerland", "Russia",
    "Denmark", "Sweden", "Norway", "Czech Republic",
    "USA", "Brazil", "Argentina", "Saudi Arabia",
    "International",   # UEFA BL/EL/UECL
    "Europe",
}

# ─── Adatbázis ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    sql = """
    CREATE TABLE IF NOT EXISTS football25_tips (
        fixture_id       BIGINT PRIMARY KEY,
        home             TEXT NOT NULL,
        away             TEXT NOT NULL,
        league           TEXT NOT NULL,
        league_id        INTEGER,
        country          TEXT,
        start_timestamp  BIGINT NOT NULL,
        tip              TEXT NOT NULL,
        line             REAL DEFAULT 2.5,
        odds             REAL,
        bookmaker_count  INTEGER,
        h2h_over_rate    REAL,
        home_over_rate   REAL,
        away_over_rate   REAL,
        combined_score   REAL,
        ht_goal_rate     REAL,
        sent_at          BIGINT NOT NULL,
        result           TEXT DEFAULT NULL,
        actual_goals     INTEGER DEFAULT NULL
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


def save_tip(data: dict) -> bool:
    sql = """
    INSERT INTO football25_tips
        (fixture_id, home, away, league, league_id, country,
         start_timestamp, tip, line, odds, bookmaker_count,
         h2h_over_rate, home_over_rate, away_over_rate,
         combined_score, ht_goal_rate, sent_at)
    VALUES
        (%(fixture_id)s, %(home)s, %(away)s, %(league)s, %(league_id)s, %(country)s,
         %(start_timestamp)s, %(tip)s, %(line)s, %(odds)s, %(bookmaker_count)s,
         %(h2h_over_rate)s, %(home_over_rate)s, %(away_over_rate)s,
         %(combined_score)s, %(ht_goal_rate)s, %(sent_at)s)
    ON CONFLICT (fixture_id) DO NOTHING
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, data)
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"DB mentés hiba: {e}")
        return False


def load_pending_tips() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM football25_tips WHERE result IS NULL ORDER BY start_timestamp"
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"DB lekérés hiba: {e}")
        return []


def load_sent_ids() -> set:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT fixture_id FROM football25_tips")
                return {r[0] for r in cur.fetchall()}
    except Exception:
        return set()


def update_result(fixture_id: int, result: str, actual_goals: int):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE football25_tips SET result=%s, actual_goals=%s WHERE fixture_id=%s",
                    (result, actual_goals, fixture_id)
                )
    except Exception as e:
        logger.error(f"DB frissítés hiba: {e}")


# ─── SofaScore hívások ────────────────────────────────────────────────────────

def sofa_get(url: str) -> dict:
    try:
        time.sleep(API_DELAY)
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug(f"SofaScore hiba ({url}): {e}")
        return {}


def _parse_odds(val) -> float | None:
    if not val:
        return None
    s = str(val).strip()
    if "/" in s:
        try:
            n, d = s.split("/")
            v = float(n) / float(d) + 1.0
            return round(v, 2) if v >= 1.01 else None
        except Exception:
            return None
    try:
        v = float(s)
        return round(v, 2) if v >= 1.01 else None
    except Exception:
        return None


def fetch_upcoming_fixtures() -> list:
    """Mai + holnapi meccsek, következő HORIZON_HOURS órán belül."""
    now_ts  = int(datetime.utcnow().timestamp())
    horizon = now_ts + HORIZON_HOURS * 3600
    results = []
    for day_offset in [0, 1]:
        d    = (date.today() + timedelta(days=day_offset)).isoformat()
        data = sofa_get(f"{SOFASCORE_BASE}/sport/football/scheduled-events/{d}")
        for ev in data.get("events", []):
            ts     = ev.get("startTimestamp", 0)
            status = ev.get("status", {}).get("type", "")
            if status in ("notstarted",) and now_ts <= ts <= horizon:
                results.append(ev)
    logger.info(f"Közelgő meccsek (SofaScore): {len(results)} a következő {HORIZON_HOURS}h-ban")
    return results


def fetch_h2h(event_id: int) -> list:
    """Utolsó 7 befejezett H2H meccs."""
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/h2h/events")
    finished = [
        e for e in data.get("events", [])
        if e.get("status", {}).get("type") == "finished"
    ]
    return finished[:7]


def fetch_team_form(team_id: int, is_home: bool, last: int = 10) -> list:
    """Csapat utolsó N hazai/vendég befejezett meccse."""
    data = sofa_get(f"{SOFASCORE_BASE}/team/{team_id}/events/last/0")
    finished = [
        e for e in data.get("events", [])
        if e.get("status", {}).get("type") == "finished"
    ]
    # H/A szplit
    filtered = [
        e for e in finished
        if (is_home  and e.get("homeTeam", {}).get("id") == team_id)
        or (not is_home and e.get("awayTeam", {}).get("id") == team_id)
    ]
    # Ha nincs elég H/A meccs, az összes befejezett meccsből vesszük
    pool = filtered if len(filtered) >= MIN_FORM_MATCHES else finished
    return pool[:last]


def fetch_odds(event_id: int) -> dict | None:
    """O/U 2.5 szorzók SofaScore-ból."""
    data    = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/odds/1/all")
    markets = data.get("markets") or []
    for mkt in markets:
        name = (mkt.get("marketName") or mkt.get("name") or "").lower()
        if not any(kw in name for kw in ["goals over/under", "total goals", "over/under"]):
            continue
        raw_hc = (mkt.get("choiceGroup") or mkt.get("handicap") or "")
        try:
            line = float(str(raw_hc))
        except Exception:
            line = None
        if line != 2.5:
            continue
        choices  = mkt.get("choices") or []
        ov_ch    = next((c for c in choices if (c.get("name") or "").lower().startswith("over")),  None)
        un_ch    = next((c for c in choices if (c.get("name") or "").lower().startswith("under")), None)
        if not ov_ch or not un_ch:
            continue
        over_odds  = _parse_odds(ov_ch.get("fractionalValue"))
        under_odds = _parse_odds(un_ch.get("fractionalValue"))
        if over_odds and under_odds:
            return {"over": over_odds, "under": under_odds, "bookmaker_count": 1}
    return None


def fetch_fixture_result(event_id: int) -> dict | None:
    """Lezárt meccs végeredménye."""
    data  = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}")
    event = data.get("event", {})
    if event.get("status", {}).get("type") != "finished":
        return None
    hs = event.get("homeScore", {}).get("current")
    as_ = event.get("awayScore", {}).get("current")
    if hs is None or as_ is None:
        return None
    return {"total_goals": hs + as_}


# ─── Statisztika számítás ─────────────────────────────────────────────────────

def _parse_match_stats(matches: list) -> dict:
    """
    SofaScore meccsekből:
      over_rate  – mérkőzések aránya ahol >2.5 gól volt
      ht_rate    – félidőben volt-e gól (period1 összesen > 0)
      count      – felhasznált meccsek száma
    """
    over = ht_goal = count = 0
    for m in matches:
        hs  = m.get("homeScore", {}).get("current")
        as_ = m.get("awayScore", {}).get("current")
        if hs is None or as_ is None:
            continue
        count += 1
        if hs + as_ > 2.5:
            over += 1
        ht_h = m.get("homeScore", {}).get("period1")
        ht_a = m.get("awayScore", {}).get("period1")
        if ht_h is not None and ht_a is not None and (ht_h + ht_a) > 0:
            ht_goal += 1

    if count == 0:
        return {"over_rate": None, "ht_rate": None, "count": 0}
    return {
        "over_rate": over / count,
        "ht_rate":   ht_goal / count,
        "count":     count,
    }


def calculate_tip(h2h_stats: dict, home_stats: dict, away_stats: dict, odds: dict) -> dict | None:
    h2h_rate  = h2h_stats.get("over_rate")
    home_rate = home_stats.get("over_rate")
    away_rate = away_stats.get("over_rate")

    rates = [r for r in [h2h_rate, home_rate, away_rate] if r is not None]
    if len(rates) < 2:
        return None

    weights = []
    weighted_sum = 0.0
    if h2h_rate is not None:
        weights.append(0.40); weighted_sum += 0.40 * h2h_rate
    if home_rate is not None:
        weights.append(0.30); weighted_sum += 0.30 * home_rate
    if away_rate is not None:
        weights.append(0.30); weighted_sum += 0.30 * away_rate
    combined = weighted_sum / sum(weights)

    ht_rates = [s.get("ht_rate") for s in [h2h_stats, home_stats, away_stats] if s.get("ht_rate") is not None]
    ht_rate  = sum(ht_rates) / len(ht_rates) if ht_rates else None

    if combined >= OVER_THRESHOLD:
        tip      = "over"
        tip_odds = odds["over"]
    elif combined <= UNDER_THRESHOLD:
        tip      = "under"
        tip_odds = odds["under"]
    else:
        return None

    all_rates = {"h2h": h2h_rate, "home": home_rate, "away": away_rate}
    available = {k: v for k, v in all_rates.items() if v is not None}
    if tip == "over":
        agreeing = sum(1 for v in available.values() if v >= CONSENSUS_THRESH)
    else:
        agreeing = sum(1 for v in available.values() if v <= (1 - CONSENSUS_THRESH))
    if agreeing < CONSENSUS_MIN:
        return None

    if tip_odds < MIN_ODDS:
        return None

    if ht_rate is not None:
        if tip == "over"  and ht_rate < HT_OVER_MIN:
            return None
        if tip == "under" and ht_rate > HT_UNDER_MAX:
            return None

    return {
        "tip":       tip,
        "odds":      tip_odds,
        "combined":  round(combined,  4),
        "h2h_rate":  round(h2h_rate,  4) if h2h_rate  is not None else None,
        "home_rate": round(home_rate, 4) if home_rate is not None else None,
        "away_rate": round(away_rate, 4) if away_rate is not None else None,
        "ht_rate":   round(ht_rate,   4) if ht_rate   is not None else None,
    }


# ─── Üzenet formázás ──────────────────────────────────────────────────────────

def build_message(ev: dict, tip: dict, odds: dict) -> str:
    home    = ev.get("homeTeam", {}).get("name", "?")
    away    = ev.get("awayTeam", {}).get("name", "?")
    ts      = ev.get("startTimestamp", 0)
    dt      = datetime.fromtimestamp(ts, tz=HU_TZ).strftime("%m.%d. %H:%M") if ts else "?"
    tourn   = ev.get("tournament", {})
    lg_name = tourn.get("name", "?")
    country = tourn.get("category", {}).get("name", "?")

    direction = tip["tip"]
    icon      = "⬆️" if direction == "over" else "⬇️"
    label     = "OVER 2.5" if direction == "over" else "UNDER 2.5"
    ov_pct    = round((tip["combined"] if direction == "over" else 1 - tip["combined"]) * 100)

    h2h_str  = f"{round((tip['h2h_rate'] or 0)*100)}%"  if tip.get("h2h_rate")  is not None else "–"
    home_str = f"{round((tip['home_rate'] or 0)*100)}%" if tip.get("home_rate") is not None else "–"
    away_str = f"{round((tip['away_rate'] or 0)*100)}%" if tip.get("away_rate") is not None else "–"
    ht_str   = f"{round((tip['ht_rate'] or 0)*100)}%"   if tip.get("ht_rate")   is not None else "–"

    return (
        f"{icon} *{label} TIPP*\n\n"
        f"⚽ *{home}* vs *{away}*\n"
        f"🏆 {country} · {lg_name}\n"
        f"🕐 {dt}\n\n"
        f"📊 *Statisztika (>2.5 gól arány):*\n"
        f"  • H2H utolsó 7: *{h2h_str}*\n"
        f"  • {home} hazai: *{home_str}*\n"
        f"  • {away} vendég: *{away_str}*\n"
        f"  • Félidei gól arány: *{ht_str}*\n\n"
        f"🎯 Kombinált jel: *{ov_pct}%* a {label} irányba\n"
        f"💰 Szorzó: *@{tip['odds']:.2f}*\n\n"
        f"⚠️ _Statisztikai elemzésen alapul. Felelősen fogadj!_"
    )


# ─── Multi-chat küldés ────────────────────────────────────────────────────────

async def send_to_all_chats(bot, text: str):
    for chat_id in CHAT_IDS:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Küldési hiba (chat_id={chat_id}): {e}")


async def send_admin(bot, text: str):
    if not ADMIN_CHAT:
        return
    try:
        await bot.send_message(chat_id=ADMIN_CHAT, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Admin üzenet hiba: {e}")


# ─── Fő tipp szkennelő ───────────────────────────────────────────────────────

def _collect_tips_sync(sent_ids: set) -> list:
    fixtures = fetch_upcoming_fixtures()
    tips_to_send = []

    for ev in fixtures:
        event_id = ev.get("id")
        if not event_id or event_id in sent_ids:
            continue

        home_id  = ev.get("homeTeam", {}).get("id")
        away_id  = ev.get("awayTeam", {}).get("id")
        home_nm  = ev.get("homeTeam", {}).get("name", "?")
        away_nm  = ev.get("awayTeam", {}).get("name", "?")
        tourn    = ev.get("tournament", {})
        category = tourn.get("category", {}).get("name", "")
        ts       = ev.get("startTimestamp", 0)

        if not home_id or not away_id:
            continue

        # Liga szűrő
        if category not in ALLOWED_CATEGORIES:
            logger.debug(f"{home_nm} vs {away_nm} — kategória kizárva ({category})")
            continue

        # H2H
        h2h_matches = fetch_h2h(event_id)
        if len(h2h_matches) < MIN_H2H_MATCHES:
            logger.debug(f"{home_nm} vs {away_nm} — kevés H2H ({len(h2h_matches)}), kihagyva")
            continue

        h2h_stats = _parse_match_stats(h2h_matches)

        # Hazai forma
        home_matches = fetch_team_form(home_id, is_home=True,  last=10)
        home_stats   = _parse_match_stats(home_matches)
        if home_stats["count"] < MIN_FORM_MATCHES:
            logger.debug(f"{home_nm} — kevés hazai forma ({home_stats['count']}), kihagyva")
            continue

        # Vendég forma
        away_matches = fetch_team_form(away_id, is_home=False, last=10)
        away_stats   = _parse_match_stats(away_matches)
        if away_stats["count"] < MIN_FORM_MATCHES:
            logger.debug(f"{away_nm} — kevés vendég forma ({away_stats['count']}), kihagyva")
            continue

        # Szorzók
        odds = fetch_odds(event_id)
        if odds is None:
            logger.debug(f"{home_nm} vs {away_nm} — nincs O/U 2.5 szorzó, kihagyva")
            continue

        # Tipp számítás
        tip = calculate_tip(h2h_stats, home_stats, away_stats, odds)
        if tip is None:
            logger.debug(f"{home_nm} vs {away_nm} — nincs egyértelmű jel, kihagyva")
            continue

        msg  = build_message(ev, tip, odds)
        meta = {
            "fixture_id":      event_id,
            "home":            home_nm,
            "away":            away_nm,
            "league":          tourn.get("name", "?"),
            "league_id":       tourn.get("id"),
            "country":         category,
            "start_timestamp": ts,
            "tip":             tip["tip"],
            "line":            2.5,
            "odds":            tip["odds"],
            "bookmaker_count": odds.get("bookmaker_count", 1),
            "h2h_over_rate":   tip.get("h2h_rate"),
            "home_over_rate":  tip.get("home_rate"),
            "away_over_rate":  tip.get("away_rate"),
            "combined_score":  tip["combined"],
            "ht_goal_rate":    tip.get("ht_rate"),
            "sent_at":         int(datetime.utcnow().timestamp()),
        }
        tips_to_send.append((msg, meta))
        sent_ids.add(event_id)

    return tips_to_send


async def scan_and_send(context):
    sent_ids = await asyncio.to_thread(load_sent_ids)
    tips     = await asyncio.to_thread(_collect_tips_sync, sent_ids)

    for msg, meta in tips:
        try:
            if not save_tip(meta):
                logger.info(f"Duplikát kihagyva: fixture_id={meta['fixture_id']}")
                continue
            await send_admin(context.bot, msg)
            logger.info(f"Tipp elküldve: {meta['home']} vs {meta['away']} | {meta['tip'].upper()} 2.5 @ {meta['odds']}")
        except Exception as e:
            logger.error(f"Tipp küldési hiba: {e}")

    if tips:
        logger.info(f"Scan kész: {len(tips)} tipp elküldve.")
    else:
        logger.debug("Scan kész: nincs új tipp.")


# ─── Eredmény figyelő ─────────────────────────────────────────────────────────

def _check_results_sync() -> list:
    now_ts  = int(datetime.utcnow().timestamp())
    pending = load_pending_tips()
    notifs  = []

    for tip in pending:
        ts = tip.get("start_timestamp", 0)
        if ts + RESULT_CHECK_MIN * 60 > now_ts:
            continue

        res = fetch_fixture_result(tip["fixture_id"])
        if res is None:
            continue

        total_goals = res["total_goals"]
        predicted   = tip["tip"]
        won         = (predicted == "over"  and total_goals > 2.5) or \
                      (predicted == "under" and total_goals <= 2.5)
        result      = "win" if won else "loss"
        update_result(tip["fixture_id"], result, total_goals)

        icon       = "✅" if won else "❌"
        label      = "OVER 2.5" if predicted == "over" else "UNDER 2.5"
        result_txt = "NYERT" if won else "VESZETT"
        odds_txt   = f"{tip['odds']:.2f}" if tip.get("odds") else "N/A"
        msg = (
            f"{icon} *Eredmény — {result_txt}!*\n\n"
            f"⚽ {tip['home']} vs {tip['away']}\n"
            f"🏆 {tip.get('league', '?')}\n"
            f"🎯 Tippünk: *{label}*\n"
            f"🏁 Végeredmény: *{total_goals} gól*\n"
            f"💰 Szorzó: @{odds_txt}"
        )
        notifs.append(msg)

    return notifs


async def check_results(context):
    notifs = await asyncio.to_thread(_check_results_sync)
    for msg in notifs:
        try:
            await send_admin(context.bot, msg)
        except Exception as e:
            logger.error(f"Eredmény értesítő hiba: {e}")


# ─── Telegram parancsok ────────────────────────────────────────────────────────

async def cmd_tippek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM football25_tips ORDER BY sent_at DESC LIMIT 10")
                rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        await update.message.reply_text(f"❌ DB hiba: {e}")
        return

    if not rows:
        await update.message.reply_text("ℹ️ Még nincs tipp az adatbázisban.")
        return

    lines = ["📋 *Utolsó 10 foci 2.5 tipp:*\n"]
    for r in rows:
        dt      = datetime.fromtimestamp(r["start_timestamp"], tz=HU_TZ).strftime("%m.%d. %H:%M")
        tip_lbl = "OVER 2.5" if r["tip"] == "over" else "UNDER 2.5"
        res_ico = {"win": "✅", "loss": "❌"}.get(r.get("result") or "", "⏳")
        odds_s  = f"@{r['odds']:.2f}" if r.get("odds") else ""
        lines.append(f"{res_ico} {r['home']} vs {r['away']} — *{tip_lbl}* {odds_s} _{dt}_")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tip, result, odds FROM football25_tips WHERE result IS NOT NULL")
                rows = cur.fetchall()
    except Exception as e:
        await update.message.reply_text(f"❌ DB hiba: {e}")
        return

    if not rows:
        await update.message.reply_text("ℹ️ Még nincs lezárt tipp.")
        return

    total  = len(rows)
    wins   = sum(1 for r in rows if r[1] == "win")
    losses = total - wins
    wr     = wins / total * 100
    roi    = sum((r[2] - 1 if r[1] == "win" else -1) for r in rows) / total * 100
    avg_o  = sum(r[2] for r in rows if r[2]) / total

    over_rows  = [r for r in rows if r[0] == "over"]
    under_rows = [r for r in rows if r[0] == "under"]
    ow = sum(1 for r in over_rows  if r[1] == "win")
    uw = sum(1 for r in under_rows if r[1] == "win")

    msg = (
        f"📊 *Foci 2.5 O/U Statisztika*\n\n"
        f"📈 Összes tipp: {total}\n"
        f"✅ Nyert: {wins} ({wr:.1f}%)\n"
        f"❌ Veszett: {losses}\n"
        f"💹 ROI: {roi:+.1f}%\n"
        f"💰 Átlag szorzó: {avg_o:.2f}\n\n"
        f"⬆️ OVER: {len(over_rows)} tipp, {ow}W ({ow/max(len(over_rows),1)*100:.0f}%)\n"
        f"⬇️ UNDER: {len(under_rows)} tipp, {uw}W ({uw/max(len(under_rows),1)*100:.0f}%)"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# ─── Főprogram ────────────────────────────────────────────────────────────────

def main():
    init_db()
    logger.info("Football 2.5 O/U Bot indul...")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("tippek", cmd_tippek))
    app.add_handler(CommandHandler("stat",   cmd_stat))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(scan_and_send, "interval", seconds=SCAN_INTERVAL, args=[app])
    scheduler.add_job(check_results, "interval", seconds=3600,          args=[app])

    async def post_init(application):
        scheduler.start()
        logger.info(f"Football 2.5 O/U Bot fut. (SofaScore | min. odds {MIN_ODDS} | HT szűrő aktív)")
        await send_admin(application.bot, "🤖 *Football 2.5 O/U Bot elindult* (SofaScore)\nSzkennelés: 30 percenként")
        await asyncio.sleep(3)
        await scan_and_send_startup(application)

    async def scan_and_send_startup(application):
        sent_ids = await asyncio.to_thread(load_sent_ids)
        tips     = await asyncio.to_thread(_collect_tips_sync, sent_ids)
        for msg, meta in tips:
            if not save_tip(meta):
                continue
            await send_admin(application.bot, msg)
            logger.info(f"Startup tipp: {meta['home']} vs {meta['away']}")
        if not tips:
            await send_admin(application.bot, "ℹ️ Startup: nincs megfelelő tipp a következő 12 órában.")
        else:
            await send_admin(application.bot, f"✅ Startup: *{len(tips)} tipp* elküldve.")

    app.post_init = post_init
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
