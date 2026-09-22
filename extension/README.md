# JD Remote 쿠키 도우미 (크롬 확장)

terabox.com 로그인 쿠키를 읽어 JD Remote 서버(`/api/accounts/terabox/cookies`)로 보내 JDownloader의 TeraBox 계정을 갱신한다.
스토어 등록 없이 "압축해제된 확장 프로그램 로드"로 설치한다.

## 설치 (1회)
1. `chrome://extensions` → **개발자 모드** 켬 → **압축해제된 확장 프로그램을 로드합니다** → 이 `extension/` 폴더
2. 아이콘 클릭 → ⚙ 설정: **JD Remote 주소**(`https://…`)와 **TeraBox 계정 이메일** 입력 → 저장(주소 접근 권한 허용)
3. 안드로이드: Kiwi Browser / Edge(안드로이드)에 같은 방법으로 설치

## 사용
팝업이 두 가지 상태를 자동으로 보여준다:
- **TeraBox 로그인** ❌ → `로그인 열기` 버튼으로 terabox.com 탭 열어 로그인
- **JD Remote 로그인** ❌ → `열기` 버튼으로 JD Remote 탭 열어 로그인
둘 다 ✅가 되면 **전송** 버튼이 활성화된다.

**자동 전송(기본 켜짐)**: terabox.com에 로그인해서 `ndus`/`BDUSS` 쿠키가 새로 생기면 백그라운드가 3초 뒤 자동으로 전송하고 알림을 띄운다. 즉 확장 설정만 해두면 *terabox 로그인 = 계정 갱신*. 설정에서 끌 수 있다.

## 동작/보안
- 읽는 쿠키: `terabox.com`/`1024terabox.com`/`terabox.app`/`teraboxapp.com` 만. 전송 대상: 설정한 JD Remote 서버만. 확장이 저장하는 것: 서버 주소·이메일·자동전송 여부(`chrome.storage.sync`).
- JD Remote 인증: 그 사이트의 세션 쿠키(`jdr_session`)를 읽어 `X-JDR-Session` 헤더로 전달(확장→서버 요청엔 SameSite로 쿠키가 안 붙을 수 있어서).
- JD terabox 플러그인이 아이디를 이메일 형식으로 요구하므로 이메일은 필수.
- 서버 주소는 manifest에 없고 `optional_host_permissions`로 런타임에 권한을 요청한다.
