import asyncio

import pytest
from fastapi import HTTPException

from app.api import extract_urls
from app.auth import Auth


def test_extract_urls_dedup_and_trailing_punct():
    t = "봐라 https://a.example/x/1, 그리고 https://a.example/x/1 https://b.example/y. 끝"
    assert extract_urls(t) == ["https://a.example/x/1", "https://b.example/y"]


def test_extract_urls_none():
    assert extract_urls("아무 링크 없음") == []


def _jd_web(handler):
    import httpx
    return httpx.MockTransport(handler)


def _webauth_handler(req):
    """jlesage webauth 흉내: 쿠키 없으면 400, 맞으면 302 + auth 쿠키, 틀리면 302 /login/."""
    import httpx
    from urllib.parse import parse_qs
    if "login_success_url" not in req.headers.get("cookie", ""):
        return httpx.Response(400)
    form = parse_qs(req.content.decode())
    if form.get("username") == ["alice"] and form.get("password") == ["pw"]:
        return httpx.Response(302, headers=[("location", "/"), ("set-cookie", "auth=tok123; Path=/; HttpOnly")])
    return httpx.Response(302, headers={"location": "/login/"})


def test_delegated_login_and_token_roundtrip():
    a = Auth("http://jdweb:5800", "secret", cookie_days=1, transport=_jd_web(_webauth_handler))
    assert asyncio.run(a.check_credentials("alice", "pw"))
    assert not asyncio.run(a.check_credentials("alice", "PW"))
    assert not asyncio.run(a.check_credentials("", "pw"))
    tok = asyncio.run(a.login("1.1.1.1", "alice", "pw"))
    assert a.verify(tok) == "alice" and a.verify(tok + "x") is None and a.verify(None) is None
    assert Auth("http://jdweb:5800", "other-secret").verify(tok) is None


def test_login_rate_limit():
    a = Auth("http://jdweb:5800", "secret", transport=_jd_web(_webauth_handler))
    for _ in range(5):
        a.record_failure("1.2.3.4")
    with pytest.raises(HTTPException) as e:
        asyncio.run(a.login("1.2.3.4", "alice", "pw"))
    assert e.value.status_code == 429
    with pytest.raises(HTTPException) as e:
        asyncio.run(a.login("9.9.9.9", "alice", "wrong"))
    assert e.value.status_code == 401


def test_state_maps_unavailable_to_disconnected(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.jd_client import JDUnavailable

    class Dead:
        async def state(self): raise JDUnavailable("no route")
        async def accounts(self): raise JDUnavailable("no route")
        async def aclose(self): pass

    with TestClient(app) as c:
        app.state.jd = Dead()
        app.state.auth = Auth("http://jdweb:5800", "test-secret", transport=_jd_web(_webauth_handler))
        # 미인증 → 401
        assert c.get("/api/state").status_code == 401
        r = c.post("/login", data={"username": "alice", "password": "wrong", "next": "/"}, follow_redirects=False)
        assert r.status_code == 303 and "error=" in r.headers["location"]
        r = c.post("/login", data={"username": "alice", "password": "pw", "next": "/"}, follow_redirects=False)
        assert r.status_code == 303 and "jdr_session" in r.headers.get("set-cookie", "")
        # 인증 후 /api/state 는 연결 끊김 정보를 200 으로
        # (Dead 는 state 만 구현 — gather 가 state 에서 먼저 실패하므로 충분)
        for name in ("packages", "grabber_links", "grabber_packages", "grabber_collecting", "speed_limit", "captchas", "accounts"):
            async def _f(*a, _n=name, **k): raise JDUnavailable("no route")
            setattr(Dead, name, _f)
        r = c.get("/api/state")
        assert r.status_code == 200 and r.json()["jd"]["connected"] is False
        assert "jdnet" in r.json()["jd"]["hint"]
        # healthz 는 503
        assert c.get("/healthz").status_code == 503
        # share → 추가 탭 프리필 리다이렉트
        r = c.get("/share?text=%EB%B4%90%20https://x.example/a%20end", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/?add=https%3A//x.example/a")


# ── 저장 위치 정규화 (_dest_path) ────────────────────────────────────────
class _S:
    jd_output_prefix = "/output"
    download_root = "/volume1/homes/sunsh/Downloads"


def test_dest_path_default_folder():
    from app.api import _dest_path
    assert _dest_path(None, _S) is None
    assert _dest_path("", _S) is None
    assert _dest_path("  ", _S) is None
    # 화면에 보이는 다운로드 폴더 그대로 = 기본 폴더
    assert _dest_path("/volume1/homes/sunsh/Downloads", _S) is None
    assert _dest_path("/volume1/homes/sunsh/Downloads/", _S) is None
    assert _dest_path("/output", _S) is None


def test_dest_path_subfolder():
    from app.api import _dest_path
    assert _dest_path("영화", _S) == "/output/영화"
    assert _dest_path("/volume1/homes/sunsh/Downloads/영화", _S) == "/output/영화"
    assert _dest_path("/volume1/homes/sunsh/Downloads/영화/2026", _S) == "/output/영화/2026"
    assert _dest_path("/output/영화", _S) == "/output/영화"


def test_dest_path_rejects_outside_and_traversal():
    from app.api import _dest_path
    for bad in ["/etc", "/volume1/homes/sunsh/Other", "../x", "/volume1/homes/sunsh/Downloads/../x"]:
        with pytest.raises(HTTPException):
            _dest_path(bad, _S)
