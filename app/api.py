"""/api/* — 프론트가 쓰는 REST. JD RemoteAPI를 폰 화면에 맞게 묶고 다듬는다."""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import cookie_meta
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


# 상태 문구 키워드 → (kind, 원인 라벨). 앞에서부터 먼저 맞는 것을 쓴다.
# '조치 필요'(action): 사용자가 원인을 풀면 이어지는 상태.  '실패'(failed): 링크가 죽었거나 잘못됨.
_ACTION_RULES = (
    ("captcha", "캡차 대기"),
    ("account", "계정 필요"), ("login", "계정 필요"), ("premium", "계정 필요"),
    ("exist", "파일 존재"),
    ("disk", "저장 공간"), ("quota", "저장 공간"), ("space", "저장 공간"),
    ("skip", "건너뜀"),
)
_FAILED_KEYS = ("error", "fail", "offline", "not found", "invalid")


def classify_status(status: str) -> tuple[str, str | None]:
    """JD 패키지 status 문구를 (kind, reason) 으로. kind 는 action/failed/waiting 중 하나."""
    low = (status or "").lower()
    for key, label in _ACTION_RULES:
        if key in low:
            return "action", label
    if any(k in low for k in _FAILED_KEYS):
        return "failed", None
    return "waiting", None


def _pkg_view(request: Request, p: dict) -> dict:
    total = p.get("bytesTotal") or 0
    loaded = p.get("bytesLoaded") or 0
    status = (p.get("status") or "").strip()
    finished = bool(p.get("finished"))
    running = bool(p.get("running")) or (p.get("speed") or 0) > 0
    enabled = p.get("enabled", True)
    reason = None
    if finished:
        kind = "finished"
    elif running:
        kind = "running"
    elif not enabled:
        kind = "paused"
    else:
        kind, reason = classify_status(status)
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
        "reason": reason,
        "finished": finished,
        "running": running,
        "enabled": enabled,
        "childCount": p.get("childCount") or 0,
        "hosts": p.get("hosts") or [],
        "path": _host_path(request, p.get("saveTo")),
        "comment": p.get("comment"),
    }


AUTOSTART_TTL = 600.0   # 즉시 다운로드 표시를 유지하는 최대 시간(초). 이 안에 검증이 끝나면 JD 가 알아서 다운로드로 옮긴다.


def _pending(request: Request) -> dict[str, float]:
    st = request.app.state
    if not hasattr(st, "autostart_pending"):
        st.autostart_pending = {}
    now = time.time()
    for u, ts in list(st.autostart_pending.items()):
        if now - ts > AUTOSTART_TTL:
            del st.autostart_pending[u]
    return st.autostart_pending


def _is_autostart(l: dict, pending: dict[str, float]) -> bool:
    """장바구니(링크그래버)의 링크가 '즉시 다운로드'로 들어와 검증 중인 것인지.
    URL 이 일치하면 확실. JD 가 URL 을 정규화해 바꿔 놓는 경우가 있어, 그때는 추가 시각이 즉시 다운로드
    요청 시각과 60초 안에 겹치는지로 판단한다. 오프라인으로 판정된 링크는 JD 가 옮기지 않으므로 장바구니로 돌려보낸다."""
    if not pending or (l.get("availability") or "").upper() == "OFFLINE":
        return False
    if (l.get("url") or "") in pending:
        return True
    added = (l.get("addedDate") or 0) / 1000.0
    return added > 0 and any(abs(added - ts) <= 60 for ts in pending.values())


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
    except JDUnavailable as e:
        return {"ts": time.time(), "jd": {"connected": False, "error": str(e),
                "hint": "docker network connect jdnet jdownloader2"}, "packages": [], "linkgrabber": None}
    except JDError as e:
        raise HTTPException(502, f"JDownloader 오류: {e.kind}")
    views = [_pkg_view(request, p) for p in pkgs]
    speed = sum(v["speed"] for v in views)
    gp = {p.get("uuid"): p for p in gpkgs}
    pending = _pending(request)
    glink_views = []
    autostart_pkgs: set = set()
    for l in glinks:
        v = _link_view(l)
        v["autostart"] = _is_autostart(l, pending)
        if v["autostart"]:
            autostart_pkgs.add(l.get("packageUUID"))
        glink_views.append(v)
    return {
        "ts": time.time(),
        "jd": {"connected": True, "state": st, "speed": speed, "speedlimit": limit,
               "captchas": len(caps), "novncUrl": s.novnc_url, "dsmUrl": s.dsm_url, "browserUrl": s.browser_url,
               "downloadRoot": s.download_root, "pollMs": s.poll_ms,
               "accountAlert": lock.reason if lock.locked else None},
        "packages": views,
        "linkgrabber": {
            "collecting": collecting,
            "links": glink_views,
            "packages": [{"uuid": p.get("uuid"), "name": _clean_name(p.get("name")),
                          "bytesTotal": p.get("bytesTotal") or 0, "childCount": p.get("childCount") or 0,
                          "hosts": p.get("hosts") or [], "path": _host_path(request, p.get("saveTo")),
                          "autostart": p.get("uuid") in autostart_pkgs}
                         for p in gp.values()],
        },
    }


@router.get("/packages/{uuid}/links")
async def package_links(uuid: int, request: Request) -> list[dict]:
    links = await _guard(_jd(request).links([uuid]))
    return [_link_view(l) for l in links]


async def _ensure_running(jd: JDClient) -> bool:
    """JD 다운로드 컨트롤러가 정지(STOPPED) 상태면 켠다. 사용자가 '시작'을 눌렀는데 목록에만 옮겨지고
    실제로는 돌지 않는 일을 막는다. (JD 는 받던 것이 모두 끝나면 컨트롤러를 스스로 멈춘다.)"""
    try:
        st = await jd.state()
        if str(st).upper().startswith("STOPPED"):
            await jd.start()
            return True
    except (JDError, JDUnavailable):
        pass
    return False


def _dest_path(raw: str | None, s) -> str | None:
    """저장 위치 입력을 JD 내부 경로로 바꾼다. 없거나 기본 폴더면 None(=JD 기본 저장 위치).

    화면(설정·추가 탭)에는 호스트 경로(DOWNLOAD_ROOT)가 보이므로 그 전체 경로를 그대로
    받아도 동작해야 한다. JD 내부 경로(JD_OUTPUT_PREFIX)로 들어와도 같게 처리한다.
    """
    if not raw:
        return None
    sub = raw.strip()
    under_root = False
    for root in (s.download_root, s.jd_output_prefix):
        root = (root or "").rstrip("/")
        if root and (sub == root or sub.startswith(root + "/")):
            sub, under_root = sub[len(root):], True
            break
    if sub.startswith("/") and not under_root:
        raise HTTPException(400, "다운로드 폴더 밖의 경로는 쓸 수 없습니다.")
    parts = [x for x in sub.strip().strip("/").split("/") if x]
    if not parts:
        return None  # 기본 다운로드 폴더
    if ".." in parts:
        raise HTTPException(400, "폴더 경로가 올바르지 않습니다.")
    return f"{s.jd_output_prefix}/{'/'.join(parts)}"


# ── 링크 추가 / 링크그래버 ────────────────────────────────────────────────
class AddLinks(BaseModel):
    text: str = Field(..., description="URL들이 들어 있는 텍스트(줄바꿈/공백 구분, 잡문 섞여도 됨)")
    packageName: str | None = None
    destFolder: str | None = Field(None, description="저장 위치. 하위 폴더 이름이거나, 화면에 보이는 다운로드 폴더 전체 경로(그 아래 하위 폴더 포함)")
    autostart: bool = False


@router.post("/links")
async def add_links(body: AddLinks, request: Request) -> dict:
    urls = extract_urls(body.text)
    if not urls:
        raise HTTPException(400, "URL을 찾지 못했습니다.")
    s = _settings(request)
    dest = _dest_path(body.destFolder, s)
    await _guard(_jd(request).add_links(urls, body.packageName or None, dest, body.autostart))
    started = False
    if body.autostart:
        _pending(request).update({u: time.time() for u in urls})
        started = await _ensure_running(_jd(request))
    return {"added": len(urls), "urls": urls, "autostart": body.autostart, "controllerStarted": started}


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
    started = await _ensure_running(jd)
    return {"moved": len(link_ids) or len(pkg_ids), "controllerStarted": started}


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
    v = {
        "uuid": a.get("uuid"), "hostname": a.get("hostname"), "username": a.get("username") or a.get("userName"),
        "enabled": a.get("enabled", True), "valid": a.get("valid"), "error": a.get("error"),
        "validUntil": a.get("validUntil"), "trafficLeft": a.get("trafficLeft"), "trafficMax": a.get("trafficMax"),
    }
    # 쿠키 로그인 계정이면 '넣어 둔 쿠키가 언제까지 유효한지'가 실제로 중요한 날짜다.
    ck = cookie_meta.get_expiry(a.get("hostname") or "")
    if ck:
        v["cookieExpiry"] = ck.get("expiry")
        v["cookieName"] = ck.get("cookie")
    return v


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
    # 붙여넣은 값이 쿠키 내보내기(JSON 배열 또는 cookies.txt)면 만료 날짜만 기록한다.
    if a:
        raw: Any = body.password
        try:
            parsed = json.loads(raw)
            raw = parsed if isinstance(parsed, list) else raw
        except (TypeError, ValueError):
            pass
        exp = cookie_meta.earliest_auth_expiry(raw)
        if exp:
            cookie_meta.save_expiry(a.get("hostname") or "", exp[0], exp[1])
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
    잠금 상태에서도 허용되는 유일한 API. 쿠키 값은 JD로 전달만 하고 저장/로그하지 않는다(만료 날짜만 기록)."""
    jd = _jd(request)
    cookie_str = body.cookies if isinstance(body.cookies, str) else json.dumps(body.cookies, ensure_ascii=False)
    if not cookie_str.strip():
        raise HTTPException(400, "쿠키가 비어 있습니다.")
    if isinstance(body.cookies, list) and not any(str(c.get("name", "")).lower() in ("ndus", "bduss", "stoken") for c in body.cookies if isinstance(c, dict)):
        raise HTTPException(400, "TeraBox 로그인 쿠키(ndus/BDUSS)가 없습니다. terabox.com에 로그인된 상태에서 다시 시도하세요.")
    # 인증 쿠키 중 가장 먼저 만료되는 것이 JD 입장의 실제 한계다(값은 저장하지 않고 날짜만).
    exp = cookie_meta.earliest_auth_expiry(body.cookies)
    if exp:
        cookie_meta.save_expiry("terabox.com", exp[0], exp[1])
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
