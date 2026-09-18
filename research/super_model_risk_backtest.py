import os, json, math
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import numpy as np
import super_model_adaptive_backtest as b

INITIAL_DEPOSIT=float(os.environ.get('INITIAL_DEPOSIT','1000'))
RISK_PCT=float(os.environ.get('RISK_PCT','0.10'))
MAX_TRANCHES=b.GRID_STEPS+1  # initial + 10 grid fills = 11
MODE_DAYS=60
WARMUP_BARS=150

# --- Dynamic account-risk sizing -------------------------------------------------
# Risk definition: max campaign margin = 10% of current account equity.
# That campaign margin is split equally across the initial fill + 10 grid fills.
# At 10x leverage, a $1,000 account starts with a $100 campaign margin budget,
# or $9.0909 margin / $90.909 notional per tranche if all 11 tranches fill.

def ensure_rm(self):
    if not hasattr(self,'_initial_deposit'):
        self._initial_deposit=INITIAL_DEPOSIT
        self._risk_pct=RISK_PCT
        self._campaign_budget=0.0
        self._campaign_tranche_margin=0.0
        self._bankrupt=False
        self._max_margin_used=0.0
        self._max_campaign_budget=0.0
        self._equity_history=[]

def account_equity(self,p):
    ensure_rm(self)
    return self._initial_deposit+self.realized+self.upnl(p)

def rm_add_fill(self,side,p,kind,ts):
    ensure_rm(self)
    if self.pos and self.pos['side']!=side:return False
    if self.pos and self.pos['profitStopArmed']:return False
    if self.pos and self.pos.get('tranches',0)>=MAX_TRANCHES:return False
    margin=self._campaign_tranche_margin
    if margin<=0:return False
    notional=margin*b.LEV
    maker=kind.startswith('GRID') or kind=='LIMIT'
    self.realized-=notional*(b.MAKER if maker else b.TAKER)
    if not self.pos:
        self.pos=dict(side=side,qty=0.,margin=0.,cost=0.,tranches=0,avg=p,opened=ts,profitStopArmed=False,profitStopPrice=None,profitLockStep=0)
    self.pos['qty']+=notional/p
    self.pos['margin']+=margin
    self.pos['cost']+=notional
    self.pos['tranches']+=1
    self.pos['avg']=self.pos['cost']/self.pos['qty']
    self._max_margin_used=max(self._max_margin_used,self.pos['margin'])
    return True

def rm_grid(self,side,anchor,active):
    used=max(0,(self.pos['tranches']-1) if self.pos and self.pos['side']==side else 0)
    rem=max(0,b.GRID_STEPS-used)
    self.pending=[dict(side=side,price=anchor*(1-b.GRID_GAP*n) if side=='LONG' else anchor*(1+b.GRID_GAP*n),activeFrom=active,gridLevel=used+n) for n in range(1,rem+1)]

def rm_start(self,side,p,active,ts,i,kind='AUTO'):
    ensure_rm(self)
    if self.pos:self.close('AUTO SWITCH CLOSE',p,ts,i)
    eq=account_equity(self,p)
    if eq<=1e-9:
        self._bankrupt=True;self.pending=[];return False
    self._campaign_budget=max(0.0,eq*self._risk_pct)
    self._campaign_tranche_margin=self._campaign_budget/MAX_TRANCHES
    self._max_campaign_budget=max(self._max_campaign_budget,self._campaign_budget)
    if self.add_fill(side,p,kind,ts):
        self.grid(side,p,active)
        self.stats['campaigns']+=1
        self.campaign_start_realized=self.realized
        self.campaign_entry_ctx=self.ctx(i)
        return True
    return False

def rm_run(self):
    ensure_rm(self)
    i=self.rs;self.sync(i)
    eq0=account_equity(self,self.a[i]['c'])
    peak=eq0;min_eq=eq0;maxdd=0.;maxdd_pct=0.
    self._equity_history=[(int(self.a[i]['ts']),eq0)]
    for i in range(self.rs+1,len(self.a)):
        self.process_bar(i);self.sync(i)
        eq=account_equity(self,self.a[i]['c'])
        if eq>peak:peak=eq
        dd=peak-eq
        maxdd=max(maxdd,dd)
        if peak>0:maxdd_pct=max(maxdd_pct,dd/peak)
        min_eq=min(min_eq,eq)
        self._equity_history.append((int(self.a[i]['ts']),eq))
        c=i-self.rs+1
        if c in self.early and self.early[c] is None:self.early[c]=eq-self._initial_deposit
    last=self.a[-1]['c']
    final_eq=account_equity(self,last)
    mtm=self.realized+self.upnl(last)
    return dict(
        realized=self.realized,open_pnl=self.upnl(last),mtm=mtm,
        initial_deposit=self._initial_deposit,final_equity=final_eq,
        net_profit=final_eq-self._initial_deposit,return_pct=(final_eq/self._initial_deposit-1)*100,
        max_drawdown=maxdd,max_drawdown_pct=maxdd_pct*100,min_equity=min_eq,
        funding=self.funding,bankrupt=self._bankrupt,max_margin_used=self._max_margin_used,
        max_campaign_budget=self._max_campaign_budget,risk_pct=self._risk_pct*100,
        tranche_count_cap=MAX_TRANCHES,**self.stats,
        early50=self.early[50],early100=self.early[100],early150=self.early[150],n_trades=len(self.trades))

b.Sim.add_fill=rm_add_fill
b.Sim.grid=rm_grid
b.Sim.start=rm_start
b.Sim.run=rm_run


def replay_ts(year):
    return int(datetime(year,7,1,tzinfo=timezone.utc).timestamp()*1000)

def add_missing_h1_5m(symbol,year,m5map):
    if year==2020:return
    with ThreadPoolExecutor(max_workers=6) as ex:
        fs=[ex.submit(b.load_kline_month,'um',symbol,'5m',year,m,'klines') for m in range(1,7)]
        for f in fs:
            d=f.result()
            if d is None or d.empty:continue
            for z in d.itertuples(index=False):
                bucket=(int(z.ts)//b.BAR)*b.BAR
                m5map.setdefault(bucket,[]).append(dict(ts=int(z.ts),o=float(z.o),h=float(z.h),l=float(z.l),c=float(z.c)))
    for k in list(m5map):m5map[k].sort(key=lambda x:x['ts'])

def daily_equity(history):
    if not history:return pd.DataFrame(columns=['date','equity'])
    df=pd.DataFrame(history,columns=['ts','equity'])
    df['date']=pd.to_datetime(df['ts'],unit='ms',utc=True).dt.date.astype(str)
    return df.groupby('date',as_index=False).tail(1)[['date','equity']].reset_index(drop=True)

def year_end_equity(history):
    if not history:return {}
    df=pd.DataFrame(history,columns=['ts','equity'])
    df['year']=pd.to_datetime(df['ts'],unit='ms',utc=True).dt.year
    return {str(int(y)):float(g.iloc[-1].equity) for y,g in df.groupby('year')}

def run_annual(symbol,year,out):
    rows,m5=b.load_year(symbol,year)
    a,rs=b.build_adaptive(rows,replay_ts(year),MODE_DAYS)
    start=min(rs+WARMUP_BARS,len(a)-1)
    sim=b.Sim(a,m5,start)
    met=sim.run()
    met.update(test='ANNUAL',symbol=symbol,year=year,model='60D',warmup_bars=WARMUP_BARS,warmup_days=WARMUP_BARS/12.0)
    out.mkdir(parents=True,exist_ok=True)
    (out/'summary.json').write_text(json.dumps(met,indent=2,ensure_ascii=False))
    pd.DataFrame([met]).to_csv(out/'summary.csv',index=False)
    pd.DataFrame(b.analyze_losses(sim.trades,top=12)).to_csv(out/'worst_losses.csv',index=False)
    daily_equity(sim._equity_history).to_csv(out/'equity_daily.csv',index=False)
    print('RESULT_RISK',json.dumps(met,ensure_ascii=False),flush=True)

def run_continuous(symbol,out):
    all_rows=[];all_m5={}
    for year in range(2020,2026):
        rows,m5=b.load_year(symbol,year)
        add_missing_h1_5m(symbol,year,m5)
        all_rows.extend(rows)
        for k,v in m5.items():all_m5.setdefault(k,[]).extend(v)
    all_rows.sort(key=lambda r:r['ts'])
    for k in list(all_m5):all_m5[k].sort(key=lambda x:x['ts'])
    rp=replay_ts(2020)
    a,rs=b.build_adaptive(all_rows,rp,MODE_DAYS)
    start=min(rs+WARMUP_BARS,len(a)-1)
    sim=b.Sim(a,all_m5,start)
    met=sim.run()
    met.update(test='CONTINUOUS_2020_2025',symbol=symbol,year='2020-2025',model='60D',warmup_bars=WARMUP_BARS,warmup_days=WARMUP_BARS/12.0,train_period='2020-01-01..2020-06-30',trade_start=str(pd.to_datetime(a[start]['ts'],unit='ms',utc=True)))
    met['year_end_equity']=year_end_equity(sim._equity_history)
    out.mkdir(parents=True,exist_ok=True)
    (out/'summary.json').write_text(json.dumps(met,indent=2,ensure_ascii=False))
    pd.DataFrame([{k:v for k,v in met.items() if k!='year_end_equity'}]).to_csv(out/'summary.csv',index=False)
    pd.DataFrame([{'year':y,'equity':e} for y,e in met['year_end_equity'].items()]).to_csv(out/'year_end_equity.csv',index=False)
    pd.DataFrame(b.analyze_losses(sim.trades,top=20)).to_csv(out/'worst_losses.csv',index=False)
    daily_equity(sim._equity_history).to_csv(out/'equity_daily.csv',index=False)
    print('RESULT_CONTINUOUS',json.dumps(met,ensure_ascii=False),flush=True)

if __name__=='__main__':
    kind=os.environ.get('BACKTEST_KIND','ANNUAL').upper()
    symbol=os.environ['BACKTEST_SYMBOL']
    root=Path('research/super_model_output_risk')
    if kind=='ANNUAL':
        year=int(os.environ['BACKTEST_YEAR'])
        run_annual(symbol,year,root/'annual'/f'{symbol}_{year}')
    elif kind=='CONTINUOUS':
        run_continuous(symbol,root/'continuous'/symbol)
    else:raise ValueError(kind)
