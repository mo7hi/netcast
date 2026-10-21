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

"""Base classes and data structures for NetCast networking backends."""

import ipaddress
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

# Strict validation patterns
INTERFACE_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]{1,16}$")
MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")


def validate_interface_name(name: str) -> bool:
    """Validate a Linux network interface name.

    Args:
        name: Network interface identifier.

    Returns:
        True if the interface name conforms to Linux kernel naming rules.
    """
    return bool(INTERFACE_PATTERN.fullmatch(name))


def validate_mac_address(mac: str) -> bool:
    """Validate a MAC address format.

    Args:
        mac: Hardware address string.

    Returns:
        True if the MAC address is valid.
    """
    return bool(MAC_PATTERN.fullmatch(mac))


def validate_ip_address(ip: str) -> bool:
    """Validate an IPv4 address.

    Args:
        ip: IPv4 address string.

    Returns:
        True if valid IPv4.
    """
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ValueError:
        return False


@dataclass(slots=True)
class HotspotConfig:
    """Configuration specification for establishing a NetCast Wi-Fi hotspot."""

    ssid: str
    password: str | None = None
    band: str = "2.4GHz"
    channel: int = 0
    source_interface: str = ""
    ap_interface: str = ""
    source_type: str = "wifi"

    def validate(self) -> None:
        """Validate hotspot configuration parameters.

        Raises:
            ValueError: If any parameter fails validation constraints.
        """
        if not self.ssid or len(self.ssid.encode("utf-8")) > 32:
            raise ValueError("SSID must be between 1 and 32 UTF-8 bytes")
        if any(ord(c) < 32 or ord(c) == 127 for c in self.ssid):
            raise ValueError("SSID must not contain control characters")

        if self.password is not None:
            pwd_len = len(self.password)
            if pwd_len < 8 or pwd_len > 63:
                raise ValueError("WPA2 passphrase must be between 8 and 63 ASCII characters")
            if not (self.password.isascii() and self.password.isprintable()):
                raise ValueError("WPA2 passphrase must be printable ASCII")

        valid_bands = {"2.4GHz", "5GHz", "6GHz", "2.4", "5", "6", "bg", "a"}
        if self.band not in valid_bands:
            raise ValueError(f"Invalid band '{self.band}'. Must be one of {valid_bands}")

        if self.channel < 0 or self.channel > 196:
            raise ValueError(f"Invalid Wi-Fi channel: {self.channel}")

        if self.ap_interface and not validate_interface_name(self.ap_interface):
            raise ValueError(f"Invalid AP interface name: {self.ap_interface}")

        if self.source_interface and not validate_interface_name(self.source_interface):
            raise ValueError(f"Invalid source interface name: {self.source_interface}")

    @property
    def is_5ghz(self) -> bool:
        """True when the band selector means 5 GHz ('a' from the UI/CLI, or '5GHz')."""
        return self.band in ("a", "5", "5GHz")


@dataclass(slots=True)
class HotspotStatus:
    """Operational status and runtime state of an active or inactive hotspot."""

    active: bool
    ssid: str
    password: str | None = None
    band: str = "2.4GHz"
    channel: int = 0
    ap_interface: str = ""
    source_interface: str = ""
    gateway_ip: str = "192.168.12.1"
    client_count: int = 0


@dataclass(slots=True)
class ClientInfo:
    """Metadata and statistics for a station connected to the NetCast hotspot."""

    ip: str
    mac: str
    hostname: str | None = None
    connected_at: datetime | None = None
    rx_bytes: int = 0
    tx_bytes: int = 0


class NetworkingBackend(ABC):
    """Abstract base class defining the contract for NetCast hotspot networking backends."""

    @abstractmethod
    async def create_hotspot(self, config: HotspotConfig) -> HotspotStatus:
        """Create and start a Wi-Fi hotspot using the provided configuration.

        Args:
            config: Hotspot configuration parameters.

        Returns:
            HotspotStatus reflecting the active state of the created hotspot.
        """
        pass

    @abstractmethod
    async def stop_hotspot(self) -> None:
        """Deactivate and tear down the running hotspot, releasing all acquired resources."""
        pass

    @abstractmethod
    async def get_connected_clients(self) -> list[ClientInfo]:
        """Enumerate all client stations currently associated and leased on the hotspot.

        Returns:
            List of ClientInfo instances representing connected stations.
        """
        pass

    @abstractmethod
    async def get_status(self) -> HotspotStatus:
        """Query the current operational status of the hotspot.

        Returns:
            HotspotStatus containing runtime metrics and configuration.
        """
        pass
