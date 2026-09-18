import os, json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import super_model_adaptive_backtest as b

symbol=os.environ['BACKTEST_SYMBOL']
year=int(os.environ['BACKTEST_YEAR'])
out=Path('research/super_model_output_gridcap')/f'{symbol}_{year}'
out.mkdir(parents=True,exist_ok=True)
rows,m5=b.load_year(symbol,year)
replay=int(datetime(year,7,1,tzinfo=timezone.utc).timestamp()*1000)

# Freeze model/execution candidate selected before this test: 60D memory + 150-bar no-trade warm-up.
a,rs=b.build_adaptive(rows,replay,60)
start=min(rs+150,len(a)-1)
results=[]; losses=[]

orig_add=b.Sim.add_fill
orig_grid=b.Sim.grid

for cap in [6,8,11]:
    def add_fill_capped(self, side, p, kind, ts, _cap=cap):
        if self.pos and self.pos.get('tranches',0) >= _cap:
            return False
        return orig_add(self,side,p,kind,ts)
    def grid_capped(self, side, anchor, active, _cap=cap):
        used=max(0,(self.pos['tranches']-1) if self.pos and self.pos['side']==side else 0)
        remaining=max(0,(_cap-1)-used)
        self.pending=[dict(side=side,
            price=anchor*(1-b.GRID_GAP*n) if side=='LONG' else anchor*(1+b.GRID_GAP*n),
            activeFrom=active,gridLevel=used+n) for n in range(1,remaining+1)]
    b.Sim.add_fill=add_fill_capped
    b.Sim.grid=grid_capped
    sim=b.Sim(a,m5,start)
    metrics=sim.run();metrics.update(symbol=symbol,year=year,mode='60',warmup_bars=150,max_tranches=cap)
    results.append(metrics)
    for tr in b.analyze_losses(sim.trades,top=8): losses.append(dict(symbol=symbol,year=year,max_tranches=cap,**tr))
    mx=max([t.get('tranches',0) for t in sim.trades]+([sim.pos.get('tranches',0)] if sim.pos else [0]))
    print('RESULT_GRIDCAP',json.dumps(metrics,ensure_ascii=False),'MAX_SEEN',mx,flush=True)

b.Sim.add_fill=orig_add;b.Sim.grid=orig_grid
pd.DataFrame(results).to_csv(out/'results.csv',index=False)
(out/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
(out/'worst_losses.json').write_text(json.dumps(losses,indent=2,ensure_ascii=False))
print('DONE_GRIDCAP',symbol,year,flush=True)
