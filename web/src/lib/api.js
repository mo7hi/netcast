/** NetCast API client — REST + WebSocket */

const BASE = '';
let token = '';

export async function fetchToken() {
  try {
    const r = await fetch('/api/token');
    const d = await r.json();
    token = d.token || '';
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

export const getStatus = () => api('GET', '/api/status');
export const getClients = () => api('GET', '/api/clients');
export const getInterfaces = () => api('GET', '/api/interfaces');
export const getPreflight = () => api('GET', '/api/preflight');
export const getSkins = () => api('GET', '/api/skins');
export const getConfig = () => api('GET', '/api/config');
export const getQR = () => fetch('/api/qr', { headers: headers() }).then(r => r.text());
export const startHotspot = (cfg) => api('POST', '/api/hotspot/start', cfg);
export const stopHotspot = () => api('POST', '/api/hotspot/stop');
export const updateConfig = (cfg) => api('PUT', '/api/config', cfg);

/** WebSocket for real-time events */
export function connectWS(onEvent) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/ws`;
  let ws;
  let retryDelay = 1000;

  function connect() {
    ws = new WebSocket(url);
    ws.onopen = () => { retryDelay = 1000; };
    ws.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)); } catch {}
    };
    ws.onclose = () => {
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    };
  }
  connect();
  return () => ws?.close();
}
