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

"""Legacy iptables implementation of NetCast FirewallBackend."""

import asyncio
import logging
import shutil
from pathlib import Path

from daemon.backends.base import validate_interface_name, validate_ip_address
from daemon.firewall.base import FirewallBackend

logger = logging.getLogger(__name__)

CHAIN_NAT_PRE = "NETCAST_PREROUTING"
CHAIN_NAT_POST = "NETCAST_POSTROUTING"
CHAIN_FILTER_FWD = "NETCAST_FORWARD"


class IptablesBackend(FirewallBackend):
    """Legacy iptables backend maintaining dedicated custom chains for clean teardown."""

    def __init__(self, iptables_path: str | None = None) -> None:
        """Initialize iptables backend.

        Args:
            iptables_path: Path to iptables binary.
        """
        self._iptables = iptables_path or shutil.which("iptables") or "/sbin/iptables"
        self._iptables_save = shutil.which("iptables-save") or "/sbin/iptables-save"
        self._iptables_restore = shutil.which("iptables-restore") or "/sbin/iptables-restore"
        self._source_iface: str = ""
        self._ap_iface: str = ""
        self._gateway_ip: str = ""
        self._captive_portal_active: bool = False
        self._authorized_clients: set[str] = set()

    async def _run_iptables(self, *args: str) -> None:
        """Execute an iptables command.

        Args:
            args: Command arguments for iptables.
        """
        cmd = [self._iptables, *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore").strip()
            logger.debug("iptables command %s returned %d: %s", " ".join(cmd), proc.returncode, err)

    async def _enable_ip_forwarding(self) -> None:
        """Enable IPv4 kernel packet forwarding."""
        try:
            ip_fwd_path = Path("/proc/sys/net/ipv4/ip_forward")
            if ip_fwd_path.exists():
                ip_fwd_path.write_text("1\n")
                return
        except OSError:
            pass

        sysctl_bin = shutil.which("sysctl") or "/sbin/sysctl"
        proc = await asyncio.create_subprocess_exec(
            sysctl_bin,
            "-w",
            "net.ipv4.ip_forward=1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def setup_nat(self, source_iface: str, ap_iface: str, gateway_ip: str) -> None:
        """Set up IPv4 forwarding and masquerade rules using custom chains."""
        if not (validate_interface_name(source_iface) and validate_interface_name(ap_iface)):
            raise ValueError("Invalid interface name")
        if not validate_ip_address(gateway_ip):
            raise ValueError(f"Invalid gateway IP: {gateway_ip}")

        self._source_iface = source_iface
        self._ap_iface = ap_iface
        self._gateway_ip = gateway_ip

        await self._enable_ip_forwarding()
        await self.teardown_nat()

        # Create custom chains
        await self._run_iptables("-t", "nat", "-N", CHAIN_NAT_PRE)
        await self._run_iptables("-t", "nat", "-N", CHAIN_NAT_POST)
        await self._run_iptables("-t", "filter", "-N", CHAIN_FILTER_FWD)

        # Hook custom chains into top of standard chains
        await self._run_iptables("-t", "nat", "-I", "PREROUTING", "1", "-j", CHAIN_NAT_PRE)
        await self._run_iptables("-t", "nat", "-I", "POSTROUTING", "1", "-j", CHAIN_NAT_POST)
        await self._run_iptables("-t", "filter", "-I", "FORWARD", "1", "-j", CHAIN_FILTER_FWD)

        # Standard NAT rules
        await self._run_iptables(
            "-t",
            "nat",
            "-A",
            CHAIN_NAT_POST,
            "-o",
            source_iface,
            "-j",
            "MASQUERADE",
        )

        # Forwarding rules
        await self._run_iptables(
            "-t",
            "filter",
            "-A",
            CHAIN_FILTER_FWD,
            "-m",
            "state",
            "--state",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
        )
        await self._run_iptables(
            "-t",
            "filter",
            "-A",
            CHAIN_FILTER_FWD,
            "-i",
            ap_iface,
            "-o",
            source_iface,
            "-j",
            "ACCEPT",
        )
        await self._run_iptables(
            "-t",
            "filter",
            "-A",
            CHAIN_FILTER_FWD,
            "-i",
            source_iface,
            "-o",
            ap_iface,
            "-j",
            "ACCEPT",
        )

        # Mandatory MSS Clamping
        await self.setup_mss_clamp()

    async def teardown_nat(self) -> None:
        """Unlink and flush custom NetCast chains from iptables."""
        # Unhook from base chains
        await self._run_iptables("-t", "nat", "-D", "PREROUTING", "-j", CHAIN_NAT_PRE)
        await self._run_iptables("-t", "nat", "-D", "POSTROUTING", "-j", CHAIN_NAT_POST)
        await self._run_iptables("-t", "filter", "-D", "FORWARD", "-j", CHAIN_FILTER_FWD)

        # Remove MSS clamp from mangle if present
        await self._run_iptables(
            "-t",
            "mangle",
            "-D",
            "FORWARD",
            "-p",
            "tcp",
            "--tcp-flags",
            "SYN,RST",
            "SYN",
            "-j",
            "TCPMSS",
            "--clamp-mss-to-pmtu",
        )

        # Flush custom chains
        await self._run_iptables("-t", "nat", "-F", CHAIN_NAT_PRE)
        await self._run_iptables("-t", "nat", "-F", CHAIN_NAT_POST)
        await self._run_iptables("-t", "filter", "-F", CHAIN_FILTER_FWD)

        # Delete custom chains
        await self._run_iptables("-t", "nat", "-X", CHAIN_NAT_PRE)
        await self._run_iptables("-t", "nat", "-X", CHAIN_NAT_POST)
        await self._run_iptables("-t", "filter", "-X", CHAIN_FILTER_FWD)

        self._authorized_clients.clear()
        self._captive_portal_active = False

    async def setup_mss_clamp(self) -> None:
        """Apply TCP MSS clamping via mangle table."""
        # First ensure no duplicates
        await self._run_iptables(
            "-t",
            "mangle",
            "-D",
            "FORWARD",
            "-p",
            "tcp",
            "--tcp-flags",
            "SYN,RST",
            "SYN",
            "-j",
            "TCPMSS",
            "--clamp-mss-to-pmtu",
        )
        await self._run_iptables(
            "-t",
            "mangle",
            "-I",
            "FORWARD",
            "1",
            "-p",
            "tcp",
            "--tcp-flags",
            "SYN,RST",
            "SYN",
            "-j",
            "TCPMSS",
            "--clamp-mss-to-pmtu",
        )

    async def setup_captive_portal(self, gateway_ip: str, web_port: int) -> None:
        """Configure captive portal DNS/HTTP interception and port 443 block."""
        if not validate_ip_address(gateway_ip):
            raise ValueError(f"Invalid gateway IP: {gateway_ip}")
        if web_port < 1 or web_port > 65535:
            raise ValueError(f"Invalid web port: {web_port}")

        self._gateway_ip = gateway_ip
        self._captive_portal_active = True

        if not self._ap_iface:
            return

        # DNS hijacking to local DNS
        await self._run_iptables(
            "-t",
            "nat",
            "-A",
            CHAIN_NAT_PRE,
            "-i",
            self._ap_iface,
            "-p",
            "udp",
            "--dport",
            "53",
            "-j",
            "REDIRECT",
            "--to-ports",
            "53",
        )
        await self._run_iptables(
            "-t",
            "nat",
            "-A",
            CHAIN_NAT_PRE,
            "-i",
            self._ap_iface,
            "-p",
            "tcp",
            "--dport",
            "53",
            "-j",
            "REDIRECT",
            "--to-ports",
            "53",
        )

        # Drop port 443 HTTPS for fast captive portal detection
        await self._run_iptables(
            "-t",
            "filter",
            "-I",
            CHAIN_FILTER_FWD,
            "2",
            "-i",
            self._ap_iface,
            "-p",
            "tcp",
            "--dport",
            "443",
            "-j",
            "DROP",
        )

        # Redirect unauthenticated HTTP to captive portal
        await self._run_iptables(
            "-t",
            "nat",
            "-A",
            CHAIN_NAT_PRE,
            "-i",
            self._ap_iface,
            "-p",
            "tcp",
            "--dport",
            "80",
            "-j",
            "REDIRECT",
            "--to-ports",
            str(web_port),
        )

    async def authorize_client(self, client_ip: str, timeout_seconds: int = 0) -> None:
        """Allow a client IP past captive portal filtering."""
        if not validate_ip_address(client_ip):
            raise ValueError(f"Invalid client IP: {client_ip}")

        self._authorized_clients.add(client_ip)

        # Insert bypass rule in PREROUTING before the HTTP redirect
        await self._run_iptables(
            "-t",
            "nat",
            "-I",
            CHAIN_NAT_PRE,
            "1",
            "-s",
            client_ip,
            "-j",
            "ACCEPT",
        )

        # Insert forward accept rule at top of FORWARD
        await self._run_iptables(
            "-t",
            "filter",
            "-I",
            CHAIN_FILTER_FWD,
            "1",
            "-s",
            client_ip,
            "-j",
            "ACCEPT",
        )

        if timeout_seconds > 0:
            asyncio.create_task(self._deauthorize_after_timeout(client_ip, timeout_seconds))

    async def _deauthorize_after_timeout(self, client_ip: str, delay: int) -> None:
        """Revoke client authorization when timeout elapses."""
        await asyncio.sleep(delay)
        if client_ip in self._authorized_clients:
            self._authorized_clients.discard(client_ip)
            await self._run_iptables(
                "-t",
                "nat",
                "-D",
                CHAIN_NAT_PRE,
                "-s",
                client_ip,
                "-j",
                "ACCEPT",
            )
            await self._run_iptables(
                "-t",
                "filter",
                "-D",
                CHAIN_FILTER_FWD,
                "-s",
                client_ip,
                "-j",
                "ACCEPT",
            )

    async def setup_fair_share(self, ap_iface: str, total_bw_kbps: int, clients: list[str]) -> None:
        """Apply traffic shaping and fairness using Linux tc."""
        if not validate_interface_name(ap_iface):
            raise ValueError("Invalid interface name")
        tc_bin = shutil.which("tc") or "/sbin/tc"

        cmd = [
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
            "dual-dsthost",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            cmd_fq = [tc_bin, "qdisc", "replace", "dev", ap_iface, "root", "fq_codel"]
            proc_fq = await asyncio.create_subprocess_exec(
                *cmd_fq,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc_fq.communicate()

    async def save_snapshot(self) -> bytes:
        """Export iptables snapshot using iptables-save."""
        proc = await asyncio.create_subprocess_exec(
            self._iptables_save,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout if proc.returncode == 0 else b""

    async def restore_snapshot(self, data: bytes) -> None:
        """Restore iptables ruleset using iptables-restore."""
        if not data:
            return
        proc = await asyncio.create_subprocess_exec(
            self._iptables_restore,
            "--noflush",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate(input=data)
