"""
Coupon Bot
- The Odds API: szorzók és meccsek
- SofaScore: H2H + forma megerősítés
- Cél: ~2.0 összesített szorzó, 2-3 meccs/szelvény
- Küldési ablak: 08:00-20:00 (Budapest)
"""

import os
import json
import logging
import requests
import psycopg2
import psycopg2.extras
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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
ODDS_API_KEY = os.environ["ODDS_API_KEY"]
SUPABASE_DB_URL = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL", "")

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SOFASCORE_BASE = "https://api.sofascore.com/api/v1"

SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8",
}

MIN_PICK_ODDS = 1.28
MAX_PICK_ODDS = 1.85
TARGET_COMBINED = 2.00
MIN_COMBINED = 1.75
MAX_COMBINED = 2.60
MIN_PICKS = 2
MAX_PICKS = 3
MIN_BOOKMAKERS = 4
MAX_ODDS_STD = 0.12

TRUSTED_SPORTS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
    "basketball_nba",
    "basketball_euroleague",
    "americanfootball_nfl",
    "icehockey_nhl",
    "tennis_atp_french_open",
    "tennis_atp_us_open",
    "tennis_atp_wimbledon",
    "tennis_wta_us_open",
]

SPORT_EMOJI = {
    "soccer": "⚽",
    "basketball": "🏀",
    "americanfootball": "🏈",
    "tennis": "🎾",
    "icehockey": "🏒",
    "baseball": "⚾",
    "rugby": "🏉",
    "mma": "🥊",
}

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


# ── Odds API ──────────────────────────────────────────────────────────────────

def odds_get(path, params=None):
    p = {"apiKey": ODDS_API_KEY}
    if params:
        p.update(params)
    try:
        r = requests.get(f"{ODDS_API_BASE}{path}", params=p, timeout=12)
        if r.ok:
            return r.json()
        try:
            err = r.json()
            if err.get("error_code") == "OUT_OF_USAGE_CREDITS":
                logger.warning("⚠️ Odds API kvóta kimerült! Eredmények nem frissíthetők automatikusan.")
                return None
        except Exception:
            pass
        logger.error(f"Odds API HTTP {r.status_code} {path}")
    except Exception as e:
        logger.error(f"Odds API error {path}: {e}")
    return None


def fetch_active_sports():
    data = odds_get("/sports/")
    if not data:
        return []
    return [s["key"] for s in data if s.get("active") and not s.get("has_outrights")]


def fetch_odds_for_sport(sport_key):
    now_ts = datetime.utcnow().timestamp()
    data = odds_get(f"/sports/{sport_key}/odds/", {
        "regions": "eu",
        "markets": "h2h,totals,double_chance,spreads",
        "oddsFormat": "decimal",
        "dateFormat": "unix",
    })
    if not data:
        return []
    return [e for e in data
            if now_ts + 2 * 3600 <= e.get("commence_time", 0) <= now_ts + 48 * 3600]


def fetch_scores_for_sport(sport_key):
    data = odds_get(f"/sports/{sport_key}/scores/", {"daysFrom": 2, "dateFormat": "unix"})
    return data or []


def extract_market_picks(event):
    """
    Returns a list of candidate picks from ALL markets (h2h, totals, double_chance, spreads).
    Each entry: {outcome_key, pick_label, odds, std, n_bookmakers, line (optional)}
    Only includes outcomes within MIN_PICK_ODDS..MAX_PICK_ODDS range.
    """
    bookmakers = event.get("bookmakers", [])
    n_bm = len(bookmakers)
    if n_bm < MIN_BOOKMAKERS:
        return []

    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")

    # Collect odds per market key → outcome_key → [prices]
    market_data = {}  # market_key → {outcome_key: {"prices": [], "line": float|None, "label": str}}

    for bm in bookmakers:
        for mkt in bm.get("markets", []):
            mkey = mkt["key"]
            if mkey not in market_data:
                market_data[mkey] = {}
            for oc in mkt.get("outcomes", []):
                name = oc.get("name", "")
                price = oc.get("price")
                point = oc.get("point")  # for totals & spreads

                # Build a stable outcome key
                if mkey == "h2h":
                    if name == home_team:
                        okey = "home"
                        label = f"{home_team} győz"
                    elif name == away_team:
                        okey = "away"
                        label = f"{away_team} győz"
                    else:
                        okey = "draw"
                        label = "Döntetlen"
                elif mkey == "totals":
                    if point is None:
                        continue
                    direction = "over" if name.lower().startswith("over") else "under"
                    okey = f"{direction}_{point}"
                    hu = "Több mint" if direction == "over" else "Kevesebb mint"
                    label = f"{hu} {point} gól"
                elif mkey == "double_chance":
                    # The Odds API uses: "HomeTeam/Draw", "Draw/AwayTeam", "HomeTeam/AwayTeam"
                    if name in (home_team, f"{home_team}/Draw", f"Draw/{home_team}"):
                        okey = "1X"
                        label = f"{home_team} vagy döntetlen (1X)"
                    elif name in (away_team, f"{away_team}/Draw", f"Draw/{away_team}"):
                        okey = "X2"
                        label = f"Döntetlen vagy {away_team} (X2)"
                    elif "Draw" not in name:
                        okey = "12"
                        label = f"{home_team} vagy {away_team} (12)"
                    else:
                        continue
                elif mkey == "spreads":
                    if point is None:
                        continue
                    if name == home_team:
                        okey = f"home_spread_{point}"
                        sign = f"+{point}" if point > 0 else str(point)
                        label = f"{home_team} ({sign}) hendikep"
                    elif name == away_team:
                        okey = f"away_spread_{point}"
                        sign = f"+{point}" if point > 0 else str(point)
                        label = f"{away_team} ({sign}) hendikep"
                    else:
                        continue
                else:
                    continue

                if price is None:
                    continue

                if okey not in market_data[mkey]:
                    market_data[mkey][okey] = {"prices": [], "line": point, "label": label}
                market_data[mkey][okey]["prices"].append(price)

    # Build picks from aggregated data
    picks = []
    for mkey, outcomes in market_data.items():
        for okey, info in outcomes.items():
            prices = info["prices"]
            if len(prices) < MIN_BOOKMAKERS:
                continue
            avg = sum(prices) / len(prices)
            std = max(prices) - min(prices)
            if not (MIN_PICK_ODDS <= avg <= MAX_PICK_ODDS):
                continue
            if std > MAX_ODDS_STD:
                continue
            picks.append({
                "market": mkey,
                "outcome_key": okey,
                "pick_label": info["label"],
                "odds": round(avg, 3),
                "std": round(std, 4),
                "line": info["line"],
                "n_bookmakers": n_bm,
            })
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


_SPORT_KEY_TO_SOFA = {
    "soccer": "football",
    "basketball": "basketball",
    "americanfootball": "american-football",
    "baseball": "baseball",
    "hockey": "ice-hockey",
    "tennis": "tennis",
    "mma": "mma",
}


def _sport_key_to_sofa_sport(sport_key):
    prefix = sport_key.split("_")[0].lower()
    return _SPORT_KEY_TO_SOFA.get(prefix, "football")


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
    Returns the H2H win rate of the picked team.
    pick_side: 'home' or 'away'
    Returns float 0-1 or None if not enough data.
    """
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/h2h")
    if not data:
        return None

    all_events = data.get("events", [])
    if len(all_events) < 3:
        return None

    # Count how many times the home/away team of the current match won
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

    if total < 3:
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


def verify_with_sofascore(home_team, away_team, pick_side):
    """
    Returns:
      True  - SofaScore confirms the pick
      False - SofaScore contradicts the pick → skip
      None  - no SofaScore data, neutral
    """
    sofa = search_sofa_event(home_team, away_team)
    if not sofa:
        return None

    event_id, home_id, away_id = sofa

    h2h_rate = get_h2h_win_rate(event_id, pick_side)
    picked_team_id = home_id if pick_side == "home" else away_id
    form_rate = get_recent_form(picked_team_id)

    signals = []
    if h2h_rate is not None:
        signals.append(h2h_rate)
    if form_rate is not None:
        signals.append(form_rate)

    if not signals:
        return None

    avg = sum(signals) / len(signals)

    if avg >= 0.50:
        return True
    elif avg < 0.35:
        return False
    return None


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
    logger.info("Szelvény keresés indul...")
    active_sports = fetch_active_sports()
    sports_to_scan = [s for s in TRUSTED_SPORTS if s in active_sports]
    if not sports_to_scan:
        sports_to_scan = active_sports[:8]

    logger.info(f"Sportágak: {sports_to_scan}")
    sent_ids = get_sent_event_ids()
    candidates = []

    for sport_key in sports_to_scan:
        events = fetch_odds_for_sport(sport_key)
        sport_prefix = sport_key.split("_")[0]

        for event in events:
            event_id = event.get("id", "")
            if event_id in sent_ids:
                continue

            home = event.get("home_team", "")
            away = event.get("away_team", "")
            start_ts = event.get("commence_time", 0)

            market_picks = extract_market_picks(event)
            if not market_picks:
                continue

            # Score all outcomes, keep best one per event (avoid correlated picks)
            best_pick = None
            best_score = 0
            for mp in market_picks:
                sc = score_pick(mp["odds"], mp["std"], mp["n_bookmakers"])
                if sc <= 0:
                    continue

                # SofaScore confirmation only for h2h home/away picks
                sofa_ok = None
                if mp["market"] == "h2h" and mp["outcome_key"] in ("home", "away"):
                    side = mp["outcome_key"]
                    sofa_ok = verify_with_sofascore(home, away, side)
                    if sofa_ok is False:
                        logger.info(f"SofaScore kizárt: {home} vs {away} [{mp['pick_label']}]")
                        continue

                bonus = 1.15 if sofa_ok is True else 1.0
                final_score = sc * bonus

                if final_score > best_score:
                    best_score = final_score
                    best_pick = {
                        "event_id": event_id,
                        "sport_key": sport_key,
                        "sport": sport_prefix,
                        "home": home,
                        "away": away,
                        "market": mp["market"],
                        "outcome_key": mp["outcome_key"],
                        "pick_name": mp["pick_label"],
                        "line": mp.get("line"),
                        "odds": round(mp["odds"], 2),
                        "n_bookmakers": mp["n_bookmakers"],
                        "start_timestamp": start_ts,
                        "score": round(final_score, 4),
                        "sofa_confirmed": sofa_ok is True,
                        "result": None,
                    }

            if best_pick:
                candidates.append(best_pick)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"{len(candidates)} jelölt tipp összesen")
    return candidates


def build_coupon(candidates):
    """
    Build a 2-3 pick coupon with combined odds closest to TARGET_COMBINED (~2.0).
    Diversify by sport.
    """
    seen_events = set()
    seen_sports = set()
    pool = []

    for p in candidates:
        if p["event_id"] in seen_events:
            continue
        if p["sport"] in seen_sports:
            continue
        pool.append(p)
        seen_events.add(p["event_id"])
        seen_sports.add(p["sport"])
        if len(pool) >= 6:
            break

    best = None
    best_diff = 999

    for n in [2, 3]:
        for combo in combinations(pool[:6], n):
            combined = 1.0
            for p in combo:
                combined *= p["odds"]
            combined = round(combined, 2)
            if not (MIN_COMBINED <= combined <= MAX_COMBINED):
                continue
            diff = abs(combined - TARGET_COMBINED)
            if diff < best_diff:
                best_diff = diff
                best = (list(combo), combined)

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
    Tries Odds API first, then falls back to SofaScore (free, no quota).
    """
    # ── 1. Odds API (primary) ──────────────────────────────────────────────────
    sport_key = pick.get("sport_key", "")
    if sport_key not in sports_cache:
        sports_cache[sport_key] = fetch_scores_for_sport(sport_key)
    scores = sports_cache[sport_key]
    if scores:
        matched = next((s for s in scores if s.get("id") == pick["event_id"]), None)
        if matched and matched.get("completed"):
            scores_list = matched.get("scores") or []
            home_team = matched.get("home_team", "")
            away_team = matched.get("away_team", "")
            home_obj = next((s for s in scores_list if s.get("name") == home_team), None)
            away_obj = next((s for s in scores_list if s.get("name") == away_team), None)
            if home_obj and away_obj:
                return int(home_obj["score"]), int(away_obj["score"])

    # ── 2. SofaScore fallback (free, no quota) ────────────────────────────────
    start_ts = pick.get("start_timestamp", 0)
    now_ts = int(datetime.utcnow().timestamp())
    if now_ts - start_ts < 5400:
        return None
    result = get_sofa_result(pick.get("home", ""), pick.get("away", ""), start_ts, pick.get("sport_key", "soccer"))
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
