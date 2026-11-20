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


# The replacement image has no white margins; Streamlit scales it to mobile width.
banner = Path("royal_sports_banner.png")
if banner.exists():
    st.image(str(banner), use_container_width=True)
else:
    st.markdown("# 👑 QUEEN'S EDGE | 254 — ROYAL SPORTS SNIPER")
st.caption("🏈 NFL · 🏀 NBA · 🎓 NCAAF · 🏀 NCAAB · More sports in research scanner")
st.caption("V1.4 · Sportsbook benchmarks + improved research filtering · Paper BUY/CASH OUT paused pending validated edge and executable quotes")
header, refresh = st.columns([3, 1])
with header:
    st.markdown("### 💜 Royal Sports Scanner")
with refresh:
    if st.button("🔄 Refresh", use_container_width=True):
        scan.clear()
        st.rerun()
with st.spinner("Queen is scanning sports markets..."):
    markets, errors, series_count = scan()
for error in errors:
    st.warning(error)
for m in markets:
    m["decision"], m["reason"], m["rank"] = evaluate(m)
markets.sort(key=lambda m: (-m["rank"], -(m.get("volume") or 0)))
a,b,c = st.columns(3)
a.metric("🏟️ Markets", len(markets))
b.metric("👑 Kalshi", sum(m["platform"] == "Kalshi" for m in markets))
c.metric("💜 Polymarket", sum(m["platform"] == "Polymarket" for m in markets))
st.caption(f"Partial discovery from up to 80 of {series_count} Kalshi sports series and sampled Polymarket global sports tags. Polymarket global is not necessarily Polymarket US. Prices may be stale or non-executable.")

st.markdown('<div class="royal-head">💜 ROYAL PAPER BANKROLL</div>', unsafe_allow_html=True)
try:
    with ledger_connection() as ledger:
        account = paper_summary(ledger)
    p1,p2 = st.columns(2)
    p3,p4 = st.columns(2)
    p1.metric('Starting paper funds',f'${STARTING_BANKROLL:,.0f}')
    p2.metric('Available paper cash',f"${account['cash']:,.2f}")
    p3.metric('Open positions',account['open'])
    p4.metric('Realized paper P/L',f"${account['realized']:+,.2f}")
except (sqlite3.Error, OSError) as e:
    st.warning('Paper ledger unavailable: '+str(e)[:150])

st.markdown('<div class="royal-head">📡 QUEEN’S LIVE ACTIVITY</div>', unsafe_allow_html=True)
st.info('SCANNING / RESEARCH MODE — No statistically verified signals yet. Auto paper BUY and CASH OUT are paused; no trades are being invented.')
st.caption('Paper sizes: $100 strong · $75 moderate · $50 qualified. Top 5 is not a daily trade limit.')
with st.expander('🏈🏀 LIVE SPORTS SCOREBOARD — CONTEXT ONLY', expanded=False):
    st.caption('Public scoreboard snapshots; not synchronized with tradable quotes. Not a basis for live entries.')
    tabs=st.tabs(['NFL','NBA','NCAAF','NCAAB'])
    for league, tab in zip(['NFL','NBA','NCAAF','NCAAB'],tabs):
        with tab:
            try:
                games=live_scoreboard(league)
                if games: st.dataframe(games,use_container_width=True,hide_index=True)
                else: st.caption('No games returned for this scoreboard.')
            except (requests.RequestException,KeyError,ValueError) as e:
                st.caption('Scoreboard unavailable: '+str(e)[:100])

st.markdown('<div class="royal-head">📈 SPORTSBOOK ODDS INTELLIGENCE</div>', unsafe_allow_html=True)
key = odds_api_key()
if key:
    odds, odds_errors = sportsbook_benchmarks(key)
    st.caption('Independent sportsbook moneyline benchmarks. Margin removed within each two-outcome book, then averaged. Not a calibrated prediction or BUY signal.')
    st.caption(f'{len(odds)} upcoming games with sportsbook data across NFL, NBA, NCAAF and NCAAB.')
    with st.expander('View sportsbook benchmarks', expanded=False):
        if odds:
            rows = []
            for event in odds[:40]:
                for team, prob in event['probabilities'].items():
                    rows.append({'League':event['league'], 'Game':event['game'], 'Team':team,
                                 'Market benchmark':f'{prob*100:.1f}%', 'Books':event['books'],
                                 'Start (UTC)':event['commence']})
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info('No two-outcome sportsbook odds returned for supported leagues.')
        for error in odds_errors:
            st.caption('Provider: ' + error)
else:
    st.caption('Sportsbook comparison is ready but not connected. Optional ODDS_API_KEY in Streamlit Secrets enables supported moneyline benchmarks; no paid account is required just to run the dashboard.')

st.markdown('<div class="royal-head">👑 TOP 5 TO INVESTIGATE</div>', unsafe_allow_html=True)
st.caption("Upcoming, active markets prioritized by timing and activity. NOT predictions, guaranteed picks, or confirmed positive expected value.")
candidates = [m for m in markets if m["decision"].startswith("🟡")]
if candidates:
    for i,m in enumerate(top_five_diverse(candidates),1): display_card(m,i)
else:
    st.info("No eligible research candidates in the sampled results. Refresh later; Queen will not manufacture picks.")

st.markdown('<div class="royal-head">💚 QUALIFIED VALUE PICKS</div>', unsafe_allow_html=True)
st.info("No verified value picks yet. Sportsbook benchmarks are not a validated independent prediction model; exact contract matching, executable order-book depth, and fees are not connected. No paper entries will be created from research rankings.")
st.markdown('<div class="royal-head">🔴 LIVE GAME OPPORTUNITIES</div>', unsafe_allow_html=True)
st.info("Scoreboard context is available above; synchronized in-play probabilities and executable quotes are not. Live BUY/CASH OUT signals remain paused.")

with st.expander("🏆 EXPLORE MORE SPORTS MARKETS", expanded=False):
    search = st.text_input("Search team, player, or market", placeholder="Lakers, touchdowns, NBA...")
    a,b,c = st.columns(3)
    with a: league = st.selectbox("Sport", ["All Sports"] + list(SPORTS) + ["Other Sports"])
    with b: typ = st.selectbox("Market", ["All Types", "Moneyline / Other", "Player Props", "Totals", "Spreads", "Combos"])
    with c: platform = st.selectbox("Platform", ["Both", "Kalshi", "Polymarket"])
    filtered = [m for m in markets if (not search or search.lower() in (m["title"] + " " + m["sport"]).lower()) and (league == "All Sports" or m["sport"] == league) and (typ == "All Types" or m["type"] == typ) and (platform == "Both" or m["platform"] == platform)]
    st.caption(f"{len(filtered)} matching · showing up to 8")
    for m in filtered[:8]: display_card(m)
    if not filtered: st.caption("No matching markets in this scan.")

with st.expander("📊 ROYAL TRACKING CENTER — PAPER TRADES & HISTORY", expanded=False):
    try:
        with ledger_connection() as con:
            positions=[dict(r) for r in con.execute("SELECT opened_at,platform,title,side,grade,contracts,entry_price,status,realized_pnl,closed_at FROM paper_trades ORDER BY opened_at DESC LIMIT 100").fetchall()]
            activity=[dict(r) for r in con.execute("SELECT created_at,action,details FROM paper_activity ORDER BY event_id DESC LIMIT 100").fetchall()]
        st.markdown("**Paper positions**")
        if positions: st.dataframe(positions,use_container_width=True,hide_index=True)
        else: st.caption("No paper positions. Queen will only enter on independently validated signals.")
        st.markdown("**Activity log**")
        if activity: st.dataframe(activity,use_container_width=True,hide_index=True)
        else: st.caption("No automated trade events yet.")
    except (sqlite3.Error,OSError) as e: st.warning("Paper ledger unavailable: "+str(e)[:120])
    st.caption("Snapshots are discovered markets, not bets or predictions. Local storage is not durable on Streamlit Community Cloud.")
    if "v12_logged" not in st.session_state: st.session_state.v12_logged = None
    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M") if markets else None
    if snapshot and snapshot != st.session_state.v12_logged:
        try:
            log_snapshot(markets)
            st.session_state.v12_logged = snapshot
        except Exception as e: st.warning(f"History write unavailable: {e}")
    try:
        con = init_db()
        total = con.execute("SELECT COUNT(*) FROM royal_v11").fetchone()[0]
        st.metric("Market snapshots (V1.1 + V1.2)", total)
        rows = con.execute("SELECT timestamp,platform,sport,market_type,title,ask,status FROM royal_v11 ORDER BY rowid DESC LIMIT 100").fetchall()
        con.close()
        if rows:
            st.dataframe([{"UTC":r[0], "Platform":r[1], "Sport":r[2], "Type":r[3], "Market":r[4], "YES":cents(r[5]), "Status":r[6]} for r in rows], use_container_width=True, hide_index=True)
    except Exception as e: st.warning(f"Tracking unavailable: {e}")
st.caption("QUEEN'S EDGE | 254 · ROYAL SPORTS SNIPER V1.4 RESEARCH · Paper only · No real orders · Local SQLite is not durable on Community Cloud · 24/7 worker not yet deployed")
