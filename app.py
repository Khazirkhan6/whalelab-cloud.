import os, time, json, math, random, sqlite3, threading, urllib.request
from datetime import datetime, timezone
from flask import Flask, jsonify, request, render_template_string

app=Flask(__name__)
DB=os.environ.get('WHALELAB_DB','whalelab.db')
TOKEN=os.environ.get('WHALELAB_TOKEN','')
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT']
FEE=.0004
lock=threading.RLock()
state={'running':False,'source':'starting','last_update':0,'prices':{},'previous':{},'failures':0,'events':[],'account':'main'}

def conn():
 c=sqlite3.connect(DB,timeout=15,check_same_thread=False);c.row_factory=sqlite3.Row;return c

def init():
 with conn() as c:
  c.execute('CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, cash REAL, start REAL, day TEXT, day_start REAL, locked INTEGER DEFAULT 0, losses INTEGER DEFAULT 0)')
  c.execute('CREATE TABLE IF NOT EXISTS positions (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, symbol TEXT, side TEXT, entry REAL, qty REAL, stop REAL, target REAL, opened TEXT)')
  c.execute('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, symbol TEXT, side TEXT, entry REAL, exit REAL, qty REAL, pnl REAL, reason TEXT, closed TEXT)')
  for name,amount in [('main',1000.),('tiny',6.)]:
   c.execute('INSERT OR IGNORE INTO accounts(name,cash,start,day,day_start) VALUES(?,?,?,?,?)',(name,amount,amount,datetime.now(timezone.utc).date().isoformat(),amount))

def event(msg):
 state['events'].insert(0,{'time':datetime.now(timezone.utc).isoformat(),'message':msg});state['events']=state['events'][:60]

def account(c,name):return c.execute('SELECT * FROM accounts WHERE name=?',(name,)).fetchone()
def positions(c,name):return [dict(x) for x in c.execute('SELECT * FROM positions WHERE account=?',(name,))]
def equity(c,name):
 a=account(c,name);return a['cash']+sum(p['qty']*(state['prices'].get(p['symbol'],p['entry'])-p['entry'])*(1 if p['side']=='LONG' else -1)-p['qty']*state['prices'].get(p['symbol'],p['entry'])*FEE for p in positions(c,name))
def rollover(c,name):
 a=account(c,name);today=datetime.now(timezone.utc).date().isoformat()
 if a['day']!=today:
  val=equity(c,name);c.execute('UPDATE accounts SET day=?,day_start=?,locked=0,losses=0 WHERE name=?',(today,val,name));state['running']=False;event('UTC day rollover; manual restart required')
def safety(c,name):
 rollover(c,name);a=account(c,name)
 if equity(c,name)<=a['day_start']*.98:
  c.execute('UPDATE accounts SET locked=1 WHERE name=?',(name,));state['running']=False;event('Daily loss lock triggered')
 if a['losses']>=3:
  c.execute('UPDATE accounts SET locked=1 WHERE name=?',(name,));state['running']=False

def close(c,p,reason):
 px=state['prices'].get(p['symbol'])
 if not px:return
 pnl=p['qty']*(px-p['entry'])*(1 if p['side']=='LONG' else -1)-(p['entry']+px)*p['qty']*FEE
 c.execute('UPDATE accounts SET cash=cash+?, losses=CASE WHEN ?<0 THEN losses+1 ELSE 0 END WHERE name=?',(pnl,pnl,p['account']))
 c.execute('DELETE FROM positions WHERE id=?',(p['id'],))
 c.execute('INSERT INTO trades(account,symbol,side,entry,exit,qty,pnl,reason,closed) VALUES(?,?,?,?,?,?,?,?,?)',(p['account'],p['symbol'],p['side'],p['entry'],px,p['qty'],pnl,reason,datetime.now(timezone.utc).isoformat()))
 event(f"{p['symbol']} {p['side']} closed {pnl:+.3f} USDT ({reason})")

def tick():
 urls=['https://fapi.binance.com/fapi/v1/ticker/price','https://fapi1.binance.com/fapi/v1/ticker/price']
 prices=None;errors=[]
 for url in urls:
  try:
   req=urllib.request.Request(url,headers={'User-Agent':'WhaleLab-Paper/1.0'})
   with urllib.request.urlopen(req,timeout=8) as response: data=json.load(response)
   d={x['symbol']:float(x['price']) for x in data}
   if all(math.isfinite(d.get(s,0)) and d[s]>0 for s in SYMS):prices={s:d[s] for s in SYMS};break
  except Exception as e:errors.append(type(e).__name__)
 with lock:
  if prices is None:
   state['running']=False;state['source']='unavailable';state['failures']+=1
   if state['failures']==1 or state['failures']%10==0:event('Price feed failed; trading STOPPED. Auto-retrying prices.')
   return
  if state['failures']:event('Price feed restored. Manual START required.')
  state['failures']=0;state['previous']=state['prices'];state['prices']=prices;state['last_update']=time.time();state['source']='Binance public futures prices'
  with conn() as c:
   for name in ['main','tiny']:
    for p in positions(c,name):
     px=prices[p['symbol']]
     if (p['side']=='LONG' and (px<=p['stop'] or px>=p['target'])) or (p['side']=='SHORT' and (px>=p['stop'] or px<=p['target'])):close(c,p,'stop/target crossed at observed price')
    safety(c,name)
   if state['running']:
    name=state['account'];a=account(c,name);openpos=positions(c,name)
    if not a['locked'] and len(openpos)<5:
     ranked=[]
     for sym in SYMS:
      old=state['previous'].get(sym)
      if not old:continue
      change=(prices[sym]-old)/old
      if abs(change)>=.0008:ranked.append((abs(change),sym,'LONG' if change>0 else 'SHORT'))
     for _,sym,side in sorted(ranked,reverse=True)[:2]:
      if len(openpos)>=5 or any(p['symbol']==sym for p in openpos):continue
      price=prices[sym];eq=equity(c,name);dist=price*.006
      qty=min(eq*.0025/(dist+price*.0008),max(0,a['cash'])/(price*1.01))
      notional=qty*price
      if notional<5 or sum(p['qty']*prices[p['symbol']] for p in openpos)+notional>eq:continue
      stop=price-dist if side=='LONG' else price+dist;target=price+1.5*dist if side=='LONG' else price-1.5*dist
      c.execute('UPDATE accounts SET cash=cash-? WHERE name=?',(notional*FEE,name))
      c.execute('INSERT INTO positions(account,symbol,side,entry,qty,stop,target,opened) VALUES(?,?,?,?,?,?,?,?)',(name,sym,side,price,qty,stop,target,datetime.now(timezone.utc).isoformat()))
      openpos.append({'symbol':sym,'qty':qty});event(f'Paper {side} {sym} opened, {notional:.2f} USDT notional')

def worker():
 while True:
  try:tick()
  except Exception as e:
   with lock:state['running']=False;event('Internal worker error; trading stopped')
  time.sleep(12)

def auth():
 if not TOKEN:return False
 return request.headers.get('X-WhaleLab-Token')==TOKEN

@app.get('/api/status')
def status():
 if not auth():return jsonify(error='Unauthorized; set WHALELAB_TOKEN on server'),401
 with lock:
  with conn() as c:
   name=state['account'];rollover(c,name);a=account(c,name);tr=[dict(t) for t in c.execute('SELECT * FROM trades WHERE account=? ORDER BY id DESC LIMIT 40',(name,))];pos=positions(c,name)
   return jsonify(running=state['running'],source=state['source'],last_update=state['last_update'],prices=state['prices'],account=name,equity=equity(c,name),cash=a['cash'],day_pnl=equity(c,name)-a['day_start'],locked=bool(a['locked']),positions=pos,trades=tr,events=state['events'][:20],failures=state['failures'])
@app.post('/api/control')
def control():
 if not auth():return jsonify(error='Unauthorized'),401
 data=request.get_json(silent=True) or {};action=data.get('action')
 with lock:
  with conn() as c:
   if action=='stop':state['running']=False;event('Manual SMART STOP')
   elif action=='start':
    a=account(c,state['account'])
    if a['locked']:return jsonify(error='Safety lock active'),409
    if time.time()-state['last_update']>30:return jsonify(error='No fresh Binance prices'),409
    state['running']=True;event('Manual START')
   elif action=='close_all':
    state['running']=False
    if time.time()-state['last_update']>30:return jsonify(error='Cannot close at stale prices; trading stopped'),409
    for p in positions(c,state['account']):close(c,p,'manual close all')
   elif action=='account':
    name=data.get('name')
    if name not in ('main','tiny'):return jsonify(error='Unknown account'),400
    state['running']=False;state['account']=name;event('Account switched; manual restart required')
   else:return jsonify(error='Unknown action'),400
 return jsonify(ok=True)
HTML='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>WhaleLab Cloud</title><style>body{font:15px system-ui;background:#0b1020;color:#eef4ff;margin:0}main{max-width:720px;margin:auto;padding:20px}.card{background:#182137;border:1px solid #34405a;padding:16px;border-radius:14px;margin:12px 0}.row{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}button,input,select{padding:12px;border-radius:9px;font:inherit}button{background:#16ad84;color:#071a13;border:0;font-weight:bold}button.stop{background:#ffc869}button.close{background:#ff8291}input{background:#0b1020;color:white;border:1px solid #475573;width:95%}small{color:#aab9d2}li{padding:6px 0}h1{font-size:24px}.danger{color:#ff8291}</style></head><body><main><h1>🐋 WhaleLab Cloud <small> PAPER ONLY</small></h1><p>Server-managed Binance price polling and virtual trading. No real-money orders.</p><div class="card"><label>Dashboard access token (provided by your hosting setup)</label><input id="token" type="password" placeholder="Paste server token"><p><button onclick="load()">Connect dashboard</button></p><small>Token is kept only in this browser tab, not saved to storage.</small></div><div class="card"><div id="status">Connect to view status.</div><div class="row"><button onclick="action('start')">▶ START</button><button class="stop" onclick="action('stop')">■ SMART STOP</button><button class="close" onclick="if(confirm('Close all virtual positions?'))action('close_all')">✕ CLOSE ALL</button></div><p><select id="account" onchange="action('account',this.value)"><option value="main">$1,000 virtual</option><option value="tiny">$6 virtual 🐋</option></select></p></div><div class="card"><h3>Open virtual positions</h3><div id="positions">—</div></div><div class="card"><h3>Recent closed trades</h3><div id="trades">—</div></div><div class="card"><h3>Activity log</h3><div id="events">—</div></div><p><small>Cloud uptime depends on hosting. Free hosts may sleep, restart, or lose ephemeral storage. No trained AI, guaranteed stop fills, or live trading.</small></p></main><script>const $=x=>document.getElementById(x);async function api(path,body){let opts={headers:{'X-WhaleLab-Token':$('token').value}};if(body){opts.method='POST';opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body)}let r=await fetch(path,opts);let j=await r.json();if(!r.ok)throw Error(j.error||'Request failed');return j}async function load(){if(!$('token').value)return;try{let d=await api('/api/status');$('account').value=d.account;$('status').innerHTML='<h2>'+(d.running?'🟢 RUNNING':'🟠 STOPPED')+'</h2><p>Source: '+d.source+'<br>Last update: '+(d.last_update?new Date(d.last_update*1000).toLocaleTimeString():'never')+'<br>Equity: $'+d.equity.toFixed(2)+'<br>Daily P&L: $'+d.day_pnl.toFixed(2)+'<br>Open positions: '+d.positions.length+'<br>Safety lock: '+(d.locked?'ACTIVE':'off')+'</p>';$('positions').innerHTML=d.positions.map(p=>'<p>'+p.symbol+' '+p.side+' · entry '+p.entry.toFixed(4)+' · stop '+p.stop.toFixed(4)+' · target '+p.target.toFixed(4)+'</p>').join('')||'None';$('trades').innerHTML=d.trades.slice(0,12).map(t=>'<p>'+t.symbol+' '+t.side+' · $'+t.pnl.toFixed(3)+' · '+t.reason+'</p>').join('')||'None';$('events').innerHTML='<ul>'+d.events.slice(0,12).map(e=>'<li><small>'+new Date(e.time).toLocaleTimeString()+'</small> '+e.message+'</li>').join('')+'</ul>'}catch(e){$('status').textContent='Error: '+e.message}}async function action(a,name){try{await api('/api/control',{action:a,name});await load()}catch(e){alert(e.message)}}setInterval(()=>{if($('token').value)load()},12000)</script></body></html>'''
@app.get('/')
def home():return render_template_string(HTML)
init()
threading.Thread(target=worker,daemon=True).start()
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.environ.get('PORT','8080')))
