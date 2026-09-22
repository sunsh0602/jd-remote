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


def test_lock_flow_end_to_end(monkeypatch):
    import app.api as api_mod
    monkeypatch.setattr(api_mod.asyncio, "sleep", _nosleep)
    acct = {"uuid": 7, "hostname": "terabox.com", "username": "me@x.io", "enabled": True, "valid": True, "error": None}
    with TestClient(app) as c:
        app.state.auth = Auth("http://jdweb:5800", "test-secret", transport=_jd_web(_webauth_handler))
        app.state.jd = FakeJD([acct])
        app.state.lock = LockState()
        _login(c)                       # 정상 상태에서 로그인
        assert c.get("/api/state").status_code == 200
        acct["valid"] = False; acct["error"] = "Cookie login failed"   # 이후 쿠키 만료
        # 만료 감지 → /api/state 423, 페이지는 잠금 로그인 화면
        r = c.get("/api/state"); assert r.status_code == 423 and r.json()["detail"]["locked"]
        r = c.get("/", follow_redirects=False); assert r.status_code == 302 and "locked=1" in r.headers["location"]
        r = c.get("/login"); assert r.status_code == 200 and "TeraBox 쿠키가 만료" in r.text and "hidden" in r.text
        assert c.get("/api/lock").json()["locked"] is True
        # 잠금 중 다른 API는 423, 쿠키 갱신은 통과 (확장이 쓰는 헤더 인증으로)
        assert c.post("/api/cleanup-finished").status_code == 423
        token = c.cookies.get("jdr_session"); c.cookies.clear()
        r = c.post("/api/accounts/terabox/cookies", json={"cookies": [{"name": "ndus", "value": "x", "domain": ".terabox.com"}]},
                   headers={"X-JDR-Session": token})
        assert r.status_code == 200, r.text
        j = r.json(); assert j["action"] == "updated" and j["locked"] is False and j["account"]["valid"] is True
        assert app.state.jd.calls[0][:3] == ("update", 7, "me@x.io") and '"ndus"' in app.state.jd.calls[0][3]
        # 잠금 해제 후 정상
        c.cookies.set("jdr_session", token)
        assert c.get("/api/state").status_code == 200
        assert c.get("/api/lock").json()["locked"] is False


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
