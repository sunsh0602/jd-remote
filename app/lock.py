"""TeraBox 쿠키 만료 감지 → jd-remote 잠금.

JD accountsV2 의 terabox 계정이 enabled 인데 valid=false 이거나 error 가 있으면 "만료"로 본다.
잠기면 /api/* 는 423, 페이지는 /login?locked=1 로 보낸다. 쿠키 갱신 엔드포인트만 열어 둔다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .jd_client import JDClient, JDError, JDUnavailable

TERABOX_KEYS = ("terabox", "1024tera", "nephobox", "mirrobox", "momerybox", "teraboxapp", "4funbox", "freeterabox")


def is_terabox(hostname: str | None) -> bool:
    h = (hostname or "").lower()
    return any(k in h for k in TERABOX_KEYS)


def account_expired(a: dict) -> bool:
    if not is_terabox(a.get("hostname")) or not a.get("enabled", False):   # JD 는 false 를 생략한다
        return False
    return a.get("valid") is False or bool(a.get("error"))


@dataclass
class LockState:
    locked: bool = False
    reason: str | None = None
    checked_at: float = 0.0
    account: dict | None = field(default=None, repr=False)

    def update_from_accounts(self, accounts: list[dict]) -> None:
        tb = [a for a in accounts if is_terabox(a.get("hostname"))]
        bad = [a for a in tb if account_expired(a)]
        self.checked_at = time.time()
        if bad:
            a = bad[0]
            self.locked = True
            self.account = a
            self.reason = f"TeraBox 계정({a.get('username') or a.get('userName') or ''}) 쿠키가 만료되었거나 오류입니다: {a.get('error') or 'invalid'}"
        else:
            self.locked = False
            self.reason = None
            self.account = tb[0] if tb else None

    async def refresh(self, jd: JDClient, max_age: float = 20.0) -> "LockState":
        """max_age 초 안에 검사했으면 재사용. JD 연결 실패는 잠금 변경 없이 통과."""
        if time.time() - self.checked_at < max_age:
            return self
        try:
            self.update_from_accounts(await jd.accounts())
        except (JDUnavailable, JDError):
            self.checked_at = time.time()
        return self
