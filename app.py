import os, re, sqlite3, hashlib, html
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote
import requests
import streamlit as st

st.set_page_config(page_title="ROYAL SPORTS SNIPER V3.3 | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
CT = ZoneInfo("America/Chicago")
BASE = "https://gateway.polymarket.us"
LEAGUES = {"NBA":"nba", "NFL":"nfl", "NCAAB":"ncaab", "NCAAF":"ncaaf", "NHL":"nhl", "MLB":"mlb", "WNBA":"wnba", "MLS":"mls", "EPL":"epl", "UFC":"ufc", "Tennis":"tennis"}
ESPN = {"NBA":"basketball/nba", "NFL":"football/nfl", "NCAAB":"basketball/mens-college-basketball", "NCAAF":"football/college-football", "NHL":"hockey/nhl", "MLB":"baseball/mlb", "WNBA":"basketball/wnba", "MLS":"soccer/usa.1", "EPL":"soccer/eng.1", "Tennis":"tennis"}
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
    c.execute("""CREATE TABLE IF NOT EXISTS royal_quote_snapshots_v30 (
      id TEXT PRIMARY KEY, event_id TEXT, slug TEXT, sport TEXT, title TEXT,
      bid REAL, ask REAL, live INTEGER, observed_at TEXT)""")
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
        r = requests.get(BASE + path, params=params, headers={"Accept":"application/json", "User-Agent":"RoyalSportsSniper254/3.3"}, timeout=12)
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
                          "scores": {t.get("team", {}).get("displayName", ""):t.get("score") for t in teams}, "winner": next((t.get("team", {}).get("displayName", "") for t in teams if t.get("winner") is True), None), "logos":{t.get("team", {}).get("displayName", ""):t.get("team", {}).get("logo", "") for t in teams}}
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

def identify_yes_team(market):
    """Fail closed: map YES only with an explicit contract-side assertion.

    The event's two marketSides are competitors, not YES/NO outcomes.
    The event slug and a low quote cannot prove which team YES pays on.
    """
    sides = [v for v in market.get("marketSides", []) if isinstance(v, dict)]
    names = [str((v.get("team") or {}).get("name") or "").strip() for v in sides]
    names = [n for n in names if n]
    if not names:
        return None, "No named competitors in marketSides"

    # A one-sided, explicitly named winner contract is sufficient only when
    # the market's own question states the proposition, not just an event title.
    proposition = str(market.get("question") or market.get("contractQuestion") or "").strip()
    if proposition:
        matches = [n for n in names if re.search(r"\b" + re.escape(n) + r"\b", proposition, re.I)]
        winner_question = bool(re.search(r"\b(win|wins|winner|beat|defeat|victory)\b", proposition, re.I))
        if len(matches) == 1 and winner_question and not re.search(r"\b(not|won.t|lose|loses)\b", proposition, re.I):
            return matches[0], "Explicit team-to-win contract question"

    explicit = []
    for side in sides:
        team = str((side.get("team") or {}).get("name") or "").strip()
        if not team:
            continue
        # Check side labels, but never treat an arbitrary team array order as YES.
        for key in ("side", "outcome", "position", "result", "label", "contractSide"):
            val = side.get(key)
            if isinstance(val, str) and val.strip().upper() == "YES":
                explicit.append(team)
                break
    if len(set(explicit)) == 1:
        return explicit[0], "Explicit YES side on named competitor"
    return None, f"Unverified YES mapping: {len(sides)} competitors, no explicit winning side"

@st.cache_data(ttl=120, show_spinner=False)
def market_details(slug):
    """Optional detailed metadata. Endpoint may not be available for every market."""
    if not slug or not re.fullmatch(r"[A-Za-z0-9_-]+", str(slug)):
        return None, "Invalid slug"
    return api(f"/v1/markets/{quote(str(slug), safe='')}")

def resolve_contract(market):
    # Never infer the YES team from slug abbreviation or event title.
    name, reason = identify_yes_team(market)
    if name:
        return name, reason, "event market"
    slug = market.get("slug")
    if not slug:
        return None, reason, "none"
    detail, err = market_details(slug)
    if not isinstance(detail, dict):
        return None, reason + (f"; detail unavailable ({err})" if err else ""), "none"
    # Only use a nested object that itself describes the exact same market.
    candidates = [detail]
    for key in ("market", "marketData"):
        obj = detail.get(key)
        if isinstance(obj, dict):
            candidates.append(obj)
    for candidate in candidates:
        if candidate.get("slug") and candidate["slug"] != slug:
            continue
        merged = dict(market)
        for key in ("question", "contractQuestion", "marketSides"):
            if candidate.get(key):
                merged[key] = candidate[key]
        name, detail_reason = identify_yes_team(merged)
        if name:
            return name, detail_reason, "market detail"
    return None, reason + "; detail does not establish YES outcome", "market detail"

def events_for(sport):
    # Different league identifiers may appear across Polymarket US surfaces.
    # Try a fallback only when the canonical league returned no usable events.
    aliases = {
        "NCAAB": ["ncaab", "cbb"],
        "NCAAF": ["ncaaf", "cfb"],
        "WNBA": ["wnba", "wcbb"],
        "Tennis": ["tennis", "atp", "wta"],
    }
    found, seen, failures = [], set(), []
    for league in aliases.get(sport, [LEAGUES[sport]]):
        # Two pages prevent the first 50 events from hiding upcoming games.
        for offset in (0, 50):
            payload, error = api(f"/v2/leagues/{league}/events", {"limit":50, "offset":offset})
            if error:
                failures.append(f"{league}: {error}")
                break
            items = payload if isinstance(payload, list) else payload.get("events", []) if isinstance(payload, dict) else []
            if not isinstance(items, list): break
            for event in items:
                if not isinstance(event, dict): continue
                ident = str(event.get("id") or event.get("slug") or "")
                if ident and ident not in seen:
                    seen.add(ident)
                    found.append(event)
            if len(items) < 50: break
        if found and sport not in ("Tennis",): break
    return found, ("; ".join(failures) if not found and failures else None)

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
            snapshot_id=hashlib.sha256((x["event_id"]+"|"+x["slug"]+"|"+utc().isoformat()).encode()).hexdigest()[:32]
            c.execute("INSERT OR IGNORE INTO royal_quote_snapshots_v30 (id,event_id,slug,sport,title,bid,ask,live,observed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (snapshot_id,x["event_id"],x["slug"],x["sport"],x["title"],q["bid"],q["ask"],int(x["live"]),utc().isoformat()))
            c.execute("""INSERT OR IGNORE INTO poly_watch_v293
              (id,event_id,sport,title,market_slug,selection,bid,ask,market_state,game_live,observed_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (ident,x["event_id"],x["sport"],x["title"],x["slug"],x["selection"],q["bid"],q["ask"],q["state"],int(x["live"]),utc().isoformat()))
        c.commit()

def money_profit(price, stake):
    # Theoretical gross settlement profit for a $1-winning contract, not an execution quote.
    return stake * (1 / price - 1)

SPORT_ICON = {"NBA":"🏀","NFL":"🏈","NCAAB":"🏀","NCAAF":"🏈","NHL":"🏒","MLB":"⚾","WNBA":"🏀","MLS":"⚽","EPL":"⚽","UFC":"🥊","Tennis":"🎾"}

def safe_logo(url):
    return url if isinstance(url,str) and url.startswith("https://") and ("espncdn.com" in url or "espn.com" in url) else None

def game_logos(x):
    g=x.get("score_game") or {}
    return [safe_logo(v) for v in (g.get("logos") or {}).values() if safe_logo(v)][:2]

def display_row(x, number=None):
    q = x.get("quote")
    sport = x.get("sport", "Sports")
    icon = SPORT_ICON.get(sport, "🏆")
    title = html.escape(str(x.get("title") or "Sports match"))
    logos = game_logos(x)
    logo_html = "".join(f'<img src="{html.escape(u,quote=True)}" class="team-logo" alt="team logo">' for u in logos)
    valid = bool(q and q.get("valid"))
    verified = bool(x.get("verified_side"))
    state = "🔴 LIVE" if x.get("live") else "UPCOMING"
    # A price is not a prediction. Never label an unidentified YES as a named team.
    if valid and verified:
        action = "WATCH"
        main = f"YES: {html.escape(str(x['selection']))}"
        secondary = f"Buy {q['ask']*100:.0f}¢ · Sell {q['bid']*100:.0f}¢"
    elif valid:
        action = "WAIT"
        main = "Team/outcome not verified"
        secondary = f"Contract YES {q['ask']*100:.0f}¢ · not a team pick"
    else:
        action = "WAIT"
        main = "No verified quote"
        secondary = "Price unavailable"
    with st.container(border=True):
        st.markdown(f'''<div class="royal-game">
          <div class="royal-game-head"><span class="sport-icon">{icon}</span>
          <span class="royal-match">{f'{number}. ' if number else ''}{title}</span>
          <span class="logos">{logo_html}</span></div>
          <div class="royal-game-bottom"><div class="royal-game-text">
          <div class="royal-meta">{html.escape(sport)} · {state} · {html.escape(str(x.get('market_type','Market')))}</div>
          <div class="royal-selection">{main}</div><div class="royal-price">{secondary}</div></div>
          <span class="royal-watch">{action}</span></div></div>''', unsafe_allow_html=True)
        with st.expander("Why this status? · Contract details"):
            st.write("Game:", x.get("score_detail") or local(x.get("start")))
            st.write("Verified YES outcome:", x["selection"] if verified else "Not established by contract metadata")
            st.write("Mapping check:", x.get("side_reason", "Unknown"))
            st.write("Mapping source:", x.get("mapping_source", "Not available"))
            st.write("Market slug:", x.get("slug"))
            if valid:
                st.write(f"Observed YES ask {q['ask']:.0%}, bid {q['bid']:.0%}")
                if verified:
                    st.write(f"If YES wins, hypothetical $100 settlement profit: ${money_profit(q['ask'],100):,.2f} before fees")
            st.write("Automatic paper test:", paper_reason(x))
            st.caption("WATCH means monitor, not buy. These prices are not independent win probabilities.")

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
    st.subheader("🧪 $100 Paper Results")
    a,b,c,d = st.columns(4)
    a.metric("Paper entries", totals[0]); b.metric("Wins", totals[1] or 0)
    c.metric("Losses", totals[2] or 0); d.metric("Settled paper P&L", f"${totals[3]:,.2f}")
    st.caption("Each qualifying pregame YES contract is simulated at its observed ask; no orders are placed. Open P&L uses indicative bid, not a guaranteed cash-out. Game results are provisional until official market settlement is checked. Fees/slippage excluded.")
    if not records:
        st.info("No qualifying paper entries yet. Scan Today or Next 3 Days, then open 'Why paper entries are skipped' to share the sample contract metadata. No unverified team will be paper-traded.")
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
.block-container{padding-top:.5rem;max-width:1050px;padding-left:.75rem;padding-right:.75rem}
h1,h2,h3{color:#c493ff!important;letter-spacing:-.025em}
h2{font-size:clamp(1.15rem,3vw,1.5rem)!important}
[data-testid="stMetric"]{background:#1c1424;padding:.45rem;border-radius:11px;border:1px solid #352541}
[data-testid="stMetricLabel"]{font-size:.72rem}
[data-testid="stMetricValue"]{font-size:1.2rem}
.stButton>button{border-radius:22px;min-height:2.55rem}
[data-testid="stExpander"]{border-radius:10px;border-color:#352b3a}
.royal-game{padding:.1rem 0 .05rem;margin:0;min-height:100px}
.royal-game-head{display:flex;align-items:flex-start;gap:.3rem;min-width:0}
.sport-icon{font-size:1.2rem;flex-shrink:0}
.royal-match{font-size:.88rem;font-weight:700;color:#f2eafa;flex:1;min-width:0;line-height:1.22;overflow-wrap:anywhere}
.logos{display:flex;gap:3px;align-items:center;flex-shrink:0}
.team-logo{height:19px;width:19px;object-fit:contain}
.royal-game-bottom{display:flex;align-items:flex-start;justify-content:space-between;gap:.25rem;margin-left:0;padding-top:.3rem;flex-wrap:wrap}
.royal-game-text{min-width:0}
.royal-meta{font-size:.67rem;color:#b3a6c0;margin:0 0 .12rem}
.royal-selection{font-size:.78rem;font-weight:700;color:#e9d3ff;line-height:1.2}
.royal-price{font-size:.68rem;color:#a99cb1;margin-top:.1rem}
.royal-watch{font-size:.64rem;font-weight:750;color:#f3c978;border:1px solid #59462d;border-radius:18px;padding:.18rem .5rem;white-space:nowrap}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:12px!important}
[data-testid="stVerticalBlockBorderWrapper"]:has(.royal-game){background:linear-gradient(145deg,#211628,#15121b);border:1px solid #46304e!important;padding:.45rem .55rem!important;min-width:0}
[data-testid="stVerticalBlockBorderWrapper"]:has(.royal-game) [data-testid="stExpander"]{margin-top:.1rem}
[data-testid="stVerticalBlockBorderWrapper"]:has(.royal-game) [data-testid="stExpander"] summary{font-size:.69rem;padding:.32rem!important}
[data-testid="stVerticalBlockBorderWrapper"]:has(.royal-game) [data-testid="stExpander"] [data-testid="stExpanderDetails"]{font-size:.76rem}
@media(max-width:650px){
 .block-container{padding-left:.55rem;padding-right:.55rem}
 [data-testid="stHorizontalBlock"]{gap:.3rem!important}
 [data-testid="column"]{min-width:calc(50% - .3rem)!important;flex:1 1 calc(50% - .3rem)!important}
 [data-testid="stMetric"]{padding:.35rem}
 [data-testid="stMetricValue"]{font-size:1.05rem}
 .royal-match{font-size:.76rem}
 .team-logo{width:15px;height:15px}
 .royal-game{padding:.02rem;min-height:118px}
 .sport-icon{font-size:.95rem}
 .royal-meta{font-size:.6rem}
 .royal-selection{font-size:.72rem}
 .royal-price{font-size:.63rem}
 .royal-watch{font-size:.6rem;padding:.1rem .35rem}
 [data-testid="stVerticalBlockBorderWrapper"]:has(.royal-game){padding:.35rem!important}
}
</style>""",unsafe_allow_html=True)

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
st.caption("V3.3 · POLYMARKET US · LIVE SPORTS WATCH · PAPER ONLY")

sport = st.radio("🏆 Choose Your Sport", ["All Sports"] + list(LEAGUES), horizontal=True)
window = st.radio("📅 Choose Game Window", ["🔴 Live", "📅 Today", "Tomorrow", "Next 3 Days"], horizontal=True, index=1)
with st.expander("⚙️ Scan settings & important notes"):
    max_leagues = st.slider("Maximum leagues in All Sports", 1, len(LEAGUES), len(LEAGUES))
    max_quotes = st.slider("Maximum contracts to price per scan", 10, 120, 60, step=5)
    st.caption("The public Polymarket US API is used without an account key. Quotes are cached for two minutes. Game scores come from ESPN and may lag.")
    st.caption("Paper entries are simulated at the observed ask; the experimental entry filter is NOT a validated prediction or positive-edge strategy. Existing history is preserved separately.")

if st.button("👑 Scan Polymarket US", type="primary", use_container_width=True):
    st.session_state["royal_scan"] = True
if not st.session_state.get("royal_scan"):
    st.info("Press Scan Polymarket US to discover games and request available market prices.")
    st.stop()

priority = ["NCAAB", "NCAAF", "NBA", "NFL", "NHL", "Tennis", "MLB", "WNBA", "MLS", "EPL", "UFC"]
selected = priority[:max_leagues] if sport == "All Sports" else [sport]
with st.spinner("Finding Polymarket US sports events and checking market quotes..."):
    index = score_index(selected)
    all_events, errors = [], []
    for s in selected:
        items, err = events_for(s)
        if err: errors.append(f"{s}: {err}")
        all_events.extend((s, e) for e in items)

current = utc().astimezone(CT).date()
def in_window(s, e, game):
    live = bool(e.get("live") or (e.get("eventState") or {}).get("live") or str(e.get("status", "")).lower() in ("live", "in_progress", "inprogress") or (game and game["state"] == "in"))
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
    is_live = bool(e.get("live") or (e.get("eventState") or {}).get("live") or str(e.get("status", "")).lower() in ("live", "in_progress", "inprogress") or (game and game["state"] == "in"))
    if is_live: live_count += 1
    for m in e.get("markets") or []:
        if not isinstance(m, dict) or m.get("closed") or m.get("archived"): continue
        slug = m.get("slug")
        if not slug: continue
        sides = m.get("marketSides") or []
        # One contract is one instrument. Only label a side when the API identifies it.
        verified_team, side_reason, mapping_source = resolve_contract(m)
        selection = verified_team or (m.get("titleShort") or m.get("question") or m.get("title") or slug)
        market_type = str(m.get("sportsMarketTypeV2") or m.get("sportsMarketType") or "Market").replace("SPORTS_MARKET_TYPE_", "").replace("_", " ").title()
        rows.append({"event_id":str(e.get("id")), "sport":s, "title":str(e.get("title") or "Sports event"),
                     "slug":str(slug), "selection":str(selection), "market_type":market_type, "verified_side":bool(verified_team), "side_reason":side_reason, "mapping_source":mapping_source, "raw_sides":sides, "market_raw":m, "event_raw":e,
                     "start":e.get("eventDate") or e.get("startTime") or e.get("startDate"),
                     "live":is_live, "game_state":game["state"] if game else "unknown",
                     "score_detail":game["detail"] if game else "", "score_game":game, "quote":None, "error":None})

# Give every league a fair share of quote requests; one busy league cannot crowd out college sports.
rows.sort(key=lambda x:(not x["live"], parse_date(x["start"]) or datetime.max.replace(tzinfo=timezone.utc)))
from collections import defaultdict
buckets = defaultdict(list)
for row in rows:
    buckets[row["sport"]].append(row)
checked = []
while len(checked) < max_quotes and any(buckets.values()):
    for league in selected:
        if len(checked) >= max_quotes: break
        if buckets[league]:
            checked.append(buckets[league].pop(0))
for x in checked:
    x["quote"], x["error"] = bbo(x["slug"])
    if x["quote"] and x["quote"]["valid"]: quoted += 1
observations(checked)
paper_update(checked)



with st.expander("🔎 Why paper entries are skipped", expanded=False):
    from collections import Counter
    reasons = Counter(paper_reason(x) for x in checked)
    if not reasons:
        st.write("No markets checked in this game window.")
    for reason, count in reasons.most_common():
        st.write(f"{count} contracts · {reason}")
    st.caption("Team arrays and event slugs do not identify the YES winner. The expanded sample now includes original contract metadata for troubleshooting; no guessing.")
    if rows:
        st.markdown("**Sample market metadata (copy and send this to me)**")
        sample = next((x for x in rows if not x.get("verified_side")), rows[0])
        st.json({"event":sample["title"], "market_slug":sample["slug"], "market_type":sample["market_type"],
                 "marketSides":sample.get("raw_sides"), "market_metadata":sample.get("market_raw"), "mapping_result":sample.get("side_reason")}, expanded=False)

st.subheader("👑 Queen’s Top 10 · Market Watch")
watch = [x for x in checked if x.get("quote") and x["quote"]["valid"] and "OPEN" in x["quote"]["state"].upper()]
watch.sort(key=lambda x:(not x["verified_side"], x["quote"]["ask"]-x["quote"]["bid"], x["quote"]["ask"]))
# Diversify Top 10 across sports before filling remaining slots.
unique, seen = [], set()
for league in selected:
    candidate = next((x for x in watch if x["sport"] == league and x["event_id"] not in seen), None)
    if candidate:
        unique.append(candidate); seen.add(candidate["event_id"])
for x in watch:
    if x["event_id"] in seen: continue
    unique.append(x); seen.add(x["event_id"])
if window == "🔴 Live":
    st.caption("Showing only markets marked live by the event feed or matched scoreboard. Tennis is included when the US feed provides it.")
# Pair consecutive games in a compact two-column grid, including on phones.
for offset in range(0, min(len(unique), 10), 2):
    left_card, right_card = st.columns(2, gap="small")
    with left_card:
        display_row(unique[offset], offset + 1)
    if offset + 1 < min(len(unique), 10):
        with right_card:
            display_row(unique[offset + 1], offset + 2)
if not unique: st.info("No valid open two-sided quotes in this scan. Queen says WAIT. Try Today or Next 3 Days and check the feed diagnostics.")
st.caption("Top 10 includes different leagues when possible. WATCH is not a buy recommendation; no independent win-probability model is validated yet.")

paper_panel()

with st.expander("📡 Market feed & scan details",expanded=False):
    a,b,c,d=st.columns(4)
    a.metric("Events",len(all_events));b.metric("Markets",len(rows));c.metric("Quotes",quoted);d.metric("Live",live_count)
    if errors:
        for err in errors: st.caption(err)
    st.write("Events discovered by sport:")
    for league in selected:
        st.caption(f"{league}: {sum(1 for a,b in all_events if a == league)} events · {sum(1 for x in checked if x['sport'] == league)} quotes requested")
    st.caption("Open market is not the same as a live game. Quotes are indicative.")
    for x in checked: display_row(x)

left,right = st.columns(2)
with left:
    st.subheader("🎯 Royal Parlays")
    legs = st.radio("Parlay legs", [2,3,5,10], horizontal=True)
    st.info("Parlay calculations paused until compatible Polymarket US combo pricing and each leg's settlement rules are verified. No fabricated combined odds.")
with right:
    st.subheader("🔥 Underdog Watch")
    dogs = [x for x in watch if x.get("verified_side") and x["quote"]["ask"] < .5]
    if dogs:
        for x in dogs[:4]: st.write(f"**{x['selection']}** · {x['sport']} · YES ask {x['quote']['ask']:.0%} · WATCH ONLY")
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
