"""Virtual AP interface management for single-radio Wi-Fi repeater mode.

A Wi-Fi card that supports concurrent AP+STA can stay connected to the upstream
network (wlan0, managed) while also hosting the hotspot on a second virtual
interface (netcast0, type __ap) on the same radio. Both must use one channel.
"""

import asyncio
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger("netcast.virtual_iface")

VIRTUAL_AP_NAME = "netcast0"
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


def derive_mac(parent_mac: str) -> str:
    """Locally-administered MAC for the virtual interface, different from the parent's."""
    octets = [int(x, 16) for x in parent_mac.strip().split(":")]
    if len(octets) != 6:
        raise ValueError(f"Bad MAC address: {parent_mac!r}")
    octets[0] = (octets[0] | 0x02) & 0xFE
    octets[5] = (octets[5] + 1) & 0xFF
    return ":".join(f"{o:02x}" for o in octets)


def _bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for d in ("/sbin", "/usr/sbin", "/bin", "/usr/bin"):
        if Path(d, name).is_file():
            return str(Path(d, name))
    return name


async def _run(*args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="ignore"), err.decode(errors="ignore")


async def delete_virtual_ap(name: str = VIRTUAL_AP_NAME) -> None:
    """Remove the virtual interface if it exists (idempotent)."""
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid interface name: {name!r}")
    await _run(_bin("iw"), "dev", name, "del")


async def create_virtual_ap(parent: str, name: str = VIRTUAL_AP_NAME) -> str:
    """Create ``name`` as an AP-mode interface on the same radio as ``parent``."""
    for n in (parent, name):
        if not _NAME_RE.fullmatch(n):
            raise ValueError(f"Invalid interface name: {n!r}")

    await delete_virtual_ap(name)  # clear leftovers from a crash
    rc, _, err = await _run(_bin("iw"), "dev", parent, "interface", "add", name, "type", "__ap")
    if rc != 0:
        raise RuntimeError(f"Could not create virtual AP interface on {parent}: {err.strip()}")

    try:
        mac = derive_mac(Path(f"/sys/class/net/{parent}/address").read_text())
        rc, _, err = await _run(_bin("ip"), "link", "set", "dev", name, "address", mac)
        if rc != 0:
            logger.warning("Could not set MAC on %s (driver may not allow it): %s", name, err.strip())
    except (OSError, ValueError) as exc:
        logger.warning("Could not derive MAC for %s: %s", name, exc)
    return name
