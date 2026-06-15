#!/usr/bin/env python3
"""
Lotteria VM 2026 - Backend server
Fetches live results from ESPN API and serves them to the frontend.
"""

import json
import time
import threading
from datetime import date, timedelta
from pathlib import Path

import requests
from flask import Flask, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ESPN abbreviation → Norwegian name
TEAM_MAP = {
    'ALG': 'Algerie',
    'ARG': 'Argentina',
    'AUS': 'Australia',
    'AUT': 'Østerrike',
    'BEL': 'Belgia',
    'BIH': 'Bosnia-Hercegovina',
    'BRA': 'Brasil',
    'CAN': 'Canada',
    'CPV': 'Kapp Verde',
    'COL': 'Colombia',
    'COD': 'DR Kongo',
    'CRO': 'Kroatia',
    'CUW': 'Curaçao',
    'CZE': 'Tsjekkia',
    'ECU': 'Ecuador',
    'EGY': 'Egypt',
    'ENG': 'England',
    'FRA': 'Frankrike',
    'GER': 'Tyskland',
    'GHA': 'Ghana',
    'HAI': 'Haiti',
    'IRN': 'Iran',
    'IRQ': 'Irak',
    'CIV': 'Elfenbenskysten',
    'JPN': 'Japan',
    'JOR': 'Jordan',
    'MEX': 'Mexico',
    'MAR': 'Marokko',
    'NED': 'Nederland',
    'NZL': 'New Zealand',
    'NOR': 'Norge',
    'PAN': 'Panama',
    'PAR': 'Paraguay',
    'POR': 'Portugal',
    'QAT': 'Qatar',
    'KSA': 'Saudi-Arabia',
    'SCO': 'Skottland',
    'SEN': 'Senegal',
    'RSA': 'Sør-Afrika',
    'KOR': 'Sør-Korea',
    'ESP': 'Spania',
    'SWE': 'Sverige',
    'SUI': 'Sveits',
    'TUN': 'Tunisia',
    'TUR': 'Tyrkia',
    'USA': 'USA',
    'URU': 'Uruguay',
    'UZB': 'Usbekistan',
}

# Load match schedule and build lookup: (home_name, away_name) → match_num
with open(Path(__file__).parent / 'data.json') as f:
    MATCH_DATA = json.load(f)

MATCH_LOOKUP = {}
for m in MATCH_DATA['matches']:
    MATCH_LOOKUP[(m['home'], m['away'])] = m['num']

# Cache: refreshed every 60 seconds
_cache = {'results': {}, 'live': [], 'updated': 0}
_cache_lock = threading.Lock()

ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/FIFA.WORLD/scoreboard'

# Group stage dates: June 11–28, 2026
TOURNAMENT_DATES = [
    (date(2026, 6, 11) + timedelta(days=i)).strftime('%Y%m%d')
    for i in range(18)
]


def fetch_day(date_str):
    """Fetch ESPN scoreboard for a given date string (YYYYMMDD)."""
    try:
        r = requests.get(ESPN_BASE, params={'dates': date_str}, timeout=8)
        r.raise_for_status()
        return r.json().get('events', [])
    except Exception as e:
        print(f'ESPN fetch error for {date_str}: {e}')
        return []


def parse_events(events):
    """Parse ESPN events into {match_num: result_dict}."""
    results = {}
    live = []

    for event in events:
        comp = event['competitions'][0]
        status = comp['status']['type']
        state = status.get('state', '')          # pre / in / post
        completed = status.get('completed', False)

        home_comp = next((c for c in comp['competitors'] if c['homeAway'] == 'home'), None)
        away_comp = next((c for c in comp['competitors'] if c['homeAway'] == 'away'), None)
        if not home_comp or not away_comp:
            continue

        home_abr = home_comp['team']['abbreviation']
        away_abr = away_comp['team']['abbreviation']
        home_name = TEAM_MAP.get(home_abr, home_abr)
        away_name = TEAM_MAP.get(away_abr, away_abr)

        match_num = MATCH_LOOKUP.get((home_name, away_name))
        if match_num is None:
            # Try reversed (sometimes ESPN swaps home/away vs schedule)
            match_num = MATCH_LOOKUP.get((away_name, home_name))
            if match_num:
                home_name, away_name = away_name, home_name
                home_comp, away_comp = away_comp, home_comp

        if match_num is None:
            print(f'No match found for {home_name} vs {away_name}')
            continue

        home_score = int(home_comp.get('score', 0)) if state in ('in', 'post') else None
        away_score = int(away_comp.get('score', 0)) if state in ('in', 'post') else None

        result = {
            'home_score': home_score,
            'away_score': away_score,
            'state': state,          # pre / in / post
            'completed': completed,
            'clock': comp['status'].get('displayClock', ''),
            'status_name': status.get('shortDetail', status.get('name', '')),
        }
        results[match_num] = result

        if state == 'in':
            live.append({
                'match_num': match_num,
                'home': home_name,
                'away': away_name,
                'home_score': home_score,
                'away_score': away_score,
                'clock': result['clock'],
            })

    return results, live


def refresh_cache():
    """Fetch all tournament days and update cache."""
    all_results = {}
    all_live = []
    today = date.today().strftime('%Y%m%d')

    for date_str in TOURNAMENT_DATES:
        events = fetch_day(date_str)
        if events:
            results, live = parse_events(events)
            all_results.update(results)
            if date_str == today:
                all_live.extend(live)

    with _cache_lock:
        _cache['results'] = all_results
        _cache['live'] = all_live
        _cache['updated'] = time.time()

    print(f'Cache refreshed: {len(all_results)} results, {len(all_live)} live')


def background_refresh():
    """Refresh every 60 seconds."""
    while True:
        try:
            refresh_cache()
        except Exception as e:
            print(f'Refresh error: {e}')
        time.sleep(60)


# Start background refresh (works both with gunicorn and direct python)
threading.Thread(target=background_refresh, daemon=True).start()

# ── Routes ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_file(Path(__file__).parent / 'index.html')

@app.route('/api/results')
def api_results():
    with _cache_lock:
        age = time.time() - _cache['updated']
        return jsonify({
            'results': _cache['results'],
            'live': _cache['live'],
            'cache_age_seconds': int(age),
        })

@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    """Manual refresh endpoint."""
    threading.Thread(target=refresh_cache, daemon=True).start()
    return jsonify({'status': 'refreshing'})


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 3456))
    print(f'Starting Lotteria VM 2026 server on http://localhost:{port}')
    app.run(port=port, debug=False)
