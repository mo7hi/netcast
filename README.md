<div align="center">

# ⚡ NetCast

### The VLC of Software Routers

**Share any internet connection as a Wi-Fi hotspot. It plays everything, works everywhere, and just works.**

[![License](https://img.shields.io/badge/license-BSD--2--Clause-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-3776ab.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/platform-Linux-orange.svg)](#platform-support)

</div>

---

## What is NetCast?

NetCast turns your Linux machine into a Wi-Fi hotspot — regardless of where your internet comes from. Ethernet, Wi-Fi repeating, USB tethering, PPPoE modems — it handles everything.

Think of it as **VLC for networking**: it plays every input format, works on any machine, and focuses on reliability over feature bloat.

```
Ethernet cable  ──┐
Wi-Fi network   ──┤──→  NetCast  ──→  📶 Wi-Fi Hotspot
USB tethering   ──┤      daemon       for all your devices
PPPoE modem     ──┘
```

## Why NetCast?

| Feature | linux-wifi-hotspot | Windows Hotspot | Connectify | NetCast |
|---------|-------------------|-----------------|------------|---------|
| Price | Free | Free | **$40/yr** 💸 | **Free** |
| Platform | Linux only | Windows only | Windows only | **Linux** (Win/Mac planned) |
| Internet sources | Wi-Fi/Ethernet | Wi-Fi/Ethernet | Wi-Fi/Ethernet/3G/4G | **All** (+ PPPoE, USB, repeater) |
| Client limit | Unlimited | 8 | Unlimited (Pro) | **Unlimited** |
| Open source | ✓ | ✗ | ✗ | **✓** (BSD-2-Clause) |
| QR code connect | ✗ | ✗ | ✗ | **✓** (scan & go) |
| Per-client bandwidth | ✗ | ✗ | ✗ | **✓** (Fair Share) |
| Captive portal | ✗ | ✗ | ✗ | **✓** (landing page) |
| Crash recovery | ✗ | Partial | Partial | **✓** (4-layer Dead Man's Switch) |
| Plugin system | ✗ | ✗ | ✗ | **✓** (`pip install netcast-plugin-xxx`) |
| Manage from phone | ✗ | ✗ | ✗ | **✓** (web UI on connected device) |
| Theme skins | ✗ | ✗ | ✗ | **✓** (4 built-in + community) |
| Runs as root | Yes | N/A | Admin | **No** (Linux capabilities) |
| Ad-free | ✓ | ✓ | ✗ (ads in free tier) | **✓** |

## Quick Start

### Install

```bash
# Ubuntu / Debian
sudo apt install hostapd dnsmasq nftables iw
git clone https://github.com/mo7hi/netcast.git
cd netcast
make dev        # Development mode
# OR
sudo make install  # System install
```

### Run

```bash
# Start the daemon
sudo systemctl start netcastd

# Open the web UI
xdg-open http://127.0.0.1:8384

# Or use the CLI
netcast start --source eth0 --ssid "MyHotspot" --password "supersecret"
netcast status
netcast qr       # Print QR code in terminal
netcast stop
```

## Architecture

```
 ┌──────────────────────────────────────────┐
 │  Web Browser (or phone on hotspot)       │
 │  Svelte UI — small prebuilt bundle      │
 └──────────────┬───────────────────────────┘
                │ REST + WebSocket
 ┌──────────────┴───────────────────────────┐
 │  netcastd  (Python asyncio daemon)       │
 │  • Runs as 'netcast' user (not root!)    │
 │  • CAP_NET_ADMIN + CAP_NET_RAW only      │
 │  • systemd watchdog + Dead Man's Switch  │
 │  • Plugin system (pluggy)                │
 └──────────────┬───────────────────────────┘
                │
 ┌──────────────┴───────────────────────────┐
 │  Linux Networking Stack                   │
 │  hostapd · NetworkManager · nftables     │
 │  dnsmasq · tc · pppd                     │
 └──────────────────────────────────────────┘
```

## Plugins

NetCast is extensible. Install plugins with pip:

```bash
pip install netcast-plugin-telegram   # Notifications on Telegram
pip install netcast-plugin-adblock    # DNS-based ad blocking
pip install netcast-plugin-bandwidth  # Bandwidth monitoring dashboard
```

### Write Your Own Plugin

```python
# my_plugin.py
from daemon.hooks import hookimpl

class MyPlugin:
    @hookimpl
    def on_client_connect(self, client):
        print(f"📱 {client.hostname} connected!")

    @hookimpl
    def on_hotspot_start(self, config, status):
        print(f"🟢 Hotspot '{config.ssid}' is live!")
```

```toml
# pyproject.toml
[project.entry-points."netcast"]
my_plugin = "my_plugin:MyPlugin"
```

Available hooks: `register_source`, `register_ui_tab`, `on_hotspot_start`, `on_hotspot_stop`, `on_client_connect`, `on_client_disconnect`, `register_firewall_rules`, `register_api_routes`, `register_skin`, `modify_captive_portal`.

## Themes

NetCast ships with 4 built-in themes. Switch instantly in Settings.

| Theme | Vibe |
|-------|------|
| 🌙 **Midnight** (default) | Deep blue, cyan glow, premium feel |
| 🚨 **Panic Mode** | Max contrast, giant text, zero confusion |
| 💚 **Cyberpunk** | Terminal green, hacker aesthetic |
| 🖥️ **Retro 95** | Classic Windows look, beveled borders |

Community themes are just CSS files — distribute them as plugins.

## Platform Support

| Platform | Status | Notes |
|----------|--------|-------|
| 🐧 Linux | ✅ Full Support | Ubuntu, Fedora, Arch, Debian |
| 🪟 Windows | 🚧 Coming (v2) | Go daemon + WinRT API |
| 🍎 macOS | 📋 Planned (v3) | Assistant Mode |

## Security

- **No root required** — runs with Linux capabilities (`CAP_NET_ADMIN`, `CAP_NET_RAW`)
- **Session tokens** — API protected against DNS rebinding attacks
- **4-layer crash recovery** — signal handlers → ExecStopPost → watchdog → OS pipe sentinel
- **Ephemeral firewall** — all rules in one nftables table, atomically deleted on stop
- **Plugin sandboxing** — UI tabs in sandboxed iframes with CSP headers

## Dependencies

- Python 3.11+
- hostapd, dnsmasq, nftables (or iptables), iw
- NetworkManager (optional, recommended)
- Node.js 18+ (build only, not runtime)

## Development

```bash
git clone https://github.com/mo7hi/netcast.git
cd netcast
make dev

# Terminal 1: Run daemon
sudo python -m daemon

# Terminal 2: Run web UI dev server
cd web && pnpm run dev
```

## License

BSD-2-Clause — use it however you want.

## Credits

NetCast is a spiritual successor to [linux-wifi-hotspot](https://github.com/lakinduakash/linux-wifi-hotspot) by lakinduakash, which itself wraps [create_ap](https://github.com/oblique/create_ap) by oblique. We stand on the shoulders of giants.

---

<div align="center">

**NetCast** — Because sharing internet shouldn't require a PhD in networking.

</div>
