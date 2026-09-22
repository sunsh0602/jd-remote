# JD Remote 쿠키 도우미 (크롬 확장)

terabox.com 로그인 쿠키를 읽어 JD Remote 서버(`/api/accounts/terabox/cookies`)로 보내 JDownloader의 TeraBox 계정을 갱신한다.
스토어 등록 없이 "압축해제된 확장 프로그램 로드"로 설치한다.

## 설치
1. `chrome://extensions` → 우측 상단 **개발자 모드** 켬 → **압축해제된 확장 프로그램을 로드합니다** → 이 `extension/` 폴더 선택
2. 확장 아이콘 클릭 → **JD Remote 주소** 입력(예: `https://jdr.example.com`) → **주소 저장** (해당 주소 접근 권한 요청이 뜨면 허용)
3. 안드로이드: Kiwi Browser 또는 Edge(안드로이드)에 같은 방법으로 설치(폴더 대신 zip 로드)

## 사용
1. 같은 브라우저에서 `terabox.com` 로그인
2. JD Remote에도 로그인된 상태(세션 쿠키 필요)
3. 아이콘 클릭 → **terabox.com 쿠키 → JD Remote 전송**
→ 서버가 JD `updateAccount`(기존 계정) 또는 `addAccount`로 반영하고 결과(정상/오류)를 표시. JD Remote가 잠금 상태였다면 자동 해제.

## 동작/보안
- 쿠키는 `terabox.com`/`1024terabox.com`/`terabox.app`/`teraboxapp.com` 도메인만 읽고, 설정한 JD Remote 서버로만 전송. 확장 자체는 저장하지 않음(서버 주소만 `chrome.storage.sync`).
- JD Remote 인증은 그 사이트의 세션 쿠키(`jdr_session`)를 읽어 `X-JDR-Session` 헤더로 전달 — 확장→서버 요청에는 SameSite 정책으로 쿠키가 안 붙을 수 있어서.
- 서버 주소는 manifest에 하드코딩하지 않고 `optional_host_permissions` 로 런타임에 권한을 요청한다.
