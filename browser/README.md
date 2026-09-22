# browser/ — 폰에서 TeraBox 쿠키 갱신용 NAS 크롬

`ndus` 등 TeraBox 로그인 쿠키는 HttpOnly 라 폰 브라우저에서는 꺼낼 수 없다(확장이 필요). 대신 NAS 에
데스크톱 크로미움을 하나 띄우고 폰 브라우저로 그 화면에 접속한다. 화면 하나로 안드로이드·아이폰 모두 된다.

## 동작
- 처음 한 번: 이 크롬에서 **terabox.com 로그인**, **jd-remote 로그인**(확장이 세션 쿠키를 쓴다), 확장 아이콘 → 서버 주소(`https://jdr.<your-domain>`)와 TeraBox 이메일 입력.
- 이후: 폰에서 이 화면을 열고 terabox.com 페이지가 뜨는 것만 확인하면 끝. 접속하면 30일짜리 `ndut_fmt`/`ndut_fmv` 가 재발급되고 확장의 자동 전송이 jd-remote 로 보낸다.
- 확장 팝업의 `JD Remote ✗` 는 jd-remote 세션(기본 30일)이 끝났다는 뜻 → 이 크롬에서 jd-remote 에 다시 로그인.

## 배포 (NAS)
```sh
cd /volume1/docker/jd-remote/browser
cp .env.example .env && vi .env          # PUID/PGID(id), CUSTOM_USER/PASSWORD
sudo docker compose up -d
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5820/   # 401 이면 기본 인증 동작
```
DSM 리버스 프록시: `browser.<your-domain>`(HTTPS 443, HSTS) → `http://localhost:5820`, **WebSocket 헤더**
(`Upgrade: $http_upgrade`, `Connection: $connection_upgrade`)와 타임아웃 3600 필요 — jdownloader2 noVNC 항목과 동일.

## 주의
- 로그인된 브라우저가 항상 켜져 있는 셈. 5820 을 호스트 밖에 열지 말고, 기본 인증 비밀번호는 길게.
- 확장은 `../extension` 을 읽기 전용으로 마운트해 `--load-extension` 으로 올린다. 저장소를 갱신하면 크롬 재시작(`docker compose restart`)으로 반영.
- 크로미움 빌드가 `--load-extension` 을 거부하면 `chrome://extensions` → 개발자 모드 → 압축해제된 확장 로드 → `/extension`. 프로필(`/config`)에 남는다.
