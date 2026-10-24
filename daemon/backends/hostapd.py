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

"""Hostapd and dnsmasq subprocess networking backend for NetCast."""

import asyncio
import contextlib
import grp
import logging
import os
import pwd
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from daemon.backends.base import (
    ClientInfo,
    HotspotConfig,
    HotspotStatus,
    NetworkingBackend,
    validate_interface_name,
)

logger = logging.getLogger(__name__)

# Base runtime directories
RUN_DIR = Path("/run/netcast")
VAR_DIR = Path("/var/lib/netcast")
MANAGED_IFACES_DIR = VAR_DIR / "managed_ifaces"


def _tail(path: Path, max_bytes: int = 2000) -> str:
    """Return the tail of a subprocess log file for error messages."""
    try:
        return path.read_bytes()[-max_bytes:].decode("utf-8", errors="ignore").strip()
    except OSError:
        return ""


class HostapdBackend(NetworkingBackend):
    """Hotspot backend that supervises hostapd and dnsmasq as direct subprocesses."""

    def __init__(self, run_dir: Path | None = None, managed_dir: Path | None = None) -> None:
        """Initialize hostapd backend paths and process references.

        Args:
            run_dir: Directory for temporary runtime configs and control sockets.
            managed_dir: Directory tracking unmanaged interfaces for Dead Man's Switch cleanup.
        """
        self._run_dir = run_dir or RUN_DIR
        self._managed_dir = managed_dir or MANAGED_IFACES_DIR
        self._hostapd_proc: asyncio.subprocess.Process | None = None
        self._dnsmasq_proc: asyncio.subprocess.Process | None = None
        self._current_config: HotspotConfig | None = None
        self._gateway_ip: str = "192.168.12.1"
        self._was_unmanaged_by_us: bool = False

    def _ensure_directories(self) -> None:
        """Ensure required state and runtime directories exist."""
        try:
            self._run_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Fallback to tmp if /run is unprivileged
            self._run_dir = Path("/tmp/netcast_run")
            self._run_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._managed_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._managed_dir = Path("/tmp/netcast_managed_ifaces")
            self._managed_dir.mkdir(parents=True, exist_ok=True)

    async def _handle_nm_conflict(self, iface: str) -> None:
        """Detect if NetworkManager manages the interface and mark it unmanaged.

        Tracks the interface in /var/lib/netcast/managed_ifaces/ for Dead Man's Switch cleanup.

        Args:
            iface: Interface identifier.
        """
        if not validate_interface_name(iface):
            raise ValueError(f"Invalid interface name: {iface!r}")
        nmcli_path = shutil.which("nmcli")
        if not nmcli_path:
            return

        try:
            # Check device status via nmcli
            proc = await asyncio.create_subprocess_exec(
                nmcli_path,
                "-t",
                "-f",
                "DEVICE,STATE",
                "device",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                output = stdout.decode("utf-8", errors="ignore")
                for line in output.splitlines():
                    parts = line.strip().split(":")
                    if len(parts) >= 2 and parts[0] == iface:
                        state = parts[1].lower()
                        if state != "unmanaged":
                            logger.info(
                                "Interface %s is managed by NetworkManager; setting to unmanaged",
                                iface,
                            )
                            set_proc = await asyncio.create_subprocess_exec(
                                nmcli_path,
                                "device",
                                "set",
                                iface,
                                "managed",
                                "no",
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                            )
                            await set_proc.communicate()

                            # Mark tracking file for cleanup
                            self._ensure_directories()
                            marker = self._managed_dir / iface
                            marker.write_text(f"unmanaged_at={datetime.now(UTC).isoformat()}\n")
                            self._was_unmanaged_by_us = True
                        break
        except Exception as exc:
            logger.warning(
                "Failed to evaluate/handle NetworkManager conflict for %s: %s", iface, exc
            )

    async def _restore_nm_management(self, iface: str) -> None:
        """Restore NetworkManager management on the interface if previously unmanaged.

        Args:
            iface: Interface identifier.
        """
        if not validate_interface_name(iface):
            raise ValueError(f"Invalid interface name: {iface!r}")
        marker = self._managed_dir / iface
        if marker.exists() or self._was_unmanaged_by_us:
            nmcli_path = shutil.which("nmcli")
            if nmcli_path:
                try:
                    logger.info("Restoring NetworkManager management for %s", iface)
                    proc = await asyncio.create_subprocess_exec(
                        nmcli_path,
                        "device",
                        "set",
                        iface,
                        "managed",
                        "yes",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
                except Exception as exc:
                    logger.warning(
                        "Failed to restore NetworkManager management for %s: %s", iface, exc
                    )

            if marker.exists():
                with contextlib.suppress(OSError):
                    marker.unlink()
            self._was_unmanaged_by_us = False

    def _generate_hostapd_conf(self, config: HotspotConfig) -> str:
        """Generate hostapd.conf content adhering to NetCast requirements.

        Args:
            config: Hotspot configuration.

        Returns:
            Hostapd configuration file string.
        """
        is_5ghz = config.is_5ghz
        hw_mode = "a" if is_5ghz else "g"
        default_channel = 36 if is_5ghz else 6
        channel = config.channel if config.channel > 0 else default_channel

        ctrl_dir = self._run_dir / "hostapd_ctrl"
        ctrl_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            f"interface={config.ap_interface}",
            "driver=nl80211",
            f"ssid={config.ssid}",
            f"hw_mode={hw_mode}",
            f"channel={channel}",
            "ieee80211n=1",
            f"ctrl_interface={ctrl_dir}",
        ]

        if is_5ghz:
            lines.extend(
                [
                    "ieee80211ac=1",
                    "wmm_enabled=1",
                ]
            )

        if config.password:
            lines.extend(
                [
                    "wpa=2",
                    f"wpa_passphrase={config.password}",
                    "wpa_key_mgmt=WPA-PSK",
                    "rsn_pairwise=CCMP",
                ]
            )

        return "\n".join(lines) + "\n"

    def _generate_dnsmasq_conf(self, config: HotspotConfig) -> str:
        """Generate dnsmasq configuration for DHCP and DNS services on the AP interface.

        Args:
            config: Hotspot configuration.

        Returns:
            Dnsmasq configuration file string.
        """
        lease_file = self._run_dir / "dnsmasq.leases"
        lines = [
            f"interface={config.ap_interface}",
            "bind-dynamic",
            "domain-needed",
            "bogus-priv",
            "no-poll",
            f"user={pwd.getpwuid(os.getuid()).pw_name}",
            f"group={grp.getgrgid(os.getgid()).gr_name}",
            "log-facility=-",
            "dhcp-range=192.168.12.10,192.168.12.250,255.255.255.0,24h",
            f"dhcp-option=option:router,{self._gateway_ip}",
            f"dhcp-option=option:dns-server,{self._gateway_ip}",
            f"dhcp-leasefile={lease_file}",
        ]
        # Plugins (private DNS, ad blocking) add their own lines; plain DNS is the fallback.
        extra = list(getattr(self, "extra_dnsmasq", None) or [])
        if not any(line.startswith("server=") for line in extra):
            lines += ["server=1.1.1.1", "server=8.8.8.8"]
        lines.append(f"address=/netcast.lan/{self._gateway_ip}")
        lines += extra
        return "\n".join(lines) + "\n"

    async def _configure_interface_ip(self, iface: str) -> None:
        """Configure IP address on the AP interface and bring link up.

        Args:
            iface: AP interface identifier.
        """
        if not validate_interface_name(iface):
            raise ValueError(f"Invalid interface name: {iface!r}")

        # Flush existing addresses from interface
        proc_flush = await asyncio.create_subprocess_exec(
            "ip",
            "addr",
            "flush",
            "dev",
            iface,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc_flush.communicate()

        # Assign gateway IP
        proc_addr = await asyncio.create_subprocess_exec(
            "ip",
            "addr",
            "add",
            f"{self._gateway_ip}/24",
            "dev",
            iface,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc_addr.communicate()

        # Bring interface UP
        proc_up = await asyncio.create_subprocess_exec(
            "ip",
            "link",
            "set",
            iface,
            "up",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc_up.communicate()

    async def create_hotspot(self, config: HotspotConfig) -> HotspotStatus:
        """Start hostapd and dnsmasq subprocesses to establish the hotspot.

        Args:
            config: Hotspot configuration parameters.

        Returns:
            HotspotStatus reflecting runtime status.

        Raises:
            RuntimeError: If hostapd or dnsmasq fail to start.
        """
        config.validate()

        if self._hostapd_proc and self._hostapd_proc.returncode is None:
            await self.stop_hotspot()

        self._ensure_directories()
        self._current_config = config
        self._gateway_ip = "192.168.12.1"

        # 1. Handle NM conflict
        await self._handle_nm_conflict(config.ap_interface)

        # 2. Configure AP interface IP and state
        await self._configure_interface_ip(config.ap_interface)

        # 3. Write configuration files
        hostapd_conf_path = self._run_dir / "hostapd.conf"
        dnsmasq_conf_path = self._run_dir / "dnsmasq.conf"

        hostapd_conf_path.write_text(self._generate_hostapd_conf(config), encoding="utf-8")
        dnsmasq_conf_path.write_text(self._generate_dnsmasq_conf(config), encoding="utf-8")

        # 4. Start hostapd subprocess
        logger.info("Spawning hostapd with config %s", hostapd_conf_path)
        hostapd_log = self._run_dir / "hostapd.log"
        with hostapd_log.open("wb") as log_fh:
            self._hostapd_proc = await asyncio.create_subprocess_exec(
                "hostapd",
                str(hostapd_conf_path),
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
            )

        # Brief delay to detect immediate initialization failures
        await asyncio.sleep(1.0)
        if self._hostapd_proc.returncode is not None:
            code = self._hostapd_proc.returncode
            err_msg = _tail(hostapd_log)
            await self.stop_hotspot()  # also restores NetworkManager management
            raise RuntimeError(f"hostapd failed to start (exit {code}): {err_msg}")

        # 5. Start dnsmasq subprocess (in foreground mode with -k)
        logger.info("Spawning dnsmasq with config %s", dnsmasq_conf_path)
        dnsmasq_log = self._run_dir / "dnsmasq.log"
        with dnsmasq_log.open("wb") as log_fh:
            self._dnsmasq_proc = await asyncio.create_subprocess_exec(
                "dnsmasq",
                "-C",
                str(dnsmasq_conf_path),
                "-k",
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
            )

        await asyncio.sleep(0.5)
        if self._dnsmasq_proc.returncode is not None:
            code = self._dnsmasq_proc.returncode
            err_msg = _tail(dnsmasq_log)
            await self.stop_hotspot()  # clean up hostapd on failure
            raise RuntimeError(f"dnsmasq failed to start (exit {code}): {err_msg}")

        return await self.get_status()

    async def _terminate_process(self, proc: asyncio.subprocess.Process | None, name: str) -> None:
        """Safely terminate a subprocess using SIGTERM with SIGKILL fallback.

        Args:
            proc: Subprocess instance to terminate.
            name: Human-readable process name for logging.
        """
        if proc is None or proc.returncode is not None:
            return

        logger.info("Terminating %s (PID %s)", name, proc.pid)
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.5)
            except TimeoutError:
                logger.warning("%s did not terminate in time; sending SIGKILL", name)
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning("Error terminating %s: %s", name, exc)

    async def stop_hotspot(self) -> None:
        """Terminate hostapd and dnsmasq child processes, restore NM and flush network state."""
        # Terminate dnsmasq first to stop accepting new requests
        await self._terminate_process(self._dnsmasq_proc, "dnsmasq")
        self._dnsmasq_proc = None

        # Terminate hostapd
        await self._terminate_process(self._hostapd_proc, "hostapd")
        self._hostapd_proc = None

        if self._current_config:
            ap_iface = self._current_config.ap_interface
            # Flush interface IP
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ip",
                    "addr",
                    "flush",
                    "dev",
                    ap_iface,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
            except Exception as exc:
                logger.debug("Failed to flush IP for %s: %s", ap_iface, exc)

            # Restore NetworkManager management if applicable
            await self._restore_nm_management(ap_iface)

        # Clean up temporary configuration files
        for conf_file in ("hostapd.conf", "dnsmasq.conf"):
            p = self._run_dir / conf_file
            if p.exists():
                with contextlib.suppress(OSError):
                    p.unlink()

        self._current_config = None

    async def kick_client(self, mac: str) -> None:
        """Disconnect one station; it is free to rejoin."""
        from daemon.backends.base import validate_mac_address

        if not self._current_config:
            raise RuntimeError("The hotspot is not running")
        if not validate_mac_address(mac):
            raise ValueError("Invalid device address")
        cli = shutil.which("hostapd_cli")
        if not cli:
            raise RuntimeError("hostapd_cli is not installed")
        ap_iface = self._current_config.ap_interface
        proc = await asyncio.create_subprocess_exec(
            cli, "-p", str(self._run_dir / "hostapd_ctrl"), "-i", ap_iface, "deauthenticate", mac,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Could not disconnect the device: {err.decode(errors='ignore').strip()}")

    async def _live_station_macs(self, ap_iface: str) -> set[str] | None:
        """MACs currently associated with the AP, or None if undeterminable."""
        iw_bin = shutil.which("iw")
        if not iw_bin:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                iw_bin,
                "dev",
                ap_iface,
                "station",
                "dump",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        return {
            line.split()[1].lower()
            for line in stdout.decode("utf-8", errors="ignore").splitlines()
            if line.startswith("Station ")
        }

    async def get_connected_clients(self) -> list[ClientInfo]:
        """Parse dnsmasq leases and hostapd station dump to compile connected clients.

        Returns:
            List of ClientInfo instances for active stations.
        """
        clients: dict[str, ClientInfo] = {}
        if not self._current_config:
            return []

        ap_iface = self._current_config.ap_interface

        # 1. Parse dnsmasq leases file
        lease_paths = [
            self._run_dir / "dnsmasq.leases",
            Path("/var/lib/misc/dnsmasq.leases"),
        ]

        for lp in lease_paths:
            if lp.is_file():
                try:
                    content = lp.read_text(encoding="utf-8")
                    for line in content.splitlines():
                        parts = line.strip().split()
                        if len(parts) >= 3:
                            expiry = int(parts[0])
                            mac = parts[1].lower()
                            ip = parts[2]
                            hostname = parts[3] if len(parts) > 3 and parts[3] != "*" else None
                            clients[mac] = ClientInfo(
                                ip=ip,
                                mac=mac,
                                hostname=hostname,
                                connected_at=datetime.fromtimestamp(expiry, tz=UTC),
                            )
                except Exception as exc:
                    logger.debug("Failed reading lease file %s: %s", lp, exc)

        # 1b. Drop stale DHCP leases: only keep stations that are associated right now
        live = await self._live_station_macs(ap_iface)
        if live is not None:
            clients = {m: c for m, c in clients.items() if m in live}

        # 2. Augment station stats via hostapd_cli if socket exists, or iw station dump
        ctrl_path = self._run_dir / "hostapd_ctrl"
        ctrl_sock = ctrl_path / ap_iface

        if ctrl_sock.exists() and shutil.which("hostapd_cli"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "hostapd_cli",
                    "-p",
                    str(ctrl_path),
                    "-i",
                    ap_iface,
                    "all_sta",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    current_mac: str | None = None
                    for line in stdout.decode("utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if re.match(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$", line):
                            current_mac = line.lower()
                            if current_mac not in clients:
                                clients[current_mac] = ClientInfo(
                                    ip="0.0.0.0",
                                    mac=current_mac,
                                    hostname=None,
                                    connected_at=datetime.now(UTC),
                                )
                        elif current_mac and "=" in line:
                            k, v = line.split("=", 1)
                            if k == "rx_bytes" and current_mac in clients:
                                clients[current_mac].rx_bytes = int(v)
                            elif k == "tx_bytes" and current_mac in clients:
                                clients[current_mac].tx_bytes = int(v)
            except Exception as exc:
                logger.debug("Failed querying hostapd_cli all_sta: %s", exc)

        # 3. Fallback: Parse /proc/net/arp to resolve any unassigned IPs
        try:
            arp_path = Path("/proc/net/arp")
            if arp_path.is_file():
                for line in arp_path.read_text(encoding="utf-8").splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 6:
                        ip, _flags, mac, dev = parts[0], parts[2], parts[3].lower(), parts[5]
                        if dev == ap_iface and mac in clients and clients[mac].ip == "0.0.0.0":
                            clients[mac].ip = ip
        except Exception as exc:
            logger.debug("Failed reading /proc/net/arp: %s", exc)

        return list(clients.values())

    async def get_status(self) -> HotspotStatus:
        """Check if hostapd subprocess is running and return current operational status."""
        is_active = self._hostapd_proc is not None and self._hostapd_proc.returncode is None

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
