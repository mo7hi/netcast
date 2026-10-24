export function bytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function rate(kbps) {
  if (kbps == null) return '–';
  return kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${Math.round(kbps)} kbps`;
}

export function bandName(b) {
  return b === 'a' || b === '5GHz' || b === '5' ? '5 GHz' : '2.4 GHz';
}

export function randomPassword(len = 12) {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789';
  const buf = new Uint32Array(len);
  crypto.getRandomValues(buf);
  return Array.from(buf, (n) => chars[n % chars.length]).join('');
}

export async function copy(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch {
    const el = document.createElement('textarea');
    el.value = text; document.body.appendChild(el); el.select();
    const ok = document.execCommand('copy'); el.remove(); return ok;
  }
}

/** Make a server-generated SVG scale to its container (segno omits the viewBox). */
export function fitSvg(svg) {
  if (!svg || /viewBox=/.test(svg)) return svg;
  const m = svg.match(/<svg[^>]*?\swidth="([\d.]+)"[^>]*?\sheight="([\d.]+)"/);
  if (!m) return svg;
  return svg.replace('<svg', `<svg viewBox="0 0 ${m[1]} ${m[2]}"`);
}
