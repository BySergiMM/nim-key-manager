'use strict';
/*
 * NIM Key Manager dashboard.
 *
 * Everything the API returns (key names, project names and descriptions, audit actors,
 * IP addresses...) is written by users or by remote clients, so it is data, never markup:
 * it only reaches the page through textContent, element properties and `new Option()`.
 * Do not build HTML from strings in this file, and do not add inline handlers to
 * index.html: the page is served with a Content-Security-Policy that forbids both.
 */

const TOKEN_KEY = 'nkm_token';
const VIEWS = ['overview', 'keys', 'projects', 'audit'];
// Values the API may return for a key status; anything else gets no colour class.
const STATUS_CLASSES = new Set(['active', 'expired', 'invalid', 'revoked']);

let token = sessionStorage.getItem(TOKEN_KEY) || null;
let toastTimer = null;

function el(id) { return document.getElementById(id); }

/** Create an element. `attrs` supports only `class`, `colspan` and `click` (a listener). */
function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (name === 'class') node.className = value;
    else if (name === 'colspan') node.colSpan = value;
    else if (name === 'click') node.addEventListener('click', value);
    else throw new Error('unsupported attribute: ' + name);
  }
  // Strings become Text nodes (never parsed as markup); Nodes are appended as they are.
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

function toast(text, isErr) {
  const m = el('msg');
  m.textContent = text;
  m.className = isErr ? 'show err' : 'show';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { m.className = ''; }, 4000);
}

function fmt(d) { return d ? new Date(d).toLocaleString() : '—'; }

function statusClass(status) { return STATUS_CLASSES.has(status) ? status : ''; }

/** Replace a table body; an empty list shows `emptyText` (or nothing when it is null). */
function fillBody(tableId, rows, emptyText, columns) {
  const tbody = el(tableId).querySelector('tbody');
  if (rows.length || emptyText === null) {
    tbody.replaceChildren(...rows);
  } else {
    tbody.replaceChildren(h('tr', null, h('td', { class: 'muted', colspan: columns }, emptyText)));
  }
}

async function api(path, opts = {}) {
  opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
  if (token) opts.headers['Authorization'] = 'Bearer ' + token;
  const res = await fetch(path, opts);
  if (res.status === 401 && path !== '/api/v1/auth/login') { logout(); throw new Error('sesión expirada'); }
  if (!res.ok) {
    // HTTP/2 has no reason phrase, so statusText can be empty.
    let detail = res.statusText || ('HTTP ' + res.status);
    try {
      const body = await res.json();
      // The API answers {"detail": ...}; the rate limiter (429) answers {"error": ...}.
      const message = body.detail !== undefined ? body.detail : body.error;
      if (message !== undefined) {
        detail = typeof message === 'string' ? message : JSON.stringify(message);
      }
    } catch (e) { /* the body was not JSON: keep the status text */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

async function login() {
  try {
    const data = await api('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email: el('lemail').value, password: el('lpass').value }),
    });
    token = data.access_token;
    sessionStorage.setItem(TOKEN_KEY, token);
    await boot();
  } catch (e) { toast('Login: ' + e.message, true); }
}

function logout() {
  token = null;
  sessionStorage.removeItem(TOKEN_KEY);
  el('app').classList.add('hidden');
  el('who').classList.add('hidden');
  el('login').classList.remove('hidden');
}

async function boot() {
  try {
    const me = await api('/api/v1/auth/me');
    el('whoami').textContent = me.email + ' (' + me.role + ')';
    el('login').classList.add('hidden');
    el('app').classList.remove('hidden');
    el('who').classList.remove('hidden');
    await Promise.all([loadOverview(), loadKeys(), loadProjects(), loadAudit()]);
  } catch (e) { logout(); }
}

function show(name) {
  for (const v of VIEWS) {
    el('view-' + v).classList.toggle('hidden', v !== name);
    el('tab-' + v).classList.toggle('on', v === name);
  }
}

async function loadOverview() {
  const s = await api('/api/v1/stats/overview');
  const by = s.keys_by_status || {};
  const cards = [
    ['Keys totales', s.total_keys], ['Activas', by.active || 0], ['Revocadas', by.revoked || 0],
    ['Expiradas', by.expired || 0], ['Expiran pronto', s.keys_expiring_soon],
    ['Dispensaciones', s.total_dispenses], ['Proyectos', s.total_projects], ['Usuarios', s.total_users],
  ];
  el('cards').replaceChildren(...cards.map(([label, value]) =>
    h('div', { class: 'card' }, h('div', { class: 'num' }, String(value)), h('div', { class: 'lbl' }, label))));

  const usage = await api('/api/v1/stats/usage?days=30');
  fillBody('usage', usage.map((u) => h('tr', null, h('td', null, String(u.date)), h('td', null, String(u.count)))),
    'Sin uso registrado', 2);
}

function keyRow(k) {
  const id = encodeURIComponent(k.id);
  const actions = [h('button', { class: 'secondary', click: () => validateKey(id) }, 'Validar')];
  if (k.status === 'active') actions.push(h('button', { class: 'secondary', click: () => rotateKey(id) }, 'Rotar'));
  if (k.status !== 'revoked') actions.push(h('button', { class: 'danger', click: () => revokeKey(id) }, 'Revocar'));
  const state = [h('span', { class: ('badge ' + statusClass(k.status)).trim() }, String(k.status))];
  if (k.expiring_soon) state.push(' ', h('span', { class: 'badge warn' }, 'expira pronto'));
  return h('tr', null,
    h('td', null, String(k.name)),
    h('td', null, h('code', null, String(k.key_hint))),
    h('td', null, ...state),
    h('td', null, String(k.usage_count)),
    h('td', null, fmt(k.expires_at)),
    h('td', null, fmt(k.last_validated_at)),
    h('td', { class: 'row' }, ...actions));
}

async function loadKeys() {
  const keys = await api('/api/v1/keys');
  fillBody('keys', keys.map(keyRow), 'No hay keys registradas', 7);
}

async function createKey() {
  try {
    const body = { name: el('kname').value, api_key: el('kvalue').value, validate_remote: true };
    if (el('kproject').value) body.project_id = el('kproject').value;
    if (el('kexp').value) body.expires_at = new Date(el('kexp').value + 'T23:59:59Z').toISOString();
    await api('/api/v1/keys', { method: 'POST', body: JSON.stringify(body) });
    el('kname').value = el('kvalue').value = el('kexp').value = '';
    toast('Key registrada y validada contra NVIDIA');
    await Promise.all([loadKeys(), loadOverview()]);
  } catch (e) { toast(e.message, true); }
}

async function validateKey(id) {
  try {
    const r = await api('/api/v1/keys/' + id + '/validate', { method: 'POST' });
    toast('Resultado: ' + r.result);
    await loadKeys();
  } catch (e) { toast(e.message, true); }
}

async function rotateKey(id) {
  const nk = prompt('Pega la NUEVA API key creada en build.nvidia.com/settings/api-keys.\nLa key antigua quedará revocada:');
  if (!nk) return;
  try {
    await api('/api/v1/keys/' + id + '/rotate', { method: 'POST', body: JSON.stringify({ api_key: nk }) });
    toast('Rotación completada');
    await Promise.all([loadKeys(), loadOverview()]);
  } catch (e) { toast(e.message, true); }
}

async function revokeKey(id) {
  if (!confirm('¿Revocar esta key? Dejará de dispensarse.')) return;
  try {
    await api('/api/v1/keys/' + id + '/revoke', { method: 'POST' });
    toast('Key revocada');
    await Promise.all([loadKeys(), loadOverview()]);
  } catch (e) { toast(e.message, true); }
}

function projectRow(p) {
  const id = encodeURIComponent(p.id);
  return h('tr', null,
    h('td', null, String(p.name)),
    h('td', null, p.description ? String(p.description) : '—'),
    h('td', null, fmt(p.created_at)),
    h('td', null, h('button', { class: 'danger', click: () => deleteProject(id) }, 'Eliminar')));
}

async function loadProjects() {
  const projects = await api('/api/v1/projects');
  el('kproject').replaceChildren(new Option('— ninguno —', ''), ...projects.map((p) => new Option(String(p.name), p.id)));
  fillBody('projects', projects.map(projectRow), 'No hay proyectos', 4);
}

async function createProject() {
  try {
    await api('/api/v1/projects', {
      method: 'POST',
      body: JSON.stringify({ name: el('pname').value, description: el('pdesc').value || null }),
    });
    el('pname').value = el('pdesc').value = '';
    toast('Proyecto creado');
    await loadProjects();
  } catch (e) { toast(e.message, true); }
}

async function deleteProject(id) {
  if (!confirm('¿Eliminar proyecto? Sus keys quedarán sin asignar.')) return;
  try {
    await api('/api/v1/projects/' + id, { method: 'DELETE' });
    toast('Proyecto eliminado');
    await loadProjects();
  } catch (e) { toast(e.message, true); }
}

function auditRow(a) {
  const resource = (a.resource_type || '') + ' ' + (a.resource_id ? String(a.resource_id).slice(0, 8) : '');
  return h('tr', null,
    h('td', null, fmt(a.created_at)),
    h('td', null, a.actor_email ? String(a.actor_email) : 'sistema'),
    h('td', null, String(a.action)),
    h('td', null, resource),
    h('td', null, a.ip_address ? String(a.ip_address) : '—'));
}

async function loadAudit() {
  try {
    const entries = await api('/api/v1/audit?limit=100');
    fillBody('audit', entries.map(auditRow), null, 5);
  } catch (e) {
    fillBody('audit', [], 'Solo visible para administradores', 5);
  }
}

function init() {
  el('btn-login').addEventListener('click', login);
  el('btn-logout').addEventListener('click', logout);
  for (const v of VIEWS) el('tab-' + v).addEventListener('click', () => show(v));
  el('btn-create-key').addEventListener('click', createKey);
  el('btn-create-project').addEventListener('click', createProject);
  if (token) boot();
}

init();
