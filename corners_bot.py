import os
import json
import logging
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, date, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

CORNERS_BOT_TOKEN = os.environ["CORNERS_BOT_TOKEN"]
CORNERS_CHAT_ID = os.environ.get("CORNERS_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

FOOTBALL_API_BASE = "https://api-football-v1.p.rapidapi.com/v3"
FOOTBALL_API_HEADERS = {
    "x-rapidapi-key": os.environ.get("SPORTS_API_KEY", ""),
    "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
}

CORNER_LINE = 9.5
OVER_THRESHOLD = 10.5
UNDER_THRESHOLD = 8.5
RESULT_DELAY_MIN = 110
MAX_FIXTURES_PER_SCAN = 40


def get_strength(expected: float) -> tuple[str, str]:
    """Visszaadja az erősségi fokozatot (ikon, szöveg) a várható szögletszám alapján."""
    margin = abs(expected - CORNER_LINE)
    if margin >= 2.5:
        return "⚡⚡⚡", "Nagyon erős"
    elif margin >= 1.5:
        return "⚡⚡", "Erős"
    else:
        return "⚡", "Mérsékelt"

_corner_cache: dict = {}


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
        sent_at          BIGINT NOT NULL,
        result           TEXT DEFAULT NULL,
        actual_corners   INTEGER DEFAULT NULL
    );
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
         expected_corners, home_avg, away_avg, sent_at)
    VALUES (%(event_id)s, %(home)s, %(away)s, %(league)s, %(league_id)s,
            %(start_timestamp)s, %(tip)s, %(line)s, %(expected_corners)s,
            %(home_avg)s, %(away_avg)s, %(sent_at)s)
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
#  FOOTBALL API
# ─────────────────────────────────────────────

def api_get(endpoint: str, params: dict) -> dict:
    try:
        r = requests.get(
            f"{FOOTBALL_API_BASE}/{endpoint}",
            headers=FOOTBALL_API_HEADERS,
            params=params,
            timeout=15
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"API hiba ({endpoint} {params}): {e}")
        return {}


def fetch_today_fixtures() -> list:
    today = date.today().isoformat()
    data = api_get("fixtures", {"date": today, "timezone": "UTC"})
    return data.get("response", [])


def fetch_team_recent_fixtures(team_id: int, last: int = 15) -> list:
    data = api_get("fixtures", {"team": team_id, "last": last, "status": "FT"})
    return data.get("response", [])


def fetch_fixture_statistics(fixture_id: int) -> dict:
    data = api_get("fixtures/statistics", {"fixture": fixture_id})
    teams_stats = data.get("response", [])
    result = {}
    for team_data in teams_stats:
        team_id = team_data["team"]["id"]
        corners = 0
        for stat in team_data.get("statistics", []):
            if stat["type"] == "Corner Kicks":
                try:
                    corners = int(stat["value"] or 0)
                except Exception:
                    corners = 0
        result[team_id] = corners
    return result


def get_team_corner_avg(team_id: int, is_home: bool) -> float | None:
    today_str = date.today().isoformat()
    cache_key = f"{team_id}_{'home' if is_home else 'away'}"

    if cache_key in _corner_cache and _corner_cache[cache_key].get("date") == today_str:
        return _corner_cache[cache_key]["avg"]

    recent = fetch_team_recent_fixtures(team_id, last=15)
    if not recent:
        return None

    relevant = []
    for f in recent:
        home_id = f.get("teams", {}).get("home", {}).get("id")
        if is_home and home_id == team_id:
            relevant.append(f)
        elif not is_home and home_id != team_id:
            relevant.append(f)
        if len(relevant) >= 7:
            break

    if len(relevant) < 3:
        relevant = recent[:7]

    corners_list = []
    for f in relevant[:7]:
        fid = f["fixture"]["id"]
        stats = fetch_fixture_statistics(fid)
        team_corners = stats.get(team_id, 0)
        if team_corners > 0:
            corners_list.append(team_corners)

    if not corners_list:
        return None

    avg = sum(corners_list) / len(corners_list)
    _corner_cache[cache_key] = {"avg": round(avg, 2), "date": today_str}
    return avg


def fetch_fixture_result(fixture_id: int):
    data = api_get("fixtures", {"id": fixture_id})
    fixtures = data.get("response", [])
    if not fixtures:
        return None, None
    f = fixtures[0]
    status = f.get("fixture", {}).get("status", {}).get("short", "")
    if status not in ("FT", "AET", "PEN"):
        return None, None
    stats = fetch_fixture_statistics(fixture_id)
    total_corners = sum(stats.values())
    return status, total_corners if total_corners > 0 else None


# ─────────────────────────────────────────────
#  TIP ELEMZÉS
# ─────────────────────────────────────────────

def analyze_fixture(fixture: dict) -> dict | None:
    f = fixture.get("fixture", {})
    teams = fixture.get("teams", {})
    league = fixture.get("league", {})

    fixture_id = f.get("id")
    status = f.get("status", {}).get("short", "")
    if status not in ("NS", "TBD"):
        return None

    home_id = teams.get("home", {}).get("id")
    away_id = teams.get("away", {}).get("id")
    home_name = teams.get("home", {}).get("name", "?")
    away_name = teams.get("away", {}).get("name", "?")
    league_name = league.get("name", "?")
    league_id = league.get("id")
    start_ts = f.get("timestamp", 0)

    if not home_id or not away_id or not fixture_id:
        return None

    logger.info(f"Szöglet elemzés: {home_name} vs {away_name} ({league_name})")

    home_avg = get_team_corner_avg(home_id, is_home=True)
    away_avg = get_team_corner_avg(away_id, is_home=False)

    if home_avg is None or away_avg is None:
        logger.info(f"Nincs elég adat: {home_name} vs {away_name}")
        return None

    expected = round(home_avg + away_avg, 1)

    if expected >= OVER_THRESHOLD:
        tip = "over"
    elif expected <= UNDER_THRESHOLD:
        tip = "under"
    else:
        logger.info(f"Nem elég erős jel ({expected}): {home_name} vs {away_name}")
        return None

    return {
        "event_id": fixture_id,
        "home": home_name,
        "away": away_name,
        "league": league_name,
        "league_id": league_id,
        "start_timestamp": start_ts,
        "tip": tip,
        "line": CORNER_LINE,
        "expected_corners": expected,
        "home_avg": round(home_avg, 1),
        "away_avg": round(away_avg, 1),
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
    return (
        f"⚽ *Szöglet Tipp*\n\n"
        f"🏆 {tip['league']}\n"
        f"🕐 {date_str} {time_str}\n"
        f"🆚 *{tip['home']}* vs *{tip['away']}*\n"
        f"📊 Várható szögletek: *{tip['expected_corners']}*\n"
        f"   ┣ Hazai: {tip['home_avg']} | Vendég: {tip['away_avg']}\n"
        f"{tip_icon} Tipp: *{tip_label} {tip['line']} szöglet*\n"
        f"{strength_icon} Erősség: *{strength_label}*"
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

async def scan_and_send(context):
    if not CORNERS_CHAT_ID:
        logger.warning("CORNERS_CHAT_ID nincs beállítva, kihagyva")
        return

    logger.info("Szöglet tipp keresés indul...")
    existing_ids = {t["event_id"] for t in load_corner_tips()}
    fixtures = fetch_today_fixtures()

    now_ts = int(datetime.utcnow().timestamp())
    upcoming = [
        f for f in fixtures
        if f.get("fixture", {}).get("status", {}).get("short") in ("NS", "TBD")
        and f.get("fixture", {}).get("timestamp", 0) > now_ts
        and f.get("fixture", {}).get("timestamp", 0) < now_ts + 12 * 3600
    ]

    logger.info(f"{len(upcoming)} közelgő meccs a következő 12 órában")
    sent = 0

    for fixture in upcoming[:MAX_FIXTURES_PER_SCAN]:
        fid = fixture.get("fixture", {}).get("id")
        if fid in existing_ids:
            continue

        tip = analyze_fixture(fixture)
        if tip is None:
            continue

        save_corner_tip(tip)
        msg = format_tip_msg(tip)

        try:
            await context.bot.send_message(
                chat_id=CORNERS_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
            logger.info(f"Szöglet tipp elküldve: {tip['home']} vs {tip['away']}, várható: {tip['expected_corners']}, tipp: {tip['tip']}")
            sent += 1
        except Exception as e:
            logger.error(f"Tipp küldési hiba: {e}")

    logger.info(f"Szöglet scan kész, {sent} új tipp elküldve")


async def check_results(context):
    if not CORNERS_CHAT_ID:
        return

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
        msg = format_result_msg(t, actual, result)

        try:
            await context.bot.send_message(
                chat_id=CORNERS_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error(f"Eredmény értesítő hiba: {e}")


# ─────────────────────────────────────────────
#  TELEGRAM PARANCSOK
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚽ *Szöglet Bot — Parancsok*\n\n"
        "/szoglet\\_tippek — Mai szöglet tippek\n"
        "/szoglet\\_stat — Statisztikák\n"
        "/help — Súgó"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_tippek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = load_corner_tips()
    today_start = int(datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    today_tips = [t for t in tips if t.get("start_timestamp", 0) >= today_start]

    if not today_tips:
        await update.message.reply_text("⚽ Ma még nincs szöglet tipp.")
        return

    lines = [f"⚽ *Mai szöglet tippek ({len(today_tips)} db)*\n"]
    for t in today_tips:
        start_dt = datetime.utcfromtimestamp(t["start_timestamp"]) + timedelta(hours=1)
        time_str = start_dt.strftime("%H:%M")
        tip_icon = "⬆️" if t["tip"] == "over" else "⬇️"
        tip_label = "OVER" if t["tip"] == "over" else "UNDER"
        res = ""
        if t.get("result") == "win":
            res = " ✅"
        elif t.get("result") == "loss":
            res = " ❌"
        lines.append(
            f"{tip_icon} *{t['home']}* vs *{t['away']}* ({time_str})\n"
            f"   🏆 {t['league']}\n"
            f"   → {tip_label} {t['line']} | Várható: {t['expected_corners']}{res}\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = load_corner_tips()
    settled = [t for t in tips if t.get("result") is not None]
    pending = [t for t in tips if t.get("result") is None]
    wins = sum(1 for t in settled if t["result"] == "win")
    losses = len(settled) - wins
    win_rate = (wins / len(settled) * 100) if settled else 0

    over_tips = [t for t in settled if t["tip"] == "over"]
    under_tips = [t for t in settled if t["tip"] == "under"]
    over_wins = sum(1 for t in over_tips if t["result"] == "win")
    under_wins = sum(1 for t in under_tips if t["result"] == "win")

    lines = [
        "📊 *Szöglet Bot Statisztika*\n",
        f"🎯 Összes tipp: *{len(tips)}* ({len(settled)} lezárt, {len(pending)} folyamatban)",
        f"✅ Nyert: *{wins}*",
        f"❌ Veszett: *{losses}*",
        f"📈 Nyerési arány: *{win_rate:.1f}%*\n",
        f"⬆️ Over tippek: {len(over_tips)} db → {over_wins} nyert",
        f"⬇️ Under tippek: {len(under_tips)} db → {under_wins} nyert",
    ]

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    init_db()

    app = Application.builder().token(CORNERS_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("szoglet_tippek", cmd_tippek))
    app.add_handler(CommandHandler("szoglet_stat", cmd_stat))

    jq = app.job_queue
    jq.run_repeating(scan_and_send, interval=1800, first=60)
    jq.run_repeating(check_results, interval=900, first=120)

    logger.info("⚽ Szöglet Bot indul...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
