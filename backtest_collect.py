#!/usr/bin/env python3
"""
backtest_collect.py — Egyszer lefuttatja és elmenti az összes meccs adatát.
Alacsony küszöbbel (score≥0) gyűjt, hogy az optimizer offline tudjon dolgozni.
"""
import os, sys, time, json, requests
from datetime import datetime, date, timedelta
from collections import defaultdict

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sofascore.com/",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8",
    "Cache-Control": "no-cache", "Pragma": "no-cache",
}
BASE = "https://www.sofascore.com/api/v1"

ALLOWED       = ["setka", "czech"]
MIN_FORM      = 6       # laza — hogy mindent összegyűjtsünk
MIN_H2H       = 3       # laza
FORM_DAYS     = 14
H2H_DAYS      = 90
DELAY         = 0.22
BACKTEST_DAYS = 180
EXTRA_DAYS    = 16
OUTPUT_FILE   = "backtest_raw.json"


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
    if not s:
        return None
    try:
        if "/" in s:
            a, b = s.split("/")
            return round(int(a) / int(b) + 1, 3)
        v = float(s)
        return round(v, 3) if v > 1 else round(v + 1, 3)
    except Exception:
        return None


def fetch_odds(event_id: int) -> dict | None:
    data = get(f"{BASE}/event/{event_id}/odds/1/all")
    for book in data.get("markets", []):
        for choice in book.get("choices", []):
            nm = choice.get("name", "")
            fr = choice.get("fractionalValue", "")
            dv = frac2dec(fr)
            if dv and dv > 1:
                if nm == "1":
                    home_o = dv
                elif nm == "2":
                    away_o = dv
        if "home_o" in dir() and "away_o" in dir():
            return {"home": home_o, "away": away_o}
    # fallback: próbáljuk más struktúrával
    for item in data.get("oddsData", {}).get("oddsData", {}).get("markets", []):
        choices = item.get("choices", [])
        ho = ao = None
        for c in choices:
            dv = frac2dec(c.get("fractionalValue", ""))
            if not dv:
                continue
            if c.get("name") == "1":
                ho = dv
            elif c.get("name") == "2":
                ao = dv
        if ho and ao:
            return {"home": ho, "away": ao}
    return None


def calc_form(pid: int, hist: list, before_ts: int):
    recent = sorted(
        [x for x in hist if x.get("startTimestamp", 0) < before_ts],
        key=lambda x: x.get("startTimestamp", 0), reverse=True
    )[:FORM_DAYS * 3]
    wins = total = 0
    fs_wins = fs_total = 0
    for e in recent:
        hid = e.get("homeTeam", {}).get("id")
        hs  = e.get("homeScore", {}).get("current", 0) or 0
        as_ = e.get("awayScore", {}).get("current", 0) or 0
        if hs == as_:
            continue
        won = (hid == pid and hs > as_) or (hid != pid and as_ > hs)
        wins  += int(won)
        total += 1
        p1 = e.get("homeScore", {}).get("period1", 0) or 0
        p2 = e.get("awayScore", {}).get("period1", 0) or 0
        if p1 != p2:
            fs_w = (hid == pid and p1 > p2) or (hid != pid and p2 > p1)
            fs_wins  += int(fs_w)
            fs_total += 1
    return (wins, total), (fs_wins, fs_total)


def calc_h2h(hid: int, aid: int, pool: list, before_ts: int):
    relevant = sorted(
        [x for x in pool if x.get("startTimestamp", 0) < before_ts],
        key=lambda x: x.get("startTimestamp", 0), reverse=True
    )[:20]
    hw = total = 0
    for e in relevant:
        eh = e.get("homeTeam", {}).get("id")
        hs = e.get("homeScore", {}).get("current", 0) or 0
        as_ = e.get("awayScore", {}).get("current", 0) or 0
        if hs == as_:
            continue
        won = (eh == hid and hs > as_) or (eh != hid and as_ > hs)
        hw    += int(won)
        total += 1
    return hw, total


def calc_score(h2h_hw, h2h_t, hfw, hft, afw, aft, hfsw, hfst, afsw, afst):
    h2h_rate   = h2h_hw / h2h_t
    home_rate  = hfw / hft
    away_rate  = afw / aft
    h2h_score  = (h2h_rate - 0.5) * 40
    form_score = (home_rate - away_rate) * 30
    winner     = "home" if h2h_score + form_score > 0 else "away"
    if (h2h_score > 0) != (form_score > 0):
        return winner, None, h2h_rate, home_rate, away_rate, None, None

    score = h2h_score + form_score
    fs_rate = None
    if hfst >= 3 and afst >= 3:
        w_fs = hfsw / hfst if winner == "home" else afsw / afst
        l_fs = afsw / afst if winner == "home" else hfsw / hfst
        fs_rate = w_fs
        score += (w_fs - l_fs) * 20

    w_h2h  = h2h_rate if winner == "home" else 1 - h2h_rate
    w_form = home_rate if winner == "home" else away_rate
    l_form = away_rate if winner == "home" else home_rate

    return winner, score, w_h2h, w_form - l_form, w_form - l_form, fs_rate, w_form - l_form


def main():
    today      = date.today()
    total_days = BACKTEST_DAYS + EXTRA_DAYS

    player_hist: dict[int, list] = defaultdict(list)
    pair_pool:   dict[tuple, list] = defaultdict(list)

    period_start_date = today - timedelta(days=BACKTEST_DAYS)
    all_events = []

    print(f"[COLLECT] {period_start_date} → {today - timedelta(days=1)}", flush=True)
    print(f"          MIN_FORM≥{MIN_FORM}, MIN_H2H≥{MIN_H2H} | kimenet: {OUTPUT_FILE}", flush=True)
    print(f"          Becsült idő: ~{total_days * 0.5:.0f} perc\n", flush=True)

    for day_idx in range(total_days, 0, -1):
        target_date   = today - timedelta(days=day_idx)
        in_sim_period = (target_date >= period_start_date)
        date_str      = target_date.strftime("%Y-%m-%d")

        evs = get(f"{BASE}/sport/table-tennis/scheduled-events/{date_str}").get("events", [])
        day_allowed = [
            e for e in evs
            if is_allowed(e) and e.get("status", {}).get("type", "").lower() == "finished"
        ]

        target_ts        = int(datetime.combine(target_date, datetime.min.time()).timestamp())
        evict_form_cutoff = target_ts - FORM_DAYS * 86400
        evict_h2h_cutoff  = target_ts - H2H_DAYS  * 86400

        for e in day_allowed:
            hid = e.get("homeTeam", {}).get("id")
            aid = e.get("awayTeam", {}).get("id")
            if hid:
                player_hist[hid].append(e)
            if aid:
                player_hist[aid].append(e)
            if hid and aid:
                pair_pool[tuple(sorted([hid, aid]))].append(e)

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

        day_collected = 0
        for ev in day_allowed:
            hid = ev.get("homeTeam", {}).get("id")
            aid = ev.get("awayTeam", {}).get("id")
            ts  = ev.get("startTimestamp", 0)
            hs  = ev.get("homeScore", {}).get("current", 0) or 0
            as_ = ev.get("awayScore", {}).get("current", 0) or 0
            if hs == as_:
                continue
            actual = "home" if hs > as_ else "away"

            (hfw, hft), (hfsw, hfst) = calc_form(hid, player_hist.get(hid, []), ts)
            (afw, aft), (afsw, afst) = calc_form(aid, player_hist.get(aid, []), ts)
            if hft < MIN_FORM or aft < MIN_FORM:
                continue

            key = tuple(sorted([hid, aid]))
            h2h_hw, h2h_t = calc_h2h(hid, aid, pair_pool.get(key, []), ts)
            if h2h_t < MIN_H2H:
                continue

            winner, score, w_h2h, form_diff, _, fs_rate, _ = calc_score(
                h2h_hw, h2h_t, hfw, hft, afw, aft, hfsw, hfst, afsw, afst
            )
            if score is None:
                continue

            odds = fetch_odds(ev.get("id"))
            pred_odds = None
            if odds:
                pred_odds = odds["home"] if winner == "home" else odds["away"]

            all_events.append({
                "date":      date_str,
                "home":      ev.get("homeTeam", {}).get("name", ""),
                "away":      ev.get("awayTeam", {}).get("name", ""),
                "league":    ev.get("tournament", {}).get("name", ""),
                "predicted": winner,
                "actual":    actual,
                "odds":      pred_odds,
                "score":     round(score, 2),
                "h2h_rate":  round(w_h2h, 4),
                "form_diff": round(form_diff, 4),
                "fs_rate":   round(fs_rate, 4) if fs_rate is not None else None,
                "h2h_total": h2h_t,
                "form_total_home": hft,
                "form_total_away": aft,
            })
            day_collected += 1

        if day_collected > 0 or day_idx % 15 == 0:
            print(f"   {date_str} | összegyűjtve ma: {day_collected} | össz: {len(all_events)}", flush=True)

        # Mentés minden 50 eseménynél
        if len(all_events) % 50 == 0 and all_events:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(all_events, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_events, f, ensure_ascii=False, indent=2)

    print(f"\n[DONE] {len(all_events)} esemény mentve → {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
