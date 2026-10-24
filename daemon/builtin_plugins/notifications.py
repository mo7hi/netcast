"""Notifications plugin: tell you when devices join or leave, via ntfy, Telegram,
Discord, Slack or any webhook."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp

from daemon.hooks import hookimpl

logger = logging.getLogger("netcast.plugins.notifications")

CHANNELS = [
    {"value": "ntfy", "label": "ntfy (free phone notifications)"},
    {"value": "telegram", "label": "Telegram"},
    {"value": "discord", "label": "Discord"},
    {"value": "slack", "label": "Slack"},
    {"value": "webhook", "label": "Any web address (JSON)"},
]
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def clean(text: str | None, limit: int = 60) -> str:
    return _CTRL.sub("", text or "")[:limit]


def describe(client: Any) -> str:
    name = clean(getattr(client, "hostname", None)) or "Unknown device"
    return f"{name} ({clean(getattr(client, 'ip', ''))})"


def build_request(cfg: dict[str, Any], title: str, body: str, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return (url, kwargs for session.post) for the configured channel."""
    ch = cfg.get("channel", "ntfy")
    url = str(cfg.get("url", "")).strip()
    if ch == "telegram":
        token, chat = str(cfg.get("telegram_token", "")).strip(), str(cfg.get("telegram_chat", "")).strip()
        return f"https://api.telegram.org/bot{token}/sendMessage", {"json": {"chat_id": chat, "text": f"{title}\n{body}"}}
    if ch == "discord":
        return url, {"json": {"content": f"**{title}**\n{body}"}}
    if ch == "slack":
        return url, {"json": {"text": f"*{title}*\n{body}"}}
    if ch == "webhook":
        return url, {"json": {"title": title, "message": body, **extra}}
    ascii_title = title.encode("ascii", "ignore").decode() or "NetCast"
    return url, {"data": body.encode("utf-8"), "headers": {"Title": ascii_title}}


class NotificationsPlugin:
    ID = "notifications"

    def __init__(self) -> None:
        self.cfg: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    @hookimpl
    def register_plugin(self, plugins: list[dict[str, Any]]) -> None:
        not_tg = {"key": "channel", "not": "telegram"}
        plugins.append({
            "id": self.ID,
            "title": "Notifications",
            "description": "Get a message on your phone when a new device joins your hotspot.",
            "needs_restart": False,
            "fields": [
                {"key": "channel", "label": "Send messages with", "type": "select", "default": "ntfy", "options": CHANNELS},
                {"key": "url", "label": "Address", "type": "url", "default": "", "show_if": not_tg,
                 "help": "ntfy: https://ntfy.sh/your-secret-topic. Discord, Slack and webhooks: the address they give you."},
                {"key": "telegram_token", "label": "Telegram bot token", "type": "password", "default": "",
                 "show_if": {"key": "channel", "value": "telegram"}},
                {"key": "telegram_chat", "label": "Telegram chat ID", "type": "text", "default": "",
                 "show_if": {"key": "channel", "value": "telegram"}},
                {"key": "notify_join", "label": "When a device joins", "type": "toggle", "default": True},
                {"key": "notify_leave", "label": "When a device leaves", "type": "toggle", "default": False},
                {"key": "notify_hotspot", "label": "When the hotspot starts or stops", "type": "toggle", "default": True},
            ],
            "links": [],
        })

    @hookimpl
    def configure_plugin(self, plugin_id: str, values: dict[str, Any]) -> None:
        if plugin_id == self.ID:
            self.cfg = dict(values)

    def _send(self, title: str, body: str, extra: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        asyncio.get_running_loop().create_task(self._post(title, body, extra or {}))

    async def _post(self, title: str, body: str, extra: dict[str, Any]) -> None:
        try:
            url, kwargs = build_request(self.cfg, title, body, extra)
            if not url.startswith(("https://", "http://")):
                return
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s, s.post(url, **kwargs) as r:
                if r.status >= 400:
                    logger.warning("Notification service answered HTTP %d", r.status)
        except Exception as exc:  # never log the URL: it may hold a secret token
            logger.warning("Could not send notification (%s)", type(exc).__name__)

    @hookimpl
    def on_client_connect(self, client: Any) -> None:
        if self.cfg.get("notify_join", True):
            self._send("New device on your hotspot", describe(client), {"event": "join", "mac": getattr(client, "mac", "")})

    @hookimpl
    def on_client_disconnect(self, client: Any) -> None:
        if self.cfg.get("notify_leave", False):
            self._send("Device left your hotspot", describe(client), {"event": "leave", "mac": getattr(client, "mac", "")})

    @hookimpl
    def on_hotspot_start(self, config: Any, status: dict) -> None:
        if self.cfg.get("notify_hotspot", True):
            self._send("Hotspot is on", clean(status.get("ssid")), {"event": "start"})

    @hookimpl
    def on_hotspot_stop(self, reason: str) -> None:
        if self.cfg.get("notify_hotspot", True):
            self._send("Hotspot is off", clean(reason), {"event": "stop"})
