"""browser/ 인증 프록시 nginx 설정의 보안 기본값이 빠지지 않았는지 확인한다.

2026-09-30: 쿠키 갱신 접속 주소의 401 페이지에 'nginx/1.31.6' 이 노출돼 server_tokens off 를 추가했다.
"""
import re
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "browser" / "nginx" / "default.conf"


def _directives() -> str:
    return "\n".join(line.split("#", 1)[0] for line in CONF.read_text(encoding="utf-8").splitlines())


def test_server_tokens_off():
    assert re.search(r"^\s*server_tokens\s+off\s*;", _directives(), re.M), "server_tokens off 가 없으면 오류 페이지에 버전이 노출된다"


def test_basic_auth_still_on():
    d = _directives()
    assert re.search(r"^\s*auth_basic\s+\"[^\"]+\"\s*;", d, re.M)
    assert re.search(r"^\s*auth_basic_user_file\s+\S+\s*;", d, re.M)
