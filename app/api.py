"""/api/* — 프론트가 쓰는 REST. JD RemoteAPI를 폰 화면에 맞게 묶고 다듬는다."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import require_api_auth
from .jd_client import JDClient, JDError, JDUnavailable
from .lock import LockState, is_terabox

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_auth)])

URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def extract_urls(text: str) -> list[str]:
    """공유 텍스트/붙여넣기에서 URL만 뽑는다. 순서 유지, 중복 제거, 끝 구두점 제거."""
    seen: set[str] = set()
    out: list[str] = []
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:!?")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _jd(request: Request) -> JDClient:
    return request.app.state.jd


def _settings(request: Request):
    return request.app.state.settings


def _host_path(request: Request, save_to: str | None) -> str | None:
    """JD 내부 경로(/output/...)를 NAS 호스트 경로로 바꿔 사람에게 보여준다."""
    if not save_to:
        return None
    s = _settings(request)
    if save_to.startswith(s.jd_output_prefix):
        return s.download_root + save_to[len(s.jd_output_prefix):]
    return save_to


def _clean_name(name: str | None) -> str:
    # 일부 플러그인이 이름 앞에 U+2044(⁄)를 붙인다 — 표시용으로 제거
    return (name or "").replace("⁄", "/").strip("/ ") or "(이름 없음)"


def _pkg_view(request: Request, p: dict) -> dict:
    total = p.get("bytesTotal") or 0
    loaded = p.get("bytesLoaded") or 0
    status = (p.get("status") or "").strip()
    finished = bool(p.get("finished"))
    running = bool(p.get("running")) or (p.get("speed") or 0) > 0
    enabled = p.get("enabled", True)
    low = status.lower()
    if finished:
        kind = "finished"
    elif running:
        kind = "running"
    elif not enabled:
        kind = "paused"
    elif any(k in low for k in ("error", "fail", "offline", "not found", "invalid", "captcha")):
        kind = "failed"
    else:
        kind = "waiting"
    return {
        "uuid": p.get("uuid"),
        "name": _clean_name(p.get("name")),
        "bytesTotal": total,
        "bytesLoaded": loaded,
        "progress": (loaded / total) if total else (1.0 if finished else 0.0),
        "speed": p.get("speed") or 0,
        "eta": p.get("eta"),
        "status": status,
        "kind": kind,
        "finished": finished,
        "running": running,
        "enabled": enabled,
        "childCount": p.get("childCount") or 0,
        "hosts": p.get("hosts") or [],
        "path": _host_path(request, p.get("saveTo")),
        "comment": p.get("comment"),
    }


def _link_view(l: dict) -> dict:
    return {
        "uuid": l.get("uuid"),
        "packageUUID": l.get("packageUUID"),
        "name": _clean_name(l.get("name")),
        "host": l.get("host"),
        "url": l.get("url"),
        "bytesTotal": l.get("bytesTotal") or 0,
        "bytesLoaded": l.get("bytesLoaded") or 0,
        "speed": l.get("speed") or 0,
        "eta": l.get("eta"),
        "status": (l.get("status") or "").strip(),
        "finished": bool(l.get("finished")),
        "enabled": l.get("enabled", True),
        "availability": l.get("availability"),
        "skipped": bool(l.get("skipped")),
    }


async def _guard(coro):
    try:
        return await coro
    except JDUnavailable as e:
        raise HTTPException(503, f"JDownloader에 연결할 수 없습니다: {e}")
    except JDError as e:
        raise HTTPException(502, f"JDownloader 오류: {e.kind} {e.detail or ''}".strip())


# ── 상태 (폴링 1회로 전부) ─────────────────────────────────────────────────
@router.get("/state")
async def state(request: Request) -> dict[str, Any]:
    jd = _jd(request)
    s = _settings(request)
    try:
        st, pkgs, glinks, gpkgs, collecting, limit, caps, accts = await asyncio.gather(
            jd.state(), jd.packages(), jd.grabber_links(), jd.grabber_packages(),
            jd.grabber_collecting(), jd.speed_limit(), jd.captchas(), jd.accounts(),
        )
        lock: LockState = request.app.state.lock
        lock.update_from_accounts(accts)
        if lock.locked:
            raise HTTPException(423, {"locked": True, "reason": lock.reason})
    except JDUnavailable as e:
        return {"ts": time.time(), "jd": {"connected": False, "error": str(e),
                "hint": "docker network connect jdnet jdownloader2"}, "packages": [], "linkgrabber": None}
    except JDError as e:
        raise HTTPException(502, f"JDownloader 오류: {e.kind}")
    views = [_pkg_view(request, p) for p in pkgs]
    speed = sum(v["speed"] for v in views)
    gp = {p.get("uuid"): p for p in gpkgs}
    return {
        "ts": time.time(),
        "jd": {"connected": True, "state": st, "speed": speed, "speedlimit": limit,
               "captchas": len(caps), "novncUrl": s.novnc_url, "dsmUrl": s.dsm_url,
               "downloadRoot": s.download_root, "pollMs": s.poll_ms},
        "packages": views,
        "linkgrabber": {
            "collecting": collecting,
            "links": [_link_view(l) for l in glinks],
            "packages": [{"uuid": p.get("uuid"), "name": _clean_name(p.get("name")),
                          "bytesTotal": p.get("bytesTotal") or 0, "childCount": p.get("childCount") or 0,
                          "hosts": p.get("hosts") or [], "path": _host_path(request, p.get("saveTo"))}
                         for p in gp.values()],
        },
    }


@router.get("/packages/{uuid}/links")
async def package_links(uuid: int, request: Request) -> list[dict]:
    links = await _guard(_jd(request).links([uuid]))
    return [_link_view(l) for l in links]


# ── 링크 추가 / 링크그래버 ────────────────────────────────────────────────
class AddLinks(BaseModel):
    text: str = Field(..., description="URL들이 들어 있는 텍스트(줄바꿈/공백 구분, 잡문 섞여도 됨)")
    packageName: str | None = None
    destFolder: str | None = Field(None, description="JD 기준 하위 폴더 이름(프리셋). /output/<이름>")
    autostart: bool = False


@router.post("/links")
async def add_links(body: AddLinks, request: Request) -> dict:
    urls = extract_urls(body.text)
    if not urls:
        raise HTTPException(400, "URL을 찾지 못했습니다.")
    s = _settings(request)
    dest = None
    if body.destFolder:
        sub = body.destFolder.strip().strip("/")
        if ".." in sub or not sub:
            raise HTTPException(400, "폴더 이름이 올바르지 않습니다.")
        dest = f"{s.jd_output_prefix}/{sub}"
    await _guard(_jd(request).add_links(urls, body.packageName or None, dest, body.autostart))
    return {"added": len(urls), "urls": urls, "autostart": body.autostart}


class Ids(BaseModel):
    linkIds: list[int] = []
    packageIds: list[int] = []


@router.post("/linkgrabber/start")
async def grabber_start(ids: Ids, request: Request) -> dict:
    jd = _jd(request)
    link_ids, pkg_ids = ids.linkIds, ids.packageIds
    if not link_ids and not pkg_ids:  # 전체
        link_ids = [l["uuid"] for l in await _guard(jd.grabber_links())]
        if not link_ids:
            return {"moved": 0}
    await _guard(jd.grabber_move_to_downloads(link_ids, pkg_ids))
    return {"moved": len(link_ids) or len(pkg_ids)}


@router.post("/linkgrabber/remove")
async def grabber_remove(ids: Ids, request: Request) -> dict:
    await _guard(_jd(request).grabber_remove(ids.linkIds, ids.packageIds))
    return {"ok": True}


@router.post("/linkgrabber/clear-offline")
async def grabber_clear_offline(request: Request) -> dict:
    jd = _jd(request)
    links = await _guard(jd.grabber_links())
    bad = [l["uuid"] for l in links if (l.get("availability") or "").upper() in ("OFFLINE", "UNKNOWN")]
    if bad:
        await _guard(jd.grabber_remove(bad, []))
    return {"removed": len(bad)}


# ── 다운로드 목록 조작 ─────────────────────────────────────────────────────
class RemovePkgs(BaseModel):
    packageIds: list[int]
    deleteFiles: bool = False


@router.post("/packages/remove")
async def packages_remove(body: RemovePkgs, request: Request) -> dict:
    jd = _jd(request)
    if body.deleteFiles:
        await _guard(jd.delete_with_files([], body.packageIds))
    else:
        await _guard(jd.remove([], body.packageIds))
    return {"removed": len(body.packageIds), "deleteFiles": body.deleteFiles}


@router.post("/cleanup-finished")
async def cleanup_finished(request: Request) -> dict:
    await _guard(_jd(request).cleanup_finished())
    return {"ok": True}


@router.post("/packages/{uuid}/pause")
async def package_pause(uuid: int, request: Request) -> dict:
    await _guard(_jd(request).set_enabled(False, [], [uuid]))
    return {"uuid": uuid, "enabled": False}


@router.post("/packages/{uuid}/resume")
async def package_resume(uuid: int, request: Request) -> dict:
    await _guard(_jd(request).set_enabled(True, [], [uuid]))
    return {"uuid": uuid, "enabled": True}


@router.post("/links/retry")
async def links_retry(ids: Ids, request: Request) -> dict:
    await _guard(_jd(request).retry(ids.linkIds, ids.packageIds))
    return {"ok": True}


# ── 호스터 계정 ───────────────────────────────────────────────────────────
def _acct_view(a: dict) -> dict:
    return {
        "uuid": a.get("uuid"), "hostname": a.get("hostname"), "username": a.get("username") or a.get("userName"),
        "enabled": a.get("enabled", True), "valid": a.get("valid"), "error": a.get("error"),
        "validUntil": a.get("validUntil"), "trafficLeft": a.get("trafficLeft"), "trafficMax": a.get("trafficMax"),
    }


@router.get("/accounts")
async def accounts_list(request: Request) -> list[dict]:
    return [_acct_view(a) for a in await _guard(_jd(request).accounts())]


@router.get("/accounts/hosters")
async def accounts_hosters(request: Request) -> list[str]:
    return await _guard(_jd(request).premium_hosters())


class AccountIn(BaseModel):
    hostname: str = Field(..., min_length=3, description="예: terabox.com")
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1, description="비밀번호 또는 (쿠키 로그인 호스터면) 브라우저 쿠키 내보내기 텍스트")


@router.post("/accounts")
async def accounts_add(body: AccountIn, request: Request) -> dict:
    # 비밀번호(또는 terabox 처럼 쿠키 문자열)는 JD로 그대로 전달만 하고 jd-remote 는 저장/로그하지 않는다. strip 하지 않음.
    if is_terabox(body.hostname) and not EMAIL_RE.match(body.username.strip()):
        raise HTTPException(400, "TeraBox 계정은 아이디 칸에 이메일 주소를 넣어야 합니다 (JD 플러그인 요구사항).")
    await _guard(_jd(request).add_account(body.hostname.strip().lower(), body.username.strip(), body.password))
    return {"ok": True}


class AccountUpdate(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


@router.put("/accounts/{uuid}")
async def accounts_update(uuid: int, body: AccountUpdate, request: Request) -> dict:
    accts = await _guard(_jd(request).accounts())
    a = next((x for x in accts if x.get("uuid") == uuid), None)
    if a and is_terabox(a.get("hostname")) and not EMAIL_RE.match(body.username.strip()):
        raise HTTPException(400, "TeraBox 계정은 아이디 칸에 이메일 주소를 넣어야 합니다 (JD 플러그인 요구사항).")
    await _guard(_jd(request).update_account(uuid, body.username.strip(), body.password))
    return {"ok": True}


class AccountIds(BaseModel):
    ids: list[int]


@router.post("/accounts/remove")
async def accounts_remove(body: AccountIds, request: Request) -> dict:
    await _guard(_jd(request).remove_accounts(body.ids))
    return {"removed": len(body.ids)}


class TeraboxCookies(BaseModel):
    cookies: Any = Field(..., description="브라우저 쿠키 내보내기: JSON 배열(name/value/domain…) 또는 cookies.txt 텍스트")
    username: str | None = Field(None, description="계정이 없어 새로 만들 때 쓸 라벨")


@router.post("/accounts/terabox/cookies")
async def terabox_cookies(body: TeraboxCookies, request: Request) -> dict:
    """크롬 확장이 호출. 기존 TeraBox 계정이 있으면 쿠키만 교체(updateAccount), 없으면 새로 추가.
    잠금 상태에서도 허용되는 유일한 API. 쿠키는 JD로 전달만 하고 저장/로그하지 않는다."""
    import json as _json
    jd = _jd(request)
    cookie_str = body.cookies if isinstance(body.cookies, str) else _json.dumps(body.cookies, ensure_ascii=False)
    if not cookie_str.strip():
        raise HTTPException(400, "쿠키가 비어 있습니다.")
    if isinstance(body.cookies, list) and not any(str(c.get("name", "")).lower() in ("ndus", "bduss", "stoken") for c in body.cookies if isinstance(c, dict)):
        raise HTTPException(400, "TeraBox 로그인 쿠키(ndus/BDUSS)가 없습니다. terabox.com에 로그인된 상태에서 다시 시도하세요.")
    accts = await _guard(jd.accounts())
    tb = [a for a in accts if is_terabox(a.get("hostname"))]
    cur_label = (tb[0].get("username") or tb[0].get("userName") or "") if tb else ""
    label = (body.username or "").strip() or cur_label
    if not EMAIL_RE.match(label):
        raise HTTPException(400, "TeraBox 계정 이메일이 필요합니다 — JD 플러그인은 아이디 칸이 이메일 형식이 아니면 계정을 거부합니다. 확장의 이메일 칸을 채우세요.")
    if tb:
        a = tb[0]
        await _guard(jd.update_account(a["uuid"], label, cookie_str))
        action = "updated"
        uuid = a["uuid"]
    else:
        await _guard(jd.add_account("terabox.com", label, cookie_str))
        action = "added"
        uuid = None
    # 새로 추가했으면 uuid 를 찾아 재검증을 걸고, valid 가 결정될 때까지 잠깐(최대 ~8초) 기다린다
    await asyncio.sleep(1.5)
    accts = await _guard(jd.accounts())
    if uuid is None:
        cand = [a for a in accts if is_terabox(a.get("hostname"))]
        uuid = cand[-1]["uuid"] if cand else None
    if uuid:
        try:
            await jd.refresh_accounts([uuid])
        except (JDError, JDUnavailable):
            pass
        for _ in range(4):
            await asyncio.sleep(2.0)
            accts = await _guard(jd.accounts())
            a = next((x for x in accts if x.get("uuid") == uuid), None)
            if a is None or a.get("valid") is not None or a.get("error"):
                break
    lock: LockState = request.app.state.lock
    lock.update_from_accounts(accts)
    acct = next((a for a in accts if is_terabox(a.get("hostname"))), None)
    return {"action": action, "locked": lock.locked, "account": _acct_view(acct) if acct else None}


# 주의: 위 정적 경로가 아래 /{uuid}/{action} 보다 먼저 등록되어야 한다.
@router.post("/accounts/{uuid}/{action}")
async def accounts_toggle(uuid: int, action: str, request: Request) -> dict:
    jd = _jd(request)
    if action == "enable":
        await _guard(jd.set_accounts_enabled(True, [uuid]))
    elif action == "disable":
        await _guard(jd.set_accounts_enabled(False, [uuid]))
    elif action == "refresh":
        await _guard(jd.refresh_accounts([uuid]))
    else:
        raise HTTPException(404, "알 수 없는 동작")
    return {"ok": True}


# ── 전역 컨트롤 / 속도 제한 ─────────────────────────────────────────────────
@router.post("/control/{action}")
async def control(action: str, request: Request) -> dict:
    jd = _jd(request)
    if action == "start":
        await _guard(jd.start())
    elif action == "pause":
        await _guard(jd.pause(True))
    elif action == "resume":
        await _guard(jd.pause(False))
    elif action == "stop":
        await _guard(jd.stop())
    else:
        raise HTTPException(404, "알 수 없는 동작")
    return {"state": await _guard(jd.state())}


class SpeedLimit(BaseModel):
    enabled: bool
    limit: int | None = Field(None, ge=0, description="bytes/s")


@router.get("/speedlimit")
async def speed_get(request: Request) -> dict:
    return await _guard(_jd(request).speed_limit())


@router.put("/speedlimit")
async def speed_put(body: SpeedLimit, request: Request) -> dict:
    return await _guard(_jd(request).set_speed_limit(body.enabled, body.limit))
