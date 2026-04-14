import requests
import json
from datetime import datetime, timedelta
import time

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Referer': 'https://www.sofascore.com/'
}

def frac_to_dec(f):
    if f is None: return None
    try:
        s = str(f)
        if '/' in s:
            a, b = s.split('/')
            return round(int(a)/int(b)+1, 3)
        return round(float(s)+1, 3)
    except:
        return None

results = []
errors = 0

# 90 nap visszamenőleg
for days_ago in range(1, 91):
    date_str = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
    
    try:
        resp = requests.get(
            f'https://api.sofascore.com/api/v1/sport/volleyball/scheduled-events/{date_str}',
            headers=headers, timeout=10
        )
        if resp.status_code != 200:
            continue
        
        events = resp.json().get('events', [])
        
        for e in events:
            status = e.get('status', {}).get('type', '')
            if status != 'finished':
                continue
            
            eid = e['id']
            home = e['homeTeam']['name']
            away = e['awayTeam']['name']
            league = e['tournament']['name']
            
            # Eredmény
            score = e.get('homeScore', {})
            home_sets = score.get('current', 0)
            away_sets = e.get('awayScore', {}).get('current', 0)
            if home_sets == 0 and away_sets == 0:
                continue
            winner = 'home' if home_sets > away_sets else 'away'
            
            # Odds
            try:
                odds_resp = requests.get(
                    f'https://api.sofascore.com/api/v1/event/{eid}/odds/1/all',
                    headers=headers, timeout=5
                )
                if odds_resp.status_code != 200:
                    continue
                
                markets = odds_resp.json().get('markets', [])
                for m in markets:
                    if m.get('marketName') == 'Full time':
                        choices = m.get('choices', [])
                        h_frac = next((c['fractionalValue'] for c in choices if c['name']=='1'), None)
                        a_frac = next((c['fractionalValue'] for c in choices if c['name']=='2'), None)
                        h_odds = frac_to_dec(h_frac)
                        a_odds = frac_to_dec(a_frac)
                        
                        if h_odds and a_odds and h_odds > 1.0 and a_odds > 1.0:
                            margin = 1/h_odds + 1/a_odds
                            h_prob = 1/h_odds/margin
                            a_prob = 1/a_odds/margin
                            
                            fav_team = 'home' if h_odds <= a_odds else 'away'
                            fav_odds = min(h_odds, a_odds)
                            dog_odds = max(h_odds, a_odds)
                            fav_prob = max(h_prob, a_prob)
                            fav_won = (winner == fav_team)
                            
                            results.append({
                                'date': date_str,
                                'league': league,
                                'home': home,
                                'away': away,
                                'fav_odds': fav_odds,
                                'dog_odds': dog_odds,
                                'fav_prob': fav_prob,
                                'fav_won': fav_won,
                                'winner': winner
                            })
            except:
                pass
        
        time.sleep(0.2)
        
        if days_ago % 10 == 0:
            print(f'  Feldolgozva: {days_ago} nap, {len(results)} meccs eddig')
    
    except Exception as ex:
        errors += 1

print(f'\n=== BACKTEST KÉSZ: {len(results)} meccs, {errors} hiba ===\n')

if not results:
    print('Nincs adat!')
else:
    # Statisztikák szorzó tartományonként
    brackets = [
        (1.0, 1.3, 'Nagyon erős fav (@1.01–1.30)'),
        (1.3, 1.5, 'Erős fav (@1.30–1.50)'),
        (1.5, 1.7, 'Közepes fav (@1.50–1.70)'),
        (1.7, 1.9, 'Kis fav (@1.70–1.90)'),
        (1.9, 5.0, 'Szinte egyenlő (@1.90+)'),
    ]
    
    print(f"{'Kategória':<35} {'N':>5} {'Win%':>7} {'ROI':>8} {'Break-even':>12}")
    print('-'*72)
    
    for lo, hi, label in brackets:
        group = [r for r in results if lo <= r['fav_odds'] < hi]
        if len(group) < 5:
            continue
        n = len(group)
        wins = sum(1 for r in group if r['fav_won'])
        avg_odds = sum(r['fav_odds'] for r in group) / n
        roi = (wins * avg_odds - n) / n * 100
        be = 1/avg_odds*100
        print(f'{label:<35} {n:>5} {wins/n*100:>6.1f}% {roi:>+7.1f}% {be:>11.1f}%')
    
    print()
    total = len(results)
    total_fav_wins = sum(1 for r in results if r['fav_won'])
    print(f'Összesen: {total} meccs, fav win%: {total_fav_wins/total*100:.1f}%')

