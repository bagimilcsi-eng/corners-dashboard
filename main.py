import os
import logging
import asyncio
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

SPORTS_API_BASE = "https://v3.football.api-sports.io"
SPORTS_API_HEADERS = {
    "x-apisports-key": SPORTS_API_KEY
}


def fetch_live_fixtures():
    """Fetch currently live football matches."""
    url = f"{SPORTS_API_BASE}/fixtures"
    params = {"live": "all"}
    try:
        resp = requests.get(url, headers=SPORTS_API_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", [])
    except Exception as e:
        logger.error(f"Error fetching live fixtures: {e}")
        return []


def fetch_fixtures_by_date(target_date: str = None):
    """Fetch football fixtures for a given date (YYYY-MM-DD). Defaults to today."""
    if target_date is None:
        target_date = date.today().isoformat()
    url = f"{SPORTS_API_BASE}/fixtures"
    params = {"date": target_date, "timezone": "UTC"}
    try:
        resp = requests.get(url, headers=SPORTS_API_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", [])
    except Exception as e:
        logger.error(f"Error fetching fixtures for {target_date}: {e}")
        return []


def fetch_standings(league_id: int, season: int = None):
    """Fetch league standings."""
    if season is None:
        season = datetime.now().year
    url = f"{SPORTS_API_BASE}/standings"
    params = {"league": league_id, "season": season}
    try:
        resp = requests.get(url, headers=SPORTS_API_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        responses = data.get("response", [])
        if responses:
            return responses[0].get("league", {}).get("standings", [[]])[0]
        return []
    except Exception as e:
        logger.error(f"Error fetching standings for league {league_id}: {e}")
        return []


def fetch_top_scorers(league_id: int, season: int = None):
    """Fetch top scorers for a league."""
    if season is None:
        season = datetime.now().year
    url = f"{SPORTS_API_BASE}/players/topscorers"
    params = {"league": league_id, "season": season}
    try:
        resp = requests.get(url, headers=SPORTS_API_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", [])[:5]
    except Exception as e:
        logger.error(f"Error fetching top scorers: {e}")
        return []


def format_fixture(fixture: dict) -> str:
    """Format a single fixture into a readable string."""
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

    score = ""
    if home_goals is not None and away_goals is not None:
        score = f" *{home_goals} - {away_goals}*"

    time_str = f" [{elapsed}']" if elapsed else ""
    league_name = league.get("name", "")

    return f"⚽ {home} vs {away}{score} ({status}{time_str}) — _{league_name}_"


def format_standing(entry: dict, rank: int) -> str:
    team = entry.get("team", {}).get("name", "?")
    pts = entry.get("points", 0)
    played = entry.get("all", {}).get("played", 0)
    won = entry.get("all", {}).get("win", 0)
    drawn = entry.get("all", {}).get("draw", 0)
    lost = entry.get("all", {}).get("lose", 0)
    gd = entry.get("goalsDiff", 0)
    return f"{rank}. *{team}* — {pts} pts ({played}G {won}W {drawn}D {lost}L, GD: {gd:+d})"


async def send_message(text: str):
    """Send a message to the configured chat."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    async with bot:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN
        )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Sports Update Bot*\n\n"
        "Here are the available commands:\n\n"
        "🔴 /live — Show currently live matches\n"
        "📅 /today — Today's scheduled matches\n"
        "🏆 /standings — Top 5 Premier League standings\n"
        "⚽ /scorers — Top scorers in Premier League\n"
        "ℹ️ /help — Show this help message"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching live matches...")
    fixtures = fetch_live_fixtures()

    if not fixtures:
        await update.message.reply_text("⚽ No live matches right now.")
        return

    lines = ["🔴 *Live Matches*\n"]
    for f in fixtures[:15]:
        lines.append(format_fixture(f))

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching today's matches...")
    today = date.today().isoformat()
    fixtures = fetch_fixtures_by_date(today)

    if not fixtures:
        await update.message.reply_text(f"📅 No matches found for today ({today}).")
        return

    lines = [f"📅 *Today's Matches ({today})*\n"]
    for f in fixtures[:20]:
        lines.append(format_fixture(f))

    if len(fixtures) > 20:
        lines.append(f"\n_...and {len(fixtures) - 20} more matches_")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_standings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching Premier League standings...")
    standings = fetch_standings(league_id=39, season=2024)

    if not standings:
        await update.message.reply_text("❌ Could not fetch standings. Try again later.")
        return

    lines = ["🏆 *Premier League Standings (Top 10)*\n"]
    for entry in standings[:10]:
        rank = entry.get("rank", 0)
        lines.append(format_standing(entry, rank))

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_scorers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching top scorers...")
    scorers = fetch_top_scorers(league_id=39, season=2024)

    if not scorers:
        await update.message.reply_text("❌ Could not fetch top scorers. Try again later.")
        return

    lines = ["⚽ *Premier League Top Scorers (2024)*\n"]
    for i, entry in enumerate(scorers, 1):
        player = entry.get("player", {})
        stats = entry.get("statistics", [{}])[0]
        name = player.get("name", "?")
        team = stats.get("team", {}).get("name", "?")
        goals = stats.get("goals", {}).get("total", 0)
        assists = stats.get("goals", {}).get("assists") or 0
        lines.append(f"{i}. *{name}* ({team}) — {goals} goals, {assists} assists")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def send_daily_digest():
    """Send a daily digest of today's matches to the configured chat."""
    today = date.today().isoformat()
    fixtures = fetch_fixtures_by_date(today)

    if not fixtures:
        text = f"📅 *Daily Sports Digest — {today}*\n\nNo matches scheduled today."
    else:
        lines = [f"📅 *Daily Sports Digest — {today}*\n"]
        for f in fixtures[:20]:
            lines.append(format_fixture(f))
        if len(fixtures) > 20:
            lines.append(f"\n_...and {len(fixtures) - 20} more matches today_")
        text = "\n".join(lines)

    await send_message(text)
    logger.info("Daily digest sent.")


def main():
    logger.info("Starting Sports Telegram Bot...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("live", cmd_live))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("standings", cmd_standings))
    app.add_handler(CommandHandler("scorers", cmd_scorers))

    logger.info("Bot is running. Commands: /start /live /today /standings /scorers")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
