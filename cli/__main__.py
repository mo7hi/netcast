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

"""Command-line interface for NetCast communicating via D-Bus."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Sequence

from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.errors import DBusError
import segno
import segno.helpers

BUS_NAME = "net.netcast.Daemon"
OBJECT_PATH = "/net/netcast/Daemon"
INTERFACE_NAME = "net.netcast.Daemon"

# Standard Exit Codes
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_DAEMON_NOT_RUNNING = 2


# ---------------------------------------------------------------------------
# ANSI Terminal Styling (Zero External Dependencies)
# ---------------------------------------------------------------------------

def supports_color() -> bool:
    """Determine whether standard output supports ANSI color codes."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


def colorize(text: str, code: str) -> str:
    """Wrap text in an ANSI escape sequence if terminal supports colors."""
    if not supports_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text: str) -> str:
    return colorize(text, "1")


def dim(text: str) -> str:
    return colorize(text, "2")


def red(text: str) -> str:
    return colorize(text, "91")


def green(text: str) -> str:
    return colorize(text, "92")


def yellow(text: str) -> str:
    return colorize(text, "93")


def blue(text: str) -> str:
    return colorize(text, "94")


def cyan(text: str) -> str:
    return colorize(text, "96")


# ---------------------------------------------------------------------------
# D-Bus Connection Helper
# ---------------------------------------------------------------------------

class DaemonNotRunningError(Exception):
    """Raised when the NetCast D-Bus service is unreachable."""
    pass


async def get_dbus_interface() -> tuple[MessageBus, Any]:
    """Connect to the D-Bus system bus and obtain the NetCast daemon proxy interface.

    Returns:
        Tuple of (MessageBus, ProxyInterface).

    Raises:
        DaemonNotRunningError: If connection fails or daemon is not active.
    """
    bus_env = os.environ.get("NETCAST_DBUS_BUS", "").lower()
    bus_type = BusType.SESSION if bus_env == "session" else BusType.SYSTEM

    try:
        bus = await MessageBus(bus_type=bus_type).connect()
    except Exception as exc:
        raise DaemonNotRunningError(f"Could not connect to {bus_type.name.lower()} D-Bus: {exc}") from exc

    try:
        introspection = await bus.introspect(BUS_NAME, OBJECT_PATH)
        proxy = bus.get_proxy_object(BUS_NAME, OBJECT_PATH, introspection)
        iface = proxy.get_interface(INTERFACE_NAME)
        return bus, iface
    except Exception as exc:
        bus.disconnect()
        raise DaemonNotRunningError(f"NetCast daemon is not responding on D-Bus: {exc}") from exc


# ---------------------------------------------------------------------------
# CLI Command Implementations
# ---------------------------------------------------------------------------

async def cmd_start(args: argparse.Namespace) -> int:
    """Execute 'netcast start' command to activate hotspot."""
    # Strict validation of inputs
    ssid = args.ssid.strip()
    if not ssid or len(ssid.encode("utf-8")) > 32:
        sys.stderr.write(f"{red('Error:')} SSID must be between 1 and 32 UTF-8 bytes\n")
        return EXIT_ERROR

    password = args.password.strip()
    if len(password) < 8 or len(password) > 63:
        sys.stderr.write(f"{red('Error:')} Passphrase must be between 8 and 63 characters\n")
        return EXIT_ERROR

    source = args.source.strip()
    if not source:
        sys.stderr.write(f"{red('Error:')} Source network interface is required\n")
        return EXIT_ERROR

    channel = int(args.channel)
    if channel < 0 or channel > 196:
        sys.stderr.write(f"{red('Error:')} Channel must be between 0 and 196 (0 = auto)\n")
        return EXIT_ERROR

    band = args.band
    if band not in ("bg", "a"):
        sys.stderr.write(f"{red('Error:')} Band must be either 'bg' (2.4GHz) or 'a' (5GHz)\n")
        return EXIT_ERROR

    payload = {
        "source": source,
        "source_interface": source,
        "ssid": ssid,
        "password": password,
        "band": band,
        "channel": channel,
    }
    if getattr(args, "ap", None):
        payload["ap_interface"] = args.ap.strip()

    bus, iface = await get_dbus_interface()
    try:
        sys.stdout.write(f"{dim('Activating Wi-Fi hotspot on')} {cyan(source)}...\n")
        res_json = await iface.call_start_hotspot(json.dumps(payload))
        data = json.loads(res_json)

        sys.stdout.write(f"\n{green('✓')} {bold('Hotspot started successfully!')}\n")
        sys.stdout.write(f"  {bold('SSID:')}             {data.get('ssid', ssid)}\n")
        sys.stdout.write(f"  {bold('Band:')}             {data.get('band', band)}\n")
        sys.stdout.write(f"  {bold('Channel:')}          {data.get('channel', channel)} {'(auto)' if channel == 0 else ''}\n")
        if data.get("ap_interface"):
            sys.stdout.write(f"  {bold('AP Interface:')}     {data.get('ap_interface')}\n")
        sys.stdout.write(f"  {bold('Source Interface:')} {data.get('source_interface', source)}\n")
        sys.stdout.write(f"  {bold('Gateway IP:')}       {data.get('gateway_ip', '10.42.0.1')}\n")
        sys.stdout.write(f"\n{dim('Run')} {cyan('netcast qr')} {dim('to display connection QR code.')}\n")
        return EXIT_SUCCESS
    except DBusError as exc:
        sys.stderr.write(f"{red('Error starting hotspot:')} {exc.text}\n")
        return EXIT_ERROR
    finally:
        bus.disconnect()


async def cmd_stop(_args: argparse.Namespace) -> int:
    """Execute 'netcast stop' command to deactivate hotspot."""
    bus, iface = await get_dbus_interface()
    try:
        sys.stdout.write(f"{dim('Stopping hotspot...')}\n")
        await iface.call_stop_hotspot()
        sys.stdout.write(f"{green('✓')} {bold('Hotspot stopped successfully.')}\n")
        return EXIT_SUCCESS
    except DBusError as exc:
        sys.stderr.write(f"{red('Error stopping hotspot:')} {exc.text}\n")
        return EXIT_ERROR
    finally:
        bus.disconnect()


async def cmd_status(_args: argparse.Namespace) -> int:
    """Execute 'netcast status' command displaying hotspot info and connected stations."""
    bus, iface = await get_dbus_interface()
    try:
        status_raw = await iface.call_get_status()
        clients_raw = await iface.call_get_clients()

        status = json.loads(status_raw)
        clients = json.loads(clients_raw)

        is_active = status.get("active", False)
        state_str = green("ACTIVE ●") if is_active else dim("INACTIVE ○")

        sys.stdout.write(f"\n{bold('NetCast Hotspot Status')}\n")
        sys.stdout.write(f"{'─' * 46}\n")
        sys.stdout.write(f"  {bold('State:')}             {state_str}\n")
        if is_active:
            sys.stdout.write(f"  {bold('SSID:')}              {cyan(status.get('ssid', ''))}\n")
            band = status.get("band", "2.4GHz")
            ch = status.get("channel", 0)
            ch_str = f"Channel {ch}" if ch > 0 else "Auto"
            sys.stdout.write(f"  {bold('Band & Channel:')}    {band} ({ch_str})\n")
            sys.stdout.write(f"  {bold('AP Interface:')}      {status.get('ap_interface', 'auto')}\n")
            sys.stdout.write(f"  {bold('Source Interface:')}  {status.get('source_interface', 'auto')}\n")
            sys.stdout.write(f"  {bold('Gateway IP:')}        {status.get('gateway_ip', '')}\n")
            sys.stdout.write(f"  {bold('Connected Clients:')} {len(clients)}\n")

            sys.stdout.write(f"\n{bold('Connected Clients')} ({len(clients)}):\n")
            if not clients:
                sys.stdout.write(f"  {dim('No client devices currently connected.')}\n\n")
            else:
                sys.stdout.write(f"{'─' * 74}\n")
                header = f"{'IP Address':<18} {'MAC Address':<20} {'Hostname':<18} {'Traffic (RX/TX)'}"
                sys.stdout.write(f"{bold(header)}\n")
                sys.stdout.write(f"{'─' * 74}\n")
                for c in clients:
                    ip = c.get("ip", "-")
                    mac = c.get("mac", "-")
                    host = c.get("hostname") or "-"
                    rx_mb = (c.get("rx_bytes", 0) or 0) / (1024 * 1024)
                    tx_mb = (c.get("tx_bytes", 0) or 0) / (1024 * 1024)
                    traffic = f"{rx_mb:.1f}M / {tx_mb:.1f}M"
                    sys.stdout.write(f"{ip:<18} {mac:<20} {host:<18} {traffic}\n")
                sys.stdout.write("\n")
        else:
            sys.stdout.write(f"\n{dim('Hotspot is currently stopped. Use')} {cyan('netcast start')} {dim('to launch.')}\n\n")

        return EXIT_SUCCESS
    except DBusError as exc:
        sys.stderr.write(f"{red('Error querying status:')} {exc.text}\n")
        return EXIT_ERROR
    finally:
        bus.disconnect()


async def cmd_qr(_args: argparse.Namespace) -> int:
    """Execute 'netcast qr' command printing connection QR code to terminal."""
    bus, iface = await get_dbus_interface()
    try:
        status_raw = await iface.call_get_status()
        status = json.loads(status_raw)

        ssid = status.get("ssid")
        password = status.get("password")

        if not ssid:
            sys.stderr.write(f"{red('Error:')} No active hotspot running. Start a hotspot first with {cyan('netcast start')}\n")
            return EXIT_ERROR

        security = "WPA" if password else "nopass"
        qr = segno.helpers.make_wifi(ssid=ssid, password=password, security=security)

        sys.stdout.write(f"\n{bold('Scan to connect to')} {cyan(ssid)}:\n\n")
        qr.terminal(compact=True)
        sys.stdout.write("\n")
        return EXIT_SUCCESS
    except DBusError as exc:
        sys.stderr.write(f"{red('Error generating QR code:')} {exc.text}\n")
        return EXIT_ERROR
    finally:
        bus.disconnect()


async def cmd_preflight(_args: argparse.Namespace) -> int:
    """Execute 'netcast preflight' command running and displaying system checks."""
    bus, iface = await get_dbus_interface()
    try:
        sys.stdout.write(f"{dim('Running pre-flight checks...')}\n\n")
        res_raw = await iface.call_run_preflight()
        data = json.loads(res_raw)

        checks = data.get("checks", [])
        overall_passed = data.get("passed", False)

        sys.stdout.write(f"{bold('NetCast Pre-flight Diagnostics')}\n")
        sys.stdout.write(f"{'─' * 70}\n")

        for c in checks:
            passed = c.get("passed", False)
            title = c.get("title", c.get("name", "Check"))
            msg = c.get("message", "")
            required = c.get("required", True)

            if passed:
                tag = f"[{green('PASS')}]"
            elif not required:
                tag = f"[{yellow('WARN')}]"
            else:
                tag = f"[{red('FAIL')}]"

            sys.stdout.write(f"{tag:<16} {bold(title):<28} {dim(msg)}\n")

        sys.stdout.write(f"{'─' * 70}\n")
        if overall_passed:
            sys.stdout.write(f"{green('✓')} {bold('All required pre-flight checks passed! NetCast is ready.')}\n\n")
            return EXIT_SUCCESS
        else:
            sys.stdout.write(f"{red('✗')} {bold('One or more required pre-flight checks failed.')}\n\n")
            return EXIT_ERROR
    except DBusError as exc:
        sys.stderr.write(f"{red('Error running preflight:')} {exc.text}\n")
        return EXIT_ERROR
    finally:
        bus.disconnect()


async def cmd_interfaces(_args: argparse.Namespace) -> int:
    """Execute 'netcast interfaces' command listing host network devices."""
    bus, iface = await get_dbus_interface()
    try:
        interfaces_raw = await iface.call_get_interfaces()
        interfaces = json.loads(interfaces_raw)

        sys.stdout.write(f"\n{bold('Available Network Interfaces')}\n")
        sys.stdout.write(f"{'─' * 76}\n")
        header = f"{'Interface':<14} {'Type':<12} {'State':<10} {'AP Mode':<10} {'MAC Address':<20} {'IP Address'}"
        sys.stdout.write(f"{bold(header)}\n")
        sys.stdout.write(f"{'─' * 76}\n")

        for i in interfaces:
            name = i.get("name", "")
            itype = i.get("type", "unknown")
            state = i.get("state", "unknown")
            state_color = green(state) if state == "up" else dim(state)
            ap_support = green("YES") if i.get("ap_capable") else dim("NO")
            mac = i.get("mac", "") or "-"
            addrs = i.get("ip_addresses", [])
            ip_str = addrs[0] if addrs else "-"

            sys.stdout.write(f"{cyan(name):<23} {itype:<12} {state_color:<19} {ap_support:<19} {mac:<20} {ip_str}\n")

        sys.stdout.write("\n")
        return EXIT_SUCCESS
    except DBusError as exc:
        sys.stderr.write(f"{red('Error listing interfaces:')} {exc.text}\n")
        return EXIT_ERROR
    finally:
        bus.disconnect()


# ---------------------------------------------------------------------------
# Argument Parsing & CLI Entry Point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Create and configure command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="netcast",
        description="NetCast — The VLC of software routers. Share any internet connection via Wi-Fi.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", title="Commands", metavar="<command>")

    # Command: start
    p_start = subparsers.add_parser("start", help="Start a Wi-Fi hotspot")
    p_start.add_argument("--source", "-s", required=True, help="Source internet interface (e.g. eth0, wwan0)")
    p_start.add_argument("--ssid", "-n", required=True, help="Hotspot Wi-Fi SSID")
    p_start.add_argument("--password", "-p", required=True, help="WPA2 passphrase (8-63 characters)")
    p_start.add_argument("--band", "-b", choices=["bg", "a"], default="bg", help="Wi-Fi band: 'bg' (2.4GHz) or 'a' (5GHz) [default: bg]")
    p_start.add_argument("--channel", "-c", type=int, default=0, help="Channel number (0 = auto-select) [default: 0]")
    p_start.add_argument("--ap", help="Optional Wi-Fi interface to host the AP on")

    # Command: stop
    subparsers.add_parser("stop", help="Stop the active Wi-Fi hotspot")

    # Command: status
    subparsers.add_parser("status", help="Display current hotspot status and connected clients")

    # Command: qr
    subparsers.add_parser("qr", help="Display Wi-Fi credentials as QR code in terminal")

    # Command: preflight
    subparsers.add_parser("preflight", help="Run system readiness checks")

    # Command: interfaces
    subparsers.add_parser("interfaces", help="List available network interfaces")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entrypoint.

    Args:
        argv: Command-line arguments sequence, or None for sys.argv[1:].

    Returns:
        Exit code: 0 on success, 1 on error, 2 if daemon is not running.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return EXIT_SUCCESS

    handlers = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "qr": cmd_qr,
        "preflight": cmd_preflight,
        "interfaces": cmd_interfaces,
    }

    handler = handlers.get(args.subcommand)
    if not handler:
        parser.print_help()
        return EXIT_ERROR

    try:
        return asyncio.run(handler(args))
    except DaemonNotRunningError as exc:
        sys.stderr.write(f"\n{red('Error: NetCast daemon is not running.')}\n")
        sys.stderr.write(f"{dim(str(exc))}\n")
        sys.stderr.write(f"{dim('Please start the daemon with:')} {cyan('sudo systemctl start netcastd')}\n\n")
        return EXIT_DAEMON_NOT_RUNNING
    except KeyboardInterrupt:
        sys.stdout.write("\nInterrupted.\n")
        return EXIT_ERROR
    except Exception as exc:
        sys.stderr.write(f"{red('Unexpected error:')} {exc}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
