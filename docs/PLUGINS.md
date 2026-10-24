# Writing NetCast plugins

A plugin is a Python class whose methods are marked with `@hookimpl`. NetCast calls them at the right moments. Plugins can add a settings card to the **Plugins** page, change how the hotspot is set up, react to devices joining, add firewall rules, add pages and looks, and even add a new kind of internet source.

A faulty plugin cannot take NetCast down: every call is wrapped, and errors are logged.

## A complete example

```python
# netcast_hello.py
import logging
from daemon.hooks import hookimpl

log = logging.getLogger("netcast.hello")


class HelloPlugin:
    def __init__(self):
        self.cfg = {}

    @hookimpl
    def register_plugin(self, plugins):
        plugins.append({
            "id": "hello",
            "title": "Hello",
            "description": "Writes a greeting to the log when a device joins.",
            "needs_restart": False,
            "fields": [
                {"key": "greeting", "label": "Greeting", "type": "text", "default": "Welcome"},
            ],
        })

    @hookimpl
    def configure_plugin(self, plugin_id, values):
        if plugin_id == "hello":           # every plugin hears every plugin's changes
            self.cfg = values              # {"enabled": bool, "greeting": "..."}

    @hookimpl
    def on_client_connect(self, client):
        if self.cfg.get("enabled"):
            log.info("%s, %s!", self.cfg.get("greeting", "Hi"), client.hostname or client.ip)
```

Package it with an entry point:

```toml
# pyproject.toml
[project]
name = "netcast-plugin-hello"
version = "0.1.0"
dependencies = []

[project.entry-points."netcast"]
hello = "netcast_hello:HelloPlugin"
```

```bash
pip install .
sudo systemctl restart netcastd
```

The card appears on the Plugins page, off by default.

## Settings cards

`register_plugin` appends one dictionary:

| Key | Meaning |
|-----|---------|
| `id` | Lowercase letters, digits, `-` and `_`. Also the name of the saved settings. |
| `title`, `description` | Shown on the card. Write them for a non-technical reader. |
| `fields` | The form (below). |
| `needs_restart` | `True` if changes apply the next time the hotspot starts. The card says so. |
| `links` | A list, or a function returning one, of `{"label", "url", "help"}` shown as "Links to share". |
| `portal_links` | Same shape. Shown on the guest welcome page. |

Field types: `toggle`, `select` (with `options=[{"value", "label"}]`), `text`, `url`, `password`, `number` (with optional `min` and `max`). Each field has `key`, `label`, `default` and optional `help`. A field can appear only in some cases:

```python
{"key": "custom_url", "type": "url", "label": "Address",
 "show_if": {"key": "provider", "value": "custom"}}      # only when provider == "custom"
{"key": "url", "type": "url", "label": "Address",
 "show_if": {"key": "channel", "not": "telegram"}}       # for everything except telegram
```

NetCast checks every saved value against the field, so a `select` can only hold one of its options and a `url` must start with `https://` or `http://`. Values of `password` fields are never sent back to the browser; leaving one blank keeps the saved secret.

## Hooks

| Hook | When | Arguments |
|------|------|-----------|
| `register_plugin` | Once at start | `plugins` (list to append to) |
| `configure_plugin` | At start and on every save | `plugin_id`, `values` |
| `extend_dnsmasq` | Before the hotspot starts | `lines` (append dnsmasq config; may return a coroutine that NetCast awaits first) |
| `register_firewall_rules` | After NAT is set up | `firewall`, `config` |
| `on_hotspot_start` | After it is running | `config`, `status` (dict) |
| `on_hotspot_stop` | When it stops | `reason` |
| `on_client_connect` | A device joins | `client` (`.ip`, `.mac`, `.hostname`) |
| `on_client_disconnect` | A device leaves | `client` |
| `register_api_routes` | Once, when the web server is built | `app` (aiohttp) |
| `register_source` | Once at start | `registry` (dict) |
| `register_ui_tab` | Once at start | `tabs` (list) |
| `register_skin` | Once at start | `skin_registry` (dict) |
| `modify_captive_portal` | Before the welcome page is enabled | `portal_config` |

### Change DNS (what the Private DNS and ad-blocking plugins do)

```python
@hookimpl
def extend_dnsmasq(self, lines):
    if self.cfg.get("enabled"):
        lines += ["server=127.0.0.1#5354", "no-resolv"]      # your own resolver
        lines.append("addn-hosts=/var/lib/netcast/my.hosts")  # extra blocked names
```

If you need to start something first, return a coroutine and append inside it, after it succeeds:

```python
@hookimpl
def extend_dnsmasq(self, lines):
    return self._prepare(lines)

async def _prepare(self, lines):
    await self.start_my_resolver()
    lines.append("server=127.0.0.1#5354")
```

### Add firewall rules

On Linux with nftables, queue rules for a dedicated chain. NetCast applies them together with its own rules and discards them if nft rejects them:

```python
@hookimpl
def register_firewall_rules(self, firewall, config):
    if hasattr(firewall, "add_plugin_rule"):
        firewall.add_plugin_rule('ip daddr 10.0.0.0/8 drop')   # keep guests off your LAN
    if hasattr(firewall, "set_force_dns"):
        firewall.set_force_dns(True)                           # send all DNS to the local resolver
```

### Add a page

Return HTML from `register_ui_tab`; NetCast shows it on the Plugins page inside a sandboxed frame that can run scripts but has no network access and cannot call the NetCast API:

```python
@hookimpl
def register_ui_tab(self, tabs):
    tabs.append({"id": "my-stats", "title": "My stats", "html": "<h1>Hello</h1>"})
```

For public pages that devices on the hotspot should reach (like the Private DNS guest page), add routes under `/portal/` in `register_api_routes`. Everything else under `/api/` requires sign-in.

### Add a look

```python
@hookimpl
def register_skin(self, skin_registry):
    skin_registry["ocean"] = {"name": "Ocean", "css": 'html[data-theme="custom-ocean"] { --accent: #2a9d8f; --on-accent: #0b1d1a; }'}
```

Skin ids get a `custom-` prefix. A skin overrides the same tokens as the built-in looks (see `web/src/styles/tokens.css`). Check contrast for text colours: aim for at least 4.5 to 1.

### Add an internet source

```python
class MySource:
    cleanup_command = ["my-tool", "disconnect"]      # run if NetCast crashes
    async def start(self, config, cfg):
        ...                                           # bring the link up
        return "mylink0"                              # the interface to share
    async def stop(self):
        ...

@hookimpl
def register_source(self, registry):
    registry["mysource"] = MySource()
```

The PPPoE plugin (`daemon/builtin_plugins/pppoe.py`) is a full example.

## Conventions

- **Off until enabled.** Check `self.cfg.get("enabled")` in every hook.
- **Never log secrets or full URLs** that may contain tokens.
- **Be quick.** Hooks run on NetCast's event loop. Use `asyncio.create_task` for slow work.
- **Fail soft.** If your plugin cannot do its job, log a warning and let the hotspot start.

## Testing

Test the logic with plain pytest. The built-in plugins show how: `tests/test_plugins.py` runs the DNS forwarder against a local HTTPS-style server and the notifier against a local web server.
