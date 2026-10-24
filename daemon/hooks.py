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

"""Pluggy hook specifications and implementation markers for NetCast plugins.

Plugins implement these hook specifications to extend internet sources,
user interface tabs, routing policies, firewall configurations, and lifecycle events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pluggy
from aiohttp import web

if TYPE_CHECKING:
    from daemon.config import CaptivePortalConfig, HotspotConfig, NetCastConfig

PROJECT_NAME = "netcast"

hookspec = pluggy.HookspecMarker(PROJECT_NAME)
hookimpl = pluggy.HookimplMarker(PROJECT_NAME)


class NetCastHookSpecs:
    """Plugin hook specifications for NetCast daemon extension points."""

    @hookspec
    def register_source(self, registry: dict[str, Any]) -> None:
        """Register new internet source types with the daemon.

        :param registry: Mutable dictionary mapping source type identifier
                         (e.g., 'satellite', 'cellular') to handler factory or class.
        """

    @hookspec
    def register_ui_tab(self, tabs: list[dict[str, Any]]) -> None:
        """Register custom web UI navigation tabs.

        :param tabs: Mutable list of tab dictionaries containing 'id', 'title',
                     'icon', 'component', and 'endpoint'.
        """

    @hookspec
    def on_hotspot_start(self, config: HotspotConfig, status: dict[str, Any]) -> None:
        """Invoked immediately after the Wi-Fi hotspot has started successfully.

        :param config: The active HotspotConfig used to launch the AP.
        :param status: Initial runtime status dictionary containing interface name,
                       assigned IP subnet, and operating channel.
        """

    @hookspec
    def on_hotspot_stop(self, reason: str) -> None:
        """Invoked when the Wi-Fi hotspot is stopped.

        :param reason: Reason for hotspot termination (e.g., 'daemon_shutdown',
                       'user_request', 'uplink_lost').
        """

    @hookspec
    def on_client_connect(self, client: dict[str, Any]) -> None:
        """Invoked when a client station connects and is assigned an IP lease.

        :param client: Client metadata including 'mac', 'ip', 'hostname',
                       and 'lease_time'.
        """

    @hookspec
    def on_client_disconnect(self, client: dict[str, Any]) -> None:
        """Invoked when an active client station disconnects from the AP.

        :param client: Client metadata including 'mac', 'ip', and 'duration_sec'.
        """

    @hookspec
    def register_firewall_rules(self, firewall: Any, config: NetCastConfig) -> None:
        """Inject custom firewall rules (iptables/nftables) during network setup.

        :param firewall: Firewall management interface supporting rule registration.
        :param config: Full NetCast daemon configuration.
        """

    @hookspec
    def register_api_routes(self, app: web.Application) -> None:
        """Add custom aiohttp HTTP or WebSocket routes to the daemon web server.

        :param app: The aiohttp Application instance.
        """

    @hookspec
    def register_skin(self, skin_registry: dict[str, Any]) -> None:
        """Register custom CSS themes and stylesheets for the web UI.

        :param skin_registry: Mutable dictionary mapping skin IDs to CSS assets
                              or stylesheet file paths.
        """

    @hookspec
    def modify_captive_portal(self, portal_config: CaptivePortalConfig) -> None:
        """Inspect or customize captive portal behavior and splash screen configuration.

        :param portal_config: Active CaptivePortalConfig instance to inspect or adjust.
        """

    @hookspec
    def register_plugin(self, plugins: list[dict[str, Any]]) -> None:
        """Describe this plugin so it gets a settings card on the Plugins page.

        Append a dict: ``{"id", "title", "description", "fields": [...], "links": [...]}``.
        Field types: toggle, select (with ``options``), text, url, password, number.
        A field may carry ``show_if={"key": k, "value": v}`` or ``{"key": k, "not": v}``.
        ``links`` and ``portal_links`` may be callables returning ``[{"label", "url"}]``.
        """

    @hookspec
    def configure_plugin(self, plugin_id: str, values: dict[str, Any]) -> None:
        """Called at start and whenever settings are saved. Ignore ids that are not yours.

        :param plugin_id: The plugin whose settings changed.
        :param values: ``{"enabled": bool, <field key>: value, ...}`` with secrets included.
        """

    @hookspec
    def extend_dnsmasq(self, lines: list[str]) -> None:
        """Append dnsmasq config lines (upstream servers, blocklists) before the hotspot starts.

        An implementation may return a coroutine; NetCast awaits it before dnsmasq starts.
        """
