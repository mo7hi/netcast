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

"""Network interface, wireless hardware, and system daemon scanner for NetCast."""

import asyncio
import contextlib
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from daemon.backends.base import validate_interface_name

logger = logging.getLogger(__name__)

# Known USB tethering and modem drivers
USB_DRIVERS = frozenset(
    {
        "cdc_ether",
        "rndis_host",
        "cdc_ncm",
        "ipheth",
        "r8152",
        "asix",
        "ax88179_178a",
        "qmi_wwan",
        "cdc_mbim",
    }
)


def find_binary(
    name: str, fallback_paths: tuple[str, ...] = ("/sbin", "/usr/sbin", "/bin", "/usr/bin")
) -> str:
    """Locate executable binary path with system fallback directories.

    Args:
        name: Binary filename.
        fallback_paths: Directories to check if not in system PATH.

    Returns:
        Resolved binary path.
    """
    found = shutil.which(name)
    if found:
        return found
    for directory in fallback_paths:
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return name


def is_dfs_channel(channel: int) -> bool:
    """Determine whether a Wi-Fi channel is subject to Dynamic Frequency Selection (DFS).

    Args:
        channel: IEEE 802.11 channel number.

    Returns:
        True if channel falls within U-NII-2A (52-64) or U-NII-2C (100-144).
    """
    return (52 <= channel <= 64) or (100 <= channel <= 144)


def freq_to_channel(freq_mhz: int) -> int | None:
    """Convert radio frequency in MHz to Wi-Fi channel number.

    Args:
        freq_mhz: Frequency in megahertz.

    Returns:
        Channel number, or None if unknown.
    """
    if freq_mhz == 2484:
        return 14
    if 2412 <= freq_mhz <= 2472:
        return (freq_mhz - 2407) // 5
    if 5160 <= freq_mhz <= 5885:
        return (freq_mhz - 5000) // 5
    if 5935 <= freq_mhz <= 7115:
        return (freq_mhz - 5950) // 5
    return None


@dataclass(slots=True)
class WifiInterface:
    """Wireless network interface attributes discovered on the host."""

    name: str
    phy: str
    mac: str
    type: str
    channel: int | None = None
    txpower: float | None = None


@dataclass(slots=True)
class InterfaceCapabilities:
    """Hardware and regulatory capabilities of a Wi-Fi physical device (phy)."""

    supports_ap: bool
    supports_ap_sta: bool
    max_channels: int
    supported_bands: list[str]
    driver: str
    dfs_channels: list[int] = field(default_factory=list)

    def is_dfs_channel(self, channel: int) -> bool:
        """Check if channel requires DFS radar detection on this hardware."""
        if channel in self.dfs_channels:
            return True
        return is_dfs_channel(channel)


async def list_wifi_interfaces() -> list[WifiInterface]:
    """Scan and enumerate all wireless network interfaces using 'iw dev'.

    Returns:
        List of WifiInterface instances representing active Wi-Fi adapters.
    """
    iw_bin = find_binary("iw")
    try:
        proc = await asyncio.create_subprocess_exec(
            iw_bin,
            "dev",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return []
    except Exception as exc:
        logger.debug("Failed executing iw dev: %s", exc)
        return []

    interfaces: list[WifiInterface] = []
    current_phy = ""
    current_iface = ""
    current_mac = ""
    current_type = ""
    current_chan: int | None = None
    current_txpower: float | None = None

    def commit_current() -> None:
        nonlocal \
            current_iface, \
            current_phy, \
            current_mac, \
            current_type, \
            current_chan, \
            current_txpower
        if current_iface and current_phy:
            interfaces.append(
                WifiInterface(
                    name=current_iface,
                    phy=current_phy,
                    mac=current_mac,
                    type=current_type or "managed",
                    channel=current_chan,
                    txpower=current_txpower,
                )
            )
        current_iface = ""
        current_mac = ""
        current_type = ""
        current_chan = None
        current_txpower = None

    for raw_line in stdout.decode("utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if raw_line.startswith("phy#"):
            commit_current()
            current_phy = raw_line.strip()
        elif line.startswith("Interface "):
            commit_current()
            current_iface = line.split()[1]
        elif line.startswith("addr "):
            current_mac = line.split()[1].lower()
        elif line.startswith("type "):
            current_type = line.split()[1]
        elif line.startswith("channel "):
            # Format: channel 5 (2432 MHz), width: 40 MHz...
            parts = line.split()
            with contextlib.suppress(IndexError, ValueError):
                current_chan = int(parts[1])
        elif line.startswith("txpower "):
            parts = line.split()
            with contextlib.suppress(IndexError, ValueError):
                current_txpower = float(parts[1])

    commit_current()
    return interfaces


async def get_interface_capabilities(iface: str) -> InterfaceCapabilities:
    """Inspect wireless hardware capabilities of an interface via sysfs and 'iw phy'.

    Args:
        iface: Wireless network interface name.

    Returns:
        InterfaceCapabilities for the interface.

    Raises:
        ValueError: If interface name is invalid.
        RuntimeError: If device capabilities cannot be queried.
    """
    if not validate_interface_name(iface):
            raise ValueError(f"Invalid interface name: {iface!r}")

    # 1. Detect driver from sysfs
    driver = "unknown"
    driver_link = Path(f"/sys/class/net/{iface}/device/driver")
    if driver_link.exists():
        with contextlib.suppress(OSError):
            driver = os.path.basename(os.path.realpath(driver_link))

    # 2. Determine phy name
    phy_name = ""
    phy_link = Path(f"/sys/class/net/{iface}/phy80211/name")
    if phy_link.exists():
        with contextlib.suppress(OSError):
            phy_name = phy_link.read_text(encoding="utf-8").strip()

    if not phy_name:
        # Fallback to scanning iw dev
        wifi_devs = await list_wifi_interfaces()
        for dev in wifi_devs:
            if dev.name == iface:
                phy_name = dev.phy.replace("phy#", "phy")
                break

    iw_bin = find_binary("iw")
    args = [iw_bin, "phy", phy_name, "info"] if phy_name else [iw_bin, "list"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except Exception as exc:
        raise RuntimeError(f"Failed to query wireless capabilities for {iface}: {exc}") from exc

    output = stdout.decode("utf-8", errors="ignore")

    # Parse supported interface modes
    supports_ap = False
    in_modes = False
    for line in output.splitlines():
        trimmed = line.strip()
        if "Supported interface modes:" in line:
            in_modes = True
            continue
        if in_modes:
            if trimmed.startswith("*"):
                mode = trimmed.lstrip("* ").strip()
                if mode == "AP":
                    supports_ap = True
            elif line and not line.startswith("\t") and not line.startswith(" "):
                in_modes = False

    # Parse virtual interface combinations for AP+STA
    supports_ap_sta = False
    max_channels = 1
    in_combinations = False
    for line in output.splitlines():
        trimmed = line.strip()
        if "valid interface combinations:" in line:
            in_combinations = True
            continue
        if in_combinations:
            if "#channels" in trimmed:
                chan_match = re.search(r"#channels\s*<=\s*(\d+)", trimmed)
                if chan_match:
                    chan_val = int(chan_match.group(1))
                    if chan_val > max_channels:
                        max_channels = chan_val
            if "managed" in trimmed and "AP" in trimmed:
                supports_ap_sta = True
            elif (
                line
                and not line.startswith("\t")
                and not line.startswith(" ")
                and not trimmed.startswith("*")
            ):
                in_combinations = False

    # Parse supported frequency bands and DFS channels
    bands: set[str] = set()
    dfs_channels: list[int] = []

    for line in output.splitlines():
        trimmed = line.strip()
        # Example: * 5260.0 MHz [52] (22.0 dBm) (no IR, radar detection)
        # or * 2412.0 MHz [1] (20.0 dBm)
        freq_match = re.search(r"\*\s*([0-9\.]+)\s*MHz\s*\[(\d+)\]", trimmed)
        if freq_match:
            freq = float(freq_match.group(1))
            ch = int(freq_match.group(2))
            if 2400 <= freq <= 2500:
                bands.add("2.4GHz")
            elif 5000 <= freq <= 5900:
                bands.add("5GHz")
            elif 5925 <= freq <= 7125:
                bands.add("6GHz")

            if "radar detection" in trimmed or "DFS" in trimmed or is_dfs_channel(ch):
                dfs_channels.append(ch)

    supported_bands = sorted(bands, reverse=False)

    return InterfaceCapabilities(
        supports_ap=supports_ap,
        supports_ap_sta=supports_ap_sta,
        max_channels=max_channels,
        supported_bands=supported_bands,
        driver=driver,
        dfs_channels=dfs_channels,
    )


async def list_ethernet_interfaces() -> list[str]:
    """List physical, non-virtual, non-wireless Ethernet interfaces.

    Returns:
        List of interface names (e.g. ['eno1', 'eth0']).
    """
    ethernet_ifaces: list[str] = []
    net_path = Path("/sys/class/net")
    if not net_path.is_dir():
        return []

    for entry in sorted(net_path.iterdir()):
        iface = entry.name
        if iface == "lo":
            continue

        real_path = os.path.realpath(entry)
        # Exclude purely virtual interfaces (bridges, docker, veth, tunnels)
        if "/devices/virtual/net/" in real_path:
            continue

        # Exclude wireless interfaces
        if (entry / "wireless").exists() or (entry / "phy80211").exists():
            continue

        # Exclude USB interfaces (handled by list_usb_tethering_interfaces)
        if "/usb" in real_path:
            continue

        # Check interface type (ARPHRD_ETHER is 1)
        type_file = entry / "type"
        if type_file.exists():
            try:
                if int(type_file.read_text().strip()) == 1:
                    ethernet_ifaces.append(iface)
            except ValueError:
                pass

    return ethernet_ifaces


async def list_usb_tethering_interfaces() -> list[str]:
    """Detect and enumerate USB tethering network interfaces.

    Returns:
        List of interface identifiers (e.g. ['usb0', 'rndis0', 'enp0s20f0u1']).
    """
    usb_ifaces: list[str] = []
    net_path = Path("/sys/class/net")
    if not net_path.is_dir():
        return []

    for entry in sorted(net_path.iterdir()):
        iface = entry.name
        if iface == "lo":
            continue

        real_path = os.path.realpath(entry)
        is_usb = "/usb" in real_path

        # Check driver
        driver_path = entry / "device" / "driver"
        driver = ""
        if driver_path.exists():
            with contextlib.suppress(OSError):
                driver = os.path.basename(os.path.realpath(driver_path))

        if (
            is_usb
            or driver in USB_DRIVERS
            or re.match(r"^(usb\d+|rndis\d+|enp\w+u\w+|enx[0-9a-fA-F]{12})$", iface)
        ) and not (entry / "wireless").exists() and not (entry / "phy80211").exists():
            usb_ifaces.append(iface)

    return usb_ifaces


async def get_connected_channel(iface: str) -> int | None:
    """Retrieve the Wi-Fi channel on which an interface is currently associated.

    Args:
        iface: Station interface name.

    Returns:
        Channel number if associated, or None if disconnected/unavailable.
    """
    if not validate_interface_name(iface):
            raise ValueError(f"Invalid interface name: {iface!r}")
    iw_bin = find_binary("iw")

    # Try 'iw dev <iface> link'
    try:
        proc = await asyncio.create_subprocess_exec(
            iw_bin,
            "dev",
            iface,
            "link",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            output = stdout.decode("utf-8", errors="ignore")
            # Example: freq: 2432 or Connected to ... (on wlp2s0)
            freq_match = re.search(r"freq:\s*(\d+)", output)
            if freq_match:
                return freq_to_channel(int(freq_match.group(1)))
    except Exception as exc:
        logger.debug("iw dev link failed for %s: %s", iface, exc)

    # Try 'iw dev <iface> info'
    try:
        proc = await asyncio.create_subprocess_exec(
            iw_bin,
            "dev",
            iface,
            "info",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            output = stdout.decode("utf-8", errors="ignore")
            chan_match = re.search(r"channel\s*(\d+)", output)
            if chan_match:
                return int(chan_match.group(1))
    except Exception as exc:
        logger.debug("iw dev info failed for %s: %s", iface, exc)

    return None


async def detect_network_manager() -> str:
    """Detect whether NetworkManager is installed and actively running.

    Returns:
        'running', 'stopped', or 'not_found'.
    """
    systemctl_bin = shutil.which("systemctl")
    if systemctl_bin:
        try:
            proc = await asyncio.create_subprocess_exec(
                systemctl_bin,
                "is-active",
                "NetworkManager",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            status = stdout.decode("utf-8").strip()
            if status == "active":
                return "running"
            elif status in ("inactive", "failed", "deactivating"):
                return "stopped"
        except Exception as exc:
            logger.debug("systemctl check failed: %s", exc)

    # Check pid / runtime socket
    if Path("/run/NetworkManager/NetworkManager.pid").exists():
        return "running"

    if shutil.which("NetworkManager") or Path("/usr/sbin/NetworkManager").exists():
        return "stopped"

    return "not_found"
