/** NetCast API client: REST + WebSocket */

import { fitSvg } from './format.js';

let token = '';
export const getToken = () => token;

/** Loopback UIs get the master token; remote UIs reuse a saved login session. */
export async function fetchToken() {
  try {
    const r = await fetch('/api/token');
    if (r.ok) {
      token = (await r.json()).token || '';
      if (token) return true;
    }
  } catch { /* ignore */ }
  const saved = sessionStorage.getItem('netcast-token');
  if (saved) {
    token = saved;
    try { await api('GET', '/api/status'); return true; }
    catch { token = ''; sessionStorage.removeItem('netcast-token'); }
  }
  return false;
}

export const authStatus = () => fetch('/api/auth/status').then((r) => r.json());

export async function login(password) {
  const r = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `Sign-in failed (${r.status})`);
  token = d.token;
  sessionStorage.setItem('netcast-token', token);
}

function headers() {
  const h = { 'Content-Type': 'application/json' };
  if (token) h['X-NetCast-Token'] = token;
  return h;
}

async function api(method, path, body) {
  const opts = { method, headers: headers() };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).error || msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

const text = (path) => fetch(path, { headers: headers() }).then((r) => r.text());

// --- Adapters ---------------------------------------------------------------

const HIDDEN = /^(lo|docker|br-|br\d|veth|virbr|tap|ifb|netcast|ppp0$)/;
const CELLULAR = /^(wwan|wwp|rmnet|ccmni)/;
const VPN = /^(wg|tun|tailscale|zt|nordlynx|proton)/;
const USB = /^(usb|rndis|enx|enp\w+u\w+)/;

export function groupInterfaces(list) {
  const out = { wifi: [], ethernet: [], usb: [], cellular: [], vpn: [] };
  for (const i of list) {
    if (HIDDEN.test(i.name)) continue;
    if (i.is_wireless) out.wifi.push(i.name);
    else if (CELLULAR.test(i.name)) out.cellular.push(i.name);
    else if (VPN.test(i.name)) out.vpn.push(i.name);
    else if (USB.test(i.name)) out.usb.push(i.name);
    else if (i.type === 'ethernet') out.ethernet.push(i.name);
  }
  return out;
}

function adaptPreflight(r) {
  return {
    passed: !!r.passed,
    checks: (r.checks || []).map((c) => ({
      name: c.title || c.name,
      state: c.passed ? 'pass' : c.required ? 'fail' : 'warn',
      message: c.message || '',
      fix: c.fix_hint || '',
    })),
  };
}

export function flattenConfig(c) {
  return {
    ssid: c.hotspot?.ssid ?? '',
    password: c.hotspot?.password ?? '',
    band: c.hotspot?.band ?? 'bg',
    channel: c.hotspot?.channel === 'auto' ? 0 : (c.hotspot?.channel ?? 0),
    fair_share_enabled: !!c.fair_share?.enabled,
    total_bandwidth_kbps: c.fair_share?.total_bandwidth_kbps ?? 0,
    captive_portal_enabled: !!c.captive_portal?.enabled,
    captive_portal_message: c.captive_portal?.message ?? '',
  };
}

export function nestConfig(f) {
  return {
    hotspot: { ssid: f.ssid, password: f.password, band: f.band, channel: Number(f.channel) || 0 },
    fair_share: { enabled: !!f.fair_share_enabled, total_bandwidth_kbps: Number(f.total_bandwidth_kbps) || 0 },
    captive_portal: { enabled: !!f.captive_portal_enabled, message: f.captive_portal_message ?? '' },
  };
}

// --- Endpoints --------------------------------------------------------------

export const getStatus = () => api('GET', '/api/status');
export const getClients = () => api('GET', '/api/clients');
export const getBlocked = () => api('GET', '/api/blocked');
export const getPlatform = () => api('GET', '/api/platform');
export const getInterfaces = async () => groupInterfaces(await api('GET', '/api/interfaces'));
export const getPreflight = async (params = {}) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();
  return adaptPreflight(await api('GET', '/api/preflight' + (q ? `?${q}` : '')));
};
export const getConfig = async () => flattenConfig(await api('GET', '/api/config'));
export const saveConfig = async (f) => flattenConfig(await api('PUT', '/api/config', nestConfig(f)));
export const setPPPoE = (username, password) => api('PUT', '/api/config', { pppoe: { username, password } });
export const startHotspot = (cfg) => api('POST', '/api/hotspot/start', cfg);
export const stopHotspot = () => api('POST', '/api/hotspot/stop');
export const getQR = () => text('/api/qr').then(fitSvg);
export const getRemote = () => api('GET', '/api/remote');
export const getRemoteQR = (url) => text('/api/remote/qr?url=' + encodeURIComponent(url)).then(fitSvg);
export const setAdminPassword = (password) => api('PUT', '/api/admin-password', { password });
export const getPlugins = () => api('GET', '/api/plugins');
export const savePlugin = (id, enabled, values) => api('PUT', `/api/plugins/${encodeURIComponent(id)}`, { enabled, values });
export const kickClient = (mac) => api('POST', `/api/clients/${encodeURIComponent(mac)}/kick`);
export const blockClient = (mac, block) => api('POST', `/api/clients/${encodeURIComponent(mac)}/block`, { block });
export const getSkins = () => api('GET', '/api/skins');
export const getSkinCss = (id) => text(`/api/skins/${encodeURIComponent(id)}`);
export const getUiTabs = () => api('GET', '/api/ui/tabs');
export const getMonitor = () => api('GET', '/api/monitor/stats');

/** WebSocket for real-time events */
export function connectWS(onEvent) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/ws?token=${encodeURIComponent(token)}`;
  let ws;
  let retry = 1000;
  let closed = false;
  function connect() {
    ws = new WebSocket(url);
    ws.onopen = () => { retry = 1000; };
    ws.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch { /* ignore */ } };
    ws.onclose = () => {
      if (closed) return;
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 30000);
    };
  }
  connect();
  return () => { closed = true; ws?.close(); };
}
