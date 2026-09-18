import json, os, threading, time
from pathlib import Path
from account import PaperAccount, now_ms
from model import fetch_closed_5m, fetch_funding_events, fetch_live_price, latest_signal

class PaperController:
    def __init__(self,state_path):
        self.path=Path(state_path); self.path.parent.mkdir(parents=True,exist_ok=True); state={}
        if self.path.exists():
            try:state=json.loads(self.path.read_text())
            except Exception:pass
        self.accounts={s:PaperAccount(s,state.get(s)) for s in ['BTCUSDT','ETHUSDT']}; self.stop=threading.Event()
    def save(self):
        tmp=self.path.with_suffix('.tmp'); tmp.write_text(json.dumps({s:a.to_dict() for s,a in self.accounts.items()},ensure_ascii=False,indent=2)); os.replace(tmp,self.path)
    def init_one(self,a):
        try:
            p=fetch_live_price(a.symbol); sig=latest_signal(a.symbol)
            with a.lock:
                a.last_price=p; a.sync_signal(sig,p); a.last_exec_5m=a.last_exec_5m or (now_ms()//300000)*300000; a.last_funding_ts=a.last_funding_ts or now_ms()-12*3600000; a.error=None
        except Exception as e:
            with a.lock:a.error=f"INIT: {type(e).__name__}: {e}"; a.log('SYSTEM','ERROR',a.error)
    def start(self):
        for a in self.accounts.values():self.init_one(a)
        self.save(); threading.Thread(target=self.loop,daemon=True).start()
    def loop(self):
        lm={s:0 for s in self.accounts}; lf={s:0 for s in self.accounts}
        while not self.stop.is_set():
            for s,a in self.accounts.items():
                try:
                    p=fetch_live_price(s)
                    with a.lock:a.last_price=p
                    bars=fetch_closed_5m(s,(a.last_exec_5m or (now_ms()//300000)*300000)+300000)
                    if bars:
                        with a.lock:
                            for b in bars:a.process_5m(b); a.last_exec_5m=b['ts']
                    if time.time()-lm[s]>120:
                        sig=latest_signal(s)
                        with a.lock:
                            if sig.get('bar_ts')!=a.model_bar_ts:a.sync_signal(sig,p)
                            else:a.p24=sig.get('p24'); a.persist=sig.get('persist')
                            a.error=None
                        lm[s]=time.time()
                    if time.time()-lf[s]>900:
                        ev=fetch_funding_events(s,(a.last_funding_ts or now_ms()-12*3600000)+1)
                        with a.lock:
                            for ts,rate in ev:
                                if ts<=now_ms():a.apply_funding(ts,rate,p); a.last_funding_ts=max(a.last_funding_ts or 0,ts)
                        lf[s]=time.time()
                except Exception as e:
                    with a.lock:a.error=f"LOOP: {type(e).__name__}: {e}"
                self.save()
            self.stop.wait(10)
    def public(self):return {s:a.public() for s,a in self.accounts.items()}
    def action(self,s,action):
        a=self.accounts[s]; p=fetch_live_price(s)
        with a.lock:a.manual(action,p)
        self.save(); return a.public()
    def reset(self,s):
        self.accounts[s]=PaperAccount(s); self.init_one(self.accounts[s]); self.accounts[s].log('MANUAL','RESET','Paper reset to $1000'); self.save(); return self.accounts[s].public()
