"""Ad and tracker blocking plugin.

Downloads a public blocklist in hosts format, keeps only plain "0.0.0.0 domain" lines,
and gives it to dnsmasq, which answers blocked names with nothing. Works for every
device on the hotspot with no app to install.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any

import aiohttp

from daemon.hooks import hookimpl

logger = logging.getLogger("netcast.plugins.adblock")

MAX_BYTES = 30 * 1024 * 1024
MAX_AGE = 7 * 24 * 3600
_BASE = "https://raw.githubusercontent.com/StevenBlack/hosts/master"
LISTS = {
    "unified": ("Ads, trackers and malware", f"{_BASE}/hosts"),
    "strict": ("Also block fake news and gambling", f"{_BASE}/alternates/fakenews-gambling/hosts"),
    "custom": ("Your own list (hosts format)", None),
}
_LINE = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1)\s+([A-Za-z0-9][A-Za-z0-9._-]{0,251})\s*(?:#.*)?$")
_SKIP = {"localhost", "localhost.localdomain", "local", "broadcasthost", "ip6-localhost", "ip6-loopback", "0.0.0.0"}


def filter_hosts(text: str, limit: int = 500_000) -> list[str]:
    """Keep only well-formed blocking entries. Anything else (other IPs, junk) is dropped,
    so an untrusted list can block names but never redirect them elsewhere."""
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        domain = m.group(1).lower()
        if domain in _SKIP or domain in seen:
            continue
        seen.add(domain)
        out.append(f"0.0.0.0 {domain}")
        if len(out) >= limit:
            break
    return out


class AdblockPlugin:
    ID = "adblock"

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cfg: dict[str, Any] = {}
        self._cache_dir = cache_dir

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    def _dir(self) -> Path:
        if self._cache_dir is None:
            from daemon.watchdog import get_state_dir

            self._cache_dir = get_state_dir()
        return self._cache_dir

    def _url(self) -> str | None:
        key = self.cfg.get("list", "unified")
        if key == "custom":
            url = str(self.cfg.get("custom_url", "")).strip()
            return url if url.startswith(("https://", "http://")) else None
        return LISTS.get(key, LISTS["unified"])[1]

    @hookimpl
    def register_plugin(self, plugins: list[dict[str, Any]]) -> None:
        plugins.append({
            "id": self.ID,
            "title": "Ad and tracker blocking",
            "description": "Blocks known ad, tracker and malware websites for every device on your hotspot, with nothing to install on the devices.",
            "needs_restart": True,
            "fields": [
                {"key": "list", "label": "What to block", "type": "select", "default": "unified",
                 "options": [{"value": k, "label": v[0]} for k, v in LISTS.items()]},
                {"key": "custom_url", "label": "Blocklist address", "type": "url", "default": "",
                 "show_if": {"key": "list", "value": "custom"}},
            ],
            "links": [],
        })

    @hookimpl
    def configure_plugin(self, plugin_id: str, values: dict[str, Any]) -> None:
        if plugin_id == self.ID:
            self.cfg = dict(values)

    @hookimpl
    def extend_dnsmasq(self, lines: list[str]) -> Any:
        return self._prepare(lines) if self.enabled else None

    async def _prepare(self, lines: list[str]) -> None:
        url = self._url()
        if not url:
            return
        path = self._dir() / f"blocklist-{hashlib.sha1(url.encode()).hexdigest()[:10]}.hosts"
        stale = not path.exists() or time.time() - path.stat().st_mtime > MAX_AGE
        if stale:
            try:
                path.write_text("\n".join(filter_hosts(await self._download(url))) + "\n", encoding="utf-8")
                logger.info("Blocklist updated (%s)", path.name)
            except Exception as exc:
                logger.warning("Could not update the blocklist (%s); %s", type(exc).__name__,
                               "using the old copy" if path.exists() else "ads will not be blocked")
        if path.exists():
            lines.append(f"addn-hosts={path}")

    @staticmethod
    async def _download(url: str) -> str:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.content.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("blocklist is too large")
        return data.decode("utf-8", errors="ignore")
