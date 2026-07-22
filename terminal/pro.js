import {
  PAPER_RECOVERY_POLICY,
  STORAGE_KEY,
  SYMBOLS,
  appendActivity,
  createDefaultStore,
  deepMerge,
  loadStore,
  migrateStore,
  saveStore,
} from './modules/store.js';
import {
  ACTIVE_CAMPAIGN_STATUSES,
  RECOVERY_PATH_POLICY,
  campaignLadder as buildCampaignLadder,
  createCampaign as createPaperCampaign,
  moveManualCampaign,
  paperPortfolioSnapshot,
  previewCampaign,
  processCampaignQuote,
  recoveryCandlePath,
  recalculateCampaign,
  validateRecoveryCandleRange,
} from './modules/paper-engine.js';
import {
  filterRadarCandidates,
  radarFeatureAt as computeRadarFeatureAt,
  scanRadarCandidates as computeRadarCandidates,
} from './modules/radar-engine.js';
import {
  createBackupSnapshot,
  summarizeBackupSnapshot,
  validateBackupSnapshot,
} from './modules/backup.js';
import {
  aggregateFinalOos,
  blockSummary,
  classifyGalka,
  galkaInsight,
  loadGalkaStatsPack,
  probabilityCliff,
  researchFeaturesAt,
} from './modules/galka-stats.js';
import {
  processShadowBook,
  labelShadowCandidate,
  registerShadowCandidate,
  setShadowEnabled,
  summarizeShadow,
} from './modules/shadow-engine.js';

(()=>{
'use strict';

const LWC = window.LightweightCharts;
const VERSION = 'pro-v2.1.0-galka-lab';
const INTERVALS = ['1m','3m','5m','15m','30m','1h','4h','1d'];
const REST = 'https://fapi.binance.com';
const WS_BASE = 'wss://fstream.binance.com/stream?streams=';
const PRE_RESTORE_BACKUP_KEY = `${STORAGE_KEY}-pre-restore-backup`;
const RECOVERY_CANDLE_MS = 60_000;
const RECOVERY_CHECKPOINT_MS = 5_000;
const RECOVERY_MAX_BUFFER = 50_000;
const COLORS = {green:'#089981',red:'#f23645',blue:'#2962ff',orange:'#ff9800',purple:'#9c6ade',cyan:'#26c6da',gray:'#8b93a4'};
const GALKA_TYPE_COLORS={'Deep capitulation':'#ef5da8','Fast V':'#16c7a3','Multi-test':'#7c6cff','Rounded recovery':'#f2a93b'};
const SHADOW_SETTINGS={makerFee:.0002,takerFee:.0005,slippage:.0002,reclaimBufferPct:.1,trailDistancePct:.75};
const $ = id => document.getElementById(id);
const els = Object.fromEntries([
  'symbolSelect','intervalSelect','chartTypeSelect','indicatorBtn','alertBtn','replayBtn','snapshotBtn','fullscreenBtn',
  'toggleTools','toggleSidebar','closeTools','leftbar','sidebar','connectionButton','connectionDot','connectionText','tickerText','themeBtn','clock','radarBtn','radarLegend',
  'ohlc','zoomOut','zoomIn','autoScaleBtn','scaleMode','goDateBtn','fitBtn','latestBtn','chartStack','chartMainWrap','chartHealth','chartHealthText','levelCluster',
  'mainChart','drawingCanvas','watermark','loading','toast','indicatorPane','paneTitle','oscChart','closePane',
  'replayPanel','replayBack','replayPlay','replayStep','replaySlider','replayLabel','replayExit','replayMarkGalka','replayReveal',
  'watchlist','refreshBtn','compareSelect','equity','openPnl','realizedPnl','marginUsed','botTitle','botState',
  'campaignCard','fillsCount','ladderSummary','levelsList','paperPortfolioCards','paperStreamBadge','paperNavBadge','manualGalkaBtn','manualGalkaPrice','applyManualGalkaPrice','moveManualGalka','manualLevelHint','cancelManualGalka','exportManualExamples','manualExamplesCount','closeSidebarSheet','sheetBackdrop','sheetHandle','sheetTitle','sheetSubtitle','startingBalance','leverage','symbolNotional','maxHours','signalMode','ladderStepPct','manualDepthPct','exitMode','reclaimBufferPct','trailDistancePct','savePaperSettings',
  'resetPaper','exportTrades','tradeHistory','objectsList','exportWorkspace','importWorkspace','templateName','saveTemplate',
  'templatesList','alertSymbol','alertDirection','alertPrice','alertNote','createAlert','alertsList',
  'dwTime','dwOpen','dwHigh','dwLow','dwClose','dwVolume','dwAtr','dwChange','diagnostics',
  'indicatorModal','indicatorSearch','indicatorList','goDateModal','goDateInput','goDateApply','chartActionBtn','chartActionMenu','quickSetGalka','quickExactGalka','quickMoveGalka','quickRadar','quickLevels',
  'magnetBtn','lockBtn','hideDrawingsBtn','undoBtn','redoBtn','deleteBtn','clearBtn','drawingColor','drawingWidth','drawingDash','duplicateDrawing','lockSelectedDrawing','openDrawingProperties',
  'radarPanelToggle','radarFilters','radarMinScore','radarMinScoreValue','radarVisibleOnly','radarContext','radarCount','radarCandidatesList','radarDetail','radarScore','radarStrength','radarCandidateTime','radarDropAtr','radarRecovery','radarBalance','radarSharpness','radarCloseLift','radarManualMatch','radarExplanation','radarPositive','radarNegative','radarPrev','radarNext',
  'radarStatsEvidence','labPackStatus','labSymbol','labInterval','labType','labWindow','labProfile','labRegime','labSafetyBanner','labDataMeta','labOosMetrics','labDepthMetrics','labHistogramCaption','labDepthHistogram','labDepthCurve','labSurvival','labTypeFrequency','labTypePerformance','labHeatmap','labProfileLevels','labGridCards','labStopCards','labExitCards','labDrift','labCliff','labRegimes','labPortability','shadowToggle','shadowStatus','shadowMetrics','shadowRecords','exportShadow','openWatchlist',
  'sessionStatus','sessionStatusDot','sessionWs','sessionQuoteAge','sessionTab','sessionEngineGap','sessionRecovery','exportSnapshot','importSnapshot','lastBackupText','activityLog','clearActivity','startOnboarding',
  'pretradeModal','previewGalka','previewSymbol','previewFirst','previewLast','previewCount','previewNotional','previewAverage','previewPnl','confirmPretrade',
  'restoreModal','restoreSummary','confirmRestore','drawingPropertiesModal','propertyColor','propertyWidth','propertyDash','propertyDuplicate','propertyLock','propertyDelete',
  'onboardingModal','onboardingProgress','onboardingVisual','onboardingStep','onboardingTitle','onboardingText','skipOnboarding','nextOnboarding'
].map(id=>[id,$(id)]));

let store=loadStore();
const defaultStore=createDefaultStore;
function save(){saveStore(store);}
function logActivity(type,message,meta={},at=nowIso()){appendActivity(store,{type,message,meta},at);}
if(num(store.paper.settings.symbolNotional)===3333.33&&!store.paper.trades.length&&!SYMBOLS.some(x=>store.paper.symbols[x].campaign)){store.paper.settings.symbolNotional=400;save();}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function clamp(v,a,b){return Math.max(a,Math.min(b,v));}
function num(v,d=0){const n=Number(v);return Number.isFinite(n)?n:d;}
function money(v){return '$'+num(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});}
function signedMoney(v){return (num(v)>=0?'+':'')+money(v);}
function price(v,s=runtime.symbol){
  if(v==null||!Number.isFinite(Number(v))) return '—';
  const p=s==='SOLUSDT'?4:2; return Number(v).toFixed(p);
}
function fmtTime(ts){return ts?new Date(ts*1000).toLocaleString('ru-RU',{year:'2-digit',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';}
function nowIso(){return new Date().toISOString();}
function key(symbol=runtime.symbol,interval=runtime.interval){return symbol+'|'+interval;}
function toast(text,kind=''){
  els.toast.textContent=text;els.toast.className='toast '+kind;clearTimeout(runtime.toastTimer);
  runtime.toastTimer=setTimeout(()=>els.toast.classList.add('hidden'),3500);
}
function download(name,blob){
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},1000);
}
function csvEscape(v){const s=String(v??'');return /[",\n]/.test(s)?'"'+s.replaceAll('"','""')+'"':s;}
function displayNumber(v){return v!==null&&v!==''&&Number.isFinite(Number(v));}
function pct(v,d=1){return displayNumber(v)?`${(Number(v)*100).toFixed(d)}%`:'—';}
function pctPoint(v,d=2){return displayNumber(v)?`${Number(v).toFixed(d)}%`:'—';}
function signedPctPoint(v,d=3){return displayNumber(v)?`${Number(v)>=0?'+':''}${Number(v).toFixed(d)}%`:'—';}
function compactNumber(v){return displayNumber(v)?Number(v).toLocaleString('ru-RU'):'—';}

const runtime={
  symbol:store.ui.symbol,interval:store.ui.interval,chartType:store.ui.chartType,
  mainChart:null,priceSeries:null,markerApi:null,compareSeries:null,paperLines:[],
  indicatorSeries:[],volumeSeries:null,oscChart:null,oscSeries:[],
  candles:new Map(),botCandles:Object.fromEntries(SYMBOLS.map(s=>[s,[]])),
  quotes:Object.fromEntries(SYMBOLS.map(s=>[s,{bid:null,ask:null,last:null,open24:null,change24:null,updated:null,marketAt:null}])),
  ws:null,reconnect:null,wsAttempt:0,loading:new Set(),syncing:false,connectionState:'idle',lastQuoteAt:null,lastEngineAt:null,disconnectedAt:null,recovering:false,lastCatchupAt:null,recoveryPromise:null,recoveryQuoteBuffer:[],recoveryBufferOverflow:false,recoveryOverflowAt:null,lastRecoverySummary:null,lastRecoveryPersistAt:0,
  tool:'cursor',drawingStart:null,drawingPreview:null,drawingAwaitSecond:false,selectedDrawing:null,drawingEdit:null,longPressTimer:null,undo:[],redo:[],
  dpr:window.devicePixelRatio||1,toastTimer:null,lastCrosshair:null,
  replay:{active:false,index:0,playing:false,timer:null,source:null,pendingLabel:null,revealed:false},
  hiddenLiveUpdates:false,manualDrag:false,manualDragOriginal:null,pendingManual:null,pendingRestore:null,radarCandidates:[],radarSelected:null,radarRangeTimer:null,onboardingIndex:0,sheetGesture:null,
  galkaStats:null,galkaStatsPromise:null,galkaStatsError:null,lastShadowPersistAt:0,shadowDirty:false
};

function chartColors(){
  const light=store.ui.theme==='light';
  return {
    background:light?'#ffffff':'#0b0e13',text:light?'#4b5565':'#a5adbd',
    grid:light?'#e8ebf0':'#1c222d',border:light?'#d0d5dd':'#2a303d'
  };
}
function createMainChart(){
  const c=chartColors();
  runtime.mainChart=LWC.createChart(els.mainChart,{
    autoSize:true,
    layout:{background:{type:'solid',color:c.background},textColor:c.text,fontFamily:'Inter,system-ui',attributionLogo:true},
    grid:{vertLines:{visible:false},horzLines:{visible:false}},
    crosshair:{mode:LWC.CrosshairMode.Normal,vertLine:{labelBackgroundColor:'#2962ff'},horzLine:{labelBackgroundColor:'#2962ff'}},
    rightPriceScale:{visible:true,borderColor:c.border,autoScale:true,scaleMargins:{top:.08,bottom:.12}},
    leftPriceScale:{visible:false,borderColor:c.border},
    timeScale:{borderColor:c.border,timeVisible:true,secondsVisible:false,rightOffset:8,barSpacing:7,fixLeftEdge:false,fixRightEdge:false},
    handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:true},
    handleScale:{axisPressedMouseMove:true,mouseWheel:true,pinch:true}
  });
  runtime.mainChart.subscribeCrosshairMove(onCrosshair);
  runtime.mainChart.subscribeClick(onChartClick);
  runtime.mainChart.timeScale().subscribeVisibleTimeRangeChange(()=>{drawAll();if(store.ui.radar?.enabled&&store.ui.radar?.visibleOnly){clearTimeout(runtime.radarRangeTimer);runtime.radarRangeTimer=setTimeout(updateMarkers,140);}});
  runtime.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range=>{
    if(runtime.syncing||!runtime.oscChart||!range)return;
    runtime.syncing=true;try{runtime.oscChart.timeScale().setVisibleLogicalRange(range);}catch(_){}
    runtime.syncing=false;
  });
  createPriceSeries();
}
function createOscChart(){
  const c=chartColors();
  runtime.oscChart=LWC.createChart(els.oscChart,{
    autoSize:true,layout:{background:{type:'solid',color:c.background},textColor:c.text,attributionLogo:false},
    grid:{vertLines:{visible:false},horzLines:{visible:false}},
    rightPriceScale:{borderColor:c.border,scaleMargins:{top:.12,bottom:.12}},
    timeScale:{visible:false,borderColor:c.border,timeVisible:true},
    crosshair:{mode:LWC.CrosshairMode.Normal},
    handleScroll:{mouseWheel:false,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
    handleScale:{axisPressedMouseMove:true,mouseWheel:false,pinch:false}
  });
  runtime.oscChart.timeScale().subscribeVisibleLogicalRangeChange(range=>{
    if(runtime.syncing||!runtime.mainChart||!range)return;
    runtime.syncing=true;try{runtime.mainChart.timeScale().setVisibleLogicalRange(range);}catch(_){}
    runtime.syncing=false;
  });
}
function priceDataForType(rows,type){
  if(type==='heikin'){
    let po=null,pc=null;
    return rows.map((c,i)=>{
      const close=(c.open+c.high+c.low+c.close)/4;
      const open=i===0?(c.open+c.close)/2:(po+pc)/2;
      const out={time:c.time,open,high:Math.max(c.high,open,close),low:Math.min(c.low,open,close),close};
      po=open;pc=close;return out;
    });
  }
  if(['line','area','baseline'].includes(type)) return rows.map(c=>({time:c.time,value:c.close}));
  return rows.map(c=>({time:c.time,open:c.open,high:c.high,low:c.low,close:c.close}));
}
function createPriceSeries(){
  if(runtime.priceSeries){try{runtime.mainChart.removeSeries(runtime.priceSeries);}catch(_){}}
  const type=runtime.chartType;
  if(type==='bars') runtime.priceSeries=runtime.mainChart.addSeries(LWC.BarSeries,{upColor:COLORS.green,downColor:COLORS.red,thinBars:true});
  else if(type==='line') runtime.priceSeries=runtime.mainChart.addSeries(LWC.LineSeries,{color:COLORS.blue,lineWidth:2});
  else if(type==='area') runtime.priceSeries=runtime.mainChart.addSeries(LWC.AreaSeries,{lineColor:COLORS.blue,topColor:'rgba(41,98,255,.35)',bottomColor:'rgba(41,98,255,.02)',lineWidth:2});
  else if(type==='baseline') runtime.priceSeries=runtime.mainChart.addSeries(LWC.BaselineSeries,{baseValue:{type:'price',price:0},topLineColor:COLORS.green,topFillColor1:'rgba(8,153,129,.28)',topFillColor2:'rgba(8,153,129,.02)',bottomLineColor:COLORS.red,bottomFillColor1:'rgba(242,54,69,.02)',bottomFillColor2:'rgba(242,54,69,.28)'});
  else runtime.priceSeries=runtime.mainChart.addSeries(LWC.CandlestickSeries,{upColor:COLORS.green,downColor:COLORS.red,borderVisible:false,wickUpColor:COLORS.green,wickDownColor:COLORS.red});
  const rows=runtime.candles.get(key())||[];
  runtime.priceSeries.setData(priceDataForType(rows,type));
  updateMarkers();drawAll();
}
function applyTheme(){
  document.body.dataset.theme=store.ui.theme;
  const c=chartColors();
  runtime.mainChart?.applyOptions({layout:{background:{type:'solid',color:c.background},textColor:c.text},grid:{vertLines:{visible:false},horzLines:{visible:false}},rightPriceScale:{borderColor:c.border},leftPriceScale:{borderColor:c.border},timeScale:{borderColor:c.border}});
  runtime.oscChart?.applyOptions({layout:{background:{type:'solid',color:c.background},textColor:c.text},grid:{vertLines:{visible:false},horzLines:{visible:false}},rightPriceScale:{borderColor:c.border}});
  drawAll();
}
function applyScaleMode(){
  const M=LWC.PriceScaleMode||{};
  const map={normal:M.Normal??0,log:M.Logarithmic??1,percent:M.Percentage??2,indexed:M.IndexedTo100??3};
  runtime.mainChart.priceScale('right').applyOptions({mode:map[store.ui.scaleMode]});
}
function chartRows(){return runtime.candles.get(key())||[];}
function botRows(symbol){return runtime.botCandles[symbol]||[];}
function nearestIndex(rows,time){
  let lo=0,hi=rows.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(rows[mid].time<time)lo=mid+1;else hi=mid;}
  if(lo<=0)return 0;if(lo>=rows.length)return rows.length-1;
  return Math.abs(rows[lo].time-time)<Math.abs(rows[lo-1].time-time)?lo:lo-1;
}
function paperRecovery(){return store.paper.recovery;}
function paperRecoverySymbol(symbol){return paperRecovery().symbols[symbol];}
function lastClosedMinute(nowMs=Date.now()){return Math.floor(nowMs/RECOVERY_CANDLE_MS)*RECOVERY_CANDLE_MS-1;}
function activeCampaignSymbols(){return SYMBOLS.filter(symbol=>ACTIVE_CAMPAIGN_STATUSES.has(store.paper.symbols[symbol]?.campaign?.status));}
function initializePaperRecovery(nowMs=Date.now()){
  const recovery=paperRecovery();
  recovery.policy=PAPER_RECOVERY_POLICY;
  if(recovery.checkpointAt==null&&recovery.gapStartedAt==null){
    recovery.checkpointAt=nowMs;
    recovery.lastRecoveryStatus='baseline';
    for(const symbol of SYMBOLS){
      const state=paperRecoverySymbol(symbol);state.lastMarketAt=state.lastMarketAt??nowMs;state.lastRecoveryStatus='baseline';
    }
    save();runtime.lastRecoveryPersistAt=nowMs;
    return false;
  }
  return needsPaperRecovery(nowMs);
}
function needsPaperRecovery(nowMs=Date.now()){
  const recovery=paperRecovery();
  if(recovery.gapStartedAt!=null)return true;
  const closedThrough=lastClosedMinute(nowMs);
  return activeCampaignSymbols().some(symbol=>num(paperRecoverySymbol(symbol).lastMarketAt,nowMs)<closedThrough);
}
function markPaperGap(reason,atMs=Date.now()){
  const recovery=paperRecovery(),processed=activeCampaignSymbols().map(symbol=>num(paperRecoverySymbol(symbol).lastMarketAt,atMs));
  const start=Math.min(atMs,...processed);
  recovery.gapStartedAt=recovery.gapStartedAt==null?start:Math.min(recovery.gapStartedAt,start);
  recovery.gapReason=recovery.gapReason||reason;
  recovery.gapSequence=num(recovery.gapSequence)+1;
  recovery.checkpointAt=atMs;
  recovery.lastRecoveryStatus='pending';
  save();runtime.lastRecoveryPersistAt=atMs;
}
function markMarketProcessed(symbol,eventTime=Date.now()){
  const recovery=paperRecovery(),state=paperRecoverySymbol(symbol),at=num(eventTime,Date.now());
  state.lastMarketAt=Math.max(num(state.lastMarketAt,0),at);
  recovery.checkpointAt=Date.now();
}
function maybePersistRecoveryCursor(force=false){
  const now=Date.now();if(!force&&now-runtime.lastRecoveryPersistAt<RECOVERY_CHECKPOINT_MS)return;
  save();runtime.lastRecoveryPersistAt=now;
}
async function fetchKlines(symbol,interval,limit=1500){
  const url=`${REST}/fapi/v1/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`;
  const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw new Error(`${symbol} ${interval}: HTTP ${r.status}`);
  const data=await r.json();
  return data.map(x=>({openTime:num(x[0]),closeTime:num(x[6]),time:Math.floor(x[0]/1000),open:num(x[1]),high:num(x[2]),low:num(x[3]),close:num(x[4]),volume:num(x[5])}));
}
async function fetchClosedMinuteRange(symbol,startMs,endMs){
  if(!(endMs>startMs))return[];
  let cursor=Math.max(0,Math.floor(startMs/RECOVERY_CANDLE_MS)*RECOVERY_CANDLE_MS),pages=0;
  const output=[];
  while(cursor<=endMs&&pages<32){
    const url=`${REST}/fapi/v1/klines?symbol=${symbol}&interval=1m&startTime=${cursor}&endTime=${endMs}&limit=1500`;
    const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw new Error(`${symbol} recovery: HTTP ${response.status}`);
    const data=await response.json();if(!Array.isArray(data)||!data.length)break;
    const rows=data.map(x=>({openTime:num(x[0]),closeTime:num(x[6]),time:Math.floor(x[0]/1000),open:num(x[1]),high:num(x[2]),low:num(x[3]),close:num(x[4]),volume:num(x[5])})).filter(row=>row.closeTime>startMs&&row.closeTime<=endMs);
    output.push(...rows);pages+=1;
    const next=num(data.at(-1)?.[0],cursor)+RECOVERY_CANDLE_MS;if(next<=cursor)break;cursor=next;
    if(data.length<1500)break;
  }
  return validateRecoveryCandleRange(output,startMs,endMs);
}
async function ensureData(symbol,interval,force=false){
  const k=symbol+'|'+interval;
  if(!force&&runtime.candles.has(k))return runtime.candles.get(k);
  if(runtime.loading.has(k))return;
  runtime.loading.add(k);
  try{
    const rows=await fetchKlines(symbol,interval);
    runtime.candles.set(k,rows);
    if(interval==='15m')runtime.botCandles[symbol]=rows.map(x=>({...x}));
    return rows;
  }finally{runtime.loading.delete(k);}
}
async function loadCurrent(fit=true){
  els.loading.classList.remove('hidden');els.loading.textContent=`Загрузка ${runtime.symbol} ${runtime.interval}…`;
  try{
    await ensureData(runtime.symbol,runtime.interval);
    if(runtime.interval!=='15m')await ensureData(runtime.symbol,'15m');
    runtime.priceSeries.setData(priceDataForType(chartRows(),runtime.chartType));
    await updateCompare();
    updateIndicators();
    updateMarkers();
    els.watermark.textContent=runtime.symbol+' · '+runtime.interval;
    if(fit)runtime.mainChart.timeScale().fitContent();
    updateDataWindow(chartRows().at(-1));
  }catch(e){console.error(e);toast('Ошибка данных: '+e.message,'error');}
  finally{els.loading.classList.add('hidden');}
}
async function bootstrap(){
  setConnection('Загрузка данных…','warn');
  const recoverOnStart=initializePaperRecovery();
  try{
    await Promise.all(SYMBOLS.map(s=>ensureData(s,'15m')));
    await ensureData(runtime.symbol,runtime.interval);
    scanRecentPatterns();
    await loadCurrent();
    if(recoverOnStart)await catchUpAfterReconnect('startup');
    connectWs();
  }catch(e){console.error(e);setConnection('Ошибка загрузки: '+e.message,'error');connectWs();}
}

function recoveryStartForSymbol(symbol,nowMs){
  const recovery=paperRecovery(),state=paperRecoverySymbol(symbol),candidates=[recovery.gapStartedAt,state.lastMarketAt,recovery.checkpointAt].filter(value=>value!=null).map(Number).filter(Number.isFinite);
  const rawStart=candidates.length?Math.min(...candidates):nowMs;
  const earliest=nowMs-336*3_600_000;
  const recoveredThrough=state.lastRecoveredCloseAt==null?-Infinity:Number(state.lastRecoveredCloseAt)+1;
  const campaignCreated=Date.parse(store.paper.symbols[symbol]?.campaign?.createdAt||'');
  const afterMs=Math.max(rawStart,recoveredThrough,earliest,Number.isFinite(campaignCreated)?campaignCreated:-Infinity);
  return{afterMs,truncated:rawStart<earliest,rawStart};
}
function logRecoveredPaperEvents(symbol,result){
  for(const event of result.events){
    const at=new Date(event.atMs).toISOString(),meta={price:event.price,stop:event.stop,recovered:true,policy:RECOVERY_PATH_POLICY,candleOpenTime:event.candleOpenTime};
    if(event.type==='level_filled')logActivity('paper',`${symbol.replace('USDT','')}: L${event.level} восстановлена после reconnect`,meta,at);
    if(event.type==='trailing_armed')logActivity('paper',`${symbol.replace('USDT','')}: trailing восстановлен после reconnect`,meta,at);
    if(event.type==='trailing_raised')logActivity('paper',`${symbol.replace('USDT','')}: stop восстановлен выше`,meta,at);
  }
}
async function recoverMissedPaperTrading(reason,nowMs=Date.now()){
  const recovery=paperRecovery(),endMs=lastClosedMinute(nowMs),symbols=activeCampaignSymbols();
  const gapSequence=num(recovery.gapSequence);
  const summary={reason,policy:RECOVERY_PATH_POLICY,symbols:symbols.length,candles:0,boundaryCandles:0,fills:0,l1Cycles:0,trailingArmed:0,trailingRaised:0,closed:0,expired:0,truncated:0,failures:[]};
  recovery.lastRecoveryStatus='running';
  const fetched=await Promise.all(symbols.map(async symbol=>{
    const start=recoveryStartForSymbol(symbol,nowMs),state=paperRecoverySymbol(symbol);state.lastRecoveryStatus='running';
    try{return{symbol,start,candles:await fetchClosedMinuteRange(symbol,start.afterMs,endMs)};}
    catch(error){return{symbol,start,error};}
  }));
  const contexts=new Map(),timeline=[],recoveryPrices={};
  for(const item of fetched){
    const {symbol,start}=item,state=paperRecoverySymbol(symbol),ss=store.paper.symbols[symbol],campaign=ss.campaign;
    if(item.error){state.lastRecoveryStatus='error';summary.failures.push({symbol,message:item.error.message});continue;}
    if(!campaign){state.lastRecoveryStatus='ok';continue;}
    const result={events:[],candlesReplayed:0,boundaryCandles:0,lastCloseTime:null,lastEventAt:null,close:null,expiredWithoutFill:false,liquidated:false};
    const context={symbol,start,state,ss,campaign,result,seenCandles:new Set(),terminal:false};contexts.set(symbol,context);
    for(const candle of item.candles){for(const point of recoveryCandlePath(candle,start.afterMs))timeline.push({...point,symbol});}
  }
  timeline.sort((a,b)=>a.atMs-b.atMs||String(a.symbol).localeCompare(String(b.symbol))||a.candleOpenTime-b.candleOpenTime);
  for(let index=0;index<timeline.length;){
    const atMs=timeline[index].atMs,batch=[];while(index<timeline.length&&timeline[index].atMs===atMs)batch.push(timeline[index++]);
    for(const point of batch){
      const context=contexts.get(point.symbol);if(!context||context.terminal)continue;
      const {campaign,result}=context,seenKey=String(point.candleOpenTime);recoveryPrices[point.symbol]=point.price;
      if(!context.seenCandles.has(seenKey)){context.seenCandles.add(seenKey);result.candlesReplayed+=1;result.boundaryCandles+=point.boundary?1:0;result.lastCloseTime=point.candleCloseTime;}
      const stopBefore=num(campaign.trailStop),step=processCampaignQuote(campaign,{bid:point.price,ask:point.price},store.paper.settings,point.atMs);result.lastEventAt=point.atMs;
      result.events.push(...step.events.map(event=>({...event,atMs:point.atMs,recovered:true,phase:point.phase,candleOpenTime:point.candleOpenTime})));
      for(const event of step.events){if(event.type==='l1_cycle_closed')recordL1Cycle(point.symbol,{...event,atMs:point.atMs},{atMs:point.atMs,recovered:true,deferRender:true});}
      if(step.close){const exitPrice=step.close.reason==='reclaim_trailing_stop'&&stopBefore>0?stopBefore:step.close.price;result.close={...step.close,price:exitPrice,atMs:point.atMs};context.terminal=true;closeCampaign(point.symbol,exitPrice,step.close.reason,{atMs:point.atMs,recovered:true,deferRender:true});summary.closed+=1;}
      else if(step.expiredWithoutFill){result.expiredWithoutFill=true;context.terminal=true;context.ss.campaign=null;if(context.ss.pattern)context.ss.pattern.status='expired';summary.expired+=1;logActivity('paper',`${point.symbol.replace('USDT','')}: кампания истекла во время reconnect`,{recovered:true,policy:RECOVERY_PATH_POLICY},new Date(point.atMs).toISOString());}
    }
    const openSymbols=[...contexts.values()].filter(context=>!context.terminal&&context.ss.campaign?.qty).map(context=>context.symbol);
    if(openSymbols.length&&openSymbols.every(symbol=>num(recoveryPrices[symbol])>0)){
      const liquidated=checkGlobalLiquidation({atMs,recovered:true,deferRender:true,prices:recoveryPrices});
      for(const symbol of liquidated){const context=contexts.get(symbol);if(context){context.terminal=true;context.result.liquidated=true;context.result.lastEventAt=atMs;}summary.closed+=1;}
    }
  }
  for(const context of contexts.values()){
    const {symbol,start,state,result}=context;
    summary.candles+=result.candlesReplayed;summary.boundaryCandles+=result.boundaryCandles;summary.truncated+=start.truncated?1:0;
    summary.fills+=result.events.filter(event=>event.type==='level_filled').length;
    const recoveredCycles=result.events.filter(event=>event.type==='l1_cycle_closed');summary.l1Cycles+=recoveredCycles.length;
    summary.trailingArmed+=result.events.filter(event=>event.type==='trailing_armed').length;summary.trailingRaised+=result.events.filter(event=>event.type==='trailing_raised').length;
    if(result.lastCloseTime!=null)state.lastRecoveredCloseAt=Math.max(num(state.lastRecoveredCloseAt,0),num(result.lastCloseTime));
    state.lastMarketAt=Math.max(num(state.lastMarketAt,0),num(result.lastEventAt,endMs));state.lastRecoveryAt=nowMs;state.lastRecoveryStatus=start.truncated?'truncated':'ok';state.recoveredCandles=num(state.recoveredCandles)+result.candlesReplayed;state.boundaryCandles=num(state.boundaryCandles)+result.boundaryCandles;
    logRecoveredPaperEvents(symbol,result);
  }
  recovery.lastRecoveryAt=nowMs;recovery.lastRecoveryStatus=summary.failures.length?'partial':summary.truncated?'truncated':'ok';
  if(!summary.failures.length&&num(recovery.gapSequence)===gapSequence&&!document.hidden){recovery.gapStartedAt=null;recovery.gapReason=null;}
  recovery.checkpointAt=nowMs;runtime.lastCatchupAt=nowMs;runtime.lastRecoverySummary=summary;
  const message=`Paper replay: ${summary.candles} × 1m, fills ${summary.fills}, L1 cycles ${summary.l1Cycles}, exits ${summary.closed}, boundary ${summary.boundaryCandles}`;
  logActivity(summary.failures.length?'risk':'connection',summary.failures.length?'Paper replay завершён частично':message,{...summary});
  save();runtime.lastRecoveryPersistAt=nowMs;
  return summary;
}
function bufferRecoveryQuote(quote){
  if(runtime.recoveryQuoteBuffer.length>=RECOVERY_MAX_BUFFER){const dropped=runtime.recoveryQuoteBuffer.splice(0,Math.floor(RECOVERY_MAX_BUFFER/4));runtime.recoveryBufferOverflow=true;runtime.recoveryOverflowAt=Math.min(runtime.recoveryOverflowAt??Infinity,...dropped.map(item=>item.eventTime));}
  runtime.recoveryQuoteBuffer.push(quote);
}
function flushRecoveryQuotes(){
  const buffered=runtime.recoveryQuoteBuffer.splice(0).sort((a,b)=>a.eventTime-b.eventTime||num(a.updateId)-num(b.updateId)||String(a.symbol).localeCompare(b.symbol));
  for(const quote of buffered)processQuote(quote.symbol,quote.bid,quote.ask,{eventTime:quote.eventTime,updateId:quote.updateId,source:'buffered',silent:true});
  const overflowed=runtime.recoveryBufferOverflow;
  if(overflowed){const overflowAt=runtime.recoveryOverflowAt??Date.now();logActivity('risk','Буфер live-котировок переполнился во время paper replay',{limit:RECOVERY_MAX_BUFFER,overflowAt});runtime.recoveryBufferOverflow=false;runtime.recoveryOverflowAt=null;markPaperGap('buffer-overflow',overflowAt);}
  if(buffered.length){renderWatchlist();renderPaper();renderActivity();renderTicker();updateMarkers();}
  return overflowed;
}
async function catchUpAfterReconnect(reason='reconnect'){
  if(runtime.recoveryPromise)return runtime.recoveryPromise;
  runtime.recovering=true;renderSessionHealth();
  runtime.recoveryPromise=(async()=>{
    const requests=SYMBOLS.map(symbol=>ensureData(symbol,'15m',true));
    if(runtime.interval!=='15m')requests.push(ensureData(runtime.symbol,runtime.interval,true));
    const [chartResults,summary]=await Promise.all([Promise.allSettled(requests),recoverMissedPaperTrading(reason)]);
    const chartFailures=chartResults.filter(item=>item.status==='rejected');
    if(chartFailures.length)logActivity('risk','Часть свечей графика не обновилась',{failures:chartFailures.map(item=>item.reason?.message||String(item.reason))});
    scanRecentPatterns();
    if(!runtime.replay.active){runtime.priceSeries.setData(priceDataForType(chartRows(),runtime.chartType));updateIndicators();updateMarkers();}
    els.sessionRecovery.textContent=summary.failures.length?`Paper replay частичный: ошибки ${summary.failures.map(item=>item.symbol).join(', ')}. Повторим при следующем reconnect.`:`Восстановлено ${summary.candles} закрытых 1m свечей · fills ${summary.fills} · exits ${summary.closed} · boundary ${summary.boundaryCandles}${summary.truncated?` · capped ${summary.truncated}`:''}.`;
    save();renderActivity();return summary;
  })();
  try{return await runtime.recoveryPromise;}
  catch(error){console.error(error);logActivity('risk','Не удалось восстановить paper-события',{message:error.message});save();return null;}
  finally{runtime.recovering=false;runtime.recoveryPromise=null;const overflowed=flushRecoveryQuotes();renderSessionHealth();if(overflowed)setTimeout(()=>catchUpAfterReconnect('buffer-overflow'),0);}
}
function updateCandleMap(symbol,interval,c){
  const k=symbol+'|'+interval,rows=runtime.candles.get(k)||[];
  if(rows.length&&rows.at(-1).time===c.time)rows[rows.length-1]=c;
  else{rows.push(c);if(rows.length>5000)rows.splice(0,rows.length-5000);}
  runtime.candles.set(k,rows);
  if(interval==='15m')runtime.botCandles[symbol]=rows;
  if(symbol===runtime.symbol&&interval===runtime.interval&&!runtime.replay.active){
    runtime.priceSeries.update(runtime.chartType==='heikin'?priceDataForType(rows,runtime.chartType).at(-1):priceDataForType([c],runtime.chartType)[0]);
    updateIndicators(true);drawAll();
  }
}
function connectWs(){
  clearTimeout(runtime.reconnect);
  try{runtime.ws?.close();}catch(_){}
  const streams=[];
  for(const s of SYMBOLS){
    const x=s.toLowerCase();streams.push(`${x}@bookTicker`,`${x}@kline_15m`);
    if(runtime.interval!=='15m')streams.push(`${x}@kline_${runtime.interval}`);
  }
  const ws=new WebSocket(WS_BASE+streams.join('/'));runtime.ws=ws;runtime.wsAttempt++;
  setConnection('Подключение к Binance…','warn');
  ws.onopen=()=>{
    const wasDisconnected=!!runtime.disconnectedAt;
    setConnection('Онлайн · Binance USD-M Futures','ok');
    if(wasDisconnected||runtime.wsAttempt>1||needsPaperRecovery())catchUpAfterReconnect(wasDisconnected?'reconnect':runtime.wsAttempt>1?'socket-restart':'startup-retry');
    runtime.disconnectedAt=null;
  };
  ws.onerror=()=>setConnection('Ошибка WebSocket','error');
  ws.onclose=()=>{
    if(runtime.ws!==ws)return;
    if(!runtime.disconnectedAt){runtime.disconnectedAt=Date.now();markPaperGap('websocket',runtime.disconnectedAt);logActivity('connection','Поток котировок потерян');save();}
    setConnection('Переподключение…','warn');runtime.reconnect=setTimeout(connectWs,3000);
  };
  ws.onmessage=e=>{
    try{
      const p=JSON.parse(e.data),d=p.data||p,s=d.s;if(!SYMBOLS.includes(s))return;
      const eventTime=num(d.E,num(d.T,Date.now()));
      if(d.e==='bookTicker'){
        processQuote(s,num(d.b),num(d.a),{eventTime,updateId:d.u,source:'live'});
      }else if(d.e==='kline'){
        const k=d.k,interval=k.i,c={time:Math.floor(k.t/1000),open:num(k.o),high:num(k.h),low:num(k.l),close:num(k.c),volume:num(k.v)};
        updateCandleMap(s,interval,c);
        if(interval==='15m'&&k.x){detectLatestPattern(s);if(!runtime.recovering)processBotQuote(s,{nowMs:eventTime,source:'kline'});}
        if(k.x&&(store.ui.radar?.enabled||store.shadow.enabled)&&s===runtime.symbol&&interval===runtime.interval)updateMarkers();
      }
    }catch(err){console.error(err);}
  };
}
function setConnection(text,type){
  const changed=runtime.connectionState!==type;
  runtime.connectionState=type;els.connectionText.textContent=text;els.connectionDot.className='dot '+type;
  els.connectionButton?.setAttribute('title',text);els.radarBtn?.setAttribute('aria-pressed',String(!!store.ui.radar?.enabled));
  if(changed&&type==='ok'){logActivity('connection','WebSocket online');save();}
  renderSessionHealth();
}
function processLiveShadow(symbol,bid,ask,eventTime){
  const result=processShadowBook(store.shadow,symbol,{bid,ask},SHADOW_SETTINGS,eventTime);
  if(!result.changed)return;
  runtime.shadowDirty=true;
  const important=result.events.filter(event=>['shadow_fill','shadow_return','shadow_closed'].includes(event.type));
  if(important.length){
    const closed=important.filter(event=>event.type==='shadow_closed').length,fills=important.filter(event=>event.type==='shadow_fill').length;
    logActivity('shadow',`${symbol.replace('USDT','')}: shadow ${fills?`fills ${fills}`:closed?`closed ${closed}`:'return observed'}`,{events:important,paperBalanceImpact:0},new Date(eventTime).toISOString());
  }
  if(important.length||Date.now()-runtime.lastShadowPersistAt>=5000){save();runtime.lastShadowPersistAt=Date.now();runtime.shadowDirty=false;}
  if(document.querySelector('[data-panel-id="lab"]')?.classList.contains('active'))renderShadow();
}
function processQuote(symbol,bid,ask,{eventTime=Date.now(),updateId=null,source='live',silent=false}={}){
  const q=runtime.quotes[symbol],prev=q.last,eventAt=Number(eventTime)>0?Number(eventTime):Date.now();
  q.bid=bid;q.ask=ask;q.last=(bid+ask)/2;q.updated=Date.now();q.marketAt=eventAt;
  runtime.lastQuoteAt=q.updated;
  if(prev&&q.open24==null)q.open24=prev;
  if(q.open24)q.change24=q.last/q.open24-1;
  if(runtime.recovering&&source==='live'){
    bufferRecoveryQuote({symbol,bid,ask,eventTime:eventAt,updateId});
    if(!silent){renderWatchlist();renderPaperHeader();renderTicker();}
    return;
  }
  markMarketProcessed(symbol,eventAt);
  if(source==='live')processAlerts(symbol,q.last,prev);
  processBotQuote(symbol,{quote:{bid,ask},nowMs:eventAt,source,suppressRender:silent});
  if(source==='live')processLiveShadow(symbol,bid,ask,eventAt);
  runtime.lastEngineAt=Date.now();
  maybePersistRecoveryCursor();
  if(!silent){renderWatchlist();renderPaperHeader();renderTicker();}
}
function renderTicker(){
  const q=runtime.quotes[runtime.symbol];
  els.tickerText.textContent=q.last?`${runtime.symbol} ${price(q.last)}  ${q.change24==null?'':(q.change24>=0?'+':'')+(q.change24*100).toFixed(2)+'%'}`:'—';
}
function renderWatchlist(){
  els.watchlist.innerHTML=SYMBOLS.map(s=>{
    const q=runtime.quotes[s],chg=q.change24;
    return `<div class="watch-row ${s===runtime.symbol?'active':''}" data-symbol="${s}">
      <div><b>${s.replace('USDT','')}</b><small>Binance Perp</small></div>
      <b>${price(q.last,s)}</b><b class="${chg==null?'':chg>=0?'up':'down'}">${chg==null?'—':(chg>=0?'+':'')+(chg*100).toFixed(2)+'%'}</b>
    </div>`;
  }).join('');
}
function onCrosshair(param){
  if(!param?.time||!runtime.priceSeries){els.chartMainWrap.classList.remove('crosshair-active');return;}
  els.chartMainWrap.classList.add('crosshair-active');
  const rows=chartRows();if(!rows.length)return;
  const t=typeof param.time==='number'?param.time:Math.floor(Date.UTC(param.time.year,param.time.month-1,param.time.day)/1000);
  const row=rows[nearestIndex(rows,t)];runtime.lastCrosshair=row;
  updateDataWindow(row);
}
function updateDataWindow(c){
  if(!c)return;const rows=chartRows(),i=rows.findIndex(x=>x.time===c.time),a=atrValue(rows,i,14),chg=c.open?c.close/c.open-1:0;
  els.ohlc.innerHTML=`O <b>${price(c.open)}</b> H <b>${price(c.high)}</b> L <b>${price(c.low)}</b> C <b class="${c.close>=c.open?'up':'down'}">${price(c.close)}</b> <span>${(chg*100).toFixed(2)}%</span>`;
  els.dwTime.textContent=fmtTime(c.time);els.dwOpen.textContent=price(c.open);els.dwHigh.textContent=price(c.high);
  els.dwLow.textContent=price(c.low);els.dwClose.textContent=price(c.close);els.dwVolume.textContent=Math.round(c.volume).toLocaleString('en-US');
  els.dwAtr.textContent=a?price(a):'—';els.dwChange.textContent=(chg>=0?'+':'')+(chg*100).toFixed(2)+'%';
}
function zoom(factor){
  const ts=runtime.mainChart.timeScale(),r=ts.getVisibleLogicalRange();if(!r)return;
  const mid=(r.from+r.to)/2,half=(r.to-r.from)*factor/2;ts.setVisibleLogicalRange({from:mid-half,to:mid+half});
}
function setRange(range){
  const rows=chartRows();if(!rows.length)return;const end=rows.at(-1).time;let start=rows[0].time;
  const day=86400,now=new Date(end*1000);
  if(range==='1D')start=end-day;else if(range==='5D')start=end-5*day;else if(range==='1M')start=end-30*day;
  else if(range==='3M')start=end-90*day;else if(range==='6M')start=end-180*day;else if(range==='1Y')start=end-365*day;
  else if(range==='YTD')start=Date.UTC(now.getUTCFullYear(),0,1)/1000;
  runtime.mainChart.timeScale().setVisibleRange({from:start,to:end});
}

/* Indicators */
function sma(rows,period){
  let sum=0;const out=[];
  for(let i=0;i<rows.length;i++){sum+=rows[i].close;if(i>=period)sum-=rows[i-period].close;if(i>=period-1)out.push({time:rows[i].time,value:sum/period});}
  return out;
}
function ema(rows,period){
  if(!rows.length)return[];const k=2/(period+1),out=[];let v=rows[0].close;
  for(let i=0;i<rows.length;i++){v=i?rows[i].close*k+v*(1-k):v;if(i>=period-1)out.push({time:rows[i].time,value:v});}
  return out;
}
function stdWindow(rows,period){
  const out=[];
  for(let i=period-1;i<rows.length;i++){const a=rows.slice(i-period+1,i+1).map(x=>x.close),m=a.reduce((x,y)=>x+y,0)/period,s=Math.sqrt(a.reduce((x,y)=>x+(y-m)**2,0)/period);out.push({time:rows[i].time,m,sd:s});}
  return out;
}
function vwap(rows){
  let pv=0,v=0;return rows.map(c=>{const tp=(c.high+c.low+c.close)/3;pv+=tp*c.volume;v+=c.volume;return{time:c.time,value:v?pv/v:tp};});
}
function rsi(rows,period=14){
  const out=[];let ag=0,al=0;
  for(let i=1;i<rows.length;i++){const d=rows[i].close-rows[i-1].close,g=Math.max(d,0),l=Math.max(-d,0);
    if(i<=period){ag+=g;al+=l;if(i===period){ag/=period;al/=period;out.push({time:rows[i].time,value:al===0?100:100-100/(1+ag/al)});}}
    else{ag=(ag*(period-1)+g)/period;al=(al*(period-1)+l)/period;out.push({time:rows[i].time,value:al===0?100:100-100/(1+ag/al)});}
  }return out;
}
function macd(rows){
  const fast=emaFull(rows,12),slow=emaFull(rows,26);const line=rows.map((c,i)=>({time:c.time,value:fast[i]-slow[i]}));
  const sig=emaValues(line.map(x=>x.value),9);return line.map((x,i)=>({time:x.time,macd:x.value,signal:sig[i],hist:x.value-sig[i]})).slice(25);
}
function emaFull(rows,p){return emaValues(rows.map(x=>x.close),p);}
function emaValues(vals,p){if(!vals.length)return[];const k=2/(p+1),o=[];let v=vals[0];for(let i=0;i<vals.length;i++){v=i?vals[i]*k+v*(1-k):v;o.push(v);}return o;}
function atrValue(rows,index,period=14){
  if(index<period||index>=rows.length)return null;let sum=0;
  for(let i=index-period+1;i<=index;i++){const prev=rows[i-1].close,c=rows[i];sum+=Math.max(c.high-c.low,Math.abs(c.high-prev),Math.abs(c.low-prev));}
  return sum/period;
}
function atrSeries(rows,period=14){
  const out=[];for(let i=period;i<rows.length;i++)out.push({time:rows[i].time,value:atrValue(rows,i,period)});return out;
}
function clearIndicatorSeries(){
  for(const s of runtime.indicatorSeries){try{runtime.mainChart.removeSeries(s);}catch(_){}}
  runtime.indicatorSeries=[];if(runtime.volumeSeries){try{runtime.mainChart.removeSeries(runtime.volumeSeries);}catch(_){}runtime.volumeSeries=null;}
  for(const s of runtime.oscSeries){try{runtime.oscChart.removeSeries(s);}catch(_){}}
  runtime.oscSeries=[];
}
function updateIndicators(){
  if(!runtime.mainChart||!runtime.oscChart)return;const rows=chartRows();clearIndicatorSeries();if(!rows.length)return;
  const ind=store.ui.indicators,add=(data,color,width=1)=>{
    const s=runtime.mainChart.addSeries(LWC.LineSeries,{color,lineWidth:width,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false});s.setData(data);runtime.indicatorSeries.push(s);return s;
  };
  if(ind.sma20)add(sma(rows,20),'#f6c344',2);
  if(ind.ema20)add(ema(rows,20),'#42a5f5',2);
  if(ind.ema50)add(ema(rows,50),'#9c6ade',2);
  if(ind.bollinger){const b=stdWindow(rows,20);add(b.map(x=>({time:x.time,value:x.m+2*x.sd})),'#8b93a4');add(b.map(x=>({time:x.time,value:x.m})),'#607d8b');add(b.map(x=>({time:x.time,value:x.m-2*x.sd})),'#8b93a4');}
  if(ind.vwap)add(vwap(rows),'#ff7043',2);
  if(ind.volume){
    runtime.volumeSeries=runtime.mainChart.addSeries(LWC.HistogramSeries,{priceScaleId:'volume',priceFormat:{type:'volume'},priceLineVisible:false,lastValueVisible:false});
    runtime.mainChart.priceScale('volume').applyOptions({scaleMargins:{top:.78,bottom:0}});
    runtime.volumeSeries.setData(rows.map(c=>({time:c.time,value:c.volume,color:c.close>=c.open?'rgba(8,153,129,.45)':'rgba(242,54,69,.45)'})));
  }
  const lower=store.ui.lowerIndicator;
  if(!lower){els.indicatorPane.classList.add('hidden');return;}
  els.indicatorPane.classList.remove('hidden');
  if(lower==='rsi'){
    els.paneTitle.textContent='RSI 14';const s=runtime.oscChart.addSeries(LWC.LineSeries,{color:'#9c6ade',lineWidth:2});s.setData(rsi(rows));runtime.oscSeries.push(s);
    const hi=runtime.oscChart.addSeries(LWC.LineSeries,{color:'#8b93a4',lineStyle:LWC.LineStyle.Dashed,lastValueVisible:false,priceLineVisible:false});hi.setData(rows.map(c=>({time:c.time,value:70})));runtime.oscSeries.push(hi);
    const lo=runtime.oscChart.addSeries(LWC.LineSeries,{color:'#8b93a4',lineStyle:LWC.LineStyle.Dashed,lastValueVisible:false,priceLineVisible:false});lo.setData(rows.map(c=>({time:c.time,value:30})));runtime.oscSeries.push(lo);
  }else if(lower==='macd'){
    els.paneTitle.textContent='MACD 12 26 9';const m=macd(rows);
    const a=runtime.oscChart.addSeries(LWC.LineSeries,{color:'#42a5f5',lineWidth:2});a.setData(m.map(x=>({time:x.time,value:x.macd})));runtime.oscSeries.push(a);
    const b=runtime.oscChart.addSeries(LWC.LineSeries,{color:'#ff9800',lineWidth:1});b.setData(m.map(x=>({time:x.time,value:x.signal})));runtime.oscSeries.push(b);
    const h=runtime.oscChart.addSeries(LWC.HistogramSeries,{priceLineVisible:false,lastValueVisible:false});h.setData(m.map(x=>({time:x.time,value:x.hist,color:x.hist>=0?'rgba(8,153,129,.65)':'rgba(242,54,69,.65)'})));runtime.oscSeries.push(h);
  }else if(lower==='atr'){
    els.paneTitle.textContent='ATR 14';const s=runtime.oscChart.addSeries(LWC.LineSeries,{color:'#ff9800',lineWidth:2});s.setData(atrSeries(rows));runtime.oscSeries.push(s);
  }
  const r=runtime.mainChart.timeScale().getVisibleLogicalRange();if(r)runtime.oscChart.timeScale().setVisibleLogicalRange(r);
}
async function updateCompare(){
  if(runtime.compareSeries){try{runtime.mainChart.removeSeries(runtime.compareSeries);}catch(_){}runtime.compareSeries=null;}
  const s=store.ui.compare;if(!s||s===runtime.symbol){runtime.mainChart.priceScale('left').applyOptions({visible:false});return;}
  await ensureData(s,runtime.interval);
  runtime.mainChart.priceScale('left').applyOptions({visible:true,mode:LWC.PriceScaleMode.Percentage});
  runtime.compareSeries=runtime.mainChart.addSeries(LWC.LineSeries,{priceScaleId:'left',color:'#ff9800',lineWidth:2,priceLineVisible:false,title:s});
  runtime.compareSeries.setData((runtime.candles.get(s+'|'+runtime.interval)||[]).map(c=>({time:c.time,value:c.close})));
}

/* Drawings */
function drawingsKey(){return key();}
function drawingStore(){if(!store.ui.drawings[drawingsKey()])store.ui.drawings[drawingsKey()]=[];return store.ui.drawings[drawingsKey()];}
function snapshotDrawings(){runtime.undo.push(JSON.stringify(drawingStore()));if(runtime.undo.length>60)runtime.undo.shift();runtime.redo=[];}
function restoreDrawings(serialized){store.ui.drawings[drawingsKey()]=JSON.parse(serialized);save();drawAll();renderObjects();}
function resizeCanvas(){
  const r=els.drawingCanvas.getBoundingClientRect();runtime.dpr=window.devicePixelRatio||1;
  els.drawingCanvas.width=Math.max(1,Math.floor(r.width*runtime.dpr));els.drawingCanvas.height=Math.max(1,Math.floor(r.height*runtime.dpr));
  els.drawingCanvas.getContext('2d').setTransform(runtime.dpr,0,0,runtime.dpr,0,0);
}
function eventPoint(e,snap=store.ui.magnet){
  const r=els.drawingCanvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
  let time=runtime.mainChart.timeScale().coordinateToTime(x),pr=runtime.priceSeries.coordinateToPrice(y);
  if(time==null||pr==null)return null;
  if(typeof time!=='number')time=Math.floor(Date.UTC(time.year,time.month-1,time.day)/1000);
  let p={time,price:num(pr)};
  if(snap){
    const rows=chartRows(),i=nearestIndex(rows,p.time),c=rows[i];
    if(c){p.time=c.time;const vals=[c.open,c.high,c.low,c.close],near=vals.sort((a,b)=>Math.abs(a-p.price)-Math.abs(b-p.price))[0];p.price=near;}
  }
  return p;
}
function coord(p){return{x:runtime.mainChart.timeScale().timeToCoordinate(p.time),y:runtime.priceSeries.priceToCoordinate(p.price)};}
function pointDistance(a,b){return Math.hypot(a.x-b.x,a.y-b.y);}
function segmentDistance(point,start,end){
  const dx=end.x-start.x,dy=end.y-start.y,length=dx*dx+dy*dy;
  if(!length)return pointDistance(point,start);
  const t=clamp(((point.x-start.x)*dx+(point.y-start.y)*dy)/length,0,1);
  return pointDistance(point,{x:start.x+t*dx,y:start.y+t*dy});
}
function drawingHitAt(point){
  const rows=drawingStore();
  for(let index=rows.length-1;index>=0;index--){
    const d=rows[index];if(d.hidden)continue;const a=coord(d.p1),b=d.p2?coord(d.p2):null;
    if(a.x==null||a.y==null)continue;
    if(pointDistance(point,a)<=14||(b&&pointDistance(point,b)<=14))return d;
    if(d.type==='horizontal'&&Math.abs(point.y-a.y)<=10)return d;
    if(d.type==='vertical'&&Math.abs(point.x-a.x)<=10)return d;
    if(d.type==='text'&&pointDistance(point,a)<=28)return d;
    if(b&&['trend','ray','channel','measure','longPosition'].includes(d.type)&&segmentDistance(point,a,b)<=10)return d;
    if(b&&['rect','fib'].includes(d.type)){
      const minX=Math.min(a.x,b.x)-10,maxX=Math.max(a.x,b.x)+10,minY=Math.min(a.y,b.y)-10,maxY=Math.max(a.y,b.y)+10;
      if(point.x>=minX&&point.x<=maxX&&point.y>=minY&&point.y<=maxY)return d;
    }
  }
  return null;
}
function syncDrawingInteraction(){
  const editing=runtime.tool==='cursor'&&!!runtime.selectedDrawing;
  els.drawingCanvas.classList.toggle('editing-object',editing);
}
function selectDrawing(id){runtime.selectedDrawing=id||null;syncDrawingInteraction();renderObjects();drawAll();}
function openSelectedDrawingProperties(){
  const d=drawingStore().find(item=>item.id===runtime.selectedDrawing);if(!d)return toast('Сначала выбери объект','error');
  els.propertyColor.value=d.color||COLORS.blue;els.propertyWidth.value=String(d.width||2);
  els.propertyDash.value=(d.dash||[]).length>1?((d.dash||[])[0]<=2?'dotted':'dashed'):'solid';
  openModal(els.drawingPropertiesModal);
}
function beginDrawingEdit(e){
  const d=drawingStore().find(item=>item.id===runtime.selectedDrawing);if(!d)return;
  if(d.locked)return toast('Объект заблокирован','error');
  const start=eventPoint(e,false);if(!start)return;const rect=els.drawingCanvas.getBoundingClientRect(),pixel={x:e.clientX-rect.left,y:e.clientY-rect.top};
  const hit=drawingHitAt(pixel);if(!hit||hit.id!==d.id){selectDrawing(null);return;}
  const a=coord(d.p1),b=d.p2?coord(d.p2):null;let mode='move';
  if(pointDistance(pixel,a)<=16)mode='p1';else if(b&&pointDistance(pixel,b)<=16)mode='p2';
  snapshotDrawings();runtime.drawingEdit={id:d.id,start,mode,original:JSON.parse(JSON.stringify(d)),moved:false,startX:e.clientX,startY:e.clientY};
  clearTimeout(runtime.longPressTimer);runtime.longPressTimer=setTimeout(()=>{if(!runtime.drawingEdit?.moved)openSelectedDrawingProperties();},560);
  try{els.drawingCanvas.setPointerCapture(e.pointerId);}catch(_){}
}
function line(ctx,a,b){ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();}
function label(ctx,text,x,y,color='#dfe3eb'){
  ctx.save();ctx.font='12px system-ui';const w=ctx.measureText(text).width+10;ctx.fillStyle='rgba(17,21,29,.92)';ctx.fillRect(x,y-17,w,20);ctx.fillStyle=color;ctx.fillText(text,x+5,y-3);ctx.restore();
}
function drawShape(ctx,d,preview=false){
  if(d.hidden||store.ui.drawingsHidden)return;
  const a=coord(d.p1),b=d.p2?coord(d.p2):null;if(a.x==null||a.y==null)return;
  ctx.save();ctx.lineWidth=d.width||2;ctx.strokeStyle=preview?COLORS.orange:(d.color||COLORS.blue);ctx.fillStyle=d.fill||'rgba(41,98,255,.10)';ctx.setLineDash(preview?[6,4]:(d.dash||[]));
  const w=els.drawingCanvas.clientWidth,h=els.drawingCanvas.clientHeight;
  if(d.type==='horizontal'){line(ctx,{x:0,y:a.y},{x:w,y:a.y});label(ctx,price(d.p1.price),Math.max(0,w-82),a.y,d.color||COLORS.blue);}
  else if(d.type==='vertical'){line(ctx,{x:a.x,y:0},{x:a.x,y:h});label(ctx,fmtTime(d.p1.time),Math.max(0,a.x-65),20,d.color||COLORS.blue);}
  else if(d.type==='text'){ctx.fillStyle=d.color||COLORS.blue;ctx.font=(d.fontSize||14)+'px system-ui';ctx.fillText(d.text||'Текст',a.x,a.y);}
  else if(b&&b.x!=null&&b.y!=null){
    if(d.type==='trend')line(ctx,a,b);
    else if(d.type==='ray'){const dx=b.x-a.x,dy=b.y-a.y,t=dx===0?0:(w-a.x)/dx,end={x:w,y:a.y+dy*t};line(ctx,a,end);label(ctx,`GALKA ${price(d.p1.price)}`,Math.max(0,Math.min(a.x,w-112)),a.y,d.color||COLORS.blue);}
    else if(d.type==='rect'){ctx.beginPath();ctx.rect(Math.min(a.x,b.x),Math.min(a.y,b.y),Math.abs(b.x-a.x),Math.abs(b.y-a.y));ctx.fill();ctx.stroke();}
    else if(d.type==='channel'){
      const off=(d.offsetPct||1)/100*((d.p1.price+d.p2.price)/2),a2=coord({time:d.p1.time,price:d.p1.price+off}),b2=coord({time:d.p2.time,price:d.p2.price+off});
      line(ctx,a,b);line(ctx,a2,b2);ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.lineTo(b2.x,b2.y);ctx.lineTo(a2.x,a2.y);ctx.closePath();ctx.fill();
    }else if(d.type==='fib'){
      const lv=[0,.236,.382,.5,.618,.786,1];for(const f of lv){const y=a.y+(b.y-a.y)*f;line(ctx,{x:Math.min(a.x,b.x),y},{x:Math.max(a.x,b.x),y});label(ctx,String(f),Math.min(a.x,b.x),y,d.color||COLORS.blue);}
    }else if(d.type==='measure'){
      ctx.beginPath();ctx.rect(Math.min(a.x,b.x),Math.min(a.y,b.y),Math.abs(b.x-a.x),Math.abs(b.y-a.y));ctx.stroke();
      const pct=(d.p2.price/d.p1.price-1)*100,bars=Math.round(Math.abs(d.p2.time-d.p1.time)/(intervalSeconds(runtime.interval)||60));
      label(ctx,`${pct>=0?'+':''}${pct.toFixed(2)}% · ${bars} бар.`,Math.min(a.x,b.x),Math.min(a.y,b.y),pct>=0?COLORS.green:COLORS.red);
    }else if(d.type==='longPosition'){
      const entry=d.p1.price,target=d.p2.price,stop=d.stopPrice??entry-(target-entry)/2,sc=coord({time:d.p2.time,price:stop});
      ctx.fillStyle='rgba(8,153,129,.18)';ctx.fillRect(Math.min(a.x,b.x),Math.min(a.y,b.y),Math.abs(b.x-a.x),Math.abs(b.y-a.y));
      ctx.strokeStyle=COLORS.green;line(ctx,{x:Math.min(a.x,b.x),y:b.y},{x:Math.max(a.x,b.x),y:b.y});
      ctx.fillStyle='rgba(242,54,69,.18)';ctx.fillRect(Math.min(a.x,b.x),Math.min(a.y,sc.y),Math.abs(b.x-a.x),Math.abs(sc.y-a.y));
      ctx.strokeStyle=COLORS.red;line(ctx,{x:Math.min(a.x,b.x),y:sc.y},{x:Math.max(a.x,b.x),y:sc.y});
      const rr=Math.abs((target-entry)/(entry-stop));label(ctx,`Long · R:R ${rr.toFixed(2)}`,Math.min(a.x,b.x),a.y,COLORS.green);
    }
  }
  if(runtime.selectedDrawing===d.id){
    ctx.strokeStyle=COLORS.orange;ctx.fillStyle='#10151d';ctx.setLineDash([]);ctx.lineWidth=2;
    ctx.beginPath();ctx.arc(a.x,a.y,6,0,Math.PI*2);ctx.fill();ctx.stroke();
    if(b){ctx.beginPath();ctx.arc(b.x,b.y,6,0,Math.PI*2);ctx.fill();ctx.stroke();}
  }
  ctx.restore();
}
function drawAll(){
  if(!runtime.mainChart)return;resizeCanvas();const ctx=els.drawingCanvas.getContext('2d');ctx.clearRect(0,0,els.drawingCanvas.clientWidth,els.drawingCanvas.clientHeight);
  for(const d of drawingStore())drawShape(ctx,d);if(runtime.drawingPreview)drawShape(ctx,runtime.drawingPreview,true);
}
function setTool(tool){
  runtime.tool=tool;runtime.drawingStart=null;runtime.drawingPreview=null;runtime.drawingAwaitSecond=false;
  if(tool!=='cursor')runtime.selectedDrawing=null;
  els.leftbar.querySelectorAll('[data-tool]').forEach(b=>b.classList.toggle('active',b.dataset.tool===tool));
  const manualTool=tool==='manualGalka'||tool==='manualMove';
  els.drawingCanvas.classList.toggle('drawing',manualTool||(tool!=='cursor'&&tool!=='crosshair'&&!store.ui.drawingsLocked));
  els.drawingCanvas.classList.toggle('dragging-level',tool==='manualMove');
  syncDrawingInteraction();
  if(tool==='crosshair')runtime.mainChart.applyOptions({crosshair:{mode:LWC.CrosshairMode.Normal}});
  drawAll();
}
function addDrawing(d){
  snapshotDrawings();d.id='D'+Date.now()+Math.random().toString(16).slice(2,6);d.color=d.color||els.drawingColor?.value||COLORS.blue;d.width=d.width||num(els.drawingWidth?.value,2);if(!d.dash){const style=els.drawingDash?.value;d.dash=style==='dashed'?[7,5]:style==='dotted'?[2,4]:[];}drawingStore().push(d);save();renderObjects();drawAll();
}
function drawingDown(e){
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
}
function drawingMove(e){
  if(runtime.drawingEdit){
    const edit=runtime.drawingEdit,d=drawingStore().find(item=>item.id===edit.id),point=eventPoint(e,false);if(!d||!point)return;
    if(Math.hypot(e.clientX-edit.startX,e.clientY-edit.startY)>6){edit.moved=true;clearTimeout(runtime.longPressTimer);}
    if(edit.mode==='p1')d.p1=point;
    else if(edit.mode==='p2')d.p2=point;
    else{
      const dt=point.time-edit.start.time,dp=point.price-edit.start.price;d.p1={time:edit.original.p1.time+dt,price:edit.original.p1.price+dp};
      if(edit.original.p2)d.p2={time:edit.original.p2.time+dt,price:edit.original.p2.price+dp};
    }
    drawAll();return;
  }
  if(runtime.manualDrag){const p=eventPoint(e,false);if(p)updateManualLevel(p.price,false);return;}
  if(!runtime.drawingStart)return;const p=eventPoint(e);if(!p)return;
  runtime.drawingPreview={type:runtime.tool,p1:runtime.drawingStart,p2:p,color:els.drawingColor?.value||COLORS.blue,width:num(els.drawingWidth?.value,2)};drawAll();
}
function drawingUp(e){
  if(runtime.drawingEdit){
    clearTimeout(runtime.longPressTimer);const moved=runtime.drawingEdit.moved;runtime.drawingEdit=null;
    try{els.drawingCanvas.releasePointerCapture(e.pointerId);}catch(_){}
    if(moved){save();renderObjects();drawAll();}return;
  }
  if(runtime.manualDrag){
    const p=eventPoint(e,false),fallback=runtime.manualDragOriginal;runtime.manualDrag=false;
    const ok=p&&updateManualLevel(p.price,true);if(!ok&&fallback)updateManualLevel(fallback,true,true);
    runtime.manualDragOriginal=null;try{els.drawingCanvas.releasePointerCapture(e.pointerId);}catch(_){}setTool('cursor');return;
  }
  if(runtime.drawingAwaitSecond)return;
  if(!runtime.drawingStart)return;const p=eventPoint(e);
  if(p)addDrawing({type:runtime.tool,p1:runtime.drawingStart,p2:p});
  runtime.drawingStart=null;runtime.drawingPreview=null;try{els.drawingCanvas.releasePointerCapture(e.pointerId);}catch(_){}
  drawAll();
}
function renderObjects(){
  const rows=drawingStore();
  els.objectsList.innerHTML=rows.length?rows.map(d=>`<div class="object-row ${runtime.selectedDrawing===d.id?'active':''}" data-id="${esc(d.id)}">
    <span>${esc(d.type)} <small>${d.locked?'LOCKED':''}</small></span>
    <button data-action="toggle">${d.hidden?'○':'◉'}</button><button data-action="remove">×</button>
  </div>`).join(''):'<div class="card muted">Объектов нет.</div>';
}

/* Alerts */
function renderAlerts(){
  const a=store.ui.alerts;
  els.alertsList.innerHTML=a.length?a.map(x=>`<div class="alert-row"><span><b>${esc(x.symbol)}</b><small>${x.direction==='above'?'выше':'ниже'} ${price(x.price,x.symbol)} ${esc(x.note||'')}</small></span><button data-alert-toggle="${x.id}">${x.active?'●':'○'}</button><button data-alert-delete="${x.id}">×</button></div>`).join(''):'<div class="card muted">Алертов нет.</div>';
}
function processAlerts(symbol,current,previous){
  if(previous==null)return;
  let changed=false;
  for(const a of store.ui.alerts){
    if(!a.active||a.symbol!==symbol)continue;
    const hit=a.direction==='above'?(previous<a.price&&current>=a.price):(previous>a.price&&current<=a.price);
    if(hit){a.active=false;a.triggeredAt=nowIso();changed=true;toast(`Алерт ${symbol}: ${a.direction==='above'?'выше':'ниже'} ${price(a.price,symbol)}`,'alert');beep();if('Notification' in window&&Notification.permission==='granted')new Notification('Galka Alert',{body:`${symbol} ${a.direction==='above'?'выше':'ниже'} ${price(a.price,symbol)}`});}
  }
  if(changed){save();renderAlerts();}
}
function beep(){
  try{const ac=new (window.AudioContext||window.webkitAudioContext)(),o=ac.createOscillator(),g=ac.createGain();o.connect(g);g.connect(ac.destination);o.frequency.value=880;g.gain.value=.08;o.start();o.stop(ac.currentTime+.15);}catch(_){}
}


/* Explainable Radar is isolated from the paper engine and never creates campaigns. */
function decorateRadarCandidates(candidates){
  const pack=runtime.galkaStats,rows=chartRows();
  if(!pack||!pack.data.intervals.includes(runtime.interval))return candidates;
  const seconds=intervalSeconds(runtime.interval);
  return candidates.map(candidate=>{
    const features=researchFeaturesAt(rows,candidate.index,runtime.interval);
    const confirmation=rows[candidate.index+6];
    const confirmationAt=confirmation?(confirmation.time+seconds)*1000:NaN;
    if(!features||!Number.isFinite(confirmationAt)||confirmationAt>Date.now())return candidate;
    try{
      const classified=classifyGalka(pack,features);
      return {...candidate,symbol:runtime.symbol,interval:runtime.interval,galkaType:classified.type,typeDistance:classified.distance,featureCoverage:classified.featureCoverage,modelHash:classified.modelHash,confirmationAt};
    }catch(error){console.warn('Galka classifier:',error);return candidate;}
  });
}
function syncShadowCandidates(candidates){
  const pack=runtime.galkaStats;
  if(!pack||!store.shadow.enabled)return 0;
  let registered=0;
  for(const candidate of candidates){
    if(!candidate.galkaType||!candidate.confirmationAt)continue;
    const profile=pack.profiles?.[candidate.galkaType]?.[store.shadow.profile];
    const result=registerShadowCandidate(store.shadow,{...candidate,candidateId:candidate.patternId,type:candidate.galkaType,profile:store.shadow.profile,manualLabel:radarLabel(candidate)?.label||null},profile,{modelVersion:pack.modelVersion,modelHash:pack.model.model_hash});
    if(result.registered)registered++;
  }
  if(registered){
    logActivity('shadow',`Shadow: добавлено новых Radar-кандидатов ${registered}`,{registered,profile:store.shadow.profile,paperBalanceImpact:0});
    save();renderActivity();renderShadow();
  }
  return registered;
}
async function ensureGalkaStats(){
  if(runtime.galkaStats)return runtime.galkaStats;
  if(runtime.galkaStatsPromise)return runtime.galkaStatsPromise;
  runtime.galkaStatsError=null;els.labPackStatus.textContent='Проверка…';els.labPackStatus.className='stream-badge';
  runtime.galkaStatsPromise=loadGalkaStatsPack().then(pack=>{
    runtime.galkaStats=pack;runtime.galkaStatsError=null;els.labPackStatus.textContent='Verified';els.labPackStatus.className='stream-badge ok';
    const types=Object.values(pack.model.type_names);if(!types.includes(store.ui.lab.type))store.ui.lab.type=types[0];
    scanRadar();renderRadar();renderLab();save();return pack;
  }).catch(error=>{
    runtime.galkaStatsError=error;els.labPackStatus.textContent='Ошибка';els.labPackStatus.className='stream-badge error';renderLab();throw error;
  }).finally(()=>{runtime.galkaStatsPromise=null;});
  return runtime.galkaStatsPromise;
}
function scanRadar(){
  if(!store.ui.radar?.enabled&&!store.shadow.enabled){runtime.radarCandidates=[];return[];}
  const rows=chartRows(),visible=runtime.mainChart?.timeScale().getVisibleLogicalRange?.();let fromIndex=14,toIndex=rows.length-4;
  if(store.ui.radar.visibleOnly&&visible){fromIndex=Math.max(14,Math.floor(visible.from)-6);toIndex=Math.min(rows.length-4,Math.ceil(visible.to)+6);}
  const candidates=decorateRadarCandidates(computeRadarCandidates({rows,symbol:runtime.symbol,interval:runtime.interval,minScore:store.ui.radar.minScore,manualExamples:store.training.manualExamples,intervalSeconds:intervalSeconds(runtime.interval),fromIndex,toIndex}));
  runtime.radarCandidates=candidates;
  if(runtime.radarSelected)runtime.radarSelected=candidates.find(x=>x.patternId===runtime.radarSelected.patternId)||null;
  syncShadowCandidates(candidates);
  return candidates;
}
function visibleRadarCandidates(){
  const filter=store.ui.radar?.filter||'all',candidates=runtime.radarCandidates||[];
  if(filter==='strong')return filterRadarCandidates(candidates,'strong');
  if(filter==='mine')return candidates.filter(candidate=>candidate.manualMatch||radarLabel(candidate)?.label==='positive');
  if(filter==='profitable'||filter==='losing')return candidates.filter(candidate=>{
    const ev=candidate.galkaType&&runtime.galkaStats?aggregateFinalOos(runtime.galkaStats,candidate.galkaType)?.balanced_net_return_pct_mean:null;
    return displayNumber(ev)&&(filter==='profitable'?Number(ev)>=0:Number(ev)<0);
  });
  return candidates;
}
function radarLabel(candidate){return store.training.radarLabels.filter(x=>x.patternId===candidate?.patternId&&x.symbol===runtime.symbol&&x.interval===runtime.interval).at(-1)||null;}
function selectRadarCandidate(candidate,openSheet=false){
  runtime.radarSelected=candidate||null;renderRadar();updateMarkers();
  if(candidate){try{runtime.mainChart.timeScale().scrollToPosition(candidate.index-chartRows().length+12,false);}catch(_){}if(openSheet)showMobilePanel('radar');}
}
function renderRadar(){
  const on=!!store.ui.radar?.enabled,c=on?visibleRadarCandidates():[],all=on?(runtime.radarCandidates||[]):[],strong=all.filter(x=>x.strength==='strong').length,medium=all.filter(x=>x.strength==='medium').length,weak=all.length-strong-medium;
  els.radarBtn.classList.toggle('active',on);els.radarBtn.setAttribute('aria-pressed',String(on));els.radarLegend.classList.toggle('hidden',!on);
  if(on)els.radarLegend.innerHTML=`<b>Radar ${c.length}</b><span><i class="radar-dot strong"></i>${strong}</span><span><i class="radar-dot medium"></i>${medium}</span><span><i class="radar-dot weak"></i>${weak}</span>`;
  els.radarPanelToggle.checked=on;els.radarMinScore.value=store.ui.radar.minScore;els.radarMinScoreValue.textContent=store.ui.radar.minScore;els.radarVisibleOnly.checked=!!store.ui.radar.visibleOnly;
  els.radarContext.textContent=`${runtime.symbol.replace('USDT','')} · ${runtime.interval}`;els.radarCount.textContent=c.length;
  els.radarFilters.querySelectorAll('[data-radar-filter]').forEach(button=>button.classList.toggle('active',button.dataset.radarFilter===(store.ui.radar.filter||'all')));
  els.radarCandidatesList.innerHTML=c.map(candidate=>{const label=radarLabel(candidate);return `<button class="radar-candidate ${runtime.radarSelected?.patternId===candidate.patternId?'active':''}" data-radar-id="${esc(candidate.patternId)}" type="button"><span><b>${Math.round(candidate.score)}/100</b><i class="radar-dot ${candidate.strength}"></i></span><small>${fmtTime(candidate.time)}</small><small>${candidate.galkaType?esc(candidate.galkaType):candidate.manualMatch?'★ ручное совпадение':label?label.label==='positive'?'✓ это галка':'× не галка':candidate.strength}</small></button>`;}).join('');
  const selected=runtime.radarSelected,label=radarLabel(selected);els.radarDetail.classList.toggle('empty',!selected);
  els.radarScore.textContent=selected?Math.round(selected.score):'—';els.radarStrength.textContent=selected?(selected.strength==='strong'?'Сильный кандидат':selected.strength==='medium'?'Средний кандидат':'Слабый кандидат'):'Выбери кандидата';els.radarCandidateTime.textContent=selected?fmtTime(selected.time):'Коснись метки на графике или списка';
  els.radarDropAtr.textContent=selected?selected.dropAtr.toFixed(2)+' ATR':'—';els.radarRecovery.textContent=selected?(selected.recovery*100).toFixed(0)+'%':'—';els.radarBalance.textContent=selected?(selected.balance*100).toFixed(0)+'%':'—';els.radarSharpness.textContent=selected?selected.sharpness.toFixed(2):'—';els.radarCloseLift.textContent=selected?selected.closeLift.toFixed(2):'—';els.radarManualMatch.textContent=selected?(selected.manualMatch?'Да':'Нет'):'—';
  els.radarExplanation.textContent=selected?`Score ${selected.score}: падение ${selected.dropAtr.toFixed(2)} ATR, восстановление ${(selected.recovery*100).toFixed(0)}%, баланс плеч ${(selected.balance*100).toFixed(0)}%, sharpness ${selected.sharpness.toFixed(2)}, close lift ${selected.closeLift.toFixed(2)}.${label?` Твоя оценка: ${label.label==='positive'?'это галка':'не галка'}.`:''}`:'Radar использует только свечи вокруг V-образной точки и показывает вклад понятных признаков.';
  if(selected?.galkaType&&runtime.galkaStats){
    const pack=runtime.galkaStats,block=blockSummary(pack,{type:selected.galkaType}),aggregate=aggregateFinalOos(pack,selected.galkaType),recent=pack.statistics.recent.find(row=>row.galka_type===selected.galkaType&&row.window==='90d'),profile=pack.profiles[selected.galkaType]?.Balanced,cliff=probabilityCliff(pack,{symbol:runtime.symbol,interval:runtime.interval,type:selected.galkaType,window:'all'}),grid=profile?.depths_pct||[],invalidation=pack.stops.probability[selected.galkaType];
    const warning=cliff&&!cliff.insufficient_data?`Ниже ${pctPoint(cliff.cliff_depth_pct)} вероятность падает ${pct(cliff.probability_before)} → ${pct(cliff.probability_after)}.`:`Probability stop research: ${pctPoint(invalidation)}; устойчивый local cliff не подтверждён.`;
    els.radarStatsEvidence.classList.remove('muted');els.radarStatsEvidence.innerHTML=`<span class="type-chip" style="--lab-accent:${GALKA_TYPE_COLORS[selected.galkaType]||COLORS.purple}">${esc(selected.galkaType)}</span><b>Final OOS · n=${compactNumber(block?.event_count)} · Return 6h ${pct(block?.return_6h_probability)} [${pct(block?.return_6h_block_ci_low)}, ${pct(block?.return_6h_block_ci_high)}]</b><small>Return 24h ${pct(block?.return_24h_probability)} · depth p50/p75/p90 ${pctPoint(block?.depth_success_p50)} / ${pctPoint(block?.depth_success_p75)} / ${pctPoint(block?.depth_success_p90)}</small><small>Recent 90d p75 ${pctPoint(recent?.depth_p75)} (${signedPctPoint(recent?.depth_p75_delta)}) · grid ${pctPoint(grid[0])}–${pctPoint(grid.at(-1))} · Balanced EV ${signedPctPoint(aggregate?.balanced_net_return_pct_mean)}</small><small>Model features ${pct(selected.featureCoverage,0)} · cross-market feature imputed from train median</small><small class="down">${esc(warning)}</small>`;
  }else{
    els.radarStatsEvidence.classList.add('muted');els.radarStatsEvidence.textContent=selected?(runtime.galkaStatsError?'Galka Lab недоступен: '+runtime.galkaStatsError.message:'Тип появится после полной research-confirmation (+6 свечей) на 5m–1h.'):'Galka Lab загрузит тип и историческую статистику без изменения Radar score.';
  }
  els.radarPositive.disabled=!selected;els.radarNegative.disabled=!selected;
}
function toggleRadar(){
  store.ui.radar.enabled=!store.ui.radar.enabled;runtime.radarSelected=null;save();scanRadar();renderRadar();updateMarkers();
  if(store.ui.radar.enabled)ensureGalkaStats().catch(()=>{});
  logActivity('radar',store.ui.radar.enabled?'Radar включён':'Radar выключен',{count:runtime.radarCandidates.length});save();renderActivity();
  toast(store.ui.radar.enabled?`Radar включён: ${runtime.radarCandidates.length} кандидатов`:'Radar выключен');
}
function radarCandidateColor(c){return c.manualMatch?COLORS.cyan:(GALKA_TYPE_COLORS[c.galkaType]||(c.strength==='strong'?COLORS.green:c.strength==='medium'?COLORS.orange:COLORS.gray));}
function onRadarChartClick(param){
  if(!store.ui.radar?.enabled||runtime.tool!=='cursor'||!param?.time||!runtime.radarCandidates.length)return;
  const t=typeof param.time==='number'?param.time:Math.floor(Date.UTC(param.time.year,param.time.month-1,param.time.day)/1000),maxGap=intervalSeconds(runtime.interval)*2;
  let best=null,gap=Infinity;for(const c of runtime.radarCandidates){const d=Math.abs(c.time-t);if(d<gap){gap=d;best=c;}}
  if(!best||gap>maxGap)return;selectRadarCandidate(best,true);
  toast(`Radar ${Math.round(best.score)}/100 · ${best.dropAtr.toFixed(2)} ATR · recovery ${(best.recovery*100).toFixed(0)}%`,'alert');
}
function onChartClick(param){
  if(runtime.tool==='cursor'&&param?.point){const drawing=drawingHitAt(param.point);if(drawing){selectDrawing(drawing.id);return;}if(runtime.selectedDrawing){selectDrawing(null);return;}}
  onRadarChartClick(param);
}
function labelRadarCandidate(value){
  const candidate=runtime.radarSelected;if(!candidate)return;
  store.training.radarLabels.push({id:`RL-${Date.now()}-${store.training.radarLabels.length+1}`,patternId:candidate.patternId,symbol:runtime.symbol,interval:runtime.interval,time:candidate.time,level:candidate.level,score:candidate.score,features:{dropAtr:candidate.dropAtr,recovery:candidate.recovery,balance:candidate.balance,sharpness:candidate.sharpness,closeLift:candidate.closeLift},label:value,labeledAt:nowIso()});
  labelShadowCandidate(store.shadow,candidate.patternId,value);
  logActivity('radar',value==='positive'?'Radar: отмечено «Это галка»':'Radar: отмечено «Не галка»',{patternId:candidate.patternId,score:candidate.score});save();renderRadar();renderActivity();toast('Оценка сохранена');
}
function moveRadarSelection(delta){
  const candidates=visibleRadarCandidates();if(!candidates.length)return;let index=candidates.findIndex(item=>item.patternId===runtime.radarSelected?.patternId);index=index<0?(delta>0?0:candidates.length-1):(index+delta+candidates.length)%candidates.length;selectRadarCandidate(candidates[index]);
}

/* Galka Lab consumes a signed, static research pack. Historical evidence cannot mutate paper. */
function labOptions(){return{symbol:store.ui.lab.symbol,interval:store.ui.lab.interval,type:store.ui.lab.type,window:store.ui.lab.window,profile:store.ui.lab.profile,regime:store.ui.lab.regime||'all'};}
function renderLabControls(){
  els.labSymbol.value=store.ui.lab.symbol;els.labInterval.value=store.ui.lab.interval;els.labType.value=store.ui.lab.type;els.labWindow.value=store.ui.lab.window;els.labProfile.value=store.ui.lab.profile;els.labRegime.value=store.ui.lab.regime||'all';
}
function rankLabel(value){return String(value||'').replaceAll('_',' ').replace(/reclaim (\d{3}) trail (\d{3})/,(_,a,b)=>`reclaim ${(Number(a)/100).toFixed(2)} · trail ${(Number(b)/100).toFixed(2)}`);}
function renderHeatmap(conditional){
  if(!conditional?.depths?.length){els.labHeatmap.className='lab-heatmap muted';els.labHeatmap.textContent='Для этого среза недостаточно данных.';return;}
  const horizons=conditional.depths[0][1].map(row=>row[0]);
  els.labHeatmap.className='lab-heatmap';els.labHeatmap.innerHTML=`<table><thead><tr><th>Depth</th>${horizons.map(hour=>`<th>${hour}h</th>`).join('')}</tr></thead><tbody>${conditional.depths.map(([depth,rows])=>`<tr><td>${Number(depth).toFixed(2)}%</td>${rows.map(([,probability,low,high,sample,insufficient])=>`<td class="heat ${insufficient?'insufficient':''}" style="--heat:${Math.round(num(probability)*100)}" title="n=${sample} · CI ${pct(low)}–${pct(high)}">${probability==null?'—':pct(probability,0)}${insufficient?'*':''}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
function renderLabCharts(pack,options,conditional){
  const histogram=pack.statistics.histograms.filter(row=>row.symbol===options.symbol&&row.interval===options.interval&&row.galka_type===options.type&&row.window===options.window),histMax=Math.max(0,...histogram.map(row=>num(row.count_all_complete)));
  els.labHistogramCaption.textContent=histogram.length?`${options.symbol} ${options.interval} · ${options.window}`:`${options.window}: distribution отсутствует в compact pack`;
  els.labDepthHistogram.innerHTML=histogram.length?histogram.map(row=>`<div class="lab-bar-row"><span>${Number(row.depth_from_pct).toFixed(2)}${row.depth_to_pct==null?'+':`–${Number(row.depth_to_pct).toFixed(2)}`}%</span><span class="lab-bar-track"><i style="--bar:${histMax?num(row.count_all_complete)/histMax*100:0}"></i></span><b title="returned / complete">${compactNumber(row.count_returned)}/${compactNumber(row.count_all_complete)}</b></div>`).join(''):'<small class="muted">Доступно для 90d, 365d и all.</small>';
  const depthRows=(conditional?.depths||[]).map(([depth,rows])=>{const point=rows.find(row=>row[0]===24);return{depth,probability:point?.[1],sample:point?.[4],insufficient:point?.[5]};});
  els.labDepthCurve.innerHTML=depthRows.length?depthRows.map(row=>`<div class="lab-bar-row ${row.insufficient?'muted':''}"><span>${Number(row.depth).toFixed(2)}%</span><span class="lab-bar-track"><i style="--bar:${num(row.probability)*100}"></i></span><b>${pct(row.probability,0)}${row.insufficient?'*':''}</b></div>`).join(''):'<small class="muted">Нет conditional rows для периода.</small>';
  const survival=pack.statistics.survival.filter(row=>row.symbol===options.symbol&&row.interval===options.interval&&row.galka_type===options.type&&row.window===options.window&&displayNumber(row.cumulative_return_probability));
  if(survival.length){
    const width=320,height=98,pad=12,maxMinutes=Math.max(...survival.map(row=>num(row.minutes))),maxLog=Math.log1p(maxMinutes),points=survival.map(row=>[pad+Math.log1p(num(row.minutes))/maxLog*(width-pad*2),height-pad-num(row.cumulative_return_probability)*(height-pad*2)]),line=points.map((point,index)=>`${index?'L':'M'}${point[0].toFixed(1)},${point[1].toFixed(1)}`).join(' '),area=`${line} L${points.at(-1)[0].toFixed(1)},${height-pad} L${points[0][0].toFixed(1)},${height-pad} Z`;
    els.labSurvival.innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Cumulative return probability over time"><path class="curve-area" d="${area}"></path><path class="curve-line" d="${line}"></path><text x="${pad}" y="${height-1}">${survival[0].minutes}m</text><text x="${width-pad}" y="${height-1}" text-anchor="end">${Math.round(maxMinutes/60)}h</text><text x="${width-pad}" y="10" text-anchor="end">${pct(survival.at(-1).cumulative_return_probability)}</text></svg>`;
  }else els.labSurvival.innerHTML='<small class="muted">Доступно для 90d, 365d и all.</small>';
  const types=Object.values(pack.model.type_names),frequencies=types.map(type=>aggregateFinalOos(pack,type)),maxFrequency=Math.max(...frequencies.map(row=>row.count_candidates));
  els.labTypeFrequency.innerHTML=frequencies.map(row=>`<div class="lab-bar-row"><span title="${esc(row.galka_type)}">${esc(row.galka_type)}</span><span class="lab-bar-track"><i style="--bar:${row.count_candidates/maxFrequency*100};background:${GALKA_TYPE_COLORS[row.galka_type]||COLORS.purple}"></i></span><b>${compactNumber(row.count_candidates)}</b></div>`).join('');
  const performances=frequencies.map(row=>({type:row.galka_type,value:row.balanced_net_return_pct_mean})),maxPerformance=Math.max(...performances.map(row=>Math.abs(num(row.value))),.001);
  els.labTypePerformance.innerHTML=performances.map(row=>`<div class="lab-bar-row ${num(row.value)>=0?'positive':'negative'}"><span title="${esc(row.type)}">${esc(row.type)}</span><span class="lab-bar-track"><i style="--bar:${Math.abs(num(row.value))/maxPerformance*100}"></i></span><b>${signedPctPoint(row.value)}</b></div>`).join('');
}
function renderShadow(){
  const state=store.shadow,summary=summarizeShadow(state);els.shadowToggle.checked=state.enabled;
  els.shadowStatus.className=`shadow-status ${state.enabled?'on':'muted'}`;els.shadowStatus.textContent=state.enabled?`Включён с ${new Date(state.startedAt).toLocaleString('ru-RU')} · ${state.profile} · новые research-confirmed Radar-кандидаты`:'Выключен по умолчанию. Исторические кандидаты задним числом не добавляются.';
  els.shadowMetrics.innerHTML=`<span><small>Records</small><b>${summary.total}</b></span><span><small>Active</small><b>${summary.active}</b></span><span><small>Complete</small><b>${summary.completed}</b></span><span><small>Fill rate</small><b>${pct(summary.fillRate)}</b></span><span><small>Return rate</small><b>${pct(summary.returnRate)}</b></span><span><small>Manual labels</small><b>${summary.manualCompared}</b></span><span class="${num(summary.meanNetReturnPct)>=0?'positive':'negative'}"><small>Mean candidate EV</small><b>${signedPctPoint(summary.meanNetReturnPct)}</b></span><span><small>Paper impact</small><b>$0.00</b></span>`;
  els.shadowRecords.innerHTML=(state.records||[]).slice(-30).reverse().map(record=>`<div class="shadow-record"><span><b>${esc(record.symbol.replace('USDT',''))} · ${esc(record.type)}</b><small>${esc(record.profile)} · ${new Date(record.confirmationAt).toLocaleString('ru-RU')} · depth ${pctPoint(record.maxDepthPct)}</small></span><span><b data-status="${esc(record.status)}">${esc(record.status)}</b><small>${record.netReturnPct==null?`${record.levels.filter(level=>level.status==='filled').length}/${record.levels.length} fills`:signedPctPoint(record.netReturnPct)}</small></span></div>`).join('');
}
function renderLab(){
  renderLabControls();renderShadow();
  const pack=runtime.galkaStats;
  if(!pack){
    els.labPackStatus.textContent=runtime.galkaStatsError?'Ошибка':'Не загружен';els.labPackStatus.className=`stream-badge ${runtime.galkaStatsError?'error':''}`;
    els.labDataMeta.textContent=runtime.galkaStatsError?`Пакет отклонён: ${runtime.galkaStatsError.message}`:'Пакет загружается только при открытии Lab.';
    for(const element of [els.labOosMetrics,els.labDepthMetrics,els.labDepthHistogram,els.labDepthCurve,els.labSurvival,els.labTypeFrequency,els.labTypePerformance,els.labProfileLevels,els.labGridCards,els.labStopCards,els.labExitCards])element.innerHTML='';
    els.labHeatmap.className='lab-heatmap muted';els.labHeatmap.textContent='Ожидание проверенного пакета…';els.labDrift.textContent='—';els.labCliff.textContent='—';els.labRegimes.textContent='—';els.labPortability.textContent='—';return;
  }
  els.labPackStatus.textContent='Verified';els.labPackStatus.className='stream-badge ok';
  const options=labOptions(),insight=galkaInsight(pack,options),aggregate=insight.finalOosAggregate,block=insight.block,selected=insight.summary,profile=insight.profile;
  const balancedBlocked=num(aggregate?.balanced_net_return_pct_mean)>=0?false:true,conservativeBlocked=num(aggregate?.conservative_net_return_pct_mean)>=0?false:true;
  els.labSafetyBanner.className='lab-safety';els.labSafetyBanner.textContent=balancedBlocked||conservativeBlocked?`PROMOTION BLOCKED · Final OOS ${options.type}: Conservative ${signedPctPoint(aggregate?.conservative_net_return_pct_mean)}, Balanced ${signedPctPoint(aggregate?.balanced_net_return_pct_mean)}. Shadow-only; auto-paper остаётся выключен.`:`OOS gate пройден только статистически; live shadow всё равно обязателен. Auto-paper остаётся выключен.`;
  if(options.profile==='Aggressive')els.labSafetyBanner.textContent+=` Aggressive — stress-test only (${signedPctPoint(aggregate?.aggressive_net_return_pct_mean)} OOS).`;
  els.labDataMeta.innerHTML=`<b>${esc(pack.modelVersion)} · schema ${esc(pack.schemaVersion)}</b><br>${new Date(pack.data.start).toLocaleDateString('ru-RU')}–${new Date(pack.data.end).toLocaleDateString('ru-RU')} · ${esc(options.symbol)} ${esc(options.interval)} ${esc(options.window)} · selected n=${compactNumber(selected?.count_candidates)} · checksum verified`;
  const activation=aggregate?.count_candidates?aggregate.count_activated/aggregate.count_candidates:null,returnRate=aggregate?.count_complete?aggregate.count_returned/aggregate.count_complete:null;
  els.labOosMetrics.innerHTML=`<span><small>Candidates</small><b>${compactNumber(aggregate?.count_candidates)}</b></span><span><small>Activation</small><b>${pct(activation)}</b></span><span><small>Any return</small><b>${pct(returnRate)}</b></span><span><small>Return 1h</small><b>${pct(block?.return_1h_probability)}</b></span><span><small>Return 24h</small><b>${pct(block?.return_24h_probability)}</b></span><span><small>Return 48h</small><b>${pct(block?.return_48h_probability)}</b></span><span class="negative"><small>Balanced EV</small><b>${signedPctPoint(aggregate?.balanced_net_return_pct_mean)}</b></span><span><small>Return p50</small><b>${num(aggregate?.return_minutes_p50).toFixed(1)}m</b></span><span><small>Return p90</small><b>${num(aggregate?.return_minutes_p90).toFixed(1)}m</b></span><span><small>MAE mean</small><b>${pctPoint(aggregate?.mae_mean_pct)}</b></span><span><small>MFE after reclaim</small><b>${pctPoint(aggregate?.mfe_after_reclaim_mean_pct)}</b></span><span><small>UTC-day blocks</small><b>${compactNumber(block?.block_count)}</b></span>`;
  els.labDepthMetrics.innerHTML=['50','75','90','95','99'].map(q=>`<span><small>Depth p${q}</small><b>${pctPoint(block?.[`depth_success_p${q}`])}</b></span>`).join('');
  renderLabCharts(pack,options,insight.conditional);
  renderHeatmap(insight.conditional);
  els.labProfileLevels.innerHTML=profile?profile.depths_pct.map((depth,index)=>`<span>${Number(depth).toFixed(2)}% · ${pct(profile.weights[index],0)}</span>`).join(''):'';
  const oosKeys={Conservative:'conservative_net_return_pct_mean',Balanced:'balanced_net_return_pct_mean',Aggressive:'aggressive_net_return_pct_mean'};
  els.labGridCards.innerHTML=insight.grids.map(row=>{const oos=num(aggregate?.[oosKeys[row.profile]],NaN);return `<div class="lab-compare-card ${row.profile===options.profile?'active':''} ${oos>=0?'positive':'negative'}"><small>${esc(row.profile)}${row.stress_test_only?' · STRESS':''}</small><b>${signedPctPoint(oos)} OOS</b><small>all-history ${signedPctPoint(row.mean_net_return_pct)} · fill ${pct(row.fill_probability)} · CVaR ${pctPoint(row.cvar_95_pct)}</small></div>`;}).join('');
  const stops=[...insight.stops].sort((a,b)=>num(b.mean_net_return_pct)-num(a.mean_net_return_pct)),exits=[...insight.exits].sort((a,b)=>num(b.mean_net_return_pct)-num(a.mean_net_return_pct)).slice(0,6);
  els.labStopCards.innerHTML=`<small class="muted">Top all-history stops</small>${stops.map((row,index)=>`<div class="lab-rank-row"><span><b>${index+1}. ${esc(rankLabel(row.stop))}</b><small>n=${compactNumber(row.count)} · CVaR ${pctPoint(row.cvar_95_pct)}</small></span><b>${signedPctPoint(row.mean_net_return_pct)}</b></div>`).join('')}`;
  els.labExitCards.innerHTML=`<small class="muted">Top all-history exits</small>${exits.map((row,index)=>`<div class="lab-rank-row"><span><b>${index+1}. ${esc(rankLabel(row.exit))}</b><small>n=${compactNumber(row.count)} · CVaR ${pctPoint(row.cvar_95_pct)}</small></span><b>${signedPctPoint(row.mean_net_return_pct)}</b></div>`).join('')}`;
  const recent=pack.statistics.recent.filter(row=>row.galka_type===options.type);
  els.labDrift.innerHTML=`<b>Recent drift · all markets/TF · p75 / return 24h</b><br>${recent.map(row=>`${esc(row.window)}: ${pctPoint(row.depth_p75)} (${signedPctPoint(row.depth_p75_delta)}) / ${pct(row.return_24h_probability)}${row.insufficient_data?' · n<40':''}`).join('<br>')}`;
  const cliff=insight.cliff||probabilityCliff(pack,{...options,window:'all'})||probabilityCliff(pack,{...options,window:'90d'});
  els.labCliff.innerHTML=cliff&&!cliff.insufficient_data?`<b>Probability cliff · ${esc(cliff.window)}</b><br>${pctPoint(cliff.cliff_depth_pct)}: ${pct(cliff.probability_before)} → ${pct(cliff.probability_after)} (−${(Math.abs(num(cliff.probability_drop))*100).toFixed(1)} pp)${cliff.significant_cliff?' · significant':' · exploratory'}`:'<b>Probability cliff</b><br>В выбранном срезе устойчивый cliff не подтверждён (minimum n=40).';
  const regimes=pack.statistics.regimes.filter(row=>row.galka_type===options.type&&(options.regime==='all'||row.regime===options.regime)).sort((a,b)=>num(b.balanced_net_return_pct_mean)-num(a.balanced_net_return_pct_mean)),best=regimes[0],worst=regimes.at(-1);
  els.labRegimes.innerHTML=`<b>Market regimes · all history · ${esc(options.regime)}</b><br>Лучший: ${esc(best?.regime||'—')} / ${esc(best?.volatility_regime||'—')} · ${signedPctPoint(best?.balanced_net_return_pct_mean)}<br>Худший: ${esc(worst?.regime||'—')} / ${esc(worst?.volatility_regime||'—')} · ${signedPctPoint(worst?.balanced_net_return_pct_mean)}`;
  const portability=pack.statistics.blockBootstrap.filter(row=>row.scope==='symbol'&&row.galka_type===options.type&&row.split==='final_oos');
  els.labPortability.innerHTML=`<b>Final OOS portability · block CI 24h</b><br>${portability.map(row=>`${esc(row.symbol.replace('USDT',''))}: ${pct(row.return_24h_probability)} [${pct(row.return_24h_block_ci_low)}, ${pct(row.return_24h_block_ci_high)}] · p75 ${pctPoint(row.depth_success_p75)}`).join('<br>')}`;
}
function updateLabFilters(){
  store.ui.lab={symbol:els.labSymbol.value,interval:els.labInterval.value,type:els.labType.value,window:els.labWindow.value,profile:els.labProfile.value,regime:els.labRegime.value};store.shadow.profile=store.ui.lab.profile;save();renderLab();
}
async function toggleShadowMode(){
  const enable=els.shadowToggle.checked;
  if(enable){
    try{
      const pack=await ensureGalkaStats();store.shadow.profile=store.ui.lab.profile;setShadowEnabled(store.shadow,true,Date.now(),{modelVersion:pack.modelVersion,modelHash:pack.model.model_hash});
      if(!store.ui.radar.enabled)store.ui.radar.enabled=true;
      logActivity('shadow',`Shadow mode включён · ${store.shadow.profile}`,{paperBalanceImpact:0,autoPaper:false,realOrders:false});save();scanRadar();renderRadar();updateMarkers();toast('Shadow включён · Radar активирован','alert');
    }catch(error){els.shadowToggle.checked=false;toast('Shadow не включён: '+error.message,'error');}
  }else{
    setShadowEnabled(store.shadow,false);logActivity('shadow','Shadow mode выключен',{paperBalanceImpact:0});save();toast('Shadow выключен');
  }
  renderLab();renderActivity();
}
function exportShadowRecords(){
  download(`galka-shadow-${Date.now()}.json`,new Blob([JSON.stringify({kind:'galka-shadow-v1',exportedAt:nowIso(),modelVersion:store.shadow.modelVersion,paperBalanceImpact:0,state:store.shadow},null,2)],{type:'application/json'}));
}

/* Galka paper bot */
function atrAt(rows,index,period=14){return atrValue(rows,index,period);}
function evaluatePattern(rows,index,symbol){
  const L=4,R=4;if(index<L||index+R>=rows.length)return null;const cur=rows[index],w=rows.slice(index-L,index+R+1);
  if(cur.low!==Math.min(...w.map(x=>x.low)))return null;const a=atrAt(rows,index);if(!a)return null;
  const left=Math.max(...rows.slice(index-L,index).map(x=>x.high)),right=Math.max(...rows.slice(index+1,index+R+1).map(x=>x.high)),drop=left-cur.low,recovery=(right-cur.low)/Math.max(drop,1e-12);
  if(drop<1.5*a||recovery<.65)return null;
  return{patternId:`${symbol}-${cur.time}`,vLow:cur.low,vLowTime:cur.time,confirmedTime:rows[index+R].time,atr:a,dropAtr:drop/a,recovery,status:'watching',createdAt:nowIso()};
}
function scanRecentPatterns(){
  if(store.paper.settings.signalMode!=='auto')return;
  for(const s of SYMBOLS){
    const rows=botRows(s);let found=null;
    for(let i=Math.max(14,rows.length-350);i<rows.length-4;i++){const p=evaluatePattern(rows,i,s);if(p)found=p;}
    if(found&&!store.paper.symbols[s].campaign&&(Date.now()/1000-found.confirmedTime)/3600<=336)store.paper.symbols[s].pattern=found;
  }save();renderPaper();
}
function detectLatestPattern(symbol){
  if(store.paper.settings.signalMode!=='auto')return;
  const rows=botRows(symbol),i=rows.length-5,p=evaluatePattern(rows,i,symbol);if(!p)return;
  const ss=store.paper.symbols[symbol];if(!ss.campaign&&ss.pattern?.patternId!==p.patternId){ss.pattern=p;save();renderPaper();updateMarkers();}
}
function campaignLadder(st,p){
  return buildCampaignLadder(st,p);
}
function createCampaign(symbol,p,nowMs=Date.now()){
  return createPaperCampaign(symbol,p,store.paper.settings,nowMs);
}
function editableManualCampaign(symbol=runtime.symbol){
  const c=store.paper.symbols[symbol]?.campaign;
  return c?.source==='manual'&&!c.qty&&!c.levels.some(x=>x.status==='filled')?c:null;
}
function manualLevelAllowed(symbol,value){
  if(!(value>0))return{ok:false,message:'Укажи корректную цену уровня'};
  const step=clamp(num(store.paper.settings.ladderStepPct,.15),.05,2),firstEntry=value*(1-step/100),ask=runtime.quotes[symbol]?.ask;
  if(ask&&ask<=firstEntry)return{ok:false,message:'Цена уже ниже первой лимитки — такой уровень ставить поздно'};
  return{ok:true};
}
function updateManualLevel(value,final=true,force=false){
  const symbol=runtime.symbol,c=editableManualCampaign(symbol);if(!c){if(final)toast('Уровень можно двигать только до первой покупки','error');return false;}
  const next=num(value),valid=force?{ok:next>0}:manualLevelAllowed(symbol,next);if(!valid.ok){if(final)toast(valid.message,'error');return false;}
  if(!moveManualCampaign(c,next,store.paper.settings)){if(final)toast('Уровень уже заблокирован первой покупкой','error');return false;}
  const p=store.paper.symbols[symbol].pattern;if(p?.patternId===c.patternId)p.vLow=next;
  if(c.trainingExampleId){const x=store.training.manualExamples.find(v=>v.id===c.trainingExampleId);if(x){x.level=next;x.updatedAt=nowIso();}}
  els.manualGalkaPrice.value=price(next,symbol);if(final){logActivity('paper',`${symbol.replace('USDT','')}: GALKA перемещена`,{level:next});save();renderActivity();}renderPaper();updateMarkers();return true;
}
function beginManualMove(p,e){
  const c=editableManualCampaign();if(!c){toast('Двигать уровень можно только до первой покупки','error');setTool('cursor');return;}
  runtime.manualDrag=true;runtime.manualDragOriginal=c.vLow;try{els.drawingCanvas.setPointerCapture(e.pointerId);}catch(_){}
  updateManualLevel(p.price,false);els.manualLevelHint.textContent='Тяни вверх или вниз. Отпусти палец для фиксации.';
}
function applyManualPrice(){
  const value=num(els.manualGalkaPrice.value),c=editableManualCampaign();
  if(c){updateManualLevel(value,true);return;}
  const active=store.paper.symbols[runtime.symbol].campaign;if(active?.qty||active?.levels?.some(x=>x.status==='filled'))return toast('Сначала закрой открытую позицию','error');
  const last=chartRows().at(-1);if(!last)return toast('График ещё не загружен','error');setManualGalka({time:last.time,price:value});
}
function startManualMove(){
  if(!editableManualCampaign())return toast('Сначала установи уровень; после покупки двигать его нельзя','error');
  setTool('manualMove');closeMobileOverlays();toast('Коснись графика и тяни уровень вверх или вниз');
}
function setManualGalka(p){
  if(runtime.replay.active){markReplayGalka(p);setTool('cursor');return;}
  const symbol=runtime.symbol,ss=store.paper.symbols[symbol],active=ss.campaign;
  if(active?.qty){toast('Сначала закрой открытую позицию по этой монете','error');setTool('cursor');return;}
  const allowed=manualLevelAllowed(symbol,p.price);if(!allowed.ok){toast(allowed.message,'error');setTool('cursor');return;}
  const preview=previewCampaign(p.price,store.paper.settings);runtime.pendingManual={...p,symbol,interval:runtime.interval};
  els.previewGalka.textContent=price(p.price,symbol);els.previewSymbol.textContent=`${symbol.replace('USDT','')} · ${runtime.interval}`;els.previewFirst.textContent=price(preview.first.price,symbol);els.previewLast.textContent=price(preview.last.price,symbol);els.previewCount.textContent=preview.count;els.previewNotional.textContent=money(preview.totalNotional);els.previewAverage.textContent=price(preview.averageEntry,symbol);els.previewPnl.textContent=signedMoney(preview.estimatedPnlAtGalka);
  setTool('cursor');openModal(els.pretradeModal);
}
function confirmManualGalka(){
  const p=runtime.pendingManual;if(!p)return;const symbol=p.symbol,ss=store.paper.symbols[symbol],active=ss.campaign;
  const allowed=manualLevelAllowed(symbol,p.price);if(!allowed.ok){toast(allowed.message,'error');closePretrade();return;}
  if(active?.trainingExampleId){const old=store.training.manualExamples.find(x=>x.id===active.trainingExampleId);if(old)old.status='superseded';}
  const rows=chartRows(),idx=nearestIndex(rows,p.time),id='M-'+symbol+'-'+Date.now(),context=rows.slice(Math.max(0,idx-39),idx+1).map(c=>({time:c.time,open:c.open,high:c.high,low:c.low,close:c.close,volume:c.volume}));
  const pattern={patternId:id,source:'manual',trainingExampleId:id,vLow:p.price,vLowTime:p.time,confirmedTime:p.time,atr:atrValue(rows,idx,14)||Math.max(p.price*.001,1e-9),dropAtr:0,recovery:0,status:'trading',createdAt:nowIso()};
  store.training.manualExamples.push({id,symbol,interval:p.interval,level:p.price,selectedCandleTime:p.time,selectedAt:nowIso(),status:'active',context,features:computeRadarFeatureAt(rows,idx)});
  ss.pattern=pattern;ss.campaign=createCampaign(symbol,pattern);logActivity('paper',`${symbol.replace('USDT','')}: GALKA установлена`,{level:p.price,levels:ss.campaign.levels.length,notional:store.paper.settings.symbolNotional});save();closePretrade();renderPaper();renderActivity();updateMarkers();setTool('cursor');renderSimpleTradeBar();closeMobileOverlays();
  toast(`${symbol}: уровень галки ${price(p.price,symbol)}, лимитки выставлены`,'alert');
}
function closePretrade(){runtime.pendingManual=null;els.pretradeModal.classList.add('hidden');}
function cancelManualSelection(){
  const ss=store.paper.symbols[runtime.symbol],c=ss.campaign;
  if(c?.qty)return toast('Нельзя снять уровень: уже есть покупки','error');
  if(c?.trainingExampleId){const x=store.training.manualExamples.find(v=>v.id===c.trainingExampleId);if(x)x.status='cancelled';}
  ss.campaign=null;if(ss.pattern?.source==='manual')ss.pattern.status='cancelled';logActivity('paper',`${runtime.symbol.replace('USDT','')}: GALKA и лимитки сняты`);save();renderPaper();renderActivity();updateMarkers();toast('Ручной уровень снят');
}
function exportManualExamples(){
  const payload={version:VERSION,exportedAt:nowIso(),examples:store.training.manualExamples};
  download(`galka-manual-examples-${Date.now()}.json`,new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
}
function recalcCampaign(c){
  recalculateCampaign(c);
}
function recordL1Cycle(symbol,event,{atMs=Date.now(),recovered=false,deferRender=false}={}){
  const ss=store.paper.symbols[symbol],c=ss.campaign;if(!c||!event)return null;
  const cycle=num(event.cycle,num(c.l1Cycles,1)),cycleKey=c.campaignId+':L1:'+cycle;if(store.paper.trades.some(trade=>trade.cycleKey===cycleKey))return null;
  const exitTime=new Date(num(atMs,Date.now())).toISOString(),fees=num(event.entryFees)+num(event.exitFee),net=num(event.netPnl);
  const trade={tradeId:'P'+String(store.paper.trades.length+1).padStart(6,'0'),campaignId:c.campaignId,patternId:c.patternId,cycleKey,cycleOnly:true,finalCampaign:false,l1Cycle:cycle,symbol,side:'long',entryTime:event.fillTime||c.createdAt,exitTime,averageEntry:event.averageEntry,exitPrice:event.price,qty:event.qty,filledNotional:event.filledNotional,levelsFilled:1,levelsTotal:c.levels.length,grossPnl:event.grossPnl,fees,netPnl:net,reason:'l1_cycle_target',vLow:c.vLow,exitMode:'target',executionSource:recovered?'recovery':'live',recoveryPolicy:recovered?RECOVERY_PATH_POLICY:null};
  if(c.trainingExampleId){const x=store.training.manualExamples.find(v=>v.id===c.trainingExampleId);if(x){x.l1Cycles=cycle;x.l1CyclePnl=num(x.l1CyclePnl)+net;x.updatedAt=exitTime;}}
  store.paper.trades.push(trade);store.paper.realizedPnl+=net;store.paper.fees+=fees;logActivity('paper',`${symbol.replace('USDT','')}: L1 цикл ${cycle} закрыт ${signedMoney(net)} и перезаряжен`,{reason:trade.reason,tradeId:trade.tradeId,recovered,cycle},trade.exitTime);save();if(!deferRender){renderPaper();renderActivity();updateMarkers();renderSimpleTradeBar();}return trade;
}
function closeCampaign(symbol,rawExit,reason,{atMs=Date.now(),recovered=false,deferRender=false}={}){
  const ss=store.paper.symbols[symbol],c=ss.campaign;if(!c?.qty)return;
  if(store.paper.trades.some(trade=>trade.campaignId===c.campaignId&&trade.finalCampaign===true)){ss.campaign=null;save();return;}
  const st=store.paper.settings,exit=reason==='v_low_target'?rawExit:rawExit*(1-st.slippage),exitNotional=c.qty*exit,exitFee=exitNotional*(reason==='v_low_target'?st.makerFee:st.takerFee),gross=c.qty*(exit-c.averageEntry),net=gross-c.entryFees-exitFee;const exitTime=new Date(num(atMs,Date.now())).toISOString();
  const trade={tradeId:'P'+String(store.paper.trades.length+1).padStart(6,'0'),campaignId:c.campaignId,patternId:c.patternId,cycleOnly:false,finalCampaign:true,symbol,side:'long',entryTime:c.levels.find(x=>x.status==='filled')?.fillTime||c.createdAt,exitTime,averageEntry:c.averageEntry,exitPrice:exit,qty:c.qty,filledNotional:c.filledNotional,levelsFilled:c.levels.filter(x=>x.status==='filled').length,levelsTotal:c.levels.length,grossPnl:gross,fees:c.entryFees+exitFee,netPnl:net,reason,vLow:c.vLow,exitMode:c.exitMode||'target',l1Cycles:num(c.l1Cycles),l1CycleRealizedPnl:num(c.l1CycleRealizedPnl),trailActivatedAt:c.trailActivatedAt||null,trailHigh:c.trailHigh||null,trailStop:c.trailStop||null,executionSource:recovered?'recovery':'live',recoveryPolicy:recovered?RECOVERY_PATH_POLICY:null};
  if(c.trainingExampleId){const x=store.training.manualExamples.find(v=>v.id===c.trainingExampleId);if(x)Object.assign(x,{status:'closed',exitTime:trade.exitTime,exitPrice:trade.exitPrice,netPnl:num(x.l1CyclePnl)+trade.netPnl,reason:trade.reason,levelsFilled:trade.levelsFilled,levelsTotal:trade.levelsTotal,l1Cycles:trade.l1Cycles,trailHigh:trade.trailHigh});}
  store.paper.trades.push(trade);store.paper.realizedPnl+=net;store.paper.fees+=trade.fees;ss.campaign=null;if(ss.pattern?.patternId===c.patternId)ss.pattern.status=reason;logActivity(net>=0?'paper':'risk',`${symbol.replace('USDT','')}: GALKA завершена ${signedMoney(net)} · L1 циклов ${num(c.l1Cycles)}`,{reason,tradeId:trade.tradeId,recovered,l1Cycles:num(c.l1Cycles)},trade.exitTime);save();if(!deferRender){renderPaper();renderActivity();updateMarkers();renderSimpleTradeBar();}
}
function accountSnapshot(prices=null){
  const campaigns={},marketPrices={};for(const symbol of SYMBOLS){campaigns[symbol]=store.paper.symbols[symbol].campaign;marketPrices[symbol]=prices?.[symbol]??runtime.quotes[symbol].bid;}
  return paperPortfolioSnapshot(campaigns,marketPrices,store.paper.settings,store.paper.realizedPnl);
}
function checkGlobalLiquidation({atMs=Date.now(),recovered=false,deferRender=false,prices=null}={}){
  const snap=accountSnapshot(prices);if(!snap.notional||snap.equity>snap.maintenance)return[];
  const closed=[];for(const symbol of SYMBOLS){const c=store.paper.symbols[symbol].campaign,bid=num(prices?.[symbol]??runtime.quotes[symbol].bid);if(c?.qty&&bid>0){closed.push(symbol);closeCampaign(symbol,bid,'paper_liquidation',{atMs,recovered,deferRender});}}
  return closed;
}
function processBotQuote(symbol,{quote=runtime.quotes[symbol],nowMs=Date.now(),source='live',suppressRender=false}={}){
  const q=quote;if(!q.bid||!q.ask)return;
  const ss=store.paper.symbols[symbol],p=ss.pattern;let changed=false;
  if(store.paper.settings.signalMode==='auto'&&!ss.campaign&&p&&p.status==='watching'){
    const age=(nowMs/1000-p.confirmedTime)/3600;
    if(age<=336&&q.bid<p.vLow-.10*p.atr){ss.campaign=createCampaign(symbol,p,nowMs);p.status='trading';changed=true;}
  }
  const c=ss.campaign;
  if(c){
    const result=processCampaignQuote(c,{bid:q.bid,ask:q.ask},store.paper.settings,nowMs);changed=changed||result.changed;
    const restored=source==='buffered',eventAt=new Date(nowMs).toISOString();
    for(const event of result.events){
      if(event.type==='level_filled')logActivity('paper',`${symbol.replace('USDT','')}: L${event.level} исполнена`,{price:event.price,recovered:restored},eventAt);
      if(event.type==='l1_cycle_closed'){recordL1Cycle(symbol,event,{atMs:nowMs,recovered:restored,deferRender:true});if(symbol===runtime.symbol&&!suppressRender)toast(`${symbol.replace('USDT','')}: L1 закрыта ${signedMoney(event.netPnl)} и поставлена снова`,'alert');}
      if(event.type==='trailing_armed'){logActivity('paper',`${symbol.replace('USDT','')}: trailing активирован`,{stop:event.stop,recovered:restored},eventAt);if(symbol===runtime.symbol&&!suppressRender)toast(`${symbol}: trailing активирован, стоп ${price(event.stop,symbol)}`,'alert');}
      if(event.type==='trailing_raised')logActivity('paper',`${symbol.replace('USDT','')}: stop поднят`,{stop:event.stop,recovered:restored},eventAt);
    }
    if(result.close){closeCampaign(symbol,result.close.price,result.close.reason,{atMs:nowMs,recovered:restored,deferRender:suppressRender});return;}
    if(result.expiredWithoutFill){ss.campaign=null;if(p)p.status='expired';changed=true;logActivity('paper',`${symbol.replace('USDT','')}: кампания истекла без fill`,{recovered:restored},eventAt);}
  }
  if(changed){save();if(!suppressRender){renderPaper();renderActivity();updateMarkers();}}
  checkGlobalLiquidation({atMs:nowMs,recovered:source==='buffered',deferRender:suppressRender});
}
function renderPaperHeader(){
  const s=accountSnapshot();els.equity.textContent=money(s.equity);els.openPnl.textContent=signedMoney(s.unreal);els.openPnl.className=s.unreal>=0?'up':'down';els.realizedPnl.textContent=signedMoney(store.paper.realizedPnl);els.realizedPnl.className=store.paper.realizedPnl>=0?'up':'down';els.marginUsed.textContent=money(s.margin);
  const online=runtime.connectionState==='ok',age=runtime.lastQuoteAt?Date.now()-runtime.lastQuoteAt:Infinity;els.paperStreamBadge.textContent=online&&age<10000?'LIVE':online?'STALE':'OFFLINE';els.paperStreamBadge.className='stream-badge '+(online&&age<10000?'ok':'error');
}
function durationUntil(timestamp){if(!timestamp)return'—';const ms=timestamp-Date.now();if(ms<=0)return'истекла';const hours=Math.floor(ms/3600000),minutes=Math.floor(ms%3600000/60000);return hours?`${hours}ч ${minutes}м`:`${minutes}м`;}
function renderPaper(){
  renderPaperHeader();const symbol=runtime.symbol,ss=store.paper.symbols[symbol],p=ss.pattern,c=ss.campaign;
  els.manualExamplesCount.textContent=store.training.manualExamples.length;
  els.paperPortfolioCards.innerHTML=SYMBOLS.map(item=>{const state=store.paper.symbols[item],campaign=state.campaign,quote=runtime.quotes[item],filled=campaign?.levels?.filter(level=>level.status==='filled').length||0,total=campaign?.levels?.length||0,pnl=campaign?.qty&&quote.bid?campaign.qty*(quote.bid-campaign.averageEntry)-campaign.entryFees:0,status=campaign?(campaign.status==='trailing'?'TRAILING':campaign.qty?'OPEN':'WAITING'):'IDLE',level=campaign?.vLow||state.pattern?.vLow;return `<article class="portfolio-card ${item===symbol?'active':''}" data-paper-symbol="${item}" tabindex="0"><span class="coin-mark">${item.replace('USDT','')}</span><span class="portfolio-main"><span><b>${level?price(level,item):'Без GALKA'}</b><small>${filled}/${total||'—'} fills</small></span><span class="portfolio-progress"><i style="width:${total?filled/total*100:0}%"></i></span><small>${campaign?`Цель ${price(campaign.vLow,item)}${num(campaign.l1Cycles)?` · L1×${num(campaign.l1Cycles)}`:''}`:'Можно поставить уровень'}</small></span><span class="portfolio-side"><span class="status-label ${campaign?.status||''}">${status}</span><b class="${pnl>=0?'up':'down'}">${campaign?.qty?signedMoney(pnl):price(quote.last,item)}</b><small>${campaign?durationUntil(campaign.expiresAt):'поток '+(quote.updated?'есть':'—')}</small></span></article>`;}).join('');
  els.paperNavBadge.classList.toggle('visible',SYMBOLS.some(item=>!!store.paper.symbols[item].campaign));
  const editable=editableManualCampaign(symbol),shownLevel=c?.vLow||(p?.source==='manual'?p.vLow:null);
  if(document.activeElement!==els.manualGalkaPrice)els.manualGalkaPrice.value=shownLevel?price(shownLevel,symbol):'';
  els.moveManualGalka.disabled=!editable;els.cancelManualGalka.disabled=!c;
  els.manualLevelHint.textContent=c?.qty?'Есть покупки: уровень зафиксирован.':editable?'Можно ввести цену или двигать линию вместе со всеми лимитками.':'Укажи цену или выбери уровень на графике.';
  els.botTitle.textContent=`Galka ${store.paper.settings.signalMode==='manual'?'manual':'auto'} · ${symbol.replace('USDT','')}`;els.botState.textContent=c?(c.status==='trailing'?'Трейлинг':c.status==='open'?'Позиция':'Лимитки'):p?'Галка выбрана':'Ожидание';
  if(c){
    const filled=c.levels.filter(x=>x.status==='filled').length,quote=runtime.quotes[symbol],pnl=c.qty&&quote.bid?c.qty*(quote.bid-c.averageEntry)-c.entryFees:0;
    els.campaignCard.innerHTML=`<div class="campaign-topline"><span><small>${c.source==='manual'?'РУЧНАЯ GALKA':'AUTO EXPERIMENT'}</small><strong>${price(c.vLow,symbol)}</strong></span><span class="status-label ${c.status}">${esc(c.status.toUpperCase())}</span></div><div class="campaign-metrics"><span><small>Средняя</small><b>${price(c.averageEntry,symbol)}</b></span><span><small>Номинал</small><b>${money(c.filledNotional)}</b></span><span><small>Open PnL</small><b class="${pnl>=0?'up':'down'}">${signedMoney(pnl)}</b></span><span><small>Reclaim</small><b>${price(c.reclaimPrice,symbol)}</b></span><span><small>Trail stop</small><b class="${c.trailArmed?'down':''}">${price(c.trailStop,symbol)}</b></span><span><small>Истекает</small><b>${durationUntil(c.expiresAt)}</b></span></div>`;
    els.levelsList.innerHTML=c.levels.map(l=>{const state=l.status==='filled'?'FILLED':l.status==='cancelled'?'CANCELLED':'WAIT';return `<div class="level-row ${l.status}"><span class="level-index">L${l.index}</span><span class="level-main"><span><b>${price(l.price,symbol)}</b><small>−${l.depthPct}%</small></span><span class="level-progress"><i style="width:${l.status==='filled'?100:0}%"></i></span><small>${money(l.notional)}${l.fillTime?' · '+fmtTime(Math.floor(Date.parse(l.fillTime)/1000)):''}</small></span><b class="level-state">${state}</b></div>`;}).join('');
    els.fillsCount.textContent=filled+'/'+c.levels.length;els.ladderSummary.textContent=`${filled} исполнено · ${money(c.levels.reduce((sum,level)=>sum+level.notional,0))}`;
  }else{
    const detail=p?(p.source==='manual'?'Выбран вручную':`Drop ${num(p.dropAtr).toFixed(2)} ATR · recovery ${(num(p.recovery)*100).toFixed(0)}%`):'';
    els.campaignCard.innerHTML=p?`<div class="campaign-topline"><span><small>ПОСЛЕДНЯЯ GALKA</small><strong>${price(p.vLow,symbol)}</strong></span><span class="status-label">${esc(p.status)}</span></div><small>${detail}</small>`:'<span>Нажми «На графике» или введи точную цену. Перед созданием лестницы появится безопасный preview.</span>';
    els.levelsList.innerHTML='<div class="muted" style="padding:12px;text-align:center">Активных лимиток нет.</div>';els.fillsCount.textContent='0';els.ladderSummary.textContent='Ожидает GALKA';
  }
  els.tradeHistory.innerHTML=store.paper.trades.length?store.paper.trades.slice().reverse().slice(0,100).map(t=>`<div class="trade-item"><div><b>${esc(t.symbol)}</b><b class="${t.netPnl>=0?'up':'down'}">${signedMoney(t.netPnl)}</b></div><small>${fmtTime(Math.floor(Date.parse(t.exitTime)/1000))} · ${esc(t.reason)} · ${t.levelsFilled}/${t.levelsTotal||6}</small></div>`).join(''):'<div class="muted">Сделок пока нет.</div>';
}
function updateMarkers(){
  if(!runtime.priceSeries)return;
  for(const line of runtime.paperLines){try{runtime.priceSeries.removePriceLine(line);}catch(_){}}runtime.paperLines=[];
  const addPaperLine=(value,color,title,axisLabelVisible=true,lineStyle=LWC.LineStyle.Dashed,lineWidth=2)=>{if(value>0)runtime.paperLines.push(runtime.priceSeries.createPriceLine({price:value,color,lineWidth,lineStyle,axisLabelVisible,title}));};
  const markers=[],symbol=runtime.symbol,ss=store.paper.symbols[symbol],p=ss.pattern,c=ss.campaign;
  scanRadar();renderRadar();
  if(store.ui.radar?.enabled){
    const visible=runtime.mainChart.timeScale().getVisibleLogicalRange?.(),span=visible?Math.max(1,visible.to-visible.from):chartRows().length,bucket=Math.max(1,Math.ceil(span/34)),groups=new Map();
    for(const candidate of visibleRadarCandidates()){
      const groupKey=Math.floor(candidate.index/bucket),group=groups.get(groupKey)||[];group.push(candidate);groups.set(groupKey,group);
    }
    for(const group of groups.values()){
      const r=group.reduce((best,item)=>item.score>best.score?item:best,group[0]),cluster=group.length>1?`×${group.length}`:`${Math.round(r.score)}`;
      markers.push({time:r.time,position:'belowBar',color:radarCandidateColor(r),shape:r.strength==='strong'?'arrowUp':'circle',text:`G${cluster}${r.manualMatch?'★':''}`});
    }
    if(runtime.radarSelected){
      const selected=runtime.radarSelected,color=radarCandidateColor(selected);addPaperLine(selected.level,color,`RADAR ${Math.round(selected.score)}`);
      if(selected.galkaType&&runtime.galkaStats){
        const block=blockSummary(runtime.galkaStats,{type:selected.galkaType}),profile=runtime.galkaStats.profiles[selected.galkaType]?.Balanced,stop=runtime.galkaStats.stops.probability[selected.galkaType];
        for(const percentile of [50,75,90]){const depth=block?.[`depth_success_p${percentile}`];if(displayNumber(depth))addPaperLine(selected.level*(1-Number(depth)/100),color,`p${percentile}`,percentile===90,LWC.LineStyle.Dotted,1);}
        const gridDepth=profile?.depths_pct?.at(-1);if(displayNumber(gridDepth))addPaperLine(selected.level*(1-Number(gridDepth)/100),COLORS.cyan,'GRID',true,LWC.LineStyle.Dashed,1);
        if(displayNumber(stop))addPaperLine(selected.level*(1-Number(stop)/100),COLORS.red,'INVALID',true,LWC.LineStyle.Dashed,1);
      }
    }
  }
  els.levelCluster.classList.add('hidden');
  if(store.ui.showLevels!==false){
    if(p)addPaperLine(p.vLow,p.source==='manual'?COLORS.orange:COLORS.blue,p.source==='manual'?'GALKA':'V-low',true,LWC.LineStyle.Solid);
    if(c){
      const specs=c.levels.map(level=>({...level,y:runtime.priceSeries.priceToCoordinate(level.price)})).sort((a,b)=>num(a.y)-num(b.y)),groups=[];
      for(const spec of specs){const group=groups.at(-1);if(group&&spec.y!=null&&group.at(-1).y!=null&&Math.abs(spec.y-group.at(-1).y)<18)group.push(spec);else groups.push([spec]);}
      const clustered=[];
      for(const group of groups){
        const label=group.length>1?`L${group[0].index}–L${group.at(-1).index}`:`L${group[0].index}`;group.forEach((level,index)=>addPaperLine(level.price,level.status==='filled'?COLORS.green:COLORS.gray,index===0?label:'',index===0));if(group.length>1)clustered.push(`${label}: ${group.length}`);
      }
      if(clustered.length){els.levelCluster.textContent='Сгруппированы '+clustered.join(' · ');els.levelCluster.classList.remove('hidden');}
      if(c.averageEntry)addPaperLine(c.averageEntry,COLORS.cyan,'AVG',true,LWC.LineStyle.Solid);
      if(c.exitMode!=='target'&&c.reclaimPrice)addPaperLine(c.reclaimPrice,COLORS.purple,'RECLAIM');
      if(c.trailArmed&&c.trailStop)addPaperLine(c.trailStop,COLORS.red,'TRAIL STOP',true,LWC.LineStyle.Solid);
    }
  }
  if(p&&p.source!=='manual')markers.push({time:p.vLowTime,position:'belowBar',color:COLORS.blue,shape:'circle',text:'V-low'});
  for(const t of store.paper.trades.filter(x=>x.symbol===symbol).slice(-100)){
    const a=Math.floor(Date.parse(t.entryTime)/1000),b=Math.floor(Date.parse(t.exitTime)/1000);
    if(a)markers.push({time:a,position:'belowBar',color:COLORS.green,shape:'arrowUp',text:'BUY'});
    if(b)markers.push({time:b,position:'aboveBar',color:t.netPnl>=0?COLORS.green:COLORS.red,shape:'square',text:signedMoney(t.netPnl)});
  }
  if(c)for(const l of c.levels.filter(x=>x.status==='filled')){const t=Math.floor(Date.parse(l.fillTime)/1000);markers.push({time:t,position:'belowBar',color:COLORS.green,shape:'arrowUp',text:'L'+l.index});}
  markers.sort((a,b)=>a.time-b.time);
  try{runtime.markerApi?.setMarkers([]);}catch(_){}
  runtime.markerApi=LWC.createSeriesMarkers(runtime.priceSeries,markers);
}

/* Replay */
function startReplay(){
  const rows=chartRows();if(rows.length<100)return;runtime.replay.active=true;runtime.replay.source=rows.map(x=>({...x}));runtime.replay.index=Math.max(50,Math.floor(rows.length*.7));runtime.replay.pendingLabel=null;runtime.replay.revealed=false;
  els.replaySlider.min=50;els.replaySlider.max=rows.length-1;els.replaySlider.value=runtime.replay.index;els.replayPanel.classList.remove('hidden');applyReplay();
  closeMobileOverlays();toast('Replay: будущее скрыто. Ищи галку без подсказки.');
}
function applyReplay(){
  const r=runtime.replay;if(!r.active)return;const rows=r.source.slice(0,r.index+1);runtime.priceSeries.setData(priceDataForType(rows,runtime.chartType));els.replayLabel.textContent=fmtTime(rows.at(-1).time);els.replaySlider.value=r.index;runtime.mainChart.timeScale().scrollToRealTime();updateIndicatorsReplay(rows);
}
function updateIndicatorsReplay(rows){
  const original=runtime.candles.get(key());runtime.candles.set(key(),rows);updateIndicators();runtime.candles.set(key(),original);
}
function replayStep(delta=1){runtime.replay.index=clamp(runtime.replay.index+delta,50,runtime.replay.source.length-1);applyReplay();if(runtime.replay.index>=runtime.replay.source.length-1)pauseReplay();}
function playReplay(){if(runtime.replay.playing){pauseReplay();return;}runtime.replay.playing=true;els.replayPlay.textContent='Ⅱ';runtime.replay.timer=setInterval(()=>replayStep(1),350);}
function pauseReplay(){runtime.replay.playing=false;clearInterval(runtime.replay.timer);els.replayPlay.textContent='▶';}
function markReplayGalka(point=null){
  const replay=runtime.replay;if(!replay.active)return;const visible=replay.source.slice(0,replay.index+1),index=point?Math.min(replay.index,nearestIndex(visible,point.time)):replay.index,candle=visible[index];if(!candle)return;
  const level=point?.price||candle.low;replay.pendingLabel={symbol:runtime.symbol,interval:runtime.interval,index,time:candle.time,level,markedAt:nowIso(),context:visible.slice(Math.max(0,index-39),index+1).map(row=>({...row}))};replay.revealed=false;toast(`Replay: GALKA отмечена ${price(level)}. Пройди свечи или покажи результат.`,'alert');
}
function revealReplayResult(){
  const replay=runtime.replay,label=replay.pendingLabel;if(!replay.active||!label)return toast('Сначала отметь галку','error');if(replay.revealed)return;
  const end=Math.min(replay.source.length-1,label.index+24),future=replay.source.slice(label.index+1,end+1),maxHigh=Math.max(label.level,...future.map(row=>row.high)),minLow=Math.min(label.level,...future.map(row=>row.low)),outcome={bars:future.length,maxLiftPct:(maxHigh/label.level-1)*100,maxDropPct:(minLow/label.level-1)*100,reclaimed:future.some(row=>row.high>=label.level)};
  store.training.replayExamples.push({id:`RP-${Date.now()}-${store.training.replayExamples.length+1}`,...label,revealedAt:nowIso(),outcome});logActivity('radar',`Replay: пример сохранён · ${outcome.reclaimed?'уровень возвращён':'без возврата'}`,outcome);save();renderActivity();replay.revealed=true;replay.index=end;applyReplay();toast(`Результат: максимум ${outcome.maxLiftPct>=0?'+':''}${outcome.maxLiftPct.toFixed(2)}%, просадка ${outcome.maxDropPct.toFixed(2)}%`,'alert');
}
function exitReplay(){pauseReplay();runtime.replay.active=false;runtime.replay.source=null;runtime.replay.pendingLabel=null;els.replayPanel.classList.add('hidden');runtime.priceSeries.setData(priceDataForType(chartRows(),runtime.chartType));updateIndicators();runtime.mainChart.timeScale().scrollToRealTime();}

/* Workspace, templates, screenshot */
function workspacePayload(){return{version:VERSION,createdAt:nowIso(),ui:store.ui};}
function renderTemplates(){
  const t=store.ui.templates;els.templatesList.innerHTML=Object.keys(t).length?Object.entries(t).map(([name])=>`<div class="template-item"><span>${esc(name)}</span><span><button data-template-load="${esc(name)}">Открыть</button><button data-template-delete="${esc(name)}">×</button></span></div>`).join(''):'<div class="card muted">Шаблонов нет.</div>';
}
async function takeSnapshot(){
  try{
    let canvas=runtime.mainChart.takeScreenshot?.();
    if(!canvas)throw new Error('takeScreenshot недоступен');
    const out=document.createElement('canvas');out.width=canvas.width;out.height=canvas.height;const ctx=out.getContext('2d');ctx.drawImage(canvas,0,0);
    const overlay=els.drawingCanvas;ctx.drawImage(overlay,0,0,out.width,out.height);
    out.toBlob(b=>download(`galka-${runtime.symbol}-${runtime.interval}-${Date.now()}.png`,b),'image/png');
  }catch(e){toast('Снимок не создан: '+e.message,'error');}
}
function exportTradesCsv(){
  const cols=['tradeId','symbol','entryTime','exitTime','averageEntry','exitPrice','filledNotional','levelsFilled','fees','netPnl','reason'];
  const lines=[cols.join(','),...store.paper.trades.map(t=>cols.map(c=>csvEscape(t[c])).join(','))];
  download('galka-paper-trades.csv',new Blob([lines.join('\n')],{type:'text/csv;charset=utf-8'}));
}
function exportFullSnapshot({automatic=false}={}){
  const snapshot=createBackupSnapshot(store,VERSION),name=`galka-pro-snapshot-${new Date().toISOString().replaceAll(':','-')}.json`;
  download(name,new Blob([JSON.stringify(snapshot,null,2)],{type:'application/json'}));
  if(!automatic){logActivity('backup','Полный snapshot экспортирован',{name});save();renderActivity();els.lastBackupText.textContent=`Последний экспорт: ${new Date().toLocaleString('ru-RU')}`;}
  return snapshot;
}
async function prepareRestore(file){
  try{
    const snapshot=JSON.parse(await file.text()),summary=summarizeBackupSnapshot(snapshot);runtime.pendingRestore={snapshot,store:validateBackupSnapshot(snapshot)};
    els.restoreSummary.innerHTML=`<span><small>Активные кампании</small><b>${summary.campaigns}</b></span><span><small>Filled уровни</small><b>${summary.filledLevels}</b></span><span><small>Paper-сделки</small><b>${summary.trades}</b></span><span><small>Рисунки</small><b>${summary.drawings}</b></span><span><small>Ручные примеры</small><b>${summary.manualExamples}</b></span><span><small>Radar labels</small><b>${summary.radarLabels}</b></span><span><small>Shadow records</small><b>${summary.shadowRecords}</b></span>`;
    openModal(els.restoreModal);
  }catch(error){runtime.pendingRestore=null;toast('Snapshot не принят: '+error.message,'error');}
}
function closeRestore(){runtime.pendingRestore=null;els.restoreModal.classList.add('hidden');els.importSnapshot.value='';}
function confirmRestore(){
  if(!runtime.pendingRestore)return;const current=createBackupSnapshot(store,VERSION);localStorage.setItem(PRE_RESTORE_BACKUP_KEY,JSON.stringify(current));exportFullSnapshot({automatic:true});
  store=migrateStore(runtime.pendingRestore.store);logActivity('backup','Snapshot импортирован после автоматического backup');save();runtime.pendingRestore=null;location.reload();
}
function renderActivity(){
  const rows=(store.activity||[]).slice(-120).reverse();els.activityLog.innerHTML=rows.length?rows.map(event=>`<div class="activity-item ${esc(event.type||'')}"><i class="activity-dot"></i><span><b>${esc(event.message)}</b><small>${new Date(event.at).toLocaleString('ru-RU')}</small></span></div>`).join(''):'<div class="muted" style="padding:12px;text-align:center">Журнал пока пуст.</div>';
}
function ageText(timestamp){if(!timestamp)return'нет данных';const seconds=Math.max(0,Math.floor((Date.now()-timestamp)/1000));if(seconds<2)return'сейчас';if(seconds<60)return`${seconds}с`;const minutes=Math.floor(seconds/60);return minutes<60?`${minutes}м`:`${Math.floor(minutes/60)}ч ${minutes%60}м`;}
function renderSessionHealth(){
  const online=runtime.connectionState==='ok',quoteAge=runtime.lastQuoteAt?Date.now()-runtime.lastQuoteAt:Infinity,healthy=online&&quoteAge<10000&&!runtime.recovering,status=runtime.recovering?'Восстанавливаем paper-события':healthy?'Сессия здорова':online?'Котировки задерживаются':'Поток недоступен',kind=healthy?'ok':online?'warn':'error';
  els.sessionStatus.textContent=status;els.sessionStatusDot.className='dot '+kind;els.sessionWs.textContent=online?'Online':runtime.connectionState==='warn'?'Connecting':'Offline';els.sessionQuoteAge.textContent=ageText(runtime.lastQuoteAt);els.sessionTab.textContent=document.hidden?'Заморожена / скрыта':'Активна';els.sessionEngineGap.textContent=ageText(runtime.lastEngineAt);els.chartHealth.className='chart-health '+kind;els.chartHealthText.textContent=runtime.recovering?'Paper replay · 1m':healthy?'Поток '+ageText(runtime.lastQuoteAt):status;els.paperStreamBadge?.classList.toggle('ok',healthy);
}

const onboardingSteps=[
  {icon:'BTC',title:'Выбери рынок',text:'BTC, ETH и SOL обслуживаются paper-движком одновременно. На графике открыт один рынок.'},
  {icon:'G',title:'Поставь GALKA',text:'Коснись уровня на графике или введи точную цену. Сначала увидишь безопасный preview лестницы.'},
  {icon:'L1',title:'Проверь лимитки',text:'WAIT меняется на FILLED только при достижении цены. После первого fill уровень GALKA блокируется.'},
  {icon:'↗',title:'Reclaim и trailing',text:'Возврат выше reclaim активирует trailing. Stop не опускается и никогда не ниже GALKA.'},
  {icon:'⌁',title:'Используй Radar',text:'Radar объясняет score и принимает оценки «Это галка» / «Не галка», но не открывает сделки.'},
];
function renderOnboarding(){const step=onboardingSteps[runtime.onboardingIndex];els.onboardingVisual.textContent=step.icon;els.onboardingStep.textContent=`ШАГ ${runtime.onboardingIndex+1} ИЗ ${onboardingSteps.length}`;els.onboardingTitle.textContent=step.title;els.onboardingText.textContent=step.text;els.nextOnboarding.textContent=runtime.onboardingIndex===onboardingSteps.length-1?'Готово':'Дальше';els.onboardingProgress.querySelectorAll('span').forEach((item,index)=>item.classList.toggle('active',index<=runtime.onboardingIndex));}
function startOnboarding(){runtime.onboardingIndex=0;renderOnboarding();openModal(els.onboardingModal);}
function finishOnboarding(){store.ui.onboarding.completed=true;store.ui.onboarding.version=1;save();els.onboardingModal.classList.add('hidden');}


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
  const style=document.createElement('style');style.id='simpleGalkaStyle';style.textContent=`
    :root{--simple-bar:74px;--bottom-nav:0px}
    .mobile-nav,#radarBtn,.chart-actions,.chart-health,.ohlc,.radar-legend,.chart-attribution{display:none!important}
    #connectionText{display:none!important}.connection-button{width:36px!important;padding:0!important}
    .terminal-grid{height:calc(100dvh - var(--top) - var(--safe-top) - var(--simple-bar) - var(--safe-bottom))!important;grid-template-columns:1fr!important}
    .workspace{grid-column:1!important}
    #toggleSidebar{display:inline-flex!important}
    .drawing-pill{display:flex!important;bottom:10px!important}
    .sidebar{display:none!important}
    .sidebar.open{display:flex!important;position:fixed!important;z-index:180!important;left:8px!important;right:8px!important;top:auto!important;bottom:calc(var(--simple-bar) + var(--safe-bottom) + 8px)!important;width:auto!important;height:min(68dvh,680px)!important;max-height:calc(100dvh - var(--top) - var(--simple-bar) - 20px)!important;transform:none!important;border:1px solid var(--line)!important;border-radius:20px!important;box-shadow:var(--shadow)!important;background:var(--panel)!important}
    .sidebar .side-panel{display:none!important}.sidebar .side-panel.active{display:block!important}
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
  `;document.head.append(style);
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

/* Rendering and UI */
function renderDiagnostics(){
  const s=accountSnapshot();
  els.diagnostics.textContent=JSON.stringify({version:VERSION,storageKey:STORAGE_KEY,symbol:runtime.symbol,interval:runtime.interval,chartType:runtime.chartType,ws:runtime.ws?.readyState,quoteAge:ageText(runtime.lastQuoteAt),tabVisible:!document.hidden,recovering:runtime.recovering,recoveryPolicy:PAPER_RECOVERY_POLICY,lastRecovery:runtime.lastRecoverySummary,lastCatchup:runtime.lastCatchupAt?new Date(runtime.lastCatchupAt).toISOString():null,galkaStats:runtime.galkaStats?.modelVersion||runtime.galkaStatsError?.message||'not-loaded',shadow:summarizeShadow(store.shadow),rows:chartRows().length,botRows:Object.fromEntries(SYMBOLS.map(x=>[x,botRows(x).length])),drawings:drawingStore().length,alerts:store.ui.alerts.filter(x=>x.active).length,equity:s.equity},null,2);
}
function renderAll(){renderWatchlist();renderPaper();renderObjects();renderAlerts();renderTemplates();renderRadar();renderLab();renderActivity();renderDiagnostics();renderSessionHealth();renderTicker();renderSimpleTradeBar();}
function autoCenterActiveMarket({fitTime=false}={}){
  const chart=runtime.mainChart;if(!chart)return;
  const scale=chart.priceScale('right');
  scale.applyOptions({autoScale:true});
  els.autoScaleBtn?.classList.add('active');
  if(fitTime)chart.timeScale().fitContent();else chart.timeScale().scrollToRealTime();
  requestAnimationFrame(()=>{scale.applyOptions({autoScale:true});if(!fitTime)chart.timeScale().scrollToRealTime();});
  setTimeout(()=>scale.applyOptions({autoScale:true}),120);
}
async function changeSymbol(symbol){
  runtime.selectedDrawing=null;syncDrawingInteraction();runtime.radarSelected=null;runtime.symbol=symbol;store.ui.symbol=symbol;els.symbolSelect.value=symbol;els.watermark.textContent=symbol+' · '+runtime.interval;save();
  try{runtime.priceSeries?.setData([]);}catch(_){}
  await loadCurrent(false);
  renderAll();
  autoCenterActiveMarket();
}
async function changeInterval(interval){
  runtime.selectedDrawing=null;syncDrawingInteraction();runtime.radarSelected=null;runtime.interval=interval;store.ui.interval=interval;els.intervalSelect.value=interval;save();connectWs();
  try{runtime.priceSeries?.setData([]);}catch(_){}
  await loadCurrent(false);
  renderAll();
  autoCenterActiveMarket();
}
function changeChartType(type){runtime.chartType=type;store.ui.chartType=type;save();createPriceSeries();loadCurrent(false);}
function openPanel(name){
  document.querySelectorAll('.side-tabs button').forEach(b=>b.classList.toggle('active',b.dataset.panel===name));
  document.querySelectorAll('.side-panel').forEach(p=>p.classList.toggle('active',p.dataset.panelId===name));
  const titles={paper:['Paper','Все три инструмента'],radar:['Radar',`${runtime.symbol.replace('USDT','')} · ${runtime.interval}`],lab:['Galka Lab','OOS статистика · live shadow'],watchlist:['Watchlist','BTC · ETH · SOL'],objects:['Рисование','Объекты и свойства'],more:['More','Сессия, backup и настройки'],alerts:['Алерты','Локальные уведомления'],data:['Данные','Свеча и диагностика']},copy=titles[name]||[name,''];els.sheetTitle.textContent=copy[0];els.sheetSubtitle.textContent=copy[1];
  document.querySelectorAll('.mobile-nav [data-mobile-panel]').forEach(button=>button.classList.toggle('active',button.dataset.mobilePanel===name));store.ui.sheet.panel=name;save();
  if(name==='lab'||name==='radar')ensureGalkaStats().catch(()=>{});if(name==='lab')renderLab();
}
function syncMobileOverlay(){
  const mobile=matchMedia('(max-width:1099px)').matches,open=mobile&&(els.sidebar.classList.contains('open')||els.leftbar.classList.contains('open'));
  els.sheetBackdrop.classList.toggle('open',open);
}
function closeMobileOverlays(){els.leftbar.classList.remove('open');if(matchMedia('(max-width:1099px)').matches){els.sidebar.classList.remove('open');els.sidebar.setAttribute('aria-hidden','true');document.querySelectorAll('.mobile-nav [data-mobile-panel]').forEach(button=>button.classList.toggle('active',button.dataset.mobilePanel==='chart'));}syncMobileOverlay();}
function showMobilePanel(name){openPanel(name);els.leftbar.classList.remove('open');els.sidebar.classList.add('open');els.sidebar.setAttribute('aria-hidden','false');syncMobileOverlay();}
function openModal(el){el.classList.remove('hidden');}
function closeModals(){document.querySelectorAll('.modal').forEach(m=>m.classList.add('hidden'));runtime.pendingManual=null;runtime.pendingRestore=null;}
function beginSheetGesture(event){if(!matchMedia('(max-width:700px) and (orientation:portrait)').matches)return;runtime.sheetGesture={startY:event.clientY,lastY:event.clientY};try{els.sheetHandle.setPointerCapture(event.pointerId);}catch(_){}}
function moveSheetGesture(event){if(!runtime.sheetGesture)return;runtime.sheetGesture.lastY=event.clientY;const delta=Math.max(0,event.clientY-runtime.sheetGesture.startY);els.sidebar.style.transform=`translateY(${delta}px)`;}
function endSheetGesture(event){if(!runtime.sheetGesture)return;const delta=runtime.sheetGesture.lastY-runtime.sheetGesture.startY;els.sidebar.style.transform='';runtime.sheetGesture=null;try{els.sheetHandle.releasePointerCapture(event.pointerId);}catch(_){}if(delta>86){closeMobileOverlays();return;}if(delta<-60){els.sidebar.classList.remove('snap-low');els.sidebar.classList.add('snap-high');store.ui.sheet.snap='high';}else if(delta>40){els.sidebar.classList.remove('snap-high');els.sidebar.classList.add('snap-low');store.ui.sheet.snap='low';}else{els.sidebar.classList.remove('snap-low','snap-high');store.ui.sheet.snap='medium';}save();}
const indicatorDefs=[
  ['sma20','SMA 20','Простая средняя на графике','price'],['ema20','EMA 20','Экспоненциальная средняя','price'],
  ['ema50','EMA 50','Среднесрочная EMA','price'],['bollinger','Bollinger Bands','Полосы 20 / 2σ','price'],
  ['vwap','VWAP','Средняя по объёму','price'],['volume','Volume','Объём под свечами','price'],
  ['rsi','RSI 14','Осциллятор 0–100','lower'],['macd','MACD','12 / 26 / 9','lower'],['atr','ATR 14','Истинный диапазон','lower']
];
function renderIndicatorList(filter=''){
  const q=filter.toLowerCase();
  els.indicatorList.innerHTML=indicatorDefs.filter(x=>(x[1]+' '+x[2]).toLowerCase().includes(q)).map(([id,name,desc,type])=>{
    const active=type==='lower'?store.ui.lowerIndicator===id:!!store.ui.indicators[id];
    return `<div class="indicator-item"><span><b>${esc(name)}</b><small>${esc(desc)}</small></span><button data-indicator="${id}" data-kind="${type}" class="${active?'active':''}">${active?'Добавлен':'Добавить'}</button></div>`;
  }).join('');
}
function intervalSeconds(i){const n=parseInt(i,10);if(i.endsWith('m'))return n*60;if(i.endsWith('h'))return n*3600;if(i.endsWith('d'))return n*86400;return 60;}

/* Events */
els.symbolSelect.value=runtime.symbol;els.intervalSelect.value=runtime.interval;els.chartTypeSelect.value=runtime.chartType;els.compareSelect.value=store.ui.compare;els.scaleMode.value=store.ui.scaleMode;
els.startingBalance.value=store.paper.settings.startingBalance;els.leverage.value=store.paper.settings.leverage;els.symbolNotional.value=store.paper.settings.symbolNotional;els.maxHours.value=store.paper.settings.maxHours;els.signalMode.value=store.paper.settings.signalMode;els.ladderStepPct.value=store.paper.settings.ladderStepPct;els.manualDepthPct.value=store.paper.settings.manualDepthPct;els.exitMode.value=store.paper.settings.exitMode;els.reclaimBufferPct.value=store.paper.settings.reclaimBufferPct;els.trailDistancePct.value=store.paper.settings.trailDistancePct;
els.symbolSelect.onchange=e=>changeSymbol(e.target.value);
els.intervalSelect.onchange=e=>changeInterval(e.target.value);
els.chartTypeSelect.onchange=e=>changeChartType(e.target.value);
els.compareSelect.onchange=async e=>{store.ui.compare=e.target.value;save();await updateCompare();};
els.themeBtn.onclick=()=>{store.ui.theme=store.ui.theme==='dark'?'light':'dark';save();applyTheme();};
els.radarBtn.onclick=()=>showMobilePanel('radar');
els.connectionButton.onclick=()=>showMobilePanel('more');els.chartHealth.onclick=()=>showMobilePanel('more');
els.zoomIn.onclick=()=>zoom(.72);els.zoomOut.onclick=()=>zoom(1.38);
els.fitBtn.onclick=()=>autoCenterActiveMarket({fitTime:true});els.latestBtn.onclick=()=>autoCenterActiveMarket();
els.autoScaleBtn.onclick=()=>{const active=!els.autoScaleBtn.classList.contains('active');els.autoScaleBtn.classList.toggle('active',active);runtime.mainChart.priceScale('right').applyOptions({autoScale:active});};
els.scaleMode.onchange=e=>{store.ui.scaleMode=e.target.value;save();applyScaleMode();};
document.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>setRange(b.dataset.range));
els.indicatorBtn.onclick=()=>{renderIndicatorList();openModal(els.indicatorModal);};
els.indicatorSearch.oninput=e=>renderIndicatorList(e.target.value);
els.indicatorList.onclick=e=>{const b=e.target.closest('[data-indicator]');if(!b)return;const id=b.dataset.indicator,kind=b.dataset.kind;if(kind==='lower')store.ui.lowerIndicator=store.ui.lowerIndicator===id?null:id;else store.ui.indicators[id]=!store.ui.indicators[id];save();renderIndicatorList(els.indicatorSearch.value);updateIndicators();};
els.closePane.onclick=()=>{store.ui.lowerIndicator=null;save();updateIndicators();};
els.alertBtn.onclick=()=>showMobilePanel('alerts');
els.createAlert.onclick=async()=>{const p=num(els.alertPrice.value);if(!p)return toast('Укажи цену алерта','error');store.ui.alerts.push({id:'A'+Date.now(),symbol:els.alertSymbol.value,direction:els.alertDirection.value,price:p,note:els.alertNote.value.trim(),active:true,createdAt:nowIso()});save();renderAlerts();els.alertPrice.value='';els.alertNote.value='';if('Notification' in window&&Notification.permission==='default')try{await Notification.requestPermission();}catch(_){}};
els.alertsList.onclick=e=>{const tid=e.target.dataset.alertToggle,did=e.target.dataset.alertDelete;if(tid){const a=store.ui.alerts.find(x=>x.id===tid);if(a)a.active=!a.active;}if(did)store.ui.alerts=store.ui.alerts.filter(x=>x.id!==did);save();renderAlerts();};
els.watchlist.onclick=e=>{const r=e.target.closest('[data-symbol]');if(r)changeSymbol(r.dataset.symbol);};
els.refreshBtn.onclick=()=>Promise.all(SYMBOLS.map(s=>ensureData(s,'15m',true))).then(()=>{scanRecentPatterns();renderAll();});
els.leftbar.onclick=e=>{const b=e.target.closest('[data-tool]');if(!b)return;const tool=b.dataset.tool;setTool(tool);if(tool==='ray'||tool==='measure'){els.leftbar.classList.remove('open');syncMobileOverlay();toast(tool==='ray'?'Луч: коснись начала, потом направления':'Линейка: коснись начала, потом конца','alert');}};
els.manualGalkaBtn.onclick=()=>{setTool('manualGalka');closeMobileOverlays();toast('Коснись точной цены уровня на графике');};
els.applyManualGalkaPrice.onclick=applyManualPrice;els.manualGalkaPrice.onkeydown=e=>{if(e.key==='Enter')applyManualPrice();};els.manualGalkaPrice.onfocus=()=>{if(matchMedia('(max-width:700px) and (orientation:portrait)').matches){els.sidebar.classList.remove('snap-low');els.sidebar.classList.add('snap-high');}};els.moveManualGalka.onclick=startManualMove;
els.cancelManualGalka.onclick=cancelManualSelection;els.exportManualExamples.onclick=exportManualExamples;
els.confirmPretrade.onclick=confirmManualGalka;document.querySelectorAll('[data-close-pretrade]').forEach(button=>button.onclick=closePretrade);
els.closeSidebarSheet.onclick=closeMobileOverlays;els.sheetBackdrop.onclick=closeMobileOverlays;els.closeTools.onclick=closeMobileOverlays;
els.magnetBtn.onclick=()=>{store.ui.magnet=!store.ui.magnet;els.magnetBtn.classList.toggle('active',store.ui.magnet);save();};
els.lockBtn.onclick=()=>{store.ui.drawingsLocked=!store.ui.drawingsLocked;els.lockBtn.classList.toggle('active',store.ui.drawingsLocked);setTool(runtime.tool);save();};
els.hideDrawingsBtn.onclick=()=>{store.ui.drawingsHidden=!store.ui.drawingsHidden;els.hideDrawingsBtn.classList.toggle('active',store.ui.drawingsHidden);save();drawAll();};
els.undoBtn.onclick=()=>{if(!runtime.undo.length)return;runtime.redo.push(JSON.stringify(drawingStore()));restoreDrawings(runtime.undo.pop());};
els.redoBtn.onclick=()=>{if(!runtime.redo.length)return;runtime.undo.push(JSON.stringify(drawingStore()));restoreDrawings(runtime.redo.pop());};
function selectedDrawing(){return drawingStore().find(item=>item.id===runtime.selectedDrawing)||null;}
function deleteSelectedDrawing(){if(!runtime.selectedDrawing)return;snapshotDrawings();store.ui.drawings[drawingsKey()]=drawingStore().filter(x=>x.id!==runtime.selectedDrawing);selectDrawing(null);save();}
function duplicateSelectedDrawing(){const source=selectedDrawing();if(!source)return toast('Сначала выбери объект','error');snapshotDrawings();const copy=JSON.parse(JSON.stringify(source)),shift=intervalSeconds(runtime.interval)*2;copy.id='D'+Date.now()+Math.random().toString(16).slice(2,6);copy.p1.time+=shift;if(copy.p2)copy.p2.time+=shift;copy.locked=false;drawingStore().push(copy);save();selectDrawing(copy.id);}
function toggleSelectedLock(){const drawing=selectedDrawing();if(!drawing)return toast('Сначала выбери объект','error');drawing.locked=!drawing.locked;save();renderObjects();drawAll();}
function applyDrawingStyle(color,width,dash){const drawing=selectedDrawing();if(!drawing)return;snapshotDrawings();drawing.color=color;drawing.width=num(width,2);drawing.dash=dash==='dashed'?[7,5]:dash==='dotted'?[2,4]:[];save();renderObjects();drawAll();}
els.deleteBtn.onclick=deleteSelectedDrawing;
els.clearBtn.onclick=()=>{if(confirm('Удалить все рисунки этого графика?')){snapshotDrawings();store.ui.drawings[drawingsKey()]=[];selectDrawing(null);save();}};
els.drawingCanvas.addEventListener('pointerdown',drawingDown);els.drawingCanvas.addEventListener('pointermove',drawingMove);els.drawingCanvas.addEventListener('pointerup',drawingUp);els.drawingCanvas.addEventListener('pointercancel',drawingUp);
els.objectsList.onclick=e=>{const row=e.target.closest('[data-id]');if(!row)return;const id=row.dataset.id,d=drawingStore().find(x=>x.id===id);if(!d)return;const act=e.target.dataset.action;if(act==='toggle'){d.hidden=!d.hidden;save();drawAll();renderObjects();}else if(act==='remove'){snapshotDrawings();store.ui.drawings[drawingsKey()]=drawingStore().filter(x=>x.id!==id);save();selectDrawing(null);}else selectDrawing(id);};
els.duplicateDrawing.onclick=duplicateSelectedDrawing;els.lockSelectedDrawing.onclick=toggleSelectedLock;els.openDrawingProperties.onclick=openSelectedDrawingProperties;
els.drawingColor.oninput=()=>{if(selectedDrawing())applyDrawingStyle(els.drawingColor.value,els.drawingWidth.value,els.drawingDash.value);};els.drawingWidth.onchange=()=>{if(selectedDrawing())applyDrawingStyle(els.drawingColor.value,els.drawingWidth.value,els.drawingDash.value);};els.drawingDash.onchange=()=>{if(selectedDrawing())applyDrawingStyle(els.drawingColor.value,els.drawingWidth.value,els.drawingDash.value);};
els.propertyColor.oninput=()=>applyDrawingStyle(els.propertyColor.value,els.propertyWidth.value,els.propertyDash.value);els.propertyWidth.onchange=()=>applyDrawingStyle(els.propertyColor.value,els.propertyWidth.value,els.propertyDash.value);els.propertyDash.onchange=()=>applyDrawingStyle(els.propertyColor.value,els.propertyWidth.value,els.propertyDash.value);els.propertyDuplicate.onclick=duplicateSelectedDrawing;els.propertyLock.onclick=toggleSelectedLock;els.propertyDelete.onclick=()=>{deleteSelectedDrawing();closeModals();};
els.replayBtn.onclick=()=>runtime.replay.active?exitReplay():startReplay();
els.replayPlay.onclick=playReplay;els.replayStep.onclick=()=>replayStep(1);els.replayBack.onclick=()=>replayStep(-1);els.replayExit.onclick=exitReplay;els.replaySlider.oninput=e=>{runtime.replay.index=num(e.target.value);applyReplay();};
els.replayMarkGalka.onclick=()=>markReplayGalka();els.replayReveal.onclick=revealReplayResult;
els.snapshotBtn.onclick=takeSnapshot;
els.fullscreenBtn.onclick=()=>document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen?.();
els.goDateBtn.onclick=()=>openModal(els.goDateModal);
els.goDateApply.onclick=()=>{const t=Date.parse(els.goDateInput.value)/1000;if(!Number.isFinite(t))return;const span=intervalSeconds(runtime.interval)*100;runtime.mainChart.timeScale().setVisibleRange({from:t-span/2,to:t+span/2});closeModals();};
document.querySelectorAll('[data-close-modal]').forEach(b=>b.onclick=closeModals);document.querySelectorAll('.modal').forEach(m=>m.onclick=e=>{if(e.target===m)closeModals();});
document.querySelector('.side-tabs').onclick=e=>{const b=e.target.closest('[data-panel]');if(b)openPanel(b.dataset.panel);};
document.querySelectorAll('[data-back-panel]').forEach(button=>button.onclick=()=>openPanel(button.dataset.backPanel));
els.savePaperSettings.onclick=()=>{const s=store.paper.settings;s.startingBalance=Math.max(100,num(els.startingBalance.value,1000));s.leverage=clamp(num(els.leverage.value,10),1,20);s.symbolNotional=clamp(num(els.symbolNotional.value,400),50,10000);s.maxHours=clamp(num(els.maxHours.value,72),1,336);s.signalMode='manual';s.ladderStepPct=.15;s.manualDepthPct=2;s.exitMode='target';s.reclaimBufferPct=0;s.trailDistancePct=clamp(num(els.trailDistancePct.value,.75),.05,10);logActivity('paper','Paper-настройки сохранены');save();renderPaper();renderActivity();updateMarkers();toast('Paper-настройки сохранены');};
els.resetPaper.onclick=()=>{if(!confirm('Удалить все paper-позиции, лимитки, сделки и PnL по BTC, ETH и SOL? Это действие нельзя отменить.'))return;const settings=store.paper.settings,recovery=store.paper.recovery;store.paper=defaultStore().paper;store.paper.settings=settings;store.paper.recovery=recovery;logActivity('risk','Paper-счёт сброшен после подтверждения');save();renderPaper();renderActivity();updateMarkers();};
els.exportTrades.onclick=exportTradesCsv;
els.exportWorkspace.onclick=()=>download(`galka-workspace-${Date.now()}.json`,new Blob([JSON.stringify(workspacePayload(),null,2)],{type:'application/json'}));
els.importWorkspace.onchange=async e=>{const f=e.target.files?.[0];if(!f)return;try{const x=JSON.parse(await f.text());if(!x.ui)throw new Error('Нет ui');store.ui=deepMerge(defaultStore().ui,x.ui);save();location.reload();}catch(err){toast('Ошибка импорта: '+err.message,'error');}};
els.saveTemplate.onclick=()=>{const name=els.templateName.value.trim();if(!name)return;store.ui.templates[name]={chartType:runtime.chartType,interval:runtime.interval,scaleMode:store.ui.scaleMode,indicators:store.ui.indicators,lowerIndicator:store.ui.lowerIndicator,theme:store.ui.theme};save();els.templateName.value='';renderTemplates();};
els.templatesList.onclick=e=>{const load=e.target.dataset.templateLoad,del=e.target.dataset.templateDelete;if(del){delete store.ui.templates[del];save();renderTemplates();}if(load){const t=store.ui.templates[load];Object.assign(store.ui,t);save();location.reload();}};
els.exportSnapshot.onclick=()=>exportFullSnapshot();els.importSnapshot.onchange=e=>{const file=e.target.files?.[0];if(file)prepareRestore(file);};els.confirmRestore.onclick=confirmRestore;document.querySelectorAll('[data-close-restore]').forEach(button=>button.onclick=closeRestore);
els.clearActivity.onclick=()=>{if(confirm('Очистить только журнал событий? Paper-позиции и настройки останутся.')){store.activity=[];save();renderActivity();}};
els.startOnboarding.onclick=startOnboarding;els.skipOnboarding.onclick=finishOnboarding;els.nextOnboarding.onclick=()=>{if(runtime.onboardingIndex>=onboardingSteps.length-1)finishOnboarding();else{runtime.onboardingIndex++;renderOnboarding();}};
els.radarPanelToggle.onchange=()=>{if(els.radarPanelToggle.checked!==store.ui.radar.enabled)toggleRadar();};els.radarMinScore.oninput=e=>{store.ui.radar.minScore=num(e.target.value,45);save();scanRadar();renderRadar();updateMarkers();};els.radarVisibleOnly.onchange=e=>{store.ui.radar.visibleOnly=e.target.checked;save();scanRadar();renderRadar();updateMarkers();};
els.radarFilters.onclick=e=>{const button=e.target.closest('[data-radar-filter]');if(!button)return;store.ui.radar.filter=button.dataset.radarFilter;save();renderRadar();updateMarkers();};els.radarCandidatesList.onclick=e=>{const button=e.target.closest('[data-radar-id]');if(!button)return;selectRadarCandidate(runtime.radarCandidates.find(item=>item.patternId===button.dataset.radarId));};els.radarPositive.onclick=()=>labelRadarCandidate('positive');els.radarNegative.onclick=()=>labelRadarCandidate('negative');els.radarPrev.onclick=()=>moveRadarSelection(-1);els.radarNext.onclick=()=>moveRadarSelection(1);
[els.labSymbol,els.labInterval,els.labType,els.labWindow,els.labProfile,els.labRegime].forEach(element=>element.onchange=updateLabFilters);els.shadowToggle.onchange=toggleShadowMode;els.exportShadow.onclick=exportShadowRecords;els.openWatchlist.onclick=()=>openPanel('watchlist');
els.paperPortfolioCards.onclick=e=>{const card=e.target.closest('[data-paper-symbol]');if(card)changeSymbol(card.dataset.paperSymbol);};els.paperPortfolioCards.onkeydown=e=>{if((e.key==='Enter'||e.key===' ')&&e.target.closest('[data-paper-symbol]')){e.preventDefault();changeSymbol(e.target.closest('[data-paper-symbol]').dataset.paperSymbol);}};
els.chartActionBtn.onclick=()=>{const open=els.chartActionMenu.classList.toggle('hidden')===false;els.chartActionBtn.classList.toggle('open',open);els.chartActionBtn.setAttribute('aria-expanded',String(open));};
function closeChartActions(){els.chartActionMenu.classList.add('hidden');els.chartActionBtn.classList.remove('open');els.chartActionBtn.setAttribute('aria-expanded','false');}
els.quickSetGalka.onclick=()=>{closeChartActions();setTool('manualGalka');closeMobileOverlays();toast('Коснись уровня GALKA на графике');};els.quickExactGalka.onclick=()=>{closeChartActions();showMobilePanel('paper');setTimeout(()=>els.manualGalkaPrice.focus(),220);};els.quickMoveGalka.onclick=()=>{closeChartActions();startManualMove();};els.quickRadar.onclick=()=>{closeChartActions();toggleRadar();};els.quickLevels.onclick=()=>{store.ui.showLevels=store.ui.showLevels===false;save();updateMarkers();closeChartActions();toast(store.ui.showLevels?'Торговые уровни показаны':'Торговые уровни скрыты');};
els.toggleTools.onclick=()=>{els.sidebar.classList.remove('open');els.leftbar.classList.toggle('open');syncMobileOverlay();};els.toggleSidebar.onclick=()=>showMobilePanel('more');
document.querySelector('.mobile-nav').onclick=e=>{const b=e.target.closest('[data-mobile-panel]');if(!b)return;const panel=b.dataset.mobilePanel;if(panel==='chart')closeMobileOverlays();else showMobilePanel(panel);};
els.sheetHandle.addEventListener('pointerdown',beginSheetGesture);els.sheetHandle.addEventListener('pointermove',moveSheetGesture);els.sheetHandle.addEventListener('pointerup',endSheetGesture);els.sheetHandle.addEventListener('pointercancel',endSheetGesture);
window.addEventListener('resize',()=>{resizeCanvas();drawAll();syncMobileOverlay();});
document.addEventListener('visibilitychange',()=>{if(document.hidden)markPaperGap('background');renderSessionHealth();if(!document.hidden){if(!runtime.ws||runtime.ws.readyState>1)connectWs();else catchUpAfterReconnect('background');}});
window.addEventListener('pagehide',()=>markPaperGap('pagehide'));
window.addEventListener('pageshow',event=>{if(event.persisted){if(!runtime.ws||runtime.ws.readyState>1)connectWs();else catchUpAfterReconnect('bfcache');}});
window.addEventListener('offline',()=>{markPaperGap('offline');setConnection('Устройство offline','error');});window.addEventListener('online',()=>connectWs());
document.addEventListener('keydown',e=>{
  if(e.target.matches('input,select,textarea'))return;
  if(e.key==='Escape'){closeModals();setTool('cursor');closeMobileOverlays();}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();els.undoBtn.click();}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='y'){e.preventDefault();els.redoBtn.click();}
  if(e.key==='Delete')els.deleteBtn.click();
  if(e.key==='+'||e.key==='=')zoom(.75);if(e.key==='-')zoom(1.35);
  if(e.key.toLowerCase()==='f')els.fitBtn.click();if(e.key.toLowerCase()==='l')setTool('trend');
});
setInterval(()=>{els.clock.textContent=new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'});if(runtime.shadowDirty&&Date.now()-runtime.lastShadowPersistAt>=5000){save();runtime.shadowDirty=false;runtime.lastShadowPersistAt=Date.now();}renderDiagnostics();renderPaperHeader();renderSessionHealth();},1000);

installSimpleGalkaUi();
setInterval(renderSimpleTradeBar,1000);

/* Init */
document.body.dataset.theme=store.ui.theme;
els.magnetBtn.classList.toggle('active',store.ui.magnet);els.lockBtn.classList.toggle('active',store.ui.drawingsLocked);els.hideDrawingsBtn.classList.toggle('active',store.ui.drawingsHidden);
if(store.ui.sheet.snap==='low')els.sidebar.classList.add('snap-low');if(store.ui.sheet.snap==='high')els.sidebar.classList.add('snap-high');if(matchMedia('(min-width:1100px)').matches)els.sidebar.setAttribute('aria-hidden','false');
createMainChart();createOscChart();applyScaleMode();renderIndicatorList();openPanel(matchMedia('(min-width:1100px)').matches?(store.ui.sheet.panel||'paper'):'paper');if(!matchMedia('(min-width:1100px)').matches)closeMobileOverlays();renderAll();scanRadar();renderRadar();resizeCanvas();bootstrap();
if(store.shadow.enabled)ensureGalkaStats().catch(()=>{});
if(!store.ui.onboarding.completed)setTimeout(startOnboarding,1400);
if('serviceWorker' in navigator)window.addEventListener('load',()=>navigator.serviceWorker.register('./sw.js').catch(error=>console.warn('Service worker:',error)));
})();
