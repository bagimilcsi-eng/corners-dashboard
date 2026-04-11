#!/usr/bin/env python3
"""
Football 2.5 Over/Under Bot
- API-Football (RapidAPI) adatforrás
- H2H: utolsó 7 meccs; hazai csapat utolsó 10 hazai; vendég utolsó 10 vendég meccs
- Félidei szűrő: HT gól ráta alapján
- Konszenzus szűrő: min. 2 forrásnak azonos irányt kell mutatnia
- Liga whitelist: csak megbízható, adatgazdag ligák
- Min. szorzó: 1.55 | Min. könyvjelző: 3 fogadóiroda
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

FOOTBALL_API_BASE = "https://api-football-v1.p.rapidapi.com/v3"
FOOTBALL_HEADERS  = {
    "x-rapidapi-key":  os.environ.get("SPORTS_API_KEY", ""),
    "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
}

# Csoportok ahova a tippek mennek
CHAT_IDS = [6617439213, -1003802326194, -1003835559510]

# Szűrő paraméterek
MIN_ODDS         = 1.55
MIN_BOOKMAKERS   = 3      # min. fogadóiroda az O/U 2.5 piacra
MIN_H2H_MATCHES  = 7      # minimum H2H meccs (megbízható statisztikához)
MIN_FORM_MATCHES = 5      # minimum forma meccs (hazai/vendég)
OVER_THRESHOLD   = 0.62   # combined over ráta → OVER tipp
UNDER_THRESHOLD  = 0.38   # combined over ráta → UNDER tipp
HT_OVER_MIN      = 0.35   # ha HT gól ráta < 35% → ne adj over tippet
HT_UNDER_MAX     = 0.58   # ha HT gól ráta > 58% → ne adj under tippet
CONSENSUS_MIN    = 2      # min. ennyi forrásnak kell azonos irányt mutatnia
CONSENSUS_THRESH = 0.55   # forrás "OVER irányú" ha over_rate >= ez az érték
SCAN_INTERVAL    = 1800   # 30 perc
RESULT_CHECK_MIN = 105    # meccs után ennyi perccel ellenőrzünk eredményt
API_DELAY        = 0.45   # másodperc API hívások között
HORIZON_HOURS    = 12     # ennyi órán belüli meccsekre tippelünk

# Liga whitelist — csak megbízható, adatgazdag bajnokságok
ALLOWED_LEAGUE_IDS: set[int] = {
    2,    # Champions League
    3,    # Europa League
    848,  # Conference League
    39,   # Premier League
    40,   # Championship
    41,   # League One
    135,  # Serie A
    136,  # Serie B
    78,   # Bundesliga
    79,   # 2. Bundesliga
    61,   # Ligue 1
    62,   # Ligue 2
    140,  # La Liga
    141,  # La Liga 2
    88,   # Eredivisie
    94,   # Primeira Liga
    144,  # Belgian Pro League
    179,  # Scottish Premiership
    203,  # Süper Lig
    197,  # Super League Greece
    218,  # Austrian Bundesliga
    235,  # Russian Premier League
    307,  # Saudi Pro League
    253,  # MLS
    71,   # Brasileirão Série A
    72,   # Brasileirão Série B
    128,  # Argentine Liga Profesional
    130,  # Argentine Primera Nacional
    383,  # Swiss Super League
    113,  # Allsvenskan
    119,  # Danish Superliga
    103,  # Eliteserien
}

# Aktuális szezon
_now = datetime.utcnow()
SEASON = _now.year - 1 if _now.month < 7 else _now.year

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


# ─── API-Football hívások ─────────────────────────────────────────────────────

def api_get(endpoint: str, params: dict = {}) -> dict:
    time.sleep(API_DELAY)
    try:
        url = f"{FOOTBALL_API_BASE}/{endpoint}"
        resp = requests.get(url, headers=FOOTBALL_HEADERS, params=params, timeout=12)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"API {endpoint} → HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"API hiba ({endpoint}): {e}")
    return {}


def fetch_upcoming_fixtures() -> list:
    """Mai + holnapi meccsek, következő HORIZON_HOURS órán belül."""
    now_ts  = int(datetime.utcnow().timestamp())
    horizon = now_ts + HORIZON_HOURS * 3600
    results = []
    for day_offset in [0, 1]:
        d = (date.today() + timedelta(days=day_offset)).isoformat()
        data = api_get("fixtures", {"date": d, "timezone": "UTC"})
        for fx in data.get("response", []):
            ts = fx.get("fixture", {}).get("timestamp", 0)
            status = fx.get("fixture", {}).get("status", {}).get("short", "")
            if status == "NS" and now_ts <= ts <= horizon:
                results.append(fx)
    return results


def fetch_h2h(h2h_key: str) -> list:
    """H2H: utolsó 7 meccs (befejezett)."""
    data = api_get("fixtures/headtohead", {"h2h": h2h_key, "last": 14, "status": "FT"})
    matches = data.get("response", [])
    finished = [
        m for m in matches
        if m.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")
    ]
    return finished[:7]


def fetch_team_form(team_id: int, venue: str, last: int = 10) -> list:
    """Csapat forma: utolsó N hazai/vendég meccs."""
    data = api_get("fixtures", {
        "team":   team_id,
        "season": SEASON,
        "venue":  venue,
        "last":   last,
        "status": "FT",
    })
    return data.get("response", [])


def fetch_odds(fixture_id: int) -> dict | None:
    """
    O/U 2.5 szorzók + könyvmérő lefedettség.
    Visszaad: {"over": float, "under": float, "bookmaker_count": int} vagy None
    """
    data = api_get("odds", {"fixture": fixture_id, "bet": 5})
    bookmakers = data.get("response", [{}])[0].get("bookmakers", []) if data.get("response") else []
    if not bookmakers:
        return None

    over_odds   = []
    under_odds  = []
    bk_count    = 0

    for bk in bookmakers:
        for bet in bk.get("bets", []):
            if bet.get("id") == 5 or "over" in bet.get("name", "").lower():
                values = bet.get("values", [])
                o = u = None
                for v in values:
                    val = v.get("value", "").lower()
                    try:
                        odd = float(v.get("odd", 0))
                    except (ValueError, TypeError):
                        continue
                    if odd <= 1:
                        continue
                    if "over 2.5" in val or val == "over":
                        o = odd
                    elif "under 2.5" in val or val == "under":
                        u = odd
                if o and u:
                    over_odds.append(o)
                    under_odds.append(u)
                    bk_count += 1
                break

    if bk_count < MIN_BOOKMAKERS:
        return None

    avg_over  = sum(over_odds)  / len(over_odds)
    avg_under = sum(under_odds) / len(under_odds)
    return {"over": round(avg_over, 3), "under": round(avg_under, 3), "bookmaker_count": bk_count}


def fetch_fixture_result(fixture_id: int) -> dict | None:
    """Lezárt meccs eredményének lekérése."""
    data = api_get("fixtures", {"id": fixture_id})
    resp = data.get("response", [])
    if not resp:
        return None
    fx = resp[0]
    status = fx.get("fixture", {}).get("status", {}).get("short", "")
    if status not in ("FT", "AET", "PEN"):
        return None
    goals = fx.get("goals", {})
    h = goals.get("home")
    a = goals.get("away")
    if h is None or a is None:
        return None
    return {"total_goals": h + a}


# ─── Statisztika számítás ─────────────────────────────────────────────────────

def _parse_match_stats(matches: list, team_id: int | None = None) -> dict:
    """
    Visszaad:
      over_rate   – mérkőzések aránya ahol >2.5 gól volt
      avg_goals   – átlag gól/meccs
      ht_rate     – első félidőben volt gól (≥1 gól) arányban
      count       – felhasznált meccsek száma
    """
    over = ht_goal = total_goals = count = 0
    for m in matches:
        goals  = m.get("goals", {})
        score  = m.get("score", {})
        hg     = goals.get("home")
        ag     = goals.get("away")
        if hg is None or ag is None:
            continue
        total = hg + ag
        total_goals += total
        count += 1
        if total > 2.5:
            over += 1
        ht = score.get("halftime", {})
        ht_h = ht.get("home")
        ht_a = ht.get("away")
        if ht_h is not None and ht_a is not None and (ht_h + ht_a) > 0:
            ht_goal += 1

    if count == 0:
        return {"over_rate": None, "avg_goals": None, "ht_rate": None, "count": 0}
    return {
        "over_rate": over / count,
        "avg_goals": total_goals / count,
        "ht_rate":   ht_goal / count,
        "count":     count,
    }


def calculate_tip(h2h_stats: dict, home_stats: dict, away_stats: dict, odds: dict) -> dict | None:
    """
    Tipp meghatározása. Visszaad tipp dict-et vagy None-t ha nincs elegendő adat/jel.
    """
    h2h_rate  = h2h_stats.get("over_rate")
    home_rate = home_stats.get("over_rate")
    away_rate = away_stats.get("over_rate")

    # Legalább 2 forrásból kell adat
    rates = [r for r in [h2h_rate, home_rate, away_rate] if r is not None]
    if len(rates) < 2:
        return None

    # Súlyozott kombinált ráta
    weights = []
    weighted_sum = 0.0
    if h2h_rate is not None:
        weights.append(0.40)
        weighted_sum += 0.40 * h2h_rate
    if home_rate is not None:
        weights.append(0.30)
        weighted_sum += 0.30 * home_rate
    if away_rate is not None:
        weights.append(0.30)
        weighted_sum += 0.30 * away_rate
    combined = weighted_sum / sum(weights)

    # HT gól ráta (kombináció)
    ht_rates = [s.get("ht_rate") for s in [h2h_stats, home_stats, away_stats] if s.get("ht_rate") is not None]
    ht_rate  = sum(ht_rates) / len(ht_rates) if ht_rates else None

    # Tipp irány
    if combined >= OVER_THRESHOLD:
        tip = "over"
        tip_odds = odds["over"]
    elif combined <= UNDER_THRESHOLD:
        tip = "under"
        tip_odds = odds["under"]
    else:
        return None

    # Konszenzus szűrő: min. 2 forrásnak azonos irányt kell mutatnia
    all_rates = {"h2h": h2h_rate, "home": home_rate, "away": away_rate}
    available = {k: v for k, v in all_rates.items() if v is not None}
    if tip == "over":
        agreeing = sum(1 for v in available.values() if v >= CONSENSUS_THRESH)
    else:
        agreeing = sum(1 for v in available.values() if v <= (1 - CONSENSUS_THRESH))
    if agreeing < CONSENSUS_MIN:
        return None

    # Szorzó szűrő
    if tip_odds < MIN_ODDS:
        return None

    # Félidei szűrő
    if ht_rate is not None:
        if tip == "over"  and ht_rate < HT_OVER_MIN:
            return None
        if tip == "under" and ht_rate > HT_UNDER_MAX:
            return None

    return {
        "tip":          tip,
        "odds":         tip_odds,
        "combined":     round(combined, 4),
        "h2h_rate":     round(h2h_rate,  4) if h2h_rate  is not None else None,
        "home_rate":    round(home_rate, 4) if home_rate is not None else None,
        "away_rate":    round(away_rate, 4) if away_rate is not None else None,
        "ht_rate":      round(ht_rate,   4) if ht_rate   is not None else None,
    }


# ─── Üzenet formázás ──────────────────────────────────────────────────────────

def build_message(fx: dict, tip: dict, odds: dict) -> str:
    league  = fx.get("league", {})
    teams   = fx.get("teams", {})
    home    = teams.get("home", {}).get("name", "?")
    away    = teams.get("away", {}).get("name", "?")
    ts      = fx.get("fixture", {}).get("timestamp", 0)
    dt      = datetime.fromtimestamp(ts, tz=HU_TZ).strftime("%m.%d. %H:%M") if ts else "?"
    lg_name = league.get("name", "?")
    country = league.get("country", "?")

    direction = tip["tip"]
    icon      = "⬆️" if direction == "over" else "⬇️"
    label     = "OVER 2.5" if direction == "over" else "UNDER 2.5"
    ov_pct    = round((tip["combined"] if direction == "over" else 1 - tip["combined"]) * 100)
    bk_count  = odds["bookmaker_count"]

    h2h_str  = f"{round((tip['h2h_rate'] or 0)*100)}%" if tip.get("h2h_rate") is not None else "–"
    home_str = f"{round((tip['home_rate'] or 0)*100)}%" if tip.get("home_rate") is not None else "–"
    away_str = f"{round((tip['away_rate'] or 0)*100)}%" if tip.get("away_rate") is not None else "–"
    ht_str   = f"{round((tip['ht_rate'] or 0)*100)}%" if tip.get("ht_rate") is not None else "–"

    return (
        f"{icon} *{label} TIPP*\n\n"
        f"⚽ *{home}* vs *{away}*\n"
        f"🏆 {country} · {lg_name}\n"
        f"🕐 {dt}\n\n"
        f"📊 *Statisztika (>2.5 gól arány):*\n"
        f"  • H2H utolsó 5: *{h2h_str}*\n"
        f"  • {home} hazai: *{home_str}*\n"
        f"  • {away} vendég: *{away_str}*\n"
        f"  • Félidei gól arány: *{ht_str}*\n\n"
        f"🎯 Kombináld jel: *{ov_pct}%* a {label} irányba\n"
        f"💰 Átlag szorzó ({bk_count} iroda): *@{tip['odds']:.2f}*\n\n"
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
    """Szinkron adatgyűjtés thread-ben — HTTP hívások."""
    fixtures = fetch_upcoming_fixtures()
    logger.info(f"Közelgő meccsek: {len(fixtures)} (következő {HORIZON_HOURS}h)")

    tips_to_send = []

    for fx in fixtures:
        fixture_data = fx.get("fixture", {})
        fixture_id   = fixture_data.get("id")
        if not fixture_id or fixture_id in sent_ids:
            continue

        teams    = fx.get("teams", {})
        home_id  = teams.get("home", {}).get("id")
        away_id  = teams.get("away", {}).get("id")
        home_nm  = teams.get("home", {}).get("name", "?")
        away_nm  = teams.get("away", {}).get("name", "?")
        league   = fx.get("league", {})
        league_id = league.get("id")
        ts       = fixture_data.get("timestamp", 0)

        if not home_id or not away_id:
            continue

        # Liga whitelist ellenőrzés
        if league_id not in ALLOWED_LEAGUE_IDS:
            logger.debug(f"{home_nm} vs {away_nm} — liga kizárva (id={league_id}), kihagyva")
            continue

        h2h_key = f"{home_id}-{away_id}"

        # H2H
        h2h_matches = fetch_h2h(h2h_key)
        if len(h2h_matches) < MIN_H2H_MATCHES:
            logger.debug(f"{home_nm} vs {away_nm} — kevés H2H ({len(h2h_matches)}), kihagyva")
            continue

        h2h_stats = _parse_match_stats(h2h_matches)

        # Hazai csapat hazai forma
        home_matches = fetch_team_form(home_id, "home", last=10)
        home_stats   = _parse_match_stats(home_matches)
        if home_stats["count"] < MIN_FORM_MATCHES:
            logger.debug(f"{home_nm} — kevés hazai forma ({home_stats['count']}), kihagyva")
            continue

        # Vendég csapat vendég forma
        away_matches = fetch_team_form(away_id, "away", last=10)
        away_stats   = _parse_match_stats(away_matches)
        if away_stats["count"] < MIN_FORM_MATCHES:
            logger.debug(f"{away_nm} — kevés vendég forma ({away_stats['count']}), kihagyva")
            continue

        # Szorzók + könyvmérő lefedettség
        odds = fetch_odds(fixture_id)
        if odds is None:
            logger.debug(f"{home_nm} vs {away_nm} — nincs elegendő fogadóiroda, kihagyva")
            continue

        # Tipp számítás
        tip = calculate_tip(h2h_stats, home_stats, away_stats, odds)
        if tip is None:
            logger.debug(f"{home_nm} vs {away_nm} — nincs egyértelmű jel, kihagyva")
            continue

        msg = build_message(fx, tip, odds)
        meta = {
            "fixture_id":      fixture_id,
            "home":            home_nm,
            "away":            away_nm,
            "league":          league.get("name", "?"),
            "league_id":       league.get("id"),
            "country":         league.get("country"),
            "start_timestamp": ts,
            "tip":             tip["tip"],
            "line":            2.5,
            "odds":            tip["odds"],
            "bookmaker_count": odds["bookmaker_count"],
            "h2h_over_rate":   tip.get("h2h_rate"),
            "home_over_rate":  tip.get("home_rate"),
            "away_over_rate":  tip.get("away_rate"),
            "combined_score":  tip["combined"],
            "ht_goal_rate":    tip.get("ht_rate"),
            "sent_at":         int(datetime.utcnow().timestamp()),
        }
        tips_to_send.append((msg, meta))
        sent_ids.add(fixture_id)

    return tips_to_send


async def scan_and_send(context):
    """30 percenként fut — új tippek keresése és küldése."""
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
    now_ts    = int(datetime.utcnow().timestamp())
    pending   = load_pending_tips()
    notifs    = []

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

        icon        = "✅" if won else "❌"
        label       = "OVER 2.5" if predicted == "over" else "UNDER 2.5"
        result_txt  = "NYERT" if won else "VESZETT"
        odds_txt    = f"{tip['odds']:.2f}" if tip.get("odds") else "N/A"
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
    """60 percenként ellenőrzi a lezárt meccseket."""
    notifs = await asyncio.to_thread(_check_results_sync)
    for msg in notifs:
        try:
            await send_admin(context.bot, msg)
        except Exception as e:
            logger.error(f"Eredmény értesítő hiba: {e}")


# ─── Telegram parancsok ────────────────────────────────────────────────────────

async def cmd_tippek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Utolsó 10 tipp listázása."""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM football25_tips ORDER BY sent_at DESC LIMIT 10"
                )
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
    """Összesített statisztika."""
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
    avg_o  = sum(r[2] for r in rows) / total

    over_rows  = [r for r in rows if r[0] == "over"]
    under_rows = [r for r in rows if r[0] == "under"]
    ow = sum(1 for r in over_rows if r[1] == "win")
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
    scheduler.add_job(scan_and_send,   "interval", seconds=SCAN_INTERVAL,    args=[app])
    scheduler.add_job(check_results,   "interval", seconds=3600,              args=[app])

    async def post_init(application):
        scheduler.start()
        logger.info(f"Tipp szkennelő bekapcsolva ({SCAN_INTERVAL}s).")
        logger.info(f"Eredmény figyelő bekapcsolva (3600s).")
        await send_admin(application.bot, "🤖 *Football 2.5 O/U Bot elindult*\nSzkennelés: 30 percenként")
        # Első scan azonnal
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
    logger.info("Football 2.5 O/U Bot fut. (Min. odds 1.55 | Min. 3 fogadóiroda | HT szűrő aktív)")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
