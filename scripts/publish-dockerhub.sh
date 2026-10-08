#!/bin/sh
# Docker Hub(bitsets/jd-remote)에 이미지를 빌드·푸시한다.
# 작업 머신에서 실행: 커밋된 HEAD 를 그대로(git archive) NAS 로 보내 NAS 의 도커로 빌드한다(linux/amd64).
# 사전: NAS 에서 한 번 `docker login -u bitsets` (Docker Hub 액세스 토큰, Read & Write) — 토큰은 NAS 에만 저장된다.
#
#   scripts/publish-dockerhub.sh            → :latest 와 :<커밋 7자리> 두 태그로 푸시
#   SSH_HOST=nas IMAGE=bitsets/jd-remote scripts/publish-dockerhub.sh
set -eu
SSH_HOST=${SSH_HOST:-nas}
IMAGE=${IMAGE:-bitsets/jd-remote}
cd "$(dirname "$0")/.."
if [ -n "$(git status --porcelain)" ]; then echo "커밋되지 않은 변경이 있습니다. 커밋 후 실행하세요."; exit 1; fi
SHA=$(git rev-parse --short=7 HEAD)
echo "== $IMAGE:$SHA (+latest) 빌드 → $SSH_HOST"
git archive --format=tar HEAD Dockerfile requirements.txt app | ssh -o BatchMode=yes "$SSH_HOST" "
  set -e; D=/usr/local/bin/docker; W=\$(mktemp -d); trap 'rm -rf \$W' EXIT
  tar xf - -C \$W
  \$D build --pull -t $IMAGE:$SHA -t $IMAGE:latest \
    --label org.opencontainers.image.source=https://github.com/bitsets/jd-remote \
    --label org.opencontainers.image.revision=$SHA \$W
  \$D push $IMAGE:$SHA
  \$D push $IMAGE:latest
  \$D image inspect $IMAGE:latest --format '{{.Id}} {{.Architecture}} {{.Size}}'
"
echo "== 완료: docker.io/$IMAGE:latest , :$SHA"
