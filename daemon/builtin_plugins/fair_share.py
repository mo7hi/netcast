# BSD-2-Clause License
# Copyright (c) 2024 NetCast Contributors
"""Fair Share Plugin — Per-client bandwidth shaping using tc (HTB + fq_codel).

Implements equal bandwidth allocation across all connected clients using
Linux Traffic Control (tc). Uses HTB as the root qdisc with fq_codel leaf
qdiscs for low-latency, fair queuing.

Architecture:
  AP interface (wlan0)
    └── htb root (1:0) — total bandwidth cap
        ├── htb class 1:10 — client 192.168.12.2 (bw / N)
        │   └── fq_codel
        ├── htb class 1:11 — client 192.168.12.3 (bw / N)
        │   └── fq_codel
        └── ... (one class per client)

  For ingress (download): uses IFB (Intermediate Functional Block) device
  to redirect and shape incoming traffic.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.backends.base import ClientInfo, HotspotConfig

from daemon.hooks import hookimpl

logger = logging.getLogger("netcast.plugins.fair_share")


@dataclass
class TrafficClass:
    """A tc class for a single client."""
    class_id: str       # e.g., "1:10"
    client_ip: str
    rate_kbps: int
    ceil_kbps: int


class FairSharePlugin:
    """Per-client bandwidth shaping using tc HTB + fq_codel."""

    def __init__(self) -> None:
        self._ap_iface: str = ""
        self._ifb_iface: str = "ifb0"
        self._total_bw_kbps: int = 0
        self._classes: dict[str, TrafficClass] = {}  # ip -> TrafficClass
        self._next_minor: int = 10
        self._enabled: bool = False

    @hookimpl
    def on_hotspot_start(self, config: HotspotConfig, status: dict) -> None:
        """Set up root tc qdiscs when hotspot starts."""
        if not getattr(config, 'fair_share_enabled', False):
            return

        self._ap_iface = getattr(config, 'ap_interface', '') or status.get('ap_interface', '')
        self._total_bw_kbps = getattr(config, 'total_bandwidth_kbps', 0)
        self._enabled = True

        if not self._ap_iface:
            logger.warning("Fair Share: No AP interface found, disabling")
            self._enabled = False
            return

        # Auto-detect bandwidth if set to 0
        if self._total_bw_kbps <= 0:
            self._total_bw_kbps = 50000  # Default 50 Mbps if unknown
            logger.info("Fair Share: Using default 50 Mbps total bandwidth")

        asyncio.create_task(self._setup_root_qdiscs())

    @hookimpl
    def on_hotspot_stop(self, reason: str) -> None:
        """Remove all tc rules when hotspot stops."""
        if self._enabled:
            asyncio.create_task(self._teardown())

    @hookimpl
    def on_client_connect(self, client: ClientInfo) -> None:
        """Add a traffic class for the new client and redistribute bandwidth."""
        if self._enabled:
            asyncio.create_task(self._add_client(client.ip))

    @hookimpl
    def on_client_disconnect(self, client: ClientInfo) -> None:
        """Remove the client's traffic class and redistribute bandwidth."""
        if self._enabled:
            asyncio.create_task(self._remove_client(client.ip))

    async def _run(self, *args: str) -> tuple[int, str, str]:
        """Run a command and return (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode or 0, stdout.decode(), stderr.decode()

    async def _setup_root_qdiscs(self) -> None:
        """Set up HTB root qdisc on the AP interface."""
        iface = self._ap_iface
        total = self._total_bw_kbps

        logger.info(f"Fair Share: Setting up HTB on {iface} ({total} kbps total)")

        # Egress (upload) shaping on AP interface
        await self._run("tc", "qdisc", "del", "dev", iface, "root")
        await self._run(
            "tc", "qdisc", "add", "dev", iface, "root", "handle", "1:",
            "htb", "default", "99",
        )
        # Root class with total bandwidth
        await self._run(
            "tc", "class", "add", "dev", iface, "parent", "1:", "classid", "1:1",
            "htb", "rate", f"{total}kbit", "ceil", f"{total}kbit",
        )
        # Default class for unclassified traffic
        await self._run(
            "tc", "class", "add", "dev", iface, "parent", "1:1", "classid", "1:99",
            "htb", "rate", f"{total // 4}kbit", "ceil", f"{total}kbit",
        )
        await self._run(
            "tc", "qdisc", "add", "dev", iface, "parent", "1:99", "fq_codel",
        )

        # Ingress shaping via IFB device
        await self._run("modprobe", "ifb", "numifbs=1")
        await self._run("ip", "link", "set", "dev", self._ifb_iface, "up")
        await self._run("tc", "qdisc", "del", "dev", iface, "ingress")
        await self._run("tc", "qdisc", "add", "dev", iface, "ingress")
        await self._run(
            "tc", "filter", "add", "dev", iface, "parent", "ffff:",
            "protocol", "ip", "u32", "match", "u32", "0", "0",
            "action", "mirred", "egress", "redirect", "dev", self._ifb_iface,
        )
        # HTB on IFB for ingress shaping
        await self._run("tc", "qdisc", "del", "dev", self._ifb_iface, "root")
        await self._run(
            "tc", "qdisc", "add", "dev", self._ifb_iface, "root", "handle", "1:",
            "htb", "default", "99",
        )
        await self._run(
            "tc", "class", "add", "dev", self._ifb_iface, "parent", "1:", "classid", "1:1",
            "htb", "rate", f"{total}kbit", "ceil", f"{total}kbit",
        )

        logger.info("Fair Share: Root qdiscs configured")

    async def _add_client(self, ip: str) -> None:
        """Add tc class + filter for a new client."""
        if ip in self._classes:
            return

        minor = self._next_minor
        self._next_minor += 1
        class_id = f"1:{minor}"

        tc = TrafficClass(class_id=class_id, client_ip=ip, rate_kbps=0, ceil_kbps=0)
        self._classes[ip] = tc

        await self._redistribute()

    async def _remove_client(self, ip: str) -> None:
        """Remove tc class for a disconnected client."""
        tc = self._classes.pop(ip, None)
        if not tc:
            return

        # Delete the class (and its filter)
        for dev in [self._ap_iface, self._ifb_iface]:
            await self._run("tc", "class", "del", "dev", dev, "classid", tc.class_id)

        await self._redistribute()

    async def _redistribute(self) -> None:
        """Redistribute bandwidth equally among all clients."""
        n = len(self._classes)
        if n == 0:
            return

        per_client = max(self._total_bw_kbps // n, 64)  # Min 64 kbps
        logger.info(f"Fair Share: {n} clients, {per_client} kbps each")

        for ip, tc in self._classes.items():
            tc.rate_kbps = per_client
            tc.ceil_kbps = self._total_bw_kbps  # Allow burst to full speed

            for dev in [self._ap_iface, self._ifb_iface]:
                # Replace or add the class
                await self._run(
                    "tc", "class", "replace", "dev", dev,
                    "parent", "1:1", "classid", tc.class_id,
                    "htb", "rate", f"{per_client}kbit",
                    "ceil", f"{self._total_bw_kbps}kbit",
                )
                # fq_codel leaf qdisc
                await self._run(
                    "tc", "qdisc", "replace", "dev", dev,
                    "parent", tc.class_id, "fq_codel",
                )

            # Filter to match this client's IP
            minor_num = tc.class_id.split(":")[1]
            await self._run(
                "tc", "filter", "replace", "dev", self._ap_iface,
                "parent", "1:", "protocol", "ip", "prio", "1",
                "u32", "match", "ip", "dst", f"{ip}/32",
                "flowid", tc.class_id,
            )
            await self._run(
                "tc", "filter", "replace", "dev", self._ifb_iface,
                "parent", "1:", "protocol", "ip", "prio", "1",
                "u32", "match", "ip", "src", f"{ip}/32",
                "flowid", tc.class_id,
            )

    async def _teardown(self) -> None:
        """Remove all tc rules."""
        logger.info("Fair Share: Tearing down traffic shaping")
        await self._run("tc", "qdisc", "del", "dev", self._ap_iface, "root")
        await self._run("tc", "qdisc", "del", "dev", self._ap_iface, "ingress")
        await self._run("tc", "qdisc", "del", "dev", self._ifb_iface, "root")
        await self._run("ip", "link", "set", "dev", self._ifb_iface, "down")
        self._classes.clear()
        self._next_minor = 10
