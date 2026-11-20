import os, math, json, sqlite3, hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict
import requests
import streamlit as st

st.set_page_config(page_title="ROYAL SPORTS SNIPER V2.9.1 | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
CT=ZoneInfo("America/Chicago")
SPORTS={
 "NBA":("basketball_nba","basketball/nba"),"NFL":("americanfootball_nfl","football/nfl"),
 "NCAAB":("basketball_ncaab","basketball/mens-college-basketball"),
 "NCAAF":("americanfootball_ncaaf","football/college-football"),
 "NHL":("icehockey_nhl","hockey/nhl"),"MLB":("baseball_mlb","baseball/mlb"),
 "WNBA":("basketball_wnba","basketball/wnba"),"MLS":("soccer_usa_mls","soccer/usa.1"),
 "EPL":("soccer_epl","soccer/eng.1"),"Tennis":("tennis_atp_french_open",None),
 "UFC":("mma_mixed_martial_arts",None)
}
# Sport availability is checked against the provider's current catalog.
DB=Path(os.getenv("ROYAL_DB_PATH","royal_sports_history.db"))
DB.parent.mkdir(parents=True,exist_ok=True)

def conn():
 c=sqlite3.connect(str(DB),timeout=20); c.row_factory=sqlite3.Row
 c.execute("PRAGMA journal_mode=WAL")
 c.execute("""CREATE TABLE IF NOT EXISTS picks (
 id TEXT PRIMARY KEY, sport TEXT, event_id TEXT, event_time TEXT, home TEXT, away TEXT,
 market TEXT, selection TEXT, point REAL, odds INTEGER, book TEXT, probability REAL,
 edge REAL, stake REAL, placed_at TEXT, status TEXT DEFAULT 'OPEN', result TEXT,
 profit REAL, settled_at TEXT, notes TEXT)""")
 c.execute("""CREATE TABLE IF NOT EXISTS parlays (
 id TEXT PRIMARY KEY, sport TEXT, legs TEXT, odds_decimal REAL, probability REAL,
 stake REAL, placed_at TEXT, status TEXT DEFAULT 'OPEN', profit REAL, settled_at TEXT)""")
 c.execute("""CREATE TABLE IF NOT EXISTS live_predictions (
 id TEXT PRIMARY KEY, sport TEXT, event_id TEXT, event_time TEXT,
 home TEXT, away TEXT, selection TEXT, home_score REAL, away_score REAL,
 period TEXT, predicted_at TEXT, confidence REAL, rationale TEXT,
 status TEXT DEFAULT 'OPEN', result TEXT, final_home REAL, final_away REAL,
 settled_at TEXT)""")
 c.commit(); return c

def now(): return datetime.now(timezone.utc)
def dt(v):
 try: return datetime.fromisoformat(str(v).replace('Z','+00:00')).astimezone(timezone.utc)
 except Exception: return now()
def local(v): return dt(v).astimezone(CT).strftime('%a %b %-d, %-I:%M %p CT')
def decimal(o): return 1+o/100 if o>0 else 1+100/abs(o) if o<0 else 1
def implied(o): return 1/decimal(o)
def american(o): return f'{o:+d}'
def money(v): return f'${v:,.2f}'
def safe_float(x,default=None):
 try: return float(x)
 except (ValueError,TypeError): return default

def get_key():
 try: return st.secrets.get('ODDS_API_KEY',os.getenv('ODDS_API_KEY',''))
 except Exception: return os.getenv('ODDS_API_KEY','')

@st.cache_data(ttl=1800,show_spinner=False)
def catalog(key):
 try:
  r=requests.get('https://api.the-odds-api.com/v4/sports/',params={'apiKey':key},timeout=12);r.raise_for_status()
  return {s['key'] for s in r.json() if s.get('active')},None
 except Exception as e: return set(),str(e)

@st.cache_data(ttl=900,show_spinner=False)
def odds_feed(sport,key):
 try:
  r=requests.get(f'https://api.the-odds-api.com/v4/sports/{sport}/odds/',params={
   'apiKey':key,'regions':'us','markets':'h2h,spreads,totals','oddsFormat':'american','dateFormat':'iso'},timeout=18)
  r.raise_for_status(); return r.json(),None,r.headers.get('x-requests-remaining','?')
 except Exception as e: return [],str(e),'?'

@st.cache_data(ttl=60,show_spinner=False)
def scoreboard(path,date):
 try:
  r=requests.get(f'https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard',params={'dates':date,'limit':500},timeout=12)
  r.raise_for_status();return r.json().get('events',[])
 except Exception:return []

def espn_data(path):
 if not path:return []
 today=now().astimezone(CT).date()
 events=[]
 for offset in range(4): events.extend(scoreboard(path,(today+timedelta(days=offset)).strftime('%Y%m%d')))
 return events

def norm(x):
 import re,unicodedata
 x=unicodedata.normalize('NFKD',str(x)).encode('ascii','ignore').decode().lower()
 return re.sub(r'[^a-z0-9]','',x)

def records_and_logos(events):
 data={}; games={}
 for event in events:
  comp=(event.get('competitions') or [{}])[0]; teams=comp.get('competitors') or []
  game={"status":event.get('status',{}).get('type',{}).get('state','pre'),
        "detail":event.get('status',{}).get('type',{}).get('shortDetail',''),"teams":{}}
  for t in teams:
   team=t.get('team') or {}; name=team.get('displayName','')
   logos=team.get('logos') or []
   logo=team.get('logo') or (logos[0].get('href') if logos else '')
   rec=None
   for rr in t.get('records') or []:
    summary=rr.get('summary','');parts=summary.split('-')
    if len(parts)>=2 and parts[0].isdigit() and parts[1].isdigit():
     w,l=int(parts[0]),int(parts[1]);
     if w+l>=5:rec=(w,l);break
   data[norm(name)]={'record':rec,'logo':logo,'name':name}
   game['teams'][norm(name)]={'score':safe_float(t.get('score')),'homeAway':t.get('homeAway')}
  for t in teams:games[norm(t.get('team',{}).get('displayName',''))]=game
 return data,games

def team_info(name,lookup):
 n=norm(name)
 if n in lookup:return lookup[n]
 matches=[v for k,v in lookup.items() if k.endswith(n) or n.endswith(k)]
 return matches[0] if len(matches)==1 else {}

def model_prob(home,away,lookup):
 """Deliberately conservative experimental record-based estimator, not a validated predictive model."""
 h=team_info(home,lookup).get('record');a=team_info(away,lookup).get('record')
 if not h or not a:return None,'Missing comparable team records'
 # Shrink records toward .500 and use a modest home advantage.
 hw=(h[0]+8)/(h[0]+h[1]+16); aw=(a[0]+8)/(a[0]+a[1]+16)
 logit=lambda x:math.log(x/(1-x))
 raw=0.5*(logit(hw)-logit(aw))+0.12
 p=1/(1+math.exp(-raw))
 return min(.78,max(.22,p)),f'Experimental record model: home {h[0]}-{h[1]}, away {a[0]}-{a[1]}'

def collect(events,sport,lookup,games):
 picks=[]; horizon=now()+timedelta(hours=72)
 for e in events:
  when=dt(e.get('commence_time'));home=e.get('home_team','');away=e.get('away_team','')
  if when>horizon or when<now()-timedelta(hours=7):continue
  game=games.get(norm(home)) or games.get(norm(away)) or {}
  state=game.get('status','pre')
  if state=='post':continue
  p_home,reason=model_prob(home,away,lookup)
  # Consensus from the available books, best executable-looking quote for display.
  by_market=defaultdict(list)
  for b in e.get('bookmakers',[]):
   for m in b.get('markets',[]):
    for o in m.get('outcomes',[]):
     price=o.get('price');point=safe_float(o.get('point'))
     if not isinstance(price,(int,float)) or price==0:continue
     name=o.get('name','')
     if m.get('key')=='h2h' and name in (home,away):key=('Moneyline',name,None)
     elif m.get('key')=='spreads' and name in (home,away) and point is not None:key=('Spread',name,point)
     elif m.get('key')=='totals' and name in ('Over','Under') and point is not None:key=('Total',name,point)
     else:continue
     by_market[key].append((int(price),b.get('title','Sportsbook')))
  for (market,selection,point),quotes in by_market.items():
   best=max(quotes,key=lambda q:decimal(q[0]));price,book=best
   if decimal(price)>8:continue
   # A record-only model does not establish spread/total probabilities.
   p=(p_home if selection==home else 1-p_home) if market=='Moneyline' and p_home is not None else None
   if p is None:signal='WATCH';edge=None
   else:
    edge=p-implied(price)
    signal='VALUE PICK' if edge>=.06 and decimal(price)>=1.55 and state=='pre' else 'WATCH'
   picks.append(dict(sport=sport,event_id=str(e.get('id')),event_time=e.get('commence_time'),home=home,away=away,
    market=market,selection=selection,point=point,odds=price,book=book,probability=p,edge=edge,
    signal=signal,reason=reason if market=='Moneyline' else 'No validated model for this market',
    state=state,game=game,logo=team_info(selection,lookup).get('logo','')))
 return picks

def pick_id(p):
 raw='|'.join(map(str,[p['sport'],p['event_id'],p['market'],p['selection'],p['point']]))
 return hashlib.sha256(raw.encode()).hexdigest()[:24]

def store_picks(picks):
 # Store one qualifying selection per game; never record multiple duplicate picks on rerun.
 qualified=sorted([p for p in picks if p['signal']=='VALUE PICK'],key=lambda p:p['edge'],reverse=True)
 chosen={}
 for p in qualified:
  if p['event_id'] not in chosen:chosen[p['event_id']]=p
 with conn() as c:
  for p in chosen.values():
   c.execute('''INSERT OR IGNORE INTO picks(id,sport,event_id,event_time,home,away,market,selection,point,odds,book,probability,edge,stake,placed_at,notes)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(pick_id(p),p['sport'],p['event_id'],p['event_time'],p['home'],p['away'],p['market'],p['selection'],p['point'],p['odds'],p['book'],p['probability'],p['edge'],100,now().isoformat(),p['reason']))
  c.commit()
 return chosen

def settle_picks(games_by_sport):
 with conn() as c:
  rows=c.execute("SELECT * FROM picks WHERE status='OPEN'").fetchall()
  for r in rows:
   if r['market']!='Moneyline':continue
   games=games_by_sport.get(r['sport'],{})
   g=games.get(norm(r['home'])) or games.get(norm(r['away'])) or {}
   if g.get('status')!='post':continue
   h=g.get('teams',{}).get(norm(r['home']),{}).get('score')
   a=g.get('teams',{}).get(norm(r['away']),{}).get('score')
   if h is None or a is None or h==a:continue
   win=(h>a and r['selection']==r['home']) or (a>h and r['selection']==r['away'])
   profit=round(r['stake']*(decimal(r['odds'])-1),2) if win else -r['stake']
   c.execute("UPDATE picks SET status='SETTLED',result=?,profit=?,settled_at=? WHERE id=?",('WIN' if win else 'LOSS',profit,now().isoformat(),r['id']))
  c.commit()

def parlay_ideas(picks,n):
 eligible=sorted([p for p in picks if p['signal']=='MODEL LEAN' and p['state']=='pre'],key=lambda p:p['edge'],reverse=True)
 chosen=[];used=set()
 for p in eligible:
  if p['event_id'] in used:continue
  chosen.append(p);used.add(p['event_id'])
  if len(chosen)==n:break
 return chosen if len(chosen)==n else []

def payout(p,stake=100):return stake*(decimal(p['odds'])-1)

def display_pick(p,i):
 with st.container(border=True):
  a,b,c,d=st.columns([.65,4.5,2.1,1.8],vertical_alignment='center')
  with a:
   if p.get('logo','').startswith('https://'):st.image(p['logo'],width=36)
   else:st.write('🏆')
  with b:
   st.markdown(f"**#{i} · {p['selection']}** · {p['sport']}")
   st.caption(f"{p['market']}"+(f" {p['point']:+g}" if p['point'] is not None else '')+f" · {local(p['event_time'])}")
  with c:
   st.markdown(f"**+{money(payout(p))}**")
   st.caption('Potential profit / $100')
  with d:
   st.markdown(f"**{p['signal']}**")
   st.caption(f"Model {p['probability']:.0%}" if p['probability'] is not None else 'No estimate')
  with st.expander('Why? · Experimental only',expanded=False):
   st.write(f"QUEEN'S MODEL LEAN: {p['selection']} {p['market']}. Book: {p['book']} · Odds: {american(p['odds'])}.")
   st.write(p['reason'])
   st.warning('Unvalidated record model. MODEL LEAN is NOT a verified value bet. No new $100 bets are automatically recorded from this model.')

# LIVE WATCH: independent ESPN discovery; never depends on sportsbook events.
def espn_live_games(sports):
 found=[]
 for sport in sports:
  path=SPORTS[sport][1]
  if not path:continue
  today=now().astimezone(CT).date()
  for day in [today-timedelta(days=1),today,today+timedelta(days=1)]:
   for e in scoreboard(path,day.strftime('%Y%m%d')):
    comp=(e.get('competitions') or [{}])[0]
    teams=comp.get('competitors') or []
    if len(teams)<2:continue
    sides={t.get('homeAway'):t for t in teams}
    h=sides.get('home');a=sides.get('away')
    if not h or not a:continue
    status=e.get('status',{}).get('type',{})
    def parse_team(t):
     tm=t.get('team') or {}; rec=None
     for r in t.get('records') or []:
      parts=str(r.get('summary','')).split('-')
      if len(parts)>=2 and parts[0].isdigit() and parts[1].isdigit():
       w,l=int(parts[0]),int(parts[1]);rec=(w,l);break
     return {'name':tm.get('displayName',''), 'logo':tm.get('logo',''), 'score':safe_float(t.get('score')), 'record':rec}
    found.append({'id':str(e.get('id')),'sport':sport,'date':e.get('date'),
     'state':status.get('state','pre'),'detail':status.get('shortDetail',''),
     'home':parse_team(h),'away':parse_team(a)})
 dedup={f"{x['sport']}:{x['id']}":x for x in found}
 return list(dedup.values())

def live_lean(g):
 h,a=g['home'],g['away'];hs,ass=h['score'],a['score']
 if hs is None or ass is None:return None,None,'Score unavailable'
 # Explicitly a weak, score-only heuristic, not a probability model.
 if hs==ass:return None,None,'Tied game — WAIT for a lead'
 side=h if hs>ass else a
 return side['name'],None,f"{side['name']} currently leads {int(max(hs,ass))}–{int(min(hs,ass))}. Score-only lean; no calibrated win probability or verified live price."

def live_id(g):return hashlib.sha256(f"live|{g['sport']}|{g['id']}".encode()).hexdigest()[:24]

def update_live_history(games):
 # Save one immutable prediction per game, only if currently in progress.
 with conn() as c:
  for g in games:
   ident=live_id(g)
   if g['state']=='in':
    selection,confidence,why=live_lean(g)
    if selection:
     c.execute("""INSERT OR IGNORE INTO live_predictions
      (id,sport,event_id,event_time,home,away,selection,home_score,away_score,period,predicted_at,confidence,rationale)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (ident,g['sport'],g['id'],g['date'],g['home']['name'],g['away']['name'],selection,
       g['home']['score'],g['away']['score'],g['detail'],now().isoformat(),confidence,why))
   if g['state']=='post':
    h,a=g['home']['score'],g['away']['score']
    if h is None or a is None:continue
    winner=g['home']['name'] if h>a else g['away']['name'] if a>h else None
    if winner:
     saved=c.execute("SELECT selection FROM live_predictions WHERE id=? AND status='OPEN'",(ident,)).fetchone()
     if saved is not None:
      outcome='WIN' if saved['selection']==winner else 'LOSS'
      c.execute("""UPDATE live_predictions SET status='SETTLED',result=?,final_home=?,final_away=?,settled_at=?
       WHERE id=? AND status='OPEN'""",
       (outcome,h,a,now().isoformat(),ident))
  c.commit()

def live_panel():
 st.subheader('🔴 Live Game Watch · Independent Score Feed')
 st.caption('Auto-refreshes every 60 seconds while this page is open. ESPN score updates can lag. Predictions are experimental and score-only, NOT priced live market picks.')
 scan_sports=[s for s in (choices if sport=='All Sports' else [sport]) if SPORTS[s][1]]
 if not scan_sports:
  st.info('No supported scoreboard for this sport.');return
 games=espn_live_games(scan_sports)
 update_live_history(games)
 live=[g for g in games if g['state']=='in']
 with conn() as c:
  summary=c.execute("SELECT COUNT(*) n, SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) w, SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) l FROM live_predictions").fetchone()
  hist=c.execute('SELECT * FROM live_predictions ORDER BY predicted_at DESC LIMIT 15').fetchall()
 m1,m2,m3=st.columns(3)
 m1.metric('Live Games Found',len(live));m2.metric('Live Predictions',summary['n'] or 0);m3.metric('Live Correct / Wrong',f"{summary['w'] or 0} / {summary['l'] or 0}")
 if not live:st.info('No games currently in progress on the available ESPN scoreboards for the selected sport(s). This does not mean no sports markets are trading.')
 for g in live[:25]:
  selection,_,why=live_lean(g)
  with st.container(border=True):
   left,right=st.columns([4,2])
   with left:
    st.markdown(f"**{g['sport']} · {g['away']['name']} @ {g['home']['name']}**")
    st.caption(g['detail'])
    st.markdown(f"**{g['away']['score'] if g['away']['score'] is not None else '—'} — {g['home']['score'] if g['home']['score'] is not None else '—'}**")
   with right:
    st.markdown(f"**QUEEN SAYS: {selection or 'WAIT'}**")
    st.caption('Experimental live lean · NO live odds')
   with st.expander('Why Queen says this',expanded=False):st.write(why)
 with st.expander('📊 Timestamped Live Prediction History',expanded=False):
  if not hist:st.caption('No live predictions yet. Queen records one automatically when a supported game is in progress and has a non-tied score.')
  for r in hist:
   st.write(f"{r['sport']} · {r['selection']} · {r['status']} · {r['result'] or 'Pending'} · At {local(r['predicted_at'])} · Score {r['away_score']:g}–{r['home_score']:g}")
 st.caption('Live price / profit: unavailable until verified in-play quotes are integrated. Local SQLite history on Streamlit Cloud may be erased by restart or redeploy.')

st.markdown('''<style>
.block-container{padding-top:1rem;max-width:1350px}.stButton>button{border-radius:22px!important}
[data-testid="stMetric"]{background:rgba(118,62,160,.10);padding:.55rem;border-radius:12px}
h1,h2,h3{color:#ae74ed!important}div[data-testid="stVerticalBlock"]{gap:.5rem}
</style>''',unsafe_allow_html=True)

banner=next((x for x in ['royal_banner.png','banner.png','sports_banner.png','queens_banner.png'] if Path(x).exists()),None)
if banner:st.image(banner,use_container_width=True)
else:st.title('👑 QUEEN’S EDGE | 254 — ROYAL SPORTS SNIPER')
st.caption('V2.9.1 LIVE WATCH · EXPERIMENTAL PAPER TESTING · NO REAL ORDERS')

# Refresh the live panel independently of the pregame market scanner.
@st.fragment(run_every='60s')
def live_fragment():
 live_panel()

key=get_key()
if not key:
 st.error('Missing ODDS_API_KEY. Add it to Streamlit App Settings → Secrets, then reload.');st.stop()
active,err=catalog(key)
if err:st.warning('Sports catalog temporarily unavailable: '+err)
choices=[s for s,(code,path) in SPORTS.items() if code in active]
if not choices:
 st.warning('No configured sports are currently available from the odds provider. Check your API key and provider credits.');st.stop()

st.markdown('**🏆 Choose Your Sport**')
sport_names=['All Sports']+choices
sport=st.radio('Sports',sport_names,horizontal=True,label_visibility='collapsed',key='sport_choice')
st.markdown('**📅 Choose Game Window**')
period=st.radio('Game window',['🔴 Live','📅 Today','Tomorrow','Next 3 Days'],horizontal=True,label_visibility='collapsed',key='game_window')

# Fetching every sport can exhaust low-tier API credits; explicit scan keeps cost under user control.
with st.expander('⚙️ Scanner settings & data notes',expanded=False):
 st.caption('Each sport request can consume multiple API credits. Cached results refresh approximately every 15 minutes; pressing Scan does not bypass the cache.')
 max_sports=st.slider('Maximum sports to scan in All Sports',1,len(choices),len(choices))
 st.caption('To scan every available sport, move the slider to the maximum. Only currently active sports appear in the selector.')
 st.caption('ESPN provides independent live score discovery. Live in-game market odds are NOT connected. Score-based predictions have no hypothetical betting P&L.')
 st.caption('On Streamlit Community Cloud, local SQLite may be lost on restart or redeploy. Use persistent database storage before relying on long-term results.')

if 'scan' not in st.session_state:st.session_state.scan=False
if st.button('👑 Scan Royal Markets',type='primary',use_container_width=True):st.session_state.scan=True
if not st.session_state.scan:
 st.info('Press **Scan Royal Markets** to load available matchups and paper-test qualifying pregame picks.');st.stop()

selected=(choices[:max_sports] if sport=='All Sports' else [sport]) if st.session_state.scan else []
all_picks=[];games_by_sport={};errors=[];remaining='?'
with st.spinner('Queen is checking available games and prices…'):
 for s in selected:
  code,path=SPORTS[s];events,e,remaining=odds_feed(code,key)
  if e:errors.append(f'{s}: {e}');continue
  lookup,games=records_and_logos(espn_data(path));games_by_sport[s]=games
  all_picks.extend(collect(events,s,lookup,games))
settle_picks(games_by_sport)
if errors:st.warning('Some feeds could not load: '+'; '.join(errors[:3]))

local_now=now().astimezone(CT)
def window_ok(p):
 t=dt(p['event_time']).astimezone(CT)
 if period=='🔴 Live':return p['state']=='in'
 if p['state']!='pre':return False
 if period=='📅 Today':return t.date()==local_now.date()
 if period=='Tomorrow':return t.date()==local_now.date()+timedelta(days=1)
 return now()<=dt(p['event_time'])<=now()+timedelta(hours=72)

shown=[p for p in all_picks if window_ok(p)]
# Paper-lock all qualifying pregame selections from fetched sports, not just the visible tab.
locked=store_picks(all_picks)
with conn() as c:
 stats=c.execute("SELECT COUNT(*) n,SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) w,SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) l,COALESCE(SUM(profit),0) pnl FROM picks").fetchone()
 recent=c.execute('SELECT * FROM picks ORDER BY placed_at DESC LIMIT 30').fetchall()

m1,m2,m3,m4=st.columns(4)
m1.metric('Paper Picks',stats['n']);m2.metric('Wins',stats['w'] or 0);m3.metric('Losses',stats['l'] or 0);m4.metric('Paper P&L',money(stats['pnl']))

live_fragment()

st.subheader(f"👑 Queen's Top 10 — {sport} (experimental)")
ranked=sorted([p for p in shown if p['signal']=='MODEL LEAN'],key=lambda p:(p['edge'],p['probability']),reverse=True)
# Keep one pick per game in the visible recommendations.
unique=[];used=set()
for p in ranked:
 if p['event_id'] not in used:unique.append(p);used.add(p['event_id'])
for i,p in enumerate(unique[:10],1):display_pick(p,i)
if not unique:
 st.info('No experimental model leans in this view. Queen will not invent picks.')
 if period=='🔴 Live':st.caption('Live scores can be viewed, but this version does not have a verified live-odds feed. No live entries are generated.')

left,right=st.columns(2)
with left:
 st.subheader('🎯 Royal Parlays')
 leg_count=st.radio('Parlay legs',[2,3,5,10],horizontal=True,key='legs')
 legs=parlay_ideas(shown,leg_count)
 if legs:
  combined=math.prod(decimal(p['odds']) for p in legs)
  estimate=math.prod(p['probability'] for p in legs)
  st.write(f'**${5*(combined-1):,.2f} potential profit on a $5 paper stake** · ${5*combined:,.2f} total payout')
  st.caption(f'Experimental joint win estimate: {estimate:.1%}. Assumes independence; not validated. Indicative quotes from different books may not be available together as one parlay.')
  for p in legs:st.caption(f"• {p['sport']}: {p['selection']} {p['market']} ({american(p['odds'])})")
 else:st.info(f'Not enough qualifying, distinct games for a {leg_count}-leg parlay. No forced legs.')
 st.caption('Illustration only. Odds may come from different sportsbooks; no live prices or automatic parlay tracking.')
with right:
 st.subheader('🔥 Underdog Watch')
 dogs=sorted([p for p in shown if p['market']=='Moneyline' and p['odds']>0 and p['probability'] is not None],key=lambda p:p['edge'],reverse=True)
 if dogs:
  for p in dogs[:4]:
   st.markdown(f"**{p['selection']}** · {p['sport']} · {american(p['odds'])} · $20 profit: **{money(payout(p,20))}**")
   st.caption(f"Estimated chance {p['probability']:.0%} · {p['signal']} · {p['reason']}")
 else:st.info('No underdogs with enough available team data in this view.')
 st.caption('Underdogs are watch candidates, not automatic $100 paper entries unless they also qualify for the main picks.')

with st.expander('🔴 Live scores and games',expanded=False):
 live=[p for p in all_picks if p['state']=='in']
 seen=set()
 for p in live:
  if p['event_id'] in seen:continue
  seen.add(p['event_id'])
  g=p['game']; h=g.get('teams',{}).get(norm(p['home']),{}).get('score');a=g.get('teams',{}).get(norm(p['away']),{}).get('score')
  st.write(f"{p['away']} {a if a is not None else ''} @ {p['home']} {h if h is not None else ''} · {g.get('detail','Live')}")
 if not seen:st.caption('No live games in the currently scanned sports with available odds data.')
with st.expander('📊 Royal Paper History',expanded=False):
 for r in recent:
  st.write(f"{r['status']} · {r['sport']} · {r['selection']} {r['market']} · {american(r['odds'])} · $100 stake · {r['result'] or 'Pending'} · {money(r['profit']) if r['profit'] is not None else 'Unsettled'}")
 if not recent:st.caption('No paper picks recorded yet.')
st.caption(f'Provider credits remaining (last odds response): {remaining} · Quotes may be delayed. Model is experimental, not a proven profitable strategy. Paper-only; no wagers placed.')
