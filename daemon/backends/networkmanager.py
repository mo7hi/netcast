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

"""NetworkManager D-Bus networking backend for NetCast."""

import asyncio
import logging
import re
from typing import Any, cast
import uuid
from datetime import UTC, datetime
from pathlib import Path

from dbus_next.aio.message_bus import MessageBus
from dbus_next.constants import BusType
from dbus_next.signature import Variant

from daemon.backends.base import (
    ClientInfo,
    HotspotConfig,
    HotspotStatus,
    NetworkingBackend,
    validate_interface_name,
)

logger = logging.getLogger(__name__)

NM_BUS_NAME = "org.freedesktop.NetworkManager"
NM_OBJECT_PATH = "/org/freedesktop/NetworkManager"
NM_INTERFACE = "org.freedesktop.NetworkManager"
NM_ACTIVE_CONN_INTERFACE = "org.freedesktop.NetworkManager.Connection.Active"
NM_DEVICE_INTERFACE = "org.freedesktop.NetworkManager.Device"
NM_IP4_CONFIG_INTERFACE = "org.freedesktop.NetworkManager.IP4Config"
NM_SETTINGS_CONN_INTERFACE = "org.freedesktop.NetworkManager.Settings.Connection"
DBUS_PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

# Active connection states defined by NetworkManager specification
NM_ACTIVE_CONNECTION_STATE_UNKNOWN = 0
NM_ACTIVE_CONNECTION_STATE_ACTIVATING = 1
NM_ACTIVE_CONNECTION_STATE_ACTIVATED = 2
NM_ACTIVE_CONNECTION_STATE_DEACTIVATING = 3
NM_ACTIVE_CONNECTION_STATE_DEACTIVATED = 4


class NetworkManagerBackend(NetworkingBackend):
    """Hotspot management backend leveraging NetworkManager D-Bus API."""

    def __init__(self) -> None:
        """Initialize NetworkManager backend state."""
        self._bus: MessageBus | None = None
        self._active_connection_path: str | None = None
        self._connection_path: str | None = None
        self._current_config: HotspotConfig | None = None
        self._gateway_ip: str = "10.42.0.1"

    async def _get_bus(self) -> MessageBus:
        """Obtain or reconnect the system D-Bus message bus."""
        if self._bus is None or not self._bus.connected:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        return self._bus

    @classmethod
    async def is_available(cls) -> bool:
        """Check if NetworkManager D-Bus service is reachable and version >= 1.16.

        Returns:
            True if NetworkManager is active and meets version requirements.
        """
        bus: MessageBus | None = None
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            introspection = await bus.introspect(NM_BUS_NAME, NM_OBJECT_PATH)
            obj = bus.get_proxy_object(NM_BUS_NAME, NM_OBJECT_PATH, introspection)
            prop_iface = obj.get_interface(DBUS_PROPERTIES_INTERFACE)
            version_var = await prop_iface.call_get(NM_INTERFACE, "Version")
            version_str = str(version_var.value)

            match = re.search(r"(\d+)\.(\d+)", version_str)
            if not match:
                return False

            major, minor = int(match.group(1)), int(match.group(2))
            return (major, minor) >= (1, 16)
        except Exception as exc:
            logger.debug("NetworkManager availability check failed: %s", exc)
            return False
        finally:
            if bus is not None:
                bus.disconnect()

    async def _find_device(self, iface_name: str) -> str:
        """Find the NetworkManager device object path for a given network interface.

        Args:
            iface_name: Target interface identifier (e.g. wlan0).

        Returns:
            D-Bus object path corresponding to the device.

        Raises:
            RuntimeError: If device cannot be located via NetworkManager.
        """
        if not validate_interface_name(iface_name):
            raise ValueError(f"Invalid interface name: {iface_name!r}")
        bus = await self._get_bus()
        introspection = await bus.introspect(NM_BUS_NAME, NM_OBJECT_PATH)
        nm_obj = bus.get_proxy_object(NM_BUS_NAME, NM_OBJECT_PATH, introspection)
        nm_iface = nm_obj.get_interface(NM_INTERFACE)

        # First attempt direct lookup via GetDeviceByIpIface
        try:
            dev_path = await nm_iface.call_get_device_by_ip_iface(iface_name)
            if dev_path and dev_path != "/":
                return str(dev_path)
        except Exception as exc:
            logger.debug(
                "GetDeviceByIpIface failed for %s, falling back to enumeration: %s", iface_name, exc
            )

        # Fallback: Enumerate all devices and inspect their interface property
        devices = await nm_iface.call_get_devices()
        for dev_path in devices:
            try:
                dev_intro = await bus.introspect(NM_BUS_NAME, dev_path)
                dev_obj = bus.get_proxy_object(NM_BUS_NAME, dev_path, dev_intro)
                prop_iface = dev_obj.get_interface(DBUS_PROPERTIES_INTERFACE)
                dev_iface_name_var = await prop_iface.call_get(NM_DEVICE_INTERFACE, "Interface")
                if dev_iface_name_var.value == iface_name:
                    return str(dev_path)
            except Exception as exc:
                logger.debug("Failed to inspect device %s: %s", dev_path, exc)

        raise RuntimeError(f"NetworkManager device for interface '{iface_name}' not found")

    async def create_hotspot(self, config: HotspotConfig) -> HotspotStatus:
        """Create and activate a Wi-Fi hotspot using NetworkManager AddAndActivateConnection.

        Args:
            config: Hotspot configuration parameters.

        Returns:
            HotspotStatus showing active status.
        """
        config.validate()

        if self._active_connection_path:
            await self.stop_hotspot()

        bus = await self._get_bus()
        dev_path = await self._find_device(config.ap_interface)

        # Build connection settings specification
        conn_id = f"NetCast-{config.ssid}"
        conn_uuid = str(uuid.uuid4())
        band_code = "a" if config.is_5ghz else "bg"

        settings: dict[str, dict[str, Variant]] = {
            "connection": {
                "id": Variant("s", conn_id),
                "uuid": Variant("s", conn_uuid),
                "type": Variant("s", "802-11-wireless"),
                "interface-name": Variant("s", config.ap_interface),
                "autoconnect": Variant("b", False),
            },
            "802-11-wireless": {
                "ssid": Variant("ay", config.ssid.encode("utf-8")),
                "mode": Variant("s", "ap"),
                "band": Variant("s", band_code),
            },
            "ipv4": {
                "method": Variant("s", "shared"),
            },
            "ipv6": {
                "method": Variant("s", "ignore"),
            },
        }

        if config.channel > 0:
            settings["802-11-wireless"]["channel"] = Variant("u", config.channel)

        if config.password:
            settings["802-11-wireless-security"] = {
                "key-mgmt": Variant("s", "wpa-psk"),
                "psk": Variant("s", config.password),
                "proto": Variant("as", ["rsn"]),
                "pairwise": Variant("as", ["ccmp"]),
                "group": Variant("as", ["ccmp"]),
            }

        nm_intro = await bus.introspect(NM_BUS_NAME, NM_OBJECT_PATH)
        nm_obj = bus.get_proxy_object(NM_BUS_NAME, NM_OBJECT_PATH, nm_intro)
        nm_iface = nm_obj.get_interface(NM_INTERFACE)

        logger.info(
            "Calling NM AddAndActivateConnection for SSID '%s' on %s",
            config.ssid,
            config.ap_interface,
        )
        conn_path, active_path = await nm_iface.call_add_and_activate_connection(
            settings, dev_path, "/"
        )

        self._connection_path = str(conn_path)
        self._active_connection_path = str(active_path)
        self._current_config = config

        # Wait briefly for active connection to reach ACTIVATED state
        for _ in range(20):
            await asyncio.sleep(0.5)
            status = await self.get_status()
            if status.active:
                break
        else:
            await self.stop_hotspot()
            raise RuntimeError("NetworkManager did not activate the hotspot within 10 seconds")

        # Attempt to determine assigned gateway IP
        await self._resolve_gateway_ip(bus, str(active_path))

        return await self.get_status()

    async def _resolve_gateway_ip(self, bus: MessageBus, active_path: str) -> None:
        """Determine the gateway IP address assigned to the hotspot interface."""
        try:
            act_intro = await bus.introspect(NM_BUS_NAME, active_path)
            act_obj = bus.get_proxy_object(NM_BUS_NAME, active_path, act_intro)
            prop = act_obj.get_interface(DBUS_PROPERTIES_INTERFACE)
            ip4_config_path_var = await prop.call_get(NM_ACTIVE_CONN_INTERFACE, "Ip4Config")
            ip4_config_path = str(ip4_config_path_var.value)

            if ip4_config_path and ip4_config_path != "/":
                ip_intro = await bus.introspect(NM_BUS_NAME, ip4_config_path)
                ip_obj = bus.get_proxy_object(NM_BUS_NAME, ip4_config_path, ip_intro)
                ip_prop = ip_obj.get_interface(DBUS_PROPERTIES_INTERFACE)
                address_data = await ip_prop.call_get(NM_IP4_CONFIG_INTERFACE, "AddressData")
                if address_data.value and len(address_data.value) > 0:
                    addr_entry = address_data.value[0]
                    if "address" in addr_entry:
                        self._gateway_ip = str(addr_entry["address"].value)
                        return
        except Exception as exc:
            logger.debug("Failed to resolve gateway IP via D-Bus: %s", exc)

        # Fallback standard NM shared network IP
        self._gateway_ip = "10.42.0.1"

    async def stop_hotspot(self) -> None:
        """Deactivate connection and delete temporary profile from NetworkManager."""
        bus = await self._get_bus()

        if self._active_connection_path:
            try:
                nm_intro = await bus.introspect(NM_BUS_NAME, NM_OBJECT_PATH)
                nm_obj = bus.get_proxy_object(NM_BUS_NAME, NM_OBJECT_PATH, nm_intro)
                nm_iface = nm_obj.get_interface(NM_INTERFACE)
                logger.info("Deactivating connection %s", self._active_connection_path)
                await nm_iface.call_deactivate_connection(self._active_connection_path)
            except Exception as exc:
                logger.warning(
                    "Error deactivating connection %s: %s", self._active_connection_path, exc
                )
            finally:
                self._active_connection_path = None

        if self._connection_path:
            try:
                conn_intro = await bus.introspect(NM_BUS_NAME, self._connection_path)
                conn_obj = bus.get_proxy_object(NM_BUS_NAME, self._connection_path, conn_intro)
                settings_conn = conn_obj.get_interface(NM_SETTINGS_CONN_INTERFACE)
                logger.info("Deleting NM connection settings %s", self._connection_path)
                await settings_conn.call_delete()
            except Exception as exc:
                logger.debug("Error deleting connection %s: %s", self._connection_path, exc)
            finally:
                self._connection_path = None

        self._current_config = None

    async def get_connected_clients(self) -> list[ClientInfo]:
        """Query connected clients parsing DHCP leases and kernel neighbor/ARP tables."""
        clients: dict[str, ClientInfo] = {}
        if not self._current_config:
            return []

        ap_iface = self._current_config.ap_interface

        # 1. Parse NetworkManager dnsmasq leases if available
        lease_paths = [
            Path(f"/var/lib/NetworkManager/dnsmasq-{ap_iface}.leases"),
            Path(f"/run/NetworkManager/dnsmasq-{ap_iface}.leases"),
            Path("/var/lib/misc/dnsmasq.leases"),
        ]

        for lp in lease_paths:
            if lp.is_file():
                try:
                    content = lp.read_text(encoding="utf-8")
                    for line in content.splitlines():
                        parts = line.strip().split()
                        # Format: <expiry_epoch> <mac> <ip> <hostname> <client_id>
                        if len(parts) >= 3:
                            expiry = int(parts[0])
                            mac = parts[1].lower()
                            ip = parts[2]
                            hostname = parts[3] if len(parts) > 3 and parts[3] != "*" else None
                            conn_time = datetime.fromtimestamp(expiry, tz=UTC)
                            clients[mac] = ClientInfo(
                                ip=ip,
                                mac=mac,
                                hostname=hostname,
                                connected_at=conn_time,
                            )
                except Exception as exc:
                    logger.debug("Failed reading lease file %s: %s", lp, exc)

        # 2. Parse /proc/net/arp to augment or discover clients
        try:
            arp_path = Path("/proc/net/arp")
            if arp_path.is_file():
                content = arp_path.read_text(encoding="utf-8")
                lines = content.splitlines()
                # Header: IP address HW type Flags HW address Mask Device
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) >= 6:
                        ip, flags, mac, dev = parts[0], parts[2], parts[3].lower(), parts[5]
                        if (
                            dev == ap_iface
                            and mac != "00:00:00:00:00:00"
                            and flags != "0x0"
                            and mac not in clients
                        ):
                            clients[mac] = ClientInfo(
                                ip=ip,
                                mac=mac,
                                hostname=None,
                                connected_at=datetime.now(UTC),
                            )
        except Exception as exc:
            logger.debug("Failed reading /proc/net/arp: %s", exc)

        # 3. Read station traffic stats using iw station dump if available
        try:
            proc = await asyncio.create_subprocess_exec(
                "iw",
                "dev",
                ap_iface,
                "station",
                "dump",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                current_mac: str | None = None
                for line in stdout.decode("utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.startswith("Station "):
                        current_mac = line.split()[1].lower()
                    elif current_mac and current_mac in clients:
                        if line.startswith("rx bytes:"):
                            clients[current_mac].rx_bytes = int(line.split(":")[1].strip())
                        elif line.startswith("tx bytes:"):
                            clients[current_mac].tx_bytes = int(line.split(":")[1].strip())
        except Exception as exc:
            logger.debug("Failed reading station dump: %s", exc)

        return list(clients.values())

    async def get_status(self) -> HotspotStatus:
        """Query NM ActiveConnection properties to return current operational status."""
        if not self._current_config:
            return HotspotStatus(
                active=False,
                ssid="",
                password=None,
                band="2.4GHz",
                channel=0,
                ap_interface="",
                source_interface="",
                gateway_ip=self._gateway_ip,
                client_count=0,
            )

        is_active = False
        if self._active_connection_path:
            try:
                bus = await self._get_bus()
                act_intro = await bus.introspect(NM_BUS_NAME, self._active_connection_path)
                act_obj = bus.get_proxy_object(NM_BUS_NAME, self._active_connection_path, act_intro)
                prop = act_obj.get_interface(DBUS_PROPERTIES_INTERFACE)
                state_var = await prop.call_get(NM_ACTIVE_CONN_INTERFACE, "State")
                state = int(state_var.value)
                is_active = state == NM_ACTIVE_CONNECTION_STATE_ACTIVATED
            except Exception as exc:
                logger.debug("Failed to query active connection state: %s", exc)
                is_active = False

        clients = await self.get_connected_clients() if is_active else []

        return HotspotStatus(
            active=is_active,
            ssid=self._current_config.ssid,
            password=self._current_config.password,
            band=self._current_config.band,
            channel=self._current_config.channel,
            ap_interface=self._current_config.ap_interface,
            source_interface=self._current_config.source_interface,
            gateway_ip=self._gateway_ip,
            client_count=len(clients),
        )
