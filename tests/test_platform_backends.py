import pytest

from daemon.backends import macos, windows
from daemon.backends.base import HotspotConfig

ARP = """? (192.168.2.2) at a4:83:e7:1:22:33 on bridge100 ifscope [ethernet]
? (192.168.2.255) at ff:ff:ff:ff:ff:ff on bridge100 ifscope [ethernet]
? (192.168.2.9) at (incomplete) on bridge100 ifscope [ethernet]
"""


async def test_macos_reports_devices_and_waits_for_user(monkeypatch):
    calls = []

    async def fake_run(*args):
        calls.append(args)
        if args[0] == "ifconfig":
            return 0, "bridge100: flags=8863\n\tinet 192.168.2.1 netmask 0xffffff00\n\tstatus: active\n"
        if args[0] == "arp":
            return 0, ARP
        return 0, ""

    monkeypatch.setattr(macos, "_run", fake_run)
    monkeypatch.setattr(macos.shutil, "which", lambda name: "/usr/bin/" + name)
    backend = macos.MacOSAssistBackend()
    assert backend.assistant and len(backend.steps) >= 4
    status = await backend.create_hotspot(HotspotConfig(ssid="Cafe", password="longenough1"))
    assert status.active and status.ssid == "Cafe" and status.gateway_ip == "192.168.2.1"
    assert [c.mac for c in await backend.get_connected_clients()] == ["a4:83:e7:01:22:33"]
    assert any(a[0] == "open" for a in calls)  # opened System Settings


async def test_macos_inactive_until_sharing_is_on(monkeypatch):
    async def fake_run(*args):
        return 1, ""

    monkeypatch.setattr(macos, "_run", fake_run)
    monkeypatch.setattr(macos.shutil, "which", lambda name: None)
    backend = macos.MacOSAssistBackend()
    assert not (await backend.create_hotspot(HotspotConfig(ssid="Cafe", password="longenough1"))).active


async def test_windows_passes_secrets_through_environment_not_script(monkeypatch):
    seen = {}

    async def fake_ps(self, body, env=None):
        seen.update(body=body, env=env or {})
        if "ConfigureAccessPointAsync" in body:
            return {"ok": True}
        return {"active": True, "ssid": "Cafe", "password": "p@ss\"word;1", "clients": 2, "band": "FiveGigahertz"}

    monkeypatch.setattr(windows.WindowsBackend, "_ps", fake_ps)
    backend = windows.WindowsBackend()
    status = await backend.create_hotspot(HotspotConfig(ssid='Cafe "x"', password='p@ss"word;1', band="a"))
    assert status.active and status.client_count == 2 and status.band == "5GHz"
    assert status.gateway_ip == "192.168.137.1"


async def test_windows_start_script_never_contains_user_input(monkeypatch):
    scripts = []

    async def fake_ps(self, body, env=None):
        scripts.append((body, env))
        return {"active": False}

    monkeypatch.setattr(windows.WindowsBackend, "_ps", fake_ps)
    await windows.WindowsBackend().create_hotspot(HotspotConfig(ssid="'; Remove-Item C:\\ #", password="longenough1"))
    start_script, env = scripts[0]
    assert "Remove-Item" not in start_script and env["NC_SSID"] == "'; Remove-Item C:\\ #"


async def test_windows_requires_a_password():
    with pytest.raises(ValueError):
        await windows.WindowsBackend().create_hotspot(HotspotConfig(ssid="Open"))
