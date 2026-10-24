"""Per-platform locations for NetCast state and the built web UI."""

import os
import sys
from pathlib import Path


def _system_state() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "NetCast"
    if sys.platform == "darwin":
        return Path("/Library/Application Support/NetCast")
    return Path("/var/lib/netcast")


def _user_state() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NetCast"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "NetCast"
    return Path.home() / ".local" / "state" / "netcast"


def _web_dir() -> Path:
    override = os.environ.get("NETCAST_WEB_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "NetCast" / "web"
    if sys.platform == "darwin":
        return Path("/usr/local/lib/netcast/web")
    return Path("/usr/lib/netcast/web")


SYSTEM_STATE_DIR = _system_state()
USER_STATE_DIR = _user_state()
WEB_DIR = _web_dir()
