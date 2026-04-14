#!/usr/bin/env python3
"""
Röplabda Backtest – SofaScore ingyenes API
Stratégia: Szett fogadás – 3-0 (UNDER 3.5 szett) vs 3-1/3-2 (OVER 2.5 szett)
Csak olyan meccsekre tippel ahol TÉNYLEGESEN VAN odds adat

Ligák: CEV Champions League, olasz, lengyel, francia, török, orosz, brazil top ligák
"""
from __future__ import annotations

import json
import time
import logging
import requests
from datetime import date, timedelta
from collections import defaultdict

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("backtest_volleyball")

SOFASCORE_BASE = "https://www.sofascore.com/api/v1"
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":          "application/json",
    "Referer":         "https://www.sofascore.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":          "https://www.sofascore.com",
}

OUTPUT_FILE = "backtest_raw_volleyball.json"
DAYS_BACK   = 90

TOP_CATEGORIES = {
    "Italy", "Poland", "France", "Turkey", "Brazil", "Russia",
    "Germany", "Spain", "Serbia", "Greece", "Netherlands", "Belgium",
    "Japan", "Argentina", "USA", "International", "South Korea",
    "Slovenia", "Czech Republic", "Romania", "Azerbaijan", "Iran",
    "Finland", "Switzerland", "Sweden", "Portugal", "Croatia",
    "China", "South America",
}


def sofa_get(url: str, retries: int = 3) -> dict:
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return {}
        except Exception as e:
            logger.warning(f"Hiba ({i+1}/{retries}): {e}")
        time.sleep(0.8)
    return {}


def fetch_day(day: date) -> list:
    url  = f"{SOFASCORE_BASE}/sport/volleyball/scheduled-events/{day.isoformat()}"
    data = sofa_get(url)
    events = data.get("events") or []
    results = []

    for ev in events:
        status = (ev.get("status") or {}).get("type", "")
        if status != "finished":
            continue

        tourn = ev.get("tournament") or {}
        cat   = ((tourn.get("category") or {}).get("name") or "")

        if cat not in TOP_CATEGORIES:
            continue

        t_name = (tourn.get("name") or "").lower()
        if any(k in t_name for k in ["u18","u20","u21","u16","youth","junior","beach","sand"]):
            continue

        home = ev.get("homeTeam") or {}
        away = ev.get("awayTeam") or {}

        hs_raw = ev.get("homeScore") or {}
        as_raw = ev.get("awayScore") or {}

        home_sets = hs_raw.get("current")
        away_sets = as_raw.get("current")

        if home_sets is None or away_sets is None:
            continue

        home_sets = int(home_sets)
        away_sets = int(away_sets)
        total_sets = home_sets + away_sets

        # Érvényes szett végeredmény: 3-0, 3-1, 3-2
        if not ((home_sets == 3 and away_sets in [0,1,2]) or
                (away_sets == 3 and home_sets in [0,1,2])):
            continue

        winner = "home" if home_sets > away_sets else "away"

        # Nemi szűrés torna neve alapján
        gender = "women"
        t_lower = (tourn.get("name") or "").lower()
        c_lower = cat.lower()
        if "women" in t_lower or "frauen" in t_lower or "feminin" in t_lower or "ladies" in t_lower or "noi" in t_lower:
            gender = "women"
        elif "men" in t_lower and "women" not in t_lower:
            gender = "men"
        else:
            gender = "men"

        results.append({
            "event_id":   ev.get("id"),
            "date":       day.isoformat(),
            "tournament": tourn.get("name", "?"),
            "category":   cat,
            "gender":     gender,
            "home":       home.get("name", "?"),
            "away":       away.get("name", "?"),
            "home_sets":  home_sets,
            "away_sets":  away_sets,
            "total_sets": total_sets,
            "winner":     winner,
            "score_type": f"{max(home_sets,away_sets)}-{min(home_sets,away_sets)}",
        })

    return results


def analyze(matches: list):
    if not matches:
        print("Nincs elegendő adat!")
        return

    men   = [m for m in matches if m["gender"] == "men"]
    women = [m for m in matches if m["gender"] == "women"]
    days  = DAYS_BACK

    print(f"\n{'='*65}")
    print(f"📊 ALAPSTATISZTIKÁK ({days} nap)")
    print(f"{'='*65}")
    print(f"Összes meccs: {len(matches)}")
    print(f"Férfi: {len(men)} ({len(men)/days:.1f}/nap) | Női: {len(women)} ({len(women)/days:.1f}/nap)")

    for label, grp in [("FÉRFI", men), ("NŐI", women)]:
        if not grp:
            continue
        s30 = sum(1 for m in grp if m["score_type"] == "3-0")
        s31 = sum(1 for m in grp if m["score_type"] == "3-1")
        s32 = sum(1 for m in grp if m["score_type"] == "3-2")
        n   = len(grp)
        print(f"\n{label} ({n} meccs, {n/days:.1f}/nap):")
        print(f"  3-0: {s30} ({s30/n*100:.1f}%)")
        print(f"  3-1: {s31} ({s31/n*100:.1f}%)")
        print(f"  3-2: {s32} ({s32/n*100:.1f}%)")
        print(f"  3 szettig megy (3-0): {s30/n*100:.1f}%")
        print(f"  4 szettig megy (3-1): {s31/n*100:.1f}%")
        print(f"  5 szettig megy (3-2): {s32/n*100:.1f}%")

    def test_strategy(grp, label, win_fn, odds_val):
        wins  = sum(1 for m in grp if win_fn(m))
        total = len(grp)
        if total == 0:
            return
        losses = total - wins
        roi    = ((wins * (odds_val - 1)) - losses) / total * 100
        wr     = wins / total * 100
        status = "✅" if roi > 10 else "⚠️" if roi > 5 else "❌"
        print(f"  {status} {label:55s}: {wins}/{total} ({wr:.1f}%) ROI: {roi:+.1f}%")

    print(f"\n{'='*65}")
    print(f"🏐 STRATÉGIÁK – FÉRFI")
    print(f"{'='*65}")
    for odds in [1.75, 1.80, 1.85, 1.90]:
        test_strategy(men, f"UNDER 3.5 szett (3-0 vége) @{odds}", lambda m: m["score_type"]=="3-0", odds)
    print()
    for odds in [1.75, 1.80, 1.85, 1.90]:
        test_strategy(men, f"UNDER 4.5 szett (3-0/3-1 vége) @{odds}", lambda m: m["score_type"] in ["3-0","3-1"], odds)
    print()
    for odds in [1.75, 1.80, 1.85, 1.90]:
        test_strategy(men, f"OVER 4.5 szett (3-2 vége) @{odds}", lambda m: m["score_type"]=="3-2", odds)

    print(f"\n{'='*65}")
    print(f"🏐 STRATÉGIÁK – NŐI")
    print(f"{'='*65}")
    for odds in [1.75, 1.80, 1.85, 1.90]:
        test_strategy(women, f"UNDER 3.5 szett (3-0 vége) @{odds}", lambda m: m["score_type"]=="3-0", odds)
    print()
    for odds in [1.75, 1.80, 1.85, 1.90]:
        test_strategy(women, f"UNDER 4.5 szett (3-0/3-1 vége) @{odds}", lambda m: m["score_type"] in ["3-0","3-1"], odds)
    print()
    for odds in [1.75, 1.80, 1.85, 1.90]:
        test_strategy(women, f"OVER 4.5 szett (3-2 vége) @{odds}", lambda m: m["score_type"]=="3-2", odds)

    print(f"\n{'='*65}")
    print(f"🏆 TOP LIGÁK (férfi, 3-0 arány)")
    print(f"{'='*65}")
    by_league = defaultdict(list)
    for m in men:
        by_league[m["tournament"]].append(m)
    for league, ms in sorted(by_league.items(), key=lambda x: -len(x[1]))[:15]:
        n   = len(ms)
        s30 = sum(1 for m in ms if m["score_type"]=="3-0")
        s31 = sum(1 for m in ms if m["score_type"]=="3-1")
        s32 = sum(1 for m in ms if m["score_type"]=="3-2")
        roi30 = ((s30*0.85) - (n-s30)) / n * 100
        print(f"  {league:40s}: {n:3d} meccs | 3-0: {s30/n*100:.0f}% | 3-1: {s31/n*100:.0f}% | 3-2: {s32/n*100:.0f}% | ROI@1.85: {roi30:+.1f}%")


def main():
    today = date.today()
    start = today - timedelta(days=DAYS_BACK)
    all_matches = []

    day = start
    while day <= today - timedelta(days=1):
        logger.info(f"Nap: {day} …")
        matches = fetch_day(day)
        all_matches.extend(matches)
        if matches:
            logger.info(f"  → {len(matches)} röplabda meccs")
        day += timedelta(days=1)
        time.sleep(0.4)

    logger.info(f"\n✅ Összesen {len(all_matches)} meccs gyűjtve")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_matches, f, ensure_ascii=False, indent=2)
    logger.info(f"Mentve: {OUTPUT_FILE}")

    analyze(all_matches)
    print(f"\n✅ Backtest kész! ({len(all_matches)} meccs, {DAYS_BACK} nap)")


if __name__ == "__main__":
    main()
