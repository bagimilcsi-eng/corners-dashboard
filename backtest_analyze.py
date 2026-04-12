"""
TT Bot Backtest Elemzés — backtest_raw.json alapján
"""
import json
from collections import defaultdict
from datetime import datetime, date

# ─── Paraméterek (main.py-ból szinkronizálva) ───────────────────────────────
MIN_FORM_MATCHES   = 8
MIN_H2H_MATCHES    = 5
STRONG_THRESHOLD   = 38.0
FORMA_ONLY_THRESHOLD = 22.0

LEAGUE_FILTERS = {
    "TT Cup":        {"min_score": 38.0, "min_odds": 1.70, "max_odds": 99.0, "h2h_required": True},
    "Setka Cup":     {"min_score": 42.0, "min_odds": 1.70, "max_odds": 1.90, "h2h_required": True},
    "Czech Liga Pro":{"min_score": 22.0, "min_odds": 1.55, "max_odds": 99.0, "h2h_required": False},
}

# ─── Betöltés ────────────────────────────────────────────────────────────────
with open("backtest_raw.json") as f:
    events = json.load(f)

print(f"Betöltve: {len(events):,} esemény")

# ─── Szimulációs logika ──────────────────────────────────────────────────────
tips = []
rejected = defaultdict(int)

for e in events:
    league = e["league"]
    cfg = LEAGUE_FILTERS.get(league)
    if not cfg:
        rejected["ismeretlen liga"] += 1
        continue

    score       = e["score"]           # negatív = away jósolt
    abs_score   = abs(score)
    odds        = e.get("odds") or 0
    h2h_total   = e.get("h2h_total", 0)
    h2h_rate    = e.get("h2h_rate", 0)
    form_home   = e.get("form_total_home", 0)
    form_away   = e.get("form_total_away", 0)
    predicted   = e.get("predicted", "")   # "home" / "away"
    actual      = e.get("actual", "")

    # Forma elégséges?
    if form_home < MIN_FORM_MATCHES or form_away < MIN_FORM_MATCHES:
        rejected["kevés forma"] += 1
        continue

    # H2H elégséges?
    h2h_available = h2h_total >= MIN_H2H_MATCHES
    if cfg["h2h_required"] and not h2h_available:
        rejected["kevés h2h"] += 1
        continue

    # Küszöb meghatározás
    use_h2h   = h2h_available
    threshold = STRONG_THRESHOLD if use_h2h else FORMA_ONLY_THRESHOLD

    # Ellentmondó jelek (ha H2H aktív)
    if use_h2h:
        h2h_score  = (h2h_rate - 0.5) * 40
        form_diff  = e.get("form_diff", 0)
        form_score = form_diff * 20
        if form_score != 0 and h2h_score != 0:
            if (h2h_score > 0) != (form_score > 0):
                rejected["ellentmondó jelek"] += 1
                continue

    # Score küszöb
    if abs_score < threshold:
        rejected["gyenge score"] += 1
        continue

    # Odds tartomány
    if not odds or odds < cfg["min_odds"] or odds > cfg["max_odds"]:
        rejected["odds tartományon kívül"] += 1
        continue

    # Predicted irány szükséges
    if not predicted or abs_score == 0:
        rejected["nincs előrejelzés"] += 1
        continue

    # Nyert-e?
    won = (predicted == actual)

    tips.append({
        "date":      e["date"],
        "league":    league,
        "home":      e["home"],
        "away":      e["away"],
        "predicted": predicted,
        "actual":    actual,
        "odds":      odds,
        "score":     abs_score,
        "won":       won,
        "use_h2h":   use_h2h,
    })

# ─── Eredmények ──────────────────────────────────────────────────────────────
print(f"\n{'═'*60}")
print(f"  TIPPEK SZÁMA: {len(tips):,}")
print(f"{'═'*60}")

if not tips:
    print("Nincsenek tippek a megadott paraméterekkel.")
    exit()

# Dátum tartomány
dates = sorted(set(t["date"] for t in tips))
total_days = (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days + 1
tip_days   = len(dates)

print(f"  Időszak:      {dates[0]} → {dates[-1]}  ({total_days} nap)")
print(f"  Aktív napok:  {tip_days} nap")
print(f"  Napi átlag:   {len(tips)/total_days:.2f} tipp/nap")

# ROI összesített
wins  = sum(1 for t in tips if t["won"])
losses= len(tips) - wins
profit = sum((t["odds"] - 1) for t in tips if t["won"]) - losses
roi    = profit / len(tips) * 100
winrate= wins / len(tips) * 100

print(f"\n  Nyert:        {wins:,} ({winrate:.1f}%)")
print(f"  Veszített:    {losses:,}")
print(f"  Profit:       {profit:+.2f} egység (1 egység/tipp)")
print(f"  ROI:          {roi:+.2f}%")

# ─── Per-liga bontás ─────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"  LIGÁNKÉNTI BONTÁS")
print(f"{'─'*60}")
print(f"  {'Liga':<18} {'Tipp':>5} {'Nyert':>5} {'Win%':>6} {'ROI':>7} {'Napi':>5} {'Szorzó':>7}")
print(f"  {'─'*60}")

by_league = defaultdict(list)
for t in tips:
    by_league[t["league"]].append(t)

for league, ltips in sorted(by_league.items()):
    lw   = sum(1 for t in ltips if t["won"])
    ll   = len(ltips) - lw
    lp   = sum((t["odds"]-1) for t in ltips if t["won"]) - ll
    lroi = lp / len(ltips) * 100
    lwp  = lw / len(ltips) * 100
    lavg = sum(t["odds"] for t in ltips) / len(ltips)
    l_days = (datetime.fromisoformat(max(t["date"] for t in ltips)) -
              datetime.fromisoformat(min(t["date"] for t in ltips))).days + 1
    print(f"  {league:<18} {len(ltips):>5} {lw:>5} {lwp:>5.1f}% {lroi:>+6.1f}% {len(ltips)/l_days:>5.2f}  {lavg:>6.2f}")

# ─── H2H vs Forma-only ───────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"  H2H vs FORMA-ONLY MÓDOK")
print(f"{'─'*60}")
print(f"  {'Mód':<14} {'Tipp':>5} {'Nyert':>5} {'Win%':>6} {'ROI':>7}")
print(f"  {'─'*40}")
for use_h2h_val, label in [(True, "H2H mód"), (False, "Forma-only")]:
    subset = [t for t in tips if t["use_h2h"] == use_h2h_val]
    if not subset:
        continue
    sw  = sum(1 for t in subset if t["won"])
    sl  = len(subset) - sw
    sp  = sum((t["odds"]-1) for t in subset if t["won"]) - sl
    sr  = sp / len(subset) * 100
    print(f"  {label:<14} {len(subset):>5} {sw:>5} {sw/len(subset)*100:>5.1f}% {sr:>+6.1f}%")

# ─── Havi bontás ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"  HAVI BONTÁS")
print(f"{'─'*60}")
print(f"  {'Hónap':<10} {'Tipp':>5} {'Nyert':>5} {'Win%':>6} {'ROI':>7} {'Profit':>8}")
print(f"  {'─'*50}")

by_month = defaultdict(list)
for t in tips:
    ym = t["date"][:7]
    by_month[ym].append(t)

for ym in sorted(by_month):
    mt = by_month[ym]
    mw = sum(1 for t in mt if t["won"])
    ml = len(mt) - mw
    mp = sum((t["odds"]-1) for t in mt if t["won"]) - ml
    mr = mp / len(mt) * 100
    bar = "+" * int(max(mp, 0)) + "-" * int(max(-mp, 0))
    print(f"  {ym:<10} {len(mt):>5} {mw:>5} {mw/len(mt)*100:>5.1f}% {mr:>+6.1f}% {mp:>+8.2f}  {bar[:20]}")

# ─── Odds sávok ──────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"  ODDS SÁVOK")
print(f"{'─'*60}")
print(f"  {'Sáv':<14} {'Tipp':>5} {'Nyert':>5} {'Win%':>6} {'ROI':>7}")
print(f"  {'─'*40}")
buckets = [(1.0,1.5),(1.5,1.7),(1.7,1.9),(1.9,2.2),(2.2,3.0),(3.0,99)]
for lo, hi in buckets:
    subset = [t for t in tips if lo <= t["odds"] < hi]
    if not subset:
        continue
    sw = sum(1 for t in subset if t["won"])
    sl = len(subset) - sw
    sp = sum((t["odds"]-1) for t in subset if t["won"]) - sl
    sr = sp / len(subset) * 100
    print(f"  {lo:.1f}–{hi:.1f}{'+'if hi==99 else '':<10} {len(subset):>5} {sw:>5} {sw/len(subset)*100:>5.1f}% {sr:>+6.1f}%")

# ─── Score sávok ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"  SCORE SÁVOK (bizonyossági szint)")
print(f"{'─'*60}")
print(f"  {'Sáv':<12} {'Tipp':>5} {'Nyert':>5} {'Win%':>6} {'ROI':>7}")
print(f"  {'─'*40}")
sbuckets = [(22,30),(30,38),(38,46),(46,55),(55,100)]
for lo, hi in sbuckets:
    subset = [t for t in tips if lo <= t["score"] < hi]
    if not subset:
        continue
    sw = sum(1 for t in subset if t["won"])
    sl = len(subset) - sw
    sp = sum((t["odds"]-1) for t in subset if t["won"]) - sl
    sr = sp / len(subset) * 100
    print(f"  {lo}–{hi:<8} {len(subset):>5} {sw:>5} {sw/len(subset)*100:>5.1f}% {sr:>+6.1f}%")

# ─── Kizárási okok ───────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"  KIZÁRÁSOK ({sum(rejected.values()):,} esemény)")
print(f"{'─'*60}")
for reason, cnt in sorted(rejected.items(), key=lambda x: -x[1]):
    print(f"  {reason:<25}: {cnt:>7,}")

print(f"\n{'═'*60}")
