import tomllib
from pathlib import Path

from daemon.config import parse_config_dict

DEFAULT = Path(__file__).resolve().parents[1] / "daemon" / "default_config.toml"


def test_shipped_default_config_is_valid():
    with open(DEFAULT, "rb") as fh:
        cfg = parse_config_dict(tomllib.load(fh))
    assert cfg.hotspot.band == "bg"
    assert cfg.network.source_type == "auto"


def test_pppoe_section_roundtrip():
    cfg = parse_config_dict({"pppoe": {"username": "u", "password": "p"}})
    assert cfg.pppoe.mtu == 1492
