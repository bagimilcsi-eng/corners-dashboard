#!/usr/bin/env python3
"""
Rugby Union / League Backtest – SofaScore ingyenes API
Stratégia: O/U pont – csapat forma alapján
Ellenőrzi az odds elérhetőségét is (Odds API + SofaScore)
"""
import json, time, requests, logging, os
from datetime import date, timedelta
from collections import defaultdict

logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO)
log = logging.getLogger()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}
OUT  = "backtest_raw_rugby.json"
DAYS = 90

TOP_CAT = {
    # Rugby Union
    "England","France","New Zealand","Australia","South Africa","Ireland",
    "Wales","Scotland","Italy","Argentina","Japan","Fiji","Georgia",
    "International","Six Nations","World","Super Rugby","United Rugby",
    # Rugby League
    "Australia","England","France","Papua New Guinea",
    # Közös
    "Europe","Americas","Oceania","Asia",
}

def fetch_day(sport, day):
    try:
        r = requests.get(
            f"https://www.sofascore.com/api/v1/sport/{sport}/scheduled-events/{day}",
            headers=HEADERS, timeout=10
        )
        if r.status_code != 200:
            return []
        return r.json().get("events") or []
    except Exception:
        return []

def parse_event(ev, sport):
    if (ev.get("status") or {}).get("type") != "finished":
        return None
    tourn = ev.get("tournament") or {}
    cat   = ((tourn.get("category") or {}).get("name") or "")
    t     = (tourn.get("name") or "").lower()
    if any(k in t for k in ["u18","u20","u21","u16","youth","junior","women","ladies","girl"]):
        return None
    hs = ev.get("homeScore") or {}
    as_ = ev.get("awayScore") or {}
    hp  = hs.get("current")
    ap  = as_.get("current")
    if hp is None or ap is None:
        return None
    total = int(hp) + int(ap)
    return {
        "event_id":   ev.get("id"),
        "date":       str(ev.get("startTimestamp",""))[:10] if ev.get("startTimestamp") else "",
        "tournament": tourn.get("name","?"),
        "category":   cat,
        "sport":      sport,
        "home":       (ev.get("homeTeam") or {}).get("name","?"),
        "away":       (ev.get("awayTeam") or {}).get("name","?"),
        "home_pts":   int(hp),
        "away_pts":   int(ap),
        "total_pts":  total,
        "winner":     "home" if hp > ap else "away" if ap > hp else "draw",
    }

all_matches = []
today = date.today()

for sport in ["rugby-union", "rugby-league"]:
    log.info(f"=== {sport.upper()} ===")
    day = today - timedelta(days=DAYS)
    while day < today:
        events = fetch_day(sport, day)
        ms = [r for e in events if (r := parse_event(e, sport)) is not None]
        all_matches.extend(ms)
        if ms:
            log.info(f"  {day}: {len(ms)} meccs (ossz: {len(all_matches)})")
        day += timedelta(days=1)
        time.sleep(0.25)

with open(OUT, "w") as f:
    json.dump(all_matches, f, ensure_ascii=False, indent=2)
log.info(f"Mentve: {OUT} ({len(all_matches)} meccs)")

# ===================== ELEMZÉS =====================
union  = [m for m in all_matches if m["sport"] == "rugby-union"]
league = [m for m in all_matches if m["sport"] == "rugby-league"]

print(f"\n{'='*65}")
print(f"RUGBY BACKTEST – {DAYS} nap")
print(f"{'='*65}")
print(f"Rugby Union:  {len(union):5d} meccs ({len(union)/DAYS:.1f}/nap)")
print(f"Rugby League: {len(league):5d} meccs ({len(league)/DAYS:.1f}/nap)")

def analyze(grp, label):
    if not grp:
        return
    n    = len(grp)
    pts  = [m["total_pts"] for m in grp]
    avg  = sum(pts)/n
    print(f"\n--- {label} ({n} meccs, atlag: {avg:.1f} pont) ---")
    for line in [34.5, 39.5, 44.5, 49.5, 54.5, 59.5, 64.5, 69.5]:
        over  = sum(1 for p in pts if p > line)
        under = n - over
        pct_o = over/n*100
        pct_u = under/n*100
        for odds in [1.80, 1.85, 1.90]:
            roi_o = ((over*(odds-1)) - under) / n * 100
            roi_u = ((under*(odds-1)) - over) / n * 100
            if roi_o > 10:
                print(f"  [OK]  OVER  {line} @{odds}: {over}/{n} ({pct_o:.1f}%) ROI: {roi_o:+.1f}%")
            if roi_u > 10:
                print(f"  [OK]  UNDER {line} @{odds}: {under}/{n} ({pct_u:.1f}%) ROI: {roi_u:+.1f}%")

    # Liga bontás
    by_l = defaultdict(list)
    for m in grp:
        by_l[m["tournament"]].append(m["total_pts"])
    print(f"  Top ligák:")
    for lg, pts2 in sorted(by_l.items(), key=lambda x: -len(x[1]))[:12]:
        if len(pts2) < 10:
            continue
        a2   = sum(pts2)/len(pts2)
        o44  = sum(1 for p in pts2 if p > 44.5)
        o54  = sum(1 for p in pts2 if p > 54.5)
        print(f"    {lg:42s}: {len(pts2):3d} meccs | atlag: {a2:.1f} | O44.5: {o44/len(pts2)*100:.0f}% | O54.5: {o54/len(pts2)*100:.0f}%")

analyze(union,  "RUGBY UNION")
analyze(league, "RUGBY LEAGUE")

# ===================== ODDS API =====================
print(f"\n{'='*65}")
print("ODDS API – Rugby ligák")
print(f"{'='*65}")
KEY = os.getenv("ODDS_API_KEY")
r = requests.get("https://api.the-odds-api.com/v4/sports", params={"apiKey": KEY}, timeout=10)
sports_list = r.json()
rugby_sports = [s for s in sports_list if "rugby" in s.get("key","").lower() or "rugby" in s.get("title","").lower()]
if rugby_sports:
    for s in rugby_sports:
        print(f"  {s['key']:45s} | {s['title']} | Active: {s['active']}")
else:
    print("  Nincs rugby liga az Odds API-ban!")

# Ha van rugby liga, nézzük az odds-ot
for sport_key in [s["key"] for s in rugby_sports if s["active"]][:3]:
    print(f"\n  === {sport_key} – totals odds ===")
    r2 = requests.get(
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
        params={"apiKey": KEY, "regions": "eu", "markets": "totals", "oddsFormat": "decimal"},
        timeout=10
    )
    if r2.status_code == 200:
        matches_api = r2.json()
        print(f"  Meccsek: {len(matches_api)}")
        for m in matches_api[:3]:
            print(f"    {m['home_team']} vs {m['away_team']} ({m['commence_time'][:10]})")
            for bk in m.get("bookmakers",[])[:2]:
                for mkt in bk.get("markets",[]):
                    if mkt["key"] == "totals":
                        outcomes = mkt["outcomes"][:2]
                        print(f"      {bk['title']}: {outcomes[0]['name']} {outcomes[0].get('point','')} @{outcomes[0]['price']} | {outcomes[1]['name']} @{outcomes[1]['price']}")
    print(f"  Marado API kérések: {r2.headers.get('x-requests-remaining','?')}")

print("\nKESZ!")
