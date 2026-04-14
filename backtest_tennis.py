#!/usr/bin/env python3
"""
Tenisz Backtest – SofaScore ingyenes API
Stratégiák:
  A) Total Games OVER 22.5 – hasonló rangsorú játékosok (diff ≤ 30)
  B) Total Games UNDER 22.5 – nagy rangsor különbség (diff > 80)
  C) Első szett nyerő = mérkőzés nyerő (megbízhatóság elemzés)
  D) Felszín alapú total games átlag

ATP + WTA, utolsó ~150 nap
"""
from __future__ import annotations

import json
import time
import logging
import requests
from datetime import date, timedelta

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("backtest_tennis")

SOFASCORE_BASE = "https://www.sofascore.com/api/v1"
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":          "application/json",
    "Referer":         "https://www.sofascore.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":          "https://www.sofascore.com",
}

OUTPUT_FILE = "backtest_raw_tennis.json"
DAYS_BACK   = 150   # utolsó ~5 hónap

# Tenisz kategóriák (SofaScore category ID-k)
# ATP = 3, WTA = 6, ATP Challengers = 5, Grand Slam = benne van ATP-ben
ALLOWED_CATEGORIES = {"ATP", "WTA", "Grand Slam"}


def sofa_get(url: str, retries: int = 3) -> dict:
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return {}
            logger.warning(f"HTTP {r.status_code}: {url}")
        except Exception as e:
            logger.warning(f"Hiba ({i+1}/{retries}): {e}")
        time.sleep(1.5)
    return {}


def calc_total_games(home_score: dict, away_score: dict) -> int | None:
    """Összes gem kiszámítása a home/away score adatokból.
    Struktúra: homeScore.period1 = home gemek az 1. szettben
               awayScore.period1 = away gemek az 1. szettben
    """
    total = 0
    found = False
    for p in ["period1", "period2", "period3", "period4", "period5"]:
        h = home_score.get(p)
        a = away_score.get(p)
        if h is not None and a is not None:
            total += int(h) + int(a)
            found = True
    return total if found and total > 0 else None


def get_surface(tournament: dict) -> str:
    """Felszín meghatározása a torna alapján."""
    name = (tournament.get("name") or "").lower()
    cat  = ((tournament.get("category") or {}).get("name") or "").lower()
    # Roland Garros → clay, Wimbledon → grass, stb.
    if any(k in name for k in ["clay", "roland", "monte", "madrid", "rome", "barcelona", "hamburg"]):
        return "Clay"
    if any(k in name for k in ["grass", "wimbledon", "halle", "queens", "eastbourne", "s-hertogenbosch"]):
        return "Grass"
    if any(k in name for k in ["indoor", "covered"]):
        return "Indoor Hard"
    return "Hard"


def fetch_day(day: date) -> list:
    """Egy nap befejezett tenisz meccseit gyűjti."""
    url  = f"{SOFASCORE_BASE}/sport/tennis/scheduled-events/{day.isoformat()}"
    data = sofa_get(url)
    events = data.get("events") or []
    results = []

    for ev in events:
        # Csak befejezett meccsek
        status = (ev.get("status") or {}).get("type", "")
        if status != "finished":
            continue

        tourn = ev.get("tournament") or {}
        cat   = (tourn.get("category") or {}).get("name") or ""

        # Szűrés: csak ATP / WTA / Grand Slam
        is_atp = "atp" in cat.lower() or "grand slam" in cat.lower()
        is_wta = "wta" in cat.lower()
        if not is_atp and not is_wta:
            continue

        home = ev.get("homeTeam") or {}
        away = ev.get("awayTeam") or {}

        home_ranking = (home.get("playerTeamInfo") or {}).get("ranking") or \
                       home.get("ranking") or None
        away_ranking = (away.get("playerTeamInfo") or {}).get("ranking") or \
                       away.get("ranking") or None

        home_score_raw = ev.get("homeScore") or {}
        away_score_raw = ev.get("awayScore") or {}

        # Szett eredmények
        home_sets = home_score_raw.get("current")
        away_sets = away_score_raw.get("current")

        # Gemek kiszámítása (flat period1/period2/... mezők)
        total_games = calc_total_games(home_score_raw, away_score_raw)

        score = home_score_raw

        surface = get_surface(tourn)
        tour    = "ATP" if is_atp else "WTA"

        rec = {
            "event_id":       ev.get("id"),
            "date":           day.isoformat(),
            "tournament":     tourn.get("name", "?"),
            "category":       cat,
            "tour":           tour,
            "surface":        surface,
            "home_player":    home.get("name", "?"),
            "away_player":    away.get("name", "?"),
            "home_ranking":   home_ranking,
            "away_ranking":   away_ranking,
            "home_sets":      home_sets,
            "away_sets":      away_sets,
            "total_games":    total_games,
            "winner":         "home" if (home_sets or 0) > (away_sets or 0) else "away",
        }
        results.append(rec)

    return results


# ─── Főprogram ─────────────────────────────────────────────────────────────────

def main():
    today   = date.today()
    start   = today - timedelta(days=DAYS_BACK)
    all_matches = []

    # Napi betöltés
    day = start
    while day <= today - timedelta(days=1):
        logger.info(f"Nap: {day} …")
        matches = fetch_day(day)
        all_matches.extend(matches)
        logger.info(f"  → {len(matches)} befejezett mérkőzés")
        day += timedelta(days=1)
        time.sleep(0.6)

    logger.info(f"\n✅ Összesen {len(all_matches)} meccs gyűjtve")

    # Mentés
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_matches, f, ensure_ascii=False, indent=2)
    logger.info(f"Mentve: {OUTPUT_FILE}")

    # ─── Elemzés ────────────────────────────────────────────────────────────────

    valid = [m for m in all_matches if m["total_games"] is not None and m["total_games"] > 0]
    logger.info(f"Elemzésre alkalmas (van total_games): {len(valid)} meccs")

    if not valid:
        logger.warning("Nincs elemezhető adat!")
        return

    # Átlagok felszín szerint
    print("\n" + "="*60)
    print("📊 TOTAL GAMES ÁTLAG FELSZÍN ÉS TORNA SZERINT")
    print("="*60)
    from collections import defaultdict
    surf_games = defaultdict(list)
    tour_games = defaultdict(list)
    for m in valid:
        surf_games[m["surface"]].append(m["total_games"])
        tour_games[m["tour"]].append(m["total_games"])

    for surf, games in sorted(surf_games.items()):
        avg = sum(games) / len(games)
        over = sum(1 for g in games if g > 22.5)
        pct  = over / len(games) * 100
        print(f"  {surf:15s}: átlag {avg:.1f} gem | OVER 22.5: {over}/{len(games)} ({pct:.1f}%)")

    print()
    for tour, games in sorted(tour_games.items()):
        avg = sum(games) / len(games)
        over = sum(1 for g in games if g > 22.5)
        pct  = over / len(games) * 100
        print(f"  {tour:6s}: átlag {avg:.1f} gem | OVER 22.5: {over}/{len(games)} ({pct:.1f}%)")

    # ─── Stratégia A: OVER 22.5 – hasonló rangsorú játékosok ────────────────────
    print("\n" + "="*60)
    print("🅐 STRATÉGIA: OVER 22.5 – hasonló rangsor (diff ≤ 30)")
    print("="*60)
    _test_strategy(
        matches=valid,
        condition=lambda m: (
            m["home_ranking"] and m["away_ranking"] and
            abs(m["home_ranking"] - m["away_ranking"]) <= 30
        ),
        win_cond=lambda m: m["total_games"] > 22.5,
        odds=1.85,
        label="OVER 22.5"
    )

    # ─── Stratégia B: UNDER 22.5 – nagy rangsor különbség ───────────────────────
    print("\n" + "="*60)
    print("🅑 STRATÉGIA: UNDER 22.5 – nagy rangsor különbség (diff > 80)")
    print("="*60)
    _test_strategy(
        matches=valid,
        condition=lambda m: (
            m["home_ranking"] and m["away_ranking"] and
            abs(m["home_ranking"] - m["away_ranking"]) > 80
        ),
        win_cond=lambda m: m["total_games"] < 22.5,
        odds=1.85,
        label="UNDER 22.5"
    )

    # ─── Stratégia C: OVER 21.5 ATP ─────────────────────────────────────────────
    print("\n" + "="*60)
    print("🅒 STRATÉGIA: ATP OVER 21.5 – Agyag + kemény, hasonló rangsor (diff ≤ 50)")
    print("="*60)
    _test_strategy(
        matches=valid,
        condition=lambda m: (
            m["tour"] == "ATP" and
            m["surface"] in ("Clay", "Hard") and
            m["home_ranking"] and m["away_ranking"] and
            abs(m["home_ranking"] - m["away_ranking"]) <= 50
        ),
        win_cond=lambda m: m["total_games"] > 21.5,
        odds=1.80,
        label="OVER 21.5"
    )

    # ─── Stratégia D: WTA UNDER 21.5 – nagy különbség ───────────────────────────
    print("\n" + "="*60)
    print("🅓 STRATÉGIA: WTA UNDER 21.5 – nagy rangsor különbség (diff > 60)")
    print("="*60)
    _test_strategy(
        matches=valid,
        condition=lambda m: (
            m["tour"] == "WTA" and
            m["home_ranking"] and m["away_ranking"] and
            abs(m["home_ranking"] - m["away_ranking"]) > 60
        ),
        win_cond=lambda m: m["total_games"] < 21.5,
        odds=1.85,
        label="UNDER 21.5"
    )

    # ─── Stratégia E: OVER 22.5 top játékosok (mindkettő top 50) ────────────────
    print("\n" + "="*60)
    print("🅔 STRATÉGIA: OVER 22.5 – mindkét játékos top 50")
    print("="*60)
    _test_strategy(
        matches=valid,
        condition=lambda m: (
            m["home_ranking"] and m["away_ranking"] and
            m["home_ranking"] <= 50 and m["away_ranking"] <= 50
        ),
        win_cond=lambda m: m["total_games"] > 22.5,
        odds=1.85,
        label="OVER 22.5"
    )

    print("\n✅ Backtest kész!")
    print(f"📁 Nyers adat: {OUTPUT_FILE} ({len(all_matches)} meccs)")


def _test_strategy(matches, condition, win_cond, odds, label):
    filtered = [m for m in matches if condition(m)]
    if not filtered:
        print(f"  Nincs elegendő mérkőzés")
        return

    wins   = sum(1 for m in filtered if win_cond(m))
    losses = len(filtered) - wins
    win_rate = wins / len(filtered) * 100
    roi = ((wins * (odds - 1)) - losses) / len(filtered) * 100

    print(f"  Tippek száma : {len(filtered)}")
    print(f"  Találat      : {wins} ({win_rate:.1f}%)")
    print(f"  Veszett      : {losses}")
    print(f"  Feltételezett szorzó: {odds}")
    print(f"  ROI          : {roi:+.2f}%")
    print(f"  Értékelés    : {'✅ Nyereséges!' if roi > 5 else '⚠️ Marginális' if roi > 0 else '❌ Veszteséges'}")

    # Felszín bontás
    from collections import defaultdict
    by_surf = defaultdict(list)
    for m in filtered:
        by_surf[m["surface"]].append(win_cond(m))
    print(f"  Felszín bontás ({label}):")
    for surf, results in sorted(by_surf.items()):
        w = sum(results)
        pct = w / len(results) * 100
        r   = ((w * (odds - 1)) - (len(results) - w)) / len(results) * 100
        print(f"    {surf:15s}: {w}/{len(results)} ({pct:.1f}%) | ROI: {r:+.1f}%")


if __name__ == "__main__":
    main()
