"""
Coupon Bot
- SofaScore: szorzók, H2H + forma megerősítés
- Cél: ~2.0 összesített szorzó, 2-3 meccs/szelvény
- Küldési ablak: 08:00-20:00 (Budapest)
"""

import os
import sys
import json
import logging
import requests
import psycopg2

if os.environ.get("COUPON_BOT_DISABLED", "").lower() in ("1", "true", "yes"):
    print("COUPON_BOT_DISABLED=true — kilépés.")
    sys.exit(0)
import psycopg2.extras
import asyncio
from datetime import datetime, timezone
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
from itertools import combinations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

HU_TZ = ZoneInfo("Europe/Budapest")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

COUPON_BOT_TOKEN = os.environ["COUPON_BOT_TOKEN"]
COUPON_CHAT_ID = os.environ.get("COUPON_CHAT_ID", "")
SUPABASE_DB_URL = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL", "")

SOFASCORE_BASE = "https://api.sofascore.com/api/v1"
ODDS_API_KEY  = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_SPORTS = [
    "soccer_epl",
    "soccer_germany_bundesliga",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "basketball_nba",
    "basketball_euroleague",
    "icehockey_nhl",
]
ODDS_QUOTA_FILE = "odds_quota_state.json"
ODDS_CACHE_FILE  = "odds_api_daily_cache.json"

SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8",
}

MIN_PICK_ODDS = 1.33      # emelt: 1.28 → 1.33 (könyves 75% max)
MAX_PICK_ODDS = 1.60      # emelt: 1.50 → 1.60 (több edge lehetőség)
TARGET_COMBINED = 2.00
MIN_COMBINED = 1.80       # emelt: 1.75 → 1.80
MAX_COMBINED = 2.60
MIN_PICKS = 2
MAX_PICKS = 3
MIN_BOOKMAKERS = 1
MAX_ODDS_STD = 0.10       # szigorítva: 0.12 → 0.10
MAX_EVENTS_PER_SPORT = 30
MAX_TOTAL_EVENTS = 120    # több sport → több esemény
MIN_H2H_RATE     = 0.65   # H2H arány minimum (külön, nem átlag)
MIN_FORM_RATE    = 0.65   # Forma arány minimum (külön, nem átlag)
MIN_PICKED_FORM  = 0.70   # A KIVÁLASZTOTT csapat formája minimum

SOFA_SPORTS = [
    "football",
    "basketball",
    "ice-hockey",
]

SOFA_TO_EMOJI = {
    "football": "⚽",
    "basketball": "🏀",
    "ice-hockey": "🏒",
    "baseball": "⚾",
    "tennis": "🎾",
}

SPORT_UNIT = {
    "football": "gól",
    "basketball": "pont",
    "ice-hockey": "gól",
    "baseball": "fut",
    "tennis": "játék",
}

SPORT_EMOJI = SOFA_TO_EMOJI  # backward compat for format_coupon

application = None


# ── Database ──────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(SUPABASE_DB_URL)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS coupons (
                    id              SERIAL PRIMARY KEY,
                    coupon_number   INTEGER NOT NULL DEFAULT 0,
                    picks           JSONB NOT NULL,
                    combined_odds   REAL NOT NULL,
                    sent_at         BIGINT NOT NULL,
                    result          TEXT DEFAULT NULL,
                    settled_at      BIGINT DEFAULT NULL
                )
            """)
            conn.commit()
    logger.info("coupons tábla inicializálva")


def get_next_coupon_number():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(coupon_number), 0) + 1 FROM coupons")
            return cur.fetchone()[0]


def save_coupon(picks, combined_odds):
    number = get_next_coupon_number()
    sent_at = int(datetime.utcnow().timestamp())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO coupons (coupon_number, picks, combined_odds, sent_at) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (number, json.dumps(picks), combined_odds, sent_at),
            )
            conn.commit()
    return number


def get_pending_coupons():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM coupons WHERE result IS NULL ORDER BY sent_at DESC")
            rows = cur.fetchall()
    result = []
    for row in rows:
        r = dict(row)
        if isinstance(r["picks"], str):
            r["picks"] = json.loads(r["picks"])
        result.append(r)
    return result


def update_coupon_result(coupon_id, result):
    settled_at = int(datetime.utcnow().timestamp())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE coupons SET result=%s, settled_at=%s WHERE id=%s",
                (result, settled_at, coupon_id),
            )
            conn.commit()


def update_pick_result(coupon_id, event_id, result):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT picks FROM coupons WHERE id=%s", (coupon_id,))
            row = cur.fetchone()
            if not row:
                return
            picks = row[0] if isinstance(row[0], list) else json.loads(row[0])
            for p in picks:
                if p.get("event_id") == event_id:
                    p["result"] = result
            cur.execute("UPDATE coupons SET picks=%s WHERE id=%s", (json.dumps(picks), coupon_id))
            conn.commit()


def get_sent_event_ids():
    """Return event IDs that should not appear in a new coupon.
    Rules:
    - Any pick from a still-pending coupon (result IS NULL) is always excluded.
    - Any pick sent within the last 48 hours is also excluded (catches settled ones).
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cutoff = int(datetime.utcnow().timestamp()) - 48 * 3600
                cur.execute(
                    "SELECT picks FROM coupons WHERE result IS NULL OR sent_at > %s",
                    (cutoff,)
                )
                rows = cur.fetchall()
        ids = set()
        for (picks,) in rows:
            data = picks if isinstance(picks, list) else json.loads(picks)
            for p in data:
                ids.add(p.get("event_id", ""))
        return ids
    except Exception as e:
        logger.error(f"get_sent_event_ids error: {e}")
        return set()


# ── SofaScore odds & events ───────────────────────────────────────────────────

def frac_to_dec(frac: str) -> float | None:
    """Convert '9/4' → 3.25, or plain decimal string → float."""
    try:
        if "/" in frac:
            n, d = frac.split("/")
            return round(1 + int(n) / int(d), 3)
        return round(float(frac), 3)
    except (ValueError, ZeroDivisionError, AttributeError):
        return None


FRIENDLY_KEYWORDS = {"friendly", "friendlies", "exhibition", "test match", "preparation"}

def fetch_sofa_events(sport: str, date_str: str) -> list:
    """Return notstarted events 2–48 h from now for a sport/date. Barátságos meccsek kizárva."""
    now_ts = datetime.utcnow().timestamp()
    data = sofa_get(f"{SOFASCORE_BASE}/sport/{sport}/scheduled-events/{date_str}")
    if not data:
        return []
    out = []
    for ev in data.get("events", []):
        if ev.get("status", {}).get("type") != "notstarted":
            continue
        ts = ev.get("startTimestamp", 0)
        if not (now_ts + 7200 <= ts <= now_ts + 48 * 3600):
            continue
        # ── Barátságos meccsek kizárása ──────────────────────────────────────
        t_name = ev.get("tournament", {}).get("name", "").lower()
        if any(kw in t_name for kw in FRIENDLY_KEYWORDS):
            continue
        ev["_sofa_sport"] = sport
        out.append(ev)
    return out


def fetch_sofa_odds_markets(event_id: int) -> list:
    """Return the markets list from /event/{id}/odds/1/all."""
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/odds/1/all")
    return data.get("markets", []) if data else []


def extract_market_picks_sofa(event: dict, markets: list) -> list:
    """
    Parse SofaScore markets and return candidate picks.
    Markets: Full time (h2h), Double chance, Match goals (totals),
             Asian handicap (spreads).
    Returns list of {market, outcome_key, pick_label, odds, line}.
    """
    home = event.get("homeTeam", {}).get("name", "")
    away = event.get("awayTeam", {}).get("name", "")
    sport = event.get("_sofa_sport", "football")

    picks = []

    for market in markets:
        mname = market.get("marketName", "").lower()
        cgroup = market.get("choiceGroup", "")      # e.g. "2.5" for Match goals
        period = market.get("marketPeriod", "")     # "Full-time" vs "1st half" etc.

        # Only full-time markets
        if period and "full" not in period.lower():
            continue

        for choice in market.get("choices", []):
            name = choice.get("name", "")
            frac = choice.get("fractionalValue", "")
            odds = frac_to_dec(frac)
            if odds is None or not (MIN_PICK_ODDS <= odds <= MAX_PICK_ODDS):
                continue

            # ── Full time (h2h) ──────────────────────────────────────────────
            if "full time" in mname or mname in ("winner", "home/away", "moneyline"):
                if name == "1":
                    picks.append({"market": "h2h", "outcome_key": "home",
                                  "pick_label": f"{home} győz", "odds": odds, "line": None})
                elif name == "2":
                    picks.append({"market": "h2h", "outcome_key": "away",
                                  "pick_label": f"{away} győz", "odds": odds, "line": None})
                elif name in ("X", "Draw"):
                    picks.append({"market": "h2h", "outcome_key": "draw",
                                  "pick_label": "Döntetlen", "odds": odds, "line": None})

            # ── Double chance ────────────────────────────────────────────────
            elif "double chance" in mname:
                dc = {"1X": (f"{home} vagy döntetlen (1X)", "1X"),
                      "X2": (f"Döntetlen vagy {away} (X2)", "X2"),
                      "12": (f"{home} vagy {away} (12)", "12")}
                if name in dc:
                    label, okey = dc[name]
                    picks.append({"market": "double_chance", "outcome_key": okey,
                                  "pick_label": label, "odds": odds, "line": None})

            # ── Match goals / Total points (over/under) ──────────────────────
            elif "match goals" in mname or "total" in mname:
                try:
                    line = float(cgroup)
                except (ValueError, TypeError):
                    continue
                unit = "gól" if sport == "football" else "pont"
                if name.lower() == "over":
                    picks.append({"market": "totals", "outcome_key": f"over_{line}",
                                  "pick_label": f"Több mint {line} {unit}", "odds": odds, "line": line})
                elif name.lower() == "under":
                    picks.append({"market": "totals", "outcome_key": f"under_{line}",
                                  "pick_label": f"Kevesebb mint {line} {unit}", "odds": odds, "line": line})

            # ── Asian handicap ───────────────────────────────────────────────
            elif "asian handicap" in mname or "handicap" in mname:
                import re
                m = re.match(r"\(([+-]?\d+(?:\.\d+)?)\)\s+(.+)", name)
                if not m:
                    continue
                hval = float(m.group(1))
                team_name = m.group(2).strip()
                sign = f"+{hval}" if hval > 0 else str(hval)
                if team_name.lower() in home.lower() or home.lower() in team_name.lower():
                    picks.append({"market": "spreads",
                                  "outcome_key": f"home_spread_{hval}",
                                  "pick_label": f"{home} ({sign}) hendikep",
                                  "odds": odds, "line": hval})
                elif team_name.lower() in away.lower() or away.lower() in team_name.lower():
                    picks.append({"market": "spreads",
                                  "outcome_key": f"away_spread_{hval}",
                                  "pick_label": f"{away} ({sign}) hendikep",
                                  "odds": odds, "line": hval})

    return picks


# ── SofaScore H2H + Form ──────────────────────────────────────────────────────

def sofa_get(url):
    try:
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return None


def search_sofa_event(home_team, away_team, start_ts=None):
    query = requests.utils.quote(home_team[:20])
    data = sofa_get(f"{SOFASCORE_BASE}/search/multi/{query}")
    if not data:
        return None
    events = data.get("events", [])
    away_lower = away_team.lower()
    for e in events:
        a = e.get("awayTeam", {}).get("name", "").lower()
        if not any(token in a for token in away_lower.split()[:2] if len(token) > 3):
            continue
        if start_ts:
            ev_ts = e.get("startTimestamp", 0)
            if abs(ev_ts - start_ts) > 86400:
                continue
        return e.get("id"), e.get("homeTeam", {}).get("id"), e.get("awayTeam", {}).get("id")
    return None


_KNOWN_SOFA_SPORTS = {"football", "basketball", "ice-hockey", "american-football", "tennis", "baseball", "mma"}
_LEGACY_PREFIX_MAP = {
    "soccer": "football", "basketball": "basketball",
    "americanfootball": "american-football", "baseball": "baseball",
    "hockey": "ice-hockey", "tennis": "tennis", "mma": "mma",
}


def _sport_key_to_sofa_sport(sport_key):
    """Handles both new SofaScore sport names and legacy Odds API prefixes."""
    if sport_key in _KNOWN_SOFA_SPORTS:
        return sport_key
    prefix = sport_key.split("_")[0].lower()
    return _LEGACY_PREFIX_MAP.get(prefix, "football")


def get_sofa_result(home_team, away_team, start_ts, sport_key="soccer"):
    """
    Returns (home_score, away_score) if the match is finished, else None.
    Uses SofaScore scheduled-events (free, no quota, no API key needed).
    """
    from datetime import datetime as _dt
    date_str = _dt.utcfromtimestamp(start_ts).strftime("%Y-%m-%d")
    sofa_sport = _sport_key_to_sofa_sport(sport_key)

    data = sofa_get(f"{SOFASCORE_BASE}/sport/{sofa_sport}/scheduled-events/{date_str}")
    if not data:
        return None

    events = data.get("events", [])
    home_lower = home_team.lower()
    away_lower = away_team.lower()

    def _normalize(s):
        import unicodedata
        return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()

    def _name_match(stored, query):
        stored_n, query_n = _normalize(stored), _normalize(query)
        tokens = [t for t in query_n.split() if len(t) > 2]
        return any(t in stored_n or t[:5] in stored_n for t in tokens) if tokens else query_n in stored_n

    for ev in events:
        h_name = ev.get("homeTeam", {}).get("name", "").lower()
        a_name = ev.get("awayTeam", {}).get("name", "").lower()
        ev_ts = ev.get("startTimestamp", 0)
        home_ok = _name_match(h_name, home_lower)
        ts_ok = abs(ev_ts - start_ts) < 7200
        # Home team + timestamp sufficient — teams don't play twice in 2 hours
        if home_ok and ts_ok:
            if ev.get("status", {}).get("type") != "finished":
                return None
            h = ev.get("homeScore", {}).get("current")
            a = ev.get("awayScore", {}).get("current")
            if h is None or a is None:
                return None
            return int(h), int(a)

    return None


def get_h2h_win_rate(event_id, pick_side):
    """
    Returns H2H win rate of picked side (min 5 H2H meccs szükséges).
    pick_side: 'home' or 'away'
    """
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/h2h")
    if not data:
        return None

    all_events = data.get("events", [])
    if len(all_events) < 5:
        return None

    wins = 0
    total = 0
    for e in all_events[:10]:
        h_score = e.get("homeScore", {}).get("current")
        a_score = e.get("awayScore", {}).get("current")
        if h_score is None or a_score is None:
            continue
        total += 1
        if pick_side == "home" and h_score > a_score:
            wins += 1
        elif pick_side == "away" and a_score > h_score:
            wins += 1

    if total < 5:
        return None
    return wins / total


def get_recent_form(team_id):
    """Returns recent win rate (last 5 matches) for a team."""
    data = sofa_get(f"{SOFASCORE_BASE}/team/{team_id}/events/last/0")
    if not data:
        return None
    events = [e for e in data.get("events", [])
              if e.get("status", {}).get("type") == "finished"][:5]
    if len(events) < 3:
        return None
    wins = 0
    for e in events:
        h_score = e.get("homeScore", {}).get("current", 0)
        a_score = e.get("awayScore", {}).get("current", 0)
        home_team_id = e.get("homeTeam", {}).get("id")
        if home_team_id == team_id:
            if h_score > a_score:
                wins += 1
        else:
            if a_score > h_score:
                wins += 1
    return wins / len(events)


def get_team_over_rate(team_id: int, line: float, last_n: int = 7) -> float | None:
    """
    Visszaadja a csapat utolsó N meccsének over-arányát a megadott vonalra.
    Pl. line=2.5 → hány meccsben volt 3+ gól összesen.
    """
    data = sofa_get(f"{SOFASCORE_BASE}/team/{team_id}/events/last/0")
    if not data:
        return None
    events = [e for e in data.get("events", [])
              if e.get("status", {}).get("type") == "finished"][:last_n]
    if len(events) < 5:
        return None
    over_count = 0
    for e in events:
        h = e.get("homeScore", {}).get("current") or 0
        a = e.get("awayScore", {}).get("current") or 0
        if (h + a) > line:
            over_count += 1
    return over_count / len(events)


def verify_totals_pick(home_id: int, away_id: int, line: float, direction: str) -> bool | None:
    """
    Over/Under statisztikai szűrő:
    - Over: mindkét csapat utolsó 7 meccsének >= 70%-a over volt a vonalra
    - Under: mindkét csapat utolsó 7 meccsének <= 35%-a over volt (azaz 65%+ under)
    Returns True (megerősített) / False (ellentmond) / None (nincs adat)
    """
    home_rate = get_team_over_rate(home_id, line)
    away_rate = get_team_over_rate(away_id, line)

    if home_rate is None or away_rate is None:
        return None

    if direction == "over":
        if home_rate >= 0.75 and away_rate >= 0.75:
            return True
        elif home_rate < 0.55 or away_rate < 0.55:
            return False
    elif direction == "under":
        if home_rate <= 0.30 and away_rate <= 0.30:
            return True
        elif home_rate > 0.50 or away_rate > 0.50:
            return False
    return None


def verify_with_sofascore(home_team, away_team, pick_side):
    """
    H2H + forma szűrő — MINDKETTŐ külön kell, nem átlag.
    H2H >= MIN_H2H_RATE ÉS forma >= MIN_FORM_RATE → True
    Bármelyik hiányzik → None (elutasítva)
    Bármelyik alacsony → False
    Returns True / False / None.
    """
    sofa = search_sofa_event(home_team, away_team)
    if not sofa:
        return None

    event_id, home_id, away_id = sofa

    h2h_rate = get_h2h_win_rate(event_id, pick_side)
    picked_team_id = home_id if pick_side == "home" else away_id
    form_rate = get_recent_form(picked_team_id)

    # Mindkét signal szükséges
    if h2h_rate is None or form_rate is None:
        return None

    if h2h_rate >= MIN_H2H_RATE and form_rate >= MIN_FORM_RATE:
        return True
    elif h2h_rate < 0.45 or form_rate < 0.40:
        return False
    return None


# ── Odds API – kvóta kezelés + napi cache ────────────────────────────────────

def _load_quota_state() -> dict:
    try:
        with open(ODDS_QUOTA_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"exhausted": False, "remaining": 500, "exhausted_month": None}


def _save_quota_state(state: dict):
    try:
        with open(ODDS_QUOTA_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        logger.warning(f"Kvóta állapot mentés hiba: {e}")


def _is_odds_api_available() -> bool:
    """True ha az Odds API kulcs megvan és a kvóta nem merült ki."""
    if not ODDS_API_KEY:
        return False
    state = _load_quota_state()
    now = datetime.utcnow()
    # Hónap 2-án (vagy később) automatikus újraengedélyezés
    if state.get("exhausted") and now.day >= 2:
        if state.get("exhausted_month") != now.month:
            logger.info("Odds API kvóta: havi reset – újra engedélyezve")
            state["exhausted"] = False
            state["remaining"] = 500
            state["exhausted_month"] = None
            _save_quota_state(state)
    return not state.get("exhausted", False)


def _update_quota(headers: dict):
    """Frissíti a kvóta állapotát a válasz fejlécek alapján."""
    remaining_str = headers.get("x-requests-remaining", "")
    if not remaining_str:
        return
    try:
        remaining = int(remaining_str)
    except ValueError:
        return
    state = _load_quota_state()
    state["remaining"] = remaining
    if remaining <= 0:
        state["exhausted"] = True
        state["exhausted_month"] = datetime.utcnow().month
        logger.warning("Odds API kvóta elfogyott – visszaállás SofaScore-ra")
    else:
        logger.info(f"Odds API kvóta: {remaining} lekérés maradt")
    _save_quota_state(state)


def _load_odds_cache() -> list | None:
    """Visszaadja a mai napi cache-t, ha létezik."""
    try:
        with open(ODDS_CACHE_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") == datetime.utcnow().strftime("%Y-%m-%d"):
            return data.get("picks", [])
    except Exception:
        pass
    return None


def _save_odds_cache(picks: list):
    try:
        with open(ODDS_CACHE_FILE, "w") as f:
            json.dump({"date": datetime.utcnow().strftime("%Y-%m-%d"), "picks": picks}, f)
    except Exception as e:
        logger.warning(f"Odds cache mentés hiba: {e}")


def _fetch_odds_api_sport(sport_key: str) -> list:
    """Egy sport odds-ait kéri le az Odds API-tól. Visszaad nyers event listát."""
    try:
        r = requests.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/odds/",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "eu,uk",
                "markets": "h2h,totals",
                "oddsFormat": "decimal",
            },
            timeout=15,
        )
        _update_quota(r.headers)
        if r.status_code == 401:
            logger.warning("Odds API: érvénytelen kulcs (401)")
            return []
        if r.status_code in (402, 429):
            state = _load_quota_state()
            state["exhausted"] = True
            state["exhausted_month"] = datetime.utcnow().month
            _save_quota_state(state)
            logger.warning(f"Odds API kvóta limit ({r.status_code}) – visszaállás SofaScore-ra")
            return []
        if not r.ok:
            logger.warning(f"Odds API [{sport_key}]: HTTP {r.status_code}")
            return []
        return r.json()
    except Exception as ex:
        logger.error(f"Odds API hiba [{sport_key}]: {ex}")
        return []


def _parse_odds_api_event(ev: dict) -> list:
    """Kinyeri a pick jelölteket egy Odds API event objektumból."""
    home = ev.get("home_team", "")
    away = ev.get("away_team", "")
    sport_key = ev.get("sport_key", "soccer")
    start_ts = 0
    try:
        from datetime import datetime as _dt
        start_ts = int(_dt.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).timestamp())
    except Exception:
        pass

    h2h_home, h2h_away, h2h_draw = [], [], []
    totals_over: dict[float, list] = {}
    totals_under: dict[float, list] = {}

    for bm in ev.get("bookmakers", []):
        for market in bm.get("markets", []):
            mkey = market.get("key", "")
            if mkey == "h2h":
                for outcome in market.get("outcomes", []):
                    try:
                        price = float(outcome["price"])
                    except (KeyError, ValueError, TypeError):
                        continue
                    name = outcome.get("name", "")
                    if name == home:
                        h2h_home.append(price)
                    elif name == away:
                        h2h_away.append(price)
                    elif name.lower() in ("draw", "döntetlen"):
                        h2h_draw.append(price)
            elif mkey == "totals":
                for outcome in market.get("outcomes", []):
                    try:
                        price = float(outcome["price"])
                        line  = float(outcome["point"])
                    except (KeyError, ValueError, TypeError):
                        continue
                    direction = outcome.get("name", "").lower()
                    if direction == "over":
                        totals_over.setdefault(line, []).append(price)
                    elif direction == "under":
                        totals_under.setdefault(line, []).append(price)

    picks = []

    def _make(outcome_key, label, odds_list, market, line=None):
        if not odds_list:
            return
        avg = round(sum(odds_list) / len(odds_list), 3)
        n   = len(odds_list)
        std = round((sum((o - avg) ** 2 for o in odds_list) / n) ** 0.5 if n > 1 else 0.0, 4)
        _sofa_sport = _sport_key_to_sofa_sport(sport_key)
        if MIN_PICK_ODDS <= avg <= MAX_PICK_ODDS:
            picks.append({
                "event_id":    f"odds_{ev.get('id', '')}",
                "sport_key":   sport_key,
                "sport":       _sofa_sport,
                "home":        home,
                "away":        away,
                "market":      market,
                "outcome_key": outcome_key,
                "pick_name":   label,
                "line":        line,
                "odds":        avg,
                "std":         std,
                "n_bm":        n,
                "n_bookmakers": n,
                "start_timestamp": start_ts,
                "sofa_confirmed": False,
                "result":      None,
            })

    _make("home", f"{home} győz", h2h_home, "h2h")
    _make("away", f"{away} győz", h2h_away, "h2h")
    _make("draw", "Döntetlen",    h2h_draw,  "h2h")
    _unit = SPORT_UNIT.get(_sport_key_to_sofa_sport(sport_key), "gól")
    for line, ol in totals_over.items():
        _make(f"over_{line}",  f"Több mint {line} {_unit}",    ol, "totals", line)
    for line, ol in totals_under.items():
        _make(f"under_{line}", f"Kevesebb mint {line} {_unit}", ol, "totals", line)

    return picks


def collect_odds_api_picks(sent_ids: set) -> list:
    """
    Odds API pick-gyűjtő napi cache-sel.
    - Ha van mai cache → abból dolgozik (0 API hívás)
    - Ha nincs → lekéri az összes sportot (N API hívás), elmenti cache-be
    - Ha kvóta elfogyott → üres lista (SofaScore veszi át)
    """
    now_ts = datetime.utcnow().timestamp()

    # Napi cache próba
    cached = _load_odds_cache()
    if cached is not None:
        logger.info(f"Odds API: napi cache-ből dolgozik ({len(cached)} pick)")
        source = cached
    elif not _is_odds_api_available():
        logger.info("Odds API: kvóta elfogyott – SofaScore fallback aktív")
        return []
    else:
        # Friss lekérés
        all_events = []
        for sport_key in ODDS_API_SPORTS:
            if not _is_odds_api_available():
                logger.info("Odds API kvóta közben merült ki – abbahagyja a lekérést")
                break
            events = _fetch_odds_api_sport(sport_key)
            logger.info(f"Odds API [{sport_key}]: {len(events)} mérkőzés")
            all_events.extend(events)

        # Minden event-ből parse-olunk pick-et, cache-eljük
        source = []
        for ev in all_events:
            picks = _parse_odds_api_event(ev)
            source.extend(picks)
        _save_odds_cache(source)
        logger.info(f"Odds API: {len(source)} pick, cache elmentve")

    # Szűrés + pontozás
    candidates = []
    for p in source:
        if p["event_id"] in sent_ids:
            continue
        ts = p.get("start_timestamp", 0)
        if not (now_ts + 7200 <= ts <= now_ts + 48 * 3600):
            continue
        sc = score_pick(p["odds"], std=p.get("std", 0.0), n_bm=p.get("n_bm", 1))
        if sc <= 0:
            continue
        p["score"] = round(sc, 4)
        candidates.append(p)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"Odds API: {len(candidates)} jelölt tipp (szűrés után)")
    return candidates


# ── API-Football odds (multi-bookmaker futball) ──────────────────────────────

FOOTBALL_API_BASE = "https://api-football-v1.p.rapidapi.com/v3"
FOOTBALL_API_HEADERS = {
    "x-rapidapi-key": os.environ.get("SPORTS_API_KEY", ""),
    "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
}

# Bet ID-k: 1=Match Winner, 5=Goals Over/Under, 8=Asian Handicap
AFL_BET_MATCH_WINNER = 1
AFL_BET_GOALS_OU     = 5


def fetch_api_football_odds(date_str: str) -> list:
    """
    Lekéri az adott napra elérhető futball odds-okat az API-Football-ból.
    Visszaad: nyers response lista.
    """
    sports_key = os.environ.get("SPORTS_API_KEY", "")
    if not sports_key:
        return []
    try:
        r = requests.get(
            f"{FOOTBALL_API_BASE}/odds",
            headers=FOOTBALL_API_HEADERS,
            params={"date": date_str, "timezone": "UTC"},
            timeout=15,
        )
        if not r.ok:
            logger.warning(f"API-Football odds [{date_str}]: HTTP {r.status_code}")
            return []
        return r.json().get("response", [])
    except Exception as ex:
        logger.error(f"API-Football odds hiba: {ex}")
        return []


def parse_api_football_odds_item(item: dict) -> list:
    """
    Kinyeri a pick jelölteket egy API-Football odds objektumból.
    Visszaad: [{market, outcome_key, pick_label, odds, std, n_bm, line}]
    """
    home = item.get("teams", {}).get("home", {}).get("name", "")
    away = item.get("teams", {}).get("away", {}).get("name", "")
    if not home or not away:
        return []

    h2h_home_odds, h2h_away_odds = [], []
    totals_over: dict[float, list] = {}
    totals_under: dict[float, list] = {}

    for bm in item.get("bookmakers", []):
        for bet in bm.get("bets", []):
            bet_id = bet.get("id")
            if bet_id == AFL_BET_MATCH_WINNER:
                for v in bet.get("values", []):
                    try:
                        price = float(v.get("odd", 0))
                    except (ValueError, TypeError):
                        continue
                    if v.get("value") == "Home":
                        h2h_home_odds.append(price)
                    elif v.get("value") == "Away":
                        h2h_away_odds.append(price)
            elif bet_id == AFL_BET_GOALS_OU:
                for v in bet.get("values", []):
                    raw = v.get("value", "")
                    try:
                        price = float(v.get("odd", 0))
                    except (ValueError, TypeError):
                        continue
                    if raw.startswith("Over"):
                        try:
                            line = float(raw.split(" ")[1])
                        except (IndexError, ValueError):
                            continue
                        totals_over.setdefault(line, []).append(price)
                    elif raw.startswith("Under"):
                        try:
                            line = float(raw.split(" ")[1])
                        except (IndexError, ValueError):
                            continue
                        totals_under.setdefault(line, []).append(price)

    picks = []

    def make_pick(outcome_key, label, odds_list, market, line=None):
        if not odds_list:
            return
        avg_odds = sum(odds_list) / len(odds_list)
        n = len(odds_list)
        std = (sum((o - avg_odds) ** 2 for o in odds_list) / n) ** 0.5 if n > 1 else 0.0
        avg_odds = round(avg_odds, 3)
        if MIN_PICK_ODDS <= avg_odds <= MAX_PICK_ODDS:
            picks.append({
                "market": market,
                "outcome_key": outcome_key,
                "pick_label": label,
                "odds": avg_odds,
                "std": round(std, 4),
                "n_bm": n,
                "line": line,
            })

    make_pick("home", f"{home} győz", h2h_home_odds, "h2h")
    make_pick("away", f"{away} győz", h2h_away_odds, "h2h")
    for line, ol in totals_over.items():
        make_pick(f"over_{line}", f"Több mint {line} gól", ol, "totals", line)
    for line, ol in totals_under.items():
        make_pick(f"under_{line}", f"Kevesebb mint {line} gól", ol, "totals", line)

    return picks


def collect_api_football_picks(sent_ids: set) -> list:
    """
    API-Football alapú futball pick-gyűjtő.
    Multi-bookmaker odds → valódi std + n_bm → jobb pontszámítás.
    Napi 2 API hívás (ma + holnap).
    """
    from datetime import timedelta
    now = datetime.utcnow()
    dates = [now.strftime("%Y-%m-%d"),
             (now + timedelta(days=1)).strftime("%Y-%m-%d")]
    now_ts = now.timestamp()

    candidates = []
    for date_str in dates:
        items = fetch_api_football_odds(date_str)
        logger.info(f"API-Football odds [{date_str}]: {len(items)} mérkőzés")
        for item in items:
            fixture = item.get("fixture", {})
            event_id = f"afl_{fixture.get('id', '')}"
            if event_id in sent_ids:
                continue
            start_ts = fixture.get("timestamp", 0)
            if not (now_ts + 7200 <= start_ts <= now_ts + 48 * 3600):
                continue

            home = item.get("teams", {}).get("home", {}).get("name", "")
            away = item.get("teams", {}).get("away", {}).get("name", "")

            picks = parse_api_football_odds_item(item)
            if not picks:
                continue

            best_pick = None
            best_score = 0
            for mp in picks:
                sc = score_pick(mp["odds"], std=mp["std"], n_bm=mp["n_bm"])
                if sc <= 0:
                    continue
                if sc > best_score:
                    best_score = sc
                    best_pick = {
                        "event_id": event_id,
                        "sport_key": "football",
                        "sport": "football",
                        "home": home,
                        "away": away,
                        "market": mp["market"],
                        "outcome_key": mp["outcome_key"],
                        "pick_name": mp["pick_label"],
                        "line": mp.get("line"),
                        "odds": round(mp["odds"], 2),
                        "n_bookmakers": mp["n_bm"],
                        "start_timestamp": int(start_ts),
                        "score": round(best_score, 4),
                        "sofa_confirmed": False,
                        "result": None,
                    }

            if best_pick:
                candidates.append(best_pick)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"API-Football: {len(candidates)} jelölt tipp összesen")
    return candidates


# ── Pick scoring & coupon building ───────────────────────────────────────────

def score_pick(odds, std, n_bm):
    """Generic pick scorer — works for any market type."""
    if not (MIN_PICK_ODDS <= odds <= MAX_PICK_ODDS):
        return 0
    if std > MAX_ODDS_STD:
        return 0
    confidence = 1 / odds
    return confidence * (n_bm ** 0.5) * (1 - std * 3)


def _sync_collect_picks():
    from datetime import timedelta
    logger.info("Szelvény keresés indul (SofaScore)...")
    now = datetime.utcnow()
    dates = [now.strftime("%Y-%m-%d"),
             (now + timedelta(days=1)).strftime("%Y-%m-%d")]

    sent_ids = get_sent_event_ids()
    candidates = []
    total_checked = 0

    for sport in SOFA_SPORTS:
        if total_checked >= MAX_TOTAL_EVENTS:
            break
        sport_checked = 0
        for date_str in dates:
            if sport_checked >= MAX_EVENTS_PER_SPORT:
                break
            events = fetch_sofa_events(sport, date_str)
            for event in events:
                if sport_checked >= MAX_EVENTS_PER_SPORT or total_checked >= MAX_TOTAL_EVENTS:
                    break
                event_id = str(event.get("id", ""))
                if event_id in sent_ids:
                    continue

                home = event.get("homeTeam", {}).get("name", "")
                away = event.get("awayTeam", {}).get("name", "")
                home_id = event.get("homeTeam", {}).get("id")
                away_id = event.get("awayTeam", {}).get("id")
                start_ts = event.get("startTimestamp", 0)

                sport_checked += 1
                total_checked += 1

                # Forma szűrő: legalább egy csapatnak ≥MIN_PICKED_FORM kell
                if home_id and away_id:
                    home_form = get_recent_form(home_id)
                    away_form = get_recent_form(away_id)
                    hf = home_form if home_form is not None else 0.0
                    af = away_form if away_form is not None else 0.0
                    if max(hf, af) < MIN_PICKED_FORM:
                        logger.info(f"Forma kizárt: {home}={home_form} vs {away}={away_form}")
                        continue

                markets = fetch_sofa_odds_markets(int(event_id))
                if not markets:
                    continue

                market_picks = extract_market_picks_sofa(event, markets)
                if not market_picks:
                    continue

                # Score all outcomes, keep best per event (no correlated picks)
                best_pick = None
                best_score = 0
                for mp in market_picks:
                    sc = score_pick(mp["odds"], std=0.0, n_bm=4)
                    if sc <= 0:
                        continue

                    sofa_ok = None

                    # ── H2H szűrő (h2h piac) ─────────────────────────────────
                    if mp["market"] == "h2h" and mp["outcome_key"] in ("home", "away"):
                        sofa_ok = verify_with_sofascore(home, away, mp["outcome_key"])
                        if sofa_ok is not True:
                            logger.info(f"H2H kizárt (nincs megerősítés): {home} vs {away} [{mp['pick_label']}]")
                            continue

                    # ── Over/Under statisztikai szűrő (totals piac) ───────────
                    elif mp["market"] == "totals" and home_id and away_id:
                        line = mp.get("line")
                        direction = "over" if "over" in mp["outcome_key"] else "under"
                        if line is not None:
                            sofa_ok = verify_totals_pick(home_id, away_id, line, direction)
                            if sofa_ok is not True:
                                logger.info(f"Totals kizárt (over-ráta vagy nincs adat): {home} vs {away} [{mp['pick_label']}]")
                                continue

                    bonus = 1.20 if sofa_ok is True else 1.0
                    final_score = sc * bonus

                    if final_score > best_score:
                        best_score = final_score
                        best_pick = {
                            "event_id": event_id,
                            "sport_key": sport,
                            "sport": sport,
                            "home": home,
                            "away": away,
                            "market": mp["market"],
                            "outcome_key": mp["outcome_key"],
                            "pick_name": mp["pick_label"],
                            "line": mp.get("line"),
                            "odds": round(mp["odds"], 2),
                            "n_bookmakers": 1,
                            "start_timestamp": start_ts,
                            "score": round(final_score, 4),
                            "sofa_confirmed": sofa_ok is True,
                            "result": None,
                        }

                if best_pick:
                    candidates.append(best_pick)

    # ── Odds API: multi-bookmaker futball odds (napi cache + auto-fallback) ──
    odds_picks = collect_odds_api_picks(sent_ids)
    candidates.extend(odds_picks)

    # ── API-Football: fallback ha Odds API nem elérhető ───────────────────
    if not odds_picks:
        afl_picks = collect_api_football_picks(sent_ids)
        candidates.extend(afl_picks)
        src_label = "SofaScore + API-Football"
    else:
        src_label = "SofaScore + Odds API"

    candidates.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"{len(candidates)} jelölt tipp összesen ({src_label})")
    return candidates


def build_coupon(candidates):
    """
    Build a 2-3 pick coupon with combined odds closest to TARGET_COMBINED (~2.0).
    Diversify by sport (max 2 per sport), different events only.
    """
    seen_events = set()
    sport_count: dict = {}
    pool = []

    for p in candidates:
        if p["event_id"] in seen_events:
            continue
        sport = p["sport"]
        if sport_count.get(sport, 0) >= 3:
            continue
        pool.append(p)
        seen_events.add(p["event_id"])
        sport_count[sport] = sport_count.get(sport, 0) + 1
        if len(pool) >= 12:
            break

    logger.info(f"Pool mérete: {len(pool)} pick | sportok: { {p['sport'] for p in pool} }")
    for p in pool:
        logger.info(f"  Pool pick: {p['home']} vs {p['away']} [{p['sport']}] @{p['odds']}")

    best = None
    best_diff = 999

    for n in [2, 3]:
        for combo in combinations(pool[:12], n):
            combined = 1.0
            for p in combo:
                combined *= p["odds"]
            combined = round(combined, 2)
            if not (MIN_COMBINED <= combined <= MAX_COMBINED):
                continue
            diff = abs(combined - TARGET_COMBINED)
            # Vegyes piac preferencia: h2h + totals kombó előnyt kap
            markets = {p.get("market", "h2h") for p in combo}
            if len(markets) > 1:
                diff -= 0.08
            if diff < best_diff:
                best_diff = diff
                best = (list(combo), combined)

    if best is None:
        # Legjobb közelítés logolása
        best_approx = None
        best_approx_diff = 999
        for n in [2, 3]:
            for combo in combinations(pool[:12], n):
                combined = 1.0
                for p in combo:
                    combined *= p["odds"]
                combined = round(combined, 2)
                diff = abs(combined - TARGET_COMBINED)
                if diff < best_approx_diff:
                    best_approx_diff = diff
                    best_approx = (combined, [p['odds'] for p in combo])
        if best_approx:
            logger.info(f"Legjobb közelítés: {best_approx[0]}x (odds: {best_approx[1]}) — kívül van {MIN_COMBINED}-{MAX_COMBINED} tartományon")

    return best


# ── Telegram ──────────────────────────────────────────────────────────────────

def esc(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r'\_*[]()~`>#+-=|{}.!'
    return "".join(f"\\{c}" if c in special else c for c in str(text))


MARKET_EMOJI = {
    "h2h": "",
    "totals": "📊",
    "double_chance": "🔀",
    "spreads": "↕️",
}


def format_coupon(picks, combined_odds, number):
    lines = [f"🎯 *SZELVÉNY \\#{number:03d}*\n"]
    for p in picks:
        sport_emoji = SPORT_EMOJI.get(p["sport"], "🏅")
        market_emoji = MARKET_EMOJI.get(p.get("market", "h2h"), "")
        dt = datetime.fromtimestamp(p["start_timestamp"], tz=HU_TZ)
        time_str = esc(dt.strftime("%m.%d %H:%M"))
        confirmed = " ✔️" if p.get("sofa_confirmed") else ""
        pick_name = esc(p["pick_name"])
        matchup = esc(f"{p['home']} vs {p['away']}")
        odds_str = esc(f"{p['odds']:.2f}")
        prefix = f"{sport_emoji}{market_emoji}".strip()
        lines.append(
            f"{prefix} *{pick_name}*{confirmed}\n"
            f"   _{matchup}_\n"
            f"   🕐 {time_str}  💰 @{odds_str}  \\({p['n_bookmakers']} iroda\\)"
        )
    combined_str = esc(f"{combined_odds:.2f}")
    lines.append(f"\n📊 *Összesített szorzó: {combined_str}x*")
    lines.append(f"🎲 {len(picks)} mérkőzés")
    return "\n".join(lines)


# ── Scheduler jobs ────────────────────────────────────────────────────────────

async def scan_and_send(context=None, force=False):
    now_hu = datetime.now(HU_TZ)
    if not force and not (8 <= now_hu.hour < 20):
        logger.info(f"Időn kívül ({now_hu.hour}:{now_hu.minute:02d}), kihagyva")
        return

    if not COUPON_CHAT_ID:
        logger.warning("COUPON_CHAT_ID nincs beállítva! Küldj /start üzenetet a botnak.")
        return

    candidates = await asyncio.to_thread(_sync_collect_picks)
    if not candidates:
        logger.info("Nincs megfelelő tipp")
        return

    result = build_coupon(candidates)
    if not result:
        logger.info("Nem sikerült ~2.0x szelvényt összerakni")
        return

    picks, combined_odds = result
    number = save_coupon(picks, combined_odds)

    bot = context.bot if context else application.bot
    msg = format_coupon(picks, combined_odds, number)
    await bot.send_message(
        chat_id=COUPON_CHAT_ID,
        text=msg,
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    logger.info(f"Szelvény #{number:03d} elküldve ({combined_odds:.2f}x, {len(picks)} meccs)")


def _resolve_pick_score(pick, sports_cache):
    """
    Returns (home_score, away_score) for a finished pick, or None if not yet settled.
    Uses SofaScore scheduled-events (free, no quota, no API key needed).
    """
    start_ts = pick.get("start_timestamp", 0)
    now_ts = int(datetime.utcnow().timestamp())
    if now_ts - start_ts < 5400:
        return None
    result = get_sofa_result(
        pick.get("home", ""), pick.get("away", ""),
        start_ts, pick.get("sport_key", "football")
    )
    if result:
        logger.info(f"SofaScore eredmény: {pick['home']} {result[0]}-{result[1]} {pick['away']}")
        return result

    return None


def _sync_check_results():
    pending = get_pending_coupons()
    if not pending:
        return

    sports_cache = {}
    for coupon in pending:
        picks = coupon["picks"]
        all_settled = True
        coupon_won = True

        for pick in picks:
            if pick.get("result") is not None:
                if pick["result"] == "loss":
                    coupon_won = False
                continue

            score = _resolve_pick_score(pick, sports_cache)
            if score is None:
                all_settled = False
                continue

            home_score, away_score = score
            market = pick.get("market", "h2h")
            okey = pick.get("outcome_key", pick.get("pick", "home"))

            if market == "h2h":
                if okey == "home":
                    pick_result = "win" if home_score > away_score else "loss"
                elif okey == "away":
                    pick_result = "win" if away_score > home_score else "loss"
                else:  # draw
                    pick_result = "win" if home_score == away_score else "loss"

            elif market == "totals":
                line = float(pick.get("line") or 2.5)
                total = home_score + away_score
                if okey.startswith("over"):
                    pick_result = "win" if total > line else "loss"
                else:
                    pick_result = "win" if total < line else "loss"

            elif market == "double_chance":
                if okey == "1X":
                    pick_result = "win" if home_score >= away_score else "loss"
                elif okey == "X2":
                    pick_result = "win" if away_score >= home_score else "loss"
                else:  # 12
                    pick_result = "win" if home_score != away_score else "loss"

            elif market == "spreads":
                line = float(pick.get("line") or 0)
                if okey.startswith("home"):
                    pick_result = "win" if (home_score + line) > away_score else "loss"
                else:
                    pick_result = "win" if (away_score + line) > home_score else "loss"

            else:
                pick_result = "win" if home_score > away_score else "loss"

            update_pick_result(coupon["id"], pick["event_id"], pick_result)
            if pick_result == "loss":
                coupon_won = False

        if all_settled:
            final = "win" if coupon_won else "loss"
            update_coupon_result(coupon["id"], final)
            logger.info(f"Szelvény #{coupon['coupon_number']:03d} lezárva: {final}")


async def check_results(context=None):
    await asyncio.to_thread(_sync_check_results)


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🎯 Szelvény Bot aktív!\n\n"
        f"Chat ID: `{chat_id}`\n\n"
        f"Állítsd be a `COUPON_CHAT_ID` secret értékét erre: `{chat_id}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_szelveny(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Szelvény keresése folyamatban...")
    await scan_and_send(context, force=True)


async def cmd_folyamatban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = get_pending_coupons()
    if not pending:
        await update.message.reply_text("Nincs folyamatban lévő szelvény.")
        return
    lines = []
    for c in pending:
        picks = c["picks"]
        pick_str = ", ".join(p["pick_name"] for p in picks)
        lines.append(f"#{c['coupon_number']:03d} — {pick_str} ({c['combined_odds']:.2f}x)")
    await update.message.reply_text("📋 Folyamatban:\n" + "\n".join(lines))


async def cmd_lezar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manuális lezárás: /lezar 3 win  vagy  /lezar 3 loss"""
    args = context.args
    if len(args) < 2 or args[1] not in ("win", "loss", "nyert", "vesztett"):
        await update.message.reply_text(
            "Használat: /lezar <szelvényszám> <win|loss>\nPl: /lezar 3 win"
        )
        return
    try:
        number = int(args[0])
    except ValueError:
        await update.message.reply_text("A szelvényszámnak számnak kell lennie.")
        return

    result_val = "win" if args[1] in ("win", "nyert") else "loss"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM coupons WHERE coupon_number=%s AND result IS NULL", (number,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text(f"#{number:03d} nem található vagy már le van zárva.")
                return
            coupon_id = row[0]
        conn.commit()

    update_coupon_result(coupon_id, result_val)
    emoji = "✅ NYERT" if result_val == "win" else "❌ VESZTETT"
    await update.message.reply_text(f"#{number:03d} manuálisan lezárva: {emoji}")
    logger.info(f"Szelvény #{number:03d} manuálisan lezárva: {result_val}")


async def post_init(app):
    init_db()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(scan_and_send, "interval", minutes=30, id="scan",
                      next_run_time=datetime.now(timezone.utc))
    scheduler.add_job(check_results, "interval", minutes=20, id="results",
                      next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    logger.info("🎯 Szelvény Bot indul...")


def main():
    global application
    application = (
        Application.builder()
        .token(COUPON_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("szelveny", cmd_szelveny))
    application.add_handler(CommandHandler("folyamatban", cmd_folyamatban))
    application.add_handler(CommandHandler("lezar", cmd_lezar))
    application.run_polling()


if __name__ == "__main__":
    main()
