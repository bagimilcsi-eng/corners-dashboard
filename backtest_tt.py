#!/usr/bin/env python3
"""
backtest_tt.py  –  Asztalitenisz bot visszamenőleges szimulációja (180 nap)
Rolling-window módszer: csak 30 nap esemény él egyszerre a memóriában.
Forma + H2H: csak Setka Cup + Czech Liga meccsekből (ismert korlát).
Odds: SofaScore API a qualifying meccsekre.
"""
import os, sys, time, json, requests
from datetime import datetime, date, timedelta
from collections import defaultdict, deque

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sofascore.com/",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8",
    "Cache-Control": "no-cache", "Pragma": "no-cache",
}
BASE = "https://www.sofascore.com/api/v1"

ALLOWED        = ["setka", "czech"]
MIN_FORM       = 10
MIN_H2H        = 5
THRESHOLD      = 27.5
MIN_H2H_RATE   = 0.70
MIN_FS_RATE    = 0.70
MIN_FORM_DIFF  = 0.20
MIN_ODDS       = 1.65
FORM_DAYS      = 14
H2H_DAYS       = 90
DELAY          = 0.22
BACKTEST_DAYS  = 180
EXTRA_DAYS     = 16   # forma-buffer az időszak elejéhez


def get(url: str) -> dict:
    time.sleep(DELAY)
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def is_allowed(ev: dict) -> bool:
    t = ev.get("tournament", {})
    txt = (t.get("name", "") + " " + t.get("category", {}).get("name", "")).lower()
    return any(k in txt for k in ALLOWED)


def frac2dec(s: str):
    if not s: return None
    try:
        if "/" in s:
            a, b = s.split("/"); return round(int(a)/int(b)+1, 3)
        v = float(s); return round(v, 3) if v > 1 else round(v+1, 3)
    except: return None


def parse_odd(c: dict):
    dv = c.get("decimalValue")
    if dv:
        try:
            v = float(dv)
            if v > 1: return round(v, 3)
        except: pass
    return frac2dec(c.get("fractionalValue", ""))


def fetch_odds(eid: int):
    data = get(f"{BASE}/event/{eid}/odds/1/all")
    for mkt in data.get("markets", []):
        if mkt.get("marketName") == "Full time":
            om = {}
            for c in mkt.get("choices", []):
                odd = parse_odd(c)
                if odd: om[c.get("name", "")] = odd
            if "1" in om and "2" in om:
                return {"home": om["1"], "away": om["2"]}
            v = list(om.values())
            if len(v) >= 2: return {"home": v[0], "away": v[1]}
    return None


# ── Forma számítás a rolling window-ból ───────────────────────────────────────

def calc_form(pid: int, history: list, before_ts: int):
    """Visszaad: ((form_w, form_t), (fs_w, fs_t))"""
    cut = before_ts - FORM_DAYS * 86400
    fw = ft = fsw = fst = 0
    for e in reversed(history):          # legfrissebb előre
        if ft >= 10: break
        ts = e.get("startTimestamp", 0)
        if ts >= before_ts or ts < cut: continue
        ih = (e.get("homeTeam", {}).get("id") == pid)
        hs  = e.get("homeScore", {}).get("current", 0) or 0
        as_ = e.get("awayScore", {}).get("current", 0) or 0
        ft += 1
        if (ih and hs > as_) or (not ih and as_ > hs): fw += 1
        h1 = e.get("homeScore", {}).get("period1")
        a1 = e.get("awayScore", {}).get("period1")
        if h1 is not None and a1 is not None:
            fst += 1
            if (ih and h1 > a1) or (not ih and a1 > h1): fsw += 1
    return (fw, ft), (fsw, fst)


# ── H2H a rolling pair pool-ból ───────────────────────────────────────────────

def calc_h2h(home_id: int, away_id: int, pool: list, before_ts: int):
    matches = sorted(
        [e for e in pool if e.get("startTimestamp", 0) < before_ts],
        key=lambda e: e.get("startTimestamp", 0), reverse=True
    )
    if len(matches) < MIN_H2H: return 0, 0
    hw = 0
    for e in matches[:5]:
        hs  = e.get("homeScore", {}).get("current", 0) or 0
        as_ = e.get("awayScore", {}).get("current", 0) or 0
        eh  = e.get("homeTeam", {}).get("id")
        if (eh == home_id and hs > as_) or (eh == away_id and as_ > hs): hw += 1
    return hw, 5


# ── Tipp számítás (azonos main.py calculate_tip-pel) ─────────────────────────

def calc_tip(h2h_hw, h2h_t, hfw, hft, afw, aft,
             hfsw=0, hfst=0, afsw=0, afst=0):
    sc = 0.0
    if hft < MIN_FORM or aft < MIN_FORM: return "uncertain", sc
    if h2h_t < MIN_H2H: return "uncertain", sc
    hr  = hfw / hft; ar = afw / aft; h2r = h2h_hw / h2h_t
    hs  = (h2r - 0.5) * 40
    fs  = (hr - ar) * 30
    hfr = (hfsw / hfst) if hfst >= 5 else None
    afr = (afsw / afst) if afst >= 5 else None
    fss = ((hfr - afr) * 20) if hfr is not None and afr is not None else 0.0
    if (hs > 0) != (fs > 0): return "uncertain", sc
    sc = hs + fs + fss
    if abs(sc) < THRESHOLD: return "uncertain", sc
    w   = "home" if sc > 0 else "away"
    wh  = h2r if w == "home" else 1 - h2r
    wf  = hr  if w == "home" else ar
    lf  = ar  if w == "home" else hr
    wfs = hfr if w == "home" else afr
    if wh < MIN_H2H_RATE: return "uncertain", sc
    if wf - lf < MIN_FORM_DIFF: return "uncertain", sc
    if wfs is not None and wfs < MIN_FS_RATE: return "uncertain", sc
    return w, sc


# ── Főprogram ─────────────────────────────────────────────────────────────────

def main():
    today      = date.today()
    total_days = BACKTEST_DAYS + EXTRA_DAYS

    # Rolling window struktúrák (Setka/Czech only → kis memória)
    player_hist: dict[int, list] = defaultdict(list)  # pid → időrendi lista
    pair_pool:   dict[tuple, list] = defaultdict(list)  # (id,id) → lista

    results  = []
    skip     = defaultdict(int)
    period_start_date = today - timedelta(days=BACKTEST_DAYS)

    print(f"[START] Backtest: {period_start_date} → {today-timedelta(days=1)}", flush=True)
    print(f"        Szűrők: forma≥{MIN_FORM}, H2H≥{MIN_H2H}, score≥{THRESHOLD}, "
          f"H2H%≥{int(MIN_H2H_RATE*100)}, forma_diff≥{int(MIN_FORM_DIFF*100)}pp, "
          f"odds≥{MIN_ODDS}", flush=True)
    print(f"        API delay: {DELAY}s\n", flush=True)

    cutoff_form_ts = 0   # frissítjük menet közben (memóriaoptimalizálás)

    for day_idx in range(total_days, 0, -1):
        target_date  = today - timedelta(days=day_idx)
        in_sim_period = (target_date >= period_start_date)

        date_str = target_date.strftime("%Y-%m-%d")
        evs = get(f"{BASE}/sport/table-tennis/scheduled-events/{date_str}").get("events", [])

        # Csak allowed + finished meccseket tartjuk meg
        day_allowed = [
            e for e in evs
            if is_allowed(e) and e.get("status", {}).get("type", "").lower() == "finished"
        ]

        # Rolling window frissítése
        target_ts = int(datetime.combine(target_date, datetime.min.time()).timestamp())
        evict_form_cutoff = target_ts - FORM_DAYS * 86400
        evict_h2h_cutoff  = target_ts - H2H_DAYS  * 86400

        for e in day_allowed:
            hid = e.get("homeTeam", {}).get("id")
            aid = e.get("awayTeam", {}).get("id")
            if hid: player_hist[hid].append(e)
            if aid: player_hist[aid].append(e)
            if hid and aid:
                pair_pool[tuple(sorted([hid, aid]))].append(e)

        # Periódikusan kiürítjük a régi bejegyzéseket (memóriagazdálkodás)
        if day_idx % 7 == 0:
            for pid in list(player_hist.keys()):
                player_hist[pid] = [
                    x for x in player_hist[pid]
                    if x.get("startTimestamp", 0) >= evict_form_cutoff
                ]
                if not player_hist[pid]:
                    del player_hist[pid]
            for key in list(pair_pool.keys()):
                pair_pool[key] = [
                    x for x in pair_pool[key]
                    if x.get("startTimestamp", 0) >= evict_h2h_cutoff
                ]
                if not pair_pool[key]:
                    del pair_pool[key]

        if not in_sim_period:
            continue

        # Szimuláció az adott nap meccsein
        for ev in day_allowed:
            hid = ev.get("homeTeam", {}).get("id")
            aid = ev.get("awayTeam", {}).get("id")
            ts  = ev.get("startTimestamp", 0)
            hs  = ev.get("homeScore", {}).get("current", 0) or 0
            as_ = ev.get("awayScore", {}).get("current", 0) or 0
            if hs == as_: skip["draw"] += 1; continue
            actual = "home" if hs > as_ else "away"

            (hfw, hft), (hfsw, hfst) = calc_form(hid, player_hist.get(hid, []), ts)
            (afw, aft), (afsw, afst) = calc_form(aid, player_hist.get(aid, []), ts)
            if hft < MIN_FORM or aft < MIN_FORM: skip["forma"] += 1; continue

            key = tuple(sorted([hid, aid]))
            h2h_hw, h2h_t = calc_h2h(hid, aid, pair_pool.get(key, []), ts)
            if h2h_t < MIN_H2H: skip["h2h"] += 1; continue

            winner, score = calc_tip(h2h_hw, h2h_t, hfw, hft, afw, aft, hfsw, hfst, afsw, afst)
            if winner == "uncertain": skip["bizony"] += 1; continue

            odds = fetch_odds(ev.get("id"))
            if not odds: skip["no_odds"] += 1; continue
            pred_odds = odds["home"] if winner == "home" else odds["away"]
            if pred_odds < MIN_ODDS: skip["low_odds"] += 1; continue

            results.append({
                "event_id":  ev.get("id"),
                "date":      date_str,
                "home":      ev.get("homeTeam", {}).get("name", ""),
                "away":      ev.get("awayTeam", {}).get("name", ""),
                "league":    ev.get("tournament", {}).get("name", ""),
                "predicted": winner,
                "actual":    actual,
                "odds":      pred_odds,
                "score":     round(score, 1),
                "result":    "win" if winner == actual else "loss",
            })

        # Napi státusz log + közbülső mentés
        tip_today = sum(1 for r in results if r["date"] == date_str)
        if in_sim_period and (day_idx % 15 == 0 or tip_today > 0):
            wins_so_far = sum(1 for r in results if r["result"] == "win")
            print(f"   {date_str} | tippek: {len(results)} "
                  f"({wins_so_far}W/{len(results)-wins_so_far}L) "
                  f"| ma: {tip_today}", flush=True)

        # Közbülső mentés minden 20 tippnél
        if len(results) % 20 == 0 and results:
            with open("backtest_results.json", "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

    # ── Végeredmény kimutatás ──────────────────────────────────────────────────
    sep = "═" * 64
    print(f"\n{sep}", flush=True)
    print(f" BACKTEST EREDMÉNY  –  {BACKTEST_DAYS} nap  |  Setka Cup + Czech Liga")
    print(sep)
    total_sim = sum(skip.values()) + len(results)
    print(f" Vizsgált meccsek összesen:       {total_sim}")
    print(f"   kiszűrve (forma <{MIN_FORM}):        {skip['forma']}")
    print(f"   kiszűrve (H2H <{MIN_H2H} meccs):      {skip['h2h']}")
    print(f"   kiszűrve (bizonytalan):         {skip['bizony']}")
    print(f"   kiszűrve (nincs odds):          {skip['no_odds']}")
    print(f"   kiszűrve (odds <{MIN_ODDS}):        {skip['low_odds']}")
    print(f"   döntetlen/egyéb:                {skip['draw']}")
    print(f"\n  ► ADOTT TIPPEK: {len(results)}")

    if results:
        wins   = sum(1 for r in results if r["result"] == "win")
        losses = len(results) - wins
        wr     = wins / len(results) * 100
        roi    = sum((r["odds"]-1 if r["result"]=="win" else -1) for r in results) / len(results) * 100
        avg_o  = sum(r["odds"] for r in results) / len(results)
        profit = sum((r["odds"]-1 if r["result"]=="win" else -1) for r in results)
        pf     = sum(r["odds"]-1 for r in results if r["result"]=="win") / max(losses, 1)

        print(f"\n  Nyertes:        {wins}  ({wr:.1f}%)")
        print(f"  Vesztes:        {losses}")
        print(f"  ROI:            {roi:+.1f}%")
        print(f"  Átlag szorzó:   {avg_o:.2f}")
        print(f"  Flat profit:    {profit:+.2f} unit")
        print(f"  Profit Factor:  {pf:.2f}")

        bal = pk = 0.0; max_dd = 0.0
        for r in results:
            bal += (r["odds"]-1) if r["result"]=="win" else -1
            if bal > pk: pk = bal
            dd = pk - bal
            if dd > max_dd: max_dd = dd
        print(f"  Max drawdown:   -{max_dd:.2f} unit")

        print(f"\n Liga bontás:")
        ls = defaultdict(lambda: {"w": 0, "l": 0})
        for r in results:
            ls[r["league"]]["w" if r["result"]=="win" else "l"] += 1
        for lg, s in sorted(ls.items(), key=lambda x: -(x[1]["w"]+x[1]["l"])):
            t = s["w"] + s["l"]
            print(f"   {lg:<38} {s['w']:>3}/{t:<3}  {s['w']/t*100:.1f}%")

        print(f"\n Havi bontás:")
        ms = defaultdict(lambda: {"w": 0, "l": 0, "r": 0.0})
        for r in results:
            m = r["date"][:7]
            ms[m]["w" if r["result"]=="win" else "l"] += 1
            ms[m]["r"] += (r["odds"]-1) if r["result"]=="win" else -1
        for m, s in sorted(ms.items()):
            t = s["w"] + s["l"]
            print(f"   {m}  {s['w']:>3}/{t:<3}  ({s['w']/t*100:.1f}%)  ROI: {s['r']/t*100:+.1f}%")

        with open("backtest_results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n Részletes tippek → backtest_results.json")

    print(sep)
    print(f" MEGJEGYZÉS: Forma és H2H csak Setka/Czech meccsekből számítva.")
    print(f" A valós bot más TT ligákat is figyelembe vesz.\n")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
