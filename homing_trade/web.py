"""Browser dashboard + control center for the homing-trade bot.

`python -m homing_trade.web` serves a single-page UI on http://localhost:<web_port> and runs
the trading engine in a background thread. The UI gives full visibility (live prices, every
algo + AI brain, positions, logs) and control (start / pause / resume / stop / reset, and
exit any open trade). Stdlib only — no Flask/React.

Run the UI INSTEAD of the bare daemon (both run an engine; don't run both on one DB).
"""
import http.server
import json
import queue
import threading
import time
import webbrowser

from homing_trade.config import CONFIG, from_env
from homing_trade.repository import Repository
from homing_trade.engine import run as engine_run
from homing_trade.feed import get_prices
from homing_trade.notify import build_notifier


class Controller:
    """Owns the engine thread and the start/stop/pause/reset/exit controls."""

    def __init__(self, cfg, runner=None, notifier=None):
        self.cfg = cfg
        self._runner = runner or engine_run
        self._thread = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._commands = queue.Queue()
        self.last_error = None
        self.notifier = notifier or build_notifier(cfg)

    def status(self):
        if self._thread and self._thread.is_alive():
            return "paused" if self._paused.is_set() else "running"
        return "stopped"

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._paused.clear()
        self.last_error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._runner(self.cfg, notifier=self.notifier, should_stop=self._stop.is_set,
                         is_paused=self._paused.is_set, sleeper=lambda s: self._stop.wait(s),
                         commands=self._commands)
        except Exception as exc:  # surface engine crashes in the UI
            self.last_error = str(exc)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)
            self._thread = None

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def close_trade(self, strategy):
        if strategy:
            self._commands.put({"action": "close", "strategy": strategy})

    def reset(self):
        """Stop and wipe the paper ledger (keeps cached candles)."""
        self.stop()
        repo = Repository.open(self.cfg.db_path)
        try:
            repo.reset_paper_ledger()
        finally:
            repo.close()


def build_state(cfg, controller):
    """Snapshot of everything the UI shows (read-only)."""
    repo = Repository.open(cfg.db_path)
    try:
        names = repo.strategy_names()
        strategies = []
        for n in names:
            bal = repo.get_balance(n)
            pos = repo.get_open_position(n)
            eq = repo.latest_equity(n)
            equity = eq if eq is not None else bal
            position = None
            if pos is not None:
                position = {"side": pos.side, "size": round(pos.size, 6),
                            "entry": round(pos.entry_price, 2), "leverage": pos.leverage,
                            "stop": round(pos.stop_price, 2),
                            "unreal": round(equity - bal, 2)}
            item = {"name": n, "is_ai": n.startswith("llm_"),
                    "balance": round(bal, 2), "equity": round(equity, 2),
                    "pnl_pct": round((equity - cfg.starting_balance) / cfg.starting_balance * 100, 2),
                    "position": position}
            if n.startswith("llm_"):
                lr = repo.recent_llm_responses(n, 1)
                if lr:
                    r = lr[0]
                    item["ai"] = {"action": r["action"], "confidence": r["confidence"],
                                  "observation": r["observation"], "prediction": r["prediction"],
                                  "rationale": r["rationale"], "error": r["error"], "ts": r["ts"]}
            strategies.append(item)
        trades = repo.recent_trades(25)
        decisions = repo.recent_decisions(40)
        return {
            "status": controller.status(),
            "last_error": controller.last_error,
            "starting_balance": cfg.starting_balance,
            "config": {"interval": cfg.interval, "leverage_max": cfg.leverage_max,
                       "kill_switch": cfg.max_daily_loss, "pair": cfg.pair_candles},
            "strategies": strategies, "trades": trades, "decisions": decisions,
        }
    finally:
        repo.close()


class _Handler(http.server.BaseHTTPRequestHandler):
    cfg = None
    controller = None
    _price_cache = {"ts": 0.0, "data": {}}

    def log_message(self, *args):
        pass  # keep the console quiet

    def _send(self, body, code=200, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _prices(self):
        c = _Handler._price_cache
        if time.time() - c["ts"] > 5:  # cache so polling doesn't hammer CoinDCX
            try:
                c["data"] = get_prices(self.cfg.price_symbols)
            except Exception as exc:
                c["data"] = {"error": str(exc)}
            c["ts"] = time.time()
        return c["data"]

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send(DASHBOARD_HTML, ctype="text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(build_state(self.cfg, self.controller))
        elif self.path == "/api/prices":
            self._send(self._prices())
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:
            body = {}
        if self.path == "/api/control":
            action = body.get("action")
            ctrl = self.controller
            fn = {"start": ctrl.start, "stop": ctrl.stop, "pause": ctrl.pause,
                  "resume": ctrl.resume, "reset": ctrl.reset}.get(action)
            if fn is None:
                return self._send({"error": "unknown action"}, 400)
            fn()
            self._send({"status": ctrl.status()})
        elif self.path == "/api/close":
            self.controller.close_trade(body.get("strategy"))
            self._send({"ok": True})
        else:
            self._send({"error": "not found"}, 404)


def make_server(cfg, controller):
    _Handler.cfg = cfg
    _Handler.controller = controller
    return http.server.ThreadingHTTPServer(("127.0.0.1", cfg.web_port), _Handler)


def main(cfg=None, *, open_browser=True):
    cfg = cfg or from_env()
    controller = Controller(cfg)
    controller.start()  # bot is live the moment the UI opens
    server = make_server(cfg, controller)
    url = f"http://localhost:{server.server_address[1]}"
    print(f"homing-trade UI → {url}  (Ctrl-C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        server.shutdown()


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>homing-trade</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--bg:#0d1117;--card:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e;
        --grn:#3fb950;--red:#f85149;--blu:#58a6ff;--yel:#d29922}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);
    font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
  header{display:flex;align-items:center;gap:14px;padding:12px 18px;border-bottom:1px solid var(--bd);
    position:sticky;top:0;background:var(--bg);flex-wrap:wrap}
  h1{font-size:16px;margin:0;letter-spacing:.5px} h1 small{color:var(--mut);font-weight:400}
  .badge{padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;text-transform:uppercase}
  .running{background:rgba(63,185,80,.18);color:var(--grn)}
  .paused{background:rgba(210,153,34,.18);color:var(--yel)}
  .stopped{background:rgba(248,81,73,.18);color:var(--red)}
  button{background:var(--card);color:var(--fg);border:1px solid var(--bd);border-radius:6px;
    padding:6px 12px;cursor:pointer;font:inherit} button:hover{border-color:var(--blu)}
  button.danger:hover{border-color:var(--red);color:var(--red)}
  .spacer{flex:1}
  main{padding:16px 18px;display:grid;gap:16px;grid-template-columns:1fr;max-width:1400px;margin:0 auto}
  .row{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
  .grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(320px,1fr))}
  .card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px 14px}
  .card h3{margin:0 0 6px;font-size:13px;display:flex;justify-content:space-between;align-items:center}
  .price .v{font-size:20px;font-weight:700} .mut{color:var(--mut)} .up{color:var(--grn)} .dn{color:var(--red)}
  .tag{font-size:10px;padding:1px 6px;border-radius:8px;background:rgba(88,166,255,.15);color:var(--blu)}
  .ai .tag{background:rgba(210,153,34,.15);color:var(--yel)}
  .pos{margin-top:8px;padding:8px;border:1px dashed var(--bd);border-radius:8px;font-size:13px}
  .kv{display:flex;justify-content:space-between} .b{font-weight:700}
  .reason{margin-top:8px;font-size:12px;color:var(--mut);white-space:pre-wrap;max-height:150px;overflow:auto}
  .reason b{color:var(--fg)}
  .err{color:var(--red);font-weight:700}
  table{width:100%;border-collapse:collapse;font-size:12px} td,th{padding:4px 6px;text-align:left;border-bottom:1px solid var(--bd)}
  th{color:var(--mut);font-weight:600;position:sticky;top:0;background:var(--card)}
  .logwrap{max-height:340px;overflow:auto} h2{font-size:14px;margin:4px 0;color:var(--mut)}
</style></head><body>
<header>
  <h1>🎯 homing-trade <small id="cfg"></small></h1>
  <span id="status" class="badge stopped">…</span>
  <div class="spacer"></div>
  <button onclick="ctl('start')">▶ Start</button>
  <button onclick="ctl('pause')">⏸ Pause</button>
  <button onclick="ctl('resume')">⏵ Resume</button>
  <button onclick="ctl('stop')">⏹ Stop</button>
  <button class="danger" onclick="resetBot()">⟲ Reset</button>
</header>
<main>
  <div><h2>live prices</h2><div id="prices" class="row"></div></div>
  <div><h2>strategies &amp; AI</h2><div id="strats" class="grid"></div></div>
  <div class="grid">
    <div class="card"><h3>recent trades</h3><div class="logwrap"><table id="trades"><thead>
      <tr><th>time</th><th>strategy</th><th>act</th><th>side</th><th>price</th><th>pnl</th></tr></thead><tbody></tbody></table></div></div>
    <div class="card"><h3>decision log</h3><div class="logwrap"><table id="decisions"><thead>
      <tr><th>time</th><th>strategy</th><th>action</th><th>reason</th></tr></thead><tbody></tbody></table></div></div>
  </div>
  <div id="err" class="err"></div>
</main>
<script>
const $=s=>document.querySelector(s);
const fmt=n=>n==null?'—':Number(n).toLocaleString(undefined,{maximumFractionDigits:2});
const tm=ts=>new Date(ts).toLocaleTimeString();
async function ctl(action){await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});refresh();}
async function resetBot(){if(confirm('Reset wipes the paper ledger (wallets, trades, positions). Continue?')){await ctl('reset');}}
async function closeTrade(s){if(confirm('Exit '+s+' open trade at market?')){await fetch('/api/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({strategy:s})});refresh();}}
async function refresh(){
  try{
    const s=await (await fetch('/api/state')).json();
    const st=$('#status'); st.textContent=s.status; st.className='badge '+s.status;
    $('#cfg').textContent=`· ${s.config.pair} · ${s.config.interval} · ${s.config.leverage_max}× · kill ₹${fmt(s.config.kill_switch)}`;
    $('#err').textContent=s.last_error?('engine error: '+s.last_error):'';
    $('#strats').innerHTML=s.strategies.map(card).join('')||'<div class="mut">no strategies yet — click Start</div>';
    $('#trades').querySelector('tbody').innerHTML=s.trades.map(t=>
      `<tr><td>${tm(t.ts)}</td><td>${t.strategy}</td><td>${t.action}</td><td>${t.side}</td><td>${fmt(t.price)}</td><td class="${t.pnl>=0?'up':'dn'}">${fmt(t.pnl)}</td></tr>`).join('');
    $('#decisions').querySelector('tbody').innerHTML=s.decisions.map(d=>
      `<tr><td>${tm(d.ts)}</td><td>${d.strategy}</td><td>${d.action}</td><td class="mut">${(d.reason||'').slice(0,90)}</td></tr>`).join('');
  }catch(e){$('#err').textContent='UI fetch failed: '+e;}
}
function card(x){
  const pnl=x.pnl_pct>=0?'up':'dn';
  let pos=x.position?`<div class="pos"><div class="kv b"><span>${x.position.side} ${x.position.leverage}×</span>
    <span class="${x.position.unreal>=0?'up':'dn'}">${fmt(x.position.unreal)}</span></div>
    <div class="kv mut"><span>entry ${fmt(x.position.entry)}</span><span>size ${x.position.size}</span></div>
    <button class="danger" style="margin-top:6px" onclick="closeTrade('${x.name}')">✕ Exit trade</button></div>`
    :`<div class="pos mut">flat</div>`;
  let ai='';
  if(x.ai){
    ai=`<div class="reason">${x.ai.error?`<span class="err">⚠ ${x.ai.error}</span>`:''}
      <b>action:</b> ${x.ai.action} (${fmt(x.ai.confidence)})
      ${x.ai.observation?`\n<b>saw:</b> ${x.ai.observation}`:''}
      ${x.ai.prediction?`\n<b>predicts:</b> ${x.ai.prediction}`:''}
      ${x.ai.rationale?`\n<b>why:</b> ${x.ai.rationale}`:''}</div>`;
  }
  return `<div class="card ${x.is_ai?'ai':''}"><h3><span>${x.name} ${x.is_ai?'<span class="tag">AI</span>':'<span class="tag">algo</span>'}</span>
    <span class="${pnl}">${x.pnl_pct>=0?'+':''}${fmt(x.pnl_pct)}%</span></h3>
    <div class="kv"><span class="mut">equity</span><span class="b">₹${fmt(x.equity)}</span></div>
    <div class="kv"><span class="mut">balance</span><span>₹${fmt(x.balance)}</span></div>${pos}${ai}</div>`;
}
async function prices(){
  try{
    const p=await (await fetch('/api/prices')).json();
    $('#prices').innerHTML=Object.entries(p).filter(([k])=>k!=='error').map(([k,v])=>
      v?`<div class="card price"><h3>${k}</h3><div class="v">${fmt(v.last)}</div>
         <div class="${v.change>=0?'up':'dn'}">${v.change>=0?'+':''}${fmt(v.change)}% 24h</div></div>`
      :`<div class="card price"><h3>${k}</h3><div class="v mut">n/a</div></div>`).join('')
      ||'<div class="mut">prices unavailable</div>';
  }catch(e){}
}
refresh();prices();setInterval(refresh,2000);setInterval(prices,5000);
</script></body></html>"""


if __name__ == "__main__":
    main()
