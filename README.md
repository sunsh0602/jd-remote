# jd-remote

JDownloader2를 폰에서 쓰기 위한 모바일 우선 웹앱(PWA). NAS Docker에서 JD 컨테이너 옆에 떠서
JD 로컬 RemoteAPI(3128)를 호출한다. Transmission Remote처럼 **링크 추가 / 상태 / 완료 정리 / 일시정지·재개**가 목적.

접속: `https://jdr.<your-domain>` (DSM 리버스 프록시 → `127.0.0.1:5810`). 실제 도메인·경로는 이 저장소에 두지 않고 `.env`와 별도 운영 문서에서 관리한다.

## 기능
- 다운로드 목록: 진행바·속도·ETA, 필터(전체/진행중/완료/실패/일시정지)·검색, 카드 **좌 스와이프 삭제 / 우 스와이프 일시정지·재개**, 탭하면 파일 목록·경로 복사·DSM 열기
- 수집됨(링크그래버): 온라인/오프라인 확인 후 패키지별/전체 시작, 오프라인 제거
- 추가: 붙여넣기(잡문 섞여도 URL만 추출), 저장 위치(하위 폴더) 하나, "바로 시작" 옵션
- 안드로이드 **공유 → JD Remote**(PWA share_target), 홈 화면 설치, 앱 열 때 **클립보드 URL 감지** 배너
- 계정 탭: 호스터 계정 목록·추가·변경·사용 여부·갱신·삭제 (`accountsV2`). 추가 가능한 호스터는 드롭다운으로 제한(현재 TeraBox만; `index.html`의 option과 `app.js`의 `COOKIE_HOSTERS`에 추가). **쿠키 로그인 전용 호스터(terabox 계열)는 비밀번호 입력을 잠그고 브라우저 쿠키 내보내기 텍스트만 받음**(JD 플러그인이 비밀번호 로그인을 지원하지 않음; 도메인 입력 시 자동 전환). 그 외 호스터는 비밀번호 또는 쿠키 선택. 비밀번호/쿠키는 JD로 전달만, 앱에 저장 안 함
- 상단: 전역 시작/일시정지, 속도 제한 토글(프리셋), 완료 정리, 60초 속도 스파크라인, **캡차 대기 배너**(noVNC 바로가기), JD 연결 끊김 안내
- 로그인은 **JD 컨테이너의 기존 계정(noVNC용 webauth)에 위임** — jd-remote는 비밀번호를 저장·관리하지 않음. 세션 30일 유지, 실패 지연·분당 5회 제한

## TeraBox 쿠키 만료 → 잠금 → 확장으로 갱신
- JD의 TeraBox 계정(`accountsV2`)이 `valid=false` 또는 `error`면 jd-remote는 **잠금**: 모든 `/api/*`는 423, 화면은 로그인 페이지에 "TeraBox 쿠키가 만료되어 로그아웃되었습니다" 안내(로그인 폼 숨김). 세션 쿠키 자체는 유지해서 확장이 갱신 API를 인증할 수 있게 한다.
- 갱신: `extension/`의 크롬 확장(**JD Remote 쿠키 도우미**) — terabox.com 로그인 후 아이콘 클릭 → 쿠키를 `POST /api/accounts/terabox/cookies`로 전송(`X-JDR-Session` 헤더 인증) → JD `setUserNameAndPassword` → 잠금 자동 해제, 로그인 화면이 5초 폴링(`/api/lock`)으로 앱에 복귀. 설치/사용법은 [extension/README.md](extension/README.md).
- 잠금 중에도 `/api/accounts/terabox/cookies`와 공개 `/api/lock`만 동작한다.

## 구성
```
폰 ─https─▶ DSM 리버스 프록시(jdr.<your-domain>:443) ─▶ http://127.0.0.1:5810 (이 앱)
                                                              └▶ http://jdownloader2:3128 (도커 네트워크 jdnet)
```
- JD 컨테이너 설정 `cfg/org.jdownloader.api.RemoteAPIConfig.json`: `deprecatedapienabled:true, deprecatedapilocalhostonly:false, deprecatedapiport:3128` (컨테이너 정지 상태에서 편집).
- 3128은 호스트에 publish하지 않는다. 두 컨테이너가 `jdnet`에 함께 있으면 이름으로 통신.

## 배포 (NAS, 레지스트리 없이 로컬 빌드)
```sh
# 1회
sudo docker network create jdnet
sudo docker network connect jdnet jdownloader2
git clone <this-repo> /volume1/docker/jd-remote   # 또는 rsync
cd /volume1/docker/jd-remote && cp .env.example .env && vi .env   # JDR_SECRET(openssl rand -base64 32) 만 채우면 됨
# 배포/업데이트 (매번)
sudo ./deploy.sh
```
`deploy.sh`: `git pull`(저장소면) → `docker compose up -d --build` → `/healthz` 확인.
이후 DSM 리버스 프록시 `jdr.<your-domain>` → `http://localhost:5810` 생성(HSTS, WebSocket 불필요).

## 개발
```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest pytest-asyncio
.venv/bin/pytest -q
JDR_SECRET=dev JD_API_URL=http://<nas-lan-ip>:3128 JD_WEB_URL=http://<nas-lan-ip>:5800 .venv/bin/uvicorn app.main:app --reload
```
(JD API를 로컬에서 붙이려면 NAS에서 임시로 `-p 127.0.0.1:3128:3128` publish 후 SSH 터널을 쓴다. 평소엔 닫아 둔다.)

## 환경변수
| 이름 | 기본 | 설명 |
|---|---|---|
| `JD_WEB_URL` | `http://jdownloader2:5800` | 로그인 위임 대상(jlesage webauth). 계정은 `docker exec -ti jdownloader2 webauth-user add <name>` |
| `JDR_SECRET` | (권장) | 세션 서명 키. 비우면 재시작마다 로그아웃 |
| `JD_API_URL` | `http://jdownloader2:3128` | JD RemoteAPI |
| `JD_OUTPUT_PREFIX` / `DOWNLOAD_ROOT` | `/output` / `/volume1/Downloads` | JD 내부 경로 ↔ 표시용 호스트 경로(.env에서 실제 값 지정) |
| `NOVNC_URL` / `DSM_URL` | (없음) | 배너·설정 링크. 비우면 해당 버튼 숨김 |
| `POLL_MS` / `COOKIE_DAYS` | `3000` / `30` | 폴링 주기, 로그인 유지 |

## 인증 위임 메모
jlesage webauth: `POST /login/login` form `username`/`password`, 요청에 쿠키 `login_success_url=/; login_failure_url=/login/`가 **필수**(없으면 400).
성공 → `302 /` + `Set-Cookie: auth=<token>`(24h). 실패 → auth 쿠키 없음. jd-remote는 auth 쿠키 유무만 보고 자체 세션을 발급한다.

## JD RemoteAPI 메모 (확인된 것, JD build 48637)
- **POST `/<ns>/<method>` body `{"params":[…]}`만 동작.** GET `?params=`는 INTERNAL_SERVER_ERROR/BAD_PARAMETERS.
- 성공 `{"data":…}`, 실패 `{"src":"DEVICE","type":"…"}`.
- `downloadcontroller/getSpeedInBytes` 없음 → 패키지 `speed` 합산.
- 쓰는 메서드: `downloadcontroller/{getCurrentState,start,stop,pause}`, `downloadsV2/{queryPackages,queryLinks,removeLinks,cleanup,setEnabled,resetLinks,forceDownload}`, `linkgrabberv2/{addLinks,queryLinks,queryPackages,isCollecting,moveToDownloadlist,removeLinks}`, `config/{get,set}`(GeneralSettings.DownloadSpeedLimit*), `captcha/list`, `accountsV2/{listAccounts,listPremiumHoster,addAccount,setUserNameAndPassword,enableAccounts,disableAccounts,refreshAccounts,removeAccounts}`.
