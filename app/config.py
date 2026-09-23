"""환경변수 기반 설정. 컨테이너 기동 시 한 번 읽는다."""
from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    secret: str
    jd_api_url: str
    jd_web_url: str            # jlesage webauth 로그인 위임 대상 (컨테이너의 noVNC nginx)
    jd_output_prefix: str      # JD 컨테이너 안에서 보이는 다운로드 루트 (/output)
    download_root: str         # 같은 폴더의 NAS 호스트 경로 (사람에게 보여줄 경로)
    novnc_url: str
    dsm_url: str
    browser_url: str
    poll_ms: int
    cookie_days: int
    jd_timeout_s: float


def load() -> Settings:
    secret = _env("JDR_SECRET")
    if not secret:
        # 비어 있으면 매 기동마다 바뀌어 로그인이 풀린다 — 경고만 하고 임시 생성
        secret = secrets.token_urlsafe(32)
        print("경고: JDR_SECRET 미설정 — 재시작 시 세션이 모두 만료됩니다.", file=sys.stderr)
    return Settings(
        secret=secret,
        jd_api_url=(_env("JD_API_URL", "http://jdownloader2:3128") or "").rstrip("/"),
        jd_web_url=(_env("JD_WEB_URL", "http://jdownloader2:5800") or "").rstrip("/"),
        jd_output_prefix=_env("JD_OUTPUT_PREFIX", "/output") or "/output",
        download_root=_env("DOWNLOAD_ROOT", "/volume1/Downloads") or "",
        novnc_url=_env("NOVNC_URL", "") or "",
        dsm_url=_env("DSM_URL", "") or "",
        browser_url=_env("BROWSER_URL", "") or "",   # 폰용 TeraBox 쿠키 갱신 크롬(browser/). 비우면 아이콘 숨김
        poll_ms=int(_env("POLL_MS", "3000") or 3000),
        cookie_days=int(_env("COOKIE_DAYS", "30") or 30),
        jd_timeout_s=float(_env("JD_TIMEOUT_S", "10") or 10),
    )
