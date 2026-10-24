#!/bin/sh
# NetCast: install the system packages NetCast drives (Linux).
# Usage: sudo sh scripts/install-deps.sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo: sudo sh scripts/install-deps.sh" >&2
  exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  # dnsmasq-base gives NetCast the dnsmasq program without starting a system DNS server,
  # which would fight NetCast over port 53.
  PKGS="hostapd dnsmasq-base nftables iw ppp python3-pip python3-venv nodejs npm"
  echo "Installing with apt: $PKGS"
  apt-get update
  # shellcheck disable=SC2086
  DEBIAN_FRONTEND=noninteractive apt-get install -y $PKGS
elif command -v dnf >/dev/null 2>&1; then
  PKGS="hostapd dnsmasq nftables iw ppp python3-pip nodejs npm"
  echo "Installing with dnf: $PKGS"
  # shellcheck disable=SC2086
  dnf install -y $PKGS
elif command -v pacman >/dev/null 2>&1; then
  PKGS="hostapd dnsmasq nftables iw ppp python-pip nodejs npm"
  echo "Installing with pacman: $PKGS"
  # shellcheck disable=SC2086
  pacman -S --needed --noconfirm $PKGS
elif command -v zypper >/dev/null 2>&1; then
  PKGS="hostapd dnsmasq nftables iw ppp python3-pip nodejs npm"
  echo "Installing with zypper: $PKGS"
  # shellcheck disable=SC2086
  zypper --non-interactive install $PKGS
else
  echo "Unknown package manager. Install these yourself: hostapd, dnsmasq, nftables, iw, ppp, python 3.11+, node 18+." >&2
  exit 1
fi

echo
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet dnsmasq 2>/dev/null; then
  echo "NOTE: a system dnsmasq service is running and holds port 53."
  echo "      NetCast needs that port while the hotspot is on. Stop it with:"
  echo "      sudo systemctl disable --now dnsmasq"
fi
echo "Done. Next: make web && sudo make install"
