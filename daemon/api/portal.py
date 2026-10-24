"""The page guests see when they join the hotspot with the captive portal on."""

import html
from typing import Any

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Join __SSID__</title>
<style>
:root{--ground:#f3f5f1;--surface:#fff;--ink:#1f1511;--ink2:#4a423e;--accent:#58ba4f;--on:#1f1511;--link:#1f6b1c;--focus:#0b57d0}
@media(prefers-color-scheme:dark){:root{--ground:#0e1310;--surface:#161c18;--ink:#eef3ec;--ink2:#a9b5a8;--accent:#6fd465;--on:#0e1310;--link:#7fdc76;--focus:#86bbff}}
@media(prefers-contrast:more){:root{--ground:#000;--surface:#000;--ink:#fff;--ink2:#fff;--accent:#7cff72;--on:#000;--link:#7cff72}}
*{box-sizing:border-box;margin:0}
body{font:1.125rem/1.55 'Atkinson Hyperlegible',system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--ground);color:var(--ink);min-height:100dvh;display:grid;place-items:center;padding:1.25rem}
main{width:min(100%,28rem);background:var(--surface);padding:2rem 1.5rem;display:flex;flex-direction:column;gap:1.25rem}
img{width:56px;height:56px}
h1{font-size:2rem;line-height:1.05;letter-spacing:-.035em}
p{color:var(--ink2)}
button{min-height:3.5rem;border:0;border-radius:999px;background:var(--accent);color:var(--on);font:inherit;font-weight:700;font-size:1.2rem;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}
a{color:var(--link)}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
ul{padding-left:1.2rem}
.ok{color:var(--ink);font-weight:700}
.err{color:#b3141b;font-weight:700}
@media(prefers-color-scheme:dark){.err{color:#ff9a9c}}
footer{font-size:.9375rem;color:var(--ink2)}
</style>
</head>
<body>
<main>
  <img src="/favicon.svg" alt="">
  <h1>Welcome to __SSID__</h1>
  <p>__MESSAGE__</p>
  <button id="go" type="button">Get online</button>
  <p id="status" role="status" aria-live="polite"></p>
  __EXTRAS__
  <footer>Shared with NetCast.</footer>
</main>
<script>
const go = document.getElementById('go'), out = document.getElementById('status');
go.addEventListener('click', async () => {
  go.disabled = true; out.className = ''; out.textContent = 'Connecting…';
  try {
    const r = await fetch('/portal/authorize', { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.authorized) {
      out.className = 'ok'; out.textContent = 'You are online. You can close this page.';
      go.hidden = true;
    } else { throw new Error(d.error || 'Something went wrong.'); }
  } catch (e) {
    out.className = 'err'; out.textContent = e.message + ' Try again.'; go.disabled = false;
  }
});
</script>
</body>
</html>
"""


def render_portal(state: Any) -> str:
    cfg = getattr(state, "config", {}) or {}
    cp = cfg.get("captive_portal", {}) if isinstance(cfg, dict) else {}
    message = cp.get("message") or "Tap the button to get online."
    ssid = ""
    status = getattr(state, "_active_config", None)
    if status is not None:
        ssid = getattr(status, "ssid", "") or ""
    ssid = ssid or (cfg.get("hotspot", {}).get("ssid", "") if isinstance(cfg, dict) else "") or "this network"

    links = []
    get_links = getattr(state, "portal_links", None)
    if callable(get_links):
        links = get_links()
    extras = ""
    if links:
        items = "".join(
            f'<li><a href="{html.escape(l["url"], quote=True)}">{html.escape(l["label"])}</a></li>' for l in links
        )
        extras = f"<section aria-label=\"Extras\"><p>Optional:</p><ul>{items}</ul></section>"

    return (
        _TEMPLATE.replace("__SSID__", html.escape(ssid))
        .replace("__MESSAGE__", html.escape(message))
        .replace("__EXTRAS__", extras)
    )
