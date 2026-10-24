import asyncio
import os
import plistlib
import socket
import time

import pytest
from aiohttp import web

from daemon.api.portal import render_portal
from daemon.builtin_plugins import adblock, notifications, private_dns


async def serve(handler, path="/{tail:.*}", method="*"):
    app = web.Application()
    app.router.add_route(method, path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ private DNS

def test_provider_resolution():
    assert private_dns.resolve_provider({"provider": "quad9"})[1] == "https://dns.quad9.net/dns-query"
    assert private_dns.resolve_provider({"provider": "nextdns", "nextdns_id": "ab12cd"})[1] == "https://dns.nextdns.io/ab12cd"
    assert private_dns.resolve_provider({"provider": "nextdns", "nextdns_id": "../x"})[1] is None
    assert private_dns.resolve_provider({"provider": "custom", "custom_url": "https://dns.example/q"})[1] == "https://dns.example/q"
    assert private_dns.resolve_provider({"provider": "custom", "custom_url": "http://insecure"})[1] is None


def test_mobileconfig_is_valid_plist_and_escapes():
    raw = private_dns.build_mobileconfig('Evil <&> "name"', "https://dns.example/q?a=1&b=2")
    plist = plistlib.loads(raw)
    dns = plist["PayloadContent"][0]
    assert dns["DNSSettings"] == {"DNSProtocol": "HTTPS", "ServerURL": "https://dns.example/q?a=1&b=2"}
    assert dns["PayloadType"] == "com.apple.dnsSettings.managed"
    assert plist["PayloadType"] == "Configuration"
    assert private_dns.build_mobileconfig("n", "https://x/y") == private_dns.build_mobileconfig("n", "https://x/y")  # stable UUIDs


async def test_doh_forwarder_relays_and_fails_closed():
    seen = []

    async def doh(request):
        assert request.headers["Content-Type"] == "application/dns-message"
        body = await request.read()
        seen.append(body)
        if body.startswith(b"FAIL"):
            return web.Response(status=500)
        return web.Response(body=b"\x80" + body[1:], content_type="application/dns-message")

    runner, base = await serve(doh)
    port = free_udp_port()
    fwd = private_dns.DohForwarder(base + "/dns-query", port=port)
    await fwd.start()
    loop = asyncio.get_running_loop()
    answers: asyncio.Queue = asyncio.Queue()

    class Client(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            answers.put_nowait(data)

    transport, _ = await loop.create_datagram_endpoint(Client, remote_addr=("127.0.0.1", port))
    try:
        query = b"\x12\x34" + b"\x01\x00" + b"\x00\x01" + b"\x00" * 8
        transport.sendto(query)
        reply = await asyncio.wait_for(answers.get(), 3)
        assert reply == b"\x80" + query[1:]

        transport.sendto(b"FAIL" + b"\x00" * 12)  # upstream error -> no answer, never plain DNS
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(answers.get(), 0.5)
        assert fwd.errors == 1
    finally:
        transport.close()
        await fwd.stop()
        await runner.cleanup()


async def test_private_dns_plugin_adds_dnsmasq_lines_only_when_enabled():
    plugin = private_dns.PrivateDNSPlugin()
    lines: list[str] = []
    assert plugin.extend_dnsmasq(lines) is None and lines == []
    plugin.configure_plugin("other", {"enabled": True})
    assert not plugin.enabled
    plugin.configure_plugin("private_dns", {"enabled": True, "provider": "cloudflare"})
    plugin.cfg  # noqa: B018
    # point at an unused local port so the test does not need the real one
    private_dns.PORT = free_udp_port()
    await plugin.extend_dnsmasq(lines)
    assert f"server=127.0.0.1#{private_dns.PORT}" in lines and "no-resolv" in lines
    await plugin._stop()


# --------------------------------------------------------------------- adblock

HOSTS = """# header
127.0.0.1 localhost
0.0.0.0 ads.example.com
0.0.0.0 ads.example.com
0.0.0.0 Tracker.Example.NET # trailing comment
1.2.3.4 redirected.example.org
0.0.0.0 bad_domain!.com
::1 ip6-localhost
0.0.0.0 0.0.0.0
"""


def test_filter_hosts_keeps_only_safe_blocking_entries():
    assert adblock.filter_hosts(HOSTS) == ["0.0.0.0 ads.example.com", "0.0.0.0 tracker.example.net"]


async def test_adblock_downloads_caches_and_refreshes(tmp_path):
    hits = []

    async def hosts(request):
        hits.append(1)
        return web.Response(text=HOSTS)

    runner, base = await serve(hosts)
    plugin = adblock.AdblockPlugin(cache_dir=tmp_path)
    plugin.configure_plugin("adblock", {"enabled": True, "list": "custom", "custom_url": base + "/hosts"})
    try:
        lines: list[str] = []
        await plugin.extend_dnsmasq(lines)
        path = tmp_path / lines[0].split("=", 1)[1].split("/")[-1]
        assert lines[0].startswith("addn-hosts=") and path.read_text().splitlines()[0] == "0.0.0.0 ads.example.com"
        await plugin.extend_dnsmasq([])
        assert len(hits) == 1  # fresh cache is reused
        old = time.time() - adblock.MAX_AGE - 10
        os.utime(path, (old, old))
        await plugin.extend_dnsmasq([])
        assert len(hits) == 2  # stale cache is refreshed
    finally:
        await runner.cleanup()


async def test_adblock_keeps_old_copy_when_download_fails(tmp_path):
    plugin = adblock.AdblockPlugin(cache_dir=tmp_path)
    plugin.configure_plugin("adblock", {"enabled": True, "list": "custom", "custom_url": "http://127.0.0.1:9/none"})
    lines: list[str] = []
    await plugin.extend_dnsmasq(lines)
    assert lines == []  # nothing cached and nothing downloaded: no crash, no entry


# ---------------------------------------------------------------- notifications

class Client:
    hostname = "Maria's\niPhone\x07"
    ip = "192.168.12.23"
    mac = "aa:bb:cc:dd:ee:ff"


def test_message_text_is_sanitized():
    assert notifications.describe(Client()) == "Maria'siPhone (192.168.12.23)"


@pytest.mark.parametrize("channel,key", [("discord", "json"), ("slack", "json"), ("webhook", "json"), ("ntfy", "data")])
def test_request_shape_per_channel(channel, key):
    url, kwargs = notifications.build_request({"channel": channel, "url": "https://x.example/h"}, "T", "B", {"event": "join"})
    assert url == "https://x.example/h" and key in kwargs


def test_telegram_builds_api_url_and_ntfy_title_is_ascii():
    url, kw = notifications.build_request({"channel": "telegram", "telegram_token": "1:abc", "telegram_chat": "42"}, "T", "B", {})
    assert url == "https://api.telegram.org/bot1:abc/sendMessage" and kw["json"]["chat_id"] == "42"
    _, kw = notifications.build_request({"channel": "ntfy", "url": "https://n.sh/t"}, "Nüw dévice", "B", {})
    assert kw["headers"]["Title"] == "Nw dvice"


async def test_notification_is_posted_when_a_device_joins():
    got = []

    async def receive(request):
        got.append(await request.json())
        return web.Response(text="ok")

    runner, base = await serve(receive)
    plugin = notifications.NotificationsPlugin()
    plugin.configure_plugin("notifications", {"enabled": True, "channel": "webhook", "url": base + "/hook", "notify_join": True})
    try:
        plugin.on_client_connect(Client())
        await asyncio.sleep(0.3)
        assert got and got[0]["event"] == "join" and "192.168.12.23" in got[0]["message"]

        plugin.configure_plugin("notifications", {"enabled": False, "channel": "webhook", "url": base + "/hook"})
        plugin.on_client_connect(Client())
        await asyncio.sleep(0.2)
        assert len(got) == 1  # disabled plugin stays silent
    finally:
        await runner.cleanup()


# ----------------------------------------------------------------- portal page

class FakeState:
    config = {"captive_portal": {"message": "Hi <b>there</b>"}, "hotspot": {"ssid": "Cafe <script>"}}
    _active_config = None

    def portal_links(self):
        return [{"label": "Private DNS", "url": "/portal/private-dns"}]


def test_portal_escapes_everything_and_lists_plugin_links():
    page = render_portal(FakeState())
    assert "<script>alert" not in page and "Cafe &lt;script&gt;" in page
    assert "Hi &lt;b&gt;there&lt;/b&gt;" in page
    assert '<a href="/portal/private-dns">Private DNS</a>' in page
    assert 'lang="en"' in page and "aria-live" in page
