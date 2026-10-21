# BSD-2-Clause License
# Copyright (c) 2024 NetCast Contributors
"""Status Monitor Plugin — Real-time bandwidth and client monitoring.

Provides a WebSocket-based real-time monitoring feed:
- Per-client bandwidth usage (read from /proc/net/dev and nftables counters)
- Total hotspot bandwidth
- Client connect/disconnect events
- System health (CPU, memory, temperature)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from daemon.backends.base import ClientInfo

from daemon.hooks import hookimpl

logger = logging.getLogger("netcast.plugins.status_monitor")


@dataclass
class InterfaceStats:
    """Network interface statistics from /proc/net/dev."""
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_packets: int = 0
    tx_packets: int = 0
    timestamp: float = 0.0


class StatusMonitorPlugin:
    """Real-time bandwidth and client monitoring."""

    def __init__(self) -> None:
        self._ap_iface: str = ""
        self._active: bool = False
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._poll_task: asyncio.Task | None = None
        self._last_stats: dict[str, InterfaceStats] = {}
        self._start_time: float = 0

    @hookimpl
    def register_api_routes(self, app: web.Application) -> None:
        """Register monitoring WebSocket and REST endpoints."""
        app.router.add_get("/api/monitor/ws", self._monitor_ws)
        app.router.add_get("/api/monitor/stats", self._get_stats)
        app.router.add_get("/api/monitor/health", self._get_health)

    @hookimpl
    def on_hotspot_start(self, config: object, status: dict) -> None:
        """Start monitoring when hotspot starts."""
        self._ap_iface = status.get("ap_interface", "")
        self._active = True
        self._start_time = time.time()
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(f"Status Monitor: Started monitoring {self._ap_iface}")

    @hookimpl
    def on_hotspot_stop(self, reason: str) -> None:
        """Stop monitoring when hotspot stops."""
        self._active = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    @hookimpl
    def on_client_connect(self, client: ClientInfo) -> None:
        """Broadcast client connection event."""
        asyncio.create_task(self._broadcast({
            "type": "client_connected",
            "data": {
                "ip": client.ip,
                "mac": client.mac,
                "hostname": client.hostname,
                "timestamp": time.time(),
            }
        }))

    @hookimpl
    def on_client_disconnect(self, client: ClientInfo) -> None:
        """Broadcast client disconnection event."""
        asyncio.create_task(self._broadcast({
            "type": "client_disconnected",
            "data": {
                "ip": client.ip,
                "mac": client.mac,
                "hostname": client.hostname,
                "timestamp": time.time(),
            }
        }))

    def _read_proc_net_dev(self, iface: str) -> InterfaceStats:
        """Read interface stats from /proc/net/dev."""
        try:
            with open("/proc/net/dev", "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if parts and parts[0].rstrip(":") == iface:
                        return InterfaceStats(
                            rx_bytes=int(parts[1]),
                            tx_bytes=int(parts[9]),
                            rx_packets=int(parts[2]),
                            tx_packets=int(parts[10]),
                            timestamp=time.time(),
                        )
        except (OSError, IndexError, ValueError):
            pass
        return InterfaceStats(timestamp=time.time())

    async def _poll_loop(self) -> None:
        """Poll interface stats and broadcast updates every 2 seconds."""
        while self._active:
            try:
                current = self._read_proc_net_dev(self._ap_iface)
                prev = self._last_stats.get(self._ap_iface)

                if prev and current.timestamp > prev.timestamp:
                    dt = current.timestamp - prev.timestamp
                    rx_rate = (current.rx_bytes - prev.rx_bytes) / dt  # bytes/sec
                    tx_rate = (current.tx_bytes - prev.tx_bytes) / dt

                    await self._broadcast({
                        "type": "bandwidth_update",
                        "data": {
                            "rx_rate_kbps": round(rx_rate * 8 / 1000, 1),
                            "tx_rate_kbps": round(tx_rate * 8 / 1000, 1),
                            "rx_total_mb": round(current.rx_bytes / 1048576, 2),
                            "tx_total_mb": round(current.tx_bytes / 1048576, 2),
                            "uptime_seconds": round(time.time() - self._start_time),
                        }
                    })

                self._last_stats[self._ap_iface] = current
                await asyncio.sleep(2)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Status Monitor: Poll error: {e}")
                await asyncio.sleep(5)

    async def _broadcast(self, message: dict) -> None:
        """Send message to all connected WebSocket clients."""
        data = json.dumps(message)
        dead: set[web.WebSocketResponse] = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(data)
            except (ConnectionError, RuntimeError):
                dead.add(ws)
        self._ws_clients -= dead

    async def _monitor_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket endpoint for real-time monitoring data."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)
        logger.debug("Status Monitor: WebSocket client connected")

        try:
            async for _ in ws:
                pass  # Read loop keeps connection alive
        finally:
            self._ws_clients.discard(ws)
        return ws

    async def _get_stats(self, request: web.Request) -> web.Response:
        """Get current interface statistics."""
        stats = self._read_proc_net_dev(self._ap_iface) if self._ap_iface else InterfaceStats()
        return web.json_response({
            "interface": self._ap_iface,
            "rx_bytes": stats.rx_bytes,
            "tx_bytes": stats.tx_bytes,
            "rx_packets": stats.rx_packets,
            "tx_packets": stats.tx_packets,
            "uptime_seconds": round(time.time() - self._start_time) if self._active else 0,
        })

    async def _get_health(self, request: web.Request) -> web.Response:
        """Get system health stats."""
        health: dict = {"status": "ok"}

        # CPU load
        try:
            with open("/proc/loadavg", "r") as f:
                parts = f.read().split()
                health["load_1m"] = float(parts[0])
                health["load_5m"] = float(parts[1])
        except (OSError, IndexError, ValueError):
            pass

        # Memory
        try:
            with open("/proc/meminfo", "r") as f:
                meminfo: dict[str, int] = {}
                for line in f:
                    parts = line.split()
                    if parts[0] in ("MemTotal:", "MemAvailable:"):
                        meminfo[parts[0].rstrip(":")] = int(parts[1])
                if "MemTotal" in meminfo and "MemAvailable" in meminfo:
                    health["mem_total_mb"] = meminfo["MemTotal"] // 1024
                    health["mem_available_mb"] = meminfo["MemAvailable"] // 1024
                    health["mem_used_pct"] = round(
                        100 * (1 - meminfo["MemAvailable"] / meminfo["MemTotal"]), 1
                    )
        except (OSError, IndexError, ValueError):
            pass

        # CPU temperature
        thermal = Path("/sys/class/thermal/thermal_zone0/temp")
        if thermal.exists():
            try:
                temp = int(thermal.read_text().strip()) / 1000
                health["cpu_temp_c"] = round(temp, 1)
            except (ValueError, OSError):
                pass

        return web.json_response(health)
