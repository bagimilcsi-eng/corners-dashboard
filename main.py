import os
import sys
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

if os.environ.get("TT_BOT_DISABLED", "").lower() in ("1", "true", "yes"):
    print("TT_BOT_DISABLED=true — bot nem indul el ezen a környezeten.")
    sys.exit(0)

HU_TZ = ZoneInfo("Europe/Budapest")
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PROD_API_URL = os.environ.get("PROD_API_URL", "").rstrip("/")
REPLIT_DB_URL = os.environ.get("REPLIT_DB_URL", "")

# Összes chat ID ahova az automatikus tippek mennek
TELEGRAM_CHAT_IDS = [6617439213, -1003802326194, -1003835559510]

SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sofascore.com/",
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

FOOTBALL_API_BASE = "https://api-football-v1.p.rapidapi.com/v3"
FOOTBALL_API_HEADERS = {
    "x-rapidapi-key": os.environ.get("SPORTS_API_KEY", ""),
    "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
}

ALLOWED_KEYWORDS = ["setka", "czech"]


# ─────────────────────────────────────────────
#  SOFASCORE – ASZTALITENISZ
# ─────────────────────────────────────────────


def sofascore_fetch_events(target_date: str) -> list:
    """Asztalitenisz meccsek lekérése SofaScore-ból adott dátumra."""
    url = f"https://www.sofascore.com/api/v1/sport/table-tennis/scheduled-events/{target_date}"
    try:
        resp = requests.get(url, headers=SOFASCORE_HEADERS, timeout=12)
        resp.raise_for_status()
        return resp.json().get("events", [])
    except Exception as e:
        logger.error(f"SofaScore fetch hiba ({target_date}): {e}")
        return []


def sofascore_fetch_h2h(event_id: int, home_name: str = "", away_name: str = "") -> tuple[int, int]:
    """
    H2H utolsó 5 meccs lekérése.
    Visszaad: (home_wins, 5) ha van elég adat, (0, 0) ha nincs minimum 5 H2H meccs.
    """
    url = f"https://www.sofascore.com/api/v1/event/{event_id}/h2h"
    try:
        resp = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if resp.status_code != 200:
            return 0, 0
        data = resp.json()

        # Egyedi H2H meccsek listája
        events = data.get("events", [])
        finished = [e for e in events if e.get("status", {}).get("type", "").lower() == "finished"]

        if len(finished) >= 5 and home_name:
            last5 = finished[:5]
            home_wins = 0
            for e in last5:
                h = e.get("homeTeam", {}).get("name", "")
                a = e.get("awayTeam", {}).get("name", "")
                hs = e.get("homeScore", {}).get("current", 0) or 0
                as_ = e.get("awayScore", {}).get("current", 0) or 0
                if home_name.lower() in h.lower() and hs > as_:
                    home_wins += 1
                elif home_name.lower() in a.lower() and as_ > hs:
                    home_wins += 1
            return home_wins, 5

        # Fallback: teamDuel összesítő
        duel = data.get("teamDuel")
        if not duel:
            return 0, 0
        home_wins = duel.get("homeWins", 0)
        away_wins = duel.get("awayWins", 0)
        draws = duel.get("draws", 0)
        total = home_wins + away_wins + draws
        if total < 5:
            return 0, 0
        return home_wins, total
    except Exception as e:
        logger.error(f"H2H fetch hiba: {e}")
        return 0, 0


def _parse_choice_odd(c: dict) -> float | None:
    """Kinyeri a decimal szorzót egy choice objektumból (törtszám vagy decimal mező)."""
    # 1. decimalValue mező (pl. asztalitenisz)
    dv = c.get("decimalValue")
    if dv:
        try:
            v = float(dv)
            if v > 1.0:
                return round(v, 3)
        except Exception:
            pass
    # 2. fractionalValue mező (pl. "8/11")
    frac = c.get("fractionalValue", "")
    return fractional_to_decimal(frac)


def sofascore_fetch_odds(event_id: int) -> dict | None:
    """Szorzók lekérése egy meccshez. Visszaad: {'home': float, 'away': float} vagy None."""
    url = f"https://www.sofascore.com/api/v1/event/{event_id}/odds/1/all"
    try:
        resp = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if resp.status_code != 200:
            return None
        markets = resp.json().get("markets", [])
        for market in markets:
            if market.get("marketName") == "Full time":
                choices = market.get("choices", [])
                odds_map = {}
                for c in choices:
                    name = c.get("name", "")
                    odd = _parse_choice_odd(c)
                    if odd:
                        odds_map[name] = odd
                # Elsőként "1"/"2" nevek, fallback: első két choice sorrendben
                if "1" in odds_map and "2" in odds_map:
                    return {"home": odds_map["1"], "away": odds_map["2"]}
                ordered = [v for v in odds_map.values()]
                if len(ordered) >= 2:
                    return {"home": ordered[0], "away": ordered[1]}
        return None
    except Exception as e:
        logger.error(f"Odds fetch hiba: {e}")
        return None


def fractional_to_decimal(frac: str) -> float | None:
    """Törtszám odds konvertálása decimálisba. Pl. '8/11' → 1.727"""
    try:
        if not frac:
            return None
        if "/" in frac:
            num, den = frac.split("/")
            return round(int(num) / int(den) + 1, 3)
        v = float(frac)
        # Ha már decimal (>1), visszadjuk ahogy van
        return round(v, 3) if v > 1.0 else round(v + 1, 3)
    except Exception:
        return None


def is_allowed(event: dict) -> bool:
    """Setka Cup és Czech Liga meccsek — mindkét helyen van H2H adat a SofaScore-ban."""
    t    = event.get("tournament", {})
    text = (t.get("name", "") + " " + t.get("category", {}).get("name", "")).lower()
    return any(kw in text for kw in ALLOWED_KEYWORDS)


def get_status_hu(event: dict) -> str:
    """Magyar státusz visszaadása."""
    status_type = event.get("status", {}).get("type", "")
    status_desc = event.get("status", {}).get("description", "")
    mapping = {
        "notstarted": "Nem kezdődött",
        "inprogress": "Folyamatban",
        "finished": "Befejezett",
        "postponed": "Elhalasztva",
        "canceled": "Törölve",
    }
    return mapping.get(status_type.lower(), status_desc or status_type)


def format_score(event: dict) -> str:
    """Pontozás formázása (szetek)."""
    hs = event.get("homeScore", {})
    as_ = event.get("awayScore", {})
    hc = hs.get("current")
    ac = as_.get("current")
    if hc is not None and ac is not None:
        return f"*{hc}–{ac}*"
    return ""


def format_event_line(event: dict) -> str:
    """Egy meccs egyszerű sorba formázva."""
    home = event.get("homeTeam", {}).get("name", "?")
    away = event.get("awayTeam", {}).get("name", "?")
    score = format_score(event)
    league = event.get("tournament", {}).get("name", "")
    ts = event.get("startTimestamp")
    time_str = ""
    if ts:
        dt = datetime.fromtimestamp(ts, tz=HU_TZ)
        time_str = f" [{dt.strftime('%H:%M')}]"
    score_part = f" {score}" if score else ""
    return f"🏓 {home} vs {away}{score_part}{time_str} — _{league}_"


# ─────────────────────────────────────────────
#  TIPP ELEMZŐ LOGIKA
# ─────────────────────────────────────────────


def get_player_recent_form(
    player_name: str, all_events: list, last: int = 10
) -> tuple[int, int]:
    """Játékos legutóbbi formájának kiszámítása a mai adatokból."""
    wins = 0
    total = 0
    for e in all_events:
        if e.get("status", {}).get("type", "").lower() != "finished":
            continue
        home = e.get("homeTeam", {}).get("name", "")
        away = e.get("awayTeam", {}).get("name", "")
        if (
            player_name.lower() not in home.lower()
            and player_name.lower() not in away.lower()
        ):
            continue
        hs = e.get("homeScore", {}).get("current") or 0
        as_ = e.get("awayScore", {}).get("current") or 0
        total += 1
        if player_name.lower() in home.lower() and hs > as_:
            wins += 1
        elif player_name.lower() in away.lower() and as_ > hs:
            wins += 1
        if total >= last:
            break
    return wins, total


def form_bar(wins: int, total: int) -> str:
    if total == 0:
        return "–"
    filled = round((wins / total) * 5)
    return "●" * filled + "○" * (5 - filled)


def get_player_first_set_rate(
    player_name: str, all_events: list, last: int = 10
) -> tuple[int, int]:
    """Játékos első szett megnyerési aránya a napi meccsek alapján (fallback)."""
    wins = 0
    total = 0
    for e in all_events:
        if e.get("status", {}).get("type", "").lower() != "finished":
            continue
        home = e.get("homeTeam", {}).get("name", "")
        away = e.get("awayTeam", {}).get("name", "")
        if (
            player_name.lower() not in home.lower()
            and player_name.lower() not in away.lower()
        ):
            continue
        hs1 = e.get("homeScore", {}).get("period1")
        as1 = e.get("awayScore", {}).get("period1")
        if hs1 is None or as1 is None:
            continue
        is_home = player_name.lower() in home.lower()
        total += 1
        if is_home and hs1 > as1:
            wins += 1
        elif not is_home and as1 > hs1:
            wins += 1
        if total >= last:
            break
    return wins, total


def sofascore_fetch_player_stats(
    team_id: int, last: int = 10
) -> tuple[tuple[int, int], tuple[int, int]]:
    """
    Játékos egyéni forma és első szett statisztika lekérése SofaScore-ból.
    Csak az elmúlt 14 nap befejezett meccseit nézi, maximum 'last' darabot.
    Visszaad: ((forma_wins, forma_total), (fs_wins, fs_total))
    """
    cutoff = int(datetime.utcnow().timestamp()) - 14 * 24 * 3600
    url = f"https://www.sofascore.com/api/v1/team/{team_id}/events/last/0"
    try:
        resp = requests.get(url, headers=SOFASCORE_HEADERS, timeout=8)
        if resp.status_code != 200:
            return (0, 0), (0, 0)
        events = resp.json().get("events", [])
        form_w = 0
        form_t = 0
        fs_w = 0
        fs_t = 0
        for e in events:
            if form_t >= last:
                break
            ts = e.get("startTimestamp", 0)
            if ts < cutoff:
                continue
            if e.get("status", {}).get("type", "").lower() != "finished":
                continue
            is_home = e.get("homeTeam", {}).get("id") == team_id
            hs = e.get("homeScore", {}).get("current", 0) or 0
            as_ = e.get("awayScore", {}).get("current", 0) or 0
            # Forma
            form_t += 1
            if is_home and hs > as_:
                form_w += 1
            elif not is_home and as_ > hs:
                form_w += 1
            # Első szett
            hs1 = e.get("homeScore", {}).get("period1")
            as1 = e.get("awayScore", {}).get("period1")
            if hs1 is not None and as1 is not None:
                fs_t += 1
                if is_home and hs1 > as1:
                    fs_w += 1
                elif not is_home and as1 > hs1:
                    fs_w += 1
        return (form_w, form_t), (fs_w, fs_t)
    except Exception as e:
        logger.error(f"Játékos stats fetch hiba (team_id={team_id}): {e}")
        return (0, 0), (0, 0)


MIN_FORM_MATCHES         = 8     # Minimum 8 forma meccs kötelező
MIN_H2H_MATCHES          = 5     # Min. 5 H2H meccs – kötelező
STRONG_THRESHOLD         = 38.0  # Erős tipp küszöb (H2H kötelező, max ~70 pont)
FORMA_ONLY_THRESHOLD     = 22.0  # Küszöb ha nincs H2H adat (forma+1.szett alapú, max ~50 pont)
MIN_H2H_RATE             = 0.72  # H2H győzelmi arány minimum 72%
MIN_FIRST_SET_RATE       = 0.65  # 1. szett győzelmi arány minimum 65% (ha van ≥5 adat)
MIN_FORM_DIFF            = 0.28  # Forma különbség minimum 28 százalékpont


def calculate_tip(
    h2h_home_wins: int,
    h2h_total: int,
    home_form_wins: int,
    home_form_total: int,
    away_form_wins: int,
    away_form_total: int,
    home_fs_wins: int = 0,
    home_fs_total: int = 0,
    away_fs_wins: int = 0,
    away_fs_total: int = 0,
    h2h_required: bool = True,
) -> tuple[str, str, float]:
    """
    Tipp kiszámítása pontozással. Visszaad: (winner, bizalom, score).

    Kötelező feltételek:
    - Min. 8 forma meccs mindkét játékoshoz
    - Ha h2h_required=True: min. 5 H2H meccs kötelező
    - Ha h2h_required=False: H2H hiányában forma+1.szett alapján dönt
    - H2H és forma iránya megegyezik (csak ha H2H elérhető)
    - Pontszám eléri a küszöböt
    - Forma különbség ≥ 28 százalékpont
    - 1. szett arány ≥ 65% (ha van ≥5 adat)
    """
    score = 0.0

    if home_form_total < MIN_FORM_MATCHES or away_form_total < MIN_FORM_MATCHES:
        return "uncertain", "🔴 Kevés forma adat (min. 8)", score

    h2h_available = h2h_total >= MIN_H2H_MATCHES

    if not h2h_available and h2h_required:
        return "uncertain", "🔴 Kevés H2H adat (min. 5)", score

    home_rate = home_form_wins / home_form_total
    away_rate = away_form_wins / away_form_total

    form_score = (home_rate - away_rate) * 30

    home_fs_rate = (home_fs_wins / home_fs_total) if home_fs_total >= 5 else None
    away_fs_rate = (away_fs_wins / away_fs_total) if away_fs_total >= 5 else None
    first_set_score = 0.0
    if home_fs_rate is not None and away_fs_rate is not None:
        first_set_score = (home_fs_rate - away_fs_rate) * 20

    use_h2h = h2h_available and h2h_required

    if use_h2h:
        h2h_rate  = h2h_home_wins / h2h_total
        h2h_score = (h2h_rate - 0.5) * 40
        if (h2h_score > 0) != (form_score > 0):
            return "uncertain", "🔴 Ellentmondó jelek (H2H vs forma)", score
        score = h2h_score + form_score + first_set_score
        threshold = STRONG_THRESHOLD
    else:
        h2h_rate  = None
        h2h_score = 0.0
        score = form_score + first_set_score
        threshold = FORMA_ONLY_THRESHOLD

    if abs(score) < threshold:
        return "uncertain", "🔴 Bizonytalan", score

    if score > 0:
        winner = "home"
        w_form = home_rate
        l_form = away_rate
        w_fs   = home_fs_rate
    else:
        winner = "away"
        w_form = away_rate
        l_form = home_rate
        w_fs   = away_fs_rate

    if use_h2h:
        w_h2h = h2h_rate if winner == "home" else (1.0 - h2h_rate)
        if w_h2h < MIN_H2H_RATE:
            return "uncertain", f"🔴 H2H gyenge ({w_h2h*100:.0f}% < 72%)", score

    if round(w_form - l_form, 6) < MIN_FORM_DIFF:
        return "uncertain", f"🔴 Forma különbség kicsi ({(w_form - l_form)*100:.0f}% < 28%)", score
    if w_fs is not None and w_fs < MIN_FIRST_SET_RATE:
        return "uncertain", f"🔴 1. szett gyenge ({w_fs*100:.0f}% < 65%)", score

    mode = "H2H+forma" if h2h_available else "csak forma"
    return winner, f"🟢 Erős tipp ({mode})", score


MIN_ODDS = 1.55  # Globális minimum szorzó

# Per-liga szűrők — backteszt alapján optimalizálva (176 nap, 1712 tipp)
# TT Cup:        WR=60.3%, ROI=+13.5% (score≥38, odds≥1.70)
# Setka Cup:     WR=54.4%, ROI=+0.6%  (score≥42, odds 1.70-1.90) — 1.90+ katasztrofális
# Czech Liga Pro:WR=64.6%, ROI=+17.1% (score≥50 H2H-val) — H2H nélkül forma alapú, min_score=22
LEAGUE_FILTERS: dict[str, dict] = {
    "TT Cup":         {"min_score": 38, "min_odds": 1.70, "max_odds": 99.0, "h2h_required": True},
    "Setka Cup":      {"min_score": 42, "min_odds": 1.70, "max_odds": 1.90, "h2h_required": True},
    "Czech Liga Pro": {"min_score": 22, "min_odds": 1.55, "max_odds": 99.0, "h2h_required": False},
}
# ─────────────────────────────────────────────
#  ADATBÁZIS – TIPP ELŐZMÉNYEK
# ─────────────────────────────────────────────


def get_db_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


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
    """Menti a tippet. True ha valóban új volt (sikeres INSERT), False ha már létezett."""
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
        if inserted:
            sync_tip_to_prod(record)
            sync_all_tips_to_kv()
        return inserted
    except Exception as e:
        logger.error(f"DB save_tip_record hiba: {e}")
        return False


def fetch_match_result(event_id: int) -> str | None:
    """
    Lekéri a meccs végeredményét.
    Visszaad: 'home', 'away', 'postponed', vagy None ha még nincs vége.
    """
    try:
        r = requests.get(
            f"https://www.sofascore.com/api/v1/event/{event_id}",
            headers=SOFASCORE_HEADERS,
            timeout=8,
        )
        if r.status_code != 200:
            return None
        event = r.json().get("event", {})
        status_type = event.get("status", {}).get("type", "").lower()
        if status_type in ("postponed", "canceled", "cancelled", "abandoned", "interrupted"):
            return "postponed"
        if status_type != "finished":
            return None
        hs = event.get("homeScore", {}).get("current", 0) or 0
        as_ = event.get("awayScore", {}).get("current", 0) or 0
        if hs > as_:
            return "home"
        elif as_ > hs:
            return "away"
        return None
    except Exception:
        return None


def update_tip_result(event_id: int, result: str, actual_winner: str):
    try:
        conn = get_db_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tips SET result=%s, actual_winner=%s WHERE event_id=%s
                """,
                    (result, actual_winner, event_id),
                )
        conn.close()
        sync_result_to_prod(event_id, result, actual_winner)
        sync_all_tips_to_kv()
    except Exception as e:
        logger.error(f"DB update_tip_result hiba: {e}")


def sync_all_tips_to_kv():
    """Elmenti az összes tippet a Replit KV store-ba (dev+production között megosztott)."""
    if not REPLIT_DB_URL:
        return
    try:
        tips = load_tips()
        # Decimalt float-ra konvertáljuk JSON-hoz
        serializable = []
        for t in tips:
            row = dict(t)
            if row.get("odds") is not None:
                row["odds"] = float(row["odds"])
            serializable.append(row)
        payload = json.dumps(serializable, default=str)
        requests.post(REPLIT_DB_URL, data={"tips_data": payload}, timeout=6)
        logger.debug(f"KV sync kész: {len(serializable)} tipp")
    except Exception as e:
        logger.warning(f"KV sync hiba: {e}")


def sync_tip_to_prod(record: dict):
    """Elküldi a tippet a production API-ra ha PROD_API_URL be van állítva."""
    if not PROD_API_URL:
        return
    try:
        requests.post(
            f"{PROD_API_URL}/api/tips",
            json={
                "event_id": record["event_id"],
                "home": record["home"],
                "away": record["away"],
                "league": record["league"],
                "predicted": record["predicted"],
                "predicted_name": record["predicted_name"],
                "odds": record.get("odds"),
                "start_timestamp": record["start_timestamp"],
                "sent_at": record["sent_at"],
            },
            timeout=6,
        )
    except Exception as e:
        logger.warning(f"Prod API tipp sync hiba: {e}")


def sync_result_to_prod(event_id: int, result: str, actual_winner: str):
    """Frissíti az eredményt a production API-n ha PROD_API_URL be van állítva."""
    if not PROD_API_URL:
        return
    try:
        requests.patch(
            f"{PROD_API_URL}/api/tips/{event_id}",
            json={"result": result, "actual_winner": actual_winner},
            timeout=6,
        )
    except Exception as e:
        logger.warning(f"Prod API eredmény sync hiba: {e}")


def resolve_pending_tips(tips: list) -> list:
    """Lekéri a még lezáratlan tippek eredményét és frissíti az adatbázisban."""
    now_ts = int(datetime.utcnow().timestamp())
    for t in tips:
        if t.get("result") is not None:
            continue
        if t.get("start_timestamp", 0) + 45 * 60 > now_ts:
            continue
        actual = fetch_match_result(t["event_id"])
        if actual is not None:
            result = "win" if actual == t["predicted"] else "loss"
            t["actual_winner"] = actual
            t["result"] = result
            update_tip_result(t["event_id"], result, actual)
    return tips


def build_tip_message(
    event: dict, all_events: list
) -> tuple[str | None, float | None, dict | None]:
    """
    Tipp üzenet összeállítása egy meccshez.
    Visszaad: (üzenet vagy None, tippelt szorzó vagy None, tipp-meta dict vagy None)
    """
    home = event.get("homeTeam", {}).get("name", "?")
    away = event.get("awayTeam", {}).get("name", "?")
    home_team_id = event.get("homeTeam", {}).get("id")
    away_team_id = event.get("awayTeam", {}).get("id")
    event_id = event.get("id")
    league = event.get("tournament", {}).get("name", "?")
    category = event.get("tournament", {}).get("category", {}).get("name", "")
    ts = event.get("startTimestamp")
    time_str = datetime.fromtimestamp(ts, tz=HU_TZ).strftime("%H:%M") if ts else "?"

    # Per-liga konfig (h2h_required és score/odds küszöbök)
    lg_cfg = next(
        (cfg for key, cfg in LEAGUE_FILTERS.items() if key.lower() in league.lower()),
        {"min_score": 38, "min_odds": 1.55, "max_odds": 99.0, "h2h_required": True},
    )
    h2h_required = lg_cfg.get("h2h_required", True)

    # Szorzók lekérése
    odds = sofascore_fetch_odds(event_id) if event_id else None

    # H2H – csak ha a liga megköveteli
    h2h_home_wins, h2h_total = 0, 0
    if event_id:
        h2h_home_wins, h2h_total = sofascore_fetch_h2h(event_id, home, away)

    # Forma + első szett – elmúlt 14 nap, utolsó 10 meccs egyénenként
    if home_team_id:
        (home_w, home_t), (home_fs_w, home_fs_t) = sofascore_fetch_player_stats(home_team_id, last=10)
    else:
        home_w, home_t, home_fs_w, home_fs_t = 0, 0, 0, 0

    if away_team_id:
        (away_w, away_t), (away_fs_w, away_fs_t) = sofascore_fetch_player_stats(away_team_id, last=10)
    else:
        away_w, away_t, away_fs_w, away_fs_t = 0, 0, 0, 0

    winner, confidence, score = calculate_tip(
        h2h_home_wins,
        h2h_total,
        home_w,
        home_t,
        away_w,
        away_t,
        home_fs_w,
        home_fs_t,
        away_fs_w,
        away_fs_t,
        h2h_required=h2h_required,
    )

    # Csak Erős tippeket küldünk
    if not confidence.startswith("🟢 Erős tipp"):
        logger.info(f"{home} vs {away} — [{league}] kizárva: {confidence} (score={score:.1f})")
        return None, None, None

    # Per-liga score szűrő
    if abs(score) < lg_cfg["min_score"]:
        logger.info(
            f"{home} vs {away} — [{league}] score szűrő "
            f"({abs(score):.1f} < {lg_cfg['min_score']}), kihagyva"
        )
        return None, None, None

    # Szorzó meghatározása a tippelt oldalhoz
    tip_odds = None
    if odds:
        if winner == "home":
            tip_odds = odds["home"]
        elif winner == "away":
            tip_odds = odds["away"]

    # Ha nincs szorzó, alapértelmezett 1.75 — ligánkénti min. odds felett
    if tip_odds is None:
        tip_odds = 1.75

    # Per-liga odds szűrő (min ÉS max)
    if tip_odds < lg_cfg["min_odds"] or tip_odds > lg_cfg["max_odds"]:
        logger.info(
            f"{home} vs {away} — [{league}] odds szűrő "
            f"({tip_odds:.2f} nem esik {lg_cfg['min_odds']:.2f}–{lg_cfg['max_odds']:.2f} sávba), kihagyva"
        )
        return None, tip_odds, None

    # Tipp meta adat (mentéshez)
    predicted_name = home if winner == "home" else away
    tip_meta = {
        "event_id": event_id,
        "home": home,
        "away": away,
        "league": league,
        "predicted": winner,
        "predicted_name": predicted_name,
        "odds": tip_odds,
        "start_timestamp": ts,
        "sent_at": int(datetime.utcnow().timestamp()),
        "result": None,
        "actual_winner": None,
    }

    # Üzenet összeállítása
    if winner == "home":
        tip_str = f"🏓 *Tipp: {home}* győz"
    else:
        tip_str = f"🏓 *Tipp: {away}* győz"

    h2h_str = f"{h2h_home_wins}–{h2h_total - h2h_home_wins}" if h2h_total >= 5 else "nincs adat"

    if odds:
        odds_str = (
            f"💰 Szorzó: {home} *{odds['home']:.2f}* | {away} *{odds['away']:.2f}*"
        )
        if tip_odds:
            odds_str += f"\n💵 Tippelt szorzó: *{tip_odds:.2f}*"
    else:
        odds_str = "💰 Szorzó: _nem elérhető_"

    # Első szett sor (csak ha van elég adat)
    if home_fs_t >= 5 and away_fs_t >= 5:
        home_fs_pct = round(home_fs_w / home_fs_t * 100)
        away_fs_pct = round(away_fs_w / away_fs_t * 100)
        first_set_str = f"1️⃣ 1. szett: {home} *{home_fs_pct}%* | {away} *{away_fs_pct}%*"
    else:
        first_set_str = None

    lines = [
        f"🔶 *{home}* vs *{away}*",
        f"📍 {league} ({category}) | 🕐 {time_str}",
        f"",
        odds_str,
        f"🔁 H2H (utolsó 5): {h2h_str}",
        f"📈 Forma (utolsó 10): {home} {form_bar(home_w, home_t)} | {away} {form_bar(away_w, away_t)}",
    ]
    if first_set_str:
        lines.append(first_set_str)
    lines += [
        f"",
        f"{tip_str}",
        f"{confidence} (pontszám: {score:+.1f})",
    ]
    msg = "\n".join(lines)
    return msg, tip_odds, tip_meta


# ─────────────────────────────────────────────
#  TELEGRAM PARANCSOK – ASZTALITENISZ
# ─────────────────────────────────────────────


async def cmd_tt_tippek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Setka Cup és Czech Liga közelgő meccsek tippjei."""
    await update.message.reply_text(
        "🔍 *Setka Cup & Czech Liga* — meccsek elemzése...",
        parse_mode=ParseMode.MARKDOWN,
    )

    events = []
    used_date = None
    for delta in [0, 1]:
        check_date = (date.today() + timedelta(days=delta)).isoformat()
        all_ev = sofascore_fetch_events(check_date)
        filtered = [e for e in all_ev if is_allowed(e)]
        if filtered:
            events = all_ev  # az összes kell a forma számításhoz
            filtered_events = filtered
            used_date = check_date
            break

    if not events:
        await update.message.reply_text(
            "❌ Nem találtam Setka Cup vagy Czech Liga meccseket ma/holnap."
        )
        return

    if used_date != date.today().isoformat():
        await update.message.reply_text(
            f"ℹ️ Ma nincs meccs — holnapi ({used_date}) meccseket elemzem..."
        )

    # Csak a még nem befejezett meccsek
    upcoming = [
        e
        for e in filtered_events
        if e.get("status", {}).get("type", "").lower() == "notstarted"
    ]
    if not upcoming:
        upcoming = [
            e
            for e in filtered_events
            if e.get("status", {}).get("type", "").lower() != "finished"
        ]
    if not upcoming:
        upcoming = filtered_events

    league_names = sorted({e.get("tournament", {}).get("name", "?") for e in upcoming})
    await update.message.reply_text(
        f"📊 *{min(len(upcoming), 8)} meccs* elemzése — {', '.join(league_names[:3])}\n_(kérlek várj...)_",
        parse_mode=ParseMode.MARKDOWN,
    )

    sent = 0
    skipped_low_odds = 0
    for event in upcoming[:15]:
        try:
            msg, tip_odds, tip_meta = build_tip_message(event, events)
            if msg is None:
                skipped_low_odds += 1
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
        msg = f"❌ Nem találtam {MIN_ODDS:.2f}+ szorzójú tippet az elemzett meccsekből."
        if skipped_low_odds:
            msg += f"\n_{skipped_low_odds} meccs ki lett szűrve, mert a tippelt oldal szorzója {MIN_ODDS} alatt volt._"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    else:
        footer = f"✅ *{sent} tipp generálva* (min. {MIN_ODDS:.2f}+ szorzó)"
        if skipped_low_odds:
            footer += (
                f"\n_({skipped_low_odds} meccs kiszűrve: szorzó {MIN_ODDS} alatt)_"
            )
        footer += "\n\n⚠️ _A tippek statisztikai elemzésen alapulnak. Felelősen fogadj!_"
        await update.message.reply_text(footer, parse_mode=ParseMode.MARKDOWN)


async def cmd_tt_elo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Élő Setka Cup / Czech Liga meccsek."""
    await update.message.reply_text("🔍 Élő meccsek keresése...")
    today = date.today().isoformat()
    all_events = sofascore_fetch_events(today)
    live = [
        e
        for e in all_events
        if is_allowed(e) and e.get("status", {}).get("type", "").lower() == "inprogress"
    ]

    if not live:
        total_live = [
            e
            for e in all_events
            if e.get("status", {}).get("type", "").lower() == "inprogress"
        ]
        msg = "🏓 Jelenleg nincs élő Setka Cup vagy Czech Liga meccs."
        if total_live:
            msg += f"\n_(Más ligában {len(total_live)} élő meccs van.)_"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    lines = [f"🔴 *Élő meccsek — Setka Cup / Czech Liga ({len(live)})*\n"]
    for e in live[:20]:
        home = e.get("homeTeam", {}).get("name", "?")
        away = e.get("awayTeam", {}).get("name", "?")
        score = format_score(e)
        league = e.get("tournament", {}).get("name", "")
        lines.append(f"🏓 {home} {score} {away} — _{league}_")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_tt_mai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mai összes Setka Cup / Czech Liga meccs."""
    await update.message.reply_text("🔍 Mai meccsek lekérése...")
    today = date.today().isoformat()
    all_events = sofascore_fetch_events(today)
    events = [e for e in all_events if is_allowed(e)]

    if not events:
        await update.message.reply_text(
            f"📅 Ma ({today}) nincs Setka Cup / Czech Liga meccs."
        )
        return

    # Csoportosítás liga szerint
    leagues: dict[str, list] = {}
    for e in events:
        name = e.get("tournament", {}).get("name", "?")
        leagues.setdefault(name, []).append(e)

    total = len(events)
    lines = [
        f"📅 *Mai meccsek — Setka Cup / Czech Liga ({today})*\n*Összesen: {total} meccs*\n"
    ]

    for league_name, lg_events in list(leagues.items())[:5]:
        lines.append(f"\n🏆 *{league_name}* ({len(lg_events)} meccs)")
        for e in lg_events[:6]:
            lines.append(format_event_line(e))
        if len(lg_events) > 6:
            lines.append(f"  _...és még {len(lg_events) - 6} meccs_")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_tt_eredmenyek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mai Setka Cup / Czech Liga eredmények."""
    await update.message.reply_text("🔍 Eredmények lekérése...")
    today = date.today().isoformat()
    all_events = sofascore_fetch_events(today)
    finished = [
        e
        for e in all_events
        if is_allowed(e) and e.get("status", {}).get("type", "").lower() == "finished"
    ]

    if not finished:
        await update.message.reply_text(
            "❌ Ma még nincs befejezett Setka Cup / Czech Liga meccs."
        )
        return

    lines = [f"✅ *Mai eredmények — Setka Cup / Czech Liga ({len(finished)} meccs)*\n"]
    for e in finished[:20]:
        home = e.get("homeTeam", {}).get("name", "?")
        away = e.get("awayTeam", {}).get("name", "?")
        hs = e.get("homeScore", {}).get("current", "?")
        as_ = e.get("awayScore", {}).get("current", "?")
        league = e.get("tournament", {}).get("name", "")
        winner = "➡️" if hs == as_ else ("🏆" if (hs or 0) > (as_ or 0) else "  ")
        lines.append(f"{winner} *{home}* {hs}–{as_} *{away}* — _{league}_")

    if len(finished) > 20:
        lines.append(f"\n_...és még {len(finished) - 20} eredmény_")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_tt_ranglista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Legjobb játékosok mai teljesítmény alapján."""
    await update.message.reply_text("🔍 Mai statisztikák összesítése...")
    today = date.today().isoformat()
    all_events = sofascore_fetch_events(today)
    events = [
        e
        for e in all_events
        if is_allowed(e) and e.get("status", {}).get("type", "").lower() == "finished"
    ]

    if not events:
        await update.message.reply_text(
            "❌ Nincs elég befejezett meccs a ranglista összeállításához."
        )
        return

    # Játékos statisztikák összegyűjtése
    stats: dict[str, dict] = {}
    for e in events:
        home = e.get("homeTeam", {}).get("name", "?")
        away = e.get("awayTeam", {}).get("name", "?")
        hs = e.get("homeScore", {}).get("current") or 0
        as_ = e.get("awayScore", {}).get("current") or 0
        for player, won in [(home, hs > as_), (away, as_ > hs)]:
            if player not in stats:
                stats[player] = {"wins": 0, "losses": 0}
            if won:
                stats[player]["wins"] += 1
            else:
                stats[player]["losses"] += 1

    # Top játékosok (min. 3 meccs)
    ranked = [
        (name, s["wins"], s["wins"] + s["losses"])
        for name, s in stats.items()
        if s["wins"] + s["losses"] >= 3
    ]
    ranked.sort(key=lambda x: (-x[1], -x[2]))

    lines = [f"🏆 *Mai legjobb játékosok — Setka Cup / Czech Liga*\n_(min. 3 meccs)_\n"]
    for i, (name, wins, total) in enumerate(ranked[:15], 1):
        losses = total - wins
        pct = round(wins / total * 100)
        lines.append(f"{i}. *{name}* — {wins}W / {losses}L ({pct}%)")

    if not ranked:
        await update.message.reply_text(
            "❌ Nincs elég adat a ranglistához (min. 3 meccs szükséges)."
        )
        return

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
#  LABDARÚGÁS
# ─────────────────────────────────────────────


def fetch_live_football():
    try:
        resp = requests.get(
            f"{FOOTBALL_API_BASE}/fixtures",
            headers=FOOTBALL_API_HEADERS,
            params={"live": "all"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("response", [])
    except Exception as e:
        logger.error(f"Live football hiba: {e}")
        return []


def fetch_football_today():
    today = date.today().isoformat()
    try:
        resp = requests.get(
            f"{FOOTBALL_API_BASE}/fixtures",
            headers=FOOTBALL_API_HEADERS,
            params={"date": today, "timezone": "UTC"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("response", [])
    except Exception as e:
        logger.error(f"Football today hiba: {e}")
        return []


def format_football_fixture(fixture: dict) -> str:
    f = fixture.get("fixture", {})
    teams = fixture.get("teams", {})
    goals = fixture.get("goals", {})
    home = teams.get("home", {}).get("name", "?")
    away = teams.get("away", {}).get("name", "?")
    hg = goals.get("home")
    ag = goals.get("away")
    status = f.get("status", {}).get("short", "?")
    elapsed = f.get("status", {}).get("elapsed")
    score = f" *{hg}–{ag}*" if hg is not None and ag is not None else ""
    time_str = f" [{elapsed}']" if elapsed else ""
    league = fixture.get("league", {}).get("name", "")
    return f"⚽ {home} vs {away}{score} ({status}{time_str}) — _{league}_"


# ─────────────────────────────────────────────
#  TELEGRAM PARANCSOK – ÁLTALÁNOS
# ─────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Sports Bot — Parancsok*\n\n"
        "🏓 *Asztalitenisz (Setka Cup & Czech Liga):*\n"
        "/tt\\_tippek — Közelgő meccsek elemzése és tippek\n"
        "/tt\\_elo — Élő meccsek\n"
        "/tt\\_mai — Mai összes meccs\n"
        "/tt\\_eredmenyek — Mai eredmények\n"
        "/tt\\_ranglista — Legjobb játékosok ma\n"
        "/tt\\_statisztika — Nyerési arány és tipp előzmények\n\n"
        "⚽ *Labdarúgás:*\n"
        "/live — Élő futballmeccsek\n"
        "/mai — Mai futballmeccsek\n\n"
        "ℹ️ /help — Súgó"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Élő futballmeccsek...")
    fixtures = fetch_live_football()
    if not fixtures:
        await update.message.reply_text("⚽ Jelenleg nincs élő futballmeccs.")
        return
    lines = [f"🔴 *Élő futballmeccsek ({len(fixtures)})*\n"]
    for f in fixtures[:15]:
        lines.append(format_football_fixture(f))
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_mai_foci(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Mai futballmeccsek...")
    fixtures = fetch_football_today()
    if not fixtures:
        await update.message.reply_text("📅 Ma nincs futballmeccs.")
        return
    lines = [f"📅 *Mai futballmeccsek ({len(fixtures)})*\n"]
    for f in fixtures[:20]:
        lines.append(format_football_fixture(f))
    if len(fixtures) > 20:
        lines.append(f"\n_...és még {len(fixtures) - 20} meccs_")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
#  STATISZTIKA PARANCS
# ─────────────────────────────────────────────


async def cmd_tt_statisztika(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tipp előzmények és nyerési arány kimutatása."""
    await update.message.reply_text(
        "📊 Statisztikák frissítése...", parse_mode=ParseMode.MARKDOWN
    )

    tips = load_tips()
    if not tips:
        await update.message.reply_text(
            "📭 Még nincs mentett tipp. A bot indítása óta nem küldött tippet, "
            "vagy a fájl törlődött.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Lezáratlan tippek eredményeinek lekérése
    tips = resolve_pending_tips(tips)

    wins = [t for t in tips if t.get("result") == "win"]
    losses = [t for t in tips if t.get("result") == "loss"]
    pending = [t for t in tips if t.get("result") is None]

    total_settled = len(wins) + len(losses)
    win_rate = (len(wins) / total_settled * 100) if total_settled > 0 else 0

    # ROI becslés (ha van odds adat)
    roi_total = 0.0
    roi_count = 0
    for t in wins + losses:
        o = t.get("odds")
        if o:
            roi_total += (o - 1) if t["result"] == "win" else -1
            roi_count += 1
    roi_pct = (roi_total / roi_count * 100) if roi_count > 0 else 0

    # Ligánkénti bontás
    league_stats: dict[str, dict] = {}
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

    # Utolsó 5 tipp
    recent = sorted(tips, key=lambda t: t.get("sent_at", 0), reverse=True)[:5]
    recent_lines = []
    for t in recent:
        r = t.get("result")
        icon = "✅" if r == "win" else ("❌" if r == "loss" else "⏳")
        odds_str = f" @ {t['odds']:.2f}" if t.get("odds") else ""
        recent_lines.append(
            f"{icon} {t['predicted_name']}{odds_str} — {t['home']} vs {t['away']}"
        )

    lines = [
        f"📊 *Tipp statisztikák*\n",
        f"🎯 Összes tipp: *{len(tips)}* ({total_settled} lezárt, {len(pending)} folyamatban)",
        f"✅ Nyert: *{len(wins)}* | ❌ Veszített: *{len(losses)}*",
        f"📈 Nyerési arány: *{win_rate:.1f}%*",
    ]
    if roi_count > 0:
        roi_icon = "📈" if roi_pct >= 0 else "📉"
        lines.append(
            f"{roi_icon} Becsült ROI: *{roi_pct:+.1f}%* (1 egységes tét alapján)"
        )

    if league_stats:
        lines.append("\n*Ligánkénti bontás:*")
        for lg, s in league_stats.items():
            tot = s["wins"] + s["losses"]
            pct = f"{s['wins'] / tot * 100:.0f}%" if tot > 0 else "–"
            lines.append(
                f"• {lg}: {s['wins']}W / {s['losses']}L ({pct}) | ⏳ {s['pending']}"
            )

    if recent_lines:
        lines.append("\n*Utolsó 5 tipp:*")
        lines.extend(recent_lines)

    lines.append("\n⚠️ _Statisztikai elemzésen alapul. Felelősen fogadj!_")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
#  MULTI-CHAT KÜLDŐ
# ─────────────────────────────────────────────


async def send_to_all_chats(bot, text: str):
    """Elküldi az üzenetet az összes konfigurált chat ID-ra."""
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Küldési hiba (chat_id={chat_id}): {e}")


# ─────────────────────────────────────────────
#  AUTOMATIKUS STARTUP TIPPEK
# ─────────────────────────────────────────────


async def send_startup_tips(app):
    """Bot indításkor automatikusan elküldi a következő 8 óra tippjeit a chat_id-re."""
    if not TELEGRAM_CHAT_ID:
        logger.warning(
            "TELEGRAM_CHAT_ID nincs beállítva – startup tippek nem küldhetők."
        )
        return

    logger.info("Startup tippek generálása (következő 8 óra)...")
    now_ts = int(datetime.utcnow().timestamp())
    horizon_ts = now_ts + 8 * 3600  # 8 óra előre

    today = date.today().isoformat()
    events = sofascore_fetch_events(today)

    # Ha a horizont átnyúlik a következő napra, adjuk hozzá holnap meccseit is
    if horizon_ts > int(
        datetime(
            datetime.utcnow().year,
            datetime.utcnow().month,
            datetime.utcnow().day,
            23,
            59,
            59,
        ).timestamp()
    ):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        events += sofascore_fetch_events(tomorrow)

    upcoming_8h = [
        e
        for e in events
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
    # Státusz üzenet csak a bot saját csatornájára
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=(
            f"🤖 *Bot elindult — Automatikus tippek*\n"
            f"📅 Következő 8 óra tippjei ({len(upcoming_8h)} meccs elemzése)\n"
            f"📍 {', '.join(league_names[:3])}\n"
            f"🔕 _(Csak Erős/Közepes, min. {MIN_ODDS:.2f}+ szorzó)_"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    already_sent_ids = {t["event_id"] for t in load_tips()}

    MAX_STARTUP_TIPS = 20
    sent = 0
    for event in upcoming_8h:
        if sent >= MAX_STARTUP_TIPS:
            break
        event_id = event.get("id")
        if event_id and event_id in already_sent_ids:
            logger.info(f"Már elküldött tipp, kihagyva: event_id={event_id}")
            continue
        try:
            msg, tip_odds, tip_meta = build_tip_message(event, events)
            if msg is None:
                continue
            if tip_meta:
                if not save_tip_record(tip_meta):
                    logger.info(f"Duplikát kihagyva (startup): event_id={event_id}")
                    continue
            # Tipp üzenet minden csoportba megy
            await send_to_all_chats(app.bot, msg)
            sent += 1
        except Exception as e:
            logger.error(f"Startup tipp hiba: {e}")

    if sent == 0:
        # Státusz csak a bot saját csatornájára
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=(
                f"❌ Nincs megfelelő tipp a következő 8 órában.\n"
                f"_(Minden meccs kiszűrve: bizonytalan statisztika vagy szorzó < {MIN_ODDS:.2f})_"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        # Összefoglaló csak a bot saját csatornájára
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=(
                f"✅ *{sent} startup tipp elküldve!*\n"
                f"⚠️ _Statisztikai elemzésen alapul. Felelősen fogadj!_"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

    logger.info(f"Startup tippek kész: {sent} tipp elküldve.")


# ─────────────────────────────────────────────
#  EREDMÉNY FIGYELŐ ÉS ÉRTESÍTŐ
# ─────────────────────────────────────────────


def _collect_results_sync() -> list:
    """Szinkron eredmény-lekérés thread-ben — nem blokkolja az event loop-ot."""
    tips = load_tips()
    now_ts = int(datetime.utcnow().timestamp())
    notifications = []

    for t in tips:
        if t.get("result") is not None:
            continue
        if t.get("start_timestamp", 0) + 45 * 60 > now_ts:
            continue

        actual = fetch_match_result(t["event_id"])
        if actual is None:
            continue

        odds_text = f"{t['odds']:.2f}" if t.get("odds") else "N/A"
        predicted_name = t.get("predicted_name", t.get("predicted", "?"))

        if actual == "postponed":
            update_tip_result(t["event_id"], "postponed", "postponed")
            msg = (
                f"⚠️ *Meccs elmaradt!*\n\n"
                f"🏓 {t['home']} vs {t['away']}\n"
                f"🏆 {t.get('league', '?')}\n"
                f"🎯 Tippünk: *{predicted_name}*\n"
                f"📊 Szorzó: {odds_text}\n"
                f"ℹ️ A meccs elmaradt vagy törölték — tipp érvénytelen."
            )
        else:
            result = "win" if actual == t["predicted"] else "loss"
            update_tip_result(t["event_id"], result, actual)
            won = result == "win"
            icon = "✅" if won else "❌"
            result_text = "NYERT" if won else "VESZETT"
            msg = (
                f"{icon} *Eredmény — {result_text}!*\n\n"
                f"🏓 {t['home']} vs {t['away']}\n"
                f"🏆 {t.get('league', '?')}\n"
                f"🎯 Tippünk: *{predicted_name}*\n"
                f"📊 Szorzó: {odds_text}\n"
                f"🏆 Tényleges győztes: {t['home'] if actual == 'home' else t['away']}"
            )
            actual = result  # store result for logging

        notifications.append((t["event_id"], actual, msg))

    return notifications


async def check_results_and_notify(context):
    """10 percenként ellenőrzi a lezárt meccseket és Telegramra küldi az eredményt."""
    if not TELEGRAM_CHAT_ID:
        return

    notifications = await asyncio.to_thread(_collect_results_sync)

    for event_id, result, msg in notifications:
        try:
            await send_to_all_chats(context.bot, msg)
            logger.info(f"Eredmény értesítő elküldve: event_id={event_id}, result={result}")
        except Exception as e:
            logger.error(f"Eredmény értesítő hiba: {e}")


# ─────────────────────────────────────────────
#  FOLYAMATOS TIPP FIGYELŐ (15 percenként)
# ─────────────────────────────────────────────

SCAN_INTERVAL_SEC = 900  # 15 perc


def _collect_new_tips_sync() -> list:
    """Szinkron HTTP adatgyűjtés — thread-ben fut, nem blokkolja az event loop-ot."""
    now_ts = int(datetime.utcnow().timestamp())
    horizon_ts = now_ts + 12 * 3600

    today = date.today().isoformat()
    events = sofascore_fetch_events(today)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    events += sofascore_fetch_events(tomorrow)

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
    """15 percenként fut: ha új erős tipp van, azonnal küldi Telegramra."""
    if not TELEGRAM_CHAT_ID:
        return

    tips_to_send = await asyncio.to_thread(_collect_new_tips_sync)

    for msg, tip_meta in tips_to_send:
        try:
            if tip_meta:
                if not save_tip_record(tip_meta):
                    logger.info(f"Duplikát kihagyva (scan): event_id={tip_meta.get('event_id')}")
                    continue
            await send_to_all_chats(context.bot, msg)
            logger.info(f"Automatikus tipp elküldve: event_id={tip_meta.get('event_id') if tip_meta else '?'}")
        except Exception as e:
            logger.error(f"Automatikus tipp hiba: {e}")

    if tips_to_send:
        logger.info(f"Scan kész: {len(tips_to_send)} új tipp elküldve.")
    else:
        logger.debug("Scan kész: nincs új tipp.")


# ─────────────────────────────────────────────
#  FŐPROGRAM
# ─────────────────────────────────────────────


def backfill_prod_api():
    """Induláskor feltölti az összes dev DB tippet a production API-ra (ha PROD_API_URL be van állítva)."""
    if not PROD_API_URL:
        return
    tips = load_tips()
    if not tips:
        return
    ok = 0
    for t in tips:
        try:
            resp = requests.post(
                f"{PROD_API_URL}/api/tips",
                json={
                    "event_id": t["event_id"],
                    "home": t["home"],
                    "away": t["away"],
                    "league": t["league"],
                    "predicted": t["predicted"],
                    "predicted_name": t["predicted_name"],
                    "odds": float(t["odds"]) if t.get("odds") is not None else None,
                    "start_timestamp": t["start_timestamp"],
                    "sent_at": t["sent_at"],
                },
                timeout=8,
            )
            if t.get("result"):
                requests.patch(
                    f"{PROD_API_URL}/api/tips/{t['event_id']}",
                    json={
                        "result": t["result"],
                        "actual_winner": t.get("actual_winner"),
                    },
                    timeout=8,
                )
            ok += 1
        except Exception as e:
            logger.warning(f"Backfill hiba event_id={t['event_id']}: {e}")
    logger.info(
        f"Production API backfill kész: {ok}/{len(tips)} tipp feltöltve → {PROD_API_URL}"
    )


async def cmd_lezar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manuálisan lezár egy függőben lévő tippet."""
    tips = load_tips()
    pending = [t for t in tips if t.get("result") is None]
    if not pending:
        await update.message.reply_text("Nincs függőben lévő tipp.")
        return
    for t in pending:
        predicted_name = t.get("predicted_name", t.get("predicted", "?"))
        odds_text = f"{t['odds']:.2f}" if t.get("odds") else "N/A"
        eid = t["event_id"]
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Nyert", callback_data=f"lezar_win_{eid}"),
                InlineKeyboardButton("❌ Veszett", callback_data=f"lezar_loss_{eid}"),
                InlineKeyboardButton("⚠️ Elmaradt", callback_data=f"lezar_postponed_{eid}"),
            ]
        ])
        await update.message.reply_text(
            f"🏓 *{t['home']} vs {t['away']}*\n"
            f"🏆 {t.get('league', '?')}\n"
            f"🎯 Tipp: *{predicted_name}* @ {odds_text}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )


async def callback_lezar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline gomb lekezelése a manuális lezáráshoz."""
    query = update.callback_query
    await query.answer()
    data = query.data  # pl. lezar_win_12345
    parts = data.split("_", 2)
    if len(parts) != 3:
        return
    _, action, event_id_str = parts
    try:
        event_id = int(event_id_str)
    except ValueError:
        return

    tips = load_tips()
    tip = next((t for t in tips if str(t["event_id"]) == str(event_id)), None)
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
        f"{label}\n\n"
        f"🏓 {tip['home']} vs {tip['away']}\n"
        f"🏆 {tip.get('league', '?')}\n"
        f"🎯 Tipp: {predicted_name}",
        parse_mode=ParseMode.MARKDOWN,
    )


def main():
    logger.info("Sports Bot indul...")
    sync_all_tips_to_kv()
    backfill_prod_api()
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(send_startup_tips)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # Asztalitenisz
    app.add_handler(CommandHandler("tt_tippek", cmd_tt_tippek))
    app.add_handler(CommandHandler("tt_elo", cmd_tt_elo))
    app.add_handler(CommandHandler("tt_mai", cmd_tt_mai))
    app.add_handler(CommandHandler("tt_eredmenyek", cmd_tt_eredmenyek))
    app.add_handler(CommandHandler("tt_ranglista", cmd_tt_ranglista))
    app.add_handler(CommandHandler("tt_statisztika", cmd_tt_statisztika))
    app.add_handler(CommandHandler("lezar", cmd_lezar))
    app.add_handler(CallbackQueryHandler(callback_lezar, pattern=r"^lezar_"))

    # Labdarúgás
    app.add_handler(CommandHandler("live", cmd_live))
    app.add_handler(CommandHandler("mai", cmd_mai_foci))

    # Folyamatos figyelő: 15 percenként ellenőrzi az új tippeket
    if app.job_queue:
        app.job_queue.run_repeating(
            scan_and_send_tips,
            interval=SCAN_INTERVAL_SEC,
            first=300,  # első futás 5 perccel az indítás után
        )
        app.job_queue.run_repeating(
            check_results_and_notify,
            interval=600,  # 10 percenként
            first=120,  # első futás 2 perccel az indítás után
        )
        logger.info(f"Automatikus tipp figyelő bekapcsolva ({SCAN_INTERVAL_SEC}s).")
        logger.info("Eredmény értesítő bekapcsolva (600s).")
    else:
        logger.warning("JobQueue nem elérhető – automatikus figyelő kikapcsolva.")

    logger.info("Bot fut. Asztalitenisz: SofaScore API (Setka Cup + Czech Liga | Czech: forma-alapú H2H nélkül | min. odds 1.55)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
