import os, io, math, json, time, zipfile, statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

BASE='https://data.binance.vision/data'
CACHE=Path('research/super_model_cache')
OUT=Path('research/super_model_output')
CACHE.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
SYMBOLS=['ETHUSDT','BTCUSDT']
YEARS=list(range(2020,2026))
MODES=['ORIGINAL',14,30,60]
DAY=86400000; BAR=7200000; H=12
LEV=10; TRANCHE_MARGIN=10.0; TRANCHE_NOTIONAL=100.0; MMR=.005; TAKER=.0005; MAKER=.0002
GRID_STEPS=10; GRID_GAP=.01; PROFIT_LOCK_ROE=.10

session=requests.Session(); session.headers.update({'User-Agent':'Mozilla/5.0 super-model-research'})

def norm_ts(x):
    x=int(float(x))
    return x//1000 if x>10**14 else x

def dl(url, dest, retries=4):
    dest=Path(dest)
    if dest.exists() and dest.stat().st_size>100: return dest
    dest.parent.mkdir(parents=True,exist_ok=True)
    err=None
    for k in range(retries):
        try:
            r=session.get(url,timeout=60)
            if r.status_code==404: return None
            r.raise_for_status(); dest.write_bytes(r.content); return dest
        except Exception as e:
            err=e; time.sleep(1.5*(k+1))
    print('DOWNLOAD_FAIL',url,repr(err),flush=True); return None

def kline_url(market,symbol,interval,year,month,kind='klines'):
    if market=='spot': return f'{BASE}/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}.zip'
    if kind=='klines': return f'{BASE}/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}.zip'
    if kind=='premium': return f'{BASE}/futures/um/monthly/premiumIndexKlines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}.zip'
    raise ValueError(kind)

def funding_urls(symbol,year,month):
    return [
      f'{BASE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{year}-{month:02d}.zip',
      f'{BASE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{year}-{month:02d}.csv.zip',
    ]

def csv_from_zip(path, header=None):
    if path is None:return pd.DataFrame()
    with zipfile.ZipFile(path) as z:
        names=[n for n in z.namelist() if n.lower().endswith('.csv')]
        if not names:return pd.DataFrame()
        data=z.read(names[0])
    return pd.read_csv(io.BytesIO(data),header=header)

def load_kline_month(market,symbol,interval,year,month,kind='klines'):
    tag=f'{market}_{kind}_{symbol}_{interval}_{year}_{month:02d}.zip'
    p=dl(kline_url(market,symbol,interval,year,month,kind), CACHE/tag)
    if p is None:return pd.DataFrame(columns=['ts','o','h','l','c','qv','tbq','closeTs'])
    df=csv_from_zip(p,header=None)
    if df.empty:return df
    if str(df.iloc[0,0]).lower() in ('open_time','opentime'):
        df=df.iloc[1:].reset_index(drop=True)
    if df.shape[1]<11: raise RuntimeError(f'bad kline {p} {df.shape}')
    out=pd.DataFrame({
      'ts':df.iloc[:,0].map(norm_ts), 'o':pd.to_numeric(df.iloc[:,1]), 'h':pd.to_numeric(df.iloc[:,2]),
      'l':pd.to_numeric(df.iloc[:,3]), 'c':pd.to_numeric(df.iloc[:,4]),
      'closeTs':df.iloc[:,6].map(norm_ts), 'qv':pd.to_numeric(df.iloc[:,7]), 'tbq':pd.to_numeric(df.iloc[:,10])
    })
    return out

def load_funding_month(symbol,year,month):
    p=None
    for ix,u in enumerate(funding_urls(symbol,year,month)):
        p=dl(u,CACHE/f'fund_{ix}_{symbol}_{year}_{month:02d}.zip')
        if p:break
    if not p:return pd.DataFrame(columns=['t','r'])
    try:
        with zipfile.ZipFile(p) as z:
            n=[x for x in z.namelist() if x.lower().endswith('.csv')][0]; raw=z.read(n)
        d=pd.read_csv(io.BytesIO(raw))
        cols={str(c).lower():c for c in d.columns}
        tc=next((c for k,c in cols.items() if 'funding' in k and 'time' in k),None) or next((c for k,c in cols.items() if 'calc_time' in k),None)
        rc=next((c for k,c in cols.items() if 'funding' in k and 'rate' in k),None) or next((c for k,c in cols.items() if 'last_funding_rate' in k),None)
        if tc is not None and rc is not None:
            return pd.DataFrame({'t':d[tc].map(norm_ts),'r':pd.to_numeric(d[rc])})
    except Exception: pass
    d=csv_from_zip(p,header=None)
    if d.empty:return pd.DataFrame(columns=['t','r'])
    arr=d.copy(); best_t=None
    for c in arr.columns:
        x=pd.to_numeric(arr[c],errors='coerce')
        med=x.dropna().abs().median() if x.notna().any() else 0
        if med>1e11: best_t=c; break
    ratec=None
    for c in arr.columns:
        if c==best_t:continue
        x=pd.to_numeric(arr[c],errors='coerce')
        if x.notna().mean()>.8 and x.dropna().abs().quantile(.95)<.1:
            ratec=c
    if best_t is None or ratec is None:return pd.DataFrame(columns=['t','r'])
    return pd.DataFrame({'t':arr[best_t].map(norm_ts),'r':pd.to_numeric(arr[ratec],errors='coerce')}).dropna()

def load_year(symbol,year):
    print('LOAD',symbol,year,flush=True)
    jobs=[]
    with ThreadPoolExecutor(max_workers=16) as ex:
        for m in range(1,13):
            jobs.append(('fut2h',m,ex.submit(load_kline_month,'um',symbol,'2h',year,m,'klines')))
            jobs.append(('spot2h',m,ex.submit(load_kline_month,'spot',symbol,'2h',year,m,'klines')))
            jobs.append(('prem2h',m,ex.submit(load_kline_month,'um',symbol,'2h',year,m,'premium')))
            jobs.append(('fund',m,ex.submit(load_funding_month,symbol,year,m)))
            if m>=7: jobs.append(('fut5m',m,ex.submit(load_kline_month,'um',symbol,'5m',year,m,'klines')))
        parts={k:[] for k in ['fut2h','spot2h','prem2h','fund','fut5m']}
        for k,m,f in jobs:
            try: parts[k].append(f.result())
            except Exception as e: print('PART_FAIL',symbol,year,k,m,repr(e),flush=True)
    def cat(k):
        xs=[x for x in parts[k] if x is not None and not x.empty]
        return pd.concat(xs,ignore_index=True).sort_values(xs[0].columns[0]).drop_duplicates(xs[0].columns[0]) if xs else pd.DataFrame()
    fut,spot,prem,fund,m5=map(cat,['fut2h','spot2h','prem2h','fund','fut5m'])
    if fut.empty or spot.empty: raise RuntimeError(f'missing core data {symbol} {year}: fut {len(fut)} spot {len(spot)}')
    sm=spot.set_index('ts'); pm=prem.set_index('ts') if not prem.empty else None
    fund=fund.sort_values('t') if not fund.empty else pd.DataFrame(columns=['t','r'])
    f_t=fund['t'].to_numpy(dtype=np.int64) if not fund.empty else np.array([],dtype=np.int64)
    f_r=fund['r'].to_numpy(dtype=float) if not fund.empty else np.array([],dtype=float)
    rows=[]
    for z in fut.itertuples(index=False):
        ts=int(z.ts); closeTs=int(z.closeTs); qv=float(z.qv); pdlt=2*float(z.tbq)-qv
        if ts in sm.index:
            ss=sm.loc[ts]; sq=float(ss.qv); sdlt=2*float(ss.tbq)-sq
        else: sq=sdlt=None
        pr=float(pm.loc[ts].c) if pm is not None and ts in pm.index else None
        if len(f_t):
            lo72=np.searchsorted(f_t,closeTs-72*3600000,side='left'); hi=np.searchsorted(f_t,closeTs,side='right')
            lo24=np.searchsorted(f_t,closeTs-24*3600000,side='left')
            f24=float(f_r[lo24:hi].sum()); f72=float(f_r[lo72:hi].sum())
            loe=np.searchsorted(f_t,ts,side='right'); events=[(int(f_t[j]),float(f_r[j])) for j in range(loe,hi)]
        else: f24=f72=0.; events=[]
        rows.append(dict(ts=ts,o=float(z.o),h=float(z.h),l=float(z.l),c=float(z.c),closeTs=closeTs,qv=qv,perpDelta=pdlt,
                         spotQv=sq,spotDelta=sdlt,premium=pr,funding24=f24,funding72=f72,fundingEvents=events))
    m5map={}
    if not m5.empty:
        for z in m5.itertuples(index=False):
            bucket=(int(z.ts)//BAR)*BAR
            m5map.setdefault(bucket,[]).append(dict(ts=int(z.ts),o=float(z.o),h=float(z.h),l=float(z.l),c=float(z.c)))
    print('DATA',symbol,year,'2h',len(rows),'5m',sum(map(len,m5map.values())),'fund',len(fund),'prem',len(prem),flush=True)
    return rows,m5map

def clip(x,a,b):return max(a,min(b,x))
def sigmoid(z):return 1/(1+math.exp(-clip(z,-30,30)))
def safe_flow(a,i,n,kd,kq):
    d=q=0.; c=0
    for j in range(i-n+1,i+1):
        if j<0:continue
        D=a[j].get(kd); Q=a[j].get(kq)
        if D is not None and Q:
            d+=D;q+=Q;c+=1
    return d/q if c>=math.ceil(n*.7) and q else None

def rv(a,i,n):
    if i<n:return None
    return math.sqrt(sum(math.log(a[j]['c']/a[j-1]['c'])**2 for j in range(i-n+1,i+1)))
def efficiency(a,i,n):
    if i<n:return None
    den=sum(abs(math.log(a[j]['c']/a[j-1]['c'])) for j in range(i-n+1,i+1))
    return abs(math.log(a[i]['c']/a[i-n]['c']))/den if den else 0
def prem_avg(a,i,n):
    z=[a[j].get('premium') for j in range(max(0,i-n+1),i+1) if a[j].get('premium') is not None]
    return sum(z)/len(z) if z else None

def feat(a,i):
    if i<36:return None
    r=a[i]; ret=lambda n:math.log(r['c']/a[i-n]['c'])
    pf=lambda n:safe_flow(a,i,n,'perpDelta','qv'); sf=lambda n:safe_flow(a,i,n,'spotDelta','spotQv')
    vals=[pf(3),pf(6),pf(12),pf(36),sf(3),sf(6),sf(12),sf(36)]
    if any(x is None for x in vals):return None
    p6,p12,p24,p72,s6,s12,s24,s72=vals
    vol24,vol72=rv(a,i,12),rv(a,i,36); eff24,eff72=efficiency(a,i,12),efficiency(a,i,36)
    pa,pa72=prem_avg(a,i,12),prem_avg(a,i,36)
    q24=sum(a[j]['qv'] for j in range(i-11,i+1)); q7=sum(a[j]['qv'] for j in range(max(0,i-83),i+1))
    volimp=math.log((q24/12)/(q7/min(84,i+1))) if q7 else 0
    return np.array([
      clip(ret(3)/.025,-4,4),clip(ret(6)/.04,-4,4),clip(ret(12)/.06,-4,4),clip(ret(36)/.11,-4,4),
      clip(p6/.08,-4,4),clip(p12/.08,-4,4),clip(p24/.08,-4,4),clip(p72/.08,-4,4),
      clip(s6/.08,-4,4),clip(s12/.08,-4,4),clip(s24/.08,-4,4),clip(s72/.08,-4,4),
      clip((s12-p12)/.08,-4,4),clip((s24-p24)/.08,-4,4),clip((r.get('funding24') or 0)/.0015,-4,4),clip((r.get('funding72') or 0)/.004,-4,4),
      clip((pa or 0)/.0015,-4,4),clip((pa72 or 0)/.0015,-4,4),clip((vol24 or 0)/.05,-4,4),clip((vol72 or 0)/.09,-4,4),
      clip((eff24 or 0)-.35,-1,1)*2,clip((eff72 or 0)-.28,-1,1)*2,clip(volimp,-3,3),
      clip((ret(12)/.04)-(p24/.08)*.45,-4,4),clip((ret(12)/.04)-(s24/.08)*.55,-4,4)
    ],dtype=float)

def bucket(a,i):
    f=feat(a,i)
    if f is None:return None
    sg=lambda x:1 if x>.35 else -1 if x<-.35 else 0
    return (sg(f[2]),sg(f[6]),sg(f[10]),sg(f[14]),sg(f[16]),1 if f[20]>.1 else 0)

def finalize_state(a,replay_from):
    sm=None;st=0;held=0;opp=0
    for i,r in enumerate(a):
        p=r.get('pRaw')
        if p is not None: sm=p if sm is None else .58*p+.42*sm
        r['p24']=sm
        for n,h in [(3,6),(6,12),(12,24)]:
            if i>=n:
                r['mom'+str(h)]=math.log(r['c']/a[i-n]['c']);r['pf'+str(h)]=safe_flow(a,i,n,'perpDelta','qv');r['sf'+str(h)]=safe_flow(a,i,n,'spotDelta','spotQv')
            else:r['mom'+str(h)]=r['pf'+str(h)]=r['sf'+str(h)]=None
        if sm is None:r['state']='WARMUP';r['lock']=0;r['persist']=None;continue
        held+=1; cl=(r['pf12'] or 0)>-.015 and (r['sf12'] or 0)>-.015; cs=(r['pf12'] or 0)<.015 and (r['sf12'] or 0)<.015
        if st==0:
            if sm>=.60 and cl:st=1;held=0;opp=0
            elif sm<=.40 and cs:st=-1;held=0;opp=0
        elif st==1 and held>=6:
            if sm<=.35 and cs:
                opp+=1
                if opp>=2:st=-1;held=0;opp=0
            else:
                opp=0
                if sm<.46:st=0;held=0
        elif st==-1 and held>=6:
            if sm>=.65 and cl:
                opp+=1
                if opp>=2:st=1;held=0;opp=0
            else:
                opp=0
                if sm>.54:st=0;held=0
        r['state']='LONG' if st==1 else 'SHORT' if st==-1 else 'NEUTRAL'; r['lock']=max(0,6-held) if st else 0
        agree=total=0; dr=1 if sm>=.5 else -1
        for h in [6,12,24]:
            for k in ['mom','pf','sf']:
                v=r.get(k+str(h))
                if v is not None:total+=1;agree+=1 if np.sign(v)==dr else 0
        r['persist']=clip(30+50*abs(sm-.5)*2+(20*agree/total if total else 0),0,100)
    rs=next((i for i,r in enumerate(a) if r['ts']>=replay_from),max(0,len(a)-4380))
    return rs

def build_original(rows,replay_from):
    a=[dict(r) for r in rows];D=25;w=np.zeros(D);b=0.;seen=0;buckets={}
    for i in range(len(a)):
        j=i-H
        if j>=84:
            x=feat(a,j)
            if x is not None:
                y=1. if a[i]['c']>a[j]['c'] else 0.
                for ep in range(5):
                    p=sigmoid(b+float(w@x));err=y-p;lr=.035/math.sqrt(1+seen/250)
                    b+=lr*err;w+=lr*(err*x-.0018*w)
                seen+=1;bk=bucket(a,j)
                if bk is not None:
                    u,n=buckets.get(bk,(1.5,3));buckets[bk]=(u+y,n+1)
        x=feat(a,i)
        if x is not None and seen>450:
            pl=sigmoid(b+float(w@x));bk=bucket(a,i);bs=buckets.get(bk);pb=bs[0]/bs[1] if bs and bs[1]>=12 else .5
            struct=sigmoid(.45*x[2]+.35*x[6]+.45*x[10]-.12*x[14]-.08*x[16]+.10*x[20]);a[i]['pRaw']=.68*pl+.20*pb+.12*struct
        else:a[i]['pRaw']=None
    return a,finalize_state(a,replay_from)

def fit_window(a,end_idx,days):
    D=25;win=days*12;w=np.zeros(D);b=0.;buckets={};samples=[]
    for j in range(max(84,end_idx-win+1),end_idx+1):
        x=feat(a,j)
        if x is None or j+H>=len(a):continue
        y=1. if a[j+H]['c']>a[j]['c'] else 0.;samples.append((x,y));bk=bucket(a,j)
        if bk is not None:
            u,n=buckets.get(bk,(1.5,3));buckets[bk]=(u+y,n+1)
    if len(samples)<60:return None
    for ep in range(6):
        lr=.045/math.sqrt(1+ep*.7)
        for x,y in samples:
            p=sigmoid(b+float(w@x));err=y-p;b+=lr*err;w+=lr*(err*x-.0022*w)
    return w,b,buckets

def build_adaptive(rows,replay_from,days):
    a=[dict(r) for r in rows];mdl=None;last=-9999
    for i in range(len(a)):
        matured=i-H
        if matured>=84 and (mdl is None or i-last>=12):mdl=fit_window(a,matured,days);last=i
        x=feat(a,i)
        if x is not None and mdl is not None:
            w,b,buckets=mdl;pl=sigmoid(b+float(w@x));bk=bucket(a,i);bs=buckets.get(bk);pb=bs[0]/bs[1] if bs and bs[1]>=8 else .5
            struct=sigmoid(.45*x[2]+.35*x[6]+.45*x[10]-.12*x[14]-.08*x[16]+.10*x[20]);a[i]['pRaw']=.68*pl+.20*pb+.12*struct
        else:a[i]['pRaw']=None
    return a,finalize_state(a,replay_from)

@dataclass
class Sim:
    a:list;m5:dict;rs:int
    realized:float=0.; funding:float=0.; pos:dict|None=None; pending:list=field(default_factory=list)
    known:str|None=None; initialized:bool=False; processed_funding:set=field(default_factory=set); stats:dict=field(default_factory=lambda:dict(campaigns=0,profitStops=0,trailMoves=0,switches=0,liquidations=0))
    trades:list=field(default_factory=list); campaign_start_realized:float=0.; campaign_entry_ctx:dict|None=None; equity_curve:list=field(default_factory=list); early:dict=field(default_factory=lambda:{50:None,100:None,150:None})
    def upnl(self,p):
        if not self.pos:return 0.
        return self.pos['qty']*(p-self.pos['avg']) if self.pos['side']=='LONG' else self.pos['qty']*(self.pos['avg']-p)
    def roe(self,p):return self.upnl(p)/self.pos['margin'] if self.pos else 0.
    def liq(self):
        e=self.pos['avg'];return e*(1-1/LEV)/(1-MMR) if self.pos['side']=='LONG' else e*(1+1/LEV)/(1+MMR)
    def lock_price(self,step):
        gain=PROFIT_LOCK_ROE*step*self.pos['margin'];return self.pos['avg']+gain/self.pos['qty'] if self.pos['side']=='LONG' else self.pos['avg']-gain/self.pos['qty']
    def roe_step(self,p):
        r=self.roe(p);return math.floor((r+1e-9)/PROFIT_LOCK_ROE) if r>0 else 0
    def add_fill(self,side,p,kind,ts):
        if self.pos and self.pos['side']!=side:return False
        if self.pos and self.pos['profitStopArmed']:return False
        maker=kind.startswith('GRID') or kind=='LIMIT';self.realized-=TRANCHE_NOTIONAL*(MAKER if maker else TAKER)
        if not self.pos:self.pos=dict(side=side,qty=0.,margin=0.,cost=0.,tranches=0,avg=p,opened=ts,profitStopArmed=False,profitStopPrice=None,profitLockStep=0)
        self.pos['qty']+=TRANCHE_NOTIONAL/p;self.pos['margin']+=TRANCHE_MARGIN;self.pos['cost']+=TRANCHE_NOTIONAL;self.pos['tranches']+=1;self.pos['avg']=self.pos['cost']/self.pos['qty'];return True
    def grid(self,side,anchor,active):self.pending=[dict(side=side,price=anchor*(1-GRID_GAP*n) if side=='LONG' else anchor*(1+GRID_GAP*n),activeFrom=active,gridLevel=n) for n in range(1,GRID_STEPS+1)]
    def start(self,side,p,active,ts,i,kind='AUTO'):
        if self.pos:self.close('AUTO SWITCH CLOSE',p,ts,i)
        if self.add_fill(side,p,kind,ts):
            self.grid(side,p,active);self.stats['campaigns']+=1;self.campaign_start_realized=self.realized;self.campaign_entry_ctx=self.ctx(i);return True
        return False
    def ctx(self,i):
        r=self.a[i];return dict(i=i,ts=r['ts'],state=r.get('state'),p24=r.get('p24'),persist=r.get('persist'),c=r['c'],mom12=r.get('mom12'),pf12=r.get('pf12'),sf12=r.get('sf12'))
    def close(self,reason,p,ts,i):
        if not self.pos:return
        side=self.pos['side'];tr=self.pos['tranches'];gross=self.upnl(p);fee=self.pos['qty']*p*TAKER;self.realized+=gross-fee
        pnl=self.realized-self.campaign_start_realized
        self.trades.append(dict(reason=reason,side=side,pnl=pnl,tranches=tr,entry=self.campaign_entry_ctx,exit=self.ctx(i)|{'price':p,'ts':ts}))
        self.pos=None;self.pending=[]
    def liquidate(self,p,ts,i):
        if not self.pos:return
        side=self.pos['side'];tr=self.pos['tranches'];self.realized-=self.pos['margin'];self.stats['liquidations']+=1
        pnl=self.realized-self.campaign_start_realized
        self.trades.append(dict(reason='LIQUIDATION',side=side,pnl=pnl,tranches=tr,entry=self.campaign_entry_ctx,exit=self.ctx(i)|{'price':p,'ts':ts}))
        self.pos=None;self.pending=[]
    def set_lock(self,step):
        if not self.pos or step<=self.pos['profitLockStep']:return
        if self.pos['profitLockStep']>0:self.stats['trailMoves']+=1
        self.pos['profitLockStep']=step;self.pos['profitStopArmed']=True;self.pos['profitStopPrice']=self.lock_price(step);self.pending=[]
    def restart_profit(self,side,p,i,ts):
        if self.known==side:self.start(side,p,i,ts,i,'AUTO RESTART')
    def hit_stop(self,p,ts,i):
        if not self.pos:return
        side=self.pos['side'];self.close(f'PROFIT STOP +{self.pos["profitLockStep"]*10}%',p,ts,i);self.stats['profitStops']+=1;self.restart_profit(side,p,i,ts)
    def between(self,a,b,x):return x>=min(a,b)-1e-12 and x<=max(a,b)+1e-12
    def favorable_dir(self):return 1 if self.pos['side']=='LONG' else -1
    def adverse_dir(self):return -1 if self.pos['side']=='LONG' else 1
    def process_adverse(self,fr,to,i,ts):
        if not self.pos or np.sign(to-fr)!=self.adverse_dir():return False
        cur=fr
        for _ in range(40):
            if not self.pos:return True
            lp=self.liq();cands=[o for o in self.pending if o['activeFrom']<=i and self.between(cur,to,o['price'])]
            cands.sort(key=lambda o:o['price'],reverse=(to<cur));g=cands[0] if cands else None;gp=g['price'] if g else None;lh=self.between(cur,to,lp)
            if gp is None and not lh:break
            if gp is not None and (not lh or abs(gp-cur)<=abs(lp-cur)):
                self.pending.remove(g)
                if not self.add_fill(g['side'],g['price'],f'GRID L{g["gridLevel"]}',ts):break
                cur=g['price'];continue
            self.liquidate(lp,ts,i);return True
        return not self.pos
    def process_favorable(self,fr,to,ts):
        if not self.pos or np.sign(to-fr)!=self.favorable_dir():return
        st=self.roe_step(to)
        if st>self.pos['profitLockStep']:self.set_lock(st)
    def process_armed(self,fr,to,i,ts):
        if not self.pos or not self.pos['profitStopArmed'] or np.sign(to-fr)!=self.adverse_dir():return False
        sp=self.pos['profitStopPrice']
        if not self.between(fr,to,sp) or abs(fr-sp)<1e-10:return False
        side=self.pos['side'];self.hit_stop(sp,ts,i)
        if self.pos and self.pos['side']==side:
            if self.process_adverse(sp,to,i,ts):return True
            self.process_favorable(sp,to,ts)
        return True
    def path5(self,m,i):
        if not self.pos:return
        pts=[m['o'],m['l'],m['h'],m['c']] if m['c']>=m['o'] else [m['o'],m['h'],m['l'],m['c']]
        for fr,to in zip(pts[:-1],pts[1:]):
            if not self.pos:break
            if fr==to:continue
            if self.pos['profitStopArmed'] and self.process_armed(fr,to,i,m['ts']):continue
            if self.process_adverse(fr,to,i,m['ts']):break
            self.process_favorable(fr,to,m['ts'])
    def apply_funding(self,r,m):
        if not self.pos:return
        end=m['ts']+300000
        for t,rate in r.get('fundingEvents',[]):
            key=(self.pos['side'],t)
            if key in self.processed_funding or not (m['ts']<=t<end):continue
            pay=self.pos['qty']*m['c']*rate*(-1 if self.pos['side']=='LONG' else 1);self.realized+=pay;self.funding+=pay;self.processed_funding.add(key)
    def process_bar(self,i):
        r=self.a[i];bars=self.m5.get(r['ts'],[])
        if bars:
            for m in bars:
                if not self.pos:break
                self.path5(m,i);self.apply_funding(r,m)
        else:
            m=dict(ts=r['ts'],o=r['o'],h=r['h'],l=r['l'],c=r['c']);self.path5(m,i)
    def sync(self,i):
        r=self.a[i];sig=r['state'] if r['state'] in ('LONG','SHORT') else None
        if sig is None:
            self.pending=[];self.known=None;self.initialized=True;return
        if not self.initialized:
            self.initialized=True;self.known=sig
            if not self.pos:self.start(sig,r['c'],i+1,r['ts'],i,'AUTO FIRST')
            return
        if self.known is None:
            self.known=sig
            if not self.pos:self.start(sig,r['c'],i+1,r['ts'],i,'AFTER NEUTRAL')
            elif self.pos['side']==sig:
                if not self.pending and not self.pos['profitStopArmed']:self.grid(sig,r['c'],i+1)
            else:
                self.close(f'NEUTRAL->{sig}',r['c'],r['ts'],i);self.stats['switches']+=1;self.start(sig,r['c'],i+1,r['ts'],i,'AUTO SWITCH')
            return
        if self.known!=sig:
            old=self.known;self.known=sig;self.stats['switches']+=1
            if self.pos:self.close(f'SIGNAL {old}->{sig}',r['c'],r['ts'],i)
            else:self.pending=[]
            self.start(sig,r['c'],i+1,r['ts'],i,'AUTO SWITCH');return
        if not self.pos:self.start(sig,r['c'],i+1,r['ts'],i,'AUTO RESUME')
    def run(self):
        i=self.rs;self.sync(i)
        peak=-1e18;maxdd=0.
        for i in range(self.rs+1,len(self.a)):
            self.process_bar(i);self.sync(i)
            eq=self.realized+self.upnl(self.a[i]['c']);peak=max(peak,eq);maxdd=max(maxdd,peak-eq);self.equity_curve.append(eq)
            c=i-self.rs+1
            if c in self.early and self.early[c] is None:self.early[c]=eq
        last=self.a[-1]['c'];mtm=self.realized+self.upnl(last)
        return dict(realized=self.realized,open_pnl=self.upnl(last),mtm=mtm,max_drawdown=maxdd,funding=self.funding,**self.stats,early50=self.early[50],early100=self.early[100],early150=self.early[150],n_trades=len(self.trades))

def analyze_losses(trades,top=8):
    losses=sorted([t for t in trades if t['pnl']<0],key=lambda x:x['pnl'])[:top]
    for t in losses:
        e=t['entry'] or {}; hyp=[]; p=e.get('p24'); side=t['side']
        if p is not None and ((side=='LONG' and p<.62) or (side=='SHORT' and p>.38)):hyp.append('weak_initial_probability')
        if e.get('persist') is not None and e.get('persist')<55:hyp.append('low_persistence')
        if e.get('pf12') is not None and e.get('sf12') is not None:
            if side=='LONG' and (e['pf12']<0 or e['sf12']<0):hyp.append('flow_disagreement')
            if side=='SHORT' and (e['pf12']>0 or e['sf12']>0):hyp.append('flow_disagreement')
        if t['tranches']>=8:hyp.append('deep_grid_amplification')
        if t['reason']=='LIQUIDATION':hyp.append('trend_against_grid')
        t['hypotheses']=hyp
    return losses

def main():
    results=[]; all_losses=[]
    for symbol in SYMBOLS:
      for year in YEARS:
        rows,m5=load_year(symbol,year); replay=int(datetime(year,7,1,tzinfo=timezone.utc).timestamp()*1000)
        for mode in MODES:
            print('MODEL',symbol,year,mode,flush=True)
            if mode=='ORIGINAL':a,rs=build_original(rows,replay)
            else:a,rs=build_adaptive(rows,replay,mode)
            sim=Sim(a,m5,rs);metrics=sim.run();metrics.update(symbol=symbol,year=year,mode=str(mode))
            results.append(metrics)
            losses=analyze_losses(sim.trades)
            for tr in losses: all_losses.append(dict(symbol=symbol,year=year,mode=str(mode),**tr))
            print('RESULT',json.dumps(metrics,ensure_ascii=False),flush=True)
    rdf=pd.DataFrame(results);rdf.to_csv(OUT/'results.csv',index=False)
    (OUT/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
    (OUT/'worst_losses.json').write_text(json.dumps(all_losses,indent=2,ensure_ascii=False))
    agg=rdf.groupby('mode').agg(mtm_sum=('mtm','sum'),mtm_mean=('mtm','mean'),median=('mtm','median'),dd_mean=('max_drawdown','mean'),positive=('mtm',lambda x:int((x>0).sum())),tests=('mtm','size'),liq=('liquidations','sum')).reset_index()
    agg.to_csv(OUT/'aggregate.csv',index=False)
    lines=['# SUPER MODEL ADAPTIVE BACKTEST','',rdf.to_markdown(index=False),'','## Aggregate',agg.to_markdown(index=False),'','## Worst losses']
    for z in sorted(all_losses,key=lambda x:x['pnl'])[:30]:
        lines.append(f"- {z['symbol']} {z['year']} {z['mode']} {z['side']} {z['reason']}: ${z['pnl']:.2f}, tranches={z['tranches']}, hypotheses={','.join(z.get('hypotheses',[]))}")
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print('\nAGGREGATE\n',agg.to_string(index=False),flush=True)
    print('OUTPUT',OUT.resolve(),flush=True)

if __name__=='__main__':main()
