# Testing NetCast on real hardware

The automated tests (`make test`) cover the plugins, saved settings, sign-in, and helper code. They cannot start a hotspot. This list does. Do the steps in order, and note which pass.

You need: a Linux computer with a Wi-Fi adapter that supports access-point mode, a phone, and a second device if possible.

## 0. Before you start

```bash
make test                                   # automated tests pass
iw list | grep -A8 "Supported interface modes"   # shows "* AP"
sudo systemctl status netcastd              # active (running)
netcast preflight                           # no required problems
```

## 1. Hotspot from Ethernet *(the core path)*

1. Computer on Ethernet, Wi-Fi adapter free. Open the interface, **Automatic**, **Start hotspot**.
2. Phone: scan the QR code. **Expect:** it joins and loads a website.
3. `sudo nft list ruleset | grep -E "masquerade|blocked_macs"` shows the Ethernet interface.
4. **Stop hotspot.** **Expect:** `sudo nft list tables` no longer lists `inet netcast`, and NetworkManager manages the adapter again (`nmcli device`).

## 2. Crash recovery

1. Start a hotspot. 2. `sudo kill -9 $(pgrep -f "python.*daemon")` (or the `netcastd` process).
3. **Expect** within seconds: `sudo nft list tables` has no `inet netcast`, no `hostapd` process (`pgrep hostapd`), and `ip link` shows no `netcast0`.

## 3. Remote control

1. On the computer: Settings → Remote control → set an admin password.
2. Start the hotspot. On the phone, open `http://netcast.lan/` (or the numeric address shown).
3. **Expect:** sign-in page, then the full interface. Press **Stop hotspot** from the phone; the phone loses Wi-Fi; the computer shows it stopped.
4. Enter a wrong password six times. **Expect:** a "try again in a few minutes" message.

## 4. Devices

1. With two devices joined, open Devices on the phone.
2. **Disconnect** one. **Expect:** it drops and can rejoin.
3. **Block** it. **Expect:** it can join but cannot load pages. **Unblock**: works again. Restart `netcastd` while blocked: it stays blocked.

## 5. Plugins

- **Private DNS:** turn on, restart the hotspot. On a joined phone, visit a DNS leak test (such as dnsleaktest.com). **Expect:** your chosen provider, not your internet provider. Open the guest link; on an iPhone, the profile downloads.
- **Ad blocking:** turn on, restart. **Expect:** `dig +short doubleclick.net @192.168.12.1` returns `0.0.0.0`.
- **Notifications:** use ntfy with a fresh topic. Join a device. **Expect:** a message within a few seconds.

## 6. Other sources

| Source | Test |
|--------|------|
| **Wi-Fi, one adapter** | Computer on Wi-Fi only. Choose *Wi-Fi network*. **Expect:** works if the card supports AP+client, otherwise a clear message naming the adapter. `iw dev` shows `netcast0` while running and not after. |
| **Phone over USB** | Plug in, turn on USB tethering. **Expect:** *Found: usb0* (or similar) and a working hotspot. |
| **VPN tunnel** | Bring up WireGuard. **Expect:** *Found: wg0*; a joined phone shows the VPN's exit address. |
| **PPPoE** | Modem in an Ethernet port, credentials entered. **Expect:** `ip addr show netcastppp0` has an address; internet works. If not: `journalctl -u netcast-pppoe`. |

## 7. Welcome page and equal share

- Settings → turn on **welcome page**, restart the hotspot. A newly joined phone should be taken to the page; **Get online** unlocks the internet.
- Turn on **equal share**. Download on two devices at once: speeds should be close to equal (`tc -s class show dev <ap-interface>`).

## 8. Windows *(experimental)*

Windows 10/11 with Wi-Fi, run as a normal desktop user: `python -m daemon`, set `NETCAST_WEB_DIR` to `web\dist`. Open the interface, press **Start hotspot**. **Expect:** Windows Mobile Hotspot turns on with your name and password; devices appear. If the start fails, the message is Windows' own reason.

## 9. macOS *(experimental)*

`python -m daemon`, set `NETCAST_WEB_DIR`. Press **Open Internet Sharing**, follow the steps. **Expect:** once sharing is on, NetCast shows the hotspot as running and lists devices.

## Reporting a problem

Send: your distribution and kernel (`uname -r`), the Wi-Fi adapter (`lspci | grep -i net` or `lsusb`), the driver (`ethtool -i wlan0`), the output of `iw list`, the full error from the interface, and `journalctl -u netcastd --since "10 min ago"`. Remove passwords first.
