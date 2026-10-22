import pytest

from daemon.virtual_iface import derive_mac


def test_derive_mac_differs_and_is_local_unicast():
    new = derive_mac("a0:b1:c2:d3:e4:ff")
    assert new != "a0:b1:c2:d3:e4:ff"
    first = int(new.split(":")[0], 16)
    assert first & 0x02 and not first & 0x01
    assert new.endswith(":00")  # last byte wraps


def test_bad_mac():
    with pytest.raises(ValueError):
        derive_mac("not-a-mac")
