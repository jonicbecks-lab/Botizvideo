import os, json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import super_model_adaptive_backtest as b

symbol=os.environ['BACKTEST_SYMBOL']
year=int(os.environ['BACKTEST_YEAR'])
out=Path('research/super_model_output_challengers')/f'{symbol}_{year}'
out.mkdir(parents=True,exist_ok=True)
rows,m5=b.load_year(symbol,year)
replay=int(datetime(year,7,1,tzinfo=timezone.utc).timestamp()*1000)
# Frozen development baseline selected before these challengers:
# 60D memory, 150 closed 2H bars no-trade warmup, max 11 tranches.
a,rs=b.build_adaptive(rows,replay,60)
start=min(rs+150,len(a)-1)

class Cap11Sim(b.Sim):
    def add_fill(self,side,p,kind,ts):
        if self.pos and self.pos.get('tranches',0)>=11:return False
        return super().add_fill(side,p,kind,ts)
    def grid(self,side,anchor,active):
        used=max(0,(self.pos['tranches']-1) if self.pos and self.pos['side']==side else 0)
        remaining=max(0,10-used)
        self.pending=[dict(side=side,
            price=anchor*(1-b.GRID_GAP*n) if side=='LONG' else anchor*(1+b.GRID_GAP*n),
            activeFrom=active,gridLevel=used+n) for n in range(1,remaining+1)]

class Strong65Sim(Cap11Sim):
    # One isolated change: every NEW campaign/restart needs the model's own
    # strong threshold (LONG >= .65, SHORT <= .35). Existing positions are untouched.
    def start(self,side,p,active,ts,i,kind='AUTO'):
        r=self.a[i];prob=r.get('p24')
        ok=(prob is not None and ((side=='LONG' and prob>=.65) or (side=='SHORT' and prob<=.35)))
        if not ok:return False
        return super().start(side,p,active,ts,i,kind)

class FlowBrakeSim(Cap11Sim):
    # One isolated change: if either 12h perp or spot flow opposes the held side,
    # cancel only unfilled GRID orders. Position stays. Re-arm remaining GRID when
    # both flows align again and directional state still matches the position.
    def sync(self,i):
        super().sync(i)
        if not self.pos or self.pos.get('profitStopArmed'):return
        r=self.a[i];side=self.pos['side']
        if r.get('state') not in ('LONG','SHORT') or r.get('state')!=side:return
        pf,sf=r.get('pf12'),r.get('sf12')
        disagree=False
        if pf is not None and sf is not None:
            disagree=(side=='LONG' and (pf<0 or sf<0)) or (side=='SHORT' and (pf>0 or sf>0))
        if disagree:
            self.pending=[]
        elif not self.pending and self.pos.get('tranches',0)<11:
            self.grid(side,r['c'],i+1)

variants=[('BASELINE',Cap11Sim),('STRONG65',Strong65Sim),('FLOW_BRAKE',FlowBrakeSim)]
results=[];losses=[]
for name,Cls in variants:
    sim=Cls(a,m5,start);metrics=sim.run();metrics.update(symbol=symbol,year=year,variant=name,mode='60',warmup_bars=150,max_tranches=11)
    results.append(metrics)
    for tr in b.analyze_losses(sim.trades,top=8):losses.append(dict(symbol=symbol,year=year,variant=name,**tr))
    maxtr=max([t.get('tranches',0) for t in sim.trades]+([sim.pos.get('tranches',0)] if sim.pos else [0]))
    print('RESULT_CHALLENGER',json.dumps(metrics,ensure_ascii=False),'MAX_TRANCHES',maxtr,flush=True)

pd.DataFrame(results).to_csv(out/'results.csv',index=False)
(out/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
(out/'worst_losses.json').write_text(json.dumps(losses,indent=2,ensure_ascii=False))
print('DONE_CHALLENGERS',symbol,year,flush=True)
