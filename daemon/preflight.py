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

"""Pre-flight verification system assessing hardware, kernel, and network compatibility."""

import asyncio
import logging
import os
import platform
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from daemon.backends.base import HotspotConfig
from daemon.scanner import (
    detect_network_manager,
    find_binary,
    get_connected_channel,
    get_interface_capabilities,
    is_dfs_channel,
    list_wifi_interfaces,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Check:
    """Individual pre-flight validation check result."""

    name: str
    status: str  # 'pass' | 'warn' | 'fail'
    message: str
    fix_hint: str = ""


@dataclass(slots=True)
class PreFlightResult:
    """Consolidated pre-flight assessment report."""

    passed: bool
    checks: list[Check] = field(default_factory=list)


async def check_ap_mode_support(config: HotspotConfig) -> Check:
    """Verify that the selected AP interface supports master/AP mode."""
    if not config.ap_interface:
        return Check(
            name="AP mode support",
            status="fail",
            message="No AP interface specified in hotspot configuration.",
            fix_hint="Specify a valid Wi-Fi adapter for ap_interface.",
        )

    try:
        caps = await get_interface_capabilities(config.ap_interface)
        if caps.supports_ap:
            return Check(
                name="AP mode support",
                status="pass",
                message=f"Interface {config.ap_interface} supports Access Point (AP) mode.",
            )
        else:
            return Check(
                name="AP mode support",
                status="fail",
                message=f"Interface {config.ap_interface} does not support Access Point (AP) mode.",
                fix_hint="Select a Wi-Fi adapter with master/AP mode support in nl80211.",
            )
    except Exception as exc:
        return Check(
            name="AP mode support",
            status="fail",
            message=f"Failed checking AP support on {config.ap_interface}: {exc}",
            fix_hint="Verify the wireless adapter is plugged in and supported by Linux.",
        )


async def check_ap_sta_concurrency(config: HotspotConfig) -> Check:
    """Verify hardware supports concurrent AP and STA mode if running single-radio repeater."""
    if config.source_type == "wifi" and config.source_interface:
        try:
            # Check if source and AP interface are on the same physical radio (or same iface)
            wifi_devs = {dev.name: dev for dev in await list_wifi_interfaces()}
            source_dev = wifi_devs.get(config.source_interface)
            ap_dev = wifi_devs.get(config.ap_interface)

            same_phy = (source_dev and ap_dev and source_dev.phy == ap_dev.phy) or (
                config.source_interface == config.ap_interface
            )

            if same_phy:
                target_iface = config.ap_interface or config.source_interface
                caps = await get_interface_capabilities(target_iface)
                if caps.supports_ap_sta:
                    return Check(
                        name="AP+STA support (for wifi repeater)",
                        status="pass",
                        message=f"Radio {target_iface} supports simultaneous AP and STA operation.",
                    )
                else:
                    return Check(
                        name="AP+STA support (for wifi repeater)",
                        status="fail",
                        message=(
                            f"Adapter {target_iface} does not support simultaneous AP and STA "
                            "operation on a single physical radio."
                        ),
                        fix_hint=(
                            "Use a secondary USB Wi-Fi dongle or connect upstream via Ethernet "
                            "or USB tethering."
                        ),
                    )
        except Exception as exc:
            return Check(
                name="AP+STA support (for wifi repeater)",
                status="warn",
                message=f"Could not verify AP+STA combinations: {exc}",
                fix_hint="Ensure wireless tools (iw) are installed.",
            )

    return Check(
        name="AP+STA support (for wifi repeater)",
        status="pass",
        message="Independent interfaces used; AP+STA concurrency requirement satisfied.",
    )


async def check_channel_lock(config: HotspotConfig) -> Check:
    """Detect whether AP channel must match upstream station channel in repeater mode."""
    if config.source_type == "wifi" and config.source_interface:
        try:
            wifi_devs = {dev.name: dev for dev in await list_wifi_interfaces()}
            source_dev = wifi_devs.get(config.source_interface)
            ap_dev = wifi_devs.get(config.ap_interface)

            same_phy = (source_dev and ap_dev and source_dev.phy == ap_dev.phy) or (
                config.source_interface == config.ap_interface
            )

            if same_phy:
                sta_channel = await get_connected_channel(config.source_interface)
                if sta_channel is not None:
                    if config.channel > 0 and config.channel != sta_channel:
                        return Check(
                            name="Channel lock detection",
                            status="fail",
                            message=(
                                f"Channel conflict: AP requested channel {config.channel}, but "
                                f"upstream STA is connected to channel {sta_channel}. A single "
                                "radio cannot tune to two different channels simultaneously."
                            ),
                            fix_hint=f"Set hotspot channel to {sta_channel} (matching upstream).",
                        )
                    return Check(
                        name="Channel lock detection",
                        status="pass",
                        message=f"Hotspot channel matches upstream channel ({sta_channel}).",
                    )
                else:
                    return Check(
                        name="Channel lock detection",
                        status="warn",
                        message=(
                            f"Upstream Wi-Fi {config.source_interface} is not associated. "
                            "Hotspot channel must lock to upstream channel once connected."
                        ),
                        fix_hint="Connect to upstream Wi-Fi network before starting hotspot.",
                    )
        except Exception as exc:
            return Check(
                name="Channel lock detection",
                status="warn",
                message=f"Could not evaluate channel lock: {exc}",
            )

    return Check(
        name="Channel lock detection",
        status="pass",
        message="Channel lock not applicable (upstream is not a shared-radio Wi-Fi).",
    )


async def check_dfs_channel(config: HotspotConfig) -> Check:
    """Warn if the selected channel resides in the 5GHz DFS radar detection spectrum."""
    if config.channel > 0 and is_dfs_channel(config.channel):
        return Check(
            name="DFS channel warning",
            status="warn",
            message=(
                f"Channel {config.channel} is in the 5GHz DFS radar detection band. "
                "Hotspot must perform a 60-second Channel Availability Check (CAC)."
            ),
            fix_hint="Use a non-DFS channel (36, 40, 44, 48 or 149-165) for instant startup.",
        )

    return Check(
        name="DFS channel warning",
        status="pass",
        message=f"Channel {config.channel or 'auto'} is outside DFS radar spectrum.",
    )


async def check_linux_611_iwlwifi(config: HotspotConfig) -> Check:
    """Check for Linux 6.11+ iwlwifi regulatory restriction on 5GHz AP and AP+STA."""
    try:
        release = platform.release()
        match = re.match(r"^(\d+)\.(\d+)", release)
        kernel_version = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    except Exception:
        kernel_version = (0, 0)
        release = "unknown"

    driver = "unknown"
    if config.ap_interface:
        try:
            driver_link = Path(f"/sys/class/net/{config.ap_interface}/device/driver")
            if driver_link.exists():
                driver = os.path.basename(os.path.realpath(driver_link))
        except OSError:
            pass

    if driver == "iwlwifi" and kernel_version >= (6, 11):
        if config.is_5ghz:
            return Check(
                name="Linux 6.11+ iwlwifi restriction",
                status="warn",  # unverified for every kernel/firmware combination
                message=(
                    f"Linux kernel {release} with iwlwifi driver strictly forbids 5GHz AP mode "
                    "due to Location Aware Regulatory (LAR) lockdown (-EBUSY / -EINVAL)."
                ),
                fix_hint=(
                    "Configure band='2.4GHz' or utilize a dedicated USB Wi-Fi dongle (MediaTek)."
                ),
            )
        else:
            return Check(
                name="Linux 6.11+ iwlwifi restriction",
                status="warn",
                message=(
                    f"Linux kernel {release} with iwlwifi driver detected. 2.4GHz hotspot will "
                    "operate, but 5GHz AP operation is blocked by the kernel driver."
                ),
                fix_hint="Keep hotspot configured to 2.4GHz band.",
            )

    return Check(
        name="Linux 6.11+ iwlwifi restriction",
        status="pass",
        message="Kernel and driver do not suffer from 6.11+ iwlwifi 5GHz restriction.",
    )


async def check_network_manager(config: HotspotConfig) -> Check:
    """Detect NetworkManager availability and potential daemon interface competition."""
    nm_status = await detect_network_manager()
    if nm_status == "running":
        return Check(
            name="NetworkManager detection",
            status="pass",
            message="NetworkManager is running and available for native hotspot activation.",
        )
    elif nm_status == "stopped":
        return Check(
            name="NetworkManager detection",
            status="warn",
            message=(
                "NetworkManager is installed but stopped. Standalone hostapd/dnsmasq backend will "
                "be used."
            ),
            fix_hint="Run 'systemctl start NetworkManager' if NetworkManager backend is desired.",
        )
    else:
        return Check(
            name="NetworkManager detection",
            status="warn",
            message="NetworkManager not found. Using standalone hostapd + dnsmasq backend.",
            fix_hint="Ensure hostapd and dnsmasq packages are installed.",
        )


async def check_firewall_tools() -> Check:
    """Confirm existence of nftables or iptables with preference for nftables."""
    nft_bin = find_binary("nft")
    iptables_bin = find_binary("iptables")

    has_nft = bool(shutil.which(nft_bin) or os.path.exists(nft_bin))
    has_ipt = bool(shutil.which(iptables_bin) or os.path.exists(iptables_bin))

    if has_nft:
        return Check(
            name="Firewall tool detection (nft vs iptables)",
            status="pass",
            message="Modern nftables firewall tool found (table inet netcast).",
        )
    elif has_ipt:
        return Check(
            name="Firewall tool detection (nft vs iptables)",
            status="warn",
            message="nftables not found; legacy iptables will be used as fallback.",
            fix_hint="Install nftables ('apt install nftables') for atomic ruleset application.",
        )
    else:
        return Check(
            name="Firewall tool detection (nft vs iptables)",
            status="fail",
            message="Neither nftables nor iptables is installed on this system.",
            fix_hint="Install nftables ('sudo apt install nftables').",
        )


async def check_port_53_conflict() -> Check:
    """Verify local port 53 availability and inspect systemd-resolved coexistence."""
    has_wildcard_53 = False
    proc = None
    try:
        ss_bin = find_binary("ss")
        proc = await asyncio.create_subprocess_exec(
            ss_bin,
            "-tuln",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            for line in stdout.decode("utf-8").splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    local_addr = parts[4]
                    if local_addr.endswith(":53") and (
                        local_addr.startswith("0.0.0.0")
                        or local_addr.startswith("*")
                        or local_addr.startswith("[::]")
                    ):
                        has_wildcard_53 = True
                        break
    except Exception as exc:
        logger.debug("Failed querying ss for port 53: %s", exc)

    if has_wildcard_53:
        return Check(
            name="Port 53 conflict (systemd-resolved)",
            status="warn",
            message=(
                "A DNS service is listening on 0.0.0.0:53 (wildcard). "
                "NetCast dnsmasq must use interface-specific binding (bind-dynamic)."
            ),
            fix_hint="Disable conflicting DNS stub listener or bind to 127.0.0.53 only.",
        )

    return Check(
        name="Port 53 conflict (systemd-resolved)",
        status="pass",
        message="Port 53 is free or configured with address-specific bindings.",
    )


async def check_intel_lar(config: HotspotConfig) -> Check:
    """Check Intel Location Aware Regulatory (LAR) state preventing 5GHz AP initiation."""
    driver = ""
    if config.ap_interface:
        try:
            driver_link = Path(f"/sys/class/net/{config.ap_interface}/device/driver")
            if driver_link.exists():
                driver = os.path.basename(os.path.realpath(driver_link))
        except OSError:
            pass

    if driver != "iwlwifi":
        return Check(
            name="Intel 5GHz LAR check",
            status="pass",
            message="Non-Intel Wi-Fi card; Intel LAR restriction does not apply.",
        )

    # If band is not 5GHz, LAR does not impede 2.4GHz
    if "5" not in config.band:
        return Check(
            name="Intel 5GHz LAR check",
            status="pass",
            message="Operating on 2.4GHz band; unaffected by Intel 5GHz LAR.",
        )

    # Check regulatory domain
    iw_bin = find_binary("iw")
    try:
        proc = await asyncio.create_subprocess_exec(
            iw_bin,
            "reg",
            "get",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="ignore")

        # In Intel LAR, country 00 or PASSIVE-SCAN / NO-IR indicates uninitialized AP mode
        if "country 00" in output:
            return Check(
                name="Intel 5GHz LAR check",
                status="warn",
                message=(
                    "Intel Wi-Fi LAR (Location Aware Regulatory) has restricted 5GHz AP mode "
                    "(NO-IR / PASSIVE-SCAN active). The card refuses to initiate radiation on 5GHz."
                ),
                fix_hint=(
                    "Set band='2.4GHz' or connect to an authorized 5GHz AP to update LAR country."
                ),
            )
    except Exception as exc:
        logger.debug("Failed querying iw reg get: %s", exc)

    return Check(
        name="Intel 5GHz LAR check",
        status="pass",
        message="Intel regulatory domain allows AP operation.",
    )


async def check_corporate_network(config: HotspotConfig) -> Check:
    """Detect upstream 802.1X Enterprise authentication or captive portal networks."""
    if not config.source_interface:
        return Check(
            name="Corporate network detection (802.1X)",
            status="pass",
            message="No upstream interface specified.",
        )

    nmcli_bin = find_binary("nmcli")
    if shutil.which(nmcli_bin):
        try:
            proc = await asyncio.create_subprocess_exec(
                nmcli_bin,
                "-t",
                "-f",
                "DEVICE,TYPE,CONNECTION",
                "device",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                for line in stdout.decode("utf-8").splitlines():
                    parts = line.strip().split(":")
                    if len(parts) >= 3 and parts[0] == config.source_interface:
                        conn_name = parts[2]
                        if conn_name:
                            # Inspect connection security
                            show_proc = await asyncio.create_subprocess_exec(
                                nmcli_bin,
                                "-s",
                                "-t",
                                "-f",
                                "802-11-wireless-security.key-mgmt,802-1x.eap",
                                "connection",
                                "show",
                                conn_name,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                            )
                            show_out, _ = await show_proc.communicate()
                            text = show_out.decode("utf-8").lower()
                            if "wpa-eap" in text or re.search(r"802-1x\.eap:\s*\S", text):
                                return Check(
                                    name="Corporate network detection (802.1X)",
                                    status="warn",
                                    message=(
                                        f"Upstream network '{conn_name}' uses 802.1X "
                                        "authentication. Network isolation or MAC policies "
                                        "may limit client traffic."
                                    ),
                                    fix_hint=(
                                        "Ensure NAT masquerading is enabled so upstream sees "
                                        "only NetCast MAC."
                                    ),
                                )
        except Exception as exc:
            logger.debug("Corporate network check failed: %s", exc)

    return Check(
        name="Corporate network detection (802.1X)",
        status="pass",
        message="Standard upstream network detected without 802.1X enterprise hurdles.",
    )


async def run_preflight(config: HotspotConfig) -> PreFlightResult:
    """Execute complete suite of pre-flight system, hardware, and kernel checks.

    Args:
        config: Proposed hotspot configuration.

    Returns:
        PreFlightResult detailing pass/warn/fail status of all 10 checks.
    """
    checks: list[Check] = [
        await check_ap_mode_support(config),
        await check_ap_sta_concurrency(config),
        await check_channel_lock(config),
        await check_dfs_channel(config),
        await check_linux_611_iwlwifi(config),
        await check_network_manager(config),
        await check_firewall_tools(),
        await check_port_53_conflict(),
        await check_intel_lar(config),
        await check_corporate_network(config),
    ]

    has_failures = any(c.status == "fail" for c in checks)
    return PreFlightResult(
        passed=not has_failures,
        checks=checks,
    )
