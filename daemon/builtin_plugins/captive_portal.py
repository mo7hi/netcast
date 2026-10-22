# BSD-2-Clause License
# Copyright (c) 2024 NetCast Contributors
"""Captive Portal plugin (lifecycle hooks only).

The portal page, authorization endpoint and OS probe routes live in
``daemon.api.web`` and the firewall rules in ``daemon.firewall``. This plugin
only lets other plugins adjust the portal message through ``modify_captive_portal``.
"""
from __future__ import annotations

import logging
from typing import Any

from daemon.hooks import hookimpl

logger = logging.getLogger("netcast.plugins.captive_portal")


class CaptivePortalPlugin:
    @hookimpl
    def on_hotspot_start(self, config: Any, status: dict) -> None:
        if getattr(config, "captive_portal_enabled", False):
            logger.info("Captive portal active on %s", status.get("gateway_ip", "?"))

    @hookimpl
    def modify_captive_portal(self, portal_config: Any) -> None:
        """Extension point: plugins may adjust ``portal_config.message`` here."""
