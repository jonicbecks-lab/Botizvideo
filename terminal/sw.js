const CACHE_NAME = 'galka-final-integration-shell-v10';
const APP_SHELL = [
  './pro.html',
  './pro.css?v=7',
  './pro.js?v=8',
  './manifest.webmanifest',
  './icons/galka-mark.svg',
  './icons/galka-192.png',
  './icons/galka-512.png',
  './modules/store.js',
  './modules/paper-engine.js',
  './modules/radar-engine.js',
  './modules/backup.js',
  './modules/galka-stats.js',
  './modules/shadow-engine.js',
  'https://unpkg.com/lightweight-charts@5.2.0/dist/lightweight-charts.standalone.production.js',
];

const PATCH_VERSION = 'final-integration-v3.2-l1-cycle-tools';

function replaceOnce(source, oldText, newText, label) {
  if (!source.includes(oldText)) {
    console.error(`Galka integration patch missing: ${label}`);
    return source;
  }
  return source.replace(oldText, newText);
}

function simpleUiSource() {
  return `
function renderSimpleTradeBar(){
  const root=document.getElementById('simpleTradeBar');if(!root)return;
  const input=document.getElementById('simpleGalkaPrice'),status=document.getElementById('simpleTradeStatus'),launch=document.getElementById('simpleGalkaLaunch');
  const symbol=runtime.symbol,ss=store.paper.symbols[symbol],c=ss?.campaign,filled=c?.levels?.filter(level=>level.status==='filled').length||0,total=c?.levels?.length||8,q=runtime.quotes[symbol],pnl=c?.qty&&q?.bid?c.qty*(q.bid-c.averageEntry)-c.entryFees:0,cycles=num(c?.l1Cycles),cyclePnl=num(c?.l1CycleRealizedPnl);
  if(document.activeElement!==input)input.value=c?.vLow?price(c.vLow,symbol):'';
  input.placeholder=symbol.replace('USDT','')+' · цена GALKA';
  if(!c){status.textContent=symbol.replace('USDT','')+' · нет GALKA';status.className='simple-trade-status idle';launch.textContent='Запустить';launch.disabled=false;input.disabled=false;return;}
  const cycleText=cycles?' · L1×'+cycles+' '+signedMoney(cyclePnl):'';
  if(c.qty||filled){status.textContent=symbol.replace('USDT','')+' · '+filled+'/'+total+' · '+signedMoney(pnl)+cycleText;status.className='simple-trade-status '+(pnl>=0?'up-state':'down-state');launch.textContent='Активна';launch.disabled=true;input.disabled=true;return;}
  status.textContent=symbol.replace('USDT','')+' · ждём 0/'+total+cycleText;status.className='simple-trade-status waiting';launch.textContent='Перенести';launch.disabled=false;input.disabled=false;
}
function installSimpleGalkaUi(){
  if(document.getElementById('simpleTradeBar'))return;
  const style=document.createElement('style');style.id='simpleGalkaStyle';style.textContent=\`
    :root{--simple-bar:74px;--bottom-nav:0px}
    .mobile-nav,.side-tabs,#radarBtn,#toggleSidebar,.chart-actions,.chart-health,.ohlc,.radar-legend,.chart-attribution{display:none!important}
    #connectionText{display:none!important}.connection-button{width:36px!important;padding:0!important}
    .terminal-grid{height:calc(100dvh - var(--top) - var(--safe-top) - var(--simple-bar) - var(--safe-bottom))!important;grid-template-columns:1fr!important}
    .drawing-pill{bottom:10px!important}
    .sidebar{display:none!important}
    .sidebar.open{display:flex!important;position:fixed!important;z-index:180!important;left:8px!important;right:8px!important;top:auto!important;bottom:calc(var(--simple-bar) + var(--safe-bottom) + 8px)!important;width:auto!important;height:min(68dvh,680px)!important;max-height:calc(100dvh - var(--top) - var(--simple-bar) - 20px)!important;transform:none!important;border:1px solid var(--line)!important;border-radius:20px!important;box-shadow:var(--shadow)!important;background:var(--panel)!important}
    .sidebar .side-panel{display:none!important}.sidebar .side-panel[data-panel-id="paper"].active{display:block!important}
    .sidebar [data-panel-id="paper"] .training-row{display:none!important}
    .sidebar label:has(#signalMode),.sidebar label:has(#ladderStepPct),.sidebar label:has(#manualDepthPct),.sidebar label:has(#exitMode),.sidebar label:has(#reclaimBufferPct),.sidebar label:has(#trailDistancePct){display:none!important}
    .campaign-metrics span:nth-child(4),.campaign-metrics span:nth-child(5){display:none!important}
    .leftbar{display:none!important;position:fixed!important;z-index:190!important;left:max(8px,var(--safe-left))!important;top:calc(var(--top) + var(--safe-top) + 8px)!important;width:92px!important;height:auto!important;max-height:calc(100dvh - var(--top) - var(--simple-bar) - 24px)!important;padding:6px!important;border:1px solid var(--line)!important;border-radius:16px!important;background:var(--panel-glass)!important;box-shadow:var(--shadow)!important;overflow:auto!important}
    .leftbar.open{display:flex!important}.leftbar button{display:none!important}
    .leftbar [data-tool="cursor"],.leftbar [data-tool="ray"],.leftbar [data-tool="measure"],.leftbar #deleteBtn,.leftbar #clearBtn{display:flex!important;flex-direction:column!important;gap:1px!important;min-height:52px!important;font-size:16px!important}
    .leftbar button small{font-size:9px!important;color:var(--muted)!important;font-weight:750!important}
    .leftbar .tool-separator,.leftbar #magnetBtn,.leftbar #lockBtn,.leftbar #hideDrawingsBtn,.leftbar #undoBtn,.leftbar #redoBtn{display:none!important}
    #simpleTradeBar{position:fixed;z-index:170;left:0;right:0;bottom:0;height:calc(var(--simple-bar) + var(--safe-bottom));padding:8px max(8px,var(--safe-right)) calc(8px + var(--safe-bottom)) max(8px,var(--safe-left));display:grid;grid-template-columns:minmax(96px,.8fr) minmax(118px,1.25fr) minmax(100px,.9fr);gap:7px;align-items:center;border-top:1px solid var(--line);background:color-mix(in srgb,var(--panel) 96%,transparent);backdrop-filter:blur(22px);box-shadow:0 -12px 32px rgba(0,0,0,.28)}
    #simpleTradeBar input,#simpleTradeBar button{height:50px;min-height:50px;border-radius:13px}
    #simpleGalkaPrice{font-size:16px;font-weight:800;font-variant-numeric:tabular-nums}
    #simpleGalkaLaunch{border-color:color-mix(in srgb,var(--galka) 62%,var(--line));background:var(--galka);color:#231504;font-weight:900}
    #simpleGalkaLaunch:disabled{background:var(--panel-2);color:var(--muted)}
    .simple-trade-status{padding:0 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left;font-size:11px;font-weight:850}
    .simple-trade-status.idle{color:var(--muted)}.simple-trade-status.waiting{color:var(--galka);background:var(--galka-dim)}.simple-trade-status.up-state{color:var(--green);background:var(--green-dim)}.simple-trade-status.down-state{color:var(--red);background:var(--red-dim)}
    @media(max-width:480px){.brand-copy{display:none!important}.brand{min-width:auto!important}.live-price{font-size:10px}.simple-trade-status{font-size:9px;padding:0 5px}#simpleTradeBar{grid-template-columns:minmax(82px,.72fr) minmax(108px,1.2fr) minmax(92px,.82fr)}}
  \`;document.head.append(style);
  document.body.insertAdjacentHTML('beforeend','<div id="simpleTradeBar" aria-label="Быстрый запуск GALKA"><button id="simpleTradeStatus" class="simple-trade-status idle" type="button">Нет GALKA</button><input id="simpleGalkaPrice" type="number" step="any" inputmode="decimal" placeholder="Цена GALKA" aria-label="Цена GALKA"><button id="simpleGalkaLaunch" type="button">Запустить</button></div>');
  const input=document.getElementById('simpleGalkaPrice'),launch=document.getElementById('simpleGalkaLaunch'),status=document.getElementById('simpleTradeStatus');
  const submit=()=>{const value=num(input.value);if(!(value>0))return toast('Введи цену GALKA','error');els.manualGalkaPrice.value=String(value);applyManualPrice();};
  launch.onclick=submit;input.onkeydown=event=>{if(event.key==='Enter')submit();};status.onclick=()=>showMobilePanel('paper');
  const toolLabel=els.toggleTools?.querySelector('span:last-child');if(toolLabel)toolLabel.textContent='Инструменты';
  const toolHead=els.leftbar?.querySelector('.tool-rail-head > span');if(toolHead)toolHead.textContent='TOOLS';
  const cursor=els.leftbar?.querySelector('[data-tool="cursor"]');if(cursor)cursor.innerHTML='<span>⌖</span><small>Курсор</small>';
  const ray=els.leftbar?.querySelector('[data-tool="ray"]');if(ray){ray.title='Луч от GALKA';ray.setAttribute('aria-label','Луч от GALKA');ray.innerHTML='<span>↗</span><small>Луч</small>';}
  const measure=els.leftbar?.querySelector('[data-tool="measure"]');if(measure){measure.title='Линейка процентов';measure.setAttribute('aria-label','Линейка процентов');measure.innerHTML='<span>%</span><small>Проценты</small>';}
  if(els.deleteBtn)els.deleteBtn.innerHTML='<span>⌫</span><small>Удалить</small>';
  if(els.clearBtn)els.clearBtn.innerHTML='<span>×</span><small>Очистить</small>';
  renderSimpleTradeBar();
}
`;
}

function patchProSource(originalSource) {
  let source = originalSource;

  const oldMarketSwitch = [
    'function changeSymbol(symbol){',
    "  runtime.selectedDrawing=null;syncDrawingInteraction();runtime.radarSelected=null;runtime.symbol=symbol;store.ui.symbol=symbol;els.symbolSelect.value=symbol;els.watermark.textContent=symbol+' · '+runtime.interval;save();",
    '  loadCurrent(false);renderAll();runtime.mainChart.timeScale().scrollToRealTime();',
    '}',
    'async function changeInterval(interval){',
    "  runtime.selectedDrawing=null;syncDrawingInteraction();runtime.radarSelected=null;runtime.interval=interval;store.ui.interval=interval;els.intervalSelect.value=interval;save();connectWs();await loadCurrent();renderAll();",
    '}',
  ].join('\n');

  const newMarketSwitch = [
    'function autoCenterActiveMarket({fitTime=false}={}){',
    '  const chart=runtime.mainChart;if(!chart)return;',
    "  const scale=chart.priceScale('right');",
    '  scale.applyOptions({autoScale:true});',
    "  els.autoScaleBtn?.classList.add('active');",
    '  if(fitTime)chart.timeScale().fitContent();else chart.timeScale().scrollToRealTime();',
    '  requestAnimationFrame(()=>{scale.applyOptions({autoScale:true});if(!fitTime)chart.timeScale().scrollToRealTime();});',
    '  setTimeout(()=>scale.applyOptions({autoScale:true}),120);',
    '}',
    'async function changeSymbol(symbol){',
    "  runtime.selectedDrawing=null;syncDrawingInteraction();runtime.radarSelected=null;runtime.symbol=symbol;store.ui.symbol=symbol;els.symbolSelect.value=symbol;els.watermark.textContent=symbol+' · '+runtime.interval;save();",
    "  try{runtime.priceSeries?.setData([]);}catch(_){}",
    '  await loadCurrent(false);',
    '  renderAll();',
    '  autoCenterActiveMarket();',
    '}',
    'async function changeInterval(interval){',
    "  runtime.selectedDrawing=null;syncDrawingInteraction();runtime.radarSelected=null;runtime.interval=interval;store.ui.interval=interval;els.intervalSelect.value=interval;save();connectWs();",
    "  try{runtime.priceSeries?.setData([]);}catch(_){}",
    '  await loadCurrent(false);',
    '  renderAll();',
    '  autoCenterActiveMarket();',
    '}',
  ].join('\n');

  source = replaceOnce(source, oldMarketSwitch, newMarketSwitch, 'symbol and interval auto-center');
  source = replaceOnce(source,"els.fitBtn.onclick=()=>runtime.mainChart.timeScale().fitContent();els.latestBtn.onclick=()=>runtime.mainChart.timeScale().scrollToRealTime();","els.fitBtn.onclick=()=>autoCenterActiveMarket({fitTime:true});els.latestBtn.onclick=()=>autoCenterActiveMarket();",'fit and latest autoscale');
  source = replaceOnce(source,"  tool:'cursor',drawingStart:null,drawingPreview:null,selectedDrawing:null,drawingEdit:null,longPressTimer:null,undo:[],redo:[],","  tool:'cursor',drawingStart:null,drawingPreview:null,drawingAwaitSecond:false,selectedDrawing:null,drawingEdit:null,longPressTimer:null,undo:[],redo:[],",'two-tap drawing state');
  source = replaceOnce(source,`function setTool(tool){
  runtime.tool=tool;runtime.drawingStart=null;runtime.drawingPreview=null;
  if(tool!=='cursor')runtime.selectedDrawing=null;
  els.leftbar.querySelectorAll('[data-tool]').forEach(b=>b.classList.toggle('active',b.dataset.tool===tool));
  const manualTool=tool==='manualGalka'||tool==='manualMove';
  els.drawingCanvas.classList.toggle('drawing',manualTool||(tool!=='cursor'&&tool!=='crosshair'&&!store.ui.drawingsLocked));
  els.drawingCanvas.classList.toggle('dragging-level',tool==='manualMove');
  syncDrawingInteraction();
  if(tool==='crosshair')runtime.mainChart.applyOptions({crosshair:{mode:LWC.CrosshairMode.Normal}});
  drawAll();
}`,`function setTool(tool){
  runtime.tool=tool;runtime.drawingStart=null;runtime.drawingPreview=null;runtime.drawingAwaitSecond=false;
  if(tool!=='cursor')runtime.selectedDrawing=null;
  els.leftbar.querySelectorAll('[data-tool]').forEach(b=>b.classList.toggle('active',b.dataset.tool===tool));
  const manualTool=tool==='manualGalka'||tool==='manualMove';
  els.drawingCanvas.classList.toggle('drawing',manualTool||(tool!=='cursor'&&tool!=='crosshair'&&!store.ui.drawingsLocked));
  els.drawingCanvas.classList.toggle('dragging-level',tool==='manualMove');
  syncDrawingInteraction();
  if(tool==='crosshair')runtime.mainChart.applyOptions({crosshair:{mode:LWC.CrosshairMode.Normal}});
  drawAll();
}`,'reset two-tap drawing state');
  source = replaceOnce(source,`function drawingDown(e){
  const manualTool=runtime.tool==='manualGalka'||runtime.tool==='manualMove';
  if(runtime.tool==='cursor'){if(runtime.selectedDrawing)beginDrawingEdit(e);return;}
  if(runtime.tool==='crosshair'||(store.ui.drawingsLocked&&!manualTool))return;
  const p=eventPoint(e,!manualTool);if(!p)return;
  if(runtime.tool==='manualGalka'){setManualGalka(p);return;}
  if(runtime.tool==='manualMove'){beginManualMove(p,e);return;}
  if(runtime.tool==='horizontal'||runtime.tool==='vertical'){addDrawing({type:runtime.tool,p1:p});return;}
  if(runtime.tool==='text'){const text=prompt('Текст на графике:');if(text)addDrawing({type:'text',p1:p,text,fontSize:14});return;}
  runtime.drawingStart=p;runtime.drawingPreview={type:runtime.tool,p1:p,p2:p,color:els.drawingColor?.value||COLORS.blue,width:num(els.drawingWidth?.value,2)};els.drawingCanvas.setPointerCapture(e.pointerId);
}`,`function drawingDown(e){
  const manualTool=runtime.tool==='manualGalka'||runtime.tool==='manualMove';
  if(runtime.tool==='cursor'){if(runtime.selectedDrawing)beginDrawingEdit(e);return;}
  if(runtime.tool==='crosshair'||(store.ui.drawingsLocked&&!manualTool))return;
  const p=eventPoint(e,!manualTool);if(!p)return;
  if(runtime.tool==='manualGalka'){setManualGalka(p);return;}
  if(runtime.tool==='manualMove'){beginManualMove(p,e);return;}
  if(runtime.tool==='horizontal'||runtime.tool==='vertical'){addDrawing({type:runtime.tool,p1:p});return;}
  if(runtime.tool==='text'){const text=prompt('Текст на графике:');if(text)addDrawing({type:'text',p1:p,text,fontSize:14});return;}
  if(runtime.tool==='ray'||runtime.tool==='measure'){
    if(!runtime.drawingAwaitSecond){runtime.drawingStart=p;runtime.drawingAwaitSecond=true;runtime.drawingPreview={type:runtime.tool,p1:p,p2:p,color:els.drawingColor?.value||COLORS.blue,width:num(els.drawingWidth?.value,2)};drawAll();toast(runtime.tool==='ray'?'Луч: коснись второй точки направления':'Линейка: коснись второй точки','alert');return;}
    const type=runtime.tool,start=runtime.drawingStart;runtime.drawingAwaitSecond=false;runtime.drawingStart=null;runtime.drawingPreview=null;if(start)addDrawing({type,p1:start,p2:p});setTool('cursor');toast(type==='ray'?'Луч добавлен':'Измерение добавлено','alert');return;
  }
  runtime.drawingStart=p;runtime.drawingPreview={type:runtime.tool,p1:p,p2:p,color:els.drawingColor?.value||COLORS.blue,width:num(els.drawingWidth?.value,2)};els.drawingCanvas.setPointerCapture(e.pointerId);
}`,'two-tap ray and measure');
  source = replaceOnce(source,"  if(!runtime.drawingStart)return;const p=eventPoint(e);\n  if(p)addDrawing({type:runtime.tool,p1:runtime.drawingStart,p2:p});","  if(runtime.drawingAwaitSecond)return;\n  if(!runtime.drawingStart)return;const p=eventPoint(e);\n  if(p)addDrawing({type:runtime.tool,p1:runtime.drawingStart,p2:p});",'keep first drawing tap alive');
  source = replaceOnce(source,"els.leftbar.onclick=e=>{const b=e.target.closest('[data-tool]');if(b)setTool(b.dataset.tool);};","els.leftbar.onclick=e=>{const b=e.target.closest('[data-tool]');if(!b)return;const tool=b.dataset.tool;setTool(tool);if(tool==='ray'||tool==='measure'){els.leftbar.classList.remove('open');syncMobileOverlay();toast(tool==='ray'?'Луч: коснись начала, потом направления':'Линейка: коснись начала, потом конца','alert');}};",'drawing tool activation');
  source = replaceOnce(source,'/* Rendering and UI */',`${simpleUiSource()}\n/* Rendering and UI */`,'simple UI functions');
  source = replaceOnce(source,"function renderAll(){renderWatchlist();renderPaper();renderObjects();renderAlerts();renderTemplates();renderRadar();renderLab();renderActivity();renderDiagnostics();renderSessionHealth();renderTicker();}","function renderAll(){renderWatchlist();renderPaper();renderObjects();renderAlerts();renderTemplates();renderRadar();renderLab();renderActivity();renderDiagnostics();renderSessionHealth();renderTicker();renderSimpleTradeBar();}",'simple status render');
  source = replaceOnce(source,"if(p)markers.push({time:p.vLowTime,position:'belowBar',color:p.source==='manual'?COLORS.orange:COLORS.blue,shape:'circle',text:p.source==='manual'?'GALKA':'V-low'});","if(p&&p.source!=='manual')markers.push({time:p.vLowTime,position:'belowBar',color:COLORS.blue,shape:'circle',text:'V-low'});",'remove manual GALKA point marker');
  source = replaceOnce(source,"if(c.reclaimPrice)addPaperLine(c.reclaimPrice,COLORS.purple,'RECLAIM');","if(c.exitMode!=='target'&&c.reclaimPrice)addPaperLine(c.reclaimPrice,COLORS.purple,'RECLAIM');",'hide reclaim in target mode');
  source = replaceOnce(source,"else if(d.type==='ray'){const dx=b.x-a.x,dy=b.y-a.y,t=dx===0?0:(w-a.x)/dx;line(ctx,a,{x:w,y:a.y+dy*t});}","else if(d.type==='ray'){const dx=b.x-a.x,dy=b.y-a.y,t=dx===0?0:(w-a.x)/dx,end={x:w,y:a.y+dy*t};line(ctx,a,end);label(ctx,`GALKA ${price(d.p1.price)}`,Math.max(0,Math.min(a.x,w-112)),a.y,d.color||COLORS.blue);}",'ray GALKA label');
  source = replaceOnce(source,"updateMarkers();setTool('cursor');showMobilePanel('paper');","updateMarkers();setTool('cursor');renderSimpleTradeBar();closeMobileOverlays();",'keep chart open after launch');
  source = replaceOnce(source,"s.signalMode=els.signalMode.value==='auto'?'auto':'manual';s.ladderStepPct=clamp(num(els.ladderStepPct.value,.15),.05,2);s.manualDepthPct=clamp(num(els.manualDepthPct.value,1.5),s.ladderStepPct,10);s.exitMode=els.exitMode.value==='target'?'target':'trail';s.reclaimBufferPct=clamp(num(els.reclaimBufferPct.value,.10),0,5);","s.signalMode='manual';s.ladderStepPct=.15;s.manualDepthPct=2;s.exitMode='target';s.reclaimBufferPct=0;",'fixed simple paper settings');
  source = replaceOnce(source,"campaign?.trailArmed?`Stop ${price(campaign.trailStop,item)}`:campaign?`Reclaim ${price(campaign.reclaimPrice,item)}`:'Можно поставить уровень'","campaign?`Цель ${price(campaign.vLow,item)}${num(campaign.l1Cycles)?` · L1×${num(campaign.l1Cycles)}`:''}`:'Можно поставить уровень'",'paper target and L1 cycle copy');

  const oldCloseCampaign = `function closeCampaign(symbol,rawExit,reason,{atMs=Date.now(),recovered=false,deferRender=false}={}){
  const ss=store.paper.symbols[symbol],c=ss.campaign;if(!c?.qty)return;
  if(store.paper.trades.some(trade=>trade.campaignId===c.campaignId)){ss.campaign=null;save();return;}
  const st=store.paper.settings,exit=reason==='v_low_target'?rawExit:rawExit*(1-st.slippage),exitNotional=c.qty*exit,exitFee=exitNotional*(reason==='v_low_target'?st.makerFee:st.takerFee),gross=c.qty*(exit-c.averageEntry),net=gross-c.entryFees-exitFee;
  const exitTime=new Date(num(atMs,Date.now())).toISOString();
  const trade={tradeId:'P'+String(store.paper.trades.length+1).padStart(6,'0'),campaignId:c.campaignId,patternId:c.patternId,symbol,side:'long',entryTime:c.levels.find(x=>x.status==='filled')?.fillTime||c.createdAt,exitTime,averageEntry:c.averageEntry,exitPrice:exit,qty:c.qty,filledNotional:c.filledNotional,levelsFilled:c.levels.filter(x=>x.status==='filled').length,levelsTotal:c.levels.length,grossPnl:gross,fees:c.entryFees+exitFee,netPnl:net,reason,vLow:c.vLow,exitMode:c.exitMode||'target',trailActivatedAt:c.trailActivatedAt||null,trailHigh:c.trailHigh||null,trailStop:c.trailStop||null,executionSource:recovered?'recovery':'live',recoveryPolicy:recovered?RECOVERY_PATH_POLICY:null};
  if(c.trainingExampleId){const x=store.training.manualExamples.find(v=>v.id===c.trainingExampleId);if(x)Object.assign(x,{status:'closed',exitTime:trade.exitTime,exitPrice:trade.exitPrice,netPnl:trade.netPnl,reason:trade.reason,levelsFilled:trade.levelsFilled,levelsTotal:trade.levelsTotal,trailHigh:trade.trailHigh});}
  store.paper.trades.push(trade);store.paper.realizedPnl+=net;store.paper.fees+=trade.fees;ss.campaign=null;if(ss.pattern?.patternId===c.patternId)ss.pattern.status=reason;
  logActivity(net>=0?'paper':'risk',\`\${symbol.replace('USDT','')}: paper-сделка закрыта \${signedMoney(net)}\`,{reason,tradeId:trade.tradeId,recovered},trade.exitTime);save();if(!deferRender){renderPaper();renderActivity();updateMarkers();}
}`;
  const newCloseCampaign = `function recordL1Cycle(symbol,event,{atMs=Date.now(),recovered=false,deferRender=false}={}){
  const ss=store.paper.symbols[symbol],c=ss.campaign;if(!c||!event)return null;
  const cycle=num(event.cycle,num(c.l1Cycles,1)),cycleKey=c.campaignId+':L1:'+cycle;if(store.paper.trades.some(trade=>trade.cycleKey===cycleKey))return null;
  const exitTime=new Date(num(atMs,Date.now())).toISOString(),fees=num(event.entryFees)+num(event.exitFee),net=num(event.netPnl);
  const trade={tradeId:'P'+String(store.paper.trades.length+1).padStart(6,'0'),campaignId:c.campaignId,patternId:c.patternId,cycleKey,cycleOnly:true,finalCampaign:false,l1Cycle:cycle,symbol,side:'long',entryTime:event.fillTime||c.createdAt,exitTime,averageEntry:event.averageEntry,exitPrice:event.price,qty:event.qty,filledNotional:event.filledNotional,levelsFilled:1,levelsTotal:c.levels.length,grossPnl:event.grossPnl,fees,netPnl:net,reason:'l1_cycle_target',vLow:c.vLow,exitMode:'target',executionSource:recovered?'recovery':'live',recoveryPolicy:recovered?RECOVERY_PATH_POLICY:null};
  if(c.trainingExampleId){const x=store.training.manualExamples.find(v=>v.id===c.trainingExampleId);if(x){x.l1Cycles=cycle;x.l1CyclePnl=num(x.l1CyclePnl)+net;x.updatedAt=exitTime;}}
  store.paper.trades.push(trade);store.paper.realizedPnl+=net;store.paper.fees+=fees;logActivity('paper',\`\${symbol.replace('USDT','')}: L1 цикл \${cycle} закрыт \${signedMoney(net)} и перезаряжен\`,{reason:trade.reason,tradeId:trade.tradeId,recovered,cycle},trade.exitTime);save();if(!deferRender){renderPaper();renderActivity();updateMarkers();renderSimpleTradeBar();}return trade;
}
function closeCampaign(symbol,rawExit,reason,{atMs=Date.now(),recovered=false,deferRender=false}={}){
  const ss=store.paper.symbols[symbol],c=ss.campaign;if(!c?.qty)return;
  if(store.paper.trades.some(trade=>trade.campaignId===c.campaignId&&trade.finalCampaign===true)){ss.campaign=null;save();return;}
  const st=store.paper.settings,exit=reason==='v_low_target'?rawExit:rawExit*(1-st.slippage),exitNotional=c.qty*exit,exitFee=exitNotional*(reason==='v_low_target'?st.makerFee:st.takerFee),gross=c.qty*(exit-c.averageEntry),net=gross-c.entryFees-exitFee;const exitTime=new Date(num(atMs,Date.now())).toISOString();
  const trade={tradeId:'P'+String(store.paper.trades.length+1).padStart(6,'0'),campaignId:c.campaignId,patternId:c.patternId,cycleOnly:false,finalCampaign:true,symbol,side:'long',entryTime:c.levels.find(x=>x.status==='filled')?.fillTime||c.createdAt,exitTime,averageEntry:c.averageEntry,exitPrice:exit,qty:c.qty,filledNotional:c.filledNotional,levelsFilled:c.levels.filter(x=>x.status==='filled').length,levelsTotal:c.levels.length,grossPnl:gross,fees:c.entryFees+exitFee,netPnl:net,reason,vLow:c.vLow,exitMode:c.exitMode||'target',l1Cycles:num(c.l1Cycles),l1CycleRealizedPnl:num(c.l1CycleRealizedPnl),trailActivatedAt:c.trailActivatedAt||null,trailHigh:c.trailHigh||null,trailStop:c.trailStop||null,executionSource:recovered?'recovery':'live',recoveryPolicy:recovered?RECOVERY_PATH_POLICY:null};
  if(c.trainingExampleId){const x=store.training.manualExamples.find(v=>v.id===c.trainingExampleId);if(x)Object.assign(x,{status:'closed',exitTime:trade.exitTime,exitPrice:trade.exitPrice,netPnl:num(x.l1CyclePnl)+trade.netPnl,reason:trade.reason,levelsFilled:trade.levelsFilled,levelsTotal:trade.levelsTotal,l1Cycles:trade.l1Cycles,trailHigh:trade.trailHigh});}
  store.paper.trades.push(trade);store.paper.realizedPnl+=net;store.paper.fees+=trade.fees;ss.campaign=null;if(ss.pattern?.patternId===c.patternId)ss.pattern.status=reason;logActivity(net>=0?'paper':'risk',\`\${symbol.replace('USDT','')}: GALKA завершена \${signedMoney(net)} · L1 циклов \${num(c.l1Cycles)}\`,{reason,tradeId:trade.tradeId,recovered,l1Cycles:num(c.l1Cycles)},trade.exitTime);save();if(!deferRender){renderPaper();renderActivity();updateMarkers();renderSimpleTradeBar();}
}`;
  source = replaceOnce(source,oldCloseCampaign,newCloseCampaign,'L1 cycle ledger and final close');
  source = replaceOnce(source,"const summary={reason,policy:RECOVERY_PATH_POLICY,symbols:symbols.length,candles:0,boundaryCandles:0,fills:0,trailingArmed:0,trailingRaised:0,closed:0,expired:0,truncated:0,failures:[]};","const summary={reason,policy:RECOVERY_PATH_POLICY,symbols:symbols.length,candles:0,boundaryCandles:0,fills:0,l1Cycles:0,trailingArmed:0,trailingRaised:0,closed:0,expired:0,truncated:0,failures:[]};",'recovery L1 cycle summary');
  source = replaceOnce(source,"    summary.fills+=result.events.filter(event=>event.type==='level_filled').length;\n    summary.trailingArmed+=result.events.filter(event=>event.type==='trailing_armed').length;\n    summary.trailingRaised+=result.events.filter(event=>event.type==='trailing_raised').length;\n    if(result.lastCloseTime!=null)state.lastRecoveredCloseAt=Math.max(num(state.lastRecoveredCloseAt,0),num(result.lastCloseTime));","    summary.fills+=result.events.filter(event=>event.type==='level_filled').length;\n    const recoveredCycles=result.events.filter(event=>event.type==='l1_cycle_closed');summary.l1Cycles+=recoveredCycles.length;for(const event of recoveredCycles)recordL1Cycle(symbol,event,{atMs:event.atMs,recovered:true,deferRender:true});\n    summary.trailingArmed+=result.events.filter(event=>event.type==='trailing_armed').length;\n    summary.trailingRaised+=result.events.filter(event=>event.type==='trailing_raised').length;\n    if(result.lastCloseTime!=null)state.lastRecoveredCloseAt=Math.max(num(state.lastRecoveredCloseAt,0),num(result.lastCloseTime));",'record recovered L1 cycles');
  source = replaceOnce(source,"const message=`Paper replay: ${summary.candles} × 1m, fills ${summary.fills}, exits ${summary.closed}, boundary ${summary.boundaryCandles}`;","const message=`Paper replay: ${summary.candles} × 1m, fills ${summary.fills}, L1 cycles ${summary.l1Cycles}, exits ${summary.closed}, boundary ${summary.boundaryCandles}`;",'recovery message cycles');
  source = replaceOnce(source,"      if(event.type==='level_filled')logActivity('paper',`${symbol.replace('USDT','')}: L${event.level} исполнена`,{price:event.price,recovered:restored},eventAt);\n      if(event.type==='trailing_armed'){logActivity('paper',`${symbol.replace('USDT','')}: trailing активирован`,{stop:event.stop,recovered:restored},eventAt);if(symbol===runtime.symbol&&!suppressRender)toast(`${symbol}: trailing активирован, стоп ${price(event.stop,symbol)}`,'alert');}","      if(event.type==='level_filled')logActivity('paper',`${symbol.replace('USDT','')}: L${event.level} исполнена`,{price:event.price,recovered:restored},eventAt);\n      if(event.type==='l1_cycle_closed'){recordL1Cycle(symbol,event,{atMs:nowMs,recovered:restored,deferRender:true});if(symbol===runtime.symbol&&!suppressRender)toast(`${symbol.replace('USDT','')}: L1 закрыта ${signedMoney(event.netPnl)} и поставлена снова`,'alert');}\n      if(event.type==='trailing_armed'){logActivity('paper',`${symbol.replace('USDT','')}: trailing активирован`,{stop:event.stop,recovered:restored},eventAt);if(symbol===runtime.symbol&&!suppressRender)toast(`${symbol}: trailing активирован, стоп ${price(event.stop,symbol)}`,'alert');}",'live L1 cycle ledger');
  source = replaceOnce(source,'/* Init */','installSimpleGalkaUi();\nsetInterval(renderSimpleTradeBar,1000);\n\n/* Init */','simple UI init');
  return source;
}

async function servePatchedPro(request) {
  let response;
  try {response=await fetch(request,{cache:'no-store'});if(!response.ok)throw new Error(`HTTP ${response.status}`);} catch(error){response=await caches.match(request);if(!response)throw error;}
  const source=await response.text(),patched=patchProSource(source),headers=new Headers(response.headers);headers.set('content-type','text/javascript; charset=utf-8');headers.set('cache-control','no-store');headers.set('x-galka-patch',PATCH_VERSION);return new Response(patched,{status:200,statusText:'OK',headers});
}

self.addEventListener('install',(event)=>{event.waitUntil(caches.open(CACHE_NAME).then((cache)=>cache.addAll(APP_SHELL)));self.skipWaiting();});
self.addEventListener('activate',(event)=>{event.waitUntil(caches.keys().then((keys)=>Promise.all(keys.filter((key)=>key!==CACHE_NAME).map((key)=>caches.delete(key)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',(event)=>{const request=event.request;if(request.method!=='GET')return;const url=new URL(request.url);
  // Market data must always be live. The service worker never runs the paper engine.
  if(url.hostname==='fapi.binance.com'||url.hostname==='fstream.binance.com')return;
  if(url.origin===self.location.origin&&url.pathname.endsWith('/terminal/pro.js')){event.respondWith(servePatchedPro(request));return;}
  if(request.mode==='navigate'){event.respondWith(fetch(request).then((response)=>{const copy=response.clone();caches.open(CACHE_NAME).then((cache)=>cache.put('./pro.html',copy));return response;}).catch(()=>caches.match('./pro.html')));return;}
  const isLocalAppAsset=url.origin===self.location.origin&&['script','style','worker'].includes(request.destination);
  if(isLocalAppAsset){event.respondWith(fetch(request,{cache:'no-store'}).then((response)=>{if(response.ok){const copy=response.clone();caches.open(CACHE_NAME).then((cache)=>cache.put(request,copy));}return response;}).catch(()=>caches.match(request)));return;}
  event.respondWith(caches.match(request).then((cached)=>{const refresh=fetch(request).then((response)=>{if(response.ok||response.type==='opaque'){const copy=response.clone();caches.open(CACHE_NAME).then((cache)=>cache.put(request,copy));}return response;}).catch(()=>cached);return cached||refresh;}));
});
