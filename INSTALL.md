# jd-remote 설치 가이드 (Synology NAS)

jd-remote 는 NAS 의 JDownloader 2 를 폰에서 다루는 웹앱입니다. 이 문서는 Synology DSM 사용자를 대상으로,
SSH 없이 **DSM 작업 스케줄러**만으로 설치하고 업데이트하는 방법을 설명합니다.

- 이미지: `ghcr.io/bitsets/jd-remote:latest`
- 배포 방식: 작업 스케줄러에 "배포" 작업을 하나 만들어 두고 **수동 실행**합니다. 처음 실행하면 설치, 다시 실행하면 최신 이미지로 업데이트됩니다.

```
폰 ─https─▶ DSM 리버스 프록시 ─▶ 127.0.0.1:5810 jd-remote ─▶ jdownloader2:3128 (JD RemoteAPI)
                                                       └────▶ jdownloader2:5800 (로그인 확인)
```

---

## 0. 요구 사항

| 항목 | 내용 |
|---|---|
| DSM | 7.2 이상 |
| 패키지 | Container Manager |
| CPU | x86_64(Intel/AMD) 모델. ARM 모델은 배포된 이미지에 arm64 가 포함된 경우에만 동작합니다 |
| 도메인 | 폰에서 밖에서 쓰려면 DDNS(예: `xxx.synology.me`)와 인증서. 집 안에서만 쓰면 생략 가능 |

> 아래 예시는 공유 폴더가 `/volume1/docker`, 다운로드 폴더가 `/volume1/Downloads` 라고 가정합니다.
> 본인 NAS 의 경로로 바꿔 쓰세요(제어판 → 공유 폴더에서 확인).

---

## 1. 사전 준비 — 도커 환경

### 1-1. Container Manager 설치
패키지 센터 → **Container Manager** 설치. 설치되면 `docker` 공유 폴더가 자동으로 생깁니다.

### 1-2. 폴더 만들기
File Station 에서 다음 폴더를 만듭니다.

```
/volume1/docker/jdownloader2/config     JDownloader 설정
/volume1/docker/jd-remote               jd-remote 설정(.env)
/volume1/Downloads                      다운로드 받을 곳(이미 있으면 그대로)
```

### 1-3. JDownloader 2 컨테이너 (이미 쓰고 있다면 1-4 로)
제어판 → **작업 스케줄러** → 생성 → 예약된 작업 → **사용자 정의 스크립트**

- 일반: 작업 이름 `JDownloader2 배포`, 사용자 **root**, "사용" 체크 해제(수동 실행만)
- 작업 설정 → 사용자 정의 스크립트:

```sh
D=/usr/local/bin/docker
$D pull jlesage/jdownloader-2
$D rm -f jdownloader2 2>/dev/null
$D run -d --name=jdownloader2 \
  -p 127.0.0.1:5800:5800 \
  -e USER_ID=1026 -e GROUP_ID=100 \
  -e TZ=Asia/Seoul -e DARK_MODE=1 \
  -e WEB_AUTHENTICATION=1 \
  -e WEB_AUTHENTICATION_ALLOW_INSECURE=1 \
  -e WEB_AUTHENTICATION_USERNAME=<JD 화면 아이디> \
  -e WEB_AUTHENTICATION_PASSWORD=<JD 화면 비밀번호> \
  -v /volume1/docker/jdownloader2/config:/config \
  -v /volume1/Downloads:/output \
  --restart always \
  jlesage/jdownloader-2
# jd-remote 와 같은 도커 네트워크
$D network inspect jdnet >/dev/null 2>&1 || $D network create jdnet
$D network connect jdnet jdownloader2 2>/dev/null || true
```

- `USER_ID`/`GROUP_ID` 는 다운로드 파일의 소유자입니다. 본인 DSM 계정의 값으로 바꾸세요
  (작업 스케줄러에서 사용자=본인 계정, 스크립트 `id` 로 한 번 실행 → 작업 결과 메일/로그에서 확인).
- **이 아이디·비밀번호가 jd-remote 로그인 계정이 됩니다.** jd-remote 는 자체 비밀번호를 두지 않고 JD 화면의 계정으로 로그인을 확인합니다.
- `WEB_AUTHENTICATION_ALLOW_INSECURE=1` 은 앞단(DSM 리버스 프록시)이 HTTPS 를 맡기 때문에 필요합니다. 5800 은 `127.0.0.1` 에만 열려 밖에서 직접 닿지 않습니다.

작업을 선택 → **실행**. 1~2분 뒤 컨테이너 매니저에서 `jdownloader2` 가 실행 중이면 성공입니다.

### 1-4. JDownloader RemoteAPI 켜기 (1회)
jd-remote 는 JD 의 로컬 API(포트 3128)로 동작합니다. 기본으로 꺼져 있어 한 번 켜야 합니다.
작업 스케줄러에 `JD RemoteAPI 켜기`(root, 수동) 를 만들고 아래를 넣어 **실행**하세요.

```sh
D=/usr/local/bin/docker
C=/volume1/docker/jdownloader2/config/cfg/org.jdownloader.api.RemoteAPIConfig.json
$D stop jdownloader2                       # JD 가 꺼진 상태에서 고쳐야 저장이 유지됨
if [ -f "$C" ]; then
  sed -i -E 's/"deprecatedapienabled" *: *false/"deprecatedapienabled":true/; s/"deprecatedapilocalhostonly" *: *true/"deprecatedapilocalhostonly":false/' "$C"
  grep -q deprecatedapiport "$C" || sed -i 's/}$/,"deprecatedapiport":3128}/' "$C"
else
  echo '{"deprecatedapienabled":true,"deprecatedapilocalhostonly":false,"deprecatedapiport":3128}' > "$C"
fi
$D start jdownloader2
cat "$C"
```

- 3128 은 호스트에 publish 하지 않습니다. 같은 도커 네트워크(`jdnet`)의 jd-remote 만 닿습니다.
- `cfg` 폴더가 없다는 오류가 나면 1-3 의 JD 가 아직 한 번도 끝까지 뜨지 않은 것입니다. 2~3분 기다렸다 다시 실행하세요.

---

## 2. jd-remote 배포

### 2-1. 설정 파일(.env) 만들기 (1회)
`/volume1/docker/jd-remote/.env` 를 만듭니다. File Station 에서 텍스트 파일로 업로드하거나,
작업 스케줄러에 1회용 스크립트로 만들어도 됩니다.

```ini
# 세션 서명 키 — 아무 긴 무작위 문자열. 바꾸면 모든 기기가 로그아웃됩니다.
JDR_SECRET=<32자 이상 무작위 문자열>

# JD 위치(도커 네트워크 jdnet 안의 컨테이너 이름). 1-3 대로 만들었다면 그대로
JD_WEB_URL=http://jdownloader2:5800
JD_API_URL=http://jdownloader2:3128

# 다운로드 경로: JD 안의 경로와 그에 대응하는 NAS 경로(화면 표시·저장 위치 입력용)
JD_OUTPUT_PREFIX=/output
DOWNLOAD_ROOT=/volume1/Downloads

# (선택) 설정 화면의 바로가기 기본 주소. 비우면 각 사용자가 ⚙ 설정에서 직접 넣습니다.
NOVNC_URL=https://jd.<your-domain>
BROWSER_URL=

POLL_MS=3000
COOKIE_DAYS=30
TZ=Asia/Seoul
```

`JDR_SECRET` 은 작업 스케줄러에서 `openssl rand -base64 32` 를 한 번 실행해 결과로 받거나, 비밀번호 생성기로 만든 긴 문자열이면 됩니다.

### 2-2. 배포 작업 만들기
작업 스케줄러 → 생성 → 예약된 작업 → 사용자 정의 스크립트

- 일반: 작업 이름 `jd-remote 배포`, 사용자 **root**, "사용" 체크 해제(수동 실행만)
- 작업 설정 → (선택) "실행 세부 정보를 이메일로 보내기" 체크 — 결과 로그를 메일로 받을 수 있습니다
- 사용자 정의 스크립트:

```sh
#!/bin/sh
# jd-remote 배포/업데이트 — 실행할 때마다 latest 이미지를 받아 컨테이너를 교체한다.
set -e
D=/usr/local/bin/docker
IMAGE=ghcr.io/bitsets/jd-remote:latest
NAME=jd-remote
ENV_FILE=/volume1/docker/jd-remote/.env

[ -f "$ENV_FILE" ] || { echo "설정 파일이 없습니다: $ENV_FILE (2-1 참고)"; exit 1; }

echo "== 이미지 받기"
$D pull "$IMAGE"

echo "== 네트워크 확인"
$D network inspect jdnet >/dev/null 2>&1 || $D network create jdnet
$D network connect jdnet jdownloader2 2>/dev/null || true

echo "== 컨테이너 교체"
$D rm -f "$NAME" 2>/dev/null || true
$D run -d --name "$NAME" \
  --env-file "$ENV_FILE" \
  -p 127.0.0.1:5810:8000 \
  -v jd-remote-data:/data \
  --network jdnet \
  --restart unless-stopped \
  --log-opt max-size=5m --log-opt max-file=3 \
  "$IMAGE"

echo "== 상태 확인"
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 3
  R=$(curl -s -m 4 http://127.0.0.1:5810/healthz || true)
  [ -n "$R" ] && { echo "healthz: $R"; break; }
done
$D ps --filter "name=$NAME" --format "{{.Names}}  {{.Status}}  {{.Image}}"

echo "== 옛 이미지 정리"
$D image prune -f >/dev/null 2>&1 || true
```

작업을 선택 → **실행**. 메일(또는 작업 스케줄러 → 작업 결과 → 로그)에서 다음과 비슷하게 나오면 성공입니다.

```
healthz: {"ok":true,"jd":"IDLE"}
jd-remote  Up 10 seconds (health: starting)  ghcr.io/bitsets/jd-remote:latest
```

`"jd":"IDLE"`(또는 RUNNING) 대신 연결 오류가 보이면 1-4(RemoteAPI)와 `jdnet` 연결을 확인하세요.

- `jd-remote-data` 볼륨에 추가 이력, 쿠키 만료일 같은 작은 상태가 저장됩니다. 컨테이너를 교체해도 유지됩니다.
- 5810 은 `127.0.0.1` 에만 열립니다. 밖에서는 아래 리버스 프록시로만 들어옵니다.

### 2-3. 리버스 프록시 (밖에서 쓰려면)
제어판 → 로그인 포털 → 고급 → **리버스 프록시** → 생성

| 항목 | 값 |
|---|---|
| 소스 | 프로토콜 HTTPS, 호스트 이름 `jdr.<your-domain>`, 포트 443, HSTS 사용 |
| 대상 | 프로토콜 HTTP, 호스트 이름 `localhost`, 포트 `5810` |

인증서: 제어판 → 보안 → 인증서에서 `jdr.<your-domain>` 을 포함하는 인증서(와일드카드 권장)를 이 항목에 지정합니다.
공유기에서 443 → NAS 443 포트포워딩이 되어 있어야 합니다.

JD 화면(noVNC)도 밖에서 보려면 같은 방법으로 `jd.<your-domain>` → `localhost:5800` 을 만들고,
사용자 지정 머리글에서 **WebSocket** 을 추가(생성 → WebSocket)하세요. 시간 제한은 3600초 정도로 늘리면 끊김이 줄어듭니다.

### 2-4. 확인
폰 브라우저로 `https://jdr.<your-domain>` 접속 → 1-3 의 JD 계정으로 로그인 → 다운로드 탭이 보이면 끝입니다.
크롬 메뉴의 "홈 화면에 추가"로 앱처럼 설치할 수 있습니다.

---

## 3. 업데이트

작업 스케줄러 → `jd-remote 배포` 선택 → **실행**. 끝입니다.
latest 이미지를 다시 받아 컨테이너만 교체하며, `.env` 와 `jd-remote-data` 볼륨의 데이터는 유지됩니다.

자동으로 업데이트하려면 같은 작업의 일정을 예: 매주 일요일 새벽 4시로 정하고 "사용"을 체크하세요.
받는 도중 바뀐 기능이 마음에 들지 않을 수 있으니 수동 실행을 권합니다.

### 특정 버전으로 고정·되돌리기
스크립트의 `IMAGE=` 를 `ghcr.io/bitsets/jd-remote:<태그>` 로 바꿔 실행하면 그 버전으로 바뀝니다.
되돌린 뒤 다시 `latest` 로 바꾸면 최신으로 돌아옵니다.

---

## 4. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 로그인 화면에서 계속 실패 | 1-3 의 `WEB_AUTHENTICATION_USERNAME/PASSWORD`. JD 화면(5800)에 같은 계정으로 들어가지는지 |
| 상단에 "JDownloader에 연결할 수 없습니다" | 1-4 RemoteAPI 설정, `docker network connect jdnet jdownloader2` |
| 재시작할 때마다 로그아웃 | `.env` 의 `JDR_SECRET` 이 비어 있지 않은지 |
| 저장 위치 경로가 이상하게 보임 | `.env` 의 `DOWNLOAD_ROOT` 가 1-3 의 `/output` 마운트 경로와 같은지 |
| 배포 작업이 `pull` 에서 실패 | NAS 의 인터넷 연결, 이미지 이름(`ghcr.io/bitsets/jd-remote:latest`) |
| 폰에서 JD 화면(noVNC)이 로딩 바에서 멈춤 | 일부 안드로이드 기기의 H.264 디코더 문제(아래 "알려진 문제") |

컨테이너 로그: 컨테이너 매니저 → 컨테이너 → `jd-remote` → 로그.

### 알려진 문제: 폰에서 JD 화면이 로딩 바에서 멈춤
JD 이미지의 noVNC 가 시작 시 H.264 하드웨어 디코딩을 시험하는데, 일부 안드로이드 기기는 이 시험에 끝내 답하지 않아
화면이 뜨지 않습니다. 이 이미지의 VNC 서버는 H.264 를 쓰지 않으므로 시험을 건너뛰어도 손해가 없습니다.
1-3 스크립트 맨 끝에 아래 두 줄을 추가하세요(JD 컨테이너를 새로 만들 때마다 적용됨).

```sh
sleep 20
$D exec jdownloader2 sed -i 's|^supportsWebCodecsH264Decode = await _checkWebCodecsH264DecodeSupport();$|supportsWebCodecsH264Decode = false;|' /opt/noVNC/core/util/browser.js
```

---

## 5. 제거

1. 작업 스케줄러에서 `jd-remote 배포` 삭제
2. 컨테이너 매니저 → 컨테이너 `jd-remote` 중지 후 삭제, 이미지 `ghcr.io/bitsets/jd-remote` 삭제
3. (데이터까지 지우려면) 컨테이너 매니저 → 볼륨 `jd-remote-data` 삭제, `/volume1/docker/jd-remote` 폴더 삭제
4. 리버스 프록시 항목 `jdr.<your-domain>` 삭제

JDownloader 와 다운로드 파일은 그대로 남습니다.

---

## 부록: 선택 기능

- **TeraBox 쿠키 자동 갱신(크롬 확장)**: 저장소의 [extension/](extension/README.md) 참고.
- **폰에서 쿠키 갱신용 NAS 크롬**: 저장소의 [browser/](browser/README.md) 참고. 배포하면 `.env` 의 `BROWSER_URL` 에 그 주소를 넣으세요.
