import httpx
import pytest
from fastapi.testclient import TestClient

from app.lock import LockState, account_expired, is_terabox
from app.main import app
from app.auth import Auth
from tests.test_auth_api import _jd_web, _webauth_handler


def test_is_terabox_and_expired():
    assert is_terabox("terabox.com") and is_terabox("www.1024terabox.com") and not is_terabox("mega.nz")
    assert account_expired({"hostname": "terabox.com", "enabled": True, "valid": False})
    assert account_expired({"hostname": "terabox.com", "enabled": True, "valid": True, "error": "cookie expired"})
    assert not account_expired({"hostname": "terabox.com", "enabled": False, "valid": False})  # 사용 안 함이면 무시
    assert not account_expired({"hostname": "mega.nz", "enabled": True, "valid": False})       # 다른 호스터는 잠금 대상 아님


def test_lockstate_update():
    ls = LockState()
    ls.update_from_accounts([{"hostname": "terabox.com", "enabled": True, "valid": True, "username": "u"}])
    assert not ls.locked
    ls.update_from_accounts([{"hostname": "terabox.com", "enabled": True, "valid": False, "username": "u", "error": "bad"}])
    assert ls.locked and "bad" in ls.reason


class FakeJD:
    """계정 목록만 상태로 가진 JD 흉내. 다른 호출은 빈 값."""
    def __init__(self, accounts): self.accounts_ = accounts; self.calls = []
    async def aclose(self): pass
    async def state(self): return "IDLE"
    async def packages(self): return []
    async def links(self, *a): return []
    async def grabber_links(self): return []
    async def grabber_packages(self): return []
    async def grabber_collecting(self): return False
    async def speed_limit(self): return {"enabled": False, "limit": 0}
    async def captchas(self): return []
    async def accounts(self): return self.accounts_
    async def update_account(self, uuid, user, pw):
        self.calls.append(("update", uuid, user, pw))
        for a in self.accounts_:
            if a["uuid"] == uuid: a["valid"] = True; a["error"] = None
    async def add_account(self, host, user, pw): self.calls.append(("add", host, user, pw))
    async def refresh_accounts(self, ids): self.calls.append(("refresh", ids))


def _login(c):
    r = c.post("/login", data={"username": "alice", "password": "pw", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303


def test_expiry_shows_banner_not_logout(monkeypatch):
    import app.api as api_mod
    monkeypatch.setattr(api_mod.asyncio, "sleep", _nosleep)
    acct = {"uuid": 7, "hostname": "terabox.com", "username": "me@x.io", "enabled": True, "valid": True, "error": None}
    with TestClient(app) as c:
        app.state.auth = Auth("http://jdweb:5800", "test-secret", transport=_jd_web(_webauth_handler))
        app.state.jd = FakeJD([acct])
        app.state.lock = LockState()
        _login(c)
        r = c.get("/api/state"); assert r.status_code == 200 and r.json()["jd"]["accountAlert"] is None
        # 쿠키 만료 → state 는 여전히 200, accountAlert 로 표시 (로그아웃/423 없음)
        acct["valid"] = False; acct["error"] = "Cookie login failed"
        r = c.get("/api/state")
        assert r.status_code == 200
        assert r.json()["jd"]["accountAlert"] and "Cookie login failed" in r.json()["jd"]["accountAlert"]
        # 페이지도 그대로 접근 (리다이렉트 없음)
        assert c.get("/", follow_redirects=False).status_code == 200
        # 쿠키 재전송으로 정상화되면 alert 사라짐
        token = c.cookies.get("jdr_session")
        r = c.post("/api/accounts/terabox/cookies", json={"cookies": [{"name": "ndus", "value": "x"}], "username": "me@x.io"},
                   headers={"X-JDR-Session": token})
        assert r.status_code == 200 and r.json()["action"] == "updated"
        assert c.get("/api/state").json()["jd"]["accountAlert"] is None


def test_cookie_push_rejects_without_login_cookie(monkeypatch):
    import app.api as api_mod
    monkeypatch.setattr(api_mod.asyncio, "sleep", _nosleep)
    with TestClient(app) as c:
        app.state.auth = Auth("http://jdweb:5800", "test-secret", transport=_jd_web(_webauth_handler))
        app.state.jd = FakeJD([]); app.state.lock = LockState()
        _login(c)
        r = c.post("/api/accounts/terabox/cookies", json={"cookies": [{"name": "foo", "value": "x"}]})
        assert r.status_code == 400 and "ndus" in r.json()["detail"]
        # 이메일 없이는 거부 (JD terabox 플러그인이 이메일 아닌 아이디를 거부함)
        r = c.post("/api/accounts/terabox/cookies", json={"cookies": [{"name": "ndus", "value": "x"}], "username": "me"})
        assert r.status_code == 400 and "이메일" in r.json()["detail"]
        # 계정이 없으면 새로 추가
        r = c.post("/api/accounts/terabox/cookies", json={"cookies": [{"name": "ndus", "value": "x"}], "username": "me@x.io"})
        assert r.status_code == 200 and r.json()["action"] == "added"
        assert app.state.jd.calls[0][:3] == ("add", "terabox.com", "me@x.io")


async def _nosleep(*a, **k):
    return None
