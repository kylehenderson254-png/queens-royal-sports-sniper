import json
import re
import sqlite3
import hashlib
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st

st.set_page_config(page_title="Royal Sports Sniper | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
POLY = "https://gamma-api.polymarket.com"
DB = Path(os.environ.get("ROYAL_DB_PATH", "royal_sports_history.db"))
STARTING_BANKROLL = 10000.0
PAPER_SIZES = {"STRONG": 100.0, "MODERATE": 75.0, "QUALIFIED": 50.0}
SPORTS = {"NFL": ["nfl", "football", "touchdown", "passing yards", "rushing yards"], "NBA": ["nba", "basketball", "rebounds", "assists", "wnba"], "MLB": ["mlb", "baseball", "strikeouts"], "NHL": ["nhl", "hockey", "shots on goal"], "Soccer": ["soccer", "premier league", "champions league", "epl", "mls", "uefa", "fifa"], "Tennis": ["tennis", "atp", "wta", "wimbledon"], "MMA": ["ufc", "mma", "boxing"], "Golf": ["golf", "pga"], "NCAAF": ["ncaaf", "college football"], "NCAAB": ["ncaab", "college basketball", "march madness"], "College": ["ncaa"]}
BLOCKED = ("president", "election", "secretary general", "elon musk", "political", "prime minister", "congress", "senate", "cabinet", "pope", "nato")

st.markdown('''<style>
.stApp{background:radial-gradient(ellipse at top,#291044 0%,#110a20 52%,#090611 100%);color:#f5eeff}
.block-container{max-width:1240px;padding-top:1rem;padding-bottom:3rem}
h1,h2,h3{color:#ebd3ff!important}
div[data-testid="stMetric"]{background:linear-gradient(135deg,#35204e,#1b112d);border:1px solid #7c3cc1;border-radius:16px;padding:13px}
div[data-testid="stMetricValue"]{color:#f5d47d}
div[data-testid="stExpander"]{background:#1d1230;border:1px solid #55357b;border-radius:14px}
div.stButton>button{background:linear-gradient(110deg,#7426bd,#aa48eb);color:white;border:0;border-radius:12px;font-weight:700}
a{color:#d8a7ff!important}
.royal-head{color:#f3d47d;font-size:1.12rem;font-weight:800;margin-top:1rem}
.royal-sub{color:#c8b2e5;font-size:.9rem}
</style>''', unsafe_allow_html=True)


def get_json(url, params=None):
    r = requests.get(url, params=params, headers={"Accept": "application/json", "User-Agent": "RoyalSportsSniper/1.3"}, timeout=17)
    r.raise_for_status()
    return r.json()


def decimal_price(value, cents=False):
    try:
        x = float(value)
        if cents:
            x /= 100
        return x if 0 < x < 1 else None
    except (TypeError, ValueError):
        return None


def kalshi_no_ask(m):
    v = decimal_price(m.get("no_ask_dollars"))
    return v if v is not None else decimal_price(m.get("no_ask"), cents=True)


def kalshi_ask(m):
    value = decimal_price(m.get("yes_ask_dollars"))
    if value is not None:
        return value
    return decimal_price(m.get("yes_ask"), cents=True)


def sport_from_text(text):
    low = text.lower()
    for name, words in SPORTS.items():
        if any(re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", low) for word in words):
            return name
    return None


def kind_of(text):
    t = text.lower()
    if any(x in t for x in ("rebounds", "assists", "touchdown", "passing yards", "rushing yards", "strikeouts", "player points", "shots on goal", "three-pointers")):
        return "Player Props"
    if any(x in t for x in ("parlay", "combo", "same game")):
        return "Combos"
    if any(x in t for x in ("spread", "handicap", "cover the")):
        return "Spreads"
    if any(x in t for x in ("over", "under", "total points", "total goals", "total runs")):
        return "Totals"
    return "Moneyline / Other"


def valid_sports(text, verified=False):
    low = text.lower()
    return not any(b in low for b in BLOCKED) and (verified or sport_from_text(text) is not None)


def kalshi_markets():
    # Series category is authoritative; do not trust the broad /events category argument.
    series_data = get_json(f"{KALSHI}/series", {"category": "Sports"})
    series = [s for s in series_data.get("series", []) if s.get("category", "").lower() == "sports"]
    # Prefer popular game and player prop series; avoid alphabetical soccer-only sampling.
    def priority(item):
        t = (str(item.get("ticker", "")) + " " + str(item.get("title", ""))).lower()
        groups = ["nba", "nfl", "ncaab", "ncaaf", "nhl", "mlb", "wnba", "epl", "mls", "ufc", "tennis"]
        league = next((i for i, name in enumerate(groups) if name in t), len(groups))
        game = 0 if any(w in t for w in ("game", "match", "points", "touchdown", "rebound", "assist", "spread", "total")) else 1
        return (league, game, t)
    series.sort(key=priority)
    results = []
    for s in series[:80]:
        ticker = s.get("ticker")
        if not ticker:
            continue
        try:
            payload = get_json(f"{KALSHI}/markets", {"series_ticker": ticker, "status": "open", "limit": 100})
        except requests.RequestException:
            continue
        for m in payload.get("markets", []):
            title = m.get("title") or m.get("subtitle") or s.get("title") or ticker
            combined = f"{s.get('title', '')} {ticker} {title}"
            if not valid_sports(combined, verified=True):
                continue
            results.append({"platform": "Kalshi", "id": m.get("ticker", ""), "title": title,
                "sport": sport_from_text(combined) or "Other Sports", "type": kind_of(title),
                "price": kalshi_ask(m), "no_price": kalshi_no_ask(m), "url": "https://kalshi.com/markets/" + quote(str(ticker).lower()),
                "volume": float(m.get("volume") or 0), "close": m.get("close_time", "")})
    return results, len(series)


def poly_prices(m):
    try:
        outcomes = m.get("outcomes", [])
        prices = m.get("outcomePrices", [])
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(prices, str):
            prices = json.loads(prices)
        if not isinstance(prices, list) or not isinstance(outcomes, list):
            return None
        for i, outcome in enumerate(outcomes):
            if str(outcome).lower() == "yes" and i < len(prices):
                return decimal_price(prices[i])
        return decimal_price(prices[0]) if len(prices) == 2 else None
    except (ValueError, TypeError, IndexError):
        return None


def poly_no_price(m):
    try:
        outcomes = m.get("outcomes", [])
        prices = m.get("outcomePrices", [])
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if isinstance(prices, str): prices = json.loads(prices)
        for i, outcome in enumerate(outcomes):
            if str(outcome).lower() == "no" and i < len(prices):
                return decimal_price(prices[i])
    except (TypeError, ValueError, IndexError):
        pass
    return None


def polymarket_markets():
    results = []
    # Gamma can reject some combinations of order and tag parameters with 422.
    # Query small batches by league, without order; fall back to broad sports tag.
    seen_events = set()
    for tag in ("nba", "nfl", "ncaab", "ncaaf", "nhl", "mlb", "soccer", "tennis", "ufc", "sports"):
        try:
            events = get_json(f"{POLY}/events", {"tag_slug": tag, "active": "true", "closed": "false", "limit": 70})
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 422:
                try:
                    events = get_json(f"{POLY}/events", {"tag_slug": tag, "closed": "false", "limit": 50})
                except requests.RequestException:
                    continue
            else:
                continue
        except requests.RequestException:
            continue
        if not isinstance(events, list):
            break
        for event in events:
            event_id = str(event.get("id") or event.get("slug") or "")
            if event_id in seen_events: continue
            seen_events.add(event_id)
            event_title = str(event.get("title") or "")
            for m in event.get("markets", []):
                if m.get("closed") or not m.get("active", True):
                    continue
                title = str(m.get("question") or event_title)
                combined = event_title + " " + title
                if not valid_sports(combined):
                    continue
                try:
                    volume = float(m.get("volumeNum") or m.get("volume") or 0)
                except (ValueError, TypeError):
                    volume = 0
                results.append({"platform": "Polymarket", "id": str(m.get("id", "")),
                    "title": title, "sport": sport_from_text(combined) or "Other Sports",
                    "type": kind_of(title), "price": poly_prices(m), "no_price": poly_no_price(m),
                    "url": "https://polymarket.com/event/" + quote(str(event.get("slug") or "")),
                    "volume": volume, "close": m.get("endDate") or event.get("endDate") or ""})

    return results


@st.cache_data(ttl=90, show_spinner=False)
def scan():
    all_markets, errors = [], []
    series_count = 0
    try:
        k, series_count = kalshi_markets()
        all_markets += k
    except Exception as e:
        errors.append(f"Kalshi unavailable: {str(e)[:140]}")
    try:
        all_markets += polymarket_markets()
    except Exception as e:
        errors.append(f"Polymarket unavailable: {str(e)[:140]}")
    unique = {(m["platform"], m["id"]): m for m in all_markets if m["id"]}
    return list(unique.values()), errors, series_count


def init_db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS royal_v11 (timestamp TEXT, platform TEXT, market_id TEXT, sport TEXT, market_type TEXT, title TEXT, ask REAL, status TEXT)""")
    con.commit()
    return con


def log_snapshot(items):
    con = init_db()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.executemany("INSERT INTO royal_v11 VALUES (?,?,?,?,?,?,?,?)", [
        (stamp, x["platform"], x["id"], x["sport"], x["type"], x["title"], x["price"], "DISCOVERED / UNRATED") for x in items])
    con.commit()
    con.close()


def cents(x):
    return "Price unavailable" if x is None else f"{x * 100:.1f}¢"


def parse_date(value):
    try:
        if not value: return None
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except (ValueError, TypeError):
        return None


def evaluate(m):
    # Screening, not a model-predicted edge. Neither liquidity nor low price proves value.
    now = datetime.now(timezone.utc)
    close = parse_date(m.get("close"))
    hours = (close - now).total_seconds() / 3600 if close else None
    if hours is not None and hours <= 0:
        return "🔴 EXPIRED / CHECK", "Closing time has passed; confirm market status.", -999
    if hours is None:
        return "⚪ DATE NEEDED", "Contract close time unavailable; not eligible for Top 5.", -500
    if hours > 24 * 21:
        return "⚪ LONG DATED", "Outside the 21-day research window.", -200
    yes, no = m.get("price"), m.get("no_price")
    prices = [("YES", yes), ("NO", no)]
    available = [(side, price) for side, price in prices if price is not None]
    if not available:
        return "⚪ DATA NEEDED", "No current entry ask was returned.", -10
    best_side, cheapest = min(available, key=lambda x: x[1])
    vol = max(0, m.get("volume") or 0)
    league = m.get("sport") in ("NBA", "NFL", "NHL", "MLB", "College", "Soccer")
    timing = 20 if hours is not None and 0 < hours <= 48 else (10 if hours is not None and hours <= 168 else 0)
    score = timing + min(15, vol ** .25) + (10 if league else 0) + (8 if m["type"] != "Moneyline / Other" else 0)
    if yes is not None and yes >= .90 and (no is None or no >= .90):
        return "🔴 HIGH PRICE", "High entry cost; small maximum payout and substantial downside.", score - 30
    if cheapest >= .90:
        return "🔴 HIGH PRICE", "High entry cost; verify probability before considering it.", score - 25
    return "🟡 RESEARCH", f"{best_side} entry is {cents(cheapest)}; no statistical edge verified.", score


def display_card(m, idx=None):
    label, explanation, _ = evaluate(m)
    with st.container(border=True):
        left, right = st.columns([3.2, 1.1])
        with left:
            st.markdown(f"**{'👑 ' + str(idx) + '. ' if idx else ''}{m['title']}**")
            st.caption(f"{m['sport']} · {m['type']} · {m['platform']}")
            st.markdown(f"**{label}** — {explanation}")
        with right:
            st.markdown(f"**YES {cents(m.get('price'))}**")
            st.markdown(f"**NO {cents(m.get('no_price'))}**")
        if m.get("url"):
            st.link_button("View market", m["url"])



# ================================
# V1.3: PAPER LEDGER FOUNDATION
# ================================
# This ledger only acts on independently validated, executable signals.
# No probability engine or executable order-book feed is connected yet;
# therefore V1.3 deliberately opens ZERO automatic positions.
# SQLite is local and EPHEMERAL on Streamlit Community Cloud. Set
# ROYAL_DB_PATH to a durable mounted disk when deploying a worker.

def ledger_connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
      trade_id TEXT PRIMARY KEY, market_key TEXT NOT NULL,
      platform TEXT NOT NULL, market_id TEXT NOT NULL,
      title TEXT NOT NULL, league TEXT, side TEXT NOT NULL,
      grade TEXT NOT NULL, contracts INTEGER NOT NULL,
      entry_price REAL NOT NULL, entry_fee REAL NOT NULL DEFAULT 0,
      opened_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN',
      exit_price REAL, exit_fee REAL NOT NULL DEFAULT 0,
      closed_at TEXT, realized_pnl REAL, close_reason TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS paper_activity (
      event_id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_key TEXT UNIQUE, created_at TEXT NOT NULL,
      action TEXT NOT NULL, details TEXT NOT NULL)""")
    con.commit()
    return con


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def paper_summary(con):
    row = con.execute("""SELECT COALESCE(SUM(CASE WHEN status='OPEN'
        THEN contracts*entry_price+entry_fee ELSE 0 END),0) locked,
        COALESCE(SUM(CASE WHEN status='CLOSED' THEN realized_pnl ELSE 0 END),0) pnl,
        SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open_count,
        SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) closed_count
        FROM paper_trades""").fetchone()
    locked, pnl = float(row['locked']), float(row['pnl'])
    # Available cash includes released entry capital from closed trades.
    return {"cash": STARTING_BANKROLL + pnl - locked,
            "locked": locked, "realized": pnl,
            "open": row['open_count'] or 0, "closed": row['closed_count'] or 0}


def record_activity(con, action, details, event_key):
    con.execute("INSERT OR IGNORE INTO paper_activity(event_key,created_at,action,details) VALUES(?,?,?,?)",
                (event_key, now_utc(), action, details))


def open_paper_trade(con, signal):
    """For future validated signals ONLY; never call from market discovery.

    Required signal: verified=True, executable=True, current ask, grade,
    market_key, market_id, platform, title, side, and fee estimate.
    An external engine must verify independent probability, settlement
    equivalence, freshness, and available depth before calling this.
    """
    if signal.get('verified') is not True or signal.get('executable') is not True:
        return False, 'Unverified signal: paper entry blocked'
    grade = signal.get('grade')
    if grade not in PAPER_SIZES or signal.get('side') not in ('YES','NO'):
        return False, 'Unsupported signal grade or side'
    try:
        price = float(signal['ask'])
        fee_per_contract = float(signal['fee_per_contract'])
    except (KeyError, TypeError, ValueError):
        return False, 'Missing executable ask or fee'
    if not (0 < price < 1) or not (0 <= fee_per_contract < 1):
        return False, 'Invalid price or fee'
    key = str(signal.get('market_key') or '')
    if not key or not signal.get('market_id') or not signal.get('platform'):
        return False, 'Missing contract identity'
    contracts = int(PAPER_SIZES[grade] // (price + fee_per_contract))
    if contracts < 1:
        return False, 'Insufficient budget for one contract'
    cost = contracts * (price + fee_per_contract)
    trade_id = hashlib.sha256((key + '|' + signal['side']).encode()).hexdigest()[:24]
    with con:
        # Same outcome across platforms is one paper position, never two.
        existing = con.execute("SELECT trade_id FROM paper_trades WHERE market_key=? AND status='OPEN'",(key,)).fetchone()
        if existing:
            return False, 'Equivalent position already open'
        if con.execute('SELECT 1 FROM paper_trades WHERE trade_id=?',(trade_id,)).fetchone():
            return False, 'Signal previously traded; no duplicate re-entry'
        if paper_summary(con)['cash'] < cost:
            return False, 'Insufficient available paper cash'
        con.execute("""INSERT INTO paper_trades(trade_id,market_key,platform,market_id,title,league,
           side,grade,contracts,entry_price,entry_fee,opened_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
           (trade_id,key,signal['platform'],signal['market_id'],signal.get('title',''),
            signal.get('league',''),signal['side'],grade,contracts,price,
            contracts*fee_per_contract,now_utc()))
        record_activity(con,'BUY',f"{signal['platform']} | {signal.get('title','')} | {signal['side']} | {contracts} contracts",'buy:'+trade_id)
    return True, trade_id


def close_paper_trade(con, trade_id, exit_bid, fee_per_contract, reason='CASH OUT'):
    """Paper exit requires a verified executable bid, never a last price."""
    try:
        bid, fee = float(exit_bid), float(fee_per_contract)
    except (TypeError, ValueError):
        return False, 'Missing executable exit quote'
    if not (0 <= bid <= 1 and 0 <= fee < 1):
        return False, 'Invalid exit quote'
    with con:
        t = con.execute("SELECT * FROM paper_trades WHERE trade_id=? AND status='OPEN'",(trade_id,)).fetchone()
        if t is None:
            return False, 'No open position'
        exit_fee = t['contracts']*fee
        pnl = t['contracts']*(bid-t['entry_price'])-t['entry_fee']-exit_fee
        con.execute("""UPDATE paper_trades SET status='CLOSED',exit_price=?,exit_fee=?,
           closed_at=?,realized_pnl=?,close_reason=? WHERE trade_id=? AND status='OPEN' """,
           (bid,exit_fee,now_utc(),pnl,reason,trade_id))
        record_activity(con,reason,f"{t['title']} | {pnl:+.2f} paper USD",'close:'+trade_id)
    return True, pnl


@st.cache_data(ttl=120, show_spinner=False)
def live_scoreboard(league):
    # ESPN's public scoreboard is contextual information, not a licensed
    # production feed or a substitute for synchronized in-play pricing.
    paths = {'NFL':'football/nfl','NBA':'basketball/nba',
             'NCAAF':'football/college-football','NCAAB':'basketball/mens-college-basketball'}
    data = get_json('https://site.api.espn.com/apis/site/v2/sports/'+paths[league]+'/scoreboard')
    games = []
    for e in data.get('events',[]):
        c = (e.get('competitions') or [{}])[0]
        teams = c.get('competitors') or []
        if len(teams)<2: continue
        name = ' vs '.join(x.get('team',{}).get('displayName','Team') for x in teams[:2])
        score = ' – '.join(str(x.get('score','–')) for x in teams[:2])
        state = e.get('status',{}).get('type',{}).get('description','Unknown')
        games.append({'Game':name,'Score':score,'Status':state,'Start':e.get('date','')})
    return games


# ================================
# V1.4: SPORTSBOOK MARKET BENCHMARKS
# ================================
# The Odds API is optional and requires a user-provided key.
# Data coverage, permitted use and quotas depend on the provider's terms.
ODDS_SPORTS = {
    "NFL": "americanfootball_nfl", "NBA": "basketball_nba",
    "NCAAF": "americanfootball_ncaaf", "NCAAB": "basketball_ncaab",
}


def odds_api_key():
    try:
        return st.secrets.get("ODDS_API_KEY", "") or os.getenv("ODDS_API_KEY", "")
    except (FileNotFoundError, KeyError):
        return os.getenv("ODDS_API_KEY", "")


def american_implied(odd):
    try:
        v = float(odd)
        if v == 0:
            return None
        return 100 / (v + 100) if v > 0 else (-v) / ((-v) + 100)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


@st.cache_data(ttl=300, show_spinner=False)
def sportsbook_benchmarks(key):
    # Fetch h2h only: spreads, totals and props require separate outcome matching.
    results, errors = [], []
    for league, code in ODDS_SPORTS.items():
        try:
            events = get_json("https://api.the-odds-api.com/v4/sports/" + code + "/odds/", {
                "apiKey": key, "regions": "us", "markets": "h2h", "oddsFormat": "american"
            })
            if not isinstance(events, list):
                errors.append(f"{league}: unexpected odds response")
                continue
            for e in events:
                if not isinstance(e, dict):
                    continue
                # Only two-outcome events can be de-vigged with this method.
                by_team = {}
                for book in e.get("bookmakers", []):
                    for market in book.get("markets", []):
                        if market.get("key") != "h2h":
                            continue
                        vals = {o.get("name"): american_implied(o.get("price")) for o in market.get("outcomes", [])}
                        home, away = e.get("home_team"), e.get("away_team")
                        if home not in vals or away not in vals or len(vals) != 2:
                            continue
                        if vals[home] is None or vals[away] is None:
                            continue
                        total = vals[home] + vals[away]
                        if total <= 0:
                            continue
                        for team in (home, away):
                            by_team.setdefault(team, []).append(vals[team] / total)
                if by_team:
                    results.append({
                        "league": league, "game": f"{e.get('away_team','?')} @ {e.get('home_team','?')}",
                        "commence": e.get("commence_time", ""),
                        "probabilities": {team: round(sum(vals)/len(vals), 4) for team, vals in by_team.items()},
                        "books": min(len(v) for v in by_team.values()),
                    })
        except requests.RequestException as exc:
            errors.append(f"{league}: {str(exc)[:80]}")
    return results, errors


def top_five_diverse(candidates):
    # Display diversity only; never manufactures an edge or changes trade eligibility.
    chosen, seen, remaining = [], set(), list(candidates)
    while remaining and len(chosen) < 5:
        best_index = max(range(len(remaining)), key=lambda i: (
            remaining[i]["rank"] - (18 if remaining[i]["sport"] in seen else 0)
            - (9 if remaining[i]["type"] in {x["type"] for x in chosen} else 0),
            remaining[i].get("volume") or 0))
        picked = remaining.pop(best_index)
        chosen.append(picked)
        seen.add(picked["sport"])
    return chosen



# ======================================
# V1.5 — PREGAME-FIRST ROYAL DASHBOARD
# ======================================
from zoneinfo import ZoneInfo
from html import escape

CENTRAL = ZoneInfo("America/Chicago")

def local_start(value):
    d = parse_date(value)
    return d.astimezone(CENTRAL).strftime("%a %b %d · %-I:%M %p CT") if d else "Time unavailable"

def pregame_board(odds):
    now = datetime.now(timezone.utc)
    rows = []
    for event in odds:
        start = parse_date(event.get("commence"))
        if not start or not now <= start <= now + __import__("datetime").timedelta(days=2):
            continue
        probs = event.get("probabilities", {})
        if len(probs) != 2:
            continue
        favored, probability = max(probs.items(), key=lambda x: x[1])
        # A sportsbook consensus is a directional lean, NOT an independently
        # validated model or a confirmed positive-EV trading opportunity.
        rows.append({
            "league": event["league"], "game": event["game"],
            "team": favored, "prob": probability, "start": start,
            "books": event.get("books", 0), "status": "LEAN YES",
        })
    rows.sort(key=lambda x: (x["start"], -x["prob"]))
    return rows

# V1.6: ESPN pregame records and rankings, optional and fail-closed.
# A missing or ambiguous match never displays a guessed record or rank.
@st.cache_data(ttl=900, show_spinner=False)
def espn_pregame_teams(league, start_day, end_day):
    paths = {"NFL": "football/nfl", "NBA": "basketball/nba",
             "NCAAF": "football/college-football",
             "NCAAB": "basketball/mens-college-basketball"}
    url = "https://site.api.espn.com/apis/site/v2/sports/" + paths[league] + "/scoreboard"
    data = get_json(url, {"dates": start_day + "-" + end_day, "limit": 500})
    games = []
    for event in data.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        teams = []
        for competitor in competition.get("competitors", []):
            team = competitor.get("team") or {}
            record = None
            for rec in competitor.get("records") or []:
                if rec.get("type") in ("total", "overall") and rec.get("summary"):
                    record = rec["summary"]
                    break
            rank = competitor.get("curatedRank", {})
            rank = rank.get("current") if isinstance(rank, dict) else None
            if not isinstance(rank, int) or rank < 1 or rank > 25:
                rank = None
            teams.append({"name": team.get("displayName", ""),
                          "short": team.get("shortDisplayName", ""),
                          "location": team.get("location", ""),
                          "nickname": team.get("name", ""),
                          "record": record, "rank": rank})
        if len(teams) == 2:
            games.append({"start": parse_date(event.get("date")), "teams": teams})
    return games


def normalize_team_name(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", value.replace("&", " and ")))


def match_espn_game(pick, games):
    # Require both teams and a start time within 90 minutes; never fuzzy-match
    # on a mascot or school alone because that can attach incorrect records.
    names = [x.strip() for x in pick["game"].split(" @ ")]
    if len(names) != 2:
        return None
    requested = [normalize_team_name(x) for x in names]
    matches = []
    for game in games:
        start = game.get("start")
        if not start or abs((start - pick["start"]).total_seconds()) > 5400:
            continue
        aliases = [{normalize_team_name(t.get(k, "")) for k in ("name", "short")}
                   for t in game["teams"]]
        if (requested[0] in aliases[0] and requested[1] in aliases[1]) or (requested[0] in aliases[1] and requested[1] in aliases[0]):
            matches.append(game)
    return matches[0] if len(matches) == 1 else None


def compact_matchup(pick, game):
    if not game:
        return pick["game"], None
    def format_team(team):
        rank = f"#{team['rank']} " if team.get("rank") else ""
        record = f" ({team['record']})" if team.get("record") else ""
        return rank + team["name"] + record
    by_name = {normalize_team_name(t["name"]): t for t in game["teams"]}
    odds_names = [x.strip() for x in pick["game"].split(" @ ")]
    ordered = []
    for name in odds_names:
        match = next((t for t in game["teams"] if normalize_team_name(name) in
                      (normalize_team_name(t["name"]), normalize_team_name(t["short"]))), None)
        if match is None:
            return pick["game"], None
        ordered.append(match)
    return " @ ".join(format_team(t) for t in ordered), ordered


def short_reason(pick, ordered):
    # Record comparisons are descriptive only; the consensus determines the lean.
    reason = f"Sportsbook favorite ({pick['prob']:.1%} consensus)"
    if ordered and all(t.get("record") for t in ordered):
        reason += "; records shown for context"
    return reason + ". Not a verified BUY."

def bankroll_strip(account):
    cells = [
        ("START", f"${STARTING_BANKROLL:,.0f}"),
        ("AVAILABLE", f"${account['cash']:,.2f}"),
        ("OPEN", str(account["open"])),
        ("REALIZED P/L", f"${account['realized']:+,.2f}"),
    ]
    html = '<div class="royal-strip">'
    for label, value in cells:
        html += f'<div><small>{escape(label)}</small><strong>{escape(value)}</strong></div>'
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)

st.markdown("""
<style>
.royal-strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;
background:#211235;border:1px solid #70419a;border-radius:12px;padding:10px;margin:6px 0 16px}
.royal-strip>div{text-align:center;min-width:0}
.royal-strip small{display:block;font-size:.65rem;color:#c6b1d9;letter-spacing:.04em}
.royal-strip strong{display:block;font-size:clamp(.78rem,1.7vw,1.08rem);color:#f5d47d;overflow-wrap:anywhere}
.royal-pick{background:#24143c;border:1px solid #6b3b9b;border-radius:12px;padding:12px;margin:8px 0}
.royal-pick b{color:#f5d47d}
@media(max-width:600px){.royal-strip{gap:3px;padding:8px 3px}.royal-strip small{font-size:.55rem}.royal-strip strong{font-size:.73rem}}
</style>
""", unsafe_allow_html=True)

banner = Path("royal_sports_banner.png")
if banner.exists():
    st.image(str(banner), use_container_width=True)
else:
    st.title("👑 QUEEN'S EDGE | 254 — ROYAL SPORTS SNIPER")

st.caption("V1.7 · Records/rankings visibility check · Pregame-first · Kalshi + Polymarket discovery · PAPER ONLY")
st.markdown("### 👑 Today's Royal Signals")
st.caption("Sportsbook favorites before kickoff. Records and available rankings are context, not a BUY signal.")
if st.button("🔄 Refresh markets and scores"):
    scan.clear()
    live_scoreboard.clear()
    st.rerun()

key = odds_api_key()
odds, odds_errors = sportsbook_benchmarks(key) if key else ([], ["Odds API key not configured"])
board = pregame_board(odds)

if board:
    st.markdown("#### 🏆 Today's Top 5 Pregame Leans")
    selected = board[:5]
    espn_by_league = {}
    for league in {pick["league"] for pick in selected}:
        league_picks = [p for p in selected if p["league"] == league]
        first = min(p["start"] for p in league_picks).strftime("%Y%m%d")
        last = max(p["start"] for p in league_picks).strftime("%Y%m%d")
        try:
            espn_by_league[league] = espn_pregame_teams(league, first, last)
        except (requests.RequestException, KeyError, ValueError, TypeError):
            espn_by_league[league] = []
    matched_count = 0
    for i, pick in enumerate(selected, 1):
        game = match_espn_game(pick, espn_by_league.get(pick["league"], []))
        matchup, teams = compact_matchup(pick, game)
        if teams:
            matched_count += 1
        with st.container(border=True):
            st.markdown(f"**👑 {i}. {matchup}**")
            if not teams:
                st.caption("📋 Team records/rankings unavailable: ESPN matchup not confirmed.")
            elif not any(t.get("record") or t.get("rank") for t in teams):
                st.caption("📋 ESPN matched the game, but supplied no record or Top 25 rank.")
            st.markdown(f"🟣 **PREGAME LEAN — {pick['team']}**")
            st.caption(f"{pick['league']} · {local_start(pick['start'].isoformat())} · {pick['books']} books")
            st.caption("Queen's reason: " + short_reason(pick, teams))
    with st.expander("📋 Records & Rankings Status", expanded=False):
        st.caption(f"ESPN matched {matched_count} of {len(selected)} displayed games. Records and Top 25 rankings appear only when ESPN returns them for the confirmed matchup.")
        if matched_count < len(selected):
            st.caption("Missing labels are a data/matching issue, not proof the app failed to update.")
else:
    st.info("⚪ WAIT — No upcoming, supported two-outcome sportsbook games available for a pregame lean. Queen will not invent picks.")

st.markdown("#### 💚 Qualified BUY Signals")
st.info("⚪ WAIT — BUY YES / BUY NO remain disabled until independent probabilities, exact contract matching, fees, order-book depth and freshness are verified.")

st.markdown("#### 👑 Royal Paper Parlays")
st.caption("2-, 3- and 5-leg combinations require that many independently qualified, distinct-game picks. These are simulated combinations, not executable platform parlays.")
st.info("⚪ WAIT — No qualified legs yet. No paper parlays created.")

st.markdown("#### 💜 Royal Paper Bankroll")
try:
    with ledger_connection() as ledger:
        account = paper_summary(ledger)
    bankroll_strip(account)
except (sqlite3.Error, OSError) as e:
    st.warning("Paper ledger unavailable: " + str(e)[:120])

st.markdown("#### 🔴 Live Games & Queen Signals")
st.caption("Live scores are contextual, not synchronized with tradable bids. HOLD and CASH OUT require verified open positions and quotes.")
tabs = st.tabs(["NFL", "NBA", "NCAAF", "NCAAB"])
for league, tab in zip(["NFL", "NBA", "NCAAF", "NCAAB"], tabs):
    with tab:
        try:
            games = live_scoreboard(league)
            if games:
                st.dataframe(games, use_container_width=True, hide_index=True)
            else:
                st.caption("No games returned.")
        except (requests.RequestException, ValueError, KeyError) as e:
            st.caption(f"Scoreboard unavailable: {str(e)[:90]}")

with st.expander("📈 Sportsbook Odds Intelligence", expanded=False):
    if odds:
        st.dataframe([
            {"League": o["league"], "Game": o["game"], "Team": team,
             "Consensus": f"{p:.1%}", "Books": o["books"], "Start": local_start(o["commence"])}
            for o in odds for team, p in o["probabilities"].items()
        ], use_container_width=True, hide_index=True)
    else:
        st.caption("No sportsbook benchmark data.")
    for err in odds_errors:
        st.caption("Provider: " + err)

with st.expander("🔎 Sports Market Scanner & Diagnostics", expanded=False):
    with st.spinner("Scanning Kalshi and Polymarket sports markets..."):
        markets, errors, series_count = scan()
    for err in errors:
        st.warning(err)
    st.caption(f"{len(markets)} sampled markets · {sum(m['platform']=='Kalshi' for m in markets)} Kalshi · {sum(m['platform']=='Polymarket' for m in markets)} Polymarket")
    st.caption("Polymarket global markets may differ from Polymarket US. Displayed prices can be stale or non-executable.")
    search = st.text_input("Search markets", placeholder="Team, player, league...")
    league = st.selectbox("Sport", ["All Sports"] + list(SPORTS) + ["Other Sports"])
    platform = st.selectbox("Platform", ["Both", "Kalshi", "Polymarket"])
    filtered = [m for m in markets if
                (not search or search.casefold() in (m["title"] + " " + m["sport"]).casefold())
                and (league == "All Sports" or m["sport"] == league)
                and (platform == "Both" or m["platform"] == platform)]
    for m in filtered[:8]:
        display_card(m)
    st.caption(f"Showing {min(8,len(filtered))} of {len(filtered)} matching sampled contracts.")

with st.expander("📊 Royal Tracking Center — Paper Trades & History", expanded=False):
    try:
        with ledger_connection() as con:
            positions = [dict(r) for r in con.execute(
                "SELECT opened_at,platform,title,side,grade,contracts,entry_price,status,realized_pnl,closed_at FROM paper_trades ORDER BY opened_at DESC LIMIT 100")]
            activity = [dict(r) for r in con.execute(
                "SELECT created_at,action,details FROM paper_activity ORDER BY event_id DESC LIMIT 100")]
        st.markdown("**Paper positions**")
        if positions:
            st.dataframe(positions, use_container_width=True, hide_index=True)
        else:
            st.caption("No verified paper entries yet.")
        st.markdown("**Activity**")
        if activity:
            st.dataframe(activity, use_container_width=True, hide_index=True)
        else:
            st.caption("No activity yet.")
    except (sqlite3.Error, OSError) as e:
        st.warning("Tracking unavailable: " + str(e)[:120])
    st.caption("SQLite on Streamlit Community Cloud is not durable. App reruns are not 24/7 background monitoring.")

st.caption("QUEEN'S EDGE | 254 · ROYAL SPORTS SNIPER V1.7 · Paper only · No real orders")
