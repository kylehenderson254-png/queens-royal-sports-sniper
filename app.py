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
    r = requests.get(url, params=params, headers={"Accept": "application/json", "User-Agent": "RoyalSportsSniper/1.1"}, timeout=17)
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
    # Prefer recognizable leagues, and limit API requests to avoid rate limits.
    series.sort(key=lambda s: (sport_from_text(str(s.get("ticker", "")) + " " + str(s.get("title", ""))) is None, str(s.get("ticker", ""))))
    results = []
    for s in series[:28]:
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
                "price": kalshi_ask(m), "url": "https://kalshi.com/markets/" + quote(str(ticker).lower()),
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


def polymarket_markets():
    results = []
    for offset in (0, 100, 200):
        events = get_json(f"{POLY}/events", {"tag_slug": "sports", "active": "true", "closed": "false", "limit": 100, "offset": offset, "order": "volume_24hr", "ascending": "false"})
        if not isinstance(events, list):
            break
        for event in events:
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
                    "type": kind_of(title), "price": poly_prices(m),
                    "url": "https://polymarket.com/event/" + quote(str(event.get("slug") or "")),
                    "volume": volume, "close": m.get("endDate") or event.get("endDate") or ""})
        if len(events) < 100:
            break
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


def display_card(m, idx=None):
    with st.container(border=True):
        a, b = st.columns([4, 1])
        with a:
            st.markdown(f"**{'🏅 ' + str(idx) + '. ' if idx else ''}{m['title']}**")
            st.caption(f"{m['sport']} · {m['type']} · {m['platform']}")
            st.markdown("🟡 **WATCH — NOT YET RATED** · Market discovered; no statistical edge verified.")
        with b:
            st.metric("Indicative YES", cents(m["price"]))
        if m["url"]:
            st.link_button("View market", m["url"])


banner = Path("royal_sports_banner.png")
if banner.exists():
    st.image(str(banner), use_container_width=True)
else:
    st.markdown("# 👑 QUEEN'S EDGE | 254")
    st.markdown("## 🏆 ROYAL SPORTS SNIPER")
    st.caption("Upload royal_sports_banner.png to the repository root to display your banner.")
st.caption("🏈 NFL  ·  🏀 NBA  ·  ⚾ MLB  ·  🏒 NHL  ·  ⚽ Soccer  ·  🎾 Tennis  ·  🥊 MMA")
st.info("👑 V1.1 scans sports markets only. WATCH means worth researching, NOT a recommended bet. No verified probability model, live scores, parlay engine, or order execution yet.")
left, right = st.columns([3, 1])
with left:
    st.markdown("### 💜 Royal Sports Market Scanner")
with right:
    if st.button("🔄 Refresh", use_container_width=True):
        scan.clear()
        st.rerun()
with st.spinner("Queen is checking sports markets..."):
    markets, errors, series_count = scan()
for error in errors:
    st.warning(error)

# Use market activity for display ordering, NOT as evidence of profitable value.
markets.sort(key=lambda x: (x["price"] is None, -x["volume"], x["title"]))
counts = [sum(x["platform"] == p for x in markets) for p in ("Kalshi", "Polymarket")]
a, b, c = st.columns(3)
a.metric("🏟️ Sports Markets", len(markets))
b.metric("👑 Kalshi", counts[0])
c.metric("💜 Polymarket", counts[1])
st.caption(f"Partial discovery: sampled up to 28 of {series_count} Kalshi sports series and up to 300 Polymarket sports-tagged events. Polymarket Gamma represents the global platform, not necessarily Polymarket US.")

st.markdown('<div class="royal-head">👑 TOP 5 MARKETS TO RESEARCH</div>', unsafe_allow_html=True)
st.caption("Sorted by reported market activity and price availability — NOT predicted winners or best value.")
if not markets:
    st.warning("No verified sports markets were returned. Try Refresh or inspect connection warnings.")
for i, m in enumerate(markets[:5], 1):
    display_card(m, i)

st.markdown('<div class="royal-head">💚 ALL GOOD PICKS</div>', unsafe_allow_html=True)
st.caption("This will populate when a tested probability model can verify value after fees and spread.")
st.info("No statistically qualified picks yet. Queen will not label an unrated contract GOOD VALUE.")

st.markdown('<div class="royal-head">🔴 LIVE GAME OPPORTUNITIES</div>', unsafe_allow_html=True)
st.caption("Live scores, minutes remaining, player points, rebounds, touchdowns, and passes require a separate verified sports-statistics feed.")
st.info("Live player and game statistics are not connected in V1.1.")

st.markdown('<div class="royal-head">🏆 SPORTS MARKET EXPLORER</div>', unsafe_allow_html=True)
with st.container(border=True):
    search = st.text_input("Find a team, player, or market", placeholder="e.g. Lakers, NFL, touchdowns")
    a, b, c = st.columns(3)
    with a:
        league = st.selectbox("Sport", ["All Sports"] + list(SPORTS) + ["Other Sports"])
    with b:
        market_type = st.selectbox("Market type", ["All Types", "Moneyline / Other", "Player Props", "Totals", "Spreads", "Combos"])
    with c:
        platform = st.selectbox("Platform", ["Both", "Kalshi", "Polymarket"])
    filtered = [m for m in markets if (not search or search.lower() in (m["title"] + " " + m["sport"]).lower()) and (league == "All Sports" or m["sport"] == league) and (market_type == "All Types" or m["type"] == market_type) and (platform == "Both" or m["platform"] == platform)]
    st.caption(f"{len(filtered)} matching sports contracts · showing up to 12")
    for m in filtered[:12]:
        display_card(m)
    if not filtered:
        st.caption("No matches in the sampled results. Try another filter or Refresh.")

with st.expander("📊 ROYAL TRACKING CENTER — SCANS & HISTORY", expanded=False):
    st.caption("Every snapshot is a discovered market, not a trade or a prediction. Old V1 records are kept separately.")
    if "last_logged" not in st.session_state:
        st.session_state.last_logged = None
    snapshot_id = (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M") if markets else None)
    if markets and snapshot_id != st.session_state.last_logged:
        try:
            log_snapshot(markets)
            st.session_state.last_logged = snapshot_id
        except Exception as e:
            st.warning(f"Local tracking unavailable: {e}")
    try:
        con = init_db()
        total = con.execute("SELECT COUNT(*) FROM royal_v11").fetchone()[0]
        st.metric("V1.1 Market Snapshots", total)
        rows = con.execute("SELECT timestamp,platform,sport,market_type,title,ask,status FROM royal_v11 ORDER BY rowid DESC LIMIT 100").fetchall()
        con.close()
        if rows:
            st.dataframe([{"Time UTC":r[0], "Platform":r[1], "Sport":r[2], "Type":r[3], "Market":r[4], "YES ask":cents(r[5]), "Status":r[6]} for r in rows], use_container_width=True, hide_index=True)
        st.warning("Streamlit Community Cloud local SQLite history can be erased on restart/redeploy. Persistent storage is needed for reliable long-term records.")
    except Exception as e:
        st.warning(f"Tracking unavailable: {e}")
st.divider()
st.caption("QUEEN'S EDGE | 254 · ROYAL SPORTS SNIPER V1.1 · Read-only research · No orders placed")
