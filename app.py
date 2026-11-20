"""QUEEN'S EDGE | 254 — Royal Sports Sniper V2.8. Paper research only."""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import streamlit as st

st.set_page_config(page_title="Royal Sports Sniper | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
CT = ZoneInfo("America/Chicago")
DB = Path(os.getenv("ROYAL_DB_PATH", "royal_sports_history.db"))
LEAGUES = {"NBA": ("basketball_nba", "basketball/nba"), "NFL": ("americanfootball_nfl", "football/nfl"),
           "NCAAB": ("basketball_ncaab", "basketball/mens-college-basketball"),
           "NCAAF": ("americanfootball_ncaaf", "football/college-football")}
STAKE = 100.0

st.markdown("""<style>
.stApp{background:radial-gradient(ellipse at top,#2b1246 0%,#110a20 60%,#090611 100%);color:#f5efff}
.block-container{max-width:1260px;padding-top:1rem}h1,h2,h3{color:#efd6ff!important}
div[data-testid="stMetric"]{background:#211335;border:1px solid #68418d;border-radius:13px;padding:12px}
div[data-testid="stExpander"]{background:#1d1230;border:1px solid #56377a;border-radius:12px}
div.stButton>button{background:#782bb7;color:white;border:0;border-radius:11px;font-weight:700}
.royal-card{background:#1c102e;border:1px solid #66408b;border-radius:15px;padding:14px;margin:10px 0}
.royal-card strong{color:#f5d68d}.royal-small{color:#cdb8df;font-size:.85rem}
</style>""", unsafe_allow_html=True)


def now():
    return datetime.now(timezone.utc)


def dt(value):
    try:
        x = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def local(value):
    x = dt(value)
    return x.astimezone(CT).strftime("%a %b %d · %I:%M %p CT") if x else "Time unavailable"


def norm(value):
    import re
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower().replace("&", "and")))


def same(a, b):
    a, b = norm(a), norm(b)
    return bool(a and b and (a == b or (len(a) >= 9 and len(b) >= 9 and (a in b or b in a))))


def api_key():
    try:
        return st.secrets.get("ODDS_API_KEY", "") or os.getenv("ODDS_API_KEY", "")
    except (FileNotFoundError, KeyError):
        return os.getenv("ODDS_API_KEY", "")


def request_json(url, params=None):
    r = requests.get(url, params=params, timeout=16, headers={"User-Agent": "RoyalSportsSniper/2.8"})
    r.raise_for_status()
    return r.json(), r.headers


def decimal(american):
    try:
        x = float(american)
        return 1 + x / 100 if x > 0 else 1 + 100 / -x if x < 0 else None
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def odds_label(x):
    return f"{int(x):+d}" if x is not None else "N/A"


def profit(stake, odds):
    d = decimal(odds)
    return round(stake * (d - 1), 2) if d else None


@st.cache_data(ttl=900, show_spinner=False)
def get_odds(key, leagues):
    output, errors, remaining = [], [], None
    for league in leagues:
        try:
            events, headers = request_json(f"https://api.the-odds-api.com/v4/sports/{LEAGUES[league][0]}/odds/",
                {"apiKey": key, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"})
            remaining = headers.get("x-requests-remaining", remaining)
            for e in events:
                start = dt(e.get("commence_time"))
                if not start or not e.get("home_team") or not e.get("away_team"):
                    continue
                # One quoted bookmaker price per selection; never manufacture payout from consensus.
                quotes = {}
                for book in e.get("bookmakers", []):
                    for market in book.get("markets", []):
                        kind = market.get("key")
                        if kind not in ("h2h", "spreads", "totals"):
                            continue
                        for outcome in market.get("outcomes", []):
                            american = outcome.get("price")
                            if decimal(american) is None:
                                continue
                            point = outcome.get("point")
                            if kind in ("spreads", "totals") and point is None:
                                continue
                            team = outcome.get("name", "")
                            if kind in ("h2h", "spreads") and team not in (e["home_team"], e["away_team"]):
                                continue
                            if kind == "totals" and team not in ("Over", "Under"):
                                continue
                            k = (kind, team, float(point) if point is not None else None)
                            quotes.setdefault(k, []).append({"odds": int(american), "book": book.get("title", "Book"),
                                                               "updated": market.get("last_update") or book.get("last_update")})
                output.append({"id": e.get("id", ""), "league": league, "home": e["home_team"],
                               "away": e["away_team"], "start": start.isoformat(), "quotes": quotes})
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"{league}: {str(exc)[:110]}")
    return output, errors, remaining


@st.cache_data(ttl=45, show_spinner=False)
def get_scores(league, date_string):
    data, _ = request_json(f"https://site.api.espn.com/apis/site/v2/sports/{LEAGUES[league][1]}/scoreboard",
                           {"dates": date_string, "limit": 500})
    result = []
    for e in data.get("events", []):
        competition = (e.get("competitions") or [{}])[0]
        sides = {}
        for c in competition.get("competitors", []):
            t = c.get("team") or {}
            sides[c.get("homeAway")] = {"name": t.get("displayName", ""), "score": c.get("score"),
                                        "logo": t.get("logo")}
        if "home" in sides and "away" in sides:
            status = e.get("status") or {}
            result.append({"id": str(e.get("id")), "start": e.get("date"), "home": sides["home"],
                           "away": sides["away"], "state": (status.get("type") or {}).get("state", "pre"),
                           "detail": (status.get("type") or {}).get("shortDetail", ""),
                           "updated": now().isoformat()})
    return result


def match_game(event, games):
    matches = [g for g in games if dt(g["start"]) and
               abs((dt(g["start"]) - dt(event["start"])).total_seconds()) <= 5400 and
               same(g["home"]["name"], event["home"]) and same(g["away"]["name"], event["away"])]
    return matches[0] if len(matches) == 1 else None


def choose_quote(offers):
    # Best displayed American price; still a timestamped snapshot, not a guaranteed fill.
    return max(offers, key=lambda x: x["odds"]) if offers else None


def selections(event):
    picks = []
    for (kind, name, point), offers in event["quotes"].items():
        if kind == "h2h" and name not in (event["home"], event["away"]):
            continue
        q = choose_quote(offers)
        if not q:
            continue
        d = decimal(q["odds"])
        # Odds-implied likelihood is NOT a validated independent prediction or edge.
        likelihood = 1 / d
        if kind == "h2h":
            label = f"{name} to win"
        elif kind == "spreads":
            label = f"{name} {point:+g}"
        else:
            label = f"{name} {point:g} total points"
        picks.append({"key": f"{event['league']}:{event['id']}:{kind}:{name}:{point}", "event": event,
                      "kind": kind, "name": name, "point": point, "label": label,
                      "odds": q["odds"], "book": q["book"], "updated": q["updated"],
                      "likelihood": likelihood, "books": len(offers)})
    return picks


def db_open():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    # Keep all existing V2.7.1 tables intact; new data lives in new tables.
    con.execute("""CREATE TABLE IF NOT EXISTS royal_v28_picks (
      pick_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, league TEXT NOT NULL, game_id TEXT,
      home TEXT NOT NULL, away TEXT NOT NULL, start TEXT NOT NULL, kind TEXT NOT NULL,
      selection TEXT NOT NULL, point REAL, american_odds INTEGER NOT NULL,
      sportsbook TEXT NOT NULL, quote_updated TEXT, stake REAL NOT NULL,
      bucket TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
      payout REAL, pnl REAL, settled_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS royal_v28_parlays (
      parlay_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, stake REAL NOT NULL,
      decimal_odds REAL NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', payout REAL, pnl REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS royal_v28_legs (
      parlay_id TEXT NOT NULL, pick_id TEXT NOT NULL, PRIMARY KEY(parlay_id,pick_id))""")
    con.commit()
    return con


def grade(row, game):
    if game["state"] != "post":
        return None
    try:
        h, a = float(game["home"]["score"]), float(game["away"]["score"])
        kind, name, point = row["kind"], row["selection"], row["point"]
        if kind == "h2h":
            chosen = h if same(name, game["home"]["name"]) else a if same(name, game["away"]["name"]) else None
            other = a if chosen == h else h
            if chosen is None:
                return None
            difference = chosen - other
        elif kind == "spreads":
            chosen = h if same(name, game["home"]["name"]) else a if same(name, game["away"]["name"]) else None
            if chosen is None or point is None:
                return None
            difference = chosen + float(point) - (a if same(name, game["home"]["name"]) else h)
        elif kind == "totals":
            if point is None or name not in ("Over", "Under"):
                return None
            difference = (h + a - float(point)) * (1 if name == "Over" else -1)
        else:
            return None
        return "PUSH" if abs(difference) < 1e-7 else "WON" if difference > 0 else "LOST"
    except (ValueError, TypeError):
        return None


def settle(con, games):
    pending = con.execute("SELECT * FROM royal_v28_picks WHERE status='PENDING'").fetchall()
    for r in pending:
        event = dict(r)
        game = match_game(event, games.get(r["league"], []))
        if not game:
            continue
        result = grade(r, game)
        if result is None:
            continue
        stake = float(r["stake"])
        payout = round(stake * decimal(r["american_odds"]), 2) if result == "WON" else stake if result == "PUSH" else 0
        con.execute("UPDATE royal_v28_picks SET status=?,payout=?,pnl=?,settled_at=? WHERE pick_id=?",
                    (result, payout, round(payout-stake, 2), now().isoformat(), r["pick_id"]))
    for p in con.execute("SELECT * FROM royal_v28_parlays WHERE status='PENDING'").fetchall():
        legs = con.execute("""SELECT x.* FROM royal_v28_picks x JOIN royal_v28_legs l ON l.pick_id=x.pick_id
                              WHERE l.parlay_id=?""", (p["parlay_id"],)).fetchall()
        if not legs or any(l["status"] == "PENDING" for l in legs):
            continue
        factor = 1.0
        for l in legs:
            if l["status"] == "LOST":
                factor = 0.0
                break
            if l["status"] == "WON":
                factor *= decimal(l["american_odds"])
        payout = round(float(p["stake"]) * factor, 2)
        con.execute("UPDATE royal_v28_parlays SET status='SETTLED',payout=?,pnl=? WHERE parlay_id=?",
                    (payout, round(payout-float(p["stake"]), 2), p["parlay_id"]))
    con.commit()


def record_pick(con, p, bucket="MAIN"):
    if dt(p["event"]["start"]) <= now():
        return False, "Game has already started."
    identity = hashlib.sha256((p["key"] + ":" + bucket).encode()).hexdigest()[:24]
    if con.execute("SELECT 1 FROM royal_v28_picks WHERE pick_id=?", (identity,)).fetchone():
        return False, "Already recorded; no duplicate."
    reason = (f"{p['books']} quoted bookmaker observations; {p['book']} offered {odds_label(p['odds'])}. "
              "This is market-price screening, not an independently validated forecast or guaranteed value.")
    e = p["event"]
    con.execute("""INSERT INTO royal_v28_picks
      (pick_id,created_at,league,game_id,home,away,start,kind,selection,point,american_odds,
       sportsbook,quote_updated,stake,bucket,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (identity, now().isoformat(), e["league"], e["id"], e["home"], e["away"], e["start"],
       p["kind"], p["name"], p["point"], p["odds"], p["book"], p["updated"], STAKE, bucket, reason))
    con.commit()
    return True, "Locked before kickoff for $100 paper testing."


def show_pick(p, con, bucket="MAIN"):
    e = p["event"]
    with st.container(border=True):
        st.markdown(f"**{p['label']}** · {e['league']} · {e['away']} @ {e['home']}")
        st.caption(f"{local(e['start'])} · {p['kind'].upper()} · {p['book']} {odds_label(p['odds'])} · Quote: {local(p['updated'])}")
        st.markdown(f"**$100 paper stake** · Potential profit **${profit(STAKE,p['odds']):,.2f}** · Total return **${STAKE+profit(STAKE,p['odds']):,.2f}**")
        st.caption(f"Queen's reason: {p['books']} observed quotes; this selection is ranked using market prices, not a trained outcome model. No proven edge.")
        if st.button("👑 Lock $100 paper pick", key="lock_"+bucket+p["key"]):
            ok, message = record_pick(con, p, bucket)
            (st.success if ok else st.warning)(message)


banner = Path("royal_sports_banner.png")
if banner.exists():
    st.image(str(banner), use_container_width=True)
st.title("👑 QUEEN'S EDGE | 254")
st.markdown("### ROYAL SPORTS SNIPER · V2.8")
st.caption("PAPER TESTING ONLY · NBA / NFL / NCAAB / NCAAF · All times Central")

with st.expander("⚙️ Leagues and refresh", expanded=False):
    leagues = st.multiselect("Leagues", list(LEAGUES), default=list(LEAGUES))
    if st.button("Refresh odds and scores"):
        get_odds.clear()
        get_scores.clear()
        st.rerun()

key = api_key()
if not key:
    st.error("Set ODDS_API_KEY in Streamlit secrets to load real sportsbook prices. No picks will be invented.")
    st.stop()

odds, errors, quota = get_odds(key, tuple(leagues))
games = {}
score_errors = []
for league in leagues:
    unique = {}
    for offset in (-1, 0, 1, 2):
        date = (now().astimezone(CT).date() + timedelta(days=offset)).strftime("%Y%m%d")
        try:
            for g in get_scores(league, date):
                unique[g["id"]] = g
        except (requests.RequestException, ValueError, TypeError) as exc:
            score_errors.append(f"{league}: {str(exc)[:80]}")
    games[league] = list(unique.values())

con = db_open()
settle(con, games)
all_picks = [p for e in odds for p in selections(e) if dt(e["start"]) > now()]
# Rank by bookmaker coverage, then market-implied chance; avoid repeated picks for the same fixture.
all_picks.sort(key=lambda p: (-p["books"], -p["likelihood"], p["event"]["start"]))
main, dogs, used = [], [], set()
for p in all_picks:
    if p["kind"] == "h2h" and p["odds"] > 0:
        if p["event"]["id"] not in {x["event"]["id"] for x in dogs}:
            dogs.append(p)
        continue
    if p["kind"] == "h2h" and p["odds"] > -100:
        continue
    if p["event"]["id"] in used:
        continue
    main.append(p)
    used.add(p["event"]["id"])
    if len(main) >= 10:
        break

history = [dict(x) for x in con.execute("SELECT * FROM royal_v28_picks ORDER BY created_at DESC LIMIT 300")]
settled = [x for x in history if x["status"] != "PENDING"]
won = sum(x["status"] == "WON" for x in settled)
losses = sum(x["status"] == "LOST" for x in settled)
pushes = sum(x["status"] == "PUSH" for x in settled)
pnl = sum(float(x["pnl"] or 0) for x in settled)
parlays = [dict(x) for x in con.execute("SELECT * FROM royal_v28_parlays ORDER BY created_at DESC LIMIT 100")]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Royal picks", len(main))
m2.metric("Paper stake / pick", "$100")
m3.metric("Graded W–L–P", f"{won}–{losses}–{pushes}")
m4.metric("Settled paper P/L", f"${pnl:+,.2f}")

st.header("👑 Queen's Top 10")
st.caption("Strongest available market-screened selections, not independently validated predictions. Fewer than 10 is normal when quotes are limited.")
if main:
    for p in main:
        show_pick(p, con)
else:
    st.info("No eligible pregame picks with verified sportsbook quotes right now.")

st.header("🐺 Royal Underdog Watch")
st.caption("Separate from Queen's Top 10 and automatic parlays. Plus-money alone does not mean a good bet.")
for p in dogs[:5]:
    show_pick(p, con, "UNDERDOG")
if not dogs:
    st.info("No quoted plus-money underdogs available.")

st.header("👑 Royal Parlays")
legs_count = st.radio("Parlay legs", [2, 3, 5, 10], horizontal=True)
parlay_stake = st.radio("Paper parlay stake", [5, 10, 20], horizontal=True)
# Only distinct fixtures; no underdogs; preserve exact sportsbook leg prices.
leg_candidates = [p for p in main if p["kind"] == "h2h" and p["odds"] <= -100]
selected = leg_candidates[:legs_count]
if len(selected) != legs_count:
    st.info(f"Only {len(selected)} eligible independent-game moneylines available for a {legs_count}-leg ticket. No forced legs.")
else:
    combined = 1.0
    for p in selected:
        combined *= decimal(p["odds"])
        st.markdown(f"**{p['label']}** · {p['book']} {odds_label(p['odds'])}")
    st.caption("Illustrative combined decimal odds from separate bookmaker snapshots; NOT an executable sportsbook parlay quote. Correlation and repricing may apply.")
    st.markdown(f"**${parlay_stake} paper stake** · Illustrative gross ${parlay_stake*combined:,.2f} · Profit ${parlay_stake*(combined-1):,.2f}")
    if st.button("📝 Record hypothetical parlay"):
        ids = []
        for p in selected:
            record_pick(con, p, "PARLAY_LEG")
            ids.append(hashlib.sha256((p["key"]+":PARLAY_LEG").encode()).hexdigest()[:24])
        identity = hashlib.sha256(("|".join(ids)+f":{parlay_stake}").encode()).hexdigest()[:24]
        if any(dt(p["event"]["start"]) <= now() for p in selected):
            st.warning("A game started before the ticket was recorded. Ticket not saved.")
        elif con.execute("SELECT 1 FROM royal_v28_parlays WHERE parlay_id=?", (identity,)).fetchone():
            st.warning("This ticket is already recorded.")
        else:
            con.execute("INSERT INTO royal_v28_parlays (parlay_id,created_at,stake,decimal_odds) VALUES (?,?,?,?)",
                        (identity, now().isoformat(), parlay_stake, combined))
            con.executemany("INSERT INTO royal_v28_legs (parlay_id,pick_id) VALUES (?,?)", [(identity, i) for i in ids])
            con.commit()
            st.success("Hypothetical ticket recorded. Not an executable quote.")

st.header("🔴 Queen's Live Tracker")
tracked = [dict(x) for x in con.execute("SELECT * FROM royal_v28_picks ORDER BY created_at DESC LIMIT 60")]
if tracked:
    for x in tracked[:25]:
        game = match_game(x, games.get(x["league"], []))
        score = (f"{game['away']['name']} {game['away']['score']} · {game['home']['name']} {game['home']['score']} · {game['detail']}"
                 if game else "Score not matched / not available")
        st.markdown(f"**{x['selection']}** · {x['kind']} · {x['status']} · {x['bucket']} · ${x['stake']:.0f} paper")
        st.caption(score)
else:
    st.info("No V2.8 picks recorded yet. Lock a pregame paper pick to begin tracking.")

st.header("📊 Royal Performance")
for bucket, label in [("MAIN", "Main picks"), ("UNDERDOG", "Underdogs"), ("PARLAY_LEG", "Parlay legs")]:
    rows = [x for x in settled if x["bucket"] == bucket]
    w, l = sum(x["status"] == "WON" for x in rows), sum(x["status"] == "LOST" for x in rows)
    net = sum(float(x["pnl"] or 0) for x in rows)
    st.markdown(f"**{label}:** {w}W / {l}L / {sum(x['status']=='PUSH' for x in rows)}P · "
                f"{w/(w+l):.1%} win rate" if w+l else f"**{label}:** No graded results yet")
    if rows:
        st.caption(f"Paper P/L: ${net:+,.2f} · Sample: {len(rows)}")
settled_parlays = [p for p in parlays if p["status"] == "SETTLED"]
st.markdown(f"**Parlays:** {len(settled_parlays)} settled · ${sum(float(p['pnl'] or 0) for p in settled_parlays):+,.2f} paper P/L")

with st.expander("📚 Full history, live scoreboard and diagnostics", expanded=False):
    if history:
        st.dataframe(history, hide_index=True, use_container_width=True)
    if parlays:
        st.dataframe(parlays, hide_index=True, use_container_width=True)
    for league in leagues:
        live = [g for g in games[league] if g["state"] == "in"]
        if live:
            st.markdown(f"**{league} live**")
            st.dataframe([{"Away": g["away"]["name"], "Away score": g["away"]["score"],
                           "Home": g["home"]["name"], "Home score": g["home"]["score"],
                           "Status": g["detail"]} for g in live], hide_index=True)
    st.caption(f"Odds API remaining requests reported: {quota if quota is not None else 'Unknown'}")
    st.caption("Odds cache: up to 15 minutes · ESPN score cache: up to 45 seconds · Refresh manually. Streamlit is not a background monitoring service.")
    for error in errors + score_errors:
        st.warning(error)

with st.expander("🔎 Kalshi / Polymarket and player props — future modules", expanded=False):
    st.caption("V2.8 sportsbook picks work independently of Kalshi or Polymarket. Legacy market-scanner and trade tables remain in the database, but their live scanners are not activated in this replacement.")
    st.caption("Player props require verified player lines, stats and grading. Not active; no invented prop picks.")

st.caption("QUEEN'S EDGE | 254 · ROYAL SPORTS SNIPER V2.8 · RESEARCH / PAPER TESTING ONLY · NO REAL ORDERS")
