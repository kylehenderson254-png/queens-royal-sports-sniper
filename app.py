import os, re, sqlite3, hashlib, html, math, csv, io, zlib, base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote
from difflib import SequenceMatcher
import requests
import streamlit as st

st.set_page_config(page_title="ROYAL SPORTS SNIPER V7.1 | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
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
@st.cache_data(ttl=3600,show_spinner=False)
def royal_consensus(sport):
    key=royal_secret('ODDS_API_KEY') or royal_secret('THE_ODDS_API_KEY')
    if not key: return [],'Odds API key not found'
    try:
        r=requests.get('https://api.the-odds-api.com/v4/sports/'+ODDS_KEYS[sport]+'/odds',params={'apiKey':key,'regions':'us','markets':'h2h,spreads,totals','oddsFormat':'decimal'},timeout=10)
        r.raise_for_status(); data=r.json()
        return data if isinstance(data,list) else [], 'Sportsbook consensus · hourly cache'
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
            if hs is not None and aws is not None and hs!=aws: return [('WINNER',home if hs>aws else away,None,'LIVE SCORE BASELINE','Current score leader; historical form unavailable.')]
        return []
    hp=(h[0]+a[1])/2; ap=(a[0]+h[1])/2
    phase='LIVE' if game.get('state')=='in' else 'PREGAME'
    if phase=='LIVE':
        hs=numeric(game['scores'].get(home)); aws=numeric(game['scores'].get(away))
        if hs is None or aws is None or hs==aws: return []
        # Explicit live score baseline, not a calibrated win probability.
        return [('WINNER',home if hs>aws else away,None,'LIVE SCORE BASELINE',f'Current score {hs:g}–{aws:g}; leader only, clock not modeled.')]
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
@st.cache_data(ttl=90,show_spinner=False)
def fetch_board(sport, dates):
    try:
        params={'dates':dates,'limit':500}
        if sport=='NCAAF': params['groups']=80
        r=requests.get('https://site.api.espn.com/apis/site/v2/sports/'+ESPN[sport]+'/scoreboard',params=params,timeout=7)
        r.raise_for_status()
        events=r.json()['events']
        return [g for e in events for g in _score_records(e,sport)], 'OK'
    except Exception as e:
        log.warning('ESPN %s: %s',sport,type(e).__name__)
        return [],type(e).__name__

def date_range(a,b): return a.strftime('%Y%m%d')+'-'+b.strftime('%Y%m%d')

def research(sport, today):
    # bounded range requests, no daily backfill
    span=7 if sport in ('NBA','NHL') else 28
    games,status=fetch_board(sport,date_range(today-timedelta(days=span),today-timedelta(days=1)))
    odds,odds_status=royal_consensus(sport)
    return games,status,odds,odds_status

def persist(new,index):
    import psycopg2
    from psycopg2.extras import Json
    url=royal_secret('QUEEN_DATABASE_URL')
    if not url: raise RuntimeError('Missing database URL')
    conn=psycopg2.connect(url,connect_timeout=5,sslmode='require',options='-c statement_timeout=6000 -c lock_timeout=3000')
    try:
        with conn:
            with conn.cursor() as c:
                c.execute('SELECT pg_advisory_xact_lock(254702)')
                c.execute("CREATE TABLE IF NOT EXISTS royal_sports_accuracy_v7 (id text PRIMARY KEY,payload jsonb NOT NULL,result text NOT NULL DEFAULT 'PENDING')")
                for r in new:
                    key=f"{r['sport']}:{r['event_id']}:{r['phase']}:{r['kind']}"
                    c.execute('INSERT INTO royal_sports_accuracy_v7(id,payload) VALUES (%s,%s) ON CONFLICT DO NOTHING',(key,Json(r)))
                c.execute("SELECT id,payload FROM royal_sports_accuracy_v7 WHERE result='PENDING'")
                for key,r in c.fetchall():
                    result=grade_new(r,index.get((r['sport'],r['event_id'])))
                    if result: c.execute("UPDATE royal_sports_accuracy_v7 SET result=%s WHERE id=%s AND result='PENDING'",(result,key))
                c.execute('SELECT payload,result FROM royal_sports_accuracy_v7')
                return [dict(r,result=result) for r,result in c.fetchall()]
    finally: conn.close()

st.markdown("""<style>.block-container{max-width:1500px!important;padding-top:2rem!important;width:96%!important}.stApp{background:#100d20}div[data-testid=stMetric]{border:1px solid #67449b;border-radius:12px;padding:12px}img{object-fit:contain}</style>""",unsafe_allow_html=True)
banners=[p for folder in (Path('.'),Path('assets'),Path('images')) if folder.is_dir() for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg','.webp') and 'banner' in p.stem.lower()]
if banners: st.image(str(sorted(banners)[0]),width='stretch')
else: st.title('ROYAL SPORTS SNIPER | 254')
st.caption('V7.1 · PREDICTIONS FIRST · NBA / NFL / NHL / NCAAF')
page=st.segmented_control('View',['Picks','Live','Results','Stats'],default='Picks',label_visibility='collapsed') or 'Picks'
@st.fragment(run_every='120s')
def dashboard():
    today=utc().astimezone(CT).date()
    with ThreadPoolExecutor(max_workers=4) as pool:
        boards=list(pool.map(lambda sport:fetch_board(sport,date_range(today-timedelta(days=2),today+timedelta(days=3))),FOCUS))
    index={(sport,g['event_id']):g for sport,(games,status) in zip(FOCUS,boards) for g in games if g['event_id']}
    active=[(sport,g) for (sport,_),g in index.items() if g['state'] in ('pre','in')]
    if page in ('Picks','Live'):
        st.subheader('Games found')
        for sport,g in sorted(active,key=lambda x:str(x[1]['date'])):
            if page=='Live' and g['state']!='in': continue
            st.write(f"{sport} · {' vs '.join(g['teams'])} · {g['detail'] or local(g['date'])}" + (f" · {g['scores']}" if g['state']=='in' else ''))
        if not active: st.info('No upcoming or live games returned by the scoreboard.')
    with ThreadPoolExecutor(max_workers=4) as pool:
        research_rows=list(pool.map(lambda sport:research(sport,today),FOCUS))
    new=[]; diagnostics=[]
    for sport,(history,status,events,odds_status),(_,board_status) in zip(FOCUS,research_rows,boards):
        diagnostics.append(dict(Sport=sport,Scoreboard=board_status,History=len(history),Odds=len(events),Status=odds_status))
        for league,g in active:
            if league!=sport: continue
            start=parse_date(g['date']); home=g['home']; away=next((t for t in g['teams'] if t!=home),None)
            if not start or not home or not away or (g['state']=='pre' and not utc()<start<=utc()+timedelta(hours=72)): continue
            matches=[e for e in events if parse_date(e.get('commence_time')) and abs((parse_date(e['commence_time'])-start).total_seconds())<7200 and _unique_score_team(e.get('home_team'),g['teams'],sport)==home and _unique_score_team(e.get('away_team'),g['teams'],sport)==away]
            prob,spread,total=royal_lines(matches[0]) if len(matches)==1 else (None,None,None)
            predictions=forecast(g,sport,history,spread,total)
            if not predictions and g['state']=='pre' and prob is not None and abs(prob-.5)>.01:
                predictions=[('WINNER',home if prob>.5 else away,None,'SPORTSBOOK BASELINE',f'No-vig consensus {max(prob,1-prob):.1%}; independent form unavailable.')]
            for kind,selection,line,confidence,why in predictions:
                new.append(dict(sport=sport,event_id=g['event_id'],home=home,away=away,start=g['date'],phase='LIVE' if g['state']=='in' else 'PREGAME',kind=kind,selection=selection,line=line,confidence=confidence,why=why,created_at=utc().isoformat()))
    try:
        rows=persist(new,index); st.caption('Permanent prediction storage connected')
    except Exception as e:
        log.error('Prediction storage: %s',type(e).__name__)
        st.warning('Storage unavailable: '+type(e).__name__+'. Showing unsaved research only.'); rows=[dict(r,result='UNSAVED') for r in new]
    w=sum(r['result']=='WIN' for r in rows); l=sum(r['result']=='LOSS' for r in rows)
    for c,label,value in zip(st.columns(4),['Correct','Wrong','Accuracy','Pending'],[w,l,f'{w/(w+l):.1%}' if w+l else '—',sum(r['result']=='PENDING' for r in rows)]): c.metric(label,value)
    if page in ('Picks','Live'):
        st.subheader('Queen’s predictions')
        shown=[r for r in rows if r['result'] in ('PENDING','UNSAVED') and (r['sport'],r['event_id']) in index and index[(r['sport'],r['event_id'])]['state'] in ('pre','in') and (page!='Live' or r['phase']=='LIVE')]
        for r in sorted(shown,key=lambda r:r['start']):
            with st.container(border=True):
                st.write(f"**{r['sport']} · {r['away']} vs {r['home']}**")
                line=r['line']; suffix='' if line is None else f' · {line:g}' if r['kind']=='TOTAL' else f' · {line if r["selection"]==r["home"] else -line:+g}'
                st.write(f"{r['phase']} · {r['kind']} · {r['selection']}{suffix}"); st.caption(r['confidence']+' · '+r['why'])
        if not shown: st.info('No qualifying predictions. Check feed status below.')
    elif page=='Results':
        st.dataframe(rows,width='stretch',hide_index=True)
    else:
        for field in ('sport','kind','phase','confidence'):
            st.subheader(field.title())
            for value in sorted({r[field] for r in rows}):
                group=[r for r in rows if r[field]==value]; a=sum(r['result']=='WIN' for r in group); b=sum(r['result']=='LOSS' for r in group)
                st.write(f'{value}: {a} correct / {b} wrong')
    st.caption('Experimental research, not calibrated probabilities. Results use final scoreboard; no P&L. Refreshes while connected.')
    st.dataframe(diagnostics,width='stretch',hide_index=True)
dashboard()
