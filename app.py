import os
import re
import json
import sqlite3
import unicodedata
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote

import requests
import streamlit as st

st.set_page_config(page_title="Royal Sports Sniper | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
POLY = "https://gamma-api.polymarket.com"
CENTRAL = ZoneInfo("America/Chicago")
DB = Path(os.getenv("ROYAL_DB_PATH", "royal_sports_history.db"))
STARTING_BANKROLL = 10000.0
LEAGUES = {
    "NBA": ("basketball_nba", "basketball/nba"),
    "NFL": ("americanfootball_nfl", "football/nfl"),
    "NCAAB": ("basketball_ncaab", "basketball/mens-college-basketball"),
    "NCAAF": ("americanfootball_ncaaf", "football/college-football"),
}

st.markdown("""<style>
.stApp{background:radial-gradient(ellipse at top,#291044 0%,#110a20 55%,#090611 100%);color:#f5eeff}
.block-container{max-width:1240px;padding-top:1rem;padding-bottom:3rem}
h1,h2,h3{color:#ebd3ff!important}
a{color:#d8a7ff!important}
div[data-testid="stExpander"]{background:#1d1230;border:1px solid #55357b;border-radius:14px}
div.stButton>button{background:linear-gradient(110deg,#7426bd,#aa48eb);color:white;border:0;border-radius:12px;font-weight:700}
.royal-strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;background:#211235;border:1px solid #70419a;border-radius:12px;padding:10px}
.royal-strip>div{text-align:center;min-width:0}.royal-strip small{display:block;font-size:.66rem;color:#c6b1d9}
.royal-strip strong{display:block;color:#f5d47d;font-size:clamp(.76rem,1.6vw,1.05rem);overflow-wrap:anywhere}
@media(max-width:600px){.royal-strip{padding:7px 2px;gap:2px}.royal-strip small{font-size:.54rem}.royal-strip strong{font-size:.72rem}}
</style>""", unsafe_allow_html=True)


def get_json(url, params=None):
    response = requests.get(url, params=params, headers={"Accept": "application/json", "User-Agent": "RoyalSportsSniper/1.7"}, timeout=15)
    response.raise_for_status()
    return response.json(), response.headers


def when(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def local(value):
    date = when(value) if not isinstance(value, datetime) else value
    return date.astimezone(CENTRAL).strftime("%a %b %d · %I:%M %p CT") if date else "Time unavailable"


def price(value, cents=False):
    try:
        number = float(value) / (100 if cents else 1)
        return number if 0 < number < 1 else None
    except (TypeError, ValueError):
        return None


def fmt(value):
    return "Unavailable" if value is None else f"{value * 100:.1f}¢"


def norm(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", value.replace("&", "and")))


def odds_key():
    try:
        return st.secrets.get("ODDS_API_KEY", "") or os.getenv("ODDS_API_KEY", "")
    except (FileNotFoundError, KeyError):
        return os.getenv("ODDS_API_KEY", "")


def implied(american):
    try:
        x = float(american)
        return 100 / (100 + x) if x > 0 else (-x) / (100 - x) if x < 0 else None
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=900, show_spinner=False)
def sportsbook(key, active_leagues):
    """Quota-conserving pregame benchmarks; never treat as synchronized live odds."""
    output, errors, quota = [], [], None
    for league in active_leagues:
        try:
            events, headers = get_json(f"https://api.the-odds-api.com/v4/sports/{LEAGUES[league][0]}/odds/", {
                "apiKey": key, "regions": "us", "markets": "h2h", "oddsFormat": "american"})
            quota = headers.get("x-requests-remaining", quota)
            for e in events:
                home, away = e.get("home_team"), e.get("away_team")
                if not home or not away or not when(e.get("commence_time")):
                    continue
                values = {home: [], away: []}
                for book in e.get("bookmakers", []):
                    for market in book.get("markets", []):
                        if market.get("key") != "h2h":
                            continue
                        probabilities = {x.get("name"): implied(x.get("price")) for x in market.get("outcomes", [])}
                        if len(probabilities) != 2 or any(probabilities.get(t) is None for t in (home, away)):
                            continue
                        total = probabilities[home] + probabilities[away]
                        if total > 0:
                            for team in (home, away):
                                values[team].append(probabilities[team] / total)
                if min(map(len, values.values())):
                    output.append({"league": league, "home": home, "away": away,
                                   "start": when(e["commence_time"]), "books": min(map(len, values.values())),
                                   "probs": {t: sum(v) / len(v) for t, v in values.items()}})
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"{league}: {str(exc)[:100]}")
    return output, errors, quota


@st.cache_data(ttl=120, show_spinner=False)
def scoreboard(league, day):
    data, _ = get_json(f"https://site.api.espn.com/apis/site/v2/sports/{LEAGUES[league][1]}/scoreboard", {
        "dates": day, "limit": 500})
    output = []
    for event in data.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        teams = []
        for c in competition.get("competitors", []):
            team = c.get("team") or {}
            record = next((r.get("summary") for r in c.get("records", []) if r.get("type") in ("total", "overall") and r.get("summary")), None)
            rank = c.get("curatedRank") or {}
            rank = rank.get("current") if isinstance(rank, dict) else None
            teams.append({"name": team.get("displayName", ""), "short": team.get("shortDisplayName", ""),
                          "record": record, "rank": rank if isinstance(rank, int) and 1 <= rank <= 25 else None,
                          "score": c.get("score", "–")})
        if len(teams) != 2:
            continue
        status = event.get("status", {}).get("type", {})
        output.append({"start": when(event.get("date")), "teams": teams,
                       "state": status.get("state", ""), "detail": status.get("shortDetail") or status.get("description", "")})
    return output


def matching_game(event, games):
    """Both full/short names and start time must agree; ambiguous matches fail closed."""
    target = {norm(event["home"]), norm(event["away"])}
    matched = []
    for game in games:
        if not game["start"] or abs((game["start"] - event["start"]).total_seconds()) > 5400:
            continue
        options = [[norm(t["name"]), norm(t["short"])] for t in game["teams"]]
        if all(any(name in options[i] for name in target) for i in (0, 1)) and any(
            target == {a, b} for a in options[0] for b in options[1]):
            matched.append(game)
    return matched[0] if len(matched) == 1 else None


@st.cache_data(ttl=180, show_spinner=False)
def kalshi_scan():
    results = []
    series, _ = get_json(f"{KALSHI}/series", {"category": "Sports"})
    candidates = [s for s in series.get("series", []) if str(s.get("category", "")).lower() == "sports"]
    candidates.sort(key=lambda s: (0 if any(x in str(s.get("ticker", "")).lower() for x in ("nba", "nfl", "ncaab", "ncaaf")) else 1, str(s.get("ticker", ""))))
    for s in candidates[:55]:
        try:
            data, _ = get_json(f"{KALSHI}/markets", {"series_ticker": s["ticker"], "status": "open", "limit": 100})
        except (requests.RequestException, KeyError):
            continue
        for m in data.get("markets", []):
            results.append({"platform": "Kalshi", "id": str(m.get("ticker") or ""),
                            "title": str(m.get("title") or m.get("subtitle") or ""),
                            "context": f"{s.get('title', '')} {s.get('ticker', '')}",
                            "yes": price(m.get("yes_ask_dollars")) or price(m.get("yes_ask"), True),
                            "no": price(m.get("no_ask_dollars")) or price(m.get("no_ask"), True),
                            "volume": float(m.get("volume") or 0), "end": m.get("close_time"),
                            "url": "https://kalshi.com/markets/" + quote(str(s["ticker"]).lower()),
                            "quote_kind": "Exchange ask (depth not verified)"})
    return results, len(candidates)


@st.cache_data(ttl=180, show_spinner=False)
def poly_scan():
    results, seen = [], set()
    for tag in ("nba", "nfl", "ncaab", "ncaaf", "nhl", "soccer", "sports"):
        try:
            events, _ = get_json(f"{POLY}/events", {"tag_slug": tag, "active": "true", "closed": "false", "limit": 50})
        except requests.RequestException:
            continue
        if not isinstance(events, list):
            continue
        for event in events:
            for m in event.get("markets", []):
                identity = str(m.get("id") or "")
                if not identity or identity in seen or m.get("closed") or not m.get("active", True):
                    continue
                seen.add(identity)
                try:
                    outcomes = m.get("outcomes") or []
                    values = m.get("outcomePrices") or []
                    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
                    values = json.loads(values) if isinstance(values, str) else values
                    indexed = {str(o).lower(): price(v) for o, v in zip(outcomes, values)}
                except (ValueError, TypeError):
                    indexed = {}
                results.append({"platform": "Polymarket", "id": identity,
                                "title": str(m.get("question") or event.get("title") or ""),
                                "context": str(event.get("title") or ""),
                                "yes": indexed.get("yes"), "no": indexed.get("no"),
                                "volume": float(m.get("volumeNum") or 0), "end": m.get("endDate"),
                                "url": "https://polymarket.com/event/" + quote(str(event.get("slug") or "")),
                                "quote_kind": "Indicative price, NOT executable ask"})
    return results


@st.cache_data(ttl=180, show_spinner=False)
def markets_scan():
    results, errors, count = [], [], 0
    try:
        batch, count = kalshi_scan()
        results.extend(batch)
    except (requests.RequestException, ValueError, TypeError) as exc:
        errors.append("Kalshi: " + str(exc)[:100])
    try:
        results.extend(poly_scan())
    except (requests.RequestException, ValueError, TypeError) as exc:
        errors.append("Polymarket: " + str(exc)[:100])
    return results, errors, count


def match_contract(event, contracts):
    """Conservative discovery-only matchup match. No title match authorizes a BUY."""
    a, b = norm(event["home"]), norm(event["away"])
    candidates = []
    for m in contracts:
        content = norm(m["title"] + " " + m["context"])
        if a not in content or b not in content:
            continue
        end = when(m["end"])
        if end and end < event["start"] - timedelta(hours=1):
            continue
        candidates.append(m)
    return sorted(candidates, key=lambda m: (m["platform"] != "Kalshi", -(m["volume"] or 0)))[:3]


def paper_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
      trade_id TEXT PRIMARY KEY, market_key TEXT NOT NULL, platform TEXT NOT NULL,
      market_id TEXT NOT NULL, title TEXT NOT NULL, league TEXT, side TEXT NOT NULL,
      grade TEXT NOT NULL, contracts INTEGER NOT NULL, entry_price REAL NOT NULL,
      entry_fee REAL NOT NULL DEFAULT 0, opened_at TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'OPEN', exit_price REAL, exit_fee REAL NOT NULL DEFAULT 0,
      closed_at TEXT, realized_pnl REAL, close_reason TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS paper_activity (
      event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE,
      created_at TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL)""")
    con.commit()
    return con


def paper_summary(con):
    row = con.execute("""SELECT COALESCE(SUM(CASE WHEN status='OPEN' THEN contracts*entry_price+entry_fee ELSE 0 END),0) locked,
        COALESCE(SUM(CASE WHEN status='CLOSED' THEN realized_pnl ELSE 0 END),0) pnl,
        SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) opened FROM paper_trades""").fetchone()
    return {"cash": STARTING_BANKROLL + float(row["pnl"]) - float(row["locked"]),
            "pnl": float(row["pnl"]), "open": row["opened"] or 0}


def paper_strip(account):
    from html import escape
    fields = [("START", f"${STARTING_BANKROLL:,.0f}"), ("AVAILABLE", f"${account['cash']:,.2f}"),
              ("OPEN", str(account["open"])), ("REALIZED P/L", f"${account['pnl']:+,.2f}")]
    html = '<div class="royal-strip">' + "".join(
        f"<div><small>{escape(label)}</small><strong>{escape(value)}</strong></div>" for label, value in fields) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


banner = Path("royal_sports_banner.png")
if banner.exists():
    st.image(str(banner), use_container_width=True)
else:
    st.title("👑 QUEEN'S EDGE | 254 — ROYAL SPORTS SNIPER")
st.caption("V1.7 · Verified-data-first Top 5 · Live scores + team records · PAPER ONLY")
st.markdown("### 👑 Today's Royal Top 5")
st.caption("Best researched opportunities first. Live games win ties, never override missing odds or contract verification.")

if st.button("🔄 Refresh sports and markets"):
    scoreboard.clear()
    markets_scan.clear()
    kalshi_scan.clear()
    poly_scan.clear()
    st.rerun()

key = odds_key()
# Limit free quota: user selects leagues; Odds API cache is 15 minutes and refresh button does not clear it.
with st.expander("⚙️ League coverage & free API usage", expanded=False):
    leagues = st.multiselect("Sportsbook leagues to check", list(LEAGUES), default=["NBA", "NFL"],
                             help="Each selected league can use Odds API quota. NBA and NFL are the conservative default.")
    st.caption("Odds API calls are cached for 15 minutes. Refresh does not force new paid-quota requests.")

odds, odds_errors, quota = sportsbook(key, tuple(leagues)) if key and leagues else ([], [], None)
if not key:
    st.warning("ODDS_API_KEY not configured. Market discovery and ESPN scores can still work, but no probability comparisons are possible.")

with st.spinner("Checking sports schedules, scores and available contracts..."):
    contracts, market_errors, series_count = markets_scan()

now = datetime.now(timezone.utc)
games_by_league = {}
for league in leagues:
    games_by_league[league] = []
    for day in sorted({(now + timedelta(days=i)).strftime("%Y%m%d") for i in (-1, 0, 1, 2)}):
        try:
            games_by_league[league].extend(scoreboard(league, day))
        except (requests.RequestException, ValueError, KeyError):
            pass

rows = []
for event in odds:
    if not now - timedelta(hours=8) <= event["start"] <= now + timedelta(days=2):
        continue
    game = matching_game(event, games_by_league.get(event["league"], []))
    state = game["state"] if game else ("pre" if event["start"] > now else "unknown")
    if state == "post":
        continue
    matched = match_contract(event, contracts)
    # Ranking is research relevance only; never label an unverified value as edge.
    score = (30 if state == "in" else 20 if event["start"] <= now + timedelta(hours=12) else 10)
    score += min(10, event["books"]) + (8 if matched else 0)
    rows.append({"event": event, "game": game, "state": state, "markets": matched, "rank": score})
rows.sort(key=lambda x: (-x["rank"], x["event"]["start"]))

if not rows:
    st.info("⏳ WAIT — No supported games with valid sportsbook benchmarks in the selected leagues and time window. Queen won't manufacture picks.")
else:
    for index, row in enumerate(rows[:5], 1):
        e, game, state = row["event"], row["game"], row["state"]
        status = "🔴 LIVE NOW" if state == "in" else "🟣 STARTING SOON" if e["start"] > now else "⚪ STATUS UNVERIFIED"
        with st.container(border=True):
            st.markdown(f"**👑 {index}. {e['away']} @ {e['home']}**")
            st.caption(f"{e['league']} · {status} · {local(e['start'])}")
            if game:
                details = []
                for team in game["teams"]:
                    rank = f"#{team['rank']} " if team['rank'] else ""
                    record = f" ({team['record']})" if team['record'] else " (record unavailable)"
                    details.append(f"{rank}{team['name']}{record}" + (f" — {team['score']}" if state == "in" else ""))
                st.write("  |  ".join(details))
                if state == "in":
                    st.caption(game["detail"])
            else:
                st.caption("ESPN matchup, records or rankings not confirmed; unavailable rather than guessed.")
            st.caption("Pregame sportsbook consensus (NOT synchronized live odds): " + " · ".join(
                f"{team} {prob:.1%}" for team, prob in e["probs"].items()) + f" · {e['books']} books")
            if row["markets"]:
                for m in row["markets"]:
                    st.markdown(f"**{m['platform']}** · {m['title']} · YES {fmt(m['yes'])} / NO {fmt(m['no'])}")
                    st.caption(m["quote_kind"] + " · Contract outcome and settlement rules not independently verified")
                    if m["url"]:
                        st.link_button("View market", m["url"], key=f"top_{index}_{m['platform']}_{m['id']}")
            else:
                st.caption("Kalshi/Polymarket: No confidently matched contract in sampled markets; price unavailable.")
            reason = ("LIVE ODDS UNAVAILABLE — pregame consensus cannot price an in-play contract." if state == "in" else
                      "Game status unconfirmed; live pricing cannot be assessed." if state == "unknown" else
                      "Exact contract outcome, executable ask, fees and depth are not yet verified.")
            st.markdown("**⏳ WAIT** — Queen's Reason: " + reason)

st.markdown("#### 💜 Royal Paper Bankroll")
try:
    with paper_db() as con:
        account = paper_summary(con)
    paper_strip(account)
except (sqlite3.Error, OSError) as exc:
    st.warning("Paper ledger unavailable: " + str(exc)[:120])

st.markdown("#### 📡 Queen's Live Activity")
st.info("RESEARCH / WAIT — No automatic paper BUY, HOLD or CASH OUT until exact contracts, executable prices, fees, liquidity and appropriately timed probabilities are verified.")

st.markdown("#### 👑 Royal Paper Parlays")
st.caption("2-, 3- and 5-leg combinations require distinct, independently qualified picks; no combinations are fabricated.")
st.info("⏳ WAIT — No qualified legs. No paper parlays created.")

with st.expander("🔴 Live Sports Scoreboard", expanded=False):
    for league in leagues:
        st.markdown(f"**{league}**")
        unique = {}
        for game in games_by_league.get(league, []):
            if game["start"]:
                unique[(game["start"], tuple(t["name"] for t in game["teams"]))] = game
        live = [g for g in unique.values() if g["state"] == "in"]
        if live:
            st.dataframe([{"Matchup": " vs ".join(t["name"] for t in g["teams"]),
                           "Score": " – ".join(str(t["score"]) for t in g["teams"]), "Status": g["detail"]}
                          for g in live], hide_index=True, use_container_width=True)
        else:
            st.caption("No confirmed live games returned.")

with st.expander("🔎 Sports Market Scanner & Data Details", expanded=False):
    st.caption(f"Sampled {len(contracts)} markets from up to 55 of {series_count} Kalshi sports series and selected Polymarket global tags. Not exhaustive.")
    st.caption("Polymarket global and Polymarket US may differ. Indicative prices are not guaranteed executable quotes.")
    search = st.text_input("Search team or market")
    platform = st.selectbox("Platform", ["Both", "Kalshi", "Polymarket"])
    filtered = [m for m in contracts if (platform == "Both" or m["platform"] == platform)
                and norm(search) in norm(m["title"] + " " + m["context"])]
    for m in filtered[:15]:
        st.markdown(f"**{m['platform']} — {m['title']}** · YES {fmt(m['yes'])} / NO {fmt(m['no'])}")
        st.caption(m["quote_kind"])
        if m["url"]:
            st.markdown(f"[Open market]({m['url']})")
    st.caption(f"Showing {min(15, len(filtered))} of {len(filtered)} matching sampled markets.")
    if quota is not None:
        st.caption(f"Odds API reported remaining requests: {quota}")
    for error in odds_errors + market_errors:
        st.caption("Data source: " + error)

with st.expander("📊 Royal Tracking Center — Paper Trades & History", expanded=False):
    try:
        with paper_db() as con:
            positions = [dict(x) for x in con.execute("SELECT opened_at,platform,title,side,grade,contracts,entry_price,status,realized_pnl FROM paper_trades ORDER BY opened_at DESC LIMIT 100")]
            activity = [dict(x) for x in con.execute("SELECT created_at,action,details FROM paper_activity ORDER BY event_id DESC LIMIT 100")]
        if positions:
            st.dataframe(positions, use_container_width=True, hide_index=True)
        else:
            st.caption("No verified paper trades yet.")
        if activity:
            st.dataframe(activity, use_container_width=True, hide_index=True)
        else:
            st.caption("No paper activity yet.")
    except (sqlite3.Error, OSError) as exc:
        st.warning("Tracking unavailable: " + str(exc)[:120])
    st.caption("Streamlit Community Cloud local SQLite storage is not durable; page refreshes are not 24/7 background monitoring.")

st.caption("QUEEN'S EDGE | 254 · ROYAL SPORTS SNIPER V1.7 · PAPER ONLY · NO REAL ORDERS")
