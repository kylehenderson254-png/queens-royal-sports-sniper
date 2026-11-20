import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st

st.set_page_config(page_title="Royal Sports Sniper | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
POLY = "https://gamma-api.polymarket.com"
DB = Path("royal_sports_history.db")
SPORTS = {"NFL": ["nfl", "football", "touchdown", "passing yards", "rushing yards"], "NBA": ["nba", "basketball", "rebounds", "assists", "wnba"], "MLB": ["mlb", "baseball", "strikeouts"], "NHL": ["nhl", "hockey", "shots on goal"], "Soccer": ["soccer", "premier league", "champions league", "epl", "mls", "uefa", "fifa"], "Tennis": ["tennis", "atp", "wta", "wimbledon"], "MMA": ["ufc", "mma", "boxing"], "Golf": ["golf", "pga"], "College": ["ncaa", "ncaaf", "ncaab", "college football", "college basketball"]}
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
    r = requests.get(url, params=params, headers={"Accept": "application/json", "User-Agent": "RoyalSportsSniper/1.2"}, timeout=17)
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
        groups = ["nba", "nfl", "nhl", "mlb", "wnba", "ncaab", "ncaaf", "epl", "mls", "ufc", "tennis"]
        league = next((i for i, name in enumerate(groups) if name in t), len(groups))
        game = 0 if any(w in t for w in ("game", "match", "points", "touchdown", "rebound", "assist", "spread", "total")) else 1
        return (league, game, t)
    series.sort(key=priority)
    results = []
    for s in series[:40]:
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
    for tag in ("nba", "nfl", "nhl", "mlb", "soccer", "tennis", "ufc", "sports"):
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


# The replacement image has no white margins; Streamlit scales it to mobile width.
banner = Path("royal_sports_banner.png")
if banner.exists():
    st.image(str(banner), use_container_width=True)
else:
    st.markdown("# 👑 QUEEN'S EDGE | 254 — ROYAL SPORTS SNIPER")
st.caption("🏈 NFL · 🏀 NBA · ⚾ MLB · 🏒 NHL · ⚽ Soccer · 🎾 Tennis · 🥊 MMA")
st.caption("V1.2 · Sports market screening · No verified statistical picks or live score feed yet · No orders placed")
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
st.caption(f"Partial discovery from up to 40 of {series_count} Kalshi sports series and sampled Polymarket global sports tags. Polymarket global is not necessarily Polymarket US. Prices may be stale or non-executable.")

st.markdown('<div class="royal-head">👑 TOP 5 TO INVESTIGATE</div>', unsafe_allow_html=True)
st.caption("Upcoming, active markets prioritized by timing and activity. NOT predictions, guaranteed picks, or confirmed positive expected value.")
candidates = [m for m in markets if m["decision"].startswith("🟡")]
if candidates:
    for i,m in enumerate(candidates[:5],1): display_card(m,i)
else:
    st.info("No eligible research candidates in the sampled results. Refresh later; Queen will not manufacture picks.")

st.markdown('<div class="royal-head">💚 QUALIFIED VALUE PICKS</div>', unsafe_allow_html=True)
st.info("No statistically verified value picks yet. This needs team/player statistics, tested probabilities, and fees/spread checks.")
st.markdown('<div class="royal-head">🔴 LIVE GAME OPPORTUNITIES</div>', unsafe_allow_html=True)
st.info("Live scores and player stats are not connected yet. Market prices alone cannot verify live-game performance.")

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

with st.expander("📊 ROYAL TRACKING CENTER — SCANS & HISTORY", expanded=False):
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
st.caption("QUEEN'S EDGE | 254 · ROYAL SPORTS SNIPER V1.2 · Research only")
