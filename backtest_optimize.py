#!/usr/bin/env python3
"""
backtest_optimize.py — Offline paraméter optimalizáló a backtest_raw.json alapján.
Megkeresi azt a paraméterkombinációt, ami napi 2-4 tippet és >10% havi ROI-t ad.
"""
import json
from collections import defaultdict
from itertools import product

RAW_FILE = "backtest_raw.json"

with open(RAW_FILE, encoding="utf-8") as f:
    events = json.load(f)

# Csak azok ahol van odds ÉS a direction helyes (score > 0 → winner == predicted)
events = [e for e in events if e.get("odds") and e["odds"] >= 1.30]

total_days = len(set(e["date"] for e in events))
print(f"Betöltve: {len(events)} esemény, {total_days} nap\n")

# ── Paraméter rács ────────────────────────────────────────────────────────────
SCORE_VALS    = [26.5, 30, 33, 35, 38, 40, 42, 45]
H2H_RATE_VALS = [0.65, 0.68, 0.70, 0.72, 0.75, 0.78]
FORM_DIFF_VALS= [0.18, 0.20, 0.22, 0.25, 0.28, 0.30]
FS_RATE_VALS  = [0.55, 0.58, 0.60, 0.62, 0.65]
MIN_ODDS_VALS = [1.45, 1.50, 1.55]

results_table = []

for score_t, h2h_t, fd_t, fs_t, mo_t in product(
    SCORE_VALS, H2H_RATE_VALS, FORM_DIFF_VALS, FS_RATE_VALS, MIN_ODDS_VALS
):
    tips = []
    for e in events:
        if e["score"] < score_t:
            continue
        if e["h2h_rate"] < h2h_t:
            continue
        if e["form_diff"] < fd_t:
            continue
        if e["fs_rate"] is not None and e["fs_rate"] < fs_t:
            continue
        if e["odds"] < mo_t:
            continue
        tips.append(e)

    n = len(tips)
    if n < 20:
        continue

    wins   = sum(1 for t in tips if t["predicted"] == t["actual"])
    wr     = wins / n
    roi    = sum(
        (t["odds"] - 1) if t["predicted"] == t["actual"] else -1
        for t in tips
    ) / n * 100
    avg_o  = sum(t["odds"] for t in tips) / n
    per_day = n / total_days

    # Havi ROI: minden hónap ROI-ját kiszámítjuk, majd átlagoljuk
    monthly = defaultdict(lambda: {"w": 0, "n": 0, "r": 0.0})
    for t in tips:
        m = t["date"][:7]
        monthly[m]["n"] += 1
        monthly[m]["w"] += int(t["predicted"] == t["actual"])
        monthly[m]["r"] += (t["odds"] - 1) if t["predicted"] == t["actual"] else -1
    month_rois = [v["r"] / v["n"] * 100 for v in monthly.values() if v["n"] >= 5]
    avg_monthly_roi = sum(month_rois) / len(month_rois) if month_rois else 0
    min_monthly_roi = min(month_rois) if month_rois else 0

    results_table.append({
        "score":    score_t,
        "h2h":      h2h_t,
        "fd":       fd_t,
        "fs":       fs_t,
        "mo":       mo_t,
        "n":        n,
        "per_day":  per_day,
        "wr":       wr,
        "roi":      roi,
        "avg_o":    avg_o,
        "avg_m_roi": avg_monthly_roi,
        "min_m_roi": min_monthly_roi,
    })

# ── Szűrés: napi 2-4 tipp ÉS avg havi ROI > 10% ────────────────────────────
candidates = [
    r for r in results_table
    if 1.5 <= r["per_day"] <= 5.0 and r["avg_m_roi"] >= 10.0
]

# Rendezés: min havi ROI szerint (a legstabilabb eredmény)
candidates.sort(key=lambda x: (-x["min_m_roi"], -x["avg_m_roi"]))

print(f"{'SCORE':>6} {'H2H%':>5} {'FD%':>5} {'FS%':>5} {'MO':>5} "
      f"{'N':>5} {'/nap':>5} {'WR%':>6} {'ROI%':>7} {'avgMROI':>8} {'minMROI':>8}")
print("─" * 80)

for r in candidates[:30]:
    print(f"{r['score']:>6.1f} {r['h2h']*100:>5.0f} {r['fd']*100:>5.0f} "
          f"{r['fs']*100:>5.0f} {r['mo']:>5.2f} "
          f"{r['n']:>5} {r['per_day']:>5.1f} {r['wr']*100:>6.1f} "
          f"{r['roi']:>+7.1f} {r['avg_m_roi']:>+8.1f} {r['min_m_roi']:>+8.1f}")

if not candidates:
    print("Nincs olyan kombináció, ami napi 2-4 tippet ÉS >10% havi ROI-t ad.")
    print("\nLegjobb 10 eredmény (csak ROI szerint, bármilyen tippszám):")
    best = sorted(results_table, key=lambda x: -x["avg_m_roi"])[:10]
    for r in best:
        print(f"  score≥{r['score']} h2h≥{r['h2h']*100:.0f}% fd≥{r['fd']*100:.0f}pp "
              f"fs≥{r['fs']*100:.0f}% mo≥{r['mo']} → "
              f"{r['per_day']:.1f}/nap  WR={r['wr']*100:.1f}%  ROI={r['avg_m_roi']:+.1f}%")
else:
    best = candidates[0]
    print(f"\n{'═'*80}")
    print(f"  LEGJOBB BEÁLLÍTÁS:")
    print(f"    STRONG_THRESHOLD  = {best['score']}")
    print(f"    MIN_H2H_RATE      = {best['h2h']}  ({best['h2h']*100:.0f}%)")
    print(f"    MIN_FORM_DIFF     = {best['fd']}  ({best['fd']*100:.0f}pp)")
    print(f"    MIN_FIRST_SET_RATE= {best['fs']}  ({best['fs']*100:.0f}%)")
    print(f"    MIN_ODDS          = {best['mo']}")
    print(f"")
    print(f"    Tippek/nap:    {best['per_day']:.1f}")
    print(f"    Win rate:      {best['wr']*100:.1f}%")
    print(f"    Átlag szorzó:  {best['avg_o']:.2f}")
    print(f"    Össz ROI:      {best['roi']:+.1f}%")
    print(f"    Átlag havi ROI:{best['avg_m_roi']:+.1f}%")
    print(f"    Min havi ROI:  {best['min_m_roi']:+.1f}%")
    print(f"{'═'*80}")
