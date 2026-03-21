import os
import asyncio
import time
import math
import logging
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, date, timedelta
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
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

BASKETBALL_BOT_TOKEN = os.environ["BASKETBALL_BOT_TOKEN"]
BASKETBALL_CHAT_ID   = os.environ["BASKETBALL_CHAT_ID"]
DATABASE_URL         = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
ODDS_API_KEY         = os.environ.get("ODDS_API_KEY", "")

SOFASCORE_BASE = "https://www.sofascore.com/api/v1"
SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# ── Beállítások ────────────────────────────────────────────────────────────────
MIN_CONFIDENCE      = 72      # minimum megbízhatósági pont (0-100)
RESULT_DELAY_MIN    = 130     # eredmény lekérdezés késleltetése percekben
API_DELAY_SEC       = 0.5
MIN_ODDS            = 1.75    # minimum fogadási szorzó
MAX_ODDS            = 2.20
MIN_LAST_MATCHES    = 5       # minimum ennyi meccs kell a stathoz
SCAN_HOURS_AHEAD    = 24      # ennyi órán belüli meccseket nézzük

# Ligák (SofaScore sport slug + tournament ID-k)
BASKETBALL_LEAGUES = [
    {"sport": "basketball", "name": "NBA",          "tournament_id": 132},
    {"sport": "basketball", "name": "Euroleague",   "tournament_id": 3},
    {"sport": "basketball", "name": "EuroCup",      "tournament_id": 8},
    {"sport": "basketball", "name": "Spain ACB",    "tournament_id": 38},
    {"sport": "basketball", "name": "Germany BBL",  "tournament_id": 35},
    {"sport": "basketball", "name": "France Pro A", "tournament_id": 40},
    {"sport": "basketball", "name": "Italy Lega",   "tournament_id": 39},
    {"sport": "basketball", "name": "Turkey BSL",   "tournament_id": 37},
]

# Odds API kosárlabda sport kulcsok (line-shopping)
ODDS_SPORTS = [
    "basketball_nba",
    "basketball_euroleague",
]

_cache: dict = {}

# ── Adatbázis ──────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    sql = """
    CREATE TABLE IF NOT EXISTS basketball_tips (
        event_id        BIGINT PRIMARY KEY,
        home            TEXT NOT NULL,
        away            TEXT NOT NULL,
        league          TEXT NOT NULL,
        league_id       INTEGER,
        start_timestamp BIGINT NOT NULL,
        tip             TEXT NOT NULL,
        line            REAL NOT NULL,
        expected_total  REAL NOT NULL,
        home_off_rating REAL,
        away_off_rating REAL,
        home_def_rating REAL,
        away_def_rating REAL,
        home_pace       REAL,
        away_pace       REAL,
        odds            REAL,
        sent_at         BIGINT NOT NULL,
        result          TEXT DEFAULT NULL,
        actual_total    INTEGER DEFAULT NULL,
        confidence_score INTEGER DEFAULT NULL,
        injury_impact   REAL DEFAULT NULL
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    logger.info("basketball_tips tábla inicializálva")


def get_sent_event_ids() -> set:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT event_id FROM basketball_tips")
                return {str(r[0]) for r in cur.fetchall()}
    except Exception as e:
        logger.error(f"DB hiba (sent ids): {e}")
        return set()


def save_tip(tip: dict):
    sql = """
    INSERT INTO basketball_tips
        (event_id, home, away, league, league_id, start_timestamp, tip, line,
         expected_total, home_off_rating, away_off_rating, home_def_rating,
         away_def_rating, home_pace, away_pace, odds, sent_at, confidence_score,
         injury_impact)
    VALUES (%(event_id)s, %(home)s, %(away)s, %(league)s, %(league_id)s,
            %(start_timestamp)s, %(tip)s, %(line)s, %(expected_total)s,
            %(home_off_rating)s, %(away_off_rating)s, %(home_def_rating)s,
            %(away_def_rating)s, %(home_pace)s, %(away_pace)s, %(odds)s,
            %(sent_at)s, %(confidence_score)s, %(injury_impact)s)
    ON CONFLICT (event_id) DO NOTHING;
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tip)
            conn.commit()
    except Exception as e:
        logger.error(f"Tipp mentési hiba: {e}")


def update_result(event_id: int, result: str, actual_total: int):
    sql = "UPDATE basketball_tips SET result=%s, actual_total=%s WHERE event_id=%s"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (result, actual_total, event_id))
            conn.commit()
        logger.info(f"Eredmény frissítve: {event_id} → {result} ({actual_total} pont)")
    except Exception as e:
        logger.error(f"Eredmény frissítési hiba: {e}")


def load_pending_tips() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM basketball_tips WHERE result IS NULL ORDER BY start_timestamp"
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


def fetch_sofa_events(date_str: str) -> list:
    """Összes kosárlabda mérkőzés egy adott napra."""
    data = sofa_get(f"{SOFASCORE_BASE}/sport/basketball/scheduled-events/{date_str}")
    return data.get("events", [])


def fetch_team_last_matches(team_id: int, last_n: int = 10) -> list:
    cache_key = f"last_{team_id}"
    if cache_key in _cache:
        return _cache[cache_key]
    data = sofa_get(f"{SOFASCORE_BASE}/team/{team_id}/events/last/0")
    events = [e for e in data.get("events", [])
              if e.get("status", {}).get("type") == "finished"][:last_n]
    _cache[cache_key] = events
    return events


def fetch_event_score(event_id: int) -> tuple[int | None, int | None]:
    """Visszaadja a végeredményt (home_score, away_score)."""
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}")
    event = data.get("event", {})
    status = event.get("status", {}).get("type", "")
    if status != "finished":
        return None, None
    hs = event.get("homeScore", {}).get("current")
    as_ = event.get("awayScore", {}).get("current")
    return hs, as_


def fetch_h2h(event_id: int) -> list:
    cache_key = f"h2h_{event_id}"
    if cache_key in _cache:
        return _cache[cache_key]
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/h2h/events")
    events = [e for e in data.get("events", [])
              if e.get("status", {}).get("type") == "finished"]
    _cache[cache_key] = events
    return events


def fetch_sofa_odds(event_id: int) -> float | None:
    """SofaScore odds lekérdezés total over/under vonalhoz."""
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/odds/1/all")
    markets = data.get("markets", [])
    for m in markets:
        if "total" in m.get("marketName", "").lower():
            for choice in m.get("choices", []):
                if choice.get("name", "").lower() in ("over", "under"):
                    val = choice.get("fractionalValue") or choice.get("decimalValue")
                    if val:
                        try:
                            return float(val)
                        except Exception:
                            pass
    return None


# ── Odds API (line-shopping) ───────────────────────────────────────────────────

def fetch_odds_api_lines(sport: str, home: str, away: str) -> dict | None:
    """
    Visszaad egy dict-et: {'line': float, 'best_over': float, 'best_under': float, 'bookmakers': list}
    Több iroda összehasonlítása → legjobb over/under vonal és szorzó.
    """
    if not ODDS_API_KEY:
        return None
    try:
        r = requests.get(
            f"{ODDS_API_BASE}/sports/{sport}/odds/",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "eu,uk",
                "markets": "totals",
                "oddsFormat": "decimal",
            },
            timeout=15,
        )
        if r.status_code != 200:
            return None
        events = r.json()
        for ev in events:
            h = ev.get("home_team", "").lower()
            a = ev.get("away_team", "").lower()
            if home.lower()[:6] not in h and away.lower()[:6] not in a:
                continue
            # Line-shopping: collect all bookmaker totals lines
            lines_data: dict[float, dict] = {}  # line → {over_odds:[], under_odds:[]}
            for bm in ev.get("bookmakers", []):
                for market in bm.get("markets", []):
                    if market.get("key") != "totals":
                        continue
                    for outcome in market.get("outcomes", []):
                        line = float(outcome.get("point", 0))
                        side = outcome.get("name", "").lower()
                        odds = float(outcome.get("price", 0))
                        if line not in lines_data:
                            lines_data[line] = {"over": [], "under": [], "bms": []}
                        lines_data[line][side].append(odds)
                        lines_data[line]["bms"].append(bm.get("title", ""))
            if not lines_data:
                return None
            # Legtöbb irodától ajánlott vonal
            best_line = max(lines_data, key=lambda l: len(lines_data[l]["over"]))
            ld = lines_data[best_line]
            return {
                "line":       best_line,
                "best_over":  max(ld["over"])  if ld["over"]  else None,
                "best_under": max(ld["under"]) if ld["under"] else None,
                "n_bookmakers": len(set(ld["bms"])),
                "all_lines":  sorted(lines_data.keys()),
            }
    except Exception as e:
        logger.debug(f"Odds API hiba ({sport}): {e}")
    return None


# ── Statisztikák és Poisson ────────────────────────────────────────────────────

def calc_team_stats(team_id: int, is_home: bool) -> dict | None:
    """
    Kiszámolja a csapat off/def ratingét és pace-t az utolsó meccsek alapján.
    Pace = átlagos pontszám (saját + kapott) / 2 — közelítés SofaScore adatokból.
    """
    matches = fetch_team_last_matches(team_id, last_n=10)
    if len(matches) < MIN_LAST_MATCHES:
        return None

    scored_list, conceded_list, total_list = [], [], []
    for m in matches:
        hs = m.get("homeScore", {}).get("current")
        as_ = m.get("awayScore", {}).get("current")
        if hs is None or as_ is None:
            continue
        my_team_id = m.get("homeTeam", {}).get("id")
        if my_team_id == team_id:
            scored_list.append(hs)
            conceded_list.append(as_)
        else:
            scored_list.append(as_)
            conceded_list.append(hs)
        total_list.append(hs + as_)

    if len(scored_list) < MIN_LAST_MATCHES:
        return None

    avg_scored   = sum(scored_list)   / len(scored_list)
    avg_conceded = sum(conceded_list) / len(conceded_list)
    avg_total    = sum(total_list)    / len(total_list)

    # Trend: utolsó 5 vs összes
    last5_total = sum(total_list[-5:]) / 5 if len(total_list) >= 5 else avg_total

    return {
        "off_rating":   round(avg_scored,   1),
        "def_rating":   round(avg_conceded, 1),
        "pace":         round(avg_total,    1),
        "last5_pace":   round(last5_total,  1),
        "n":            len(scored_list),
    }


def poisson_prob(lam: float, k: int) -> float:
    """P(X = k) Poisson-eloszlással."""
    try:
        return (math.exp(-lam) * (lam ** k)) / math.factorial(k)
    except Exception:
        return 0.0


def poisson_over_prob(expected: float, line: float) -> float:
    """P(total > line) — Poisson közelítés."""
    threshold = int(math.floor(line))
    prob_under_eq = sum(poisson_prob(expected, k) for k in range(threshold + 1))
    return round(1.0 - prob_under_eq, 4)


def calc_expected_total(home_stats: dict, away_stats: dict) -> float:
    """
    Várható összpontszám:
    - home támadás vs away védekezés
    - away támadás vs home védekezés
    - súlyozás: forma (last5) 40%, szezon 60%
    """
    league_avg = (home_stats["pace"] + away_stats["pace"]) / 2

    home_att = (home_stats["off_rating"] / league_avg) if league_avg > 0 else 1.0
    home_def = (home_stats["def_rating"] / league_avg) if league_avg > 0 else 1.0
    away_att = (away_stats["off_rating"] / league_avg) if league_avg > 0 else 1.0
    away_def = (away_stats["def_rating"] / league_avg) if league_avg > 0 else 1.0

    home_expected = league_avg * home_att * away_def
    away_expected = league_avg * away_att * home_def
    season_total  = home_expected + away_expected

    # Forma korrekció (last5 átlag súlyozva)
    form_total = home_stats["last5_pace"] * 0.5 + away_stats["last5_pace"] * 0.5
    blended    = season_total * 0.60 + form_total * 0.40

    return round(blended, 1)


def calc_h2h_avg(event_id: int) -> float | None:
    """Egymás elleni meccsek átlagos összpontszáma."""
    h2h = fetch_h2h(event_id)
    if len(h2h) < 3:
        return None
    totals = []
    for m in h2h[:8]:
        hs = m.get("homeScore", {}).get("current")
        as_ = m.get("awayScore", {}).get("current")
        if hs is not None and as_ is not None:
            totals.append(hs + as_)
    return round(sum(totals) / len(totals), 1) if len(totals) >= 3 else None


def calc_confidence(
    expected: float,
    line: float,
    direction: str,
    prob: float,
    odds: float | None,
    h2h_avg: float | None,
    n_bookmakers: int,
) -> int:
    """
    Megbízhatósági pontszám (0–100):
    - Poisson valószínűség (40 pont max)
    - Edge a vonaltól való távolság (20 pont max)
    - H2H egyezés (15 pont max)
    - Szorzó minősége (15 pont max)
    - Irodák száma (10 pont max)
    """
    score = 0

    # 1. Poisson valószínűség
    score += min(40, int(prob * 50))

    # 2. Edge: mennyivel tér el a várható a vonaltól
    edge = abs(expected - line)
    score += min(20, int(edge * 2))

    # 3. H2H egyezés
    if h2h_avg is not None:
        h2h_over = h2h_avg > line
        direction_over = direction == "over"
        if h2h_over == direction_over:
            score += 15
        else:
            score -= 5

    # 4. Szorzó minősége
    if odds:
        if MIN_ODDS <= odds <= MAX_ODDS:
            score += 15
        elif odds > MAX_ODDS:
            score += 5

    # 5. Irodák száma
    score += min(10, n_bookmakers * 2)

    return max(0, min(100, score))


# ── Fő elemzés ─────────────────────────────────────────────────────────────────

def analyze_event(event: dict, sent_ids: set) -> dict | None:
    event_id  = str(event.get("id", ""))
    if event_id in sent_ids:
        return None

    status = event.get("status", {}).get("type", "")
    if status not in ("notstarted",):
        return None

    start_ts = event.get("startTimestamp", 0)
    now_ts   = int(time.time())
    hours_to_start = (start_ts - now_ts) / 3600
    if hours_to_start < 0.5 or hours_to_start > SCAN_HOURS_AHEAD:
        return None

    home      = event.get("homeTeam", {}).get("name", "")
    away      = event.get("awayTeam", {}).get("name", "")
    home_id   = event.get("homeTeam", {}).get("id")
    away_id   = event.get("awayTeam", {}).get("id")
    league    = event.get("tournament", {}).get("name", "Ismeretlen")
    league_id = event.get("tournament", {}).get("id")

    if not home_id or not away_id:
        return None

    home_stats = calc_team_stats(home_id, is_home=True)
    away_stats = calc_team_stats(away_id, is_home=False)

    if not home_stats or not away_stats:
        logger.info(f"Nincs elég stat: {home} vs {away}")
        return None

    expected = calc_expected_total(home_stats, away_stats)

    # Odds API line-shopping
    odds_data = None
    for sport_key in ODDS_SPORTS:
        odds_data = fetch_odds_api_lines(sport_key, home, away)
        if odds_data:
            break

    line         = odds_data["line"]       if odds_data else round(expected - 0.5)
    best_over    = odds_data["best_over"]  if odds_data else None
    best_under   = odds_data["best_under"] if odds_data else None
    n_bookmakers = odds_data["n_bookmakers"] if odds_data else 1

    # H2H
    h2h_avg = calc_h2h_avg(int(event_id))

    # Poisson valószínűségek
    prob_over  = poisson_over_prob(expected, line)
    prob_under = 1.0 - prob_over

    # Irány meghatározása
    if prob_over >= 0.58 and expected > line + 3:
        direction = "over"
        prob      = prob_over
        odds      = best_over
    elif prob_under >= 0.58 and expected < line - 3:
        direction = "under"
        prob      = prob_under
        odds      = best_under
    else:
        logger.info(f"Nincs elegendő edge: {home} vs {away} | várható={expected}, vonal={line}")
        return None

    # Szorzó ellenőrzés
    if odds and not (MIN_ODDS <= odds <= MAX_ODDS):
        logger.info(f"Szorzó kívül: {odds} | {home} vs {away}")
        return None

    confidence = calc_confidence(
        expected, line, direction, prob, odds, h2h_avg, n_bookmakers
    )

    if confidence < MIN_CONFIDENCE:
        logger.info(f"Alacsony konfidencia ({confidence}): {home} vs {away}")
        return None

    tip_label = f"{'OVER' if direction == 'over' else 'UNDER'} {line}"

    return {
        "event_id":       int(event_id),
        "home":           home,
        "away":           away,
        "league":         league,
        "league_id":      league_id,
        "start_timestamp": start_ts,
        "tip":            tip_label,
        "line":           line,
        "expected_total": expected,
        "home_off_rating": home_stats["off_rating"],
        "away_off_rating": away_stats["off_rating"],
        "home_def_rating": home_stats["def_rating"],
        "away_def_rating": away_stats["def_rating"],
        "home_pace":      home_stats["pace"],
        "away_pace":      away_stats["pace"],
        "odds":           odds,
        "sent_at":        now_ts,
        "confidence_score": confidence,
        "injury_impact":  None,
        "result":         None,
        "actual_total":   None,
        # extra (nem megy DB-be, csak üzenetbe)
        "prob":           prob,
        "h2h_avg":        h2h_avg,
        "direction":      direction,
        "n_bookmakers":   n_bookmakers,
    }


# ── Telegram üzenet ────────────────────────────────────────────────────────────

def confidence_stars(score: int) -> str:
    if score >= 85:
        return "⭐⭐⭐"
    elif score >= 75:
        return "⭐⭐"
    return "⭐"


def format_tip_message(t: dict) -> str:
    start_dt = datetime.utcfromtimestamp(t["start_timestamp"]).strftime("%Y.%m.%d %H:%M")
    stars    = confidence_stars(t["confidence_score"])
    direction_emoji = "🔼" if t["direction"] == "over" else "🔽"

    lines = [
        f"🏀 <b>Kosárlabda Over/Under Tipp</b>",
        f"",
        f"🏆 {t['league']}",
        f"⚔️ <b>{t['home']}</b> vs <b>{t['away']}</b>",
        f"🕐 {start_dt} UTC",
        f"",
        f"{direction_emoji} <b>Tipp: {t['tip']}</b>",
        f"📊 Várható összpontszám: <b>{t['expected_total']}</b>",
        f"📈 Valószínűség: <b>{t['prob']*100:.0f}%</b>",
    ]

    if t.get("odds"):
        lines.append(f"💰 Legjobb szorzó: <b>{t['odds']:.2f}</b>")
    if t.get("n_bookmakers", 0) > 1:
        lines.append(f"🏦 Irodák száma: {t['n_bookmakers']}")
    if t.get("h2h_avg"):
        lines.append(f"🔄 H2H átlag: {t['h2h_avg']} pont/meccs")

    lines += [
        f"",
        f"📉 {t['home']} off: {t['home_off_rating']} | def: {t['home_def_rating']}",
        f"📉 {t['away']} off: {t['away_off_rating']} | def: {t['away_def_rating']}",
        f"",
        f"{stars} Megbízhatóság: <b>{t['confidence_score']}/100</b>",
    ]
    return "\n".join(lines)


def format_result_message(t: dict, actual: int, result: str) -> str:
    emoji = "✅" if result == "win" else "❌"
    tip_str = t["tip"]
    return (
        f"{emoji} <b>Eredmény</b>\n"
        f"⚔️ {t['home']} vs {t['away']}\n"
        f"🎯 Tipp: {tip_str}\n"
        f"📊 Tényleges összpontszám: <b>{actual}</b>\n"
        f"{'✅ Nyert!' if result == 'win' else '❌ Veszett'}"
    )


# ── Scan és eredmény ───────────────────────────────────────────────────────────

async def scan_and_send(bot: Bot):
    logger.info("Kosárlabda scan indul...")
    sent_ids = get_sent_event_ids()
    today    = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    events = []
    for d in [today, tomorrow]:
        events.extend(fetch_sofa_events(d))

    logger.info(f"{len(events)} kosárlabda mérkőzés találva")

    sent_count = 0
    for event in events:
        tip = await asyncio.to_thread(analyze_event, event, sent_ids)
        if not tip:
            continue

        msg = format_tip_message(tip)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Nyert",   callback_data=f"bball_win_{tip['event_id']}"),
            InlineKeyboardButton("❌ Veszett", callback_data=f"bball_loss_{tip['event_id']}"),
        ]])

        try:
            await bot.send_message(
                chat_id=BASKETBALL_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            save_tip(tip)
            sent_ids.add(str(tip["event_id"]))
            sent_count += 1
            logger.info(f"Tipp elküldve: {tip['home']} vs {tip['away']} → {tip['tip']}")
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Telegram küldési hiba: {e}")

    logger.info(f"Scan kész, {sent_count} új tipp elküldve")


async def check_results(bot: Bot):
    logger.info("Eredmény ellenőrzés indul...")
    pending = load_pending_tips()
    now_ts  = int(time.time())

    for t in pending:
        start_ts = t["start_timestamp"]
        elapsed_min = (now_ts - start_ts) / 60

        if elapsed_min < RESULT_DELAY_MIN:
            continue

        hs, as_ = await asyncio.to_thread(fetch_event_score, t["event_id"])
        if hs is None or as_ is None:
            continue

        actual = hs + as_
        line   = t["line"]
        tip    = t["tip"].lower()

        if "over" in tip:
            result = "win" if actual > line else "loss"
        else:
            result = "win" if actual < line else "loss"

        update_result(t["event_id"], result, actual)

        msg = format_result_message(t, actual, result)
        try:
            await bot.send_message(
                chat_id=BASKETBALL_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Eredmény küldési hiba: {e}")


# ── Telegram parancsok ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏀 Kosárlabda Over/Under Bot aktív!\n\n"
        "/stat — statisztikák\n"
        "/lezar — manuális eredmény"
    )


async def cmd_stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE result='win')  AS wins,
                        COUNT(*) FILTER (WHERE result='loss') AS losses,
                        COUNT(*) FILTER (WHERE result IS NULL) AS pending
                    FROM basketball_tips
                """)
                row = cur.fetchone()
        wins, losses, pending = row
        total = wins + losses
        rate  = f"{wins/total*100:.0f}%" if total > 0 else "–"
        await update.message.reply_text(
            f"📊 <b>Kosárlabda statisztikák</b>\n\n"
            f"✅ Nyert: {wins}\n"
            f"❌ Veszett: {losses}\n"
            f"⏳ Nyitott: {pending}\n"
            f"🎯 Találati arány: {rate}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(f"Hiba: {e}")


async def cmd_lezar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = load_pending_tips()
    if not pending:
        await update.message.reply_text("Nincs nyitott tipp.")
        return
    for t in pending:
        dt = datetime.utcfromtimestamp(t["start_timestamp"]).strftime("%m.%d %H:%M")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Nyert",   callback_data=f"bball_win_{t['event_id']}"),
            InlineKeyboardButton("❌ Veszett", callback_data=f"bball_loss_{t['event_id']}"),
        ]])
        await update.message.reply_text(
            f"🏀 {t['home']} vs {t['away']}\n"
            f"🎯 {t['tip']} | 🕐 {dt} UTC",
            reply_markup=keyboard,
        )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("bball_"):
        return

    parts  = data.split("_")
    action = parts[1]
    eid    = int(parts[2])

    result = "win" if action == "win" else "loss"
    update_result(eid, result, 0)

    emoji = "✅ Nyert" if result == "win" else "❌ Veszett"
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"{emoji} — manuálisan lezárva.")


# ── Fő loop ────────────────────────────────────────────────────────────────────

async def main():
    init_db()
    app = Application.builder().token(BASKETBALL_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("stat",   cmd_stat))
    app.add_handler(CommandHandler("lezar",  cmd_lezar))
    app.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^bball_"))

    scheduler = AsyncIOScheduler()
    bot = app.bot

    # Scan: minden 3 órában
    scheduler.add_job(scan_and_send,    "interval", hours=3,   args=[bot], id="bball_scan",
                      next_run_time=datetime.utcnow() + timedelta(seconds=30))
    # Eredmény: minden 20 percben
    scheduler.add_job(check_results,    "interval", minutes=20, args=[bot], id="bball_results")

    scheduler.start()
    logger.info("Kosárlabda bot elindult")

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
