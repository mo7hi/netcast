"""Privileged PPPoE helper, executed as root by netcast-pppoe.service.

pppd refuses its dangerous options (``plugin`` etc.) for non-root users, so the
unprivileged daemon cannot run it. Instead the daemon drops a small JSON request
and starts the unit. This helper validates every field, writes the pppd options
file itself, and execs pppd. The daemon never controls raw pppd options.
"""

import glob
import json
import os
import re
import shutil
import sys

REQUEST = "/run/netcast/pppoe.json"
OPTIONS = "/run/netcast-pppoe/options"
IFNAME = "netcastppp0"
_NIC_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


def _clean(value: object, what: str) -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= 255):
        raise ValueError(f"{what} must be 1-255 characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{what} contains control characters")
    return value


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def validate(data: dict) -> dict:
    nic = data.get("nic")
    if not isinstance(nic, str) or not _NIC_RE.fullmatch(nic):
        raise ValueError("invalid NIC name")
    mtu = data.get("mtu", 1492)
    if not isinstance(mtu, int) or isinstance(mtu, bool) or not (576 <= mtu <= 1500):
        raise ValueError("invalid MTU")
    return {
        "nic": nic,
        "username": _clean(data.get("username"), "username"),
        "password": _clean(data.get("password"), "password"),
        "mtu": mtu,
    }


def plugin_name() -> str:
    for pattern in ("/usr/lib/pppd/*/rp-pppoe.so", "/usr/lib/pppd/*/pppoe.so"):
        found = sorted(glob.glob(pattern))
        if found:
            return os.path.basename(found[0])
    return "rp-pppoe.so"


def build_options(req: dict, plugin: str) -> str:
    return "\n".join(
        [
            f"plugin {plugin}",
            f"nic-{req['nic']}",
            f"user {quote(req['username'])}",
            f"password {quote(req['password'])}",
            f"ifname {IFNAME}",
            "noauth",
            "defaultroute",
            "replacedefaultroute",
            "usepeerdns",
            f"mtu {req['mtu']}",
            f"mru {req['mtu']}",
            "noipdefault",
            "hide-password",
            "lcp-echo-interval 20",
            "lcp-echo-failure 3",
            "persist",
            "maxfail 0",
            "holdoff 5",
            "noccp",
            "",
        ]
    )


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else REQUEST
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            raw = fh.read(8192)
    finally:
        try:
            os.unlink(path)  # do not leave credentials lying around
        except OSError:
            pass
    try:
        req = validate(json.loads(raw))
    except (ValueError, TypeError) as exc:
        print(f"netcast-pppoe: rejected request: {exc}", file=sys.stderr)
        return 2

    out_fd = os.open(OPTIONS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(out_fd, "w", encoding="utf-8") as fh:
        fh.write(build_options(req, plugin_name()))

    pppd = shutil.which("pppd") or "/usr/sbin/pppd"
    os.execv(pppd, ["pppd", "file", OPTIONS, "nodetach"])
    return 1  # unreachable


if __name__ == "__main__":
    sys.exit(main(sys.argv))
