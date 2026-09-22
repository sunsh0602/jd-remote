"""jd-remote — JDownloader2 모바일 원격 제어 (FastAPI)."""
from __future__ import annotations

import html
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import extract_urls, router as api_router
from .auth import COOKIE, Auth, client_ip, page_redirect_if_anon, set_session_cookie
from .jd_client import JDClient, JDError, JDUnavailable
from .lock import LockState
from fastapi.middleware.cors import CORSMiddleware

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = config.load()
    app.state.settings = s
    app.state.auth = Auth(s.jd_web_url, s.secret, s.cookie_days)
    app.state.jd = JDClient(s.jd_api_url, timeout=s.jd_timeout_s)
    app.state.lock = LockState()
    yield
    await app.state.jd.aclose()
    await app.state.auth.aclose()


app = FastAPI(title="jd-remote", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(api_router)
# 크롬 확장(chrome-extension://…)이 쿠키 전송 API를 호출할 수 있게 허용. 인증은 X-JDR-Session 헤더로.
app.add_middleware(CORSMiddleware, allow_origin_regex=r"^(chrome|moz|edge)-extension://.*$", allow_credentials=True,
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type", "X-JDR-Session"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _secure(request: Request) -> bool:
    # DSM 리버스 프록시가 X-Forwarded-Proto 를 넘긴다. uvicorn --proxy-headers 로 request.url.scheme 도 https.
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


# ── 페이지 ───────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def index(request: Request):
    if (r := page_redirect_if_anon(request)) is not None:
        return r
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/share", include_in_schema=False)
async def share(request: Request, url: str = "", text: str = "", title: str = ""):
    """PWA share_target 진입점. 로그인 확인 후 URL만 뽑아 추가 탭으로 넘긴다(자동 제출 X)."""
    if (r := page_redirect_if_anon(request)) is not None:
        return r
    urls = extract_urls(" ".join([url, text, title]))
    from urllib.parse import quote
    return RedirectResponse("/?add=" + quote("\n".join(urls)), status_code=303)


LOGIN_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f1115"><title>JD Remote — 로그인</title>
<link rel="manifest" href="/static/manifest.webmanifest"><link rel="icon" href="/static/icons/icon-192.png">
<link rel="stylesheet" href="/static/style.css"></head>
<body class="login"><main class="login-card">
<img src="/static/icons/icon-192.png" alt="" width="64" height="64">
<h1>JD Remote</h1>
{locked}
{error}
<form method="post" action="/login" autocomplete="on" {formhidden}>
<input type="hidden" name="next" value="{next}">
<label>아이디<input name="username" type="text" autocomplete="username" autocapitalize="off" autofocus required></label>
<label>비밀번호<input name="password" type="password" autocomplete="current-password" required></label>
<button type="submit" class="btn primary wide">로그인</button>
</form><p class="muted">JD 화면(noVNC)과 같은 계정입니다. 30일 동안 로그인이 유지됩니다.</p></main></body></html>"""


LOCKED_HTML = """<div class="banner bad" style="flex-direction:column;align-items:stretch;text-align:left;gap:8px">
<b>TeraBox 쿠키가 만료되어 로그아웃되었습니다.</b>
<span class="small">{reason}</span>
<span class="small">PC 크롬(또는 안드로이드 Kiwi/Edge)에서 <a href="https://www.terabox.com/" target="_blank" rel="noopener">terabox.com</a>에 로그인한 뒤,
<b>JD Remote 쿠키 도우미</b> 확장 아이콘을 누르세요. 갱신되면 이 화면이 자동으로 풀립니다.</span>
<span class="small muted" id="lockPoll">상태 확인 중…</span></div>
<script>(function(){{var t=setInterval(function(){{fetch('/api/lock',{{credentials:'same-origin'}}).then(function(r){{return r.json()}}).then(function(j){{if(!j.locked){{clearInterval(t);location.replace('/')}}else{{document.getElementById('lockPoll').textContent='아직 만료 상태 · '+new Date().toLocaleTimeString()}}}}).catch(function(){{}})}},5000)}})()</script>"""


@app.get("/api/lock", include_in_schema=False)
async def lock_status(request: Request):
    """공개(미인증 가능): 잠금 여부만. 로그인 화면이 폴링해 자동 복귀에 쓴다."""
    lock: LockState = await request.app.state.lock.refresh(request.app.state.jd)
    return {"locked": lock.locked, "reason": lock.reason if lock.locked else None}


@app.get("/login", include_in_schema=False)
async def login_form(request: Request, next: str = "/", error: str = "", locked: str = ""):
    lock: LockState = await request.app.state.lock.refresh(request.app.state.jd)
    if lock.locked:
        return HTMLResponse(LOGIN_HTML.format(locked=LOCKED_HTML.format(reason=html.escape(lock.reason or "")), error="",
                                              formhidden="hidden", next="/"))
    if request.app.state.auth.is_authed(request):
        return RedirectResponse(next if next.startswith("/") else "/", status_code=302)
    err = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(LOGIN_HTML.format(locked="", error=err, formhidden="",
                                          next=html.escape(next if next.startswith("/") else "/")))


@app.post("/login", include_in_schema=False)
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    auth: Auth = request.app.state.auth
    lock: LockState = await request.app.state.lock.refresh(request.app.state.jd)
    if lock.locked:
        return RedirectResponse("/login?locked=1", status_code=303)
    try:
        token = await auth.login(client_ip(request), username, password)
    except Exception as e:  # HTTPException
        detail = getattr(e, "detail", "로그인 실패")
        from urllib.parse import quote
        return RedirectResponse(f"/login?next={quote(next)}&error={quote(str(detail))}", status_code=303)
    resp = RedirectResponse(next if next.startswith("/") else "/", status_code=303)
    set_session_cookie(resp, token, auth.max_age, secure=_secure(request))
    return resp


@app.post("/logout", include_in_schema=False)
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


# ── 공개 리소스(PWA 설치용) ────────────────────────────────────────────────
@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def sw():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/healthz", include_in_schema=False)
async def healthz(request: Request):
    try:
        st = await request.app.state.jd.state()
        return {"ok": True, "jd": st}
    except JDUnavailable as e:
        return JSONResponse({"ok": False, "jd": None, "error": str(e)}, status_code=503)
    except JDError as e:
        return JSONResponse({"ok": False, "jd": None, "error": e.kind}, status_code=502)
