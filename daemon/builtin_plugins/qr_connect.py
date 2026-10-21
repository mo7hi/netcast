# BSD-2-Clause License
# Copyright (c) 2024 NetCast Contributors
"""QR Connect Plugin — Wi-Fi QR code generation using segno.

Generates QR codes in the standard WIFI: URI format that all modern
smartphones can scan to auto-connect to the hotspot.

Format: WIFI:T:WPA;S:<SSID>;P:<Password>;;
"""
from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

import segno
from aiohttp import web

if TYPE_CHECKING:
    from daemon.backends.base import HotspotConfig

from daemon.hooks import hookimpl

logger = logging.getLogger("netcast.plugins.qr_connect")


class QRConnectPlugin:
    """Wi-Fi QR code generation and serving."""

    def __init__(self) -> None:
        self._ssid: str = ""
        self._password: str = ""
        self._security: str = "WPA"
        self._svg_cache: str | None = None

    @hookimpl
    def register_api_routes(self, app: web.Application) -> None:
        """Register QR code API route."""
        app.router.add_get("/api/qr", self._serve_qr)
        app.router.add_get("/api/qr.png", self._serve_qr_png)
        app.router.add_get("/api/qr.svg", self._serve_qr)

    @hookimpl
    def on_hotspot_start(self, config: HotspotConfig, status: dict) -> None:
        """Cache QR code when hotspot starts."""
        self._ssid = status.get("ssid", "") or getattr(config, "ssid", "")
        self._password = status.get("password", "") or getattr(config, "password", "")
        self._svg_cache = None  # Invalidate cache
        logger.info(f"QR Connect: Ready for SSID '{self._ssid}'")

    @hookimpl
    def on_hotspot_stop(self, reason: str) -> None:
        """Clear cached QR code."""
        self._svg_cache = None

    def _generate_qr(self) -> segno.QRCode:
        """Generate a Wi-Fi QR code using segno's built-in helper."""
        return segno.helpers.make_wifi(
            ssid=self._ssid,
            password=self._password,
            security=self._security,
        )

    def _get_svg(self) -> str:
        """Get SVG string, using cache if available."""
        if self._svg_cache:
            return self._svg_cache

        if not self._ssid:
            return '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><text x="50%" y="50%" text-anchor="middle" fill="#888">No hotspot active</text></svg>'

        qr = self._generate_qr()
        buf = io.BytesIO()
        qr.save(
            buf,
            kind="svg",
            scale=8,
            dark="#e8e8f0",      # QR dots color (light for dark theme)
            light="#00000000",   # Transparent background
            xmldecl=False,
        )
        self._svg_cache = buf.getvalue().decode("utf-8")
        return self._svg_cache

    async def _serve_qr(self, request: web.Request) -> web.Response:
        """Serve QR code as SVG."""
        svg = self._get_svg()
        return web.Response(text=svg, content_type="image/svg+xml")

    async def _serve_qr_png(self, request: web.Request) -> web.Response:
        """Serve QR code as PNG."""
        if not self._ssid:
            return web.Response(status=404, text="No hotspot active")

        qr = self._generate_qr()
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=10, dark="#e8e8f0", light="#0f0f23")
        return web.Response(
            body=buf.getvalue(),
            content_type="image/png",
        )

    def get_terminal_qr(self) -> str:
        """Get QR code as terminal-printable string (for CLI)."""
        if not self._ssid:
            return "No hotspot active"

        qr = self._generate_qr()
        buf = io.StringIO()
        qr.terminal(out=buf, compact=True)
        return buf.getvalue()
