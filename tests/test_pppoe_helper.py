import pytest

from daemon.pppoe_helper import build_options, quote, validate


def good(**kw):
    d = {"nic": "eth0", "username": "user@isp", "password": 'p"ass\\word#1', "mtu": 1492}
    d.update(kw)
    return d


def test_quote_escapes():
    assert quote('a"b\\c') == '"a\\"b\\\\c"'


def test_options_cannot_be_injected():
    req = validate(good(username="bob"))
    opts = build_options(req, "rp-pppoe.so")
    assert "nic-eth0" in opts and "plugin rp-pppoe.so" in opts
    assert 'password "p\\"ass\\\\word#1"' in opts


@pytest.mark.parametrize(
    "bad",
    [
        good(username="a\nplugin /tmp/evil.so"),
        good(password="x\ry"),
        good(nic="eth0 evil"),
        good(nic="../etc"),
        good(mtu=9000),
        good(mtu="1492"),
        good(username=""),
        good(password="p" * 300),
    ],
)
def test_validation_rejects(bad):
    with pytest.raises(ValueError):
        validate(bad)
