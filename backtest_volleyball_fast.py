#!/usr/bin/env python3
"""Röplabda backtest – gyors verzió, folyamatos mentéssel"""
import json, time, requests, logging
from datetime import date, timedelta
from collections import defaultdict

logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO)
log = logging.getLogger()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}
OUT = "backtest_raw_volleyball.json"
DAYS = 90

TOP_CAT = {
    "Italy","Poland","France","Turkey","Brazil","Russia","Germany","Spain",
    "Serbia","Greece","Netherlands","Belgium","Japan","Argentina","USA",
    "International","South Korea","Slovenia","Czech Republic","Romania",
    "Azerbaijan","Iran","Finland","Switzerland","Sweden","Portugal","Croatia",
    "China","South America",
}

def fetch_day(day):
    try:
        r = requests.get(
            f"https://www.sofascore.com/api/v1/sport/volleyball/scheduled-events/{day}",
            headers=HEADERS, timeout=10
        )
        if r.status_code != 200:
            return []
        events = r.json().get("events") or []
    except Exception:
        return []

    results = []
    for ev in events:
        if (ev.get("status") or {}).get("type") != "finished":
            continue
        tourn = ev.get("tournament") or {}
        cat   = ((tourn.get("category") or {}).get("name") or "")
        if cat not in TOP_CAT:
            continue
        t = (tourn.get("name") or "").lower()
        if any(k in t for k in ["u18","u20","u21","u16","youth","junior","beach","sand"]):
            continue
        hs = ev.get("homeScore") or {}
        as_ = ev.get("awayScore") or {}
        hs_sets = hs.get("current")
        as_sets = as_.get("current")
        if hs_sets is None or as_sets is None:
            continue
        hs_sets, as_sets = int(hs_sets), int(as_sets)
        if not ((hs_sets == 3 and as_sets in [0,1,2]) or (as_sets == 3 and hs_sets in [0,1,2])):
            continue
        tl = t
        cl = cat.lower()
        gender = "women" if any(k in tl for k in ["women","frauen","feminin","ladies","noi"]) else "men"
        results.append({
            "event_id":   ev.get("id"),
            "date":       str(day),
            "tournament": tourn.get("name","?"),
            "category":   cat,
            "gender":     gender,
            "home":       (ev.get("homeTeam") or {}).get("name","?"),
            "away":       (ev.get("awayTeam") or {}).get("name","?"),
            "home_sets":  hs_sets,
            "away_sets":  as_sets,
            "total_sets": hs_sets + as_sets,
            "score_type": f"{max(hs_sets,as_sets)}-{min(hs_sets,as_sets)}",
        })
    return results

all_matches = []
today = date.today()
day   = today - timedelta(days=DAYS)
while day < today:
    ms = fetch_day(day)
    all_matches.extend(ms)
    if ms:
        log.info(f"{day}: {len(ms)} meccs (ossz: {len(all_matches)})")
    day += timedelta(days=1)
    time.sleep(0.25)

with open(OUT, "w") as f:
    json.dump(all_matches, f, ensure_ascii=False, indent=2)
log.info(f"Mentve: {OUT} ({len(all_matches)} meccs)")

# === ELEMZES ===
men   = [m for m in all_matches if m["gender"] == "men"]
women = [m for m in all_matches if m["gender"] == "women"]

print(f"\n{'='*65}")
print(f"ROPLABD BACKTEST – {DAYS} nap")
print(f"{'='*65}")
print(f"Osszes: {len(all_matches)} | Ferfi: {len(men)} ({len(men)/DAYS:.1f}/nap) | Noi: {len(women)} ({len(women)/DAYS:.1f}/nap)")

for label, grp in [("FERFI", men), ("NOI", women)]:
    if not grp:
        continue
    n   = len(grp)
    s30 = sum(1 for m in grp if m["score_type"]=="3-0")
    s31 = sum(1 for m in grp if m["score_type"]=="3-1")
    s32 = sum(1 for m in grp if m["score_type"]=="3-2")
    print(f"\n--- {label} ({n} meccs, {n/DAYS:.1f}/nap) ---")
    print(f"  3-0: {s30} ({s30/n*100:.1f}%)  |  3-1: {s31} ({s31/n*100:.1f}%)  |  3-2: {s32} ({s32/n*100:.1f}%)")

    def row(desc, wins, odds):
        losses = n - wins
        roi = ((wins*(odds-1)) - losses) / n * 100
        wr  = wins/n*100
        st  = "OK" if roi > 10 else "WARN" if roi > 5 else "BAD"
        print(f"  [{st}] {desc:52s}: {wins}/{n} ({wr:.1f}%) ROI:{roi:+.1f}%")

    print()
    for o in [1.75, 1.80, 1.85, 1.90]:
        row(f"UNDER 3.5 szett (3-0 vege) @{o}", s30, o)
    print()
    for o in [1.75, 1.80, 1.85, 1.90]:
        row(f"UNDER 4.5 szett (3-0/3-1)  @{o}", s30+s31, o)
    print()
    for o in [1.75, 1.80, 1.85, 1.90]:
        row(f"OVER  4.5 szett (3-2 vege) @{o}", s32, o)

print(f"\n--- TOP FERFI LIGAK ---")
by = defaultdict(list)
for m in men:
    by[m["tournament"]].append(m)
rows = sorted(by.items(), key=lambda x: -len(x[1]))
for league, ms in rows[:20]:
    if len(ms) < 15:
        continue
    n2  = len(ms)
    s30 = sum(1 for m in ms if m["score_type"]=="3-0")
    s31 = sum(1 for m in ms if m["score_type"]=="3-1")
    s32 = sum(1 for m in ms if m["score_type"]=="3-2")
    roi = ((s30*0.85)-(n2-s30))/n2*100
    print(f"  {league:42s}: {n2:3d} | 3-0:{s30/n2*100:.0f}% 3-1:{s31/n2*100:.0f}% 3-2:{s32/n2*100:.0f}% ROI@1.85:{roi:+.1f}%")

print("\nKESZ!")
