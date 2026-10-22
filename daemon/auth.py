"""Admin password + session tokens for remote (non-loopback) UI access."""

import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)


class AdminAuth:
    SESSION_TTL = 12 * 3600
    MAX_FAILS = 5
    WINDOW = 300

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.monotonic):
        self._path = path
        self._clock = clock
        self._record: dict[str, Any] | None = None
        self._loaded = False
        self._sessions: dict[str, float] = {}
        self._fails: dict[str, list[float]] = {}

    def _file(self) -> Path:
        if self._path is None:
            from daemon.watchdog import get_state_dir

            self._path = get_state_dir() / "admin.json"
        return self._path

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            self._record = json.loads(self._file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._record = None

    def is_set(self) -> bool:
        self._load()
        return self._record is not None

    def set_password(self, password: str) -> None:
        if not isinstance(password, str) or not (8 <= len(password) <= 128):
            raise ValueError("Admin password must be 8 to 128 characters")
        salt = os.urandom(16)
        record = {"salt": salt.hex(), "hash": _derive(password, salt).hex()}
        path = self._file()
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        tmp.replace(path)
        self._record = record
        self._loaded = True
        self._sessions.clear()

    def _check(self, password: str) -> bool:
        if self._record is None:
            return False
        salt = bytes.fromhex(self._record["salt"])
        return hmac.compare_digest(_derive(password, salt), bytes.fromhex(self._record["hash"]))

    def login(self, ip: str, password: str) -> str | None:
        """Return a session token, None on a wrong password; PermissionError when locked out."""
        self._load()
        now = self._clock()
        if len(self._fails) > 1000:
            self._fails.clear()
        fails = [t for t in self._fails.get(ip, []) if now - t < self.WINDOW]
        if len(fails) >= self.MAX_FAILS:
            self._fails[ip] = fails
            raise PermissionError("Too many failed attempts; try again in a few minutes")
        if not self._check(password):
            fails.append(now)
            self._fails[ip] = fails
            return None
        self._fails.pop(ip, None)
        token = secrets.token_urlsafe(32)
        self._sessions[token] = now + self.SESSION_TTL
        return token

    def valid_session(self, token: str) -> bool:
        expiry = self._sessions.get(token)
        if expiry is None:
            return False
        if expiry < self._clock():
            del self._sessions[token]
            return False
        return True
