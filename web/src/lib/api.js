/** NetCast API client — REST + WebSocket */

const BASE = '';
let token = '';

export async function fetchToken() {
  try {
    const r = await fetch('/api/token');
    if (r.ok) token = (await r.json()).token || '';
  } catch { /* portal mode — no token needed */ }
}

function headers() {
  const h = { 'Content-Type': 'application/json' };
  if (token) h['X-NetCast-Token'] = token;
  return h;
}

async function api(method, path, body) {
  const opts = { method, headers: headers() };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(BASE + path, opts);
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// --- Adapters: server schema <-> what the pages expect -----------------------

const VIRTUAL = /^(lo|docker|br-|br\d|veth|virbr|tun|tap|wg|tailscale|ifb)/;
const USB = /^(usb|rndis|enx|enp\w+u\w+)/;

function groupInterfaces(list) {
  const out = { wifi: [], ethernet: [], usb: [] };
  for (const i of list) {
    if (VIRTUAL.test(i.name)) continue;
    if (i.is_wireless) out.wifi.push(i.name);
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
      status: c.passed ? 'pass' : c.required ? 'fail' : 'warn',
      message: c.message || '',
      fix_hint: c.fix_hint || '',
    })),
  };
}

function flattenConfig(c) {
  return {
    ssid: c.hotspot?.ssid,
    password: c.hotspot?.password,
    band: c.hotspot?.band,
    channel: c.hotspot?.channel === 'auto' ? 0 : c.hotspot?.channel,
    fair_share_enabled: c.fair_share?.enabled,
    total_bandwidth_kbps: c.fair_share?.total_bandwidth_kbps,
    captive_portal_enabled: c.captive_portal?.enabled,
    captive_portal_message: c.captive_portal?.message,
  };
}

function nestConfig(f) {
  return {
    hotspot: { ssid: f.ssid, password: f.password, band: f.band, channel: Number(f.channel) || 0 },
    fair_share: {
      enabled: !!f.fair_share_enabled,
      total_bandwidth_kbps: Number(f.total_bandwidth_kbps) || 0,
    },
    captive_portal: {
      enabled: !!f.captive_portal_enabled,
      message: f.captive_portal_message ?? '',
    },
  };
}

export const getStatus = () => api('GET', '/api/status');
export const getClients = () => api('GET', '/api/clients');
export const getInterfaces = async () => groupInterfaces(await api('GET', '/api/interfaces'));
export const getPreflight = async () => adaptPreflight(await api('GET', '/api/preflight'));
export const getSkins = () => api('GET', '/api/skins');
export const getConfig = async () => flattenConfig(await api('GET', '/api/config'));
export const getQR = () => fetch('/api/qr', { headers: headers() }).then((r) => r.text());
export const startHotspot = (cfg) => api('POST', '/api/hotspot/start', cfg);
export const stopHotspot = () => api('POST', '/api/hotspot/stop');
export const updateConfig = async (cfg) => flattenConfig(await api('PUT', '/api/config', nestConfig(cfg)));

/** WebSocket for real-time events */
export function connectWS(onEvent) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/ws?token=${encodeURIComponent(token)}`;
  let ws;
  let retryDelay = 1000;
  let closed = false;

  function connect() {
    ws = new WebSocket(url);
    ws.onopen = () => { retryDelay = 1000; };
    ws.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)); } catch {}
    };
    ws.onclose = () => {
      if (closed) return;
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    };
  }
  connect();
  return () => { closed = true; ws?.close(); };
}
