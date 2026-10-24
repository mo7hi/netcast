"""Which operating system we run on and what NetCast can do there."""

import sys

_ALL_SOURCES = ["auto", "ethernet", "wifi", "usb", "cellular", "vpn", "pppoe"]

CAPABILITIES = {
    "linux": {
        "sources": _ALL_SOURCES,
        "fair_share": True,
        "captive_portal": True,
        "kick": True,
        "repeater": True,
    },
    "windows": {
        "sources": ["auto"],
        "fair_share": False,
        "captive_portal": False,
        "kick": False,
        "repeater": False,
    },
    "macos": {
        "sources": ["auto"],
        "fair_share": False,
        "captive_portal": False,
        "kick": False,
        "repeater": False,
    },
}


def current() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "other"


def capabilities(os_name: str | None = None) -> dict:
    return dict(CAPABILITIES.get(os_name or current(), CAPABILITIES["windows"]))
