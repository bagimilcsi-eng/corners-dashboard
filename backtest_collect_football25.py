#!/usr/bin/env python3
"""
backtest_collect_football25.py — Football O/U 2.5 backtest adatgyűjtő (180 nap)
API-Football alapú. Elmenti az összes qualifying meccs adatát offline optimalizáláshoz.
"""
import os, time, json, requests
from datetime import datetime, date, timedelta
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

HEADERS = {
    "x-rapidapi-key":  os.environ.get("SPORTS_API_KEY", ""),
    "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
}
BASE    = "https://api-football-v1.p.rapidapi.com/v3"
DELAY         = 1.5     # másodperc hívások között
DELAY_429     = 90      # 429 után ennyi mp szünet
MAX_RETRIES   = 6       # max újrapróbálkozás hívásanként
OUTPUT        = "backtest_raw_football25.json"
CHECKPOINT    = "backtest_ckpt_football25.json"  # folytathatóság

BACKTEST_DAYS = 180
_now  = datetime.utcnow()
SEASON = _now.year - 1 if _now.month < 7 else _now.year

ALLOWED_LEAGUE_IDS = {
    2,3,848,39,40,41,135,136,78,79,61,62,140,141,
    88,94,144,179,203,197,218,235,307,253,71,72,
    128,130,383,113,119,103,
}


def api_get(endpoint: str, params: dict = {}) -> dict:
    for attempt in range(MAX_RETRIES):
        time.sleep(DELAY)
        try:
            r = requests.get(f"{BASE}/{endpoint}", headers=HEADERS, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 403):
                wait = DELAY_429 * (attempt + 1)
                print(f"  [LIMIT] HTTP {r.status_code} — várok {wait}s ({attempt+1}/{MAX_RETRIES})...", flush=True)
                time.sleep(wait)
                continue
            print(f"  [WARN] HTTP {r.status_code} — {endpoint}", flush=True)
            return {}
        except Exception as e:
            print(f"  [ERR] {endpoint}: {e}", flush=True)
            time.sleep(10)
    print(f"  [SKIP] {endpoint} — max retry elérve", flush=True)
    return {}


def fetch_fixtures_by_date(date_str: str) -> list:
    data = api_get("fixtures", {"date": date_str, "status": "FT"})
    return [
        fx for fx in data.get("response", [])
        if fx.get("league", {}).get("id") in ALLOWED_LEAGUE_IDS
    ]


def fetch_h2h(h2h_key: str) -> list:
    data = api_get("fixtures/headtohead", {"h2h": h2h_key, "last": 14, "status": "FT"})
    finished = [
        m for m in data.get("response", [])
        if m.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")
    ]
    return finished[:7]


def fetch_team_form(team_id: int, venue: str, last: int = 10) -> list:
    data = api_get("fixtures", {
        "team": team_id, "season": SEASON,
        "venue": venue, "last": last, "status": "FT",
    })
    return data.get("response", [])


def fetch_odds(fixture_id: int) -> dict | None:
    data = api_get("odds", {"fixture": fixture_id, "bet": 5})
    bookmakers = data.get("response", [{}])[0].get("bookmakers", []) if data.get("response") else []
    over_odds, under_odds, bk_count = [], [], 0
    for bk in bookmakers:
        for bet in bk.get("bets", []):
            if bet.get("id") == 5 or "over" in bet.get("name", "").lower():
                values = bet.get("values", [])
                o = u = None
                for v in values:
                    val = v.get("value", "").lower()
                    try:
                        odd = float(v.get("odd", 0))
                    except (ValueError, TypeError):
                        continue
                    if odd <= 1:
                        continue
                    if "over 2.5" in val or val == "over":
                        o = odd
                    elif "under 2.5" in val or val == "under":
                        u = odd
                if o and u:
                    over_odds.append(o); under_odds.append(u); bk_count += 1
                break
    if bk_count < 2:
        return None
    return {
        "over":  round(sum(over_odds) / len(over_odds), 3),
        "under": round(sum(under_odds) / len(under_odds), 3),
        "bk_count": bk_count,
    }


def parse_stats(matches: list) -> dict:
    over = ht_goal = total_goals = count = 0
    for m in matches:
        goals = m.get("goals", {})
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None or ag is None:
            continue
        total = hg + ag
        total_goals += total; count += 1
        if total > 2.5:
            over += 1
        ht = m.get("score", {}).get("halftime", {})
        if ht.get("home") is not None and ht.get("away") is not None:
            if (ht["home"] + ht["away"]) > 0:
                ht_goal += 1
    if count == 0:
        return {"over_rate": None, "ht_rate": None, "count": 0}
    return {
        "over_rate": round(over / count, 4),
        "ht_rate":   round(ht_goal / count, 4),
        "count":     count,
    }


def load_checkpoint() -> tuple[list, set]:
    if os.path.exists(CHECKPOINT):
        try:
            with open(CHECKPOINT, encoding="utf-8") as f:
                data = json.load(f)
            events = data.get("events", [])
            done   = set(data.get("done_dates", []))
            print(f"  [CKPT] Folytatás: {len(events)} meccs, {len(done)} kész nap", flush=True)
            return events, done
        except Exception:
            pass
    return [], set()


def save_checkpoint(events: list, done_dates: set):
    with open(CHECKPOINT, "w", encoding="utf-8") as f:
        json.dump({"events": events, "done_dates": list(done_dates)}, f, ensure_ascii=False)


def wait_for_night_window():
    """Csak 01:00–05:30 UTC között fut (éjjel), hogy ne eszik a bot kvótájából."""
    NIGHT_START = 1     # 01:00 UTC
    NIGHT_END   = 5     # 05:30 UTC
    while True:
        h = datetime.utcnow().hour
        m = datetime.utcnow().minute
        utc_minutes = h * 60 + m
        start_min   = NIGHT_START * 60
        end_min     = NIGHT_END   * 60 + 30
        if start_min <= utc_minutes <= end_min:
            return
        next_start = datetime.utcnow().replace(hour=NIGHT_START, minute=0, second=0, microsecond=0)
        if datetime.utcnow() >= next_start.replace(hour=NIGHT_END, minute=30):
            next_start += timedelta(days=1)
        wait_sec = (next_start - datetime.utcnow()).total_seconds()
        print(f"  [WINDOW] Most {h:02d}:{m:02d} UTC — várok {wait_sec/3600:.1f}h-t (01:00 UTC-ig)...", flush=True)
        time.sleep(min(wait_sec, 3600))


def main():
    today = date.today()
    period_start = today - timedelta(days=BACKTEST_DAYS)

    all_events, done_dates = load_checkpoint()

    print(f"[FOOTBALL25 COLLECT] {period_start} → {today - timedelta(days=1)}", flush=True)
    print(f"  Liga whitelist: {len(ALLOWED_LEAGUE_IDS)} liga | Output: {OUTPUT}", flush=True)
    print(f"  API delay: {DELAY}s | 429 backoff: {DELAY_429}s", flush=True)
    print(f"  Éjjeli ablak: 01:00–05:30 UTC (hogy a bot napközben kapjon kvótát)\n", flush=True)

    for day_idx in range(BACKTEST_DAYS, 0, -1):
        target_date = today - timedelta(days=day_idx)
        date_str    = target_date.strftime("%Y-%m-%d")

        if date_str in done_dates:
            print(f"   {date_str} | [SKIP] már feldolgozva", flush=True)
            continue

        wait_for_night_window()
        fixtures = fetch_fixtures_by_date(date_str)
        day_count = 0

        for fx in fixtures:
            fd       = fx.get("fixture", {})
            teams    = fx.get("teams", {})
            goals    = fx.get("goals", {})
            home_id  = teams.get("home", {}).get("id")
            away_id  = teams.get("away", {}).get("id")
            hg       = goals.get("home")
            ag       = goals.get("away")

            if not home_id or not away_id or hg is None or ag is None:
                continue

            actual_over = (hg + ag) > 2.5

            # H2H
            h2h_matches = fetch_h2h(f"{home_id}-{away_id}")
            h2h_stats   = parse_stats(h2h_matches)

            # Forma
            home_matches = fetch_team_form(home_id, "home")
            home_stats   = parse_stats(home_matches)
            away_matches = fetch_team_form(away_id, "away")
            away_stats   = parse_stats(away_matches)

            # Min forma
            if home_stats["count"] < 4 or away_stats["count"] < 4:
                continue

            # Combined over rate
            h2h_rate  = h2h_stats["over_rate"]
            home_rate = home_stats["over_rate"]
            away_rate = away_stats["over_rate"]

            rates = [r for r in [h2h_rate, home_rate, away_rate] if r is not None]
            if len(rates) < 2:
                continue

            weights, w_sum = [], 0.0
            if h2h_rate is not None:
                weights.append(0.40); w_sum += 0.40 * h2h_rate
            if home_rate is not None:
                weights.append(0.30); w_sum += 0.30 * home_rate
            if away_rate is not None:
                weights.append(0.30); w_sum += 0.30 * away_rate
            combined = w_sum / sum(weights)

            ht_rates = [s["ht_rate"] for s in [h2h_stats, home_stats, away_stats]
                        if s.get("ht_rate") is not None]
            ht_rate = sum(ht_rates) / len(ht_rates) if ht_rates else None

            # Odds
            odds = fetch_odds(fd.get("id"))

            league = fx.get("league", {})
            all_events.append({
                "date":       date_str,
                "fixture_id": fd.get("id"),
                "home":       teams.get("home", {}).get("name", ""),
                "away":       teams.get("away", {}).get("name", ""),
                "league":     league.get("name", ""),
                "league_id":  league.get("id"),
                "actual_goals": hg + ag,
                "actual_over":  actual_over,
                "combined":   round(combined, 4),
                "h2h_rate":   round(h2h_rate, 4) if h2h_rate is not None else None,
                "home_rate":  round(home_rate, 4) if home_rate is not None else None,
                "away_rate":  round(away_rate, 4) if away_rate is not None else None,
                "ht_rate":    round(ht_rate, 4) if ht_rate is not None else None,
                "h2h_count":  h2h_stats["count"],
                "home_count": home_stats["count"],
                "away_count": away_stats["count"],
                "over_odds":  odds["over"]  if odds else None,
                "under_odds": odds["under"] if odds else None,
                "bk_count":   odds["bk_count"] if odds else 0,
            })
            day_count += 1

        done_dates.add(date_str)
        print(f"   {date_str} | meccsek: {day_count} | össz: {len(all_events)}", flush=True)

        # Checkpoint minden nap után — 429 esetén folytatható
        save_checkpoint(all_events, done_dates)
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump(all_events, f, ensure_ascii=False, indent=2)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(all_events, f, ensure_ascii=False, indent=2)

    if os.path.exists(CHECKPOINT):
        os.remove(CHECKPOINT)

    print(f"\n[DONE] {len(all_events)} meccs mentve → {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
