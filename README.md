<div align="center">

<img src="assets/icons/netcast.svg" alt="NetCast logo: a Wi-Fi symbol made of green arcs over a red watermelon slice" width="120">

# NetCast

### The VLC of software routers

Share any internet connection as a Wi-Fi hotspot, and control it from your phone.

[![License](https://img.shields.io/badge/license-BSD--2--Clause-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-3776ab.svg)](https://python.org)

</div>

---

## What it does

NetCast turns a computer into a Wi-Fi hotspot. It does not care where the internet comes from, the way VLC does not care what file you open.

```
Ethernet cable ──┐
Wi-Fi network  ──┤
Phone over USB ──┤
Mobile modem   ──┼──→  NetCast  ──→  📶 your hotspot  ──→  phones, laptops, TVs
VPN tunnel     ──┤        │
ISP modem      ──┘        └──→  controlled from your phone
```

It also gives you things a plain hotspot does not: a QR code to join, a list of devices you can disconnect or block, private DNS, ad blocking, and notifications when someone joins.

## Status

Read this before you rely on it.

| Part | State |
|------|-------|
| Linux hotspot (Ethernet, USB, mobile modem, VPN, Wi-Fi) | Written. **Not yet run on real hardware.** |
| Wi-Fi repeater on one adapter | Written. Needs a card that can do AP and client at once. Not yet run on hardware. |
| PPPoE (ISP modem) | Written. Needs the helper service. Not yet run on hardware. |
| Windows and macOS | **Experimental.** Written from the platform documentation, never run on those systems. |
| Web interface | Built and checked automatically: no accessibility violations in the four themes. |
| Plugins, saved settings, sign-in, device blocking logic | Covered by automated tests. |

[docs/TESTING.md](docs/TESTING.md) lists exactly how to check each hardware feature, and what to send me if one fails.

## Install (Linux)

You need Linux with a Wi-Fi adapter that supports access-point mode, and Python 3.11 or newer.

```bash
git clone https://github.com/netcast/netcast.git
cd netcast

sudo sh scripts/install-deps.sh      # hostapd, dnsmasq, nftables, iw, ppp, node
make web                             # builds the web interface
sudo make install                    # installs the service

sudo systemctl enable --now netcastd
```

Open **http://127.0.0.1:8384** in a browser on that computer.

Want the `netcast` command to work without `sudo`? Add yourself to the `netdev` group, then log out and in:

```bash
sudo usermod -aG netdev $USER
```

**Check your adapter first.** Run `iw list | grep -A8 "Supported interface modes"`. If `* AP` is not in the list, that adapter cannot host a hotspot.

## Your first hotspot

1. Open NetCast. You land on **Hotspot**.
2. Pick the internet source. **Automatic** uses whatever connection the computer is using now, and is the right choice for most people.
3. Keep the random password NetCast filled in, or press **New** for another. Change the network name if you like.
4. Press **Check this setup**. Fix anything marked *Problem*; *Warning* items are safe to ignore.
5. Press **Start hotspot**.
6. On the running screen, point a phone camera at the QR code to join.

## Internet sources

| Source | Choose it when | You need |
|--------|----------------|----------|
| **Automatic** | You are not sure | Nothing. Uses the connection with the default route. |
| **Ethernet cable** | A router or modem is plugged into this computer | An Ethernet port with a cable |
| **Wi-Fi network** | The only internet is another Wi-Fi network | A second Wi-Fi adapter, *or* one adapter that can be a client and an access point at once (NetCast tells you if yours cannot). The hotspot uses the same channel as the Wi-Fi it repeats. |
| **Phone over USB** | You have a phone with mobile data | A cable, and USB tethering turned on in the phone |
| **Mobile data modem** | A SIM modem is connected (shows up as `wwan0`) | A modem that is already connected to its network |
| **VPN tunnel** | You want every device on your hotspot to use a VPN | A WireGuard, OpenVPN or Tailscale connection that is up |
| **ISP modem (PPPoE)** | Your provider gives you a username and password | The modem in an Ethernet port. NetCast asks for the credentials and dials in. |

## Control it from your phone

NetCast has a full web interface made for phones, with a bottom tab bar and large buttons.

1. **On the computer**, open **Settings → Remote control** and set an **admin password**. This step is required, because a phone is a different device and must sign in.
2. Start the hotspot.
3. Join the hotspot with your phone.
4. Open the address shown under Remote control. It is `http://netcast.lan/` or `http://192.168.12.1/` on the default setup. The page also shows a QR code for it.
5. Sign in with the admin password.

Now you can start and stop the hotspot, see and block devices, and change plugins from the couch. Use your browser's **Add to Home Screen** to keep it one tap away.

Good to know:

- **It is plain HTTP.** Only use a password you have not used anywhere else. A hotspot protected by WPA2 keeps it off the open air.
- **Five wrong passwords lock that device out for five minutes.** Signing in lasts 12 hours.
- **`netcast.lan` needs the hotspot's own DNS.** If your phone has its own Private DNS set (Android), use the numeric address instead.
- **From your home network too:** set `listen_host = "0.0.0.0"` in `/etc/netcast/config.toml`, restart, and the Remote control page lists the extra addresses. Anyone on that network can then reach the sign-in page.

## Devices

The **Devices** tab lists everything connected, with its address and how much it has uploaded and downloaded.

- **Disconnect** kicks a device off. It can rejoin right away.
- **Block** cuts its internet and disconnects it. It stays blocked, even after restarts, until you press **Unblock** under *Blocked*.

Blocking works on Linux. It stops the device's internet, but a blocked phone can still see the Wi-Fi network.

## Plugins

Open **Plugins**. Each plugin is off until you turn it on, then shows its own settings.

### Private DNS

Encrypts every website lookup, so your provider or the café network cannot see which sites you visit.

1. Turn it on and choose who answers: **Cloudflare**, **Quad9** (blocks malware), **AdGuard** (blocks ads), **Mullvad**, **NextDNS** (your own settings) or your own address.
2. Leave **Make every device use it** on. This catches devices that ignore the hotspot's DNS.
3. Stop and start the hotspot.

**The link for guests.** With the plugin on, the plugin card shows a link such as `http://192.168.12.1/portal/private-dns`. Share it. It opens a page where an iPhone downloads a profile with one tap, and an Android or Windows user gets the exact steps. That keeps the private DNS working when they leave your hotspot. The same link appears on the welcome page if you use it.

If the provider cannot be reached, lookups fail rather than silently falling back to unencrypted DNS.

### Ad and tracker blocking

Blocks known ad, tracker and malware sites for every device, with nothing installed on the devices. NetCast downloads a public list, keeps only plain block entries, and refreshes it weekly. Choose the standard list, a stricter one, or your own. Restart the hotspot after changing it. Works with Private DNS.

### Notifications

Sends a message when a device joins (and optionally when one leaves or the hotspot starts and stops). Channels: **ntfy**, **Telegram**, **Discord**, **Slack**, or any web address that accepts JSON. For ntfy, pick a hard-to-guess topic name and put `https://ntfy.sh/your-topic` as the address, then subscribe to it in the ntfy phone app.

### Built into Settings

- **Equal share of the speed.** Gives each device the same slice of your connection (Linux only).
- **Welcome page.** Shows a page with a **Get online** button when someone joins. Authorized devices stay online for two hours (Linux only).

### Install more, or write your own

```bash
pip install netcast-plugin-name
sudo systemctl restart netcastd
```

A plugin is a small Python class. [docs/PLUGINS.md](docs/PLUGINS.md) has a working example in about twenty lines and the full list of hooks.

## Command line

```bash
netcast start --source eth0 --ssid "MyHotspot" --password "a-long-password"
netcast status        # state, address and connected devices
netcast qr            # QR code in the terminal
netcast preflight     # run the setup checks
netcast interfaces    # list network adapters
netcast stop
```

`--source` is optional only in the web interface; the command line needs it. Add `--ap wlan1` to choose which Wi-Fi adapter hosts the hotspot.

## Looks and accessibility

**Settings → Appearance** has four looks, each there for a reason:

| Look | For |
|------|-----|
| **Match my device** | Follows your system: light, dark, or high contrast if your system asks for it. This is the default. |
| **Light** | Bright rooms and sunlight |
| **Dark** | Night |
| **High contrast** | Low vision: black and white, heavier lines, larger text |

The interface uses a font designed for legibility (Atkinson Hyperlegible), works with a keyboard and screen readers, keeps visible focus outlines, and respects reduced-motion settings. The choice is saved on that device.

Plugins can add their own looks. They appear in the same list.

## Platform support

| System | How it works | Source choice | Equal share, welcome page, blocking |
|--------|--------------|---------------|-------------------------------------|
| **Linux** | Full: hostapd, dnsmasq and nftables | All seven sources | Yes |
| **Windows 10/11** *(experimental)* | Drives Windows Mobile Hotspot | Shares the connection Windows is using | No |
| **macOS** *(experimental)* | Assistant mode: macOS does not let apps switch on Internet Sharing, so NetCast opens the right page, shows the steps and the name and password to enter, then watches until it is on and lists your devices | Whatever you choose in System Settings | No |

The web interface and remote control work from any phone or computer with a browser, on any system. On Windows and macOS, run the daemon with `python -m daemon` and set `NETCAST_WEB_DIR` to the built `web/dist` folder.

## Configuration

Settings you change in the interface are saved for you (in `/var/lib/netcast/config.toml`) and survive restarts. You can also edit `/etc/netcast/config.toml`, which the saved settings override:

```toml
[hotspot]
ssid = "NetCast"
password = "choose-your-own"
band = "bg"            # "bg" = 2.4 GHz, "a" = 5 GHz
channel = 0            # 0 = automatic

[daemon]
listen_host = "127.0.0.1"   # "0.0.0.0" lets your home network reach the interface
listen_port = 8384

[fair_share]
enabled = false
total_bandwidth_kbps = 0

[captive_portal]
enabled = true
message = "Welcome. Tap Get online."
```

Environment variables (set them in a systemd override): `NETCAST_CONFIG` (config path), `NETCAST_BACKEND` (`hostapd` or `nm` to force the engine), `NETCAST_WEB_DIR`, `NETCAST_STATE_DIR`, `NETCAST_DBUS_BUS=session` (for running as a normal user while developing).

## Troubleshooting

**"No Wi-Fi adapter with AP mode found."** NetCast lists the adapters it found and what each can do. If you only have one Wi-Fi card and it is also your internet source, it must support being a client and an access point at once. Check with `iw list | grep -A6 "valid interface combinations"`: you want one line with both `managed` and `AP`. If not, use Ethernet or USB for the internet, or add a second USB Wi-Fi adapter.

**The hotspot starts but devices have no internet.** Run **Check this setup**. Then confirm `nft list ruleset | grep masquerade` shows your source interface.

**"hostapd failed to start".** The message includes hostapd's own reason. The usual causes are an adapter that does not support the chosen band, or something else (NetworkManager, another hotspot) using the adapter.

**Port 53 is already in use.** A system `dnsmasq` or `systemd-resolved` stub is holding it. Run `sudo systemctl disable --now dnsmasq`. NetCast already works with systemd-resolved.

**5 GHz does not work.** Some adapters and regions block 5 GHz hotspots. Use 2.4 GHz, or channels 36 to 48.

**The phone cannot sign in to remote control.** Set the admin password on the computer first (Settings → Remote control). Remote control is off without one.

**Logs.** `journalctl -u netcastd -f`. PPPoE: `journalctl -u netcast-pppoe`.

## Safety and privacy

- NetCast runs as its own `netcast` user with only the network capabilities it needs. The one exception is PPPoE, which needs a tiny root helper that checks every field before it runs `pppd`.
- Everything NetCast changes (firewall rules, interfaces) lives in one place and is removed when the hotspot stops. If NetCast crashes, a recovery script cleans up.
- The interface is only reachable from the computer itself and from devices on the hotspot. Remote devices must sign in.
- Settings that contain secrets (hotspot password, notification tokens, PPPoE login) are stored in files only the `netcast` user can read.
- Plugin pages run in a sandbox with no network and no access to NetCast.
- NetCast does not send data anywhere. The plugins you turn on contact only the service you chose (a DNS provider, a blocklist host, a notification service).

## Uninstall

```bash
sudo make uninstall
```

## Development

```bash
pip install -e ".[dev]"
cd web && npm install && npm run dev      # interface on http://localhost:5173
sudo NETCAST_DBUS_BUS=session python -m daemon
make test
```

The interface lives in `web/src`. All colours come from `web/src/styles/tokens.css`; change them there.

## License

BSD-2-Clause. Use it however you want.

## Credits

NetCast is a spiritual successor to [linux-wifi-hotspot](https://github.com/lakinduakash/linux-wifi-hotspot) by lakinduakash, which wraps [create_ap](https://github.com/oblique/create_ap) by oblique. The interface font is [Atkinson Hyperlegible](https://brailleinstitute.org/freefont) from the Braille Institute.
