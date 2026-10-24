import pytest

from daemon.fields import coerce_field

SELECT = {"key": "p", "label": "Provider", "type": "select", "options": [{"value": "a"}, {"value": "b"}]}


def test_toggle_and_number():
    assert coerce_field({"key": "t", "type": "toggle"}, 1) is True
    assert coerce_field({"key": "n", "type": "number", "min": 1, "max": 5}, "3") == 3
    with pytest.raises(ValueError):
        coerce_field({"key": "n", "type": "number", "min": 1}, 0)
    with pytest.raises(ValueError):
        coerce_field({"key": "n", "type": "number"}, "abc")


def test_select_only_allows_listed_options():
    assert coerce_field(SELECT, "a") == "a"
    with pytest.raises(ValueError):
        coerce_field(SELECT, "evil")


def test_url_scheme_and_control_characters():
    f = {"key": "u", "type": "url", "label": "Address"}
    assert coerce_field(f, " https://x.example/y ") == "https://x.example/y"
    assert coerce_field(f, "") == ""
    with pytest.raises(ValueError):
        coerce_field(f, "javascript:alert(1)")
    with pytest.raises(ValueError):
        coerce_field({"key": "t", "type": "text"}, "line\nbreak")
    with pytest.raises(ValueError):
        coerce_field({"key": "t", "type": "text"}, "x" * 501)
