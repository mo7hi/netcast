"""Daemon orchestrator for NetCast.

Owns the networking backend, firewall backend, plugin hooks, D-Bus service and
client monitoring. This is the object passed to the web app and D-Bus service
as ``daemon_state``.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import os
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web
from dbus_next import BusType

from daemon.api import web as webapi
from daemon.backends.base import ClientInfo, HotspotConfig, HotspotStatus
from daemon.config import NetCastConfig, parse_config_dict
from daemon.scanner import (
    get_interface_capabilities,
    list_ethernet_interfaces,
    list_usb_tethering_interfaces,
    list_wifi_interfaces,
)

logger = logging.getLogger("netcast.state")

AUTH_TIMEOUT_SECONDS = 7200
MONITOR_INTERVAL = 3.0
ROUTE_FILE = Path("/proc/net/route")


def _default_route_iface() -> str:
    """Return the interface carrying the IPv4 default route, or ''."""
    try:
        for line in ROUTE_FILE.read_text(encoding="utf-8").splitlines()[1:]:
            f = line.split()
            if len(f) >= 4 and f[1] == "00000000" and int(f[3], 16) & 2:
                return f[0]
    except (OSError, ValueError):
        pass
    return ""


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


class DaemonState:
    """Coordinates hotspot lifecycle across backend, firewall and plugins."""

    def __init__(
        self,
        config: NetCastConfig,
        plugin_mgr: Any = None,
        session_token: str | None = None,
        dms: Any = None,
    ) -> None:
        self._cfg = config
        self.plugin_mgr = plugin_mgr
        self.session_token = session_token
        self.dms = dms
        self.app: web.Application | None = None
        self.runner: web.AppRunner | None = None
        self.backend: Any = None
        self.firewall: Any = None
        self.dbus_bus: Any = None
        self.dbus_iface: Any = None
        self._lock = asyncio.Lock()
        self._active_config: HotspotConfig | None = None
        self._known_clients: dict[str, ClientInfo] = {}
        self._monitor_task: asyncio.Task[None] | None = None
        self._portal_site: web.TCPSite | None = None

    # ------------------------------------------------------------------ setup

    def attach(self, app: web.Application, runner: web.AppRunner) -> None:
        self.app = app
        self.runner = runner

    async def start(self) -> None:
        """Start D-Bus service and the client monitor."""
        try:
            from daemon.api.dbus_service import setup_dbus_service

            bus_env = os.environ.get("NETCAST_DBUS_BUS", "").lower()
            bus_type = BusType.SESSION if bus_env == "session" else BusType.SYSTEM
            self.dbus_bus, self.dbus_iface = await setup_dbus_service(self, bus_type=bus_type)
        except Exception as exc:
            logger.warning("D-Bus service unavailable (CLI will not work): %s", exc)
        self._monitor_task = asyncio.create_task(self._monitor_loop(), name="client-monitor")

    async def shutdown(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None
        try:
            await self.stop_hotspot()
        except Exception as exc:
            logger.error("Error stopping hotspot during shutdown: %s", exc)
        if self.dbus_bus is not None:
            with contextlib.suppress(Exception):
                self.dbus_bus.disconnect()

    # ----------------------------------------------------------- config access

    @property
    def config(self) -> dict[str, Any]:
        return self._cfg.to_dict()

    def get_config(self) -> dict[str, Any]:
        return self._cfg.to_dict()

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Validate and apply config changes in memory (not persisted to disk)."""
        if not isinstance(updates, dict):
            raise ValueError("Config update must be a JSON object")
        merged = _deep_merge(self._cfg.to_dict(), updates)
        try:
            self._cfg = parse_config_dict(merged)
        except TypeError as exc:
            raise ValueError(f"Unknown configuration key: {exc}") from exc
        return self._cfg.to_dict()

    # -------------------------------------------------------------- selection

    async def _get_backend(self) -> Any:
        if self.backend is not None:
            return self.backend
        choice = os.environ.get("NETCAST_BACKEND", "").lower()
        has_hostapd = bool(shutil.which("hostapd") and shutil.which("dnsmasq"))
        if choice != "nm" and (choice == "hostapd" or has_hostapd):
            from daemon.backends.hostapd import HostapdBackend

            self.backend = HostapdBackend()
        else:
            from daemon.backends.networkmanager import NetworkManagerBackend

            if not await NetworkManagerBackend.is_available():
                raise RuntimeError(
                    "No usable backend: install hostapd + dnsmasq, or NetworkManager >= 1.16"
                )
            self.backend = NetworkManagerBackend()
        logger.info("Using networking backend %s", type(self.backend).__name__)
        return self.backend

    def _get_firewall(self) -> Any:
        if self.firewall is not None:
            return self.firewall
        if shutil.which("nft"):
            from daemon.firewall.nftables import NftablesBackend

            self.firewall = NftablesBackend()
        elif shutil.which("iptables"):
            from daemon.firewall.iptables import IptablesBackend

            self.firewall = IptablesBackend()
        else:
            raise RuntimeError("Neither nftables nor iptables is installed")
        return self.firewall

    async def _resolve_config(self, config: HotspotConfig) -> HotspotConfig:
        """Fill in source and AP interfaces when they were not specified."""
        stype = (config.source_type or "auto").lower()
        if stype == "pppoe":
            raise NotImplementedError("PPPoE upstream is not implemented yet")

        source = config.source_interface
        if not source:
            if stype == "wifi":
                raise ValueError("Choose the upstream Wi-Fi interface")
            ethernet = await list_ethernet_interfaces()
            usb = await list_usb_tethering_interfaces()
            if stype == "ethernet" and ethernet:
                source = ethernet[0]
            elif stype == "usb" and usb:
                source = usb[0]
            else:
                source = _default_route_iface() or (ethernet or usb or [""])[0]
            if not source:
                raise ValueError("No upstream internet interface found")

        ap = config.ap_interface
        if ap and ap == source:
            raise ValueError(
                "AP and upstream interface must differ (single-radio repeater is not supported)"
            )
        if not ap:
            for dev in await list_wifi_interfaces():
                if dev.name == source:
                    continue
                try:
                    caps = await get_interface_capabilities(dev.name)
                except Exception:
                    continue
                if caps.supports_ap:
                    ap = dev.name
                    break
            if not ap:
                raise ValueError(
                    "No Wi-Fi adapter with AP mode found (other than the upstream interface)"
                )
        return replace(config, source_interface=source, ap_interface=ap, source_type=stype)

    # --------------------------------------------------------------- lifecycle

    async def start_hotspot(self, config: HotspotConfig) -> HotspotStatus:
        async with self._lock:
            if self._active_config is not None:
                await self._stop_locked("restart")

            config = await self._resolve_config(config)
            config.validate()
            backend = await self._get_backend()
            firewall = self._get_firewall()

            try:
                status = await backend.create_hotspot(config)
                if not status.active:
                    raise RuntimeError("Hotspot did not become active")
                gateway = status.gateway_ip
                await firewall.setup_nat(config.source_interface, config.ap_interface, gateway)
                if self._cfg.captive_portal.enabled:
                    port = self._cfg.daemon.listen_port
                    await firewall.setup_captive_portal(gateway, port)
                    await self._bind_portal(gateway)
            except Exception:
                await self._cleanup_quietly()
                raise

            self._active_config = config
            self._known_clients = {}
            self._record_dms(config)
            status = await backend.get_status()
            if self.dbus_iface is not None:
                self.dbus_iface.sync_properties(status)
            status_dict = webapi.serialize_status(status)
            self._fire(
                "on_hotspot_start", config=self._hook_config(config), status=status_dict
            )
            return status

    async def stop_hotspot(self) -> None:
        async with self._lock:
            await self._stop_locked("user_request")

    async def _stop_locked(self, reason: str) -> None:
        had_active = self._active_config is not None
        await self._cleanup_quietly()
        self._active_config = None
        self._known_clients = {}
        if self.dbus_iface is not None:
            self.dbus_iface.sync_properties(HotspotStatus(active=False, ssid=""))
        if self.dms is not None:
            with contextlib.suppress(Exception):
                self.dms.update_state("child_pids", [])
                self.dms.update_state("iptables_cleanup", [])
                self.dms.update_state("interfaces", [])
        if had_active:
            self._fire("on_hotspot_stop", reason=reason)

    async def _cleanup_quietly(self) -> None:
        if self._portal_site is not None:
            with contextlib.suppress(Exception):
                await self._portal_site.stop()
            self._portal_site = None
        if self.firewall is not None:
            with contextlib.suppress(Exception):
                await self.firewall.teardown_nat()
        if self.backend is not None:
            with contextlib.suppress(Exception):
                await self.backend.stop_hotspot()

    async def _bind_portal(self, gateway_ip: str) -> None:
        """Listen on the gateway IP so redirected captive-portal traffic is reachable."""
        host = self._cfg.daemon.listen_host
        if self.runner is None or host in ("0.0.0.0", "::", gateway_ip):
            return
        site = web.TCPSite(self.runner, host=gateway_ip, port=self._cfg.daemon.listen_port)
        await site.start()
        self._portal_site = site

    def _record_dms(self, config: HotspotConfig) -> None:
        """Give the dead man's switch enough state to clean up after a crash."""
        if self.dms is None:
            return
        pids = [
            p.pid
            for p in (
                getattr(self.backend, "_hostapd_proc", None),
                getattr(self.backend, "_dnsmasq_proc", None),
            )
            if p is not None and getattr(p, "pid", None)
        ]
        try:
            self.dms.update_state("child_pids", pids)
            if shutil.which("nft") and type(self.firewall).__name__ == "NftablesBackend":
                self.dms.update_state(
                    "iptables_cleanup", [["nft", "delete", "table", "inet", "netcast"]]
                )
            self.dms.update_state("interfaces", [config.ap_interface])
        except Exception as exc:
            logger.warning("Could not record dead man's switch state: %s", exc)

    # ------------------------------------------------------------------ queries

    async def get_status(self) -> HotspotStatus:
        if self.backend is None:
            return HotspotStatus(active=False, ssid="")
        return await self.backend.get_status()

    async def get_clients(self) -> list[ClientInfo]:
        if self.backend is None or self._active_config is None:
            return []
        return await self.backend.get_connected_clients()

    async def authorize_client(self, client_ip: str) -> None:
        if self.firewall is None or self._active_config is None:
            raise RuntimeError("Hotspot is not running")
        await self.firewall.authorize_client(client_ip, AUTH_TIMEOUT_SECONDS)

    # ----------------------------------------------------------------- plumbing

    def _hook_config(self, config: HotspotConfig) -> SimpleNamespace:
        """Config object exposing the attributes the built-in plugins read."""
        ns = SimpleNamespace(**asdict(config))
        ns.fair_share_enabled = self._cfg.fair_share.enabled
        ns.total_bandwidth_kbps = self._cfg.fair_share.total_bandwidth_kbps
        ns.captive_portal_enabled = self._cfg.captive_portal.enabled
        ns.captive_portal_message = self._cfg.captive_portal.message
        ns.listen_port = self._cfg.daemon.listen_port
        return ns

    def _fire(self, hook: str, **kwargs: Any) -> None:
        if self.plugin_mgr is None:
            return
        try:
            getattr(self.plugin_mgr.hook, hook)(**kwargs)
        except Exception:
            logger.exception("Plugin hook %s failed", hook)

    def _broadcast(self, event: str, data: Any) -> None:
        if self.app is not None:
            webapi.broadcast(self.app, event, data)

    async def _monitor_loop(self) -> None:
        """Detect client joins/leaves and a hotspot that died underneath us."""
        while True:
            try:
                await asyncio.sleep(MONITOR_INTERVAL)
                if self._active_config is None or self.backend is None:
                    continue
                status = await self.backend.get_status()
                if not status.active:
                    logger.error("Hotspot process exited unexpectedly; cleaning up")
                    async with self._lock:
                        if self._active_config is not None:
                            await self._stop_locked("hotspot_exited")
                    self._broadcast("status_update", webapi.serialize_status(
                        HotspotStatus(active=False, ssid="")))
                    continue

                current = {c.mac: c for c in await self.backend.get_connected_clients()}
                for mac, client in current.items():
                    if mac not in self._known_clients:
                        self._fire("on_client_connect", client=client)
                        self._broadcast("client_connected", webapi.serialize_client(client))
                        if self.dbus_iface is not None:
                            self.dbus_iface.notify_client_connected(client)
                for mac, client in self._known_clients.items():
                    if mac not in current:
                        self._fire("on_client_disconnect", client=client)
                        self._broadcast("client_disconnected", {"mac": mac})
                        if self.dbus_iface is not None:
                            self.dbus_iface.notify_client_disconnected(mac)
                self._known_clients = current
                if self.dbus_iface is not None:
                    self.dbus_iface.sync_properties(status)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Client monitor error")
