import os, json
from pathlib import Path
import pandas as pd
from datetime import datetime, timezone
import super_model_adaptive_backtest as b

symbol=os.environ['BACKTEST_SYMBOL']
year=int(os.environ['BACKTEST_YEAR'])
out=Path('research/super_model_output')/f'{symbol}_{year}'
out.mkdir(parents=True,exist_ok=True)
rows,m5=b.load_year(symbol,year)
replay=int(datetime(year,7,1,tzinfo=timezone.utc).timestamp()*1000)
results=[]; losses=[]
for mode in b.MODES:
    print('MODEL',symbol,year,mode,flush=True)
    a,rs=(b.build_original(rows,replay) if mode=='ORIGINAL' else b.build_adaptive(rows,replay,mode))
    sim=b.Sim(a,m5,rs); metrics=sim.run(); metrics.update(symbol=symbol,year=year,mode=str(mode)); results.append(metrics)
    for tr in b.analyze_losses(sim.trades): losses.append(dict(symbol=symbol,year=year,mode=str(mode),**tr))
    print('RESULT',json.dumps(metrics,ensure_ascii=False),flush=True)
pd.DataFrame(results).to_csv(out/'results.csv',index=False)
(out/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
(out/'worst_losses.json').write_text(json.dumps(losses,indent=2,ensure_ascii=False))
print('DONE',symbol,year,flush=True)
