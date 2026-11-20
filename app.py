import os, re, sqlite3, hashlib, html, math, csv, io
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote
from difflib import SequenceMatcher
import requests
import streamlit as st

st.set_page_config(page_title="ROYAL SPORTS SNIPER V4.6 | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
CT = ZoneInfo("America/Chicago")
BASE = "https://gateway.polymarket.us"
LEAGUES = {"NBA":"nba", "NFL":"nfl", "NCAAB":"ncaab", "NCAAF":"ncaaf", "NHL":"nhl", "MLB":"mlb", "WNBA":"wnba", "MLS":"mls", "EPL":"epl", "UFC":"ufc", "Tennis":"tennis/all"}
ESPN = {"NBA":"basketball/nba", "NFL":"football/nfl", "NCAAB":"basketball/mens-college-basketball", "NCAAF":"football/college-football", "NHL":"hockey/nhl", "MLB":"baseball/mlb", "WNBA":"basketball/wnba", "MLS":"soccer/usa.1", "EPL":"soccer/eng.1", "Tennis":"tennis/all"}
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
    c.execute("""CREATE TABLE IF NOT EXISTS royal_predictions_v34 (
      id TEXT PRIMARY KEY, event_id TEXT, slug TEXT, sport TEXT, title TEXT,
      selection TEXT, opponent TEXT, phase TEXT, model TEXT, model_probability REAL,
      confidence TEXT, evidence TEXT, quote_ask REAL, created_at TEXT,
      result TEXT DEFAULT 'PENDING', graded_at TEXT, grade_note TEXT,
      UNIQUE(event_id,slug,phase))""")
    c.execute("""CREATE TABLE IF NOT EXISTS royal_paper_v34 (
      id TEXT PRIMARY KEY, prediction_id TEXT UNIQUE, event_id TEXT, slug TEXT,
      sport TEXT, selection TEXT, stake REAL, entry_ask REAL, entry_at TEXT,
      last_bid REAL, status TEXT DEFAULT 'OPEN', pnl REAL, graded_at TEXT, note TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS royal_parlays_v37 (
      id TEXT PRIMARY KEY, session_key TEXT, leg_count INTEGER, created_at TEXT,
      status TEXT DEFAULT 'PENDING', wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
      note TEXT, UNIQUE(session_key,leg_count))""")
    c.execute("""CREATE TABLE IF NOT EXISTS royal_parlay_legs_v37 (
      parlay_id TEXT NOT NULL, leg_number INTEGER NOT NULL, prediction_id TEXT NOT NULL,
      event_id TEXT, sport TEXT, title TEXT, selection TEXT, model TEXT,
      PRIMARY KEY(parlay_id,leg_number))""")
    c.commit()
    return c

@st.cache_data(ttl=120, show_spinner=False)
def api(path, params=None):
    try:
        r = requests.get(BASE + path, params=params, headers={"Accept":"application/json", "User-Agent":"RoyalSportsSniper254/3.8"}, timeout=12)
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

def _score_competitor_name(comp):
    """ESPN team sports use team; tennis generally uses athlete."""
    team=comp.get("team") or {}
    athlete=comp.get("athlete") or {}
    return str(team.get("displayName") or athlete.get("displayName") or
               comp.get("displayName") or athlete.get("fullName") or "").strip()

def _tennis_sets(comp):
    parts=[]
    for entry in comp.get("linescores") or []:
        if isinstance(entry,dict):
            value=entry.get("value",entry.get("displayValue"))
        else: value=entry
        if value is not None and str(value).strip():
            number=numeric(value)
            parts.append(str(int(number)) if number is not None and number.is_integer() else str(value))
    return parts

def _score_record(event, sport, competition=None):
    """Normalize either a team game or a tennis match nested in a tournament."""
    comp = competition or ((event.get("competitions") or [None])[0])
    if not isinstance(comp, dict): return None
    participants = comp.get("competitors") or []
    if len(participants) != 2: return None
    names = [_score_competitor_name(p) for p in participants]
    if not all(names) or clean(names[0]) == clean(names[1]): return None
    status = comp.get("status") or event.get("status") or {}
    typ = status.get("type") or {}
    state = typ.get("state") or "unknown"
    detail = str(typ.get("shortDetail") or typ.get("detail") or typ.get("description") or status.get("displayClock") or "").strip()
    scores = {n:p.get("score") for n,p in zip(names,participants)}
    sets = {n:_tennis_sets(p) for n,p in zip(names,participants)} if sport == "Tennis" else {}
    return {"state":state,"detail":detail,"date":comp.get("date") or event.get("date"),
            "teams":names,"scores":scores,"sets":sets,
            "winner":next((n for n,p in zip(names,participants) if p.get("winner") is True),None),
            "logos":{n:(p.get("team") or {}).get("logo","") for n,p in zip(names,participants)},
            "records":{n:p.get("records") or [] for n,p in zip(names,participants)},
            "home":next((n for n,p in zip(names,participants) if p.get("homeAway")=="home"),""),
            "event_id":str(comp.get("id") or event.get("id") or ""),
            "source":"ESPN", "completed":bool(typ.get("completed"))}

def _score_records(event, sport):
    """ESPN tennis events are tournaments; matches live inside groupings."""
    if sport != "Tennis":
        item = _score_record(event,sport)
        return [item] if item else []
    competitions = list(event.get("competitions") or [])
    for grouping in event.get("groupings") or []:
        competitions.extend(grouping.get("competitions") or [])
    if not competitions and len(event.get("competitors") or []) == 2:
        competitions = [event]
    records = []
    for comp in competitions:
        item = _score_record(event,sport,comp)
        if item: records.append(item)
    return records

def _name_tokens(name):
    """Normalize common scoreboard abbreviations without guessing the winner."""
    text=str(name or '').lower().replace('st.', 'saint ').replace('st ', 'saint ')
    tokens=re.findall(r"[a-z0-9]+",text)
    drop={'the','fc','cf','university','college','of','at','vs','versus'}
    return [t for t in tokens if t not in drop]

def _person_name_match(a,b):
    aa,bb=_name_tokens(a),_name_tokens(b)
    if not aa or not bb: return 0.0
    if sorted(aa)==sorted(bb): return 1.0
    # First initial + last name can safely identify a player only with a unique match.
    if len(aa)>=2 and len(bb)>=2 and aa[-1]==bb[-1] and aa[0][0]==bb[0][0]: return .95
    return SequenceMatcher(None,clean(a),clean(b)).ratio()

def _team_name_match(a,b):
    aa,bb=_name_tokens(a),_name_tokens(b)
    if not aa or not bb: return 0.0
    if clean(a)==clean(b): return 1.0
    if sorted(aa)==sorted(bb): return .99
    # E.g. 'Atlanta Hawks' and 'ATL Hawks' only if shared nickname is distinctive.
    if len(aa)>=2 and len(bb)>=2 and aa[-1]==bb[-1] and len(aa[-1])>=5: return .94
    return SequenceMatcher(None,clean(a),clean(b)).ratio()

def _game_similarity(row, game):
    if row.get('sport')=='Tennis': score=_person_name_match
    else: score=_team_name_match
    a,b=row.get('selection'),row.get('opponent')
    x,y=game['teams']
    return max(min(score(a,x),score(b,y)),min(score(a,y),score(b,x)))

def score_index(sports, extra_dates=None):
    """Scoreboard snapshots for today and selected saved-event dates, not just a fixed window."""
    result={}
    today=utc().astimezone(CT).date()
    dates={today+timedelta(days=d) for d in (-2,-1,0,1,2,3)}
    for value in (extra_dates or []):
        dt=parse_date(value)
        if dt and abs((dt.astimezone(CT).date()-today).days)<=14:
            d=dt.astimezone(CT).date()
            dates.update((d-timedelta(days=1),d,d+timedelta(days=1)))
    jobs=[(sport,d.strftime('%Y%m%d')) for sport in sports if sport in ESPN for d in sorted(dates)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures={pool.submit(scores,sport,day):sport for sport,day in jobs}
        for future in as_completed(futures):
            sport=futures[future]
            try: events=future.result()
            except Exception: continue
            for event in events:
                for game in _score_records(event,sport):
                    key = (sport,str(game['event_id']) or clean(''.join(game['teams'])))
                    existing = result.get(key)
                    # Prefer a completed/live snapshot over an older pregame one.
                    priority = {'post':3,'in':2,'pre':1}
                    if not existing or priority.get(game['state'],0) >= priority.get(existing['state'],0):
                        result[key] = game
    return result

def _match_saved_game(row, index):
    """Match both opponents, sport, and time; reject ambiguous candidates."""
    sport=row.get('sport')
    a,b=clean(row.get('selection')),clean(row.get('opponent'))
    if not a or not b or a==b: return None
    created=parse_date(row.get('created_at'))
    matches=[]
    for (league,_),game in index.items():
        if league!=sport: continue
        strength=_game_similarity(row,game)
        if strength<.90: continue
        start=parse_date(game.get('date'))
        # A locked forecast should not match a game played before it was created.
        if created and start and start<created-timedelta(hours=6): continue
        # Prevent matching a similarly named rematch weeks later.
        if created and start and start>created+timedelta(days=8): continue
        matches.append((strength,game))
    matches.sort(key=lambda v:v[0],reverse=True)
    if matches:
        unique = {}
        for strength,game in matches:
            unique[(game.get('event_id'), tuple(sorted(game['teams'])))]=(strength,game)
        matches=sorted(unique.values(),key=lambda v:v[0],reverse=True)
    if matches and (len(matches)==1 or matches[0][0]-matches[1][0]>=.06):
        return matches[0][1]
    return None


def event_score(sport, event, index):
    names=[]
    for market in event.get('markets') or []:
        for side in market.get('marketSides') or []:
            name=(side.get('team') or {}).get('name')
            if name and clean(name) not in [clean(n) for n in names]: names.append(name)
    if len(names)!=2: return None
    candidates=[]
    for (league,_),game in index.items():
        if league!=sport: continue
        score=_game_similarity({'sport':sport,'selection':names[0],'opponent':names[1]},game)
        if score>=.90:
            when=parse_date(event.get('eventDate') or event.get('startTime') or event.get('startDate'))
            gd=parse_date(game.get('date'))
            if when and gd and abs((when-gd).total_seconds())>36*3600: continue
            candidates.append((score,game))
    candidates.sort(key=lambda x:x[0],reverse=True)
    if candidates and (len(candidates)==1 or candidates[0][0]-candidates[1][0]>=.06): return candidates[0][1]
    return None


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
    game=x.get("score_game") or {}
    direct=[safe_logo(v) for v in (game.get("logos") or {}).values() if safe_logo(v)]
    if direct: return direct[:2]
    catalog=st.session_state.get("royal_logo_catalog",{}).get(x.get("sport"),{})
    result=[]
    for name in competitors(x):
        exact=catalog.get(clean(name))
        if exact: result.append(exact);continue
        candidates=sorted(((SequenceMatcher(None,clean(name),key).ratio(),url) for key,url in catalog.items()),reverse=True)
        if candidates and candidates[0][0]>=.91 and (len(candidates)==1 or candidates[0][0]-candidates[1][0]>=.06):
            result.append(candidates[0][1])
    return result[:2]

def competitors(x):
    names=[]
    for side in x.get("raw_sides") or []:
        name=str((side.get("team") or {}).get("name") or "").strip()
        if name and clean(name) not in [clean(v) for v in names]: names.append(name)
    return names if len(names)==2 else []

def win_record(records):
    for rec in records or []:
        summary=str(rec.get("summary") or "")
        m=re.fullmatch(r"(\d+)-(\d+)(?:-\d+)?",summary)
        if m:
            w,l=int(m.group(1)),int(m.group(2))
            if w+l>=3: return w,l
    return None

def choose_prediction(x):
    """Simple transparent records baseline, otherwise labeled market baseline.
    Never call either a validated edge; avoid unsupported invented statistics."""
    names=competitors(x)
    if len(names)!=2: return None
    game=x.get("score_game")
    if game:
        mapped=[]
        for n in names:
            hits=[t for t in game["teams"] if clean(t)==clean(n)]
            if len(hits)!=1: break
            mapped.append(hits[0])
        if len(mapped)==2:
            records=[win_record(game.get("records",{}).get(n)) for n in mapped]
            if all(records):
                # Smoothed season win-rate difference + small home advantage.
                rates=[(w+5)/(w+l+10) for w,l in records]
                margin=1.65*(rates[0]-rates[1]) + (.10 if mapped[0]==game.get("home") else -.10 if mapped[1]==game.get("home") else 0)
                p=1/(1+math.exp(-margin))
                winner=0 if p>=.5 else 1
                confidence="MODEL · LIMITED" if abs(p-.5)>=.06 else "MODEL · CLOSE"
                return {"selection":names[winner],"opponent":names[1-winner],"prob":p if winner==0 else 1-p,
                        "model":"Season-record baseline", "confidence":confidence,
                        "evidence":f"Records: {names[0]} {records[0][0]}-{records[0][1]}, {names[1]} {records[1][0]}-{records[1][1]}; modest home adjustment. Not calibrated."}
    # When team statistics are missing, still lock a directional baseline, not an asserted model edge.
    q=x.get("quote")
    yes=x.get("selection") if x.get("verified_side") else None
    if yes and q and q.get("valid") and clean(yes) in [clean(n) for n in names]:
        chosen=next(n for n in names if clean(n)==clean(yes))
        other=next(n for n in names if clean(n)!=clean(yes))
        if q["ask"] < .5: chosen,other=other,chosen
        return {"selection":chosen,"opponent":other,"prob":None,"model":"Market-favorite baseline",
                "confidence":"BASELINE · NO INDEPENDENT EDGE",
                "evidence":"Market price used to choose the favored side; not an independent sports prediction or evidence of value."}
    return {"selection":names[0],"opponent":names[1],"prob":None,"model":"Uninformed baseline",
            "confidence":"BASELINE · NO STATS", "evidence":"No usable verified team statistics or quote. First named competitor used solely as a tracked baseline."}

def lock_predictions(items):
    now=utc().isoformat()
    with db() as c:
        for x in items:
            # Never create a new prediction after the match has ended.
            game=x.get("score_game")
            if game and game["state"]=="post": continue
            pred=choose_prediction(x)
            if not pred: continue
            # V3.6: no NEW arbitrary first-team predictions. Previously locked
            # records remain untouched and available in saved history.
            if pred["model"] == "Uninformed baseline": continue
            phase="LIVE" if x.get("live") else "PREGAME"
            pid=hashlib.sha256((x["event_id"]+"|"+x["slug"]+"|"+phase).encode()).hexdigest()[:32]
            q=x.get("quote")
            ask=q["ask"] if q and q.get("valid") and x.get("verified_side") and clean(x["selection"])==clean(pred["selection"]) else None
            c.execute("""INSERT OR IGNORE INTO royal_predictions_v34
                (id,event_id,slug,sport,title,selection,opponent,phase,model,model_probability,confidence,evidence,quote_ask,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid,x["event_id"],x["slug"],x["sport"],x["title"],pred["selection"],pred["opponent"],phase,pred["model"],pred["prob"],pred["confidence"],pred["evidence"],ask,now))
            # $100 paper entry only when Queen's chosen side IS the verified YES contract.
            # No pretending a NO-side prediction has an executable quoted YES ask.
            if ask is not None and q["ask"]-q["bid"]<=.03 and "OPEN" in q["state"].upper() and .05<=ask<=.95:
                paperid=hashlib.sha256(("paper|"+pid).encode()).hexdigest()[:32]
                c.execute("""INSERT OR IGNORE INTO royal_paper_v34
                   (id,prediction_id,event_id,slug,sport,selection,stake,entry_ask,entry_at,last_bid,note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (paperid,pid,x["event_id"],x["slug"],x["sport"],pred["selection"],100.0,ask,now,q["bid"],"Indicative paper fill; no actual order, fees excluded"))
            if q and q.get("valid"):
                c.execute("UPDATE royal_paper_v34 SET last_bid=? WHERE prediction_id=? AND status='OPEN'",(q["bid"],pid))
        c.commit()

def grade_predictions(items):
    # A completed scoreboard can grade the chosen winner, not necessarily the
    # exchange's official payout (retirement, postponement etc. need separate checks).
    with db() as c:
        for x in items:
            game=x.get("score_game")
            if not game or game.get("state")!="post" or not game.get("winner"): continue
            winner=clean(game["winner"])
            teams=[clean(n) for n in game["teams"]]
            if winner not in teams: continue
            rows=c.execute("SELECT * FROM royal_predictions_v34 WHERE event_id=? AND result='PENDING'",(x["event_id"],)).fetchall()
            for r in rows:
                # Match both competitor names to scoreboard, not merely winner substring.
                if clean(r["selection"]) not in teams or clean(r["opponent"]) not in teams: continue
                result="WIN" if clean(r["selection"])==winner else "LOSS"
                c.execute("UPDATE royal_predictions_v34 SET result=?,graded_at=?,grade_note=? WHERE id=?",
                          (result,utc().isoformat(),"Provisional ESPN result; check exchange settlement",r["id"]))
                paper=c.execute("SELECT * FROM royal_paper_v34 WHERE prediction_id=? AND status='OPEN'",(r["id"],)).fetchone()
                if paper:
                    pnl=paper["stake"]*(1/paper["entry_ask"]-1) if result=="WIN" else -paper["stake"]
                    c.execute("UPDATE royal_paper_v34 SET status=?,pnl=?,graded_at=? WHERE id=?",(result,round(pnl,2),utc().isoformat(),paper["id"]))
        c.commit()

def refresh_saved_results():
    """Grade pending saved predictions even when their games are absent from today's market scan.

    Match the exact two competitors and league; only accept completed ESPN games
    occurring after the prediction was locked. Never infer exchange settlement.
    """
    with db() as c:
        pending=[dict(r) for r in c.execute("SELECT * FROM royal_predictions_v34 WHERE result='PENDING'")]
    if not pending:
        return 0, 0, "No pending saved predictions"
    today=utc().astimezone(CT).date()
    sports=sorted({r['sport'] for r in pending if r['sport'] in ESPN})
    # Restrict historical lookups to recent dates to bound network traffic.
    # Older unmatched records stay pending rather than being guessed.
    dates=[(today-timedelta(days=i)).strftime('%Y%m%d') for i in range(0,15)]
    jobs=[(sp,day) for sp in sports for day in dates]
    completed={sp:[] for sp in sports}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures={pool.submit(scores,sp,day):sp for sp,day in jobs}
        for future in as_completed(futures):
            sp=futures[future]
            try: events=future.result()
            except Exception: continue
            for e in events:
                for game in _score_records(e,sp):
                    if game['state']!='post' or not game.get('completed') or not game.get('winner'): continue
                    game_date=parse_date(game.get('date'))
                    if game_date:
                        completed[sp].append((frozenset(clean(n) for n in game['teams']),
                                              clean(game['winner']),game_date,game['event_id']))
    changed=0
    with db() as c:
        for r in pending:
            target=frozenset((clean(r['selection']),clean(r['opponent'])))
            if len(target)!=2 or not all(target): continue
            locked_at=parse_date(r['created_at'])
            if not locked_at: continue
            matches={g[3]:g for g in completed.get(r['sport'],[]) if g[0]==target and g[2]>=locked_at-timedelta(hours=2)}
            # More than one plausible finished match: do not grade ambiguously.
            if len(matches)!=1: continue
            game=next(iter(matches.values()))
            result='WIN' if clean(r['selection'])==game[1] else 'LOSS'
            before=c.total_changes
            c.execute("UPDATE royal_predictions_v34 SET result=?,graded_at=?,grade_note=? WHERE id=? AND result='PENDING'",
                      (result,utc().isoformat(),'Provisional ESPN completed game; exchange settlement NOT verified',r['id']))
            if c.total_changes==before: continue
            changed+=1
            paper=c.execute("SELECT * FROM royal_paper_v34 WHERE prediction_id=? AND status='OPEN'",(r['id'],)).fetchone()
            if paper and paper['entry_ask'] is not None and 0<float(paper['entry_ask'])<1:
                pnl=float(paper['stake'])*(1/float(paper['entry_ask'])-1) if result=='WIN' else -float(paper['stake'])
                c.execute("UPDATE royal_paper_v34 SET status=?,pnl=?,graded_at=?,note=? WHERE id=? AND status='OPEN'",
                          (result,round(pnl,2),utc().isoformat(),'Provisional ESPN result; simulated entry, fees excluded; exchange settlement unverified',paper['id']))
        # Grade saved parlay tickets from the original locked legs.
        ids=[r['id'] for r in c.execute("SELECT id FROM royal_parlays_v37")]
        parlay_changes=0
        for pid in ids:
            legs=[r[0] for r in c.execute("SELECT COALESCE(p.result,'PENDING') FROM royal_parlay_legs_v37 l LEFT JOIN royal_predictions_v34 p ON p.id=l.prediction_id WHERE l.parlay_id=? ORDER BY l.leg_number",(pid,))]
            if not legs: continue
            wins=legs.count('WIN'); losses=legs.count('LOSS')
            state='LOSS' if losses else 'WIN' if wins==len(legs) else 'PENDING'
            old=c.execute('SELECT status,wins,losses FROM royal_parlays_v37 WHERE id=?',(pid,)).fetchone()
            if old and (old['status'],old['wins'],old['losses'])!=(state,wins,losses):
                c.execute('UPDATE royal_parlays_v37 SET status=?,wins=?,losses=? WHERE id=?',(state,wins,losses,pid))
                parlay_changes+=1
        c.commit()
    return changed,parlay_changes,'ESPN final results are provisional; exchange settlements have not been independently confirmed'


def royal_results_dashboard():
    with db() as c:
        picks=c.execute("SELECT COUNT(*) n, SUM(result='WIN') w, SUM(result='LOSS') l, SUM(result='PENDING') pending FROM royal_predictions_v34").fetchone()
        papers=c.execute("SELECT COUNT(*) n, SUM(status='WIN') w, SUM(status='LOSS') l, COALESCE(SUM(CASE WHEN status IN ('WIN','LOSS') THEN pnl ELSE 0 END),0) pnl FROM royal_paper_v34").fetchone()
        parlays=[dict(r) for r in c.execute('SELECT leg_count,status,COUNT(*) n FROM royal_parlays_v37 GROUP BY leg_count,status ORDER BY leg_count,status')]
        sport_rows=[dict(r) for r in c.execute("SELECT sport,COUNT(*) total,SUM(result='WIN') wins,SUM(result='LOSS') losses FROM royal_predictions_v34 GROUP BY sport ORDER BY total DESC")]
    w,l=picks['w'] or 0,picks['l'] or 0
    st.subheader('📊 Royal Results & Unit Scoreboard')
    a,b,c,d=st.columns(4)
    a.metric('Saved picks',picks['n'])
    b.metric('Graded accuracy',f'{w/(w+l):.1%}' if w+l else '—')
    c.metric('Pending picks',picks['pending'] or 0)
    d.metric('Recorded paper P&L',f"${papers['pnl']:+,.2f}")
    st.caption(f"Graded picks: {w} WIN / {l} LOSS. Original paper trades: {papers['n']} (${100} each where recorded). Net paper units: {papers['pnl']/BASE_UNIT:+.2f} U at $20/U. Experimental suggested units are NOT additional executed trades.")
    st.caption('Results use ESPN finals, not confirmed exchange settlements. Paper P&L excludes fees, slippage and actual fills. No parlay payout is calculated without verified combo pricing.')
    with st.expander('🏆 Accuracy by sport',expanded=False):
        for r in sport_rows:
            won,lost=r['wins'] or 0,r['losses'] or 0
            st.write(f"{SPORT_ICON.get(r['sport'],'🏆')} {r['sport']}: {won} W / {lost} L · {won/(won+lost):.1%} graded accuracy" if won+lost else f"{SPORT_ICON.get(r['sport'],'🏆')} {r['sport']}: {r['total']} tracked · not graded yet")
    with st.expander('🎯 Saved parlay result counts',expanded=False):
        if not parlays: st.caption('No saved parlay tickets yet.')
        for r in parlays: st.write(f"{r['leg_count']}-leg · {r['status']}: {r['n']}")


def prediction_lookup():
    with db() as c:
        return {(r["event_id"],r["slug"],r["phase"]):dict(r) for r in c.execute("SELECT * FROM royal_predictions_v34")}

def _csv_bytes(rows):
    if not rows: return b""
    out=io.StringIO()
    writer=csv.DictWriter(out, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows([dict(r) for r in rows])
    return out.getvalue().encode("utf-8-sig")


def _restore_history(upload, table):
    """Import only known fields into the existing tables; never overwrite saved rows."""
    allowed={"royal_predictions_v34", "royal_paper_v34"}
    if table not in allowed: return 0, "Unknown history table"
    try:
        raw=upload.getvalue().decode("utf-8-sig")
        records=list(csv.DictReader(io.StringIO(raw)))
        if len(records)>20000: return 0, "Too many rows in one import"
        with db() as c:
            columns={r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            required={"id","event_id","slug","selection"}
            if table=="royal_paper_v34": required={"id","prediction_id","event_id","slug","selection","stake","entry_ask"}
            if not records or not required.issubset(set(records[0])): return 0, "This doesn't look like the correct V3.4 history CSV"
            count=0
            for row in records:
                data={k:(v if v!="" else None) for k,v in row.items() if k in columns and k!=""}
                if not all(data.get(k) for k in required): continue
                keys=list(data)
                marks=",".join("?" for _ in keys)
                cols=",".join('"'+k+'"' for k in keys)
                before=c.total_changes
                c.execute(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({marks})",[data[k] for k in keys])
                count+=c.total_changes-before
            c.commit()
        return count, None
    except (UnicodeError, ValueError, sqlite3.Error, csv.Error) as exc:
        return 0, f"Could not import CSV: {type(exc).__name__}"



# V3.8: Risk recommendation for paper tests. No model has validated edge yet.
BASE_UNIT = 20.0
def royal_unit_advice(p):
    model = str(p.get("model") or "")
    if model == "Season-record baseline":
        return 0.0, "WATCH: season-record estimates are uncalibrated"
    if model == "Market-favorite baseline":
        return 0.0, "SKIP: market favorite is not an independent edge"
    return 0.0, "SKIP: insufficient verified evidence for a stake"

def royal_profit_preview(ask, units):
    """Gross hypothetical YES-contract settlement only; never claim a fill."""
    p=numeric(ask)
    if p is None or not (0 < p < 1):
        return None
    return BASE_UNIT * units * (1.0 / p - 1.0)

def royal_paper_test_units(p):
    """An experimental stake for measuring sizing, NOT a real-money recommendation."""
    model=str(p.get("model") or "")
    probability=numeric(p.get("model_probability"))
    if model=="Season-record baseline" and probability is not None:
        if probability >= .60: return .5, "EXPERIMENTAL · stronger season-record signal"
        if probability >= .54: return .25, "EXPERIMENTAL · modest season-record signal"
    return 0.0, "WATCH ONLY · insufficient independent evidence"

def royal_under_dog_units(x):
    """Only trial a paper unit when a named YES underdog agrees with a saved pick."""
    q=x.get("quote") or {}
    ask=numeric(q.get("ask"))
    phase="LIVE" if x.get("live") else "PREGAME"
    pred=st.session_state.get("royal_locked",{}).get((x["event_id"],x["slug"],phase))
    if not pred or ask is None or not (0<ask<.5):
        return 0.0,"WATCH ONLY"
    if clean(pred.get("selection"))!=clean(x.get("selection")):
        return 0.0,"WATCH ONLY · Queen's pick is a different side"
    probability=numeric(pred.get("model_probability"))
    if pred.get("model")!="Season-record baseline" or probability is None:
        return 0.0,"WATCH ONLY · no independent estimate"
    if probability-ask>=.15 and probability>=.50:
        return .25,"EXPERIMENTAL · uncalibrated estimate exceeds quote"
    return 0.0,"WATCH ONLY · no sufficient modeled margin"

def royal_parlay_units(legs):
    """A small shadow-paper allocation for testing combos; NOT a bet recommendation."""
    n=len(legs)
    if n<2: return 0.0,"NO TEST · insufficient legs"
    # Small, fixed research budgets avoid inventing a statistical edge.
    amount={2:0.25,3:0.15,5:0.10,10:0.05}.get(n,0.0)
    return amount,"SHADOW PAPER TEST ONLY · no validated edge or executable combined price"

def royal_unit_manager():
    with db() as c:
        picks=[dict(r) for r in c.execute("SELECT * FROM royal_predictions_v34 ORDER BY created_at DESC")]
        papers=[dict(r) for r in c.execute("SELECT * FROM royal_paper_v34")]
    st.markdown("**👑 Queen's Automatic Unit Manager**")
    st.caption("Base unit $20. Queen decides units per play; no manual adjustment needed.")
    a,b,c=st.columns(3)
    a.metric("Picks evaluated",len(picks))
    b.metric("Paper-test units",f"{sum(royal_paper_test_units(p)[0] for p in picks):.2f} U")
    c.metric("1 unit","$20")
    st.caption("These are retrospective, experimental paper-test sizes, not recommended real-money wagers. No model has demonstrated a validated edge.")
    if papers:
        pnl=sum(float(p.get("pnl") or 0) for p in papers)
        st.caption(f"Existing paper trades: {len(papers)} · Recorded P&L ${pnl:+,.2f} ({pnl/BASE_UNIT:+.2f} U). Original $100 stakes are preserved, not retroactively changed.")
    with st.expander("Review existing picks and Queen's units",expanded=False):
        for p in picks[:20]:
            units,why=royal_paper_test_units(p)
            st.markdown(f"**{p.get('selection','Unknown')}** · {units:g} U · ${units*BASE_UNIT:.2f}")
            st.caption(f"{p.get('sport','Sports')} · {p.get('title','')} · {why}")
        if len(picks)>20: st.caption(f"Showing 20 of {len(picks)}; all included in totals.")
    st.caption("Retrospective unit evaluations are not actual bets. Higher unit tiers require independently validated model performance.")

def _saved_game_banner(game, result):
    result=str(result or "PENDING").upper()
    if result=="WIN": return "✅ WON · QUEEN CALLED IT", "#16a34a"
    if result=="LOSS": return "❌ LOST · QUEEN MISSED", "#dc2626"
    if game and game.get("state")=="in": return "🔴 LIVE · IN PROGRESS", "#e11d48"
    if game and game.get("state")=="post": return "🏁 FINAL · AWAITING GRADING", "#d97706"
    if game and game.get("state")=="pre": return "📅 UPCOMING · NOT STARTED", "#64748b"
    return "⏳ PENDING · SCORE STATUS UNVERIFIED", "#a16207"

def _render_saved_score(game, row):
    if not game:
        st.caption("🕒 Scoreboard unavailable or unmatched · no verified score or clock · original pick remains saved")
        return
    st.markdown(f"**{royal_game_clock(game,game.get('date'))}**")
    line=royal_score_line(game,row.get("selection"))
    if line: st.markdown(line)
    elif game.get("state")=="in": st.caption("Live match found · score unavailable from ESPN")
    if row.get("sport")=="Tennis" and game.get("state") in ("in","post"):
        sets=game.get("sets") or {}
        names=game.get("teams") or []
        if len(names)==2 and (sets.get(names[0]) or sets.get(names[1])):
            st.caption("🎾 Sets: "+" | ".join(f"{n}: {'–'.join(sets.get(n) or ['—'])}" for n in names))
        if game.get("state")=="in":
            st.caption("Tennis has no game clock. Current game points appear only if the score provider supplies them.")

def _pick_card(r, paper=None, score_index_data=None):
    sport=r["sport"] or "Sports"
    status=r["result"] or "PENDING"
    icon=SPORT_ICON.get(sport,"🏆")
    paper_status=paper["status"] if paper else None
    game=_match_saved_game(r,score_index_data or {})
    with st.container(border=True):
        banner,color=_saved_game_banner(game,status)
        st.markdown(f'<div style="background:{color}20;border:1px solid {color};border-radius:10px;padding:7px 10px;font-weight:800">{banner}</div>',unsafe_allow_html=True)
        _render_saved_score(game,r)
        left,right=st.columns([3,1],gap="small")
        with left:
            st.markdown(f"**{icon} {r['title'] or (r['selection']+' vs. '+str(r['opponent'] or 'Opponent'))}**")
            st.caption(f"{sport} · {r['phase'] or 'PREGAME'} · Moneyline prediction")
        with right:
            if status=="WIN": st.success("WON")
            elif status=="LOSS": st.error("LOST")
            else: st.warning("PENDING")
        st.markdown(f"**👑 QUEEN'S PICK: {r['selection']} TO WIN**")
        units,why=royal_paper_test_units(r)
        st.caption(f"Experimental paper test: {units:g} U · ${units*BASE_UNIT:.2f} · {why}")
        st.caption(f"🔒 Locked {local(r['created_at'])} · {r['model'] or 'Prediction method not saved'}")
        if paper:
            price=paper["entry_ask"]
            price_text=f" · Entry {price*100:.1f}¢" if price is not None else ""
            st.markdown(f"💰 **$100 PAPER TRADE: {paper_status}**{price_text}")
            if paper_status in ("WIN","LOSS") and paper["pnl"] is not None:
                st.markdown(f"**Simulated P&L: ${paper['pnl']:+,.2f}**")
            elif paper_status=="OPEN":
                st.caption("Paper result pending · indicative entry, not a real order")
        else:
            st.caption("📝 Prediction tracked · No qualifying $100 paper trade for this pick")
        with st.expander("Why Queen picked this · More details"):
            st.write("Prediction method:",r["model"] or "Not recorded")
            st.write("Reason saved at lock:",r["evidence"] or "No explanation saved")
            if r["model_probability"] is not None:
                st.write("Uncalibrated model estimate:",f"{r['model_probability']:.1%}")
            st.write("Market:",r["slug"])
            st.write("Locked prediction result:",status)
            if paper:
                st.write("Paper contract:",paper["slug"])
                st.write("Paper entry:",f"${paper['stake']:.2f} at {paper['entry_ask']:.4f}" if paper["entry_ask"] is not None else "Unavailable")
                st.write("Paper note:",paper["note"] or "No note")
            if r["grade_note"]: st.caption(r["grade_note"])


def prediction_panel():
    st.subheader("👑 Saved Pick History")
    with db() as c:
        # No 150-row limit on statistics: historical totals must reflect all saved picks.
        totals=c.execute("SELECT COUNT(*), SUM(result='WIN'), SUM(result='LOSS') FROM royal_predictions_v34").fetchone()
        ptotals=c.execute("SELECT COUNT(*), SUM(status='WIN'), SUM(status='LOSS'), COALESCE(SUM(pnl),0) FROM royal_paper_v34").fetchone()
        rows=[dict(r) for r in c.execute("SELECT * FROM royal_predictions_v34 ORDER BY created_at DESC, id DESC")]
        papers=[dict(r) for r in c.execute("SELECT * FROM royal_paper_v34 ORDER BY entry_at DESC, id DESC")]
    wins,losses=totals[1] or 0,totals[2] or 0
    pw,pl=ptotals[1] or 0,ptotals[2] or 0
    a,b,c,d=st.columns(4)
    a.metric("Locked picks",totals[0]);b.metric("Pick accuracy",f"{100*wins/(wins+losses):.0f}%" if wins+losses else "—")
    c.metric("$100 paper entries",ptotals[0]);d.metric("Settled paper P&L",f"${ptotals[3]:+,.2f}")
    st.caption(f"Predictions: {wins} won / {losses} lost · Paper trades: {pw} won / {pl} lost · Results are provisional until exchange settlement is checked.")
    filter_choice=st.radio("Show history",["All Picks","Paper Trades","Results"],horizontal=True,key="royal_history_filter")
    paper_by_prediction={r["prediction_id"]:r for r in papers}
    if filter_choice=="Paper Trades":
        filtered=[r for r in rows if r["id"] in paper_by_prediction]
    elif filter_choice=="Results":
        filtered=[r for r in rows if r["result"] in ("WIN","LOSS","VOID","PUSH")]
    else:
        filtered=rows
    st.caption(f"Showing {min(len(filtered),st.session_state.get('royal_history_limit',12))} of {len(filtered)} saved picks · Newest first")
    limit=st.session_state.get("royal_history_limit",12)
    shown=filtered[:limit]
    # Fetch once for visible saved picks, even if those events are absent from the
    # current Polymarket market scan. No invented live status for unmatched games.
    history_sports={r.get("sport") for r in shown if r.get("sport") in ESPN}
    history_scores=score_index(sorted(history_sports), [r.get("created_at") for r in shown]) if history_sports else {}
    for r in shown:
        _pick_card(r,paper_by_prediction.get(r["id"]),history_scores)
    with st.expander("🔎 Score Feed Diagnostics (optional)", expanded=False):
        st.caption("ESPN score data is separate from Polymarket prices. Unmatched does not mean a game is over or canceled.")
        for sport in sorted(history_sports):
            found=[g for (league,_),g in history_scores.items() if league==sport]
            visible=[r for r in shown if r.get('sport')==sport]
            matched=sum(_match_saved_game(r,history_scores) is not None for r in visible)
            st.write(f"{sport}: {len(found)} score records · {matched}/{len(visible)} visible picks matched")
        st.caption("Tennis coverage may exclude Challenger tournaments. A verified provider is required before grading an unmatched match.")
    if len(filtered)>limit:
        if st.button("Show 20 more saved picks",use_container_width=True):
            st.session_state["royal_history_limit"]=limit+20
            st.rerun()
    if not filtered: st.info("No picks in this filter yet. Try All Picks.")
    with st.expander("💾 Back up or restore locked picks — no deletions",expanded=False):
        st.write("Save both CSV files to your phone or computer. These contain the original locked predictions and paper trades, including entry prices and timestamps.")
        st.download_button("⬇️ Download all locked picks (CSV)",_csv_bytes(rows),"royal_locked_picks_v34_backup.csv","text/csv",disabled=not rows,use_container_width=True)
        st.download_button("⬇️ Download all paper trades (CSV)",_csv_bytes(papers),"royal_paper_trades_v34_backup.csv","text/csv",disabled=not papers,use_container_width=True)
        st.caption("Restore adds missing original records without changing existing saved picks. Upload the picks CSV first, then paper trades CSV.")
        up1=st.file_uploader("Restore locked picks CSV",type=["csv"],key="restore_royal_predictions")
        if up1 is not None and st.button("Import missing locked picks",key="restore_preds"):
            count,err=_restore_history(up1,"royal_predictions_v34")
            if err: st.error(err)
            else: st.success(f"Restored {count} missing picks. Existing picks unchanged.")
        up2=st.file_uploader("Restore paper trades CSV",type=["csv"],key="restore_royal_papers")
        if up2 is not None and st.button("Import missing paper trades",key="restore_papers"):
            count,err=_restore_history(up2,"royal_paper_v34")
            if err: st.error(err)
            else: st.success(f"Restored {count} missing paper trades. Existing entries unchanged.")
    st.caption("Important: Streamlit Community Cloud local SQLite can be lost on restart or redeploy. This code does not delete saved history, but cannot guarantee that the host retains its database. Backups or persistent storage are necessary.")

def royal_game_clock(game, start=None):
    if not game:
        when=local(start)
        return "📅 Scheduled · "+when if when!="Time unconfirmed" else "🕒 Start time unconfirmed"
    state=game.get("state")
    detail=str(game.get("detail") or "").strip()
    when=local(game.get("date") or start)
    if state=="in": return "🔴 LIVE"+(" · "+detail if detail else " · Clock unavailable")
    if state=="post": return "🏁 FINAL"+(" · "+detail if detail.lower() not in ("","final") else "")
    if state=="pre": return "📅 "+when
    return "🕒 "+when+" · Status unconfirmed"

def royal_score_line(game, selection=None):
    if not game or game.get("state") not in ("in","post"): return None
    if game.get("sets") and game.get("source") == "ESPN":
        return None  # Tennis set scores are displayed separately, never as team points.
    teams=game.get("teams") or []
    if len(teams)!=2: return None
    a,b=teams
    scores=game.get("scores") or {}
    sa,sb=scores.get(a),scores.get(b)
    if sa is None or sb is None or str(sa).strip()=="" or str(sb).strip()=="": return None
    line=f"🏟️ {a} {sa} — {sb} {b}"
    if selection and game.get("state")=="in":
        try:
            aa,bb=float(sa),float(sb)
            delta=(aa-bb) if clean(selection)==clean(a) else (bb-aa) if clean(selection)==clean(b) else None
            if delta is not None: line += " · Queen's team "+("LEADING" if delta>0 else "TRAILING" if delta<0 else "TIED")
        except (ValueError,TypeError): pass
    return line

def display_row(x, number=None):
    q = x.get("quote")
    sport = x.get("sport", "Sports")
    icon = SPORT_ICON.get(sport, "🏆")
    title = html.escape(str(x.get("title") or "Sports match"))
    logos = game_logos(x)
    logo_html = "".join(f'<img src="{html.escape(u,quote=True)}" class="team-logo" alt="team logo">' for u in logos)
    valid = bool(q and q.get("valid"))
    verified = bool(x.get("verified_side"))
    state = "🔴 LIVE" if x.get('score_game') and x['score_game'].get('state')=='in' else "UPCOMING" if x.get('score_game') and x['score_game'].get('state')=='pre' else "STATUS UNVERIFIED"
    locked = st.session_state.get("royal_locked",{}).get((x["event_id"],x["slug"],"LIVE" if x.get("live") else "PREGAME"))
    # Predictions are distinct from verified contract quotes and recommended trades.
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
    if locked:
        action = "PENDING" if locked["result"]=="PENDING" else locked["result"]
        main = f"👑 FORECAST: {html.escape(locked['selection'])} to WIN"
        secondary = f"{html.escape(locked['confidence'])} · {secondary}"
        units,unit_reason=royal_paper_test_units(locked)
        secondary+=f" · PAPER {units:g} U (${units*BASE_UNIT:.0f})"
        if valid and verified and clean(x.get("selection"))==clean(locked.get("selection")):
            profit=royal_profit_preview(q["ask"],units)
            if profit is not None and units>0:
                secondary+=f" · WIN +${profit:.2f} / LOSS -${units*BASE_UNIT:.2f}"
        elif units>0:
            secondary+=" · payout unverified"
    with st.container(border=True):
        game=x.get("score_game")
        # Prominent, mutually exclusive status: grading is authoritative only for
        # a locked prediction, while live score is an observation, not a win.
        outcome = str(locked.get("result") or "PENDING").upper() if locked else "UNTRACKED"
        if outcome == "WIN":
            status_text, status_color = "✅ WON · QUEEN CALLED IT", "#16a34a"
        elif outcome == "LOSS":
            status_text, status_color = "❌ LOST · QUEEN MISSED", "#dc2626"
        elif game and game.get("state") == "in":
            status_text, status_color = "🔴 LIVE · IN PROGRESS", "#e11d48"
        elif game and game.get("state") == "post":
            status_text, status_color = "⏳ FINAL · AWAITING VERIFIED GRADING", "#d97706"
        elif locked:
            status_text, status_color = "⏳ PENDING · GAME NOT FINAL", "#a16207"
        else:
            status_text, status_color = "👀 WATCH · NO LOCKED FORECAST", "#64748b"
        st.markdown(
            '<div style="background:'+status_color+'20;border:1px solid '+status_color+
            ';border-radius:10px;padding:9px 12px;margin-bottom:7px;'
            'font-size:1.06rem;font-weight:800;color:inherit;">'+status_text+'</div>',
            unsafe_allow_html=True,
        )
        st.markdown(f"**{royal_game_clock(game, x.get('start'))}**")
        score_line=royal_score_line(game,locked.get("selection") if locked else None)
        if score_line: st.markdown(score_line)
        elif game and game.get("state")=="in": st.caption("Live scoreboard connected · score currently unavailable")
        if sport=="Tennis" and game and game.get("state") in ("in","post"):
            sets=game.get("sets") or {}
            names=game.get("teams") or []
            if len(names)==2 and (sets.get(names[0]) or sets.get(names[1])):
                st.caption("🎾 Sets: "+" | ".join(f"{n}: {'–'.join(sets.get(n) or ['—'])}" for n in names))
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
            if locked:
                st.write("Locked pick:",locked["selection"])
                st.write("Prediction method:",locked["model"])
                st.write("Why:",locked["evidence"])
                st.write("Prediction status:",locked["result"])
            st.write("Legacy paper-test rule:", paper_reason(x))
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
st.caption("V4.6 · IMPROVED LIVE SCORE MATCHING & PARLAY CHECKLIST · POLYMARKET US · PAPER ONLY · $20 BASE UNIT")

# Saved records load without a new market scan.
with db() as _quick_db:
    _qp=_quick_db.execute("SELECT COUNT(*) FROM royal_predictions_v34").fetchone()[0]
    _qt=_quick_db.execute("SELECT COUNT(*),COALESCE(SUM(pnl),0) FROM royal_paper_v34").fetchone()
    _ql=_quick_db.execute("SELECT COUNT(*) FROM royal_parlays_v37").fetchone()[0]
st.markdown("#### 👑 Royal Dashboard")
_q1,_q2,_q3,_q4=st.columns(4)
_q1.metric("Saved picks",_qp)
_q2.metric("Paper trades",_qt[0])
_q3.metric("Parlays",_ql)
_q4.metric("Paper P&L",f"${_qt[1]:+,.2f}")
# One simple refresh button. Automatic updates run only while the app session is active.
if st.button("👑 Refresh Queen", type="primary", use_container_width=True):
    api.clear()
    scores.clear()
    st.rerun()
st.caption("Score source: ESPN where available. The Odds API is not connected in this version; its key alone does not supply live scores. Tennis coverage may be incomplete.")
st.caption("Live market scans refresh about every 2 minutes while the page is active; ESPN scores are cached for up to 60 seconds and saved finals are checked about every 10 minutes. Updates pause when Streamlit sleeps. Tap Refresh Queen for a manual check.")

@st.fragment(run_every="600s")
def royal_auto_results():
    with st.spinner("Checking saved game results..."):
        try:
            changed, parlay_changes, _ = refresh_saved_results()
            st.caption(f"Results checked {utc().astimezone(CT).strftime('%I:%M %p CT')} · {changed} new graded picks · {parlay_changes} parlay updates")
        except Exception as exc:
            st.warning(f"Results check unavailable ({type(exc).__name__}); saved picks unchanged.")
    royal_results_dashboard()

royal_auto_results()

with st.expander("📚 History, units & backups", expanded=False):
    royal_unit_manager()
    st.caption("0 U = SKIP / WATCH: no verified edge. Experimental paper units are not real-money recommendations.")
    prediction_panel()
    with db() as _backup_db:
        _parlay_tickets=[dict(r) for r in _backup_db.execute("SELECT * FROM royal_parlays_v37")]
        _parlay_legs=[dict(r) for r in _backup_db.execute("SELECT * FROM royal_parlay_legs_v37")]
    st.markdown("**🎯 Parlay backups**")
    st.download_button("⬇️ Parlay tickets CSV", _csv_bytes(_parlay_tickets), "royal_parlays_v37_backup.csv", "text/csv", disabled=not _parlay_tickets, use_container_width=True)
    st.download_button("⬇️ Parlay legs CSV", _csv_bytes(_parlay_legs), "royal_parlay_legs_v37_backup.csv", "text/csv", disabled=not _parlay_legs, use_container_width=True)

# Automatic discovery covers live games and the next three calendar days.
# One stable daily session prevents duplicate parlays from changing filters.
max_quotes = 60
max_leagues = len(LEAGUES)
sport = "All Sports"
window = "Next 3 Days"

@st.fragment(run_every="120s")
def royal_auto_scan():
    priority = ["NCAAB", "NCAAF", "NBA", "NFL", "NHL", "Tennis", "MLB", "WNBA", "MLS", "EPL", "UFC"]
    selected = priority[:max_leagues] if sport == "All Sports" else [sport]
    with st.spinner("Finding Polymarket US sports events and checking market quotes..."):
        index = score_index(selected)
        logo_catalog={}
        for (league,_),game in index.items():
            for team,url in game.get("logos",{}).items():
                if safe_logo(url): logo_catalog.setdefault(league,{})[clean(team)]=url
        st.session_state["royal_logo_catalog"]=logo_catalog
        all_events, errors = [], []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures={pool.submit(events_for,league):league for league in selected}
            for future in as_completed(futures):
                league=futures[future]
                try: items,err=future.result()
                except Exception as exc: items,err=[],type(exc).__name__
                if err: errors.append(f"{league}: {err}")
                all_events.extend((league,e) for e in items)

    current = utc().astimezone(CT).date()
    def in_window(s, e, game):
        live = bool(e.get("live") or (e.get("eventState") or {}).get("live") or str(e.get("status", "")).lower() in ("live", "in_progress", "inprogress") or (game and game["state"] == "in"))
        if live: return True
        if window == "🔴 Live": return live
        date = parse_date(e.get("eventDate") or e.get("startTime") or e.get("startDate") or (game.get("date") if game else None))
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
            verified_team, side_reason = identify_yes_team(m)
            mapping_source = "event market" if verified_team else "not checked"
            selection = verified_team or (m.get("titleShort") or m.get("question") or m.get("title") or slug)
            market_type = str(m.get("sportsMarketTypeV2") or m.get("sportsMarketType") or "Market").replace("SPORTS_MARKET_TYPE_", "").replace("_", " ").title()
            rows.append({"event_id":str(e.get("id")), "sport":s, "title":str(e.get("title") or "Sports event"),
                         "slug":str(slug), "selection":str(selection), "market_type":market_type, "verified_side":bool(verified_team), "side_reason":side_reason, "mapping_source":mapping_source, "raw_sides":sides, "market_raw":m, "event_raw":e,
                         "start":(game.get("date") if game else None) or e.get("eventDate") or e.get("startTime") or e.get("startDate"),
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
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures={pool.submit(bbo,x["slug"]):x for x in checked}
        for future in as_completed(futures):
            x=futures[future]
            try: x["quote"],x["error"]=future.result()
            except Exception as exc: x["quote"],x["error"]=None,type(exc).__name__
            if x["quote"] and x["quote"]["valid"]: quoted+=1
    # Limit extra detailed contract checks to quoted markets, avoiding hundreds of lookups.
    unmapped=[x for x in checked if not x["verified_side"] and x.get("quote") and x["quote"].get("valid")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(resolve_contract,x["market_raw"]):x for x in unmapped[:20]}
        for future in as_completed(futures):
            x=futures[future]
            try: name,reason,source=future.result()
            except Exception: continue
            if name:
                x["selection"],x["verified_side"]=name,True
                x["side_reason"],x["mapping_source"]=reason,source
    observations(checked)
    # Lock all discovered eligible named two-competitor markets, including those beyond
    # the quote budget. Paper entries require actual checked valid quotes.
    lock_predictions(rows)
    grade_predictions(rows)
    st.session_state["royal_locked"] = prediction_lookup()
    st.caption(f"Market scan updated {utc().astimezone(CT).strftime('%I:%M %p CT')}")
    # Keep historical V3.3 paper tests intact; new pick-linked paper tests live in V3.4 tables.




    st.subheader("👑 Queen’s Featured Picks")
    st.caption("Up to five tracked forecasts with the strongest available evidence. These are not verified betting edges or buy recommendations.")
    watch = [x for x in rows if (x["event_id"],x["slug"],"LIVE" if x.get("live") else "PREGAME") in st.session_state.get("royal_locked",{})]

    def evidence_rank(x):
        locked = st.session_state["royal_locked"].get((x["event_id"],x["slug"],"LIVE" if x.get("live") else "PREGAME"), {})
        model = locked.get("model", "")
        tier = 3 if model == "Season-record baseline" else 2 if model == "Market-favorite baseline" else 1
        verified = bool(x.get("verified_side"))
        valid = bool(x.get("quote") and x["quote"].get("valid"))
        return (-tier, -int(bool(x.get("score_game"))), -int(verified and valid), parse_date(x.get("start")) or datetime.max.replace(tzinfo=timezone.utc))

    watch.sort(key=evidence_rank)
    featured, seen_events = [], set()
    for x in watch:
        if x["event_id"] in seen_events: continue
        locked = st.session_state["royal_locked"].get((x["event_id"],x["slug"],"LIVE" if x.get("live") else "PREGAME"), {})
        if locked.get("model") == "Uninformed baseline": continue
        featured.append(x)
        seen_events.add(x["event_id"])
        if len(featured) >= 5: break

    if featured:
        for offset in range(0,len(featured),2):
            col1,col2=st.columns(2,gap="small")
            with col1: display_row(featured[offset],offset+1)
            if offset+1<len(featured):
                with col2: display_row(featured[offset+1],offset+2)
    else:
        st.info("No evidence-backed featured picks found in this scan. Queen says WAIT rather than guess.")

    st.caption("Season-record estimates are uncalibrated. Market-favorite selections repeat market sentiment and are not independent forecasts.")
    with st.expander(f"📋 Other tracked games ({max(0,len(watch)-len(featured))})", expanded=False):
        other = [x for x in watch if x not in featured]
        st.caption("These are saved or monitored, but not highlighted as stronger predictions. Some older records use uninformed baselines.")
        for x in other[:20]:
            display_row(x)
        if len(other)>20: st.caption(f"Showing 20 of {len(other)} other games. Saved history below includes all recorded picks.")

    # V3.7: Separate, automatically locked prediction combinations. They are NOT
    # exchange parlay contracts, verified quotes, simulated fills, or priced trades.
    def royal_parlay_candidates():
        candidates = []
        seen = set()
        for x in sorted(watch, key=evidence_rank):
            phase = "LIVE" if x.get("live") else "PREGAME"
            r = st.session_state["royal_locked"].get((x["event_id"], x["slug"], phase))
            if not r or r.get("result") != "PENDING" or r.get("model") == "Uninformed baseline":
                continue
            # One side of one game per parlay. Do not put contradictory or repeated
            # markets for the same event into the same ticket.
            if x["event_id"] in seen: continue
            seen.add(x["event_id"])
            candidates.append(dict(r))
        return candidates

    def royal_lock_parlays(candidates, session_key):
        """Create each available size once per session; no reroll on rescans."""
        created = []
        with db() as c:
            for n in (2,3,5,10):
                if len(candidates) < n: continue
                pid = hashlib.sha256(("royal-parlay-v37|"+session_key+"|"+str(n)).encode()).hexdigest()[:32]
                before = c.total_changes
                c.execute("INSERT OR IGNORE INTO royal_parlays_v37 (id,session_key,leg_count,created_at,note) VALUES (?,?,?,?,?)",
                          (pid,session_key,n,utc().isoformat(),"Automatic paper-only prediction combo. No executable parlay price, payout or edge verified."))
                if c.total_changes == before: continue
                for i,r in enumerate(candidates[:n],1):
                    c.execute("""INSERT OR IGNORE INTO royal_parlay_legs_v37
                       (parlay_id,leg_number,prediction_id,event_id,sport,title,selection,model)
                       VALUES (?,?,?,?,?,?,?,?)""",
                       (pid,i,r["id"],r["event_id"],r["sport"],r["title"],r["selection"],r["model"]))
                created.append(n)
            c.commit()
        return created

    def royal_grade_parlays():
        """Link each leg to the original immutable prediction's graded result."""
        with db() as c:
            ids=[r["id"] for r in c.execute("SELECT id FROM royal_parlays_v37 WHERE status='PENDING'")]
            for pid in ids:
                results=[r[0] for r in c.execute("""SELECT COALESCE(p.result,'PENDING')
                    FROM royal_parlay_legs_v37 l LEFT JOIN royal_predictions_v34 p ON p.id=l.prediction_id
                    WHERE l.parlay_id=? ORDER BY l.leg_number""",(pid,))]
                if not results: continue
                wins=results.count("WIN")
                losses=results.count("LOSS")
                # One lost leg loses the combo, even if other legs are pending.
                # A fully won combo requires every leg to have a WIN grade.
                status="LOSS" if losses else "WIN" if wins==len(results) else "PENDING"
                c.execute("UPDATE royal_parlays_v37 SET wins=?,losses=?,status=? WHERE id=?",(wins,losses,status,pid))
            c.commit()

    def royal_parlay_history():
        with db() as c:
            tickets=[dict(r) for r in c.execute("SELECT * FROM royal_parlays_v37 ORDER BY created_at DESC")]
            legs={}
            for r in c.execute("""SELECT l.*,COALESCE(p.result,'PENDING') AS leg_result
                  FROM royal_parlay_legs_v37 l LEFT JOIN royal_predictions_v34 p ON p.id=l.prediction_id
                  ORDER BY l.parlay_id,l.leg_number"""):
                legs.setdefault(r["parlay_id"],[]).append(dict(r))
        return tickets,legs

    # Session is local calendar date + scan window + sport selector; a repeated scan
    # never changes the picks already locked for that session.
    royal_session=f"{current.isoformat()}|{window}|{sport}"
    royal_candidates=royal_parlay_candidates()
    royal_new=royal_lock_parlays(royal_candidates,royal_session)
    royal_grade_parlays()
    royal_tickets,royal_legs=royal_parlay_history()

    st.subheader("🔥 Queen's Underdog Sniper")
    dogs=[x for x in watch if x.get("verified_side") and x.get("quote") and x["quote"].get("valid") and x["quote"]["ask"]<.5]
    if dogs:
        for x in sorted(dogs,key=lambda a:a["quote"]["ask"])[:5]:
            ask=x["quote"]["ask"]
            recommended,reason=royal_under_dog_units(x)
            # Separate deliberately small shadow experiments from Queen's own pick.
            # A contrary-side longshot must NEVER be called a recommended bet.
            u=recommended if recommended>0 else .10
            shadow=recommended==0
            profit=royal_profit_preview(ask,u)
            per_unit=royal_profit_preview(ask,1.0)
            with st.container(border=True):
                dog_game=x.get("score_game")
                st.markdown(f"**{SPORT_ICON.get(x['sport'],'🏆')} {html.escape(str(x['selection']))}** · {ask*100:.1f}¢ YES")
                st.caption(royal_game_clock(dog_game,x.get("start")))
                dog_score=royal_score_line(dog_game,x.get("selection"))
                if dog_score: st.caption(dog_score)
                if shadow:
                    st.markdown(f"**Queen: 0 U · WATCH ONLY** · {reason}")
                    st.caption(f"Optional shadow-paper test: {u:g} U (${u*BASE_UNIT:.2f}) · NOT a recommended wager")
                else:
                    st.markdown(f"**Experimental paper units: {u:g} U · ${u*BASE_UNIT:.2f}** · {reason}")
                if u>0 and profit is not None:
                    st.caption(f"Shadow test if YES wins: +${profit:,.2f} gross · If wrong: -${u*BASE_UNIT:,.2f} · Per 1 U gross: +${per_unit:,.2f}")
                else:
                    st.caption(f"Per 1 U ($20) hypothetical gross win profit: +${per_unit:,.2f} · Actual allocation $0")
                st.caption("Hypothetical quote-based payout before fees. No fill, wager, or guaranteed value. Shadow tests are NOT Queen's picks.")
    else:
        st.caption("No verified lower-priced YES contracts in this scan.")
    st.caption("Low prices are not proof of value. Experimental allocations are for paper evaluation, not betting advice.")

    st.subheader("🎯 Queen’s Royal Parlays")
    with st.container():
        st.markdown("**👑 Automatic Royal Parlay Builder**")
        st.caption("Queen automatically locks the best available tracked predictions once per scan session. Choose a bubble to view your ticket. No manual picking required.")
        selected_legs=st.radio("Choose your parlay",[2,3,5,10],horizontal=True,
                               format_func=lambda n:{2:"👑 2 Legs",3:"👑 3 Legs",5:"👑 5 Legs",10:"🎰 10 Lotto"}[n],
                               key="royal_parlay_size_v37")
        if selected_legs==10:
            st.caption("🎰 Royal Lotto: all 10 selections must win. High-risk experimental combination.")
        ticket=next((t for t in royal_tickets if t["session_key"]==royal_session and t["leg_count"]==selected_legs),None)
        if ticket:
            tlegs=royal_legs.get(ticket["id"],[])
            status=ticket["status"]
            st.markdown(f"**{'🟢 WON' if status=='WIN' else '🔴 LOST' if status=='LOSS' else '🟡 PENDING'} · {selected_legs}-leg {'Royal Lotto' if selected_legs==10 else 'Royal Parlay'}**")
            st.caption(f"🔒 Locked {local(ticket['created_at'])} · ✅ {ticket['wins']}/{len(tlegs)} won · ❌ {ticket['losses']} lost · ⏳ {len(tlegs)-ticket['wins']-ticket['losses']} pending")
            pu,preason=royal_parlay_units(tlegs)
            st.markdown(f"**👑 Experimental parlay paper stake: {pu:g} U · ${pu*BASE_UNIT:.2f} | Potential profit: unavailable**")
            st.caption(preason + " · This is a research budget, not a recommendation to wager. No purchasable parlay or combined payout verified.")
            game_by_event={x["event_id"]:x for x in rows if x.get("score_game")}
            with db() as _leg_db:
                _saved_leg_rows={r['id']:dict(r) for r in _leg_db.execute('SELECT * FROM royal_predictions_v34 WHERE id IN ('+','.join('?' for _ in tlegs)+')',[leg['prediction_id'] for leg in tlegs])} if tlegs else {}
            _leg_sports=sorted({r.get('sport') for r in _saved_leg_rows.values() if r.get('sport') in ESPN})
            _leg_scores=score_index(_leg_sports,[r.get('created_at') for r in _saved_leg_rows.values()]) if _leg_sports else {}
            with st.container(border=True):
                for leg in tlegs:
                    icon=SPORT_ICON.get(leg["sport"],"🏆")
                    matched=game_by_event.get(str(leg.get("event_id")))
                    saved_leg=_saved_leg_rows.get(leg['prediction_id'])
                    game=(matched.get('score_game') if matched else None) or (_match_saved_game(saved_leg,_leg_scores) if saved_leg else None)
                    result=leg["leg_result"]
                    mark="✅" if result=="WIN" else "❌" if result=="LOSS" else "🔴" if game and game.get("state")=="in" else "⏳"
                    timing=royal_game_clock(game, matched.get('start') if matched else (saved_leg.get('created_at') if saved_leg else None)) if game else '🕒 Scoreboard not matched · time unverified' 
                    st.markdown(f"{mark} **{icon} {leg['selection']}** · {timing}")
                    score_line=royal_score_line(game,leg["selection"])
                    if score_line: st.caption(score_line)
            st.caption("Checks update from matched ESPN final scores. Live games show 🔴 until graded; locked picks stay unchanged.")
        else:
            st.info(f"Only {len(royal_candidates)} eligible tracked games in this scan; {selected_legs} different games are required. Queen won't invent extra legs.")
        with st.expander("ℹ️ Parlay limitations",expanded=False):
            st.caption("Locked prediction combinations are not purchasable Polymarket US parlays. No verified combined odds, payout or paper P&L. Season-record estimates are uncalibrated and market-favorite baselines are not independent edges.")
    with st.expander("ℹ️ Paper testing & data storage",expanded=False):
        st.caption("No real orders. Paper results do not establish profitability. Local SQLite on Streamlit Community Cloud may be lost on restart/redeploy; export backups or use persistent storage.")

royal_auto_scan()
