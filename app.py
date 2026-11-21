import os, re, sqlite3, hashlib, html, math, csv, io, zlib, base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote
from difflib import SequenceMatcher
import requests
import streamlit as st

st.set_page_config(page_title="ROYAL SPORTS SNIPER V5.2 | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
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


# Snapshot recovered from the user's own Nov 20 Royal Sports Sniper exports.
# Embedded so a new Streamlit container can restore without uploading another file.
# INSERT OR IGNORE never changes a newer result already in the database.
_ROYAL_SEED_DATA = {
    "royal_predictions_v34": "c-q{($&%bilO}j?k$wkAAF8Tl;3>8*S*Ku-OfoaLG?(Q3rxLX-0t|N`3ho}k;G4{G9$?OW=);Ii%RJA#!l(go2YexuRXyyHA65C2;il%{@Q-Gui>ZG4|NOWAvkGOrkM-tX@Q3wsdtI*Yns!res?9Q%>$r@5Q#Ezj+}$-bPAl(jU>(a<6XLS}^Iz`T#(}Gr)#kDEO`WO`Yaj8q4nO?sw%Npgf%R?a+X$N&{slH=8`s<Aro3(;^aI^b|5D@B|M(xc<0dGnv{Onj;*A9(1&IU<1+9F^1TjpP5dFd@w`kh9Sgkh;MwnVqihq}{FE1{hmv7p5-PFbX`j_HW<!^o|-f!>$hT$>~Ro74tqgOBgd{(}K_BL)73GSP=+Qh=)w_R3sEXyYs@1I@0eEqEWzy53S`c3ij_0wl>pWz>`KNQcN{<eE(ao1Kp7Ta|U#il9zP1CGnvAKyw_bYL9jj;T4fnT)1Pp;|^?;`$DZ;B51HU5Gast)v`X^S0-MU#qqSZ?DlCHcj&a{pE72SR@(>Q^FukxUz_{ue@iC8W&H_VV>_W%(cfg8?07;!0|+wNQe_M6_2SS{ivIwq#ss$vZe}2Mc_tg<r-4IG<dU_>GIV;A6Y)0KL3=R$Kx;Q-vet<q}uX66!D+ds&AHY8c+s<NH3sx~Ug!^i3N^bjdGLNuwko%vdQ5)#a-<PoG_7u&-Xedimkm)AB#P>)y2fwK)96g<Mcl7H6KIFbng~MP8-rO>xt#VgcdvcD-34+`kmB@t?rsMAzm;`OAMQ4?rcq3jV9qzZk=bl&7F-9ssxw3X+vcP#b|0;;adQc;a*qs@#LRY1&1In`2Nvym?o=gFBS=s6Ku6p?F)ZH%+}RpT<qwRGa<l4^7+DxTuEte*vhXxPh$H)H2Ta_W?cn%>$xGpI=b25I+k}+|H1UA%Y1V!3oEmGM4aYrF3Zrr`W^Uw9AFNt&ib+^X{s61=p4Dz<lz@>*96XtZ~EVh0*k)@=4n?x68-6m;J@EWJ+?}92P%bQEo>RFR$Lbe>TDJ;4=!fFlBM@K?TR>{2zdi{wnM*Oc=(`z$b;2T5HdVXVE%soMa%m)Q$%2E$QK7d-(hfEMT)dhVSylMe(9(>-@MGz>5!8#S3`2U6&Ud{B|1{UR~ZmyKFXiJ`S`03Po$JFkCY$xU%-UYw_fF7ouB@2TxGgh5GknN$Ibg{GzSphMrl95}0#IN>8kzf-_HWF9~Y2^NcE2;t?f8uYF*1w^+madK(wZt*@b-BKjfLb+s<v!y4f(e%r>!6(BC3;4#*2+g^7NUu``UuW`IOp19agEGLuxUbNH*Ir%`vuTuWX?Ju~Iw6dp=6Ydq5Agr^IS!t{hBq+<1)WDQd2sxe&c7z=F)kCugRgGWd9OO+?KQvA8$HSogpH_8KyltzmE8PECy7)AR%LCEhCA#cap?;P87fLmr^QY*h<Q*PfOd81@Ne+Js1Q;O(NEFhbImMJ_dvhwl;{u;x?M7tjU{F1U$ISrg+dSiO6I)cp!+3cx_V(Sg-!5J~D?ip%otkzP!?%%q|KZ}p`|@>zpn+S);vRoRsxp2NR>d0aGt@zqUuQTvd-9j!dDY_HuOa_l->mK;nye`8P+vF8xO^;NT|lvE!QW0k`v^SxE3Qt;eql1(lxM(;L|aNhanM|Ff@**S4#LUcjMN=?g4;cK_%B%_dic+Eir!n44cJvUp+gLZ%E`-lVCD_OwPw*c5&;NWM>=|MoY$OKPVfX(ROsF|^3E+DJRY16aC-vTlkyWD@UgAYrfVB~66oOjgj0*>xRchKlL_TiI2Sl25y3|dG$aR>Dg~4PRY9YUCjn1Kjh0@o+;5f(G>sSQ2qT!heEs68ym)<4Jl*=+UbVk}c2WG%B38J0^I$o=m<eP~OO30KFd_qE+EY~N;L!$hRtK4~2PqlfufTO$F0NPW`g8%lZ?>D8;`tI)LFiZCZ?S2wBiear>l}*91JO*71WgT42ZpD_UC=HXN=S(4TnMXVMpD?`8hXf3-fi2(=F2Afn<<!|vU&M-+lFl{{^`?y?tuIdzu*&oz?peyA}x<4^FY!nO%f!C#!<vfdM1h2R1xjC^`S#j*u9O8rof_pJXnz#Lv$EFv`{UJCrkLcbH-o)t@xv^@-}`QABw*<?d|UMTQn#-ulV3idq}@+vJ)e$rH0~J%_%eAc6`o%LiQGASsXpizuRvA5qymYX_Q5XNhIj^1hn5h=eYZ$+O1S$$Cc`#UN7#d&E{mGUIJQ?)posKs&8@DI-BDCf}G0BBiT$At5nW8g33BZ7rcg`m~fE`pU^u`6v0GSbnwUV@Lb+6&_*4BWaaDUSLM~8#XCUbbsyY#oqgikH#xXb<G0<vyuye6GhXAL!yA5Jg<2ymGpG$ES`|io&#655_2^Wp9O0NgM4=b#r!7w5xT4erhT7aXz*9A%X-OEvQvo%4r;zZkadB4lVp}7uCu<XJ#j37xY3x_$$JYpSw=NH4<$io7Yf?y^k|SF4#L$gVT0>$aCa_w^lrhAUYV>2`re1FrtJwNeQ~Vj=6K8KRuGca45#T;fjMHO#R$dOWxuD4`I@(4BJTRjQpwU2&2A3z7%(B>7l|=2WO7A`|>d(hU<>iy#@x({9{d<0xA#C*e_oukE?yKdxoXW~!Y$o_|2aIaT4VvH)b-7i_2lT)xDtWYx@(C%?xt>NWA7FiOM4m(}KV7^pK2=`<53Qr1<tJF*RP}ZCzIPLTK!@jN7lIk|y6|!SO~K3456od<`qK_b7AY6N45uyyFv)0}cu+PZiE5qo+MDbx_WA}^clhW18Jj&D9WPLfU!d)Gbw6^C{>_1`9LHy}C~1-*5F&U?P6lJOka$Fbv%zX_dlVHN)r@N&q24TNTtcU2(34KMS2M5C(7VYKv%j6y$|x^SWHZ5&B*m;yXl4XSMF<=?FmAw0ROKP&=#SB6uX0xC{~?gIKcjM9p=@{AIN~`*HZE}&wXi&xSAJv8T+l_wk^t%<ku+sds)P&HKr)PjprQ{<j`zHqm=*dpo<$F5Vw2~z`ENM4j_u-8bnbXUc_5q%qRJ=Yt!0V}bltcWIqDJ6h{po~oy%-ylF9zBS6}jaU41#({I6dPIPSMUo|UiSm&!N&j-SZOBiTH_j0s)JDA2Yf78fb8!bp$iIw<eGi7wll+>V<cmET>nTx@-~KV6-dutZN8pR6<3FN>#eU#<6}<$h!?&;>)HVKxfuJ+%;$vMvcLh$V@J&NxzLqKCF=fnPVQruI><gtjTcve_@g=Qs;>f3JW>Sveh_3y4g{WTj-ncXyD^Cq;Fll-uY$?~g}0JsvmyX0ck|9qRJq<FSjY^*J%?b^h<}t{RTS!%5}ogqa{4gU5YsnMj7J#3hYPIc_B5+@THCn@Mb3p6iWk7h(IaC#U3lw2vSAi~juzeY*YEZCuy=$#^uOJP^(WP3v(VMzl2r;phknv^}{_GJ1~gCFx>C)b3+N4$zyNUX}O3q9+$u#k*{a5BWtG?~1o*Ufy)+MLPp%Kk@{Yn+naXIK1Zvydf?$<r;OP#Q#(J+uqRe@9u*|C(nSx@@Kh2(Mf@BWR7xJ$s~|O2s-IPP{MQ+q+6kq?7hn+_(g!_akTjAqPSe*_T5L|F0!|omrjliUp4FEqP~vHxZY1IpTcs9t74orlSOJ%k_q>=3Qk2DBXE`o*Cr<$>F7)JJmqkYk;5GkRndByA;Y?O$FrxeiZ|7Ahv)kbS6%o6z4dOe>|e|TGKE$nm&r1!B0(iYnQ$f>KOhMMRN@LXe6N4D3D;lsSZ~_}uK8utPFLc`=a=Qv>Ji%F^|rlkzTQA@23;(x+u{oMS^f1`UQT5*fl~r^nj?Z}JBGl}F-PTYWdLOYkG2nAj+q3_GN3Kl)b*(@f3pn5(`HrG)sT$w@e=19443`TT%fa_#i+niYPb?s62y{&b|6($IjmvC5IxVQVa^Oh7!HD!*_0ge0ej=;<?G@@wQ{k2XqNZ5X<t>@_Gvb7IDD8b_?KEn<qRh+sZpHalbY|{N}T2gmc{v}DDX@CJY6HABWRu*ZF$5;mXKt~Vc?)u43d^iTV=`qXuP|r7OUoPS>n<7_QlKM`SNjhG`_;4@e*nXRo2eEpd1HgvM|xT=H5tSf#;hsT!o%<PXcF%opyb8klOF*v~5>VFYaMgEl;=g^G@2K_vfpZ!^<-NI1{uGs-U>iUOFKq<qF+rE~JkU?Hk@3HB+x}1MPBASN>+XKC!$%pe*N19Y-l}cC%deSF@*c$^-d4U=2Zf)GLB>MXcw*l{dJbBW~e9mCy()LOq7gJ$}Y+C7#62(GILLVwVU~yzZlD!$Ld~?bhc^K=hmjL8MEJQV)hhKo#SKG0rkCMDN7Ny%Pg%wYc@iKFr0tm)TDU=q4=tWAKm5;_{}7X<hyS^%|e_vf4cEhA!IbYm>veySMy+exW8mi8czK6Hk`!sF#!9jw(i%#p!eW%!_ZdwhcubiH{J1z;zm#b&fh=fCW^{OcKe+{)Eh#SZR5Xo4z|9PgAjWKli!eU25+%9qU9qlcnmIOT<x#44oO8A|;R)1KJ!``OX@`B_hU4iMyNiqK5m&Gj|dZh1GCti!*iuW%ptxa6uqwVgs>3I;u%F7=<UyQOR*{cc~XbPcqm-T&_2ZcDuQb?TOlX8kg_@Z9Gyt@4DH&RymzgPQ^1p)fy`(^TeVN&KwbFn1>iB4-!>;F4)1tlutrvKG$_@76I<7aGKPzFZ$RA(egYz0{gs{skl54%>_-CsEC?+bZs0<$y>nJ!8k4;TAEKuJ-G?<O}?*PHrpknGj|PH3d_T!>8oa2t#jGjiMSj^GeJ{}gj-%l$$gLs{V^*Hx{5N#F%*sc+SH>#)ZFEe`E{4Qd&VR}aW%cT?bCcW%|m@4D<|VK0aD>+Mn%IwOU4wSo?*;0G&hsAoOjna#*WK!yJ@fQ7dOq;!QJVy{P>}GS2e|NEz}_ve*#*OeH`dTvt4(F<xQKvDcY3hrk1DEW`eSi$|W>8c_I?E6dHG+gm>C;hi)bG`h2`+ylEcVu&A3hoxA6G(LCfTvmwDCHOt#!b@qbtaBwaN67C!-bLKpa#2XPKDtt&z610_}SMj3<wD#M(MeVOo_xi{8m&M1s^?s{=e0_<lFvfcAnzosW%OlZDkVGd^1#glTXfClF5^^!{XdG%IMxhH*^3hyJQ+>5?zG-gHgfn*L_dmSq%W>o8W?RR_CC<){<3wH_%VvTmx|D$=qNNCE6Dg8(1Zt)8mg*=%pFuF*;MUc0xv0bKxqaPdShaQWa*0pU?2Ps<Kj<mk=7`}WWoSCFJTqw~_!284o|)+PL<=T>jZPb4Ef4}a^#LWLMn&(}pBL3#dN`xxVYyw!%|7nYBt+_$;`e49@A}*Fp+4m~f|+Z05vcFJr#ziM572$GF?o;HfHzj96oVz!C@HL?)VtoFvTV%M%!}GJ56kQJOseMDRs95KSM_y&yq;Omhw_=g>RC*IdQYO!LRinO5_sYgFvb(ouVp<cU+I+Ckn?fuPfgX>SZ;NY0MZo*98N3GOqmI|4x*DfNoHN}6zwSm2)E(ZcpM`A1(F_<wQqe#gK~6%^yZJt;sffb5ZeyYkrJhRbGw9_W(9p*ZJ2aSf%4Zm3OPT@a7hhg+)(wMjO}xum&>clbMz=bE4t?$^<1Q2UGzkvg@AU0WF7?f0uN--Umel?)zN*tUOe1gA735)`P(1GZ_P5qI@gI^-waoDpWgmf{GM;!4rS%<)0?=z^Mz@nARdV3jA$A>ptmZuPXb>cex%RwqNC$qZJ@80K27ktiFq+Vh|3<m<I3pCgwwL=nZRji^il=%_Jed*aWuUMm$;)6iy`)*Q8_xS%Xm{QmXP2~Z1f6c*S+e$$}{37lSFlc<v1`C+=LfNMuf~cVFCjh(cyR8a)Z0N%T4HbIQGl%X5DNTF>ChI=u-~X#m-aj!tr^TWe}eY)d71ger95MGHEXO3UAP@LhIC6t_d^FP#2>|Y^?^-*Q1kBxmQgM3%B*RC#wBrzS7z0^d~r@j>~1Is&|s|V0113QkHn4LMGHJj60%4wn1e~I#CXtq8SP?cGYT|!z`q4maAnBu2*n}-}fp)I;vTl3T6T&XG!EVI0GJcx{zR#$JZ2Xq^Pa$ou)~Rz_NKn6Z7F{vv-T}s(EadkgpLvT#q^0JMFSJ>hnT8om-xrHxraIM<Er-Ye+m=H0VGR2rkghx|4k+h8Vr1)fa>q&(+QOdbXjuqC+?H7LM-zURWLqW`ZM)38>7sOU9v5oIEoGT#(jTqHQ0`9}3aC)q3rrUaVK!leO9flXFVaV}EtjCvd*{*e$waVYw5`1w})+vcY-pMYj~Cj%Xw)K@%~F_vyqVKin#=4io75bmCXfUZKxjef_$u;=e76Zg<hVfA^16uUg8vw3Z3WG-coRg-(8XLD@q5|B_4RiKi-|6>0+RZpig6%o98UyhF_&d>22ZW0EH<0TAl-{qkJQ?_voKR$~NpT8}Z@pY(fuGO_&Tq?s&bVgkBH(QBTPU^)SMPC%GLQ{Qs}eN1&i$dwt@HjDTG>zgx_Ts!iT@pf-#f{q2yN3+UvGiHIUOPohxolx8)Z_uMh?}=yL8uY1>7*ZwVc-VdhzlislN9Dva$h3BugfHyR+Gly<J(>}Hu<39{c_f_)purGosLYApxw0MCsT7E1BtQa_vCnv%0J;X>#&F0KyDK&7M8%5;?O2n&<7my)6py{)I+|9VPMHa|E+x--U{QE$(P0f#f(PLVXWVMu*Je*ISDFXZY}@wxw<|cQKWn#b3y&vvDkv@wL~{W%$Y?;ITyVj_f{4rjPuwNuGq*}k(;EW-*RWpTvN{ts>5?ESIQ0EL;M5aI*^SNxKN`Hvb?e}CN|G^}z(*UTrxsoPKEOP(f#m$}1OK!SFu!_v)%o<JW4a@IJ|=&DRV^Q2dmCdv|3~POxWetAh~S#$;`;AojvoE;f-a~m&$2}SEIZ0sX=DhBGLsSr5XRu1Mia_NpO~?ILX@4Z$IW5|?KvI4Uw0$PPo9XT(V?i^56@&xn#chajdoEas)MzWQ70o2+1=MY8Hk-^MdTva^#&j3OcK<zj<s`~psF{;Tb$ee<9aGB4<&Pfl2gHf%NUeTQd^clal#!sQ-+4VVAxFbc+a2v(4=(Af;b_`9U~?<GjG^VJs(Xd4}|l;6Ww=4!87X>0U15I_(pT#Lsr>rNO>CL^ttrf$J#e%isAMZ)m_zaPmogN;<wzo!f!Xj=H5*!zddCp*k)Qzo6FdvbIO51eWXbc06_3a4V@|{V>;PGwe}rr=Lxc(P}0=vs-EOOjDtgh!(32fS}SKM(Uj9n(I+y^91T=CCjwy|9hw~LgO_kv1J~1`U0wW9{DCgvxe~Z;e0CLZ4ox0Kx#p1zBGECI0QesTk~nxh@WW10a5Aa?az~VOCfBy@ZZ10brbI}C;E)|@CJ<6#t+5I%9W)V>jna`sP<P`AXko}+-0{pHg!kL!;`0i^DSCfaXwB3WEUq4Ff3xo~@H>8kT+tt@%u9SasXUx87gRa3A+u2M5Umn|=!npAVw6PV2fBiP!N;oBYIDe-&=>r_efa@xnfiKL)gy%huD+bjF$|Z(%UQq%36%$UX?2Qd?W7b*)C82^#2S#UCn~l3#kg$h>xVFKZJsR1t1QhCo?T$=gi~`j<2>W$Trg#5a|@3+a<s&~GPu<P5#UU6x!|>nzSF&PST~C`9LL`A8@|O4&!ok@0e(@uN3EUvi`2zMF3hZ&VI*G(y=j**Pu;!e2PzANMz@d=rSWN{_->PybDz-BLzczK;}n!H_|NhaAvzI3aMamKMCrN99m8U9LPjNZU%Sr7UEc9bE#dfH_{qoT_~5P`ZZ)1?U5rdK{dl>5J(D#V9Yx!Qa}ts<j)~xm%X2AF))3hREZ9geb9d9^b$Jx8c>4ms<91k)lNDONy=h`yed&b%&h;o6lZI%+Ir>}Td-p%n4=Z%8asB4{{AueDlw#U284QmSoka8ul|`#t1fe47i2#NkYh5t%#xGW_KNCOOQ<CoKh#zlq%yPn2IvJeFVuainh#LH#^x7*I36In#Z#Ynui6A9We6)bjlZ*9Yx!#^?od1$V>jLq>ueZ**kzE4U`;VuRGLOy$K;k{x;f4y4j3b6;b~7Ah335xd-k-J;p3-`?@R>pP3{7dh>Z+(acN(X--t6v2%l*hqfU{l3HA@sAS`9=ekzBN1P;Ua|C9~8Hgt?jHXnoT>HrvJL=4MrGjyaCr-!xFU;%^Op&D2}o%}rgrsy6;6Lv|o94`nlfGl#|~fe12FWpW59>ZF21l}u%>ywqGw!nQ-B5C@OTbBu-iE+Ad58%}E4$!Ix@%mg~&qj1!QKqTbeXMv&~W+pH%JrS<I&YD2CYB!5@T%Ehk`uHJ-xHm)KroV5==5<aDXtwQ+%yB=h{N|LoU~8H3KA`u9$TF=+B!L2I7?a7QTyKC+!nZ?sVg>EvN&NPJ$9WgOMI0Zy41pY@J)Th>Naq5nHWJTG4PGhjbmS7fnZ$T5xny09wUCM)G?krpvtj3Y%6_|z&A+*j!V;3TAR3Xzn16FK+5d@cYk*tsy&~M>nJKA>iMnK>i6Wsh(^-xPq?)E<Zga`cHJni8qSvyk+ZpTBD;TmbhZ_H}v}Y=w$)e=Kg?wGjgN`v@V^a-DQekt|Wmid^TO#c3$W>0ajLYT>4-)R-h$rGfV$d=-jXoGJyRn&oN0W+DS!Wg6-JAfRLrsBk!a|HN*pOoiKzF&dLUnQG;$XP2%Ba1;FPW9fZm`@B%mp^1G)Hxfi-KA7_Hd1H!5nwNCq{cB6s9NTkR6>FDp|ztOs?;apKR3JZQbLw6P1U<^MKKM1=fO+jwm!BQ_QV=TugX6f?~AKP*r+b{<yeno74VCJq6UB`efh13+E1PRJ$yxlUe1_jJd#yh7bCK$)aw~RP2sZX+$KJMNbmz^Li$636CdYI-898`f_p>?rQgk^r%5p9uCh0LTe0B$>~fy%A}Wg8d8hadI+4Q82U)rm^;1QWIE>Maj5L$hl}EO`1-YvM_d$FaGNceDF^c~^l<|>-P9k5*=lL1X38jQ@F(^?LudbMGt($9i_=g3sgYW%B3Pz{N(!h8CVE8~Zg%caH{&YoQz-bPB>*h*I;@sUe^Z_Ira|eE4gT@vWncbuAS!pmGg*y@p5;n{H_j!mEm}LoWExy8ILZBp4c}#=bcIlmyGXXj*IJof{l&Izb7VB`jPIOnR%f2Fauk)jp_#xZh0boEvv?>vv=AZSh<2!cywQyH^|pN4&?he3c5}9!&#{5{WV>0$_Si%mM(2Vb2V-NB5^U5#Mbs*UGVXCZOU->>)-+!Chul?ov0At1ZU>)c;l8k85_RYV<t#%HRx+<e?xzbnIG2k;twR7URaP-JaNthrnR+^y?dI@Cuy4_G!s{}gg&*EujE%WY=9GK+Tu?<TTqf!A&AwquqV-6FjJV0Y&jEmrz18g5TQ{*jx$J*_RepGutC==1x$3`c9$fRdZ;P;dIbq7^=KnxMA{VGCD->6j8S>ry+vDF}(A<&l@DXxTil3&dxfNVP5L9a~v<XVlAPjDP>9}IL&qx`I7yAnO^<xWXQUG?P^1Vlcb3Fd*Pb%p5Lo->5=&Y#7Mb4lAJRT`}g*cTaw0;6l89Oe>$e>l#Sw(NY!0JrTxR+U2=S}-*=T8rL5;+K-tGy0qlt<E;KuQT?np<g5ZF7W@v*^$kk03P*f%dlwQ-Zwh;^Dq(&ynNx6oR`V<zkl%wUd;Gqcg#eP#@5n7Q`w=ltYIa&r2DZO2o^|)oQpT>>x>E<Bm<nqa2A3Pp*oq%*TSzT<?-3#({Ythkmo?K{}=gbCL!x5^8LoJsD|qr&pLUV~tU~#nX2I&fm1>dk*KQ%^x^t>u-l1D6U$@I!;I}kEfNVQ|1D$5)>Q}B3cnVFyn=zF(qmx+K{x5oXNeG+4`!zU#!pMJ$}3_UbgqukOQ7Gke*iYx*5mIacnNwF&#DM%mp57PVjc#LgFL<x_?~<Ga=O&lyJ>@aedN!_{p31#bsP}U!l7&cwc-1^!##x(UYbpTkXX=3Z~wI+)w&}rdv!C&V(dH3jF8%PSdS}&&x&HqAbpSYGD@UXIVZb3d$sLF()38;IzrUp*P7f8<GlrJ{dd68m(b{(hKD{YxMQ|7scB)2Iz{n_L-xTp?NGz2V|gKMrTmFnDEFHF(#$0i#qmcP^0m)thR2^Hg0w165;CQ$DUffak1UxY;ZjO@@RQDG85#C0_mluTB6y_lTqB}Vi=}^rZ(F+f>9&(i720|_O5L*tv^2SaU#m^6?)BY+s5C(*IU@J`@`o3cT&}6>O`FKfB$d){r~7E{rCSJXO+h@W`e6m6FhTU7={)&cOt8Aw1p@{o@iH?$>>l7mvM#MFy?NthI+ZJk8!<N##@wx79F$ZZrfI{Q#h!Wubbj(Tf>=2<>7?601J#n;~6p5T4sF?3o#?2_6#`fX@vBIMreiSb)R^4l2I|FM=W-x&-<I)d~u&!vCBxCrc#{FEf40+1!OIRVbn_!0!ca$ox3Zrlt3A7hB~=1T=}qCw3WX(SIV_qR`_N9q03b1&sCg={~X~+R_@1V0-@4QkwgTC93G;SfZ>``9z5gD`ThWy9ISA=g3V$BH{im_!I}e>uVAyei4TJt1UFTSR(#klm+%<x52utT;+cRdrO-l4bSRnfB2ea|mNJEihMqC3_lw3BG~0)?#6PajEX-`gO?&!u14C-cfwVl3%mhbaIpYcaA0-0rbdT<4L`xu-yc5rLPi8aL!9shNlP1qtOBe6{SiH?1(39mhrGfA_hk^EN3m5He*ZFBOtvs4C6HuAJtsbR~xCVJH(*_VCDas{xq$6A8l#Posd|7W657C{?q|BG5`xMkq5e?5>^5jlZPDf{gAbR047<A2<2nMYupy*R7FhV9Y?M;uMoOA)G>doRd*6|#MyWRb=&I%j9RZ~M>;rT}w=Z1%)?16)c<<X?MAj|;r{jG{t`w)XR0?>>1;Ju>Yy1Wd^CyCOqS~R!c%#z+Qgzadd$KgFQ^-Pe{ksCvROI(qL;gXD02DCUc|Cf+zpz^l+$cJCX#X$l=pN?^P^{lu=k7rr!l20#Ryeyt<bAHw3GTz5!PY5{7D0j4#{fr+dUQ&{4$t_nJ4<7sNxW}0f%K?tEIQYau{6w-99awTXEn0>oNamsUf!YkT<O+cIX_Isxh-)FF+|TRGmF_zf?QSYAamEJfrn!dQcsU)L$$CV)JO{Wmo{N$rY+WfLP&_viSJw7Bd1UbJZpmD;_RYB#hr?ai&;?;VZMV6<Cp#LS35;G5CNz^a<$E&R&_JaZ4)?W1@5lF%qfu$!<l?1{vF7xW2SvL}o+&ZSFn&N*GZXMKTZQx7q170C5=OJ!1u29~0%C;kQ`E*}{3ZDAlI-YAyt=^CsJgq`oro{qd@SBIJrD9UTWmLJf1S3QG#4~!=H?DG3SpCScr@Zxc8(Dvk~6%we#Yy@HLTJCwyr%xPV*jRyYuj3>$YfQ4@2efaweF82|@)+6>8#K{|X6h8H0<F8?B7)wX!@Qm_-e8!hd!`FnjspqIi+HKQ`w`WXHykyE%WYnT;iiYhtYxIdt`19<*-|%btAf&sEPRl0c*e1(Hw~aBZSHqajh9Ec!Nm^%6h0>sf<8CJ;Ej?0J82*>lw#U-e`LNyrc04Dugnde0R8KPO5NOD+Fy)BAJxJjWlI8RvdlCaP9}5bc@e$t9c6OH{<koF>gA+B~UWt70sDZ`!Ws{c-L4n|H;B7OExs((rY6X1+x$5{>$NqjOU&bFU42E9{1LJhBJx`++s9m=;3d0i>n0oPH1A^Pf>rvXEtQ`b<AH=T{4j`Y2f930g)mrGU0@Kyz4xL?Y{o#LSR=p3}1G$IqQ^I!eoW{kN_S%r0{}@4oBf-0A(`zI5yNA3F{xF-B!h#B8q9?_5wG{NjRT6aCx+<Xo+vh9{yJP{k)F4NxK_XOTw+DrLSzXL?q|sb*TjH!4kb?X-8y(Wm7m2l83goM;BQeJP_5Egl9sCv8b>n2X$kAgu2bJ96Eu06$}~UBUWH;m#}A<sW=}g%kSMW&iVB;L2BMAO%ZaSk&y=StroMOw<J3MTMBsWWsTgZS)?rZ_r$<?$$R|yCdbya2$*eAkG9n8i`b(ybWF{;{}sc0MCj&Fr9j)G&L!J_yYHf=B8>+?#upHxUXWnzaM*pbGkdPalG7*%>_FP(U=qf4Gh7J^0-SSptBf+4xIM)U}He?0j|+Gtj}G9!F4@!Z_9pQM1{B<2Fr0^E~qgpfpZBaaV|sz=26i>1Rl|lB>gQ7n{;)8P3}l=wx`oBY}30kgI1V^b9Q4h!H$5TiA9^^-9CAlY22NUG)Wt!M3-7KF2`!vt<Q{*XYXD=UZT;`S2_5b>N>Z##*uRWY94^8L`iO2s2DSlKpLLg*{6v1s*@~<E+b7(T3lUs)E{?k?c+%$`>r(opq%|}TjAWz(4ggbN_i-r38*nzCT$=hC_L*tP1G3*Mmcn~qaNA=js_o^_Up}J?cprR{nNOF2WaD@Wa+35b|}9&m{*>iGZ%c7r&5COp5hvH+~6SzxODTSq0zo~Q1sqG$vtt_>DWKXO>q9KZuh*e@1GaK#(|pec?-4AoVml;*py~?%a3#gRZ>eziQs}yD@RTm;T-?&LN8cP$#?V^g<1SGE;Ixcohb(~_rIZ_83|PCpo9h|T%R03hpx;y<03$P(=-d0lRHm1m7e0Wzi67<L1#bdNU`6vn}`}`l$8hLb6K0HzH`qIbey@f(dDc_>zJjQSm1qcRXS1|#;{r(HwJxiRX%(As(4c^_qSXJg`Z_Fnbv#G1iZjg;KHKem@A|yrv{B4ZdqWII65@69Iq4F3fJEw)Mrl3cQ?38ikIsysM4>&Jn>+>9LDAXUM339nq236Nw0nG<qI4nf+`2PBr|sS&P5NXZ~XH9T$N|8W0+{1@xHTHB%`=I7R>}rmuxVE=j2nK658N`X-%Rs!W!>rPr)E3IX<_G&6hJ7K12S_nA@`->TmmJgBJvlP6rVP<i0*6T9YE@p3tPaUUigDdnN^ltA#^#cj72NMM)f+EIh$EH?Z27h@-STlFS518yTA9F3Dg46jO*Fu{x0?h)TMT*Qn8W^v&}6r04A5|DkBCa__s#?gsbmW@iJNwt1bQyN6yfl{D$xU`}&}t1fjdxrG__Yqkk<3CO|qKyMW05|H<s7?;zVfoE{N%sRH(Y}T21{boPq@Va0(@2@WgsAQRc$l6?G^If8|Gap*01wXnJ=s&q&3;ol~Lla!ITnD351SM&1(w&_~k7_4U)%T2N(@qP`W^-)T9CliG^8vw*bze%;Us~;7&SX{UkPPQ00RDmh;4Cuq38L0i68u^8v7Cbr-HY{h12^Y_IelW$kr$YQ`#GucP+lI&<^pD>hs#-H9+HfXTbUqJI1}e^_sia`9<4*yK4iXt_KbD-q-`GhRFkJD?>Tb2v2uJp6VP&ZcyvaD;DO{8oL1&myxLlYwl(c*y9Ax6lNL?Fl{=?R#wH)AM7Aqe!M?%Aeqbi3@!VrKSX8~i%b05oOweGwjvVbk($fLZ2?1BLT-4Bh{Z5ZUgs5pD^xdHMqZe)UwW;BBT6r>MF3`$Av`SH$z`+UB*~uF8_BcW+xa;$D#^m`&SjNQ-wD+gb{tcG>N%#WA(1vcH+`X6!T$J0uxuCfp3i=_y66g3EI9lAP>tTj=^_b=PdFz{gIi9$5zi;z*h{r7Jrz`Jt;+f?+6L@L%^DLl!9TN-2NE#esjT=Abbo<V%F?Mg(Jixlk)H$+iKD~cYTmssZFmOQTw4EnyS1r67FNfE20gidj0+ZmZ(u{TeM>ruWcY4Q{J=x6UUi7BfI=DMbU>qF2T|(lXDq-*Y?IhW~J3FhK&X@<XGBM_YaOmujOv;S9NJ~)8dvpM)>=VcINaK^c4zJD<bse-2-d$z=vcE2vNcY!Tvx?!!nAv=|V>aa$uN^-Ui$W%;nAg<wA#J@6Y2Q9JH)#IAdJD_daY*}*$L4dDGY6~dEn?MQsIIrg#c~C%D-t}KQ65TX0%-u6a^g(_JUSh5l2Ds`%JndulD;*zHj~9!FXFii*1SCH`{90IR~5X!T<vA$$@pAARE~Hfgazj*<y=O>v*w{vQ)hfn17asd*sxwN7pvH|)ybu6m#b7<H4oV~-xpu!sVjWQo;L1iN_i-r38>(0?t9IhN7c=9AH$%D$pxC!;Mv(H>P#5fhr+j$9%vn^hvjQ~T+g*T>o7c>Uw(7$Oz^he8BVkl(ov?kx153X)_{mA4a~69MZAPHEEo7#t*=k;zJlUCESDqF7dS89ZS^$x6KQ!MnG2R8QX;ve#u0Rmv<Xo~s?je>xuZa5<jXOo@opXaxbMlK`WDw)ztk4b+A8jx^)BLjB-=-VXM&+ox$q6Gcn~R=geFupsC#8{;L?z9ugB}Jbrowk8#sSbzR$A5uz5E$x1hN+R4A@#ru^Vh2STKzERv{%_bH{`7pIe^!^`F3Gx+m_09jMNT;gLi)sXdF)u{iwK9R@raw3}vmfATy1u17tD#wHMOyIuGsiHihuhf^q$w`*CZ(}%C>SF~`mKHA}w3|bXejpk(`CL%s5CIJ#u3XN0%v{eV2<yDjN~IY4My71kDw$ScyNP!v8s(5AVM4L{>}{txPUYoZHWMsqNO%PLM9_&uKRq}!-H8vl7&-5QnCSkP35QCHYIRZFwy-=Ur-HobE}^cQx*E*(mpF5UE>5++jcs#(Jf}RC&jVJGqz6J(CP%|LEcc2e%6o_UTlfAs?(?yfJu?>P?gB?Fewn8D738m#TNU+7v+p&qV=dlG|1G@*(6wcfG0q7g3{Um%-7Ow}PGNEv&cluaKkGhGB*7&&0@V#8(MmLWfH^eTWA5V)w7=$_9G3xZb60_L8Ipaf<G_~PEzF%3nx;bb^72SFlQroTcx}OOn}pWUgEKJ`8hb|2H-|2aDa1aESwB`?+~_Eb`TB1!ht#Ou_4m_fABuPQT%Wqz*}beB$L0bcw=Pl}JP1M=(L5+n0;CiGIt(uPBz<MOy7k8l`GZIPN1yO>nFH*5hx~vQzjqlAr<G@>%miBy8Xdy`nnus4aMoGts3wpSaMl+zOzv%0okzZ2-85(VH&h|ummA&cp2=^uixi(sD5t`i;Aw*hkR(&g2oZV8DW^gtPc-C;xDwISYP_?+XXrNkv=SCK`-eC0@aVt8uXYt7?YfCf4uEnZooxM?fQUzBOa^4;@L}Ab!AWv`W)vK?HxWHM`M4<S&6mYyix|XjT=5RXU0s@AH*Y#z4ns4+F4LST$$(icGiJ^?uLIRdagpe7SWdcO+~ICKE-4%i%PuG}_T$XenBB=Y440FknP5k=t;oFRQpCUscw?>Co;frsf%Np*lQ3o2-gd#B!<39ZQ4&Ra>~<L#eUjuLDGx?xf*|KP`Ai^Soa&I!tHGH*p+ZrLbrqS64w-0u^Jln*uZs;VH?cZx{tT;XS^NfHt9U<9L!yC*wl!|t>PxIzIGj_S%4Y&A)4(Ow3bitkU=zL;U0~9wph5O!)suzkZ>p@O<7tPR!&J510!5c)vl(KzCo{^abS8ijFJnhTV~C0&qWf(qVa6x)5_#l(2Kg9VYqy*0*mh^-IVa~`Cmxbg_mSM)@Fci98~7ZqMQxIxF`JJFp~O=YGA%5Zs^{7xqk~vK<T|gj&~=LVVTeHX*7#6ybs!pxyk-I+PtKz^6Y+=y9~n<dXvYF5N);7CmrNOE>HegwtJ_txS%hY_I;=>_C*|wvw&|h;&1UejpEj$^JzPA8`cPU<C9{Fjcnic(C8bl!M&ca)%zen$jRWQDn8>?<HYWt0V}3+QSMNF|^7e+}t`hrjLU|~h3!1`!%C)eO6Y43|R-)HUwdOKLV14q?goS*WX<pZ#&s@}AHT8AzB!a&=RyR-=zc=gY#p}7b<(YXi0XaGk7Tp;isjz606P1Y~r3LA7-9evwJ`#Vd^W{#R!))gRY$w+{(<XJNQx1CA%>~lHw9PH8f{P+4Nn$is3L1jKv%$0ej%TE1h`F*8+STLvwnAfdc6Z~A^A3m0ZfGXZc_Glp4p9*12tm(LQD&$C-1LD9=z~CN(!#Ue<UiJDn|Rh67*N~&+#i<1tM2b#W@Qd>PknGUdZ&#+mxl!fMhiSdeISrq+{m$oC*pUXyDg+M`sdFsjN_{1X4(>ZGQ1;no(pge@ex8AmFr<C)69ZZnQc6UAdKj56sN5_bMmx%+@5dTd63D!FH!v4$FXha@r-gOoe7>GGIbOOg}YZLVL9q?{L2uFCcBb-A=uc;IFm2nVm!WT$m|QBs%2lB{pZE&d~LYRcU^g~+`X6yWavx_9*HH1TIEd&353?Thm(}bxIX4%Cb`n^09CcPuD_mE$G*t2f2{Uv@3*G@3fFyrXCf=7<1>K~559yRU~nNwk_Zo|Mz}VNpkw18^($|rR;WKO)|=(Y9oO$qi>L9v8WQ#2f4ItB72>+=2h0A&Ob|oW3TUTDE{IJ5Ep6|VMSBGT@jmqw;bd~FRc-IK`DXAAw#$>1_wr-$vJT^&*3<YFr*#W#A}S}tb3qXz$y}387^8FLoKQ{+5sfp5sqQt%Wcy|^uKI}7qeE~<+qgrpH<9;NV3*xEPASib=Yl9g=FkyAYlaJF9LIG^7~_;k9i{DKjT6?Xb<7nGxSP*ZkMy2Lwwq6HPzO)2syD?|vqit9^Z35=hVppwOaNC=C}VS*KViM|Bzxd^CJ^ES*wh2QYj2!aaJy`~GWpZS&{Y?v&D`OWCA>2kC(`mzG7}uR<_=e^0}_J>=vGs2f@eA!r3jv+`Ra9`OUo+AUq2p}!S`3Mm%sfDfx(A8QwQIV&#e%i)8wtt!X)rutd~|x^k<S|_!_;!{tVUo1?V%M%4hg;x&SX@jnA?y4v1BL$A?=tH7f9BEI(delm`QIfzUEf9kEdpML-gC1P!Iag%m9JzCSd_#=EtDSkz6M&XtCJe0}+8w|!Hyyxnz2!r}68XfD_pO^Koj_iqZy<XbMCi7W#C4Z6Q|<r5Wtu4{2$ZnJM%JPkB<O^W**(y6>Wl+6Q6<}_0fBf2qYU&dTu6OAR>2C2NKp|41uV40Qn7T`?i7L{zbL&vI*C9H3uxTyUN`p?(9u<dkSd3MfRpvB1$C^aN1)X>BWNYSgLLS~Uvy`wl5d&APzk9bzrXPB)&UMwBdecdOz)784#u6KjweqbiJfpkI#-_vzTrbv=O6Hbh_QIf%)oZwcqX|dg`;S{!usx6*3tsh9iKfJ#fhsy5dOdzv>HlSj{3$2;4l3J&6OXq?)uPE)EX|=ax@;(3h^RYAi{@F$GM+>)c=-c!A`{Ml#s_u2!kCwx$xnRagqCN!0bj~z!Ov)VnCnUO*bgzNQSp2%(wAc3w*skx+Y|jtHyQ(REYjfPS_%m0xJr4b5@Y~hJ+X#<mCzaC)Gr`pe4i>Fy;jD7PN<8@zdMUywpZh>}R`uXu=e3x#=^yT{kFDyQKmQ6=t7bFweSG^{-%er}EPtO*w<D&#wK{3xz)~huRKz6dxkT4m>U<NWxt)-NKj3a0xSP+AtKY_az~wCeE}}T$Xr2lW$eU+_qw_S95M;n}5UfYjnt1~hfa43(`z51eyxy$p#lvBX;h|=5^Gop|cWhi2|McqhF6(>w0NdM~g?uP2N6B1p1W49qS~DvN!M$vfw&>n*5s4Iofz3zfB*$OQlKnp!Zc$)N*BKf9r=r;r|1%W>s$8P+)U!5HYefvsb7ds}&9nfyC*hPV4%+1=XC<%Clm8Fhu7;H4HH>3r|9URC3C?6h-%eX6@#oxRE2yNYj~s;SONmBCsfWybyU2x0o5ycgvSx<ct-pO7Dp_5`*(btsCzuJ6QYFelO3dn*m=MgF5JUnQjFNO{pD^a!RdvXWNN04)%Q{rMEBa43qpGiS0md*~PKIWJoCu0!@6Ayc@We};>x5ewVn7eX^-YP$Sbbi@{o|soLTt~Re<<sKdza<i%d-D*E?9YrMhlHy?W8t=M+po<v;?f7-K~@mX6!mxEf1+$`_6c;US4(O`9ILXZLe+nH~e)KrA9SoC>l_PFnm`1-FAbAzdSd=`=8&ljdACL^mzIa2<Z0;3qf*bogvXeOg;TA-P7OZ&}#j7eN2D*`^9DPJ6vy}-PHpXzu|^Ovw0d&-Ax(0whfd1x;m98F42op)S#_k>9?CAocp}KFT&A>7V>AJs;EjtMlMqHnz7t_Qacw|paf`7awZSLhG~(OXVMfuUOwLkxpocH$4jR{TAoT~vf~L!$p)dMvdOSObdbs67#EpHvnL^yV=8<f*Q!07N&cF~wT>=GFaF`fKEicIG!HCk@*#k;T7g0oEoWZj)|iCS%*Mt0z|C0di|Y2U{Jf9cT)e(0o^Ge5=Q}UC8{PZNa{-MK>4ep|++40J@IZJb#U;YvnKZQfo)i=0+xz>f{Z_5tLE`406?(;W1xK=SKRy!-W9Dl0z$8;t`+zDNlx9vyuQZja=RHs(??K!wV48n3@E$z-P`s^jIc~r7E<RipFW}*Jy=NpGq~%5Kh`ND6`UAR!8p?!aB)7BuPQRU_&&f?zMOhqtT45LJXDJmLKv+R2Frf@881(ZTLaT)3fSX(NnVfb?@W1d?eU{#TBow}^YMfN#^4O1;ld+jB!9ar+E|W6n0%%P_?mI71CdEY;W$2ACLKl~~QZ~)QnLet|mP4=k4=6@AB)=|)pJxJ=M>{12>M{d_k07ncZ5V;#%8NEP_s;caV}<i_y<Kc_q3#J*SMNU#?Mrq=^*B<FuV#Z6pqFcdVhMMoNg6D%gas9tK_vUCW->Y7>RbbPdcM6bUT?3jcdd^v(J^_lT`t!<!plLpuYv4^e|@~6Ige&DM|Z>uF(r06J>RCEQ<&UB`ryNUYTt9}4K+C`?eP$%R!ZYsL?_QM>p5|>XC0FJqBB^2UT@;!Hs0SKmP_TLvy0_t$aUkn_eG09Tz8QE5$|!RyAJu>tWPJEM-%3<K1CFnUo4X3a|w^a<;t}HnIyFddd7k=JeX71Z{f^#&iUvajC~g(d^vED%mpY1nLf-*t5go00xE?>wN5&upj7OxlO|W2U3<XK$G1rb3F?!J%`QKER@a);Nk=qjFJ*KzptI{K2%)%-mbvZ@gz_PN+E;$(=6#EQ-NtxI<ayaO{oA>^|0T~^PZ{TXQi5Sld0svjSh=^-IwG~Ff#4FfmKZ|O-C)WS=&zReB<FJVc+wDSNJ>D_Q7^2%Hf-{GCZK`L)O67W&bih;2?AC|qKxaR$NCJIkqPyosy8=_hbE;{)IMcd+uZd*Xq<xTaX(n@24;erwMGQ)bxI;pCeh?0hN8=6rPeC-IqGD>##?nR?&b@uPV@Y;sLm0nPuT|9?d6`|=Hee-^%?&slgh&hbHSDA%$=w_SDXvu1V>~U$t6xY%D#m;AMcj(l3Fcpn&p|BrRONa%{nwTzz4i+oAr7(Tuz2&f}JtSrrfgJ2toonXfh#ooJmkTI@?96hODeESsIP$oG^V9sp<^qB|gbb=b3jl^*(>P93{KV>6w71gNxic$&CRj12CJYQpPK9t@C|1PerDLbj~ka?qPKre17%ha;#PCT3_|F@cnQ(4$TEStAx}>F&#h)kz%q`b1j{t*6@57X3np@C%4Z3`2zzm*Ogop@9(z5MacV$%VOwI2X%2#qsFb8UfvP%LUx^+Vai|na!O`j(nzV%6qQ1K?|SLv^SXPa+#dSyQ}eykPrC@AN-Oj&v<^B!?v!ISaOyl7+0JW-JyG<?CGa`l-fYmf^XKZfhr68#1IEP?zwM6k;y_#;h-R`rWuz(Lrd5e$YG}%>F+zLKz#Es7w+vT|9~_JaCA92Y2jjGmc5*V#tDH8Uh6F&ll$_3mdCBJT5<(M~nQW=IbtbKc>d)0?v4ZwrPfNOgfB9j;KG&j8vl(vQcB1ldcrFlXmW=W;p>jbqr3XrM6g){1IhQ?Iz8cGMI{rD=tT)>;6*)VOxlX+_T|1^p>#%)mz*Esoz!cP=|I9PBA0%xfs_1-aY;$D<i+#jvs7vl5UaJb0`H~Zl`1MJ|YgM&HzQg=Fv=~~(^&0BXHpM042h$1ViEt)>^2ltk+)A`}sEUE&R!@;pNX{|RCy1DXfJE*>Ib0i`MkM;am3s#G;R3mjMC@hdbbKy2dZ(>68tokkl-eYMby_>elZdK!rul^WB4!Gp)wVs8z_4q@v}XX?pPJjYg~y}5H^(!}lUZ{?n1b?nlm_9G55_YC+6ir}585TuCx}c|>gN@N#r0}^YNY?(m4m*5(2+~-*&TAqb;RLe@N`;vGG!*vGOH+w2A!V7(BU>r;7brZpz%p$>Y<(X7mBOLdQn$!-JYp6Lx7scN!8wKoCj@RR6iA#Q^8!2)DZ&bD&V<FIM+%^h>9}lwdS7o7x#Ma4mFwCygF%E@aB)p;=|20(**S!|K&w)Lb54eRjASWAK%<AA!oVwtmeb)AGq6M!fH#6&O7HjH}_|MeZH$Ni}4|T>fJqwk`PZ+z6taW6c}Q`2<422AXJ}b$M$J<8Ai8F$7yyMx~EOf4IGjno_u^>JjvY+$|u{DVA%|>`fs(H`U8%ovX)CjR9D<@&G+*br@tHBM^hFDpI(>+{aHYgq*da;wO~<4T!>O~{4;4DsSh0;80FJ$ZR?d^<u;e6;mTZEfj0W%Kdv+F(lX*%*raYGGnS8fs@c|?Vlug_&;85ymS^8LkAs0E$(&>~q>yU?O-`twKHpeCATTuBVUuP%eb1~_>rbt$zGc>=|Db=o$%aG^^G;YE31)(0bSCGBc9-)c)7h!0mDfS%>K7e*ro}Nx+_#PR3}4QY+Z}R}9J3qji0%$%m1kzm1zIH?SZ1x(Ij5QV6cbG{#AsQHy3hFGduXpW5A`C$a0+a1Cv9@5v(MUF;vR4AVInBU!MVUESt21{qM_o_=%E553L^UUhJy%ws6-z$#oXYk^vnD6O))n|+&Y)bcptm7@}L*yWLkM{%1qD=E|Y=sm;@SC)CJ(i5RXS_v|3u-Sp!4CR<;J}<|JVVje-0XGwIMIT`1<{`hChy+@;#TX_paDRrk|>z#5>0P_*}mLO(?r`aNsl^!FD$+f@fokrVKLnnmC}XzzU{fr`=vT$#Arg;!D}W%W?~X2#m1<^dLGH~LGI9JGPzM)r-L=Cb%))L@NvHj{uFW?Ym~Divui>L;=%R2`L0SGjr*%eyo5zGq5T|HPT-2BDXEQoA}4%>~L_hv0C<*<ARhy%sVG<20e3fb3fb+li~0plTOQ8?VnClFu`_L=~5#<@p9D?r%G~GLPw$@<conP_2oaIFL$eBTc}0!Nr`x7Aci1_J^b1AC4P$*paM196vnCRaGA9IPFMwRptEfT^@J;>I1Cqpm+k0lQ;Z-dqkf5g#M5`c#@xbNLrCBjp)lKk?@qX9(TGS$#uoAUFUF48J`wt<}&MWd(P6_)o6}}=0PL9-i$n$RGyhIlhtaX7s5FbgLTFrzId+MoVgG=x~jgv5SiX7b!_Pm-zEt@36vgmp*Ua*K8Tc_Oq&V7+HxHk2ryh&22nA~qK_sxj&`Z-&)5kS>3V~Y5*N#Ad)oM@Cuw|(^7bD`fUjPDoKQGUq~%UB6DU>WR8uLFa^BjM`<Pj7xj>6vf$qB}n30C2&q=z&b#;1oH_b_Ui!-vFzRyV756lHO^v@M1$)lZalQ)h95@Y7EwlwV$GDiZvu4`G}wf=mc^={D(t?9dwa(p!t#8~eCl$<BHb%RximUo<?f5TYteNS~G_K0<np@%a@`IBkjr%T5??gu`~PkV*W1Wc(+G&%$&L<Fa_^GQU3)(5xF$36@{O|uSd{1TUoW%IZSCw=ptcEa)OYaW{=97Zn3DdjowOi)GREqE<CHH0N2L+%d8KryuEqU&18Fg5Kfkwb3*?#~3?psj}0`4sM}kSoFZv2y=<E|^h`npY4fXabhKNXaDSHc@I#61^2UVs>{At4wNr&WL=Aj$m8G;?F8Rbh*C+jble*Jy?iGQ_3UpJTPUzSxig^jh>G%lBLKJu^L?qk-EfnvkO#acPCeLKEV1Q=@9*$%a^Z<C$YYUfu8=;#rxti_v7k&uZ|U+yIB(|mFdJEC?{it2$qxWB{SctCOiGeo`&J<=b}3#f12+@11LdojT)87E>tpt+wA->MrdwOn8|6zddB9aYH-EY(VZ#desfbb(+*rXDYJ@B#>@TKOcp3*(nu0?1a!+S+K*^KMy9FrEU><xo}L_(OIYE;fxEl2E!R6v*6bKAUNv>w3|X9acX&$p8yw#e3SZ%FKN+0~poBZ(qKc@zGnsHs0*P81$8qDw*!$v>fMq~kUa!vH`8<tFcz||Ti?0TlcBAEfWFEk!C<!R40}GOYLx&r1ZEDm&PCznvRB~m+B>8Z?T~q<4?ZC+{^$?cBE`9>Hc>eTJwJ!FX-*8@D_OiblF!`@iBAA4o1rs9ThxShh5pahKjYcL=59sF5Y))n{>BO&g@VH!T*M16SXHh;uK{J-_eSAL@2keK--OyZ+3m}~ll2YL<Q9dZkaT9~_N`pjw!nM?+bK{mxvs$cBwVkfRCs{TwzMY-D*yX~#ziDtEkF&~e%$NzXnmgeoSTw{1XDY;$a>I`xNknVN<~6C!WKFI%xz8JHU0a_l%U4wh`0e&=10z@X)@_Fn*{cul`vA{mY$i}5RJqr>iq;F^1y?MgiRpteQrJK{DD8xSs%{odJB?TjFi!9H#)}C&9^W;Y!ElH@9!@GxCd>s?mb*__8X^O)b6+V$nHeWilF8uu%%nj5&WFw2qOSbyXUL(T@HtMA&5JU3x~x2U*JvMa!)<e4{P7uv6uB!{FPq}&=a`lFnMvhz!aT5zDR_^*Mef)voZx~6O=C{&3(*2Vw_x=&cl&1DY!_+UuHba@cZ8*}D;wB17~RvTb#EEkiJzTTPNvKR-C(0gPJz$dgp3p5kkFYHQE9<-hpypcdGs|LlRob>){e{hgz$NIQT_(M{GAa!e;+xgBnKxXKu(cNEGj~&3SI^XCiMsDv};A}(ZP)izdT6R9@gc@*O$cwiVwS2TrT5kmxO&Xqnt`-f++y;2{`5G2C?J~NQzERay%fqve)x+B=WpN6aIjTj&zB|Z(rb-+}0x#-S6=AYx(x3iFNg*lawDfa5D`5bznoFK}We2Ib)1}C!2Ws358zhve<u2!T-mKDZdY!amsoVCDX|yLKOfDi7KOsQ#=!?S<1B8tDkjr*G;=vR@36Ez4Cb<@yl<rbNyS>eyw+W#l?s1iKy&{XR`Q=#-mU>m86v+86I)hav4mpiI^b!qi{6eH(y{;KTN%EGPYv4=y-!R10~qO73>hmn9M29$Y+D8)*&R&Ht*n&tJh3Uf{D~qZC#J)=$U7_)2`9*DW;dthcu#nTVWiUb{U=rVj)KK5rKlHxDt~UBq{C~W5#<hsYi^Bh_w|e<DP*01haQ2TQzk!xFFjzjqjwV9ON@W(gP@(YkMUgi6If(#*TX~5@AA<ghsg~ax}_g0Jq(sZN7G}K65TU3;0w|wi{HyLzB4I73$;lP4NWQa1U(^;qh=*d2Yr`&~-^LL1SdWCx&Jtb2@=BiZgF`3jI1APs@i@UZ{ul7Q1!2TY#@{N_7qO+472$Sto@Qvq9E`=x8K~<2ucKh^=5YWnS};Eba9WofJ`2;Rdx(zSV6XPYla0G<lR`J-NJHbr^R1JBJg?vy*0muu_~-9=V8t0IGIkB=gFlH>s7%*OfV(L`Q^sUi}BSK7qHR(cc6639jpbh-@z?Cxdf=kFjV)-su4#1V~W?ODP2p*2F$S%*PfteZIi~`SEV&AEyx<upb`@{|@PozYn93QHB^4&D2lAMDVz+smo=mA!gQz>_ATT`E|Q;xs)PzGCf|0zqPP(9iQb<ImK~v)2_hxbaHui;#`2H4($LQP3|YDMZ(P;WI)}ofpgjcZOD{#AHBBw?$1*{Jv($mbG7}&Mjq>2^h05}7t91lv_Xy3DbWZrP>L%TxI{CRu!xQ^dgqGrfv-CoRJc%K=@)4Uz8O<4QGpEx)n)D-w9ZsLIPvSy$pG%F%st_n<+3`OQ65SEj#2&e^7X+a@vD%(a`KBo2b1a@^lfurt*fTSZTal|+t=MwEaL`0>l&ZxpNsg#-#~pGi*?LhvU4Z_zw3wnm+Sw@xD$dq%fQ$uIS<JhZ6Oe4dB`@f910QgKUsj~`o7H_rjL&6;h*ejj!_^8@9$f`W2=9&*);jk-asAe;~C|lbS@i#InR`jTuE+YQm`x7B$+!`_hG7$xISN@-1_4v6@JCH`02UXYLDYs4*!j^_vvlQ?yWx%ze0V&h0Hwm__SL7XwlaG;UqqE`R!8qpB2^e$r38S0F%~1V9|h-!8;l_I&h@F)h8p}=?dzbLz;n|mGKHCu-WV!i>^%lpPpa+=TT5j2IsLxy+w~#>)aiMhRl!cGPP9<jF89ydZj1Ez<$i^oxS2a!7pDaA9kpiXu3n`R0qxlNdqLCw*tKP=;%`1;VRRQ`#>d09dV*zV_HtV`m%U<Y)<^#=iUB&6~9!z@38O*CuMT+ak$(K%>+4ZyyHGPq64#p0f8h`po!*=qO3nB)#Tc~uVAkOoEo^N1?h)%FuNL}p3>&r-121JOi)%}1bVN*k>C=i+%N)B5Tc?;jI%xxqsO$9xLm>_o(~y5$-;J(+s$WacgZCC(BLR9Ph>NJ6IZGu&Iu2KqthTfF;PgXouQe0p+7iBI6I1~g9?S|v3XF9bx=b5?B&P5!wTZRfR1NNgLxN$=Dz7t2PcJ4c!D}R=$r{bdA<2^k`GFzJzEEg=<}(dlNo={DWTs-l;T2+EO6_!v%!GynF=HtE{O>u^_Hd>nU>n+!rj)#rskV>SH&y1u6)l8^v7#7^fv1fEj_41(~B}`Z*#kRtb5rHP%NSGtI?v&XE;Ug@7p9x(~l`sM+-Fltb8|0f0h;LQm$@wR@o4^LN7=pM=8@>Q$YpGsX4}w(F0oJgFQ5hP}O&Bb?$bwY3hfjDgJmE?)IMI4ldrd)z=kbn5BzPgSb2p?ErlR?QPsF65Ka!g_`M`tMcXRr_b^idG`84@$BhuyLbM--CbLc+eWhfD|$MM15iQVFZ*Us5<5Be#)uu{?0M@;^@tft98l6s#=rhlvni31Xm1wSJcK~tSY4&DRMYIL>Z<y_6@IP&Wv5|k6*g_Vht**NtNusk{Z~g!4tHoad>U#D(;or<z}#@3NA~vk10dGnfQe<^(C~!nyU^nlJ-O!i?`}~aOa}g>n#QPI(poYq42cHK8*OCeGIesha>zcuaa-5jIQjCpUtLyb%?TX*<c9hzS;oWeJ%G-AO)e#pWFVq+gLCUyNXnGfya_^gbT8SPZ*T1Qt$R64F#phLZ`<Jpe-u^I<)v6KL_(c2RBIZP;wI{#LyW{rPIxLe)1w3GAwPb1Gbzs*^5cKqQ;n&25BM?seId5OBN5#S&oy}q)<~^0#1!V(nB3@$HDf6gYH~Hyd5jyI+QrVzUJZ56^)BN_@4{y28NsjkZRdV(E``-ZuoPPg4FMgsO&BoZu9LV26~heCrp%jLt|}%^?#6XbUCb!Rm$6U(^u9%*oA6|uY5B&ZZ=zDoh!<j2u3{b#7Swo?`|4z1z$l|iqN5oKBm;SP4u0@6Tq1sy;OEm%Kd*jvk0>4SK2D5~*9?)L+OEw2_=El*f%}knCQD5%W?KB~yv|oYJm0I4YL-0mPYZ@Pr_M&|Y2X-$JDa@bD%q4AqhaWyzW}AkjJZ9v_)Yuw4IS>7k7v5^G2y<}sm}6+Y*Xf_l*Br2h(q5^If+piDU-`AiK*NVjbntR0W=;T;P!^-)Y94SQ`9&uRQ-#k*h>R3!o+9-A<kyrn`lYIhn;Y1OTcI+Akx8k_qaZW_;lOOjgJCaVpnGvoyYu@#;V+1KXqLSne%$ph0Zdp>+%3A?l;U5bqXGH9+gaD%2IEQl91i{iC-wYb@b=HYuCTImo%I2^u8L?H5Xq7=*XAV_g#${#Z*+~<!^JD{v=$8H8pUpEHM%kr6M`*(7=pfwqu2~u^gC_<Yu4mE-yjRhOK&mbadLJBl1}Rnz_=cuH;KGs#Jnef-aNbvki(1Z5c^EVk9bqDbbR?1OdsFsF}*nB?!o!!!27MFTg*}YYQ<R!{A&|&Lk#?R)Yht0c7M1l;n&SZ!@CwwH@THyIWT?pv0AmSlE0s+?$o<>fDNVd7JhZxV#|W7%TrRlV-|ijETJTkq}Bqu#9o3nIkr8V5OB&jQvc(Z67lc>fE*E9m?_rd*MS}7werC2UgTY-B4OkI$0ltrcMzr@-fMM?zquTD=v#SAM1<-I<B-V-dOJz4Q?F0{xV?PRJ;(QMv%dQGy)?_Z*r%GvW6?K5<c|Im(0hWGoMX+?AfEQGM~3U{j&PG#Sd~gWj<#n>FRBJ?8n;%`8AjHzrOyZ_|Y^cQW{I?moI;>{+$(NYf;TGO8;3YkBu?WgnN=>V#C2OOeHba1Z^l~X{bROKBa!~`vOpN6Ksxv?yqZ~n<Fw7z%kY^PQc!v*9;l@*WOsozG*2tIhf!xxMapuVIFfM+|Rhk1S6OvmE%EA=vSK?+MVk*@r!!eK(cd-@pTd`#EurUO%9Tb1avS2NS+Fb0RvI!(SWj#8r$s)cgeQi@8`O1PxK_k`4`~#^XeH2ekrC?Oeh|8!t=^-0X#}8lED~?gR-iaj*TdN!`<8^<?6|Fr;E2M%GF`{FG;!jb>m43J{X^TVhKZ}paUl~;?vAUq{(7BNu(J^3*$?J$J#m`cVRPoOWfy(-_~%*gG<9b#}0%0)!oMJuEf<;v=DpdwgO==$aFFYm+1wmwowU3l%pvXjF1V3_Y<t2XGUgT;a7sg0DXvOW=_Tbt8bM*iK?sRrPxmaAz+K;@DJ%ClV0l(j5DlMco;p~uf=ry@i=n^{^TCc69*rTT|B~RsTy7`#9&NOL3qSd&|*wZjL8RKAW8-=ZKl4<1|yRJ|63-J>k<F25{X<W{iS(C{)JtMq=AP#prwWOED275xTGL>>&gs~o|JqJ?eiYp@}|D6dZT1gt$l|&_hoKilvI<|Wf;%^HIPV#7^afQU{#9LC=6{lo{M^jXw0O&x7%;lJ<0q`gD=Y5kab^H9nP|=YQ$2k7%YiYON_9+q>iVMqK#V146w^kml>;u%46T&a)5{S^ExyTHnZ(52T15~|EK-jv`=RYihTcax2YpMY%VpbYw1F)Dy6kSBz&fmB7hHhkURjxV8}5lhSqa69l^|Kayxi((S2bA^IRS4l_n#X1t;mexD*SD%$)3)R`}N>7{|EBF@vs%SnrG5A(J9c%pT{s4}N~+Dg(z%OFJLk?vQIaoj*}4UZ_>GDhn~KFu`dF;JoBffC>1wV2lk)CY{RlIUn<x!?D@B^>+U_=h8oA;W-b?|2;mQIKX;Yb-TV0Gjfl%i%~`c3Nnd6bk-WBWv<~yQ6llt|Lov+<GzPhRXA?l3BA2{)2F4uYcIuOH0EbnXYy%~N<^ZR;8IE|5~HTvO4><xRpAOr?l@Dqnc)gKz~csXtM4-sAAZH%HC4!sd?98fAxUSbuwI8;bWlWNY>rMG19!0Hs1&1F@BKQ>woJe20L_!>WXdRI+o4+;jQ9P=Uq<<2dr@@?N+dK?iV@%D6HUAyLctR<3D*N5$w>oxVWxhrFTqQPm~u)mUDE7*Xm@+ucT-U{E-zy{YOokYYKa-POu2bqV;~uf!dEOa3}8fg2B7I12#)pE{l4CBj@$Y$*FbQrSMS~Ld1|$P$~VW|{`dgsZw~uoJrP&eqJ`KqIu?No>Jn#~6T&HmLMk}}0lX;%2t5It*k%aF=e2KfU){oP{_@`K$8y8#c3<$)OM#(}d`=40jBqI?6_{lkt0Uu)Vq~d^w@hfO(AI?Xmy#!>uDnGzu6u^v`nR@gU=D8ct?Rm%)vx(vY963C@*mqS;*s?Z;96WwL<_NJNCC|lg>=jT1oS=LYP7D$lOT|CsYWOG*=@cgI%GDR+X8r&x#n_Q^o+H8Bd$i#LTu@Ej8w=J(P{fYqR3?$iH0;rx^my9)ui6&nvjioY}^iRp7vKwXuI9jM8v0Fx4;u$$*YNMDfXmbC=HV3_G$x~vq)@`6ys1~bb;B_YI=O;d!pm*1nK#b=w!#!;9^01o=B?;$x`e{h`<mt;ZsgjNS+d+sEZsT;VQ<G?u>=l-~$Hmv$t(0sF+hH<;lVHts40)ySy8J#Z&T2s@0X+LQI@JJZ@I-c!ow|aK8#0b*`XA&%xxoI3dQ?%e~v*9{~fXuyb=@8z0?1Y~1S25ni^r=J>07ft^2g_a)Oj2DkbLuGiO!>$74tQCe)w?--}n1dC1|s1{xl&aI$2+7Oj2S*aN}_5rq!%>WR8dmWO6r@HthO#C3bfsbFx=2h16JjQ^bKNT3Ff(e2U=RsOYRhix8V__}ycz%!d;qc`H;sVd_1FG)xrrg}ftBGtO)|5sC3xENYBqBh}=w2W|D@IAoZ7&w%)B)S;;PM%eyRdQp?+@K<5*2dmde`ng!{Jb$dWZ4*zT^zQa}S%$F@L32&8RF0G|B#*sH`M3l}i2RK$A2L!Bb`MPa81r<v!(KoWYnQ32QLqFj_D+0*5x+Z42u|SNry54mh;gLbrOKU0*@*|CBl4@UeK^zCc%9zB(yG*8Xet-DaTaf9Dw?!7%Ebu$%|ZIg!rF7y>5%#7mZ+5k7P>_s<BtWYp@q1Gv1eIXxrr(!uKI7VXz&eCq9hDfUAL7!kidc6HOVtM4$Y?&{4E8aMZj>dV^8IGGe!NxVs1GaqS)RCASo0l2L2pemz^f{l%2^&?u_HO|d+p}fn&cIz)V`Z6KkNl;A}7h=FbRYnfLlbi}61*i-ULaA0dV8mCZ673`*!E>hFt&gGI-NOEV%(B(nX;$*f9V(V-kPBfo3YKEX3`GSN3>OI>e9T284;d}UL{DhJt>NQN%<!ui-p)0K7KGI)QPQw>rbSwc&74lalyEdr9s|qW;h4ZNVi-_h19B~y&dzPt8~5z$`fIaur@i+!YyPdi9+@i>$D?Q=wloPaQ3xr7h`DbHHp8V+BDkX#1K;u>j}h-Z<PM&r^IvF;f+>S#X-%GyRC#qN2IMS)ay)b63TFeOEDE$5M!rrI+Hk}8c@!z5E#xA9NVn5SJ>J4tYst|5<5G|GzYv>g5r_kxk~JLnR}xZlNXaGVGA}~8Y8|uHeQ>!%73zcg(zwj|rgb#mCk*WF@GTLVS*2>Kun@xvt34)MjC&R_P<J%bnK>X_!l{a??F3EPbxpT!A7=0u-qkrV=qFZ2X0sFe<ph4_QmvX%S%_&d;Bk^0qP!vAaT~cX6sS&t=tv4B1)VsZoX%t};#>G+G)*={oFJy3kIX*gnaHav*)oiYyU}}$c0ei~Rm3Ay;C{`xzt(6XXo<ha?w$R1)2@A6?_uWo$M15QuPW6*R6BJ`jIwIFz7Q+M=qU!<PDE=p(;{LR6a?YU6U=W|DfExA<1W`&N+5pQzH>KiLOI9W=k-Y(z7*@ZgsC-AS~3VOfFY3682ldq8a9+#z*z5lembn(LpNu<Ja%<P>@V$ieJ+@_XM|M|EX0g>=;eGx`y?VsB!-}+Op#htFRYK&un}7BH@7`>?b_9G>*nr>e#8&F@2gU)PhERB3@Od4I#-+DyO(xGqnbz;VpXj?QA}8-cxLKR(ovIyrkE*8YWqi9iShQ$!*1jD>z8)_?B@DUUc(LzU*`vSzaQ?R|Jn9^Ccip#H^+ziTBo{}FT}FaNsN(*wrEEyETP`W#BCDby|qw6q|x;}^+b=m=i8_L_JN-=m|co5$jz2wNkNnI(i1|2GLcwqDC5j40E$VuDBaC;LejO)^UH^syNjPUZG_bi`|~i;yE?PuzFY{Z)8azR$Bk6R2|}p_jVYvYIytT(P?d6TSq6)y<0Ia7wZ~`su&JNxX66z9sjJ&eD;#pW_W}L%&~>t@-F=HL{l&MK0{vPyaYnV8Q(K6g86-+`16pz+krbT*JXTZCj73d-@8_qtMF$`9?APstQiqT8;bk=$WWN~fIz{u>i+|*e)y=|Ej30gEE~(t>hlhm=xb3-7K)H&+mBtk_0%M^2$tCIQjC5#5t#h?GLtfk_qoeRD1zm_CMM@zk5`ZQdF#8Mw)6abA84_6O2J-i52T<+W&HA{nXQ<zMmWQ9-{`Yyyp6Q)W&E|bG(1l+0%f}xUv@wO(0aGO!O#;aY4bf=Jd4j}2DcaZF<b=j2b?y-Ztj8MAPBW+P_nq4X*oSuYAxBSLJCysO>tOx%cz?u@^(}PSf!*p>^Lk6MG*?8C<WNjgB*KBFM2djMSJo9Mc{$GBx+dR`rVi$gfKw%H%vHZf#oY<`=0aRuh!$c@3nW^hosC9D&m;{l21=b^9wJwz5ztQP()#=QP5a!%Te`HH3;S~kq<uaCtIjG_6NQBs)(9&Z2Q8^o$r=bLd#xax(ms$<ha+QJ(DR~2HxtYDBX~WeLk}|Dza9Xmmt!j~g*CYSFb5DCV=2LGpb}WF3iqJNVgj0cf2@By?(-3Pb$c?Loo|kHY*#-#VP5)UzN;IaYdj=d3{rO?U(h%8pw??tx6*}}R)x%U>S^MfQ;$ihunwdQOh<0LD?kNfymZ_?gmv!ox!s2Y%vw)W)pvlP)^xGz7ZzeRsbmx+6GcR+8A+50#}g*?4h(lPkLxfs$)%e5;JWqx5q@hobGcMgXJoEVu6u$4FY@s*clvazAKT3?L(yJnRM*mlSd~dHqtnF8%ycLTIL@Qaz;-EeUuJB@nBX5D^0ed3PCPY|m)__;!aOgY<{KAdGdvv=Q0MT`O?Wm%FkT2tBM%IQdb*iZ;11jFu-;!$=aeC*Pq~rfQ152K^b`13ADB<|s(GEI7?%Py)?1%j%ninoCR$4vKKPQmT;9yJc6F|`9}n=jc6B#XYX9ZacOQm}<!<$T+Xd*Cs`2GQOh&E6SO&ecwSjBGbs`d7OQ0B$Cta!>Q<mpUR$9ohZpUzP_vE{#eTh?2jT2K=nZ&tJsjkFJF{v_M+K_?gqNhQDwq7$Mz$@H`rFoD~x)(5`S?{;UFSRb5l9v7nfB(znYL!=(VK?r1^5AX0`5`KDgS(O?sc;R?NfF9W*pzU*_1pbz&8<+5w>d>#y>Gj&9w$Nj!dhV-zm`{{Y$4`UNfARVSa3OOp%Czq7LqBW9OR6}*i#ctABDT8m-e{!Er#K9C89U(30=rP+vDzjsH1nQSKVRf9;Tvdvb+#G3WFl$DHlRI9T@S%D3Yr}M6uipY=k0{fu*OH`(v{{w0=M72B!8ru=Moe4(+~iPpdaa{5<6rzV$Dwk8XR+y+~II)l|3?o1#+zp^Qd<Oo`?+q0<gLPtT)GZE2PnBS%c()?t6#&CJ<-3yphr9SqRs&2aV8*VDQ>-osg=nw2iZpgbu?D2L#=<;00ZFt;%vL30d%3M-bHAPF6x@7LS?(;Tx--;i(us5_mUQ-GU+ynXE8x_(FXRqdr%JQD4M3Dzd`=Ft#569`BmGoX_%=vQ*w!uvymr|TZW`IosKrFwT*y=#vHUT=Aw+p_<*p9-rB!9om4S=7NeOnZWJT)Ge}&y1Ln2&+?RoZ}OSyRvIt_b^uh`og~JoqzYx-)?+St2%2e#Il%}Lt;Ey5kV$xOpuXqz<54i3>RJwsm}oE&u+iI0=(<bgI6C{zt*2!7t7|nd;7!c^|5L8)!n8Jxhh;<O|F0k^*_RoNplffN~$tcsroWR`t462DAzAO#7e9w`Lj^c8R(b{hWFeNlbpB0qT7<pL>UOpm|}`@sV8J`JNymjd(FdyaGlDL3~pD~&FVeGCy4MgUTeaB^{IBLg=>}SO1zvoMTSZm*T3Oh3oie2bBfF{lA_Vd0Mo{$=%V+5N{6S4k}{SdQ+ocT)%bOBt8OqYo$WvS0VCTQ^M>64_86C}8kZ@{hWO)cZXl0+quJp?v$~Kh<9K0~63+w=G-nyy8K#NITs7RXXyd?>HX$zGZJu`P?zZ5ZsomeYCm1iXF8X~6^y5UOx)CqLpav`pmQkynao*&KeQtOJ%s2AfdFePEYfR5=7uLReadRByzvfG-+)g&}fUkSqYf&{UFU5#X5sV!&aT@wPqg_JhqNwIZ0+=BZo-{%{x{$(p@4`$d{J{nMT4B6kb)j?lkh$QI9F?khg=H94GYlLsh7b-CKKKbNMCzm`(O5PNIIs~m4*wAhcpqQ~UnH@Cf&0CWZTr5G0dWT3<DREnHaD8pmDWORoLkSFCP7JIz!A*=@U>7(Ss{F>GSdm%X6^1fm($}}c6gL#RM)=Pd`goCTKFQWu9lZ#MIAzLF*4=2)<iNPs0o^hgl7ORExcxu3_tt0e(s?<ZhN7cXLo#nv3u`Cp>l@pDEaP0t(sL?h-ro9z%#iDW@%bkFP)df3Zubt2*u|sa!x7mSY0=+xlSoQzPele4@UDPp?LRd_0By&x3Au{`_(H9=Nb%gho#Ah+P~uuu+3V<^XR!m=Sh{7Ut^oS{^=`>&v~`F{TwUG*ZfaQEI6=aqClY!`TpBP4H`rW5mF*$!%BX(YObP&2Cl98!#}=Vp)-Pk!uRgocdI-1gr6$g@vea<=mVy4?bOXz-t-5uHMFUc1iysE^Xcn6zL!6Hpz)pg5G%7bf0kWoN+yBx5hK*-AVU@>>AeCW6$!G~RZohoskslC+~MXNIN`*;IW}q4!}FaFgfo@Hsj!-#_IdqkR(BaE1I7Iyi3ZM+H6GA?1qRAv&P<$Q8Nt&iF;Iy04U@PJny2+P_m^+y8Yb~WJOXCEO??l$diAP#g8hJAfVMmCS09c6KW5X^D@+DnHCsS~&%dqu>RT3Ke}Sin%+SaV!!bumL@(%s2tiPZ+q|41?0kk~<{@{uIhpU?zFNI)yWNQm@71Sw8Svm?U%fgsJyv{qb%zOZlZUHL+JE5muofK!*USoz`OH^OkDDLgU+h$?ix07~YxSoc9o%Bv#RU+d0ujtI%PmhfMok?{LmHtc<Gi7LSObQ;w^_u;db`b}<&*yYDRyFS=kJC9_X1S$U%auJebX|wGl3+9&FyZIYKSCd_9xstfLofo0$69r*yuLfCNnf#p8H>Bdt^rWZEwtCj2WV^9DQK|nRvuw0)oK|E^~FDN&9I*GM>%t*8P^7**Enb9_HHLwys<K==SwPNiIHYYCLsUAK~HH)L|;Fu0#v5XUGV3Rwh9OwctPyi@63EX(P8`fL0!X>+Uo9celp4=YAH$^3Absv(0cD_9$mBtN%MTfX5tqsh`0$gX4R(iyb%&NI&QKfj6!TwYz<9HLJD|!`qO2h6&_GX`7@ZxS5ruI=Mhn#<?|^D4Vn4JMdfF$c;Ph-CU6IGkCx~{NCj$i>I=g-?<~^%pcu0H)>ufR2Raf+`iLKg#MkN68i=>E@l5glt5xc;6lWV4T!&^=%wd0CuJ!EjuZkg!$t@Kba&hJW48-4Ko0Lw*0(+t3fUT!a?ku}xf+)ive|XWvm9J`F45`9$PB?FhJx1uC>ur&CfUp0?LvdcO!s-_Dzgy%+%*Hu&rlJqKexLn7R!}Zb*r%u+sXixa*l2>C*R5%hxs=37?u(SsUQq7GpXMH*1{Uk)!(7{Qu+SJb`Ou6zYQ$Xz3P>u8deu#Ll(8nmE}xz;VnMps1`UOmT0MA0K^~Jb{|9Eb#i0953U|qcBg$LWx2XsT87=IQp8z25EUrxG5$h3X(5^9j3`A*m$E!}Df{-hx$o8o`~<PR?Nok^;(B+#D#SIVnJjBw3aVjoDJJAVgBFTdZ39y&LvvaZG6X(&HeBq8DZ=0OalLE1G;=cEp)7YakK}Wx*ESE6LvN~5-HMlC)c*&kK638",
    "royal_paper_v34": "c-p1Y%WB*(6b9gZAn#zZtvpCa_vtFVL7^mFWFL;Cqd~-D8`+aKeYGz7NPUGWDVa2oMIjqo!oNYE|Ks03f0e}MN~y%POyhOi(JhVZ{bCsoJGRVIUD-kdJp|aY;tjEusCU=6+&r#UHsG>ehx^x7Tn`JI$ARrCCQA2D$7!Y0lgtHzHdzU<$y*Z1iI&jb1&-b!a~dIptaaqx2_+$NBeW9Eh6ut02F@#G8B*+Vo%;os-GTby5J#*%a4@|P?ar=f9LvJK93plXSW{tw^9^TwqXm1)T?w_-ek=V(C~b^>!}*p!q%6Dq`tgD>cJ_H}9|zZROWlk!)h?G|c-xIrhjBgN&`mW_WnCs(I{F@mLo=V9o_KEqmqM8UUZRZ+pfM(jAt-A#GA^S*my{2lz-#B7;>mcKwM8(-G^}&nKVhfo=G)Y#GS0QUfc;R~nZDNYV_)KtYt=KZ+$z{gx3SuIVUJv=U(H478i!Cs$bc3+a*ZAYDorWG%n*|GMo4&Q*Ah~&L^??zqb0Oj6nF5q+n7T?RqFT4`Y-h2{rUMPc3ElHCh<||ynaR$+osfd1Db<_BhmBM8zly5wBeA9wE)WIC~`CrJvEX7W<`ua7}u^o8qhLivId+MIjc6&tx~H1j+N4|-%r%9wS?*Jg-Wk#X}{Chy_@dZd5$A&{s-Jrul4=`IDs`m",
    "royal_parlays_v37": "c-qC+&2Aev5C`ykfV{&Z*RF)&42K`;NlogufT0vk8lWeGAG5AdMQcdwHU@I-6Z8T4AUWib+DGUpMN`-I!nTUY&}9Yiz#ln(a`?x)_siODWxHMW>yw}4qFu@9N$JnmLt8eI2B{}$XtzTe&bRF^%k{Qh^;;d^uKPhAKXTPUbc`mNSW6ZT2HBh7#hL2CwFu}Y#!L|2{`vcFzcw%Xnl9eXSF7g5S-%;!EnXV((wLd08R8V!d*;soW&rKCUoM_~v-qk7{XLnV5B=*jEK75i&SW#`*Q<->Y$J791~oLLf1Uejv*;UnBjtQZc_lh@vr4;R%Ob7*yXen{rr$JDPo?=;Hp^})b^7G-qu@b|Go68GDl!ULpG*K_5t$*}?x7J+&}BNj#}+3Ut<I=Ylt?b78lbb*p-UP3?H(TSL?m~3Pwi|f9*XxyiUEh-)<ozzitlnQw|fkXfuf6t2RY#Ew9+U8FeVo7ETvRjjDcK8mq818Gc?pZOBdVm1no34ic=r8e8U5PNY4Q3qUef34w)iKf*SNo3KzZuh(<sR_5mW~DjQu)DVAz7-c-SmB0%cAunqMNARYq|;^9H$vMYcbvvDa&A|mifig?i)takxj3DLhI4I~_1q?rMBxXTY0bRJ6xF?-!PE5a#^iN1UO!~A%1^!(}fFD8pG=99(I@nrG!@s&jJ4EzjfO2K0Ahu3vQt4(*xh(-triEJ!MFQeFz>5%WMqY<5F^8g26ja8Bjj*H9ei)D<gXNV600L7*I>iA}zX6+$#+B-<LHFT)m5b9yiUi6yEMYAWdgZt{P1n&~h!UF_PH_P}Sg_3qjDOn57iDx2LqEJ`v$(t;m&gVyyS2kRWAj8b^lmlybc7yYkorb0^b#B`alWR7zm9!~8{tezKm+Q0>f8MP7kB0w9cfu^guw0*hkWRIIs+dBu&Vj9ZAq%mMK(O1qT9SqReKh9NbRW*=wa*nF3<Dd^G1sj9ho-PZ-)R$K_xJIb&w@XkFXav;L_(+x1!cFBES9_M(QpX+`>qA?Bak^B4rr3zxCxW>-cuxfGzliBmpEe%#>9Pr@p=H=jN{a4#n^DOFMa}_)+L25I^Q|N=(^bHV@6n8$w1ZM|M1b6@8*N_6ZwiO37kPHP^z~S5XmNeLWg3seS!UbJmzCOpf5;Q1bz0#gc^&@(VBwB?H;4vcXof@^#J|@bYu<({0qB%Csq",
}

def restore_bundled_royal_backup():
    if st.session_state.get("royal_seed_checked_v51"):
        return
    restored = {}
    try:
        with db() as c:
            for table, compressed in _ROYAL_SEED_DATA.items():
                raw = zlib.decompress(base64.b85decode(compressed)).decode("utf-8-sig")
                rows = list(csv.DictReader(io.StringIO(raw)))
                allowed = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                total = 0
                for row in rows:
                    values = {k:(v if v != "" else None) for k,v in row.items() if k in allowed}
                    if not values.get("id"):
                        continue
                    keys = list(values)
                    cols = ",".join('"'+k+'"' for k in keys)
                    placeholders = ",".join("?" for _ in keys)
                    before = c.total_changes
                    c.execute(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})", [values[k] for k in keys])
                    total += c.total_changes-before
                restored[table] = total
            c.commit()
        st.session_state["royal_seed_checked_v51"] = True
        st.session_state["royal_seed_restore_counts"] = restored
    except Exception as error:
        st.warning(f"Bundled backup could not be restored: {type(error).__name__}. Original exports remain necessary.")

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


def _verified_pick_winner(row, game):
    """Do not grade without unambiguous BOTH-side identity and a completed winner."""
    if not game or game.get("state") != "post" or not game.get("completed") or not game.get("winner"):
        return None
    teams = game.get("teams") or []
    if len(teams) != 2:
        return None
    selected = _unique_score_team(row.get("selection"), teams, row.get("sport"))
    opponent = _unique_score_team(row.get("opponent"), teams, row.get("sport"))
    if not selected or not opponent or selected == opponent or game["winner"] not in teams:
        return None
    return "WIN" if selected == game["winner"] else "LOSS"


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
            if pred["model"]=="Season-record baseline" and ask is not None and q["ask"]-q["bid"]<=.03 and "OPEN" in q["state"].upper() and .05<=ask<=.95:
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
            if not game or game.get("state")!="post" or not game.get("completed") or not game.get("winner"): continue
            winner=clean(game["winner"])
            teams=[clean(n) for n in game["teams"]]
            if winner not in teams: continue
            rows=c.execute("SELECT * FROM royal_predictions_v34 WHERE event_id=? AND result='PENDING'",(x["event_id"],)).fetchall()
            for r in rows:
                # Match both competitor names to scoreboard, not merely winner substring.
                result=_verified_pick_winner(dict(r),game)
                if result is None: continue
                c.execute("UPDATE royal_predictions_v34 SET result=?,graded_at=?,grade_note=? WHERE id=?",
                          (result,utc().isoformat(),"Provisional ESPN result; check exchange settlement",r["id"]))
                paper=c.execute("SELECT * FROM royal_paper_v34 WHERE prediction_id=? AND status='OPEN'",(r["id"],)).fetchone()
                if paper:
                    pnl=paper["stake"]*(1/paper["entry_ask"]-1) if result=="WIN" else -paper["stake"]
                    c.execute("UPDATE royal_paper_v34 SET status=?,pnl=?,graded_at=? WHERE id=?",(result,round(pnl,2),utc().isoformat(),paper["id"]))
        c.commit()

def refresh_saved_results():
    """Match past saved picks to ESPN completed games without overwriting history.

    Scoreboard grading is provisional, not an exchange-settlement confirmation.
    Keep unmatched entries pending instead of making up outcomes.
    """
    with db() as c:
        pending = [dict(r) for r in c.execute("SELECT * FROM royal_predictions_v34 WHERE result='PENDING'")]
    if not pending:
        return 0, 0, "No pending saved predictions"
    sports = sorted({r["sport"] for r in pending if r.get("sport") in ESPN})
    # Only inspect dates relevant to saved picks in the last two weeks.
    index = score_index(sports, [p.get("created_at") for p in pending])
    changed = 0
    with db() as c:
        for row in pending:
            game = _match_saved_game(row, index)
            result = _verified_pick_winner(row, game)
            if result is None:
                continue
            before = c.total_changes
            c.execute("""UPDATE royal_predictions_v34 SET result=?,graded_at=?,grade_note=?
                         WHERE id=? AND result='PENDING'""",
                      (result,utc().isoformat(),
                       "ESPN completed result; provisional only, market settlement not verified",row["id"]))
            if c.total_changes == before:
                continue
            changed += 1
            paper = c.execute("SELECT * FROM royal_paper_v34 WHERE prediction_id=? AND status='OPEN'",(row["id"],)).fetchone()
            if paper and paper["entry_ask"] is not None and 0 < float(paper["entry_ask"]) < 1:
                pnl = float(paper["stake"]) * (1 / float(paper["entry_ask"]) - 1) if result == "WIN" else -float(paper["stake"])
                c.execute("""UPDATE royal_paper_v34 SET status=?,pnl=?,graded_at=?,note=?
                             WHERE id=? AND status='OPEN'""",
                          (result,round(pnl,2),utc().isoformat(),
                           "Provisional ESPN result, simulated entry, fees omitted; settlement unverified",paper["id"]))
        parlay_changes = 0
        for (pid,) in c.execute("SELECT id FROM royal_parlays_v37").fetchall():
            outcomes = [r[0] for r in c.execute("""SELECT COALESCE(p.result,'PENDING')
                      FROM royal_parlay_legs_v37 l LEFT JOIN royal_predictions_v34 p
                      ON p.id=l.prediction_id WHERE l.parlay_id=? ORDER BY l.leg_number""",(pid,))]
            if not outcomes:
                continue
            wins, losses = outcomes.count("WIN"), outcomes.count("LOSS")
            state = "LOSS" if losses else "WIN" if wins == len(outcomes) else "PENDING"
            before = c.total_changes
            c.execute("""UPDATE royal_parlays_v37 SET status=?,wins=?,losses=?
                         WHERE id=? AND (status!=? OR wins!=? OR losses!=?)""",
                      (state,wins,losses,pid,state,wins,losses))
            parlay_changes += c.total_changes-before
        c.commit()
    return changed,parlay_changes,"Completed ESPN results are provisional; exchange settlement requires separate verification"


# Royal Focus started with the V4.7 deployment on Nov 20, 2026 at 9:07 AM CT.
# This immutable cutoff excludes earlier experiments, even in the same leagues.
ROYAL_FOCUS_SINCE_UTC = "2026-11-20T15:07:00+00:00"
ROYAL_FOCUS_SPORTS = ("NBA", "NFL", "NHL", "NCAAF")


def royal_focus_stats():
    """Read-only split: never alter historical grades, trades, or tickets."""
    with db() as c:
        where = "sport IN (?,?,?,?) AND created_at >= ?"
        args = (*ROYAL_FOCUS_SPORTS, ROYAL_FOCUS_SINCE_UTC)
        picks = c.execute(f"""SELECT COUNT(*) n,
            COALESCE(SUM(result='WIN'),0) w,
            COALESCE(SUM(result='LOSS'),0) l,
            COALESCE(SUM(result='PENDING'),0) pending
            FROM royal_predictions_v34 WHERE {where}""", args).fetchone()
        paper = c.execute("""SELECT COUNT(*) n,
            COALESCE(SUM(CASE WHEN t.status IN ('WIN','LOSS') THEN t.pnl ELSE 0 END),0) pnl
            FROM royal_paper_v34 t JOIN royal_predictions_v34 p ON p.id=t.prediction_id
            WHERE p.sport IN (?,?,?,?) AND p.created_at >= ?""", args).fetchone()
        parlays = c.execute("""SELECT COUNT(*) n FROM royal_parlays_v37
            WHERE created_at >= ? AND session_key LIKE ?""",
            (ROYAL_FOCUS_SINCE_UTC, "%ROYAL-FOCUS-NBA-NFL-NHL-NCAAF%" )).fetchone()
        all_picks = c.execute("SELECT COUNT(*) FROM royal_predictions_v34").fetchone()[0]
    return dict(picks), dict(paper), parlays['n'], all_picks


def royal_results_dashboard():
    """Always-visible results for both current Focus and historical picks."""
    focus, papers, focus_parlays, total_saved = royal_focus_stats()
    with db() as c:
        total = dict(c.execute("""SELECT COUNT(*) n,
            COALESCE(SUM(result='WIN'),0) w,
            COALESCE(SUM(result='LOSS'),0) l,
            COALESCE(SUM(result='PENDING'),0) pending
            FROM royal_predictions_v34""").fetchone())
        paper = dict(c.execute("""SELECT COUNT(*) n,
            COALESCE(SUM(status='WIN'),0) w,COALESCE(SUM(status='LOSS'),0) l,
            COALESCE(SUM(CASE WHEN status IN ('WIN','LOSS') THEN pnl ELSE 0 END),0) pnl
            FROM royal_paper_v34""").fetchone())
        recent = [dict(r) for r in c.execute("""SELECT sport,title,selection,phase,result,created_at,graded_at,grade_note
                   FROM royal_predictions_v34 ORDER BY
                   CASE WHEN result IN ('WIN','LOSS') THEN 0 ELSE 1 END,
                   COALESCE(graded_at,created_at) DESC LIMIT 40""")]
    st.subheader("📊 Queen's Results — All Time")
    a,b,c,d = st.columns(4)
    a.metric("Saved picks",total['n'])
    b.metric("Graded W / L",f"{total['w']} / {total['l']}")
    c.metric("Not graded",total['pending'])
    d.metric("Paper P&L*",f"${paper['pnl']:+,.2f}")
    accuracy = f"{total['w']/(total['w']+total['l']):.1%}" if total['w']+total['l'] else "—"
    st.caption(f"Graded accuracy: {accuracy} (based on {total['w']+total['l']} predictions ONLY) · Paper: {paper['n']} entries, {paper['w']} wins, {paper['l']} losses")
    st.caption("*Simulation only. Grading based on ESPN is provisional; exchange settlement and executable fills are NOT verified.")
    st.markdown("**👑 Royal Focus — Newer Test**")
    x,y,z=st.columns(3)
    x.metric("Focus picks",focus['n'])
    y.metric("Focus W / L",f"{focus['w']} / {focus['l']}")
    z.metric("Focus pending",focus['pending'])
    st.caption(f"NBA · NFL · NHL · NCAAF (NCAA Football), starting Nov 20 at 9:07 AM CT. {focus_parlays} research parlays, {papers['n']} focus paper entries. This is a subset of all-time history, not a reset.")
    # Tennis is a historical comparison only. Never include it in ACTIVE_SPORTS,
    # market scanning, new prediction locks, paper trades, or parlay generation.
    with db() as c:
        tennis=dict(c.execute("""SELECT COUNT(*) n,
            COALESCE(SUM(result='WIN'),0) w, COALESCE(SUM(result='LOSS'),0) l,
            COALESCE(SUM(result='PENDING'),0) pending
            FROM royal_predictions_v34 WHERE sport='Tennis'""").fetchone())
    st.markdown("**🎾 Tennis Watch — Historical Only**")
    ta,tb,tc=st.columns(3)
    ta.metric("Tennis W / L",f"{tennis['w']} / {tennis['l']}")
    tb.metric("Not graded",tennis['pending'])
    tc.metric("Graded sample",tennis['w']+tennis['l'])
    st.caption("View-only comparison. No new tennis predictions, locks, paper entries, or parlays. Grades provisional; small samples do not prove an edge.")
    st.markdown("**🏁 Latest Results & Saved Picks**")
    if recent:
        view=st.segmented_control("Show",["Results","Pending","All"],default="Results",key="royal_results_quick_filter")
        displayed=[r for r in recent if (view=="All" or (r['result'] in ('WIN','LOSS') if view=="Results" else r['result']=='PENDING'))]
        if not displayed:
            st.info("No picks have been graded in this group yet. Saved pending predictions remain in History.")
        else:
            for r in displayed[:15]:
                status = "✅ WIN" if r['result']=='WIN' else "❌ LOSS" if r['result']=='LOSS' else "⏳ PENDING"
                st.markdown(f"{status} · **{r['sport']} — {r['selection']}** · {r['title'] or ''}")
                st.caption(f"Locked {local(r['created_at'])}"+(f" · Graded {local(r['graded_at'])}" if r['graded_at'] else ""))
    else:
        st.info("No saved picks found in this database. Check the storage connection and restore a backup if needed.")


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
    c.metric("$100 paper entries",ptotals[0]);d.metric("Provisional paper P&L",f"${ptotals[3]:+,.2f}")
    st.caption(f"Predictions: {wins} won / {losses} lost · Paper trades: {pw} won / {pl} lost · Results are provisional until exchange settlement is checked.")
    filter_choice=st.radio("Show history",["All Picks","Paper Trades","Results"],horizontal=True,key="royal_history_filter")
    paper_by_prediction={r["prediction_id"]:r for r in papers}
    if filter_choice=="Paper Trades":
        filtered=[r for r in rows if r["id"] in paper_by_prediction]
    elif filter_choice=="Results":
        filtered=[r for r in rows if r["result"] in ("WIN","LOSS","VOID","PUSH")]
    else:
        filtered=rows
    st.caption(f"Showing {min(len(filtered),st.session_state.get('royal_history_limit',6))} of {len(filtered)} saved picks · Newest first")
    limit=st.session_state.get("royal_history_limit",6)
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

def royal_status_label(game, result="PENDING"):
    """Game state is distinct from prediction outcome."""
    state=(game or {}).get("state")
    outcome=str(result or "PENDING").upper()
    game_status=("LIVE" if state=="in" else "FINAL" if state=="post" else
                 "UPCOMING" if state=="pre" else "UNVERIFIED")
    prediction_status=outcome if outcome in ("WIN","LOSS") else "PENDING"
    return game_status,prediction_status


def royal_scoreboard(game, sport, selected=None, result="PENDING", start=None, compact=False):
    """ESPN-inspired compact board; one score immediately beside its own team."""
    if not game or len(game.get("teams") or [])!=2:
        return False
    names=game["teams"]
    raw=game.get("scores") or {}
    game_status,pick_status=royal_status_label(game,result)
    details=str(game.get("detail") or "").strip()
    if game_status=="LIVE":
        game_label="LIVE"+(" · "+details if details else "")
    elif game_status=="FINAL":
        game_label="FINAL"+(" · "+details if details.lower() not in ("", "final") else "")
    elif game_status=="UPCOMING":
        game_label=local(game.get("date") or start)
    else:
        game_label="TIME UNVERIFIED"
    game_color={"LIVE":"#fb7185","FINAL":"#c4b5fd","UPCOMING":"#94a3b8","UNVERIFIED":"#fbbf24"}[game_status]
    pick_color={"WIN":"#86efac","LOSS":"#fca5a5","PENDING":"#fbbf24"}.get(pick_status,"#fbbf24")
    line_rows=[]
    for team in names:
        chosen=bool(selected and _team_name_match(selected,team)>=.90)
        crown='<span style="margin-left:6px;color:#d8b4fe" aria-label="Queen pick" title="Queen pick">👑</span>' if chosen else ''
        val=raw.get(team)
        score=html.escape(str(val)) if val is not None and str(val).strip()!='' and game_status in ('LIVE','FINAL') else '—'
        team_safe=html.escape(str(team))
        name_color='#efdcff' if chosen else '#e5e7eb'
        line_rows.append(f'<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:5px 0"><span style="font-size:clamp(13px,2.6vw,17px);font-weight:{"800" if chosen else "600"};color:{name_color};min-width:0">{team_safe}{crown}</span><span style="font-size:clamp(21px,4vw,29px);font-weight:850;font-variant-numeric:tabular-nums;color:{name_color};text-align:right;min-width:36px">{score}</span></div>')
    note=''
    if selected:
        if game_status=='LIVE':
            scores=[numeric(raw.get(n)) for n in names]
            if scores[0] is not None and scores[1] is not None:
                picked=next((i for i,n in enumerate(names) if _team_name_match(selected,n)>=.90),None)
                if picked is not None:
                    delta=scores[picked]-scores[1-picked]
                    note=('LEADING BY '+str(int(delta)) if delta>0 else 'TRAILING BY '+str(int(-delta)) if delta<0 else 'TIED')
        if not note:
            note='QUEEN PICK MARKED 👑'
    footer=(f'<span style="color:{pick_color};font-size:11px;font-weight:750">PICK {html.escape(pick_status)}</span>' if selected else '<span style="color:#9ca3af;font-size:11px">WATCHING</span>')
    if note:
        footer+=f'<span style="color:#c4b5d1;font-size:11px">{html.escape(note)}</span>'
    padding='9px 12px' if compact else '13px 16px'
    st.markdown(f'<div style="background:#1b1326;border:1px solid #624076;border-radius:12px;padding:{padding};margin:4px 0 8px">'
      f'<div style="display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:5px"><span style="font-size:11px;font-weight:700;color:#c4b5d1">{html.escape("NCAAF · NCAA Football" if sport=="NCAAF" else str(sport))}</span><span style="font-size:11px;font-weight:800;color:{game_color}">{html.escape(game_label)}</span></div>'
      +''.join(line_rows)
      +f'<div style="border-top:1px solid #463653;padding-top:7px;margin-top:5px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:5px">{footer}</div></div>',unsafe_allow_html=True)
    return True


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
        action = ("WATCH" if locked.get("model")=="Market-favorite baseline" and locked["result"]=="PENDING" else "PENDING" if locked["result"]=="PENDING" else locked["result"])
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
        result=locked.get("result","PENDING") if locked else "PENDING"
        shown=royal_scoreboard(game,sport,locked.get("selection") if locked else None,result,x.get("start"),compact=True)
        if not shown:
            label=local(x.get('start'))
            st.markdown(f"**{icon} {title}** · 📅 {label}")
            if locked: st.caption(f"👑 {locked.get('selection','')} · ⏳ PICK {result}")
        if number is not None:
            st.caption(f"Top {number} · {html.escape(str(x.get('market_type','Market')))}")
        # Never confuse a YES-contract quote with the price of Queen's selected team.
        if locked:
            st.caption(f"Model: {html.escape(str(locked.get('model') or 'Unspecified'))} · {html.escape(str(locked.get('confidence') or 'Uncalibrated'))}")
        if valid:
            quoted_side=(str(x.get('selection')) if verified else 'unverified YES outcome')
            st.caption(f"Contract YES ({html.escape(quoted_side)}): ask {q['ask']*100:.0f}¢ · bid {q['bid']*100:.0f}¢")
        else:
            st.caption('Contract quote unavailable · WATCH ONLY')
        if locked and locked.get('result')=='PENDING':
            st.caption('Forecast tracked · not an instruction to buy · paper test only')
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
    c.metric("Losses", totals[2] or 0); d.metric("Provisional paper P&L", f"${totals[3]:,.2f}")
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
st.caption("V5.2 · ROYAL FOCUS: NBA • NFL • NHL • NCAAF · POLYMARKET US · PAPER ONLY · $20 BASE UNIT")

restore_bundled_royal_backup()
# Simple primary navigation: every major page is one tap away, no nested archive menus.
tab_results, tab_live, tab_history = st.tabs(["🏁 RESULTS", "🔴 LIVE & TOP 10", "📚 HISTORY & SETTINGS"])
with tab_results:
    if st.button("🔄 Check results now", key="refresh_results_now", use_container_width=True):
        scores.clear()
        st.rerun()
    st.caption("Automatic saved-result checks run while the app is awake. Game scores and exchange settlements are separate feeds.")
    st.caption("Recovery snapshot: 419 saved picks, 4 paper entries, 20 parlay ticket headers. Parlay leg details were not included in the uploaded backups.")

@st.fragment(run_every="180s")
def royal_auto_results():
    try:
        changed, parlay_changes, note = refresh_saved_results()
        st.caption(f"Latest results check {utc().astimezone(CT).strftime('%I:%M %p CT')} · {changed} newly graded picks · {parlay_changes} parlay updates")
    except Exception as exc:
        st.warning(f"Results source unavailable ({type(exc).__name__}). Existing results preserved.")
    royal_results_dashboard()

with tab_results:
    royal_auto_results()

with tab_history:
    st.subheader("📚 Full Saved History")
    st.caption("All sports and older experiments are here. Backup files preserve original entry prices and pick timestamps.")
    prediction_panel()
    st.divider()
    royal_unit_manager()
    with db() as _backup_db:
        _parlay_tickets=[dict(r) for r in _backup_db.execute("SELECT * FROM royal_parlays_v37")]
        _parlay_legs=[dict(r) for r in _backup_db.execute("SELECT * FROM royal_parlay_legs_v37")]
    st.markdown("**💾 Parlay backups**")
    st.download_button("⬇️ Parlay tickets CSV", _csv_bytes(_parlay_tickets), "royal_parlays_v37_backup.csv", "text/csv", disabled=not _parlay_tickets, use_container_width=True)
    st.download_button("⬇️ Parlay legs CSV", _csv_bytes(_parlay_legs), "royal_parlay_legs_v37_backup.csv", "text/csv", disabled=not _parlay_legs, use_container_width=True)

with tab_live:
    st.subheader("🔴 Live Sports & Queen's Picks")
    if st.button("👑 Refresh scores & prices", type="primary", use_container_width=True):
        api.clear()
        scores.clear()
        st.rerun()
    st.caption("ESPN score updates as available; Polymarket US quote scans every ~2 minutes while awake. Streaming prices are not guaranteed.")

# Automatic discovery covers live games and the next three calendar days.
# One stable daily session prevents duplicate parlays from changing filters.
max_quotes = 120
ACTIVE_SPORTS = ("NBA", "NFL", "NHL", "NCAAF")
window = "Next 3 Days"

@st.fragment(run_every="120s")
def royal_auto_scan():
    # Only these sports are eligible for new market scans and parlays.
    # Historical records from all leagues remain available for grading and export.
    selected = list(ACTIVE_SPORTS)
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
    matched_games = len({x['event_id'] for x in rows if x.get('score_game')})
    st.caption(f"Market scan {utc().astimezone(CT).strftime('%I:%M %p CT')} · {len(rows)} markets · {quoted} valid quotes · {matched_games} ESPN-matched events · {live_count} live events")
    if not matched_games and rows:
        st.warning("Sports events are arriving, but none were confidently linked to an ESPN score. Live clocks and automatic grading may remain unavailable until matched.")
    # Keep historical V3.3 paper tests intact; new pick-linked paper tests live in V3.4 tables.




    st.subheader("📡 Live Scoreboard")
    live_rows=[x for x in checked if x.get('score_game') and x['score_game'].get('state')=='in']
    unique_live={}
    for candidate in live_rows:
        unique_live.setdefault(candidate['event_id'],candidate)
    if unique_live:
        st.caption("Live scores from ESPN · scores are not final grades")
        # Compact board with score alongside each team; top picks below contain the detailed quotes.
        live_list=list(unique_live.values())[:12]
        for offset in range(0,len(live_list),2):
            cols=st.columns(2,gap="small")
            for col,candidate in zip(cols,live_list[offset:offset+2]):
                with col:
                    game=candidate['score_game']
                    saved=next((p for p in st.session_state.get('royal_locked',{}).values()
                                if p.get('event_id')==candidate['event_id'] and p.get('sport')==candidate['sport']),None)
                    royal_scoreboard(game,candidate['sport'],saved.get('selection') if saved else None,
                                     saved.get('result','PENDING') if saved else 'PENDING',candidate.get('start'),compact=True)
    else:
        st.info("No confidently matched live games right now. Results monitoring continues separately.")

    st.subheader("👑 Queen’s Top 10 Picks")
    st.caption("NBA · NFL · NHL · NCAAF (NCAA Football) | Up to ten independent record-based candidates, with cross-sport representation when available.")
    watch = [x for x in rows if (x["event_id"],x["slug"],"LIVE" if x.get("live") else "PREGAME") in st.session_state.get("royal_locked",{})]

    def evidence_rank(x):
        locked = st.session_state["royal_locked"].get((x["event_id"],x["slug"],"LIVE" if x.get("live") else "PREGAME"), {})
        model = locked.get("model", "")
        tier = 3 if model == "Season-record baseline" else 2 if model == "Market-favorite baseline" else 1
        verified = bool(x.get("verified_side"))
        valid = bool(x.get("quote") and x["quote"].get("valid"))
        return (-tier, -int(bool(x.get("score_game"))), -int(verified and valid), parse_date(x.get("start")) or datetime.max.replace(tzinfo=timezone.utc))

    watch.sort(key=evidence_rank)
    eligible=[]
    used=set()
    for x in watch:
        if x["event_id"] in used: continue
        locked=st.session_state["royal_locked"].get((x["event_id"],x["slug"],"LIVE" if x.get("live") else "PREGAME"),{})
        if locked.get("model")!="Season-record baseline": continue
        eligible.append(x);used.add(x["event_id"])
    featured=[]; selected_events=set()
    # Reserve up to two positions per sport only where evidence exists.
    for sport in ACTIVE_SPORTS:
        for x in [r for r in eligible if r["sport"]==sport][:2]:
            if len(featured)<10:
                featured.append(x);selected_events.add(x["event_id"])
    for x in eligible:
        if len(featured)>=10:break
        if x["event_id"] not in selected_events:
            featured.append(x);selected_events.add(x["event_id"])

    # Show which leagues supplied eligible games, so missing sports are explainable.
    league_counts={league:{"events":len({x["event_id"] for x in rows if x["sport"]==league}),
                           "record":len({x["event_id"] for x in watch if x["sport"]==league and
                             st.session_state["royal_locked"].get((x["event_id"],x["slug"],"LIVE" if x.get("live") else "PREGAME"),{}).get("model")=="Season-record baseline"}),
                           "quoted":sum(1 for x in checked if x["sport"]==league and x.get("quote") and x["quote"].get("valid"))}
                   for league in ACTIVE_SPORTS}
    with st.expander("🔎 Feed health & league coverage",expanded=False):
        for league,counts in league_counts.items():
            st.caption(f"{league}: {counts['events']} events in window · {counts['record']} independent record-baseline games · {counts['quoted']} valid market quotes")
        if errors: st.caption("Feed errors: "+" | ".join(errors))
        st.caption("No qualifying games means WATCH, not an invented selection. Season records are uncalibrated.")

    if featured:
        for offset in range(0,len(featured),2):
            col1,col2=st.columns(2,gap="small")
            with col1: display_row(featured[offset],offset+1)
            if offset+1<len(featured):
                with col2: display_row(featured[offset+1],offset+2)
    else:
        st.info("No season-record-based candidates found in this scan. Queen says WAIT rather than copy market favorites.")

    with st.expander("ℹ️ How Queen ranks picks", expanded=False):
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
        pool=[]
        used=set()
        for x in sorted(watch,key=evidence_rank):
            phase="LIVE" if x.get("live") else "PREGAME"
            r=st.session_state["royal_locked"].get((x["event_id"],x["slug"],phase))
            if not r or r.get("result")!="PENDING" or r.get("model")!="Season-record baseline":
                continue
            if x["event_id"] in used: continue
            used.add(x["event_id"])
            pool.append(dict(r))
        # Interleave sports: basketball cannot monopolize all parlay options.
        by_sport={sport:[] for sport in ACTIVE_SPORTS}
        for r in pool: by_sport[r["sport"]].append(r)
        ordered=[]
        while any(by_sport.values()):
            for sport in ACTIVE_SPORTS:
                if by_sport[sport]: ordered.append(by_sport[sport].pop(0))
        return ordered

    def royal_lock_parlays(candidates,session_key):
        """Lock distinct combinations, preserving previous tickets and their legs."""
        created=[]
        used_across=set()
        with db() as c:
            for n in (2,3,5,10):
                pid=hashlib.sha256(("royal-parlay-v49|"+session_key+"|"+str(n)).encode()).hexdigest()[:32]
                if c.execute("SELECT 1 FROM royal_parlays_v37 WHERE id=?",(pid,)).fetchone():
                    continue
                # Prefer new games in each ticket, then allow limited overlap if necessary.
                fresh=[r for r in candidates if r["event_id"] not in used_across]
                repeat=[r for r in candidates if r["event_id"] in used_across]
                chosen=(fresh+repeat)[:n]
                if len(chosen)<n: continue
                before=c.total_changes
                c.execute("INSERT OR IGNORE INTO royal_parlays_v37 (id,session_key,leg_count,created_at,note) VALUES (?,?,?,?,?)",
                    (pid,session_key,n,utc().isoformat(),
                     "V4.9 diversified research-only combination; no verified parlay price or betting edge."))
                if c.total_changes==before: continue
                for i,r in enumerate(chosen,1):
                    c.execute("""INSERT OR IGNORE INTO royal_parlay_legs_v37
                        (parlay_id,leg_number,prediction_id,event_id,sport,title,selection,model)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (pid,i,r["id"],r["event_id"],r["sport"],r["title"],r["selection"],r["model"]))
                used_across.update(r["event_id"] for r in chosen)
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
    royal_session=f"{current.isoformat()}|{window}|ROYAL-FOCUS-NBA-NFL-NHL-NCAAF-V49"
    royal_candidates=royal_parlay_candidates()
    royal_new=royal_lock_parlays(royal_candidates,royal_session)
    royal_grade_parlays()
    royal_tickets,royal_legs=royal_parlay_history()

    st.subheader("🔥 Queen's Underdog Sniper")
    dogs=[x for x in checked if x.get("verified_side") and x.get("quote") and x["quote"].get("valid") and x["quote"]["ask"]<.5]
    dogs=sorted(dogs,key=lambda x:(x["sport"],x["quote"]["ask"]))
    # Select across leagues first, then fill remaining slots.
    dog_choices=[]; used_events=set()
    for sport in ACTIVE_SPORTS:
        for x in dogs:
            if x["sport"]==sport and x["event_id"] not in used_events:
                dog_choices.append(x);used_events.add(x["event_id"]);break
    for x in dogs:
        if len(dog_choices)>=5:break
        if x["event_id"] not in used_events:
            dog_choices.append(x);used_events.add(x["event_id"])
    if dogs:
        for x in dog_choices[:5]:
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
        st.caption("No verified underdog quotes available right now. Queen will not invent candidates.")
    st.caption("Underdog prices alone do not establish value. Paper testing only.")

    st.subheader("🎯 Queen’s Royal Parlays")
    with st.container():
        st.markdown("**👑 Automatic Royal Parlay Builder**")
        st.caption("Queen searches beyond the Featured Top 10 and spreads tickets across sports when qualifying games exist. These are uncalibrated research combinations, not verified value bets.")
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
                    mark="✅" if result=="WIN" else "❌" if result=="LOSS" else "📡" if game and game.get("state")=="in" else "⏳"
                    timing=royal_game_clock(game, matched.get('start') if matched else (saved_leg.get('created_at') if saved_leg else None)) if game else '🕒 Scoreboard not matched · time unverified' 
                    st.markdown(f"{mark} **{icon} {leg['selection']}** · {timing}")
                    score_line=royal_score_line(game,leg["selection"])
                    if score_line: st.caption(score_line)
            st.caption("Checks update from matched ESPN final scores. Live games show 🔴 until graded; locked picks stay unchanged.")
        else:
            st.info(f"Only {len(royal_candidates)} independently assessed games in this scan; {selected_legs} different games are required. Queen won't invent extra legs.")
        with st.expander("ℹ️ Parlay limitations",expanded=False):
            st.caption("Locked prediction combinations are not purchasable Polymarket US parlays. No verified combined odds, payout or paper P&L. Season-record estimates are uncalibrated and market-favorite baselines are not independent edges.")
    with st.expander("ℹ️ Paper testing & data storage",expanded=False):
        st.caption("No real orders. Paper results do not establish profitability. Local SQLite on Streamlit Community Cloud may be lost on restart/redeploy; export backups or use persistent storage.")

with tab_live:
    royal_auto_scan()
