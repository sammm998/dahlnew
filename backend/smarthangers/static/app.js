/* SmartHangers webbapp – vanilla JS, hash-routing. */
'use strict';

// ---------------------------------------------------------------------------
// Hjälpfunktioner
// ---------------------------------------------------------------------------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
const raw = (s) => new Raw(s);
const val = (v) => v instanceof Raw ? v.s : Array.isArray(v) ? v.map(val).join('') : (v === null || v === undefined || v === false) ? '' : esc(v);
const html = (str, ...vals) => raw(str.reduce((a, s, i) => a + s + (i < vals.length ? val(vals[i]) : ''), ''));
const nf = new Intl.NumberFormat('sv-SE', { maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat('sv-SE', { maximumFractionDigits: 0 });
const fmt = (n, d = 0) => n === null || n === undefined || Number.isNaN(n) ? '—' : (d ? nf : nf0).format(n);
const kr = (n) => `${fmt(n)} kr`;
const dt = (s) => s ? new Date(s).toLocaleString('sv-SE', { dateStyle: 'short', timeStyle: 'short' }) : '—';
const chip = (st) => html`<span class="chip st-${st}">${st}</span>`;
const sev = (s) => html`<span class="chip sev-${s}">${s}</span>`;
const srcTag = (s) => { const k = (s || '').split(' ')[0]; return html`<span class="src ${k}">${s}</span>`; };

const ROLE_KEY = 'sh.role', USER_KEY = 'sh.user';
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};
const state = { role: store.get(ROLE_KEY, 'projektadmin'), user: store.get(USER_KEY, 'Användare'), meta: null };
const can = (perm) => (state.meta?.permissions?.[perm] || []).includes(state.role);

function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), err ? 6000 : 2600);
}

async function api(path, opts = {}) {
  const headers = { 'X-User': encodeURIComponent(state.user), 'X-Role': state.role, ...(opts.headers || {}) };
  let body = opts.body;
  if (body && !(body instanceof FormData)) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
  const r = await fetch(path, { method: opts.method || (body ? 'POST' : 'GET'), headers, body });
  if (!r.ok) {
    let d = r.statusText;
    try { const j = await r.json(); d = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch { /* ignore */ }
    throw new Error(d);
  }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r.text();
}

async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; } catch (e) { toast(e.message, true); throw e; }
}

function ask(title, { fields = [{ name: 'comment', label: 'Kommentar', type: 'text' }], ok = 'OK' } = {}) {
  return new Promise((resolve) => {
    const d = document.createElement('dialog');
    d.innerHTML = html`<form method="dialog" class="grid">
      <h2>${title}</h2>
      ${fields.map((f) => html`<label class="f">${f.label}
        ${f.type === 'select' ? html`<select name="${f.name}">${f.options.map((o) => html`<option value="${o[0]}" ${o[0] === f.value ? raw('selected') : ''}>${o[1]}</option>`)}</select>`
          : f.type === 'textarea' ? html`<textarea name="${f.name}" rows="4">${f.value || ''}</textarea>`
          : html`<input name="${f.name}" type="${f.type || 'text'}" value="${f.value ?? ''}" ${f.step ? raw(`step="${f.step}"`) : ''}>`}</label>`)}
      <div class="row"><span class="spacer"></span><button value="cancel" type="button" data-x>Avbryt</button><button class="primary" value="ok">${ok}</button></div>
    </form>`.s;
    document.body.appendChild(d);
    d.querySelector('[data-x]').onclick = () => { d.close(); };
    d.addEventListener('close', () => {
      const res = d.returnValue === 'ok' ? Object.fromEntries(new FormData(d.querySelector('form'))) : null;
      d.remove();
      resolve(res);
    });
    d.showModal();
    d.querySelector('input,select,textarea')?.focus();
  });
}

// Färg per system
function sysColor(name) {
  let h = 0;
  for (const c of name || '') h = (h * 31 + c.charCodeAt(0)) % 360;
  const n = (name || '').toLowerCase();
  if (n.includes('kv') || n.includes('kall')) h = 205;
  else if (n.includes('vvc')) h = 330;
  else if (n.includes('vv') || n.includes('varm')) h = 0;
  else if (n.includes('värme') || n.includes('vs')) h = 18 + (n.includes('retur') ? 25 : 0);
  else if (n.includes('spill') || n.startsWith('s ')) h = 30;
  return `hsl(${h} 65% 45%)`;
}
const STATUS_COLOR = {
  'föreslagen': '#7a8296', 'granskad': '#3d73c4', 'godkänd': '#2b8a5c', 'producerad': '#7d4cc0',
  'packad': '#c98316', 'levererad': '#d0632a', 'monterad': '#3f9b2f', 'makulerad': '#aa4444',
};

// ---------------------------------------------------------------------------
// Ritningar
// ---------------------------------------------------------------------------
/** Måttsatt sidovy av en SmartHanger (vy längs stråket). */
function hangerSVG(d, { width = 640, big = false } = {}) {
  const tj = d.takjarn;
  const L = tj.length, H = tj.height;
  const zTop = tj.z_bottom_rel + H;
  const zMin = Math.min(...d.pipes.map((p) => p.z_center_rel - p.od / 2 - p.insulation));
  const span = Math.max(zTop - zMin, 200);
  const padL = 70, padR = 150, padT = 70, padB = 40;
  const k = Math.min((width - padL - padR) / L, (big ? 520 : 380) / span);
  const W = padL + L * k + padR, Hh = padT + span * k + padB;
  const X = (t) => padL + t * k, Y = (z) => padT + (zTop - z) * k;
  const fs = big ? 14 : 12;
  const parts = [];
  // tak
  parts.push(`<line x1="${X(-30)}" y1="${Y(zTop)}" x2="${X(L + 30)}" y2="${Y(zTop)}" stroke="currentColor" stroke-width="1" stroke-dasharray="6 4" opacity=".5"/>`);
  parts.push(`<text x="${X(L + 36)}" y="${Y(zTop) + 4}" font-size="${fs - 2}" fill="currentColor" opacity=".7">tak/underlag</text>`);
  // takjärn
  parts.push(`<rect x="${X(0)}" y="${Y(zTop)}" width="${L * k}" height="${Math.max(H * k, 6)}" fill="#8a9590" stroke="currentColor" stroke-width="1"/>`);
  // infästningar
  for (const fp of tj.fastening?.positions || []) {
    parts.push(`<path d="M${X(fp)} ${Y(zTop)} l-5 -10 h10 z" fill="#555"/>`);
  }
  // längdmått takjärn
  const yd = padT - 40;
  parts.push(`<line x1="${X(0)}" y1="${yd}" x2="${X(L)}" y2="${yd}" stroke="currentColor"/>`);
  parts.push(`<line x1="${X(0)}" y1="${yd - 6}" x2="${X(0)}" y2="${Y(zTop)}" stroke="currentColor" opacity=".4"/>`);
  parts.push(`<line x1="${X(L)}" y1="${yd - 6}" x2="${X(L)}" y2="${Y(zTop)}" stroke="currentColor" opacity=".4"/>`);
  parts.push(`<text x="${X(L / 2)}" y="${yd - 6}" text-anchor="middle" font-size="${fs + 1}" font-weight="700" fill="currentColor">${L} mm</text>`);
  // hålbild (kedjemått från vänster ände)
  const yh = padT - 16;
  let prev = 0;
  const holes = [...d.pipes].sort((a, b) => a.t_on_takjarn - b.t_on_takjarn);
  for (const p of holes) {
    const t = p.t_on_takjarn;
    parts.push(`<line x1="${X(t)}" y1="${yh - 4}" x2="${X(t)}" y2="${Y(zTop)}" stroke="currentColor" opacity=".35"/>`);
    parts.push(`<text x="${X((prev + t) / 2)}" y="${yh - 2}" text-anchor="middle" font-size="${fs - 2}" fill="currentColor">${t - prev}</text>`);
    prev = t;
  }
  parts.push(`<line x1="${X(0)}" y1="${yh}" x2="${X(prev)}" y2="${yh}" stroke="currentColor" opacity=".6"/>`);
  // pendlar, svep och rör
  for (const p of d.pipes) {
    const t = p.t_on_takjarn;
    const r = p.od / 2, ri = r + p.insulation;
    const attach = p.z_center_rel + ri + p.clamp.rod_offset;
    const col = sysColor(p.system);
    parts.push(`<line x1="${X(t)}" y1="${Y(zTop)}" x2="${X(t)}" y2="${Y(attach)}" stroke="currentColor" stroke-width="${big ? 3 : 2}"/>`);
    // muttrar
    parts.push(`<rect x="${X(t) - 6}" y="${Y(tj.z_bottom_rel) - 1}" width="12" height="5" fill="currentColor"/>`);
    parts.push(`<rect x="${X(t) - 6}" y="${Y(zTop) - 5}" width="12" height="5" fill="currentColor"/>`);
    if (p.insulation) parts.push(`<circle cx="${X(t)}" cy="${Y(p.z_center_rel)}" r="${Math.max(ri * k, 4)}" fill="${col}" fill-opacity=".12" stroke="${col}" stroke-dasharray="3 2"/>`);
    parts.push(`<circle cx="${X(t)}" cy="${Y(p.z_center_rel)}" r="${Math.max(r * k, 3)}" fill="${col}" fill-opacity=".85" stroke="currentColor" stroke-width=".8"/>`);
    // svep
    parts.push(`<circle cx="${X(t)}" cy="${Y(p.z_center_rel)}" r="${Math.max(ri * k, 4) + 2.5}" fill="none" stroke="currentColor" stroke-width="2"/>`);
    // pendellängd
    const ym = (Y(zTop) + Y(attach)) / 2;
    parts.push(`<text x="${X(t) + 8}" y="${ym}" font-size="${fs + 1}" font-weight="700" fill="currentColor">${p.rod.dim}×${p.rod.length}</text>`);
    parts.push(`<text x="${X(t) + 8}" y="${ym + fs + 2}" font-size="${fs - 2}" fill="currentColor" opacity=".75">${esc(p.clamp.article)}</text>`);
  }
  // höjdmått till höger
  const xr = X(L) + 18;
  const levels = [[tj.z_bottom_rel, `UK takjärn +${fmt(tj.z_bottom_rel)}`]];
  for (const p of d.pipes) levels.push([p.z_center_rel, `CL ${p.system.split(' ')[0]} Ø${fmt(p.od, 1)} +${fmt(p.z_center_rel)}`]);
  const used = [];
  for (const [z, label] of levels) {
    let y = Y(z);
    while (used.some((u) => Math.abs(u - y) < fs + 2)) y += fs + 2;
    used.push(y);
    parts.push(`<line x1="${xr - 10}" y1="${Y(z)}" x2="${xr - 2}" y2="${y - 4}" stroke="currentColor" opacity=".4"/>`);
    parts.push(`<text x="${xr}" y="${y}" font-size="${fs - 2}" fill="currentColor">${esc(label)}</text>`);
  }
  return raw(`<svg class="drawing" viewBox="0 0 ${W} ${Hh}" style="max-width:${W}px" role="img" aria-label="Måttsatt vy">${parts.join('')}</svg>`);
}

/** Planvy (SVG) för ett plan. */
function planSVG(model, storey, { selected = null, focus = null, mini = false } = {}) {
  const pipes = model.pipes.filter((p) => p.storey === storey);
  const hangers = model.hangers.filter((h) => h.storey === storey);
  const comps = model.components.filter((c) => c.storey === storey && c.kind !== 'ceiling');
  const xs = [], ys = [];
  for (const p of pipes) { xs.push(p.start[0], p.end[0]); ys.push(p.start[1], p.end[1]); }
  for (const h of hangers) { xs.push(h.x); ys.push(h.y); }
  if (!xs.length) { xs.push(0, 10000); ys.push(0, 10000); }
  let x0 = Math.min(...xs) - 1500, x1 = Math.max(...xs) + 1500, y0 = Math.min(...ys) - 1500, y1 = Math.max(...ys) + 1500;
  if (focus) { x0 = focus.x - 3000; x1 = focus.x + 3000; y0 = focus.y - 2200; y1 = focus.y + 2200; }
  const vb = [x0, -y1, x1 - x0, y1 - y0];
  const u = Math.max(vb[2], vb[3]) / 900; // ~1px i mm
  const parts = [];
  for (const g of model.revision.grid || []) {
    if (g.orientation === 'x') {
      parts.push(`<line x1="${g.value}" y1="${-y1}" x2="${g.value}" y2="${-y0}" stroke="currentColor" stroke-dasharray="${8 * u} ${6 * u}" stroke-width="${u}" opacity=".35"/>`);
      parts.push(`<circle cx="${g.value}" cy="${-y1 + 22 * u}" r="${12 * u}" fill="none" stroke="currentColor" stroke-width="${u}" opacity=".6"/><text x="${g.value}" y="${-y1 + 27 * u}" text-anchor="middle" font-size="${13 * u}" fill="currentColor">${esc(g.tag)}</text>`);
    } else {
      parts.push(`<line x1="${x0}" y1="${-g.value}" x2="${x1}" y2="${-g.value}" stroke="currentColor" stroke-dasharray="${8 * u} ${6 * u}" stroke-width="${u}" opacity=".35"/>`);
      parts.push(`<circle cx="${x0 + 22 * u}" cy="${-g.value}" r="${12 * u}" fill="none" stroke="currentColor" stroke-width="${u}" opacity=".6"/><text x="${x0 + 22 * u}" y="${-g.value + 5 * u}" text-anchor="middle" font-size="${13 * u}" fill="currentColor">${esc(g.tag)}</text>`);
    }
  }
  for (const c of comps) {
    const [a, b] = [c.bbox_min, c.bbox_max];
    const fill = c.kind === 'obstacle' ? '#9aa3a0' : c.kind === 'pointload' ? '#c0392b' : '#d68910';
    parts.push(`<rect x="${a[0]}" y="${-b[1]}" width="${b[0] - a[0]}" height="${b[1] - a[1]}" fill="${fill}" fill-opacity="${c.kind === 'obstacle' ? 0.35 : 0.8}"><title>${esc(c.ifc_class + ' ' + c.name)}</title></rect>`);
  }
  for (const p of pipes) {
    const w = Math.max(p.od, 3 * u);
    const vertical = p.classification === 'vertikal';
    if (vertical) {
      parts.push(`<circle cx="${p.start[0]}" cy="${-p.start[1]}" r="${Math.max(p.od / 2, 5 * u)}" fill="${sysColor(p.system)}" data-pipe="${p.id}" style="cursor:pointer"><title>${esc(p.system)} (vertikal)</title></circle>`);
    } else {
      parts.push(`<line x1="${p.start[0]}" y1="${-p.start[1]}" x2="${p.end[0]}" y2="${-p.end[1]}" stroke="${sysColor(p.system)}" stroke-width="${w}" stroke-linecap="butt" data-pipe="${p.id}" style="cursor:pointer" opacity=".9"><title>${esc(p.system)} Ø${p.od} ${esc(p.material)}</title></line>`);
    }
  }
  for (const h of hangers) {
    const ax = h.axis_dir || [0, 1];
    const half = (h.takjarn_length || 200) / 2;
    const col = STATUS_COLOR[h.status] || '#666';
    const sel = selected === h.code || focus?.code === h.code;
    const sw = Math.max(sel ? 9 * u : 6 * u, 25);
    parts.push(`<g data-hanger="${esc(h.code)}" style="cursor:pointer"><title>${esc(h.code)} · ${esc(h.status)}</title>`);
    parts.push(`<circle cx="${h.x}" cy="${-h.y}" r="${Math.max(half, 16 * u)}" fill="transparent" pointer-events="all"/>`);
    if (sel) parts.push(`<circle cx="${h.x}" cy="${-h.y}" r="${half + 120}" fill="none" stroke="${col}" stroke-width="${3 * u}" opacity=".6"/>`);
    parts.push(`<line x1="${h.x - ax[0] * half}" y1="${-(h.y - ax[1] * half)}" x2="${h.x + ax[0] * half}" y2="${-(h.y + ax[1] * half)}" stroke="${col}" stroke-width="${sw}" stroke-linecap="round"/>`);
    if (h.critical || h.issue) parts.push(`<circle cx="${h.x}" cy="${-h.y}" r="${half + 50}" fill="none" stroke="#c0392b" stroke-width="${2.5 * u}" stroke-dasharray="${5 * u} ${3 * u}"/>`);
    if (!mini || sel) parts.push(`<text x="${h.x + ax[0] * (half + 60)}" y="${-(h.y + ax[1] * (half + 60))}" font-size="${(sel ? 14 : 11) * u}" font-weight="${sel ? 700 : 500}" fill="currentColor">${esc(h.code)}</text>`);
    parts.push('</g>');
  }
  return raw(`<svg class="plan" viewBox="${vb.join(' ')}" data-vb="${vb.join(' ')}" preserveAspectRatio="xMidYMid meet" ${mini ? 'style="height:320px;cursor:default"' : ''}>${parts.join('')}</svg>`);
}

function enablePanZoom(svg) {
  let vb = svg.getAttribute('viewBox').split(' ').map(Number);
  const set = () => svg.setAttribute('viewBox', vb.join(' '));
  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = svg.getBoundingClientRect();
    const f = e.deltaY > 0 ? 1.15 : 1 / 1.15;
    const mx = vb[0] + ((e.clientX - r.left) / r.width) * vb[2];
    const my = vb[1] + ((e.clientY - r.top) / r.height) * vb[3];
    vb = [mx - (mx - vb[0]) * f, my - (my - vb[1]) * f, vb[2] * f, vb[3] * f];
    set();
  }, { passive: false });
  let drag = null;
  svg.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, y: e.clientY, vb: [...vb], moved: false }; });
  svg.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const r = svg.getBoundingClientRect();
    const scale = Math.max(vb[2] / r.width, vb[3] / r.height);
    const dx = (e.clientX - drag.x) * scale, dy = (e.clientY - drag.y) * scale;
    if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 4) drag.moved = true;
    vb = [drag.vb[0] - dx, drag.vb[1] - dy, vb[2], vb[3]];
    set();
  });
  svg.addEventListener('pointerup', () => { svg._dragged = drag?.moved; drag = null; });
  svg.addEventListener('pointerleave', () => { drag = null; });
  return {
    toWorld(e) {
      const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
      const p = pt.matrixTransform(svg.getScreenCTM().inverse());
      return { x: p.x, y: -p.y };
    },
  };
}

// ---------------------------------------------------------------------------
// Ramverk: header, routing
// ---------------------------------------------------------------------------
const main = () => $('#main');
let current = { pid: null, project: null };

function renderHeader(pid) {
  const tabs = pid ? [
    ['', 'Översikt'], ['plan', 'Modell & plan'], ['review', 'Granskning'], ['production', 'Produktion'],
    ['pack', 'Packning'], ['montage', 'Montage'], ['bom', 'Material'], ['calc', 'Kalkyl'], ['log', 'Logg'],
  ] : [];
  const hash = location.hash;
  $('#header').innerHTML = html`<header class="top">
    <a class="brand" href="#/"><span class="logo">SH</span>SmartHangers</a>
    <nav>
      ${pid ? html`<a href="#/" class="muted">Projekt</a><span class="muted">›</span>` : html`<a href="#/" class="${hash === '#/' || hash === '' ? 'active' : ''}">Projekt</a>`}
      ${tabs.map(([k, l]) => { const href = `#/p/${pid}${k ? '/' + k : ''}`; const on = k ? hash.startsWith(href) : hash === href; return html`<a href="${href}" class="${on ? 'active' : ''}">${l}</a>`; })}
      <a href="#/rules" class="${hash.startsWith('#/rules') ? 'active' : ''}">Regler</a>
    </nav>
    <div class="who">
      <input id="who-user" value="${state.user}" size="10" aria-label="Användare">
      <select id="who-role" aria-label="Roll">${Object.entries(state.meta.roles).map(([k, v]) => html`<option value="${k}" ${k === state.role ? raw('selected') : ''}>${v}</option>`)}</select>
    </div>
  </header>`.s;
  $('#who-role').onchange = (e) => { state.role = e.target.value; store.set(ROLE_KEY, state.role); route(); };
  $('#who-user').onchange = (e) => { state.user = e.target.value || 'Användare'; store.set(USER_KEY, state.user); };
}

const routes = [
  [/^#?\/?$/, viewProjects],
  [/^#\/rules(?:\/(\d+))?$/, viewRules],
  [/^#\/q\/(\d+)\/([^/]+)$/, viewQR],
  [/^#\/p\/(\d+)$/, viewOverview],
  [/^#\/p\/(\d+)\/plan$/, viewPlan],
  [/^#\/p\/(\d+)\/review$/, viewReview],
  [/^#\/p\/(\d+)\/production(?:\/([^/]+))?$/, viewProduction],
  [/^#\/p\/(\d+)\/pack$/, viewPack],
  [/^#\/p\/(\d+)\/montage(?:\/([^/]+))?$/, viewMontage],
  [/^#\/p\/(\d+)\/bom$/, viewBom],
  [/^#\/p\/(\d+)\/calc$/, viewCalc],
  [/^#\/p\/(\d+)\/log$/, viewLog],
  [/^#\/p\/(\d+)\/h\/([^/]+)$/, viewHanger],
];

async function route() {
  const hash = location.hash || '#/';
  const pm = hash.match(/^#\/p\/(\d+)/);
  current.pid = pm ? Number(pm[1]) : null;
  renderHeader(current.pid);
  for (const [re, fn] of routes) {
    const mm = hash.match(re);
    if (mm) {
      main().innerHTML = '<div class="empty">Laddar…</div>';
      try { await fn(...mm.slice(1).map((x) => x && decodeURIComponent(x))); } catch (e) { main().innerHTML = html`<div class="banner crit">${e.message}</div>`.s; }
      return;
    }
  }
  main().innerHTML = '<div class="empty">Sidan finns inte.</div>';
}

async function loadProject(pid) {
  current.project = await api(`/api/projects/${pid}`);
  return current.project;
}

function rulesBanner(p) {
  if (!p.ruleset) return '';
  if (p.ruleset.status !== 'verifierad') {
    return html`<div class="banner warn"><b>Regelverket ${p.ruleset.label} är inte verifierat.</b> Värdena är exempel/platshållare och måste verifieras av tekniskt sakkunnig innan konstruktionen kan godkännas. <a href="#/rules/${p.ruleset.id}">Visa regler</a></div>`;
  }
  return '';
}

// ---------------------------------------------------------------------------
// Projektlista
// ---------------------------------------------------------------------------
async function viewProjects() {
  const ps = await api('/api/projects');
  main().innerHTML = html`
    <div class="row"><h1>Projekt</h1><span class="spacer"></span>${can('projekt.hantera') ? html`<button class="primary" id="new">Nytt projekt</button>` : ''}</div>
    ${ps.length ? html`<div class="card"><table><thead><tr><th>Projekt</th><th>Nr</th><th>Kund</th><th>Revision</th><th>Regler</th><th>Konstruktion</th><th>Produktion</th></tr></thead><tbody>
      ${ps.map((p) => html`<tr><td><a href="#/p/${p.id}">${p.name}</a></td><td>${p.number}</td><td>${p.customer}</td><td>${p.current_revision || '—'}</td>
        <td>${p.ruleset?.label} ${p.ruleset?.status === 'verifierad' ? '' : html`<span class="chip sev-varning">ej verifierad</span>`}</td>
        <td>${p.gates.design.at ? html`<span class="chip st-godkänd">godkänd</span>` : '—'}</td><td>${p.gates.production.at ? html`<span class="chip st-producerad">frisläppt</span>` : '—'}</td></tr>`)}
    </tbody></table></div>` : html`<div class="card empty">Inga projekt ännu. ${can('projekt.hantera') ? 'Skapa ett projekt och importera en IFC-modell.' : 'Byt roll till Projektadministratör för att skapa ett projekt.'}</div>`}
    <p class="muted small">Tips: en testmodell kan skapas med <code>python -m smarthangers.sample_ifc demo.ifc</code>.</p>`.s;
  $('#new')?.addEventListener('click', async () => {
    const r = await ask('Nytt projekt', { fields: [{ name: 'name', label: 'Projektnamn' }, { name: 'number', label: 'Projektnummer' }, { name: 'customer', label: 'Kund/installatör' }], ok: 'Skapa' });
    if (!r?.name) return;
    const p = await act(() => api('/api/projects', { body: r }), 'Projekt skapat');
    location.hash = `#/p/${p.id}`;
  });
}

// ---------------------------------------------------------------------------
// Översikt
// ---------------------------------------------------------------------------
async function viewOverview(pid) {
  const [p, rulesets] = await Promise.all([loadProject(pid), api('/api/rulesets')]);
  const cur = p.revisions.find((r) => r.is_current);
  const st = p.settings || {};
  const storeys = cur ? Object.keys(cur.storeys) : [];
  const gate = (key, title, perm, btn) => {
    const g = p.gates[key];
    return html`<div class="gate ${g.at ? 'done' : ''}"><b>${title}</b>
      ${g.at ? html`<span class="small">✔ ${g.by} · ${dt(g.at)} ${g.revision ? '· ' + g.revision : ''}</span>` : html`<span class="small muted">Ej beslutad</span>`}
      ${!g.at && can(perm) ? html`<button data-gate="${key}" ${key === 'production' && !p.gates.design.at ? raw('disabled title="Kräver konstruktionsgodkännande"') : ''}>${btn}</button>` : ''}</div>`;
  };
  main().innerHTML = html`
    <div class="row"><h1>${p.name}</h1><span class="muted">${p.number} ${p.customer ? '· ' + p.customer : ''}</span></div>
    ${raw(rulesBanner(p))}
    <div class="grid g4" style="margin-bottom:16px">
      <div class="card kpi"><span class="l">Modellrevision</span><span class="v">${p.current_revision || '—'}</span></div>
      <div class="card kpi"><span class="l">Upphängningar</span><span class="v">${fmt(p.hanger_count)}</span></div>
      <div class="card kpi"><span class="l">Rörmeter</span><span class="v">${fmt(Object.values(p.pipe_meters || {}).reduce((a, b) => a + b, 0), 1)}</span></div>
      <div class="card kpi"><span class="l">Öppna flaggor</span><span class="v">${p.open_flags.kritisk ? html`<span class="neg">${p.open_flags.kritisk} kritiska</span>` : ''} ${p.open_flags.varning} / ${p.open_flags.info}</span><span class="small muted">varning / info</span></div>
    </div>
    <div class="grid g2">
      <div class="card">
        <h2>Beslutspunkter</h2>
        <div class="grid g3">
          ${gate('quote', 'Offertgodkännande', 'offert.godkann', 'Godkänn offert')}
          ${gate('design', 'Konstruktionsgodkännande', 'konstruktion.godkann', 'Godkänn konstruktion')}
          ${gate('production', 'Produktionsfrisläppning', 'produktion.frislapp', 'Frisläpp produktion')}
        </div>
        ${p.design_blockers.length && !p.gates.design.at ? html`<h3>Kvar före konstruktionsgodkännande</h3><ul class="small">${p.design_blockers.map((b) => html`<li>${b}</li>`)}</ul>` : ''}
        <h3>Status</h3>
        <div class="row">${state.meta.statuses.map((s) => html`<span class="chip st-${s}">${s}: ${p.status_counts[s] || 0}</span>`)}</div>
        ${p.critical_count ? html`<p class="neg small">${p.critical_count} upphängningar har kritiska ändringar.</p>` : ''}
      </div>
      <div class="card">
        <h2>Modell</h2>
        ${can('modell.importera') ? html`<form id="upl" class="row"><input type="file" name="file" accept=".ifc" required><button class="primary">Importera revision</button></form>
          <p class="small muted">IFC (IFC2x3/IFC4). Revit-modeller exporteras till IFC. Varje uppladdning blir en ny revision.</p>` : ''}
        <div class="row">
          ${can('hangers.generera') && cur ? html`<button class="primary" id="gen">${cur.generated_at ? 'Generera om upphängningar' : 'Generera upphängningar'}</button>` : ''}
          ${cur ? html`<a class="btn" href="#/p/${pid}/plan">Öppna planvy</a><a class="btn" href="/api/projects/${pid}/export.ifc">Exportera IFC med SmartHangers</a>` : ''}
        </div>
        ${cur?.warnings?.length ? html`<div class="banner warn small">${cur.warnings.join(' ')}</div>` : ''}
        <h3>Revisioner</h3>
        <table><thead><tr><th>Rev</th><th>Fil</th><th>Uppladdad</th><th>Ändringar</th></tr></thead><tbody>
        ${[...p.revisions].reverse().map((r) => html`<tr class="${r.is_current ? 'sel' : ''}"><td><b>${r.label}</b>${r.is_current ? ' (aktuell)' : ''}</td><td class="small">${r.filename}<br><span class="muted">${r.schema} · ${r.pipe_count} rör</span></td><td class="small">${r.uploaded_by}<br>${dt(r.uploaded_at)}</td>
          <td class="small">${r.diff?.previous ? html`mot ${r.diff.previous}: +${r.diff.added.length} nya, −${r.diff.removed.length} borttagna, ${r.diff.moved.length} flyttade, ${r.diff.changed.length} ändrade<br><b>${r.diff.affected_hangers.length}</b> berörda hangers ${r.diff.affected_hangers.length ? html`<span class="muted">(${r.diff.affected_hangers.slice(0, 8).join(', ')}${r.diff.affected_hangers.length > 8 ? '…' : ''})</span>` : ''}` : '—'}</td></tr>`)}
        </tbody></table>
      </div>
      <div class="card">
        <h2>Projektinställningar</h2>
        <form id="settings" class="grid g3">
          <label class="f">Underlag (infästning)<select name="substrate">${['Betong', 'Stål', 'Trä'].map((x) => html`<option ${st.substrate === x ? raw('selected') : ''}>${x}</option>`)}</select></label>
          <label class="f">Korrosivitetsklass<select name="corrosivity">${['C1', 'C2', 'C3', 'C4', 'C5'].map((x) => html`<option ${st.corrosivity === x ? raw('selected') : ''}>${x}</option>`)}</select></label>
          <label class="f">Brandkrav<select name="fire_requirement"><option value="false">Nej</option><option value="true" ${st.fire_requirement ? raw('selected') : ''}>Ja</option></select></label>
          ${storeys.map((sn) => html`<label class="f">UK takjärn ${sn} (mm över golv, tomt = från modell)<input name="tj:${sn}" type="number" value="${st.storeys?.[sn]?.takjarn_z ?? ''}"></label>`)}
          <div class="row" style="align-self:end">${can('projekt.hantera') || can('hojd.redigera') ? html`<button class="primary">Spara</button>` : ''}</div>
        </form>
        <p class="small muted">Höjdkällor: <span class="src">modell</span> läses ur IFC, <span class="src manuell">manuell</span> är inmatad, <span class="src beräknad">beräknad</span> härleds av systemet. Ändringar kräver omgenerering.</p>
      </div>
      <div class="card">
        <h2>Regelverk</h2>
        <p>Projektet använder <b>${p.ruleset.label}</b> <span class="chip">${p.ruleset.status}</span> ${p.ruleset.verified_by ? html`<span class="small muted">verifierad av ${p.ruleset.verified_by} ${dt(p.ruleset.verified_at)}</span>` : ''}</p>
        ${can('projekt.hantera') ? html`<form id="rsel" class="row"><select name="ruleset_id">${rulesets.filter((r) => r.status !== 'utkast').map((r) => html`<option value="${r.id}" ${r.id === p.ruleset.id ? raw('selected') : ''}>${r.label} (${r.status})</option>`)}</select><button>Byt regelversion</button></form>` : ''}
        <p class="small"><a href="#/rules/${p.ruleset.id}">Visa och redigera regelbiblioteket</a></p>
        <h3>Dokument</h3>
        <div class="row small">
          <a class="btn" href="/api/projects/${pid}/bom.csv">BOM projekt (CSV)</a>
          <a class="btn" href="/api/projects/${pid}/bom.csv?level=hanger">BOM per hanger (CSV)</a>
          <a class="btn" href="/api/projects/${pid}/labels.pdf" target="_blank">Alla etiketter (PDF)</a>
          <a class="btn" href="/api/projects/${pid}/labels.zpl" target="_blank">Etiketter (ZPL)</a>
        </div>
      </div>
    </div>`.s;

  $('#upl')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const r = await act(() => api(`/api/projects/${pid}/revisions`, { body: fd }), 'Modell importerad');
    if (r.diff?.affected_hangers?.length) toast(`${r.diff.affected_hangers.length} hangers berörs av ändringar – generera om och granska.`);
    viewOverview(pid);
  });
  $('#gen')?.addEventListener('click', async () => {
    const r = await act(() => api(`/api/projects/${pid}/generate`, { method: 'POST' }));
    toast(`${r.runs} stråk · ${r.created} nya · ${r.updated + r.revised} ändrade · ${r.unchanged} oförändrade · ${r.cancelled} makulerade${r.critical ? ` · ${r.critical} KRITISKA` : ''}`);
    viewOverview(pid);
  });
  $('#settings')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const patch = { substrate: f.substrate, corrosivity: f.corrosivity, fire_requirement: f.fire_requirement === 'true', storeys: {} };
    for (const sn of storeys) { const v = f[`tj:${sn}`]; patch.storeys[sn] = { takjarn_z: v === '' ? null : Number(v) }; }
    if (!can('projekt.hantera')) { delete patch.substrate; delete patch.corrosivity; delete patch.fire_requirement; }
    await act(() => api(`/api/projects/${pid}/settings`, { method: 'PATCH', body: patch }), 'Sparat – generera om för att uppdatera upphängningar');
  });
  $('#rsel')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = Number(new FormData(e.target).get('ruleset_id'));
    await act(() => api(`/api/projects/${pid}/ruleset`, { body: { ruleset_id: id } }), 'Regelversion bytt – generera om');
    viewOverview(pid);
  });
  $$('[data-gate]').forEach((b) => b.addEventListener('click', async () => {
    await act(() => api(`/api/projects/${pid}/approve/${b.dataset.gate}`, { method: 'POST' }), 'Beslut registrerat');
    viewOverview(pid);
  }));
}

// ---------------------------------------------------------------------------
// Modell & plan
// ---------------------------------------------------------------------------
async function viewPlan(pid) {
  const [p, model] = await Promise.all([loadProject(pid), api(`/api/projects/${pid}/model`)]);
  if (!model.revision) { main().innerHTML = html`<div class="card empty">Ingen modell importerad. <a href="#/p/${pid}">Importera på översikten.</a></div>`.s; return; }
  const storeys = Object.keys(model.revision.storeys).sort((a, b) => model.revision.storeys[a] - model.revision.storeys[b]);
  const byCount = (s) => model.hangers.filter((h) => h.storey === s).length + model.pipes.filter((x) => x.storey === s).length / 100;
  let storey = store.get(`sh.storey.${pid}`, null);
  if (!storeys.includes(storey)) storey = [...storeys].sort((a, b) => byCount(b) - byCount(a))[0];
  let selected = null, addMode = false;
  const systems = [...new Set(model.pipes.map((x) => x.system))];

  const draw = () => {
    main().innerHTML = html`
      ${raw(rulesBanner(p))}
      <div class="row" style="margin-bottom:10px">
        <h1 style="margin:0">Modell & plan</h1><span class="chip">${model.revision.label}</span>
        <select id="storey">${storeys.map((s) => html`<option ${s === storey ? raw('selected') : ''}>${s}</option>`)}</select>
        <span class="spacer"></span>
        ${can('hangers.redigera') ? html`<button id="add" class="${addMode ? 'primary' : ''}">${addMode ? 'Klicka på ett rör för att lägga till…' : '+ Lägg till upphängning'}</button>` : ''}
        ${can('hangers.generera') ? html`<button id="gen">Generera om</button>` : ''}
      </div>
      <div class="split">
        <div>
          ${planSVG(model, storey, { selected })}
          <div class="legend" style="margin-top:6px">
            ${Object.entries(STATUS_COLOR).filter(([k]) => k !== 'makulerad').map(([k, c]) => html`<span><i style="background:${c}"></i>${k}</span>`)}
            <span>·</span>${systems.map((s) => html`<span><i style="background:${sysColor(s)};border-radius:2px"></i>${s}</span>`)}
            <span>· Skrolla för zoom, dra för att panorera</span>
          </div>
        </div>
        <div id="side" class="card"><p class="muted">Välj en upphängning eller ett rör i planen.</p>
          <h3>Stråk på ${storey}</h3>
          <table class="small"><thead><tr><th>Stråk</th><th>Rör</th><th class="num">Längd</th><th>UK takjärn</th></tr></thead><tbody>
          ${model.runs.filter((r) => r.storey === storey).map((r) => html`<tr><td><b>${r.key}</b></td><td>${r.lines.map((l) => l.system).join(', ')}</td><td class="num">${fmt((r.s_max - r.s_min) / 1000, 1)} m</td><td>${r.takjarn_override != null ? html`${fmt(r.takjarn_override)} <span class="src manuell">manuell</span>` : html`<span class="muted">auto</span>`}
            ${can('hojd.redigera') ? html` <a href="#" data-runtj="${r.key}">ändra</a>` : ''}</td></tr>`)}
          </tbody></table>
        </div>
      </div>`.s;
    const svg = $('svg.plan');
    const pz = enablePanZoom(svg);
    $('#storey').onchange = (e) => { storey = e.target.value; store.set(`sh.storey.${pid}`, storey); selected = null; draw(); };
    $('#gen')?.addEventListener('click', async () => {
      const r = await act(() => api(`/api/projects/${pid}/generate`, { method: 'POST' }));
      toast(`${r.created} nya · ${r.updated + r.revised} ändrade · ${r.cancelled} makulerade`);
      viewPlan(pid);
    });
    $('#add')?.addEventListener('click', () => { addMode = !addMode; draw(); });
    $$('[data-runtj]').forEach((a) => a.addEventListener('click', async (e) => {
      e.preventDefault();
      const run = model.runs.find((r) => r.storey === storey && r.key === a.dataset.runtj);
      const r = await ask(`Takjärnshöjd stråk ${run.key}`, { fields: [{ name: 'value', label: 'Underkant takjärn, mm över golv (tomt = automatiskt)', type: 'number', value: run.takjarn_override ?? '' }] });
      if (!r) return;
      await act(() => api(`/api/projects/${pid}/runs/${encodeURIComponent(storey)}/${run.key}/takjarn`, { method: 'PATCH', body: { value: r.value === '' ? null : Number(r.value) } }), 'Sparat – generera om för att uppdatera');
      viewPlan(pid);
    }));
    svg.addEventListener('click', async (e) => {
      if (svg._dragged) return;
      const hg = e.target.closest('[data-hanger]');
      const pe = e.target.closest('[data-pipe]');
      if (addMode) {
        const w = pz.toWorld(e);
        const hit = nearestRun(model, storey, w);
        if (!hit) { toast('Klicka närmare ett horisontellt rör i ett stråk.', true); return; }
        const h = await act(() => api(`/api/projects/${pid}/hangers`, { body: { storey, run_key: hit.run.key, s: Math.round(hit.s / 10) * 10 } }), 'Upphängning tillagd');
        addMode = false;
        const m2 = await api(`/api/projects/${pid}/model`);
        Object.assign(model, m2);
        selected = h.code;
        draw();
        showHanger(h.code);
        return;
      }
      if (hg) { selected = hg.dataset.hanger; draw(); showHanger(selected); }
      else if (pe) showPipe(Number(pe.dataset.pipe));
    });
    if (selected) showHanger(selected);
  };

  async function showHanger(code) {
    const h = await api(`/api/projects/${pid}/hangers/${code}`);
    $('#side').innerHTML = hangerPanel(pid, h).s;
    bindHangerPanel(pid, h, async () => { Object.assign(model, await api(`/api/projects/${pid}/model`)); draw(); });
  }
  function showPipe(id) {
    const x = model.pipes.find((q) => q.id === id);
    $('#side').innerHTML = html`<h2>${x.system}</h2>
      <dl class="kv"><dt>Namn</dt><dd>${x.name}</dd><dt>GlobalId</dt><dd class="mono small">${x.global_id}</dd>
      <dt>Material</dt><dd>${x.material}</dd><dt>Ytterdiameter</dt><dd>Ø${fmt(x.od, 1)} mm ${x.dn ? `(DN ${fmt(x.dn)})` : ''}</dd>
      <dt>Isolering</dt><dd>${x.insulation ? fmt(x.insulation) + ' mm' : '—'}</dd><dt>Typ</dt><dd>${x.classification}</dd>
      <dt>Längd</dt><dd>${fmt(Math.hypot(x.end[0] - x.start[0], x.end[1] - x.start[1], x.end[2] - x.start[2]))} mm</dd>
      <dt>Rörcentrum</dt><dd>+${fmt(x.z_override_rel ?? x.z_model_rel)} mm ${srcTag(x.z_override_rel != null ? 'manuell' : 'modell')}</dd>
      <dt>Modellhöjd</dt><dd>+${fmt(x.z_model_rel)} mm</dd></dl>
      ${can('hojd.redigera') ? html`<h3>Manuell höjd</h3><form id="ph" class="row">
        <select name="mode"><option value="center">Rörcentrum</option><option value="bottom">Underkant rör</option></select>
        <input name="value" type="number" placeholder="mm över golv" style="width:120px"><button class="primary">Spara</button>
        ${x.z_override_rel != null ? html`<button type="button" id="phreset">Återställ till modell</button>` : ''}</form>
        <p class="small muted">Generera om efter ändring.</p>` : ''}`.s;
    $('#ph')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      await act(() => api(`/api/projects/${pid}/pipes/${id}/height`, { method: 'PATCH', body: { mode: f.mode, value: Number(f.value) } }), 'Höjd sparad');
      Object.assign(model, await api(`/api/projects/${pid}/model`)); showPipe(id);
    });
    $('#phreset')?.addEventListener('click', async () => {
      await act(() => api(`/api/projects/${pid}/pipes/${id}/height`, { method: 'PATCH', body: { mode: null, value: null } }), 'Återställd');
      Object.assign(model, await api(`/api/projects/${pid}/model`)); showPipe(id);
    });
  }
  draw();
}

function nearestRun(model, storey, w) {
  let best = null;
  for (const r of model.runs.filter((x) => x.storey === storey)) {
    const s = w.x * r.u[0] + w.y * r.u[1];
    const t = w.x * r.n[0] + w.y * r.n[1];
    for (const l of r.lines) {
      if (s < l.s0 || s > l.s1) continue;
      const d = Math.abs(t - l.t);
      if (d < 400 && (!best || d < best.d)) best = { run: r, s, d };
    }
  }
  return best;
}

function hangerPanel(pid, h) {
  const d = h.data;
  const editable = can('hangers.redigera') && h.active && ['föreslagen', 'granskad', 'godkänd'].includes(h.status);
  return html`
    <div class="row"><h2 style="margin:0">${h.code}</h2><span class="chip">rev ${h.hanger_revision}</span>${chip(h.status)}${h.issue ? html`<span class="chip sev-varning">${h.issue}</span>` : ''}${h.critical ? html`<span class="chip sev-kritisk">kritisk</span>` : ''}</div>
    <p class="small muted">${h.storey} · zon ${h.zone} · stråk ${h.run_key} · ${h.origin}${h.locked ? ' · manuellt justerad' : ''} · modell ${h.model_revision}</p>
    ${hangerSVG(d)}
    <dl class="kv small">
      <dt>Position</dt><dd>X ${fmt(h.x)} · Y ${fmt(h.y)} · s ${fmt(h.s)}</dd>
      <dt>Referens</dt><dd>${h.reference?.x?.text}<br>${h.reference?.y?.text}</dd>
      <dt>Takjärn</dt><dd>${d.takjarn.article} · ${d.takjarn.length} mm · ${d.takjarn.surface}</dd>
      <dt>UK takjärn</dt><dd>+${fmt(d.takjarn.z_bottom_rel)} mm ${srcTag(d.takjarn.z_source)}</dd>
      <dt>Infästning</dt><dd>${d.takjarn.fastening ? `${d.takjarn.fastening.count} × ${d.takjarn.fastening.name}` : '—'}</dd>
      <dt>Last</dt><dd>${fmt(d.total_load_kg, 1)} kg · vikt hanger ${fmt(d.weight_kg, 2)} kg</dd>
      <dt>Regler</dt><dd class="small">${h.ruleset_label}<br><span class="muted mono">${d.rule_refs.join(' ')}</span></dd>
    </dl>
    <h3>Rör</h3>
    <table class="small"><thead><tr><th>System</th><th>Ø</th><th>CL</th><th>Pendel</th><th class="num">Last</th></tr></thead><tbody>
      ${d.pipes.map((p) => html`<tr><td>${p.system}<br><span class="muted">${p.material}${p.insulation ? ` · iso ${p.insulation}` : ''}</span></td><td>${fmt(p.od, 1)}</td><td>+${fmt(p.z_center_rel)} ${srcTag(p.z_source)}</td><td>${p.rod.dim}×${p.rod.length}<br><span class="muted">${p.clamp.article}</span></td><td class="num">${fmt(p.load_kg, 1)} kg</td></tr>`)}
    </tbody></table>
    <h3>Materiallista</h3>
    <table class="small"><tbody>${d.bom.map((b) => html`<tr><td class="mono">${b.article}</td><td>${b.name}</td><td class="num">${b.qty} st</td><td class="num">${b.cut_length ? b.cut_length + ' mm' : ''}</td></tr>`)}</tbody></table>
    ${d.warnings.length ? html`<div class="banner warn small">${d.warnings.map((w) => html`<div>⚠ ${w}</div>`)}</div>` : ''}
    <div class="row noprint" style="margin-top:10px">
      ${editable ? html`<button data-mv="-100">◀ 100</button><button data-mv="100">100 ▶</button><button data-mvto>Flytta till…</button><button class="danger" data-del>Ta bort</button>` : ''}
      ${can('hangers.granska') && h.active ? html`${h.status === 'föreslagen' ? html`<button data-st="granskad">Granskad</button>` : ''}${['föreslagen', 'granskad'].includes(h.status) ? html`<button class="primary" data-st="godkänd">Godkänn</button>` : ''}${['granskad', 'godkänd'].includes(h.status) ? html`<button data-st="föreslagen">Återställ</button>` : ''}` : ''}
      <a class="btn" href="#/p/${pid}/h/${h.code}">Detaljer & logg</a>
      <a class="btn" target="_blank" href="/api/projects/${pid}/labels.pdf?codes=${h.code}">Etikett</a>
    </div>`;
}

function bindHangerPanel(pid, h, refresh) {
  const root = $('#side') || main();
  $$('[data-mv]', root).forEach((b) => b.addEventListener('click', async () => {
    const r = await act(() => api(`/api/projects/${pid}/hangers/${h.code}/move`, { body: { s: h.s + Number(b.dataset.mv) } }), 'Flyttad');
    if (r.violations?.length) toast('Regelavvikelse: ' + r.violations.map((v) => `${v[1]}: ${v[2]}`).join('; '), true);
    await refresh();
  }));
  $('[data-mvto]', root)?.addEventListener('click', async () => {
    const f = await ask(`Flytta ${h.code}`, { fields: [{ name: 's', label: 'Ny position längs stråket (s, mm)', type: 'number', value: h.s }, { name: 'comment', label: 'Motivering' }] });
    if (!f) return;
    const r = await act(() => api(`/api/projects/${pid}/hangers/${h.code}/move`, { body: { s: Number(f.s), comment: f.comment } }), 'Flyttad');
    if (r.violations?.length) toast('Regelavvikelse: ' + r.violations.map((v) => `${v[1]}: ${v[2]}`).join('; '), true);
    await refresh();
  });
  $('[data-del]', root)?.addEventListener('click', async () => {
    const f = await ask(`Ta bort ${h.code}?`, { fields: [{ name: 'comment', label: 'Motivering' }], ok: 'Ta bort' });
    if (!f) return;
    const r = await act(() => api(`/api/projects/${pid}/hangers/${h.code}?comment=${encodeURIComponent(f.comment || '')}`, { method: 'DELETE' }), 'Borttagen');
    if (r.violations?.length) toast('Regelavvikelse: ' + r.violations.map((v) => `${v[1]}: ${v[2]}`).join('; '), true);
    await refresh();
  });
  $$('[data-st]', root).forEach((b) => b.addEventListener('click', async () => {
    await act(() => api(`/api/projects/${pid}/hangers/${h.code}/status`, { body: { status: b.dataset.st } }), `Status: ${b.dataset.st}`);
    await refresh();
  }));
}

// ---------------------------------------------------------------------------
// Hangerdetalj
// ---------------------------------------------------------------------------
async function viewHanger(pid, code) {
  await loadProject(pid);
  const h = await api(`/api/projects/${pid}/hangers/${code}`);
  main().innerHTML = html`<div class="split"><div class="card" id="side">${hangerPanel(pid, h)}</div>
    <div class="card"><h2>Spårbarhet</h2>
      ${h.flags.length ? html`<h3>Flaggor</h3>${h.flags.map((f) => html`<div class="small">${sev(f.severity)} ${f.message} ${f.resolved ? html`<span class="muted">(hanterad av ${f.resolved_by}: ${f.resolution})</span>` : ''}</div>`)}` : ''}
      <h3>Händelser</h3>
      <table class="small"><tbody>${[...h.events].reverse().map((e) => html`<tr><td>${dt(e.ts)}</td><td>${e.user}<br><span class="muted">${state.meta.roles[e.role] || e.role}</span></td><td><b>${e.action}</b>${e.details?.comment ? html`<br>${e.details.comment}` : ''}${e.details?.from && e.details?.to ? html`<br><span class="muted">${e.details.from} → ${e.details.to}</span>` : ''}${e.photo ? html`<br><a href="${e.photo}" target="_blank"><img src="${e.photo}" alt="foto" style="max-width:160px;border-radius:6px;margin-top:4px"></a>` : ''}</td></tr>`)}</tbody></table>
    </div></div>`.s;
  bindHangerPanel(pid, h, () => viewHanger(pid, code));
}

// ---------------------------------------------------------------------------
// Granskning
// ---------------------------------------------------------------------------
async function viewReview(pid) {
  const p = await loadProject(pid);
  const [hs, flags] = await Promise.all([api(`/api/projects/${pid}/hangers`), api(`/api/projects/${pid}/flags`)]);
  let filter = store.get('sh.revfilter', '');
  const draw = () => {
    const rows = hs.filter((h) => !filter || h.status === filter);
    main().innerHTML = html`
      ${raw(rulesBanner(p))}
      <h1>Granskning</h1>
      <div class="grid g2">
      <div class="card">
        <div class="row"><h2 style="margin:0">Upphängningar</h2><span class="spacer"></span>
          <select id="flt"><option value="">Alla statusar</option>${state.meta.statuses.map((s) => html`<option ${s === filter ? raw('selected') : ''}>${s}</option>`)}</select>
        </div>
        ${can('hangers.granska') ? html`<div class="row" style="margin:8px 0"><label class="small"><input type="checkbox" id="all"> Markera alla</label><span class="spacer"></span><button data-bulk="granskad">Markerade → granskad</button><button class="primary" data-bulk="godkänd">Markerade → godkänd</button></div>` : ''}
        <table class="small"><thead><tr><th></th><th>ID</th><th>Plan / zon</th><th>Stråk</th><th class="num">Rör</th><th class="num">Takjärn</th><th>Status</th><th>Varningar</th></tr></thead><tbody>
        ${rows.map((h) => html`<tr><td><input type="checkbox" data-code="${h.code}"></td><td><a href="#/p/${pid}/h/${h.code}"><b>${h.code}</b></a> <span class="muted">${h.hanger_revision}</span></td><td>${h.storey} · ${h.zone}</td><td>${h.run_key}</td><td class="num">${h.pipe_count}</td><td class="num">${h.takjarn_length}</td><td>${chip(h.status)} ${h.critical ? html`<span class="chip sev-kritisk">kritisk</span>` : ''}${h.issue ? html` <span class="chip sev-varning">${h.issue}</span>` : ''}</td><td>${h.warnings.length ? html`<span class="chip sev-varning" title="${h.warnings.join('\n')}">${h.warnings.length}</span>` : ''}</td></tr>`)}
        </tbody></table>
      </div>
      <div class="card">
        ${aiCard(pid)}
        <h2>Flaggor att hantera (${flags.length})</h2>
        <p class="small muted">Specialfall som MVP:n inte hanterar automatiskt flaggas för manuell granskning.</p>
        ${flags.length ? html`<table class="small"><tbody>${flags.map((f) => html`<tr><td>${sev(f.severity)}</td><td><b>${f.code}</b> · ${f.storey} ${f.run_key ? '· ' + f.run_key : ''}<br>${f.message}</td><td class="row">${can('ai.specialfall') && state.meta.ai.enabled ? html`<button data-aiflag="${f.id}" title="Be AI om ett lösningsförslag">AI-förslag</button>` : ''}${can('flaggor.hantera') ? html`<button data-resolve="${f.id}">Hantera</button>` : ''}</td></tr>`)}</tbody></table>` : html`<p class="muted">Inga öppna flaggor.</p>`}
        <div id="aiout"></div>
      </div></div>`.s;
    $('#flt').onchange = (e) => { filter = e.target.value; store.set('sh.revfilter', filter); draw(); };
    $('#all')?.addEventListener('change', (e) => $$('[data-code]').forEach((c) => { c.checked = e.target.checked; }));
    $$('[data-bulk]').forEach((b) => b.addEventListener('click', async () => {
      const codes = $$('[data-code]').filter((c) => c.checked).map((c) => c.dataset.code);
      if (!codes.length) { toast('Inga markerade', true); return; }
      const r = await act(() => api(`/api/projects/${pid}/hangers-bulk-status`, { body: { codes, status: b.dataset.bulk } }));
      toast(`${r.ok.length} uppdaterade${r.failed.length ? `, ${r.failed.length} misslyckades: ${r.failed[0].error}` : ''}`, !!r.failed.length);
      viewReview(pid);
    }));
    bindAiCard(pid, () => viewReview(pid));
    $$('[data-aiflag]').forEach((b) => b.addEventListener('click', async () => {
      b.disabled = true; b.textContent = 'AI analyserar…';
      try {
        const sug = await act(() => api(`/api/projects/${pid}/flags/${b.dataset.aiflag}/ai`, { method: 'POST' }));
        showSpecialSuggestion(pid, sug, () => viewReview(pid));
      } finally { b.disabled = false; b.textContent = 'AI-förslag'; }
    }));
    $$('[data-resolve]').forEach((b) => b.addEventListener('click', async () => {
      const f = await ask('Hantera flagga', { fields: [{ name: 'resolution', label: 'Beslut / åtgärd', type: 'textarea' }], ok: 'Markera hanterad' });
      if (!f?.resolution) return;
      await act(() => api(`/api/projects/${pid}/flags/${b.dataset.resolve}/resolve`, { body: { resolution: f.resolution } }), 'Flagga hanterad');
      viewReview(pid);
    }));
  };
  draw();
}

// ---------------------------------------------------------------------------
// AI-förslag (kräver alltid mänskligt beslut)
// ---------------------------------------------------------------------------
const aiNote = () => html`<p class="small muted">AI-förslag från ${state.meta.ai.model}. Förslagen kan vara fel – granska mot modell och underlag. Inget ändras förrän du godkänner, och regelmotorn kontrollerar resultatet.</p>`;
const conf = (c) => html`<span class="chip ${c === 'hög' ? 'st-godkänd' : c === 'medel' ? 'sev-varning' : 'sev-kritisk'}">${c}</span>`;

function aiCard(pid) {
  if (!can('ai.klassning')) return '';
  if (!state.meta.ai.enabled) return html`<div class="banner small">AI-stöd är avstängt. Sätt <code>ANTHROPIC_API_KEY</code> på servern för att aktivera tolkning av röriga modeller och förslag på specialfall.</div>`;
  return html`<div style="margin-bottom:16px"><h2>AI-tolkning av modell</h2>
    <p class="small">Tolkar rör där material eller system saknas eller inte känns igen, utifrån namn och egenskaper i IFC-filen.</p>
    <div class="row"><button id="aiclassify" class="primary">Tolka okända material/system</button><span id="aicand" class="small muted"></span></div>
    <div id="aiclass"></div></div>`;
}

async function bindAiCard(pid, refresh) {
  const btn = $('#aiclassify');
  if (!btn) return;
  api(`/api/projects/${pid}/ai/classify/candidates`).then((c) => { $('#aicand').textContent = `${c.count} rör behöver tolkas`; if (!c.count) btn.disabled = true; });
  const open = (await api(`/api/ai/suggestions?project_id=${pid}&kind=klassning`)).find((x) => x.status === 'föreslagen');
  if (open) showClassSuggestion(pid, open, refresh);
  btn.addEventListener('click', async () => {
    btn.disabled = true; btn.textContent = 'AI tolkar…';
    try { const sug = await act(() => api(`/api/projects/${pid}/ai/classify`, { body: { scope: 'okända' } })); showClassSuggestion(pid, sug, refresh); }
    finally { btn.disabled = false; btn.textContent = 'Tolka okända material/system'; }
  });
}

function showClassSuggestion(pid, sug, refresh) {
  const rows = sug.output.rows;
  const decide = can('ai.godkann_klassning');
  $('#aiclass').innerHTML = html`<div class="card" style="margin-top:10px">
    <div class="row"><b>Förslag #${sug.id}</b><span class="chip">${sug.status}</span><span class="small muted">${sug.target.revision} · ${dt(sug.created_at)} · ${sug.created_by}</span></div>
    ${aiNote()}
    ${sug.validation?.truncated ? html`<div class="banner warn small">Fler unika rörtyper än vad som skickades – kör igen efter beslut.</div>` : ''}
    <table class="small"><thead><tr><th></th><th>I modellen</th><th>Förslag</th><th>Säkerhet</th><th>Motivering</th></tr></thead><tbody>
    ${rows.map((r) => html`<tr><td><input type="checkbox" data-crow="${r.row}" ${decide && r.confidence !== 'låg' && r.material !== 'Okänt' ? raw('checked') : ''} ${decide ? '' : raw('disabled')}></td>
      <td>${r.current_material}<br><span class="muted">${r.current_system}</span><br><span class="muted">${r.count} rör</span></td>
      <td><b>${r.material}</b><br>${r.system_name || r.system_category}</td><td>${conf(r.confidence)}</td><td>${r.rationale}</td></tr>`)}
    </tbody></table>
    ${decide && sug.status === 'föreslagen' ? html`<div class="row" style="margin-top:8px"><button class="primary" id="capply">Godkänn markerade</button><button id="creject">Avvisa</button></div>` : html`<p class="small muted">Godkänns av BIM-/konstruktionsgranskare.</p>`}
  </div>`.s;
  $('#capply')?.addEventListener('click', async () => {
    const sel = $$('[data-crow]').filter((c) => c.checked).map((c) => Number(c.dataset.crow));
    const r = await act(() => api(`/api/ai/suggestions/${sug.id}/apply`, { body: { rows: sel } }));
    toast(`${r.changes} ändringar – generera om upphängningarna`);
    refresh();
  });
  $('#creject')?.addEventListener('click', async () => {
    const f = await ask('Avvisa förslag', { fields: [{ name: 'comment', label: 'Motivering' }], ok: 'Avvisa' });
    if (!f) return;
    await act(() => api(`/api/ai/suggestions/${sug.id}/reject`, { body: f }), 'Avvisat');
    refresh();
  });
}

function actionText(a) {
  const t = { add_hanger: 'Lägg till upphängning', move_hanger: 'Flytta upphängning', remove_hanger: 'Ta bort upphängning', set_pipe_height: 'Sätt rörhöjd', manual_note: 'Manuell åtgärd' }[a.type];
  const where = a.type === 'add_hanger' ? ` · ${a.storey || ''} ${a.run_key} s=${fmt(a.s)}`
    : a.type === 'move_hanger' ? ` · ${a.hanger_code} → s=${fmt(a.s)}`
    : a.type === 'remove_hanger' ? ` · ${a.hanger_code}`
    : a.type === 'set_pipe_height' ? ` · ${a.height_mode === 'bottom' ? 'UK' : 'CL'} +${fmt(a.value)}` : '';
  return `${t}${where}`;
}

function showSpecialSuggestion(pid, sug, refresh) {
  const o = sug.output;
  const box = $('#aiout');
  box.innerHTML = html`<div class="card" style="margin-top:12px">
    <div class="row"><h3 style="margin:0">AI-förslag #${sug.id} · ${sug.target.code}</h3><span class="chip ${o.risk === 'hög' ? 'sev-kritisk' : o.risk === 'medel' ? 'sev-varning' : 'sev-info'}">risk ${o.risk}</span>${o.requires_expert ? html`<span class="chip sev-kritisk">kräver tekniskt sakkunnig</span>` : ''}</div>
    ${aiNote()}
    <p>${o.assessment}</p>
    <h3>Åtgärder</h3>
    ${o.actions.length ? html`<ol class="steps">${o.actions.map((a, i) => html`<li><input type="checkbox" data-act="${i}" ${a.type === 'manual_note' ? raw('checked') : ''}><span><b>${actionText(a)}</b><br><span class="small">${a.description}</span></span></li>`)}</ol>` : html`<p class="muted">Inga åtgärder föreslogs.</p>`}
    ${o.notes_for_reviewer.length ? html`<h3>Att kontrollera</h3><ul class="small">${o.notes_for_reviewer.map((n) => html`<li>${n}</li>`)}</ul>` : ''}
    <label class="small"><input type="checkbox" id="sresolve"> Markera flaggan som hanterad</label>
    <div class="row" style="margin-top:8px"><button class="primary" id="sapply">Utför valda åtgärder</button><button id="sreject">Avvisa förslaget</button></div>
    <p class="small muted">Åtgärderna utförs tillsammans – misslyckas en görs inga ändringar. Positioner kontrolleras mot regelmotorn.</p>
  </div>`.s;
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  $('#sapply').addEventListener('click', async () => {
    const actions = $$('[data-act]', box).filter((c) => c.checked).map((c) => Number(c.dataset.act));
    const r = await act(() => api(`/api/ai/suggestions/${sug.id}/apply`, { body: { actions, resolve_flag: $('#sresolve').checked } }));
    const viol = r.results.flatMap((x) => x.violations || []);
    toast(`${r.results.length} åtgärder utförda${viol.length ? ' · regelavvikelser: ' + viol.map((v) => v[2]).join('; ') : ''}`, !!viol.length);
    refresh();
  });
  $('#sreject').addEventListener('click', async () => {
    await act(() => api(`/api/ai/suggestions/${sug.id}/reject`, { body: { comment: '' } }), 'Avvisat');
    box.innerHTML = '';
  });
}

/** Produktblad → regelutkast (regelbiblioteket). */
function datasheetCard(rid) {
  if (!can('regler.redigera') || !state.meta.ai.enabled) return '';
  return html`<div class="card" style="margin-top:16px"><h2>Läs in produktblad (AI)</h2>
    <p class="small">Ladda upp tillverkarens produktblad (PDF). AI:n plockar ut svep, rörvikter, klamringsavstånd, pendellaster och takjärn med sidhänvisning. Du väljer rader och får ett nytt <b>utkast</b> som sedan publiceras och verifieras som vanligt.</p>
    <form id="dsform" class="row"><input type="file" name="file" accept=".pdf,.txt,.csv,.md" required><button class="primary">Läs in</button></form>
    <div id="dsout"></div></div>`;
}

const SECTION_LABEL = { clamps: 'Svep', pipe_weight: 'Rörvikt', spacing: 'Klamringsavstånd', rod_capacity: 'Pendellast', takjarn: 'Takjärn' };

function showDatasheet(sug) {
  const o = sug.output;
  $('#dsout').innerHTML = html`<div style="margin-top:10px">
    <div class="row"><b>Förslag #${sug.id}</b><span class="chip">${sug.status}</span><span class="small muted">${sug.source_file} · ${o.manufacturer} ${o.product}</span></div>
    ${aiNote()}
    ${o.warnings.length ? html`<div class="banner warn small">${o.warnings.map((w) => html`<div>⚠ ${w}</div>`)}</div>` : ''}
    ${o.rows.length ? html`<table class="small"><thead><tr><th></th><th>Område</th><th>Värden</th><th>Källa</th><th>Ersätter</th></tr></thead><tbody>
      ${o.rows.map((r) => html`<tr><td><input type="checkbox" data-dsrow="${r.id}" ${r.ok ? raw('checked') : raw('disabled')}></td><td>${SECTION_LABEL[r.section]}</td>
        <td class="mono" style="font-size:11px">${Object.entries(r.values).filter(([, v]) => v !== null && v !== '').map(([k, v]) => `${k}=${v}`).join(' ')}${r.problems.length ? html`<br><span class="neg">${r.problems.join('; ')}</span>` : ''}</td>
        <td>s.${r.source.page ?? '?'} “${r.source.quote}”${r.source.note ? html`<br><span class="muted">${r.source.note}</span>` : ''}</td><td class="mono" style="font-size:11px">${r.replaces.join(' ') || 'ny'}</td></tr>`)}
    </tbody></table>` : html`<p class="muted">Inga värden hittades i dokumentet.</p>`}
    ${sug.status === 'föreslagen' ? html`<div class="row" style="margin-top:8px"><button class="primary" id="dsapply">Skapa regelutkast av markerade</button><button id="dsreject">Avvisa</button></div>` : ''}
  </div>`.s;
  $('#dsapply')?.addEventListener('click', async () => {
    const rows = $$('[data-dsrow]').filter((c) => c.checked).map((c) => Number(c.dataset.dsrow));
    const r = await act(() => api(`/api/ai/suggestions/${sug.id}/apply`, { body: { rows } }), 'Regelutkast skapat – kontrollera, publicera och verifiera');
    if (r.draft) location.hash = `#/rules/${r.draft.id}`;
  });
  $('#dsreject')?.addEventListener('click', async () => {
    await act(() => api(`/api/ai/suggestions/${sug.id}/reject`, { body: { comment: '' } }), 'Avvisat');
    $('#dsout').innerHTML = '';
  });
}

function bindDatasheet(rid) {
  $('#dsform')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const b = e.target.querySelector('button');
    b.disabled = true; b.textContent = 'AI läser produktbladet…';
    try { showDatasheet(await act(() => api(`/api/rulesets/${rid}/ai/datasheet`, { body: new FormData(e.target) }))); }
    finally { b.disabled = false; b.textContent = 'Läs in'; }
  });
  if (can('regler.redigera')) api(`/api/ai/suggestions?kind=produktblad&ruleset_id=${rid}`).then((l) => { const open = l.find((x) => x.status === 'föreslagen'); if (open && $('#dsout')) showDatasheet(open); });
}

/** Normtider från uppföljning (statistik, ingen språkmodell). */
async function normtidCard(pid) {
  const [proj, all] = await Promise.all([api(`/api/normtid?project_id=${pid}`), api('/api/normtid')]);
  const block = (title, r, scope) => {
    const st = r.stats;
    if (!st.n) return html`<p class="small muted">${title} (${scope}): inga kvittenser med tid ännu.</p>`;
    return html`<p class="small"><b>${title}</b> (${scope}): median <b>${fmt(st.median_min, 1)} min</b> · medel ${fmt(st.mean_min, 1)} · p25–p75 ${fmt(st.p25_min, 1)}–${fmt(st.p75_min, 1)} · n=${st.n} (${st.n_angiven} angivna, ${st.n_harledd} härledda)${st.ci95_median_min ? ` · 95 % intervall ${fmt(st.ci95_median_min[0], 1)}–${fmt(st.ci95_median_min[1], 1)} min` : ''}
      ${r.suggestion?.field ? html`<br><button type="button" data-normtid="${r.suggestion.hours}">Använd ${fmt(r.suggestion.hours, 3)} h/st i kalkylen</button>` : !r.suggestion ? html`<br><span class="muted">Förslag ges från ${proj.min_samples} kvittenser.</span>` : ''}</p>`;
  };
  return html`<div class="card" style="margin-top:16px"><h2>Normtider från uppföljning</h2>
    <p class="small muted">Räknas fram ur montörernas och produktionens kvittenser (statistik – ingen AI-gissning). ${proj.note}</p>
    ${block('Montage', proj.montage, 'detta projekt')}${block('Montage', all.montage, 'alla projekt')}
    ${block('Produktion', proj.produktion, 'detta projekt')}${block('Produktion', all.produktion, 'alla projekt')}</div>`;
}

// ---------------------------------------------------------------------------
// Produktionsstation
// ---------------------------------------------------------------------------
function productionSteps(h) {
  const d = h.data, tj = d.takjarn;
  const rods = {};
  for (const p of d.pipes) { const k = `${p.rod.dim} × ${p.rod.length} mm`; rods[k] = (rods[k] || 0) + 1; }
  const holes = [...d.pipes].sort((a, b) => a.t_on_takjarn - b.t_on_takjarn);
  const nuts = d.bom.filter((b) => b.category === 'mutter').reduce((a, b) => a + b.qty, 0);
  const parts = d.bom.reduce((a, b) => a + b.qty, 0);
  return [
    `Kapa/välj takjärn ${tj.article}: <b>${tj.length} mm</b> (${tj.surface}).`,
    `Märk hålbild från vänster ände: <b>${holes.map((p) => p.t_on_takjarn).join(' · ')} mm</b>.`,
    `Kapa/välj pendlar: ${Object.entries(rods).map(([k, n]) => `<b>${n} st ${k}</b>`).join(', ')}.`,
    `Montera pendlar enligt vy: ${holes.map((p) => `${p.t_on_takjarn} mm → ${p.rod.dim}×${p.rod.length} (${esc(p.system)})`).join('; ')}.`,
    `Placera ${nuts} muttrar och svep: ${holes.map((p) => `${esc(p.clamp.article)}`).join(', ')}.`,
    `Egenkontroll: takjärn ${tj.length} ±${'{tol}'} mm, pendellängder ±{tol} mm, totalt <b>${parts}</b> komponenter.`,
  ];
}

async function viewProduction(pid, code) {
  const p = await loadProject(pid);
  const q = await api(`/api/projects/${pid}/production/queue`);
  const queue = q.queue;
  if (!code && queue.length) code = queue[0].code;
  if (!code) {
    main().innerHTML = html`${!q.released ? html`<div class="banner warn">Produktionen är inte frisläppt för projektet.</div>` : ''}<div class="card empty">Inga godkända upphängningar väntar på produktion.</div>`.s;
    return;
  }
  const h = await api(`/api/projects/${pid}/hangers/${code}`);
  const idx = queue.findIndex((x) => x.code === code);
  const rs = await api(`/api/rulesets/${p.ruleset.id}`);
  const tol = rs.content.tolerance.cut_length;
  const steps = productionSteps(h).map((s) => s.replaceAll('{tol}', tol));
  const isOp = state.role === 'produktion';
  const canProduce = isOp && q.released && h.status === 'godkänd' && !['paus', 'materialbrist', 'avvikelse', 'omarbete'].includes(h.issue);
  main().innerHTML = html`
    ${!q.released ? html`<div class="banner warn">Produktionen är inte frisläppt – vyn är endast för förhandsgranskning.</div>` : ''}
    ${!isOp ? html`<div class="banner warn small">Byt roll till Produktionsoperatör för att kvittera.</div>` : ''}
    ${h.issue ? html`<div class="banner crit"><b>${h.issue}</b> ${h.issue_comment || ''}</div>` : ''}
    <div class="station">
      <div class="card">
        <div class="row"><span class="id">${h.code}</span><span class="chip">rev ${h.hanger_revision}</span>${chip(h.status)}</div>
        <p class="muted">${p.name} · Modell ${h.model_revision} · ${h.storey} · zon ${h.zone} · kolli ${h.package || '—'} ${h.pack_seq ? `#${h.pack_seq}` : ''}</p>
        ${hangerSVG(h.data, { width: 820, big: true })}
      </div>
      <div class="card">
        <div class="row"><h2 style="margin:0">Arbetsmoment</h2><span class="spacer"></span><span class="muted small">${idx >= 0 ? `${idx + 1} av ${queue.length} i kö` : ''}</span></div>
        <ol class="steps" style="margin-top:10px">${steps.map((s, i) => html`<li><input type="checkbox" data-step="${i}" ${isOp ? '' : raw('disabled')}><span>${raw(s)}</span></li>`)}</ol>
        <div class="grid" style="margin-top:14px">
          ${isOp ? html`<label class="f">Tidsåtgång (minuter, valfritt)<input id="dur" type="number" min="1" max="600" step="1" inputmode="numeric"></label>` : ''}
          <button class="primary big" id="done" ${canProduce ? '' : raw('disabled')}>✔ Klar – skriv ut etikett</button>
          ${isOp ? html`<div class="row">${state.meta.issues.produktion.map((i) => html`<button data-issue="${i}">${i}</button>`)}${h.issue ? html`<button data-issue="">Avsluta avvikelse</button>` : ''}</div>` : ''}
          <div class="row">
            <button id="prev" ${idx > 0 ? '' : raw('disabled')}>◀ Föregående</button><span class="spacer"></span>
            <button id="next" ${idx >= 0 && idx < queue.length - 1 ? '' : raw('disabled')}>Nästa ▶</button>
          </div>
        </div>
        <h3>Kö (produktionsordning = omvänd uttagsordning per kolli)</h3>
        <div class="row small">${queue.slice(0, 40).map((x) => html`<a class="chip ${x.code === code ? 'st-godkänd' : ''}" href="#/p/${pid}/production/${x.code}">${x.code}</a>`)}</div>
      </div>
    </div>`.s;
  $$('[data-step]').forEach((c) => c.addEventListener('change', () => c.closest('li').classList.toggle('done', c.checked)));
  $('#done')?.addEventListener('click', async () => {
    const unchecked = $$('[data-step]').filter((c) => !c.checked).length;
    if (unchecked && !confirm(`${unchecked} moment är inte avbockade. Markera ändå som klar?`)) return;
    await act(() => api(`/api/projects/${pid}/hangers/${code}/status`, { body: { status: 'producerad', comment: 'Egenkontroll utförd', duration_min: $('#dur')?.value ? Number($('#dur').value) : null } }), `${code} producerad`);
    window.open(`/api/projects/${pid}/labels.pdf?codes=${code}`, '_blank');
    const rest = queue.filter((x) => x.code !== code);
    location.hash = rest.length ? `#/p/${pid}/production/${rest[Math.min(Math.max(idx, 0), rest.length - 1)].code}` : `#/p/${pid}/production`;
  });
  $$('[data-issue]').forEach((b) => b.addEventListener('click', async () => {
    const issue = b.dataset.issue || null;
    const f = issue ? await ask(`Registrera ${issue}`, { fields: [{ name: 'comment', label: 'Beskrivning', type: 'textarea' }] }) : { comment: '' };
    if (!f) return;
    await act(() => api(`/api/projects/${pid}/hangers/${code}/issue`, { body: { issue, comment: f.comment } }), issue ? `${issue} registrerad` : 'Avvikelse avslutad');
    viewProduction(pid, code);
  }));
  $('#prev')?.addEventListener('click', () => { location.hash = `#/p/${pid}/production/${queue[idx - 1].code}`; });
  $('#next')?.addEventListener('click', () => { location.hash = `#/p/${pid}/production/${queue[idx + 1].code}`; });
}

// ---------------------------------------------------------------------------
// Packning
// ---------------------------------------------------------------------------
async function viewPack(pid) {
  const p = await loadProject(pid);
  const pks = await api(`/api/projects/${pid}/packages`);
  const cfg = { max_weight_kg: 25, max_count: 12, split_by_zone: true, ...(p.settings.packing || {}) };
  const canPlan = can('lager.hantera') || can('projekt.hantera');
  main().innerHTML = html`
    <div class="row"><h1>Packning och logistik</h1><span class="spacer"></span><a class="btn" target="_blank" href="/api/projects/${pid}/packlist.pdf">Packlistor (PDF)</a></div>
    ${canPlan ? html`<form id="plan" class="card row" style="margin-bottom:16px">
      <label class="f">Max vikt/kolli (kg)<input name="max_weight_kg" type="number" step="0.5" value="${cfg.max_weight_kg}"></label>
      <label class="f">Max antal/kolli<input name="max_count" type="number" value="${cfg.max_count}"></label>
      <label class="f">Dela per zon<select name="split_by_zone"><option value="true">Ja</option><option value="false" ${cfg.split_by_zone ? '' : raw('selected')}>Nej</option></select></label>
      <button class="primary">Planera kollin</button><span class="small muted">Grupperar ej packade hangers i montageordning per plan/zon. Redan packade kollin behålls.</span>
    </form>` : ''}
    ${pks.length ? html`<div class="grid g3">${pks.map((pk) => html`<div class="card">
      <div class="row"><h2 style="margin:0">${pk.code}</h2><span class="chip">${pk.status}</span><span class="spacer"></span><span class="small muted">${fmt(pk.weight_kg, 1)} kg · ${pk.hangers.length} st</span></div>
      <p class="small muted">${pk.storey} · zon ${pk.zone || '—'}</p>
      <table class="small"><thead><tr><th>Uttag</th><th>ID</th><th>Status</th></tr></thead><tbody>${pk.hangers.map((h) => html`<tr><td>${h.pack_seq}</td><td><a href="#/p/${pid}/h/${h.code}">${h.code}</a> <span class="muted">${h.rev}</span></td><td>${chip(h.status)}${h.issue ? html` <span class="chip sev-varning">${h.issue}</span>` : ''}</td></tr>`)}</tbody></table>
      <div class="row" style="margin-top:8px">
        <a class="btn" target="_blank" href="/api/projects/${pid}/labels.pdf?package=${pk.code}">Etiketter</a>
        <a class="btn" target="_blank" href="/api/projects/${pid}/packlist.pdf?package=${pk.code}">Packlista</a>
        ${can('lager.hantera') && pk.status === 'planerad' ? html`<button class="primary" data-pk="${pk.code}" data-st="packad">Packad</button>` : ''}
        ${can('lager.hantera') && pk.status === 'packad' ? html`<button class="primary" data-pk="${pk.code}" data-st="levererad">Levererad</button>` : ''}
      </div></div>`)}</div>` : html`<div class="card empty">Inga kollin planerade.</div>`}`.s;
  $('#plan')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    await act(() => api(`/api/projects/${pid}/packing/plan`, { body: { max_weight_kg: Number(f.max_weight_kg), max_count: Number(f.max_count), split_by_zone: f.split_by_zone === 'true' } }), 'Kollin planerade');
    viewPack(pid);
  });
  $$('[data-pk]').forEach((b) => b.addEventListener('click', async () => {
    await act(() => api(`/api/projects/${pid}/packages/${b.dataset.pk}/status`, { body: { status: b.dataset.st } }), `${b.dataset.pk} ${b.dataset.st}`);
    viewPack(pid);
  }));
}

// ---------------------------------------------------------------------------
// Montageguide (mobil)
// ---------------------------------------------------------------------------
async function viewMontage(pid, code) {
  const p = await loadProject(pid);
  const model = await api(`/api/projects/${pid}/model`);
  const hs = [...model.hangers].sort((a, b) => a.montage_seq - b.montage_seq);
  const todo = hs.filter((h) => h.status !== 'monterad');
  const next = todo.find((h) => ['levererad', 'packad'].includes(h.status)) || todo[0];
  if (!code) {
    const done = hs.length - todo.length;
    main().innerHTML = html`<div class="mobile">
      <h1>Montageguide</h1>
      <p class="muted">${p.name} · ${done} av ${hs.length} monterade</p>
      <div class="row"><input id="q" placeholder="Sök ID, t.ex. P2-014" style="flex:1"><button id="scan">Skanna QR</button></div>
      ${next ? html`<div class="card" style="margin:14px 0"><span class="muted small">Nästa att montera</span><div class="row"><span class="id">${next.code}</span>${chip(next.status)}</div>
        <p>${next.storey} · zon ${next.zone}<br>${next.reference?.x?.text}<br>${next.reference?.y?.text}</p>
        <a class="btn primary big" href="#/p/${pid}/montage/${next.code}" style="width:100%;justify-content:center">Öppna ${next.code}</a></div>` : html`<div class="banner ok">Allt monterat!</div>`}
      <table class="small"><tbody>${hs.map((h) => html`<tr data-row="${h.code.toLowerCase()}"><td>${h.montage_seq}</td><td><a href="#/p/${pid}/montage/${h.code}"><b>${h.code}</b></a></td><td>${h.storey} · ${h.zone}</td><td>${chip(h.status)}${h.issue ? html` <span class="chip sev-varning">${h.issue}</span>` : ''}</td></tr>`)}</tbody></table>
      <video id="vid" playsinline style="width:100%;display:none;border-radius:10px;margin-top:10px"></video>
    </div>`.s;
    $('#q').addEventListener('input', (e) => { const v = e.target.value.toLowerCase(); $$('[data-row]').forEach((r) => { r.style.display = r.dataset.row.includes(v) ? '' : 'none'; }); });
    $('#q').addEventListener('keydown', (e) => { if (e.key === 'Enter') { const hit = hs.find((h) => h.code.toLowerCase() === e.target.value.trim().toLowerCase()); if (hit) location.hash = `#/p/${pid}/montage/${hit.code}`; } });
    $('#scan').addEventListener('click', () => scanQR(pid));
    return;
  }
  const h = await api(`/api/projects/${pid}/hangers/${code}`);
  const d = h.data;
  const isM = state.role === 'montor';
  const after = hs.filter((x) => x.montage_seq > h.montage_seq && x.status !== 'monterad')[0];
  main().innerHTML = html`<div class="mobile">
    <a href="#/p/${pid}/montage">◀ Alla</a>
    ${h.issue ? html`<div class="banner crit"><b>${h.issue}</b> ${h.issue_comment || ''}</div>` : ''}
    ${h.critical ? html`<div class="banner crit">Kritisk ändring – kontrollera revision innan montage.</div>` : ''}
    <div class="row"><span class="id">${h.code}</span><span class="chip">rev ${h.hanger_revision}</span>${chip(h.status)}</div>
    <p class="muted">${h.storey} · zon ${h.zone} · stråk ${h.run_key} · montage nr ${h.montage_seq}</p>
    <div class="card"><dl class="kv">
      <dt>Läge</dt><dd><b>${h.reference?.x?.text}</b><br><b>${h.reference?.y?.text}</b></dd>
      <dt>UK takjärn</dt><dd><b>+${fmt(d.takjarn.z_bottom_rel)} mm</b> över golv ${srcTag(d.takjarn.z_source)}</dd>
      <dt>Riktning</dt><dd>Takjärn tvärs stråket (${d.takjarn.length} mm)</dd>
      <dt>Infästning</dt><dd>${d.takjarn.fastening ? `${d.takjarn.fastening.count} × ${d.takjarn.fastening.name}` : '—'}</dd>
      <dt>Rör</dt><dd>${d.pipes.map((x) => html`${x.system} Ø${fmt(x.od, 1)} CL +${fmt(x.z_center_rel)}<br>`)}</dd>
    </dl></div>
    <h3>Ritningsutsnitt</h3>
    ${planSVG(model, h.storey, { focus: h, mini: true })}
    ${hangerSVG(d, { width: 520 })}
    ${isM ? html`<form id="rep" class="grid" style="margin-top:12px">
      <textarea name="comment" rows="2" placeholder="Kommentar (valfritt)" style="font-family:inherit"></textarea>
      <label class="f">Tidsåtgång för montaget (minuter, valfritt – förbättrar normtiderna)<input type="number" name="duration_min" min="1" max="600" step="1" inputmode="numeric"></label>
      <label class="f">Foto<input type="file" name="photo" accept="image/*" capture="environment"></label>
      ${h.status !== 'monterad' ? html`<button class="primary big" name="action" value="monterad" ${['levererad', 'packad'].includes(h.status) ? '' : raw('disabled')}>✔ Monterad</button>` : ''}
      <div class="row">${state.meta.issues.montor.map((i) => html`<button name="action" value="${i}">${i.replace('_', ' ')}</button>`)}${h.issue ? html`<button name="action" value="avsluta">Avsluta avvikelse</button>` : ''}<button name="action" value="kommentar">Spara kommentar/foto</button></div>
    </form>` : html`<div class="banner warn small" style="margin-top:12px">Byt roll till Montör för att kvittera montage.</div>`}
    ${after ? html`<p><a class="btn big" style="width:100%;justify-content:center" href="#/p/${pid}/montage/${after.code}">Nästa: ${after.code} ▶</a></p>` : ''}
    <h3>Historik</h3>
    <table class="small"><tbody>${[...h.events].reverse().slice(0, 12).map((e) => html`<tr><td>${dt(e.ts)}</td><td>${e.user}</td><td>${e.action}${e.details?.comment ? ': ' + e.details.comment : ''}${e.photo ? html` <a href="${e.photo}" target="_blank">foto</a>` : ''}</td></tr>`)}</tbody></table>
  </div>`.s;
  let action = null;
  $$('#rep button[name=action]').forEach((b) => b.addEventListener('click', () => { action = b.value; }));
  $('#rep')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    fd.set('action', action || 'kommentar');
    if (!fd.get('photo')?.size) fd.delete('photo');
    if (!fd.get('duration_min') || (action || 'kommentar') !== 'monterad') fd.delete('duration_min');
    await act(() => api(`/api/projects/${pid}/hangers/${code}/report`, { body: fd }), 'Registrerat');
    if (action === 'monterad' && after) location.hash = `#/p/${pid}/montage/${after.code}`;
    else viewMontage(pid, code);
  });
}

async function scanQR(pid) {
  if (!('BarcodeDetector' in window)) { toast('QR-skanning stöds inte i denna webbläsare – använd mobilens kamera direkt på etiketten.', true); return; }
  const vid = $('#vid');
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    vid.srcObject = stream; vid.style.display = 'block'; await vid.play();
    const det = new window.BarcodeDetector({ formats: ['qr_code'] });
    const tick = async () => {
      if (!vid.srcObject) return;
      const codes = await det.detect(vid).catch(() => []);
      if (codes.length) {
        stream.getTracks().forEach((t) => t.stop()); vid.srcObject = null;
        const mm = codes[0].rawValue.match(/\/q\/(\d+)\/([^/?#]+)/);
        if (mm) location.hash = `#/p/${mm[1]}/montage/${mm[2]}`; else toast('Okänd QR-kod', true);
        return;
      }
      requestAnimationFrame(tick);
    };
    tick();
  } catch (e) { toast('Kunde inte starta kameran: ' + e.message, true); }
}

async function viewQR(pid, code) {
  if (state.role === 'produktion') location.replace(`#/p/${pid}/production/${code}`);
  else if (state.role === 'montor') location.replace(`#/p/${pid}/montage/${code}`);
  else location.replace(`#/p/${pid}/h/${code}`);
}

// ---------------------------------------------------------------------------
// Material
// ---------------------------------------------------------------------------
async function viewBom(pid) {
  await loadProject(pid);
  const b = await api(`/api/projects/${pid}/bom`);
  main().innerHTML = html`
    <div class="row"><h1>Materiallista (BOM)</h1><span class="spacer"></span><a class="btn" href="/api/projects/${pid}/bom.csv">CSV projekt</a><a class="btn" href="/api/projects/${pid}/bom.csv?level=hanger">CSV per hanger</a></div>
    <div class="grid g2">
      <div class="card"><h2>Per projekt · ${b.hangers} upphängningar · ${fmt(b.total_kg, 1)} kg</h2>
        <table><thead><tr><th>Artikel</th><th>Benämning</th><th class="num">Antal</th><th class="num">Total längd</th><th class="num">Lagerlängder</th><th class="num">kg</th></tr></thead><tbody>
        ${b.items.map((it) => html`<tr><td class="mono small">${it.article}</td><td>${it.name}</td><td class="num">${fmt(it.qty)}</td><td class="num">${it.total_length_mm ? fmt(it.total_length_mm / 1000, 1) + ' m' : ''}</td><td class="num">${it.stock_bars ?? ''}</td><td class="num">${fmt(it.kg, 1)}</td></tr>`)}
        </tbody></table></div>
      <div class="card"><h2>Kapoptimering</h2>
        ${Object.entries(b.cut_plans).map(([art, pl]) => html`<h3>${art}</h3>
          <p class="small">${pl.bars} st × ${pl.stock_length} mm · nyttjande ${fmt(pl.utilization * 100, 1)} % · spill ${fmt(pl.waste_mm)} mm ${pl.oversize.length ? html`<span class="neg">· ${pl.oversize.length} för långa</span>` : ''}</p>
          <details><summary class="small">Kapschema</summary><table class="small"><tbody>${pl.patterns.map((x, i) => html`<tr><td>Längd ${i + 1}</td><td class="mono">${x.cuts.join(' + ')}</td><td class="num">spill ${x.waste}</td></tr>`)}</tbody></table></details>`)}
      </div>
      <div class="card"><h2>Per kolli</h2>
        ${Object.keys(b.per_package).length ? Object.entries(b.per_package).map(([k, items]) => html`<h3>${k}</h3><table class="small"><tbody>${items.map((it) => html`<tr><td class="mono">${it.article}</td><td>${it.name}</td><td class="num">${it.qty}</td></tr>`)}</tbody></table>`) : html`<p class="muted">Inga kollin planerade.</p>`}
      </div>
    </div>`.s;
}

// ---------------------------------------------------------------------------
// Kalkyl
// ---------------------------------------------------------------------------
async function viewCalc(pid) {
  const p = await loadProject(pid);
  const c = await api(`/api/projects/${pid}/calc`);
  let inputs = { ...c.inputs };
  const labels = state.meta.calc_labels;
  const fixed = c.saved?.is_fixed_offer;
  const render = (r) => {
    $('#res').innerHTML = html`
      ${fixed ? html`<div class="banner ok">Fast offert lämnad (${c.saved.created_by}, ${dt(c.saved.created_at)}).</div>` : html`<div class="banner warn"><b>RÄKNEEXEMPEL</b> – inte en fast offert. Alla antaganden är redigerbara.</div>`}
      <div class="grid g3">
        <div class="card kpi"><span class="l">Traditionellt</span><span class="v">${kr(r.trad_total)}</span><span class="small muted">arbete ${kr(r.trad_labor)} · material ${kr(r.trad_material)} · ${fmt(r.trad_hours, 1)} h</span></div>
        <div class="card kpi"><span class="l">Med SmartHangers</span><span class="v">${kr(r.sh_total)}</span><span class="small muted">leverans ${kr(r.sh_price)} · montage ${kr(r.sh_labor)} · ${fmt(r.sh_hours, 1)} h</span></div>
        <div class="card kpi"><span class="l">Besparing</span><span class="v ${r.saving >= 0 ? 'pos' : 'neg'}">${kr(r.saving)}</span><span class="small muted">${fmt(r.saving_pct, 1)} % av traditionell kostnad</span></div>
        <div class="card kpi"><span class="l">Frigjorda montörstimmar</span><span class="v">${fmt(r.freed_montor_hours, 1)} h</span></div>
        <div class="card kpi"><span class="l">Kvarvarande lärlingstid</span><span class="v">${fmt(r.sh_apprentice_hours, 1)} h</span><span class="small muted">montörstid kvar ${fmt(r.sh_montor_hours, 1)} h</span></div>
        <div class="card kpi"><span class="l">Fastprisandel</span><span class="v">${fmt(r.fixed_price_share, 1)} %</span><span class="small muted">av total kostnad med SmartHangers</span></div>
        <div class="card kpi"><span class="l">Lönsamhetsgräns</span><span class="v">${r.breakeven_hangers === null ? 'ej lönsamt' : fmt(r.breakeven_hangers) + ' st'}</span><span class="small muted">antal upphängningar där besparingen blir 0</span></div>
        <div class="card kpi"><span class="l">Max styckpris</span><span class="v">${r.breakeven_unit_price === null ? '—' : kr(r.breakeven_unit_price)}</span><span class="small muted">vid aktuellt antal (exkl. påslag)</span></div>
        <div class="card kpi"><span class="l">Per upphängning</span><span class="v">${kr(r.per_hanger_traditional)} → ${kr(r.per_hanger_smarthanger)}</span><span class="small muted">rörlig kostnad trad → SH</span></div>
      </div>
      <div class="card" style="margin-top:16px"><h2>Känslighetsanalys – besparing (kr)</h2>
        <table class="sens"><thead><tr><th>Parameter</th>${[-20, -10, 0, 10, 20].map((x) => html`<th class="num">${x > 0 ? '+' : ''}${x} %</th>`)}</tr></thead><tbody>
        ${r.sensitivity.map((row) => html`<tr><td>${row.label}</td>${row.values.map((v) => html`<td class="${v.saving >= 0 ? 'pos' : 'neg'}">${fmt(v.saving)}</td>`)}</tr>`)}
        </tbody></table></div>`.s;
  };
  main().innerHTML = html`
    <h1>Offert och kalkyl</h1>
    <div class="split">
      <div id="res"></div>
      <div class="card">
        <h2>Antaganden</h2>
        <p class="small muted">Från modellen: ${c.auto.n_hangers} upphängningar, ${fmt(c.auto.pipe_meters, 1)} rörmeter. Standardvärden är platshållare.</p>
        <form id="cf" class="grid">
          ${Object.keys(labels).map((k) => html`<label class="f">${labels[k]}<input name="${k}" type="number" step="any" value="${inputs[k]}"></label>`)}
          <button type="button" id="reauto">Hämta antal & rörmeter från modell</button>
          ${can('kalkyl.skapa') ? html`<label class="small"><input type="checkbox" name="fixed" ${can('offert.godkann') ? '' : raw('disabled')}> Fast offert (låser märkningen “räkneexempel”)</label><button class="primary">Spara kalkyl</button>` : ''}
        </form>
        <h3>Rördimensioner</h3>
        <table class="small"><tbody>${Object.entries(c.dimensions).map(([k, v]) => html`<tr><td>${k}</td><td class="num">${fmt(v, 1)} m</td></tr>`)}</tbody></table>
      </div>
    </div>
    <div id="normtid"></div>`.s;
  render(c.result);
  normtidCard(pid).then((h) => {
    $('#normtid').innerHTML = h.s;
    $$('[data-normtid]').forEach((b) => b.addEventListener('click', () => {
      $('#cf [name=normtid_sh_h_per_hanger]').value = b.dataset.normtid;
      $('#cf').dispatchEvent(new Event('input'));
      toast('Normtiden är ifylld – spara kalkylen för att behålla den');
    }));
  }).catch(() => {});
  const readForm = () => { const f = new FormData($('#cf')); for (const k of Object.keys(labels)) inputs[k] = Number(f.get(k)); return inputs; };
  let tmo;
  $('#cf').addEventListener('input', () => { clearTimeout(tmo); tmo = setTimeout(async () => { const r = await api(`/api/projects/${pid}/calc?preview=true`, { body: { inputs: readForm() } }); render(r.result); }, 250); });
  $('#reauto').addEventListener('click', () => { $('#cf [name=n_hangers]').value = c.auto.n_hangers; $('#cf [name=pipe_meters]').value = c.auto.pipe_meters; $('#cf').dispatchEvent(new Event('input')); });
  $('#cf').addEventListener('submit', async (e) => {
    e.preventDefault();
    const isFixed = !!new FormData(e.target).get('fixed');
    await act(() => api(`/api/projects/${pid}/calc`, { body: { inputs: readForm(), is_fixed_offer: isFixed } }), 'Kalkyl sparad');
    viewCalc(pid);
  });
  void p;
}

// ---------------------------------------------------------------------------
// Logg
// ---------------------------------------------------------------------------
async function viewLog(pid) {
  await loadProject(pid);
  const ev = await api(`/api/projects/${pid}/events`);
  main().innerHTML = html`<h1>Ändringslogg</h1><div class="card"><table class="small"><thead><tr><th>Tid</th><th>Användare</th><th>Roll</th><th>Händelse</th><th>Hanger</th><th>Detaljer</th></tr></thead><tbody>
    ${ev.map((e) => html`<tr><td>${dt(e.ts)}</td><td>${e.user}</td><td>${state.meta.roles[e.role] || e.role}</td><td><b>${e.action}</b></td><td>${e.hanger ? html`<a href="#/p/${pid}/h/${e.hanger}">${e.hanger}</a>` : ''}</td><td class="mono" style="font-size:11px;max-width:420px;word-break:break-word">${Object.keys(e.details || {}).length ? JSON.stringify(e.details) : ''}${e.photo ? html` <a href="${e.photo}" target="_blank">foto</a>` : ''}</td></tr>`)}
  </tbody></table></div>`.s;
}

// ---------------------------------------------------------------------------
// Regelbibliotek
// ---------------------------------------------------------------------------
async function viewRules(rid) {
  const list = await api('/api/rulesets');
  const sel = rid ? Number(rid) : list[0]?.id;
  const rs = await api(`/api/rulesets/${sel}`);
  const c = rs.content;
  const editable = rs.status === 'utkast' && can('regler.redigera');
  main().innerHTML = html`
    <div class="row"><h1>Regelbibliotek</h1><span class="spacer"></span>
      <select id="rs">${list.map((r) => html`<option value="${r.id}" ${r.id === sel ? raw('selected') : ''}>${r.label} – ${r.status}</option>`)}</select></div>
    ${rs.status !== 'verifierad' ? html`<div class="banner warn">${c.meta?.description || 'Ej verifierad.'}</div>` : html`<div class="banner ok">Verifierad av ${rs.verified_by} ${dt(rs.verified_at)}.</div>`}
    <div class="grid g2">
      <div class="card">
        <h2>${rs.label} <span class="chip">${rs.status}</span></h2>
        <p class="small muted">Skapad av ${rs.created_by} ${dt(rs.created_at)}${rs.parent_id ? ` · baserad på #${rs.parent_id}` : ''}</p>
        ${rs.comment ? html`<p class="small" style="white-space:pre-wrap">${rs.comment}</p>` : ''}
        <div class="row">
          ${can('regler.redigera') ? html`<button id="draft">Ny version (utkast) från denna</button>` : ''}
          ${editable ? html`<button class="primary" id="save">Spara utkast</button><button id="pub">Publicera</button>` : ''}
          ${can('regler.verifiera') && rs.status === 'publicerad' ? html`<button class="primary" id="ver">Verifiera (tekniskt ansvarig)</button>` : ''}
        </div>
        <p class="small muted">Publicerade versioner är låsta. Varje SmartHanger lagrar vilken regelversion som använts.</p>
        <h3>Klamringsavstånd (mm)</h3>
        <table class="small"><thead><tr><th>Material</th><th>Ø från–till</th><th class="num">Horisontellt</th><th class="num">Vertikalt</th></tr></thead><tbody>
          ${c.spacing.table.map((r) => html`<tr><td>${r.material}</td><td>${r.od_min}–${r.od_max}</td><td class="num">${r.horizontal}</td><td class="num">${r.vertical ?? ''}</td></tr>`)}
        </tbody></table>
        <h3>Placering</h3>
        <dl class="kv small">${Object.entries(c.placement).filter(([k]) => k !== 'description').map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>
        <h3>Hangergeometri</h3>
        <dl class="kv small">${Object.entries(c.geometry).filter(([k]) => k !== 'description').map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>
      </div>
      <div class="card">
        <h2>Regeldokument (JSON)</h2>
        <p class="small muted">Regelområden: klamringsavstånd, placering, last, hangergeometri, infästning, miljö, tolerans samt artikelregister.</p>
        <textarea id="json" rows="40" ${editable ? '' : raw('readonly')}>${JSON.stringify(c, null, 2)}</textarea>
      </div>
    </div>
    ${datasheetCard(sel)}`.s;
  bindDatasheet(sel);
  $('#rs').onchange = (e) => { location.hash = `#/rules/${e.target.value}`; };
  const parse = () => { try { return JSON.parse($('#json').value); } catch (e) { toast('Ogiltig JSON: ' + e.message, true); throw e; } };
  $('#draft')?.addEventListener('click', async () => {
    const f = await ask('Ny regelversion', { fields: [{ name: 'comment', label: 'Ändringskommentar' }], ok: 'Skapa utkast' });
    if (!f) return;
    const d = await act(() => api(`/api/rulesets/${sel}/draft`, { body: { content: c, comment: f.comment } }), 'Utkast skapat');
    location.hash = `#/rules/${d.id}`;
  });
  $('#save')?.addEventListener('click', async () => { await act(() => api(`/api/rulesets/${sel}`, { method: 'PUT', body: { content: parse() } }), 'Utkast sparat'); viewRules(sel); });
  $('#pub')?.addEventListener('click', async () => {
    await act(() => api(`/api/rulesets/${sel}`, { method: 'PUT', body: { content: parse() } }));
    await act(() => api(`/api/rulesets/${sel}/publish`, { method: 'POST' }), 'Publicerad');
    viewRules(sel);
  });
  $('#ver')?.addEventListener('click', async () => {
    const f = await ask('Verifiera regelversion', { fields: [{ name: 'comment', label: 'Underlag för verifiering (t.ex. AMA-referens, beräkning, tillverkardata)', type: 'textarea' }], ok: 'Verifiera' });
    if (!f?.comment) { if (f) toast('Ange underlag för verifieringen.', true); return; }
    await act(() => api(`/api/rulesets/${sel}/verify`, { body: { comment: f.comment } }), 'Verifierad');
    viewRules(sel);
  });
}

// ---------------------------------------------------------------------------
(async function start() {
  state.meta = await api('/api/meta');
  window.addEventListener('hashchange', route);
  route();
})();
