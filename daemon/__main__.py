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

"""NetCast networking daemon entry point.

Initializes asyncio event loop, pure-Python systemd notifications, pluggy plugin
manager, aiohttp web server, random session token, Dead Man's Switch, and
systemd watchdog loop with health check validation.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import secrets
import signal
import socket
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from daemon.api.web import create_app
from daemon.config import NetCastConfig, load_config
from daemon.plugins import NetCastPluginManager, get_plugin_manager
from daemon.state import DaemonState
from daemon.watchdog import DeadMansSwitch

if TYPE_CHECKING:
    pass

logger = logging.getLogger("netcast.daemon")

DEFAULT_TOKEN_PATH = Path("/var/lib/netcast/session.token")
USER_TOKEN_PATH = Path.home() / ".local" / "state" / "netcast" / "session.token"


class SystemdNotifier:
    """Pure-Python implementation of systemd sd_notify via direct AF_UNIX socket.

    Does not depend on any third-party systemd libraries. Works with standard
    and abstract UNIX domain sockets ($NOTIFY_SOCKET).
    """

    def __init__(self) -> None:
        """Initialize notifier from the NOTIFY_SOCKET environment variable."""
        self.socket_path: str | None = os.environ.get("NOTIFY_SOCKET")

    @property
    def is_enabled(self) -> bool:
        """Return True if systemd notification socket is configured."""
        return bool(self.socket_path)

    def notify(self, state: str) -> bool:
        """Send notification datagram to systemd manager socket.

        :param state: Protocol string formatted as KEY=VALUE lines (e.g., READY=1, STOPPING=1).
        :return: True if successfully sent, False otherwise.
        """
        if not self.socket_path:
            return False

        addr = self.socket_path
        if addr.startswith("@"):
            # Linux abstract namespace socket
            addr = "\0" + addr[1:]

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                msg = state if state.endswith("\n") else f"{state}\n"
                sock.sendto(msg.encode("utf-8"), addr)
            logger.debug("Sent systemd notification: %r", state.strip())
            return True
        except OSError as err:
            logger.warning("Failed to send systemd notification %r: %s", state.strip(), err)
            return False


def generate_session_token(target_path: Path = DEFAULT_TOKEN_PATH) -> str:
    """Generate a random cryptographic session token and save with 0600 permissions.

    Attempts saving to /var/lib/netcast/session.token with fallback to user state directory.

    :param target_path: Preferred file path for session token.
    :return: Generated 64-character hexadecimal session token string.
    """
    token = secrets.token_hex(32)
    candidate_paths = [
        target_path,
        USER_TOKEN_PATH,
        Path.home() / ".config" / "netcast" / "session.token",
    ]

    saved = False
    for path in candidate_paths:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(path, flags, 0o600)
            with open(fd, "w", encoding="ascii") as f:
                f.write(token + "\n")
            logger.info("Saved daemon session token to %s (mode 0600)", path)
            saved = True
            break
        except OSError as err:
            logger.debug("Unable to write session token to %s: %s", path, err)

    if not saved:
        logger.warning(
            "Could not persist session token to disk; operating with in-memory token only."
        )

    return token


async def check_event_loop_health(
    loop: asyncio.AbstractEventLoop,
    runner: web.AppRunner | None,
    max_lag_seconds: float = 2.0,
) -> bool:
    """Health check validating that the asyncio event loop and web server are responsive.

    :param loop: The active asyncio event loop.
    :param runner: The aiohttp web server AppRunner.
    :param max_lag_seconds: Maximum tolerable latency in event loop scheduling.
    :return: True if healthy, False if loop is starved or server has stopped.
    """
    t0 = loop.time()
    await asyncio.sleep(0.005)
    lag = loop.time() - t0 - 0.005

    if lag > max_lag_seconds:
        logger.error(
            "Health check failed: event loop responsiveness degraded (lag=%0.4fs > %0.1fs)",
            lag,
            max_lag_seconds,
        )
        return False

    if runner is not None and not runner.sites:
        logger.error("Health check failed: aiohttp web server is no longer serving")
        return False

    return True


async def systemd_watchdog_task(
    notifier: SystemdNotifier,
    interval_seconds: float,
    runner: web.AppRunner,
    shutdown_event: asyncio.Event,
) -> None:
    """Periodic background task that performs health checks and pings systemd watchdog.

    :param notifier: SystemdNotifier instance.
    :param interval_seconds: Ping interval (WatchdogSec / 2).
    :param runner: aiohttp AppRunner to check during health evaluations.
    :param shutdown_event: Cancellation/shutdown trigger.
    """
    loop = asyncio.get_running_loop()
    logger.info(
        "Starting systemd watchdog task (interval=%0.2fs)",
        interval_seconds,
    )

    while not shutdown_event.is_set():
        try:
            # Wait for next interval or shutdown signal
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval_seconds)
                break
            except TimeoutError:
                pass

            # Perform comprehensive health check before sending ping
            healthy = await check_event_loop_health(loop, runner)
            if healthy:
                notifier.notify("WATCHDOG=1")
            else:
                logger.warning("Suppressed WATCHDOG=1 ping due to failed health check")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Unexpected error in watchdog ping loop: %s", exc, exc_info=True)


async def run_daemon(config_path: Path | str | None = None) -> None:
    """Main daemon execution lifecycle.

    :param config_path: Optional explicit configuration file path.
    """
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    # 1. Initialize pure-Python sd_notify
    notifier = SystemdNotifier()

    # 2. Attach POSIX signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, lambda s=sig: shutdown_event.set())

    # 3. Load configuration
    config: NetCastConfig = load_config(config_path)

    # 4. Initialize pluggy PluginManager
    logger.info("Initializing NetCast plugin manager...")
    plugin_mgr: NetCastPluginManager = get_plugin_manager()

    # 5. Arm Dead Man's Switch
    dms = DeadMansSwitch()
    try:
        dms.arm(fork_sentinel=True, install_signal_handlers=False)
    except Exception as exc:
        logger.warning("Could not arm Dead Man's Switch sentinel: %s", exc)

    # 6. Generate session token
    session_token = generate_session_token()

    # 7. Initialize and start aiohttp web server
    logger.info(
        "Starting web server on %s:%d",
        config.daemon.listen_host,
        config.daemon.listen_port,
    )
    state = DaemonState(
        config=config, plugin_mgr=plugin_mgr, session_token=session_token, dms=dms
    )
    app = create_app(
        daemon_state=state, plugin_mgr=plugin_mgr, session_token=session_token, config=config
    )
    runner = web.AppRunner(app)
    state.attach(app, runner)
    await runner.setup()
    site = web.TCPSite(
        runner,
        host=config.daemon.listen_host,
        port=config.daemon.listen_port,
    )
    await site.start()
    await state.start()  # D-Bus service + client monitor

    # 8. Start systemd watchdog background task if $WATCHDOG_USEC is set
    watchdog_task: asyncio.Task[None] | None = None
    watchdog_usec_env = os.environ.get("WATCHDOG_USEC")
    if watchdog_usec_env and watchdog_usec_env.isdigit():
        usec = int(watchdog_usec_env)
        interval = (usec / 1_000_000.0) / 2.0
        if interval > 0:
            watchdog_task = asyncio.create_task(
                systemd_watchdog_task(notifier, interval, runner, shutdown_event),
                name="systemd-watchdog",
            )

    # 9. Send READY=1 after successful initialization
    status_msg = (
        f"READY=1\nSTATUS=NetCast daemon running on "
        f"{config.daemon.listen_host}:{config.daemon.listen_port}"
    )
    notifier.notify(status_msg)
    logger.info("NetCast daemon initialized and ready.")

    # 10. Wait for shutdown signal (SIGTERM / SIGINT)
    await shutdown_event.wait()
    logger.info("Shutdown signal received; initiating graceful termination...")

    # 11. Send STOPPING=1 on shutdown
    notifier.notify("STOPPING=1\nSTATUS=NetCast daemon shutting down")

    await state.shutdown()  # stop hotspot, tear down firewall, close D-Bus

    # 12. Cancel background watchdog task
    if watchdog_task is not None:
        watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog_task

    # 13. Stop web server
    logger.info("Shutting down aiohttp web server...")
    await runner.cleanup()

    # 14. Disarm Dead Man's Switch cleanly
    dms.disarm()
    logger.info("NetCast daemon stopped cleanly.")


def main() -> None:
    """Command-line entry point for netcastd."""
    parser = argparse.ArgumentParser(
        prog="netcastd",
        description="NetCast privilege-separated Linux networking daemon",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to TOML configuration file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging output",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        asyncio.run(run_daemon(args.config))
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as exc:
        logger.critical("Fatal daemon crash: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
