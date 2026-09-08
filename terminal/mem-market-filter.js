const ALLOWED_MEMES = [
  'PONS','CASHCAT','DOGE','kPEPE','FARTCOIN','TRUMP','SPX','kBONK',
  'PEOPLE','kSHIB','WIF','BOME','MELANIA','POPCAT','kFLOKI','PNUT'
];
const allowed = new Map(ALLOWED_MEMES.map((name,index)=>[name.toUpperCase(),index]));
const select = document.getElementById('symbolSelect');

function pruneAndOrder(){
  if(!select) return;
  const current = select.value;
  const rows = [...select.options]
    .filter(option=>allowed.has(String(option.value||option.textContent||'').toUpperCase()))
    .sort((a,b)=>allowed.get(String(a.value||a.textContent||'').toUpperCase())-allowed.get(String(b.value||b.textContent||'').toUpperCase()));

  const signature = rows.map(option=>option.value).join('|');
  const existing = [...select.options].map(option=>option.value).join('|');
  if(signature !== existing){
    select.replaceChildren(...rows);
  }

  if(current && rows.some(option=>option.value===current)){
    select.value=current;
  }else{
    const cashcat=rows.find(option=>String(option.value).toUpperCase()==='CASHCAT');
    if(cashcat) select.value=cashcat.value;
    else if(rows[0]) select.value=rows[0].value;
  }
}

if(select){
  const observer = new MutationObserver(pruneAndOrder);
  observer.observe(select,{childList:true});
  pruneAndOrder();
  setInterval(pruneAndOrder,1000);
}
