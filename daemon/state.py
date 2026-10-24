"""Daemon orchestrator for NetCast.

Owns the networking backend, firewall backend, plugin hooks, D-Bus service and
client monitoring. This is the object passed to the web app and D-Bus service
as ``daemon_state``.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web

from daemon import platforms
from daemon.api import web as webapi
from daemon.backends.base import ClientInfo, HotspotConfig, HotspotStatus
from daemon.config import NetCastConfig, parse_config_dict
from daemon.fields import coerce_field
from daemon.persist import save_overlay
from daemon.scanner import (
    get_connected_channel,
    get_interface_capabilities,
    list_ethernet_interfaces,
    list_usb_tethering_interfaces,
    list_wifi_interfaces,
)
from daemon.virtual_iface import VIRTUAL_AP_NAME, create_virtual_ap, delete_virtual_ap

logger = logging.getLogger("netcast.state")

AUTH_TIMEOUT_SECONDS = 7200
MONITOR_INTERVAL = 3.0
ROUTE_FILE = Path("/proc/net/route")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
PLUGIN_ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
CELLULAR_RE = re.compile(r"^(wwan|wwp|rmnet|ccmni)")
VPN_RE = re.compile(r"^(wg|tun|tailscale|zt|nordlynx|proton)")


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


def _net_names() -> list[str]:
    try:
        return sorted(p.name for p in Path("/sys/class/net").iterdir())
    except OSError:
        return []


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


@dataclass
class Resolved:
    """Result of interface selection."""

    config: HotspotConfig
    virtual_parent: str = ""  # create a virtual AP on this Wi-Fi interface (single radio)
    source_handler: Any = None  # plugin-provided upstream (e.g. PPPoE)


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
        self.os = platforms.current()
        self.app: web.Application | None = None
        self.runner: web.AppRunner | None = None
        self.backend: Any = None
        self.firewall: Any = None
        self.dbus_bus: Any = None
        self.dbus_iface: Any = None
        # Plugin registries (filled by register_* hooks in start())
        self.sources: dict[str, Any] = {}
        self.ui_tabs: list[dict[str, Any]] = []
        self.skins: dict[str, Any] = {}
        self.plugin_descriptors: list[dict[str, Any]] = []
        self.blocked: set[str] = {
            m for m in str(config.plugins.get("_devices", {}).get("blocked", "")).split(",") if MAC_RE.match(m)
        }

        self._backends: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._active_config: HotspotConfig | None = None
        self._active_source: Any = None
        self._virtual_ap: str = ""
        self._ui_on_80 = False
        self._known_clients: dict[str, ClientInfo] = {}
        self._monitor_task: asyncio.Task[None] | None = None
        self._ui_site: web.TCPSite | None = None

    # ------------------------------------------------------------------ setup

    def attach(self, app: web.Application, runner: web.AppRunner) -> None:
        self.app = app
        self.runner = runner

    async def start(self) -> None:
        """Collect plugin registrations, start D-Bus (Linux) and the client monitor."""
        self._fire("register_source", registry=self.sources)
        self._fire("register_ui_tab", tabs=self.ui_tabs)
        self._fire("register_skin", skin_registry=self.skins)
        self._fire("register_plugin", plugins=self.plugin_descriptors)
        self.plugin_descriptors[:] = [
            d for d in self.plugin_descriptors if PLUGIN_ID_RE.match(str(d.get("id", "")))
        ]
        for d in self.plugin_descriptors:
            self._fire("configure_plugin", plugin_id=d["id"], values=self._plugin_values(d, mask=False))

        if self.os == "linux":
            try:
                from dbus_next import BusType

                from daemon.api.dbus_service import setup_dbus_service

                bus_env = os.environ.get("NETCAST_DBUS_BUS", "").lower()
                bus_type = BusType.SESSION if bus_env == "session" else BusType.SYSTEM
                self.dbus_bus, self.dbus_iface = await setup_dbus_service(self, bus_type=bus_type)
            except Exception as exc:
                logger.warning("D-Bus service unavailable (the netcast command will not work): %s", exc)
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

    def _save(self) -> None:
        try:
            save_overlay(self._cfg.to_dict())
        except OSError as exc:
            logger.warning("Configuration applied but could not be saved: %s", exc)

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Validate, apply and persist config changes (survives restarts)."""
        if not isinstance(updates, dict):
            raise ValueError("Config update must be a JSON object")
        updates = {k: v for k, v in updates.items() if k != "plugins"}  # plugins have their own API
        merged = _deep_merge(self._cfg.to_dict(), updates)
        try:
            self._cfg = parse_config_dict(merged)
        except TypeError as exc:
            raise ValueError(f"Unknown configuration key: {exc}") from exc
        self._save()
        return self._cfg.to_dict()

    # ----------------------------------------------------------------- plugins

    def _descriptor(self, plugin_id: str) -> dict[str, Any]:
        for d in self.plugin_descriptors:
            if d["id"] == plugin_id:
                return d
        raise KeyError(plugin_id)

    def _plugin_values(self, d: dict[str, Any], mask: bool) -> dict[str, Any]:
        stored = self._cfg.plugins.get(d["id"], {})
        values: dict[str, Any] = {"enabled": bool(stored.get("enabled", False))}
        for f in d.get("fields", []):
            v = stored.get(f["key"], f.get("default"))
            values[f["key"]] = "" if (mask and f.get("type") == "password") else v
        return values

    def get_plugins(self) -> list[dict[str, Any]]:
        out = []
        for d in self.plugin_descriptors:
            stored = self._cfg.plugins.get(d["id"], {})
            links = d.get("links") or []
            if callable(links):
                try:
                    links = links()
                except Exception:
                    links = []
            vals = self._plugin_values(d, mask=True)
            enabled = vals.pop("enabled")
            out.append({
                "id": d["id"],
                "title": d.get("title", d["id"]),
                "description": d.get("description", ""),
                "enabled": enabled,
                "needs_restart": bool(d.get("needs_restart", True)),
                "fields": d.get("fields", []),
                "values": vals,
                "secrets_set": {f["key"]: bool(stored.get(f["key"]))
                                for f in d.get("fields", []) if f.get("type") == "password"},
                "links": links,
            })
        return out

    def set_plugin(self, plugin_id: str, enabled: bool, values: dict[str, Any]) -> dict[str, Any]:
        d = self._descriptor(plugin_id)  # KeyError -> 404
        stored = dict(self._cfg.plugins.get(plugin_id, {}))
        stored["enabled"] = bool(enabled)
        for f in d.get("fields", []):
            if f["key"] not in values:
                continue
            raw = values[f["key"]]
            if f.get("type") == "password" and raw in (None, ""):
                continue  # blank means keep the saved secret
            stored[f["key"]] = coerce_field(f, raw)
        self._cfg.plugins[plugin_id] = stored
        self._save()
        self._fire("configure_plugin", plugin_id=plugin_id, values=self._plugin_values(d, mask=False))
        return next(p for p in self.get_plugins() if p["id"] == plugin_id)

    def portal_links(self) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        for d in self.plugin_descriptors:
            fn = d.get("portal_links")
            if callable(fn):
                with contextlib.suppress(Exception):
                    links += fn()
        return links

    # -------------------------------------------------------------- platform

    def platform_info(self) -> dict[str, Any]:
        steps: list[str] = []
        if self.os == "macos":
            from daemon.backends.macos import STEPS

            steps = list(STEPS)
        return {
            "os": self.os,
            "backend": type(self.backend).__name__.replace("Backend", "").lower() if self.backend else self._backend_kind(),
            "assistant": self.os == "macos",
            "steps": steps,
            "capabilities": platforms.capabilities(self.os),
        }

    def _backend_kind(self, need_hostapd: bool = False) -> str:
        if self.os == "windows":
            return "windows"
        if self.os == "macos":
            return "macos"
        choice = os.environ.get("NETCAST_BACKEND", "").lower()
        has_hostapd = bool(shutil.which("hostapd") and shutil.which("dnsmasq"))
        if need_hostapd or choice == "hostapd" or (choice != "nm" and has_hostapd):
            return "hostapd"
        return "nm"

    async def _get_backend(self, need_hostapd: bool = False) -> Any:
        if need_hostapd and not (shutil.which("hostapd") and shutil.which("dnsmasq")):
            raise RuntimeError("Wi-Fi repeater mode needs hostapd and dnsmasq (apt install hostapd dnsmasq)")
        kind = self._backend_kind(need_hostapd)
        if kind not in self._backends:
            if kind == "windows":
                from daemon.backends.windows import WindowsBackend

                self._backends[kind] = WindowsBackend()
            elif kind == "macos":
                from daemon.backends.macos import MacOSAssistBackend

                self._backends[kind] = MacOSAssistBackend()
            elif kind == "hostapd":
                from daemon.backends.hostapd import HostapdBackend

                self._backends[kind] = HostapdBackend()
            else:
                from daemon.backends.networkmanager import NetworkManagerBackend

                if not await NetworkManagerBackend.is_available():
                    raise RuntimeError("No usable engine: install hostapd + dnsmasq, or NetworkManager 1.16 or newer")
                self._backends[kind] = NetworkManagerBackend()
            logger.info("Using hotspot engine %s", type(self._backends[kind]).__name__)
        self.backend = self._backends[kind]
        return self.backend

    def _get_firewall(self) -> Any:
        if self.os != "linux":
            return None
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

    # -------------------------------------------------------------- selection

    async def _describe_wifi(self, names: set[str]) -> str:
        """Human-readable Wi-Fi capability summary for error messages."""
        if not shutil.which("iw"):
            return "The 'iw' tool is not installed (apt install iw)."
        if not names:
            return "No Wi-Fi interfaces were detected."
        parts = []
        for name in sorted(names):
            try:
                caps = await get_interface_capabilities(name)
                parts.append(
                    f"{name} (AP mode: {'yes' if caps.supports_ap else 'no'}, "
                    f"AP+STA: {'yes' if caps.supports_ap_sta else 'no'}, driver: {caps.driver})"
                )
            except Exception as exc:
                parts.append(f"{name} (capabilities unreadable: {exc})")
        return "Detected Wi-Fi interfaces: " + "; ".join(parts) + "."

    async def _find_ap(self, wifi_names: set[str], exclude: str) -> str:
        for name in sorted(wifi_names):
            if name == exclude:
                continue
            try:
                if (await get_interface_capabilities(name)).supports_ap:
                    return name
            except Exception:
                continue
        return ""

    async def _resolve(self, config: HotspotConfig) -> Resolved:
        """Choose source and AP interfaces; decide whether repeater mode is needed."""
        stype = (config.source_type or "auto").lower()
        if self.os != "linux":
            return Resolved(replace(config, source_type="auto"))

        wifi_names = {d.name for d in await list_wifi_interfaces() if d.name != VIRTUAL_AP_NAME}
        handler = self.sources.get(stype)
        if stype == "pppoe" and handler is None:
            raise RuntimeError("The PPPoE plugin is not loaded")
        ethernet = await list_ethernet_interfaces()
        usb = await list_usb_tethering_interfaces()
        names = _net_names()
        cellular = [n for n in names if CELLULAR_RE.match(n)]
        vpn = [n for n in names if VPN_RE.match(n)]

        source = config.source_interface
        if not source:
            default = _default_route_iface()
            if stype == "wifi":
                source = default if default in wifi_names else (sorted(wifi_names) or [""])[0]
            elif handler is not None:
                if not ethernet:
                    raise ValueError("Connect your ISP modem to an Ethernet port first")
                source = ethernet[0]
            elif stype == "ethernet" and ethernet:
                source = ethernet[0]
            elif stype == "usb" and usb:
                source = usb[0]
            elif stype == "cellular" and cellular:
                source = cellular[0]
            elif stype == "vpn" and vpn:
                source = vpn[0]
            else:
                source = default or (ethernet or usb or cellular or vpn or [""])[0]
            if not source:
                raise ValueError("No upstream internet interface found")

        uplink_is_wifi = source in wifi_names and handler is None
        ap = config.ap_interface
        virtual_parent = ""
        if ap and ap == source:
            if not uplink_is_wifi:
                raise ValueError("AP and upstream interface must differ")
            virtual_parent, ap = source, VIRTUAL_AP_NAME
        if not ap:
            ap = await self._find_ap(wifi_names, exclude=source)
        if not ap:
            if uplink_is_wifi:
                virtual_parent, ap = source, VIRTUAL_AP_NAME
            else:
                raise ValueError(
                    "No Wi-Fi adapter with AP mode found. " + await self._describe_wifi(wifi_names)
                )

        if virtual_parent:
            try:
                caps = await get_interface_capabilities(virtual_parent)
            except Exception as exc:
                raise ValueError(f"Cannot read capabilities of {virtual_parent}: {exc}") from exc
            if not (caps.supports_ap and caps.supports_ap_sta):
                raise ValueError(
                    f"Your only Wi-Fi adapter ({virtual_parent}) cannot host a hotspot while it "
                    "is also connected as a client. Use Ethernet, USB tethering or a second "
                    "Wi-Fi adapter as the internet source. " + await self._describe_wifi(wifi_names)
                )

        new_cfg = replace(config, source_interface=source, ap_interface=ap, source_type=stype)
        return Resolved(new_cfg, virtual_parent, handler)

    # --------------------------------------------------------------- lifecycle

    async def start_hotspot(self, config: HotspotConfig) -> HotspotStatus:
        async with self._lock:
            if self._active_config is not None:
                await self._stop_locked("restart")

            resolved = await self._resolve(config)
            cfg = resolved.config
            backend = await self._get_backend(need_hostapd=bool(resolved.virtual_parent))
            firewall = self._get_firewall()
            cleanup_cmds: list[list[str]] = []

            try:
                if resolved.source_handler is not None:
                    uplink = await resolved.source_handler.start(cfg, self._cfg)
                    self._active_source = resolved.source_handler
                    cfg = replace(cfg, source_interface=uplink)
                    cmd = getattr(resolved.source_handler, "cleanup_command", None)
                    if cmd:
                        cleanup_cmds.append(list(cmd))

                if resolved.virtual_parent:
                    channel = await get_connected_channel(resolved.virtual_parent)
                    if channel is None:
                        raise ValueError(
                            f"{resolved.virtual_parent} is not connected to a Wi-Fi network; "
                            "connect it first, the hotspot must share its channel"
                        )
                    await create_virtual_ap(resolved.virtual_parent, cfg.ap_interface)
                    self._virtual_ap = cfg.ap_interface
                    cleanup_cmds.append(["iw", "dev", cfg.ap_interface, "del"])
                    band = "a" if channel > 14 else "bg"
                    logger.info("Repeater mode: hotspot locked to upstream channel %d (%s)", channel, band)
                    cfg = replace(cfg, channel=channel, band=band)
                    await asyncio.sleep(0.5)  # let NetworkManager notice the new interface

                cfg.validate()
                extras = await self._dnsmasq_extras()
                if extras and type(backend).__name__ != "HostapdBackend":
                    logger.warning("DNS plugins need the hostapd engine; ignoring them for this hotspot")
                    extras = []
                backend.extra_dnsmasq = extras

                status = await backend.create_hotspot(cfg)
                if getattr(backend, "assistant", False):
                    self._active_config = cfg  # keep watching while the user finishes in System Settings
                    return status
                if not status.active:
                    raise RuntimeError("Hotspot did not become active")

                gateway = status.gateway_ip
                if firewall is not None:
                    await firewall.setup_nat(cfg.source_interface, cfg.ap_interface, gateway)
                    for mac in sorted(self.blocked):
                        with contextlib.suppress(Exception):
                            await firewall.block_mac(mac)
                    self._fire("register_firewall_rules", firewall=firewall, config=self._cfg)
                    apply_rules = getattr(firewall, "apply_plugin_rules", None)
                    if apply_rules is not None:
                        await apply_rules()
                    if self._cfg.captive_portal.enabled:
                        self._fire("modify_captive_portal", portal_config=self._cfg.captive_portal)
                        await firewall.setup_captive_portal(gateway, self._cfg.daemon.listen_port)
                await self._expose_ui(gateway, firewall)
            except Exception:
                await self._cleanup_quietly()
                raise

            self._active_config = cfg
            self._known_clients = {}
            self._record_dms(cfg, cleanup_cmds)
            status = await backend.get_status()
            if self.dbus_iface is not None:
                self.dbus_iface.sync_properties(status)
            self._fire(
                "on_hotspot_start",
                config=self._hook_config(cfg),
                status=webapi.serialize_status(status),
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
        if self._ui_site is not None:
            with contextlib.suppress(Exception):
                await self._ui_site.stop()
            self._ui_site = None
        self._ui_on_80 = False
        if self.firewall is not None:
            with contextlib.suppress(Exception):
                await self.firewall.teardown_nat()
        if self.backend is not None:
            with contextlib.suppress(Exception):
                await self.backend.stop_hotspot()
        if self._virtual_ap:
            with contextlib.suppress(Exception):
                await delete_virtual_ap(self._virtual_ap)
            self._virtual_ap = ""
        if self._active_source is not None:
            with contextlib.suppress(Exception):
                await self._active_source.stop()
            self._active_source = None

    async def _dnsmasq_extras(self) -> list[str]:
        """Let plugins add dnsmasq settings (private DNS upstream, blocklists...)."""
        lines: list[str] = []
        for result in self._fire_collect("extend_dnsmasq", lines=lines):
            if inspect.isawaitable(result):
                try:
                    await result
                except Exception:
                    logger.exception("A plugin failed while preparing DNS settings")
        return lines

    async def _expose_ui(self, gateway_ip: str, firewall: Any) -> None:
        """Serve the web UI on the hotspot address so a phone can control NetCast remotely."""
        port = self._cfg.daemon.listen_port
        host = self._cfg.daemon.listen_host
        if self.runner is not None and host not in ("0.0.0.0", "::", gateway_ip):
            try:
                site = web.TCPSite(self.runner, host=gateway_ip, port=port)
                await site.start()
                self._ui_site = site
            except OSError as exc:
                logger.warning("Could not serve the web UI on %s:%d: %s", gateway_ip, port, exc)
                return
        expose = getattr(firewall, "expose_ui", None)
        if expose is not None:
            try:
                await expose(gateway_ip, port)
                self._ui_on_80 = True
            except Exception as exc:
                logger.warning("Port 80 shortcut unavailable: %s", exc)

    def _record_dms(self, config: HotspotConfig, extra_cmds: list[list[str]]) -> None:
        """Give the dead man's switch enough state to clean up after a crash."""
        if self.dms is None or self.os != "linux":
            return
        pids = [
            p.pid
            for p in (
                getattr(self.backend, "_hostapd_proc", None),
                getattr(self.backend, "_dnsmasq_proc", None),
            )
            if p is not None and getattr(p, "pid", None)
        ]
        cmds = list(extra_cmds)
        if type(self.firewall).__name__ == "NftablesBackend":
            cmds.append(["nft", "delete", "table", "inet", "netcast"])
        try:
            self.dms.update_state("child_pids", pids)
            self.dms.update_state("iptables_cleanup", cmds)
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

    # ----------------------------------------------------------------- devices

    @staticmethod
    def _mac(mac: str) -> str:
        mac = mac.strip().lower().replace("-", ":")
        if not MAC_RE.match(mac):
            raise ValueError("That is not a valid device address")
        return mac

    async def kick_client(self, mac: str) -> None:
        mac = self._mac(mac)
        kick = getattr(self.backend, "kick_client", None)
        if kick is None:
            raise NotImplementedError("Disconnecting devices is not supported on this system")
        await kick(mac)

    async def block_client(self, mac: str, block: bool) -> None:
        mac = self._mac(mac)
        if self.os != "linux":
            raise NotImplementedError("Blocking devices is not supported on this system")
        fw = self.firewall
        if block:
            if fw is not None and self._active_config is not None:
                await fw.block_mac(mac)
            self.blocked.add(mac)
            with contextlib.suppress(Exception):
                await self.kick_client(mac)
        else:
            if fw is not None:
                with contextlib.suppress(Exception):
                    await fw.unblock_mac(mac)
            self.blocked.discard(mac)
        self._cfg.plugins["_devices"] = {"blocked": ",".join(sorted(self.blocked))}
        self._save()

    # ------------------------------------------------------------------ remote

    async def remote_info(self) -> dict[str, Any]:
        """Addresses a phone can open to control NetCast."""
        port = self._cfg.daemon.listen_port
        urls: list[dict[str, str]] = []
        if self._active_config is not None and self.backend is not None:
            status = await self.backend.get_status()
            gw = status.gateway_ip
            if self._ui_on_80:
                urls.append({"label": "From a phone connected to the hotspot", "url": f"http://{gw}/"})
            urls.append({"label": "Direct address", "url": f"http://{gw}:{port}/"})
        if self._cfg.daemon.listen_host in ("0.0.0.0", "::"):
            for iface in await webapi.get_network_interfaces():
                for addr in iface.get("ip_addresses", []):
                    ip = addr.split("/")[0]
                    if iface["name"] != "lo" and not ip.startswith("127."):
                        urls.append({"label": f"On your network ({iface['name']})", "url": f"http://{ip}:{port}/"})
        return {"urls": urls, "listen_host": self._cfg.daemon.listen_host}

    # --------------------------------------------------------------- preflight

    async def run_preflight(self, params: dict[str, str] | None = None) -> dict[str, Any]:
        """System checks plus hardware/driver checks for the proposed hotspot setup."""
        params = params or {}
        if self.os != "linux":
            backend = await self._get_backend()
            checks = await backend.preflight() if hasattr(backend, "preflight") else []
            return {"passed": all(c["passed"] for c in checks if c["required"]), "checks": checks}

        from daemon.preflight import run_preflight as run_suite

        base = await webapi.run_preflight_checks()
        checks = [c for c in base["checks"] if c["name"] in ("ip_forwarding", "kernel_modules")]
        try:
            proposed = HotspotConfig(
                ssid="preflight",
                band=params.get("band") or "bg",
                source_type=params.get("source_type") or "auto",
                source_interface=params.get("source_interface") or "",
                ap_interface=params.get("ap_interface") or "",
            )
            resolved = await self._resolve(proposed)
            cfg = resolved.config
            if resolved.virtual_parent:  # check the real radio, the virtual iface doesn't exist yet
                cfg = replace(cfg, ap_interface=resolved.virtual_parent)
            suite = await run_suite(cfg)
            for c in suite.checks:
                checks.append({
                    "name": c.name, "title": c.name, "passed": c.status == "pass",
                    "required": c.status == "fail", "message": c.message, "fix_hint": c.fix_hint,
                })
        except Exception as exc:
            checks.append({
                "name": "interface_selection", "title": "Interface selection", "passed": False,
                "required": True, "message": str(exc), "fix_hint": "",
            })
        return {
            "passed": all(c["passed"] for c in checks if c["required"]),
            "timestamp": base["timestamp"],
            "checks": checks,
        }

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

    def _fire_collect(self, hook: str, **kwargs: Any) -> list[Any]:
        if self.plugin_mgr is None:
            return []
        try:
            return [r for r in (getattr(self.plugin_mgr.hook, hook)(**kwargs) or []) if r is not None]
        except Exception:
            logger.exception("Plugin hook %s failed", hook)
            return []

    def _fire(self, hook: str, **kwargs: Any) -> None:
        self._fire_collect(hook, **kwargs)

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
                    if getattr(self.backend, "assistant", False):
                        continue  # waiting for the user to finish in System Settings
                    logger.error("Hotspot process exited unexpectedly; cleaning up")
                    async with self._lock:
                        if self._active_config is not None:
                            await self._stop_locked("hotspot_exited")
                    self._broadcast(
                        "status_update", webapi.serialize_status(HotspotStatus(active=False, ssid=""))
                    )
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

