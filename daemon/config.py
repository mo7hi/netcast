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

"""Configuration parser and data models for NetCast daemon.

Parses TOML configuration files using standard library tomllib with fallback
from /etc/netcast/config.toml to ~/.config/netcast/config.toml.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("netcast.config")

# System and user fallback configuration paths
SYSTEM_CONFIG_PATH = Path("/etc/netcast/config.toml")
USER_CONFIG_PATH = Path.home() / ".config" / "netcast" / "config.toml"

# Validation patterns
VALID_BANDS: frozenset[str] = frozenset({"bg", "a"})
VALID_SOURCE_TYPES: frozenset[str] = frozenset({"auto", "ethernet", "wifi", "pppoe", "usb"})
SSID_PATTERN = re.compile(r"^.{1,32}$")
INTERFACE_PATTERN = re.compile(r"^[a-zA-Z0-9_.:-]{1,16}$")


class ConfigError(ValueError):
    """Raised when configuration validation fails."""


@dataclass
class HotspotConfig:
    """Wi-Fi Access Point configuration settings."""

    ssid: str = "NetCast-Hotspot"
    password: str = ""
    band: str = "bg"
    channel: str | int = "auto"

    def validate(self) -> None:
        """Validate hotspot settings against operational constraints."""
        if not self.ssid or not SSID_PATTERN.match(self.ssid):
            raise ConfigError(
                f"Invalid hotspot SSID {self.ssid!r}: must be 1 to 32 characters long."
            )

        if self.password and not (8 <= len(self.password) <= 63):
            raise ConfigError(
                "Invalid hotspot password: WPA2-PSK requires 8 to 63 characters "
                "(or empty for open)."
            )

        if self.band.lower() not in VALID_BANDS:
            raise ConfigError(
                f"Invalid hotspot band {self.band!r}: must be one of {sorted(VALID_BANDS)}."
            )

        if isinstance(self.channel, str):
            val = self.channel.strip().lower()
            if val != "auto" and (not val.isdigit() or int(val) > 196):
                raise ConfigError(
                    f"Invalid hotspot channel {self.channel!r}: must be 'auto' or 1..196."
                )
        elif isinstance(self.channel, int):
            if self.channel < 0 or self.channel > 196:
                raise ConfigError(
                    f"Invalid hotspot channel {self.channel}: must be 0 (auto) or 1..196."
                )
        else:
            raise ConfigError(
                f"Hotspot channel must be 'auto' or integer, got {type(self.channel).__name__}."
            )


@dataclass
class NetworkConfig:
    """Uplink internet source configuration."""

    source_type: str = "ethernet"
    source_interface: str = "eth0"

    def validate(self) -> None:
        """Validate network uplink settings."""
        if self.source_type.lower() not in VALID_SOURCE_TYPES:
            raise ConfigError(
                f"Invalid network source_type {self.source_type!r}: "
                f"must be one of {sorted(VALID_SOURCE_TYPES)}."
            )

        if self.source_interface and not INTERFACE_PATTERN.match(self.source_interface):
            raise ConfigError(
                f"Invalid source_interface {self.source_interface!r}: "
                "must be a valid Linux network interface name (1-16 chars)."
            )


@dataclass
class DaemonConfig:
    """Core daemon server and environment settings."""

    listen_host: str = "127.0.0.1"
    listen_port: int = 8384
    theme: str = "default-dark"
    log_level: str = "info"

    def validate(self) -> None:
        """Validate daemon web server settings."""
        if not self.listen_host:
            raise ConfigError("Daemon listen_host cannot be empty.")

        if not (1 <= self.listen_port <= 65535):
            raise ConfigError(
                f"Invalid listen_port {self.listen_port}: must be between 1 and 65535."
            )

        if not self.theme or len(self.theme) > 64:
            raise ConfigError(f"Invalid theme name {self.theme!r}.")


@dataclass
class FairShareConfig:
    """Traffic shaping and bandwidth fair-sharing configuration."""

    enabled: bool = False
    total_bandwidth_kbps: int = 10000

    def validate(self) -> None:
        """Validate fair share traffic control settings."""
        if not isinstance(self.enabled, bool):
            raise ConfigError(f"Fair share 'enabled' must be boolean, got {self.enabled!r}.")

        if self.total_bandwidth_kbps < 0:
            raise ConfigError(
                f"total_bandwidth_kbps must be 0 (auto) or positive, got {self.total_bandwidth_kbps}."
            )


@dataclass
class CaptivePortalConfig:
    """Captive portal authentication and splash screen configuration."""

    enabled: bool = False
    message: str = "Welcome to NetCast"

    def validate(self) -> None:
        """Validate captive portal settings."""
        if not isinstance(self.enabled, bool):
            raise ConfigError(f"Captive portal 'enabled' must be boolean, got {self.enabled!r}.")

        if len(self.message) > 1024:
            raise ConfigError("Captive portal message is too long (maximum 1024 characters).")


@dataclass
class PPPoEConfig:
    """ISP credentials for PPPoE upstream."""

    username: str = ""
    password: str = ""
    mtu: int = 1492

    def validate(self) -> None:
        """Validate PPPoE settings."""
        for what, val in (("username", self.username), ("password", self.password)):
            if len(val) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in val):
                raise ConfigError(f"Invalid PPPoE {what}: max 255 chars, no control characters.")
        if not (576 <= self.mtu <= 1500):
            raise ConfigError(f"Invalid PPPoE mtu {self.mtu}: must be 576..1500.")


@dataclass
class NetCastConfig:
    """Complete NetCast configuration structure."""

    hotspot: HotspotConfig = field(default_factory=HotspotConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    fair_share: FairShareConfig = field(default_factory=FairShareConfig)
    captive_portal: CaptivePortalConfig = field(default_factory=CaptivePortalConfig)
    pppoe: PPPoEConfig = field(default_factory=PPPoEConfig)

    def validate(self) -> None:
        """Validate all configuration sections."""
        self.hotspot.validate()
        self.network.validate()
        self.daemon.validate()
        self.fair_share.validate()
        self.captive_portal.validate()
        self.pppoe.validate()

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary."""
        return asdict(self)


def find_config_file(custom_path: Path | str | None = None) -> Path | None:
    """Locate the configuration file following precedence rules.

    Precedence:
    1. custom_path (if explicitly provided)
    2. NETCAST_CONFIG environment variable
    3. /etc/netcast/config.toml
    4. ~/.config/netcast/config.toml

    :param custom_path: Optional explicit path to configuration file.
    :return: Resolved Path if found, otherwise None.
    """
    if custom_path:
        p = Path(custom_path).expanduser().resolve()
        if p.is_file():
            return p
        logger.warning("Specified config path does not exist: %s", p)
        return None

    env_path = os.environ.get("NETCAST_CONFIG")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if p.is_file():
            return p
        logger.warning("Config path from NETCAST_CONFIG does not exist: %s", p)

    if SYSTEM_CONFIG_PATH.is_file():
        return SYSTEM_CONFIG_PATH

    if USER_CONFIG_PATH.is_file():
        return USER_CONFIG_PATH

    return None


def parse_config_dict(data: dict[str, Any]) -> NetCastConfig:
    """Construct and validate NetCastConfig from a dictionary.

    :param data: Parsed TOML data dictionary.
    :return: Validated NetCastConfig instance.
    :raises ConfigError: If configuration values fail validation.
    """
    hotspot_data = data.get("hotspot", {})
    network_data = data.get("network", {})
    daemon_data = data.get("daemon", {})
    fair_share_data = data.get("fair_share", {})
    captive_portal_data = data.get("captive_portal", {})
    pppoe_data = data.get("pppoe", {})

    cfg = NetCastConfig(
        hotspot=HotspotConfig(**hotspot_data),
        network=NetworkConfig(**network_data),
        daemon=DaemonConfig(**daemon_data),
        fair_share=FairShareConfig(**fair_share_data),
        captive_portal=CaptivePortalConfig(**captive_portal_data),
        pppoe=PPPoEConfig(**pppoe_data),
    )
    cfg.validate()
    return cfg


def load_config(config_path: Path | str | None = None) -> NetCastConfig:
    """Load and parse the NetCast configuration file.

    If no configuration file is found, returns default configuration.

    :param config_path: Optional path to config file.
    :return: Validated NetCastConfig instance.
    :raises ConfigError: If configuration contains invalid values.
    """
    target = find_config_file(config_path)
    if not target:
        logger.info("No config file found; using default configuration.")
        cfg = NetCastConfig()
        cfg.validate()
        return cfg

    logger.info("Loading configuration from %s", target)
    try:
        with open(target, "rb") as f:
            raw_data = tomllib.load(f)
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"Failed to parse TOML configuration in {target}: {err}") from err
    except OSError as err:
        raise ConfigError(f"Failed to read configuration file {target}: {err}") from err

    return parse_config_dict(raw_data)
