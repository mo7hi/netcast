import tomllib

from daemon.persist import dumps_toml, load_overlay, save_overlay


def test_roundtrip_tricky_strings(tmp_path):
    data = {
        "hotspot": {"ssid": 'He said "hi" \\ ünï 🙂', "password": "a#b\nc\x7f", "channel": 6},
        "fair_share": {"enabled": True, "total_bandwidth_kbps": 0},
        "captive_portal": {"enabled": False, "message": "Welcome\tfriend"},
    }
    path = tmp_path / "config.toml"
    save_overlay(data, path)
    assert load_overlay(path) == data
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_none_values_skipped():
    assert tomllib.loads(dumps_toml({"a": {"x": None, "y": 1}})) == {"a": {"y": 1}}


def test_missing_and_corrupt_overlay(tmp_path):
    assert load_overlay(tmp_path / "nope.toml") == {}
    bad = tmp_path / "bad.toml"
    bad.write_text("this is [not toml")
    assert load_overlay(bad) == {}
