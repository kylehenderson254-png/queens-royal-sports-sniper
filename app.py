import os, re, sqlite3, hashlib, html, math, csv, io, zlib, base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote
from difflib import SequenceMatcher
import requests
import time
import threading
import streamlit as st

st.set_page_config(page_title="ROYAL SPORTS SNIPER V7.8.5 | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
CT = ZoneInfo("America/Chicago")
BASE = "https://gateway.polymarket.us"
LEAGUES = {"NBA":"nba", "NFL":"nfl", "NCAAB":"ncaab", "NCAAF":"cfb", "NHL":"nhl", "MLB":"mlb", "WNBA":"wnba", "MLS":"mls", "EPL":"epl", "UFC":"ufc", "Tennis":"tennis/all"}
ESPN = {"NBA":"basketball/nba", "NFL":"football/nfl", "NCAAB":"basketball/mens-college-basketball", "NCAAF":"football/college-football", "NHL":"hockey/nhl", "MLB":"baseball/mlb", "WNBA":"basketball/wnba", "MLS":"soccer/usa.1", "EPL":"soccer/eng.1", "Tennis":"tennis/all"}
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

def _royal_tennis_seed(competitor):
    """Only provider-reported tournament seeds/ranks, not inferred strength."""
    if not isinstance(competitor,dict): return None
    athlete=competitor.get('athlete') or {}
    if not isinstance(athlete,dict): athlete={}
    values=[competitor.get('seed'), competitor.get('tournamentSeed'),
            competitor.get('curatedRank'), athlete.get('seed'),athlete.get('rank')]
    for raw in values:
        if isinstance(raw,dict):raw=raw.get('current') or raw.get('rank') or raw.get('value')
        number=numeric(raw)
        if number is not None and number.is_integer() and 1<=number<=128:
            return int(number)
    return None

def _provider_image_url(value):
    """Read an actual provider-supplied image URL; never synthesize a helmet URL."""
    if isinstance(value, str):
        return safe_logo(value) if 'safe_logo' in globals() else (value if value.startswith('https://') else '')
    if isinstance(value, dict):
        for key in ('href', 'url', 'src'):
            item = value.get(key)
            if isinstance(item, str) and item.startswith('https://'):
                return item
    if isinstance(value, list):
        for item in value:
            url = _provider_image_url(item)
            if url: return url
    return ''


def _team_provider_picture(competitor, image_type='logo'):
    team=(competitor or {}).get('team') or {}
    if not isinstance(team, dict): return ''
    if image_type == 'helmet':
        # Only true, explicitly supplied helmet assets count as helmet art.
        for key in ('helmetImage', 'helmet', 'helmetLogo'):
            url=_provider_image_url(team.get(key))
            if url:return url
        return ''
    return (_provider_image_url(team.get('logo')) or
            _provider_image_url(team.get('logos')))


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
            "teams":names,"abbr":{n:str((p.get("team") or {}).get("abbreviation") or "").lower() for n,p in zip(names,participants)},"scores":scores,"sets":sets,
            "winner":next((n for n,p in zip(names,participants) if p.get("winner") is True),None),
            "logos":{n:_team_provider_picture(p) for n,p in zip(names,participants)},
            "helmets":{n:_team_provider_picture(p,'helmet') for n,p in zip(names,participants)},
            "team_colors":{n:(p.get("team") or {}).get("color","") for n,p in zip(names,participants)},
            "records":{n:p.get("records") or [] for n,p in zip(names,participants)},
            "tennis_seed":{n:_royal_tennis_seed(p) for n,p in zip(names,participants)} if sport == "Tennis" else {},
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
    if len(aa)>=2 and len(bb)>=2 and aa[-1]==bb[-1] and len(aa[-1])>=4: return .94
    return SequenceMatcher(None,clean(a),clean(b)).ratio()

def _unique_score_team(name, teams, sport):
    """Resolve abbreviations against ONLY the two contestants in this specific game."""
    scorer = _person_name_match if sport == "Tennis" else _team_name_match
    ranked = sorted(((scorer(name, t), t) for t in teams), reverse=True)
    if not ranked or ranked[0][0] < .90:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .06:
        return None
    return ranked[0][1]


def royal_secret(key):
    try: return os.getenv(key) or st.secrets.get(key, '')
    except Exception: return os.getenv(key, '')

FOCUS=('NBA','NFL','NHL','NCAAF')
FEATURED=('NBA','NFL','NCAAF')
ODDS_KEYS={'NBA':'basketball_nba','NFL':'americanfootball_nfl','NHL':'icehockey_nhl','NCAAF':'americanfootball_ncaaf'}
@st.cache_data(ttl=900,show_spinner=False)
def royal_consensus(sport, key_fingerprint, _key):
    key=_key
    if not key: return [],'Odds API paused — no credits consumed'
    try:
        r=requests.get('https://api.the-odds-api.com/v4/sports/'+ODDS_KEYS[sport]+'/odds',params={'apiKey':key,'regions':'us','markets':'h2h,spreads,totals','oddsFormat':'decimal'},timeout=10)
        r.raise_for_status(); data=r.json()
        return data if isinstance(data,list) else [], 'Sportsbook consensus · 15-minute cache'
    except Exception as exc:
        code=getattr(getattr(exc,'response',None),'status_code',None)
        logging.getLogger('royal_predictions').warning('Odds %s: %s',sport, f'HTTP {code}' if code else type(exc).__name__)
        return [],'Odds feed unavailable: '+(f'HTTP {code}' if code else type(exc).__name__)

def royal_lines(event):
    from statistics import median
    spreads=[]; totals=[]; probs=[]
    home=event.get('home_team'); away=event.get('away_team')
    for book in event.get('bookmakers',[]):
        for market in book.get('markets',[]):
            outcomes=market.get('outcomes',[])
            if market.get('key')=='h2h' and len(outcomes)==2:
                prices={o.get('name'):numeric(o.get('price')) for o in outcomes}
                if all(prices.get(t) and prices[t]>1 for t in (home,away)):
                    a,b=1/prices[home],1/prices[away]; probs.append(a/(a+b))
            if market.get('key')=='spreads':
                spreads.extend(v for o in outcomes if o.get('name')==home and (v:=numeric(o.get('point'))) is not None)
            if market.get('key')=='totals':
                totals.extend(v for o in outcomes if o.get('name')=='Over' and (v:=numeric(o.get('point'))) is not None)
    return tuple(median(x) if x else None for x in (probs,spreads,totals))


# Separate accuracy-only journal. Legacy archive remains untouched.
from statistics import mean

def forecast(game, sport, history, spread, total, preview=False, retrospective=False):
    home=game.get('home'); teams=game.get('teams',[]); target=parse_date(game.get('date'))
    if home not in teams or len(teams)!=2 or not target or game.get('state')!='pre' or (not retrospective and target<=utc()): return []
    away=next(t for t in teams if t!=home)
    valid=[]
    for g in history:
        d=parse_date(g.get('date')); ts=g.get('teams',[])
        if not d or d>=min(target,utc()) or not g.get('completed') or g.get('state')!='post' or len(ts)!=2: continue
        if any(x in str(g.get('detail','')).lower() for x in ('cancel','postpon','abandon','forfeit','suspend')): continue
        scores=[numeric(g.get('scores',{}).get(t)) for t in ts]
        if any(v is None or not math.isfinite(v) for v in scores): continue
        valid.append((d,g,dict(zip(ts,scores))))
    def rows(team):
        return sorted([(d,sc[team],sc[next(t for t in sc if t!=team)],next(t for t in sc if t!=team),g.get('home')==team) for d,g,sc in valid if team in sc],key=lambda r:r[0],reverse=True)
    def profile(team,is_home):
        allr=rows(team); recent=allr[:10]
        if len(recent)<5: return None
        def avg(rs,i): return mean(r[i] for r in rs)
        venue=[r for r in allr if r[4]==is_home]
        attack=.6*avg(recent,1)+.4*avg(allr,1); defense=.6*avg(recent,2)+.4*avg(allr,2)
        if len(venue)>=3: attack=.8*attack+.2*avg(venue,1); defense=.8*defense+.2*avg(venue,2)
        # Opponent quality uses only games before the matchup, shrunk toward zero.
        strength=[]
        for r in recent:
            opp=[x for x in rows(r[3]) if x[0]<r[0] and x[3]!=team][:10]
            if len(opp)>=3: strength.append(mean(x[1]-x[2] for x in opp))
        adj=.10*mean(strength) if strength else 0
        rest=(target-recent[0][0]).total_seconds()/86400
        return attack+adj,defense-adj,len(recent),len(venue),len(strength),rest
    h,a=profile(home,True),profile(away,False)
    if not h or not a: return []
    hp=max(0,(h[0]+a[1])/2); ap=max(0,(a[0]+h[1])/2)
    # Conservative research thresholds, not calibrated probabilities.
    margin={'NBA':4,'NFL':3,'NHL':.6,'NCAAF':5}[sport]; totalgap={'NBA':6,'NFL':4,'NHL':.7,'NCAAF':6}[sport]
    coverage='RESEARCH B' if min(h[2],a[2])>=8 and min(h[3],a[3])>=3 and min(h[4],a[4])>=3 else 'RESEARCH C'
    why=f'Recent {h[2]}/{a[2]} games, available history and venue splits: projected score {home} {hp:.1f}, {away} {ap:.1f}. Opponent adjustment samples {h[4]}/{a[4]}; rest gap {h[5]:.1f}/{a[5]:.1f} days (informational). Injuries, travel, season completeness and exchange value unverified.'
    out=[]
    if abs(hp-ap)>=margin or (preview and hp!=ap): out.append(('WINNER',home if hp>ap else away,None,coverage,why))
    if spread is not None and abs(hp-ap+spread)>=margin: out.append(('SPREAD',home if hp-ap+spread>0 else away,spread,coverage,why))
    if total is not None and abs(hp+ap-total)>=totalgap: out.append(('TOTAL','OVER' if hp+ap>total else 'UNDER',total,coverage,why))
    return out

def grade_new(r,g):
    if not g or not g.get('completed') or g.get('state')!='post': return None
    if any(x in str(g.get('detail','')).lower() for x in ('cancel','postpon','abandon','forfeit','suspend')): return None
    if r.get('kind') not in ('WINNER','SPREAD','TOTAL'): return None
    if r['kind'] in ('WINNER','SPREAD') and r.get('selection') not in (r.get('home'),r.get('away')): return None
    if r['kind']=='TOTAL' and r.get('selection') not in ('OVER','UNDER'): return None
    if r['kind']!='WINNER' and (numeric(r.get('line')) is None or not math.isfinite(numeric(r['line']))): return None
    if set(g.get('teams',[]))!={r['home'],r['away']}: return None
    h=numeric(g['scores'].get(r['home'])); a=numeric(g['scores'].get(r['away']))
    if h is None or a is None: return None
    if r['kind']=='WINNER':
        if h==a: return 'PUSH'
        if g.get('winner') not in (r['home'],r['away']): return None
        return 'WIN' if g['winner']==r['selection'] else 'LOSS'
    if r['kind']=='SPREAD': v=h-a+numeric(r['line']); positive=r['selection']==r['home']
    else: v=h+a-numeric(r['line']); positive=r['selection']=='OVER'
    return 'PUSH' if abs(v)<1e-8 else 'WIN' if (v>0)==positive else 'LOSS'


import logging
from statistics import mean
log=logging.getLogger("royal_predictions")
@st.cache_data(ttl=30,show_spinner=False)
def fetch_board(sport, day):
    return board_request(sport, day)

def board_request(sport, day):
    errors=[]
    for host in ('site.api.espn.com','site.web.api.espn.com'):
        for params in ({'dates':day, 'limit':1000, **({'groups':80} if sport=='NCAAF' else {})}, {'dates':day,'limit':1000}):
            try:
                r=requests.get('https://'+host+'/apis/site/v2/sports/'+ESPN[sport]+'/scoreboard',params=params,timeout=4,headers={'Accept':'application/json','User-Agent':'RoyalSportsResearch/7.4'})
                r.raise_for_status()
                events=r.json()['events']
                return [g for e in events for g in _score_records(e,sport)], 'OK'
            except Exception as e:
                code=getattr(getattr(e,'response',None),'status_code',None)
                errors.append(f'HTTP {code}' if code else type(e).__name__)
    return [], '/'.join(dict.fromkeys(errors))

@st.cache_data(ttl=1800,show_spinner=False)
def history_day(sport, day): return board_request(sport,day)

@st.cache_data(ttl=300,show_spinner=False)
def schedule_day(sport, day): return board_request(sport,day)

def collect_days(sport, days, fn):
    games={}; statuses=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for rows,status in pool.map(lambda d:fn(sport,d.strftime('%Y%m%d')),days):
            statuses.append(status)
            for g in rows: games[g['event_id']]=g
    ok=statuses.count('OK')
    return list(games.values()), f'{ok}/{len(statuses)} days OK' if ok else (statuses[0] if statuses else 'No dates')

def current_board(sport,today):
    recent,status=collect_days(sport,[today-timedelta(days=1),today],fetch_board)
    future,fs=collect_days(sport,[today+timedelta(days=i) for i in range(1,7)],schedule_day)
    return list({g['event_id']:g for g in recent+future}.values()),status+'; upcoming '+fs

def research(sport,today,odds_key):
    # Include Saturday college games and enough prior weeks for walk-forward testing.
    offsets=range(1,61) if sport in ('NBA','NHL') else range(1,91)
    games,status=collect_days(sport,[today-timedelta(days=i) for i in offsets],history_day)
    odds,odds_status=royal_consensus(sport,hashlib.sha256(odds_key.encode()).hexdigest(),odds_key)
    return games,status,odds,odds_status

def recover_pending(index,url):
    # Read only; network requests occur outside database transactions.
    if not url: return
    import psycopg2
    conn=psycopg2.connect(url,connect_timeout=12,sslmode='require')
    try:
        with conn.cursor() as c:
            c.execute("SELECT to_regclass('royal_sports_accuracy_v766_fresh_test')")
            if not c.fetchone()[0]: return
            c.execute("SELECT payload FROM royal_sports_accuracy_v766_fresh_test WHERE result='PENDING' AND payload->>'model'='ROYAL_766'")
            pending=[r[0] for r in c.fetchall()]
    finally: conn.close()
    dates=set()
    for r in pending:
        start=parse_date(r.get('start')); sport=r.get('sport')
        if sport not in FOCUS or not start or start>utc() or grade_new(r,index.get((sport,r.get('event_id')))): continue
        for day in {start.astimezone(CT).date(),start.date()}:
            dates.add((sport,day.strftime('%Y%m%d')))
    # Cached date lookups, bounded concurrency; no guessed outcomes.
    def lookup(item):
        sport,day=item
        return sport,fetch_board(sport,day)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for sport,(games,status) in pool.map(lookup,sorted(dates)):
            for g in games:
                key=(sport,g['event_id'])
                if key not in index or (g.get('completed') and not index[key].get('completed')): index[key]=g
    log.info('Recovery checked %s pending records across %s league/date pairs',len(pending),len(dates))

def pending_status(r,index):
    if r['result']!='PENDING': return r['result']
    g=index.get((r['sport'],r['event_id'])) or {}
    if g.get('completed'): return 'FINAL / NEEDS REVIEW'
    if g.get('state')=='in': return 'LIVE'
    start=parse_date(r.get('start'))
    if start and start>utc(): return 'UPCOMING'
    return 'AWAITING VERIFIED FINAL'

WRITE_AUDIT=[]
WRITE_LOCK=threading.Lock()
def write_note(**entry):
    with WRITE_LOCK:
        WRITE_AUDIT.append(dict(Time=local(utc().isoformat()),**entry))
        del WRITE_AUDIT[:-30]

def persist(new,index,url):
    import psycopg2
    from psycopg2.extras import Json, execute_values
    if not url: raise RuntimeError('Missing database URL')
    conn=None
    try:
        conn=psycopg2.connect(url,connect_timeout=12,sslmode='require')
        with conn:
            with conn.cursor() as c:
                c.execute("SET LOCAL statement_timeout = '15000ms'")
                c.execute("SET LOCAL lock_timeout = '5000ms'")
                c.execute('SELECT pg_advisory_xact_lock(254766)')
                c.execute("CREATE TABLE IF NOT EXISTS royal_sports_accuracy_v766_fresh_test (id text PRIMARY KEY,payload jsonb NOT NULL,result text NOT NULL DEFAULT 'PENDING')")
                attempted=len(new)
                new=[r for r in new if r.get('model')=='ROYAL_766' and r.get('confidence') in ('RESEARCH B','RESEARCH C') and r.get('phase')=='PREGAME' and parse_date(r.get('start')) and parse_date(r['start'])>utc()]
                inserted=0
                if new:
                    inserted=len(execute_values(c,'INSERT INTO royal_sports_accuracy_v766_fresh_test(id,payload) VALUES %s ON CONFLICT DO NOTHING RETURNING id',[(f"{r['sport']}:{r['event_id']}:{r['phase']}:{r['kind']}",Json(r)) for r in new],page_size=200,fetch=True))
                c.execute("SELECT id,payload FROM royal_sports_accuracy_v766_fresh_test WHERE result='PENDING' AND payload->>'model'='ROYAL_766'")
                for key,r in c.fetchall():
                    result=grade_new(r,index.get((r['sport'],r['event_id'])))
                    if result: c.execute("UPDATE royal_sports_accuracy_v766_fresh_test SET result=%s WHERE id=%s AND result='PENDING'",(result,key))
                c.execute("SELECT payload,result FROM royal_sports_accuracy_v766_fresh_test WHERE payload->>'model'='ROYAL_766' AND payload->>'confidence' IN ('RESEARCH B','RESEARCH C')")
                saved_rows=[dict(r,result=result) for r,result in c.fetchall()]
            # Leaving the transaction context commits before reporting success.
        write_note(Status='Committed',Attempted=attempted,Eligible=len(new),Inserted=inserted,JournalRows=len(saved_rows))
        return saved_rows
    except Exception as exc:
        write_note(Status='Write failed: '+type(exc).__name__+' '+str(getattr(exc,'pgcode',None) or ''),Attempted=len(new))
        raise
    finally:
        if conn is not None: conn.close()

@st.cache_data(ttl=86400,show_spinner=False)
def logo_directory(sport):
    try:
        r=requests.get('https://site.api.espn.com/apis/site/v2/sports/'+ESPN[sport]+'/teams',params={'limit':500},timeout=5); r.raise_for_status()
        teams=r.json()['sports'][0]['leagues'][0]['teams']
        return {clean(t['team']['displayName']):_provider_image_url(t['team'].get('logos')) for t in teams}
    except Exception: return {}
def logo_for(sport,name): return logo_directory(sport).get(clean(name),'')

st.markdown("""<style>.block-container{max-width:none!important;padding:1rem 2vw 2rem!important;width:100%!important}.stApp{background:radial-gradient(ellipse at top,#29134b 0%,#100d20 42%,#090812 100%)}div[data-testid=stMetric]{border:1px solid #67449b;border-radius:12px;padding:12px}img{object-fit:contain}div[data-testid=stImage]{width:100%!important}div[data-testid=stImage] img{width:100%!important;border-radius:12px}div[data-testid=stSegmentedControl] button{min-height:44px;font-weight:700}div[data-testid=stMetric]{background:#1c1532}.rgame{background:linear-gradient(125deg,#201435,#100d1d);border:1px solid #634293;border-radius:12px;padding:9px 12px;margin-bottom:5px;color:#fff;box-shadow:0 0 10px #57288a33}.rteam{display:flex;align-items:center;gap:9px;min-height:27px;font-size:13px}.rteam b{margin-left:auto}.rgame small{color:#c2addd;font-size:11px}.rpred{background:linear-gradient(135deg,#28163f,#100d1d 80%);border:1px solid #8050b4;border-radius:13px;padding:8px 10px;margin-bottom:5px;color:#fff;box-shadow:0 2px 12px #57288a33;font-size:13px}.rpred .meta{font-size:11px;color:#c6addf}.rpred .match{display:flex;align-items:center;justify-content:space-between;gap:6px;margin:5px 0}.rpred .side{flex:1;text-align:center;font-weight:700}.rpred .side.royalpick{color:#ffe39a;background:#49351f66;border:1px solid #d8a94d;border-radius:9px;padding:3px 2px;box-shadow:0 0 10px #eab54a33}.rpred .royalwinner b{color:#ffe39a}.rpred .side img{display:block;margin:0 auto 2px;width:27px;height:27px}.rpred .project{font-size:23px;font-weight:800;color:#e4c5ff;white-space:nowrap}.rpred .bet{border-top:1px solid #51346c;padding:3px 0;line-height:1.35}.rpred .bet b{color:#e0b5ff}.rpred .bet span{color:#eee;font-size:12px;line-height:1.35}.rpred .watch{color:#ffd18a;font-size:11px;margin-top:3px}.rpred .reason{color:#c6addf;font-size:11px;margin-bottom:3px}.royal-uniform-card{box-sizing:border-box;width:100%;height:330px;display:flex;flex-direction:column;gap:6px;overflow-y:auto;overflow-wrap:anywhere}.royal-live-card{box-sizing:border-box;width:100%;min-height:160px;display:flex;flex-direction:column;gap:6px;border:1px solid #654290;border-radius:12px;padding:12px;background:#171022}.royal-live-card .live-team{display:flex;align-items:center;gap:8px;font-weight:700}.royal-live-card .live-team b{margin-left:auto;font-size:22px;color:#fff}.royal-uniform-card>div:last-child{margin-top:auto}@media(max-width:640px){.royal-uniform-card{height:330px}}</style>""",unsafe_allow_html=True)
st.markdown("""<style>.royal-v76,.royal-record,.royal-live-card{background:linear-gradient(135deg,#29183e,#100b1d 80%);border:1px solid #9563c9;border-radius:15px;box-shadow:0 0 14px #7e43ba35;padding:12px;color:#f5edff}.royal-v76{min-height:305px;height:auto;box-sizing:border-box;display:flex;flex-direction:column;gap:5px;overflow:visible;font-size:13px}.royal-v76 small,.royal-record small{color:#c6addf;font-size:11px}.royal-match{display:flex;align-items:center;justify-content:space-around}.royal-side{display:flex;flex-direction:column;align-items:center;flex:1}.royal-gold{color:#f3cf7c!important;font-weight:700}.royal-projection{font-size:25px;font-weight:800;text-align:center;background:#ffffff08;border-radius:8px}.royal-record{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px}.royal-record small{display:block}.royal-live-card .live-team b{font-size:30px;color:#f3cf7c}.royal-live-card{min-height:175px}</style>""",unsafe_allow_html=True)
banners=[p for folder in (Path('.'),Path('assets'),Path('images')) if folder.is_dir() for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg','.webp') and 'banner' in p.stem.lower()]
if banners:
    banner=sorted(banners)[0]
    mime='jpeg' if banner.suffix.lower() in ('.jpg','.jpeg') else banner.suffix.lower()[1:]
    encoded=base64.b64encode(banner.read_bytes()).decode()
    st.markdown(f'<img alt="Royal Sports Sniper" style="display:block;width:100%;height:auto;max-width:none;margin:0 auto;border-radius:12px" src="data:image/{mime};base64,{encoded}">',unsafe_allow_html=True)
else: st.title('ROYAL SPORTS SNIPER | 254')
st.caption('V7.8.5 STABILITY TEST · ROYAL PICK LOCK · NBA / NFL / NHL / NCAAF')
page=st.segmented_control('View',['Picks','Live','Research','Results','Stats'],default='Picks',label_visibility='collapsed') or 'Picks'
def collect_snapshot(db_url,odds_key):
    today=utc().astimezone(CT).date()
    with ThreadPoolExecutor(max_workers=4) as pool:
        boards=list(pool.map(lambda sport:current_board(sport,today),FOCUS))
    index={(sport,g['event_id']):g for sport,(games,status) in zip(FOCUS,boards) for g in games if g['event_id']}
    active=[(sport,g) for (sport,_),g in index.items() if g['state'] in ('pre','in')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        research_rows={pool.submit(research,sport,today,odds_key):sport for sport in FOCUS}
        new=[]; diagnostics=[]; market_cards=[]; previews={}
        board_statuses={sport:status for sport,(_,status) in zip(FOCUS,boards)}
        for future in as_completed(research_rows):
            sport=research_rows[future]
            history,status,events,odds_status=future.result()
            for g in history: index.setdefault((sport,g['event_id']),g)
            board_status=board_statuses[sport]
            batch=[]
            for league,g in active:
                if league!=sport or g.get('state')!='pre' or g.get('home') not in g.get('teams',[]) or len(g.get('teams',[]))!=2: continue
                start=parse_date(g.get('date'))
                if not start or start.astimezone(CT).date()!=today: continue
                for kind,selection,line,confidence,why in forecast(g,sport,history,None,None,preview=True):
                    batch.append(dict(sport=sport,event_id=g['event_id'],home=g['home'],away=next(t for t in g['teams'] if t!=g['home']),start=g['date'],phase='PREGAME',model='ROYAL_766',revision='7.6.6',kind=kind,selection=selection,line=line,confidence=confidence,why=why,created_at=utc().isoformat()))
            write_note(Status='Pregame scan',Sport=sport,Attempted=len(batch),Upcoming=sum(1 for league,g in active if league==sport and g.get('state')=='pre' and parse_date(g.get('date')) and parse_date(g['date']).astimezone(CT).date()==today))
            try: persist(batch,index,db_url)
            except Exception as exc:
                write_note(Status="Early save failed: "+type(exc).__name__,Attempted=len(batch))
                log.warning("Early journal write: %s",type(exc).__name__)
        research_rows=[f.result() for f in sorted(research_rows,key=lambda f:FOCUS.index(research_rows[f]))]
    new=[]; diagnostics=[]; market_cards=[]; previews={}
    for sport,(history,_,_,_) in zip(FOCUS,research_rows):
        for g in history: index.setdefault((sport,g['event_id']),g)
    for sport,(history,status,events,odds_status),(_,board_status) in zip(FOCUS,research_rows,boards):
        diagnostics.append(dict(Sport=sport,Scoreboard=board_status,History=len(history),HistoryFeed=status,Odds=len(events),Status=odds_status))
        for e in events:
            start=parse_date(e.get('commence_time'))
            home=e.get('home_team'); away=e.get('away_team')
            if not start or not home or not away or not utc()<start<=utc()+timedelta(days=7): continue
            prob,spread,total=royal_lines(e)
            market_cards.append(dict(sport=sport,home=home,away=away,start=start.isoformat(),prob=prob,spread=spread,total=total))
        for league,g in active:
            if league!=sport: continue
            start=parse_date(g['date']); home=g['home']; away=next((t for t in g['teams'] if t!=home),None)
            if not start or not home or not away or (g['state']=='pre' and not utc()<start<=utc()+timedelta(days=7)): continue
            matches=[e for e in events if parse_date(e.get('commence_time')) and abs((parse_date(e['commence_time'])-start).total_seconds())<7200 and _unique_score_team(e.get('home_team'),g['teams'],sport)==home and _unique_score_team(e.get('away_team'),g['teams'],sport)==away]
            prob,spread,total=royal_lines(matches[0]) if len(matches)==1 else (None,None,None)
            predictions=forecast(g,sport,history,spread,total,preview=True)  # record all eligible same-day winner forecasts, not just large margins
            previews[(sport,g['event_id'])]=forecast(g,sport,history,None,None,preview=True)
            for kind,selection,line,confidence,why in predictions:
                if start.astimezone(CT).date()!=today: continue
                new.append(dict(sport=sport,event_id=g['event_id'],home=home,away=away,start=g['date'],phase='LIVE' if g['state']=='in' else 'PREGAME',model='ROYAL_766',revision='7.6.6',kind=kind,selection=selection,line=line,confidence=confidence,why=why,created_at=utc().isoformat()))
    storage_ok=False; storage_error=None
    try:
        try: recover_pending(index,db_url)
        except Exception as exc: log.warning("Recovery lookup: %s",type(exc).__name__)
        rows=persist(new,index,db_url); storage_ok=True
    except Exception as e:
        storage_error=type(e).__name__ + (' / '+str(e.pgcode) if getattr(e,'pgcode',None) else '')
        log.error('Prediction storage: %s',storage_error)
        rows=[dict(r,result='UNSAVED') for r in new]
    return today,index,active,market_cards,diagnostics,rows,storage_ok,storage_error,previews

class RoyalClarityWorker75C:
    def __init__(self):
        self.pool=ThreadPoolExecutor(max_workers=1)
        self.lock=threading.Lock()
        self.future=None
        self.snapshot=None
        self.finished=-float("inf")
        self.error=None
    def poll(self,db_url,odds_key):
        with self.lock:
            if self.future is not None and self.future.done():
                try:
                    self.snapshot=self.future.result(); self.error=None
                except Exception as e:
                    self.error=type(e).__name__
                    log.error('Research worker: %s',self.error)
                self.future=None; self.finished=time.monotonic()
            if self.future is None and time.monotonic()-self.finished>=30:
                self.future=self.pool.submit(collect_snapshot,db_url,odds_key)
            return self.snapshot,self.future is not None,self.error

@st.cache_resource
def royal_elite_worker_75g(): return RoyalClarityWorker75C()

def plain_bet(x):
    selection=x['selection']; line=x['line']
    if x['kind']=='WINNER': return 'Moneyline · '+selection, selection+' must win the game outright.'
    if x['kind']=='SPREAD':
        n=line if selection==x['home'] else -line
        if n<0: detail=f'{selection} must win by at least {math.floor(-n)+1}.'
        elif n>0:
            unit='goals' if x.get('sport')=='NHL' else 'points'
            limit=math.ceil(n)-1
            detail=f'{selection} must win outright.' if limit==0 else f'{selection} can win outright or lose by at most {limit} {unit if limit!=1 else unit[:-1]}.'
        else: detail=f'{selection} must win outright.'
        if float(n).is_integer(): detail+=' Exact handicap tie is a push under standard spread rules.'
        return f'Spread · {selection} {n:+g}',detail
    over=selection=='OVER'
    detail=f'Both teams combined must score {math.floor(line)+1} or more.' if over else f'Both teams combined must score {math.ceil(line)-1} or fewer.'
    if float(line).is_integer(): detail+=' Exactly '+str(int(line))+' is a push under standard total rules.'
    return f'Over/under · {selection.title()} {line:g}',detail

# Compact league marks; retain the text label if the remote image is unavailable.
LEAGUE_MARKS = {
    'NBA': 'https://a.espncdn.com/i/teamlogos/leagues/500/nba.png',
    'NFL': 'https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png',
    'NHL': 'https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png',
    'NCAAF': '',
}
def league_mark(sport):
    label=html.escape(str(sport),quote=True)
    url=LEAGUE_MARKS.get(sport)
    img=f'<img src="{url}" alt="" width="17" height="17" style="object-fit:contain;vertical-align:middle;margin-right:5px">' if url else ('🏈 ' if sport=='NCAAF' else '')
    return f'<span style="display:inline-flex;align-items:center;font-weight:700">{img}{label}</span>'

def paper_return(stake, price):
    if price is None or not math.isfinite(price) or not 0 < price < 1:
        return None
    return stake / price, stake / price - stake

def fresh_research_price(price, timestamp):
    return price if price is not None and timestamp is not None and 0 <= time.time()-timestamp <= 15 else None

def royal_paper_grade(r, m, price, rows, now):
    """Uncalibrated research tiers, never a real-money recommendation."""
    if r.get('preview') or r.get('result') != 'PENDING' or price is None:
        return 'D', 0., 'Preview, unlocked or no fresh quote'
    v=experimental_value(r,m,price)
    if not v or v[1] is None or v[1]<=0 or v[0]<.52:
        return 'D', 0., 'No positive experimental price gap'
    sample=[x for x in rows if x.get('sport')==m['sport'] and x.get('kind')=='WINNER' and x.get('result') in ('WIN','LOSS') and parse_date(x.get('start')) and parse_date(x['start'])<now]
    n=len(sample); wins=sum(x['result']=='WIN' for x in sample)
    rate=(wins+10)/(n+20)
    prob,gap,_=v
    if n>=50 and rate>=.60 and prob>=.70 and gap>=.10 and r.get('confidence')=='RESEARCH B': return 'A+',2.,'Experimental tier; 50+ graded sport picks'
    if n>=30 and rate>=.56 and prob>=.63 and gap>=.07 and r.get('confidence')=='RESEARCH B': return 'A',1.5,'Experimental tier; 30+ graded sport picks'
    if n>=20 and rate>=.53 and prob>=.59 and gap>=.05 and r.get('confidence')=='RESEARCH B': return 'B+',1.,'Experimental tier; 20+ graded sport picks'
    if gap>=.03 and prob>=.55 and r.get('confidence')=='RESEARCH B': return 'B',.5,'Limited evidence; paper only'
    return 'C',.25,'Limited evidence; paper only'

def prediction_card(group,g):
    r=group[0]; esc=lambda v:html.escape(str(v),quote=True)
    why=r['why']; match=re.search(r'projected score (.+?) (-?\d+(?:\.\d+)?), (.+?) (-?\d+(?:\.\d+)?)\.',why)
    scores={match[1]:match[2],match[3]:match[4]} if match else {}
    # Keep decimal precision in the second score, even when followed by punctuation.
    if match:
        m=re.search(r'projected score (.+?) (\d+\.\d+), (.+?) (\d+\.\d+)\.',why)
        if m: scores={m[1]:m[2],m[3]:m[4]}
    winner=next((x["selection"] for x in group if x["kind"]=="WINNER"),None)
    def side(t):
        logo=g.get('logos',{}).get(t) or ''
        img=f'<img src="{esc(logo)}" alt="">' if logo.startswith('https://') else ''
        cls="side royalpick" if t==winner else "side"
        crown="👑 " if t==winner else ""
        return f'<div class="{cls}">{img}{crown}{esc(t)}</div>'
    projected=f"{scores[r['away']]} – {scores[r['home']]}" if r['away'] in scores and r['home'] in scores else '—'
    reason=r['confidence']+' · '+why if scores else why
    bets=''
    for x in sorted(group,key=lambda x:('WINNER','SPREAD','TOTAL').index(x['kind'])):
        label,detail=plain_bet(x)
        cls="bet royalwinner" if x["kind"]=="WINNER" else "bet"
        crown="👑 " if x["kind"]=="WINNER" else ""
        bets+=f'<div class="{cls}"><b>{crown}{esc(label)}</b> <span>— {esc(detail)}</span></div>'
    unit_note=r.get('unit_note','')
    if unit_note: bets+=f'<div class="bet royalwinner"><b>{esc(unit_note)}</b></div>'
    status='PREVIEW · NOT LOCKED' if r['result']=='UNSAVED' else 'LOCKED · '+str(r['result'])
    return f'<div class="rpred"><div class="meta">{league_mark(r["sport"])}{esc(" · "+local(r["start"])+" · "+status)}</div><div class="match">{side(r["away"])}<div class="project">{esc(projected)}</div>{side(r["home"])}</div><div class="reason">Projected score · {esc(reason)}</div>{bets}<div class="watch">WATCH · Exchange price unverified · Experimental</div></div>'

def render_games(items):
    for i in range(0,len(items),3):
        for col,m in zip(st.columns(3),items[i:i+3]):
            with col:
                g=m.get('game',{}); live=g.get('state')=='in'
                def team(t):
                    logo=g.get('logos',{}).get(t) or ""
                    mark=f'<img src="{html.escape(logo,quote=True)}" width="24" height="24" style="object-fit:contain">' if logo else ''
                    score=g.get('scores',{}).get(t) if live else None
                    return f'<div class="rteam">{mark}<span>{html.escape(t)}</span><b>{html.escape(str(score)) if score is not None else ""}</b></div>'
                detail=g.get('detail') if live else local(m['start'])
                p=m.get('prob'); extra=('Market favorite: '+(m['home'] if p>.5 else m['away'] if p<.5 else 'Even')+f' {max(p,1-p):.0%}') if p is not None else ('Live score' if live else 'Scheduled')
                st.markdown(f'<div class="rgame"><small>{league_mark(m["sport"])}{html.escape(" · "+str(detail))}</small>{team(m["away"])}{team(m["home"])}<small>{html.escape(extra)} · {"ESPN" if g else "Odds API"}</small></div>',unsafe_allow_html=True)


# Public, read-only league discovery; no guessed event slugs.
@st.cache_data(ttl=90,show_spinner=False)
def poly_league(sport):
    events=[]
    for offset in range(0,500,100):
        r=requests.get(BASE+'/v2/leagues/'+quote(LEAGUES[sport],safe='')+'/events',params={'limit':100,'offset':offset,'type':'sport','section':'general'},timeout=5)
        r.raise_for_status(); batch=r.json().get('events')
        if not isinstance(batch,list): raise ValueError('Missing events array')
        events.extend(batch)
        if len(batch)<100: break
    return events

def poly_team(t, names, ab):
    if not isinstance(t,dict): return None
    fields=[str(t.get(k) or '') for k in ('name','safeName','alias','abbreviation','displayAbbreviation')]
    exact={n for n in names if any(clean(v)==clean(n) for v in fields if v)}
    if len(exact)==1: return next(iter(exact))
    codes={clean(v) for v in fields if v}
    matches=[n for n in names if clean(ab.get(n) or '') in codes]
    if len(matches)==1:return matches[0]
    # Only accept an unambiguous full-name prefix, never a fuzzy nickname.
    matches=[n for n in names if any(len(clean(v))>=5 and clean(n).startswith(clean(v)) for v in fields[:3])]
    return matches[0] if len(matches)==1 else None

SPORT_TYPES={'NFL':'football','NCAAF':'football','NBA':'basketball','NHL':'hockey'}
def classify_market(x,sport):
    typ=str(x.get('sportsMarketType') or '').lower()
    prefix=SPORT_TYPES.get(sport)
    if not prefix:return None
    return {prefix+'_team_full_game_winner':'Moneyline',prefix+'_team_full_game_spread':'Spread',prefix+'_team_full_game_total':'Total',
            'football_player_passing_yards':'Passing','football_player_rushing_yards':'Rushing',
            'football_player_receiving_yards':'Receiving'}.get(typ) if prefix=='football' else {prefix+'_team_full_game_winner':'Moneyline',prefix+'_team_full_game_spread':'Spread',prefix+'_team_full_game_total':'Total'}.get(typ)

def match_poly_event(events,names,ab,start):
    matches={}
    for e in events:
        if not isinstance(e,dict) or e.get('closed') is True or e.get('ended') is True: continue
        raw=e.get('startTime') or e.get('eventDate') or e.get('startDate')
        dt=parse_date(raw)
        if not dt or abs((dt-start).total_seconds())>7200: continue
        teams=e.get('teams') or []
        if len(teams)!=2: continue
        mapped=[poly_team(t,names,ab) for t in teams]
        slug=e.get('slug')
        if len({str(t.get('id')) for t in teams if isinstance(t,dict) and t.get('id') is not None})==2 and set(mapped)==set(names) and isinstance(slug,str) and re.fullmatch(r'[a-zA-Z0-9_-]+',slug): matches[slug]=(e,dict(zip((str(t.get('id')) for t in teams),mapped)))
    return next(iter(matches.values())) if len(matches)==1 else (None,{})

def poly_for_game(m):
    g=m['game']; start=parse_date(m['start']); names=g['teams']
    if not start:return '',[],'Missing start'
    try:
        event,team_ids=match_poly_event(poly_league(m['sport']),names,g.get('abbr',{}),start)
        if not event:return '',[],'No unique league event matched'
        out=[]
        for x in event.get('markets') or []:
            if not isinstance(x,dict) or x.get('closed') is True or x.get('active') is False or x.get('hidden') is True:continue
            title=str(x.get('question') or x.get('title') or '')
            category=classify_market(x,m['sport'])
            ms=x.get('slug'); sides=x.get('marketSides') or []
            if not category or not isinstance(ms,str) or not re.fullmatch(r'[a-zA-Z0-9_-]+',ms):continue
            def side(flag):
                ss=[y for y in sides if isinstance(y,dict) and y.get('long') is flag]
                if len(ss)!=1 or ss[0].get('tradable') is False:return ''
                y=ss[0]; tid=y.get('teamId')
                return team_ids.get(str(tid),'') if tid is not None else ''
            out.append(dict(category=category,title=title,slug=ms,long=side(True),short=side(False),line=numeric(x.get('line')),main_line=numeric((event.get('eventState') or {}).get('mainSpreadLine')),short_title=str(x.get('titleShort') or x.get('title') or title),main_total=numeric((event.get('eventState') or {}).get('mainTotalLine'))))
        return event['slug'],out,'League event matched · '+str(len(out))+' supported markets'
    except Exception as e:
        code=getattr(getattr(e,'response',None),'status_code',None)
        return '',[],'League HTTP '+str(code) if code else 'League '+type(e).__name__

def parse_poly_bbo(payload,slug):
    d=payload.get('marketData') if isinstance(payload,dict) else None
    if not isinstance(d,dict):return None,'Missing marketData'
    if d.get('marketSlug')!=slug:return None,'Quote slug mismatch'
    if d.get('state')!='MARKET_STATE_OPEN':return None,'Market state: '+str(d.get('state') or 'unknown')
    for k in ('bestBid','bestAsk'):
        if not isinstance(d.get(k),dict) or d[k].get('currency')!='USD':return None,'Quote currency unverified'
    b,a=numeric(d['bestBid']),numeric(d['bestAsk'])
    if b is None or a is None or not 0<b<=a<1:return None,'Missing or invalid two-sided prices'
    for k in ('bidDepth','askDepth','bidShares','askShares'):
        if k in d and (numeric(d[k]) is None or not numeric(d[k])>0):return None,'Empty or invalid book depth'
    return (a,1-b),'Two-sided snapshot'

def fetch_poly_quote(slug):
    try:
        r=requests.get(BASE+'/v1/markets/'+quote(slug,safe='')+'/bbo',timeout=4)
        r.raise_for_status(); return parse_poly_bbo(r.json(),slug)
    except requests.HTTPError as e:
        return None,'Quote HTTP '+str(e.response.status_code if e.response is not None else 'unknown')
    except Exception as e:return None,'Quote '+type(e).__name__

class RoyalQuoteCache:
    def __init__(self):
        self.lock=threading.Lock(); self.pool=ThreadPoolExecutor(max_workers=2); self.entries={}; self.next_request=0; self.pause_until=0
    def poll(self,slug):
        with self.lock:
            now=time.monotonic()
            e=self.entries.setdefault(slug,dict(q=None,ts=None,next=0,future=None,status='Waiting for quote'))
            f=e['future']
            if f is not None and f.done():
                try: q,status=f.result()
                except Exception as exc: q,status=None,type(exc).__name__
                e['status']=status; e['future']=None
                if q is not None: e['q']=q; e['ts']=time.time()
                e['ok']=q is not None
                e['next']=now+(12 if q is not None else 60 if '429' in status else 15)
                if '429' in status: self.pause_until=max(self.pause_until,now+60)
            if e['future'] is None and now>=max(e['next'],self.next_request,self.pause_until):
                e['future']=self.pool.submit(fetch_poly_quote,slug)
                self.next_request=now+0.5
            age=max(0,time.time()-e['ts']) if e['ts'] is not None else None
            fresh=bool(e.get('ok') and age is not None and age<=15)
            return e['q'],e['status'],e['ts'],fresh

@st.cache_resource
def royal_quote_cache(): return RoyalQuoteCache()

def poly_quote(slug): return royal_quote_cache().poll(slug)

@st.cache_resource
def royal_mapping_cache(): return {},threading.Lock()

def retained_mapping(m):
    cache,lock=royal_mapping_cache(); key=(m['sport'],m['game']['event_id'],m['start'])
    slug,markets,status=poly_for_game(m)
    with lock:
        if slug and markets:
            cache[key]=(slug,markets,time.time()); return slug,markets,status,True
        old=cache.get(key)
        if old and time.time()-old[2]<3600:
            return old[0],old[1],status+' · retained mapping',False
    return slug,markets,status,False

def projection_summary(r,m):
    match=re.search(r'projected score (.+?) (-?\d+\.\d+), (.+?) (-?\d+\.\d+)\.',r.get('why',''))
    if not match:return 'Unavailable','Unavailable'
    scores={match[1]:float(match[2]),match[3]:float(match[4])}
    if set(scores)!={m['away'],m['home']}:return 'Unavailable','Unavailable'
    winner=r['selection']
    if winner not in scores:return 'Unavailable','Unavailable'
    other=next(t for t in scores if t!=winner)
    return f"{scores[m['away']]:.1f} – {scores[m['home']]:.1f}",f"{winner} +{scores[winner]-scores[other]:.1f} pts"

def projection_margin(r,m):
    match=re.search(r'projected score (.+?) (-?\d+\.\d+), (.+?) (-?\d+\.\d+)\.',r.get('why',''))
    return float(match[2])-float(match[4]) if match else 0.0

def experimental_value(r,m,price):
    match=re.search(r'projected score (.+?) (-?\d+\.\d+), (.+?) (-?\d+\.\d+)\.',r.get('why',''))
    if not match: return None
    scores={match[1]:float(match[2]),match[3]:float(match[4])}
    if set(scores)!={m['away'],m['home']} or r['selection'] not in scores:return None
    margin=scores[r['selection']]-scores[next(t for t in scores if t!=r['selection'])]
    # Fixed illustrative uncertainty assumptions; not fitted or calibrated.
    sigma={'NFL':14.,'NCAAF':21.,'NBA':12.,'NHL':2.5}.get(m['sport'])
    if not sigma:return None
    prob=.5*(1+math.erf(margin/(sigma*math.sqrt(2))))
    return prob, (prob-price if price is not None else None),sigma


@st.cache_data(ttl=3600,show_spinner=False)
def historical_model_check(sport, history):
    from collections import Counter
    skips=Counter()
    samples=[]
    for g in sorted(history,key=lambda x:x.get('date',''))[-100:]:
        if not g.get('completed') or g.get('state')!='post': skips['Not final']+=1; continue
        if any(x in str(g.get('detail','')).lower() for x in ('cancel','postpon','abandon','forfeit','suspend')): skips['Invalid game']+=1; continue
        home=g.get('home'); teams=g.get('teams',[])
        if len(teams)!=2 or home not in teams: skips['Team mapping']+=1; continue
        away=next(t for t in teams if t!=home)
        h=numeric(g.get('scores',{}).get(home)); a=numeric(g.get('scores',{}).get(away))
        if h is None or a is None or h==a: skips['Missing score or tie']+=1; continue
        trial=dict(g,state='pre')
        preds=forecast(trial,sport,history,None,None,preview=True,retrospective=True)
        if not preds: skips['Insufficient eligible history or equal projection']+=1; continue
        _,pick,_,grade,why=preds[0]
        val=experimental_value(dict(selection=pick,why=why),dict(home=home,away=away,sport=sport),None)
        if val: samples.append((val[0],int(pick==(home if h>a else away))))
        else: skips['Probability unavailable']+=1
    n=len(samples)
    return dict(SkipReasons='; '.join(f'{k}: {v}' for k,v in skips.items()),Eligible=n,Skipped=max(0,min(100,len(history))-n),Correct=sum(y for p,y in samples),Samples=n,Accuracy=f'{sum(y for p,y in samples)/n:.1%}' if n else '—',Brier=round(sum((p-y)**2 for p,y in samples)/n,3) if n else None,MeanProbability=f'{sum(p for p,y in samples)/n:.1%}' if n else '—')

@st.cache_data(ttl=120, show_spinner=False)
def apple_feed_probe():
    endpoints = {
        'Manifest': 'https://api.sports.apple.com/v3/en-us/manifest/3.0.0',
        'Update directory': 'https://api-sports.cdn-apple.com/v3/query/updates/SPORTS',
    }
    results = []
    shapes = []
    for name, url in endpoints.items():
        started = time.monotonic()
        try:
            with requests.get(url, timeout=(3, 5), stream=True, allow_redirects=False,
                              headers={'Accept': 'application/json'}) as response:
                status = response.status_code
                if status != 200:
                    results.append(dict(Source=name, Status=f'HTTP {status}', Keys='—'))
                    continue
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > 4000000:
                        raise ValueError('Response exceeds diagnostic size limit')
                    if time.monotonic()-started > 8:
                        raise TimeoutError('Diagnostic time limit')
                import json
                payload = json.loads(data)
                # Sample each top-level branch independently so the large teams directory cannot hide updates.
                if isinstance(payload,dict):
                    for branch, value in payload.items():
                        if isinstance(value,(dict,list)):
                            shapes.extend(dict(Source=name,**dict(row,Path='$.'+str(branch)+row['Path'][1:])) for row in apple_shape(value)[:8])
                else: shapes.extend(dict(Source=name,**row) for row in apple_shape(payload))
                keys = ', '.join(str(k) for k in list(payload)[:15]) if isinstance(payload,dict) else f'{len(payload)} items' if isinstance(payload,list) else type(payload).__name__
                results.append(dict(Source=name, Status='JSON received — unverified', Keys=keys, Milliseconds=round((time.monotonic()-started)*1000)))
        except Exception as exc:
            results.append(dict(Source=name, Status=type(exc).__name__, Keys='—'))
    return results, utc().isoformat(), shapes


@st.cache_data(ttl=15, show_spinner=False)
def journal_audit(url):
    import psycopg2
    if not url: return [], [], 'Missing database URL'
    conn=None
    try:
        conn=psycopg2.connect(url,connect_timeout=8,sslmode='require')
        conn.set_session(readonly=True)
        with conn.cursor() as c:
            c.execute("SET LOCAL statement_timeout = '8000ms'")
            c.execute("SELECT to_regclass('royal_sports_accuracy_v766_fresh_test')")
            if not c.fetchone()[0]: return [], [], 'V7.6.6 table not found'
            c.execute("SELECT payload->>'model',payload->>'confidence',result,count(*) FROM royal_sports_accuracy_v766_fresh_test GROUP BY 1,2,3")
            groups=[dict(Model=m,Confidence=k,Result=r,Count=n) for m,k,r,n in c.fetchall()]
            c.execute("SELECT payload,result FROM royal_sports_accuracy_v766_fresh_test WHERE payload->>'model'='ROYAL_766' AND payload->>'confidence' IN ('RESEARCH B','RESEARCH C')")
            rows=[dict(r,result=result) for r,result in c.fetchall()]
            return groups,rows,None
    except Exception as exc:
        return [],[],type(exc).__name__
    finally:
        if conn is not None: conn.close()


@st.cache_data(ttl=60,show_spinner=False)
def archive_inventory(url):
    import psycopg2
    from psycopg2 import sql
    if not url: return [], 'Missing database URL'
    conn=None; out=[]
    try:
        conn=psycopg2.connect(url,connect_timeout=8,sslmode='require')
        conn.set_session(readonly=True,autocommit=True)
        with conn.cursor() as c:
            c.execute("SET statement_timeout = '3000ms'")
            c.execute("SELECT schemaname,tablename FROM pg_tables WHERE tablename LIKE 'royal_sports_%' OR tablename LIKE 'royal_predictions_%' ORDER BY schemaname,tablename LIMIT 40")
            tables=c.fetchall()
            for schema,table in tables:
                try:
                    c.execute(sql.SQL('SELECT count(*) FROM {}.{}').format(sql.Identifier(schema),sql.Identifier(table)))
                    out.append(dict(Schema=schema,Journal=table,Rows=c.fetchone()[0]))
                except Exception as exc:
                    out.append(dict(Schema=schema,Journal=table,Rows=type(exc).__name__))
        return out,None
    except Exception as exc: return out,type(exc).__name__
    finally:
        if conn is not None: conn.close()

def prior_journal(url):
    import psycopg2
    conn=None
    try:
        conn=psycopg2.connect(url,connect_timeout=8,sslmode='require')
        conn.set_session(readonly=True)
        with conn.cursor() as c:
            c.execute("SET LOCAL statement_timeout = '8000ms'")
            c.execute("SELECT payload,result FROM public.royal_sports_accuracy_v75g_clean_test LIMIT 20")
            return [dict(Sport=r.get('sport'),Selection=r.get('selection'),Start=local(r.get('start')),Result=result) for r,result in c.fetchall()],None
    except Exception as exc: return [],type(exc).__name__
    finally:
        if conn is not None: conn.close()


def journal_diagnostics(url):
    st.subheader('Permanent journal audit')
    if st.button('Check saved records directly',key='journal768'):
        journal_audit.clear()
    groups,rows,error=journal_audit(url)
    if error: st.warning('Journal audit: '+error)
    else:
        st.caption(f'Direct database read: {sum(g["Count"] for g in groups)} total rows; {len(rows)} eligible V7.6.6 records. No records modified by this audit.')
        if groups: st.dataframe(groups,hide_index=True,width='stretch')
        if rows: st.dataframe([dict(Sport=r.get('sport'),Selection=r.get('selection'),Start=local(r.get('start')),Result=r.get('result')) for r in rows],hide_index=True,width='stretch')
    with WRITE_LOCK: writes=list(WRITE_AUDIT)
    st.caption('Write receipts for this server process; Inserted counts new committed rows; JournalRows counts all eligible saved rows.')
    if writes: st.dataframe(writes[::-1],hide_index=True,width='stretch')
    else: st.info('No write receipts yet in this process.')
    if st.button('Inspect three prior V7.5G records (read only)',key='prior77'):
        st.session_state['prior77_result']=prior_journal(url)
    if 'prior77_result' in st.session_state:
        prior,err=st.session_state['prior77_result']
        if err: st.warning(err)
        else: st.dataframe(prior,hide_index=True,width='stretch')
    if st.button('Scan previous sports journals',key='archive769'):
        archive_inventory.clear()
        st.session_state['archive769_result']=archive_inventory(url)
    if 'archive769_result' in st.session_state:
        result=st.session_state['archive769_result']
        if isinstance(result, (tuple, list)) and len(result)==2:
            inventory,err=result
        else:
            inventory,err=[], 'Invalid scan result; press Scan previous sports journals again.'
        if err: st.warning(err)
        if inventory: st.dataframe(inventory,hide_index=True,width='stretch')
        elif not err: st.info('No matching sports tables found.')
    return rows,error


def apple_shape(payload):
    # Structural samples only: no guessed event URLs or credentials.
    out=[]
    def walk(v,path,depth):
        if len(out)>=60 or depth>5: return
        if isinstance(v,dict):
            out.append(dict(Path=path,Type='object',Count=len(v),Fields=', '.join(str(k)[:80] for k in list(v)[:15])))
            for k,x in list(v.items())[:12]:
                if isinstance(x,(dict,list)): walk(x,path+'.'+str(k)[:80],depth+1)
        elif isinstance(v,list):
            out.append(dict(Path=path,Type='array',Count=len(v),Fields=''))
            if v: walk(v[0],path+'[0]',depth+1)
    walk(payload,'$',0)
    return out


def apple_diagnostics():
    st.subheader('Apple Sports · experimental connection test')
    st.caption('Unofficial endpoints; no Apple-derived scores, predictions or grading yet. ESPN remains authoritative for this dashboard. Manual probe only; cached for two minutes.')
    if st.button('Test Apple Sports feed', key='apple_probe_768'):
        st.session_state['apple_probe_768_result'] = apple_feed_probe()
    if st.session_state.get('apple_probe_768_result'):
        result, stamp, shapes = st.session_state['apple_probe_768_result']
        st.caption('Probe completed: '+local(stamp))
        st.dataframe(result, hide_index=True, width='stretch')
        if shapes:
            for source in ('Update directory','Manifest'):
                st.caption(source+' structural sample')
                st.dataframe([r for r in shapes if r.get('Source')==source],hide_index=True,width='stretch')


def research_combo_groups(candidates, stamp):
    """Research-only ordering; no calibrated probabilities or execution advice."""
    pool={}
    for _,m,r,*_ in candidates:
        if m['sport'] not in FEATURED or not m.get('game',{}).get('event_id'):
            continue
        start=parse_date(m['start'])
        if not start or start<=stamp or m['game'].get('state')!='pre': continue
        if not r.get('preview') and r.get('result')!='PENDING': continue
        value=experimental_value(r,m,None)
        if not value or not math.isfinite(value[0]) or value[0]<=.5: continue
        key=(m['sport'],str(m['game']['event_id']))
        row=dict(sport=key[0],event_id=key[1],selection=r['selection'],home=m['home'],away=m['away'],start=m['start'],preview=bool(r.get('preview')),coverage=r['confidence'],score=value[0],model= 'ROYAL_766',kind='WINNER',line=None,created_at=stamp.isoformat())
        if key not in pool or (pool[key]['preview'] and not row['preview']): pool[key]=row
    ranked=sorted(pool.values(),key=lambda r:(-r['score'],r['coverage']!='RESEARCH B',r['preview'],r['sport'],r['event_id']))
    used=set(); groups={}
    for size in (2,3,5,10):
        if len(ranked)<size:
            groups[size]=[]; continue
        key=lambda r:(r['sport'],r['event_id'])
        unused=[r for r in ranked if key(r) not in used]
        reused=[r for r in ranked if key(r) in used]
        legs=(unused+reused)[:size]
        groups[size]=legs
        used.update(key(r) for r in legs)
    return groups,len(ranked)


@st.fragment(run_every='5s' if page=='Live' else None)
def dashboard():
    snapshot,busy,error=royal_elite_worker_75g().poll(royal_secret("QUEEN_DATABASE_URL"),"")
    if snapshot is None:
        st.info('Loading initial research in background. Navigation remains available.' if not error else 'Research failed: '+error+'; retry scheduled.')
        return
    if not isinstance(snapshot, (tuple, list)) or len(snapshot) not in (8, 9):
        st.warning('Research snapshot format changed. Waiting for a fresh snapshot.')
        return
    today,index,active,market_cards,diagnostics,rows,storage_ok,storage_error=snapshot[:8]
    today=utc().astimezone(CT).date()
    previews=snapshot[8] if len(snapshot)==9 and isinstance(snapshot[8],dict) else {}
    if storage_ok: st.caption('Permanent V7.6.6 fresh research journal connected')
    else: st.warning('Permanent storage unavailable; accuracy totals are not available. '+str(storage_error or 'Unknown error'))
    if busy: st.caption('Updating research in background; showing last completed snapshot.')
    if error: st.warning('Latest research refresh failed: '+error)
    if page in ('Picks','Live','Results','Stats'):
        _, direct_rows, direct_error = journal_audit(royal_secret("QUEEN_DATABASE_URL"))
        if direct_error is None:
            rows=direct_rows; storage_ok=True
        else:
            st.warning('Direct journal read failed: '+direct_error+'; showing research snapshot.')
    saved=all(r['result']!='UNSAVED' for r in rows) and storage_ok
    w=sum(r['result']=='WIN' for r in rows); l=sum(r['result']=='LOSS' for r in rows)
    if page in ('Results','Stats'):
        from collections import Counter
        counts=Counter(pending_status(r,index) for r in rows)
        for c,label,value in zip(st.columns(4),['Correct','Wrong','Accuracy','Pending'],[w,l,f'{w/(w+l):.1%}' if w+l else '—',sum(r['result']=='PENDING' for r in rows)]): c.metric(label,value if saved else '—')
    if page in ('Results','Stats') and saved:
        focus_rows=[r for r in rows if r.get('sport') in FEATURED]
        fw=sum(r['result']=='WIN' for r in focus_rows); fl=sum(r['result']=='LOSS' for r in focus_rows)
        st.caption('Above: all sports, including NHL research.')
        st.subheader('Royal Focus · NBA / NFL / NCAAF')
        for c,label,value in zip(st.columns(4),['Correct','Wrong','Accuracy','Pending'],[fw,fl,f'{fw/(fw+fl):.1%}' if fw+fl else '—',sum(r['result']=='PENDING' for r in focus_rows)]): c.metric(label,value)
        st.caption(f"Pushes: {counts['PUSH']} · Upcoming: {counts['UPCOMING']} · Live: {counts['LIVE']} · Awaiting verified final: {counts['AWAITING VERIFIED FINAL']} · Final needs review: {counts['FINAL / NEEDS REVIEW']}")
    if page in ('Picks','Live','Research'):
        allowed=FEATURED if page=='Picks' else FOCUS
        selected=st.segmented_control('Sport',['All',*allowed],default='All',key='sport_filter_'+page) or 'All'
        horizon=st.segmented_control('Schedule',['Today','Next 3 days','Next 7 days'],default='Next 3 days',key='royal782_schedule_'+page) or 'Next 3 days' if page in ('Picks','Research') else 'Today'
        days={'Today':0,'Next 3 days':2,'Next 7 days':6}[horizon]

        display=[]
        for sport,g in active:
            if sport not in allowed: continue
            if selected!='All' and sport!=selected: continue
            start=parse_date(g['date'])
            if not start or (page=='Live' and g['state']!='in') or (page in ('Picks','Research') and not today<=start.astimezone(CT).date()<=today+timedelta(days=days)): continue
            home=g['home']; away=next((t for t in g['teams'] if t!=home),None)
            if not home or not away: continue
            matched=[m for m in market_cards if m['sport']==sport and _unique_score_team(m['home'],g['teams'],sport)==home and _unique_score_team(m['away'],g['teams'],sport)==away and abs((parse_date(m['start'])-start).total_seconds())<7200]
            display.append(dict(sport=sport,home=home,away=away,start=g['date'],game=g,**({k:matched[0][k] for k in ('prob','spread','total')} if len(matched)==1 else {})))
        if page=='Picks':
            for m in market_cards:
                if m['sport'] not in allowed: continue
                if selected!='All' and m['sport']!=selected: continue
                if not today<=parse_date(m['start']).astimezone(CT).date()<=today+timedelta(days=days): continue
                if any(x['sport']==m['sport'] and _unique_score_team(m['home'],[x['home'],x['away']],m['sport'])==x['home'] and _unique_score_team(m['away'],[x['home'],x['away']],m['sport'])==x['away'] and abs((parse_date(x['start'])-parse_date(m['start'])).total_seconds())<7200 for x in display): continue
                display.append(m)
        live_items=[m for m in display if m.get('game',{}).get('state')=='in']
        if page=='Live':
            st.subheader('🔴 Royal Live Arena')
            st.caption('Score snapshot completed '+datetime.fromtimestamp(time.time()-max(0,time.monotonic()-royal_elite_worker_75g().finished),CT).strftime('%I:%M:%S %p CT')+' · No live predictions')
            if not live_items: st.info('No games confirmed in progress in this snapshot.')
            for i,m in enumerate(live_items):
                if i%3==0: live_cols=st.columns(3)
                g=m['game']; esc=lambda x:html.escape(str(x),quote=True)
                def live_team(t):
                    u=g.get('logos',{}).get(t,'') or ''
                    logo=f'<img src="{esc(u)}" width="36" height="36">' if u.startswith('https://') else ''
                    return f'<div class="live-team">{logo}<span>{esc(t)}</span><b>{esc(g.get("scores",{}).get(t,"—"))}</b></div>'
                saved_pick=next((r for r in rows if r.get('result')!='UNSAVED' and r['sport']==m['sport'] and r['event_id']==g.get('event_id') and r['kind']=='WINNER' and r['phase']=='PREGAME'),None)
                note='♛ Pregame selection: '+saved_pick['selection'] if saved_pick else 'ROYAL MONITOR · No locked pick'
                with live_cols[i%3]:
                    st.markdown(f'<div class="royal-live-card"><small style="color:#bda5df">{league_mark(m["sport"])} · {esc(g.get("detail", "In progress"))}</small>{live_team(m["away"])}{live_team(m["home"])}<small style="color:#f3cf7c">{esc(note)}</small></div>',unsafe_allow_html=True)
        unit_value=0.0
        if page=='Picks':
            unit_value=st.number_input('Paper value of 1 unit ($)',min_value=0.0,max_value=10000.0,value=5.0,step=0.5,key='royal784_unit')
            daily_cap=st.number_input('Daily paper exposure cap (U)',min_value=0.0,max_value=100.0,value=5.0,step=.5,key='royal785_cap')
            st.caption('Experimental lock grades, not calibrated confidence or wager advice. Prices are snapshots; refresh to check again. Preview and stale quotes receive 0U.')
            st.subheader('♛ Royal Selections')
            st.caption('NBA / NFL / NCAAF · Upcoming games only. Completed picks are in Results. Locked predictions are immutable; future previews are not locked.')
        candidates=[]
        for m in display:
            g=m.get('game',{}); key=(m['sport'],g.get('event_id'))
            if not g: continue
            saved_rows=[r for r in rows if (r['sport'],r['event_id'])==key and r['kind']=='WINNER' and r['phase']=='PREGAME']
            r=saved_rows[0] if saved_rows and saved and saved_rows[0].get('result')!='UNSAVED' else None
            if page=='Live': continue
            if g.get('state')!='pre': continue
            if not parse_date(m['start']) or parse_date(m['start']) <= utc(): continue
            if r and r.get('result') != 'PENDING': continue
            if not r:
                preview=next((v for v in previews.get(key,[]) if v[0]=='WINNER'),None)
                if not preview:
                    history=[v for (league,_),v in index.items() if league==m['sport'] and v.get('completed')]
                    preview=next((v for v in forecast(g,m['sport'],history,None,None,preview=True) if v[0]=='WINNER'),None)
                if preview:
                    _,selection,_,confidence,why=preview
                    r=dict(selection=selection,confidence=confidence,why=why,preview=True)
            if not r: continue
            slug,markets,status,mapping_ok=retained_mapping(m)
            ml=[x for x in markets if x['category']=='Moneyline']
            q=None; price=None; both=''; team_prices={}; quote_ts=None; fresh=False
            if len(ml)==1:
                item=ml[0]; q,status,quote_ts,fresh=poly_quote(item['slug']); fresh=fresh and mapping_ok
                sides=[item[k] for k in ('long','short')]
                if q and set(sides)==set(g['teams']) and r['selection'] in sides:
                    team_prices=dict(zip(sides,q)); price=q[sides.index(r['selection'])] if fresh else None; both=('Ask prices: ' if fresh else 'Stale ask prices: ')+' / '.join(f'{t} {v*100:.1f}¢' for t,v in zip(sides,q))
                elif q: status='Team-side mapping unverified'
            elif slug: status='Unique moneyline not identified'
            # Research coverage is not a calibrated probability or proof of value.
            spread_items=[x for x in markets if x['category']=='Spread' and x['line'] is not None and math.isfinite(x['line']) and x['main_line'] is not None and abs(abs(x['line'])-abs(x['main_line']))<1e-8]
            alternate_items=sorted([x for x in markets if x['category']=='Spread' and x['line'] is not None and math.isfinite(x['line']) and x['short_title'] and re.search(r'[+-]\d',x['short_title'])],key=lambda x:abs(abs(x['line'])-abs(projection_margin(r,m))))[:4] if page=='Research' else []
            spread_labels={x['short_title'] for x in spread_items if x['short_title'] and re.search(r'[+-]\d',x['short_title']) and not re.search(r'\b(?:half|quarter|period|q[1-4]|f5)\b',x['short_title'],re.I)}
            spread_text=next(iter(spread_labels)) if len(spread_labels)==1 else 'Unverified'
            r=dict(r,market_spread=spread_text,team_prices=team_prices,quote_ts=quote_ts,quote_fresh=fresh)
            r['side_research']={}
            for t in g['teams']:
                v=experimental_value(dict(r,selection=t),m,team_prices.get(t) if fresh else None)
                if v:r['side_research'][t]=v
            r['discovery_counts']=(len(ml),sum(x['category']=='Spread' for x in markets))
            r['spread_research']=[]
            if page=='Research':
                totals=[x for x in markets if x['category']=='Total' and x['line'] is not None and x['main_total'] is not None and abs(x['line']-x['main_total'])<1e-8]
                for x in totals[:2]:
                    sq,ss,ts,sf=poly_quote(x['slug'])
                    r['spread_research'].append(('Listed main total: '+x['short_title'],sq if sf and mapping_ok else None,ss))
            for x in (spread_items or alternate_items)[:4]:
                if not x['short_title']:continue
                sq,ss,ts,sf=poly_quote(x['slug'])
                r['spread_research'].append((('Main candidate: ' if x in spread_items else 'Listed alternate / main unverified: ')+x['short_title'],sq if sf and mapping_ok else None,ss))
            candidates.append((0 if r['confidence']=='RESEARCH B' else 1,m,r,price,slug,status,both))
        if page in ('Picks','Research'):
            candidates.sort(key=lambda x:(x[0],x[1]['start']))
            if page=='Picks': candidates.sort(key=lambda x:(x[2].get('preview',False),x[1]['start'],x[1]['sport'],x[1]['game']['event_id']))
            if not candidates: st.info('No qualified research previews or saved picks for this view.')
            elif page=='Picks': st.caption('Upcoming forecasts are previews unless explicitly marked LOCKED. Only same-day eligible forecasts are saved.')
            else: st.subheader('Queen’s research reasoning')
        if page=='Picks':
            # Allocate within the current view, separately for each game date.
            # Reserve no more than 0.90U per date for optional groups in total.
            now_grade=utc(); grade_map={}; remaining={}
            for _,m,r,price,*_ in candidates:
                k=(m['sport'],str(m['game']['event_id']))
                p=fresh_research_price(price,r.get('quote_ts'))
                grade_map[k]=royal_paper_grade(r,m,p,rows if saved else [],now_grade) if saved else ('D',0.,'Journal unavailable')
            for _,m,r,price,*_ in sorted(candidates,key=lambda x:(-grade_map[(x[1]['sport'],str(x[1]['game']['event_id']))][1],x[1]['start'],x[1]['sport'],str(x[1]['game']['event_id']))):
                k=(m['sport'],str(m['game']['event_id'])); day=parse_date(m['start']).astimezone(CT).date()
                grade,units,note=grade_map[k]
                left=remaining.setdefault(day,max(0.,daily_cap-min(.9,daily_cap)))
                allocated=min(units,left); remaining[day]=max(0.,left-allocated)
                grade_map[k]=(grade,allocated,note+(' · cap limited' if allocated<units else ''))
            st.caption('Paper scenario cap applies per game date within this filtered view, not across sessions or actual wagers. Up to 0.90U reserved for all optional groups.')
            st.subheader('Optional combinations · research only')
            size=st.segmented_control('Legs',[2,3,5,10],default=2,key='royal783_legs') or 2
            st.caption('Ranked independently of card order by experimental sport-adjusted projected margin; uncalibrated, not a value or safety ranking. Groups avoid repeating games across sizes when the pool allows.')
            scope=(today.isoformat(),selected,horizon)
            cache=st.session_state.setdefault('royal783_groups',{})
            regenerate=st.button('Build new research combinations',key='royal783_build')
            now=utc()
            if scope not in cache or regenerate or cache[scope][1]==0:
                groups,eligible=research_combo_groups(candidates,now)
                cache[scope]=(groups,eligible,now.isoformat())
            groups,eligible,stamp=cache[scope]
            legs=groups[size]
            current={(m['sport'],str(m['game']['event_id'])):r for _,m,r,*_ in candidates}
            current_prices={(m['sport'],str(m['game']['event_id'])):fresh_research_price(price,r.get('quote_ts')) for _,m,r,price,*_ in candidates}
            if legs:
                leg_units=[grade_map.get((r['sport'],r['event_id']),('D',0.,''))[1] if (r['sport'],r['event_id']) in current and current[(r['sport'],r['event_id'])]['selection']==r['selection'] and parse_date(r['start'])>now else 0. for r in legs]
                units=min({2:.5,3:.25,5:.1,10:.05}[size],min(leg_units,default=0.),min(.9,daily_cap)*{2:.5,3:.25,5:.1,10:.05}[size]/.9)
                stake=units*unit_value
                st.caption(f'Paper scenario: {units:g}U · ${stake:.2f} total exposure for this group (not per leg).')
                st.caption('Research snapshot · '+local(stamp)+' · held unchanged in this browser session until rebuilt. Not a saved individual prediction.')
                other_keys={(r['sport'],r['event_id']) for n,rs in groups.items() if n<size for r in rs}
                overlap=sum((r['sport'],r['event_id']) in other_keys for r in legs)
                if overlap: st.caption(f'{overlap} game(s) shared with smaller groups because the distinct-game pool is limited.')
                leg_prices=[]
                for i,r in enumerate(legs,1):
                    live_r=current.get((r['sport'],r['event_id']))
                    expired=parse_date(r['start'])<=now
                    changed=live_r is not None and live_r['selection']!=r['selection']
                    state='STARTED / EXPIRED' if expired else 'FORECAST CHANGED' if changed else 'NOT IN CURRENT VIEW' if live_r is None else 'PREVIEW' if r['preview'] else 'LOCKED PICK'
                    leg_price=current_prices.get((r['sport'],r['event_id'])) if not expired and not changed and live_r is not None else None
                    leg_prices.append(leg_price)
                    st.caption(f'Leg {i} ask: {leg_price*100:.1f}¢' if leg_price is not None else f'Leg {i} ask unavailable or stale')
                    st.markdown(f'<div class="royal-record"><div><b>{i}. {html.escape(r["selection"])}</b><small>{league_mark(r["sport"])} · {html.escape(local(r["start"]))} · {html.escape(r["coverage"])}</small></div><b class="royal-gold">{state}</b></div>',unsafe_allow_html=True)
                if len(leg_prices)==size and all(p is not None for p in leg_prices):
                    hypothetical=paper_return(stake,math.prod(leg_prices))
                    if hypothetical: st.caption(f'Illustrative multiplied-price scenario only: payout ${hypothetical[0]:.2f} · gross gain ${hypothetical[1]:.2f} if all correct. Not an exchange parlay quote; fees, dependence and fills excluded.')
                else: st.caption('Combined payout unavailable — fresh matched prices required for every leg.')
                st.caption(f'Paper singles in this group: {sum(leg_units):g}U; group adds {units:g}U; combined {sum(leg_units)+units:g}U (${(sum(leg_units)+units)*unit_value:.2f}). Weakest eligible leg limits group exposure.')
                if any(parse_date(r['start'])<=now for r in legs): st.warning('This research snapshot includes started games. Rebuild to view upcoming-only combinations.')
                buf=io.StringIO(); fields=['sport','event_id','selection','home','away','start','preview','coverage','score','model','kind','line','created_at']
                writer=csv.DictWriter(buf,fieldnames=fields); writer.writeheader(); writer.writerows(legs)
                st.download_button('Save research snapshot CSV',buf.getvalue(),file_name=f'royal_research_{size}leg_{today}.csv',mime='text/csv',key='royal783_export')
                st.caption('Research grouping only; no executable parlay quote, joint probabilities, automatic grading or orders. CSV preserves the original selections for later comparison.')
            else: st.info(f'Only {eligible} distinct eligible upcoming forecasts at snapshot; {size} needed. Rebuild to check again.')
        for i,(_,m,r,price,slug,status,both) in enumerate(candidates):
            if i%3==0: cols=st.columns(3)
            g=m['game']; esc=lambda x:html.escape(str(x),quote=True)
            def team(t):
                u=g.get('logos',{}).get(t,'') or ''
                return (f'<img src="{esc(u)}" width="24" height="24" style="vertical-align:middle"> ' if u.startswith('https://') else '')+esc(t)
            live=g.get('state')=='in'
            score=' – '.join(str(g.get('scores',{}).get(t,'—')) for t in (m['away'],m['home'])) if live else 'VS'
            detail=g.get('detail','') if live else local(m['start'])
            projected,margin=projection_summary(r,m)
            selection='♛ '+r['selection']
            value=experimental_value(r,m,price)
            fair=f'{value[0]:.0%}' if value else '—'
            edge=f'{value[1]*100:+.1f} pp' if value and value[1] is not None else '—'
            label='Preview / not locked · ' if r.get('preview') else 'LOCKED · '
            if page=='Research':
                with cols[i%3]:
                    research_row=dict(r,sport=m['sport'],home=m['home'],away=m['away'],start=m['start'],kind='WINNER',line=None,result='UNSAVED' if r.get('preview') else r.get('result','PENDING'))
                    st.markdown(prediction_card([research_row],g),unsafe_allow_html=True)
                    st.caption(label+r['confidence'])
                    if value: st.caption(f'Experimental normal-margin model: assumed uncertainty {value[2]:g} points/goals; estimate {fair}; gross price gap {edge}. Not fitted, backtested or fee-adjusted.')
                    st.caption('Market: '+status+' · '+(both or 'Price unavailable'))
                    st.caption(f'Discovery: {r["discovery_counts"][0]} moneyline / {r["discovery_counts"][1]} full-game spread contracts; main line: {r["market_spread"]}')
                    for t,v in r['side_research'].items():
                        gap=f' · illustrative gap {v[1]*100:+.1f} pp' if v[1] is not None else ''
                        st.caption(f'{t}: estimated fair {v[0]:.0%}'+gap)
                    for name,sq,ss in r['spread_research']:
                        st.caption(f'{name}: '+(f'YES {sq[0]:.0%} / NO {sq[1]:.0%}' if sq else ss))
                continue
            with cols[i%3]:
                card_row=dict(r,sport=m['sport'],home=m['home'],away=m['away'],start=m['start'],kind='WINNER',line=None,result='UNSAVED' if r.get('preview') else r.get('result','PENDING'))
                if page=='Picks':
                    p=fresh_research_price(price,r.get('quote_ts'))
                    grade,units,note=grade_map[(m['sport'],str(g['event_id']))]
                    if p is None: grade,units,note='D',0.,'Quote expired'
                    stake=units*unit_value
                    ret=paper_return(stake,p) if units>0 else None
                    card_row['unit_note']=f'Experimental lock {grade} · Paper {units:g}U · ${stake:.2f} · {note} · '+(f'Ask {p*100:.1f}¢ · payout ${ret[0]:.2f} · gross gain ${ret[1]:.2f}' if ret else 'Price / payout unavailable')
                st.markdown(prediction_card([card_row],g),unsafe_allow_html=True)
                st.caption(label+r['confidence']+' · '+(both or 'Price unavailable'))
                st.caption('Market spread: '+str(r['market_spread']))
                if slug: st.markdown(f'[Open Polymarket](https://polymarket.us/event/{slug})')
        if page in ('Picks','Research'):
            st.subheader('Upcoming Battles')
            upcoming=sorted([m for m in display if m.get('game',{}).get('state')=='pre'],key=lambda m:m['start'])
            if upcoming:
                render_games(upcoming)
            else: st.info('No upcoming games returned by the schedule feed.')
            st.caption('Future previews are not saved or graded until their game day. Schedule includes games without sufficient research.')
        st.caption('NHL is research-only; all its outcomes remain in the journal. Same-day eligible winner forecasts are recorded regardless of margin; historical records retain their original selection rules. Spread quotes are informational; player-yardage selections withheld until player models are validated. Opposite-team estimates do not model ties or voids. Live scores only; no live predictions.')
    elif page=='Results':
        if rows and saved:
            view=st.selectbox('Results filter',['All','Graded','Pending'])
            shown=[dict(r,grading_status=pending_status(r,index)) for r in rows if view=='All' or (view=='Pending')==(r['result']=='PENDING')]
            for r in sorted(shown,key=lambda r:r.get('start',''),reverse=True)[:100]:
                e=lambda v:html.escape(str(v),quote=True)
                result=r['grading_status'] if r['result']=='PENDING' else r['result']; color={'WIN':'#62d6a1','LOSS':'#ff8585'}.get(result,'#e9c46a')
                st.markdown(f'<div class="royal-record"><div><b>{e(r["selection"])}</b><small>{e(r["sport"])} · {e(r["kind"])} · {e(local(r["start"]))}</small></div><b style="color:{color}">{e(result)}</b></div>',unsafe_allow_html=True)
        else: st.info('No verified saved results available.')
    else:
        if saved and rows:
            summary=[]
            for field in ('sport','kind','phase','confidence'):
                for value in sorted({r[field] for r in rows}):
                    group=[r for r in rows if r[field]==value]; a=sum(r['result']=='WIN' for r in group); b=sum(r['result']=='LOSS' for r in group)
                    summary.append(dict(Group=field.title(),Category=value,Correct=a,Wrong=b,Pending=sum(r['result']=='PENDING' for r in group)))
            st.dataframe(summary,hide_index=True,width='stretch')
        journal_diagnostics(royal_secret("QUEEN_DATABASE_URL"))
        apple_diagnostics()
        st.subheader('Historical probability check')
        st.caption('Retrospective walk-forward check: earlier games only, up to 100 recent games per sport. No market prices or fees; not a profitability test.')
        if st.button('Run historical check'):
            checks=[]
            for sport in FOCUS:
                hist=[g for (league,_),g in index.items() if league==sport and g.get('completed')]
                checks.append(dict(Sport=sport,**historical_model_check(sport,hist)))
            st.session_state['royal765_checks']=checks
        if st.session_state.get('royal765_checks'):
            st.dataframe(st.session_state['royal765_checks'],hide_index=True,width='stretch')
        st.dataframe(diagnostics,width='stretch',hide_index=True)
    st.caption('V7.8.5 Royal Stability · fresh V7.6.6 research journal; prior records retained separately. Experimental fair prices use assumed uncertainty, not calibrated probabilities. Gross gaps exclude fees. Background snapshots refresh after completion; automatic Odds API requests disabled. No live trading.')
@st.fragment(run_every='5s')
def research_heartbeat():
    snapshot,busy,error=royal_elite_worker_75g().poll(royal_secret('QUEEN_DATABASE_URL'),'')
    st.caption('Research worker: '+('retrying' if error else 'updating' if busy else 'ready')+' · Picks stay still until refreshed.')
    if st.button('Refresh displayed data',key='royal78_refresh'):
        journal_audit.clear()
        st.rerun()
    if snapshot is not None and st.session_state.get('royal78_initial_wait'):
        st.session_state['royal78_initial_wait']=False
        st.rerun()

if page!='Live':
    initial,_,_=royal_elite_worker_75g().poll(royal_secret('QUEEN_DATABASE_URL'),'')
    st.session_state['royal78_initial_wait']=initial is None
    research_heartbeat()
dashboard()
