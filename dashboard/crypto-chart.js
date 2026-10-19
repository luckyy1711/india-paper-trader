/* NEON's TradingView-inspired interface, built on officially licensed
 * TradingView Lightweight Charts v5.2.1 (Apache-2.0).
 * Source charting library: https://github.com/tradingview/lightweight-charts
 * This app does not use or redistribute TradingView's proprietary website
 * source, Advanced Charts, Pine Script, historical data or order execution.
 */
(() => {
  'use strict';
  const L = window.LightweightCharts;
  const $ = id => document.getElementById(id);
  const root = $('chart');
  const hint = $('chart-hint');
  if (!L || !root) {
    if (hint) hint.textContent = 'Chart library unavailable. Connect to the internet and reload; trading and portfolio records are independent.';
    window.NeonTradingChart = { render() {}, redraw() {} };
    return;
  }

  const COLORS = {
    bg: '#0b111b', grid: '#1e2d3d', edge: '#2b4054',
    text: '#a5b6cb', up: '#26a69a', down: '#ef5350',
    ema20: '#f6bf63', ema50: '#7ea9fb', rsi: '#b99eff',
    draw: '#d7a7fe',
  };
  let dark = true;
  let chartKind = 'Candles';
  let interval = 1;
  let lastSymbol = '';
  let lastSource = '';
  let lastSignature = '';
  let follow = true;
  let current = [];
  let key = '';
  let lastTrades = [];
  let barsByTime = new Map();
  let tradeMarkerApi = null;
  let priceSeries = null;
  let ema20Series = null;
  let ema50Series = null;
  let volumeSeries = null;
  let rsiSeries = null;
  let levelLines = [];
  let trendLines = [];
  let drawing = 'cursor';
  let pendingTrend = null;
  const CAP_DRAWINGS = 30;
  const fmt = n => Number(n).toLocaleString('en-US', {maximumFractionDigits: 4, minimumFractionDigits: 2});
  const fmtTime = seconds => new Date(seconds * 1000).toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  });
  const announce = text => { if (hint) hint.textContent = text; };
  function normalize(rows) {
    const bars = [];
    for (const item of rows || []) {
      const time = Math.floor(Date.parse(item.time) / 1000);
      const open = Number(item.open), high = Number(item.high), low = Number(item.low), close = Number(item.close);
      if (!Number.isFinite(time) || ![open, high, low, close].every(Number.isFinite)
          || low <= 0 || low > Math.min(open, close) || high < Math.max(open, close)) continue;
      const volume = item.volume == null ? null : Number(item.volume);
      bars.push({
        time, open, high, low, close,
        volume: Number.isFinite(volume) && volume >= 0 ? volume : null,
        complete: item.complete !== false,
      });
    }
    const unique = new Map();
    for (const bar of bars) unique.set(bar.time, bar);
    return [...unique.values()].sort((a, b) => a.time - b.time);
  }
  function compress(bars, minutes) {
    if (minutes <= 1) return bars;
    const size = minutes * 60, buckets = new Map();
    for (const bar of bars) {
      const bucket = Math.floor(bar.time / size) * size;
      if (!buckets.has(bucket)) buckets.set(bucket, []);
      buckets.get(bucket).push(bar);
    }
    const result = [];
    for (const [stamp, group] of buckets) {
      group.sort((a,b) => a.time-b.time);
      let continuous = true;
      for (let i=1;i<group.length;i++) if(group[i].time-group[i-1].time!==60) continuous=false;
      // Never manufacture OHLC from missing intermediate minutes.
      if (!continuous) continue;
      result.push({
        time: stamp,
        open: group[0].open, close: group[group.length - 1].close,
        high: Math.max(...group.map(p => p.high)),
        low: Math.min(...group.map(p => p.low)),
        volume: group.every(p=>p.volume!==null) ? group.reduce((n,p)=>n+p.volume,0) : null,
        complete: group.length===minutes && group.every(p=>p.complete),
      });
    }
    return result;
  }
  function ema(bars, period) {
    let value = null;
    return bars.map((bar) => {
      value = value===null ? bar.close : value+(bar.close-value)*2/(period+1);
      return {time:bar.time, value};
    });
  }
  function rsi(bars, period=14) {
    if (bars.length<=period) return [];
    const out=[];
    for (let i=period; i<bars.length; i++) {
      let gain=0, loss=0;
      for (let j=i-period+1;j<=i;j++){
        const change=bars[j].close-bars[j-1].close;
        gain+=Math.max(0,change); loss+=Math.max(0,-change);
      }
      const value=loss===0?(gain===0?50:100):100-100/(1+gain/loss);
      out.push({time:bars[i].time,value});
    }
    return out;
  }

  const chart = L.createChart(root, {
    autoSize: true, height: root.clientHeight || 520,
    layout: {
      background: {type:L.ColorType.Solid,color:COLORS.bg}, textColor: COLORS.text,
      fontFamily: "Inter, system-ui, sans-serif", fontSize:12,
      panes: { separatorColor: COLORS.edge, separatorHoverColor: '#4b6d83', enableResize: true },
    },
    grid:{ vertLines:{color:COLORS.grid, style:L.LineStyle.Dotted}, horzLines:{color:COLORS.grid, style:L.LineStyle.Dotted}},
    crosshair: {
      mode:L.CrosshairMode.Normal,
      vertLine:{color:'#7298b8',width:1,style:L.LineStyle.Dashed,labelBackgroundColor:'#254058'},
      horzLine:{color:'#7298b8',width:1,style:L.LineStyle.Dashed,labelBackgroundColor:'#254058'},
    },
    rightPriceScale:{borderColor:COLORS.edge,scaleMargins:{top:.06,bottom:.10}},
    leftPriceScale:{visible:false},
    timeScale:{borderColor:COLORS.edge,timeVisible:true,secondsVisible:false,rightOffset:5,barSpacing:9,minBarSpacing:2,
      tickMarkFormatter:(time) => {
        const t=typeof time==='number' ? time : Date.parse(time)/1000;
        return new Date(t*1000).toLocaleTimeString('en-IN', {timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',hour12:false});
      }},
    localization: {locale:'en-IN',timeFormatter: t=>fmtTime(t)},
    handleScroll: {mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:true},
    handleScale: {axisPressedMouseMove:{time:true,price:true},mouseWheel:true,pinch:true},
    kineticScroll:{mouse:true,touch:true},
  });
  function makePrice() {
    if (priceSeries) {
      for (const l of levelLines) { try { priceSeries.removePriceLine(l); } catch (e) {} }
      levelLines=[];
      chart.removeSeries(priceSeries);
      priceSeries=null;
    }
    const options={
      priceScaleId:'right', lastValueVisible:true,priceLineVisible:true,priceLineColor:'#7199b0',
      priceFormat:{type:'price',precision:2,minMove:.01},
    };
    if (chartKind==='Line') {
      priceSeries=chart.addSeries(L.LineSeries,{...options,color:COLORS.up,lineWidth:2});
    } else if(chartKind==='Area') {
      priceSeries=chart.addSeries(L.AreaSeries,{...options,
        lineColor:COLORS.up,topColor:'rgba(38,166,154,.26)',bottomColor:'rgba(38,166,154,.01)',lineWidth:2});
    } else {
      priceSeries=chart.addSeries(L.CandlestickSeries,{
        ...options,upColor:COLORS.up,downColor:COLORS.down,
        wickUpColor:COLORS.up,wickDownColor:COLORS.down,
        borderUpColor:COLORS.up,borderDownColor:COLORS.down,
      });
    }
    tradeMarkerApi=L.createSeriesMarkers(priceSeries,[]);
    if (current.length) applyPriceData();
  }
  makePrice();

  function getSeriesBars() {
    return chartKind==='Candles'
      ? current.map(p=>({time:p.time,open:p.open,high:p.high,low:p.low,close:p.close}))
      : current.map(p=>({time:p.time,value:p.close}));
  }
  function applyPriceData() {
    if (!priceSeries) return;
    priceSeries.setData(getSeriesBars());
    if (tradeMarkerApi) tradeMarkerApi.setMarkers(buildMarkers());
  }
  function buildMarkers() {
    const found = [];
    const intervalSeconds=interval*60;
    for (const trade of lastTrades) {
      const time=Math.floor(Date.parse(trade.time)/1000);
      const candle = (lastSource==='1h')
        ? Math.floor(time/3600)*3600
        : Math.floor(time/intervalSeconds)*intervalSeconds;
      if(!barsByTime.has(candle))continue;
      const isBuy=trade.side==='BUY';
      found.push({
        time:candle,position:isBuy?'belowBar':'aboveBar',
        color:isBuy?COLORS.up:COLORS.down,
        shape:isBuy?'arrowUp':'arrowDown',
        text:isBuy?'BUY':'SELL',
      });
    }
    return found.sort((a,b)=>a.time-b.time).slice(-160);
  }
  function addOverlay(toggle, period, color, series) {
    const active=$(toggle).checked;
    if(!active){
      if(series)chart.removeSeries(series);
      return null;
    }
    if(!series)series=chart.addSeries(L.LineSeries,{
      color,lineWidth:2,lastValueVisible:false,priceLineVisible:false,
      crosshairMarkerVisible:false,priceScaleId:'right',
    });
    series.setData(ema(current,period));
    return series;
  }
  function updateVolume() {
    const available=current.some(x=>x.volume!==null);
    const enabled=$('show-volume').checked && available;
    if(!enabled){
      if(volumeSeries){chart.removeSeries(volumeSeries);volumeSeries=null;}
      if($('show-volume').checked && !available && current.length)
        announce('Volume is not provided for this saved candle source; no volumes are invented.');
      return;
    }
    if(!volumeSeries){
      volumeSeries=chart.addSeries(L.HistogramSeries,{
        priceScaleId:'volume',priceFormat:{type:'volume'},
        lastValueVisible:false,priceLineVisible:false,
      });
      chart.priceScale('volume').applyOptions({scaleMargins:{top:.78,bottom:0}});
    }
    volumeSeries.setData(current.filter(x=>x.volume!==null).map(x=>({
      time:x.time,value:x.volume,color:x.close>=x.open?'rgba(38,166,154,.33)':'rgba(239,83,80,.35)',
    })));
  }
  function updateRSI() {
    if(!$('show-rsi').checked){
      if(rsiSeries){
        chart.removeSeries(rsiSeries);rsiSeries=null;
        if(chart.panes().length>1)chart.removePane(1);
      }
      return;
    }
    if(!rsiSeries){
      rsiSeries=chart.addSeries(L.LineSeries,{
        color:COLORS.rsi,lineWidth:2,priceScaleId:'right',
        priceFormat:{type:'price',precision:1,minMove:.1},
        lastValueVisible:true,priceLineVisible:false,
      },1);
      if(chart.panes().length>1)chart.panes()[1].setHeight(115);
    }
    rsiSeries.setData(rsi(current));
  }
  function clearDrawings(){
    for(const line of levelLines) {
      try{priceSeries.removePriceLine(line);}catch(e){}
    }
    levelLines=[];
    for(const series of trendLines)chart.removeSeries(series);
    trendLines=[];
    pendingTrend=null;
    announce('Drawing tools cleared for this view.');
  }
  function setDrawMode(next) {
    drawing=next;pendingTrend=null;
    root.style.cursor=next==='cursor'?'crosshair':'cell';
    for(const b of document.querySelectorAll('[data-tool]'))
      b.classList.toggle('active',b.dataset.tool===next);
    announce(next==='level'?'Click on the chart to add a horizontal price level.':
      next==='trend'?'Click a start point, then click an end point for a trend line.':
      'Use mouse wheel to zoom; drag to pan; hover for exact prices.');
  }
  chart.subscribeClick(param => {
    if(drawing==='cursor'||!param.point||param.time==null||!priceSeries)return;
    if(levelLines.length+trendLines.length>=CAP_DRAWINGS){
      announce('30 drawings maximum for a fast chart. Clear old drawings to add more.');return;
    }
    const price=priceSeries.coordinateToPrice(param.point.y);
    if(price===null||!Number.isFinite(price))return;
    if(drawing==='level'){
      levelLines.push(priceSeries.createPriceLine({
        price,color:COLORS.draw,lineWidth:1,lineStyle:L.LineStyle.Dashed,
        axisLabelVisible:true,title:'LEVEL',
      }));
      announce('Horizontal price level added at $'+fmt(price)+'.');
    }else{
      if(!pendingTrend){
        pendingTrend={time:param.time,price};
        announce('Trend line start selected. Click the second point.');
      }else{
        const a=pendingTrend,b={time:param.time,price};pendingTrend=null;
        if(a.time===b.time){announce('Choose two different candles for a trend line.');return;}
        const line=chart.addSeries(L.LineSeries,{
          color:COLORS.draw,lineWidth:2,lastValueVisible:false,priceLineVisible:false,
          crosshairMarkerVisible:false,priceScaleId:'right',
        });
        line.setData([a,b].sort((x,y)=>x.time-y.time).map(p=>({time:p.time,value:p.price})));
        trendLines.push(line);
        announce('Trend line added.');
      }
    }
  });
  chart.subscribeCrosshairMove(param => {
    if(!param.time || !priceSeries)return;
    const t=typeof param.time==='number'?param.time:Math.floor(Date.parse(param.time)/1000);
    const data=barsByTime.get(t);
    if(!data)return;
    $('ohlc').textContent='O '+fmt(data.open)+'  H '+fmt(data.high)+'  L '+fmt(data.low)+
      '  C '+fmt(data.close)+'  '+fmtTime(t)+' IST';
    $('ohlc').className=data.close>=data.open?'ohlc green':'ohlc red';
  });

  function zoom(multiplier) {
    const axis=chart.timeScale(),range=axis.getVisibleLogicalRange();
    if(!range||!current.length)return;
    const width=Math.max(8,(range.to-range.from)*multiplier);
    const center=(range.to+range.from)/2;
    follow=false;
    axis.setVisibleLogicalRange({from:center-width/2,to:center+width/2});
  }
  function toLive(){follow=true;chart.timeScale().scrollToRealTime();announce('Following the most recent candle.');}
  function chooseRange(count){
    if(!current.length)return;
    follow=false;
    const to=current.length-1+5;
    chart.timeScale().setVisibleLogicalRange({from:Math.max(-1,to-count),to});
  }
  function saveScreenshot(){
    try{
      const canvas=chart.takeScreenshot(true);
      canvas.toBlob(blob=>{
        if(!blob){announce('Screenshot export was blocked by the browser.');return;}
        const url=URL.createObjectURL(blob);
        const link=document.createElement('a');
        link.href=url;link.download='neon-'+(lastSymbol||'crypto')+'-'+Date.now()+'.png';
        document.body.appendChild(link);link.click();link.remove();
        setTimeout(()=>URL.revokeObjectURL(url),3000);
      });
    }catch(e){announce('Could not export screenshot: '+e.message);}
  }
  function updateTheme(){
    dark=!dark;
    const bg=dark?'#0b111b':'#fafcff',fg=dark?COLORS.text:'#38485c';
    const grid=dark?COLORS.grid:'#e9edf4', edge=dark?COLORS.edge:'#d2dcea';
    chart.applyOptions({
      layout:{background:{type:L.ColorType.Solid,color:bg},textColor:fg},
      grid:{vertLines:{color:grid},horzLines:{color:grid}},
      rightPriceScale:{borderColor:edge},timeScale:{borderColor:edge},
    });
    $('chart').classList.toggle('light-chart',!dark);
    $('chart-theme').textContent=dark?'☾ Dark':'☀ Light';
  }
  function toolbarHandlers() {
    $('chart-kind').addEventListener('change',event=>{
      chartKind=event.target.value;clearDrawings();makePrice();applyPriceData();chart.timeScale().fitContent();
      announce('Chart type: '+chartKind+'. Paper trading signals are unchanged.');
    });
    $('chart-resolution').addEventListener('change',event=>{
      interval=Number(event.target.value);lastSignature='';follow=true;clearDrawings();
      window.NeonTradingChart.redraw();
    });
    for(const name of ['show-ema20','show-ema50','show-volume','show-rsi']){
      $(name).addEventListener('change',()=>{
        lastSignature='';window.NeonTradingChart.redraw();
      });
    }
    for(const btn of document.querySelectorAll('[data-tool]'))
      btn.addEventListener('click',()=>setDrawMode(btn.dataset.tool));
    $('clear-drawings').addEventListener('click',clearDrawings);
    $('chart-zoom-in').addEventListener('click',()=>zoom(.75));
    $('chart-zoom-out').addEventListener('click',()=>zoom(1.35));
    $('chart-fit').addEventListener('click',()=>{follow=false;chart.timeScale().fitContent();});
    $('chart-live').addEventListener('click',toLive);
    $('chart-theme').addEventListener('click',updateTheme);
    $('chart-screenshot').addEventListener('click',saveScreenshot);
    $('chart-fullscreen').addEventListener('click',()=>{
      const panel=$('chart-shell');
      if(!document.fullscreenElement)panel.requestFullscreen?.().catch(()=>announce('Fullscreen blocked by browser.'));
      else document.exitFullscreen?.();
    });
    for(const btn of document.querySelectorAll('[data-range]'))
      btn.addEventListener('click',()=>{
        if(btn.dataset.range==='all')chart.timeScale().fitContent();
        else chooseRange(Math.max(8,Math.ceil(Number(btn.dataset.range)/interval)));
      });
    root.addEventListener('wheel',()=>{follow=false;},{passive:true});
    root.addEventListener('pointerdown',()=>{if(drawing==='cursor')follow=false;});
    document.addEventListener('keydown',e=>{
      if(['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName))return;
      if(e.key==='+'||e.key==='='){zoom(.75);e.preventDefault();}
      if(e.key==='-'){zoom(1.35);e.preventDefault();}
      if(e.key==='Escape')setDrawMode('cursor');
    });
  }
  toolbarHandlers();
  setDrawMode('cursor');
  function render({rows,symbol,source, trades}) {
    lastTrades=Array.isArray(trades)?trades:[];
    const normalized=normalize(rows);
    const sourceMode=source==='1h'?'1h':'1m';
    const changed=symbol!==lastSymbol || sourceMode!==lastSource;
    const desiredInterval=sourceMode==='1h'?60:interval;
    let aggregated=sourceMode==='1h'?normalized:compress(normalized,desiredInterval);
    const signature=[symbol,sourceMode,desiredInterval,normalized.length,
      normalized[0]?.time||0,normalized[normalized.length-1]?.time||0,
      normalized[normalized.length-1]?.close||0,normalized[normalized.length-1]?.volume??'none',
      lastTrades.length,lastTrades[lastTrades.length-1]?.time||'',
      chartKind,...['show-ema20','show-ema50','show-volume','show-rsi'].map(id=>$(id).checked)
    ].join('|');
    if(signature===lastSignature)return;
    const previous=changed?null:chart.timeScale().getVisibleRange();
    lastSignature=signature;lastSymbol=symbol;lastSource=sourceMode;lastTrades=lastTrades;
    if(changed){clearDrawings();follow=true;}
    current=aggregated;
    barsByTime=new Map(current.map(bar=>[bar.time,bar]));
    $('chart-resolution').disabled=sourceMode==='1h';
    $('chart-resolution').title=sourceMode==='1h'
      ?'Only the saved hourly candles are available in this mode. Switch to 1m preview to choose other intervals.'
      :'Resample only the available real one-minute candles; no extra history is fetched.';
    if(!current.length){
      priceSeries.setData([]);
      if(tradeMarkerApi)tradeMarkerApi.setMarkers([]);
      ema20Series=addOverlay('show-ema20',20,COLORS.ema20,ema20Series);
      ema50Series=addOverlay('show-ema50',50,COLORS.ema50,ema50Series);
      updateVolume();updateRSI();
      $('ohlc').textContent='Waiting for actual exchange candles…';
      announce('No market candles are available for this chart view yet. Portfolio remains visible above.');
      return;
    }
    applyPriceData();
    ema20Series=addOverlay('show-ema20',20,COLORS.ema20,ema20Series);
    ema50Series=addOverlay('show-ema50',50,COLORS.ema50,ema50Series);
    updateVolume();updateRSI();
    const last=current[current.length-1];
    $('ohlc').textContent='O '+fmt(last.open)+'  H '+fmt(last.high)+'  L '+fmt(last.low)+
      '  C '+fmt(last.close)+'  '+fmtTime(last.time)+' IST';
    $('ohlc').className=last.close>=last.open?'ohlc green':'ohlc red';
    if(follow)chart.timeScale().scrollToRealTime();
    else if(previous) {
      try{chart.timeScale().setVisibleRange(previous);}
      catch(e){chart.timeScale().fitContent();}
    }
    $('chart-data-label').textContent=symbol.replace('-USD',' / USD')+' · '+desiredInterval+'m'+
      (last.complete?' · confirmed':' · latest interval still forming')+' · '+current.length+' candles';
    if(!changed) return;
    announce('Interactive exchange-candle chart. Drag to pan, wheel/pinch to zoom, hover for exact OHLC. Chart times shown in IST.');
  }

  // Used by the existing market/data fetcher; all controls are read-only.
  let previousPayload=null;
  window.NeonTradingChart = {
    render(payload){
      previousPayload=payload;
      try{render(payload);}
      catch(e){
        lastSignature='';
        announce('Chart rendering error: '+e.message+'. Trading engine is unaffected.');
        console.error('Chart rendering error',e);
      }
    },
    redraw(){if(previousPayload)this.render(previousPayload);},
  };
})();
