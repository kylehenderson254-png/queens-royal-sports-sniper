import os
import re
import json
import sqlite3
import unicodedata
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlparse
from html import escape

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
    response = requests.get(url, params=params, headers={"Accept": "application/json", "User-Agent": "RoyalSportsSniper/2.7"}, timeout=15)
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


@st.cache_data(ttl=45, show_spinner=False)
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
            logos = team.get("logos") or []
            team_logo = team.get("logo") or (logos[0].get("href") if logos and isinstance(logos[0], dict) else None)
            teams.append({"name": team.get("displayName", ""), "short": team.get("shortDisplayName", ""),
                          "record": record, "homeAway": c.get("homeAway"), "rank": rank if isinstance(rank, int) and 1 <= rank <= 25 else None,
                          "score": c.get("score", "–"), "logo": team_logo})
        if len(teams) != 2:
            continue
        status = event.get("status", {}).get("type", {})
        league_info = (data.get("leagues") or [{}])[0]
        league_logos = league_info.get("logos") or []
        league_logo = league_logos[0].get("href") if league_logos and isinstance(league_logos[0], dict) else None
        output.append({"id": str(event.get("id") or ""), "start": when(event.get("date")), "teams": teams, "league_logo": league_logo,
                       "state": status.get("state", ""), "detail": status.get("shortDetail") or status.get("description", "")})
    return output


def safe_logo(url):
    """Only display ESPN-hosted HTTPS logo URLs supplied by ESPN game data."""
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    return url if parsed.scheme == "https" and (
        parsed.hostname == "espncdn.com" or (parsed.hostname or "").endswith(".espncdn.com")
    ) else None


def compact_team(team, live=False):
    logo = safe_logo(team.get("logo"))
    with st.container():
        a, b = st.columns([1, 7], vertical_alignment="center", gap="small")
        with a:
            if logo:
                st.image(logo, width=30)
            else:
                st.caption("🏟️")
        with b:
            rank = f"#{team['rank']} " if team.get("rank") else ""
            record = f" · {team['record']}" if team.get("record") else ""
            score = f" · {team['score']}" if live else ""
            st.markdown(f"**{escape(rank + team['name'])}**{escape(record + score)}")


def team_match(a, b):
    """Require distinctive full/short names, never mascot-only college matches."""
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    a_parts, b_parts = a.split(), b.split()
    # ESPN sometimes includes or omits the school/city prefix.
    if len(a_parts) >= 2 and len(b_parts) >= 2:
        return (a in b or b in a) and min(len(a), len(b)) >= 8
    return False


def matching_game(event, games):
    """Unique ESPN game with both participants and start within 90 minutes."""
    matches = []
    for game in games:
        if not game.get("start") or not event.get("start"):
            continue
        if abs((game["start"] - event["start"]).total_seconds()) > 5400:
            continue
        home, away = event["home"], event["away"]
        teams = game["teams"]
        if (any(team_match(home, x) for x in (teams[0]["name"], teams[0]["short"])) and
            any(team_match(away, x) for x in (teams[1]["name"], teams[1]["short"]))) or ( 
            any(team_match(home, x) for x in (teams[1]["name"], teams[1]["short"])) and
            any(team_match(away, x) for x in (teams[0]["name"], teams[0]["short"]))):
            matches.append(game)
    unique = {g.get("id") or str(g["start"]): g for g in matches}
    return next(iter(unique.values())) if len(unique) == 1 else None


@st.cache_data(ttl=180, show_spinner=False)
def kalshi_scan():
    """Kalshi-only, paginated sports discovery with bounded public API requests."""
    results, seen = [], set()
    series, _ = get_json(f"{KALSHI}/series", {"category": "Sports"})
    candidates = [x for x in series.get("series", []) if str(x.get("category", "")).lower() == "sports"]
    # Prioritize leagues in our sportsbook coverage, not alphabetical unrelated sports.
    terms = ("nba", "nfl", "ncaab", "ncaaf", "basketball", "football", "college")
    candidates.sort(key=lambda x: (0 if any(t in norm(str(x.get("ticker", "")) + " " + str(x.get("title", ""))) for t in terms) else 1, str(x.get("ticker", ""))))
    checked = 0
    for series_info in candidates[:75]:
        ticker = str(series_info.get("ticker") or "")
        if not ticker:
            continue
        checked += 1
        cursor = None
        for page in range(3):
            params = {"series_ticker": ticker, "status": "open", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            try:
                data, _ = get_json(f"{KALSHI}/markets", params)
            except (requests.RequestException, ValueError, TypeError):
                break
            for m in data.get("markets", []):
                mid = str(m.get("ticker") or "")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                # YES and NO are prices for the exact outcome described by the market.
                title = str(m.get("title") or "")
                subtitle = str(m.get("subtitle") or "")
                outcome = str(m.get("yes_sub_title") or "")
                context = " ".join([str(series_info.get("title") or ""), ticker, title,
                                    subtitle, outcome, str(m.get("no_sub_title") or "")])
                results.append({"platform": "Kalshi", "id": mid, "title": title or subtitle,
                                "context": context, "outcome": outcome,
                                "yes": price(m.get("yes_ask_dollars")) or price(m.get("yes_ask"), True),
                                "no": price(m.get("no_ask_dollars")) or price(m.get("no_ask"), True),
                                "volume": float(m.get("volume") or 0),
                                "end": m.get("close_time"), "event_ticker": m.get("event_ticker"),
                                 "series_ticker": ticker, "subtitle": subtitle,
                                 "yes_sub_title": outcome, "no_sub_title": str(m.get("no_sub_title") or ""),
                                "url": "https://kalshi.com/markets/" + quote(ticker.lower()),
                                "quote_kind": "Public Kalshi ask snapshot; depth, fees and fill not guaranteed"})
            cursor = data.get("cursor")
            if not cursor:
                break
    return results, checked


@st.cache_data(ttl=180, show_spinner=False)
def polymarket_scan():
    """Bounded Gamma sports discovery; quotes are indicative, not executable asks."""
    found, seen, errors = [], set(), []
    for offset in range(0, 1000, 100):
        try:
            events, _ = get_json(f"{POLY}/events", {"active": "true", "closed": "false", "limit": 100, "offset": offset})
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append("Polymarket: " + str(exc)[:100])
            break
        if not isinstance(events, list) or not events:
            break
        for ev in events:
            title = str(ev.get("title") or "")
            slug = str(ev.get("slug") or "")
            context = " ".join([title, slug, str(ev.get("description") or "")[:250]])
            if not any(word in norm(context).split() for word in ("nba", "nfl", "ncaab", "ncaaf", "basketball", "football")):
                continue
            for m in ev.get("markets") or []:
                mid = str(m.get("id") or "")
                if not mid or mid in seen or m.get("closed") or not m.get("active", True):
                    continue
                seen.add(mid)
                try:
                    outcomes = m.get("outcomes") or []
                    values = m.get("outcomePrices") or []
                    if isinstance(outcomes, str): outcomes = json.loads(outcomes)
                    if isinstance(values, str): values = json.loads(values)
                    if len(outcomes) != 2 or len(values) != 2: continue
                    for ix, outcome in enumerate(outcomes):
                        if norm(outcome) in ("draw", "tie"): continue
                        found.append({"platform":"Polymarket", "id":mid+":"+str(ix),
                            "title":str(m.get("question") or title), "context":context+" "+str(m.get("question") or ""),
                            "outcome":str(outcome), "yes_sub_title":str(outcome), "no_sub_title":"",
                            "yes":price(values[ix]), "no":None, "volume":float(m.get("volumeNum") or 0),
                            "end":ev.get("startDate") or ev.get("endDate"), "event_ticker":slug,
                            "series_ticker":"", "subtitle":title,
                            "url":"https://polymarket.com/event/"+quote(slug),
                            "quote_kind":"Indicative last/traded outcome price; NOT a verified executable ask"})
                except (ValueError, TypeError, IndexError):
                    continue
        if len(events) < 100: break
    return found, errors


@st.cache_data(ttl=240, show_spinner=False)
def kalshi_event_details(event_ticker):
    """Look up event metadata only for a plausible candidate, not every scanned market."""
    if not event_ticker:
        return {}
    try:
        payload, _ = get_json(f"{KALSHI}/events/{quote(str(event_ticker), safe='')}")
        return payload.get("event") or {}
    except (requests.RequestException, ValueError, TypeError):
        return {}


def team_side(event, m):
    """Outcome detection uses an explicit YES label; never infer YES from NO pricing."""
    label = str(m.get("yes_sub_title") or m.get("outcome") or "")
    home, away = event["home"], event["away"]
    a, b = _team_in_text(home, label), _team_in_text(away, label)
    return (home if a else away) if a != b else None


def research_gap(event, market):
    """A price-versus-consensus comparison, NOT a verified statistical edge."""
    selection = market.get("verified_yes_team")
    benchmark = next((p for t, p in event.get("probs", {}).items() if team_match(t, selection)), None)
    ask = market.get("yes") if market.get("platform") == "Kalshi" else None
    if benchmark is None or ask is None:
        return None
    return {"benchmark": benchmark, "ask": ask, "gap": benchmark - ask}


@st.cache_data(ttl=180, show_spinner=False)
def markets_scan():
    results, errors, count = [], [], 0
    try:
        results, count = kalshi_scan()
    except (requests.RequestException, ValueError, TypeError) as exc:
        errors.append("Kalshi: " + str(exc)[:140])
    poly, poly_errors = polymarket_scan()
    return results + poly, errors + poly_errors, count


def team_aliases(team):
    """Full name and distinctive last token; aliases are for discovery, not trade verification."""
    full = norm(team)
    parts = full.split()
    aliases = {full}
    if len(parts) >= 2 and len(parts[-1]) >= 4:
        aliases.add(parts[-1])
    return aliases


# Game matching deliberately fails closed when a market's game identity is ambiguous.
# Team outcome alone is not enough to verify a contract belongs to this matchup.
LEAGUE_HINTS = {
    "NBA": ("nba", "pro basketball"),
    "NFL": ("nfl", "pro football"),
    "NCAAB": ("ncaab", "college basketball", "mens college basketball"),
    "NCAAF": ("ncaaf", "college football"),
}


def _team_in_text(team, content):
    words = set(norm(content).split())
    full = norm(team)
    if not full:
        return False
    if " " + full + " " in " " + norm(content) + " ":
        return True
    # A mascot alone is not a safe college identifier (e.g. Tigers, Bulldogs).
    if len(full.split()) < 2:
        return False
    mascot = full.split()[-1]
    return len(mascot) >= 5 and mascot in words


def _league_verified(league, market):
    series = norm(market.get("series_ticker", ""))
    context = norm(market.get("context", ""))
    hints = LEAGUE_HINTS.get(league, ())
    # Require an explicit league marker; 'basketball' alone is not NBA.
    return any((" " + h + " ") in (" " + series + " " ) or (" " + h + " ") in (" " + context + " ")
               for h in hints)


def _date_verified(event, market):
    # Close time can be later than game start; it is not a verified event date.
    # Require a recognizable date in the event/market identifier or text.
    start = event.get("start")
    if not start:
        return False
    blob = " ".join(str(market.get(k) or "") for k in
                    ("event_ticker", "id", "title", "subtitle", "context"))
    compact = re.sub(r"[^a-zA-Z0-9]", "", blob).upper()
    for dt in (start.astimezone(CENTRAL), start.astimezone(timezone.utc)):
        forms = (dt.strftime("%y%b%d").upper(), dt.strftime("%b%d%y").upper(),
                 dt.strftime("%Y%m%d"), dt.strftime("%y%m%d"))
        if any(form in compact for form in forms):
            return True
    return False


def match_contract(event, contracts):
    """Return only verified, unambiguous game-winner contracts for this event."""
    home, away = event["home"], event["away"]
    league = event["league"]
    verified = []
    for m in contracts:
        if not _league_verified(league, m) or not _date_verified(event, m):
            continue
        title = " ".join(str(m.get(k) or "") for k in
                         ("title", "subtitle", "context"))
        outcome = " ".join(str(m.get(k) or "") for k in
                           ("yes_sub_title", "outcome"))
        # Require both teams in game metadata, not just a one-team market title.
        if not (_team_in_text(home, title) and _team_in_text(away, title)):
            continue
        yes_home = _team_in_text(home, outcome)
        yes_away = _team_in_text(away, outcome)
        if yes_home == yes_away:
            continue
        end = when(m.get("end"))
        if end and end < event["start"] - timedelta(hours=1):
            continue
        item = dict(m)
        item["verified_yes_team"] = home if yes_home else away
        item["match_status"] = "League, matchup, event date and YES outcome matched; settlement rules unverified"
        verified.append(item)
    # A market may use a short team name, with the full matchup in its event metadata.
    # Keep the strict original verification as the only automatic verified pathway.
    # Never silently choose among duplicate contracts for the same YES outcome.
    by_team = {}
    for m in verified:
        by_team.setdefault(norm(m["verified_yes_team"]), []).append(m)
    return [group[0] for group in by_team.values() if len(group) == 1]


def price_tiles(m):
    """Compact price chips instead of full-width columns."""
    st.markdown(
        '<div style="display:flex;gap:9px;flex-wrap:wrap;margin:6px 0 8px">'
        '<div style="display:inline-flex;align-items:center;gap:8px;background:#103d2c;'
        'border:1px solid #258e61;border-radius:10px;padding:7px 11px">'
        '<span style="color:#8ff3b7;font-size:12px;font-weight:700">YES</span>'
        f'<strong style="color:white;font-size:18px">{escape(fmt(m.get("yes")))}</strong></div>'
        '<div style="display:inline-flex;align-items:center;gap:8px;background:#4b1b2a;'
        'border:1px solid #a8455c;border-radius:10px;padding:7px 11px">'
        '<span style="color:#ffadb9;font-size:12px;font-weight:700">NO</span>'
        f'<strong style="color:white;font-size:18px">{escape(fmt(m.get("no")))}</strong></div></div>',
        unsafe_allow_html=True)


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



def parlay_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS royal_parlays (
      parlay_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, legs INTEGER NOT NULL,
      stake REAL NOT NULL, decimal_odds REAL NOT NULL, status TEXT NOT NULL,
      settled_at TEXT, payout REAL, pnl REAL, note TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS royal_parlay_legs (
      parlay_id TEXT NOT NULL, leg_index INTEGER NOT NULL, league TEXT NOT NULL,
      home TEXT NOT NULL, away TEXT NOT NULL, start TEXT NOT NULL,
      selection TEXT NOT NULL, decimal_odds REAL NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
      PRIMARY KEY(parlay_id,leg_index))""")
    con.commit()


def settle_parlays(con, games_by_league):
    """Only settle when a single ESPN final matchup matches both teams and time."""
    parlay_tables(con)
    pending = con.execute("SELECT * FROM royal_parlay_legs WHERE status='PENDING'").fetchall()
    for leg in pending:
        event = {"home": leg["home"], "away": leg["away"], "start": when(leg["start"])}
        game = matching_game(event, games_by_league.get(leg["league"], []))
        if not game or game["state"] != "post":
            continue
        scores = {}
        try:
            scores = {norm(t["name"]): float(t["score"]) for t in game["teams"]}
            selected = norm(leg["selection"])
            if selected not in scores or len(scores) != 2:
                continue
            high = max(scores.values())
            outcome = "VOID" if len(set(scores.values())) == 1 else ("WON" if scores[selected] == high else "LOST")
            con.execute("UPDATE royal_parlay_legs SET status=? WHERE parlay_id=? AND leg_index=?",
                        (outcome, leg["parlay_id"], leg["leg_index"]))
        except (TypeError, ValueError):
            continue
    for p in con.execute("SELECT * FROM royal_parlays WHERE status='OPEN'").fetchall():
        statuses = [r["status"] for r in con.execute(
            "SELECT status FROM royal_parlay_legs WHERE parlay_id=?", (p["parlay_id"],)).fetchall()]
        if not statuses or "PENDING" in statuses:
            continue
        if "LOST" in statuses:
            payout = 0.0
        else:
            factors = [r["decimal_odds"] for r in con.execute(
                "SELECT decimal_odds FROM royal_parlay_legs WHERE parlay_id=? AND status='WON'",
                (p["parlay_id"],)).fetchall()]
            payout = float(p["stake"])
            for f in factors:
                payout *= float(f)
        pnl = payout - float(p["stake"])
        con.execute("""UPDATE royal_parlays SET status='SETTLED',settled_at=?,payout=?,pnl=?
                       WHERE parlay_id=?""",
                    (datetime.now(timezone.utc).isoformat(), payout, pnl, p["parlay_id"]))
    con.commit()


def parlay_stats(con):
    parlay_tables(con)
    row = con.execute("""SELECT
      COALESCE(SUM(CASE WHEN status='OPEN' THEN stake ELSE 0 END),0) locked,
      COALESCE(SUM(CASE WHEN status='SETTLED' THEN pnl ELSE 0 END),0) pnl,
      SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) opened
      FROM royal_parlays""").fetchone()
    return {"locked": float(row["locked"]), "pnl": float(row["pnl"]),
            "open": int(row["opened"] or 0)}


def paper_parlay_candidates(rows, now):
    """Research-only pregame legs from consensus; NOT platform parlay quotes."""
    options = []
    for row in rows:
        event = row["event"]
        if row["state"] != "pre" or event["start"] <= now:
            continue
        # We require at least 3 bookmaker observations; still not independent prediction.
        if event["books"] < 3:
            continue
        team, probability = max(event["probs"].items(), key=lambda x: x[1])
        if not 0.52 <= probability <= 0.85:
            continue
        # Conservative display-only proxy; no actual sportsbook/parlay price.
        options.append({"event": event, "team": team, "p": probability,
                        "decimal": 1 / probability,
                        "key": f"{event['league']}:{event['start'].isoformat()}:{norm(event['home'])}"})
    options.sort(key=lambda x: (-x["p"], x["event"]["start"]))
    return options

def paper_summary(con):
    row = con.execute("""SELECT COALESCE(SUM(CASE WHEN status='OPEN' THEN contracts*entry_price+entry_fee ELSE 0 END),0) locked,
        COALESCE(SUM(CASE WHEN status='CLOSED' THEN realized_pnl ELSE 0 END),0) pnl,
        SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) opened FROM paper_trades""").fetchone()
    parlays = parlay_stats(con)
    return {"cash": STARTING_BANKROLL + float(row["pnl"]) + parlays["pnl"] - float(row["locked"]) - parlays["locked"],
            "pnl": float(row["pnl"]) + parlays["pnl"],
            "open": (row["opened"] or 0) + parlays["open"]}


def paper_strip(account):
    from html import escape
    fields = [("START", f"${STARTING_BANKROLL:,.0f}"), ("AVAILABLE", f"${account['cash']:,.2f}"),
              ("OPEN", str(account["open"])), ("REALIZED P/L", f"${account['pnl']:+,.2f}")]
    html = '<div class="royal-strip">' + "".join(
        f"<div><small>{escape(label)}</small><strong>{escape(value)}</strong></div>" for label, value in fields) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


def queen_call(row):
    """Separate team lean from trade: sportsbook consensus is a benchmark, not proof of edge."""
    e = row["event"]
    if not e.get("probs"):
        return None, None, "WAIT", "No matched sportsbook benchmark to assess this game."
    team, prob = max(e["probs"].items(), key=lambda pair: pair[1])
    if row["state"] != "pre":
        return team, prob, "WAIT", "Live or uncertain game state; pregame consensus cannot support an in-play trade."
    options = []
    for m in row["markets"]:
        if norm(m["verified_yes_team"]) != norm(team) or m.get("yes") is None:
            continue
        # A Polymarket last/traded price is not an executable ask; never issue BUY from it.
        if m["platform"] != "Kalshi": continue
        gap = prob - m["yes"]
        options.append((gap, m))
    if not options:
        return team, prob, "WAIT", "No verified Kalshi YES ask for Queen's favored team; Polymarket reference prices are not executable asks."
    gap, best = max(options, key=lambda item: item[0])
    if e.get("books", 0) < 3:
        return team, prob, "WAIT", "Too few sportsbook observations to compare prices responsibly."
    if gap >= 0.10:
        return team, prob, "WATCH", f"Indicative {gap:.1%} benchmark-versus-ask gap on Kalshi; fees, liquidity, settlement and independent edge are unverified."
    return team, prob, "WAIT", f"Benchmark-versus-ask gap {gap:.1%}; no independently established value after costs."



banner = Path("royal_sports_banner.png")
if banner.exists():
    st.image(str(banner), use_container_width=True)
else:
    st.title("👑 QUEEN'S EDGE | 254 — ROYAL SPORTS SNIPER")
st.caption("V2.7 · ESPN-first Top 5 · Kalshi + Polymarket research · PAPER ONLY")
st.markdown("### 👑 Today's Royal Top 5")
st.caption("Team lean and contract decision are separate. LIVE games remain visible even when markets are unavailable.")

if st.button("🔄 Refresh sports and markets"):
    scoreboard.clear()
    markets_scan.clear()
    kalshi_scan.clear()
    polymarket_scan.clear()
    st.rerun()

key = odds_key()
# Limit free quota: user selects leagues; Odds API cache is 15 minutes and refresh button does not clear it.
with st.expander("⚙️ League coverage & free API usage", expanded=False):
    leagues = st.multiselect("Sportsbook leagues to check", list(LEAGUES), default=["NBA", "NFL", "NCAAB", "NCAAF"],
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

try:
    with paper_db() as con:
        settle_parlays(con, games_by_league)
except (sqlite3.Error, OSError):
    pass

# ESPN-first display: live games remain visible even if the odds feed removes them.
rows = []
seen_games = set()
for league in leagues:
    for game in games_by_league.get(league, []):
        start = game.get("start")
        if not start or not now - timedelta(hours=8) <= start <= now + timedelta(days=2):
            continue
        if game.get("state") == "post":
            continue
        gid = (league, game.get("id") or (start.isoformat(), tuple(t["name"] for t in game["teams"])))
        if gid in seen_games:
            continue
        seen_games.add(gid)
        home_team = next((t for t in game["teams"] if t.get("homeAway") == "home"), game["teams"][0])
        away_team = next((t for t in game["teams"] if t.get("homeAway") == "away"), game["teams"][1])
        event = {"league": league, "home": home_team["name"], "away": away_team["name"],
                 "start": start, "books": 0, "probs": {}}
        odds_matches = [o for o in odds if o["league"] == league and matching_game(o, [game])]
        if len(odds_matches) == 1:
            event["books"] = odds_matches[0]["books"]
            event["probs"] = odds_matches[0]["probs"]
        matched = match_contract(event, contracts)
        state = game.get("state") or "unknown"
        rank = (100 if state == "in" else 60 if start <= now + timedelta(hours=12) else 30)
        rank += min(10, event["books"]) + (8 if matched else 0)
        rows.append({"event": event, "game": game, "state": state, "markets": matched, "rank": rank})
# If ESPN fails completely for a league, retain independently sourced pregame odds.
for event in odds:
    if not now < event["start"] <= now + timedelta(days=2):
        continue
    if matching_game(event, games_by_league.get(event["league"], [])):
        continue
    if any(r["event"]["league"] == event["league"] and
           abs((r["event"]["start"] - event["start"]).total_seconds()) < 5400 and
           team_match(r["event"]["home"], event["home"]) and
           team_match(r["event"]["away"], event["away"]) for r in rows):
        continue
    rows.append({"event": event, "game": None, "state": "pre",
                 "markets": match_contract(event, contracts), "rank": 20 + min(10, event["books"])})
rows.sort(key=lambda x: (-x["rank"], x["event"]["start"]))
verified_games = sum(bool(r["markets"]) for r in rows)
verified_k = sum(any(m["platform"] == "Kalshi" for m in r["markets"]) for r in rows)
verified_p = sum(any(m["platform"] == "Polymarket" for m in r["markets"]) for r in rows)
st.caption(f"👑 Contract matching: {verified_games} of {len(rows)} upcoming/live games with a strictly matched sampled contract "
           f"· K {verified_k} · P {verified_p}. A missing match means unverified, not necessarily unlisted.")

if not rows:
    st.info("⏳ WAIT — No upcoming or live games returned from ESPN or the available sportsbook feed. Queen won't manufacture picks.")
else:
    for index, row in enumerate(rows[:5], 1):
        e, game, state = row["event"], row["game"], row["state"]
        status = "🔴 LIVE NOW" if state == "in" else "🟣 STARTING SOON" if e["start"] > now else "⚪ STATUS UNVERIFIED"
        with st.container(border=True):
            st.markdown(f"**👑 {index}. {escape(e['away'])} @ {escape(e['home'])}**")
            league_logo = safe_logo(game.get("league_logo")) if game else None
            if league_logo:
                logo_col, info_col = st.columns([1, 16], vertical_alignment="center", gap="small")
                with logo_col:
                    st.image(league_logo, width=24)
                with info_col:
                    st.caption(f"{e['league']} · {status} · {local(e['start'])}")
            else:
                st.caption(f"{e['league']} · {status} · {local(e['start'])}")
            if game:
                left, right = st.columns(2, gap="small")
                with left:
                    compact_team(game["teams"][0], live=state == "in")
                with right:
                    compact_team(game["teams"][1], live=state == "in")
                if state == "in":
                    st.caption(game["detail"])
            else:
                st.caption("ESPN matchup, records or rankings not confirmed; unavailable rather than guessed.")
            if e["probs"]:
                st.caption("Sportsbook pregame consensus (NOT synchronized in-play odds): " + " · ".join(
                    f"{team} {prob:.1%}" for team, prob in e["probs"].items()) + f" · {e['books']} books")
            else:
                st.caption("Sportsbook consensus unavailable for this matchup; game and scores sourced from ESPN.")
            if row["markets"]:
                for m in row["markets"]:
                    tag = "🟣 K" if m["platform"] == "Kalshi" else "🔵 P"
                    st.markdown(f"**{tag} · {escape(m['verified_yes_team'])}** · {escape(m['title'])}")
                    price_tiles(m)
                    gap = research_gap(e, m)
                    if gap:
                        direction = "below" if gap["gap"] > 0 else "above"
                        st.caption(f"Research comparison: {gap['ask']:.1%} Kalshi YES ask vs "
                                   f"{gap['benchmark']:.1%} pregame sportsbook benchmark "
                                   f"({abs(gap['gap']):.1%} {direction}). NOT an independently validated edge.")
                    st.caption(m["quote_kind"] + " · YES outcome: " + m["verified_yes_team"] + ". " + m["match_status"])
                    if m["url"]:
                        st.link_button("View market", m["url"], key=f"top_{index}_{m['platform']}_{m['id']}")
            else:
                st.caption("⚪ K / P UNVERIFIED — No exact league + game + date + outcome match in the sampled markets. This does not prove the contract is unavailable.")
            favored, confidence, call, reason = queen_call(row)
            if favored:
                st.markdown(f"**👑 Queen favors: {escape(favored)}** · sportsbook benchmark {confidence:.1%}")
            st.markdown(f"**{'👀' if call == 'WATCH' else '⏳'} {call}** — Queen's Reason: " + reason)
            if row["markets"]:
                st.caption("K = Kalshi · P = Polymarket · Prices are platform-specific and may not be executable. No automatic trades.")


st.markdown("#### 💜 Royal Paper Bankroll")
try:
    with paper_db() as con:
        account = paper_summary(con)
    paper_strip(account)
except (sqlite3.Error, OSError) as exc:
    st.warning("Paper ledger unavailable: " + str(exc)[:120])

st.markdown("#### 📡 Queen's Live Activity")
st.info("K + P RESEARCH — Queen checks league, teams, event date and YES outcome before linking a contract. "
        "A sportsbook favorite alone is NOT a platform pick. BUY stays disabled until contract rules, fees, "
        "liquidity and an independent probability estimate are verified.")

st.markdown("#### 👑 Royal Paper Parlays")
st.caption("Research simulation only. These are NOT verified BUY signals, actual sportsbook odds, or executable Kalshi parlays.")
leg_count = st.radio("Choose parlay legs", [2, 3, 5, 10], index=0,
                     horizontal=True, key="royal_legs", help="Queen automatically selects the strongest eligible pregame favorites.")
stake = st.radio("Choose paper stake ($)", [5, 10, 20], index=1,
                 horizontal=True, key="royal_stake")
st.caption("👑 Queen automatically selects teams; no manual picking required.")
# Use the full eligible pregame slate, not just the five featured games.
parlay_rows = []
for o in odds:
    if not (now + timedelta(minutes=5) < o["start"] <= now + timedelta(days=2)):
        continue
    g = matching_game(o, games_by_league.get(o["league"], []))
    if not g or g.get("state") != "pre":
        continue
    if not g.get("start") or abs((g["start"] - o["start"]).total_seconds()) > 5400:
        continue
    parlay_rows.append({"event": o, "state": "pre"})
candidates = paper_parlay_candidates(parlay_rows, now)
selected = []
used = set()
for item in candidates:
    if item["key"] in used:
        continue
    selected.append(item)
    used.add(item["key"])
    if len(selected) == leg_count:
        break
# Conservative auto-size: require at least 60% benchmark and >= 4 books per leg.
selected = [x for x in selected if x["p"] >= 0.60 and x["event"]["books"] >= 4]
if len(selected) >= 2:
    selected = selected[:min(leg_count, 3)]
    st.caption(f"👑 Queen recommends {len(selected)} research legs (requested maximum {leg_count}); fewer legs limit compounded uncertainty.")
    st.caption("⚪ These are sportsbook-consensus research selections, not confirmed K/P parlay legs. "
               "The app cannot claim they can be combined on either platform.")
if len(selected) < 2:
    st.info(f"⏳ Only {len(selected)} sufficiently supported research legs found. Queen recommends WAIT rather than forcing a parlay.")
else:
    combined_probability = 1.0
    for item in selected:
        combined_probability *= item["p"]
        parlay_game = matching_game(item["event"], games_by_league.get(item["event"]["league"], []))
        if parlay_game:
            team_data = next((t for t in parlay_game["teams"] if norm(t["name"]) == norm(item["team"])), None)
            logo = safe_logo(team_data.get("logo")) if team_data else None
        else:
            logo = None
        if logo:
            logo_col, label_col = st.columns([1, 15], vertical_alignment="center", gap="small")
            with logo_col:
                st.image(logo, width=25)
            with label_col:
                st.markdown(f"**{escape(item['team'])}** · {escape(item['event']['away'])} @ "
                            f"{escape(item['event']['home'])} · {item['event']['league']}")
        else:
            st.markdown(f"**{escape(item['team'])}** · {escape(item['event']['away'])} @ "
                        f"{escape(item['event']['home'])} · {item['event']['league']}")

    example_odds = 1 / combined_probability
    st.caption(f"Illustrative independent-leg estimate: {combined_probability:.1%} all win · "
               f"fair-price proxy {example_odds:.2f}x · illustrative gross return ${stake*example_odds:.2f}. "
               "Not a real parlay quote; correlated legs can invalidate the probability estimate.")
    st.caption("⚪ Platform: NOT VERIFIED · No executable multi-leg quote. Do not treat the illustrative return as a payout offer.")
    st.caption("Queen's Reason: Automatically selected from the strongest available pregame sportsbook consensus favorites "
               "in the selected leagues, not independently verified value opportunities.")
    if st.button("📝 Enter hypothetical $%s paper parlay" % stake, key="paper_parlay_enter"):
        import hashlib
        stamp = datetime.now(timezone.utc).isoformat()
        identity = "|".join(item["key"] + ":" + norm(item["team"]) for item in selected)
        parlay_id = hashlib.sha256(identity.encode()).hexdigest()[:20]
        try:
            with paper_db() as con:
                parlay_tables(con)
                existing = con.execute("SELECT parlay_id FROM royal_parlays WHERE parlay_id=?", (parlay_id,)).fetchone()
                if existing:
                    st.warning("This same selection was already recorded. No duplicate paper entry.")
                else:
                    account_now = paper_summary(con)
                    if account_now["cash"] < stake:
                        st.warning("Insufficient available paper cash.")
                    else:
                        con.execute("""INSERT INTO royal_parlays
                           (parlay_id,created_at,legs,stake,decimal_odds,status,note)
                           VALUES (?,?,?,?,?,'OPEN',?)""",
                           (parlay_id, stamp, len(selected), float(stake), example_odds,
                            "HYPOTHETICAL fair-odds proxy, not an executable parlay"))
                        for i, item in enumerate(selected):
                            e = item["event"]
                            con.execute("""INSERT INTO royal_parlay_legs
                              (parlay_id,leg_index,league,home,away,start,selection,decimal_odds)
                              VALUES (?,?,?,?,?,?,?,?)""",
                              (parlay_id,i,e["league"],e["home"],e["away"],
                               e["start"].isoformat(),item["team"],item["decimal"]))
                        con.commit()
                        st.success("Hypothetical parlay recorded for paper tracking. Not a real trade.")
                        st.rerun()
        except (sqlite3.Error, OSError) as exc:
            st.error("Paper ledger unavailable: " + str(exc)[:120])

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

def research_market_type(m):
    """Text classification for discovery only; never verifies settlement or a bet."""
    text = norm(" ".join(str(m.get(k) or "") for k in ("title", "subtitle", "context")))
    if any(x in text for x in ("rebound", "assist", "touchdown", "passing yard", "rushing yard",
                               "receiving yard", "player point", "three pointer", "three pointers")):
        return "Player props"
    if any(x in text for x in ("point spread", "spread", "handicap", "win by")):
        return "Point spreads"
    if any(x in text for x in ("total points", "combined points", "over under", "total yards",
                               "game total", "points scored")):
        return "Game totals"
    return "Moneyline / other"

with st.expander("🔎 Sports Market Scanner & Data Details", expanded=False):
    st.markdown("**👑 Contract matching status**")
    st.caption(f"Upcoming/live games checked: {len(rows)} · Exact matches: {verified_games} · "
               f"Kalshi games: {verified_k} · Polymarket games: {verified_p}")
    if not verified_games:
        st.info("No contracts passed the strict league, matchup, date and YES-outcome checks. "
                "This can result from limited API pagination or missing event metadata, not necessarily absent markets.")
    st.caption(f"Scanned {sum(m['platform']=='Kalshi' for m in contracts)} Kalshi markets from {series_count} series and {sum(m['platform']=='Polymarket' for m in contracts)} Polymarket outcome references. Bounded, not exhaustive.")
    st.caption("Kalshi asks and Polymarket Gamma indicative outcome prices are different quote types; neither guarantees a fill. "
               "Gamma discovery may not represent Polymarket US availability.")
    st.caption("Research categories include player props, spreads and totals when found in the sampled markets. "
               "Text classification is not contract verification; no prop model or mixed-parlay pricing is active.")
    market_type = st.radio("Market research type",
                           ["All", "Moneyline / other", "Point spreads", "Game totals", "Player props"],
                           horizontal=True)
    search = st.text_input("Search team or market")
    platform = st.radio("Platform", ["All", "Kalshi", "Polymarket"], horizontal=True)
    filtered = [m for m in contracts if (platform == "All" or m["platform"] == platform)
                and (market_type == "All" or research_market_type(m) == market_type)
                and norm(search) in norm(m["title"] + " " + m["context"])]
    for m in filtered[:15]:
        st.markdown(f"**{escape(m['platform'])} — {escape(m['title'])}**")
        price_tiles(m)
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
        with paper_db() as con:
            parlay_tables(con)
            parlays = [dict(x) for x in con.execute(
            "SELECT created_at,legs,stake,decimal_odds,status,payout,pnl,note FROM royal_parlays ORDER BY created_at DESC LIMIT 100")]
            legs_history = [dict(x) for x in con.execute(
                "SELECT parlay_id,selection,league,start,status FROM royal_parlay_legs ORDER BY start DESC LIMIT 200")]
        st.markdown("**👑 Parlay history**")
        if parlays:
            st.dataframe(parlays, use_container_width=True, hide_index=True)
            with st.expander("Individual parlay legs", expanded=False):
                st.dataframe(legs_history, use_container_width=True, hide_index=True)
            settled = [x for x in parlays if x["status"] == "SETTLED"]
            today = datetime.now(CENTRAL).date()
            def totals_since(days):
                return sum(float(x["pnl"] or 0) for x in settled if
                           when(x["created_at"]).astimezone(CENTRAL).date() >= today - timedelta(days=days))
            st.caption(f"Paper parlay P&L by ENTRY date — today ${totals_since(0):+.2f} · "
                       f"7 days ${totals_since(6):+.2f} · 30 days ${totals_since(29):+.2f} · "
                       f"all time ${sum(float(x['pnl'] or 0) for x in settled):+.2f}")
        else:
            st.caption("No hypothetical parlays recorded yet.")
        st.markdown("**Individual paper trades**")
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

st.caption("QUEEN'S EDGE | 254 · ROYAL SPORTS SNIPER V2.7 · PAPER ONLY · NO REAL ORDERS")
