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

st.set_page_config(page_title="ROYAL SPORTS SNIPER V7.4.5 | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
CT = ZoneInfo("America/Chicago")
BASE = "https://gateway.polymarket.us"
LEAGUES = {"NBA":"nba", "NFL":"nfl", "NCAAB":"ncaab", "NCAAF":"ncaaf", "NHL":"nhl", "MLB":"mlb", "WNBA":"wnba", "MLS":"mls", "EPL":"epl", "UFC":"ufc", "Tennis":"tennis/all"}
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
            "teams":names,"scores":scores,"sets":sets,
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
ODDS_KEYS={'NBA':'basketball_nba','NFL':'americanfootball_nfl','NHL':'icehockey_nhl','NCAAF':'americanfootball_ncaaf'}
@st.cache_data(ttl=900,show_spinner=False)
def royal_consensus(sport, _key):
    key=_key
    if not key: return [],'Odds API key not found'
    try:
        r=requests.get('https://api.the-odds-api.com/v4/sports/'+ODDS_KEYS[sport]+'/odds',params={'apiKey':key,'regions':'us','markets':'h2h,spreads,totals','oddsFormat':'decimal'},timeout=10)
        r.raise_for_status(); data=r.json()
        return data if isinstance(data,list) else [], 'Sportsbook consensus · 15-minute cache'
    except Exception as exc: return [],'Odds feed unavailable: '+type(exc).__name__

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

def forecast(game, sport, history, spread, total):
    home=game.get('home'); teams=game.get('teams',[])
    if home not in teams or len(teams)!=2: return []
    away=next(t for t in teams if t!=home)
    def form(team):
        rows=[]
        for g in history:
            if not g.get('completed') or g.get('state')!='post' or team not in g.get('teams',[]): continue
            start=parse_date(g.get('date')); target=parse_date(game.get('date'))
            if not start or not target or start>=target: continue
            other=next(t for t in g['teams'] if t!=team)
            a=numeric(g['scores'].get(team)); b=numeric(g['scores'].get(other))
            if a is not None and b is not None: rows.append((start,a,b))
        rows=sorted(rows,reverse=True)[:5]
        if len(rows)<3: return None
        return mean(r[1] for r in rows),mean(r[2] for r in rows),len(rows)
    h,a=form(home),form(away)
    if not h or not a:
        if game.get('state')=='in':
            hs=numeric(game['scores'].get(home)); aws=numeric(game['scores'].get(away))
            if hs is not None and aws is not None and hs!=aws: return []
        return []
    hp=(h[0]+a[1])/2; ap=(a[0]+h[1])/2
    phase='LIVE' if game.get('state')=='in' else 'PREGAME'
    if phase=='LIVE':
        hs=numeric(game['scores'].get(home)); aws=numeric(game['scores'].get(away))
        if hs is None or aws is None or hs==aws: return []
        # Explicit live score baseline, not a calibrated win probability.
        return []
    why=f'Last {h[2]}/{a[2]} completed games: projected score {home} {hp:.1f}, {away} {ap:.1f}. Simple scoring-form baseline; uncalibrated.'
    out=[]
    if hp!=ap: out.append(('WINNER',home if hp>ap else away,None,'FORM BASELINE',why))
    if spread is not None and abs(hp-ap+spread)>1e-6: out.append(('SPREAD',home if hp-ap+spread>0 else away,spread,'FORM BASELINE',why))
    if total is not None and abs(hp+ap-total)>1e-6: out.append(('TOTAL','OVER' if hp+ap>total else 'UNDER',total,'FORM BASELINE',why))
    return out

def grade_new(r,g):
    if not g or not g.get('completed') or g.get('state')!='post': return None
    if set(g.get('teams',[]))!={r['home'],r['away']}: return None
    h=numeric(g['scores'].get(r['home'])); a=numeric(g['scores'].get(r['away']))
    if h is None or a is None: return None
    if r['kind']=='WINNER':
        if h==a: return 'PUSH'
        if g.get('winner') not in (r['home'],r['away']): return None
        return 'WIN' if g['winner']==r['selection'] else 'LOSS'
    if r['kind']=='SPREAD': v=h-a+r['line']; positive=r['selection']==r['home']
    else: v=h+a-r['line']; positive=r['selection']=='OVER'
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
        for params in ({'dates':day, **({'groups':80} if sport=='NCAAF' else {})}, {'dates':day}):
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
    offsets=range(1,15) if sport in ('NBA','NHL') else [i for i in range(1,36) if (today-timedelta(days=i)).weekday() in ((3,4,5,6,0) if sport=='NFL' else (3,4,5))]
    games,status=collect_days(sport,[today-timedelta(days=i) for i in offsets],history_day)
    odds,odds_status=royal_consensus(sport,odds_key)
    return games,status,odds,odds_status

def persist(new,index,url):
    import psycopg2
    from psycopg2.extras import Json, execute_values
    if not url: raise RuntimeError('Missing database URL')
    conn=psycopg2.connect(url,connect_timeout=12,sslmode='require')
    try:
        with conn:
            with conn.cursor() as c:
                c.execute("SET LOCAL statement_timeout = '15000ms'")
                c.execute("SET LOCAL lock_timeout = '5000ms'")
                c.execute('SELECT pg_advisory_xact_lock(254740)')
                c.execute("CREATE TABLE IF NOT EXISTS royal_sports_accuracy_v74_test (id text PRIMARY KEY,payload jsonb NOT NULL,result text NOT NULL DEFAULT 'PENDING')")
                if new:
                    execute_values(c,'INSERT INTO royal_sports_accuracy_v74_test(id,payload) VALUES %s ON CONFLICT DO NOTHING',[(f"{r['sport']}:{r['event_id']}:{r['phase']}:{r['kind']}",Json(r)) for r in new],page_size=200)
                c.execute("SELECT id,payload FROM royal_sports_accuracy_v74_test WHERE result='PENDING'")
                for key,r in c.fetchall():
                    result=grade_new(r,index.get((r['sport'],r['event_id'])))
                    if result: c.execute("UPDATE royal_sports_accuracy_v74_test SET result=%s WHERE id=%s AND result='PENDING'",(result,key))
                c.execute('SELECT payload,result FROM royal_sports_accuracy_v74_test')
                return [dict(r,result=result) for r,result in c.fetchall()]
    finally: conn.close()

@st.cache_data(ttl=86400,show_spinner=False)
def logo_directory(sport):
    try:
        r=requests.get('https://site.api.espn.com/apis/site/v2/sports/'+ESPN[sport]+'/teams',params={'limit':500},timeout=5); r.raise_for_status()
        teams=r.json()['sports'][0]['leagues'][0]['teams']
        return {clean(t['team']['displayName']):_provider_image_url(t['team'].get('logos')) for t in teams}
    except Exception: return {}
def logo_for(sport,name): return logo_directory(sport).get(clean(name),'')

st.markdown("""<style>.block-container{max-width:none!important;padding:1rem 2vw 2rem!important;width:100%!important}.stApp{background:radial-gradient(ellipse at top,#29134b 0%,#100d20 42%,#090812 100%)}div[data-testid=stMetric]{border:1px solid #67449b;border-radius:12px;padding:12px}img{object-fit:contain}div[data-testid=stImage]{width:100%!important}div[data-testid=stImage] img{width:100%!important;border-radius:12px}div[data-testid=stSegmentedControl] button{min-height:44px;font-weight:700}div[data-testid=stMetric]{background:#1c1532}.rgame{background:linear-gradient(125deg,#201435,#100d1d);border:1px solid #634293;border-radius:12px;padding:9px 12px;margin-bottom:5px;color:#fff;box-shadow:0 0 10px #57288a33}.rteam{display:flex;align-items:center;gap:9px;min-height:27px;font-size:13px}.rteam b{margin-left:auto}.rgame small{color:#c2addd;font-size:11px}.rpred{background:linear-gradient(135deg,#28163f,#100d1d 80%);border:1px solid #8050b4;border-radius:13px;padding:8px 10px;margin-bottom:5px;color:#fff;box-shadow:0 2px 12px #57288a33;font-size:13px}.rpred .meta{font-size:11px;color:#c6addf}.rpred .match{display:flex;align-items:center;justify-content:space-between;gap:6px;margin:5px 0}.rpred .side{flex:1;text-align:center;font-weight:700}.rpred .side.royalpick{color:#ffe39a;background:#49351f66;border:1px solid #d8a94d;border-radius:9px;padding:3px 2px;box-shadow:0 0 10px #eab54a33}.rpred .royalwinner b{color:#ffe39a}.rpred .side img{display:block;margin:0 auto 2px;width:27px;height:27px}.rpred .project{font-size:23px;font-weight:800;color:#e4c5ff;white-space:nowrap}.rpred .bet{border-top:1px solid #51346c;padding:3px 0;line-height:1.35}.rpred .bet b{color:#e0b5ff}.rpred .bet span{color:#eee;font-size:12px;line-height:1.35}.rpred .watch{color:#ffd18a;font-size:11px;margin-top:3px}.rpred .reason{color:#c6addf;font-size:11px;margin-bottom:3px}</style>""",unsafe_allow_html=True)
banners=[p for folder in (Path('.'),Path('assets'),Path('images')) if folder.is_dir() for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg','.webp') and 'banner' in p.stem.lower()]
if banners:
    banner=sorted(banners)[0]
    mime='jpeg' if banner.suffix.lower() in ('.jpg','.jpeg') else banner.suffix.lower()[1:]
    encoded=base64.b64encode(banner.read_bytes()).decode()
    st.markdown(f'<img alt="Royal Sports Sniper" style="display:block;width:100%;height:auto;max-width:none;margin:0 auto;border-radius:12px" src="data:image/{mime};base64,{encoded}">',unsafe_allow_html=True)
else: st.title('ROYAL SPORTS SNIPER | 254')
st.caption('V7.4.5 TEST · ROYAL MARKET INTELLIGENCE · NBA / NFL / NHL / NCAAF')
page=st.segmented_control('View',['Picks','Live','Results','Stats'],default='Picks',label_visibility='collapsed') or 'Picks'
def collect_snapshot(db_url,odds_key):
    today=utc().astimezone(CT).date()
    with ThreadPoolExecutor(max_workers=4) as pool:
        boards=list(pool.map(lambda sport:current_board(sport,today),FOCUS))
    index={(sport,g['event_id']):g for sport,(games,status) in zip(FOCUS,boards) for g in games if g['event_id']}
    active=[(sport,g) for (sport,_),g in index.items() if g['state'] in ('pre','in')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        research_rows=list(pool.map(lambda sport:research(sport,today,odds_key),FOCUS))
    new=[]; diagnostics=[]; market_cards=[]
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
            predictions=forecast(g,sport,history,spread,total)
            for kind,selection,line,confidence,why in predictions:
                new.append(dict(sport=sport,event_id=g['event_id'],home=home,away=away,start=g['date'],phase='LIVE' if g['state']=='in' else 'PREGAME',kind=kind,selection=selection,line=line,confidence=confidence,why=why,created_at=utc().isoformat()))
    storage_ok=False; storage_error=None
    try:
        rows=persist(new,index,db_url); storage_ok=True
    except Exception as e:
        storage_error=type(e).__name__ + (' / '+str(e.pgcode) if getattr(e,'pgcode',None) else '')
        log.error('Prediction storage: %s',storage_error)
        rows=[dict(r,result='UNSAVED') for r in new]
    return today,index,active,market_cards,diagnostics,rows,storage_ok,storage_error

class RoyalClarityWorker742A:
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
def royal_clarity_worker_742a(): return RoyalClarityWorker742A()

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
    reason=why.split(': projected score')[0]+' · Scoring-form baseline' if scores else why
    bets=''
    for x in sorted(group,key=lambda x:('WINNER','SPREAD','TOTAL').index(x['kind'])):
        label,detail=plain_bet(x)
        cls="bet royalwinner" if x["kind"]=="WINNER" else "bet"
        crown="👑 " if x["kind"]=="WINNER" else ""
        bets+=f'<div class="{cls}"><b>{crown}{esc(label)}</b> <span>— {esc(detail)}</span></div>'
    status='Not saved' if r['result']=='UNSAVED' else 'Saved research'
    return f'<div class="rpred"><div class="meta">{esc(r["sport"]+" · "+local(r["start"])+" · "+status)}</div><div class="match">{side(r["away"])}<div class="project">{esc(projected)}</div>{side(r["home"])}</div><div class="reason">Projected score · {esc(reason)}</div>{bets}<div class="watch">WATCH · Exchange price unverified · Experimental</div></div>'

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
                st.markdown(f'<div class="rgame"><small>{html.escape(m["sport"]+" · "+str(detail))}</small>{team(m["away"])}{team(m["home"])}<small>{html.escape(extra)} · {"ESPN" if g else "Odds API"}</small></div>',unsafe_allow_html=True)

@st.fragment(run_every='5s')
def dashboard():
    snapshot,busy,error=royal_clarity_worker_742a().poll(royal_secret("QUEEN_DATABASE_URL"),royal_secret("ODDS_API_KEY") or royal_secret("THE_ODDS_API_KEY"))
    if snapshot is None:
        st.info('Loading initial research in background. Navigation remains available.' if not error else 'Research failed: '+error+'; retry scheduled.')
        return
    today,index,active,market_cards,diagnostics,rows,storage_ok,storage_error=snapshot
    if storage_ok: st.caption('Permanent TEST prediction storage connected')
    else: st.warning('Permanent storage unavailable; accuracy totals are not available. '+str(storage_error or 'Unknown error'))
    if busy: st.caption('Updating research in background; showing last completed snapshot.')
    if error: st.warning('Latest research refresh failed: '+error)
    saved=all(r['result']!='UNSAVED' for r in rows) and storage_ok
    w=sum(r['result']=='WIN' for r in rows); l=sum(r['result']=='LOSS' for r in rows)
    if page in ('Results','Stats'):
        for c,label,value in zip(st.columns(4),['Correct','Wrong','Accuracy','Pending'],[w,l,f'{w/(w+l):.1%}' if w+l else '—',sum(r['result']=='PENDING' for r in rows)]): c.metric(label,value if saved else '—')
    if page in ('Picks','Live'):
        selected=st.segmented_control('Sport',['All',*FOCUS],default='All',key='sport_filter') or 'All'
        horizon=st.segmented_control('Schedule',['Today','Next 3 days','Next 7 days'],default='Today') or 'Today' if page=='Picks' else 'Today'
        days={'Today':0,'Next 3 days':2,'Next 7 days':6}[horizon]

        display=[]
        for sport,g in active:
            if selected!='All' and sport!=selected: continue
            start=parse_date(g['date'])
            if not start or (page=='Live' and g['state']!='in') or (page=='Picks' and not today<=start.astimezone(CT).date()<=today+timedelta(days=days)): continue
            home=g['home']; away=next((t for t in g['teams'] if t!=home),None)
            if not home or not away: continue
            matched=[m for m in market_cards if m['sport']==sport and _unique_score_team(m['home'],g['teams'],sport)==home and _unique_score_team(m['away'],g['teams'],sport)==away and abs((parse_date(m['start'])-start).total_seconds())<7200]
            display.append(dict(sport=sport,home=home,away=away,start=g['date'],game=g,**({k:matched[0][k] for k in ('prob','spread','total')} if len(matched)==1 else {})))
        if page=='Picks':
            for m in market_cards:
                if selected!='All' and m['sport']!=selected: continue
                if not today<=parse_date(m['start']).astimezone(CT).date()<=today+timedelta(days=days): continue
                if any(x['sport']==m['sport'] and _unique_score_team(m['home'],[x['home'],x['away']],m['sport'])==x['home'] and _unique_score_team(m['away'],[x['home'],x['away']],m['sport'])==x['away'] and abs((parse_date(x['start'])-parse_date(m['start'])).total_seconds())<7200 for x in display): continue
                display.append(m)
        live_items=[m for m in display if m.get('game',{}).get('state')=='in']
        if live_items:
            st.subheader('Live games')
            render_games(sorted(live_items,key=lambda m:m['start']))
        if not display: st.info('Live status unavailable or no games confirmed in progress. Check Stats for feed health.' if page=='Live' else 'No verified games available for this view.')
        st.subheader('♛ Queen’s pregame predictions' if page=='Picks' else 'Live research')
        st.caption('Scoring-form baseline only; injuries, rest and opponent strength are not modeled. Exact-line ties may push.')
        shown=[r for r in rows if r['result'] in ('PENDING','UNSAVED') and (r['sport'],r['event_id']) in index and index[(r['sport'],r['event_id'])]['state']=='pre' and page=='Picks' and (selected=='All' or r['sport']==selected) and parse_date(r['start']) and today<=parse_date(r['start']).astimezone(CT).date()<=today+timedelta(days=days)]
        groups={}
        for r in shown: groups.setdefault((r['sport'],r['event_id']),[]).append(r)
        ordered=sorted(groups.values(),key=lambda group:group[0]['start'])
        for i in range(0,len(ordered),3):
            for col,group in zip(st.columns(3),ordered[i:i+3]):
                with col:
                    r=group[0]; g=index.get((r['sport'],r['event_id']),{})
                    st.markdown(prediction_card(group,g),unsafe_allow_html=True)
        if not shown: st.info('No qualifying pregame research for this view.' if page=='Picks' else 'Live predictions are not enabled. Live scores only.')
        if page=='Picks':
            upcoming=[m for m in display if m.get('game',{}).get('state')!='in']
            st.subheader('Upcoming games')
            for day in sorted({parse_date(m['start']).astimezone(CT).date() for m in upcoming}):
                st.markdown('**'+day.strftime('%A, %b %d')+'**')
                render_games(sorted([m for m in upcoming if parse_date(m['start']).astimezone(CT).date()==day],key=lambda m:m['start']))
    elif page=='Results':
        if rows and saved: st.dataframe(rows,width='stretch',hide_index=True)
        else: st.info('No verified saved results available.')
    else:
        if saved and rows:
            for field in ('sport','kind','phase','confidence'):
                st.subheader(field.title())
                for value in sorted({r[field] for r in rows}):
                    group=[r for r in rows if r[field]==value]; a=sum(r['result']=='WIN' for r in group); b=sum(r['result']=='LOSS' for r in group)
                    st.write(f'{value}: {a} correct / {b} wrong')
        st.dataframe(diagnostics,width='stretch',hide_index=True)
    st.caption('Experimental scoring-form research, not calibrated probabilities. Background snapshots refresh after completion; odds cache 15 minutes. No live trading.')
dashboard()
