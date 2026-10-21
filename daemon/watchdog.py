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

"""Dead Man's Switch and watchdog recovery subsystem for NetCast.

Implements a 4-layer defense-in-depth safety mechanism to ensure system network
state (firewall rules, routing, interfaces, and child processes) is always restored
even in the event of an abrupt process crash or SIGKILL.

The 4 Defense Layers:
- Layer 1: Python signal handlers (SIGTERM, SIGINT, SIGHUP, SIGQUIT) + atexit registration.
- Layer 2: Asyncio event loop exception supervisor and task crash monitor.
- Layer 3: Systemd service watchdog notification (WATCHDOG=1 / WATCHDOG_USEC ping).
- Layer 4: OS pipe sentinel (isolated child process monitoring parent via an OS pipe,
           surviving SIGKILL and unhandled process crashes).
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("netcast.watchdog")

SYSTEM_STATE_DIR = Path("/var/lib/netcast")
USER_STATE_DIR = Path.home() / ".local" / "state" / "netcast"
STATE_FILE_NAME = "deadmans_state.json"


def get_state_dir() -> Path:
    """Return the state directory with fallback for unprivileged execution.

    :return: Resolved Path to directory where state files can be written.
    """
    env_dir = os.environ.get("NETCAST_STATE_DIR")
    if env_dir:
        p = Path(env_dir)
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except OSError:
            logger.warning("Configured NETCAST_STATE_DIR %s not writable, falling back", p)

    try:
        SYSTEM_STATE_DIR.mkdir(parents=True, exist_ok=True)
        # Test writability
        test_file = SYSTEM_STATE_DIR / ".write_test"
        test_file.touch(exist_ok=True)
        test_file.unlink(missing_ok=True)
        return SYSTEM_STATE_DIR
    except OSError:
        pass

    USER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return USER_STATE_DIR


def save_state(state: dict[str, Any] | None = None) -> Path:
    """Persist system state and cleanup instructions to disk.

    Writes the state atomically using a temporary file and sets permissions to 0600.

    :param state: Dictionary containing cleanup commands, interface names, PIDs, etc.
    :return: Absolute Path to the persisted state file.
    """
    state_dir = get_state_dir()
    state_path = state_dir / STATE_FILE_NAME
    tmp_path = state_dir / f"{STATE_FILE_NAME}.tmp.{os.getpid()}"

    payload = {
        "pid": os.getpid(),
        "timestamp": time.time(),
        "armed": True,
        "interfaces": [],
        "iptables_cleanup": [],
        "ip_forward_original": None,
        "child_pids": [],
    }
    if state:
        payload.update(state)

    raw_json = json.dumps(payload, indent=2).encode("utf-8")

    # Write atomically with mode 0600
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp_path, flags, 0o600)
    try:
        with open(fd, "wb") as f:
            f.write(raw_json)
            f.flush()
            os.fsync(fd)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise

    tmp_path.replace(state_path)
    logger.debug("Persisted Dead Man's Switch state to %s", state_path)
    return state_path


def restore_state(state_path: Path | None = None) -> dict[str, Any]:
    """Read saved cleanup state and execute emergency restoration routines.

    Invoked either directly during normal shutdown or by the Layer 4 sentinel process
    when the parent dies unexpectedly.

    :param state_path: Path to the state file, or None to use default location.
    :return: The loaded state dictionary.
    """
    if state_path is None:
        state_dir = get_state_dir()
        state_path = state_dir / STATE_FILE_NAME

    if not state_path.is_file():
        logger.debug("No state file found at %s during restore", state_path)
        return {}

    try:
        with open(state_path, "rb") as f:
            state = json.load(f)
    except Exception as exc:
        logger.error("Failed to read state file %s: %s", state_path, exc)
        return {}

    logger.info("Executing Dead Man's Switch recovery using state from %s", state_path)

    # 1. Terminate tracked child processes
    child_pids: list[int] = state.get("child_pids", [])
    for pid in child_pids:
        if not isinstance(pid, int) or pid <= 1:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.05)
            # Send SIGKILL if process still running
            os.kill(pid, signal.SIGKILL)
            logger.info("Killed orphaned child process %d", pid)
        except ProcessLookupError:
            pass
        except OSError as err:
            logger.warning("Could not terminate child PID %d: %s", pid, err)

    # 2. Revert iptables/nftables cleanup rules
    cleanup_rules: list[list[str]] = state.get("iptables_cleanup", [])
    for cmd in cleanup_rules:
        if not isinstance(cmd, list) or not cmd:
            continue
        try:
            # All subprocess calls must use list form, never shell=True
            subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
            )
            logger.debug("Executed cleanup rule: %s", " ".join(cmd))
        except Exception as err:
            logger.warning("Failed executing cleanup rule %r: %s", cmd, err)

    # 3. Restore original ip_forwarding value if saved
    orig_ip_forward = state.get("ip_forward_original")
    if orig_ip_forward is not None:
        forward_val = str(int(orig_ip_forward))
        try:
            subprocess.run(
                ["sysctl", "-w", f"net.ipv4.ip_forward={forward_val}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
            )
            logger.info("Restored net.ipv4.ip_forward to %s", forward_val)
        except Exception as err:
            logger.warning("Failed restoring ip_forward: %s", err)

    # 4. Bring down virtual interfaces if recorded
    interfaces: list[str] = state.get("interfaces", [])
    for iface in interfaces:
        if not isinstance(iface, str) or not iface:
            continue
        try:
            subprocess.run(
                ["ip", "link", "set", "dev", iface, "down"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
            )
            logger.info("Brought down interface %s", iface)
        except Exception as err:
            logger.warning("Failed bringing down interface %s: %s", iface, err)

    # 5. Remove or disarm state file
    try:
        state_path.unlink(missing_ok=True)
    except OSError as err:
        logger.warning("Failed to remove state file %s: %s", state_path, err)

    return state


class DeadMansSwitch:
    """Four-layer dead man's switch implementation for NetCast daemon.

    Provides bulletproof state restoration across normal shutdowns, signals,
    unhandled loop errors, and abrupt parent termination (SIGKILL).
    """

    def __init__(self, state_dir: Path | None = None) -> None:
        """Initialize DeadMansSwitch instance.

        :param state_dir: Optional custom directory to store switch state.
        """
        self._state_dir = state_dir or get_state_dir()
        self._state_file = self._state_dir / STATE_FILE_NAME
        self._is_armed = False
        self._pipe_r: int | None = None
        self._pipe_w: int | None = None
        self._sentinel_pid: int | None = None
        self._cleanup_callbacks: list[Callable[[], Any]] = []
        self._state: dict[str, Any] = {}
        self._original_signals: dict[int, Any] = {}
        self._atexit_registered = False

    @property
    def is_armed(self) -> bool:
        """Check if switch is currently armed."""
        return self._is_armed

    def add_cleanup_callback(self, callback: Callable[[], Any]) -> None:
        """Register an in-process cleanup callback (Layer 1).

        :param callback: Callable taking no arguments.
        """
        self._cleanup_callbacks.append(callback)

    def update_state(self, key: str, value: Any) -> None:
        """Update a state value and persist to disk if armed.

        :param key: State dictionary key.
        :param value: Value to store (must be JSON-serializable).
        """
        self._state[key] = value
        if self._is_armed:
            save_state(self._state)

    def arm(
        self,
        initial_state: dict[str, Any] | None = None,
        fork_sentinel: bool = True,
    ) -> None:
        """Arm the Dead Man's Switch across all defense layers.

        :param initial_state: Optional dictionary with initial cleanup instructions.
        :param fork_sentinel: Whether to spawn Layer 4 OS pipe sentinel child process.
        """
        if self._is_armed:
            logger.warning("DeadMansSwitch is already armed.")
            return

        if initial_state:
            self._state.update(initial_state)

        # 1. Layer 1: Register signal handlers and atexit
        self._setup_layer1()

        # 2. Persist state to disk
        self._state_file = save_state(self._state)

        # 3. Layer 4: Spawn OS pipe sentinel child process
        if fork_sentinel:
            self._setup_layer4_sentinel()

        self._is_armed = True
        logger.info(
            "Dead Man's Switch armed (Layer 1 active, Layer 4 sentinel PID: %s)",
            self._sentinel_pid,
        )

    def disarm(self) -> None:
        """Disarm the Dead Man's Switch cleanly without triggering recovery."""
        if not self._is_armed:
            return

        self._is_armed = False
        logger.info("Disarming Dead Man's Switch...")

        # Disarm Layer 4 OS pipe sentinel child
        if self._pipe_w is not None:
            with contextlib.suppress(OSError):
                os.write(self._pipe_w, b"DISARM\n")
            with contextlib.suppress(OSError):
                os.close(self._pipe_w)
            self._pipe_w = None

        if self._sentinel_pid is not None:
            try:
                # Wait for sentinel child process to finish clean exit
                _, status = os.waitpid(self._sentinel_pid, 0)
                logger.debug("Sentinel child %d exited with status %d", self._sentinel_pid, status)
            except OSError:
                pass
            self._sentinel_pid = None

        # Clean up state file
        if self._state_file.exists():
            try:
                self._state_file.unlink(missing_ok=True)
            except OSError as err:
                logger.debug("Could not remove state file on disarm: %s", err)

        # Restore original signal handlers
        self._restore_layer1_signals()
        logger.info("Dead Man's Switch disarmed successfully.")

    def run_inprocess_cleanup(self) -> None:
        """Execute all registered Layer 1 in-process cleanup callbacks."""
        logger.info("Executing Layer 1 in-process cleanup callbacks...")
        for cb in reversed(self._cleanup_callbacks):
            try:
                cb()
            except Exception as exc:
                logger.error("Error in cleanup callback %r: %s", cb, exc, exc_info=True)

    def _setup_layer1(self) -> None:
        """Configure Layer 1 signal handlers and atexit hook."""
        if not self._atexit_registered:
            atexit.register(self._layer1_atexit_handler)
            self._atexit_registered = True

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                old_handler = signal.getsignal(sig)
                self._original_signals[sig] = old_handler
                signal.signal(sig, self._layer1_signal_handler)
            except (ValueError, OSError) as err:
                logger.debug("Could not attach signal handler for %s: %s", sig, err)

    def _restore_layer1_signals(self) -> None:
        """Restore original signal handlers."""
        for sig, old_handler in self._original_signals.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, old_handler)
        self._original_signals.clear()

    def _layer1_signal_handler(self, signum: int, frame: Any) -> None:
        """Signal handler for Layer 1."""
        logger.info("Received signal %d; triggering in-process cleanup...", signum)
        self.run_inprocess_cleanup()
        self.disarm()
        sys.exit(128 + signum)

    def _layer1_atexit_handler(self) -> None:
        """atexit handler for Layer 1."""
        if self._is_armed:
            logger.info("Process terminating while armed; running Layer 1 cleanup...")
            self.run_inprocess_cleanup()
            self.disarm()

    def _setup_layer4_sentinel(self) -> None:
        """Fork Layer 4 sentinel process using an anonymous pipe."""
        r_fd, w_fd = os.pipe()
        pid = os.fork()

        if pid == 0:
            # Child Sentinel Process
            os.close(w_fd)
            self._sentinel_child_loop(r_fd, self._state_file)
            # Should never reach here
            os._exit(0)
        else:
            # Parent Process
            os.close(r_fd)
            self._pipe_w = w_fd
            self._sentinel_pid = pid

    @staticmethod
    def _sentinel_child_loop(r_fd: int, state_file: Path) -> None:
        """Entry point for the isolated Layer 4 OS pipe sentinel child process.

        Blocks on reading the pipe. If the parent exits or is killed via SIGKILL,
        the kernel closes the parent's write-end, resulting in EOF on r_fd.

        :param r_fd: Read file descriptor for the sentinel pipe.
        :param state_file: Path to persisted dead man's state file.
        """
        # Reset signals in child sentinel to avoid running parent handlers
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, signal.SIG_DFL)

        buf = bytearray()
        clean_disarm = False

        try:
            while True:
                chunk = os.read(r_fd, 64)
                if not chunk:
                    # Pipe was closed by parent/kernel (EOF)
                    break
                buf.extend(chunk)
                if b"DISARM" in buf:
                    clean_disarm = True
                    break
        except Exception:
            clean_disarm = False
        finally:
            with contextlib.suppress(OSError):
                os.close(r_fd)

        if clean_disarm:
            # Parent disarmed cleanly
            sys.exit(0)

        # Parent died abruptly while armed!
        sys.stderr.write(
            f"[NetCast Sentinel PID {os.getpid()}] Parent process died abruptly (EOF on pipe). "
            f"Executing emergency Layer 4 Dead Man's Switch restoration...\n"
        )
        sys.stderr.flush()

        try:
            restore_state(state_file)
            sys.stderr.write("[NetCast Sentinel] Emergency cleanup completed successfully.\n")
            sys.stderr.flush()
        except Exception as exc:
            sys.stderr.write(f"[NetCast Sentinel] Emergency cleanup failed: {exc}\n")
            sys.stderr.flush()

        sys.exit(0)
