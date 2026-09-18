import os, json, copy
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import super_model_adaptive_backtest as b

symbol=os.environ['BACKTEST_SYMBOL']
year=int(os.environ['BACKTEST_YEAR'])
out=Path('research/super_model_output_hybrid')/f'{symbol}_{year}'
out.mkdir(parents=True,exist_ok=True)
rows,m5=b.load_year(symbol,year)
replay=int(datetime(year,7,1,tzinfo=timezone.utc).timestamp()*1000)

# Build the two already-defined memories independently, then make ONE pre-registered
# hybrid: equal-weight raw probability blend before the unchanged SUPER state machine.
a_orig,rs=b.build_original(rows,replay)
a60,_=b.build_adaptive(rows,replay,60)
a_h=[dict(r) for r in rows]
for i,r in enumerate(a_h):
    po=a_orig[i].get('pRaw'); p6=a60[i].get('pRaw')
    if po is None and p6 is None:r['pRaw']=None
    elif po is None:r['pRaw']=p6
    elif p6 is None:r['pRaw']=po
    else:r['pRaw']=0.5*po+0.5*p6
rs_h=b.finalize_state(a_h,replay)
if isinstance(rs_h,int): rs=rs_h
start=min(rs+150,len(a_h)-1)

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

results=[];losses=[]
for name,a in [('ORIGINAL150',a_orig),('60D150',a60),('HYBRID50_50',a_h)]:
    sim=Cap11Sim(a,m5,start);metrics=sim.run();metrics.update(symbol=symbol,year=year,variant=name,warmup_bars=150,max_tranches=11)
    results.append(metrics)
    for tr in b.analyze_losses(sim.trades,top=8):losses.append(dict(symbol=symbol,year=year,variant=name,**tr))
    print('RESULT_HYBRID',json.dumps(metrics,ensure_ascii=False),flush=True)

pd.DataFrame(results).to_csv(out/'results.csv',index=False)
(out/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
(out/'worst_losses.json').write_text(json.dumps(losses,indent=2,ensure_ascii=False))
print('DONE_HYBRID',symbol,year,flush=True)
