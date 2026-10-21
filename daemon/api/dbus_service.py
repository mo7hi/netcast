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

"""D-Bus service implementation for NetCast daemon using dbus-next.

Exposes the 'net.netcast.Daemon' interface on the system bus for privileged
management and CLI client interactions.
"""

import asyncio
from dataclasses import asdict
import json
import logging
from typing import Any

from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.errors import DBusError, ErrorType
from dbus_next.service import (
    PropertyAccess,
    ServiceInterface,
    dbus_property,
    method,
    signal,
)

from daemon.api.web import (
    get_clients_from_state,
    get_network_interfaces,
    get_status_from_state,
    run_preflight_checks,
    serialize_client,
    serialize_status,
)
from daemon.backends.base import ClientInfo, HotspotConfig, HotspotStatus

logger = logging.getLogger(__name__)

BUS_NAME = "net.netcast.Daemon"
OBJECT_PATH = "/net/netcast/Daemon"
INTERFACE_NAME = "net.netcast.Daemon"


class NetCastDaemonInterface(ServiceInterface):
    """D-Bus Service Interface exposing NetCast hotspot controls and events."""

    def __init__(self, daemon_state: Any) -> None:
        """Initialize the D-Bus interface with daemon state reference.

        Args:
            daemon_state: Host daemon state and networking backend manager.
        """
        super().__init__(INTERFACE_NAME)
        self._daemon_state = daemon_state
        self._is_active: bool = False
        self._ssid: str = ""
        self._client_count: int = 0

    # -----------------------------------------------------------------------
    # D-Bus Methods
    # -----------------------------------------------------------------------

    @method()
    async def StartHotspot(self, config_json: "s") -> "s":
        """Start a Wi-Fi hotspot given configuration JSON string.

        Args:
            config_json: JSON string with HotspotConfig fields.

        Returns:
            JSON string representing resulting HotspotStatus.

        Raises:
            DBusError: If configuration is invalid or hotspot activation fails.
        """
        logger.info("D-Bus method StartHotspot invoked")
        try:
            data = json.loads(config_json)
            config = HotspotConfig(
                ssid=str(data.get("ssid", "")).strip(),
                password=str(data.get("password", "")).strip() if data.get("password") else None,
                band=str(data.get("band", "bg")),
                channel=int(data.get("channel", 0)),
                source_interface=str(data.get("source_interface") or data.get("source") or "").strip(),
                ap_interface=str(data.get("ap_interface", "")).strip(),
                source_type=str(data.get("source_type", "wifi")),
            )
            config.validate()
        except Exception as exc:
            logger.error("Invalid hotspot configuration parameters: %s", exc)
            raise DBusError(ErrorType.INVALID_ARGS, f"Invalid configuration: {exc}") from exc

        try:
            if hasattr(self._daemon_state, "start_hotspot"):
                res = self._daemon_state.start_hotspot(config)
                status = await res if asyncio.iscoroutine(res) else res
            elif hasattr(self._daemon_state, "backend") and hasattr(self._daemon_state.backend, "create_hotspot"):
                status = await self._daemon_state.backend.create_hotspot(config)
            else:
                raise RuntimeError("Daemon state does not implement start_hotspot")

            self.sync_properties(status)
            self.HotspotStarted(status.ssid)
            return json.dumps(serialize_status(status))
        except Exception as exc:
            logger.exception("Failed to start hotspot via D-Bus: %s", exc)
            raise DBusError(ErrorType.FAILED, f"Failed to start hotspot: {exc}") from exc

    @method()
    async def StopHotspot(self) -> "b":
        """Stop the currently active Wi-Fi hotspot.

        Returns:
            True if hotspot deactivated successfully.

        Raises:
            DBusError: If deactivation encountered an unrecoverable failure.
        """
        logger.info("D-Bus method StopHotspot invoked")
        try:
            if hasattr(self._daemon_state, "stop_hotspot"):
                res = self._daemon_state.stop_hotspot()
                if asyncio.iscoroutine(res):
                    await res
            elif hasattr(self._daemon_state, "backend") and hasattr(self._daemon_state.backend, "stop_hotspot"):
                await self._daemon_state.backend.stop_hotspot()

            self._is_active = False
            self._ssid = ""
            self._client_count = 0
            self.emit_properties_changed({
                "IsActive": False,
                "SSID": "",
                "ClientCount": 0,
            })
            self.HotspotStopped()
            return True
        except Exception as exc:
            logger.exception("Failed to stop hotspot via D-Bus: %s", exc)
            raise DBusError(ErrorType.FAILED, f"Failed to stop hotspot: {exc}") from exc

    @method()
    async def GetStatus(self) -> "s":
        """Query and return current hotspot operational status.

        Returns:
            JSON string of HotspotStatus.
        """
        status = await get_status_from_state(self._daemon_state)
        self.sync_properties(status)
        return json.dumps(serialize_status(status))

    @method()
    async def GetClients(self) -> "s":
        """Query and return list of connected clients.

        Returns:
            JSON string containing list of ClientInfo objects.
        """
        clients = await get_clients_from_state(self._daemon_state)
        self._client_count = len(clients)
        self.emit_properties_changed({"ClientCount": self._client_count})
        return json.dumps([serialize_client(c) for c in clients])

    @method()
    async def RunPreflight(self) -> "s":
        """Execute pre-flight checks and return diagnostic results.

        Returns:
            JSON string containing preflight check outcomes.
        """
        if hasattr(self._daemon_state, "run_preflight"):
            res = self._daemon_state.run_preflight()
            result = await res if asyncio.iscoroutine(res) else res
        else:
            result = await run_preflight_checks()

        return json.dumps(result)

    @method()
    async def GetInterfaces(self) -> "s":
        """Enumerate host network interfaces.

        Returns:
            JSON string of interface metadata and state.
        """
        if hasattr(self._daemon_state, "get_interfaces"):
            res = self._daemon_state.get_interfaces()
            interfaces = await res if asyncio.iscoroutine(res) else res
        else:
            interfaces = await get_network_interfaces()

        return json.dumps(interfaces)

    # -----------------------------------------------------------------------
    # D-Bus Properties
    # -----------------------------------------------------------------------

    @dbus_property(PropertyAccess.READ)
    def IsActive(self) -> "b":
        """True if the Wi-Fi hotspot is currently active."""
        return self._is_active

    @dbus_property(PropertyAccess.READ)
    def SSID(self) -> "s":
        """SSID name of the active hotspot, or empty string."""
        return self._ssid

    @dbus_property(PropertyAccess.READ)
    def ClientCount(self) -> "u":
        """Number of stations currently connected."""
        return self._client_count

    # -----------------------------------------------------------------------
    # D-Bus Signals
    # -----------------------------------------------------------------------

    @signal()
    def HotspotStarted(self, ssid: "s") -> "s":
        """Emitted when hotspot activates successfully.

        Args:
            ssid: Broadcast SSID name.
        """
        return ssid

    @signal()
    def HotspotStopped(self) -> "":
        """Emitted when hotspot is deactivated."""
        return None

    @signal()
    def ClientConnected(self, client_json: "s") -> "s":
        """Emitted when a new client station associates and receives an IP.

        Args:
            client_json: Serialized ClientInfo JSON string.
        """
        return client_json

    @signal()
    def ClientDisconnected(self, mac: "s") -> "s":
        """Emitted when a client station disconnects.

        Args:
            mac: Hardware MAC address of the disconnected client.
        """
        return mac

    # -----------------------------------------------------------------------
    # Internal Helpers
    # -----------------------------------------------------------------------

    def sync_properties(self, status: HotspotStatus) -> None:
        """Update cached property values and emit PropertiesChanged if modified.

        Args:
            status: Fresh HotspotStatus instance.
        """
        changed: dict[str, Any] = {}
        if self._is_active != status.active:
            self._is_active = status.active
            changed["IsActive"] = self._is_active

        if self._ssid != status.ssid:
            self._ssid = status.ssid
            changed["SSID"] = self._ssid

        if self._client_count != status.client_count:
            self._client_count = status.client_count
            changed["ClientCount"] = self._client_count

        if changed:
            self.emit_properties_changed(changed)

    def notify_client_connected(self, client: ClientInfo | dict[str, Any]) -> None:
        """Emit ClientConnected signal and increment client count.

        Args:
            client: Connected station metadata.
        """
        if isinstance(client, ClientInfo):
            client_data = serialize_client(client)
        else:
            client_data = client

        self._client_count += 1
        self.emit_properties_changed({"ClientCount": self._client_count})
        self.ClientConnected(json.dumps(client_data))

    def notify_client_disconnected(self, mac: str) -> None:
        """Emit ClientDisconnected signal and decrement client count.

        Args:
            mac: MAC address of disconnected station.
        """
        if self._client_count > 0:
            self._client_count -= 1
        self.emit_properties_changed({"ClientCount": self._client_count})
        self.ClientDisconnected(mac)


async def setup_dbus_service(
    daemon_state: Any,
    bus: MessageBus | None = None,
    bus_type: BusType = BusType.SYSTEM,
) -> tuple[MessageBus, NetCastDaemonInterface]:
    """Connect to D-Bus, export the NetCastDaemonInterface, and request the bus name.

    Args:
        daemon_state: Daemon state manager.
        bus: Optional pre-connected MessageBus instance.
        bus_type: Bus type to connect to if bus is None (default SYSTEM).

    Returns:
        Tuple of (connected MessageBus, exported NetCastDaemonInterface).
    """
    if bus is None:
        bus = await MessageBus(bus_type=bus_type).connect()

    interface = NetCastDaemonInterface(daemon_state)
    bus.export(OBJECT_PATH, interface)
    await bus.request_name(BUS_NAME)
    logger.info("NetCast D-Bus service registered on %s as '%s'", bus_type.name, BUS_NAME)
    return bus, interface
