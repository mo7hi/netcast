# Copyright (c) 2026, NetCast Contributors
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""aiohttp web API and captive portal server for NetCast daemon.

This module provides:
- Session token authentication middleware.
- REST API routes for hotspot management, diagnostics, and configurations.
- Real-time WebSocket event broadcaster.
- Captive portal detection probes and client authorization endpoints.
- Static file serving for Svelte web UI with built-in HTML fallbacks.
"""

from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging
import ipaddress
import os
import shutil
from collections.abc import Callable
from urllib.parse import urlsplit
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
import segno
import segno.helpers
from aiohttp import web

from daemon.backends.base import (
    ClientInfo,
    HotspotConfig,
    HotspotStatus,
    validate_interface_name,
    validate_ip_address,
)

logger = logging.getLogger(__name__)

# Standard locations for session token
DEFAULT_TOKEN_PATH = Path("/var/lib/netcast/session.token")
DEV_TOKEN_PATH = Path.home() / ".local" / "share" / "netcast" / "session.token"

# Web UI static files directories (Production vs Development)
PROD_WEB_DIR = Path("/usr/lib/netcast/web")
DEV_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "dist"

# Captive portal probe paths used by popular operating systems
PROBE_PATHS = {
    "/generate_204",         # Android / Chrome
    "/gen_204",              # Android alternate
    "/hotspot-detect.html",   # Apple iOS / macOS
    "/connecttest.txt",       # Windows
    "/ncsi.txt",              # Windows alternate
    "/canonical.html",        # Ubuntu / Linux
    "/check_network_status.txt", # Firefox
}

# Available Web UI theme skins
DEFAULT_SKINS = [
    {"id": "default-dark", "name": "NetCast Dark (Default)", "primary": "#ff8800", "background": "#121214"},
    {"id": "default-light", "name": "NetCast Light", "primary": "#ff8800", "background": "#f8f9fa"},
    {"id": "vlc-orange", "name": "VLC Cone Orange", "primary": "#e66000", "background": "#1e1e24"},
    {"id": "matrix-green", "name": "Matrix Hacker", "primary": "#00ff66", "background": "#0d1117"},
    {"id": "cyberpunk", "name": "Cyberpunk Neon", "primary": "#00f0ff", "background": "#0a0a14"},
    {"id": "nord", "name": "Nord Arctic", "primary": "#88c0d0", "background": "#2e3440"},
]


# ---------------------------------------------------------------------------
# Session Token Utilities
# ---------------------------------------------------------------------------

_cached_token: str | None = None
_cached_token_mtime: float = 0.0


def resolve_token_path(app: web.Application) -> Path:
    """Determine the filesystem path to the NetCast session token file.

    Args:
        app: Current aiohttp application instance.

    Returns:
        Path pointing to the session token file.
    """
    env_path = os.environ.get("NETCAST_SESSION_TOKEN_PATH")
    if env_path:
        return Path(env_path)

    daemon_state = app.get("daemon_state")
    if daemon_state and hasattr(daemon_state, "token_path") and daemon_state.token_path:
        return Path(daemon_state.token_path)

    if DEFAULT_TOKEN_PATH.exists() or os.access(DEFAULT_TOKEN_PATH.parent, os.W_OK):
        return DEFAULT_TOKEN_PATH

    return DEV_TOKEN_PATH


def read_session_token(app: web.Application) -> str | None:
    """Read and return the active session token, using cached value if unchanged.

    Args:
        app: Current aiohttp application instance.

    Returns:
        Token string if available, or None.
    """
    global _cached_token, _cached_token_mtime

    if "session_token" in app and app["session_token"]:
        return str(app["session_token"]).strip()

    daemon_state = app.get("daemon_state")
    if daemon_state and hasattr(daemon_state, "session_token") and daemon_state.session_token:
        return str(daemon_state.session_token).strip()

    token_path = resolve_token_path(app)
    try:
        if token_path.is_file():
            mtime = token_path.stat().st_mtime
            if _cached_token is not None and mtime == _cached_token_mtime:
                return _cached_token

            token = token_path.read_text(encoding="utf-8").strip()
            if token:
                _cached_token = token
                _cached_token_mtime = mtime
                return token
    except Exception as exc:
        logger.debug("Failed reading session token from %s: %s", token_path, exc)

    return _cached_token


# ---------------------------------------------------------------------------
# WebSocket Event Broadcasting
# ---------------------------------------------------------------------------

async def broadcast_event(app: web.Application, event_type: str, data: Any) -> None:
    """Broadcast a structured JSON event to all connected WebSocket clients.

    Args:
        app: Current aiohttp application.
        event_type: Identifier of the event (e.g. 'status_update', 'client_connected').
        data: Arbitrary serializable payload.
    """
    websockets: set[web.WebSocketResponse] = app.get("websockets", set())
    if not websockets:
        return

    payload = json.dumps({
        "event": event_type,
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(UTC).isoformat(),
    })

    dead: set[web.WebSocketResponse] = set()
    for ws in list(websockets):
        try:
            if not ws.closed:
                await ws.send_str(payload)
            else:
                dead.add(ws)
        except Exception as exc:
            logger.debug("Failed to send WS broadcast to client: %s", exc)
            dead.add(ws)

    if dead:
        websockets.difference_update(dead)


def broadcast(app: web.Application, event_type: str, data: Any) -> None:
    """Schedule asynchronous event broadcast on the active event loop.

    Args:
        app: Current aiohttp application.
        event_type: Identifier of the event.
        data: Arbitrary serializable payload.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_event(app, event_type, data))
    except RuntimeError:
        logger.debug("Cannot broadcast event '%s': No active event loop", event_type)


# ---------------------------------------------------------------------------
# Pre-flight Checks & Interface Inspection
# ---------------------------------------------------------------------------

async def run_preflight_checks() -> dict[str, Any]:
    """Execute pre-flight checks validating system readiness for hotspot creation.

    Returns:
        Dictionary containing overall 'passed' status and list of individual checks.
    """
    checks: list[dict[str, Any]] = []

    # 1. IP Forwarding Check
    ip_forward_ok = False
    ip_forward_msg = "IPv4 forwarding is disabled in kernel"
    try:
        proc_ip_fwd = Path("/proc/sys/net/ipv4/ip_forward")
        if proc_ip_fwd.exists() and proc_ip_fwd.read_text(encoding="utf-8").strip() == "1":
            ip_forward_ok = True
            ip_forward_msg = "IPv4 forwarding is enabled"
        else:
            # Check via sysctl command
            proc = await asyncio.create_subprocess_exec(
                "sysctl", "-n", "net.ipv4.ip_forward",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0 and stdout.decode("utf-8").strip() == "1":
                ip_forward_ok = True
                ip_forward_msg = "IPv4 forwarding is enabled"
    except Exception as exc:
        ip_forward_msg = f"Failed to check IP forwarding: {exc}"

    checks.append({
        "name": "ip_forwarding",
        "title": "IPv4 Packet Forwarding",
        "passed": ip_forward_ok,
        "required": False,  # the daemon enables forwarding itself when starting NAT
        "message": ip_forward_msg,
    })

    # 2. Kernel Traffic Shaping Modules (sch_htb, ifb)
    modules_ok = True
    modules_msg = "Kernel traffic control modules available"
    try:
        proc_modules = Path("/proc/modules")
        loaded = proc_modules.read_text(encoding="utf-8") if proc_modules.exists() else ""
        missing_mods = []
        for mod in ("sch_htb", "ifb"):
            if mod not in loaded:
                # Check modinfo or try modprobe probe
                mod_check = await asyncio.create_subprocess_exec(
                    "modinfo", mod,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await mod_check.communicate()
                if mod_check.returncode != 0:
                    missing_mods.append(mod)

        if missing_mods:
            modules_ok = False
            modules_msg = f"Optional modules not loaded/found: {', '.join(missing_mods)}"
    except Exception as exc:
        modules_msg = f"Kernel module inspection failed: {exc}"

    checks.append({
        "name": "kernel_modules",
        "title": "Fair Share QoS Modules",
        "passed": modules_ok,
        "required": False,
        "message": modules_msg,
    })

    # 3. Wi-Fi AP Support Check
    wifi_ok = False
    wifi_msg = "No Wi-Fi interface supporting AP mode detected"
    interfaces = await get_network_interfaces()
    ap_ifaces = [iface["name"] for iface in interfaces if iface.get("is_wireless") and iface.get("ap_capable")]
    if ap_ifaces:
        wifi_ok = True
        wifi_msg = f"AP mode supported on interfaces: {', '.join(ap_ifaces)}"
    elif any(iface.get("is_wireless") for iface in interfaces):
        # Has wireless interface, but AP mode capability unconfirmed
        wifi_ok = True
        wifi_msg = "Wireless interface detected (AP capability assumed via NetworkManager)"

    checks.append({
        "name": "wifi_ap_capable",
        "title": "Wi-Fi Access Point Mode",
        "passed": wifi_ok,
        "required": True,
        "message": wifi_msg,
    })

    # 4. Firewall / Packet Filter Check (nftables or iptables)
    fw_ok = False
    fw_msg = "Neither nftables nor iptables binary located"
    if shutil.which("nft"):
        fw_ok = True
        fw_msg = "nftables packet filtering available"
    elif shutil.which("iptables"):
        fw_ok = True
        fw_msg = "iptables packet filtering available"

    checks.append({
        "name": "packet_filter",
        "title": "Firewall / NAT Support",
        "passed": fw_ok,
        "required": True,
        "message": fw_msg,
    })

    # 5. NetworkManager or D-Bus Check
    nm_ok = False
    nm_msg = "NetworkManager CLI / service check"
    if shutil.which("nmcli"):
        nm_ok = True
        nm_msg = "NetworkManager management tools available"
    else:
        nm_msg = "NetworkManager nmcli not found; manual backend required"

    checks.append({
        "name": "network_manager",
        "title": "NetworkManager Daemon",
        "passed": nm_ok,
        "required": False,
        "message": nm_msg,
    })

    all_passed = all(c["passed"] for c in checks if c["required"])
    return {
        "passed": all_passed,
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": checks,
    }


async def get_network_interfaces() -> list[dict[str, Any]]:
    """Scan and enumerate all network interfaces on the host system.

    Returns:
        List of dictionaries detailing interface attributes, state, addresses, and capabilities.
    """
    interfaces: list[dict[str, Any]] = []
    net_path = Path("/sys/class/net")
    if not net_path.exists():
        return interfaces

    # Pre-fetch IPv4 addresses using 'ip -j -4 addr show' if available
    ip_addrs_by_iface: dict[str, list[str]] = {}
    try:
        proc = await asyncio.create_subprocess_exec(
            "ip", "-j", "-4", "addr", "show",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            parsed = json.loads(stdout.decode("utf-8"))
            for entry in parsed:
                ifname = entry.get("ifname")
                addrs = [f"{a.get('local')}/{a.get('prefixlen')}" for a in entry.get("addr_info", []) if a.get("local")]
                if ifname:
                    ip_addrs_by_iface[ifname] = addrs
    except Exception as exc:
        logger.debug("Failed querying ip addresses via ip -j: %s", exc)

    # Check AP capability via nmcli if possible
    nm_ap_support: dict[str, bool] = {}
    if shutil.which("nmcli"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli", "-t", "-f", "DEVICE,WIFI-PROPERTIES.AP", "dev", "show",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                current_dev = ""
                for line in stdout.decode("utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.startswith("GENERAL.DEVICE:") or line.startswith("DEVICE:"):
                        current_dev = line.split(":", 1)[1].strip()
                    elif line.startswith("WIFI-PROPERTIES.AP:") and current_dev:
                        val = line.split(":", 1)[1].strip().lower()
                        nm_ap_support[current_dev] = (val == "yes")
        except Exception as exc:
            logger.debug("Failed querying nmcli dev show for AP support: %s", exc)

    for iface_dir in sorted(net_path.iterdir()):
        name = iface_dir.name
        if not validate_interface_name(name):
            continue

        is_wireless = (iface_dir / "wireless").exists() or (iface_dir / "phy80211").exists()
        operstate = "unknown"
        if (iface_dir / "operstate").exists():
            try:
                operstate = (iface_dir / "operstate").read_text(encoding="utf-8").strip()
            except Exception:
                pass

        mac = ""
        if (iface_dir / "address").exists():
            try:
                mac = (iface_dir / "address").read_text(encoding="utf-8").strip()
            except Exception:
                pass

        iface_type = "ethernet"
        if name == "lo":
            iface_type = "loopback"
        elif is_wireless:
            iface_type = "wireless"
        elif (iface_dir / "type").exists():
            try:
                type_code = int((iface_dir / "type").read_text(encoding="utf-8").strip())
                if type_code == 772:
                    iface_type = "loopback"
                elif type_code == 1:
                    iface_type = "ethernet"
                elif type_code == 65534:
                    iface_type = "tunnel"
            except Exception:
                pass

        ap_capable = False
        if is_wireless:
            if name in nm_ap_support:
                ap_capable = nm_ap_support[name]
            else:
                # Default to True for wireless interfaces if unverified
                ap_capable = True

        interfaces.append({
            "name": name,
            "type": iface_type,
            "is_wireless": is_wireless,
            "ap_capable": ap_capable,
            "state": operstate,
            "mac": mac,
            "ip_addresses": ip_addrs_by_iface.get(name, []),
        })

    return interfaces


# ---------------------------------------------------------------------------
# Data Serializers
# ---------------------------------------------------------------------------

def serialize_status(status: HotspotStatus) -> dict[str, Any]:
    """Convert HotspotStatus dataclass to JSON-serializable dictionary.

    Args:
        status: HotspotStatus instance.

    Returns:
        Serialized dictionary.
    """
    return asdict(status)


def serialize_client(client: ClientInfo) -> dict[str, Any]:
    """Convert ClientInfo dataclass to JSON-serializable dictionary.

    Args:
        client: ClientInfo instance.

    Returns:
        Serialized dictionary with ISO datetime string.
    """
    data = asdict(client)
    if client.connected_at:
        data["connected_at"] = client.connected_at.isoformat()
    return data


async def get_status_from_state(daemon_state: Any) -> HotspotStatus:
    """Retrieve the current HotspotStatus from daemon_state or backend.

    Args:
        daemon_state: Daemon application state reference.

    Returns:
        HotspotStatus representing current state.
    """
    if hasattr(daemon_state, "get_status"):
        res = daemon_state.get_status()
        if asyncio.iscoroutine(res):
            return await res
        return res

    if hasattr(daemon_state, "backend") and hasattr(daemon_state.backend, "get_status"):
        return await daemon_state.backend.get_status()

    if hasattr(daemon_state, "status") and isinstance(daemon_state.status, HotspotStatus):
        return daemon_state.status

    return HotspotStatus(
        active=False,
        ssid="",
        password=None,
        band="2.4GHz",
        channel=0,
        ap_interface="",
        source_interface="",
        gateway_ip="192.168.12.1",
        client_count=0,
    )


async def get_clients_from_state(daemon_state: Any) -> list[ClientInfo]:
    """Retrieve connected clients list from daemon_state or backend.

    Args:
        daemon_state: Daemon application state reference.

    Returns:
        List of ClientInfo instances.
    """
    if hasattr(daemon_state, "get_clients"):
        res = daemon_state.get_clients()
        if asyncio.iscoroutine(res):
            return await res
        return res

    if hasattr(daemon_state, "backend") and hasattr(daemon_state.backend, "get_connected_clients"):
        return await daemon_state.backend.get_connected_clients()

    if hasattr(daemon_state, "clients") and isinstance(daemon_state.clients, list):
        return daemon_state.clients

    return []


# ---------------------------------------------------------------------------
# Authentication Middleware
# ---------------------------------------------------------------------------

@web.middleware
async def session_auth_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Any],
) -> web.StreamResponse:
    """Validate session token on all protected /api/ routes.

    Exemptions:
    - Captive portal routes (/portal and /portal/*).
    - Captive portal operating system probe URLs.
    - Static file serving routes and UI fallback pages.
    """
    path = request.path

    # Captive portal routes are strictly exempt
    if path == "/portal" or path.startswith("/portal/"):
        return await handler(request)

    # OS Captive Portal Probes are strictly exempt
    if path in PROBE_PATHS:
        return await handler(request)

    # Reject DNS-rebinding attempts: Host must be localhost or an IP literal
    if (path.startswith("/api/") or path == "/ws") and not _host_is_safe(request):
        return web.json_response({"error": "Forbidden host"}, status=403)

    # Token bootstrap: handler itself restricts this to loopback peers
    if path == "/api/token":
        return await handler(request)

    # Protected REST API routes require valid token
    if path.startswith("/api/"):
        token = read_session_token(request.app)
        provided_token = request.headers.get("X-NetCast-Token")

        if not token:
            logger.warning("Request to %s denied: Daemon session token not configured", path)
            return web.json_response(
                {"error": "Unauthorized", "message": "Session token not initialized on daemon"},
                status=401,
            )

        if not provided_token or not hmac.compare_digest(provided_token.strip(), token.strip()):
            logger.warning("Unauthorized request to %s: Invalid or missing X-NetCast-Token", path)
            return web.json_response(
                {"error": "Unauthorized", "message": "Missing or invalid X-NetCast-Token header"},
                status=401,
            )

    # Real-time WebSocket route
    elif path == "/ws":
        token = read_session_token(request.app)
        provided = request.headers.get("X-NetCast-Token") or request.query.get("token")
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            return web.json_response({"error": "Forbidden origin"}, status=403)
        if not token or not provided or not hmac.compare_digest(provided.strip(), token.strip()):
            return web.json_response({"error": "Unauthorized"}, status=401)

    return await handler(request)


# ---------------------------------------------------------------------------
# REST API Handlers
# ---------------------------------------------------------------------------

def _host_is_safe(request: web.Request) -> bool:
    """Host header must be 'localhost' or an IP literal (blocks DNS rebinding)."""
    try:
        host = urlsplit("//" + request.host).hostname
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def handle_get_token(request: web.Request) -> web.Response:
    """GET /api/token - Give the session token to the local UI (loopback peers only)."""
    if request.remote not in ("127.0.0.1", "::1"):
        return web.json_response({"error": "Forbidden"}, status=403)
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        return web.json_response({"error": "Forbidden origin"}, status=403)
    token = read_session_token(request.app)
    if not token:
        return web.json_response({"error": "Token not initialized"}, status=503)
    return web.json_response({"token": token}, headers={"Cache-Control": "no-store"})


async def handle_get_status(request: web.Request) -> web.Response:
    """GET /api/status - Return current operational status."""
    daemon_state = request.app["daemon_state"]
    status = await get_status_from_state(daemon_state)
    return web.json_response(serialize_status(status))


async def handle_get_clients(request: web.Request) -> web.Response:
    """GET /api/clients - Return list of currently connected stations."""
    daemon_state = request.app["daemon_state"]
    clients = await get_clients_from_state(daemon_state)
    return web.json_response([serialize_client(c) for c in clients])


async def handle_get_preflight(request: web.Request) -> web.Response:
    """GET /api/preflight - Run pre-flight checks and broadcast results."""
    daemon_state = request.app["daemon_state"]

    if hasattr(daemon_state, "run_preflight"):
        res = daemon_state.run_preflight()
        result = await res if asyncio.iscoroutine(res) else res
    else:
        result = await run_preflight_checks()

    # Broadcast preflight result to active WebSocket listeners
    broadcast(request.app, "preflight_result", result)
    return web.json_response(result)


async def handle_post_hotspot_start(request: web.Request) -> web.Response:
    """POST /api/hotspot/start - Initialize and start Wi-Fi hotspot."""
    daemon_state = request.app["daemon_state"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    try:
        config = HotspotConfig(
            ssid=str(data.get("ssid", "")).strip(),
            password=str(data.get("password", "")).strip() if data.get("password") else None,
            band=str(data.get("band", "bg")),
            channel=int(data.get("channel", 0)),
            source_interface=str(data.get("source_interface") or data.get("source") or "").strip(),
            ap_interface=str(data.get("ap_interface", "")).strip(),
            source_type=str(data.get("source_type", "wifi")),
        )
        config.validate()
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    try:
        if hasattr(daemon_state, "start_hotspot"):
            res = daemon_state.start_hotspot(config)
            status = await res if asyncio.iscoroutine(res) else res
        elif hasattr(daemon_state, "backend") and hasattr(daemon_state.backend, "create_hotspot"):
            status = await daemon_state.backend.create_hotspot(config)
        else:
            raise RuntimeError("Daemon state does not implement start_hotspot")

        status_dict = serialize_status(status)
        broadcast(request.app, "status_update", status_dict)
        return web.json_response(status_dict, status=200)
    except Exception as exc:
        logger.exception("Failed to start hotspot: %s", exc)
        code = 400 if isinstance(exc, (ValueError, NotImplementedError)) else 500
        return web.json_response({"error": f"Failed to start hotspot: {exc}"}, status=code)


async def handle_post_hotspot_stop(request: web.Request) -> web.Response:
    """POST /api/hotspot/stop - Deactivate and stop the active Wi-Fi hotspot."""
    daemon_state = request.app["daemon_state"]
    try:
        if hasattr(daemon_state, "stop_hotspot"):
            res = daemon_state.stop_hotspot()
            if asyncio.iscoroutine(res):
                await res
        elif hasattr(daemon_state, "backend") and hasattr(daemon_state.backend, "stop_hotspot"):
            await daemon_state.backend.stop_hotspot()

        status = await get_status_from_state(daemon_state)
        broadcast(request.app, "status_update", serialize_status(status))
        return web.json_response({"success": True, "message": "Hotspot stopped successfully"})
    except Exception as exc:
        logger.exception("Failed to stop hotspot: %s", exc)
        return web.json_response({"error": f"Failed to stop hotspot: {exc}"}, status=500)


async def handle_get_qr(request: web.Request) -> web.Response:
    """GET /api/qr - Generate and return Wi-Fi credentials as SVG QR code."""
    daemon_state = request.app["daemon_state"]
    status = await get_status_from_state(daemon_state)

    ssid = status.ssid
    password = status.password

    # If inactive or SSID empty, fall back to default configuration
    if not ssid:
        cfg = getattr(daemon_state, "config", {})
        hotspot_cfg = cfg.get("hotspot", {}) if isinstance(cfg, dict) else {}
        ssid = hotspot_cfg.get("ssid", "NetCast")
        password = hotspot_cfg.get("password")

    security = "WPA" if password else "nopass"
    qr = segno.helpers.make_wifi(ssid=ssid, password=password, security=security)

    buff = io.BytesIO()
    qr.save(buff, kind="svg", scale=4, border=2, dark="#000000", light="#ffffff")
    return web.Response(body=buff.getvalue(), content_type="image/svg+xml")


async def handle_get_config(request: web.Request) -> web.Response:
    """GET /api/config - Return current daemon and hotspot configuration."""
    daemon_state = request.app["daemon_state"]
    if hasattr(daemon_state, "get_config"):
        cfg = daemon_state.get_config()
        if asyncio.iscoroutine(cfg):
            cfg = await cfg
        return web.json_response(cfg)

    cfg = getattr(daemon_state, "config", None)
    if isinstance(cfg, dict):
        return web.json_response(cfg)

    # Fallback default configuration values
    return web.json_response({
        "hotspot": {"ssid": "NetCast", "password": "netcast1234", "band": "bg", "channel": 0},
        "network": {"source_type": "auto", "source_interface": ""},
        "daemon": {"listen_host": "127.0.0.1", "listen_port": 8384, "theme": "default-dark"},
        "fair_share": {"enabled": False, "total_bandwidth_kbps": 0},
        "captive_portal": {"enabled": True, "message": "Welcome to NetCast. Tap 'Connect' to get online."},
    })


async def handle_put_config(request: web.Request) -> web.Response:
    """PUT /api/config - Update configuration settings."""
    daemon_state = request.app["daemon_state"]
    try:
        updates = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if hasattr(daemon_state, "update_config"):
        try:
            res = daemon_state.update_config(updates)
            if asyncio.iscoroutine(res):
                res = await res
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(res)

    if hasattr(daemon_state, "config") and isinstance(daemon_state.config, dict):
        daemon_state.config.update(updates)
        return web.json_response(daemon_state.config)

    return web.json_response({"success": True, "updated": updates})


async def handle_get_skins(request: web.Request) -> web.Response:
    """GET /api/skins - List available UI theme skins."""
    daemon_state = request.app["daemon_state"]
    current_theme = "default-dark"
    if hasattr(daemon_state, "config") and isinstance(daemon_state.config, dict):
        current_theme = daemon_state.config.get("daemon", {}).get("theme", "default-dark")

    return web.json_response({
        "current": current_theme,
        "skins": DEFAULT_SKINS,
    })


async def handle_get_interfaces(request: web.Request) -> web.Response:
    """GET /api/interfaces - List host network interfaces."""
    daemon_state = request.app["daemon_state"]
    if hasattr(daemon_state, "get_interfaces"):
        res = daemon_state.get_interfaces()
        interfaces = await res if asyncio.iscoroutine(res) else res
    else:
        interfaces = await get_network_interfaces()

    return web.json_response(interfaces)


# ---------------------------------------------------------------------------
# WebSocket Handler
# ---------------------------------------------------------------------------

async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    """GET /ws - Establish real-time WebSocket connection."""
    ws = web.WebSocketResponse(heartbeat=25.0)
    await ws.prepare(request)

    websockets: set[web.WebSocketResponse] = request.app["websockets"]
    websockets.add(ws)
    logger.info("WebSocket client connected. Active connections: %d", len(websockets))

    # Send initial status snapshot upon connection
    try:
        daemon_state = request.app["daemon_state"]
        status = await get_status_from_state(daemon_state)
        await ws.send_json({
            "event": "status_update",
            "type": "status_update",
            "data": serialize_status(status),
            "timestamp": datetime.now(UTC).isoformat(),
        })
    except Exception as exc:
        logger.debug("Failed sending initial status update on WS: %s", exc)

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    payload = json.loads(msg.data)
                    # Respond to client ping
                    if payload.get("type") == "ping" or payload.get("event") == "ping":
                        await ws.send_json({"type": "pong", "timestamp": datetime.now(UTC).isoformat()})
                except Exception:
                    pass
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.debug("WebSocket error: %s", ws.exception())
    finally:
        websockets.discard(ws)
        logger.info("WebSocket client disconnected. Remaining connections: %d", len(websockets))

    return ws


# ---------------------------------------------------------------------------
# Captive Portal Handlers
# ---------------------------------------------------------------------------

async def handle_probe_redirect(request: web.Request) -> web.Response:
    """Redirect OS captive portal probe URLs to /portal."""
    raise web.HTTPFound("/portal")


CAPTIVE_PORTAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NetCast - Connect to Internet</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: #121214;
      color: #e1e1e6;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 1.5rem;
    }
    .card {
      background: #202024;
      border: 1px solid #323238;
      border-radius: 1rem;
      padding: 2.5rem 2rem;
      max-width: 420px;
      width: 100%;
      text-align: center;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
    }
    .logo {
      width: 72px;
      height: 72px;
      background: linear-gradient(135deg, #ff8800, #ff5500);
      border-radius: 50%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 1.5rem;
      box-shadow: 0 4px 15px rgba(255, 136, 0, 0.4);
    }
    .logo svg { width: 36px; height: 36px; fill: #ffffff; }
    h1 { font-size: 1.6rem; font-weight: 700; margin-bottom: 0.75rem; color: #ffffff; }
    p { font-size: 1rem; color: #a8a8b3; line-height: 1.5; margin-bottom: 2rem; }
    .btn {
      display: block;
      width: 100%;
      background: #ff8800;
      color: #ffffff;
      border: none;
      border-radius: 0.5rem;
      padding: 0.9rem 1.5rem;
      font-size: 1.1rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s, transform 0.1s;
      text-decoration: none;
    }
    .btn:hover { background: #e67700; }
    .btn:active { transform: scale(0.98); }
    .status-msg {
      margin-top: 1.25rem;
      font-size: 0.95rem;
      display: none;
    }
    .status-msg.success { color: #04d361; display: block; }
    .status-msg.error { color: #f75a68; display: block; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14.5v-9l6 4.5-6 4.5z"/></svg>
    </div>
    <h1>Welcome to NetCast</h1>
    <p id="portal-msg">Tap the button below to connect to the network and get instant internet access.</p>
    <form id="auth-form" method="POST" action="/portal/authorize">
      <button type="submit" id="connect-btn" class="btn">Connect to Internet</button>
    </form>
    <div id="status" class="status-msg"></div>
  </div>
  <script>
    const form = document.getElementById('auth-form');
    const btn = document.getElementById('connect-btn');
    const status = document.getElementById('status');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      btn.disabled = true;
      btn.innerText = 'Connecting...';
      try {
        const resp = await fetch('/portal/authorize', { method: 'POST' });
        const data = await resp.json();
        if (resp.ok && data.authorized) {
          status.className = 'status-msg success';
          status.innerText = 'Connected! You now have internet access.';
          btn.innerText = 'Connected';
          setTimeout(() => { window.location.href = 'https://www.google.com'; }, 1500);
        } else {
          status.className = 'status-msg error';
          status.innerText = data.error || 'Authorization failed.';
          btn.disabled = false;
          btn.innerText = 'Try Again';
        }
      } catch (err) {
        status.className = 'status-msg error';
        status.innerText = 'Connection error. Please try again.';
        btn.disabled = false;
        btn.innerText = 'Try Again';
      }
    });
  </script>
</body>
</html>
"""


async def handle_portal_get(request: web.Request) -> web.Response:
    """GET /portal - Serve captive portal landing page."""
    # Check if a custom portal template file exists
    custom_template = Path("/etc/netcast/portal.html")
    if custom_template.is_file():
        try:
            return web.Response(text=custom_template.read_text(encoding="utf-8"), content_type="text/html")
        except Exception as exc:
            logger.debug("Failed reading custom portal template: %s", exc)

    cfg = getattr(request.app["daemon_state"], "config", {})
    msg = (cfg.get("captive_portal", {}) if isinstance(cfg, dict) else {}).get("message")
    page = CAPTIVE_PORTAL_HTML
    if msg:
        import html as _html

        page = page.replace(
            "Tap the button below to connect to the network and get instant internet access.",
            _html.escape(msg),
        )
    return web.Response(text=page, content_type="text/html")


async def handle_portal_authorize(request: web.Request) -> web.Response:
    """POST /portal/authorize - Authorize client IP in nftables/iptables."""
    client_ip = request.remote or "127.0.0.1"

    if not validate_ip_address(client_ip):
        return web.json_response({"error": f"Invalid client IP address '{client_ip}'"}, status=400)

    daemon_state = request.app["daemon_state"]

    # 1. Authorize via daemon_state hook if available
    if hasattr(daemon_state, "authorize_client"):
        try:
            res = daemon_state.authorize_client(client_ip)
            if asyncio.iscoroutine(res):
                await res
        except Exception as exc:
            logger.warning("daemon_state.authorize_client failed: %s", exc)
            return web.json_response({"error": "Authorization failed"}, status=500)

    # 2. Add client IP to nftables set 'authorized_clients'
    if shutil.which("nft"):
        try:
            cmd = ["nft", "add", "element", "inet", "netcast", "authenticated_clients", f"{{ {client_ip} }}"]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.debug("nft add element warning: %s", stderr.decode("utf-8", errors="ignore"))
        except Exception as exc:
            logger.debug("Subprocess execution of nft failed: %s", exc)

    broadcast(request.app, "client_authorized", {"ip": client_ip, "timestamp": datetime.now(UTC).isoformat()})
    return web.json_response({
        "authorized": True,
        "ip": client_ip,
        "message": "Client successfully authorized for internet access",
    })


# ---------------------------------------------------------------------------
# Static File Serving & UI Fallback
# ---------------------------------------------------------------------------

FALLBACK_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NetCast - Linux Software Router</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f1117;
      color: #e6edf3;
      padding: 2rem;
      line-height: 1.6;
    }
    .container { max-width: 800px; margin: 0 auto; }
    header {
      display: flex;
      align-items: center;
      gap: 1rem;
      margin-bottom: 2rem;
      border-bottom: 1px solid #30363d;
      padding-bottom: 1.5rem;
    }
    .logo {
      width: 48px;
      height: 48px;
      background: #ff8800;
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .logo svg { width: 28px; height: 28px; fill: #ffffff; }
    h1 { font-size: 1.8rem; font-weight: 700; color: #ffffff; }
    .badge {
      display: inline-block;
      background: #238636;
      color: #ffffff;
      font-size: 0.8rem;
      padding: 0.2rem 0.6rem;
      border-radius: 1rem;
      font-weight: 600;
    }
    .card {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 0.75rem;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
    }
    h2 { font-size: 1.2rem; margin-bottom: 1rem; color: #58a6ff; }
    ul { list-style: none; }
    li { margin-bottom: 0.5rem; }
    a { color: #58a6ff; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .note { color: #8b949e; font-size: 0.95rem; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="logo">
        <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14.5v-9l6 4.5-6 4.5z"/></svg>
      </div>
      <div>
        <h1>NetCast Daemon</h1>
        <p class="note">The VLC of software routers — sharing any internet connection via Wi-Fi</p>
      </div>
    </header>

    <div class="card">
      <h2>Daemon Status: <span class="badge">Operational</span></h2>
      <p class="note">The web UI frontend files were not detected in standard locations (<code>/usr/lib/netcast/web</code> or <code>./web/dist</code>). Running in API-only / fallback mode.</p>
    </div>

    <div class="card">
      <h2>Available Endpoints</h2>
      <ul>
        <li>📊 <a href="/api/status">/api/status</a> — Hotspot state and metrics</li>
        <li>👥 <a href="/api/clients">/api/clients</a> — Connected client stations</li>
        <li>🔍 <a href="/api/preflight">/api/preflight</a> — System readiness checks</li>
        <li>🌐 <a href="/api/interfaces">/api/interfaces</a> — Host network interfaces</li>
        <li>📱 <a href="/api/qr">/api/qr</a> — Hotspot credentials QR code</li>
        <li>🚪 <a href="/portal">/portal</a> — Captive portal landing page</li>
        <li>⚙️ <a href="/api/config">/api/config</a> — Current configuration</li>
      </ul>
    </div>
  </div>
</body>
</html>
"""


def locate_web_dist_dir() -> Path | None:
    """Find the compiled Svelte web distribution directory if present.

    Returns:
        Path object if static files exist, otherwise None.
    """
    for candidate in (PROD_WEB_DIR, DEV_WEB_DIR):
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    return None


async def handle_static_index(request: web.Request) -> web.Response:
    """Serve Svelte index.html if available, or fallback HTML page."""
    dist_dir = request.app.get("web_dist_dir")
    if dist_dir and (dist_dir / "index.html").is_file():
        return web.FileResponse(dist_dir / "index.html")

    return web.Response(text=FALLBACK_UI_HTML, content_type="text/html")


async def handle_spa_fallback(request: web.Request) -> web.Response:
    """Fallback handler for Single Page Application routing."""
    path = request.path
    if path.startswith("/api/") or path.startswith("/portal") or path == "/ws" or path in PROBE_PATHS:
        raise web.HTTPNotFound()

    dist_dir = request.app.get("web_dist_dir")
    if dist_dir and (dist_dir / "index.html").is_file():
        # Check if direct file exists (e.g. /favicon.svg)
        candidate = (dist_dir / path.lstrip("/")).resolve()
        try:
            if candidate.is_file() and candidate.is_relative_to(dist_dir.resolve()):
                return web.FileResponse(candidate)
        except Exception:
            pass
        return web.FileResponse(dist_dir / "index.html")

    return web.Response(text=FALLBACK_UI_HTML, content_type="text/html")


# ---------------------------------------------------------------------------
# Application Factory
# ---------------------------------------------------------------------------

async def _on_shutdown(app: web.Application) -> None:
    """Close all active WebSocket client connections gracefully on daemon shutdown."""
    websockets: set[web.WebSocketResponse] = app.get("websockets", set())
    logger.info("Closing %d active WebSocket connections...", len(websockets))
    for ws in list(websockets):
        try:
            await ws.close(code=aiohttp.WSCloseCode.GOING_AWAY, message=b"Server shutdown")
        except Exception:
            pass
    websockets.clear()


def create_app(
    daemon_state: Any = None,
    plugin_mgr: Any = None,
    session_token: str | None = None,
    *,
    config: Any = None,
) -> web.Application:
    """Construct and configure the NetCast aiohttp web application.

    Args:
        daemon_state: Host daemon state, or None.
        plugin_mgr: Optional NetCastPluginManager instance.
        session_token: Optional secret session token.
        config: Optional NetCastConfig configuration object.

    Returns:
        Configured aiohttp.web.Application ready to run.
    """
    if config is None and daemon_state is not None:
        if hasattr(daemon_state, "hotspot") or hasattr(daemon_state, "daemon"):
            config = daemon_state

    if daemon_state is None or daemon_state is config:
        class _DaemonStateProxy:
            def __init__(self, cfg: Any, token: str | None) -> None:
                self.config = cfg.to_dict() if hasattr(cfg, "to_dict") else (cfg or {})
                self.session_token = token
                self.clients: list[Any] = []

        daemon_state = _DaemonStateProxy(config, session_token)

    app = web.Application(middlewares=[session_auth_middleware])
    app["daemon_state"] = daemon_state
    app["config"] = config
    if session_token:
        app["session_token"] = session_token
    app["websockets"] = set()

    # Locate static web UI distribution
    web_dist_dir = locate_web_dist_dir()
    app["web_dist_dir"] = web_dist_dir

    # Register Captive Portal routes
    app.router.add_get("/portal", handle_portal_get)
    app.router.add_get("/portal/", handle_portal_get)
    app.router.add_post("/portal/authorize", handle_portal_authorize)

    # Register OS Captive Portal Probes
    for probe in PROBE_PATHS:
        app.router.add_get(probe, handle_probe_redirect)

    # Register REST API routes
    app.router.add_get("/api/token", handle_get_token)
    app.router.add_get("/api/health", handle_get_status)
    app.router.add_get("/api/status", handle_get_status)
    app.router.add_get("/api/clients", handle_get_clients)
    app.router.add_get("/api/preflight", handle_get_preflight)
    app.router.add_post("/api/hotspot/start", handle_post_hotspot_start)
    app.router.add_post("/api/hotspot/stop", handle_post_hotspot_stop)
    app.router.add_get("/api/qr", handle_get_qr)
    app.router.add_get("/api/config", handle_get_config)
    app.router.add_put("/api/config", handle_put_config)
    app.router.add_get("/api/skins", handle_get_skins)
    app.router.add_get("/api/interfaces", handle_get_interfaces)

    # Register WebSocket endpoint
    app.router.add_get("/ws", handle_ws)

    # Register plugin routes
    if plugin_mgr is not None and hasattr(plugin_mgr, "hook"):
        app["plugin_mgr"] = plugin_mgr
        plugin_mgr.hook.register_api_routes(app=app)
    elif hasattr(daemon_state, "plugin_mgr") and hasattr(daemon_state.plugin_mgr, "hook"):
        app["plugin_mgr"] = daemon_state.plugin_mgr
        daemon_state.plugin_mgr.hook.register_api_routes(app=app)

    # Register static files and SPA fallback
    if web_dist_dir and (web_dist_dir / "assets").is_dir():
        app.router.add_static("/assets", web_dist_dir / "assets")

    app.router.add_get("/", handle_static_index)
    app.router.add_get("/{tail:.*}", handle_spa_fallback)

    # Register shutdown cleanup
    app.on_shutdown.append(_on_shutdown)

    return app
