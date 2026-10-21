#!/bin/sh
# NetCast Dead Man's Switch — ExecStopPost cleanup script
# BSD-2-Clause License
#
# This script runs EVEN IF netcastd is SIGKILLed, OOM-killed, or crashes.
# systemd guarantees ExecStopPost execution for any service termination.
#
# Environment variables provided by systemd:
#   $SERVICE_RESULT: success, exit-code, signal, core-dump, watchdog, timeout
#   $EXIT_CODE: exited, killed, dumped
#   $EXIT_STATUS: numeric return code or signal number

echo "[NetCast Cleanup] Service result: $SERVICE_RESULT (Exit: $EXIT_CODE / $EXIT_STATUS)"

# 1. Kill any orphaned child processes
pkill -f "hostapd.*netcast" 2>/dev/null || true
pkill -f "dnsmasq.*netcast" 2>/dev/null || true

# 2. Delete ephemeral nftables tables (atomic, idempotent)
nft delete table inet netcast 2>/dev/null || true
nft delete table inet netcast_portal 2>/dev/null || true

# 3. Remove traffic shaping qdiscs
for iface in /var/lib/netcast/managed_ifaces/*; do
    [ -f "$iface" ] && tc qdisc del dev "$(basename "$iface")" root 2>/dev/null || true
done

# 4. Restore NetworkManager interface management
for iface_file in /var/lib/netcast/managed_ifaces/*; do
    if [ -f "$iface_file" ]; then
        iface="$(basename "$iface_file")"
        nmcli device set "$iface" managed yes 2>/dev/null || true
        echo "[NetCast Cleanup] Restored NM management for $iface"
    fi
done

# 5. Clean up state directory
rm -rf /var/lib/netcast/managed_ifaces/ 2>/dev/null || true
rm -f /var/lib/netcast/session.token 2>/dev/null || true
rm -f /var/lib/netcast/hostapd.conf 2>/dev/null || true
rm -f /var/lib/netcast/dnsmasq.conf 2>/dev/null || true

# 6. Disable IP forwarding again only if we were the ones who turned it on
if [ -f /var/lib/netcast/ip_forward_was_off ]; then
    sysctl -w net.ipv4.ip_forward=0 2>/dev/null || true
    rm -f /var/lib/netcast/ip_forward_was_off
fi

echo "[NetCast Cleanup] Network state restored."
