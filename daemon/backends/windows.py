"""Windows backend: drives the built-in Mobile Hotspot through the WinRT tethering API.

EXPERIMENTAL: written from the public WinRT documentation, not yet tested on a real
Windows machine. Windows shares the connection it is currently using; there is no
source picker. Needs Windows 10 1607+, a Wi-Fi adapter that supports a hosted network,
and an elevated or interactive user session.
"""

import asyncio
import json
import os
from typing import Any

from daemon.backends.base import (
    ClientInfo,
    HotspotConfig,
    HotspotStatus,
    NetworkingBackend,
)

GATEWAY = "192.168.137.1"  # fixed by Windows Internet Connection Sharing

_PRELUDE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskOp = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskAct = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and -not $_.IsGenericMethod -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
function Await($op, $type) {
  $t = $asTaskOp.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result
}
function AwaitAction($op) { $t = $asTaskAct.Invoke($null, @($op)); $t.Wait(-1) | Out-Null }
$null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime]
$null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]
$conn = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
"""

_MANAGER = r"""
if (-not $conn) { throw 'This computer has no internet connection to share.' }
$mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($conn)
"""

_START = _MANAGER + r"""
if ($mgr.TetheringOperationalState.ToString() -eq 'On') {
  $null = Await ($mgr.StopTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
}
$cfg = New-Object Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration
$cfg.Ssid = $env:NC_SSID
$cfg.Passphrase = $env:NC_PASS
try { $cfg.Band = [Enum]::Parse([Windows.Networking.NetworkOperators.TetheringWiFiBand], $env:NC_BAND) } catch { }
AwaitAction ($mgr.ConfigureAccessPointAsync($cfg))
$r = Await ($mgr.StartTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
if ($r.Status.ToString() -ne 'Success') { throw ('Windows would not start the hotspot: ' + $r.Status + ' ' + $r.AdditionalErrorMessage) }
@{ ok = $true } | ConvertTo-Json -Compress
"""

_STOP = _MANAGER + r"""
$null = Await ($mgr.StopTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
@{ ok = $true } | ConvertTo-Json -Compress
"""

_STATUS = r"""
if (-not $conn) {
  @{ active = $false; ssid = ''; password = ''; clients = 0; band = '' } | ConvertTo-Json -Compress
} else {
  $mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($conn)
  $cfg = $mgr.GetCurrentAccessPointConfiguration()
  $band = ''; try { $band = $cfg.Band.ToString() } catch { }
  @{ active = ($mgr.TetheringOperationalState.ToString() -eq 'On'); ssid = $cfg.Ssid; password = $cfg.Passphrase;
     clients = [int]$mgr.ClientCount; band = $band } | ConvertTo-Json -Compress
}
"""

_CLIENTS = _MANAGER + r"""
$list = @()
foreach ($c in $mgr.GetTetheringClients()) {
  $ip = ''; $name = ''
  foreach ($h in $c.HostNames) {
    if ($h.Type.ToString() -eq 'Ipv4') { $ip = $h.CanonicalName }
    elseif ($h.Type.ToString() -eq 'DomainName') { $name = $h.DisplayName }
  }
  $list += @{ mac = $c.MacAddress; ip = $ip; name = $name }
}
ConvertTo-Json -Compress -InputObject @($list)
"""

_CAPABILITY = r"""
if (-not $conn) { @{ capability = 'NoConnection' } | ConvertTo-Json -Compress }
else {
  $cap = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::GetTetheringCapabilityFromConnectionProfile($conn)
  @{ capability = $cap.ToString() } | ConvertTo-Json -Compress
}
"""


class WindowsBackend(NetworkingBackend):
    """Hotspot through Windows Mobile Hotspot (PowerShell + WinRT)."""

    assistant = False

    def __init__(self) -> None:
        self._config: HotspotConfig | None = None

    async def _ps(self, body: str, env: dict[str, str] | None = None) -> Any:
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", "-",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env={**os.environ, **(env or {})},
        )
        out, err = await proc.communicate((_PRELUDE + body).encode("utf-8"))
        text = out.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            msg = err.decode("utf-8", errors="replace").strip() or text or "PowerShell failed"
            raise RuntimeError(msg.splitlines()[0][:300])
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else {}

    async def create_hotspot(self, config: HotspotConfig) -> HotspotStatus:
        config.validate()
        if not config.password:
            raise ValueError("Windows requires a password of at least 8 characters")
        band = "FiveGigahertz" if config.is_5ghz else "TwoPointFourGigahertz"
        await self._ps(_START, {"NC_SSID": config.ssid, "NC_PASS": config.password, "NC_BAND": band})
        self._config = config
        return await self.get_status()

    async def stop_hotspot(self) -> None:
        await self._ps(_STOP)
        self._config = None

    async def get_status(self) -> HotspotStatus:
        data = await self._ps(_STATUS)
        if not data.get("active"):
            return HotspotStatus(active=False, ssid="", gateway_ip=GATEWAY)
        band = "5GHz" if "Five" in str(data.get("band", "")) else "2.4GHz"
        return HotspotStatus(
            active=True, ssid=data.get("ssid", ""), password=data.get("password") or None,
            band=band, source_interface="Windows connection", ap_interface="Mobile Hotspot",
            gateway_ip=GATEWAY, client_count=int(data.get("clients", 0)),
        )

    async def get_connected_clients(self) -> list[ClientInfo]:
        data = await self._ps(_CLIENTS)
        return [
            ClientInfo(ip=c.get("ip") or "", mac=str(c.get("mac", "")).lower().replace("-", ":"),
                       hostname=c.get("name") or None)
            for c in (data if isinstance(data, list) else [data])
            if c
        ]

    async def preflight(self) -> list[dict[str, Any]]:
        """Checks shown on the setup screen."""
        try:
            cap = (await self._ps(_CAPABILITY)).get("capability", "Unknown")
        except Exception as exc:
            return [{"name": "windows_hotspot", "title": "Windows Mobile Hotspot", "passed": False,
                     "required": True, "message": str(exc), "fix_hint": "Run NetCast as a normal desktop user."}]
        ok = cap == "Enabled"
        hints = {
            "NoConnection": "Connect this computer to the internet first.",
            "DisabledByGroupPolicy": "Your administrator has turned the Mobile Hotspot off.",
            "DisabledByHardwareLimitation": "This Wi-Fi adapter cannot host a hotspot.",
            "DisabledByOperator": "Your mobile operator blocks sharing.",
            "DisabledBySku": "This edition of Windows does not include Mobile Hotspot.",
            "DisabledByRequiredAppNotInstalled": "Install the app Windows asks for in Settings > Mobile hotspot.",
        }
        return [{"name": "windows_hotspot", "title": "Windows Mobile Hotspot", "passed": ok, "required": True,
                 "message": "Windows can share this connection." if ok else f"Windows reports: {cap}.",
                 "fix_hint": "" if ok else hints.get(cap, "")}]
