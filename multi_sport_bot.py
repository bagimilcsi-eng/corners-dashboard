from __future__ import annotations
import os
import sys
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

MULTI_SPORT_BOT_TOKEN = os.environ["MULTI_SPORT_BOT_TOKEN"]
MULTI_SPORT_CHAT_ID   = os.environ["MULTI_SPORT_CHAT_ID"]
DATABASE_URL          = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL", "")

SOFASCORE_BASE = (
    "https://www.sofascore.com/api/v1"
    if os.environ.get("REPL_ID")
    else "https://814dfd73-d8dd-4560-ab7a-2dea4ca2da33-00-3j0ryo8vfet2i.janeway.replit.dev/api/sofa"
)
SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ── Sport konfigurációk ────────────────────────────────────────────────────────
SPORT_CONFIGS = {
    "ice-hockey": {
        "label":       "Jégkorong",
        "emoji":       "🏒",
        "sofa_slug":   "ice-hockey",
        "min_line":    3.5,
        "max_line":    12.0,
        "min_expected": 3.0,
        "use_exact_poisson": True,   # kis számok → egzakt Poisson
        "ot_keys":     ["overtime", "period4", "period5"],
        "line_kw":     ["total", "over/under", "goals", "puck"],
        "referer":     "https://www.sofascore.com/ice-hockey/livescore",
    },
    "handball": {
        "label":       "Kézilabda",
        "emoji":       "🤾",
        "sofa_slug":   "handball",
        "min_line":    40.0,
        "max_line":    90.0,
        "min_expected": 35.0,
        "use_exact_poisson": False,
        "ot_keys":     ["overtime"],
        "line_kw":     ["total", "over/under", "goals"],
        "referer":     "https://www.sofascore.com/handball/livescore",
    },
    "volleyball": {
        "label":       "Röplabda",
        "emoji":       "🏐",
        "sofa_slug":   "volleyball",
        "min_line":    150.0,
        "max_line":    320.0,
        "min_expected": 140.0,
        "use_exact_poisson": False,
        "ot_keys":     [],
        "line_kw":     ["total", "over/under", "points", "maps"],
        "referer":     "https://www.sofascore.com/volleyball/livescore",
    },
}

# ── Általános beállítások ─────────────────────────────────────────────────────
MIN_CONFIDENCE   = 78
MIN_EDGE         = 2.5    # hoki: gólokban; kézilabda: gólokban; röplabda: pontokban
MIN_PROB         = 0.56
MIN_ODDS         = 1.75
MAX_ODDS         = 2.15
MIN_LAST_MATCHES = 5
SCAN_HOURS_AHEAD = 24
RESULT_DELAY_MIN = 120
API_DELAY_SEC    = 0.5
REQUIRE_BOOKMAKER = True
REQUIRE_H2H      = False   # H2H nem kötelező (kevesebb adat van)

_cache: dict = {}

# ── Adatbázis ──────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    sql = """
    CREATE TABLE IF NOT EXISTS multi_sport_tips (
        event_id         BIGINT PRIMARY KEY,
        sport            TEXT NOT NULL,
        home             TEXT NOT NULL,
        away             TEXT NOT NULL,
        league           TEXT NOT NULL,
        league_id        INTEGER,
        start_timestamp  BIGINT NOT NULL,
        tip              TEXT NOT NULL,
        line             REAL NOT NULL,
        expected_total   REAL NOT NULL,
        home_avg_scored  REAL,
        away_avg_scored  REAL,
        home_avg_conceded REAL,
        away_avg_conceded REAL,
        odds             REAL,
        sent_at          BIGINT NOT NULL,
        result           TEXT DEFAULT NULL,
        actual_total     INTEGER DEFAULT NULL,
        confidence_score INTEGER DEFAULT NULL
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    logger.info("multi_sport_tips tábla inicializálva")


def get_sent_event_ids() -> set:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT event_id FROM multi_sport_tips")
                return {str(r[0]) for r in cur.fetchall()}
    except Exception as e:
        logger.error(f"DB hiba (sent ids): {e}")
        return set()


def save_tip(tip: dict):
    sql = """
    INSERT INTO multi_sport_tips
        (event_id, sport, home, away, league, league_id, start_timestamp, tip, line,
         expected_total, home_avg_scored, away_avg_scored, home_avg_conceded,
         away_avg_conceded, odds, sent_at, confidence_score)
    VALUES (%(event_id)s, %(sport)s, %(home)s, %(away)s, %(league)s, %(league_id)s,
            %(start_timestamp)s, %(tip)s, %(line)s, %(expected_total)s,
            %(home_avg_scored)s, %(away_avg_scored)s, %(home_avg_conceded)s,
            %(away_avg_conceded)s, %(odds)s, %(sent_at)s, %(confidence_score)s)
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
    sql = "UPDATE multi_sport_tips SET result=%s, actual_total=%s WHERE event_id=%s"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (result, actual_total, event_id))
            conn.commit()
        logger.info(f"Eredmény frissítve: {event_id} → {result} ({actual_total})")
    except Exception as e:
        logger.error(f"Eredmény frissítési hiba: {e}")


def load_pending_tips() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM multi_sport_tips WHERE result IS NULL ORDER BY start_timestamp"
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"DB hiba (pending): {e}")
        return []


# ── SofaScore API ──────────────────────────────────────────────────────────────

def sofa_get(url: str, referer: str = "") -> dict:
    try:
        time.sleep(API_DELAY_SEC)
        headers = dict(SOFASCORE_HEADERS)
        if referer:
            headers["Referer"] = referer
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug(f"SofaScore hiba ({url}): {e}")
        return {}


def fetch_sofa_events(date_str: str, sport_slug: str, referer: str) -> list:
    data = sofa_get(
        f"{SOFASCORE_BASE}/sport/{sport_slug}/scheduled-events/{date_str}",
        referer=referer
    )
    return data.get("events", [])


def fetch_team_last_matches(team_id: int, referer: str, last_n: int = 10) -> list:
    cache_key = f"last_{team_id}"
    if cache_key in _cache:
        return _cache[cache_key]
    data = sofa_get(f"{SOFASCORE_BASE}/team/{team_id}/events/last/0", referer=referer)
    events = [e for e in data.get("events", [])
              if e.get("status", {}).get("type") == "finished"][:last_n]
    _cache[cache_key] = events
    return events


def fetch_event_score(event_id: int) -> tuple[int | None, int | None]:
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


def _parse_sofa_odds(val) -> float | None:
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


def fetch_sofascore_totals_line(event_id: int, cfg: dict) -> dict | None:
    try:
        url = f"{SOFASCORE_BASE}/event/{event_id}/odds/1/all"
        r = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if r.status_code != 200:
            logger.info(f"SofaScore odds HTTP {r.status_code} (event={event_id})")
            return None
        data = r.json()
        if data.get("error"):
            return None
        markets = data.get("markets") or []
        for mkt in markets:
            name = (mkt.get("marketName") or mkt.get("name") or "").lower()
            if not any(kw in name for kw in cfg["line_kw"]):
                continue
            choices = mkt.get("choices") or []
            ov = next((c for c in choices
                       if (c.get("name") or "").lower().startswith("over")), None)
            un = next((c for c in choices
                       if (c.get("name") or "").lower().startswith("under")), None)
            if not ov:
                continue
            raw_pt = (mkt.get("choiceGroup") or mkt.get("handicap")
                      or ov.get("handicap") or "")
            try:
                line = float(str(raw_pt))
            except Exception:
                continue
            if not (cfg["min_line"] <= line <= cfg["max_line"]):
                continue
            over_odds  = _parse_sofa_odds(ov.get("fractionalValue"))
            under_odds = _parse_sofa_odds(un.get("fractionalValue")) if un else None
            logger.info(f"SofaScore vonal: {line} | over={over_odds}, under={under_odds} (event={event_id})")
            return {"line": line, "over": over_odds, "under": under_odds}
    except Exception as e:
        logger.debug(f"SofaScore odds hiba (event={event_id}): {e}")
    return None


# ── Statisztikák ───────────────────────────────────────────────────────────────

def is_overtime_match(match: dict, ot_keys: list) -> bool:
    for side in ("homeScore", "awayScore"):
        s = match.get(side, {})
        for key in ot_keys:
            if s.get(key) is not None:
                return True
    return False


def calc_team_stats(team_id: int, is_home: bool, ot_keys: list, referer: str) -> dict | None:
    matches = fetch_team_last_matches(team_id, referer=referer, last_n=12)
    if len(matches) < MIN_LAST_MATCHES:
        return None

    regular = [m for m in matches if not is_overtime_match(m, ot_keys)]

    ha_matches = [
        m for m in regular
        if (is_home and m.get("homeTeam", {}).get("id") == team_id)
        or (not is_home and m.get("awayTeam", {}).get("id") == team_id)
    ]
    if len(ha_matches) < MIN_LAST_MATCHES:
        ha_matches = regular
    if len(ha_matches) < MIN_LAST_MATCHES:
        return None

    scored_list, conceded_list, total_list = [], [], []

    for m in ha_matches[:10]:
        hs  = m.get("homeScore", {}).get("current")
        as_ = m.get("awayScore", {}).get("current")
        if hs is None or as_ is None:
            continue
        if m.get("homeTeam", {}).get("id") == team_id:
            scored_list.append(hs); conceded_list.append(as_)
        else:
            scored_list.append(as_); conceded_list.append(hs)
        total_list.append(hs + as_)

    if len(scored_list) < MIN_LAST_MATCHES:
        return None

    avg_scored    = sum(scored_list)   / len(scored_list)
    avg_conceded  = sum(conceded_list) / len(conceded_list)
    avg_total     = sum(total_list)    / len(total_list)
    last5_total   = sum(total_list[-5:]) / 5 if len(total_list) >= 5 else avg_total

    return {
        "avg_scored":   round(avg_scored, 2),
        "avg_conceded": round(avg_conceded, 2),
        "avg_total":    round(avg_total, 2),
        "last5_total":  round(last5_total, 2),
        "n":            len(scored_list),
    }


def calc_expected_total(home_stats: dict, away_stats: dict) -> float:
    league_avg = (home_stats["avg_scored"] + away_stats["avg_scored"]) / 2
    if league_avg == 0:
        league_avg = 1.0

    home_att = home_stats["avg_scored"]   / league_avg
    home_def = home_stats["avg_conceded"] / league_avg
    away_att = away_stats["avg_scored"]   / league_avg
    away_def = away_stats["avg_conceded"] / league_avg

    home_expected = league_avg * home_att * away_def
    away_expected = league_avg * away_att * home_def
    season_total  = home_expected + away_expected

    form_total = (home_stats["last5_total"] + away_stats["last5_total"]) / 2
    blended    = season_total * 0.60 + form_total * 0.40

    return round(blended, 2)


def calc_h2h_avg(event_id: int) -> float | None:
    h2h = fetch_h2h(event_id)
    if len(h2h) < 3:
        return None
    totals = []
    for m in h2h[:8]:
        hs  = m.get("homeScore", {}).get("current")
        as_ = m.get("awayScore", {}).get("current")
        if hs is not None and as_ is not None:
            totals.append(hs + as_)
    return round(sum(totals) / len(totals), 2) if len(totals) >= 3 else None


# ── Poisson modell ─────────────────────────────────────────────────────────────

def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 0.0
    try:
        return math.exp(-lam) * (lam ** k) / math.factorial(k)
    except Exception:
        return 0.0


def poisson_over_prob(expected: float, line: float, use_exact: bool) -> float:
    if expected <= 0:
        return 0.0
    if use_exact:
        # Egzakt Poisson CDF (hoki kis számokhoz)
        k = int(math.floor(line))
        prob_leq = sum(poisson_pmf(i, expected) for i in range(k + 1))
        return max(0.0, min(1.0, round(1.0 - prob_leq, 4)))
    else:
        # Normál közelítés (kézilabda, röplabda)
        z = (line + 0.5 - expected) / math.sqrt(max(expected, 1.0))
        return round(0.5 * math.erfc(z / math.sqrt(2)), 4)


def calc_confidence(
    expected: float,
    line: float,
    direction: str,
    prob: float,
    odds: float | None,
    h2h_avg: float | None,
) -> int:
    score = 0

    # 1. Valószínűség (40 pont max)
    score += min(40, int(prob * 50))

    # 2. Edge (20 pont max) — arányos az elvárható tartományhoz
    edge = abs(expected - line)
    rel_edge = edge / max(line, 1.0)  # relatív edge
    score += min(20, int(rel_edge * 100))

    # 3. H2H egyezés (15 pont max)
    if h2h_avg is not None:
        h2h_over = h2h_avg > line
        if (direction == "over") == h2h_over:
            score += 15
        else:
            score -= 5

    # 4. Szorzó minősége (15 pont max)
    if odds:
        if MIN_ODDS <= odds <= MAX_ODDS:
            score += 15
        elif odds > MAX_ODDS:
            score += 5

    # 5. Alap (ha nincs H2H)
    if h2h_avg is None:
        score += 5

    return max(0, min(100, score))


# ── Fő elemzés ─────────────────────────────────────────────────────────────────

def analyze_event(event: dict, sport_key: str, cfg: dict, sent_ids: set) -> dict | None:
    event_id = str(event.get("id", ""))
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

    home    = event.get("homeTeam", {}).get("name", "")
    away    = event.get("awayTeam", {}).get("name", "")
    home_id = event.get("homeTeam", {}).get("id")
    away_id = event.get("awayTeam", {}).get("id")
    league  = event.get("tournament", {}).get("name", "Ismeretlen")
    league_id = event.get("tournament", {}).get("id")

    if not home_id or not away_id:
        return None

    referer = cfg["referer"]
    home_stats = calc_team_stats(home_id, is_home=True,  ot_keys=cfg["ot_keys"], referer=referer)
    away_stats = calc_team_stats(away_id, is_home=False, ot_keys=cfg["ot_keys"], referer=referer)

    if not home_stats or not away_stats:
        logger.info(f"Nincs elég stat: {home} vs {away} [{cfg['label']}]")
        return None

    expected = calc_expected_total(home_stats, away_stats)

    if expected < cfg["min_expected"]:
        logger.info(f"Túl alacsony várható összeg ({expected}): {home} vs {away}")
        return None

    sofa_odds = fetch_sofascore_totals_line(int(event_id), cfg)
    if sofa_odds:
        line         = sofa_odds["line"]
        best_over    = sofa_odds.get("over")
        best_under   = sofa_odds.get("under")
        n_bookmakers = 1
        line_source  = "Bookmaker"
    else:
        if REQUIRE_BOOKMAKER:
            logger.info(f"Nincs bookmaker vonal, kizárva: {home} vs {away}")
            return None
        avg_total    = (home_stats["avg_total"] + away_stats["avg_total"]) / 2
        line         = round(avg_total * 2) / 2
        best_over    = None
        best_under   = None
        n_bookmakers = 0
        line_source  = "Becsült"
        logger.info(f"Becsült vonal: {line} | {home} vs {away}")

    if not (cfg["min_line"] <= line <= cfg["max_line"]):
        logger.info(f"Vonal tartományon kívül ({line}): {home} vs {away}")
        return None

    h2h_avg = calc_h2h_avg(int(event_id))

    use_exact = cfg["use_exact_poisson"]
    prob_over  = poisson_over_prob(expected, line, use_exact)
    prob_under = 1.0 - prob_over

    edge = round(expected - line, 2)

    if edge >= MIN_EDGE and prob_over >= MIN_PROB:
        direction = "over"
        prob      = prob_over
        odds      = best_over
    elif edge <= -MIN_EDGE and prob_under >= MIN_PROB:
        direction = "under"
        prob      = prob_under
        odds      = best_under
    else:
        logger.info(f"Nincs edge: {home} vs {away} | várható={expected}, vonal={line}, edge={edge:+.2f}")
        return None

    confidence = calc_confidence(expected, line, direction, prob, odds, h2h_avg)

    if confidence < MIN_CONFIDENCE:
        logger.info(f"Alacsony konfidencia ({confidence}): {home} vs {away} [{cfg['label']}]")
        return None

    tip_label = f"{'OVER' if direction == 'over' else 'UNDER'} {line}"

    return {
        "event_id":         int(event_id),
        "sport":            sport_key,
        "home":             home,
        "away":             away,
        "league":           league,
        "league_id":        league_id,
        "start_timestamp":  start_ts,
        "tip":              tip_label,
        "line":             line,
        "expected_total":   expected,
        "home_avg_scored":  home_stats["avg_scored"],
        "away_avg_scored":  away_stats["avg_scored"],
        "home_avg_conceded": home_stats["avg_conceded"],
        "away_avg_conceded": away_stats["avg_conceded"],
        "odds":             odds,
        "sent_at":          now_ts,
        "confidence_score": confidence,
        "result":           None,
        "actual_total":     None,
        # extra (nem megy DB-be)
        "prob":             prob,
        "h2h_avg":          h2h_avg,
        "direction":        direction,
        "best_over":        best_over,
        "best_under":       best_under,
        "line_source":      line_source,
        "cfg":              cfg,
    }


# ── Telegram üzenet ────────────────────────────────────────────────────────────

def confidence_stars(score: int) -> str:
    if score >= 88:
        return "⭐⭐⭐"
    elif score >= 80:
        return "⭐⭐"
    return "⭐"


def format_tip_message(t: dict) -> str:
    cfg   = t["cfg"]
    emoji = cfg["emoji"]
    label = cfg["label"]

    dt_local = datetime.utcfromtimestamp(t["start_timestamp"]) + timedelta(hours=2)
    dt_str   = dt_local.strftime("%Y.%m.%d %H:%M")

    direction = t["direction"]
    dir_emoji = "📈" if direction == "over" else "📉"
    stars     = confidence_stars(t["confidence_score"])

    odds_str = f"{t['odds']:.2f}" if t.get("odds") else "—"

    h2h_str = f"{t['h2h_avg']:.1f}" if t.get("h2h_avg") else "—"

    lines = [
        f"{emoji} <b>{label} Over/Under Tipp</b> {stars}",
        f"",
        f"🏟 <b>{t['home']} vs {t['away']}</b>",
        f"🏆 {t['league']}",
        f"🕐 {dt_str}",
        f"",
        f"{dir_emoji} <b>{'OVER' if direction == 'over' else 'UNDER'} {t['line']}</b>",
        f"📊 Várható összeg: <b>{t['expected_total']:.1f}</b>",
        f"📉 H2H átlag: {h2h_str}",
        f"🎯 Valószínűség: {t['prob']*100:.1f}%",
        f"💰 Szorzó: {odds_str}",
        f"🔒 Konfidencia: {t['confidence_score']}%",
        f"",
        f"📋 Vonal forrása: {t['line_source']}",
        f"📈 Hazai átlag: {t['home_avg_scored']:.1f} pont | Vendég: {t['away_avg_scored']:.1f} pont",
    ]
    return "\n".join(lines)


def format_result_message(t: dict, result: str, actual_total: int) -> str:
    sport_cfg = SPORT_CONFIGS.get(t.get("sport", ""), {})
    emoji = sport_cfg.get("emoji", "🏅")
    icon  = "✅" if result == "won" else "❌"
    return (
        f"{icon} <b>Eredmény — {sport_cfg.get('label', '')}</b>\n"
        f"{t['home']} vs {t['away']}\n"
        f"Tipp: {t['tip']} | Tényleges: {actual_total}\n"
        f"{'Nyert 🎉' if result == 'won' else 'Veszett 😔'}"
    )


# ── Scan & Send ────────────────────────────────────────────────────────────────

async def scan_and_send(app: Application):
    _cache.clear()
    bot = app.bot
    sent_ids = get_sent_event_ids()
    new_tips = 0

    today     = date.today()
    tomorrow  = today + timedelta(days=1)
    dates     = [today.strftime("%Y-%m-%d"), tomorrow.strftime("%Y-%m-%d")]

    for sport_key, cfg in SPORT_CONFIGS.items():
        logger.info(f"Scan indul: {cfg['label']} ({sport_key})")
        events = []
        for d in dates:
            events.extend(fetch_sofa_events(d, cfg["sofa_slug"], cfg["referer"]))

        logger.info(f"{cfg['label']}: {len(events)} mérkőzés találva")

        for event in events:
            tip = analyze_event(event, sport_key, cfg, sent_ids)
            if not tip:
                continue

            msg = format_tip_message(tip)
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Nyert", callback_data=f"msp_won_{tip['event_id']}"),
                InlineKeyboardButton("❌ Veszett", callback_data=f"msp_lost_{tip['event_id']}"),
            ]])
            try:
                await bot.send_message(
                    chat_id=MULTI_SPORT_CHAT_ID,
                    text=msg,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
                save_tip({k: v for k, v in tip.items()
                          if k not in ("prob", "h2h_avg", "direction", "best_over",
                                       "best_under", "line_source", "cfg")})
                sent_ids.add(str(tip["event_id"]))
                new_tips += 1
                logger.info(f"Tipp elküldve: {tip['home']} vs {tip['away']} | {tip['tip']}")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Telegram küldési hiba: {e}")

    logger.info(f"Scan kész, {new_tips} új tipp elküldve")


async def check_results(app: Application):
    logger.info("Eredmény ellenőrzés indul...")
    bot = app.bot
    pending = load_pending_tips()
    now_ts  = int(time.time())

    for tip in pending:
        start_ts = tip.get("start_timestamp", 0)
        if now_ts < start_ts + RESULT_DELAY_MIN * 60:
            continue

        hs, as_ = fetch_event_score(tip["event_id"])
        if hs is None or as_ is None:
            continue

        actual_total = hs + as_
        tip_str = tip.get("tip", "")
        if "OVER" in tip_str:
            line   = tip["line"]
            result = "won" if actual_total > line else "lost"
        elif "UNDER" in tip_str:
            line   = tip["line"]
            result = "won" if actual_total < line else "lost"
        else:
            continue

        update_result(tip["event_id"], result, actual_total)
        msg = format_result_message(tip, result, actual_total)
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
        "🏒🤾🏐 Multi-Sport Over/Under Bot\n\n"
        "/scan — Azonnali scan\n"
        "/statisztika — Összesített statisztika"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Scan indul...")
    await scan_and_send(context.application)
    await update.message.reply_text("✅ Scan kész.")


async def cmd_statisztika(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT sport,
                           COUNT(*) AS total,
                           SUM(CASE WHEN result='won'  THEN 1 ELSE 0 END) AS won,
                           SUM(CASE WHEN result='lost' THEN 1 ELSE 0 END) AS lost,
                           AVG(odds) AS avg_odds
                    FROM multi_sport_tips
                    GROUP BY sport
                    ORDER BY total DESC
                """)
                rows = cur.fetchall()

        if not rows:
            await update.message.reply_text("Még nincs adat.")
            return

        lines = ["📊 <b>Multi-Sport Statisztika</b>\n"]
        total_all, won_all = 0, 0
        for sport, total, won, lost, avg_odds in rows:
            cfg = SPORT_CONFIGS.get(sport, {})
            emoji = cfg.get("emoji", "🏅")
            label = cfg.get("label", sport)
            pending = total - (won or 0) - (lost or 0)
            wr = f"{won/(won+lost)*100:.1f}%" if (won or 0) + (lost or 0) > 0 else "—"
            lines.append(
                f"{emoji} <b>{label}</b>\n"
                f"  Összes: {total} | Nyert: {won or 0} | Veszett: {lost or 0} | Függőben: {pending}\n"
                f"  Win rate: {wr} | Átl. szorzó: {avg_odds:.2f if avg_odds else '—'}\n"
            )
            total_all += total
            won_all   += (won or 0)

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Statisztika hiba: {e}")
        await update.message.reply_text("Hiba a statisztika lekérésekor.")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("msp_"):
        parts = data.split("_")
        if len(parts) < 3:
            return
        result_str = parts[1]
        event_id   = int(parts[2])

        try:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM multi_sport_tips WHERE event_id=%s", (event_id,))
                    tip = cur.fetchone()
            if not tip:
                await query.edit_message_text("Nem találom a tippet.")
                return

            result = "won" if result_str == "won" else "lost"
            update_result(event_id, result, tip.get("actual_total") or 0)
            icon = "✅" if result == "won" else "❌"
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(
                f"{icon} Kézzel rögzítve: {tip['home']} vs {tip['away']} → {'Nyert' if result=='won' else 'Veszett'}"
            )
        except Exception as e:
            logger.error(f"Callback hiba: {e}")


# ── Main ────────────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    init_db()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scan_and_send, "interval", hours=1,
        args=[app], id="scan_and_send",
        next_run_time=datetime.now() + timedelta(minutes=60)
    )
    scheduler.add_job(
        check_results, "interval", minutes=20,
        args=[app], id="check_results",
        next_run_time=datetime.now() + timedelta(minutes=20)
    )
    scheduler.start()
    app.bot_data["scheduler"] = scheduler

    logger.info("Multi-Sport bot inicializálva, startup scan indul...")
    await scan_and_send(app)


def main():
    app = (
        Application.builder()
        .token(MULTI_SPORT_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("scan",        cmd_scan))
    app.add_handler(CommandHandler("statisztika", cmd_statisztika))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Multi-Sport Bot elindul (hoki 🏒 | kézilabda 🤾 | röplabda 🏐)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
