import io, json, math, time, zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import precision_score, recall_score, f1_score, accuracy_score, roc_auc_score, confusion_matrix

BASE='https://data.binance.vision/data'
CACHE=Path('research/range_cache'); OUT=Path('research/range_lab_output')
CACHE.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
SYMBOLS=['BTCUSDT','ETHUSDT']; YEARS=list(range(2020,2026)); BAR=2*3600_000
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0 RANGE-LAB/1.0'})

def norm_ts(x):
    x=int(float(x)); return x//1000 if x>10**14 else x

def dl(url,dest,retries=4):
    dest=Path(dest)
    if dest.exists() and dest.stat().st_size>100:return dest
    dest.parent.mkdir(parents=True,exist_ok=True)
    for k in range(retries):
        try:
            r=S.get(url,timeout=60)
            if r.status_code==404:return None
            r.raise_for_status(); dest.write_bytes(r.content); return dest
        except Exception as e:
            if k==retries-1: print('DOWNLOAD_FAIL',url,repr(e),flush=True)
            time.sleep(k+1)
    return None

def read_zip(p):
    if not p:return pd.DataFrame()
    with zipfile.ZipFile(p) as z:
        n=[x for x in z.namelist() if x.lower().endswith('.csv')][0]; raw=z.read(n)
    return pd.read_csv(io.BytesIO(raw),header=None)

def kline_url(market,sym,kind,year,month):
    if market=='spot':return f'{BASE}/spot/monthly/klines/{sym}/2h/{sym}-2h-{year}-{month:02d}.zip'
    if kind=='klines':return f'{BASE}/futures/um/monthly/klines/{sym}/2h/{sym}-2h-{year}-{month:02d}.zip'
    return f'{BASE}/futures/um/monthly/premiumIndexKlines/{sym}/2h/{sym}-2h-{year}-{month:02d}.zip'

def load_klines(market,sym,kind,year,month):
    p=dl(kline_url(market,sym,kind,year,month),CACHE/f'{market}_{kind}_{sym}_{year}_{month:02d}.zip')
    if not p:return pd.DataFrame()
    d=read_zip(p)
    if d.empty:return d
    if str(d.iloc[0,0]).lower() in ('open_time','opentime'):d=d.iloc[1:].reset_index(drop=True)
    if d.shape[1]<11:return pd.DataFrame()
    return pd.DataFrame({'ts':d.iloc[:,0].map(norm_ts),'o':pd.to_numeric(d.iloc[:,1]),'h':pd.to_numeric(d.iloc[:,2]),'l':pd.to_numeric(d.iloc[:,3]),'c':pd.to_numeric(d.iloc[:,4]),'qv':pd.to_numeric(d.iloc[:,7]),'tbq':pd.to_numeric(d.iloc[:,10])})

def funding_urls(sym,year,month):
    return [f'{BASE}/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{year}-{month:02d}.zip',f'{BASE}/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{year}-{month:02d}.csv.zip']

def load_funding(sym,year,month):
    p=None
    for ix,u in enumerate(funding_urls(sym,year,month)):
        p=dl(u,CACHE/f'fund{ix}_{sym}_{year}_{month:02d}.zip')
        if p:break
    if not p:return pd.DataFrame(columns=['ts','fund'])
    try:
        with zipfile.ZipFile(p) as z:
            n=[x for x in z.namelist() if x.lower().endswith('.csv')][0]; raw=z.read(n)
        d=pd.read_csv(io.BytesIO(raw)); lc={str(c).lower():c for c in d.columns}
        tc=next((c for k,c in lc.items() if 'funding' in k and 'time' in k),None) or next((c for k,c in lc.items() if 'calc_time' in k),None)
        rc=next((c for k,c in lc.items() if 'funding' in k and 'rate' in k),None) or next((c for k,c in lc.items() if 'last_funding_rate' in k),None)
        if tc is not None and rc is not None:return pd.DataFrame({'ts':d[tc].map(norm_ts),'fund':pd.to_numeric(d[rc],errors='coerce')}).dropna()
    except Exception:pass
    d=read_zip(p)
    if d.empty:return pd.DataFrame(columns=['ts','fund'])
    tc=rc=None
    for c in d.columns:
        x=pd.to_numeric(d[c],errors='coerce'); med=x.dropna().abs().median() if x.notna().any() else 0
        if med>1e11 and tc is None:tc=c
        if x.notna().mean()>.8 and x.dropna().abs().quantile(.95)<.1:rc=c
    if tc is None or rc is None:return pd.DataFrame(columns=['ts','fund'])
    return pd.DataFrame({'ts':d[tc].map(norm_ts),'fund':pd.to_numeric(d[rc],errors='coerce')}).dropna()

def load_symbol(sym):
    jobs=[]
    with ThreadPoolExecutor(max_workers=20) as ex:
        for y in YEARS:
            for m in range(1,13):
                jobs += [('fut',ex.submit(load_klines,'um',sym,'klines',y,m)),('spot',ex.submit(load_klines,'spot',sym,'klines',y,m)),('prem',ex.submit(load_klines,'um',sym,'premium',y,m)),('fund',ex.submit(load_funding,sym,y,m))]
        parts={k:[] for k in ['fut','spot','prem','fund']}
        for k,f in jobs:
            try:
                x=f.result()
                if x is not None and not x.empty:parts[k].append(x)
            except Exception as e:print('PART_FAIL',sym,k,repr(e),flush=True)
    cat=lambda k:pd.concat(parts[k],ignore_index=True).sort_values('ts').drop_duplicates('ts') if parts[k] else pd.DataFrame()
    fut,spot,prem,fund=cat('fut'),cat('spot'),cat('prem'),cat('fund')
    if fut.empty or spot.empty:raise RuntimeError(f'core data missing {sym}')
    df=fut.rename(columns={'qv':'pqv','tbq':'ptbq'}).merge(spot[['ts','qv','tbq']].rename(columns={'qv':'sqv','tbq':'stbq'}),on='ts',how='left')
    if not prem.empty:df=df.merge(prem[['ts','c']].rename(columns={'c':'premium'}),on='ts',how='left')
    else:df['premium']=0.0
    df['perpDelta']=2*df.ptbq-df.pqv; df['spotDelta']=2*df.stbq-df.sqv
    fts=fund.ts.to_numpy(dtype=np.int64) if not fund.empty else np.array([],dtype=np.int64); fr=fund.fund.to_numpy(float) if not fund.empty else np.array([],float)
    vals=[]
    for ts in df.ts.astype(np.int64):
        hi=np.searchsorted(fts,ts+BAR,side='right'); lo=np.searchsorted(fts,ts+BAR-24*3600_000,side='left'); vals.append(float(fr[lo:hi].sum()) if hi>lo else 0.)
    df['funding24']=vals; df['symbol']=sym; df['year']=pd.to_datetime(df.ts,unit='ms',utc=True).dt.year
    print('LOADED',sym,len(df),df.year.min(),df.year.max(),flush=True); return df.reset_index(drop=True)

def safe_ratio(d,q):
    q=float(np.nansum(q)); return float(np.nansum(d))/q if q>0 else 0.0

def lin_slope(x):
    x=np.asarray(x,float); n=len(x)
    if n<2:return 0.
    t=np.arange(n,dtype=float); t-=t.mean(); den=(t*t).sum(); return float(((x-x.mean())*t).sum()/den) if den else 0.

def features(df,i,W=24):
    if i<max(W,36):return None
    w=df.iloc[i-W+1:i+1]; close=w.c.to_numpy(float); high=w.h.to_numpy(float); low=w.l.to_numpy(float); op=w.o.to_numpy(float)
    box_lo=float(np.min(low)); box_hi=float(np.max(high)); mid=(box_lo+box_hi)/2; width=max(1e-12,box_hi-box_lo)
    tr=np.maximum(high-low,np.maximum(np.abs(high-np.r_[op[0],close[:-1]]),np.abs(low-np.r_[op[0],close[:-1]]))); atr=max(float(np.mean(tr[-12:])),1e-12)
    rets=np.diff(np.log(close)); path=float(np.sum(np.abs(rets))); eff=abs(math.log(close[-1]/close[0]))/path if path else 0.
    z=close-mid; crosses=int(np.sum((z[1:]*z[:-1])<0)); touch_band=.15*width
    touch_hi=int(np.sum(high>=box_hi-touch_band)); touch_lo=int(np.sum(low<=box_lo+touch_band)); in_close=float(np.mean((close>=box_lo)&(close<=box_hi)))
    pos=(close[-1]-mid)/(width/2); slope24=lin_slope(np.log(close))*W/(atr/mid); slope12=lin_slope(np.log(close[-12:]))*12/(atr/mid)
    rv6=float(np.std(rets[-6:])) if len(rets)>=6 else 0; rv24=float(np.std(rets)) if len(rets) else 0; compression=rv6/(rv24+1e-12); body=float(np.mean(np.abs(close-op)/(high-low+1e-12)))
    pf3=safe_ratio(df.perpDelta.iloc[i-2:i+1],df.pqv.iloc[i-2:i+1]); pf6=safe_ratio(df.perpDelta.iloc[i-5:i+1],df.pqv.iloc[i-5:i+1]); pf12=safe_ratio(df.perpDelta.iloc[i-11:i+1],df.pqv.iloc[i-11:i+1])
    sf3=safe_ratio(df.spotDelta.iloc[i-2:i+1],df.sqv.iloc[i-2:i+1]); sf6=safe_ratio(df.spotDelta.iloc[i-5:i+1],df.sqv.iloc[i-5:i+1]); sf12=safe_ratio(df.spotDelta.iloc[i-11:i+1],df.sqv.iloc[i-11:i+1])
    mom3=math.log(df.c.iloc[i]/df.c.iloc[i-3]); mom6=math.log(df.c.iloc[i]/df.c.iloc[i-6]); mom12=math.log(df.c.iloc[i]/df.c.iloc[i-12]); upper_frac=float(np.mean(close>mid)); recent_pos=float(np.mean((close[-6:]-mid)/(width/2)))
    hi_slope=lin_slope(high[-12:])/atr*12; lo_slope=lin_slope(low[-12:])/atr*12; hi_idx=np.where(high>=box_hi-touch_band)[0]; lo_idx=np.where(low<=box_lo+touch_band)[0]
    since_hi=(W-1-int(hi_idx[-1]))/W if len(hi_idx) else 1.; since_lo=(W-1-int(lo_idx[-1]))/W if len(lo_idx) else 1.; prem=float(df.premium.iloc[i]) if pd.notna(df.premium.iloc[i]) else 0.; fund=float(df.funding24.iloc[i]) if pd.notna(df.funding24.iloc[i]) else 0.
    vals=[width/atr,eff,abs(slope24),abs(slope12),crosses/6,touch_hi/6,touch_lo/6,in_close,compression,body,pos,slope24,slope12,mom3/.02,mom6/.035,mom12/.055,pf3/.08,pf6/.08,pf12/.08,sf3/.08,sf6/.08,sf12/.08,(sf12-pf12)/.08,fund/.0015,prem/.0015,upper_frac,recent_pos,hi_slope,lo_slope,since_hi,since_lo]
    meta={'low':box_lo,'high':box_hi,'mid':mid,'atr':atr,'width':width,'touch_hi':touch_hi,'touch_lo':touch_lo,'crosses':crosses,'eff':eff}
    return np.clip(np.asarray(vals,float),-6,6),meta

FEATURE_NAMES=['width_atr','eff','abs_slope24','abs_slope12','mid_cross','touch_hi','touch_lo','in_close','compression','body_ratio','pos','slope24','slope12','mom3','mom6','mom12','pf3','pf6','pf12','sf3','sf6','sf12','flow_div','funding24','premium','upper_frac','recent_pos','hi_slope','lo_slope','since_hi','since_lo']
RANGE_IDX=list(range(10)); BREAK_IDX=list(range(10,31))

def label_one(df,i,m):
    gate=(m['eff']<=.42 and m['crosses']>=2 and m['touch_hi']>=2 and m['touch_lo']>=2 and 2.0<=m['width']/m['atr']<=12.0)
    fut=df.iloc[i+1:min(len(df),i+13)]
    if len(fut)<12:return gate,None,None
    inside=((fut.c>=m['low']-.25*m['atr'])&(fut.c<=m['high']+.25*m['atr'])).mean(); eu=(fut.h.max()-m['high'])/m['atr']; ed=(m['low']-fut.l.min())/m['atr']
    yr=int(inside>=.75 and eu<1.0 and ed<1.0); bo=None
    for h in range(1,25):
        if i+h>=len(df):break
        c=float(df.c.iloc[i+h])
        if c>m['high']+.35*m['atr']:bo=(1,h);break
        if c<m['low']-.35*m['atr']:bo=(0,h);break
    return gate,yr,bo

def build_dataset(dfs):
    out=[]
    for sym,df in dfs.items():
        for i in range(40,len(df)-24):
            fm=features(df,i)
            if fm is None:continue
            x,m=fm; gate,yr,bo=label_one(df,i,m)
            if yr is not None:out.append({'symbol':sym,'ts':int(df.ts.iloc[i]),'year':int(df.year.iloc[i]),'gate':gate,'y_range':yr,'bdir':None if bo is None else bo[0],'bh':None if bo is None else bo[1],'x':x})
    return out

def fit_export(X,y):
    sc=StandardScaler(); Z=sc.fit_transform(X); m=LogisticRegression(C=.5,max_iter=2000,class_weight='balanced',solver='lbfgs');m.fit(Z,y);return sc,m

def probs(sc,m,X):return m.predict_proba(sc.transform(X))[:,1]

def choose_threshold(y,p,min_precision=.62):
    best=(.5,-1,None)
    for th in np.arange(.45,.86,.01):
        pred=(p>=th).astype(int); pr=precision_score(y,pred,zero_division=0);rc=recall_score(y,pred,zero_division=0);f=f1_score(y,pred,zero_division=0);score=f if pr>=min_precision else f*.5
        if score>best[1]:best=(float(th),score,(float(pr),float(rc),float(f)))
    return best[0],best[2]

def metrics(y,p,th):
    pred=(p>=th).astype(int); o={'n':len(y),'precision':precision_score(y,pred,zero_division=0),'recall':recall_score(y,pred,zero_division=0),'f1':f1_score(y,pred,zero_division=0),'accuracy':accuracy_score(y,pred),'positive_rate':float(np.mean(pred)),'cm':confusion_matrix(y,pred,labels=[0,1]).tolist()}
    try:o['auc']=roc_auc_score(y,p)
    except Exception:o['auc']=None
    return o

def main():
    dfs={s:load_symbol(s) for s in SYMBOLS}; ds=build_dataset(dfs); print('DATASET',len(ds),flush=True); rg=[r for r in ds if r['gate']]
    arr=lambda rows,idx:np.stack([r['x'][idx] for r in rows])
    tr=[r for r in rg if r['year']<=2023];va=[r for r in rg if r['year']==2024];te=[r for r in rg if r['year']==2025]
    scR,mR=fit_export(arr(tr,RANGE_IDX),np.array([r['y_range'] for r in tr]));pv=probs(scR,mR,arr(va,RANGE_IDX));thR,vm=choose_threshold(np.array([r['y_range'] for r in va]),pv)
    br=[r for r in ds if r['gate'] and r['bdir'] is not None];btr=[r for r in br if r['year']<=2023];bva=[r for r in br if r['year']==2024];bte=[r for r in br if r['year']==2025]
    scB,mB=fit_export(arr(btr,BREAK_IDX),np.array([r['bdir'] for r in btr]));bpv=probs(scB,mB,arr(bva,BREAK_IDX));yv=np.array([r['bdir'] for r in bva]);best=(.08,-1,None)
    for conf in np.arange(.04,.31,.01):
        mask=np.abs(bpv-.5)>=conf
        if mask.mean()<.25:continue
        acc=accuracy_score(yv[mask],(bpv[mask]>=.5).astype(int));score=acc+.05*mask.mean()
        if score>best[1]:best=(float(conf),score,(float(acc),float(mask.mean()),int(mask.sum())))
    confB=best[0];prt=probs(scR,mR,arr(te,RANGE_IDX));rt=metrics(np.array([r['y_range'] for r in te]),prt,thR);bpt=probs(scB,mB,arr(bte,BREAK_IDX));yt=np.array([r['bdir'] for r in bte]);mask=np.abs(bpt-.5)>=confB
    bt={'n':len(yt),'confidence':confB,'coverage':float(mask.mean()),'calls':int(mask.sum()),'accuracy':float(accuracy_score(yt[mask],(bpt[mask]>=.5).astype(int))) if mask.any() else None}
    per={}
    for sym in SYMBOLS:
        rr=[r for r in te if r['symbol']==sym];bb=[r for r in bte if r['symbol']==sym];pr=probs(scR,mR,arr(rr,RANGE_IDX));pb=probs(scB,mB,arr(bb,BREAK_IDX));yb=np.array([r['bdir'] for r in bb]);mm=np.abs(pb-.5)>=confB
        per[sym]={'range':metrics(np.array([r['y_range'] for r in rr]),pr,thR),'breakout':{'n':len(bb),'coverage':float(mm.mean()),'accuracy':float(accuracy_score(yb[mm],(pb[mm]>=.5).astype(int))) if mm.any() else None,'calls':int(mm.sum())}}
    export={'version':'RANGE_LAB_V1','trained':'2020-2023','tuned':2024,'holdout':2025,'window_bars':24,'horizon_range_bars':12,'horizon_breakout_bars':24,'past_gate':{'eff_max':.42,'crosses_min':2,'touch_each_min':2,'width_atr_min':2.0,'width_atr_max':12.0},'range':{'features':[FEATURE_NAMES[i] for i in RANGE_IDX],'mean':scR.mean_.tolist(),'scale':scR.scale_.tolist(),'coef':mR.coef_[0].tolist(),'intercept':float(mR.intercept_[0]),'threshold':thR},'breakout':{'features':[FEATURE_NAMES[i] for i in BREAK_IDX],'mean':scB.mean_.tolist(),'scale':scB.scale_.tolist(),'coef':mB.coef_[0].tolist(),'intercept':float(mB.intercept_[0]),'confidence':confB},'validation':{'range_threshold_metrics':vm,'breakout_conf_metrics':best[2]},'holdout_2025':{'range':rt,'breakout':bt,'per_symbol':per},'counts':{'dataset':len(ds),'range_train':len(tr),'range_val':len(va),'range_test':len(te),'break_train':len(btr),'break_val':len(bva),'break_test':len(bte)}}
    (OUT/'range_model_v1.json').write_text(json.dumps(export,indent=2),encoding='utf-8')
    ex=[]
    for r in te:
        pr=float(probs(scR,mR,np.array([r['x'][RANGE_IDX]]))[0])
        if pr<thR:continue
        pb=float(probs(scB,mB,np.array([r['x'][BREAK_IDX]]))[0]);ex.append({'symbol':r['symbol'],'ts':r['ts'],'p_range':pr,'p_up':pb,'actual_range':r['y_range'],'actual_breakout':r['bdir'],'break_h':r['bh']})
    pd.DataFrame(ex).to_csv(OUT/'holdout_calls_2025.csv',index=False)
    (OUT/'REPORT.md').write_text('# RANGE LAB V1\n\n```json\n'+json.dumps(export['holdout_2025'],indent=2)+'\n```\n',encoding='utf-8')
    print(json.dumps(export['holdout_2025'],indent=2),flush=True);print('THRESHOLDS',thR,confB,flush=True)
if __name__=='__main__':main()
