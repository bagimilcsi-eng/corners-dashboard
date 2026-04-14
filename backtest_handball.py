#!/usr/bin/env python3
"""
Kézilabda Backtest – SofaScore ingyenes API
Stratégia: O/U gól – csapat forma alapján
Csak olyan meccsekre tippel ahol TÉNYLEGESEN VAN odds adat (fogadható)

Ligák: Bundesliga, Liga ASOBAL, Starligue, EHF Champions League,
       SEHA, NLB, dán, norvég, svéd bajnokság
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
logger = logging.getLogger("backtest_handball")

SOFASCORE_BASE = "https://www.sofascore.com/api/v1"
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":          "application/json",
    "Referer":         "https://www.sofascore.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":          "https://www.sofascore.com",
}

OUTPUT_FILE = "backtest_raw_handball.json"
DAYS_BACK   = 90

# Top kézilabda ligák SofaScore category name szerint
TOP_LEAGUES = {
    "Germany", "Spain", "France", "Denmark", "Norway", "Sweden",
    "EHF Champions League", "EHF European League", "SEHA",
    "Croatia", "Slovenia", "Austria", "Poland", "Portugal",
    "Hungary", "Romania", "Russia", "International",
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
        time.sleep(1.0)
    return {}


def fetch_odds(event_id: int) -> dict | None:
    """
    SofaScore odds lekérdezés – csak ha ténylegesen elérhető.
    Visszatér: {'over': float, 'under': float, 'line': float} vagy None
    """
    data = sofa_get(f"{SOFASCORE_BASE}/event/{event_id}/odds/1/all")
    markets = data.get("markets") or []
    for mkt in markets:
        name = (mkt.get("marketName") or mkt.get("name") or "").lower()
        if not any(k in name for k in ["total", "over", "under", "goals", "gól"]):
            continue
        choices = mkt.get("choices") or []
        over_ch  = next((c for c in choices if (c.get("name") or "").lower().startswith("over")), None)
        under_ch = next((c for c in choices if (c.get("name") or "").lower().startswith("under")), None)
        if not over_ch or not under_ch:
            continue
        # Szorzó kinyerése
        def parse_odds(ch):
            frac = ch.get("fractionalValue") or ""
            try:
                if "/" in str(frac):
                    a, b = str(frac).split("/")
                    return round(int(a) / int(b) + 1, 2)
            except Exception:
                pass
            try:
                return float(ch.get("decimalValue") or ch.get("price") or 0)
            except Exception:
                return None
        over_odds  = parse_odds(over_ch)
        under_odds = parse_odds(under_ch)
        # Vonal kinyerése a choice nevéből (pl. "Over 52.5")
        try:
            line = float((over_ch.get("name") or "").lower().replace("over", "").strip())
        except Exception:
            line = None
        if over_odds and under_odds and over_odds > 1.3 and under_odds > 1.3:
            return {"over": over_odds, "under": under_odds, "line": line}
    return None


def fetch_day(day: date) -> list:
    url  = f"{SOFASCORE_BASE}/sport/handball/scheduled-events/{day.isoformat()}"
    data = sofa_get(url)
    events = data.get("events") or []
    results = []

    for ev in events:
        status = (ev.get("status") or {}).get("type", "")
        if status != "finished":
            continue

        tourn = ev.get("tournament") or {}
        cat   = ((tourn.get("category") or {}).get("name") or "")

        # Szűrés: csak top ligák
        if cat not in TOP_LEAGUES:
            continue

        # Páros/cup szűrés mellőzve – minden single match jön
        t_name = (tourn.get("name") or "").lower()
        if any(k in t_name for k in ["u18", "u20", "u21", "u16", "youth", "junior", "women u"]):
            continue

        home = ev.get("homeTeam") or {}
        away = ev.get("awayTeam") or {}

        hs_raw = ev.get("homeScore") or {}
        as_raw = ev.get("awayScore") or {}
        home_goals = hs_raw.get("current")
        away_goals = as_raw.get("current")

        if home_goals is None or away_goals is None:
            continue

        total_goals = int(home_goals) + int(away_goals)

        results.append({
            "event_id":    ev.get("id"),
            "date":        day.isoformat(),
            "tournament":  tourn.get("name", "?"),
            "category":    cat,
            "gender":      "women" if "women" in (tourn.get("name") or "").lower() or "women" in cat.lower() else "men",
            "home":        home.get("name", "?"),
            "away":        away.get("name", "?"),
            "home_goals":  int(home_goals),
            "away_goals":  int(away_goals),
            "total_goals": total_goals,
            "winner":      "home" if home_goals > away_goals else "away" if away_goals > home_goals else "draw",
        })

    return results


def check_odds_availability(matches: list, sample: int = 30) -> dict:
    """
    Ellenőrzi, hogy a meccsek hány %-ánál volt elérhető odds.
    Sample: csak az első N meccs (nem akarjuk az összes lekérdezni)
    """
    sample_matches = matches[:sample]
    available = 0
    lines_found = []

    for m in sample_matches:
        odds = fetch_odds(m["event_id"])
        if odds:
            available += 1
            if odds["line"]:
                lines_found.append(odds["line"])
        time.sleep(0.5)

    return {
        "checked": len(sample_matches),
        "available": available,
        "pct": available / len(sample_matches) * 100 if sample_matches else 0,
        "common_lines": sorted(set(lines_found)),
        "avg_line": sum(lines_found) / len(lines_found) if lines_found else None,
    }


def analyze(matches: list):
    if not matches:
        logger.warning("Nincs adat az elemzéshez!")
        return

    men   = [m for m in matches if m["gender"] == "men"]
    women = [m for m in matches if m["gender"] == "women"]

    print(f"\n{'='*60}")
    print(f"📊 ALAPSTATISZTIKÁK")
    print(f"{'='*60}")
    print(f"Összes meccs: {len(matches)} (Férfi: {len(men)}, Női: {len(women)})")

    for label, grp in [("FÉRFI", men), ("NŐI", women)]:
        if not grp:
            continue
        goals = [m["total_goals"] for m in grp]
        avg   = sum(goals) / len(goals)
        print(f"\n{label} – átlag gól: {avg:.1f} | min: {min(goals)} | max: {max(goals)}")
        for line in [47.5, 49.5, 51.5, 53.5, 55.5, 57.5, 59.5]:
            over  = sum(1 for g in goals if g > line)
            under = sum(1 for g in goals if g < line)
            pct   = over / len(goals) * 100
            print(f"  OVER {line}: {over}/{len(goals)} ({pct:.1f}%) | UNDER: {under}/{len(goals)} ({100-pct:.1f}%)")

    # Stratégiák tesztelése
    def test(grp, label, win_cond, odds_val):
        wins  = sum(1 for m in grp if win_cond(m))
        total = len(grp)
        if total == 0:
            return
        losses = total - wins
        roi    = ((wins * (odds_val - 1)) - losses) / total * 100
        wr     = wins / total * 100
        status = "✅" if roi > 10 else "⚠️" if roi > 5 else "❌"
        print(f"  {status} {label:50s}: {wins}/{total} ({wr:.1f}%) ROI: {roi:+.1f}%")

    print(f"\n{'='*60}")
    print(f"🅐 FÉRFI – STRATÉGIÁK")
    print(f"{'='*60}")
    for line, odds_o, odds_u in [(49.5,1.85,1.85),(51.5,1.85,1.85),(53.5,1.85,1.85),(55.5,1.90,1.85),(57.5,1.95,1.80)]:
        test(men, f"OVER {line}  @{odds_o}", lambda m, l=line: m["total_goals"] > l, odds_o)
        test(men, f"UNDER {line} @{odds_u}", lambda m, l=line: m["total_goals"] < l, odds_u)

    print(f"\n{'='*60}")
    print(f"🅑 NŐI – STRATÉGIÁK")
    print(f"{'='*60}")
    for line, odds_o, odds_u in [(43.5,1.85,1.85),(45.5,1.85,1.85),(47.5,1.85,1.85),(49.5,1.85,1.85),(51.5,1.90,1.80)]:
        test(women, f"OVER {line}  @{odds_o}", lambda m, l=line: m["total_goals"] > l, odds_o)
        test(women, f"UNDER {line} @{odds_u}", lambda m, l=line: m["total_goals"] < l, odds_u)

    # Liga bontás
    print(f"\n{'='*60}")
    print(f"🅒 TOP LIGÁK ÁTLAG GÓL ÉS OVER/UNDER ARÁNY")
    print(f"{'='*60}")
    by_league = defaultdict(list)
    for m in men:
        by_league[m["tournament"]].append(m["total_goals"])
    for league, goals in sorted(by_league.items(), key=lambda x: -len(x[1]))[:12]:
        avg = sum(goals) / len(goals)
        o51 = sum(1 for g in goals if g > 51.5)
        u51 = len(goals) - o51
        print(f"  {league:40s}: {len(goals):3d} meccs | átlag {avg:.1f} | O51.5: {o51/len(goals)*100:.0f}% | U51.5: {u51/len(goals)*100:.0f}%")


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
            logger.info(f"  → {len(matches)} kézilabda meccs")
        day += timedelta(days=1)
        time.sleep(0.4)

    logger.info(f"\n✅ Összesen {len(all_matches)} meccs gyűjtve")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_matches, f, ensure_ascii=False, indent=2)
    logger.info(f"Mentve: {OUTPUT_FILE}")

    # Odds elérhetőség ellenőrzés (30 meccs mintán)
    logger.info("Odds elérhetőség ellenőrzése (30 meccs minta)...")
    odds_info = check_odds_availability(all_matches, sample=30)
    print(f"\n{'='*60}")
    print(f"💰 ODDS ELÉRHETŐSÉG")
    print(f"{'='*60}")
    print(f"  Ellenőrzött meccsek: {odds_info['checked']}")
    print(f"  Elérhető odds: {odds_info['available']} ({odds_info['pct']:.1f}%)")
    print(f"  Talált vonalak: {odds_info['common_lines']}")
    print(f"  Átlag vonal: {odds_info['avg_line']}")

    analyze(all_matches)
    print(f"\n✅ Backtest kész! ({len(all_matches)} meccs, {DAYS_BACK} nap)")


if __name__ == "__main__":
    main()
