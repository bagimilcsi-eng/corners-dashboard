import os
import logging
import requests
from datetime import datetime, date, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}

FOOTBALL_API_BASE = "https://v3.football.api-sports.io"
FOOTBALL_API_HEADERS = {"x-apisports-key": os.environ.get("SPORTS_API_KEY", "")}

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


def sofascore_fetch_h2h(event_id: int) -> list:
    """H2H meccsek lekérése egy meccs ID alapján."""
    url = f"https://www.sofascore.com/api/v1/event/{event_id}/h2h/events"
    try:
        resp = requests.get(url, headers=SOFASCORE_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("events", [])
    except Exception as e:
        logger.error(f"H2H fetch hiba: {e}")
        return []


def is_allowed(event: dict) -> bool:
    """Csak Setka Cup és Czech Liga meccsek."""
    t = event.get("tournament", {})
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
        dt = datetime.utcfromtimestamp(ts)
        time_str = f" [{dt.strftime('%H:%M')} UTC]"
    score_part = f" {score}" if score else ""
    return f"🏓 {home} vs {away}{score_part}{time_str} — _{league}_"


# ─────────────────────────────────────────────
#  TIPP ELEMZŐ LOGIKA
# ─────────────────────────────────────────────

def analyze_h2h_events(h2h_events: list, home_name: str) -> tuple[int, int]:
    """H2H elemzés: (home győzelmek, összes befejezett meccs)."""
    home_wins = 0
    total = 0
    for e in h2h_events:
        if e.get("status", {}).get("type", "").lower() != "finished":
            continue
        hs = (e.get("homeScore", {}).get("current") or 0)
        as_ = (e.get("awayScore", {}).get("current") or 0)
        h = e.get("homeTeam", {}).get("name", "")
        total += 1
        if home_name.lower() in h.lower() and hs > as_:
            home_wins += 1
        elif home_name.lower() not in h.lower() and as_ > hs:
            home_wins += 1
    return home_wins, total


def get_player_recent_form(player_name: str, all_events: list, last: int = 10) -> tuple[int, int]:
    """Játékos legutóbbi formájának kiszámítása a mai adatokból."""
    wins = 0
    total = 0
    for e in all_events:
        if e.get("status", {}).get("type", "").lower() != "finished":
            continue
        home = e.get("homeTeam", {}).get("name", "")
        away = e.get("awayTeam", {}).get("name", "")
        if player_name.lower() not in home.lower() and player_name.lower() not in away.lower():
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


def calculate_tip(
    h2h_home_wins: int, h2h_total: int,
    home_form_wins: int, home_form_total: int,
    away_form_wins: int, away_form_total: int,
) -> tuple[str, str, float]:
    """Tipp kiszámítása pontozással. Visszaad: (winner, bizalom, score)."""
    score = 0.0

    # H2H súly (max ±40 pont, min 3 meccs kell)
    if h2h_total >= 3:
        h2h_rate = (h2h_home_wins / h2h_total) - 0.5
        score += h2h_rate * 40

    # Forma súly (max ±30 pont)
    home_rate = (home_form_wins / home_form_total) if home_form_total > 0 else 0.5
    away_rate = (away_form_wins / away_form_total) if away_form_total > 0 else 0.5
    score += (home_rate - away_rate) * 30

    if score >= 15:
        return "home", "🟢 Erős tipp", score
    elif score >= 6:
        return "home", "🟡 Közepes tipp", score
    elif score <= -15:
        return "away", "🟢 Erős tipp", score
    elif score <= -6:
        return "away", "🟡 Közepes tipp", score
    else:
        return "uncertain", "🔴 Bizonytalan", score


def build_tip_message(event: dict, all_events: list) -> str:
    """Tipp üzenet összeállítása egy meccshez."""
    home = event.get("homeTeam", {}).get("name", "?")
    away = event.get("awayTeam", {}).get("name", "?")
    event_id = event.get("id")
    league = event.get("tournament", {}).get("name", "?")
    category = event.get("tournament", {}).get("category", {}).get("name", "")
    ts = event.get("startTimestamp")
    time_str = datetime.utcfromtimestamp(ts).strftime("%H:%M UTC") if ts else "?"

    # H2H
    h2h_home_wins, h2h_total = 0, 0
    if event_id:
        h2h_events = sofascore_fetch_h2h(event_id)
        h2h_home_wins, h2h_total = analyze_h2h_events(h2h_events, home)

    # Forma
    home_w, home_t = get_player_recent_form(home, all_events)
    away_w, away_t = get_player_recent_form(away, all_events)

    winner, confidence, score = calculate_tip(
        h2h_home_wins, h2h_total,
        home_w, home_t,
        away_w, away_t,
    )

    if winner == "home":
        tip_str = f"🏓 *Tipp: {home}* győz"
    elif winner == "away":
        tip_str = f"🏓 *Tipp: {away}* győz"
    else:
        tip_str = "🏓 *Tipp: Nagyon szoros meccs*"

    h2h_str = f"{h2h_home_wins}–{h2h_total - h2h_home_wins}" if h2h_total >= 2 else "nincs elég adat"

    return "\n".join([
        f"🔶 *{home}* vs *{away}*",
        f"📍 {league} ({category}) | 🕐 {time_str}",
        f"",
        f"🔁 H2H: {h2h_str}",
        f"📈 Forma: {home} {form_bar(home_w, home_t)} | {away} {form_bar(away_w, away_t)}",
        f"",
        f"{tip_str}",
        f"{confidence} (pontszám: {score:+.1f})",
    ])


# ─────────────────────────────────────────────
#  TELEGRAM PARANCSOK – ASZTALITENISZ
# ─────────────────────────────────────────────

async def cmd_tt_tippek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Setka Cup és Czech Liga közelgő meccsek tippjei."""
    await update.message.reply_text(
        "🔍 *Setka Cup & Czech Liga* — meccsek elemzése...",
        parse_mode=ParseMode.MARKDOWN
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
        await update.message.reply_text("❌ Nem találtam Setka Cup vagy Czech Liga meccseket ma/holnap.")
        return

    if used_date != date.today().isoformat():
        await update.message.reply_text(f"ℹ️ Ma nincs meccs — holnapi ({used_date}) meccseket elemzem...")

    # Csak a még nem befejezett meccsek
    upcoming = [e for e in filtered_events if e.get("status", {}).get("type", "").lower() == "notstarted"]
    if not upcoming:
        upcoming = [e for e in filtered_events if e.get("status", {}).get("type", "").lower() != "finished"]
    if not upcoming:
        upcoming = filtered_events

    league_names = sorted({e.get("tournament", {}).get("name", "?") for e in upcoming})
    await update.message.reply_text(
        f"📊 *{min(len(upcoming), 8)} meccs* elemzése — {', '.join(league_names[:3])}\n_(kérlek várj...)_",
        parse_mode=ParseMode.MARKDOWN
    )

    sent = 0
    for event in upcoming[:8]:
        try:
            msg = build_tip_message(event, events)
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception as e:
            logger.error(f"Tipp hiba: {e}")

    if sent == 0:
        await update.message.reply_text("❌ Nem sikerült tippet generálni. Próbáld újra.")
    else:
        await update.message.reply_text(
            f"✅ *{sent} tipp generálva!*\n\n"
            "⚠️ _A tippek statisztikai elemzésen alapulnak. Felelősen fogadj!_",
            parse_mode=ParseMode.MARKDOWN
        )


async def cmd_tt_elo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Élő Setka Cup / Czech Liga meccsek."""
    await update.message.reply_text("🔍 Élő meccsek keresése...")
    today = date.today().isoformat()
    all_events = sofascore_fetch_events(today)
    live = [e for e in all_events
            if is_allowed(e) and e.get("status", {}).get("type", "").lower() == "inprogress"]

    if not live:
        total_live = [e for e in all_events if e.get("status", {}).get("type", "").lower() == "inprogress"]
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
        await update.message.reply_text(f"📅 Ma ({today}) nincs Setka Cup / Czech Liga meccs.")
        return

    # Csoportosítás liga szerint
    leagues: dict[str, list] = {}
    for e in events:
        name = e.get("tournament", {}).get("name", "?")
        leagues.setdefault(name, []).append(e)

    total = len(events)
    lines = [f"📅 *Mai meccsek — Setka Cup / Czech Liga ({today})*\n*Összesen: {total} meccs*\n"]

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
    finished = [e for e in all_events
                if is_allowed(e) and e.get("status", {}).get("type", "").lower() == "finished"]

    if not finished:
        await update.message.reply_text("❌ Ma még nincs befejezett Setka Cup / Czech Liga meccs.")
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
    events = [e for e in all_events
              if is_allowed(e) and e.get("status", {}).get("type", "").lower() == "finished"]

    if not events:
        await update.message.reply_text("❌ Nincs elég befejezett meccs a ranglista összeállításához.")
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
        await update.message.reply_text("❌ Nincs elég adat a ranglistához (min. 3 meccs szükséges).")
        return

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
#  LABDARÚGÁS
# ─────────────────────────────────────────────

def fetch_live_football():
    try:
        resp = requests.get(f"{FOOTBALL_API_BASE}/fixtures", headers=FOOTBALL_API_HEADERS,
                            params={"live": "all"}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("response", [])
    except Exception as e:
        logger.error(f"Live football hiba: {e}")
        return []


def fetch_football_today():
    today = date.today().isoformat()
    try:
        resp = requests.get(f"{FOOTBALL_API_BASE}/fixtures", headers=FOOTBALL_API_HEADERS,
                            params={"date": today, "timezone": "UTC"}, timeout=10)
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
        "/tt\\_ranglista — Legjobb játékosok ma\n\n"
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
#  FŐPROGRAM
# ─────────────────────────────────────────────

def main():
    logger.info("Sports Bot indul...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # Asztalitenisz
    app.add_handler(CommandHandler("tt_tippek", cmd_tt_tippek))
    app.add_handler(CommandHandler("tt_elo", cmd_tt_elo))
    app.add_handler(CommandHandler("tt_mai", cmd_tt_mai))
    app.add_handler(CommandHandler("tt_eredmenyek", cmd_tt_eredmenyek))
    app.add_handler(CommandHandler("tt_ranglista", cmd_tt_ranglista))

    # Labdarúgás
    app.add_handler(CommandHandler("live", cmd_live))
    app.add_handler(CommandHandler("mai", cmd_mai_foci))

    logger.info("Bot fut. Asztalitenisz: SofaScore API (Setka Cup + Czech Liga)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
