# NetCast — Makefile
# BSD-2-Clause License
#
# Usage:
#   make web           — Build the Svelte web UI
#   make dev           — Install in development mode with dev dependencies
#   sudo make install  — Install daemon, CLI, web UI, systemd unit, desktop entry
#   sudo make uninstall — Remove everything
#   make clean         — Remove build artifacts
#   make test          — Run tests

PREFIX ?= /usr
DESTDIR ?=
SYSCONFDIR ?= /etc
LIBDIR ?= $(PREFIX)/lib
BINDIR ?= $(PREFIX)/bin
STATEDIR ?= /var/lib

.PHONY: all install uninstall dev web clean test setup-user check-root check-web

all: web

# --- Web UI Build (runs as normal user) ---
web:
	@echo "Building NetCast web UI..."
	cd web && (command -v pnpm >/dev/null && pnpm install && pnpm run build || (npm install && npm run build))
	@echo "Web UI built successfully."

# --- Development Setup (runs as normal user) ---
dev:
	pip install -e ".[dev]"
	cd web && pnpm install
	@echo ""
	@echo "Development environment ready."
	@echo "  Daemon:  sudo python -m daemon"
	@echo "  Web UI:  cd web && pnpm run dev"
	@echo ""

# --- Pre-flight checks ---
check-root:
	@if [ "$$(id -u)" -ne 0 ]; then \
		echo "Error: 'make install' must be run as root (use sudo)"; \
		exit 1; \
	fi

check-web:
	@if [ ! -d "web/dist" ]; then \
		echo "Error: Web UI not built. Run 'make web' first (as normal user), then 'sudo make install'."; \
		exit 1; \
	fi

# --- Installation (must run as root) ---
install: check-root check-web setup-user
	@echo "Installing NetCast..."

	# Python packages (install system-wide)
	pip install --break-system-packages . 2>/dev/null || pip install .
	# pip may install entry points to /usr/local/bin; the unit expects $(BINDIR)
	for b in netcastd netcast; do p=$$(command -v $$b); [ -n "$$p" ] && [ "$$p" != "$(BINDIR)/$$b" ] && ln -sf "$$p" "$(DESTDIR)$(BINDIR)/$$b" || true; done

	# Web UI static files
	install -d $(DESTDIR)$(LIBDIR)/netcast/web
	cp -r web/dist/. $(DESTDIR)$(LIBDIR)/netcast/web/

	# Cleanup script
	install -Dm755 scripts/netcast-cleanup.sh $(DESTDIR)$(BINDIR)/netcast-cleanup

	# systemd service
	install -Dm644 systemd/netcastd.service $(DESTDIR)$(SYSCONFDIR)/systemd/system/netcastd.service
	install -Dm644 systemd/netcast-pppoe.service $(DESTDIR)$(SYSCONFDIR)/systemd/system/netcast-pppoe.service

	# D-Bus policy
	install -Dm644 dbus/net.netcast.Daemon.conf $(DESTDIR)$(SYSCONFDIR)/dbus-1/system.d/net.netcast.Daemon.conf

	# PolicyKit rules
	install -Dm644 polkit/net.netcast.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/net.netcast.policy
	install -Dm644 polkit/50-netcast-nm.rules $(DESTDIR)$(SYSCONFDIR)/polkit-1/rules.d/50-netcast-nm.rules
	install -Dm644 polkit/51-netcast-pppoe.rules $(DESTDIR)$(SYSCONFDIR)/polkit-1/rules.d/51-netcast-pppoe.rules

	# Desktop entry
	install -Dm644 packaging/netcast.desktop $(DESTDIR)$(PREFIX)/share/applications/netcast.desktop

	# Icon
	install -Dm644 assets/icons/netcast.svg $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/netcast.svg

	# Default config (don't overwrite existing)
	install -d $(DESTDIR)$(SYSCONFDIR)/netcast
	test -f $(DESTDIR)$(SYSCONFDIR)/netcast/config.toml || \
		install -Dm644 daemon/default_config.toml $(DESTDIR)$(SYSCONFDIR)/netcast/config.toml

	# State directory
	install -d -o netcast -g netcast $(DESTDIR)$(STATEDIR)/netcast

	# Reload systemd and update icon cache
	systemctl daemon-reload 2>/dev/null || true
	gtk-update-icon-cache $(DESTDIR)$(PREFIX)/share/icons/hicolor 2>/dev/null || true

	@echo ""
	@echo "═══════════════════════════════════════════════════"
	@echo "  ⚡ NetCast installed successfully!"
	@echo ""
	@echo "  Enable:  sudo systemctl enable --now netcastd"
	@echo "  Open:    xdg-open http://127.0.0.1:8384"
	@echo "═══════════════════════════════════════════════════"
	@echo ""

# --- Create system user (idempotent) ---
setup-user:
	@id -u netcast >/dev/null 2>&1 || \
		(useradd --system --no-create-home --shell /usr/sbin/nologin \
		 --comment "NetCast daemon" netcast && \
		 echo "Created system user 'netcast'")

# --- Uninstallation (must run as root) ---
uninstall: check-root
	@echo "Uninstalling NetCast..."
	-systemctl stop netcastd 2>/dev/null
	-systemctl disable netcastd 2>/dev/null
	pip uninstall -y netcast 2>/dev/null || true
	rm -rf $(DESTDIR)$(LIBDIR)/netcast
	rm -f $(DESTDIR)$(BINDIR)/netcast-cleanup
	rm -f $(DESTDIR)$(SYSCONFDIR)/systemd/system/netcastd.service
	rm -f $(DESTDIR)$(SYSCONFDIR)/systemd/system/netcast-pppoe.service
	rm -f $(DESTDIR)$(SYSCONFDIR)/dbus-1/system.d/net.netcast.Daemon.conf
	rm -f $(DESTDIR)$(PREFIX)/share/polkit-1/actions/net.netcast.policy
	rm -f $(DESTDIR)$(SYSCONFDIR)/polkit-1/rules.d/50-netcast-nm.rules
	rm -f $(DESTDIR)$(SYSCONFDIR)/polkit-1/rules.d/51-netcast-pppoe.rules
	rm -f $(DESTDIR)$(PREFIX)/share/applications/netcast.desktop
	rm -f $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/netcast.svg
	-systemctl daemon-reload
	@echo "NetCast uninstalled."

# --- Testing ---
test:
	pytest tests/ -v
	ruff check daemon/ cli/

# --- Clean ---
clean:
	rm -rf web/dist web/node_modules
	rm -rf __pycache__ daemon/__pycache__ cli/__pycache__
	rm -rf *.egg-info dist build .mypy_cache .ruff_cache
