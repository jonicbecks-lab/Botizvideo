import fs from 'node:fs';
import vm from 'node:vm';
const html=fs.readFileSync('terminal/backtest.html','utf8');
const css=fs.readFileSync('terminal/backtest.css','utf8');
const js=fs.readFileSync('terminal/backtest.js','utf8');
const summary=JSON.parse(fs.readFileSync('results/reclaim_backtest/backtest-lite.json','utf8'));
const curves=JSON.parse(fs.readFileSync('results/reclaim_backtest/curves-annual.json','utf8'));
new vm.Script(js,{filename:'terminal/backtest.js'});
const checks=[
 ['mobile viewport',html.includes('viewport-fit=cover')],
 ['equity chart',html.includes('id="chart"')&&js.includes('LineSeries')],
 ['variant selector',html.includes('id="variant"')],
 ['deposit metrics',html.includes('id="metrics"')],
 ['coin sleeves',html.includes('id="coins"')],
 ['recent OOS',html.includes('id="recent"')],
 ['risk disclosure',html.includes('funding')&&html.includes('проскальзывание')],
 ['selected safe candidate',summary.meta.selected_variant==='trail075_400'],
 ['all variants',Object.keys(summary.summaries).length===12],
 ['zero liquidations candidate',summary.summaries.trail075_400.liquidations===0],
 ['dangerous preset fails',summary.summaries.trail075_3333.ending_equity===0],
 ['annual curves',Object.keys(curves).length===12&&curves.trail075_400.length>=8],
 ['mobile css',css.includes('@media(max-width:600px)')],
 ['local chart runtime',html.includes('vendor/lightweight-charts.standalone.production.js')&&!/<script[^>]+https?:\/\//.test(html)],
 ['bounded browser policy',html.includes('Content-Security-Policy')]
];
for(const [name,ok] of checks)if(!ok)throw new Error(`Backtest page check failed: ${name}`);
console.log(`Backtest page: ${checks.length} checks passed`);
