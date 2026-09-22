"""쿠키 로그인 계정의 '언제까지 쓸 수 있나'를 계산하고 보관한다.

JD 는 넘겨받은 쿠키를 계정의 비밀번호 칸에 넣어 둘 뿐 스스로 갱신하지 않는다.
그래서 JD 입장의 현실적인 한계는 **인증에 쓰이는 쿠키 중 가장 먼저 만료되는 것**이다.
TeraBox 를 예로 들면 ndus 는 1년짜리지만 ndut_fmt/ndut_fmv 는 30일짜리다.

쿠키 값 자체는 저장하지 않는다. 남기는 것은 만료 시각(초)과 그 기준이 된 쿠키 이름뿐이다.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any

# 인증에 관여하는 쿠키만 본다. 분석/광고(_ga, _fbp, _uetsid …)와 표시 설정(lang)은 제외.
AUTH_COOKIES = {"ndus", "bduss", "stoken", "ndut_fmt", "ndut_fmv", "tsid", "csrftoken"}

STORE_PATH = os.environ.get("JDR_STATE_FILE", "/data/cookie_expiry.json")

# cookies.txt(Netscape) 한 줄: domain flag path secure expiry name value
_NETSCAPE_RE = re.compile(r"^(?P<domain>[^\s#][^\s]*)\t\S+\t\S+\t\S+\t(?P<exp>\d+)\t(?P<name>\S+)\t", re.M)


def earliest_auth_expiry(cookies: Any) -> tuple[int, str] | None:
    """인증 쿠키 중 가장 이른 만료를 (유닉스초, 쿠키이름)으로 돌려준다. 없으면 None.

    세션 쿠키(만료 없음)는 브라우저를 닫으면 사라지는 값이라 기준에서 뺀다 — JD 로 넘어간
    뒤에는 만료 없이 계속 쓰이므로 '언제까지'를 말해 주지 못한다.
    """
    found: list[tuple[int, str]] = []
    if isinstance(cookies, str):
        for m in _NETSCAPE_RE.finditer(cookies):
            name, exp = m.group("name"), int(m.group("exp"))
            if name.lower() in AUTH_COOKIES and exp > 0:
                found.append((exp, name))
    elif isinstance(cookies, list):
        for c in cookies:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or "")
            if name.lower() not in AUTH_COOKIES:
                continue
            exp = c.get("expirationDate") or c.get("expires") or c.get("expiry")
            try:
                exp = int(float(exp))
            except (TypeError, ValueError):
                continue
            if exp > 0:
                found.append((exp, name))
    return min(found) if found else None


def load() -> dict:
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_expiry(hostname: str, expiry: int, cookie_name: str) -> None:
    """호스터별로 하나만 기억한다. 쓰기에 실패해도 앱 동작에는 영향을 주지 않는다."""
    data = load()
    data[(hostname or "").lower()] = {"expiry": expiry, "cookie": cookie_name}
    try:
        os.makedirs(os.path.dirname(STORE_PATH) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STORE_PATH) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, STORE_PATH)
    except OSError:
        pass


def get_expiry(hostname: str) -> dict | None:
    v = load().get((hostname or "").lower())
    return v if isinstance(v, dict) and v.get("expiry") else None
