#!/usr/bin/env python3
"""
backtest_basketball.py v3
──────────────────────────
• Phase 1: 90 nap major kosár meccs gyűjtés (SofaScore)
• Phase 2: Szimuláció pace-alapú vonallal (bookmakers pace-t követnek)
  - Nincs odds API hívás → gyors
  - H2H a 90 napos datasetből (dedup)
  - Szintetikus odds: 1.88 (standard juice)
• Phase 3: Statisztika + szűrő analízis
"""

import os, sys, time, json, math, requests
from datetime import datetime, date, timedelta
from collections import defaultdict

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sofascore.com/",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8",
}
BASE = "https://www.sofascore.com/api/v1"

MAJOR_KEYWORDS = [
    "nba", "euroleague", "eurocup", "7days", "acb", "endesa",
    "bbl", "turkish", "bsl", "lega basket", "pro a", "betclic elite",
    "vtb", "nbl", "g league", "g-league", "nbb", "cba", "lkl", "plk",
    "adriatic", "aba", "bsn",
]

# ── Szűrő paraméterek ──────────────────────────────────────────────────────
MIN_EDGE         = 4.0   # expected vs pace-vonal
MIN_PROB         = 0.55
MIN_CONFIDENCE   = 80
MIN_LAST_MATCHES = 6
MIN_H2H_MATCHES  = 2     # H2H meccsen belüli minimum
SYNTH_ODDS       = 1.88  # szintetikus bookmaker odds
COLLECT_DAYS     = 90
SIM_START_DAY    = 30    # első 30 nap: tanulóperiódus
DELAY            = 0.18


def get(url: str, timeout: int = 12) -> dict:
    time.sleep(DELAY)
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def fetch_day(date_str: str) -> list:
    data = get(f"{BASE}/sport/basketball/scheduled-events/{date_str}")
    return data.get("events", [])


def is_major(ev: dict) -> bool:
    t    = ev.get("tournament", {})
    name = (t.get("name","") + " " + t.get("category",{}).get("name","")).lower()
    return any(kw in name for kw in MAJOR_KEYWORDS)


def is_overtime(m: dict) -> bool:
    for side in ("homeScore", "awayScore"):
        if m.get(side, {}).get("overtime"):
            return True
    return False


def is_finished(m: dict) -> bool:
    return m.get("status", {}).get("type") == "finished"


def get_total(m: dict):
    hs  = m.get("homeScore", {}).get("current")
    as_ = m.get("awayScore", {}).get("current")
    return (hs + as_) if (hs is not None and as_ is not None) else None


def poisson_over_prob(expected: float, line: float) -> float:
    if expected <= 0: return 0.0
    z = (line + 0.5 - expected) / math.sqrt(max(expected, 1))
    return round(0.5 * math.erfc(z / math.sqrt(2)), 4)


def calc_team_stats(team_id: int, is_home: bool,
                    team_hist: dict, before_ts: int) -> dict | None:
    all_m = [m for m in team_hist.get(team_id, [])
             if m["ts"] < before_ts and not m["ot"]]
    if len(all_m) < MIN_LAST_MATCHES:
        return None
    ha = [m for m in all_m if m["is_home"] == is_home]
    sample = (ha if len(ha) >= MIN_LAST_MATCHES else all_m)[-10:]
    scored, conceded, totals = [], [], []
    for m in sample:
        scored.append(m["scored"]); conceded.append(m["conceded"])
        totals.append(m["total"])
    if len(scored) < MIN_LAST_MATCHES:
        return None
    avg_s = sum(scored)   / len(scored)
    avg_c = sum(conceded) / len(conceded)
    avg_t = sum(totals)   / len(totals)
    last5 = sum(totals[-5:]) / 5 if len(totals) >= 5 else avg_t
    last_ts = max(m["ts"] for m in all_m) if all_m else 0
    return {"off": round(avg_s,1), "def": round(avg_c,1),
            "pace": round(avg_t,1), "last5": round(last5,1),
            "last_ts": last_ts}


def calc_expected_total(hs: dict, as_: dict) -> float:
    la = (hs["off"] + as_["off"]) / 2 or 100.0
    home_exp = la * (hs["off"] / la) * (as_["def"] / la)
    away_exp = la * (as_["off"] / la) * (hs["def"] / la)
    season   = home_exp + away_exp
    form     = (hs["last5"] + as_["last5"]) / 2
    return round(season * 0.60 + form * 0.40, 1)


def is_b2b(last_ts: int, start_ts: int) -> bool:
    return bool(last_ts) and 0 < (start_ts - last_ts) / 3600 < 22


def calc_confidence(expected, line, direction, prob, h2h_avg) -> int:
    score  = min(40, int(prob * 50))
    edge   = abs(expected - line)
    score += min(20, int(edge * 2))
    if h2h_avg is not None:
        conf_dir = direction == "over"
        if (conf_dir and h2h_avg > line) or (not conf_dir and h2h_avg < line):
            score += 15
        else:
            score -= 5
    score += 15  # szintetikus odds rendben (1.88)
    score += 10  # alappontok
    return max(0, min(100, score))


def main():
    today     = date.today()
    start_col = today - timedelta(days=COLLECT_DAYS)
    sim_start = today - timedelta(days=COLLECT_DAYS - SIM_START_DAY)

    print(f"\n{'='*60}", flush=True)
    print(f" PHASE 1: Adatgyűjtés {start_col} → {today}", flush=True)
    print(f"{'='*60}", flush=True)

    # team_hist[team_id] = [{ ts, is_home, scored, conceded, total, ot }]
    team_hist: dict   = defaultdict(list)
    # h2h_hist[frozenset(h_id, a_id)] = [total_score]  (dedup by eid)
    h2h_hist: dict    = defaultdict(list)
    h2h_seen: set     = set()

    sim_event_ids: set  = set()
    sim_events: list    = []

    current = start_col
    total   = 0
    while current <= today:
        date_str = current.strftime("%Y-%m-%d")
        events   = fetch_day(date_str)
        day_ok   = 0
        for ev in events:
            if not is_finished(ev) or not is_major(ev):
                continue
            eid  = ev.get("id")
            ts   = ev.get("startTimestamp", 0)
            h_id = ev.get("homeTeam", {}).get("id")
            a_id = ev.get("awayTeam", {}).get("id")
            hs_s = ev.get("homeScore", {}).get("current")
            as_s = ev.get("awayScore", {}).get("current")
            ot   = is_overtime(ev)
            ht   = get_total(ev)
            if not h_id or not a_id or hs_s is None or as_s is None or ht is None:
                continue

            team_hist[h_id].append({"ts":ts,"is_home":True, "scored":hs_s,
                                     "conceded":as_s,"total":ht,"ot":ot})
            team_hist[a_id].append({"ts":ts,"is_home":False,"scored":as_s,
                                     "conceded":hs_s,"total":ht,"ot":ot})

            # H2H história (ugyanaz a pár, dedup eid-re)
            key = frozenset([h_id, a_id])
            if eid not in h2h_seen:
                h2h_seen.add(eid)
                h2h_hist[key].append({"ts":ts, "total":ht})

            if current >= sim_start and eid not in sim_event_ids:
                sim_event_ids.add(eid)
                sim_events.append(ev)
            day_ok += 1; total += 1
        print(f"  {date_str}: {day_ok} major meccs (össz: {total})", flush=True)
        current += timedelta(days=1)

    for tid in team_hist:
        team_hist[tid].sort(key=lambda x: x["ts"])
    sim_events.sort(key=lambda e: e.get("startTimestamp", 0))

    print(f"\nÖsszes: {total}  |  Szimulációs (dedup): {len(sim_events)}", flush=True)

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}", flush=True)
    print(f" PHASE 2: Szimuláció (pace-vonal, szint. odds={SYNTH_ODDS})", flush=True)
    print(f"{'='*60}", flush=True)

    results = []
    skip    = defaultdict(int)

    for ev in sim_events:
        eid      = ev.get("id")
        start_ts = ev.get("startTimestamp", 0)
        h_id     = ev.get("homeTeam", {}).get("id")
        a_id     = ev.get("awayTeam", {}).get("id")
        home     = ev.get("homeTeam", {}).get("name","")
        away     = ev.get("awayTeam", {}).get("name","")
        league   = ev.get("tournament", {}).get("name","?")
        actual   = get_total(ev)

        if not h_id or not a_id or actual is None:
            skip["no_data"] += 1; continue

        hs  = calc_team_stats(h_id, True,  team_hist, start_ts)
        as_ = calc_team_stats(a_id, False, team_hist, start_ts)
        if not hs or not as_:
            skip["no_stats"] += 1; continue

        expected = calc_expected_total(hs, as_)

        # B2B korrekció
        if is_b2b(hs["last_ts"], start_ts):  expected -= 7.0
        if is_b2b(as_["last_ts"], start_ts): expected -= 7.0
        expected = round(expected, 1)

        # Pace-alapú vonal (0.5-re kerekítve, mint a könyvmásoknál)
        pace_avg = (hs["pace"] + as_["pace"]) / 2
        line     = round(pace_avg * 2) / 2  # nearest 0.5

        edge       = round(expected - line, 1)
        prob_over  = poisson_over_prob(expected, line)
        prob_under = 1.0 - prob_over

        if edge >= MIN_EDGE and prob_over >= MIN_PROB:
            direction = "over";  prob = prob_over
        elif edge <= -MIN_EDGE and prob_under >= MIN_PROB:
            direction = "under"; prob = prob_under
        else:
            skip["no_edge"] += 1; continue

        # H2H a 90 napos datasetből
        key = frozenset([h_id, a_id])
        h2h_entries = [e for e in h2h_hist.get(key, []) if e["ts"] < start_ts]
        h2h_avg = None
        if len(h2h_entries) >= MIN_H2H_MATCHES:
            recent = sorted(h2h_entries, key=lambda x: x["ts"])[-6:]
            h2h_avg = round(sum(e["total"] for e in recent) / len(recent), 1)

        if h2h_avg is not None:
            h2h_ok = (direction=="over" and h2h_avg > line) or \
                     (direction=="under" and h2h_avg < line)
            if not h2h_ok:
                skip["h2h_mismatch"] += 1; continue
        # (Ha nincs H2H, engedjük át – a confidence alacsonyabb lesz)

        confidence = calc_confidence(expected, line, direction, prob, h2h_avg)
        if confidence < MIN_CONFIDENCE:
            skip["low_conf"] += 1; continue

        result = "win" if (
            (direction=="over"  and actual > line) or
            (direction=="under" and actual < line)
        ) else "loss"

        results.append({
            "date":      datetime.utcfromtimestamp(start_ts).strftime("%Y-%m-%d"),
            "home":      home, "away": away, "league": league,
            "direction": direction, "line": line, "expected": expected,
            "actual":    actual,   "edge": edge, "prob": round(prob,3),
            "confidence": confidence, "h2h_avg": h2h_avg, "result": result,
        })
        w = "WIN" if result=="win" else "LOSS"
        print(f"  TIP {w}: {home} vs {away} | {direction.upper()} {line} "
              f"| exp={expected} | actual={actual} | conf={confidence}", flush=True)

    # ── Phase 3 ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}", flush=True)
    print(f" PHASE 3: Összefoglaló", flush=True)
    print(f"{'='*60}", flush=True)
    print("Kiszűrve:", flush=True)
    for k, v in sorted(skip.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}", flush=True)

    if not results:
        print("\nNINCS ELÉG ADAT – szűrők enyhítése szükséges.", flush=True)
        return

    ODDS = SYNTH_ODDS

    def profit(sub):
        return sum(ODDS-1 if r["result"]=="win" else -1 for r in sub)

    wins = sum(1 for r in results if r["result"]=="win")
    n    = len(results)
    wr   = wins / n * 100
    roi  = profit(results) / n * 100

    bal = pk = 0.0; mdd = 0.0
    monthly = defaultdict(lambda: {"w":0,"l":0,"p":0.0})
    for r in results:
        g = ODDS-1 if r["result"]=="win" else -1
        bal += g
        if bal > pk: pk = bal
        if pk - bal > mdd: mdd = pk - bal
        m = r["date"][:7]
        monthly[m]["w" if r["result"]=="win" else "l"] += 1
        monthly[m]["p"] = round(monthly[m]["p"] + g, 2)

    print(f"\n Tippek:       {n}", flush=True)
    print(f" Nyertes:      {wins} ({wr:.1f}%)", flush=True)
    print(f" ROI:          {roi:+.1f}%  (szint. odds={ODDS})", flush=True)
    print(f" Flat profit:  {bal:+.2f}u", flush=True)
    print(f" Max drawdown: -{mdd:.2f}u", flush=True)
    print(f"\n Havi:", flush=True)
    for m, s in sorted(monthly.items()):
        n2 = s["w"] + s["l"]
        print(f"  {'✓' if s['p']>0 else '✗'} {m}: {s['w']}/{n2} "
              f"({s['w']/n2*100:.0f}%) {s['p']:+.2f}u  ROI:{s['p']/n2*100:+.0f}%", flush=True)

    pmth = sum(1 for s in monthly.values() if s["p"] > 0)
    print(f"\n Nyereséges hónapok: {pmth}/{len(monthly)}", flush=True)

    # ── Szűrő analízis ────────────────────────────────────────────────────────
    print(f"\n{'─'*60}", flush=True)
    print(" SZŰRŐ ANALÍZIS", flush=True)
    print(f"{'─'*60}", flush=True)

    print("\n Confidence küszöb:", flush=True)
    for thr in [70, 75, 78, 80, 82, 85, 88, 90]:
        sub = [r for r in results if r["confidence"] >= thr]
        if len(sub) < 5: continue
        w2 = sum(1 for r in sub if r["result"]=="win")
        print(f"  >={thr}: n={len(sub):>4}  WR={w2/len(sub)*100:.1f}%"
              f"  ROI={profit(sub)/len(sub)*100:+.1f}%", flush=True)

    print("\n |Edge| küszöb:", flush=True)
    for me in [4, 5, 6, 7, 8, 10, 12, 15]:
        sub = [r for r in results if abs(r["edge"]) >= me]
        if len(sub) < 5: continue
        w2 = sum(1 for r in sub if r["result"]=="win")
        print(f"  >={me:>2}: n={len(sub):>4}  WR={w2/len(sub)*100:.1f}%"
              f"  ROI={profit(sub)/len(sub)*100:+.1f}%", flush=True)

    print("\n Prob küszöb:", flush=True)
    for mp in [0.55, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70]:
        sub = [r for r in results if r["prob"] >= mp]
        if len(sub) < 5: continue
        w2 = sum(1 for r in sub if r["result"]=="win")
        print(f"  >={mp:.2f}: n={len(sub):>4}  WR={w2/len(sub)*100:.1f}%"
              f"  ROI={profit(sub)/len(sub)*100:+.1f}%", flush=True)

    print("\n Irány:", flush=True)
    for d in ["over","under"]:
        sub = [r for r in results if r["direction"]==d]
        if not sub: continue
        w2 = sum(1 for r in sub if r["result"]=="win")
        print(f"  {d:5}: n={len(sub):>4}  WR={w2/len(sub)*100:.1f}%"
              f"  ROI={profit(sub)/len(sub)*100:+.1f}%", flush=True)

    print("\n H2H megerősítés:", flush=True)
    s1 = [r for r in results if r["h2h_avg"] is not None]
    s2 = [r for r in results if r["h2h_avg"] is None]
    for lbl, sub in [("Van H2H", s1), ("Nincs H2H", s2)]:
        if not sub: continue
        w2 = sum(1 for r in sub if r["result"]=="win")
        print(f"  {lbl}: n={len(sub):>4}  WR={w2/len(sub)*100:.1f}%"
              f"  ROI={profit(sub)/len(sub)*100:+.1f}%", flush=True)

    print("\n Top ligák:", flush=True)
    ls = defaultdict(lambda: {"w":0,"l":0,"p":0.0})
    for r in results:
        ls[r["league"]]["w" if r["result"]=="win" else "l"] += 1
        ls[r["league"]]["p"] = round(ls[r["league"]]["p"] + (ODDS-1 if r["result"]=="win" else -1), 2)
    for lg, s in sorted(ls.items(), key=lambda x: -(x[1]["w"]+x[1]["l"]))[:15]:
        n2 = s["w"] + s["l"]
        print(f"  {lg}: {s['w']}/{n2} ({s['w']/n2*100:.0f}%)  ROI:{s['p']/n2*100:+.0f}%", flush=True)

    with open("backtest_basketball_results.json","w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n Mentve: backtest_basketball_results.json ({len(results)} rekord)", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
