
import streamlit as st
import requests
import sqlite3
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# ==================================================
# QUEEN'S EDGE | 254 — ROYAL SPORTS SNIPER
# VERSION 1.0 — LIVE MARKET SCANNER
# PAPER RESEARCH ONLY — NO REAL TRADES
# ==================================================

st.set_page_config(
    page_title="Royal Sports Sniper | Queen's Edge 254",
    page_icon="👑",
    layout="wide",
    initial_sidebar_state="collapsed",
)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
POLY = "https://gamma-api.polymarket.com"
DB = Path("royal_sports_history.db")

SPORT_WORDS = (
    "nba", "nfl", "nhl", "mlb", "ncaa",
    "basketball", "football", "baseball",
    "hockey", "soccer", "tennis",
    "ufc", "mma", "golf", "wnba",
    "touchdown", "rebounds", "assists",
    "passing yards", "rushing yards",
    "strikeouts", "player points",
    "premier league", "champions league",
)

# -------------------------------
# ROYAL DESIGN
# -------------------------------
st.markdown("""
<style>
.stApp {
    background: #100b1c;
    color: #f4edff;
}
.block-container {
    max-width: 1250px;
    padding-top: 1.3rem;
}
h1, h2, h3 {
    color: #d8b4fe !important;
}
div[data-testid="stMetric"] {
    background: #251936;
    border: 1px solid #493265;
    border-radius: 13px;
    padding: 12px;
}
div[data-testid="stMetricValue"] {
    color: #e8d3ff;
}
div.stButton > button {
    background: #673ab7;
    color: white;
    border-radius: 10px;
    border: none;
}
div[data-testid="stExpander"] {
    background: #20172f;
    border-radius: 12px;
}
.royal-card {
    background: #251936;
    border: 1px solid #493265;
    padding: 17px;
    border-radius: 14px;
    margin-bottom: 12px;
}
.small {
    color: #baa9d2;
    font-size: 0.87rem;
}
.good {
    color: #75e2b0;
    font-weight: bold;
}
</style>
""", unsafe_allow_html=True)


# -------------------------------
# DATA STORAGE
# -------------------------------
def database():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT,
            platform TEXT,
            market_id TEXT,
            title TEXT,
            yes_price REAL,
            status TEXT
        )
    """)
    con.commit()
    return con


def save_scan(markets):
    if not markets:
        return

    con = database()
    stamp = datetime.now(timezone.utc).isoformat()

    con.executemany("""
        INSERT INTO scans (
            scanned_at, platform, market_id,
            title, yes_price, status
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, [
        (
            stamp,
            m["platform"],
            m["id"],
            m["title"],
            m["price"],
            "MARKET DISCOVERED",
        )
        for m in markets
    ])

    con.commit()
    con.close()


# -------------------------------
# HTTP CONNECTION
# -------------------------------
def get_json(url, params=None):
    response = requests.get(
        url,
        params=params,
        timeout=18,
        headers={
            "User-Agent": "RoyalSportsSniper/1.0",
            "Accept": "application/json",
        },
    )
    response.raise_for_status()
    return response.json()


def number(value):
    try:
        if value is None or value == "":
            return None
        x = float(value)
        return x if 0 <= x <= 1 else None
    except (TypeError, ValueError):
        return None


def kalshi_price(m):
    # Prefer dollar-denominated bid/ask fields.
    for field in ("yes_ask_dollars", "yes_ask"):
        value = number(m.get(field))
        if value is not None:
            return value

    # Legacy integer-cent fields.
    value = m.get("yes_ask")
    try:
        if value is not None:
            x = float(value)
            if 0 <= x <= 100:
                return x / 100
    except (TypeError, ValueError):
        pass

    return None


# -------------------------------
# KALSHI SPORTS
# -------------------------------
def fetch_kalshi():
    markets = []
    cursor = None

    # Fetch a limited set of sports events.
    for _ in range(3):
        params = {
            "limit": 100,
            "category": "Sports",
            "with_nested_markets": "true",
            "status": "open",
        }
        if cursor:
            params["cursor"] = cursor

        data = get_json(
            f"{KALSHI}/events",
            params=params,
        )

        for event in data.get("events", []):
            event_title = event.get("title", "Sports event")

            for m in event.get("markets", []):
                if m.get("status") != "active":
                    continue

                title = (
                    m.get("title")
                    or m.get("subtitle")
                    or event_title
                )

                markets.append({
                    "platform": "Kalshi",
                    "id": str(m.get("ticker", "")),
                    "title": title,
                    "event": event_title,
                    "price": kalshi_price(m),
                    "url": "",
                })

        cursor = data.get("cursor")
        if not cursor:
            break

    return markets


# -------------------------------
# POLYMARKET SPORTS
# -------------------------------
def fetch_polymarket():
    markets = []

    # Gamma is a discovery source for Polymarket.
    # Availability may differ from Polymarket US.
    for offset in (0, 100, 200):
        data = get_json(
            f"{POLY}/events",
            params={
                "limit": 100,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "tag_slug": "sports",
            },
        )

        if not isinstance(data, list):
            break

        for event in data:
            event_title = event.get("title", "Sports event")

            for m in event.get("markets", []):
                if m.get("closed") or not m.get("active", True):
                    continue

                title = m.get("question") or event_title

                prices = m.get("outcomePrices", [])
                outcomes = m.get("outcomes", [])

                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except (ValueError, TypeError):
                        prices = []

                if isinstance(outcomes, str):
                    try:
                        outcomes = json.loads(outcomes)
                    except (ValueError, TypeError):
                        outcomes = []

                price = None
                if isinstance(outcomes, list) and isinstance(prices, list):
                    for i, outcome in enumerate(outcomes):
                        if str(outcome).strip().lower() == "yes":
                            if i < len(prices):
                                price = number(prices[i])
                            break

                markets.append({
                    "platform": "Polymarket",
                    "id": str(m.get("id", "")),
                    "title": title,
                    "event": event_title,
                    "price": price,
                    "url": event.get("slug", ""),
                })

        if len(data) < 100:
            break

    return markets


# -------------------------------
# CACHE / SCANNER
# -------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def scan_markets():
    results = []
    errors = []

    for name, func in (
        ("Kalshi", fetch_kalshi),
        ("Polymarket", fetch_polymarket),
    ):
        try:
            found = func()
            results.extend(found)
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    unique = {}
    for market in results:
        key = (market["platform"], market["id"])
        unique[key] = market

    return list(unique.values()), errors


def classify(title):
    text = title.lower()

    if any(w in text for w in (
        "rebounds", "assists", "passing",
        "rushing", "touchdown", "strikeouts",
        "shots on goal", "player points",
    )):
        return "Player Props"

    if any(w in text for w in (
        "total", "over", "under",
        "combined points",
    )):
        return "Game Totals"

    return "Other Sports Markets"


def price_text(price):
    if price is None:
        return "Not available"
    return f"{price * 100:.1f}¢"


# -------------------------------
# HEADER
# -------------------------------
st.markdown(
    "<h1 style='text-align:center'>"
    "👑 QUEEN'S EDGE | 254"
    "</h1>",
    unsafe_allow_html=True,
)

st.markdown(
    "<h2 style='text-align:center'>"
    "ROYAL SPORTS SNIPER"
    "</h2>",
    unsafe_allow_html=True,
)

st.caption(
    "Kalshi + Polymarket market discovery | "
    "Sports intelligence | Paper research"
)

st.info(
    "VERSION 1: Live market discovery only. "
    "Queen is not yet making statistical picks. "
    "Displayed prices are indicative and may not be executable."
)

# -------------------------------
# REFRESH
# -------------------------------
left, right = st.columns([2, 1])

with left:
    st.write("👑 Royal Market Scanner")

with right:
    if st.button("🔄 Refresh Markets", use_container_width=True):
        scan_markets.clear()
        st.rerun()

with st.spinner("Queen is checking sports markets..."):
    markets, errors = scan_markets()

if "last_saved" not in st.session_state:
    st.session_state.last_saved = 0.0

# Limit repeated database writes.
if markets and time.time() - st.session_state.last_saved > 60:
    save_scan(markets)
    st.session_state.last_saved = time.time()

# -------------------------------
# SUMMARY
# -------------------------------
kalshi_count = sum(
    1 for m in markets if m["platform"] == "Kalshi"
)
poly_count = sum(
    1 for m in markets if m["platform"] == "Polymarket"
)

a, b, c = st.columns(3)

a.metric("Markets Found", len(markets))
b.metric("Kalshi", kalshi_count)
c.metric("Polymarket", poly_count)

st.caption(
    "Discovery is limited to the pages requested. "
    "These counts do not represent every available market."
)

for error in errors:
    st.warning(f"Connection issue — {error}")

# -------------------------------
# QUEEN'S TOP PICKS
# -------------------------------
st.subheader("👑 QUEEN'S TOP PICKS")

st.info(
    "No verified picks yet. Queen needs a sports "
    "statistics feed, a tested probability model, "
    "and executable market prices before ranking value."
)

# -------------------------------
# ALL GOOD PICKS
# -------------------------------
st.subheader("💜 ALL GOOD PICKS")

st.write(
    "This section will show every market that "
    "passes Queen's statistical value checks, "
    "not just her Top 5."
)

st.caption("No qualified picks available in Version 1.")

# -------------------------------
# LIVE OPPORTUNITIES
# -------------------------------
st.subheader("🔴 LIVE OPPORTUNITIES")

st.write(
    "Live game clocks, player statistics, "
    "and changing recommendations will appear "
    "here after a verified sports feed is connected."
)

# -------------------------------
# MARKET EXPLORER
# -------------------------------
st.subheader("🏟️ SPORTS MARKET EXPLORER")

search = st.text_input(
    "Search teams, players, games or contracts",
    placeholder="Example: basketball, touchdowns, rebounds",
)

category = st.selectbox(
    "Market type",
    [
        "All Markets",
        "Player Props",
        "Game Totals",
        "Other Sports Markets",
    ],
)

platform = st.selectbox(
    "Platform",
    ["Both", "Kalshi", "Polymarket"],
)

filtered = []

for market in markets:
    if search:
        haystack = (
            market["title"] + " " + market["event"]
        ).lower()
        if search.lower() not in haystack:
            continue

    if category != "All Markets":
        if classify(market["title"]) != category:
            continue

    if platform != "Both":
        if market["platform"] != platform:
            continue

    filtered.append(market)

st.caption(f"{len(filtered)} matching markets")

if not filtered:
    st.warning(
        "No matching markets were returned. "
        "Try another search or refresh."
    )

for market in filtered[:50]:
    with st.container(border=True):
        st.markdown(f"**{market['title']}**")
        st.caption(
            f"{market['platform']} | "
            f"{classify(market['title'])}"
        )
        st.write(
            "Indicative YES price: "
            + price_text(market["price"])
        )
        st.caption(
            "QUEEN'S DECISION: NOT YET EVALUATED"
        )

if len(filtered) > 50:
    st.caption(
        "Showing the first 50 matches. "
        "Narrow your search to see more."
    )

# -------------------------------
# ROYAL TRACKING CENTER
# -------------------------------
with st.expander(
    "📊 ROYAL TRACKING CENTER — FULL HISTORY",
    expanded=False,
):
    st.write(
        "This section records discovered markets "
        "and will later include decisions, "
        "statistics, paper entries, exits, "
        "skips, wins, losses and model accuracy."
    )

    con = database()

    count = con.execute(
        "SELECT COUNT(*) FROM scans"
    ).fetchone()[0]

    st.metric("Recorded Market Snapshots", count)

    rows = con.execute("""
        SELECT
            scanned_at,
            platform,
            title,
            yes_price,
            status
        FROM scans
        ORDER BY id DESC
        LIMIT 100
    """).fetchall()

    con.close()

    if rows:
        st.dataframe(
            [
                {
                    "Time (UTC)": r[0],
                    "Platform": r[1],
                    "Market": r[2],
                    "YES Price": price_text(r[3]),
                    "Status": r[4],
                }
                for r in rows
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.warning(
        "This first version uses a local SQLite file. "
        "On Streamlit Community Cloud, local data "
        "may disappear after an app restart or redeploy. "
        "Persistent cloud storage is needed before "
        "relying on long-term tracking."
    )

st.divider()

st.caption(
    "QUEEN'S EDGE | 254 — ROYAL SPORTS SNIPER V1.0 "
    "| Independent research dashboard | "
    "No trading orders are placed."
)
