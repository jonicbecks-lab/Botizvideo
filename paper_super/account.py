import math, time, threading

LEV=10.0; MMR=.005; TAKER=.0005; MAKER=.0002; GRID_STEPS=10; GRID_GAP=.01; PROFIT_LOCK_ROE=.10; RISK_PCT=.10
now_ms=lambda:int(time.time()*1000)

class PaperAccount:
    def __init__(self,symbol,state=None):
        self.symbol=symbol; self.lock=threading.RLock(); self.starting_deposit=1000.; self.balance=1000.; self.position=None; self.pending=[]
        self.signal='WARMUP'; self.p24=None; self.persist=None; self.model_bar_ts=None; self.auto_enabled=True; self.manual_override=False
        self.known_signal=None; self.initialized=False; self.last_exec_5m=None; self.last_funding_ts=None; self.last_price=None; self.error=None
        self.stats={'campaigns':0,'profitStops':0,'trailMoves':0,'switches':0,'liquidations':0}; self.events=[]
        if state:
            for k,v in state.items():
                if hasattr(self,k): setattr(self,k,v)
    def to_dict(self):
        keys=['starting_deposit','balance','position','pending','signal','p24','persist','model_bar_ts','auto_enabled','manual_override','known_signal','initialized','last_exec_5m','last_funding_ts','last_price','stats']
        d={k:getattr(self,k) for k in keys}; d['events']=self.events[-500:]; return d
    def upnl(self,p=None):
        if not self.position:return 0.
        p=float(p or self.last_price or self.position['avg']); q=self.position['qty']; e=self.position['avg']
        return q*(p-e) if self.position['side']=='LONG' else q*(e-p)
    def equity(self,p=None):return self.balance+self.upnl(p)
    def roe(self,p):return self.upnl(p)/self.position['margin'] if self.position and self.position['margin'] else 0.
    def liq_price(self):
        if not self.position:return None
        e=self.position['avg']; return e*(1-1/LEV)/(1-MMR) if self.position['side']=='LONG' else e*(1+1/LEV)/(1+MMR)
    def snapshot(self):
        p=self.last_price; pos=None
        if self.position: pos={'side':self.position['side'],'avg':self.position['avg'],'margin':self.position['margin'],'tranches':self.position['tranches'],'roe':self.roe(p or self.position['avg'])}
        return {'price':p,'balance':self.balance,'equity':self.equity(p),'upnl':self.upnl(p),'signal':self.signal,'position':pos}
    def log(self,actor,action,detail=''):
        self.events.append({'ts':now_ms(),'actor':actor,'action':action,'detail':detail,'snapshot':self.snapshot()}); self.events=self.events[-500:]
    def add_fill(self,side,p,actor='AUTO',kind='MARKET'):
        if self.position and (self.position['side']!=side or self.position.get('profitStopArmed') or self.position.get('tranches',0)>=11):return False
        if not self.position:
            cap=max(0.,self.equity(p))*RISK_PCT; tm=cap/11; tn=tm*LEV
            if tm<=0:return False
            self.position={'side':side,'qty':0.,'margin':0.,'cost':0.,'tranches':0,'avg':p,'opened':now_ms(),'profitStopArmed':False,'profitStopPrice':None,'profitLockStep':0,'campaignMarginCap':cap,'trancheMargin':tm,'trancheNotional':tn,'source':actor}
        tn=self.position['trancheNotional']; self.balance-=tn*(MAKER if kind.startswith('GRID') else TAKER)
        self.position['qty']+=tn/p; self.position['margin']+=self.position['trancheMargin']; self.position['cost']+=tn; self.position['tranches']+=1; self.position['avg']=self.position['cost']/self.position['qty']
        self.log(actor,'FILL',f"{side} {kind} @ {p:.4f}; tranche {self.position['tranches']}/11"); return True
    def grid(self,side,anchor):
        if not self.position or self.position['side']!=side or self.position.get('profitStopArmed'): self.pending=[]; return
        used=max(0,self.position['tranches']-1); rem=max(0,10-used); self.pending=[]
        for n in range(1,rem+1):
            price=anchor*(1-GRID_GAP*n) if side=='LONG' else anchor*(1+GRID_GAP*n)
            self.pending.append({'side':side,'price':price,'gridLevel':used+n})
    def start_campaign(self,side,p,actor='AUTO',reason='START'):
        if self.position:self.close(p,actor,reason+' CLOSE OLD')
        if not self.add_fill(side,p,actor,'MARKET'):return False
        self.grid(side,p); self.stats['campaigns']+=1; self.log(actor,'CAMPAIGN',f"{reason}: {side}; risk cap ${self.position['campaignMarginCap']:.2f}"); return True
    def close(self,p,actor='AUTO',reason='CLOSE'):
        if not self.position:self.pending=[]; return 0.
        gross=self.upnl(p); fee=self.position['qty']*p*TAKER; pnl=gross-fee; side=self.position['side']; tr=self.position['tranches']; self.balance+=pnl; self.position=None; self.pending=[]
        self.log(actor,'CLOSE',f"{reason}: {side}; {tr} tranches; PnL ${pnl:.2f}"); return pnl
    def liquidate(self,p):
        if not self.position:return
        margin=self.position['margin']; side=self.position['side']; self.balance-=margin; self.position=None; self.pending=[]; self.stats['liquidations']+=1; self.log('AUTO','LIQUIDATION',f"{side} @ {p:.4f}; loss margin ${margin:.2f}")
    def set_lock(self,step):
        if not self.position or step<=self.position['profitLockStep']:return
        if self.position['profitLockStep']>0:self.stats['trailMoves']+=1
        gain=PROFIT_LOCK_ROE*step*self.position['margin']; q=self.position['qty']; avg=self.position['avg']
        sp=avg+gain/q if self.position['side']=='LONG' else avg-gain/q
        self.position.update(profitLockStep=step,profitStopArmed=True,profitStopPrice=sp); self.pending=[]; self.log('AUTO','PROFIT_LOCK',f"+{step*10}% ROE @ {sp:.4f}")
    @staticmethod
    def between(a,b,x):return min(a,b)-1e-12<=x<=max(a,b)+1e-12
    def process_segment(self,fr,to,ts):
        if not self.position or fr==to:return
        direction=1 if to>fr else -1; fav=1 if self.position['side']=='LONG' else -1; adv=-fav
        if self.position.get('profitStopArmed') and direction==adv:
            sp=self.position['profitStopPrice']
            if self.between(fr,to,sp) and abs(fr-sp)>1e-10:
                side=self.position['side']; step=self.position['profitLockStep']; self.close(sp,'AUTO',f"PROFIT STOP +{step*10}%"); self.stats['profitStops']+=1
                if self.auto_enabled and not self.manual_override and self.signal==side:self.start_campaign(side,sp,'AUTO','PROFIT RESTART')
                if not self.position:return
                fr=sp; direction=1 if to>fr else -1; fav=1 if self.position['side']=='LONG' else -1; adv=-fav
        if self.position and direction==adv:
            cur=fr
            for _ in range(20):
                if not self.position:return
                lp=self.liq_price(); hits=[o for o in self.pending if self.between(cur,to,o['price'])]; hits.sort(key=lambda x:abs(x['price']-cur)); g=hits[0] if hits else None; gp=g['price'] if g else None; lh=lp is not None and self.between(cur,to,lp)
                if gp is None and not lh:break
                if gp is not None and (not lh or abs(gp-cur)<=abs(lp-cur)):
                    self.pending.remove(g)
                    if not self.add_fill(g['side'],gp,'AUTO',f"GRID L{g['gridLevel']}"):break
                    cur=gp; continue
                self.liquidate(lp); return
        if self.position and direction==(1 if self.position['side']=='LONG' else -1):
            roe=self.roe(to); step=math.floor((roe+1e-9)/PROFIT_LOCK_ROE) if roe>0 else 0
            if step>self.position['profitLockStep']:self.set_lock(step)
    def process_5m(self,b):
        self.last_price=b['c']; pts=[b['o'],b['l'],b['h'],b['c']] if b['c']>=b['o'] else [b['o'],b['h'],b['l'],b['c']]
        for fr,to in zip(pts[:-1],pts[1:]):
            if not self.position:break
            self.process_segment(fr,to,b['ts'])
    def apply_funding(self,ts,rate,p):
        if not self.position:return
        pay=self.position['qty']*p*rate*(-1 if self.position['side']=='LONG' else 1); self.balance+=pay; self.log('AUTO','FUNDING',f"rate {rate:.6%}; PnL ${pay:.4f}")
    def sync_signal(self,d,p):
        new=d.get('state','WARMUP'); old=self.signal; self.signal=new; self.p24=d.get('p24'); self.persist=d.get('persist'); self.model_bar_ts=d.get('bar_ts'); self.last_price=p
        if new!=old:self.log('MODEL','SIGNAL',f"{old} -> {new}; P↑ {self.p24}")
        if not self.auto_enabled or self.manual_override:return
        sig=new if new in ('LONG','SHORT') else None
        if sig is None:
            if self.pending:self.pending=[]; self.log('AUTO','GRID_CANCEL','NEUTRAL: keep position, remove grid')
            self.known_signal=None; self.initialized=True; return
        if not self.initialized:self.initialized=True; self.known_signal=sig; self.start_campaign(sig,p,'AUTO','FIRST SIGNAL') if not self.position else None; return
        if self.known_signal is None:
            self.known_signal=sig
            if not self.position:self.start_campaign(sig,p,'AUTO','AFTER NEUTRAL')
            elif self.position['side']==sig:
                if not self.position.get('profitStopArmed') and not self.pending:self.grid(sig,p); self.log('AUTO','GRID_RESTORE','Same signal after NEUTRAL')
            else:self.close(p,'AUTO',f"NEUTRAL->{sig}"); self.stats['switches']+=1; self.start_campaign(sig,p,'AUTO','SWITCH')
            return
        if self.known_signal!=sig:
            old=self.known_signal; self.known_signal=sig; self.stats['switches']+=1
            if self.position:self.close(p,'AUTO',f"SIGNAL {old}->{sig}")
            self.start_campaign(sig,p,'AUTO','SWITCH'); return
        if not self.position:self.start_campaign(sig,p,'AUTO','AUTO RESUME')
    def manual(self,action,p):
        action=action.upper(); self.last_price=p
        if action in ('LONG','SHORT'):
            self.manual_override=True
            if self.position and self.position['side']==action:self.log('MANUAL','NOOP',f"Already {action}; use ADD")
            else:
                if self.position:self.close(p,'MANUAL',f"FORCE {action}")
                self.start_campaign(action,p,'MANUAL',f"FORCE {action}")
        elif action=='CLOSE':self.manual_override=True; self.close(p,'MANUAL','MANUAL CLOSE')
        elif action=='CANCEL_GRID':self.manual_override=True; self.pending=[]; self.log('MANUAL','GRID_CANCEL','override active')
        elif action=='RESTORE_GRID':
            self.manual_override=True
            if self.position and not self.position.get('profitStopArmed'):self.grid(self.position['side'],p); self.log('MANUAL','GRID_RESTORE','from current price')
        elif action=='ADD':
            self.manual_override=True
            if self.position:self.add_fill(self.position['side'],p,'MANUAL','ADD')
        elif action=='RETURN_AUTO':self.manual_override=False; self.log('MANUAL','RETURN_AUTO','override released'); self.sync_signal({'state':self.signal,'p24':self.p24,'persist':self.persist,'bar_ts':self.model_bar_ts},p)
        elif action=='TOGGLE_AUTO':self.auto_enabled=not self.auto_enabled; self.log('MANUAL','AUTO',f"auto_enabled={self.auto_enabled}")
        else:raise ValueError(action)
    def public(self):
        p=self.last_price; pos=None
        if self.position:pos=dict(self.position); pos['upnl']=self.upnl(p); pos['roe']=self.roe(p or self.position['avg']); pos['liq']=self.liq_price()
        return {'symbol':self.symbol,'price':p,'balance':self.balance,'equity':self.equity(p),'return_pct':(self.equity(p)/self.starting_deposit-1)*100,'signal':self.signal,'p24':self.p24,'persist':self.persist,'model_bar_ts':self.model_bar_ts,'auto_enabled':self.auto_enabled,'manual_override':self.manual_override,'position':pos,'pending':self.pending,'stats':self.stats,'events':list(reversed(self.events[-100:])),'error':self.error}
