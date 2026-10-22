"""PPPoE source plugin: dial an ISP modem through the privileged helper unit."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from daemon.hooks import hookimpl

logger = logging.getLogger("netcast.plugins.pppoe")

UNIT = "netcast-pppoe.service"
IFNAME = "netcastppp0"
CONNECT_TIMEOUT = 30.0


async def _run(*args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="ignore"), err.decode(errors="ignore")


class PPPoESource:
    """Source handler: ``start`` returns the ppp interface, ``stop`` hangs up."""

    cleanup_command = ["systemctl", "stop", UNIT]

    def __init__(self, request_path: Path | None = None) -> None:
        runtime = Path(os.environ.get("NETCAST_RUNTIME_DIR", "/run/netcast"))
        self._request = request_path or runtime / "pppoe.json"

    async def start(self, config: Any, cfg: Any) -> str:
        creds = cfg.pppoe
        if not creds.username or not creds.password:
            raise ValueError("PPPoE username and password are required")
        nic = config.source_interface
        if not nic:
            raise ValueError("Select the Ethernet port connected to the ISP modem")

        await _run("ip", "link", "set", "dev", nic, "up")
        self._request.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._request, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {"nic": nic, "username": creds.username, "password": creds.password,
                 "mtu": creds.mtu},
                fh,
            )

        rc, _, err = await _run("systemctl", "start", "--no-ask-password", UNIT)
        if rc != 0:
            raise RuntimeError(f"Could not start the PPPoE helper unit: {err.strip()}")

        deadline = time.monotonic() + CONNECT_TIMEOUT
        while time.monotonic() < deadline:
            rc, out, _ = await _run("ip", "-4", "-o", "addr", "show", "dev", IFNAME)
            if rc == 0 and out.strip():
                logger.info("PPPoE link up on %s", IFNAME)
                return IFNAME
            rc, _, _ = await _run("systemctl", "is-active", "--quiet", UNIT)
            if rc != 0:
                break
            await asyncio.sleep(1.0)
        await self.stop()
        raise RuntimeError(
            "PPPoE did not connect. Check the modem cable and credentials "
            f"(journalctl -u {UNIT})"
        )

    async def stop(self) -> None:
        await _run("systemctl", "stop", UNIT)
        try:
            self._request.unlink()
        except OSError:
            pass


class PPPoEPlugin:
    @hookimpl
    def register_source(self, registry: dict[str, Any]) -> None:
        registry["pppoe"] = PPPoESource()
