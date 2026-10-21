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

"""Nftables implementation of NetCast FirewallBackend."""

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path

from daemon.backends.base import validate_interface_name, validate_ip_address
from daemon.firewall.base import FirewallBackend

logger = logging.getLogger(__name__)

TABLE_NAME = "netcast"
TABLE_FAMILY = "inet"


class NftablesBackend(FirewallBackend):
    """Firewall and NAT management utilizing Linux nftables in an isolated table."""

    def __init__(self, nft_path: str | None = None) -> None:
        """Initialize Nftables backend.

        Args:
            nft_path: Optional explicit path to nft executable.
        """
        self._nft = nft_path or shutil.which("nft") or "/sbin/nft"
        self._source_iface: str = ""
        self._ap_iface: str = ""
        self._gateway_ip: str = ""
        self._captive_portal_active: bool = False
        self._captive_web_port: int = 8080

    async def _execute_nft(self, rules: str) -> None:
        """Apply rules atomically via 'nft -f -' using stdin.

        Args:
            rules: Nftables configuration script.

        Raises:
            RuntimeError: If nft returns a non-zero exit code.
        """
        proc = await asyncio.create_subprocess_exec(
            self._nft,
            "-f",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=rules.encode("utf-8"))
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="ignore").strip()
            logger.error(
                "nft command failed (exit %d): %s\nRules:\n%s", proc.returncode, err_msg, rules
            )
            raise RuntimeError(f"nft failed (exit {proc.returncode}): {err_msg}")

    async def _enable_ip_forwarding(self) -> None:
        """Enable IPv4 kernel packet forwarding."""
        try:
            ip_fwd_path = Path("/proc/sys/net/ipv4/ip_forward")
            if ip_fwd_path.exists():
                ip_fwd_path.write_text("1\n")
                return
        except OSError:
            pass

        # Fallback via sysctl command
        sysctl_bin = shutil.which("sysctl") or "/sbin/sysctl"
        proc = await asyncio.create_subprocess_exec(
            sysctl_bin,
            "-w",
            "net.ipv4.ip_forward=1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    def _build_ruleset(self) -> str:
        """Generate the complete ephemeral table inet netcast ruleset.

        Returns:
            Nftables script string.
        """
        lines = [
            f"table {TABLE_FAMILY} {TABLE_NAME} {{",
            "    set authenticated_clients {",
            "        type ipv4_addr",
            "        flags timeout",
            "    }",
            "",
            "    chain prerouting {",
            "        type nat hook prerouting priority dstnat; policy accept;",
        ]

        if self._captive_portal_active and self._ap_iface:
            lines.extend(
                [
                    f'        iifname "{self._ap_iface}" udp dport 53 redirect to :53',
                    f'        iifname "{self._ap_iface}" tcp dport 53 redirect to :53',
                    f'        iifname "{self._ap_iface}" ip saddr @authenticated_clients accept',
                    (
                        f'        iifname "{self._ap_iface}" tcp dport 80 '
                        f'redirect to :{self._captive_web_port}'
                    ),
                ]
            )

        lines.extend(
            [
                "    }",
                "",
                "    chain forward {",
                "        type filter hook forward priority filter; policy accept;",
                "        tcp flags syn tcp option maxseg size set rt mtu",
                "        ct state established,related accept",
            ]
        )

        if self._ap_iface and self._source_iface:
            if self._captive_portal_active:
                lines.extend(
                    [
                        (
                            f'        iifname "{self._ap_iface}" oifname "{self._source_iface}" '
                            "ip saddr @authenticated_clients accept"
                        ),
                        f'        iifname "{self._ap_iface}" tcp dport 443 drop',
                        f'        iifname "{self._ap_iface}" oifname "{self._source_iface}" drop',
                    ]
                )
            else:
                lines.append(
                    f'        iifname "{self._ap_iface}" oifname "{self._source_iface}" accept'
                )
            lines.append(
                f'        iifname "{self._source_iface}" oifname "{self._ap_iface}" accept'
            )

        lines.extend(
            [
                "    }",
                "",
                "    chain postrouting {",
                "        type nat hook postrouting priority srcnat; policy accept;",
            ]
        )

        if self._source_iface:
            lines.append(f'        oifname "{self._source_iface}" masquerade')

        lines.extend(
            [
                "    }",
                "}",
                "",
            ]
        )
        return "\n".join(lines)

    async def setup_nat(self, source_iface: str, ap_iface: str, gateway_ip: str) -> None:
        """Configure IPv4 forwarding, masquerading, and base routing between interfaces.

        Args:
            source_iface: Outbound network interface.
            ap_iface: Inbound access point interface.
            gateway_ip: Gateway IP on the AP interface.
        """
        validate_interface_name(source_iface)
        validate_interface_name(ap_iface)
        if not validate_ip_address(gateway_ip):
            raise ValueError(f"Invalid gateway IP: {gateway_ip}")

        self._source_iface = source_iface
        self._ap_iface = ap_iface
        self._gateway_ip = gateway_ip

        await self._enable_ip_forwarding()

        # Delete existing netcast table if present to ensure clean state
        with contextlib.suppress(Exception):
            await self.teardown_nat()

        ruleset = self._build_ruleset()
        logger.info("Applying nftables NAT rules for %s -> %s", ap_iface, source_iface)
        await self._execute_nft(ruleset)

    async def teardown_nat(self) -> None:
        """Atomically delete table inet netcast and all contained chains/rules."""
        logger.info("Deleting table %s %s", TABLE_FAMILY, TABLE_NAME)
        proc = await asyncio.create_subprocess_exec(
            self._nft,
            "delete",
            "table",
            TABLE_FAMILY,
            TABLE_NAME,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore").strip()
            # Ignore "No such file or directory" error when table does not exist
            if "No such file or directory" not in err and "does not exist" not in err:
                logger.warning("Error deleting table %s %s: %s", TABLE_FAMILY, TABLE_NAME, err)

        self._source_iface = ""
        self._ap_iface = ""
        self._gateway_ip = ""
        self._captive_portal_active = False

    async def setup_mss_clamp(self) -> None:
        """Ensure TCP MSS clamping rule is active in the forward chain."""
        # Built-in to _build_ruleset, but can also be applied directly if table exists
        cmd = [
            self._nft,
            "add",
            "rule",
            TABLE_FAMILY,
            TABLE_NAME,
            "forward",
            "tcp",
            "flags",
            "syn",
            "tcp",
            "option",
            "maxseg",
            "size",
            "set",
            "rt",
            "mtu",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def setup_captive_portal(self, gateway_ip: str, web_port: int) -> None:
        """Activate captive portal redirection rules and HTTPS drop."""
        if not validate_ip_address(gateway_ip):
            raise ValueError(f"Invalid gateway IP: {gateway_ip}")
        if web_port < 1 or web_port > 65535:
            raise ValueError(f"Invalid web port: {web_port}")

        self._gateway_ip = gateway_ip
        self._captive_web_port = web_port
        self._captive_portal_active = True

        ruleset = self._build_ruleset()
        logger.info("Rebuilding nftables with captive portal on port %d", web_port)
        await self._execute_nft(ruleset)

    async def authorize_client(self, client_ip: str, timeout_seconds: int = 0) -> None:
        """Add client IP to authenticated_clients set with optional timeout."""
        if not validate_ip_address(client_ip):
            raise ValueError(f"Invalid client IP: {client_ip}")

        element = (
            f"{client_ip} timeout {timeout_seconds}s"
            if timeout_seconds > 0
            else client_ip
        )

        cmd = [
            self._nft,
            "add",
            "element",
            TABLE_FAMILY,
            TABLE_NAME,
            "authenticated_clients",
            "{",
            element,
            "}",
        ]
        logger.info("Authorizing client %s (timeout: %ss)", client_ip, timeout_seconds)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"Failed to authorize client {client_ip}: {err}")

    async def setup_fair_share(self, ap_iface: str, total_bw_kbps: int, clients: list[str]) -> None:
        """Apply fair-share bandwidth management via Linux Traffic Control (tc)."""
        validate_interface_name(ap_iface)
        if total_bw_kbps <= 0:
            raise ValueError("Bandwidth must be positive")

        tc_bin = shutil.which("tc") or "/sbin/tc"

        # Apply CAKE or fq_codel queue discipline on the interface
        # CAKE provides automatic flow isolation and host fairness
        cmd_cake = [
            tc_bin,
            "qdisc",
            "replace",
            "dev",
            ap_iface,
            "root",
            "cake",
            "bandwidth",
            f"{total_bw_kbps}kbit",
            "diffserv4",
            "dual-srchost",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd_cake,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            # Fallback to fq_codel if CAKE module is unavailable
            logger.debug(
                "tc cake unavailable, falling back to fq_codel: %s", stderr.decode().strip()
            )
            cmd_fq = [
                tc_bin,
                "qdisc",
                "replace",
                "dev",
                ap_iface,
                "root",
                "fq_codel",
            ]
            proc_fq = await asyncio.create_subprocess_exec(
                *cmd_fq,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc_fq.communicate()

    async def save_snapshot(self) -> bytes:
        """Export serialized table inet netcast ruleset."""
        proc = await asyncio.create_subprocess_exec(
            self._nft,
            "list",
            "table",
            TABLE_FAMILY,
            TABLE_NAME,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout
        return b""

    async def restore_snapshot(self, data: bytes) -> None:
        """Restore ruleset from saved snapshot."""
        if not data:
            return
        await self._execute_nft(data.decode("utf-8"))
