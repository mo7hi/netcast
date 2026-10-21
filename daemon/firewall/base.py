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

"""Abstract base class and interface for NetCast firewall backends."""

from abc import ABC, abstractmethod


class FirewallBackend(ABC):
    """Abstract interface defining required firewall, NAT, and traffic control operations."""

    @abstractmethod
    async def setup_nat(self, source_iface: str, ap_iface: str, gateway_ip: str) -> None:
        """Configure IPv4 forwarding, masquerading, and routing between interfaces.

        Args:
            source_iface: Outbound internet-facing network interface (e.g. eth0, wlan1).
            ap_iface: Inbound access point interface (e.g. wlan0, ap0).
            gateway_ip: Local gateway IPv4 address assigned to the AP interface.
        """
        pass

    @abstractmethod
    async def teardown_nat(self) -> None:
        """Tear down all NAT, forwarding, and related firewall rules created by NetCast."""
        pass

    @abstractmethod
    async def setup_mss_clamp(self) -> None:
        """Configure TCP MSS clamping to path MTU to prevent packet fragmentation over PPPoE."""
        pass

    @abstractmethod
    async def setup_captive_portal(self, gateway_ip: str, web_port: int) -> None:
        """Establish captive portal firewall redirects and access restrictions.

        Diverts DNS queries to local gateway, redirects unauthenticated HTTP to web_port,
        and drops HTTPS port 443 so client captive portal detection triggers promptly.

        Args:
            gateway_ip: IP address of the local captive portal server.
            web_port: Local HTTP listening port serving the captive portal UI.
        """
        pass

    @abstractmethod
    async def authorize_client(self, client_ip: str, timeout_seconds: int = 0) -> None:
        """Grant a client station full internet access, bypassing captive portal restrictions.

        Args:
            client_ip: IPv4 address of the authenticated client station.
            timeout_seconds: Optional duration before authorization expires (0 = no timeout).
        """
        pass

    @abstractmethod
    async def setup_fair_share(self, ap_iface: str, total_bw_kbps: int, clients: list[str]) -> None:
        """Configure dynamic bandwidth distribution and fair sharing across connected clients.

        Args:
            ap_iface: Target access point interface.
            total_bw_kbps: Total allocated bandwidth in kilobits per second.
            clients: List of active client IP addresses to participate in bandwidth allocation.
        """
        pass

    @abstractmethod
    async def save_snapshot(self) -> bytes:
        """Serialize and export the current firewall state ruleset.

        Returns:
            Byte representation of the active ruleset.
        """
        pass

    @abstractmethod
    async def restore_snapshot(self, data: bytes) -> None:
        """Restore firewall state from a previously saved snapshot.

        Args:
            data: Serialized ruleset bytes.
        """
        pass
