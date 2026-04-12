"""
NBA / Basketball Rest-Advantage Bot
====================================
Stratégia: UNDER tipp amikor legalább egy csapat back-to-back (B2B) vagy
3-in-4 helyzetben van (3 meccs 4 napon belül).

Kutatási alap:
  – B2B hazai: átlagos teljesítménycsökkentés ~3.5 pont
  – B2B vendég (utazással): ~5.0 pont csökkentés
  – Mindkét csapat B2B: az egyéni büntetések összege
  – 3-in-4: ~2.5 pont csapatonként
  – Könyvesek átlagos korrekciója: ~1.5 pont/B2B csapat
  → Nettó edge: 2.0–8.5 pont → szisztematikus UNDER lehetőség

Csatorna: MULTI_SPORT_BOT_TOKEN / MULTI_SPORT_CHAT_ID
"""
from __future__ import annotations

import os
import asyncio
import time
import math
import logging
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("nba_rest_bot")

# ── Konfiguráció ───────────────────────────────────────────────────────────────

MULTI_SPORT_BOT_TOKEN = os.environ["MULTI_SPORT_BOT_TOKEN"]
MULTI_SPORT_CHAT_ID   = os.environ["MULTI_SPORT_CHAT_ID"]
DATABASE_URL          = (
    os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
)

SOFASCORE_BASE = (
    "https://www.sofascore.com/api/v1"
    if os.environ.get("REPL_ID")
    else "https://814dfd73-d8dd-4560-ab7a-2dea4ca2da33-00-3j0ryo8vfet2i.janeway.replit.dev/api/sofa"
)
SOFASCORE_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":         "https://www.sofascore.com/basketball/livescore",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8",
    "Cache-Control":   "no-cache",
}

# ── Research-backed paraméterek ────────────────────────────────────────────────

# Teljesítménycsökkentés back-to-back szituációkban (pontban)
B2B_HOME_PENALTY  = 3.5   # hazai csapat B2B-n van
B2B_AWAY_PENALTY  = 5.0   # vendég csapat B2B-n + utazási terhelés
THREE_IN_FOUR_PEN = 2.5   # 3-in-4 szituáció csapatonként
B2B_HOURS_LIMIT   = 28    # max. óra a két meccs közt → B2B minősítés
BOOK_ADJ_PER_TEAM = 1.5   # becsült könyves kiigazítás B2B csapatonként

# Szűrési küszöbök
MIN_NET_EDGE      = 2.0   # nettó pont edge (büntetés – könyves korr.) az UNDER fogadáshoz
MIN_ODDS          = 1.68  # minimum elfogadható szorzó
MAX_ODDS          = 2.15  # maximum szorzó
MIN_CONFIDENCE    = 58    # 0–100 konfidencia minimum
SCAN_HOURS_AHEAD  = 30    # ennyit nézünk előre
RESULT_DELAY_MIN  = 135   # ennyivel meccs vége után ellenőrzünk (perc)
API_DELAY_SEC     = 0.4
MIN_LAST_MATCHES  = 5     # legalább ennyi befejezett meccs kell a csapatnál

# Liga fehérlista: ahol a B2B-kutatás releváns (főlig NBA + top európai ligák)
VALID_LEAGUE_KW = [
    "nba", "euroleague", "eurocup", "7days",
    "acb", "endesa",
    "lega basket", "serie a",
    "pro a", "betclic elite",
    "vtb",
    "g league", "g-league",
    "adriatic", "aba",
    "nbl",
    "bbl", "turkish basketball super",
    "lkl", "plk", "nbb",
]
EXCLUDE_LEAGUE_KW = [
    "cyber", "nexlvl", "esport", "virtual", "2k",
    "nbl, west", "nbl, east",
    "championship round", "commissioners cup",
]

_cache: dict = {}

# ── Adatbázis ──────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    sql = """
    CREATE TABLE IF NOT EXISTS rest_advantage_tips (
        event_id          BIGINT PRIMARY KEY,
        home              TEXT NOT NULL,
        away              TEXT NOT NULL,
        league            TEXT NOT NULL,
        start_timestamp   BIGINT NOT NULL,
        tip               TEXT NOT NULL,
        line              REAL NOT NULL,
        home_b2b          BOOLEAN DEFAULT FALSE,
        away_b2b          BOOLEAN DEFAULT FALSE,
        home_3in4         BOOLEAN DEFAULT FALSE,
        away_3in4         BOOLEAN DEFAULT FALSE,
        total_penalty     REAL NOT NULL,
        net_edge          REAL NOT NULL,
        odds              REAL,
        sent_at           BIGINT NOT NULL,
        result            TEXT DEFAULT NULL,
        actual_total      REAL DEFAULT NULL,
        confidence_score  INTEGER DEFAULT NULL
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    logger.info("rest_advantage_tips tábla kész")


def get_sent_ids() -> set:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT event_id FROM rest_advantage_tips")
                return {str(r[0]) for r in cur.fetchall()}
    except Exception as e:
        logger.error(f"DB hiba (sent_ids): {e}")
        return set()


def save_tip(t: dict):
    sql = """
    INSERT INTO rest_advantage_tips
        (event_id, home, away, league, start_timestamp, tip, line,
         home_b2b, away_b2b, home_3in4, away_3in4,
         total_penalty, net_edge, odds, sent_at, confidence_score)
    VALUES
        (%(event_id)s, %(home)s, %(away)s, %(league)s, %(start_timestamp)s,
         %(tip)s, %(line)s, %(home_b2b)s, %(away_b2b)s,
         %(home_3in4)s, %(away_3in4)s, %(total_penalty)s, %(net_edge)s,
         %(odds)s, %(sent_at)s, %(confidence_score)s)
    ON CONFLICT (event_id) DO NOTHING;
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, t)
            conn.commit()
    except Exception as e:
        logger.error(f"Tipp mentési hiba: {e}")


def update_result(event_id: int, result: str, actual_total: float):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE rest_advantage_tips SET result=%s, actual_total=%s WHERE event_id=%s",
                    (result, actual_total, event_id),
                )
            conn.commit()
        logger.info(f"Eredmény frissítve: {event_id} → {result} ({actual_total})")
    except Exception as e:
        logger.error(f"Eredmény frissítési hiba: {e}")


def load_pending() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM rest_advantage_tips WHERE result IS NULL ORDER BY start_timestamp"
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"DB hiba (pending): {e}")
        return []


# ── SofaScore API ──────────────────────────────────────────────────────────────

def sofa_get(url: str) -> dict:
    try:
        time.sleep(API_DELAY_SEC)
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug(f"SofaScore hiba ({url}): {e}")
        return {}


def fetch_events(date_str: str) -> list:
    data = sofa_get(f"{SOFASCORE_BASE}/sport/basketball/scheduled-events/{date_str}")
    return data.get("events", [])


def fetch_last_matches(team_id: int, last_n: int = 10) -> list:
    key = f"last_{team_id}"
    if key in _cache:
        return _cache[key]
    data = sofa_get(f"{SOFASCORE_BASE}/team/{team_id}/events/last/0")
    events = [e for e in data.get("events", [])
              if e.get("status", {}).get("type") == "finished"][:last_n]
    _cache[key] = events
    return events


def fetch_event_score(event_id: int) -> tuple[float | None, float | None]:
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}")
    ev   = data.get("event", {})
    if ev.get("status", {}).get("type") != "finished":
        return None, None
    hs = ev.get("homeScore", {}).get("current")
    as_ = ev.get("awayScore", {}).get("current")
    return hs, as_


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


def fetch_totals_line(event_id: int) -> dict | None:
    """SofaScore totálok vonala és szorzói."""
    try:
        url = f"{SOFASCORE_BASE}/event/{event_id}/odds/1/all"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("error"):
            return None
        for mkt in (data.get("markets") or []):
            name = (mkt.get("marketName") or mkt.get("name") or "").lower()
            if not any(kw in name for kw in ["total", "over/under", "points"]):
                continue
            choices = mkt.get("choices") or []
            ov = next((c for c in choices if (c.get("name") or "").lower().startswith("over")), None)
            un = next((c for c in choices if (c.get("name") or "").lower().startswith("under")), None)
            if not ov:
                continue
            raw = (mkt.get("choiceGroup") or mkt.get("handicap") or ov.get("handicap") or "")
            try:
                line = float(str(raw))
            except Exception:
                continue
            if line < 60:   # kosárnál 60 pont az abszolút minimum
                continue
            over_odds  = _parse_odds(ov.get("fractionalValue"))
            under_odds = _parse_odds(un.get("fractionalValue")) if un else None
            logger.info(f"Vonal: {line} | OVER={over_odds} UNDER={under_odds} (event={event_id})")
            return {"line": line, "over": over_odds, "under": under_odds}
    except Exception as e:
        logger.debug(f"Odds hiba ({event_id}): {e}")
    return None


# ── Liga szűrő ─────────────────────────────────────────────────────────────────

def is_valid_league(event: dict) -> bool:
    t    = event.get("tournament", {})
    name = (t.get("name", "") + " " + t.get("category", {}).get("name", "")).lower()
    if any(kw in name for kw in EXCLUDE_LEAGUE_KW):
        return False
    return any(kw in name for kw in VALID_LEAGUE_KW)


# ── Rest-advantage detektálás ──────────────────────────────────────────────────

def is_overtime(match: dict) -> bool:
    for side in ("homeScore", "awayScore"):
        s = match.get(side, {})
        if s.get("overtime") or s.get("period5") is not None:
            return True
    return False


def analyze_rest(team_id: int, start_ts: int, is_home: bool) -> dict:
    """
    Visszaad egy rest-profilt:
      b2b      – bool, ha az előző meccs < B2B_HOURS_LIMIT óra
      three_in_four – bool, ha 3 meccs volt az elmúlt 4 napban
      last_game_ts – az utolsó meccs timestampje
      rest_hours   – pihenőórák száma
      penalty      – várható teljesítménycsökkentés pontban
    """
    matches = fetch_last_matches(team_id, last_n=12)
    finished = [m for m in matches if not is_overtime(m)]

    # Utolsó meccs időpontja
    last_ts = max(
        (m.get("startTimestamp", 0) for m in finished),
        default=0,
    )

    rest_hours = (start_ts - last_ts) / 3600 if last_ts else 9999

    b2b = 0 < rest_hours < B2B_HOURS_LIMIT

    # 3-in-4: az elmúlt 4 napban hány befejezett meccs volt
    four_days_ago = start_ts - 4 * 24 * 3600
    recent_count  = sum(
        1 for m in finished
        if four_days_ago <= m.get("startTimestamp", 0) < start_ts
    )
    three_in_four = recent_count >= 2   # + az aktuális = legalább 3

    # Büntetés kiszámítása
    penalty = 0.0
    if b2b:
        penalty += B2B_AWAY_PENALTY if not is_home else B2B_HOME_PENALTY
    elif three_in_four:
        penalty += THREE_IN_FOUR_PEN

    return {
        "b2b":           b2b,
        "three_in_four": three_in_four,
        "rest_hours":    round(rest_hours, 1),
        "penalty":       penalty,
        "last_ts":       last_ts,
        "n_matches":     len(finished),
    }


# ── Konfidencia ────────────────────────────────────────────────────────────────

def calc_confidence(
    net_edge: float,
    total_penalty: float,
    home_b2b: bool,
    away_b2b: bool,
    home_3in4: bool,
    away_3in4: bool,
    odds: float | None,
    under_rate_h2h: float | None,
) -> int:
    score = 0

    # 1. Nettó edge mérete (40 pont max)
    score += min(40, int(net_edge * 8))

    # 2. Mindkét csapat fáradt? (20 pont)
    if home_b2b and away_b2b:
        score += 20
    elif home_b2b or away_b2b:
        score += 14
    elif home_3in4 or away_3in4:
        score += 8

    # 3. Szorzó minőség (20 pont)
    if odds:
        if MIN_ODDS <= odds <= MAX_ODDS:
            score += 20
        elif odds > MAX_ODDS:
            score += 8

    # 4. H2H UNDER tendencia (20 pont)
    if under_rate_h2h is not None:
        if under_rate_h2h >= 0.60:
            score += 20
        elif under_rate_h2h >= 0.50:
            score += 10

    return max(0, min(100, score))


def fetch_h2h_under_rate(event_id: int, line: float) -> float | None:
    """H2H meccseknél mekkora arányban ment UNDER a jelenlegi vonalnál."""
    key = f"h2h_{event_id}"
    if key in _cache:
        h2h = _cache[key]
    else:
        data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/h2h/events")
        h2h  = [e for e in data.get("events", [])
                if e.get("status", {}).get("type") == "finished"]
        _cache[key] = h2h

    if len(h2h) < 3:
        return None

    unders = 0
    total  = 0
    for m in h2h[:8]:
        hs  = m.get("homeScore", {}).get("current")
        as_ = m.get("awayScore", {}).get("current")
        if hs is None or as_ is None:
            continue
        total += 1
        if (hs + as_) < line:
            unders += 1

    return round(unders / total, 3) if total >= 3 else None


# ── Fő elemzés ─────────────────────────────────────────────────────────────────

def analyze_event(event: dict, sent_ids: set) -> dict | None:
    event_id = str(event.get("id", ""))
    if event_id in sent_ids:
        return None

    status = event.get("status", {}).get("type", "")
    if status != "notstarted":
        return None

    if not is_valid_league(event):
        return None

    start_ts   = event.get("startTimestamp", 0)
    now_ts     = int(time.time())
    hours_left = (start_ts - now_ts) / 3600

    if hours_left < 0.5 or hours_left > SCAN_HOURS_AHEAD:
        return None

    home    = event.get("homeTeam", {}).get("name", "")
    away    = event.get("awayTeam", {}).get("name", "")
    home_id = event.get("homeTeam", {}).get("id")
    away_id = event.get("awayTeam", {}).get("id")
    league  = event.get("tournament", {}).get("name", "Ismeretlen")

    if not home_id or not away_id:
        return None

    # Rest profil mindkét csapatnál
    home_rest = analyze_rest(home_id, start_ts, is_home=True)
    away_rest = analyze_rest(away_id, start_ts, is_home=False)

    if home_rest["n_matches"] < MIN_LAST_MATCHES or away_rest["n_matches"] < MIN_LAST_MATCHES:
        logger.info(f"Kevés adat: {home} vs {away}")
        return None

    # Ha egyik csapat sem fáradt → nincs edge
    home_tired = home_rest["b2b"] or home_rest["three_in_four"]
    away_tired = away_rest["b2b"] or away_rest["three_in_four"]
    if not home_tired and not away_tired:
        return None

    total_penalty = home_rest["penalty"] + away_rest["penalty"]

    # Könyvesek becsült korrekciója
    n_b2b_teams   = int(home_rest["b2b"]) + int(away_rest["b2b"])
    book_adj      = n_b2b_teams * BOOK_ADJ_PER_TEAM
    net_edge      = round(total_penalty - book_adj, 2)

    if net_edge < MIN_NET_EDGE:
        logger.info(
            f"Nettó edge nem elég ({net_edge:.1f} < {MIN_NET_EDGE}): {home} vs {away}"
            f" | büntetés={total_penalty:.1f}, könyves korr.={book_adj:.1f}"
        )
        return None

    # Bookmaker vonal SofaScore-ból
    sofa = fetch_totals_line(int(event_id))
    if not sofa:
        logger.info(f"Nincs bookmaker vonal: {home} vs {away}")
        return None

    line       = sofa["line"]
    under_odds = sofa.get("under")

    if under_odds and under_odds < MIN_ODDS:
        logger.info(f"UNDER szorzó túl alacsony ({under_odds}): {home} vs {away}")
        return None
    if under_odds and under_odds > MAX_ODDS:
        logger.info(f"UNDER szorzó túl magas ({under_odds}): {home} vs {away}")
        return None

    # H2H UNDER arány
    under_rate = fetch_h2h_under_rate(int(event_id), line)

    confidence = calc_confidence(
        net_edge, total_penalty,
        home_rest["b2b"], away_rest["b2b"],
        home_rest["three_in_four"], away_rest["three_in_four"],
        under_odds, under_rate,
    )

    if confidence < MIN_CONFIDENCE:
        logger.info(f"Alacsony konfidencia ({confidence}): {home} vs {away}")
        return None

    tip_label = f"UNDER {line}"
    logger.info(
        f"✅ Tipp: {home} vs {away} → {tip_label} | "
        f"büntetés={total_penalty:.1f}, net_edge={net_edge:.1f}, "
        f"conf={confidence}, odds={under_odds}"
    )

    return {
        # DB mezők
        "event_id":         int(event_id),
        "home":             home,
        "away":             away,
        "league":           league,
        "start_timestamp":  start_ts,
        "tip":              tip_label,
        "line":             line,
        "home_b2b":         home_rest["b2b"],
        "away_b2b":         away_rest["b2b"],
        "home_3in4":        home_rest["three_in_four"],
        "away_3in4":        away_rest["three_in_four"],
        "total_penalty":    total_penalty,
        "net_edge":         net_edge,
        "odds":             under_odds,
        "sent_at":          now_ts,
        "confidence_score": confidence,
        "result":           None,
        "actual_total":     None,
        # extra (üzenetbe, DB-be NEM)
        "_home_rest":       home_rest,
        "_away_rest":       away_rest,
        "_under_rate":      under_rate,
        "_over_odds":       sofa.get("over"),
    }


# ── Telegram üzenet ────────────────────────────────────────────────────────────

def _stars(conf: int) -> str:
    if conf >= 80:
        return "⭐⭐⭐"
    if conf >= 68:
        return "⭐⭐"
    return "⭐"


def _rest_tag(rest: dict, label: str) -> str:
    parts = []
    if rest["b2b"]:
        parts.append(f"B2B ({rest['rest_hours']:.0f}h)")
    elif rest["three_in_four"]:
        parts.append("3-in-4")
    else:
        parts.append(f"{rest['rest_hours']:.0f}h pihenő")
    return f"{label}: {', '.join(parts)}"


def format_tip_message(t: dict) -> str:
    hr = t["_home_rest"]
    ar = t["_away_rest"]

    dt = (datetime.utcfromtimestamp(t["start_timestamp"]) + timedelta(hours=2)).strftime("%Y.%m.%d %H:%M")
    stars  = _stars(t["confidence_score"])
    odds_s = f"{t['odds']:.2f}" if t.get("odds") else "—"
    over_s = f"{t['_over_odds']:.2f}" if t.get("_over_odds") else "—"

    # Fáradtság magyarázat
    fatigue_lines = []
    if hr["b2b"]:
        fatigue_lines.append(f"😴 {t['home']}: B2B ({hr['rest_hours']:.0f}h) → -{hr['penalty']:.1f} pt")
    elif hr["three_in_four"]:
        fatigue_lines.append(f"😴 {t['home']}: 3-in-4 → -{hr['penalty']:.1f} pt")
    if ar["b2b"]:
        fatigue_lines.append(f"😴 {t['away']}: B2B ({ar['rest_hours']:.0f}h) → -{ar['penalty']:.1f} pt")
    elif ar["three_in_four"]:
        fatigue_lines.append(f"😴 {t['away']}: 3-in-4 → -{ar['penalty']:.1f} pt")

    under_rate_s = (
        f"{t['_under_rate']*100:.0f}%" if t.get("_under_rate") is not None else "—"
    )

    lines = [
        f"🏀 <b>Rest-Advantage UNDER Tipp</b> {stars}",
        f"",
        f"🏆 {t['league']}",
        f"⚔️ <b>{t['home']}</b> vs <b>{t['away']}</b>",
        f"🕐 {dt}",
        f"",
        f"📉 <b>UNDER {t['line']}</b>",
        f"",
        *fatigue_lines,
        f"",
        f"📊 Össz. fáradsági büntetés: <b>-{t['total_penalty']:.1f} pt</b>",
        f"📐 Nettó edge (korrigált): <b>{t['net_edge']:.1f} pt</b>",
        f"🔄 H2H UNDER arány: {under_rate_s}",
        f"",
        f"💰 UNDER szorzó: <b>{odds_s}</b> | OVER: {over_s}",
        f"{stars} Konfidencia: <b>{t['confidence_score']}/100</b>",
    ]
    return "\n".join(lines)


def format_result_message(t: dict, actual: float, result: str) -> str:
    icon = "✅" if result == "won" else "❌"
    return (
        f"{icon} <b>Rest-Bot Eredmény</b>\n"
        f"⚔️ {t['home']} vs {t['away']}\n"
        f"🎯 Tipp: {t['tip']} | Tényleges: {actual:.0f}\n"
        f"{'✅ Nyert! 🎉' if result == 'won' else '❌ Veszett'}"
    )


# ── Scan és eredmény ───────────────────────────────────────────────────────────

async def scan_and_send(app: Application):
    _cache.clear()
    bot      = app.bot
    sent_ids = get_sent_ids()
    new_tips = 0

    today    = date.today()
    tomorrow = today + timedelta(days=1)
    events   = []
    for d in [today.isoformat(), tomorrow.isoformat()]:
        events.extend(fetch_events(d))

    logger.info(f"Scan: {len(events)} meccs találva (ma + holnap)")

    for ev in events:
        tip = await asyncio.to_thread(analyze_event, ev, sent_ids)
        if not tip:
            continue

        msg = format_tip_message(tip)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Nyert",   callback_data=f"rest_won_{tip['event_id']}"),
            InlineKeyboardButton("❌ Veszett", callback_data=f"rest_lost_{tip['event_id']}"),
        ]])

        try:
            await bot.send_message(
                chat_id=MULTI_SPORT_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            db_tip = {k: v for k, v in tip.items() if not k.startswith("_")}
            save_tip(db_tip)
            sent_ids.add(str(tip["event_id"]))
            new_tips += 1
            logger.info(f"Tipp elküldve: {tip['home']} vs {tip['away']} → {tip['tip']}")
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Telegram küldési hiba: {e}")

    logger.info(f"Scan kész — {new_tips} új tipp elküldve")


async def check_results(app: Application):
    bot     = app.bot
    pending = load_pending()
    now_ts  = int(time.time())

    for t in pending:
        elapsed_min = (now_ts - t["start_timestamp"]) / 60
        if elapsed_min < RESULT_DELAY_MIN:
            continue

        hs, as_ = await asyncio.to_thread(fetch_event_score, t["event_id"])
        if hs is None or as_ is None:
            continue

        actual = float(hs + as_)
        result = "won" if actual < t["line"] else "lost"

        update_result(t["event_id"], result, actual)
        msg = format_result_message(t, actual, result)
        try:
            await bot.send_message(
                chat_id=MULTI_SPORT_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Eredmény küldési hiba: {e}")


# ── Telegram parancsok ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏀 Rest-Advantage Bot\n\n"
        "UNDER tippek back-to-back és 3-in-4 szituációkban.\n\n"
        "/scan — Azonnali scan\n"
        "/stat — Statisztika\n"
        "/help — Segítség"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Scan indul...")
    await scan_and_send(context.application)
    await update.message.reply_text("✅ Scan kész.")


async def cmd_stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN result='won'  THEN 1 ELSE 0 END) AS won,
                        SUM(CASE WHEN result='lost' THEN 1 ELSE 0 END) AS lost,
                        SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) AS pending,
                        AVG(odds) FILTER (WHERE odds IS NOT NULL) AS avg_odds,
                        SUM(CASE WHEN home_b2b OR away_b2b THEN 1 ELSE 0 END) AS b2b_count,
                        AVG(net_edge) AS avg_edge
                    FROM rest_advantage_tips
                """)
                r = cur.fetchone()

        if not r or not r[0]:
            await update.message.reply_text("Még nincs adat.")
            return

        total, won, lost, pending, avg_odds, b2b_count, avg_edge = r
        settled = (won or 0) + (lost or 0)
        wr   = f"{won/(settled)*100:.1f}%" if settled > 0 else "—"
        roi  = f"{((won or 0) * (avg_odds or 1.90) - settled) / settled * 100:.1f}%" if settled > 0 else "—"

        msg = (
            f"🏀 <b>Rest-Advantage Bot Statisztika</b>\n\n"
            f"Összes tipp: {total}\n"
            f"✅ Nyert: {won or 0} | ❌ Veszett: {lost or 0} | ⏳ Folyamatban: {pending or 0}\n"
            f"Win rate: <b>{wr}</b>\n"
            f"ROI: <b>{roi}</b>\n"
            f"Átl. szorzó: {f'{avg_odds:.2f}' if avg_odds else '—'}\n"
            f"Átl. nettó edge: {f'{avg_edge:.1f} pt' if avg_edge else '—'}\n"
            f"Ebből B2B tipp: {b2b_count or 0}"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Stat hiba: {e}")
        await update.message.reply_text("Hiba a statisztika lekérésekor.")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if not data.startswith("rest_"):
        return

    parts = data.split("_")
    if len(parts) < 3:
        return

    result_str = parts[1]            # "won" vagy "lost"
    event_id   = int(parts[2])
    result     = "won" if result_str == "won" else "lost"

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM rest_advantage_tips WHERE event_id=%s", (event_id,))
                tip = cur.fetchone()

        if not tip:
            await query.edit_message_text("Nem találom a tippet.")
            return

        update_result(event_id, result, tip.get("actual_total") or 0)
        icon = "✅" if result == "won" else "❌"
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"{icon} Kézzel rögzítve: {tip['home']} vs {tip['away']} → "
            f"{'Nyert' if result == 'won' else 'Veszett'}"
        )
    except Exception as e:
        logger.error(f"Callback hiba: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    init_db()

    # Régi tippek törlése – tiszta lap
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rest_advantage_tips")
        conn.commit()
        conn.close()
        logger.info("rest_advantage_tips tábla törölve – tiszta indulás")
    except Exception as e:
        logger.warning(f"Tábla törlési hiba: {e}")

    # Startup üzenet a Multi Sport csatornára
    try:
        await app.bot.send_message(
            chat_id=MULTI_SPORT_CHAT_ID,
            text=(
                "🏀 <b>NBA Rest-Advantage Bot aktív</b>\n\n"
                "Ettől a pillanattól az <b>NBA UNDER tippek</b> ide érkeznek.\n\n"
                "📌 <b>Stratégia:</b> Back-to-Back / 3-in-4 fáradtsági UNDER\n"
                "🎯 <b>Szűrők:</b> min. 2pt nettó előny · odds 1.68–2.15 · konfidencia ≥58%\n"
                "🕘 <b>Scan:</b> 09:05 – 21:05 CET (páratlan órákon)\n\n"
                "Várj tippekre – a következő scan 3 percen belül lefut."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Startup üzenet hiba: {e}")

    scheduler = AsyncIOScheduler(timezone="Europe/Budapest")

    # Scan: minden 2 órában, CET 09:00 és 21:00 között
    scheduler.add_job(
        scan_and_send,
        "cron",
        hour="9,11,13,15,17,19,21",
        minute=5,
        args=[app],
        id="scan",
    )

    # Eredmény: 20 percenként
    scheduler.add_job(
        check_results,
        "interval",
        minutes=20,
        args=[app],
        id="results",
    )

    # Startup scan 3 perc múlva (timezone-aware)
    from zoneinfo import ZoneInfo as _ZI
    _tz = _ZI("Europe/Budapest")
    scheduler.add_job(
        scan_and_send,
        "date",
        run_date=datetime.now(_tz) + timedelta(minutes=3),
        args=[app],
        id="startup_scan",
    )

    scheduler.start()
    logger.info("NBA Rest-Advantage Bot elindult ✅")


def main():
    app = (
        Application.builder()
        .token(MULTI_SPORT_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan",  cmd_scan))
    app.add_handler(CommandHandler("stat",  cmd_stat))
    app.add_handler(CommandHandler("help",  cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Bot polling indul...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
