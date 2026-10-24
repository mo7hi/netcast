from daemon.plugins import NetCastPluginManager


def test_builtin_plugins_load_and_match_hook_specs():
    pm = NetCastPluginManager()
    assert pm.load_builtin_plugins() >= 8  # fair_share, captive_portal, qr_connect, status_monitor, pppoe, private_dns, adblock, notifications
    pm.check_pending()  # raises if any hook name or argument does not match its spec


def test_dns_plugins_register_settings_cards():
    pm = NetCastPluginManager()
    pm.load_builtin_plugins()
    cards: list = []
    pm.hook.register_plugin(plugins=cards)
    assert {c["id"] for c in cards} >= {"private_dns", "adblock", "notifications"}
