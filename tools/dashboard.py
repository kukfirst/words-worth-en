#!/usr/bin/env python3
"""Live view of the translation run. stdlib only; reads progress.jsonl + run.log."""
import json, pathlib, re, subprocess, time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROGRESS, RUNLOG, UNITS = ROOT / 'progress.jsonl', ROOT / 'run.log', ROOT / 'units.json'
LIVE = ROOT / 'live.json'
TOTAL_UNITS = len(json.load(open(UNITS)))
TOTAL_FILES = len({u['file'] for u in json.load(open(UNITS))})

def records():
    if not PROGRESS.exists():
        return []
    out = []
    for line in PROGRESS.read_text(errors='replace').splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out

def alive():
    r = subprocess.run(['pgrep', '-f', 'tools/translate.py'], capture_output=True, text=True)
    return bool(r.stdout.strip())

def state():
    recs = records()
    done = [r for r in recs if r.get('event') == 'file_done']
    batches = [r for r in recs if r.get('event') == 'batch']
    rejects = [r for r in recs if r.get('event') == 'batch_reject']
    reviews = [r for r in recs if r.get('event') == 'review']
    starts = [r for r in recs if r.get('event') == 'file_start']
    units_done = sum(r.get('units', 0) for r in done)
    secs = sum(r.get('secs', 0) for r in done)
    rate = units_done / secs if secs else 0
    cur = starts[-1]['file'] if starts and (not done or starts[-1]['file'] != done[-1]['file']) else None
    cur_at = 0
    if cur:
        cur_at = sum(b.get('n', 0) for b in batches if b['file'] == cur)
    pairs = []
    for b in batches[-6:]:
        for ja, en in b.get('pairs', [])[:40]:
            pairs.append({'file': b['file'], 'ja': ja, 'en': en})
    try:
        lv = json.loads(LIVE.read_text())
    except Exception:
        lv = {}
    if lv.get('ts'):
        lv['age'] = round(time.time() - lv['ts'], 1)          # silence detector
    if lv.get('started'):
        lv['elapsed'] = round(time.time() - lv['started'], 1)  # how long this batch runs
    return {
        'live': lv,
        'alive': alive(),
        'files_done': len(done), 'files_total': TOTAL_FILES,
        'units_done': units_done, 'units_total': TOTAL_UNITS,
        'units_failed': sum(r.get('failed', 0) for r in done),
        'gates_bad': [r for r in done if r.get('gate') != 'ok'],
        'rate': round(rate, 2),
        'eta_h': round((TOTAL_UNITS - units_done) / rate / 3600, 1) if rate else None,
        'current': cur, 'current_at': cur_at,
        'rejects': rejects[-8:], 'reviews': reviews[-8:],
        'pairs': pairs[-60:],
        'recent_files': [{'f': r['file'], 'u': r.get('units'), 's': r.get('secs'),
                          'g': r.get('gate'), 'x': r.get('failed', 0)} for r in done[-14:]][::-1],
    }

HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>Words Worth — прогресс</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#242833;--fg:#e6e8ee;--dim:#8b93a7;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24;--ja:#7dd3fc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 ui-monospace,"JetBrains Mono",Menlo,monospace}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.3px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
.live{background:var(--ok);box-shadow:0 0 8px var(--ok)}.dead{background:var(--bad)}
.wrap{padding:18px 20px;display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0 0 10px}
.big{font-size:30px;font-weight:600;letter-spacing:-.5px}.sub{color:var(--dim);font-size:12px}
.bar{height:7px;background:#0b0d11;border-radius:4px;overflow:hidden;margin:10px 0 6px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#3b82f6,#22d3ee)}
.stats{display:flex;gap:22px;flex-wrap:wrap;margin-top:8px}
.stats div span{display:block;color:var(--dim);font-size:11px}
.flow{grid-column:1/-1;max-height:46vh;overflow:auto}
.pair{padding:6px 0;border-bottom:1px solid var(--line)}
.pair:last-child{border:0}.pair .j{color:var(--ja)}.pair .e{color:var(--fg)}
.pair .f{color:var(--dim);font-size:11px}
table{width:100%;border-collapse:collapse}td{padding:4px 8px 4px 0;border-bottom:1px solid var(--line);font-size:12px}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
pre{white-space:pre-wrap;word-break:break-word;margin:4px 0;font-size:11px;color:var(--dim)}
.empty{color:var(--dim);font-size:12px}
.now{grid-column:1/-1}
.tail{background:#0b0d11;border:1px solid var(--line);border-radius:6px;padding:8px 10px;
  max-height:130px;overflow:auto;color:#cfd6e6;font-size:11.5px;margin:10px 0 6px}
.ja-now{color:var(--ja);font-size:11.5px;max-height:60px;overflow:auto}
.stale{color:var(--warn)}
</style></head><body>
<header><h1>Words Worth — JA→EN</h1><span id="status"></span><span class="sub" id="cur"></span><span class="sub" id="upd" style="margin-left:auto"></span></header>
<div class="wrap">
 <div class="card now"><h2>Сейчас</h2>
   <div id="nowhead" class="sub"></div>
   <div class="bar"><i id="nowb"></i></div>
   <div class="stats"><div><span>фаза</span><b id="ph"></b></div><div><span>токенов</span><b id="tk"></b></div>
     <div><span>ток/с</span><b id="ts"></b></div><div><span>батч идёт</span><b id="el"></b></div>
     <div><span>молчит</span><b id="ag"></b></div>
     <div><span>строк готово</span><b id="ld"></b></div></div>
   <pre id="tail" class="tail"></pre>
   <div id="jaline" class="ja-now"></div>
 </div>
 <div class="card"><h2>Строки</h2><div class="big" id="u"></div><div class="bar"><i id="ub"></i></div>
   <div class="stats"><div><span>файлы</span><b id="f"></b></div><div><span>скорость</span><b id="r"></b></div>
   <div><span>осталось</span><b id="eta"></b></div><div><span>не переведено</span><b id="fail"></b></div></div></div>
 <div class="card"><h2>Файлы (последние)</h2><table id="files"></table></div>
 <div class="card"><h2>Отказы гейтов и ревью</h2><div id="probs"></div></div>
 <div class="card flow"><h2>Поток перевода</h2><div id="flow"></div></div>
</div>
<script>
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function tick(){
 let d; try{ d=await (await fetch('/api')).json() }catch(e){ return }
 document.getElementById('status').innerHTML=
   `<span class="dot ${d.alive?'live':'dead'}"></span>${d.alive?'работает':'ОСТАНОВЛЕН'}`;
 document.getElementById('cur').textContent=d.current?`сейчас: ${d.current} (${d.current_at} строк)`:'';
 document.getElementById('u').textContent=`${d.units_done.toLocaleString('ru')} / ${d.units_total.toLocaleString('ru')}`;
 document.getElementById('ub').style.width=(100*d.units_done/d.units_total).toFixed(1)+'%';
 document.getElementById('f').textContent=`${d.files_done} / ${d.files_total}`;
 document.getElementById('r').textContent=d.rate?d.rate+' строк/с':'—';
 document.getElementById('eta').textContent=d.eta_h!=null?d.eta_h+' ч':'—';
 const fl=document.getElementById('fail'); fl.textContent=d.units_failed; fl.className=d.units_failed?'bad':'ok';
 document.getElementById('files').innerHTML=d.recent_files.map(r=>
   `<tr><td>${esc(r.f.replace('.MES.rkt',''))}</td><td>${r.u}</td><td>${r.s}s</td>
    <td class="${r.g==='ok'?'ok':'bad'}">${r.g==='ok'?'ok':'gate'}</td>
    <td class="${r.x?'bad':''}">${r.x||''}</td></tr>`).join('')||'<tr><td class="empty">—</td></tr>';
 const p=[...d.gates_bad.map(g=>`<pre class="bad">${esc(g.file)}: ${esc(String(g.gate).slice(0,300))}</pre>`),
          ...d.rejects.map(r=>`<pre class="warn">${esc(r.file)} @${r.at}: ${esc(String(r.why).slice(0,220))}</pre>`),
          ...d.reviews.map(r=>`<pre>${esc(r.file)} @${r.at}: ${r.issues} замечаний — ${esc(String(r.sample).slice(0,220))}</pre>`)];
 document.getElementById('probs').innerHTML=p.join('')||'<div class="empty">пока чисто</div>';
 document.getElementById('flow').innerHTML=d.pairs.slice().reverse().map(x=>
   `<div class="pair"><div class="f">${esc(x.file.replace('.MES.rkt',''))}</div>
    <div class="j">${esc(x.ja)||'&nbsp;'}</div><div class="e">${esc(x.en)||'&nbsp;'}</div></div>`).join('')
   ||'<div class="empty">ждём первый батч с новым логом…</div>';
 const L=d.live||{};
 const phases={translate:'перевод',review:'ревью',gates:'гейты',summarise:'резюме сцены'};
 document.getElementById('ph').textContent=phases[L.phase]||L.phase||'—';
 document.getElementById('tk').textContent=L.gen_tokens??'—';
 document.getElementById('ts').textContent=L.tok_s??'—';
 const el=document.getElementById('el');
 const fmt=v=>v==null?'—':(v<60?v.toFixed(0)+' с':Math.floor(v/60)+' мин '+Math.round(v%60)+' с');
 el.textContent=fmt(L.elapsed);
 const ag=document.getElementById('ag');
 ag.textContent=fmt(L.age);
 ag.className=(L.age>20?'stale':'');
 document.getElementById('ld').textContent=L.lines_done??'—';
 document.getElementById('nowhead').textContent=L.file
   ? `${L.file.replace('.MES.rkt','')} — батч ${L.batch??'?'} / ${L.batches??'?'}`
     + (L.attempt>1?`  (попытка ${L.attempt})`:'') : '—';
 const bw=(L.batches&&L.batch)?100*L.batch/L.batches:0;
 document.getElementById('nowb').style.width=bw.toFixed(1)+'%';
 document.getElementById('tail').textContent=L.gen_tail||'(ждём ответа модели…)';
 document.getElementById('jaline').textContent=(L.batch_ja||[]).join('  ·  ');
 document.getElementById('upd').textContent='обновлено '+new Date().toLocaleTimeString('ru');
}
tick(); setInterval(tick,1500);
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path.startswith('/api'):
            body = json.dumps(state(), ensure_ascii=False).encode()
            ct = 'application/json; charset=utf-8'
        else:
            body = HTML.encode()
            ct = 'text/html; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == '__main__':
    print('http://127.0.0.1:8777', flush=True)
    HTTPServer(('127.0.0.1', 8777), H).serve_forever()
