#!/bin/sh
# jdownloader2 의 webauth-htpasswd 는 600 이라 nginx 워커(nginx 사용자)가 직접 못 읽는다.
# 시작 시 복사하고, 이후 60초마다 바뀌었으면 다시 복사한다(JD 에서 비밀번호를 바꿔도 재시작 없이 반영).
SRC=/jd-htpasswd
DST=/etc/nginx/htpasswd
sync_file() {
  if [ -r "$SRC" ]; then
    if ! cmp -s "$SRC" "$DST" 2>/dev/null; then
      cp "$SRC" "$DST.tmp" && chown root:nginx "$DST.tmp" && chmod 0640 "$DST.tmp" && mv "$DST.tmp" "$DST"
      echo "[htpasswd] synced from $SRC"
    fi
  else
    echo "[htpasswd] WARN: $SRC not readable — 모든 요청이 401 이 된다" >&2
  fi
}
sync_file
( while sleep 60; do sync_file; done ) &
