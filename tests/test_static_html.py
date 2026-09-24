"""정적 화면 파일의 기본 위생: id 중복, JS 에서 참조하는 id 의 존재.

2026-09-25 사례: 상단 '전체 시작'과 장바구니 '전체 다운로드 시작'이 같은 id(btnStartAll)를 써서
장바구니 버튼이 죽었고, 상단 버튼에는 두 핸들러가 붙었다. 이 테스트가 그 종류의 실수를 막는다.
"""
import re
from collections import Counter
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_no_duplicate_ids_in_index_html():
    ids = re.findall(r'\bid="([^"]+)"', (STATIC / "index.html").read_text(encoding="utf-8"))
    dup = [i for i, n in Counter(ids).items() if n > 1]
    assert not dup, f"index.html 에 중복 id: {dup}"


def test_every_id_used_by_app_js_exists_in_html():
    html_ids = set(re.findall(r'\bid="([^"]+)"', (STATIC / "index.html").read_text(encoding="utf-8")))
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'", js))
    missing = sorted(u for u in used if u not in html_ids)
    assert not missing, f"app.js 가 참조하지만 index.html 에 없는 id: {missing}"
