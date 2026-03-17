import os
import logging
import requests
from datetime import datetime, date
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SPORTS_API_KEY = os.environ["SPORTS_API_KEY"]

FOOTBALL_API_BASE = "https://v3.football.api-sports.io"
TT_API_BASE = "https://v1.tabletennis.api-sports.io"

API_HEADERS = {
    "x-apisports-key": SPORTS_API_KEY
}


# ─────────────────────────────────────────────
#  ASZTALITENISZ API FÜGGVÉNYEK
# ─────────────────────────────────────────────

def tt_fetch_games(target_date: str = None, live: bool = False) -> list:
    """Asztalitenisz meccsek lekérése dátum vagy live szűrővel."""
    params = {}
    if live:
        params["live"] = "all"
    else:
        params["date"] = target_date or date.today().isoformat()
    try:
        resp = requests.get(f"{TT_API_BASE}/games", headers=API_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("response", [])
    except Exception as e:
        logger.error(f"TT games hiba: {e}")
        return []


def tt_fetch_h2h(player1_id: int, player2_id: int) -> list:
    """Két játékos egymás elleni meccseinek lekérése."""
    try:
        params = {"h2h": f"{player1_id}-{player2_id}"}
        resp = requests.get(f"{TT_API_BASE}/games/h2h", headers=API_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("response", [])
    except Exception as e:
        logger.error(f"TT H2H hiba: {e}")
        return []


def tt_fetch_player_games(player_id: int, last: int = 10) -> list:
    """Egy játékos utolsó N meccsének lekérése a forma meghatározásához."""
    try:
        params = {"player": player_id, "last": last}
        resp = requests.get(f"{TT_API_BASE}/games", headers=API_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("response", [])
    except Exception as e:
        logger.error(f"TT játékos meccsek hiba: {e}")
        return []


def tt_fetch_rankings(limit: int = 20) -> list:
    """Asztalitenisz világranglista lekérése."""
    try:
        resp = requests.get(f"{TT_API_BASE}/players/rankings", headers=API_HEADERS,
                            params={"type": "world"}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("response", [])[:limit]
    except Exception as e:
        logger.error(f"TT rankings hiba: {e}")
        return []


# ─────────────────────────────────────────────
#  TIPP ELEMZŐ LOGIKA
# ─────────────────────────────────────────────

def get_player_form(player_id: int, player_name: str, last: int = 5) -> tuple[int, int]:
    """Visszaadja: (győzelmek, összes meccs) az utolsó N mérkőzésen."""
    games = tt_fetch_player_games(player_id, last=last)
    wins = 0
    total = 0
    for g in games:
        players = g.get("players", {})
        home = players.get("home", {})
        away = players.get("away", {})
        scores = g.get("scores", {})
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AWD"):
            continue
        total += 1
        home_name = home.get("name", "")
        away_name = away.get("name", "")
        home_sets = scores.get("home", 0) or 0
        away_sets = scores.get("away", 0) or 0
        if player_name.lower() in home_name.lower() and home_sets > away_sets:
            wins += 1
        elif player_name.lower() in away_name.lower() and away_sets > home_sets:
            wins += 1
    return wins, total


def analyze_h2h(h2h_games: list, player1_name: str) -> tuple[int, int]:
    """Visszaadja: (player1 győzelmei, összes befejezett meccs) a H2H adatokból."""
    p1_wins = 0
    total = 0
    for g in h2h_games:
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AWD"):
            continue
        players = g.get("players", {})
        home = players.get("home", {}).get("name", "")
        away = players.get("away", {}).get("name", "")
        scores = g.get("scores", {})
        home_sets = scores.get("home", 0) or 0
        away_sets = scores.get("away", 0) or 0
        total += 1
        if player1_name.lower() in home.lower() and home_sets > away_sets:
            p1_wins += 1
        elif player1_name.lower() in away.lower() and away_sets > home_sets:
            p1_wins += 1
    return p1_wins, total


def calculate_tip(
    p1_rank: int | None, p2_rank: int | None,
    p1_h2h_wins: int, h2h_total: int,
    p1_form_wins: int, p1_form_total: int,
    p2_form_wins: int, p2_form_total: int,
) -> tuple[str, str, float]:
    """
    Tipp kiszámítása pontozási rendszerrel.
    Visszaad: ('home'|'away'|'uncertain', bizalom_szint, pontszám)
    """
    score = 0.0

    # 1. Ranglista különbség (max ±50 pont)
    if p1_rank and p2_rank and p1_rank > 0 and p2_rank > 0:
        rank_diff = p2_rank - p1_rank
        normalized = rank_diff / max(p1_rank, p2_rank)
        score += normalized * 50

    # 2. Egymás elleni mérleg (max ±30 pont)
    if h2h_total >= 3:
        h2h_rate = (p1_h2h_wins / h2h_total) - 0.5
        score += h2h_rate * 30

    # 3. Legutóbbi forma (max ±20 pont)
    p1_form_rate = (p1_form_wins / p1_form_total) if p1_form_total > 0 else 0.5
    p2_form_rate = (p2_form_wins / p2_form_total) if p2_form_total > 0 else 0.5
    form_diff = (p1_form_rate - p2_form_rate)
    score += form_diff * 20

    # Döntés
    if score >= 20:
        confidence = "🟢 Erős tipp"
        winner = "home"
    elif score >= 8:
        confidence = "🟡 Közepes tipp"
        winner = "home"
    elif score <= -20:
        confidence = "🟢 Erős tipp"
        winner = "away"
    elif score <= -8:
        confidence = "🟡 Közepes tipp"
        winner = "away"
    else:
        confidence = "🔴 Bizonytalan"
        winner = "uncertain"

    return winner, confidence, score


def form_bar(wins: int, total: int) -> str:
    """Vizuális forma sáv, pl. ●●●○○"""
    if total == 0:
        return "–"
    filled = round((wins / total) * 5)
    return "●" * filled + "○" * (5 - filled)


def build_tip_message(game: dict) -> str | None:
    """Egyetlen meccshez tipp üzenet összeállítása."""
    players = game.get("players", {})
    home = players.get("home", {})
    away = players.get("away", {})

    home_name = home.get("name", "?")
    away_name = away.get("name", "?")
    home_id = home.get("id")
    away_id = away.get("id")

    tournament = game.get("league", {}).get("name", "")
    game_date = game.get("date", "")[:16].replace("T", " ")

    # Ranglista pozíciók
    home_rank = home.get("ranking") or home.get("rank")
    away_rank = away.get("ranking") or away.get("rank")

    rank_str_home = f"#{home_rank}" if home_rank else "N/A"
    rank_str_away = f"#{away_rank}" if away_rank else "N/A"

    # H2H és forma lekérése (csak ha van player ID)
    p1_h2h_wins, h2h_total = 0, 0
    p1_form_wins, p1_form_total = 0, 0
    p2_form_wins, p2_form_total = 0, 0

    if home_id and away_id:
        h2h_games = tt_fetch_h2h(home_id, away_id)
        p1_h2h_wins, h2h_total = analyze_h2h(h2h_games, home_name)

    if home_id:
        p1_form_wins, p1_form_total = get_player_form(home_id, home_name, last=5)
    if away_id:
        p2_form_wins, p2_form_total = get_player_form(away_id, away_name, last=5)

    winner, confidence, score = calculate_tip(
        home_rank, away_rank,
        p1_h2h_wins, h2h_total,
        p1_form_wins, p1_form_total,
        p2_form_wins, p2_form_total,
    )

    if winner == "home":
        tipp_str = f"🏓 *Tipp: {home_name}* győz"
    elif winner == "away":
        tipp_str = f"🏓 *Tipp: {away_name}* győz"
    else:
        tipp_str = "🏓 *Tipp: Nagyon szoros meccs*"

    h2h_str = f"{p1_h2h_wins}–{h2h_total - p1_h2h_wins}" if h2h_total >= 2 else "nincs elég adat"

    lines = [
        f"🔶 *{home_name}* vs *{away_name}*",
        f"📍 {tournament} | 🕐 {game_date} UTC",
        f"",
        f"🌍 Rangsor: {home_name} ({rank_str_home}) vs {away_name} ({rank_str_away})",
        f"🔁 H2H (utolsó): {h2h_str}",
        f"📈 Forma (5 meccs): {home_name} {form_bar(p1_form_wins, p1_form_total)} | {away_name} {form_bar(p2_form_wins, p2_form_total)}",
        f"",
        f"{tipp_str}",
        f"{confidence} (pontszám: {score:+.1f})",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  TELEGRAM PARANCSOK – ASZTALITENISZ
# ─────────────────────────────────────────────

async def cmd_tt_tippek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mai asztalitenisz meccsek elemzése és tippek."""
    await update.message.reply_text("🔍 Asztalitenisz meccsek elemzése folyamatban...")

    today = date.today().isoformat()
    games = tt_fetch_games(target_date=today)

    # Ha nincs mai meccs, holnapot próbáljuk
    if not games:
        from datetime import timedelta
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        games = tt_fetch_games(target_date=tomorrow)
        if games:
            await update.message.reply_text(f"ℹ️ Ma nincs meccs, holnapi ({tomorrow}) meccseket elemzem...")
        else:
            await update.message.reply_text("❌ Nem találtam közelgő asztalitenisz meccseket.")
            return

    # Csak a nem befejezett meccseket elemezzük
    upcoming = [g for g in games if g.get("status", {}).get("short", "") in ("NS", "TBD", "")]
    if not upcoming:
        upcoming = games[:5]

    await update.message.reply_text(f"📊 {len(upcoming[:8])} meccs elemzése... (kérlek várj)")

    sent = 0
    for game in upcoming[:8]:
        try:
            msg = build_tip_message(game)
            if msg:
                await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
                sent += 1
        except Exception as e:
            logger.error(f"Tipp összeállítási hiba: {e}")
            continue

    if sent == 0:
        await update.message.reply_text("❌ Nem sikerült tippet generálni. Próbáld újra később.")
    else:
        await update.message.reply_text(
            f"✅ *{sent} tipp generálva!*\n\n"
            "⚠️ _Fontos: A tippek statisztikai elemzésen alapulnak, "
            "nem garantálnak nyerést. Felelősen fogadj!_",
            parse_mode=ParseMode.MARKDOWN
        )


async def cmd_tt_elo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Éppen zajló asztalitenisz meccsek."""
    await update.message.reply_text("🔍 Élő asztalitenisz meccsek keresése...")
    games = tt_fetch_games(live=True)

    if not games:
        await update.message.reply_text("🏓 Jelenleg nincs élő asztalitenisz meccs.")
        return

    lines = [f"🔴 *Élő asztalitenisz meccsek ({len(games)})*\n"]
    for g in games[:10]:
        players = g.get("players", {})
        home = players.get("home", {}).get("name", "?")
        away = players.get("away", {}).get("name", "?")
        scores = g.get("scores", {})
        h_sets = scores.get("home", "–")
        a_sets = scores.get("away", "–")
        league = g.get("league", {}).get("name", "")
        lines.append(f"🏓 *{home}* {h_sets}–{a_sets} *{away}* — _{league}_")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_tt_ranglista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asztalitenisz világranglista top 15."""
    await update.message.reply_text("🔍 Ranglista lekérése...")
    rankings = tt_fetch_rankings(limit=15)

    if not rankings:
        await update.message.reply_text("❌ Nem sikerült lekérni a ranglistát.")
        return

    lines = ["🏆 *Asztalitenisz Világranglista (Top 15)*\n"]
    for entry in rankings:
        rank = entry.get("position") or entry.get("rank", "?")
        player = entry.get("player", {})
        name = player.get("name", "?") if isinstance(player, dict) else entry.get("name", "?")
        country = entry.get("country", {})
        flag = country.get("code", "") if isinstance(country, dict) else ""
        points = entry.get("points", "")
        pts_str = f" — {points} pont" if points else ""
        lines.append(f"{rank}. *{name}* {flag}{pts_str}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
#  LABDARÚGÁS API FÜGGVÉNYEK
# ─────────────────────────────────────────────

def fetch_live_fixtures():
    url = f"{FOOTBALL_API_BASE}/fixtures"
    try:
        resp = requests.get(url, headers=API_HEADERS, params={"live": "all"}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("response", [])
    except Exception as e:
        logger.error(f"Live fixtures hiba: {e}")
        return []


def fetch_fixtures_by_date(target_date: str = None):
    if target_date is None:
        target_date = date.today().isoformat()
    try:
        resp = requests.get(f"{FOOTBALL_API_BASE}/fixtures", headers=API_HEADERS,
                            params={"date": target_date, "timezone": "UTC"}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("response", [])
    except Exception as e:
        logger.error(f"Fixtures hiba: {e}")
        return []


def fetch_standings(league_id: int, season: int = None):
    if season is None:
        season = datetime.now().year
    try:
        resp = requests.get(f"{FOOTBALL_API_BASE}/standings", headers=API_HEADERS,
                            params={"league": league_id, "season": season}, timeout=10)
        resp.raise_for_status()
        responses = resp.json().get("response", [])
        if responses:
            return responses[0].get("league", {}).get("standings", [[]])[0]
        return []
    except Exception as e:
        logger.error(f"Standings hiba: {e}")
        return []


def format_fixture(fixture: dict) -> str:
    f = fixture.get("fixture", {})
    teams = fixture.get("teams", {})
    goals = fixture.get("goals", {})
    league = fixture.get("league", {})
    home = teams.get("home", {}).get("name", "?")
    away = teams.get("away", {}).get("name", "?")
    home_goals = goals.get("home")
    away_goals = goals.get("away")
    status = f.get("status", {}).get("short", "?")
    elapsed = f.get("status", {}).get("elapsed")
    score = f" *{home_goals}–{away_goals}*" if home_goals is not None and away_goals is not None else ""
    time_str = f" [{elapsed}']" if elapsed else ""
    return f"⚽ {home} vs {away}{score} ({status}{time_str}) — _{league.get('name', '')}_"


# ─────────────────────────────────────────────
#  TELEGRAM PARANCSOK – LABDARÚGÁS
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Sports Bot — Parancsok*\n\n"
        "🏓 *Asztalitenisz:*\n"
        "/tt\\_tippek — Mai meccsek elemzése és tippek\n"
        "/tt\\_elo — Élő asztalitenisz meccsek\n"
        "/tt\\_ranglista — Világranglista Top 15\n\n"
        "⚽ *Labdarúgás:*\n"
        "/live — Élő futballmeccsek\n"
        "/mai — Mai futballmeccsek\n"
        "/tabella — Premier League tabella\n\n"
        "ℹ️ /help — Súgó"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Élő meccsek keresése...")
    fixtures = fetch_live_fixtures()
    if not fixtures:
        await update.message.reply_text("⚽ Jelenleg nincs élő meccs.")
        return
    lines = ["🔴 *Élő meccsek*\n"] + [format_fixture(f) for f in fixtures[:15]]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_mai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Mai meccsek lekérése...")
    today = date.today().isoformat()
    fixtures = fetch_fixtures_by_date(today)
    if not fixtures:
        await update.message.reply_text(f"📅 Ma ({today}) nincs meccs.")
        return
    lines = [f"📅 *Mai meccsek ({today})*\n"] + [format_fixture(f) for f in fixtures[:20]]
    if len(fixtures) > 20:
        lines.append(f"\n_...és még {len(fixtures) - 20} meccs_")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_tabella(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Premier League tabella lekérése...")
    standings = fetch_standings(league_id=39, season=2024)
    if not standings:
        await update.message.reply_text("❌ Nem sikerült lekérni a tabellát.")
        return
    lines = ["🏆 *Premier League Tabella (Top 10)*\n"]
    for e in standings[:10]:
        r = e.get("rank", 0)
        team = e.get("team", {}).get("name", "?")
        pts = e.get("points", 0)
        played = e.get("all", {}).get("played", 0)
        won = e.get("all", {}).get("win", 0)
        lost = e.get("all", {}).get("lose", 0)
        gd = e.get("goalsDiff", 0)
        lines.append(f"{r}. *{team}* — {pts} pt ({played}M {won}W {lost}V, GD: {gd:+d})")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
#  FŐPROGRAM
# ─────────────────────────────────────────────

def main():
    logger.info("Sports Bot indul...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Asztalitenisz parancsok
    app.add_handler(CommandHandler("tt_tippek", cmd_tt_tippek))
    app.add_handler(CommandHandler("tt_elo", cmd_tt_elo))
    app.add_handler(CommandHandler("tt_ranglista", cmd_tt_ranglista))

    # Labdarúgás parancsok
    app.add_handler(CommandHandler("live", cmd_live))
    app.add_handler(CommandHandler("mai", cmd_mai))
    app.add_handler(CommandHandler("tabella", cmd_tabella))

    # Általános
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    logger.info("Bot fut. Parancsok: /tt_tippek /tt_elo /tt_ranglista /live /mai /tabella")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
