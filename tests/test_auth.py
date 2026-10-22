import pytest

from daemon.auth import AdminAuth


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, clock=None):
    return AdminAuth(tmp_path / "admin.json", clock=clock or Clock())


def test_unset_by_default_and_login_fails(tmp_path):
    a = make(tmp_path)
    assert not a.is_set()
    assert a.login("1.1.1.1", "whatever") is None


def test_set_login_and_persist(tmp_path):
    a = make(tmp_path)
    a.set_password("correct horse")
    token = a.login("1.1.1.1", "correct horse")
    assert token and a.valid_session(token)
    assert a.login("1.1.1.1", "wrong") is None
    assert make(tmp_path).login("2.2.2.2", "correct horse")  # survives restart
    assert oct((tmp_path / "admin.json").stat().st_mode & 0o777) == "0o600"


def test_short_password_rejected(tmp_path):
    with pytest.raises(ValueError):
        make(tmp_path).set_password("short")


def test_lockout_and_recovery(tmp_path):
    clock = Clock()
    a = make(tmp_path, clock)
    a.set_password("correct horse")
    for _ in range(5):
        assert a.login("9.9.9.9", "bad") is None
    with pytest.raises(PermissionError):
        a.login("9.9.9.9", "correct horse")  # locked even with the right password
    assert a.login("8.8.8.8", "correct horse")  # other IPs unaffected
    clock.t += AdminAuth.WINDOW + 1
    assert a.login("9.9.9.9", "correct horse")


def test_session_expiry_and_password_change_revokes(tmp_path):
    clock = Clock()
    a = make(tmp_path, clock)
    a.set_password("correct horse")
    token = a.login("1.1.1.1", "correct horse")
    clock.t += AdminAuth.SESSION_TTL + 1
    assert not a.valid_session(token)
    token = a.login("1.1.1.1", "correct horse")
    a.set_password("another password")
    assert not a.valid_session(token)
