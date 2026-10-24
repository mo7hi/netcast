import tomllib

from daemon.persist import dumps_toml, load_overlay, save_overlay


def test_roundtrip_tricky_strings_and_nested_tables(tmp_path):
    data = {
        "hotspot": {"ssid": 'He said "hi" \\ ünï 🙂', "password": "a#b\nc\x7f", "channel": 6},
        "fair_share": {"enabled": True, "total_bandwidth_kbps": 0},
        "plugins": {
            "private_dns": {"enabled": True, "provider": "quad9", "force_dns": False},
            "notifications": {"enabled": False, "telegram_token": "123:abc"},
            "_devices": {"blocked": "aa:bb:cc:dd:ee:ff,11:22:33:44:55:66"},
        },
    }
    path = tmp_path / "config.toml"
    save_overlay(data, path)
    assert load_overlay(path) == data
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_none_values_are_skipped_and_empty_tables_stay_valid():
    out = tomllib.loads(dumps_toml({"a": {"x": None, "y": 1}, "plugins": {}}))
    assert out == {"a": {"y": 1}, "plugins": {}}


def test_missing_and_corrupt_overlay(tmp_path):
    assert load_overlay(tmp_path / "nope.toml") == {}
    bad = tmp_path / "bad.toml"
    bad.write_text("this is [not toml")
    assert load_overlay(bad) == {}
