import os
import asyncio
import time
import logging
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, date, timedelta
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
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

# ═══════════════════════════════════════════════════════════════════
#  KONFIGURÁCIÓ – Töltsd ki PythonAnywhere-en (vagy .env fájlban)!
# ═══════════════════════════════════════════════════════════════════
_BOT_TOKEN    = ""   # Telegram bot token (BotFather-től)
_CHAT_ID      = ""   # Telegram chat/csoport ID (pl. -1001234567890)
_DATABASE_URL = ""   # Supabase PostgreSQL URL (postgresql://user:pass@host:5432/db)
# ═══════════════════════════════════════════════════════════════════

CORNERS_BOT_TOKEN = os.environ.get("CORNERS_BOT_TOKEN") or _BOT_TOKEN
CORNERS_CHAT_ID   = os.environ.get("CORNERS_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or _CHAT_ID
DATABASE_URL      = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DATABASE_URL

# Csoportok ahol a tippek és eredmények megjelennek
GROUP_CHAT_IDS = [-1003715006026, -1003835559510]

SOFASCORE_BASE = os.environ.get("SOFASCORE_BASE", "https://www.sofascore.com/api/v1")
SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://www.sofascore.com",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

CORNER_LINE = 9.5
OVER_THRESHOLD = 10.5   # volt 12.0 — az új formula pontosabb, alacsonyabb küszöb elég
UNDER_THRESHOLD = 8.5   # volt 7.0
RESULT_DELAY_MIN = 110
MAX_FIXTURES_PER_SCAN = 30
MIN_RECENT_MATCHES = 3
API_DELAY_SEC = 0.4
MIN_CORNER_ODDS = 1.60
MIN_WING_CORNERS = 4.5  # min CF hogy "wing-play" legyen
OVER_85_MIN_RATE = 0.60 # min hit rate az over 8.5-re
MIN_CONFIDENCE = 84     # legalább ennyi pont kell hogy kimenjen a tipp

_corner_cache: dict = {}
_event_corner_cache: dict = {}  # event_id -> (h, a) — megakadályozza a dupla API hívást


def get_confidence_label(score: int) -> tuple[str, str]:
    if score >= 75:
        return "⚡⚡⚡", "Nagyon erős"
    elif score >= 50:
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
    ALTER TABLE corner_tips ADD COLUMN IF NOT EXISTS confidence_score INTEGER DEFAULT NULL;
    ALTER TABLE corner_tips ADD COLUMN IF NOT EXISTS home_ca REAL DEFAULT NULL;
    ALTER TABLE corner_tips ADD COLUMN IF NOT EXISTS away_ca REAL DEFAULT NULL;
    ALTER TABLE corner_tips ADD COLUMN IF NOT EXISTS home_over_rate REAL DEFAULT NULL;
    ALTER TABLE corner_tips ADD COLUMN IF NOT EXISTS away_over_rate REAL DEFAULT NULL;
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
         expected_corners, home_avg, away_avg, home_ca, away_ca,
         home_over_rate, away_over_rate, confidence_score, odds, sent_at)
    VALUES (%(event_id)s, %(home)s, %(away)s, %(league)s, %(league_id)s,
            %(start_timestamp)s, %(tip)s, %(line)s, %(expected_corners)s,
            %(home_avg)s, %(away_avg)s, %(home_ca)s, %(away_ca)s,
            %(home_over_rate)s, %(away_over_rate)s, %(confidence_score)s,
            %(odds)s, %(sent_at)s)
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
#  SOFASCORE API
# ─────────────────────────────────────────────

def fractional_to_decimal(fractional: str) -> float | None:
    try:
        if "/" in fractional:
            num, den = fractional.split("/")
            return round(int(num) / int(den) + 1, 2)
        return round(float(fractional), 2)
    except Exception:
        return None


def sofa_get(url: str) -> dict:
    try:
        time.sleep(API_DELAY_SEC)
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"SofaScore API hiba ({url}): {e}")
        return {}


def fetch_today_fixtures() -> list:
    today = date.today().isoformat()
    data = sofa_get(f"{SOFASCORE_BASE}/sport/football/scheduled-events/{today}")
    return data.get("events", [])


def fetch_team_recent_matches(team_id: int) -> list:
    data = sofa_get(f"{SOFASCORE_BASE}/team/{team_id}/events/last/0")
    events = data.get("events", [])
    return [e for e in events if e.get("status", {}).get("type") == "finished"]


def fetch_event_corners(event_id: int) -> tuple[int | None, int | None]:
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/statistics")
    stats = data.get("statistics", [])
    for period in stats:
        if period.get("period") == "ALL":
            for group in period.get("groups", []):
                for item in group.get("statisticsItems", []):
                    if "corner" in item.get("name", "").lower():
                        try:
                            h = int(item.get("home") or 0)
                            a = int(item.get("away") or 0)
                            return h, a
                        except Exception:
                            pass
    return None, None


def fetch_event_corners_cached(event_id: int) -> tuple[int | None, int | None]:
    if event_id in _event_corner_cache:
        return _event_corner_cache[event_id]
    result = fetch_event_corners(event_id)
    _event_corner_cache[event_id] = result
    return result


def get_team_corner_stats(team_id: int, is_home: bool) -> dict | None:
    """
    Visszaad egy statisztikai dict-et:
      cf        - Corners For (saját szögletek) átlaga H/A kontextusban
      ca        - Corners Against (kapott szögletek) átlaga H/A kontextusban
      season_total - összes szöglet/meccs átlaga (cf+ca)
      l5_total  - utolsó 5 meccs átlaga (trend)
      over_85_rate - over 8.5 szöglet találati arány
      n         - felhasznált meccsek száma
    """
    today_str = date.today().isoformat()
    cache_key = f"{team_id}_{'H' if is_home else 'A'}_v2"

    if cache_key in _corner_cache and _corner_cache[cache_key].get("date") == today_str:
        return _corner_cache[cache_key]["stats"]

    recent = fetch_team_recent_matches(team_id)
    if not recent:
        return None

    # H/A szplit
    if is_home:
        ha_matches = [e for e in recent if e.get("homeTeam", {}).get("id") == team_id]
    else:
        ha_matches = [e for e in recent if e.get("awayTeam", {}).get("id") == team_id]

    # Ha nincs elég H/A meccs, használjuk az összeset
    if len(ha_matches) < MIN_RECENT_MATCHES:
        ha_matches = recent

    if len(ha_matches) < MIN_RECENT_MATCHES:
        return None

    data_points = []  # (cf, ca, total)
    for ev in ha_matches[:8]:
        eid = ev.get("id")
        if not eid:
            continue
        h, a = fetch_event_corners_cached(eid)
        if h is None:
            continue
        home_team_id = ev.get("homeTeam", {}).get("id")
        if home_team_id == team_id:
            data_points.append((h, a, h + a))
        else:
            data_points.append((a, h, h + a))

    if len(data_points) < MIN_RECENT_MATCHES:
        return None

    cf_vals = [d[0] for d in data_points]
    ca_vals = [d[1] for d in data_points]
    total_vals = [d[2] for d in data_points]

    season_cf = sum(cf_vals) / len(cf_vals)
    season_ca = sum(ca_vals) / len(ca_vals)
    season_total = sum(total_vals) / len(total_vals)

    l5 = total_vals[:5]
    l5_total = sum(l5) / len(l5) if l5 else season_total

    over_85_rate = sum(1 for t in total_vals if t > 8.5) / len(total_vals)

    stats = {
        "cf": round(season_cf, 2),
        "ca": round(season_ca, 2),
        "season_total": round(season_total, 2),
        "l5_total": round(l5_total, 2),
        "over_85_rate": round(over_85_rate, 2),
        "n": len(data_points),
    }
    _corner_cache[cache_key] = {"stats": stats, "date": today_str}
    return stats


def fetch_corner_odds(event_id: int, tip: str) -> float | None:
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/odds/1/all")
    markets = data.get("markets", [])
    for market in markets:
        if "corner" in market.get("marketName", "").lower():
            for choice in market.get("choices", []):
                name = choice.get("name", "").lower()
                if (tip == "over" and name == "over") or (tip == "under" and name == "under"):
                    odds = fractional_to_decimal(choice.get("fractionalValue", ""))
                    if odds:
                        return odds
    return None


def fetch_event_result(event_id: int):
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}")
    event = data.get("event", {})
    status_type = event.get("status", {}).get("type", "")
    if status_type != "finished":
        return None, None

    h_corners, a_corners = fetch_event_corners(event_id)
    if h_corners is None:
        return None, None

    return "FT", h_corners + a_corners


# ─────────────────────────────────────────────
#  TIP ELEMZÉS
# ─────────────────────────────────────────────

def analyze_fixture(event: dict) -> dict | None:
    status_type = event.get("status", {}).get("type", "")
    if status_type != "notstarted":
        return None

    event_id = event.get("id")
    home_team = event.get("homeTeam", {})
    away_team = event.get("awayTeam", {})
    home_id = home_team.get("id")
    away_id = away_team.get("id")
    home_name = home_team.get("name", "?")
    away_name = away_team.get("name", "?")
    tournament = event.get("tournament", {})
    league_name = tournament.get("name", "?")
    category_name = tournament.get("category", {}).get("name", "")
    start_ts = event.get("startTimestamp", 0)

    if not home_id or not away_id or not event_id:
        return None

    logger.info(f"Szöglet elemzés: {home_name} vs {away_name} ({league_name})")

    home_stats = get_team_corner_stats(home_id, is_home=True)
    away_stats = get_team_corner_stats(away_id, is_home=False)

    if home_stats is None or away_stats is None:
        logger.info(f"Nincs elég adat: {home_name} vs {away_name}")
        return None

    # ── 1. BASE (40%): H/A szplit ──────────────────────────────────────────
    # Home team corners at home + Away team corners against at away
    # Away team corners at away + Home team corners against at home
    base = (home_stats["cf"] + away_stats["ca"] +
            away_stats["cf"] + home_stats["ca"]) / 2

    # ── 2. TREND BOOST (30%): L5 vs szezon átlag ──────────────────────────
    avg_l5 = (home_stats["l5_total"] + away_stats["l5_total"]) / 2
    avg_season = (home_stats["season_total"] + away_stats["season_total"]) / 2
    trend_boost = avg_l5 > avg_season
    if trend_boost:
        base *= 1.10

    expected = round(base, 1)

    # ── 3. CONFIDENCE SCORE ────────────────────────────────────────────────
    confidence = 0

    # Wing-play: mindkét csapat CF >= 4.5
    wing_play = home_stats["cf"] >= MIN_WING_CORNERS and away_stats["cf"] >= MIN_WING_CORNERS
    if wing_play:
        confidence += 35

    # Over 8.5 hit rate mindkét csapatnál >= küszöb
    over_rate_ok = (home_stats["over_85_rate"] >= OVER_85_MIN_RATE and
                    away_stats["over_85_rate"] >= OVER_85_MIN_RATE)
    if over_rate_ok:
        confidence += 35

    # Trend boost aktív
    if trend_boost:
        confidence += 15

    # Várható érték távolsága a vonaltól
    margin = abs(expected - CORNER_LINE)
    if margin >= 2.0:
        confidence += 15
    elif margin >= 1.0:
        confidence += 8

    if confidence < MIN_CONFIDENCE:
        logger.info(f"Alacsony konfidencia ({confidence}%): {home_name} vs {away_name} — kihagyva")
        return None

    if expected >= OVER_THRESHOLD:
        tip = "over"
    elif expected <= UNDER_THRESHOLD:
        tip = "under"
    else:
        logger.info(f"Várható szögletek ({expected}) a semleges zónában: {home_name} vs {away_name}")
        return None

    # ── 4. ODDS ────────────────────────────────────────────────────────────
    odds = fetch_corner_odds(event_id, tip)
    if odds is None:
        logger.info(f"Nincs szöglet odds a fogadóirodában, kizárva: {home_name} vs {away_name}")
        return None
    elif odds < MIN_CORNER_ODDS:
        logger.info(f"Szorzó túl alacsony ({odds}): {home_name} vs {away_name}")
        return None

    full_league = f"{category_name} – {league_name}" if category_name else league_name

    return {
        "event_id": event_id,
        "home": home_name,
        "away": away_name,
        "league": full_league,
        "league_id": tournament.get("id"),
        "start_timestamp": start_ts,
        "tip": tip,
        "line": CORNER_LINE,
        "expected_corners": expected,
        "home_avg": round(home_stats["cf"], 1),
        "away_avg": round(away_stats["cf"], 1),
        "home_ca": round(home_stats["ca"], 1),
        "away_ca": round(away_stats["ca"], 1),
        "home_over_rate": home_stats["over_85_rate"],
        "away_over_rate": away_stats["over_85_rate"],
        "confidence_score": confidence,
        "odds": odds,
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
    conf = tip.get("confidence_score") or 0
    conf_icon, conf_label = get_confidence_label(conf)
    odds = tip.get("odds")
    odds_str = f"\n💰 Szorzó: *{odds}*" if odds else ""

    home_cf = tip.get("home_avg", "?")
    away_cf = tip.get("away_avg", "?")
    home_ca = tip.get("home_ca")
    away_ca = tip.get("away_ca")
    home_rate = tip.get("home_over_rate")
    away_rate = tip.get("away_over_rate")

    cf_ca_str = ""
    if home_ca is not None and away_ca is not None:
        cf_ca_str = (
            f"   ┣ Hazai CF: {home_cf} | CA: {home_ca}\n"
            f"   ┗ Vendég CF: {away_cf} | CA: {away_ca}\n"
        )
    else:
        cf_ca_str = f"   ┣ Hazai: {home_cf} | Vendég: {away_cf}\n"

    rate_str = ""
    if home_rate is not None and away_rate is not None:
        rate_str = f"📈 Over 8.5 ráta: {int(home_rate*100)}% | {int(away_rate*100)}%\n"

    return (
        f"⚽ *Szöglet Tipp*\n\n"
        f"🏆 {tip['league']}\n"
        f"🕐 {date_str} {time_str}\n"
        f"🆚 *{tip['home']}* vs *{tip['away']}*\n"
        f"📊 Várható szögletek: *{tip['expected_corners']}*\n"
        f"{cf_ca_str}"
        f"{rate_str}"
        f"{tip_icon} Tipp: *{tip_label} {tip['line']} szöglet*\n"
        f"{conf_icon} Konfidencia: *{conf_label}* ({conf}%)"
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

def _sync_collect_tips() -> list:
    existing_ids = {t["event_id"] for t in load_corner_tips()}
    fixtures = fetch_today_fixtures()
    now_ts = int(datetime.utcnow().timestamp())
    upcoming = [
        e for e in fixtures
        if e.get("status", {}).get("type") == "notstarted"
        and e.get("startTimestamp", 0) > now_ts
        and e.get("startTimestamp", 0) < now_ts + 12 * 3600
    ]
    logger.info(f"{len(upcoming)} közelgő meccs a következő 12 órában")
    tips_to_send = []
    for event in upcoming[:MAX_FIXTURES_PER_SCAN]:
        eid = event.get("id")
        if eid in existing_ids:
            continue
        tip = analyze_fixture(event)
        if tip is None:
            continue
        save_corner_tip(tip)
        tips_to_send.append(tip)
    return tips_to_send


def _sync_collect_results() -> list:
    tips = load_corner_tips()
    now_ts = int(datetime.utcnow().timestamp())
    results = []
    for t in tips:
        if t.get("result") is not None:
            continue
        if t.get("start_timestamp", 0) + RESULT_DELAY_MIN * 60 > now_ts:
            continue
        status, actual = fetch_event_result(t["event_id"])
        if actual is None:
            continue
        if t["tip"] == "over":
            result = "win" if actual > t["line"] else "loss"
        else:
            result = "win" if actual < t["line"] else "loss"
        update_corner_result(t["event_id"], result, actual)
        logger.info(f"Eredmény frissítve: {t['event_id']} -> {result} (szögletek: {actual})")
        results.append((t, actual, result))
    return results


async def _send_to_all(bot: Bot, msg: str):
    """Tipp/eredmény: admin csatorna + mindkét csoport."""
    for chat_id in [CORNERS_CHAT_ID] + GROUP_CHAT_IDS:
        if not chat_id:
            continue
        try:
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Küldési hiba ({chat_id}): {e}")


async def scan_and_send(bot: Bot):
    if not CORNERS_CHAT_ID:
        logger.warning("CORNERS_CHAT_ID nincs beállítva, kihagyva")
        return
    logger.info("Szöglet tipp keresés indul...")
    tips = await asyncio.to_thread(_sync_collect_tips)
    sent = 0
    for tip in tips:
        msg = format_tip_msg(tip)
        await _send_to_all(bot, msg)
        logger.info(f"Szöglet tipp elküldve: {tip['home']} vs {tip['away']}, várható: {tip['expected_corners']}, tipp: {tip['tip']}")
        sent += 1
    logger.info(f"Szöglet scan kész, {sent} új tipp elküldve")


async def check_results(bot: Bot):
    results = await asyncio.to_thread(_sync_collect_results)
    if not CORNERS_CHAT_ID:
        return
    for t, actual, result in results:
        msg = format_result_msg(t, actual, result)
        await _send_to_all(bot, msg)


# ─────────────────────────────────────────────
#  PARANCSOK
# ─────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = load_corner_tips()
    settled = [t for t in tips if t.get("result") is not None]
    wins = [t for t in settled if t.get("result") == "win"]
    pending = [t for t in tips if t.get("result") is None]

    win_rate = round(len(wins) / len(settled) * 100) if settled else 0

    text = (
        "✅ *Szöglet Bot fut!*\n\n"
        f"📊 *Mai statisztika:*\n"
        f"• Összes tipp: {len(tips)}\n"
        f"• Lezárt: {len(settled)}\n"
        f"• Győzelem: {len(wins)}\n"
        f"• Találati arány: {win_rate}%\n"
        f"• Függőben: {len(pending)}\n\n"
        f"⚙️ *Beállítások:*\n"
        f"• Vonal: {CORNER_LINE}\n"
        f"• Over küszöb: {OVER_THRESHOLD}\n"
        f"• Under küszöb: {UNDER_THRESHOLD}\n"
        f"• Min. szorzó: {MIN_CORNER_ODDS}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_lezar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manuálisan lezárja a függőben lévő szöglet tippeket."""
    tips = load_corner_tips()
    pending = [t for t in tips if t.get("result") is None]
    if not pending:
        await update.message.reply_text("Nincs függőben lévő szöglet tipp.")
        return
    for t in pending:
        eid = t["event_id"]
        tip_dir = "Over" if t.get("tip") == "over" else "Under"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Nyert", callback_data=f"clezar_win_{eid}"),
                InlineKeyboardButton("❌ Veszett", callback_data=f"clezar_loss_{eid}"),
            ]
        ])
        await update.message.reply_text(
            f"⚽ *{t['home']} vs {t['away']}*\n"
            f"🏆 {t.get('league', '?')}\n"
            f"🎯 Tipp: *{tip_dir} {t.get('line', 9.5)}* szöglet",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )


async def callback_clezar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline gomb lekezelése szöglet manuális lezáráshoz."""
    query = update.callback_query
    await query.answer()
    data = query.data  # pl. clezar_win_12345
    parts = data.split("_", 2)
    if len(parts) != 3:
        return
    _, action, event_id_str = parts
    try:
        event_id = int(event_id_str)
    except ValueError:
        return

    tips = load_corner_tips()
    tip = next((t for t in tips if str(t["event_id"]) == str(event_id)), None)
    if not tip:
        await query.edit_message_text("Tipp nem található.")
        return

    result = action  # "win" or "loss"
    # Use a placeholder corner count based on result
    if result == "win":
        actual_corners = int(tip.get("line", 9.5)) + (2 if tip.get("tip") == "over" else -2)
        label = "✅ Nyertként lezárva"
    else:
        actual_corners = int(tip.get("line", 9.5)) + (-2 if tip.get("tip") == "over" else 2)
        label = "❌ Veszettként lezárva"

    update_corner_result(event_id, result, actual_corners)
    tip_dir = "Over" if tip.get("tip") == "over" else "Under"
    await query.edit_message_text(
        f"{label}\n\n"
        f"⚽ {tip['home']} vs {tip['away']}\n"
        f"🎯 Tipp: {tip_dir} {tip.get('line', 9.5)} szöglet",
        parse_mode=ParseMode.MARKDOWN,
    )


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

async def post_init(app: Application):
    init_db()
    bot = app.bot
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scan_and_send, "interval", seconds=1800, args=[bot], next_run_time=datetime.utcnow())
    scheduler.add_job(check_results, "interval", seconds=900, args=[bot], next_run_time=datetime.utcnow() + timedelta(seconds=120))
    scheduler.start()
    logger.info("⚽ Szöglet Bot indul... (SofaScore + parancsok)")


def main():
    app = (
        Application.builder()
        .token(CORNERS_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("start", cmd_status))
    app.add_handler(CommandHandler("help", cmd_status))
    app.add_handler(CommandHandler("lezar", cmd_lezar))
    app.add_handler(CallbackQueryHandler(callback_clezar, pattern=r"^clezar_"))

    app.run_polling()


if __name__ == "__main__":
    main()
