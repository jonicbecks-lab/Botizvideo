import os, json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import super_model_adaptive_backtest as b

# Preserve corrected campaign risk cap: initial tranche + at most 10 grid fills.
_orig_add_fill = b.Sim.add_fill

def add_fill_cap11(self, side, p, kind, ts):
    if self.pos and self.pos.get('tranches', 0) >= b.GRID_STEPS + 1:
        return False
    return _orig_add_fill(self, side, p, kind, ts)

def grid_remaining(self, side, anchor, active):
    used = max(0, (self.pos['tranches'] - 1) if self.pos and self.pos['side'] == side else 0)
    remaining = max(0, b.GRID_STEPS - used)
    self.pending = [
        dict(
            side=side,
            price=anchor*(1-b.GRID_GAP*n) if side=='LONG' else anchor*(1+b.GRID_GAP*n),
            activeFrom=active,
            gridLevel=used+n,
        )
        for n in range(1, remaining+1)
    ]

b.Sim.add_fill = add_fill_cap11
b.Sim.grid = grid_remaining

symbol=os.environ['BACKTEST_SYMBOL']
year=int(os.environ['BACKTEST_YEAR'])
out=Path('research/super_model_output_warmup')/f'{symbol}_{year}'
out.mkdir(parents=True,exist_ok=True)
rows,m5=b.load_year(symbol,year)
replay=int(datetime(year,7,1,tzinfo=timezone.utc).timestamp()*1000)

results=[]; losses=[]
for mode in ['ORIGINAL',60]:
    print('MODEL_WARMUP',symbol,year,mode,flush=True)
    a,rs=(b.build_original(rows,replay) if mode=='ORIGINAL' else b.build_adaptive(rows,replay,60))
    for delay in [0,50,100,150]:
        start=min(rs+delay,len(a)-1)
        sim=b.Sim(a,m5,start)
        metrics=sim.run()
        metrics.update(symbol=symbol,year=year,mode=str(mode),warmup_bars=delay,warmup_days=delay/12.0,risk_cap='11_tranches')
        results.append(metrics)
        for tr in b.analyze_losses(sim.trades,top=5):
            losses.append(dict(symbol=symbol,year=year,mode=str(mode),warmup_bars=delay,**tr))
        max_tr=max([t.get('tranches',0) for t in sim.trades] + ([sim.pos.get('tranches',0)] if sim.pos else [0]))
        print('RESULT_WARMUP',json.dumps(metrics,ensure_ascii=False),'MAX_TRANCHES',max_tr,flush=True)

pd.DataFrame(results).to_csv(out/'results.csv',index=False)
(out/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
(out/'worst_losses.json').write_text(json.dumps(losses,indent=2,ensure_ascii=False))
print('DONE_WARMUP',symbol,year,flush=True)
