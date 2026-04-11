#!/usr/bin/env python3
"""
backtest_collect_basketball.py — Kosárlabda O/U backtest adatgyűjtő (180 nap)
SofaScore alapú. Poisson modell, edge + confidence. Offline optimalizáláshoz.
"""
import os, time, math, json, requests
from datetime import datetime, date, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":    "https://www.sofascore.com/basketball/livescore",
    "Accept":     "application/json, text/plain, */*",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8",
    "Cache-Control": "no-cache", "Pragma": "no-cache",
}
BASE    = "https://www.sofascore.com/api/v1"
DELAY   = 0.4
OUTPUT  = "backtest_raw_basketball.json"

BACKTEST_DAYS  = 180
MIN_MATCHES    = 6


def sofa_get(url: str) -> dict:
    time.sleep(DELAY)
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def is_overtime(match: dict) -> bool:
    for side in ("homeScore", "awayScore"):
        s = match.get(side, {})
        if s.get("overtime") or s.get("period5") is not None:
            return True
    return False


def fetch_team_matches(team_id: int, last_n: int = 12) -> list:
    data = sofa_get(f"{BASE}/team/{team_id}/events/last/0")
    return [e for e in data.get("events", [])
            if e.get("status", {}).get("type") == "finished"][:last_n]


def fetch_h2h(event_id: int) -> list:
    data = sofa_get(f"{BASE}/event/{event_id}/h2h/events")
    return [e for e in data.get("events", [])
            if e.get("status", {}).get("type") == "finished"]


def _parse_odds(val) -> float | None:
    if not val:
        return None
    s = str(val).strip()
    if "/" in s:
        try:
            n, d = s.split("/")
            v = float(n) / float(d) + 1.0
            return round(v, 2) if v >= 1.01 else None
        except Exception:
            return None
    try:
        v = float(s)
        return round(v, 2) if v >= 1.01 else None
    except Exception:
        return None


def fetch_odds(event_id: int) -> dict | None:
    data = sofa_get(f"{BASE}/event/{event_id}/odds/1/all")
    markets = data.get("markets") or []
    for mkt in markets:
        name = (mkt.get("marketName") or mkt.get("name") or "").lower()
        if not any(kw in name for kw in ["total", "over/under", "points"]):
            continue
        choices = mkt.get("choices") or []
        ov = next((c for c in choices if (c.get("name") or "").lower().startswith("over")), None)
        un = next((c for c in choices if (c.get("name") or "").lower().startswith("under")), None)
        if not ov:
            continue
        raw_pt = (mkt.get("choiceGroup") or mkt.get("handicap")
                  or ov.get("handicap") or "")
        try:
            line = float(str(raw_pt))
        except Exception:
            continue
        if line < 50:
            continue
        over_odds  = _parse_odds(ov.get("fractionalValue"))
        under_odds = _parse_odds(un.get("fractionalValue")) if un else None
        return {"line": line, "over": over_odds, "under": under_odds}
    return None


def poisson_over_prob(expected: float, line: float) -> float:
    if expected <= 0:
        return 0.0
    z = (line + 0.5 - expected) / math.sqrt(expected)
    return round(0.5 * math.erfc(z / math.sqrt(2)), 4)


def calc_team_stats(team_id: int, is_home: bool, before_ts: int) -> dict | None:
    matches = fetch_team_matches(team_id, last_n=12)
    matches = [m for m in matches if m.get("startTimestamp", 0) < before_ts]
    if len(matches) < MIN_MATCHES:
        return None

    regular = [m for m in matches if not is_overtime(m)]
    ha = [m for m in regular
          if (is_home and m.get("homeTeam", {}).get("id") == team_id)
          or (not is_home and m.get("awayTeam", {}).get("id") == team_id)]
    pool = ha if len(ha) >= MIN_MATCHES else regular
    if len(pool) < MIN_MATCHES:
        return None

    scored, conceded, totals = [], [], []
    last_ts = 0
    for m in pool[:10]:
        hs  = m.get("homeScore", {}).get("current")
        as_ = m.get("awayScore", {}).get("current")
        if hs is None or as_ is None:
            continue
        ts = m.get("startTimestamp", 0)
        if ts > last_ts:
            last_ts = ts
        if m.get("homeTeam", {}).get("id") == team_id:
            scored.append(hs); conceded.append(as_)
        else:
            scored.append(as_); conceded.append(hs)
        totals.append(hs + as_)

    if len(scored) < MIN_MATCHES:
        return None

    return {
        "off": round(sum(scored)   / len(scored),   1),
        "def": round(sum(conceded) / len(conceded), 1),
        "pace":  round(sum(totals) / len(totals),   1),
        "last5": round(sum(totals[-5:]) / min(5, len(totals)), 1),
        "n":     len(scored),
        "last_ts": last_ts,
    }


def main():
    today        = date.today()
    period_start = today - timedelta(days=BACKTEST_DAYS)
    all_events   = []

    print(f"[BASKETBALL COLLECT] {period_start} → {today - timedelta(days=1)}", flush=True)
    print(f"  MIN_MATCHES: {MIN_MATCHES} | Output: {OUTPUT}", flush=True)
    print(f"  API delay: {DELAY}s\n", flush=True)

    for day_idx in range(BACKTEST_DAYS, 0, -1):
        target_date = today - timedelta(days=day_idx)
        date_str    = target_date.strftime("%Y-%m-%d")

        data   = sofa_get(f"{BASE}/sport/basketball/scheduled-events/{date_str}")
        events = [e for e in data.get("events", [])
                  if e.get("status", {}).get("type") == "finished"]

        day_count = 0
        for ev in events:
            ev_id   = ev.get("id")
            home_id = ev.get("homeTeam", {}).get("id")
            away_id = ev.get("awayTeam", {}).get("id")
            ts      = ev.get("startTimestamp", 0)
            hs      = ev.get("homeScore", {}).get("current")
            as_     = ev.get("awayScore", {}).get("current")

            if not home_id or not away_id or hs is None or as_ is None:
                continue
            if is_overtime(ev):
                continue

            actual_total = hs + as_

            home_stats = calc_team_stats(home_id, True,  ts)
            away_stats = calc_team_stats(away_id, False, ts)
            if not home_stats or not away_stats:
                continue

            # Várható total
            lg_avg = 100.0
            expected = round(
                (home_stats["off"] + away_stats["def"]) / 2 +
                (away_stats["off"] + home_stats["def"]) / 2, 1
            )

            # Odds + vonal
            odds = fetch_odds(ev_id)
            if not odds:
                continue
            line = odds["line"]

            edge = round(expected - line, 1)
            prob_over  = poisson_over_prob(expected, line)
            prob_under = round(1.0 - prob_over, 4)

            # Confidence (0-100): milyen erős a jel
            confidence = round(min(100, abs(edge) * 8 + (abs(prob_over - 0.5)) * 60), 1)

            # H2H
            h2h = fetch_h2h(ev_id)
            h2h_totals = []
            for m in h2h:
                if is_overtime(m):
                    continue
                mhs = m.get("homeScore", {}).get("current")
                mas = m.get("awayScore", {}).get("current")
                if mhs is not None and mas is not None:
                    h2h_totals.append(mhs + mas)
            h2h_avg = round(sum(h2h_totals) / len(h2h_totals), 1) if h2h_totals else None

            direction = "over" if edge > 0 else "under"
            pred_odds = odds.get("over") if direction == "over" else odds.get("under")
            actual_over = actual_total > line

            tournament = ev.get("tournament", {})
            all_events.append({
                "date":         date_str,
                "event_id":     ev_id,
                "home":         ev.get("homeTeam", {}).get("name", ""),
                "away":         ev.get("awayTeam", {}).get("name", ""),
                "league":       tournament.get("name", ""),
                "category":     tournament.get("category", {}).get("name", ""),
                "actual_total": actual_total,
                "actual_over":  actual_over,
                "line":         line,
                "expected":     expected,
                "edge":         edge,
                "prob_over":    prob_over,
                "prob_under":   prob_under,
                "confidence":   confidence,
                "direction":    direction,
                "h2h_avg":      h2h_avg,
                "h2h_count":    len(h2h_totals),
                "home_pace":    home_stats["pace"],
                "away_pace":    away_stats["pace"],
                "home_last5":   home_stats["last5"],
                "away_last5":   away_stats["last5"],
                "home_n":       home_stats["n"],
                "away_n":       away_stats["n"],
                "over_odds":    odds.get("over"),
                "under_odds":   odds.get("under"),
                "pred_odds":    pred_odds,
            })
            day_count += 1

        print(f"   {date_str} | meccsek: {day_count} | össz: {len(all_events)}", flush=True)

        if len(all_events) % 30 == 0 and all_events:
            with open(OUTPUT, "w", encoding="utf-8") as f:
                json.dump(all_events, f, ensure_ascii=False, indent=2)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(all_events, f, ensure_ascii=False, indent=2)

    print(f"\n[DONE] {len(all_events)} meccs mentve → {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
