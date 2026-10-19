/* NEON 1-minute browser paper lab.
 * Separate from the authoritative GitHub Actions hourly portfolio.
 * Runs only while this page is open. Explicit opt-in, localStorage per device.
 * One read-only API call per minute for both BTC/ETH; no exchange orders.
 */
(()=>{'use strict';
 const $=id=>document.getElementById(id);
 const KEY='neon-minute-paper-v1';
 const P=['BTCUSDT','ETHUSDT'];
 const FEE=.002,SLIP=.001;
 let history={},live={},online=false,lastFetch=0;
 const cash=n=>'$'+Number(n||0).toLocaleString('en-US',{maximumFractionDigits:2});
 const dateString=ts=>new Date(ts).toISOString();
 const initial=()=>({cash:1000,positions:{},peak:1000,locked:false,lastMinute:null,startedAt:new Date().toISOString(),trades:[]});
 function load(){try{const s=JSON.parse(localStorage.getItem(KEY)||'null');return s&&typeof s.cash==='number'&&s.positions&&Array.isArray(s.trades)?s:null}catch{return null}}
 function save(s){localStorage.setItem(KEY,JSON.stringify(s))}
 function sma(a,n){return a.slice(-n).reduce((x,y)=>x+y,0)/n}
 function decide(bars){if(bars.length<55)return false;const a=bars.map(x=>x.close);return a.at(-1)>sma(a,20)&&sma(a,20)>sma(a,50)}
 function equity(s){return s.cash+P.reduce((sum,p)=>sum+(s.positions[p]||0)*(live[p]||0),0)}
 function record(s,action,p,qty,price,min,reason){s.trades.push({action,p,qty,price,min,reason});s.trades=s.trades.slice(-300)}
 function sell(s,p,price,min,reason){const qty=s.positions[p]||0;if(!qty)return;const exec=price*(1-SLIP);s.cash+=qty*exec*(1-FEE);delete s.positions[p];record(s,'SELL',p,qty,exec,min,reason)}
 function buy(s,p,price,min){if(s.positions[p]||s.locked)return;const budget=Math.min(equity(s)*.25,s.cash);if(budget<5)return;const exec=price*(1+SLIP),qty=budget/(exec*(1+FEE));s.cash-=budget;s.positions[p]=qty;record(s,'BUY',p,qty,exec,min,'1m SMA20 > SMA50 + price above SMA20')}
 function tick(s){
   const timestamps=P.map(p=>history[p]?.at(-1)?.t);
   if(timestamps.some(x=>!x)||new Set(timestamps).size!==1)return;
   const min=timestamps[0];
   if(s.lastMinute&&min<=s.lastMinute)return;
   if(s.lastMinute&&min-s.lastMinute>5*60000){s.lastMinute=min;save(s);return}
   // Never backfill a trade at historical prices when browser wasn't running.
   if(Date.now()-min>180000)return;
   const prices=Object.fromEntries(P.map(p=>[p,history[p].at(-1).close]));
   let e=equity(s);s.peak=Math.max(s.peak,e);
   if(e<=s.peak*.9){s.locked=true;for(const p of P)sell(s,p,prices[p],min,'10% drawdown risk lock')}
   if(!s.locked)for(const p of P){
     const yes=decide(history[p]);
     if(!yes)sell(s,p,prices[p],min,'Trend exited');
     else buy(s,p,prices[p],min);
   }
   s.lastMinute=min;save(s);
 }
 function render(){
   const s=load(),on=Boolean(s),selected=$('symbol').value.replace('-USD','USDT');
   $('minute-status').textContent=!on?'Not started. Start to run 1m virtual trades only in this browser.':s.locked?'RISK LOCKED · No further buys':'RUNNING WHILE PAGE OPEN · Last evaluated '+(s.lastMinute?dateString(s.lastMinute):'none');
   $('minute-toggle').textContent=on?'Stop & reset browser demo':'Start 1-minute paper demo';
   $('minute-toggle').title=on?'Erases this device-only demo (GitHub portfolio unaffected)':'Begins isolated $1,000 virtual paper demo';
   $('minute-cash').textContent=on?cash(s.cash):'—';$('minute-equity').textContent=on?cash(equity(s)):'—';
   $('minute-fills').textContent=on?String(s.trades.length):'—';
   $('minute-price').textContent=live[selected]?cash(live[selected]):'Unavailable';
   $('minute-latest').textContent=lastFetch?'Last price refresh '+new Date(lastFetch).toLocaleTimeString():'Waiting for market data';
   const rows=on?s.trades.slice(-8).reverse():[];
   $('minute-ledger').textContent=rows.length?rows.map(x=>dateString(x.min)+' '+x.action+' '+x.p+' '+x.qty.toFixed(6)+' @ '+cash(x.price)).join('\n'):'No 1-minute virtual trades yet.';
 }
 async function update(){
   const endpoint='https://data-api.binance.vision/api/v3/klines';
   try{
     // Public browser requests can fail because of regional restrictions/CORS.
     let source='Binance public USDT feed';
     async function binance(p){
       const u=endpoint+'?symbol='+p+'&interval=1m&limit=80';
       const response=await fetch(u,{cache:'no-store',signal:AbortSignal.timeout(10000)});
       if(!response.ok)throw Error('Binance HTTP '+response.status);
       const raw=await response.json();
       if(!Array.isArray(raw)||raw.length<55)throw Error('Not enough Binance 1m bars');
       return raw.map(r=>({t:Number(r[0]),close:Number(r[4]),closed:Number(r[6])<Date.now()-2000}));
     }
     async function coinbase(p){
       const pair=p.replace('USDT','-USD');
       const url='https://api.exchange.coinbase.com/products/'+pair+'/candles?granularity=60';
       const response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(10000)});
       if(!response.ok)throw Error('Coinbase HTTP '+response.status);
       const raw=await response.json();
       if(!Array.isArray(raw)||raw.length<55)throw Error('Not enough Coinbase 1m bars');
       return raw.map(r=>({t:Number(r[0])*1000,close:Number(r[4]),closed:Number(r[0])*1000+62000<Date.now()})).sort((a,b)=>a.t-b.t);
     }
     let output;
     try{output=await Promise.all(P.map(binance))}
     catch(firstError){
       try{output=await Promise.all(P.map(coinbase));source='Coinbase public USD feed'}
       catch(secondError){throw Error('Binance: '+firstError.message+'; Coinbase: '+secondError.message)}
     }
     for(let i=0;i<P.length;i++){
       const rows=output[i].filter(r=>r.closed&&Number.isFinite(r.close)&&r.close>0);
       if(!rows.length)throw Error('No confirmed bars');
       history[P[i]]=rows;live[P[i]]=Number(output[i].at(-1).close);
     }
     online=true;lastFetch=Date.now();
     const s=load();if(s){tick(s)}
     $('minute-network').textContent=source+' · 1-minute candles. Binance USDT is an approximate USD proxy.';
   }catch(e){
     online=false;
     $('minute-network').textContent='1-minute feed unavailable ('+e.message+'). No trades processed. Public Binance API access varies by network/region.';
   }
   render();
 }
 $('minute-toggle').addEventListener('click',()=>{
   const s=load();if(s){if(!confirm('Reset ONLY your browser 1-minute demo? Recorded demo trades will be deleted. GitHub cloud portfolio will NOT change.'))return;localStorage.removeItem(KEY);}
   else{if(!online){$('minute-network').textContent='Market feed unavailable; cannot start.';return}const fresh=initial();fresh.lastMinute=history[P[0]]?.at(-1)?.t||null;save(fresh)}
   render();
 });
 $('symbol').addEventListener('change',render);
 render();update();setInterval(update,60000);
})();
