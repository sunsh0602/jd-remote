"""로그인은 JD 컨테이너(jlesage webauth)에 위임한다. jd-remote는 비밀번호를 저장하지 않는다.

흐름:
  1) 사용자 ID/PW → POST http://jdownloader2:5800/login/login (form username/password,
     쿠키 login_success_url=/; login_failure_url=/login/ 필수 — 없으면 400)
  2) 성공이면 응답에 Set-Cookie: auth=<token> 이 온다. 실패면 없다.
  3) 성공 시 jd-remote 자체 서명 쿠키(사용자명만 담음, cookie_days)를 발급. JD 토큰은 보관하지 않는다.
계정 관리는 JD 쪽: docker exec -ti jdownloader2 webauth-user add|update|del|list <name>
"""
from __future__ import annotations

import asyncio
import secrets
import time
from collections import defaultdict, deque

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE = "jdr_session"
SESSION_HEADER = "x-jdr-session"   # 크롬 확장이 쿠키 대신 헤더로 세션 토큰을 보냄 (SameSite 우회)


class Auth:
    def __init__(self, jd_web_url: str, secret: str, cookie_days: int = 30,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=jd_web_url.rstrip("/"), timeout=8.0,
                                       transport=transport, follow_redirects=False)
        self._ser = URLSafeTimedSerializer(secret, salt="jdr-session")
        self.max_age = cookie_days * 86400
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── 위임 검증 ─────────────────────────────────────────────────────
    async def check_credentials(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        try:
            r = await self._http.post(
                "/login/login",
                data={"username": username, "password": password},
                headers={"Cookie": "login_success_url=/; login_failure_url=/login/"},
            )
        except httpx.HTTPError as e:
            raise HTTPException(503, f"JD 인증 서버에 연결할 수 없습니다: {e}")
        # 성공 판별: auth=<token> 쿠키 발급 여부 (실패는 302 /login/ 또는 4xx, auth 쿠키 없음)
        return any(k == "auth" and v for k, v in r.cookies.items()) or \
            any(h.startswith("auth=") for h in r.headers.get_list("set-cookie"))

    def too_many_attempts(self, ip: str, limit: int = 5, window: float = 60.0) -> bool:
        q = self._attempts[ip]
        now = time.monotonic()
        while q and now - q[0] > window:
            q.popleft()
        return len(q) >= limit

    def record_failure(self, ip: str) -> None:
        self._attempts[ip].append(time.monotonic())

    async def login(self, ip: str, username: str, password: str) -> str:
        """성공 시 세션 토큰 반환. 실패 시 HTTPException."""
        if self.too_many_attempts(ip):
            raise HTTPException(429, "로그인 시도가 너무 많습니다. 1분 후 다시 시도하세요.")
        if not await self.check_credentials(username.strip(), password):
            self.record_failure(ip)
            await asyncio.sleep(1.0)
            raise HTTPException(401, "아이디 또는 비밀번호가 틀렸습니다.")
        return self.issue(username.strip())

    # ── 세션 토큰 ──────────────────────────────────────────────────────
    def issue(self, username: str) -> str:
        return self._ser.dumps({"v": 2, "u": username, "n": secrets.token_hex(4)})

    def verify(self, token: str | None) -> str | None:
        """유효하면 사용자명, 아니면 None."""
        if not token:
            return None
        try:
            data = self._ser.loads(token, max_age=self.max_age)
            return data.get("u") or None
        except (BadSignature, SignatureExpired):
            return None

    def session_token(self, request: Request) -> str | None:
        return request.headers.get(SESSION_HEADER) or request.cookies.get(COOKIE)

    def is_authed(self, request: Request) -> bool:
        return self.verify(self.session_token(request)) is not None


def client_ip(request: Request) -> str:
    # DSM 리버스 프록시가 X-Forwarded-For 를 붙인다. 프록시는 127.0.0.1 로만 도달.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def set_session_cookie(resp, token: str, max_age: int, secure: bool) -> None:
    resp.set_cookie(COOKIE, token, max_age=max_age, httponly=True, secure=secure,
                    samesite="lax", path="/")


def require_api_auth(request: Request) -> None:
    """/api/* 용 — 미인증이면 401 JSON. TeraBox 쿠키 만료로 잠긴 상태면 쿠키 갱신 엔드포인트만 통과."""
    auth: Auth = request.app.state.auth
    if not auth.is_authed(request):
        raise HTTPException(401, "로그인이 필요합니다.")


def page_redirect_if_anon(request: Request) -> RedirectResponse | None:
    auth: Auth = request.app.state.auth
    if auth.is_authed(request):
        return None
    nxt = request.url.path
    if request.url.query:
        nxt += "?" + request.url.query
    return RedirectResponse(f"/login?next={nxt}", status_code=302)
