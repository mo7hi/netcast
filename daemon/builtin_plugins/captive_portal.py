# BSD-2-Clause License
# Copyright (c) 2024 NetCast Contributors
"""Captive Portal Plugin — Landing page + client authorization.

Implements captive portal functionality:
1. Configures dnsmasq DNS hijacking
2. Sets up nftables redirect rules
3. Handles client authorization via nftables sets with timeouts
4. Serves OS probe URLs to trigger native captive portal popups
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from daemon.backends.base import HotspotConfig

from daemon.hooks import hookimpl

logger = logging.getLogger("netcast.plugins.captive_portal")

# How long an authorized client stays authorized (seconds)
AUTH_TIMEOUT = 7200  # 2 hours


class CaptivePortalPlugin:
    """Captive portal with DNS hijacking and native OS popup support."""

    def __init__(self) -> None:
        self._enabled: bool = False
        self._gateway_ip: str = ""
        self._web_port: int = 8384
        self._message: str = "Welcome to NetCast. Tap 'Connect' to get online."

    @hookimpl
    def register_api_routes(self, app: web.Application) -> None:
        """Register captive portal routes (auth-exempt)."""
        app.router.add_get("/portal", self._portal_page)
        app.router.add_post("/portal/authorize", self._authorize)

        # OS probe URLs — redirect to portal
        for probe_path in [
            "/generate_204",              # Android
            "/hotspot-detect.html",       # Apple / macOS
            "/connecttest.txt",           # Windows
            "/canonical.html",            # Firefox
            "/success.txt",               # Firefox
            "/ncsi.txt",                  # Windows NCSI
            "/check_network_status.txt",  # Chromium
        ]:
            app.router.add_get(probe_path, self._probe_redirect)

    @hookimpl
    def on_hotspot_start(self, config: HotspotConfig, status: dict) -> None:
        """Enable captive portal if configured."""
        if not getattr(config, 'captive_portal_enabled', True):
            return

        self._enabled = True
        self._gateway_ip = status.get("gateway_ip", "192.168.12.1")
        self._web_port = getattr(config, 'listen_port', 8384)
        self._message = getattr(config, 'captive_portal_message', self._message)

        logger.info(f"Captive Portal: Enabled at {self._gateway_ip}:{self._web_port}/portal")

    @hookimpl
    def on_hotspot_stop(self, reason: str) -> None:
        """Disable captive portal."""
        self._enabled = False

    @hookimpl
    def modify_captive_portal(self, portal_config: dict) -> None:
        """Allow other plugins to modify portal settings."""
        if "message" in portal_config:
            self._message = portal_config["message"]

    async def _portal_page(self, request: web.Request) -> web.Response:
        """Serve the captive portal landing page."""
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NetCast — Connect</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
      background: linear-gradient(135deg, #0f0f23, #1a1a3e, #2d1b69);
      color: #e8e8f0;
    }}
    .card {{
      max-width: 400px; width: 90%; padding: 2.5rem 2rem; text-align: center;
      background: rgba(255,255,255,0.04); backdrop-filter: blur(20px);
      border: 1px solid rgba(255,255,255,0.08); border-radius: 24px;
      box-shadow: 0 24px 80px rgba(0,0,0,0.5);
    }}
    .logo {{ font-size: 3rem; margin-bottom: 0.75rem; }}
    h1 {{ font-size: 1.5rem; font-weight: 800; margin-bottom: 0.75rem; }}
    .msg {{ color: #9a9abe; font-size: 0.9rem; margin-bottom: 2rem; line-height: 1.5; }}
    .btn {{
      display: block; width: 100%; padding: 1rem; border: none; border-radius: 14px;
      background: linear-gradient(135deg, #00d4ff, #a855f7); color: #fff;
      font-size: 1.1rem; font-weight: 700; cursor: pointer;
      box-shadow: 0 8px 32px rgba(0,212,255,0.3);
    }}
    .btn:active {{ transform: scale(0.98); }}
    .footer {{ margin-top: 1.5rem; font-size: 0.72rem; color: #4a4a6a; }}
    .ok {{ color: #00e88a; font-size: 1.2rem; font-weight: 700; padding: 2rem 0; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">⚡</div>
    <h1>Welcome to NetCast</h1>
    <p class="msg">{self._message}</p>
    <div id="content">
      <button class="btn" onclick="authorize()">🌐 Connect to Internet</button>
    </div>
    <p class="footer">Powered by NetCast — The VLC of Software Routers</p>
  </div>
  <script>
    async function authorize() {{
      try {{
        const r = await fetch('/portal/authorize', {{ method: 'POST' }});
        if (r.ok) {{
          document.getElementById('content').innerHTML = '<p class="ok">✓ Connected! You\\'re online.</p>';
          setTimeout(() => window.location.href = 'http://www.google.com', 1500);
        }}
      }} catch(e) {{ alert('Error: ' + e.message); }}
    }}
  </script>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def _authorize(self, request: web.Request) -> web.Response:
        """Authorize a client by adding their IP to the nftables allowed set."""
        client_ip = request.remote
        if not client_ip:
            return web.json_response({"error": "Cannot determine client IP"}, status=400)

        try:
            proc = await asyncio.create_subprocess_exec(
                "nft", "add", "element", "inet", "netcast_portal",
                "authenticated_clients", f"{{ {client_ip} timeout {AUTH_TIMEOUT}s }}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(f"Captive Portal: Failed to authorize {client_ip}: {stderr.decode()}")
                return web.json_response({"error": "Authorization failed"}, status=500)

            logger.info(f"Captive Portal: Authorized {client_ip} for {AUTH_TIMEOUT}s")
            return web.json_response({"status": "authorized"})

        except Exception as e:
            logger.error(f"Captive Portal: Error authorizing {client_ip}: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _probe_redirect(self, request: web.Request) -> web.Response:
        """Redirect OS captive portal probes to the portal page."""
        portal_url = f"http://{self._gateway_ip}:{self._web_port}/portal"
        raise web.HTTPFound(portal_url)
