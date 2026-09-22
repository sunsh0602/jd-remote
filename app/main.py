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

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = config.load()
    app.state.settings = s
    app.state.auth = Auth(s.jd_web_url, s.secret, s.cookie_days)
    app.state.jd = JDClient(s.jd_api_url, timeout=s.jd_timeout_s)
    yield
    await app.state.jd.aclose()
    await app.state.auth.aclose()


app = FastAPI(title="jd-remote", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(api_router)
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
{error}
<form method="post" action="/login" autocomplete="on">
<input type="hidden" name="next" value="{next}">
<label>아이디<input name="username" type="text" autocomplete="username" autocapitalize="off" autofocus required></label>
<label>비밀번호<input name="password" type="password" autocomplete="current-password" required></label>
<button type="submit" class="btn primary wide">로그인</button>
</form><p class="muted">JD 화면(noVNC)과 같은 계정입니다. 30일 동안 로그인이 유지됩니다.</p></main></body></html>"""


@app.get("/login", include_in_schema=False)
async def login_form(request: Request, next: str = "/", error: str = ""):
    if request.app.state.auth.is_authed(request):
        return RedirectResponse(next if next.startswith("/") else "/", status_code=302)
    err = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(LOGIN_HTML.format(error=err, next=html.escape(next if next.startswith("/") else "/")))


@app.post("/login", include_in_schema=False)
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    auth: Auth = request.app.state.auth
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
