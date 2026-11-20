import os, re, sqlite3, hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote
import requests
import streamlit as st

st.set_page_config(page_title="ROYAL SPORTS SNIPER V2.9.3 | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
CT = ZoneInfo("America/Chicago")
BASE = "https://gateway.polymarket.us"
LEAGUES = {"NBA":"nba", "NFL":"nfl", "NCAAB":"ncaab", "NCAAF":"ncaaf", "NHL":"nhl", "MLB":"mlb", "WNBA":"wnba", "MLS":"mls", "EPL":"epl", "UFC":"ufc"}
ESPN = {"NBA":"basketball/nba", "NFL":"football/nfl", "NCAAB":"basketball/mens-college-basketball", "NCAAF":"football/college-football", "NHL":"hockey/nhl", "MLB":"baseball/mlb", "WNBA":"basketball/wnba", "MLS":"soccer/usa.1", "EPL":"soccer/eng.1"}
DB = Path(os.getenv("ROYAL_DB_PATH", "royal_sports_history.db"))
DB.parent.mkdir(parents=True, exist_ok=True)

def utc(): return datetime.now(timezone.utc)
def parse_date(s):
    try: return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError): return None

def local(s):
    d = parse_date(s)
    return d.astimezone(CT).strftime("%a %b %d, %I:%M %p CT") if d else "Time unconfirmed"

def numeric(x):
    try:
        if isinstance(x, dict): x = x.get("value")
        return float(x)
    except (TypeError, ValueError): return None

def clean(x): return re.sub(r"[^a-z0-9]", "", str(x).lower())

def db():
    c = sqlite3.connect(str(DB), timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS poly_watch_v293 (
      id TEXT PRIMARY KEY, event_id TEXT, sport TEXT, title TEXT, market_slug TEXT,
      selection TEXT, bid REAL, ask REAL, market_state TEXT, game_live INTEGER,
      observed_at TEXT, status TEXT DEFAULT 'OBSERVED')""")
    c.commit()
    return c

@st.cache_data(ttl=120, show_spinner=False)
def api(path, params=None):
    try:
        r = requests.get(BASE + path, params=params, headers={"Accept":"application/json", "User-Agent":"RoyalSportsSniper254/2.9.3"}, timeout=12)
        r.raise_for_status()
        return r.json(), None
    except requests.HTTPError as e:
        return None, f"HTTP {e.response.status_code}" if e.response is not None else "HTTP error"
    except (requests.RequestException, ValueError) as e:
        return None, type(e).__name__

@st.cache_data(ttl=60, show_spinner=False)
def scores(sport, date):
    path = ESPN.get(sport)
    if not path: return []
    try:
        r = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard", params={"dates":date, "limit":300}, timeout=12)
        r.raise_for_status()
        return r.json().get("events", [])
    except (requests.RequestException, ValueError): return []

def score_index(sports):
    result = {}
    today = utc().astimezone(CT).date()
    for sport in sports:
        if sport not in ESPN: continue
        for delta in (-1, 0, 1, 2, 3):
            for e in scores(sport, (today + timedelta(days=delta)).strftime("%Y%m%d")):
                comp = (e.get("competitions") or [{}])[0]
                teams = comp.get("competitors") or []
                names = [t.get("team", {}).get("displayName", "") for t in teams]
                if len(names) < 2: continue
                status = e.get("status", {}).get("type", {})
                record = {"state":status.get("state", "unknown"), "detail":status.get("shortDetail", ""), "date":e.get("date"), "teams":names,
                          "scores": {t.get("team", {}).get("displayName", ""):t.get("score") for t in teams}}
                result[(sport, frozenset(clean(n) for n in names))] = record
    return result

def event_score(sport, event, index):
    names = []
    for market in event.get("markets") or []:
        for side in market.get("marketSides") or []:
            name = (side.get("team") or {}).get("name")
            if name and clean(name) not in [clean(n) for n in names]: names.append(name)
    if len(names) != 2: return None
    key = (sport, frozenset(clean(n) for n in names))
    return index.get(key)

def events_for(sport):
    payload, error = api(f"/v2/leagues/{LEAGUES[sport]}/events", {"limit":50, "offset":0})
    if error: return [], error
    items = payload if isinstance(payload, list) else payload.get("events", []) if isinstance(payload, dict) else []
    return [e for e in items if isinstance(e, dict)], None

def bbo(slug):
    if not slug or not re.fullmatch(r"[A-Za-z0-9_-]+", str(slug)): return None, "Invalid market slug"
    payload, error = api(f"/v1/markets/{quote(str(slug), safe='')}/bbo")
    if error: return None, error
    data = payload.get("marketData") if isinstance(payload, dict) else None
    if not isinstance(data, dict): return None, "Missing marketData"
    bid, ask = numeric(data.get("bestBid")), numeric(data.get("bestAsk"))
    # Never treat missing, malformed, or inverted quotes as tradeable.
    valid = bid is not None and ask is not None and 0 < bid <= ask < 1
    return {"bid":bid, "ask":ask, "state":str(data.get("state", "UNKNOWN")), "valid":valid,
            "bid_size":numeric(data.get("bidShares")), "ask_size":numeric(data.get("askShares"))}, None

def observations(items):
    with db() as c:
        for x in items:
            if not x.get("quote") or not x["quote"].get("valid"): continue
            ident = hashlib.sha256((x["event_id"] + "|" + x["slug"] + "|" + x["selection"]).encode()).hexdigest()[:32]
            q = x["quote"]
            c.execute("""INSERT OR IGNORE INTO poly_watch_v293
              (id,event_id,sport,title,market_slug,selection,bid,ask,market_state,game_live,observed_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (ident,x["event_id"],x["sport"],x["title"],x["slug"],x["selection"],q["bid"],q["ask"],q["state"],int(x["live"]),utc().isoformat()))
        c.commit()

def money_profit(price, stake):
    # Theoretical gross settlement profit for a $1-winning contract, not an execution quote.
    return stake * (1 / price - 1)

def display_row(x, number=None):
    q = x.get("quote")
    with st.container(border=True):
        a,b,c = st.columns([4.6, 2.1, 1.5], vertical_alignment="center")
        title = f"**{number}. {x['title']}**" if number else f"**{x['title']}**"
        a.markdown(title)
        a.caption(f"{x['sport']} · {x['selection']} · {x['market_type']} · {local(x['start'])}")
        if x["live"]: a.caption("🔴 Game LIVE" + (f" · {x['score_detail']}" if x['score_detail'] else ""))
        elif x["game_state"] == "pre": a.caption("📅 Game scheduled · market status is separate")
        else: a.caption("Game status not independently matched")
        if q and q["valid"]:
            b.markdown(f"**Ask {q['ask']:.0%} · Bid {q['bid']:.0%}**")
            b.caption("Indicative best offer / bid")
            c.markdown("**WATCH**" if "OPEN" in q["state"].upper() else "**WAIT**")
            c.caption("No validated edge model")
        else:
            b.markdown("**Price unavailable**")
            b.caption(x.get("error") or "No valid two-sided quote")
            c.markdown("**WAIT**")
        with st.expander("Details / hypothetical payout", expanded=False):
            st.write("Polymarket US market slug:", x["slug"])
            st.write("Market status:", q["state"] if q else "Unknown")
            if q and q["valid"]:
                st.write(f"$100 theoretical gross profit if this exact outcome wins: **${money_profit(q['ask'], 100):,.2f}**. This ignores fees, slippage, liquidity and settlement-rule differences.")
            st.caption("No recommendation or live trade is being made. Quotes may be delayed or change before an order could fill.")

st.markdown("""<style>
.block-container{padding-top:1rem;max-width:1300px}
h1,h2,h3{color:#b37cf1!important}
[data-testid="stMetric"]{background:rgba(127,71,166,.13);padding:.65rem;border-radius:12px}
.stButton>button{border-radius:20px}
</style>""", unsafe_allow_html=True)
for name in ("royal_banner.png", "banner.png", "sports_banner.png", "queens_banner.png"):
    if Path(name).is_file():
        st.image(name, use_container_width=True)
        break
else: st.title("👑 QUEEN’S EDGE | 254 — ROYAL SPORTS SNIPER")
st.caption("V2.9.3 · POLYMARKET US ONLY · READ-ONLY MARKET WATCH · PAPER OBSERVATIONS")

sport = st.radio("🏆 Choose Your Sport", ["All Sports"] + list(LEAGUES), horizontal=True)
window = st.radio("📅 Choose Game Window", ["🔴 Live", "📅 Today", "Tomorrow", "Next 3 Days"], horizontal=True, index=1)
with st.expander("⚙️ Scan settings & important notes"):
    max_leagues = st.slider("Maximum leagues in All Sports", 1, len(LEAGUES), 6)
    max_quotes = st.slider("Maximum contracts to price per scan", 5, 100, 35, step=5)
    st.caption("The public Polymarket US API is used without an account key. Quotes are cached for two minutes. Game scores come from ESPN and may lag.")
    st.caption("This version does not calculate verified betting edge or automatically wager. Existing historical picks in your old database are preserved, not added to the new watch statistics.")

if st.button("👑 Scan Polymarket US", type="primary", use_container_width=True):
    st.session_state["royal_scan"] = True
if not st.session_state.get("royal_scan"):
    st.info("Press Scan Polymarket US to discover games and request available market prices.")
    st.stop()

selected = list(LEAGUES)[:max_leagues] if sport == "All Sports" else [sport]
with st.spinner("Finding Polymarket US sports events and checking market quotes..."):
    index = score_index(selected)
    all_events, errors = [], []
    for s in selected:
        items, err = events_for(s)
        if err: errors.append(f"{s}: {err}")
        all_events.extend((s, e) for e in items)

current = utc().astimezone(CT).date()
def in_window(s, e, game):
    live = bool(e.get("live") or (e.get("eventState") or {}).get("live") or (game and game["state"] == "in"))
    if window == "🔴 Live": return live
    date = parse_date(e.get("eventDate") or e.get("startTime") or e.get("startDate"))
    if not date: return False
    d = date.astimezone(CT).date()
    if window == "📅 Today": return d == current
    if window == "Tomorrow": return d == current + timedelta(days=1)
    return current <= d <= current + timedelta(days=3)

rows, live_count, quoted = [], 0, 0
for s, e in all_events:
    game = event_score(s, e, index)
    if not in_window(s, e, game): continue
    is_live = bool(e.get("live") or (e.get("eventState") or {}).get("live") or (game and game["state"] == "in"))
    if is_live: live_count += 1
    for m in e.get("markets") or []:
        if not isinstance(m, dict) or m.get("closed") or m.get("archived"): continue
        slug = m.get("slug")
        if not slug: continue
        sides = m.get("marketSides") or []
        # One contract is one instrument. Only label a side when the API identifies it.
        selection = (m.get("titleShort") or m.get("question") or m.get("title") or slug)
        if len(sides) == 1 and isinstance(sides[0], dict):
            selection = (sides[0].get("team") or {}).get("name") or sides[0].get("description") or selection
        market_type = str(m.get("sportsMarketTypeV2") or m.get("sportsMarketType") or "Market").replace("SPORTS_MARKET_TYPE_", "").replace("_", " ").title()
        rows.append({"event_id":str(e.get("id")), "sport":s, "title":str(e.get("title") or "Sports event"),
                     "slug":str(slug), "selection":str(selection), "market_type":market_type,
                     "start":e.get("eventDate") or e.get("startTime") or e.get("startDate"),
                     "live":is_live, "game_state":game["state"] if game else "unknown",
                     "score_detail":game["detail"] if game else "", "quote":None, "error":None})

# Prefer games already underway, then the earliest scheduled. Bound API calls.
rows.sort(key=lambda x:(not x["live"], parse_date(x["start"]) or datetime.max.replace(tzinfo=timezone.utc)))
for x in rows[:max_quotes]:
    x["quote"], x["error"] = bbo(x["slug"])
    if x["quote"] and x["quote"]["valid"]: quoted += 1
observations(rows[:max_quotes])

st.subheader("📡 Polymarket US — Live & Open Sports Markets")
a,b,c,d = st.columns(4)
a.metric("Events discovered", len(all_events))
b.metric("Markets in view", len(rows))
c.metric("Valid BBO quotes", quoted)
d.metric("Live events in view", live_count)
if errors:
    with st.expander("Feed diagnostics"):
        for error in errors: st.caption(error)
if not rows: st.info("No matching markets for this sport and time window. Try Next 3 Days or another sport.")
else:
    st.caption(f"Showing up to {min(len(rows), max_quotes)} priced/checked contracts. OPEN market ≠ LIVE game. No unsupported prices are invented.")
    for x in rows[:max_quotes]: display_row(x)

st.subheader("👑 Queen's Top 10 — Watchlist")
watch = [x for x in rows[:max_quotes] if x.get("quote") and x["quote"]["valid"] and "OPEN" in x["quote"]["state"].upper()]
watch.sort(key=lambda x:(x["quote"]["ask"]-x["quote"]["bid"], x["quote"]["ask"]))
unique, seen = [], set()
for x in watch:
    if x["event_id"] in seen: continue
    unique.append(x); seen.add(x["event_id"])
for i,x in enumerate(unique[:10], 1): display_row(x, i)
if not unique: st.info("No verified open two-sided quotes in this scan. Queen says WAIT.")
st.caption("Watchlist ranks narrower bid/ask spreads, NOT estimated profitability or likelihood of winning. No VALUE PICK claims without a validated model.")

left,right = st.columns(2)
with left:
    st.subheader("🎯 Royal Parlays")
    legs = st.radio("Parlay legs", [2,3,5,10], horizontal=True)
    st.info("Parlay calculations paused until compatible Polymarket US combo pricing and each leg's settlement rules are verified. No fabricated combined odds.")
with right:
    st.subheader("🔥 Underdog Watch")
    dogs = [x for x in watch if x["quote"]["ask"] < .5]
    if dogs:
        for x in dogs[:4]: st.write(f"**{x['selection']}** · {x['sport']} · ask {x['quote']['ask']:.0%} · WATCH ONLY")
    else: st.info("No lower-priced outcomes with verified quotes in this scan.")
    st.caption("A low price does not prove an upset opportunity or positive expected value.")

with st.expander("📊 Royal Paper Observation History", expanded=False):
    with db() as c:
        history = c.execute("SELECT * FROM poly_watch_v293 ORDER BY observed_at DESC LIMIT 50").fetchall()
        total = c.execute("SELECT COUNT(*) FROM poly_watch_v293").fetchone()[0]
    st.caption(f"{total} distinct contract observations. These are NOT wagers, wins, or losses. First seen quote is locked per contract.")
    for r in history:
        st.write(f"{r['sport']} · {r['selection']} · bid {r['bid']:.0%} / ask {r['ask']:.0%} · {local(r['observed_at'])} · {r['market_state']}")
st.caption("No real orders. Local SQLite on Streamlit Community Cloud can be lost on restart/redeploy; persistent storage is required for dependable long-term tracking.")
