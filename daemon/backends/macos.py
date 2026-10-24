"""macOS backend ("assistant mode").

macOS has no supported API for turning on Internet Sharing from an app. So NetCast
opens the right System Settings pane, shows the exact steps and the name and password
to enter, then watches for the sharing bridge and reports devices like any other
backend. EXPERIMENTAL: not yet tested on a real Mac.
"""

import asyncio
import re
import shutil

from daemon.backends.base import (
    ClientInfo,
    HotspotConfig,
    HotspotStatus,
    NetworkingBackend,
)

BRIDGE = "bridge100"  # created by macOS while Internet Sharing is on
PANE = "x-apple.systempreferences:com.apple.Sharing-Settings.extension"

STEPS = [
    "In System Settings, open General, then Sharing, and select Internet Sharing (click the info button).",
    "Under “Share your connection from”, choose where your internet comes from.",
    "Under “To devices using”, turn on Wi-Fi, then click Wi-Fi Options.",
    "Enter the network name and password from this page. Choose WPA2/WPA3 Personal security.",
    "Click Done, then turn Internet Sharing on. NetCast notices and shows your devices.",
]


async def _run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="ignore")


class MacOSAssistBackend(NetworkingBackend):
    assistant = True
    steps = STEPS

    def __init__(self) -> None:
        self._config: HotspotConfig | None = None

    async def create_hotspot(self, config: HotspotConfig) -> HotspotStatus:
        config.validate()
        self._config = config
        if shutil.which("open"):
            await _run("open", PANE)
        return await self.get_status()

    async def stop_hotspot(self) -> None:
        self._config = None
        if shutil.which("open"):
            await _run("open", PANE)  # sharing can only be switched off by the user

    async def get_status(self) -> HotspotStatus:
        rc, out = await _run("ifconfig", BRIDGE)
        active = rc == 0 and "status: active" in out
        cfg = self._config
        if not active or cfg is None:
            return HotspotStatus(active=False, ssid=cfg.ssid if cfg else "")
        ip = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
        clients = await self.get_connected_clients()
        return HotspotStatus(
            active=True, ssid=cfg.ssid, password=cfg.password, band=cfg.band,
            source_interface="Internet Sharing", ap_interface=BRIDGE,
            gateway_ip=ip.group(1) if ip else "192.168.2.1", client_count=len(clients),
        )

    async def get_connected_clients(self) -> list[ClientInfo]:
        rc, out = await _run("arp", "-an", "-i", BRIDGE)
        clients = []
        for m in re.finditer(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]+) on", out):
            mac = ":".join(p.zfill(2) for p in m.group(2).split(":"))
            if mac != "ff:ff:ff:ff:ff:ff" and "incomplete" not in m.group(0):
                clients.append(ClientInfo(ip=m.group(1), mac=mac))
        return clients
