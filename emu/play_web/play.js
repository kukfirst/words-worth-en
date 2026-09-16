'use strict';
// The cockpit page: one WebSocket to emu/play.py. Frames in (binary RGB565), telemetry, map
// and events in (JSON); keys, mouse and commands out (JSON).
const { Screen, FILTERS, SLIDER_INDEX } = window.WWScreen;
const $ = id => document.getElementById(id);
const app = $('app');

const store = {
  get(k, d) { try { const v = localStorage.getItem('ww.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('ww.' + k, JSON.stringify(v)); } catch (e) { /* private window */ } },
};

const S = { emu: {}, tele: {}, map: null, slots: [], settings: {}, link: false, emuAt: 0, framesIn: 0, fpsIn: 0 };
const view = {
  filter: store.get('filter', 'crt'),
  params: store.get('params', {}),
  aspect: store.get('aspect', '4:3'),
  integer: store.get('integer', false),
  mouse: store.get('mouse', 'pointer'),
  theater: store.get('theater', false),
  fold: store.get('fold', false),
};

// ---------------------------------------------------------------- screen
const screen = new Screen($('screen'));
function paramsFor(id) {
  const f = FILTERS.find(x => x.id === id) || FILTERS[0];
  const p = view.params[id];
  return Array.isArray(p) && p.length === 4 ? p : f.p.slice();
}
function applyFilter() {
  screen.setFilter(view.filter, paramsFor(view.filter));
  $('filter').value = view.filter;
  const f = FILTERS.find(x => x.id === view.filter) || FILTERS[0];
  const box = $('sliders');
  box.innerHTML = '';
  for (const name of f.sliders) {
    const i = SLIDER_INDEX[name];
    const lab = document.createElement('label');
    lab.innerHTML = `${name}<input type="range" min="0" max="100">`;
    const inp = lab.querySelector('input');
    const max = name === 'curvature' ? 0.3 : 1;
    inp.value = Math.round(paramsFor(view.filter)[i] / max * 100);
    inp.oninput = () => {
      const p = paramsFor(view.filter);
      p[i] = inp.value / 100 * max;
      view.params[view.filter] = p;
      store.set('params', view.params);
      screen.setParams(p);
    };
    inp.onchange = () => inp.blur();
    box.appendChild(lab);
  }
}
for (const f of FILTERS) {
  const o = document.createElement('option');
  o.value = f.id; o.textContent = f.name;
  $('filter').appendChild(o);
}
$('filter').onchange = e => { view.filter = e.target.value; store.set('filter', view.filter); applyFilter(); e.target.blur(); };

function layout() {
  const stage = $('stage');
  const pad = view.theater ? 16 : 28;
  const W = stage.clientWidth - pad, H = stage.clientHeight - pad;
  const [sw, sh] = screen.src;
  const ar = view.aspect === 'square' ? sw / sh : 4 / 3;
  let w, h;
  if (view.integer) {
    const n = Math.max(1, Math.floor(Math.min(W / sw, H / (sw / ar))));
    w = sw * n; h = w / ar;
    if (w > W || h > H) { w = Math.min(W, H * ar); h = w / ar; }
  } else {
    w = Math.min(W, H * ar); h = w / ar;
  }
  screen.resize(Math.floor(w), Math.floor(h));
}
new ResizeObserver(layout).observe($('stage'));
$('aspect').value = view.aspect;
$('aspect').onchange = e => { view.aspect = e.target.value; store.set('aspect', view.aspect); layout(); e.target.blur(); };
$('intscale').checked = view.integer;
$('intscale').onchange = e => { view.integer = e.target.checked; store.set('integer', view.integer); layout(); e.target.blur(); };

// ---------------------------------------------------------------- link
let ws = null, retry = 0;
function send(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => { S.link = true; retry = 0; renderEmu(); };
  ws.onclose = () => {
    S.link = false; renderEmu();
    setTimeout(connect, Math.min(3000, 250 * (++retry)));
  };
  ws.onmessage = e => {
    if (typeof e.data === 'string') onText(JSON.parse(e.data));
    else onFrame(e.data);
  };
}
let lastSrc = '';
function onFrame(buf) {
  const dv = new DataView(buf);
  const w = dv.getUint16(4, true), h = dv.getUint16(6, true);
  screen.frame(w, h, new Uint16Array(buf, 16, w * h));
  S.framesIn++;
  if (lastSrc !== w + 'x' + h) { lastSrc = w + 'x' + h; layout(); }
}
function onText(m) {
  switch (m.t) {
    case 'hello':
      S.settings = m.settings || {};
      S.emu = m.emu || {}; S.emuAt = performance.now();
      S.tele = m.tele || {}; S.map = m.map; S.slots = m.slots || []; S.text = m.text;
      renderEmu(); renderTele(); renderMap(); renderSlots(); renderText();
      for (const e of (m.events || []).slice(-2)) if (Date.now() / 1000 - e.ts < 60) toast(e.msg, e.level);
      break;
    case 'emu': S.emu = m; S.emuAt = performance.now(); if (m.settings) S.settings = m.settings; renderEmu(); break;
    case 'tele': renderTele(m); break;
    case 'map': S.map = m; renderMap(); break;
    case 'text': S.text = m.text; renderText(); holdStatus(); break;
    case 'text_edit': onTextEdit(m); break;
    case 'text_check': onTextCheck(m); break;
    case 'slots': S.slots = m.slots; renderSlots(); break;
    case 'event': toast(m.msg, m.level); break;
  }
}

// ---------------------------------------------------------------- keyboard
const CODE = {
  ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  Space: 'space', Enter: 'return_key', NumpadEnter: 'kp_enter', Escape: 'escape', Backspace: 'backspace',
  Minus: 'minus', Equal: 'equals', BracketLeft: 'leftbracket', BracketRight: 'rightbracket',
  Semicolon: 'semicolon', Quote: 'quote', Comma: 'comma', Period: 'period', Slash: 'slash',
  Backslash: 'backslash', Backquote: 'backquote', ShiftLeft: 'lshift', ShiftRight: 'rshift',
  ControlLeft: 'lctrl', ControlRight: 'rctrl', AltLeft: 'lalt', AltRight: 'ralt',
  Insert: 'insert', Delete: 'delete', Home: 'home', End: 'end', PageUp: 'pageup', PageDown: 'pagedown',
  NumpadDecimal: 'kp_period', NumpadAdd: 'kp_plus', NumpadSubtract: 'kp_minus',
  NumpadMultiply: 'kp_multiply', NumpadDivide: 'kp_divide', CapsLock: 'capslock',
};
'abcdefghijklmnopqrstuvwxyz'.split('').forEach(c => { CODE['Key' + c.toUpperCase()] = c; });
['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']
  .forEach((n, i) => { CODE['Digit' + i] = n; CODE['Numpad' + i] = 'kp' + i; });

const held = new Set();
let turbo = false;
function setTurbo(on) { if (turbo !== on) { turbo = on; send({ c: 'turbo', d: on }); renderEmu(); } }
const RESERVED = {
  Tab: d => setTurbo(d),
  F1: d => d && toggleHelp(),
  F2: d => d && saveSlot('slot1'),
  F4: d => d && loadSlot('slot1', true),
  F7: d => d && cycleFilter(),
  F8: d => d && screenshot(),
  F9: d => d && togglePause(),
  Pause: d => d && togglePause(),
  F10: d => d && toggleTheater(),
  F11: d => d && toggleFullscreen(),
};
// ⚠️ While the proofreader types into the editor, the keyboard belongs to the page, not to
// the game -- otherwise every letter would also be pressed on the PC-98.
// ⚠️ A read-only box is NOT typing: the caret may rest in it while the line is still being
// identified, and the arrows must go on driving the game until it can actually be edited.
const typing = e => {
  const el = e.target instanceof Element
    && e.target.closest('input:not([type=range]), textarea, [contenteditable]');
  return !!el && !el.readOnly;
};
window.addEventListener('keydown', e => {
  if (typing(e)) return;
  if (e.ctrlKey && (e.code === 'KeyR' || e.code === 'KeyW' || e.code === 'KeyT')) return;   // browser keys
  if (RESERVED[e.code]) { e.preventDefault(); if (!e.repeat) RESERVED[e.code](true); return; }
  if (e.code === 'Escape' && !$('help').hidden) { e.preventDefault(); toggleHelp(); return; }
  const k = CODE[e.code];
  if (!k) return;
  e.preventDefault();
  if (e.repeat || held.has(e.code)) return;
  held.add(e.code);
  send({ c: 'key', k, d: 1 });
  padFlash(k, true);
}, true);
window.addEventListener('keyup', e => {
  if (typing(e)) return;
  if (RESERVED[e.code]) { e.preventDefault(); RESERVED[e.code](false); return; }
  const k = CODE[e.code];
  if (!k) return;
  e.preventDefault();
  held.delete(e.code);
  send({ c: 'key', k, d: 0 });
  padFlash(k, false);
}, true);
function releaseAll() { held.clear(); turbo = false; send({ c: 'release_all' }); }
window.addEventListener('blur', releaseAll);
document.addEventListener('visibilitychange', () => { if (document.hidden) releaseAll(); });

// on-screen keys: held while pressed, like the real ones
for (const b of document.querySelectorAll('[data-key]')) {
  const k = b.dataset.key;
  const up = () => { if (b.classList.contains('hit')) { b.classList.remove('hit'); send({ c: 'key', k, d: 0 }); } };
  b.addEventListener('pointerdown', e => { e.preventDefault(); b.setPointerCapture(e.pointerId); b.classList.add('hit'); send({ c: 'key', k, d: 1 }); });
  b.addEventListener('pointerup', up);
  b.addEventListener('pointercancel', up);
  b.addEventListener('lostpointercapture', up);
}
function padFlash(k, on) {
  const b = document.querySelector(`[data-key="${k}"]`);
  if (b) b.classList.toggle('hit', on);
}

// ---------------------------------------------------------------- mouse
const canvas = $('screen');
let aim = null, aimSent = null, rel = [0, 0];
function srcPoint(e) {
  const r = canvas.getBoundingClientRect();
  return screen.toSource((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
}
function setMouseMode(mode) {
  view.mouse = mode; store.set('mouse', mode);
  $('mouse-mode').value = mode;
  app.classList.toggle('capture', mode === 'capture');
  if (mode !== 'capture' && document.pointerLockElement) document.exitPointerLock();
}
$('mouse-mode').onchange = e => { setMouseMode(e.target.value); e.target.blur(); };
canvas.addEventListener('pointermove', e => {
  if (document.pointerLockElement === canvas) {
    const r = canvas.getBoundingClientRect();
    rel[0] += e.movementX * screen.src[0] / r.width;
    rel[1] += e.movementY * screen.src[1] / r.height;
  } else if (view.mouse === 'pointer') {
    aim = srcPoint(e);
  }
});
canvas.addEventListener('pointerdown', e => {
  e.preventDefault();
  if (view.mouse === 'capture' && document.pointerLockElement !== canvas) {
    const p = canvas.requestPointerLock({ unadjustedMovement: true });
    if (p && p.catch) p.catch(() => canvas.requestPointerLock());
    return;
  }
  if (view.mouse === 'pointer') {
    // always re-send: the core lets go of the cursor once it arrives (the game may have moved
    // it since with the arrow keys), and a click must land where the pointer is
    aim = srcPoint(e); aimSent = null; flushMouse();
  }
  send({ c: 'btn', b: e.button === 2 ? 'right' : 'left', d: 1 });
});
canvas.addEventListener('pointerup', e => {
  send({ c: 'btn', b: e.button === 2 ? 'right' : 'left', d: 0 });
});
canvas.addEventListener('contextmenu', e => e.preventDefault());
document.addEventListener('pointerlockchange', () => {
  $('capturehint').hidden = document.pointerLockElement !== canvas;
});
function flushMouse() {
  if (aim && (!aimSent || aim[0] !== aimSent[0] || aim[1] !== aimSent[1])) {
    send({ c: 'aim', x: aim[0], y: aim[1] });
    aimSent = aim;
  }
  const dx = Math.trunc(rel[0]), dy = Math.trunc(rel[1]);
  if (dx || dy) { send({ c: 'move', dx, dy }); rel[0] -= dx; rel[1] -= dy; }
}
(function mouseLoop() { flushMouse(); requestAnimationFrame(mouseLoop); })();
setMouseMode(view.mouse);

// ---------------------------------------------------------------- controls
for (const b of document.querySelectorAll('#speed button')) {
  b.onclick = () => { send({ c: 'speed', v: +b.dataset.speed }); b.blur(); };
}
$('btn-grind').onclick = e => {
  const g = S.emu.grind || {};
  send({ c: 'grind', on: !g.on, hp_floor: +$('grind-hp').value });
  e.target.blur();
};
$('grind-hp').onchange = e => {
  e.target.blur();
  if ((S.emu.grind || {}).on) send({ c: 'grind', on: true, hp_floor: +e.target.value });
};
function togglePause() { send({ c: 'pause', d: !S.emu.paused }); }
$('btn-pause').onclick = e => { togglePause(); e.target.blur(); };
$('btn-sound').onclick = e => { send({ c: 'sound', on: !S.settings.sound }); e.target.blur(); };
$('vol').oninput = e => send({ c: 'sound', vol: e.target.value / 100 });
$('vol').onchange = e => e.target.blur();
function cycleFilter() {
  const i = FILTERS.findIndex(f => f.id === view.filter);
  view.filter = FILTERS[(i + 1) % FILTERS.length].id;
  store.set('filter', view.filter);
  applyFilter();
  toast('Filter: ' + FILTERS.find(f => f.id === view.filter).name);
}
async function screenshot() {
  const blob = await screen.snapshot();
  if (!blob) return;
  const a = document.createElement('a');
  const d = new Date(), p = n => String(n).padStart(2, '0');
  a.download = `wordsworth-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.png`;
  a.href = URL.createObjectURL(blob);
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  toast('Screenshot saved to Downloads');
}
$('btn-shot').onclick = e => { screenshot(); e.target.blur(); };
function toggleTheater(force) {
  view.theater = force === undefined ? !view.theater : force;
  store.set('theater', view.theater);
  app.classList.toggle('theater', view.theater);
  $('btn-theater').classList.toggle('on', view.theater);
  requestAnimationFrame(layout);
}
$('btn-theater').onclick = e => { toggleTheater(); e.target.blur(); };
function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().then(() => toggleTheater(true)).catch(() => {});
}
document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && view.theater) toggleTheater(false); });
$('btn-full').onclick = e => { toggleFullscreen(); e.target.blur(); };
let ctlTimer = 0;
document.addEventListener('mousemove', () => {
  if (!view.theater) return;
  app.classList.add('ctl');
  clearTimeout(ctlTimer);
  ctlTimer = setTimeout(() => app.classList.remove('ctl'), 1800);
});
function toggleHelp() { $('help').hidden = !$('help').hidden; }
$('btn-help').onclick = e => { toggleHelp(); e.target.blur(); };
$('help-close').onclick = toggleHelp;

// ---------------------------------------------------------------- overlays
// Settings and lists open over the page instead of inside it. Anything that grows in the flow
// down here steals height from the stage, and the game picture resizes under the player.
const POPS = ['view-pop', 'text-pop', 'why-pop'];
function closePop(id) { const p = $(id); if (p) p.hidden = true; }
function openPop(id) { POPS.forEach(p => { if (p !== id) closePop(p); }); $(id).hidden = false; }
function togglePop(id) { $(id).hidden ? openPop(id) : closePop(id); }
document.addEventListener('pointerdown', e => {
  if (!(e.target instanceof Element) || !e.target.closest('.pophost')) POPS.forEach(closePop);
}, true);
window.addEventListener('keydown', e => { if (e.code === 'Escape') POPS.forEach(closePop); }, true);
$('btn-view').onclick = e => { togglePop('view-pop'); e.currentTarget.blur(); };
$('text-alt').onclick = e => { togglePop('text-pop'); e.currentTarget.blur(); };
$('text-why').onclick = e => { togglePop('why-pop'); e.currentTarget.blur(); };

// The one thing down here the player may resize: their own choice, remembered, never automatic.
function setFold(on) {
  view.fold = on;
  store.set('fold', on);
  app.classList.toggle('tcfold', on);
  $('tc-fold').textContent = on ? '▸' : '▾';
  $('tc-fold').title = on ? 'Show the line panel' : 'Hide the line panel';
  requestAnimationFrame(layout);
}
$('tc-fold').onclick = e => { setFold(!view.fold); e.currentTarget.blur(); };

// two-step buttons: the first click arms, the second within 3 s acts
function arm(btn, label, act) {
  if (btn.classList.contains('arm')) { btn.classList.remove('arm'); btn.textContent = btn.dataset.label; act(); return; }
  btn.dataset.label = btn.textContent;
  btn.classList.add('arm');
  btn.textContent = label;
  setTimeout(() => { if (btn.classList.contains('arm')) { btn.classList.remove('arm'); btn.textContent = btn.dataset.label; } }, 3000);
}
$('btn-reboot').onclick = e => arm(e.target, 'Sure? unsaved progress is lost', () => send({ c: 'reboot' }));
$('veil-btn').onclick = () => send({ c: 'restart_emulator' });

// ---------------------------------------------------------------- rendering: emulator
const SPEED_NAME = { 1: '×1', 2: '×2', 4: '×4', 0: 'MAX' };
function led(id, cls) { const el = $(id); el.className = 'led' + (cls ? ' ' + cls : ''); }
function veil(text, button) {
  $('veil').hidden = !text;
  if (text) { $('veil-text').textContent = text; $('veil-btn').hidden = !button; }
}
function renderEmu() {
  const e = S.emu || {};
  const stale = S.link && performance.now() - S.emuAt > 3000;
  led('led-link', S.link && !stale ? 'ok' : 'bad');
  const st = e.state;
  led('led-emu', st === 'running' ? 'ok' : st === 'booting' || st === 'starting' ? 'warn' : st ? 'bad' : '');
  led('led-audio', e.sound ? 'ok' : (S.settings.sound ? 'warn' : ''));
  $('led-audio').title = e.sound ? `Sound on · ${e.dropped || 0} blocks dropped` :
    S.settings.sound ? 'Sound is on but plays at ×1 only' : 'Sound off';
  led('led-mem', e.mem ? 'ok' : st === 'running' ? 'warn' : '');
  $('fps').textContent = e.fps != null ? e.fps.toFixed(1) + ' fps' : '--.- fps';
  $('speedtag').textContent = turbo || e.turbo ? 'TURBO' : SPEED_NAME[e.speed] || '';
  for (const b of document.querySelectorAll('#speed button')) b.classList.toggle('on', +b.dataset.speed === e.speed);
  const g = e.grind || {};
  $('btn-grind').classList.toggle('on', !!g.on);
  $('btn-grind').textContent = g.on ? 'Stop autobattle' : 'Autobattle';
  $('grind-info').textContent = g.on ? `${g.fights} fights` : (g.stopped || '');
  if (document.activeElement !== $('grind-hp') && g.cfg && g.cfg.hp_floor != null) $('grind-hp').value = g.cfg.hp_floor;
  $('btn-pause').classList.toggle('on', !!e.paused);
  $('pausetag').hidden = !e.paused;
  $('btn-sound').textContent = S.settings.sound ? '🔊' : '🔇';
  $('btn-sound').classList.toggle('on', !!S.settings.sound);
  if (document.activeElement !== $('vol') && S.settings.volume != null) $('vol').value = Math.round(S.settings.volume * 100);
  if (e.uptime != null) {
    const u = e.uptime, h = Math.floor(u / 3600), m = Math.floor(u / 60) % 60;
    $('session').textContent = (h ? h + ':' : '') + String(m).padStart(2, '0') + ':' + String(u % 60).padStart(2, '0');
  }
  if (!S.link) veil('Reconnecting to the cockpit…');
  else if (stale) veil('No signal from the cockpit server…');
  else if (st === 'booting' || st === 'starting') veil('Booting the PC-98…');
  else if (st === 'crashed') veil('The emulator stopped — restarting it…');
  else if (st === 'stopped') veil('The emulator keeps crashing. See emu/play.log.', true);
  else veil(null);
}
setInterval(() => {
  S.fpsIn = S.framesIn; S.framesIn = 0;
  $('netinfo').textContent = S.link ? `${S.fpsIn} frames/s in` : 'offline';
  renderEmu();
}, 1000);

// ---------------------------------------------------------------- rendering: telemetry
const ITEMS = [['Heal Herb', '✚'], ['Stamina Herb', '❖'], ['Gold Bar', '▰'], ['Ascension Stone', '◆']];
const EQUIP = [['weapon', 'WEAPON'], ['armor', 'ARMOR'], ['helm', 'HELM'], ['shield', 'SHIELD']];
function prettyScene(s) {
  if (!s) return '—';
  s = s.replace(/\.MES$/i, '');
  const m = /^FLOOR0*(\d+)([A-Z]?)$/i.exec(s);
  if (m) return 'Floor ' + m[1] + (m[2] || '');
  if (/^START1?$/i.test(s)) return 'Title';
  return s.charAt(0) + s.slice(1).toLowerCase();
}
function setNum(id, v) {
  const el = $(id), txt = v == null ? '--' : String(v);
  if (el.textContent !== txt) {
    if (el.textContent !== '--' && v != null) { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); }
    el.textContent = txt;
  }
}
let lastHp = null;
function renderTele(m) {
  if (m) S.tele = m;
  const t = S.tele || {};
  const play = !!t.in_play;
  app.classList.toggle('offgame', !play);
  $('scene-tag').textContent = prettyScene(t.scene);
  $('hud-scene').textContent = prettyScene(t.scene);
  $('hero-state').textContent = play ? '' : (t.scene ? 'not in the dungeon' : 'waiting for the game');
  if (!play) return;
  const st = t.stats || {};
  // One hero with two names (emu/state.py hero()): show the one he goes by now, as the player
  // typed it. Before Fabrice names him in the second half he has none.
  const typed = (t.names || {})[t.hero];
  $('hero-name').innerHTML = `<span>${esc(t.hero ? (typed || t.hero) : 'Nameless Man')}</span>`;
  const hp = st.hp, mx = st.hp_max;
  if (hp != null && mx) {
    const pct = Math.max(0, Math.min(100, hp / mx * 100));
    const cls = pct < 25 ? 'low' : pct < 50 ? 'mid' : '';
    for (const id of ['hp-fill', 'hud-hpfill']) { $(id).style.width = pct + '%'; $(id).className = cls; }
    $('hp-text').textContent = `${hp}/${mx}`;
    $('hud-hp').textContent = `${hp}/${mx}`;
    if (lastHp != null && hp !== lastHp) {
      const c = document.querySelector('.card.hero');
      c.classList.remove('hit', 'heal'); void c.offsetWidth;
      c.classList.add(hp < lastHp ? 'hit' : 'heal');
    }
    lastHp = hp;
  }
  setNum('lvl', st.level); setNum('exp', st.exp); setNum('str', st.str); setNum('def', st.def); setNum('gold', st.gold);
  $('hud-lvl').textContent = 'LV ' + (st.level ?? '--');
  $('hud-gold').textContent = 'G ' + (st.gold ?? '--');
  const it = t.items || {};
  $('items').innerHTML = ITEMS.map(([n, ic]) =>
    `<div class="item${it[n] ? '' : ' zero'}"><span class="ic">${ic}</span><span class="n">${n}</span><b>${it[n] ?? 0}</b></div>`).join('');
  const eq = t.equipment || {};
  $('equip').innerHTML = EQUIP.map(([k, l]) => `<span class="k">${l}</span><span>${esc(eq[k] || '—')}</span>`).join('');
}
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

// ---------------------------------------------------------------- rendering: line on screen
// One box, not two. It shows the line the game is drawing, and when that line can be rewritten
// it IS the editor -- same element size either way, so nothing here ever moves the picture.
// ⚠️ The window is re-read several times a second and the panel used to re-render with it,
// which yanked the editor away mid-word. So the panel HOLDS: it stops following the screen the
// moment you start editing, and says so -- a panel frozen over a running game is otherwise
// indistinguishable from a frozen cockpit.
let picked = null;              // id of the candidate whose text is open in the box
let whyFor = null;              // which line the warning on show belongs to
// ⚠️ not `held`: the keyboard section already owns that name for the keys being pressed
let holding = false;            // the panel is pinned to a line; the GAME is not
let holdId = null;              // WHICH line is pinned
let holdCand = null;            // and that line as the cockpit last offered it
function liveCand() { return (S.text && S.text.candidates || []).find(x => x.id === picked) || null; }
function candById(id) { return (S.text && S.text.candidates || []).find(x => x.id === id) || null; }
function onScreen() { return !!(S.text && S.text.lines && S.text.lines.some(l => l.trim())); }
function activeCand() { return holding ? holdCand : liveCand(); }
function dirty() {
  const c = activeCand();
  return !!(c && c.editable && !$('text-en').readOnly && $('text-en').value !== (c.en || ''));
}
// ⚠️ Holding pins a LINE. It must never freeze the panel itself: `renderText` is held off while
// this is on, so if nothing here put the editor back, a hold that began on a blank window left
// the box read-only and Save dead until the tab was reloaded -- which is exactly how it was
// found (2026-09-16). So: take the line back the moment it is on screen again.
// The cockpit only writes a line that is on screen NOW, and that guard stays; Save follows the
// LIVE candidate, never the snapshot, or it goes on lying after the game has moved.
function holdStatus() {
  if (!holding) return;
  const live = holdId ? candById(holdId) : null;
  const gone = !!holdId && !live;
  if (live) {
    holdCand = live;
    if ($('text-en').readOnly) {         // nothing is being typed: safe to take the line back
      picked = holdId;
      renderText(true);
    }
  }
  $('tc-state').textContent = gone
    ? 'held · the game moved on — press ▶ to follow the screen again'
    : 'held · the game keeps running';
  $('tc-state').classList.toggle('gone', gone);
  $('text-save').disabled = !(live && live.editable);
  refreshHold();
}
// R4: holding exists to protect work in progress, so there must be work possible. Holding a
// window the cockpit cannot rewrite -- one the engine printed in pieces, "part of a line" --
// pins a dead panel: nothing can be typed, Save is off, and the note says all is well.
function canHold() {
  const c = liveCand();
  return !!(c && c.editable);
}
// The control says what it does: it was read as the game's own pause, which it is not.
function refreshHold() {
  const b = $('tc-hold');
  b.disabled = !holding && !canHold();
  if (!b.classList.contains('arm')) b.textContent = holding ? '▶ follow' : '⏸ hold';
  b.title = holding ? 'Follow the screen again — anything typed here is discarded'
    : b.disabled ? 'Nothing to hold here: this line is not one the cockpit can rewrite'
    : 'Hold this line while the game goes on. The game is not paused.';
}
function setHeld(on) {
  if (on && !holding && !canHold()) return refreshHold();
  holding = on;
  holdCand = on ? liveCand() : null;
  holdId = holdCand ? holdCand.id : null;
  app.classList.toggle('held', on);
  $('tc-hold').classList.toggle('on', on);
  refreshHold();
  $('tc-state').textContent = on ? 'held · the game keeps running' : '';
  $('tc-state').classList.toggle('gone', false);
  if (on) holdStatus();
  else {
    // ⚠️ Leave the caret in the box while the panel follows again and the next reading of the
    // window overwrites what is being typed -- the very thing holding exists to prevent.
    if (document.activeElement === $('text-en')) $('text-en').blur();
    showWhy([]);
    renderText();
  }
}
$('tc-hold').onclick = e => {
  const b = e.currentTarget;
  b.blur();
  if (!holding) return setHeld(true);
  if (dirty()) return arm(b, 'Discard?', () => setHeld(false));
  setHeld(false);
};
// The caret landing in a box that cannot yet be edited holds nothing -- there is nothing to
// protect. renderText picks that click up the moment the line becomes editable (R10).
$('text-en').addEventListener('focus', () => {
  if (!holding && !$('text-en').readOnly) setHeld(true);
});
function renderText(force) {
  // The caret in an EDITABLE box counts as a hold whatever else happened: no reading of the
  // window may overwrite a correction while someone is typing it.
  // ⚠️ Only when it is editable. A caret resting in the read-only box (clicked while the game
  // was still typing the message out) must not block the very update that makes it typable --
  // that would keep the line unreachable for as long as the pointer stayed in it (R10).
  const box0 = $('text-en');
  if ((holding || (document.activeElement === box0 && !box0.readOnly)) && !force) return;
  const t = S.text;
  const cs = (t && t.candidates) || [];
  const has = !!(t && t.lines && t.lines.some(l => l.trim()));
  const exact = cs.filter(c => c.exact);
  // R14: several entries can render the same window line. When they all hold the same text the
  // correction belongs in every one of them, so the panel treats them as one editable line and
  // says how many it will write. When they differ, no choice can make it writable -- the
  // cockpit cannot tell which occurrence is on screen -- so the box stays read-only.
  const group = exact.filter(x => x.editable);
  const sameText = group.length > 1 && group.every(x => (x.en || '') === (group[0].en || ''));
  if (picked && !group.some(x => x.id === picked)) picked = null;
  if (group.length > 1 && !sameText) picked = null;
  if (!picked && group.length && (group.length === 1 || sameText)) picked = group[0].id;
  const c = cs.find(x => x.id === picked);
  const canEdit = !!(c && c.editable);
  const places = sameText ? group.length : 1;

  $('text-src').innerHTML = !has ? ''
    : t.settling ? '<span class="near">…</span>'
    : !t.sources ? '<span class="near">no script here to match against</span>'
    : !cs.length ? '<span class="near">not found in the sources</span>'
    : c ? esc(c.line ? `${c.file}:${c.line}` : c.id)
          + (c.tag ? ` <span class="near">· text after ${esc(c.tag)}</span>` : '')
          + (places > 1 ? ` <span class="near">· ${places} places, all corrected together</span>` : '')
          + (c.editable ? '' : ` <span class="near">${esc(c.why || 'read-only')}</span>`)
    : group.length > 1
      ? `<span class="near">${group.length} places hold different text — the cockpit cannot tell `
        + 'which is on screen, so edit them by hand</span>'
    : exact.length > 1 ? `<span class="near">${exact.length} sources render the same line</span>`
    : '<span class="near">≈ part of a line</span>';

  const alt = $('text-alt');
  alt.hidden = cs.length < 2;
  alt.textContent = `${cs.length}${t && t.more ? '+' : ''} matches`;
  if (alt.hidden) closePop('text-pop');

  // R2: one surface. The same field always, read-only until the line can be rewritten.
  const box = $('text-en');
  const wasReadOnly = box.readOnly;
  box.readOnly = !canEdit;
  box.placeholder = has ? 'this line is not one the cockpit can rewrite'
                        : 'nothing in the message window';
  $('text-save').disabled = !canEdit;
  if (picked !== whyFor) showWhy([]);           // a warning belongs to the line it was about
  box.value = canEdit ? (c.en || '') : (has ? t.lines.join('\n') : '');
  // R10: the game types its messages out, so a click often lands while the line is still being
  // identified. That click counts -- when the line becomes editable, start holding right then
  // instead of asking for a second one.
  if (canEdit && wasReadOnly && document.activeElement === box) setHeld(true);
  refreshHold();

  $('text-cands').innerHTML = cs.map(x => `
    <div class="cand${x.id === picked ? ' on' : ''}${x.editable ? '' : ' off'}" data-id="${esc(x.id)}">
      <span class="src">${x.line ? esc(x.file) + ':' + x.line : esc(x.id)}</span>
      ${x.exact ? '' : '<span class="tag near">part</span>'}
      ${x.edited ? '<span class="tag edit">edited</span>' : ''}
      ${x.editable ? '' : `<span class="tag off">${esc(x.why || 'read-only')}</span>`}
      <div class="text">${esc((x.en || x.form || '').replace(/\n/g, ' ⏎ '))}</div>
    </div>`).join('') + (t && t.more ? `<div class="dim">…and ${t.more} more</div>` : '');
  for (const el of $('text-cands').querySelectorAll('.cand')) {
    el.onclick = () => { picked = el.dataset.id; closePop('text-pop'); renderText(true); };
  }
}
// One place puts problems on screen, whether they came from typing or from a refused save.
// ⚠️ It only ever changes a button's label and an overlay: both are free of the layout.
function showWhy(problems, refused, head) {
  const why = $('text-why');
  whyFor = problems.length ? picked : null;
  why.classList.toggle('hard', !!refused);
  if (!problems.length) {
    why.hidden = true;
    closePop('why-pop');
    return;
  }
  why.hidden = false;
  why.textContent = refused ? 'not written' : `${problems.length} problem${problems.length > 1 ? 's' : ''}`;
  $('why-pop').innerHTML = `<div class="pop-h">${esc(head)}</div>`
    + problems.map(p => `<div class="prob">· ${esc(p)}</div>`).join('');
  if (refused) openPop('why-pop');               // a refusal is news; a warning can wait
}
// While the proofreader types, the cockpit's own checker says what would be refused -- the
// speaker tag renamed, a lost {0}, a line too wide -- before they reach for Save.
let checkTimer = 0;
$('text-en').addEventListener('input', () => {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(() => {
    if (picked) send({ c: 'text_check', id: picked, en: $('text-en').value });
  }, 400);
});
function onTextCheck(m) {
  if (m.id !== picked) return;
  showWhy(m.problems || [], false, 'THE GAME WOULD REFUSE THIS');
}
$('text-save').onclick = e => {
  const c = activeCand();
  if (!c || !liveCand()) return;      // the cockpit only writes the line that is on screen now
  e.target.blur();
  $('text-save').disabled = true;
  showWhy([]);
  send({ c: 'text_edit', id: c.id, en: $('text-en').value });
};
function onTextEdit(m) {
  $('text-save').disabled = false;
  if (m.ok) {
    showWhy([]);
    toast(`Saved ${m.id} to ${m.file}`);
    // R13: the work is done, so let go. A panel left held after a save is a frozen picture of
    // the past -- the message window closes behind it, Save goes dark because that line is no
    // longer on screen, and nothing can be edited again until it is released by hand.
    setHeld(false);
    return;
  }
  showWhy([m.why || 'refused', ...(m.problems || [])], true, 'NOT WRITTEN');
}

// ---------------------------------------------------------------- rendering: map
const EDGE_CLASS = { wall: 'w', door: 'dr', blocked: 'bl' };
function mapSvg(mp, withGrid) {
  const cells = mp.cells || {}, E = mp.edges || {};
  const ks = Object.keys(cells);
  if (!ks.length) return '';
  const pts = ks.map(k => k.split(',').map(Number));
  const x0 = Math.min(...pts.map(p => p[0])), x1 = Math.max(...pts.map(p => p[0]));
  const y0 = Math.min(...pts.map(p => p[1])), y1 = Math.max(...pts.map(p => p[1]));
  const U = 10, pad = 2;
  const here = mp.here || [];
  let g = '';
  for (const k of ks) {
    const [x, y] = k.split(',').map(Number), c = cells[k], L = x * U, T = y * U;
    const me = here[0] === x && here[1] === y;
    const poi = c.poi || {};
    const tip = [`(${x}, ${y})`, poi.title, (poi.info || []).join(' · '), c.key, c.note, c.event ? 'event' : '']
      .filter(Boolean).join(' — ');
    g += `<rect class="c${c.seen ? ' seen' : ''}${me ? ' here' : ''}" x="${L}" y="${T}" width="${U}" height="${U}"><title>${esc(tip)}</title></rect>`;
    if (withGrid) g += `<rect class="grid" fill="none" x="${L}" y="${T}" width="${U}" height="${U}"/>`;
  }
  for (const k of ks) {
    const [x, y] = k.split(',').map(Number), e = E[k] || {}, L = x * U, T = y * U, R = L + U, B = T + U;
    const line = (a, b, c2, d, st) => (st && st !== 'open')
      ? `<line class="${EDGE_CLASS[st] || 'u'}" x1="${a}" y1="${b}" x2="${c2}" y2="${d}"/>` : '';
    g += line(L, T, R, T, e['1']) + line(L, B, R, B, e['3']) + line(L, T, L, B, e['2']) + line(R, T, R, B, e['0']);
    const c = cells[k];
    if (c.key) g += `<text class="star" x="${L + U / 2}" y="${T + U / 2 + 2.5}">★</text>`;
    else if (c.event) g += `<circle class="ev" cx="${L + U / 2}" cy="${T + U / 2}" r="1.4"/>`;
  }
  if (here[0] != null) {
    const A = { 0: 0, 1: -90, 2: 180, 3: 90 }[mp.facing] || 0;
    g += `<g transform="translate(${here[0] * U + U / 2},${here[1] * U + U / 2}) rotate(${A})"><path class="me" d="M -3.2 -3 L 4.2 0 L -3.2 3 L -1.6 0 Z"/></g>`;
  }
  const vx = x0 * U - pad, vy = y0 * U - pad, vw = (x1 - x0 + 1) * U + 2 * pad, vh = (y1 - y0 + 1) * U + 2 * pad;
  return `<svg viewBox="${vx} ${vy} ${vw} ${vh}" preserveAspectRatio="xMidYMid meet">${g}</svg>`;
}
function renderMap() {
  const mp = S.map;
  if (!mp || !mp.cells || !Object.keys(mp.cells).length) {
    // the frame keeps its size: only what is inside it changes
    $('map').innerHTML = `<div class="mapnone"><span class="sigil">◈</span><i class="dim">${
      mp && mp.floor
        ? `No map in ${esc(prettyScene(mp.floor))} — it appears on the dungeon floors.`
        : 'The map appears once you are in the dungeon.'}</i></div>`;
    $('hud-map').innerHTML = '';
    $('map-title').textContent = '';
    return;
  }
  $('map').innerHTML = mapSvg(mp, true);
  $('hud-map').innerHTML = mapSvg(mp, false);
  const h = mp.here || [];
  $('map-title').textContent = `${prettyScene(mp.floor || mp.scene)} · ${h[0] ?? '?'}, ${h[1] ?? '?'}`;
}

// ---------------------------------------------------------------- snapshots
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + ' min ago';
  if (s < 86400) return Math.floor(s / 3600) + ' h ago';
  return new Date(ts * 1000).toLocaleDateString();
}
function slotTile(s, auto) {
  const label = auto ? 'A' + (+s.name.slice(4) + 1) : String(s.name.slice(4));
  const meta = s.used
    ? `<b>${prettyScene(s.scene)}</b>${s.level != null ? ' Lv' + s.level : ''}<br>${ago(s.time || 0)}`
    : 'empty';
  const thumb = s.thumb ? `style="background-image:url('${s.thumb}')"` : '';
  const tip = s.used && s.hp != null ? ` title="HP ${s.hp}/${s.hp_max}"` : '';
  return `<div class="slot" data-name="${s.name}"${tip}>
    <div class="thumb" ${thumb}><span class="no">${label}</span></div>
    <div class="meta">${meta}</div>
    <div class="btns">${auto ? '' : '<button data-act="save">Save</button>'}<button data-act="load"${s.used ? '' : ' disabled'}>Load</button></div>
  </div>`;
}
function renderSlots() {
  const slots = S.slots || [];
  $('slots').innerHTML = slots.filter(s => s.name.startsWith('slot')).map(s => slotTile(s, false)).join('');
  $('autos').innerHTML = slots.filter(s => s.name.startsWith('auto')).map(s => slotTile(s, true)).join('');
  for (const b of document.querySelectorAll('.slot button')) {
    const name = b.closest('.slot').dataset.name;
    const used = (S.slots.find(s => s.name === name) || {}).used;
    b.onclick = () => {
      b.blur();
      if (b.dataset.act === 'save') {
        if (used) arm(b, 'Overwrite?', () => saveSlot(name)); else saveSlot(name);
      } else {
        arm(b, 'Load?', () => loadSlot(name));
      }
    };
  }
}
function saveSlot(name) { send({ c: 'slot_save', name }); toast('Snapshot → ' + name.replace('slot', 'slot ')); }
function loadSlot(name, fromKey) {
  const s = (S.slots || []).find(x => x.name === name);
  if (!s || !s.used) { toast(name.replace('slot', 'Slot ') + ' is empty', 'error'); return; }
  if (fromKey && !loadSlot.armed) {
    loadSlot.armed = true;
    toast('Press F4 again to load slot 1 (unsaved progress is lost)');
    setTimeout(() => { loadSlot.armed = false; }, 3000);
    return;
  }
  loadSlot.armed = false;
  send({ c: 'slot_load', name });
}
setInterval(renderSlots, 30000);

// ---------------------------------------------------------------- toasts
function toast(msg, level) {
  const d = document.createElement('div');
  d.className = 'toast' + (level === 'error' ? ' error' : '');
  d.textContent = msg;
  $('toasts').appendChild(d);
  setTimeout(() => d.remove(), level === 'error' ? 10000 : 4000);
}

// ---------------------------------------------------------------- start
applyFilter();
toggleTheater(view.theater);
setFold(view.fold);
setHeld(false);
layout();
connect();
window.WW = { S, view, screen, send };     // for debugging from the console
