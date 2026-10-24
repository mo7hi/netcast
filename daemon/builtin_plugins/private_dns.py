"""Private DNS plugin.

Encrypts every DNS lookup that leaves the hotspot, so the upstream network cannot see
which sites your devices visit. Two parts:

* A small local DNS-over-HTTPS forwarder. dnsmasq (which hands out addresses and
  answers devices) is pointed at it instead of at plain-text DNS.
* A guest page and an Apple configuration profile, so a phone can keep using the same
  private DNS after it leaves your hotspot.
"""
from __future__ import annotations

import asyncio
import html
import logging
import uuid
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import aiohttp
from aiohttp import web

from daemon.hooks import hookimpl

logger = logging.getLogger("netcast.plugins.private_dns")

PORT = 5354
# id: (label, DoH URL, DoT hostname for Android "Private DNS")
PROVIDERS: dict[str, tuple[str, str | None, str | None]] = {
    "cloudflare": ("Cloudflare (fast)", "https://cloudflare-dns.com/dns-query", "one.one.one.one"),
    "quad9": ("Quad9 (blocks malware)", "https://dns.quad9.net/dns-query", "dns.quad9.net"),
    "adguard": ("AdGuard (blocks ads)", "https://dns.adguard-dns.com/dns-query", "dns.adguard-dns.com"),
    "mullvad": ("Mullvad (no logging)", "https://dns.mullvad.net/dns-query", "dns.mullvad.net"),
    "nextdns": ("NextDNS (your own settings)", None, None),
    "custom": ("Your own DNS-over-HTTPS address", None, None),
}


def resolve_provider(values: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Return (label, DoH URL, DoT hostname) for the saved settings; URL is None if incomplete."""
    key = values.get("provider", "quad9")
    label, url, dot = PROVIDERS.get(key, PROVIDERS["quad9"])
    if key == "nextdns":
        nid = str(values.get("nextdns_id", "")).strip()
        if nid.isalnum():
            return label, f"https://dns.nextdns.io/{nid}", f"{nid}.dns.nextdns.io"
        return label, None, None
    if key == "custom":
        custom = str(values.get("custom_url", "")).strip()
        return label, (custom if custom.startswith("https://") else None), None
    return label, url, dot


def build_mobileconfig(name: str, doh_url: str) -> bytes:
    """Apple DNS-over-HTTPS configuration profile (iOS 14+, macOS 11+)."""
    ns = uuid.UUID("6f1d3b3a-5d8e-4c0a-9f55-0c4e7b9a2d11")
    inner, outer = uuid.uuid5(ns, "dns:" + doh_url), uuid.uuid5(ns, "profile:" + doh_url)
    n, u = xml_escape(name), xml_escape(doh_url)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>DNSSettings</key>
      <dict>
        <key>DNSProtocol</key><string>HTTPS</string>
        <key>ServerURL</key><string>{u}</string>
      </dict>
      <key>PayloadDescription</key><string>Encrypts DNS lookups with {n}.</string>
      <key>PayloadDisplayName</key><string>{n}</string>
      <key>PayloadIdentifier</key><string>net.netcast.dns.{inner}</string>
      <key>PayloadType</key><string>com.apple.dnsSettings.managed</string>
      <key>PayloadUUID</key><string>{str(inner).upper()}</string>
      <key>PayloadVersion</key><integer>1</integer>
    </dict>
  </array>
  <key>PayloadDescription</key><string>Private DNS from NetCast</string>
  <key>PayloadDisplayName</key><string>{n}</string>
  <key>PayloadIdentifier</key><string>net.netcast.profile.{outer}</string>
  <key>PayloadRemovalDisallowed</key><false/>
  <key>PayloadType</key><string>Configuration</string>
  <key>PayloadUUID</key><string>{str(outer).upper()}</string>
  <key>PayloadVersion</key><integer>1</integer>
</dict>
</plist>
""".encode("utf-8")


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, forwarder: "DohForwarder") -> None:
        self.forwarder = forwarder
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Any) -> None:
        if 12 <= len(data) <= 4096 and self.transport is not None:
            asyncio.get_running_loop().create_task(self.forwarder.handle(data, addr, self.transport))


class DohForwarder:
    """UDP DNS on 127.0.0.1 that forwards each query over HTTPS (fails closed)."""

    def __init__(self, url: str, port: int = PORT) -> None:
        self.url, self.port = url, port
        self.errors = 0
        self._transport: asyncio.DatagramTransport | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), local_addr=("127.0.0.1", self.port)
        )

    async def handle(self, query: bytes, addr: Any, transport: asyncio.DatagramTransport) -> None:
        assert self._session is not None
        try:
            async with self._session.post(
                self.url, data=query,
                headers={"Content-Type": "application/dns-message", "Accept": "application/dns-message"},
            ) as resp:
                if resp.status == 200:
                    body = await resp.content.read(65535)
                    transport.sendto(body, addr)
                else:
                    self.errors += 1
        except Exception as exc:  # network down, TLS error, timeout
            self.errors += 1
            logger.debug("DoH query failed: %s", type(exc).__name__)

    async def stop(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None
        if self._session:
            await self._session.close()
            self._session = None


class PrivateDNSPlugin:
    ID = "private_dns"

    def __init__(self) -> None:
        self.cfg: dict[str, Any] = {}
        self._fwd: DohForwarder | None = None
        self._gateway = "192.168.12.1"

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    # ------------------------------------------------------------- registration
    @hookimpl
    def register_plugin(self, plugins: list[dict[str, Any]]) -> None:
        plugins.append({
            "id": self.ID,
            "title": "Private DNS",
            "description": (
                "Encrypts every website lookup your devices make, so your internet provider "
                "or the café's network cannot see which sites you visit."
            ),
            "needs_restart": True,
            "fields": [
                {"key": "provider", "label": "Who answers the lookups", "type": "select", "default": "quad9",
                 "options": [{"value": k, "label": v[0]} for k, v in PROVIDERS.items()]},
                {"key": "nextdns_id", "label": "NextDNS configuration ID", "type": "text", "default": "",
                 "help": "The six-character ID from your NextDNS dashboard.",
                 "show_if": {"key": "provider", "value": "nextdns"}},
                {"key": "custom_url", "label": "DNS-over-HTTPS address", "type": "url", "default": "",
                 "help": "Starts with https://", "show_if": {"key": "provider", "value": "custom"}},
                {"key": "force_dns", "label": "Make every device use it, even if it has its own DNS set",
                 "type": "toggle", "default": True,
                 "help": "Redirects all DNS traffic from the hotspot to the encrypted resolver."},
            ],
            "links": self._links,
            "portal_links": self._portal_links,
        })

    @hookimpl
    def configure_plugin(self, plugin_id: str, values: dict[str, Any]) -> None:
        if plugin_id == self.ID:
            self.cfg = dict(values)

    # --------------------------------------------------------------- lifecycle
    @hookimpl
    def extend_dnsmasq(self, lines: list[str]) -> Any:
        return self._prepare(lines) if self.enabled else None

    async def _prepare(self, lines: list[str]) -> None:
        _, url, _ = resolve_provider(self.cfg)
        if not url:
            logger.warning("Private DNS is on but no provider address is set; using plain DNS")
            return
        if self._fwd is None or self._fwd.url != url:
            await self._stop()
            fwd = DohForwarder(url)
            await fwd.start()
            self._fwd = fwd
        lines += [f"server=127.0.0.1#{PORT}", "no-resolv"]

    @hookimpl
    def register_firewall_rules(self, firewall: Any, config: Any) -> None:
        if self.enabled and self.cfg.get("force_dns", True) and hasattr(firewall, "set_force_dns"):
            firewall.set_force_dns(True)

    @hookimpl
    def on_hotspot_start(self, config: Any, status: dict) -> None:
        self._gateway = status.get("gateway_ip") or self._gateway

    @hookimpl
    def on_hotspot_stop(self, reason: str) -> None:
        asyncio.get_running_loop().create_task(self._stop())

    async def _stop(self) -> None:
        if self._fwd is not None:
            await self._fwd.stop()
            self._fwd = None

    # ------------------------------------------------------------- guest links
    def _page_url(self) -> str:
        return f"http://{self._gateway}/portal/private-dns"

    def _links(self) -> list[dict[str, str]]:
        return [{"label": "Private DNS page for guests", "url": self._page_url(),
                 "help": "Share this with guests. It has one-tap setup for iPhone and steps for Android and Windows."}]

    def _portal_links(self) -> list[dict[str, str]]:
        return [{"label": "Set up private DNS on your device", "url": "/portal/private-dns"}] if self.enabled else []

    @hookimpl
    def register_api_routes(self, app: web.Application) -> None:
        app.router.add_get("/portal/private-dns", self._page)
        app.router.add_get("/portal/private-dns.mobileconfig", self._profile)

    async def _profile(self, request: web.Request) -> web.Response:
        label, url, _ = resolve_provider(self.cfg)
        if not self.enabled or not url:
            raise web.HTTPNotFound()
        return web.Response(
            body=build_mobileconfig(f"NetCast Private DNS ({label})", url),
            content_type="application/x-apple-aspen-config",
            headers={"Content-Disposition": 'attachment; filename="netcast-private-dns.mobileconfig"'},
        )

    async def _page(self, request: web.Request) -> web.Response:
        label, url, dot = resolve_provider(self.cfg)
        if not self.enabled or not url:
            raise web.HTTPNotFound()
        e = html.escape
        android = (
            f"<p>Open <strong>Settings → Network &amp; internet → Private DNS</strong>, choose "
            f"<em>Private DNS provider hostname</em> and enter <code>{e(dot)}</code>.</p>"
            if dot else "<p>This provider has no hostname for Android. Use a browser or app that supports DNS-over-HTTPS.</p>"
        )
        body = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Private DNS</title>
<style>
body{{font:18px/1.55 system-ui,sans-serif;max-width:40rem;margin:0 auto;padding:1.5rem;background:#f3f5f1;color:#1f1511}}
@media(prefers-color-scheme:dark){{body{{background:#0e1310;color:#eef3ec}}a{{color:#7fdc76}}}}
h1{{letter-spacing:-.03em}} code{{font-size:.95em;overflow-wrap:anywhere}}
a.btn{{display:inline-block;padding:.8rem 1.4rem;border-radius:999px;background:#58ba4f;color:#1f1511;font-weight:700;text-decoration:none}}
</style></head><body><main>
<h1>Private DNS</h1>
<p>This hotspot already encrypts your lookups with <strong>{e(label)}</strong>. To keep that protection when you leave, set it up on your device.</p>
<h2>iPhone, iPad and Mac</h2>
<p><a class="btn" href="/portal/private-dns.mobileconfig">Download the profile</a></p>
<p>Then open Settings and tap <em>Profile Downloaded</em> to install it.</p>
<h2>Android</h2>{android}
<h2>Windows 11</h2>
<p>Settings → Network &amp; internet → Wi-Fi → your network → DNS server assignment → Manual, then add the DNS-over-HTTPS address <code>{e(url)}</code>.</p>
</main></body></html>"""
        return web.Response(text=body, content_type="text/html")
