#!/usr/bin/env python3
"""
🏓 Asztalitenisz Telegram Bot — Standalone verzió
==================================================
Csak ezt az egy fájlt kell a külső szerverre másolni.

Szükséges környezeti változók:
  TELEGRAM_BOT_TOKEN   — Telegram bot token (@BotFather-től)
  TELEGRAM_CHAT_ID     — Chat / csoport ID ahol a tippek megjelennek
  DATABASE_URL         — PostgreSQL connection string
                         pl.: postgresql://user:jelszo@host:5432/adatbazis

Telepítés (Ubuntu/Debian):
  pip install "python-telegram-bot[job-queue]" requests psycopg2-binary cloudscraper

Indítás:
  python tt_bot_standalone.py

Folyamatos futtatás (systemd vagy screen/tmux ajánlott):
  while true; do python tt_bot_standalone.py; sleep 5; done
"""
from __future__ import annotations

import os
import time
import json
import asyncio
import logging
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, date, timedelta, timezone
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
    import cloudscraper as _cloudscraper
    _HAS_CLOUDSCRAPER = True
except ImportError:
    _HAS_CLOUDSCRAPER = False

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

# ── Naplózás ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Időzóna ───────────────────────────────────────────────────────────────────
HU_TZ = ZoneInfo("Europe/Budapest")

# ══════════════════════════════════════════════════════════════════════════════
#  BEÁLLÍTÁSOK — itt módosítsd az értékeket
# ══════════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = "8785627523:AAFpUodzk6-pEhR6esp8v-V2JA1cjHAFlc4"
TELEGRAM_CHAT_ID   = "6617439213"
DATABASE_URL       = "postgresql://postgres.sodpzmupwqeusehszeag:Sanyika2024@aws-1-eu-west-3.pooler.supabase.com:6543/postgres"

# Ligák (kulcsszavak kisbetűsen)
ALLOWED_KEYWORDS = ["setka", "czech"]

# Tipp szűrők
MIN_ODDS           = 1.65   # Minimális szorzó a tippelt oldalon
MIN_FORM_MATCHES   = 10     # Minimum befejezett meccs a forma számításhoz
MIN_H2H_MATCHES    = 5      # Minimum H2H meccs
STRONG_THRESHOLD   = 27.5   # Ponthatár az erős tipphez

# Kemény kapuk (csak ezeket teljesítő meccsek kapnak tippet)
MIN_H2H_RATE       = 0.70   # H2H győzelmi arány ≥ 70%
MIN_FIRST_SET_RATE = 0.70   # 1. szett győzelmi arány ≥ 70% (ha van adat)
MIN_FORM_DIFF      = 0.20   # Napi forma különbség ≥ 20 százalékpont

# Időzítések
SCAN_INTERVAL_SEC  = 900    # 15 percenként keres új tippet
MAX_STARTUP_TIPS   = 20     # Max. tipp indításkor

# ══════════════════════════════════════════════════════════════════════════════
#  SOFASCORE PROXY (Repliten keresztül — Oracle IP nem blokkolódik)
# ══════════════════════════════════════════════════════════════════════════════

_SOFA_DIRECT = "https://api.sofascore.app/api/v1"
_SOFA_PROXY  = "https://814dfd73-d8dd-4560-ab7a-2dea4ca2da33-00-3j0ryo8vfet2i.janeway.replit.dev/api/sofa"


def _ss_get(url: str, timeout: int = 12) -> requests.Response:
    """SofaScore GET – a Replit proxyn keresztül (megkerüli az Oracle IP-blokkolást)."""
    proxy_url = url.replace(_SOFA_DIRECT, _SOFA_PROXY)
    try:
        resp = requests.get(proxy_url, timeout=timeout)
        return resp
    except Exception as e:
        logger.error(f"Proxy GET hiba ({proxy_url}): {e}")
        raise

# ══════════════════════════════════════════════════════════════════════════════
#  ADATBÁZIS
# ══════════════════════════════════════════════════════════════════════════════


def get_db_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Létrehozza a tips táblát, ha még nem létezik."""
    conn = get_db_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tips (
                    id               SERIAL PRIMARY KEY,
                    event_id         BIGINT UNIQUE NOT NULL,
                    home             TEXT NOT NULL,
                    away             TEXT NOT NULL,
                    league           TEXT,
                    predicted        TEXT,
                    predicted_name   TEXT,
                    odds             NUMERIC(6,3),
                    start_timestamp  BIGINT,
                    sent_at          BIGINT,
                    result           TEXT,
                    actual_winner    TEXT
                )
            """)
    conn.close()
    logger.info("DB init kész.")


def load_tips() -> list:
    try:
        conn = get_db_conn()
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM tips ORDER BY sent_at DESC")
                rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"DB load_tips hiba: {e}")
        return []


def save_tip_record(record: dict) -> bool:
    """Elmenti a tippet. True ha új, False ha már létezett."""
    try:
        conn = get_db_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tips
                        (event_id, home, away, league, predicted, predicted_name,
                         odds, start_timestamp, sent_at, result, actual_winner)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (
                        record["event_id"],
                        record["home"],
                        record["away"],
                        record["league"],
                        record["predicted"],
                        record["predicted_name"],
                        record.get("odds"),
                        record["start_timestamp"],
                        record["sent_at"],
                        record.get("result"),
                        record.get("actual_winner"),
                    ),
                )
                inserted = cur.rowcount > 0
        conn.close()
        return inserted
    except Exception as e:
        logger.error(f"DB save_tip_record hiba: {e}")
        return False


def update_tip_result(event_id: int, result: str, actual_winner: str):
    try:
        conn = get_db_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tips SET result=%s, actual_winner=%s WHERE event_id=%s",
                    (result, actual_winner, event_id),
                )
        conn.close()
    except Exception as e:
        logger.error(f"DB update_tip_result hiba: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  SOFASCORE API
# ══════════════════════════════════════════════════════════════════════════════


def sofascore_fetch_events(target_date: str) -> list:
    url = f"https://api.sofascore.app/api/v1/sport/table-tennis/scheduled-events/{target_date}"
    try:
        resp = _ss_get(url, timeout=12)
        resp.raise_for_status()
        return resp.json().get("events", [])
    except Exception as e:
        logger.error(f"SofaScore fetch hiba ({target_date}): {e}")
        return []


def sofascore_fetch_live_events() -> list:
    """Valódi élő meccsek lekérése a live végpontról."""
    url = "https://api.sofascore.app/api/v1/sport/table-tennis/events/live"
    try:
        resp = _ss_get(url, timeout=12)
        resp.raise_for_status()
        return resp.json().get("events", [])
    except Exception as e:
        logger.error(f"SofaScore live fetch hiba: {e}")
        return []


def sofascore_fetch_multi_day(days: int = 3) -> list:
    """Több nap eseményeit kéri le (tegnap, ma, holnap stb.)."""
    all_events = []
    seen_ids = set()
    for delta in range(-(days // 2), days // 2 + 1):
        d = (date.today() + timedelta(days=delta)).isoformat()
        for e in sofascore_fetch_events(d):
            eid = e.get("id")
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                all_events.append(e)
    return all_events


def sofascore_fetch_h2h(event_id: int, home_name: str = "", away_name: str = "") -> tuple:
    """
    H2H utolsó 5 meccs lekérése.
    Visszaad: (home_wins, total) — minimum 5 meccs kell, különben (0, 0).
    """
    url = f"https://api.sofascore.app/api/v1/event/{event_id}/h2h"
    try:
        resp = _ss_get(url, timeout=8)
        if resp.status_code != 200:
            return 0, 0
        data = resp.json()

        events = data.get("events", [])
        finished = [e for e in events if e.get("status", {}).get("type", "").lower() == "finished"]

        if len(finished) >= 5 and home_name:
            last5 = finished[:5]
            home_wins = 0
            for e in last5:
                h  = e.get("homeTeam", {}).get("name", "")
                a  = e.get("awayTeam", {}).get("name", "")
                hs = e.get("homeScore", {}).get("current", 0) or 0
                as_ = e.get("awayScore", {}).get("current", 0) or 0
                if home_name.lower() in h.lower() and hs > as_:
                    home_wins += 1
                elif home_name.lower() in a.lower() and as_ > hs:
                    home_wins += 1
            return home_wins, 5

        duel = data.get("teamDuel")
        if not duel:
            return 0, 0
        home_wins = duel.get("homeWins", 0)
        away_wins = duel.get("awayWins", 0)
        draws     = duel.get("draws", 0)
        total     = home_wins + away_wins + draws
        if total < 5:
            return 0, 0
        return home_wins, total
    except Exception as e:
        logger.error(f"H2H fetch hiba: {e}")
        return 0, 0


def fractional_to_decimal(frac: str):
    try:
        if not frac:
            return None
        if "/" in frac:
            num, den = frac.split("/")
            return round(int(num) / int(den) + 1, 3)
        v = float(frac)
        return round(v, 3) if v > 1.0 else round(v + 1, 3)
    except Exception:
        return None


def _parse_choice_odd(c: dict):
    dv = c.get("decimalValue")
    if dv:
        try:
            v = float(dv)
            if v > 1.0:
                return round(v, 3)
        except Exception:
            pass
    return fractional_to_decimal(c.get("fractionalValue", ""))


def sofascore_fetch_odds(event_id: int):
    url = f"https://api.sofascore.app/api/v1/event/{event_id}/odds/1/all"
    try:
        resp = _ss_get(url, timeout=8)
        if resp.status_code != 200:
            return None
        markets = resp.json().get("markets", [])
        for market in markets:
            if market.get("marketName") == "Full time":
                choices   = market.get("choices", [])
                odds_map  = {}
                for c in choices:
                    odd = _parse_choice_odd(c)
                    if odd:
                        odds_map[c.get("name", "")] = odd
                if "1" in odds_map and "2" in odds_map:
                    return {"home": odds_map["1"], "away": odds_map["2"]}
                ordered = list(odds_map.values())
                if len(ordered) >= 2:
                    return {"home": ordered[0], "away": ordered[1]}
        return None
    except Exception as e:
        logger.error(f"Odds fetch hiba: {e}")
        return None


def sofascore_fetch_player_stats(team_id: int, last: int = 10) -> tuple:
    """
    Játékos forma + első szett arány az elmúlt 14 napból (max. 'last' meccs).
    Visszaad: ((forma_w, forma_t), (fs_w, fs_t))
    """
    cutoff = int(datetime.now(timezone.utc).timestamp()) - 14 * 24 * 3600
    url = f"https://api.sofascore.app/api/v1/team/{team_id}/events/last/0"
    try:
        resp = _ss_get(url, timeout=8)
        if resp.status_code != 200:
            return (0, 0), (0, 0)
        events  = resp.json().get("events", [])
        form_w  = 0; form_t = 0
        fs_w    = 0; fs_t   = 0
        for e in events:
            if form_t >= last:
                break
            if e.get("startTimestamp", 0) < cutoff:
                continue
            if e.get("status", {}).get("type", "").lower() != "finished":
                continue
            is_home = e.get("homeTeam", {}).get("id") == team_id
            hs  = e.get("homeScore", {}).get("current", 0) or 0
            as_ = e.get("awayScore", {}).get("current", 0) or 0
            form_t += 1
            if (is_home and hs > as_) or (not is_home and as_ > hs):
                form_w += 1
            hs1 = e.get("homeScore", {}).get("period1")
            as1 = e.get("awayScore", {}).get("period1")
            if hs1 is not None and as1 is not None:
                fs_t += 1
                if (is_home and hs1 > as1) or (not is_home and as1 > hs1):
                    fs_w += 1
        return (form_w, form_t), (fs_w, fs_t)
    except Exception as e:
        logger.error(f"Játékos stats fetch hiba (team_id={team_id}): {e}")
        return (0, 0), (0, 0)


# ══════════════════════════════════════════════════════════════════════════════
#  TIPP LOGIKA
# ══════════════════════════════════════════════════════════════════════════════


def calculate_tip(
    h2h_home_wins, h2h_total,
    home_form_wins, home_form_total,
    away_form_wins, away_form_total,
    home_fs_wins=0, home_fs_total=0,
    away_fs_wins=0, away_fs_total=0,
):
    """
    Visszaad: (winner, bizalom_szöveg, score)
    winner: 'home' | 'away' | 'uncertain'

    Kemény feltételek:
    1. Min. 10 forma meccs mindkét játékoshoz
    2. Min. 5 H2H meccs
    3. H2H és forma iránya megegyezik
    4. Pontozás eléri a STRONG_THRESHOLD-ot
    5. H2H győzelmi arány ≥ 70%
    6. Forma különbség ≥ 20 százalékpont
    7. 1. szett arány ≥ 70% (ha van ≥5 adat)
    """
    score = 0.0

    if home_form_total < MIN_FORM_MATCHES or away_form_total < MIN_FORM_MATCHES:
        return "uncertain", "🔴 Kevés forma adat (min. 10)", score

    if h2h_total < MIN_H2H_MATCHES:
        return "uncertain", "🔴 Kevés H2H adat (min. 5)", score

    home_rate = home_form_wins / home_form_total
    away_rate = away_form_wins / away_form_total
    h2h_rate  = h2h_home_wins / h2h_total

    h2h_score  = (h2h_rate - 0.5) * 40
    form_score = (home_rate - away_rate) * 30

    home_fs_rate = (home_fs_wins / home_fs_total) if home_fs_total >= 5 else None
    away_fs_rate = (away_fs_wins / away_fs_total) if away_fs_total >= 5 else None
    first_set_score = 0.0
    if home_fs_rate is not None and away_fs_rate is not None:
        first_set_score = (home_fs_rate - away_fs_rate) * 20

    if (h2h_score > 0) != (form_score > 0):
        return "uncertain", "🔴 Ellentmondó jelek (H2H vs forma)", score

    score = h2h_score + form_score + first_set_score

    if abs(score) < STRONG_THRESHOLD:
        return "uncertain", "🔴 Bizonytalan", score

    # Győztes irány
    if score > 0:
        winner = "home"
        w_h2h  = h2h_rate
        w_form = home_rate
        l_form = away_rate
        w_fs   = home_fs_rate
    else:
        winner = "away"
        w_h2h  = 1.0 - h2h_rate
        w_form = away_rate
        l_form = home_rate
        w_fs   = away_fs_rate

    # Kemény kapuk
    if w_h2h < MIN_H2H_RATE:
        return "uncertain", f"🔴 H2H gyenge ({w_h2h*100:.0f}% < 70%)", score
    if w_form - l_form < MIN_FORM_DIFF:
        return "uncertain", f"🔴 Forma különbség kicsi ({(w_form - l_form)*100:.0f}% < 20%)", score
    if w_fs is not None and w_fs < MIN_FIRST_SET_RATE:
        return "uncertain", f"🔴 1. szett gyenge ({w_fs*100:.0f}% < 70%)", score

    return winner, "🟢 Erős tipp", score


# ══════════════════════════════════════════════════════════════════════════════
#  SEGÉDFÜGGVÉNYEK
# ══════════════════════════════════════════════════════════════════════════════


def is_allowed(event: dict) -> bool:
    t    = event.get("tournament", {})
    text = (t.get("name", "") + " " + t.get("category", {}).get("name", "")).lower()
    return any(kw in text for kw in ALLOWED_KEYWORDS)


def form_bar(wins: int, total: int) -> str:
    if total == 0:
        return "–"
    filled = round((wins / total) * 5)
    return "●" * filled + "○" * (5 - filled)


def format_score(event: dict) -> str:
    hs = event.get("homeScore", {})
    as_ = event.get("awayScore", {})
    hc  = hs.get("current")
    ac  = as_.get("current")
    if hc is not None and ac is not None:
        return f"*{hc}–{ac}*"
    return ""


def format_event_line(event: dict) -> str:
    home   = event.get("homeTeam", {}).get("name", "?")
    away   = event.get("awayTeam", {}).get("name", "?")
    score  = format_score(event)
    league = event.get("tournament", {}).get("name", "")
    ts     = event.get("startTimestamp")
    time_str = ""
    if ts:
        dt = datetime.fromtimestamp(ts, tz=HU_TZ)
        time_str = f" [{dt.strftime('%H:%M')}]"
    score_part = f" {score}" if score else ""
    return f"🏓 {home} vs {away}{score_part}{time_str} — _{league}_"


def fetch_match_result(event_id: int):
    try:
        r = _ss_get(f"https://api.sofascore.app/api/v1/event/{event_id}", timeout=8)
        if r.status_code != 200:
            return None
        event       = r.json().get("event", {})
        status_type = event.get("status", {}).get("type", "").lower()
        if status_type in ("postponed", "canceled", "cancelled", "abandoned", "interrupted"):
            return "postponed"
        if status_type != "finished":
            return None
        hs  = event.get("homeScore", {}).get("current", 0) or 0
        as_ = event.get("awayScore", {}).get("current", 0) or 0
        if hs > as_:
            return "home"
        elif as_ > hs:
            return "away"
        return None
    except Exception:
        return None


def resolve_pending_tips(tips: list) -> list:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    for t in tips:
        if t.get("result") is not None:
            continue
        if t.get("start_timestamp", 0) + 45 * 60 > now_ts:
            continue
        actual = fetch_match_result(t["event_id"])
        if actual is not None:
            result           = "win" if actual == t["predicted"] else "loss"
            t["actual_winner"] = actual
            t["result"]        = result
            update_tip_result(t["event_id"], result, actual)
    return tips


# ══════════════════════════════════════════════════════════════════════════════
#  TIPP ÜZENET ÖSSZEÁLLÍTÁSA
# ══════════════════════════════════════════════════════════════════════════════


def build_tip_message(event: dict, all_events: list) -> tuple:
    """
    Visszaad: (üzenet | None, szorzó | None, meta_dict | None)
    """
    home         = event.get("homeTeam", {}).get("name", "?")
    away         = event.get("awayTeam", {}).get("name", "?")
    home_team_id = event.get("homeTeam", {}).get("id")
    away_team_id = event.get("awayTeam", {}).get("id")
    event_id     = event.get("id")
    league       = event.get("tournament", {}).get("name", "?")
    category     = event.get("tournament", {}).get("category", {}).get("name", "")
    ts           = event.get("startTimestamp")
    time_str     = datetime.fromtimestamp(ts, tz=HU_TZ).strftime("%H:%M") if ts else "?"

    odds = sofascore_fetch_odds(event_id) if event_id else None

    h2h_home_wins, h2h_total = 0, 0
    if event_id:
        h2h_home_wins, h2h_total = sofascore_fetch_h2h(event_id, home, away)

    if home_team_id:
        (home_w, home_t), (home_fs_w, home_fs_t) = sofascore_fetch_player_stats(home_team_id, last=10)
    else:
        home_w = home_t = home_fs_w = home_fs_t = 0

    if away_team_id:
        (away_w, away_t), (away_fs_w, away_fs_t) = sofascore_fetch_player_stats(away_team_id, last=10)
    else:
        away_w = away_t = away_fs_w = away_fs_t = 0

    winner, confidence, score = calculate_tip(
        h2h_home_wins, h2h_total,
        home_w, home_t,
        away_w, away_t,
        home_fs_w, home_fs_t,
        away_fs_w, away_fs_t,
    )

    if confidence != "🟢 Erős tipp":
        return None, None, None

    tip_odds = None
    if odds:
        tip_odds = odds["home"] if winner == "home" else odds["away"]
    if tip_odds is None:
        tip_odds = 1.62

    if tip_odds < MIN_ODDS:
        return None, tip_odds, None

    predicted_name = home if winner == "home" else away
    tip_meta = {
        "event_id":       event_id,
        "home":           home,
        "away":           away,
        "league":         league,
        "predicted":      winner,
        "predicted_name": predicted_name,
        "odds":           tip_odds,
        "start_timestamp": ts,
        "sent_at":        int(datetime.now(timezone.utc).timestamp()),
        "result":         None,
        "actual_winner":  None,
    }

    tip_str = f"🏓 *Tipp: {predicted_name}* győz"
    h2h_str = f"{h2h_home_wins}–{h2h_total - h2h_home_wins}" if h2h_total >= 5 else "nincs adat"

    if odds:
        odds_str = f"💰 Szorzó: {home} *{odds['home']:.2f}* | {away} *{odds['away']:.2f}*"
        odds_str += f"\n💵 Tippelt szorzó: *{tip_odds:.2f}*"
    else:
        odds_str = "💰 Szorzó: _nem elérhető_"

    home_fs_rate_pct = round(home_fs_w / home_fs_t * 100) if home_fs_t >= 5 else None
    away_fs_rate_pct = round(away_fs_w / away_fs_t * 100) if away_fs_t >= 5 else None
    first_set_str = None
    if home_fs_rate_pct is not None and away_fs_rate_pct is not None:
        first_set_str = f"1️⃣ 1. szett: {home} *{home_fs_rate_pct}%* | {away} *{away_fs_rate_pct}%*"

    home_form_pct = round(home_w / home_t * 100) if home_t > 0 else 0
    away_form_pct = round(away_w / away_t * 100) if away_t > 0 else 0

    lines = [
        f"🔶 *{home}* vs *{away}*",
        f"📍 {league} ({category}) | 🕐 {time_str}",
        f"",
        odds_str,
        f"🔁 H2H (utolsó 5): {h2h_str}",
        f"📈 Forma (utolsó 10): {home} {form_bar(home_w, home_t)} *{home_form_pct}%* | {away} {form_bar(away_w, away_t)} *{away_form_pct}%*",
    ]
    if first_set_str:
        lines.append(first_set_str)
    lines += [
        f"",
        tip_str,
        f"{confidence} (pontszám: {score:+.1f})",
    ]
    return "\n".join(lines), tip_odds, tip_meta


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM PARANCSOK
# ══════════════════════════════════════════════════════════════════════════════


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Asztalitenisz Bot — Parancsok*\n\n"
        "🏓 *Setka Cup & Czech Liga:*\n"
        "/tt\\_tippek — Közelgő meccsek elemzése és tippek\n"
        "/tt\\_elo — Élő meccsek\n"
        "/tt\\_mai — Mai összes meccs\n"
        "/tt\\_eredmenyek — Mai eredmények\n"
        "/tt\\_ranglista — Legjobb játékosok ma\n"
        "/tt\\_statisztika — Nyerési arány és tipp előzmények\n"
        "/lezar — Függő tippek manuális lezárása\n\n"
        "ℹ️ /help — Súgó"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_tt_tippek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 *Setka Cup & Czech Liga* — meccsek elemzése...",
        parse_mode=ParseMode.MARKDOWN,
    )

    events = []
    used_date = None
    filtered_events = []
    for delta in [-1, 0, 1]:
        check_date = (date.today() + timedelta(days=delta)).isoformat()
        all_ev     = sofascore_fetch_events(check_date)
        filtered   = [e for e in all_ev if is_allowed(e)]
        if filtered:
            events          = all_ev
            filtered_events = filtered
            used_date       = check_date
            break

    # Ha semmi nincs a scheduled-events-ben, próbáljuk a live végpontot
    if not filtered_events:
        live_ev = sofascore_fetch_live_events()
        filtered_live = [e for e in live_ev if is_allowed(e)]
        if filtered_live:
            events = live_ev
            filtered_events = filtered_live
            used_date = date.today().isoformat()

    if not filtered_events:
        await update.message.reply_text("❌ Nem találtam Setka Cup vagy Czech Liga meccseket (tegnap/ma/holnap).")
        return

    if used_date != date.today().isoformat():
        await update.message.reply_text(
            f"ℹ️ Ma nincs meccs — holnapi ({used_date}) meccseket elemzem..."
        )

    upcoming = [e for e in filtered_events if e.get("status", {}).get("type", "").lower() == "notstarted"]
    if not upcoming:
        upcoming = [e for e in filtered_events if e.get("status", {}).get("type", "").lower() != "finished"]
    if not upcoming:
        upcoming = filtered_events

    league_names = sorted({e.get("tournament", {}).get("name", "?") for e in upcoming})
    await update.message.reply_text(
        f"📊 *{min(len(upcoming), 8)} meccs* elemzése — {', '.join(league_names[:3])}\n_(kérlek várj...)_",
        parse_mode=ParseMode.MARKDOWN,
    )

    sent = 0
    skipped = 0
    for event in upcoming[:15]:
        try:
            msg, tip_odds, tip_meta = build_tip_message(event, events)
            if msg is None:
                skipped += 1
                continue
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            if tip_meta:
                save_tip_record(tip_meta)
            sent += 1
            if sent >= 8:
                break
        except Exception as e:
            logger.error(f"Tipp hiba: {e}")

    if sent == 0:
        m = f"❌ Nem találtam {MIN_ODDS:.2f}+ szorzójú tippet az elemzett meccsekből."
        if skipped:
            m += f"\n_{skipped} meccs kiszűrve (alacsony szorzó vagy bizonytalan statisztika)._"
        await update.message.reply_text(m, parse_mode=ParseMode.MARKDOWN)
    else:
        footer = f"✅ *{sent} tipp generálva* (min. {MIN_ODDS:.2f}+ szorzó)"
        if skipped:
            footer += f"\n_({skipped} meccs kiszűrve)_"
        footer += "\n\n⚠️ _A tippek statisztikai elemzésen alapulnak. Felelősen fogadj!_"
        await update.message.reply_text(footer, parse_mode=ParseMode.MARKDOWN)


async def cmd_tt_elo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Élő meccsek keresése...")
    all_live = sofascore_fetch_live_events()
    live = [e for e in all_live if is_allowed(e)]

    if not live:
        msg = "🏓 Jelenleg nincs élő Setka Cup vagy Czech Liga meccs."
        if all_live:
            msg += f"\n_(Más asztalitenisz ligában {len(all_live)} élő meccs van.)_"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    lines = [f"🔴 *Élő meccsek — Setka Cup / Czech Liga ({len(live)})*\n"]
    for e in live[:20]:
        home   = e.get("homeTeam", {}).get("name", "?")
        away   = e.get("awayTeam", {}).get("name", "?")
        score  = format_score(e)
        league = e.get("tournament", {}).get("name", "")
        lines.append(f"🏓 {home} {score} {away} — _{league}_")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_tt_mai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Mai meccsek lekérése...")
    today  = date.today().isoformat()
    events = [e for e in sofascore_fetch_events(today) if is_allowed(e)]

    if not events:
        await update.message.reply_text(f"📅 Ma ({today}) nincs Setka Cup / Czech Liga meccs.")
        return

    leagues: dict = {}
    for e in events:
        name = e.get("tournament", {}).get("name", "?")
        leagues.setdefault(name, []).append(e)

    lines = [f"📅 *Mai meccsek — Setka Cup / Czech Liga ({today})*\n*Összesen: {len(events)} meccs*\n"]
    for league_name, lg_events in list(leagues.items())[:5]:
        lines.append(f"\n🏆 *{league_name}* ({len(lg_events)} meccs)")
        for e in lg_events[:6]:
            lines.append(format_event_line(e))
        if len(lg_events) > 6:
            lines.append(f"  _...és még {len(lg_events) - 6} meccs_")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_tt_eredmenyek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Eredmények lekérése...")
    today    = date.today().isoformat()
    finished = [
        e for e in sofascore_fetch_events(today)
        if is_allowed(e) and e.get("status", {}).get("type", "").lower() == "finished"
    ]

    if not finished:
        await update.message.reply_text("❌ Ma még nincs befejezett Setka Cup / Czech Liga meccs.")
        return

    lines = [f"✅ *Mai eredmények — Setka Cup / Czech Liga ({len(finished)} meccs)*\n"]
    for e in finished[:20]:
        home   = e.get("homeTeam", {}).get("name", "?")
        away   = e.get("awayTeam", {}).get("name", "?")
        hs     = e.get("homeScore", {}).get("current", "?")
        as_    = e.get("awayScore", {}).get("current", "?")
        league = e.get("tournament", {}).get("name", "")
        icon   = "🏆" if (hs or 0) > (as_ or 0) else "  "
        lines.append(f"{icon} *{home}* {hs}–{as_} *{away}* — _{league}_")

    if len(finished) > 20:
        lines.append(f"\n_...és még {len(finished) - 20} eredmény_")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_tt_ranglista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Mai statisztikák összesítése...")
    events = [
        e for e in sofascore_fetch_multi_day(days=3)
        if is_allowed(e) and e.get("status", {}).get("type", "").lower() == "finished"
    ]

    if not events:
        await update.message.reply_text("❌ Nincs elég befejezett meccs a ranglistához (tegnap/ma/holnap).")
        return

    stats: dict = {}
    for e in events:
        home = e.get("homeTeam", {}).get("name", "?")
        away = e.get("awayTeam", {}).get("name", "?")
        hs   = e.get("homeScore", {}).get("current") or 0
        as_  = e.get("awayScore", {}).get("current") or 0
        for player, won in [(home, hs > as_), (away, as_ > hs)]:
            if player not in stats:
                stats[player] = {"wins": 0, "losses": 0}
            if won:
                stats[player]["wins"] += 1
            else:
                stats[player]["losses"] += 1

    ranked = [
        (name, s["wins"], s["wins"] + s["losses"])
        for name, s in stats.items()
        if s["wins"] + s["losses"] >= 3
    ]
    ranked.sort(key=lambda x: (-x[1], -x[2]))

    if not ranked:
        await update.message.reply_text("❌ Nincs elég adat a ranglistához (min. 3 meccs szükséges).")
        return

    lines = [f"🏆 *Mai legjobb játékosok — Setka Cup / Czech Liga*\n_(min. 3 meccs)_\n"]
    for i, (name, wins, total) in enumerate(ranked[:15], 1):
        losses = total - wins
        pct    = round(wins / total * 100)
        lines.append(f"{i}. *{name}* — {wins}W / {losses}L ({pct}%)")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_tt_statisztika(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Statisztikák frissítése...", parse_mode=ParseMode.MARKDOWN)

    tips = load_tips()
    if not tips:
        await update.message.reply_text("📭 Még nincs mentett tipp.", parse_mode=ParseMode.MARKDOWN)
        return

    tips = resolve_pending_tips(tips)

    wins    = [t for t in tips if t.get("result") == "win"]
    losses  = [t for t in tips if t.get("result") == "loss"]
    pending = [t for t in tips if t.get("result") is None]

    total_settled = len(wins) + len(losses)
    win_rate      = (len(wins) / total_settled * 100) if total_settled > 0 else 0

    roi_total = 0.0; roi_count = 0
    for t in wins + losses:
        o = t.get("odds")
        if o:
            roi_total += (float(o) - 1) if t["result"] == "win" else -1
            roi_count += 1
    roi_pct = (roi_total / roi_count * 100) if roi_count > 0 else 0

    league_stats: dict = {}
    for t in tips:
        lg = t.get("league", "?")
        if lg not in league_stats:
            league_stats[lg] = {"wins": 0, "losses": 0, "pending": 0}
        r = t.get("result")
        if r == "win":
            league_stats[lg]["wins"] += 1
        elif r == "loss":
            league_stats[lg]["losses"] += 1
        else:
            league_stats[lg]["pending"] += 1

    recent = sorted(tips, key=lambda t: t.get("sent_at", 0), reverse=True)[:5]
    recent_lines = []
    for t in recent:
        r        = t.get("result")
        icon     = "✅" if r == "win" else ("❌" if r == "loss" else "⏳")
        odds_str = f" @ {float(t['odds']):.2f}" if t.get("odds") else ""
        recent_lines.append(f"{icon} {t['predicted_name']}{odds_str} — {t['home']} vs {t['away']}")

    lines = [
        f"📊 *Tipp statisztikák*\n",
        f"🎯 Összes tipp: *{len(tips)}* ({total_settled} lezárt, {len(pending)} folyamatban)",
        f"✅ Nyert: *{len(wins)}* | ❌ Veszített: *{len(losses)}*",
        f"📈 Nyerési arány: *{win_rate:.1f}%*",
    ]
    if roi_count > 0:
        roi_icon = "📈" if roi_pct >= 0 else "📉"
        lines.append(f"{roi_icon} Becsült ROI: *{roi_pct:+.1f}%* (1 egységes tét alapján)")

    if league_stats:
        lines.append("\n*Ligánkénti bontás:*")
        for lg, s in league_stats.items():
            tot = s["wins"] + s["losses"]
            pct = f"{s['wins'] / tot * 100:.0f}%" if tot > 0 else "–"
            lines.append(f"• {lg}: {s['wins']}W / {s['losses']}L ({pct}) | ⏳ {s['pending']}")

    if recent_lines:
        lines.append("\n*Utolsó 5 tipp:*")
        lines.extend(recent_lines)

    lines.append("\n⚠️ _Statisztikai elemzésen alapul. Felelősen fogadj!_")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_lezar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips    = load_tips()
    pending = [t for t in tips if t.get("result") is None]
    if not pending:
        await update.message.reply_text("Nincs függőben lévő tipp.")
        return
    for t in pending:
        predicted_name = t.get("predicted_name", t.get("predicted", "?"))
        odds_text      = f"{float(t['odds']):.2f}" if t.get("odds") else "N/A"
        eid            = t["event_id"]
        keyboard       = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Nyert",    callback_data=f"lezar_win_{eid}"),
            InlineKeyboardButton("❌ Veszett",  callback_data=f"lezar_loss_{eid}"),
            InlineKeyboardButton("⚠️ Elmaradt", callback_data=f"lezar_postponed_{eid}"),
        ]])
        await update.message.reply_text(
            f"🏓 *{t['home']} vs {t['away']}*\n"
            f"🏆 {t.get('league', '?')}\n"
            f"🎯 Tipp: *{predicted_name}* @ {odds_text}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )


async def callback_lezar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 2)
    if len(parts) != 3:
        return
    _, action, event_id_str = parts
    try:
        event_id = int(event_id_str)
    except ValueError:
        return

    tips = load_tips()
    tip  = next((t for t in tips if str(t["event_id"]) == str(event_id)), None)
    if not tip:
        await query.edit_message_text("Tipp nem található.")
        return

    if action == "win":
        update_tip_result(event_id, "win", tip.get("predicted", "home"))
        label = "✅ Nyertként lezárva"
    elif action == "loss":
        other = "away" if tip.get("predicted") == "home" else "home"
        update_tip_result(event_id, "loss", other)
        label = "❌ Veszettként lezárva"
    elif action == "postponed":
        update_tip_result(event_id, "postponed", "postponed")
        label = "⚠️ Elmaradtként lezárva"
    else:
        return

    predicted_name = tip.get("predicted_name", tip.get("predicted", "?"))
    await query.edit_message_text(
        f"{label}\n\n🏓 {tip['home']} vs {tip['away']}\n"
        f"🏆 {tip.get('league', '?')}\n🎯 Tipp: {predicted_name}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  AUTOMATIKUS INDÍTÁSI TIPPEK
# ══════════════════════════════════════════════════════════════════════════════


async def send_startup_tips(app):
    if not TELEGRAM_CHAT_ID:
        return

    logger.info("Startup tippek generálása (következő 8 óra)...")
    now_ts      = int(datetime.now(timezone.utc).timestamp())
    horizon_ts  = now_ts + 8 * 3600

    today  = date.today().isoformat()
    events = sofascore_fetch_events(today)

    if horizon_ts > int(datetime(datetime.now(timezone.utc).year, datetime.now(timezone.utc).month,
                                  datetime.now(timezone.utc).day, 23, 59, 59).timestamp()):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        events  += sofascore_fetch_events(tomorrow)

    upcoming_8h = [
        e for e in events
        if is_allowed(e)
        and e.get("status", {}).get("type", "").lower() == "notstarted"
        and now_ts <= e.get("startTimestamp", 0) <= horizon_ts
    ]
    upcoming_8h.sort(key=lambda e: e.get("startTimestamp", 0))

    if not upcoming_8h:
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="ℹ️ *Startup tippek:* A következő 8 órában nincs Setka Cup / Czech Liga meccs.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    league_names = list({e.get("tournament", {}).get("name", "?") for e in upcoming_8h})
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=(
            f"🤖 *Bot elindult — Automatikus tippek*\n"
            f"📅 Következő 8 óra ({len(upcoming_8h)} meccs elemzése)\n"
            f"📍 {', '.join(league_names[:3])}\n"
            f"🔕 _(Csak Erős, min. {MIN_ODDS:.2f}+ szorzó | H2H≥70% | 1.szett≥70% | forma Δ≥20%)_"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    already_sent_ids = {t["event_id"] for t in load_tips()}
    sent = 0
    for event in upcoming_8h:
        if sent >= MAX_STARTUP_TIPS:
            break
        event_id = event.get("id")
        if event_id and event_id in already_sent_ids:
            continue
        try:
            msg, tip_odds, tip_meta = build_tip_message(event, events)
            if msg is None:
                continue
            if tip_meta:
                if not save_tip_record(tip_meta):
                    continue
            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
            sent += 1
        except Exception as e:
            logger.error(f"Startup tipp hiba: {e}")

    if sent == 0:
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"❌ Nincs megfelelő tipp a következő 8 órában.\n_(Minden meccs kiszűrve)_",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"✅ *{sent} startup tipp elküldve!*\n⚠️ _Felelősen fogadj!_",
            parse_mode=ParseMode.MARKDOWN,
        )

    logger.info(f"Startup tippek kész: {sent} tipp elküldve.")


# ══════════════════════════════════════════════════════════════════════════════
#  EREDMÉNY FIGYELŐ
# ══════════════════════════════════════════════════════════════════════════════


def _collect_results_sync() -> list:
    tips          = load_tips()
    now_ts        = int(datetime.now(timezone.utc).timestamp())
    notifications = []

    for t in tips:
        if t.get("result") is not None:
            continue
        if t.get("start_timestamp", 0) + 45 * 60 > now_ts:
            continue

        actual    = fetch_match_result(t["event_id"])
        if actual is None:
            continue

        odds_text      = f"{float(t['odds']):.2f}" if t.get("odds") else "N/A"
        predicted_name = t.get("predicted_name", t.get("predicted", "?"))

        if actual == "postponed":
            update_tip_result(t["event_id"], "postponed", "postponed")
            msg = (
                f"⚠️ *Meccs elmaradt!*\n\n"
                f"🏓 {t['home']} vs {t['away']}\n"
                f"🏆 {t.get('league', '?')}\n"
                f"🎯 Tippünk: *{predicted_name}*\n"
                f"📊 Szorzó: {odds_text}\n"
                f"ℹ️ A meccs elmaradt — tipp érvénytelen."
            )
        else:
            result = "win" if actual == t["predicted"] else "loss"
            update_tip_result(t["event_id"], result, actual)
            won         = result == "win"
            icon        = "✅" if won else "❌"
            result_text = "NYERT" if won else "VESZETT"
            msg = (
                f"{icon} *Eredmény — {result_text}!*\n\n"
                f"🏓 {t['home']} vs {t['away']}\n"
                f"🏆 {t.get('league', '?')}\n"
                f"🎯 Tippünk: *{predicted_name}*\n"
                f"📊 Szorzó: {odds_text}\n"
                f"🏆 Tényleges győztes: {t['home'] if actual == 'home' else t['away']}"
            )

        notifications.append((t["event_id"], msg))

    return notifications


async def check_results_and_notify(context):
    if not TELEGRAM_CHAT_ID:
        return
    notifications = await asyncio.to_thread(_collect_results_sync)
    for event_id, msg in notifications:
        try:
            await context.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
            logger.info(f"Eredmény értesítő elküldve: event_id={event_id}")
        except Exception as e:
            logger.error(f"Eredmény értesítő hiba: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  FOLYAMATOS TIPP FIGYELŐ (15 percenként)
# ══════════════════════════════════════════════════════════════════════════════


def _collect_new_tips_sync() -> list:
    now_ts     = int(datetime.now(timezone.utc).timestamp())
    horizon_ts = now_ts + 12 * 3600

    today    = date.today().isoformat()
    events   = sofascore_fetch_events(today)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    events  += sofascore_fetch_events(tomorrow)

    upcoming = [
        e for e in events
        if is_allowed(e)
        and e.get("status", {}).get("type", "").lower() == "notstarted"
        and now_ts <= e.get("startTimestamp", 0) <= horizon_ts
    ]
    if not upcoming:
        return []

    already_sent_ids = {t["event_id"] for t in load_tips()}
    results = []

    for event in upcoming:
        event_id = event.get("id")
        if event_id and event_id in already_sent_ids:
            continue
        try:
            msg, tip_odds, tip_meta = build_tip_message(event, events)
            if msg is None:
                continue
            results.append((msg, tip_meta))
            if tip_meta and event_id:
                already_sent_ids.add(event_id)
        except Exception as e:
            logger.error(f"Tipp építés hiba: {e}")

    return results


async def scan_and_send_tips(context):
    if not TELEGRAM_CHAT_ID:
        return

    tips_to_send = await asyncio.to_thread(_collect_new_tips_sync)

    for msg, tip_meta in tips_to_send:
        try:
            if tip_meta:
                if not save_tip_record(tip_meta):
                    continue
            await context.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
            logger.info(f"Automatikus tipp elküldve: event_id={tip_meta.get('event_id') if tip_meta else '?'}")
        except Exception as e:
            logger.error(f"Automatikus tipp hiba: {e}")

    if tips_to_send:
        logger.info(f"Scan kész: {len(tips_to_send)} új tipp elküldve.")
    else:
        logger.debug("Scan kész: nincs új tipp.")


# ══════════════════════════════════════════════════════════════════════════════
#  FŐPROGRAM
# ══════════════════════════════════════════════════════════════════════════════


def main():
    logger.info("Asztalitenisz Bot indul...")
    init_db()

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(send_startup_tips)
        .build()
    )

    app.add_handler(CommandHandler("start",           cmd_start))
    app.add_handler(CommandHandler("help",            cmd_help))
    app.add_handler(CommandHandler("tt_tippek",       cmd_tt_tippek))
    app.add_handler(CommandHandler("tt_elo",          cmd_tt_elo))
    app.add_handler(CommandHandler("tt_mai",          cmd_tt_mai))
    app.add_handler(CommandHandler("tt_eredmenyek",   cmd_tt_eredmenyek))
    app.add_handler(CommandHandler("tt_ranglista",    cmd_tt_ranglista))
    app.add_handler(CommandHandler("tt_statisztika",  cmd_tt_statisztika))
    app.add_handler(CommandHandler("lezar",           cmd_lezar))
    app.add_handler(CallbackQueryHandler(callback_lezar, pattern=r"^lezar_"))

    if app.job_queue:
        app.job_queue.run_repeating(scan_and_send_tips,        interval=SCAN_INTERVAL_SEC, first=300)
        app.job_queue.run_repeating(check_results_and_notify,  interval=600,               first=120)
        logger.info(f"Automatikus tipp figyelő: {SCAN_INTERVAL_SEC}s | Eredmény figyelő: 600s")
    else:
        logger.warning("JobQueue nem elérhető – automatikus figyelők kikapcsolva.")

    logger.info("Bot fut. Liga: Setka Cup + Czech Liga (SofaScore)")
    logger.info(f"Szűrők: H2H≥{MIN_H2H_RATE*100:.0f}% | 1.szett≥{MIN_FIRST_SET_RATE*100:.0f}% | formaΔ≥{MIN_FORM_DIFF*100:.0f}% | min.odds={MIN_ODDS}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
