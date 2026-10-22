import pytest
from aiohttp.test_utils import TestClient, TestServer

from daemon.api.web import create_app
from daemon.auth import AdminAuth
from daemon.backends.base import HotspotStatus


class Stub:
    config = {}
    session_token = None

    async def get_status(self):
        return HotspotStatus(active=False, ssid="")


@pytest.fixture
async def client(tmp_path):
    app = create_app(daemon_state=Stub(), session_token="master-token", config=object())
    app["admin_auth"] = AdminAuth(tmp_path / "admin.json")
    app["admin_auth"].set_password("correct horse")
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_api_requires_token(client):
    assert (await client.get("/api/status")).status == 401
    r = await client.get("/api/status", headers={"X-NetCast-Token": "master-token"})
    assert r.status == 200


async def test_rebinding_host_rejected(client):
    r = await client.get("/api/status", headers={"Host": "evil.example.com",
                                                 "X-NetCast-Token": "master-token"})
    assert r.status == 403


async def test_login_gives_working_session(client):
    r = await client.post("/api/login", json={"password": "wrong"})
    assert r.status == 401
    r = await client.post("/api/login", json={"password": "correct horse"})
    token = (await r.json())["token"]
    r = await client.get("/api/status", headers={"X-NetCast-Token": token})
    assert r.status == 200


async def test_loopback_gets_master_token(client):
    r = await client.get("/api/token")
    assert r.status == 200 and (await r.json())["token"] == "master-token"
