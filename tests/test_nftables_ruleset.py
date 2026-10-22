from daemon.firewall.nftables import NftablesBackend


def backend():
    b = NftablesBackend(nft_path="/bin/true")
    b._source_iface, b._ap_iface = "eth0", "wlan0"
    return b


def test_ruleset_replaces_atomically_and_masquerades():
    rules = backend()._build_ruleset()
    lines = rules.splitlines()
    assert lines[0] == "add table inet netcast" and lines[1] == "delete table inet netcast"
    assert 'oifname "eth0" masquerade' in rules


def test_captive_portal_blocks_unauthenticated():
    b = backend()
    b._captive_portal_active = True
    rules = b._build_ruleset()
    assert "@authenticated_clients accept" in rules
    assert 'oifname "eth0" drop' in rules


def test_plugin_rules_land_in_own_chain():
    b = backend()
    b.add_plugin_rule("ip daddr 10.0.0.0/8 drop")
    rules = b._build_ruleset()
    assert "chain plugin_rules" in rules and "ip daddr 10.0.0.0/8 drop" in rules
