import os, re, sqlite3, hashlib, html
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote
import requests
import streamlit as st

st.set_page_config(page_title="ROYAL SPORTS SNIPER V2.9.5 | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
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
    c.execute("""CREATE TABLE IF NOT EXISTS royal_paper_v294 (
      id TEXT PRIMARY KEY, event_id TEXT, sport TEXT, title TEXT, slug TEXT,
      selection TEXT, entry_ask REAL, stake REAL, entry_at TEXT,
      last_bid REAL, last_seen TEXT, game_state TEXT,
      status TEXT DEFAULT 'OPEN', settled_at TEXT, pnl REAL,
      note TEXT)""")
    c.commit()
    return c

@st.cache_data(ttl=120, show_spinner=False)
def api(path, params=None):
    try:
        r = requests.get(BASE + path, params=params, headers={"Accept":"application/json", "User-Agent":"RoyalSportsSniper254/2.9.5"}, timeout=12)
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
                          "scores": {t.get("team", {}).get("displayName", ""):t.get("score") for t in teams}, "winner": next((t.get("team", {}).get("displayName", "") for t in teams if t.get("winner") is True), None)}
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
    heading = f"{number}. {x['title']}" if number else x['title']
    with st.container(border=True):
        st.markdown(f"**{html.escape(heading)}**")
        st.caption(f"{x['sport']} · {html.escape(x['selection'])} · {x['market_type']} · {local(x['start'])}")
        if x['live']:
            st.markdown("🔴 **LIVE GAME**" + (f" · {html.escape(x['score_detail'])}" if x['score_detail'] else ""))
        elif x['game_state'] == 'pre':
            st.caption("📅 Scheduled game")
        else:
            st.caption("Game identity / status not confirmed")
        if q and q['valid']:
            st.markdown(f"**Buy YES: {q['ask']:.0%}** · Sell YES: {q['bid']:.0%} · **{'WATCH' if 'OPEN' in q['state'].upper() else 'WAIT'}**")
        else:
            st.markdown("**WAIT · Price unavailable**")
        with st.expander("Why WATCH? · Price, payout & paper-test check"):
            st.write("Outcome:", x['selection'])
            st.write("Market:", x['slug'])
            st.write("Market status:", q['state'] if q else "Unknown")
            if q and q['valid']:
                st.write(f"Illustrative $100 YES profit if this outcome wins: ${money_profit(q['ask'], 100):,.2f} before fees and slippage.")
            st.write("Paper entry:", paper_reason(x))
            st.caption("A price is not a prediction. Quotes are indicative and may change.")


def paper_reason(x):
    q = x.get('quote')
    if not q or not q.get('valid'): return "SKIP — no valid two-sided quote"
    if not x.get('verified_side'): return "SKIP — contract side is not verified as one team"
    if x.get('market_type', '').lower() not in ('moneyline', 'money line'): return "SKIP — not a verified moneyline"
    game = x.get('score_game')
    if not game: return "SKIP — ESPN team/game match not confirmed"
    if game['state'] != 'pre' or x['live']: return "SKIP — game is not pregame"
    if clean(x['selection']) not in [clean(t) for t in game['teams']]: return "SKIP — selected team does not match scoreboard"
    if 'OPEN' not in q['state'].upper(): return "SKIP — market not confirmed open"
    if q['ask'] - q['bid'] > .03: return "SKIP — spread exceeds 3 cents"
    if not (.25 <= q['ask'] <= .75): return "SKIP — price outside 25–75 cent testing range"
    return "ELIGIBLE for experimental paper entry (not a value prediction)"

def paper_update(items):
    """Conservative forward test: log only independently matched pregame team-moneyline YES.
    Prices are indicative. An entry is an experiment, not a recommendation.
    ESPN winner is used ONLY for matched completed games; otherwise stays OPEN.
    """
    now = utc().isoformat()
    with db() as c:
        for x in items:
            q = x.get("quote")
            if not q or not q.get("valid"):
                continue
            ident = hashlib.sha256((x["event_id"] + "|" + x["slug"]).encode()).hexdigest()[:32]
            existing = c.execute("SELECT * FROM royal_paper_v294 WHERE id=?", (ident,)).fetchone()
            game = x.get("score_game")
            if existing:
                if existing["status"] == "OPEN":
                    c.execute("UPDATE royal_paper_v294 SET last_bid=?, last_seen=?, game_state=? WHERE id=?",
                              (q["bid"], now, game["state"] if game else "unknown", ident))
                    if game and game["state"] == "post" and game.get("winner"):
                        winner = clean(game["winner"])
                        selection = clean(existing["selection"])
                        # Exact team identity only; never grade a draw, a non-team market, or uncertain mapping.
                        teams = [clean(n) for n in game["teams"]]
                        if selection in teams and winner in teams:
                            won = selection == winner
                            pnl = (existing["stake"] * (1 / existing["entry_ask"] - 1)) if won else -existing["stake"]
                            c.execute("UPDATE royal_paper_v294 SET status=?, settled_at=?, pnl=?, note=? WHERE id=?",
                                      ("WIN" if won else "LOSS", now, round(pnl,2), "ESPN final winner; verify official market settlement", ident))
                continue
            # One candidate per event; never silently create opposing positions.
            if c.execute("SELECT 1 FROM royal_paper_v294 WHERE event_id=?", (x["event_id"],)).fetchone():
                continue
            if not (x.get("verified_side") and x.get("market_type", "").lower() in ("moneyline", "money line")):
                continue
            if not game or game["state"] != "pre" or x["live"]:
                continue
            if clean(x["selection"]) not in [clean(t) for t in game["teams"]]:
                continue
            if "OPEN" not in q["state"].upper() or q["ask"] - q["bid"] > .03:
                continue
            # Avoid extremely cheap longshots and near-certain contracts; paper test a repeatable price band.
            if not (.25 <= q["ask"] <= .75):
                continue
            c.execute("""INSERT OR IGNORE INTO royal_paper_v294
              (id,event_id,sport,title,slug,selection,entry_ask,stake,entry_at,last_bid,last_seen,game_state,note)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (ident,x["event_id"],x["sport"],x["title"],x["slug"],x["selection"],q["ask"],100.0,now,q["bid"],now,"pre","Experimental price/liquidity filter; not a validated edge"))
        c.commit()


def paper_panel():
    with db() as c:
        records = c.execute("SELECT * FROM royal_paper_v294 ORDER BY entry_at DESC LIMIT 100").fetchall()
        totals = c.execute("SELECT COUNT(*), SUM(status='WIN'), SUM(status='LOSS'), COALESCE(SUM(pnl),0) FROM royal_paper_v294").fetchone()
    st.subheader("🧪 Queen's Automatic $100 Paper Tests")
    a,b,c,d = st.columns(4)
    a.metric("Paper entries", totals[0]); b.metric("Wins", totals[1] or 0)
    c.metric("Losses", totals[2] or 0); d.metric("Settled paper P&L", f"${totals[3]:,.2f}")
    st.caption("Each qualifying pregame YES contract is simulated at its observed ask; no orders are placed. Open P&L uses indicative bid, not a guaranteed cash-out. Game results are provisional until official market settlement is checked. Fees/slippage excluded.")
    if not records:
        st.info("No qualifying paper entries yet. Select Today or Next 3 Days and scan. Queen will not force a test when the side, game, or quote cannot be verified.")
    for r in records[:12]:
        with st.container(border=True):
            a,b = st.columns([3,1])
            a.markdown(f"**{r['selection']} — {r['title']}**")
            a.caption(f"{r['sport']} · Entry {r['entry_ask']:.0%} · {local(r['entry_at'])} · $100 simulated YES")
            b.markdown(f"**{r['status']}**")
            if r['status'] == 'OPEN' and r['last_bid'] is not None:
                value = 100 * r['last_bid'] / r['entry_ask']
                b.caption(f"Indicative exit P&L ${value-100:+,.2f}")
            elif r['pnl'] is not None:
                b.caption(f"Paper result ${r['pnl']:+,.2f}")
            with st.expander("Contract and test details"):
                st.write("Contract:", r['slug'])
                st.write("Observed entry ask:", f"{r['entry_ask']:.4f}")
                st.write("Latest bid:", f"{r['last_bid']:.4f}" if r['last_bid'] is not None else "unavailable")
                st.write("Last checked:", local(r['last_seen']))
                st.caption(r['note'] or "Experimental observation")


st.markdown("""<style>
.block-container{padding-top:.65rem;max-width:1060px;padding-left:1rem;padding-right:1rem}
h1,h2,h3{color:#c49aff!important;letter-spacing:-.02em}
h1{font-size:clamp(1.45rem,4vw,2.5rem)!important}
h2{font-size:clamp(1.2rem,3vw,1.6rem)!important}
[data-testid="stMetric"]{background:#211926;padding:.7rem;border-radius:18px;border:1px solid #362840}
[data-testid="stMetricLabel"]{font-size:.8rem}
[data-testid="stMetricValue"]{font-size:1.45rem}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:18px!important}
.stButton>button{border-radius:22px;min-height:2.8rem}
[data-testid="stExpander"]{border-radius:15px}
@media(max-width:650px){
 .block-container{padding-left:.8rem;padding-right:.8rem}
 [data-testid="stHorizontalBlock"]{gap:.45rem!important}
 [data-testid="column"]{min-width:calc(50% - .5rem)!important;flex:1 1 calc(50% - .5rem)!important}
 [data-testid="stMetric"]{padding:.55rem}
 [data-testid="stMetricValue"]{font-size:1.25rem}
 div[data-testid="stVerticalBlockBorderWrapper"]{padding:.2rem!important}
}
</style>""", unsafe_allow_html=True)

# Accept common banner filenames, including images kept in an assets/ folder.
banner = None
for folder in (Path('.'), Path('assets'), Path('images')):
    if not folder.is_dir(): continue
    images = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg','.webp')]
    preferred = [p for p in images if any(k in p.stem.lower() for k in ('banner','royal','sniper','queen'))]
    if preferred:
        banner = sorted(preferred, key=lambda p: (not ('banner' in p.stem.lower()),p.name))[0]
        break
if banner:
    st.image(str(banner), use_container_width=True)
else:
    st.markdown("# 👑 ROYAL SPORTS SNIPER | 254")
    st.caption("Original banner not found. Add its image to the GitHub repository to restore it.")
st.caption("V2.9.5 · POLYMARKET US ONLY · PAPER TESTING · NO REAL ORDERS")

sport = st.radio("🏆 Choose Your Sport", ["All Sports"] + list(LEAGUES), horizontal=True)
window = st.radio("📅 Choose Game Window", ["🔴 Live", "📅 Today", "Tomorrow", "Next 3 Days"], horizontal=True, index=1)
with st.expander("⚙️ Scan settings & important notes"):
    max_leagues = st.slider("Maximum leagues in All Sports", 1, len(LEAGUES), 6)
    max_quotes = st.slider("Maximum contracts to price per scan", 5, 100, 35, step=5)
    st.caption("The public Polymarket US API is used without an account key. Quotes are cached for two minutes. Game scores come from ESPN and may lag.")
    st.caption("Paper entries are simulated at the observed ask; the experimental entry filter is NOT a validated prediction or positive-edge strategy. Existing history is preserved separately.")

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
                     "slug":str(slug), "selection":str(selection), "market_type":market_type, "verified_side":len(sides)==1 and isinstance(sides[0],dict) and bool((sides[0].get("team") or {}).get("name")),
                     "start":e.get("eventDate") or e.get("startTime") or e.get("startDate"),
                     "live":is_live, "game_state":game["state"] if game else "unknown",
                     "score_detail":game["detail"] if game else "", "score_game":game, "quote":None, "error":None})

# Prefer games already underway, then the earliest scheduled. Bound API calls.
rows.sort(key=lambda x:(not x["live"], parse_date(x["start"]) or datetime.max.replace(tzinfo=timezone.utc)))
for x in rows[:max_quotes]:
    x["quote"], x["error"] = bbo(x["slug"])
    if x["quote"] and x["quote"]["valid"]: quoted += 1
observations(rows[:max_quotes])
paper_update(rows[:max_quotes])

st.subheader("📡 Market Feed")
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
    with st.expander(f"📡 View all {min(len(rows), max_quotes)} scanned markets", expanded=False):
        for x in rows[:max_quotes]: display_row(x)

paper_panel()

with st.expander("🔎 Why paper entries are skipped", expanded=False):
    from collections import Counter
    reasons = Counter(paper_reason(x) for x in rows[:max_quotes])
    if not reasons:
        st.write("No markets checked in this game window.")
    for reason, count in reasons.most_common():
        st.write(f"{count} contracts · {reason}")
    st.caption("The app may skip eligible entries when a paper entry already exists for the same event. This section is for debugging, not betting advice.")

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
st.caption("No real orders. Paper records are experimental and do not establish profitability. Local SQLite on Streamlit Community Cloud can be lost on restart/redeploy; persistent storage is required for dependable long-term tracking.")
