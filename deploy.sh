#!/bin/sh
# NAS에서 실행: 코드 갱신 → 이미지 빌드 → 컨테이너 교체 → 헬스 확인
# 사용: sudo ./deploy.sh        (git 저장소면 pull, 아니면 현재 디렉터리 그대로 빌드)
set -e
cd "$(dirname "$0")"
D=/usr/local/bin/docker
[ -f .env ] || { echo ".env 가 없습니다. .env.example 을 복사해 채우세요."; exit 1; }
if [ -d .git ]; then git pull --ff-only; fi
$D network inspect jdnet >/dev/null 2>&1 || $D network create jdnet
$D network connect jdnet jdownloader2 2>/dev/null || true
$D compose up -d --build
sleep 3
echo "--- healthz"; curl -s -m 5 http://127.0.0.1:5810/healthz; echo
$D compose ps
