import os
import asyncio
import time
import logging
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, date, timedelta
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if os.environ.get("CORNERS_BOT_DISABLED", "").lower() in ("1", "true", "yes"):
    print("CORNERS_BOT_DISABLED beállítva — bot nem indul el.")
    sys.exit(0)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

CORNERS_BOT_TOKEN = os.environ["CORNERS_BOT_TOKEN"]
CORNERS_CHAT_ID = os.environ.get("CORNERS_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SPORTS_API_KEY = os.environ.get("SPORTS_API_KEY", "")

API_BASE = "https://v3.football.api-sports.io"
API_HEADERS = {
    "x-apisports-key": SPORTS_API_KEY,
}

CORNER_LINE = 9.5
OVER_THRESHOLD = 12.0
UNDER_THRESHOLD = 7.0
RESULT_DELAY_MIN = 110
MAX_FIXTURES_PER_SCAN = 30
MIN_RECENT_MATCHES = 4
API_DELAY_SEC = 1.0
MIN_CORNER_ODDS = 1.60

_corner_cache: dict = {}


def get_strength(expected: float) -> tuple[str, str]:
    margin = abs(expected - CORNER_LINE)
    if margin >= 2.5:
        return "⚡⚡⚡", "Nagyon erős"
    elif margin >= 1.5:
        return "⚡⚡", "Erős"
    else:
        return "⚡", "Mérsékelt"


# ─────────────────────────────────────────────
#  ADATBÁZIS
# ─────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    sql = """
    CREATE TABLE IF NOT EXISTS corner_tips (
        event_id         BIGINT PRIMARY KEY,
        home             TEXT NOT NULL,
        away             TEXT NOT NULL,
        league           TEXT NOT NULL,
        league_id        INTEGER,
        start_timestamp  BIGINT NOT NULL,
        tip              TEXT NOT NULL,
        line             REAL DEFAULT 9.5,
        expected_corners REAL NOT NULL,
        home_avg         REAL,
        away_avg         REAL,
        odds             REAL DEFAULT NULL,
        sent_at          BIGINT NOT NULL,
        result           TEXT DEFAULT NULL,
        actual_corners   INTEGER DEFAULT NULL
    );
    ALTER TABLE corner_tips ADD COLUMN IF NOT EXISTS odds REAL DEFAULT NULL;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    logger.info("corner_tips tábla inicializálva")


def load_corner_tips() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM corner_tips ORDER BY start_timestamp DESC")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"Corner tippek betöltési hiba: {e}")
        return []


def save_corner_tip(tip: dict):
    sql = """
    INSERT INTO corner_tips
        (event_id, home, away, league, league_id, start_timestamp, tip, line,
         expected_corners, home_avg, away_avg, odds, sent_at)
    VALUES (%(event_id)s, %(home)s, %(away)s, %(league)s, %(league_id)s,
            %(start_timestamp)s, %(tip)s, %(line)s, %(expected_corners)s,
            %(home_avg)s, %(away_avg)s, %(odds)s, %(sent_at)s)
    ON CONFLICT (event_id) DO NOTHING;
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tip)
            conn.commit()
    except Exception as e:
        logger.error(f"Corner tipp mentési hiba: {e}")


def update_corner_result(event_id: int, result: str, actual_corners: int):
    sql = "UPDATE corner_tips SET result=%s, actual_corners=%s WHERE event_id=%s"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (result, actual_corners, event_id))
            conn.commit()
        logger.info(f"Corner eredmény frissítve: event_id={event_id}, result={result}, corners={actual_corners}")
    except Exception as e:
        logger.error(f"Corner eredmény frissítési hiba: {e}")


# ─────────────────────────────────────────────
#  API-FOOTBALL
# ─────────────────────────────────────────────

def api_get(endpoint: str, params: dict) -> dict:
    try:
        time.sleep(API_DELAY_SEC)
        url = f"{API_BASE}/{endpoint}"
        r = requests.get(url, headers=API_HEADERS, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"API-Football hiba ({endpoint}): {e}")
        return {}


def fetch_today_fixtures() -> list:
    today = date.today().isoformat()
    data = api_get("fixtures", {"date": today, "status": "NS", "timezone": "Europe/Budapest"})
    return data.get("response", [])


def fetch_team_last_fixtures(team_id: int, last: int = 10) -> list:
    data = api_get("fixtures", {"team": team_id, "last": last, "status": "FT"})
    return data.get("response", [])


def fetch_fixture_statistics(fixture_id: int) -> dict:
    data = api_get("fixtures/statistics", {"fixture": fixture_id})
    results = {}
    for team_stat in data.get("response", []):
        team_id = team_stat.get("team", {}).get("id")
        for stat in team_stat.get("statistics", []):
            if stat.get("type") == "Corner Kicks":
                try:
                    results[team_id] = int(stat.get("value") or 0)
                except Exception:
                    results[team_id] = 0
    return results


def get_team_corner_avg(team_id: int, is_home: bool) -> float | None:
    today_str = date.today().isoformat()
    cache_key = f"{team_id}_{'home' if is_home else 'away'}"

    if cache_key in _corner_cache and _corner_cache[cache_key].get("date") == today_str:
        return _corner_cache[cache_key]["avg"]

    fixtures = fetch_team_last_fixtures(team_id, last=10)
    if not fixtures:
        return None

    relevant = []
    for fx in fixtures:
        teams = fx.get("teams", {})
        if is_home and teams.get("home", {}).get("id") == team_id:
            relevant.append(fx)
        elif not is_home and teams.get("away", {}).get("id") == team_id:
            relevant.append(fx)

    if len(relevant) < MIN_RECENT_MATCHES:
        relevant = fixtures

    if len(relevant) < MIN_RECENT_MATCHES:
        return None

    corners_list = []
    for fx in relevant[:7]:
        fx_id = fx.get("fixture", {}).get("id")
        if not fx_id:
            continue
        stat = fetch_fixture_statistics(fx_id)
        if team_id in stat:
            corners_list.append(stat[team_id])

    if len(corners_list) < MIN_RECENT_MATCHES:
        return None

    avg = sum(corners_list) / len(corners_list)
    _corner_cache[cache_key] = {"avg": round(avg, 2), "date": today_str}
    return avg


def fetch_fixture_result(fixture_id: int):
    data = api_get("fixtures", {"id": fixture_id})
    responses = data.get("response", [])
    if not responses:
        return None, None

    fx = responses[0]
    status = fx.get("fixture", {}).get("status", {}).get("short", "")
    if status not in ("FT", "AET", "PEN"):
        return None, None

    stat = fetch_fixture_statistics(fixture_id)
    if not stat:
        return None, None

    total = sum(stat.values())
    return "FT", total


# ─────────────────────────────────────────────
#  TIP ELEMZÉS
# ─────────────────────────────────────────────

def analyze_fixture(fixture: dict) -> dict | None:
    teams = fixture.get("teams", {})
    home_team = teams.get("home", {})
    away_team = teams.get("away", {})
    home_id = home_team.get("id")
    away_id = away_team.get("id")
    home_name = home_team.get("name", "?")
    away_name = away_team.get("name", "?")
    league = fixture.get("league", {})
    league_name = league.get("name", "?")
    league_country = league.get("country", "")
    league_id = league.get("id")
    fx_data = fixture.get("fixture", {})
    fixture_id = fx_data.get("id")
    start_ts = int(datetime.fromisoformat(fx_data.get("date", "").replace("Z", "+00:00")).timestamp()) if fx_data.get("date") else 0

    if not home_id or not away_id or not fixture_id:
        return None

    logger.info(f"Szöglet elemzés: {home_name} vs {away_name} ({league_name})")

    home_avg = get_team_corner_avg(home_id, is_home=True)
    away_avg = get_team_corner_avg(away_id, is_home=False)

    if home_avg is None or away_avg is None:
        logger.info(f"Nincs elég corner adat: {home_name} vs {away_name}")
        return None

    expected = round(home_avg + away_avg, 1)

    if expected >= OVER_THRESHOLD:
        tip = "over"
    elif expected <= UNDER_THRESHOLD:
        tip = "under"
    else:
        logger.info(f"Nem elég erős jel ({expected}): {home_name} vs {away_name}")
        return None

    full_league = f"{league_country} – {league_name}" if league_country else league_name

    return {
        "event_id": fixture_id,
        "home": home_name,
        "away": away_name,
        "league": full_league,
        "league_id": league_id,
        "start_timestamp": start_ts,
        "tip": tip,
        "line": CORNER_LINE,
        "expected_corners": expected,
        "home_avg": round(home_avg, 1),
        "away_avg": round(away_avg, 1),
        "odds": None,
        "sent_at": int(datetime.utcnow().timestamp()),
        "result": None,
    }


# ─────────────────────────────────────────────
#  ÜZENET FORMÁZÁS
# ─────────────────────────────────────────────

def format_tip_msg(tip: dict) -> str:
    start_dt = datetime.utcfromtimestamp(tip["start_timestamp"]) + timedelta(hours=1)
    date_str = start_dt.strftime("%Y.%m.%d")
    time_str = start_dt.strftime("%H:%M")
    tip_icon = "⬆️" if tip["tip"] == "over" else "⬇️"
    tip_label = "OVER" if tip["tip"] == "over" else "UNDER"
    strength_icon, strength_label = get_strength(tip["expected_corners"])
    odds = tip.get("odds")
    odds_str = f"\n💰 Szorzó: *{odds}*" if odds else ""
    return (
        f"⚽ *Szöglet Tipp*\n\n"
        f"🏆 {tip['league']}\n"
        f"🕐 {date_str} {time_str}\n"
        f"🆚 *{tip['home']}* vs *{tip['away']}*\n"
        f"📊 Várható szögletek: *{tip['expected_corners']}*\n"
        f"   ┣ Hazai: {tip['home_avg']} | Vendég: {tip['away_avg']}\n"
        f"{tip_icon} Tipp: *{tip_label} {tip['line']} szöglet*\n"
        f"{strength_icon} Erősség: *{strength_label}*"
        f"{odds_str}"
    )


def format_result_msg(tip: dict, actual: int, result: str) -> str:
    icon = "✅" if result == "win" else "❌"
    label = "NYERT" if result == "win" else "VESZETT"
    tip_label = "OVER" if tip["tip"] == "over" else "UNDER"
    return (
        f"{icon} *Szöglet Eredmény — {label}!*\n\n"
        f"🏆 {tip['league']}\n"
        f"🆚 {tip['home']} vs {tip['away']}\n"
        f"🎯 Tippünk: *{tip_label} {tip['line']}*\n"
        f"📊 Várható volt: {tip['expected_corners']}\n"
        f"🔢 Tényleges szögletek: *{actual}*"
    )


# ─────────────────────────────────────────────
#  SCHEDULER FELADATOK
# ─────────────────────────────────────────────

async def scan_and_send(bot: Bot):
    if not CORNERS_CHAT_ID:
        logger.warning("CORNERS_CHAT_ID nincs beállítva, kihagyva")
        return

    logger.info("Szöglet tipp keresés indul...")
    existing_ids = {t["event_id"] for t in load_corner_tips()}
    fixtures = fetch_today_fixtures()

    now_ts = int(datetime.utcnow().timestamp())
    upcoming = [
        fx for fx in fixtures
        if fx.get("fixture", {}).get("status", {}).get("short") == "NS"
    ]

    logger.info(f"{len(upcoming)} közelgő meccs ma")
    sent = 0

    for fixture in upcoming[:MAX_FIXTURES_PER_SCAN]:
        fx_id = fixture.get("fixture", {}).get("id")
        if fx_id in existing_ids:
            continue

        tip = analyze_fixture(fixture)
        if tip is None:
            continue

        save_corner_tip(tip)
        msg = format_tip_msg(tip)

        try:
            await bot.send_message(
                chat_id=CORNERS_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
            logger.info(f"Szöglet tipp elküldve: {tip['home']} vs {tip['away']}, várható: {tip['expected_corners']}, tipp: {tip['tip']}")
            sent += 1
        except Exception as e:
            logger.error(f"Tipp küldési hiba: {e}")

    logger.info(f"Szöglet scan kész, {sent} új tipp elküldve")


async def check_results(bot: Bot):
    tips = load_corner_tips()
    now_ts = int(datetime.utcnow().timestamp())

    for t in tips:
        if t.get("result") is not None:
            continue
        if t.get("start_timestamp", 0) + RESULT_DELAY_MIN * 60 > now_ts:
            continue

        status, actual = fetch_fixture_result(t["event_id"])
        if actual is None:
            continue

        if t["tip"] == "over":
            result = "win" if actual > t["line"] else "loss"
        else:
            result = "win" if actual < t["line"] else "loss"

        update_corner_result(t["event_id"], result, actual)
        logger.info(f"Eredmény frissítve: {t['event_id']} -> {result} (szögletek: {actual})")

        if not CORNERS_CHAT_ID:
            continue

        msg = format_result_msg(t, actual, result)
        try:
            await bot.send_message(
                chat_id=CORNERS_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error(f"Eredmény értesítő hiba: {e}")


# ─────────────────────────────────────────────
#  MAIN — polling nélkül, csak APScheduler
# ─────────────────────────────────────────────

async def main():
    init_db()

    bot = Bot(token=CORNERS_BOT_TOKEN)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(scan_and_send, "interval", seconds=1800, args=[bot], next_run_time=datetime.utcnow())
    scheduler.add_job(check_results, "interval", seconds=900, args=[bot], next_run_time=datetime.utcnow() + timedelta(seconds=120))
    scheduler.start()

    logger.info("⚽ Szöglet Bot indul... (API-Football, polling nélkül)")

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot leállítva.")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
