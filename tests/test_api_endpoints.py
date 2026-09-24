"""API 수준 통합 테스트 — 가짜 JD 클라이언트를 app.state.jd 에 끼워 실제 라우트를 호출한다.

검증하는 것: JD 의 false-생략 해석, 즉시 다운로드(job) 처리, 시작 계열 동작의 엔진 기동,
전체 시작/일시정지, 순서 이동, 오프라인 정리, 저장 위치 변환, 쿠키 만료 기록, 인증.
"""
from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JDR_SECRET", "test-secret")
os.environ.setdefault("JD_WEB_URL", "http://jdweb:5800")
os.environ.setdefault("JD_OUTPUT_PREFIX", "/output")
os.environ.setdefault("DOWNLOAD_ROOT", "/vol/dl")


class FakeJD:
    """JD RemoteAPI 흉내. JD 처럼 false boolean 은 응답에서 생략한다."""

    def __init__(self):
        self.controller = "STOPPED_STATE"
        self.pkgs: list[dict] = []          # {"uuid","name","enabled"(생략 가능),"finished","running","status","bytesTotal","bytesLoaded"}
        self.grabber: list[dict] = []       # {"uuid","packageUUID","availability","url","name","job"}
        self.gpkgs: list[dict] = []
        self.accounts_data: list[dict] = []
        self.calls: list[tuple] = []
        self.next_job = 900
        self.moved: list[list[int]] = []

    # 컨트롤러
    async def state(self): return self.controller
    async def start(self): self.calls.append(("start",)); self.controller = "RUNNING"
    async def stop(self): self.controller = "STOPPED_STATE"
    async def pause(self, paused): self.calls.append(("pause", paused)); self.controller = "PAUSE" if paused else "RUNNING"
    # 다운로드 목록
    async def packages(self, uuids=None):
        return [{k: v for k, v in p.items() if v is not False} for p in self.pkgs]   # false 생략
    async def links(self, package_uuids=None): return []
    async def remove(self, link_ids, package_ids): self.calls.append(("remove", package_ids))
    async def delete_with_files(self, link_ids, package_ids): self.calls.append(("delete", package_ids))
    async def cleanup_finished(self): self.calls.append(("cleanup",))
    async def move_packages(self, package_ids, after): self.calls.append(("move", package_ids, -1 if after is None else after))
    async def set_enabled(self, enabled, link_ids, package_ids):
        self.calls.append(("set_enabled", enabled, package_ids))
        for p in self.pkgs:
            if p["uuid"] in package_ids: p["enabled"] = enabled
    async def retry(self, link_ids, package_ids): self.calls.append(("retry", package_ids))
    # 링크그래버
    async def add_links(self, links, package_name=None, dest_folder=None, autostart=False, assign_job_id=False):
        self.calls.append(("add_links", links, dest_folder, autostart, assign_job_id))
        self.next_job += 1
        return {"id": self.next_job} if assign_job_id else None
    async def grabber_collecting(self): return False
    async def grabber_links(self): return [dict(l) for l in self.grabber]
    async def grabber_links_by_jobs(self, job_ids): return [dict(l) for l in self.grabber if l.get("job") in job_ids]
    async def grabber_packages(self): return list(self.gpkgs)
    async def grabber_move_to_downloads(self, link_ids, package_ids):
        self.moved.append(list(link_ids)); self.grabber = [l for l in self.grabber if l["uuid"] not in link_ids]
    async def grabber_remove(self, link_ids, package_ids):
        self.calls.append(("grabber_remove", link_ids, package_ids)); self.grabber = [l for l in self.grabber if l["uuid"] not in link_ids]
    # 기타
    async def speed_limit(self): return {"enabled": False, "limit": 0}
    async def set_speed_limit(self, enabled, limit): return {"enabled": enabled, "limit": limit}
    async def captchas(self): return []
    async def accounts(self): return list(self.accounts_data)
    async def update_account(self, uuid, username, password): self.calls.append(("update_account", uuid, username))
    async def add_account(self, host, username, password): self.calls.append(("add_account", host, username))
    async def refresh_accounts(self, ids): pass
    async def aclose(self): pass


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import cookie_meta
    from app.main import app
    monkeypatch.setattr(cookie_meta, "STORE_PATH", str(tmp_path / "cookie.json"))
    with TestClient(app) as c:
        fake = FakeJD()
        app.state.jd = fake
        app.state.autostart_jobs = {}
        c.cookies.set("jdr_session", app.state.auth.issue("tester"))
        c.fake = fake
        yield c


# ── 인증 ──────────────────────────────────────────────────────────────────
def test_api_requires_session(client):
    client.cookies.clear()
    assert client.get("/api/state").status_code == 401


# ── 상태 해석: JD 는 false 를 생략한다 ────────────────────────────────────
def test_state_omitted_enabled_is_paused_and_running_detected(client):
    f = client.fake
    f.pkgs = [
        {"uuid": 1, "name": "a", "bytesTotal": 10, "bytesLoaded": 0},                              # enabled 생략 → 일시정지
        {"uuid": 2, "name": "b", "bytesTotal": 10, "bytesLoaded": 3, "enabled": True, "running": True, "speed": 5},
        {"uuid": 3, "name": "c", "bytesTotal": 10, "bytesLoaded": 10, "enabled": True, "finished": True},
        {"uuid": 4, "name": "d", "bytesTotal": 10, "bytesLoaded": 0, "enabled": True, "status": "Account required"},
    ]
    d = client.get("/api/state").json()
    kinds = {p["uuid"]: p["kind"] for p in d["packages"]}
    assert kinds == {1: "paused", 2: "running", 3: "finished", 4: "action"}
    assert next(p for p in d["packages"] if p["uuid"] == 4)["reason"] == "계정 필요"
    assert d["jd"]["speed"] == 5


# ── 즉시 다운로드(job) ─────────────────────────────────────────────────────
def test_add_links_autostart_registers_job_and_does_not_use_jd_autostart(client):
    r = client.post("/api/links", json={"text": "받아라 https://x.example/a 그리고 https://x.example/b", "autostart": True}).json()
    call = next(c for c in client.fake.calls if c[0] == "add_links")
    assert call[1] == ["https://x.example/a", "https://x.example/b"]
    assert call[3] is False and call[4] is True          # JD autostart 는 끄고 job id 만 받는다
    assert r["jobId"] == 901 and 901 in client.app.state.autostart_jobs


def test_add_links_cart_mode_has_no_job(client):
    r = client.post("/api/links", json={"text": "https://x.example/a"}).json()
    assert r["jobId"] is None and client.app.state.autostart_jobs == {}


def test_autostart_waits_while_crawler_has_not_created_links(client):
    client.post("/api/links", json={"text": "https://x.example/a", "autostart": True})
    client.get("/api/state")                              # 링크 0개 → 작업 유지
    assert 901 in client.app.state.autostart_jobs


def test_autostart_moves_only_its_online_links_and_starts_engine(client):
    f = client.fake
    client.post("/api/links", json={"text": "https://x.example/a", "autostart": True})
    f.grabber = [
        {"uuid": 11, "packageUUID": 100, "availability": "ONLINE", "job": 901, "name": "mine"},
        {"uuid": 12, "packageUUID": 100, "availability": "UNKNOWN", "job": 901, "name": "mine2"},
        {"uuid": 21, "packageUUID": 200, "availability": "ONLINE", "job": 555, "name": "cart-item"},   # 장바구니에 있던 것
    ]
    f.gpkgs = [{"uuid": 100, "name": "P"}, {"uuid": 200, "name": "Cart"}]
    d = client.get("/api/state").json()
    assert f.moved == [[11]]                              # 장바구니의 21 은 건드리지 않는다
    assert f.controller == "RUNNING"
    remaining = {l["uuid"]: l for l in d["linkgrabber"]["links"]}
    assert remaining[12]["autostart"] is True and remaining[12]["jobId"] == 901   # 아직 검증 중 → 다운로드 탭 '검증 중'
    assert remaining[21]["autostart"] is False            # 장바구니 항목 그대로
    assert 901 in client.app.state.autostart_jobs        # 12 가 남아 있어 작업 유지


def test_autostart_finishes_and_leaves_offline_in_cart(client):
    f = client.fake
    client.post("/api/links", json={"text": "https://x.example/a", "autostart": True})
    f.grabber = [{"uuid": 11, "packageUUID": 100, "availability": "OFFLINE", "job": 901, "name": "dead"}]
    d = client.get("/api/state").json()
    assert f.moved == [] and 901 not in client.app.state.autostart_jobs
    assert d["linkgrabber"]["links"][0]["autostart"] is False   # 장바구니에 오프라인으로 보인다


def test_autostart_cancel_returns_links_to_cart(client):
    f = client.fake
    client.post("/api/links", json={"text": "https://x.example/a", "autostart": True})
    f.grabber = [{"uuid": 12, "packageUUID": 100, "availability": "UNKNOWN", "job": 901, "name": "m"}]
    client.get("/api/state")
    assert client.post("/api/autostart/cancel", json={"jobId": 901}).json()["cancelled"] is True
    d = client.get("/api/state").json()
    assert d["linkgrabber"]["links"][0]["autostart"] is False


# ── 시작 계열은 엔진을 켠다 ────────────────────────────────────────────────
def test_grabber_start_moves_and_starts_engine(client):
    f = client.fake
    f.grabber = [{"uuid": 1, "packageUUID": 9, "availability": "ONLINE"}]
    r = client.post("/api/linkgrabber/start", json={}).json()
    assert f.moved == [[1]] and f.controller == "RUNNING" and r["controllerStarted"] is True


def test_package_resume_enables_and_starts_engine_and_clears_pause(client):
    f = client.fake
    f.pkgs = [{"uuid": 1, "name": "a", "bytesTotal": 1, "bytesLoaded": 0}]
    f.controller = "PAUSE"
    client.post("/api/packages/1/resume")
    assert ("set_enabled", True, [1]) in f.calls and ("pause", False) in f.calls
    assert f.pkgs[0]["enabled"] is True and f.controller == "RUNNING"


def test_package_pause_only_disables(client):
    f = client.fake
    f.pkgs = [{"uuid": 1, "name": "a", "bytesTotal": 1, "bytesLoaded": 0, "enabled": True}]
    client.post("/api/packages/1/pause")
    assert f.pkgs[0]["enabled"] is False and f.controller == "STOPPED_STATE"


def test_retry_starts_engine(client):
    f = client.fake
    client.post("/api/links/retry", json={"packageIds": [1]})
    assert ("retry", [1]) in f.calls and f.controller == "RUNNING"


# ── 전체 시작 / 전체 일시정지 ─────────────────────────────────────────────
def test_start_all_enables_unfinished_and_starts(client):
    f = client.fake
    f.pkgs = [
        {"uuid": 1, "name": "a", "bytesTotal": 1, "bytesLoaded": 0},                    # 비활성
        {"uuid": 2, "name": "b", "bytesTotal": 1, "bytesLoaded": 0, "enabled": True},
        {"uuid": 3, "name": "c", "bytesTotal": 1, "bytesLoaded": 1, "enabled": True, "finished": True},
    ]
    f.controller = "PAUSE"
    r = client.post("/api/control/start-all").json()
    assert ("set_enabled", True, [1, 2]) in f.calls        # 완료(3)는 제외
    assert r["resumed"] == 2 and f.controller == "RUNNING"


def test_pause_all_disables_only_enabled_unfinished(client):
    f = client.fake
    f.pkgs = [
        {"uuid": 1, "name": "a", "bytesTotal": 1, "bytesLoaded": 0},                    # 이미 비활성
        {"uuid": 2, "name": "b", "bytesTotal": 1, "bytesLoaded": 0, "enabled": True},
        {"uuid": 3, "name": "c", "bytesTotal": 1, "bytesLoaded": 1, "enabled": True, "finished": True},
    ]
    r = client.post("/api/control/pause-all").json()
    assert ("set_enabled", False, [2]) in f.calls and r["paused"] == 1


# ── 순서 / 정리 ───────────────────────────────────────────────────────────
def test_move_to_top_uses_minus_one(client):
    client.post("/api/packages/7/move", json={"after": None})
    client.post("/api/packages/7/move", json={"after": 3})
    assert ("move", [7], -1) in client.fake.calls and ("move", [7], 3) in client.fake.calls


def test_clear_offline_keeps_unchecked_links(client):
    f = client.fake
    f.grabber = [
        {"uuid": 1, "packageUUID": 9, "availability": "OFFLINE"},
        {"uuid": 2, "packageUUID": 9, "availability": "UNKNOWN"},      # 아직 검증 안 됨 → 남긴다
        {"uuid": 3, "packageUUID": 9, "availability": "ONLINE"},
    ]
    r = client.post("/api/linkgrabber/clear-offline").json()
    assert r["removed"] == 1 and [l["uuid"] for l in f.grabber] == [2, 3]


def test_packages_remove_with_and_without_files(client):
    client.post("/api/packages/remove", json={"packageIds": [1], "deleteFiles": False})
    client.post("/api/packages/remove", json={"packageIds": [2], "deleteFiles": True})
    assert ("remove", [1]) in client.fake.calls and ("delete", [2]) in client.fake.calls


# ── 저장 위치 ────────────────────────────────────────────────────────────
def test_add_links_dest_folder_forms(client):
    for raw, expected in [("영화", "/output/영화"), ("/vol/dl/영화/2026", "/output/영화/2026"), ("/vol/dl", None), ("", None)]:
        client.fake.calls.clear()
        client.post("/api/links", json={"text": "https://x.example/a", "destFolder": raw})
        assert next(c for c in client.fake.calls if c[0] == "add_links")[2] == expected, raw
    assert client.post("/api/links", json={"text": "https://x.example/a", "destFolder": "/etc"}).status_code == 400
    assert client.post("/api/links", json={"text": "링크 없음"}).status_code == 400


# ── 쿠키 전송 ────────────────────────────────────────────────────────────
def test_terabox_cookies_records_expiry_and_updates_account(client, monkeypatch):
    import asyncio
    monkeypatch.setattr(asyncio, "sleep", _noop)   # 검증 대기(최대 ~8초)를 건너뛴다
    f = client.fake
    f.accounts_data = [{"uuid": 5, "hostname": "terabox.com", "username": "me@x.com", "enabled": True, "valid": True}]
    cookies = [{"name": "ndus", "expirationDate": 1821622962.3, "domain": ".terabox.com"},
               {"name": "ndut_fmt", "expirationDate": 1792681952, "domain": ".terabox.com"}]
    r = client.post("/api/accounts/terabox/cookies", json={"cookies": cookies}).json()
    assert r["action"] == "updated" and ("update_account", 5, "me@x.com") in f.calls
    acct = client.get("/api/accounts").json()[0]
    assert acct["cookieExpiry"] == 1792681952 and acct["cookieName"] == "ndut_fmt"   # 가장 이른 인증 쿠키


async def _noop(*_):
    return None


def test_terabox_cookies_rejects_without_login_cookie(client):
    r = client.post("/api/accounts/terabox/cookies", json={"cookies": [{"name": "_ga", "expirationDate": 1}]})
    assert r.status_code == 400
