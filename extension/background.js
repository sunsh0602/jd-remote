/* terabox 로그인 쿠키(ndus 등)가 새로 생기면 자동으로 JD Remote 에 전송 (설정 '자동 전송' 켜짐일 때) */
importScripts('common.js');
let timer = null; let lastSent = 0;
chrome.cookies.onChanged.addListener((info) => {
  const c = info.cookie;
  if (info.removed || !JDR.LOGIN_COOKIES.includes(c.name)) return;
  if (!JDR.TERABOX_DOMAINS.some((d) => c.domain.endsWith(d))) return;
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const s = await JDR.getSettings();
    if (!s.auto || Date.now() - lastSent < 30000) return;   // 30초 내 중복 방지
    try {
      const j = await JDR.send(); lastSent = Date.now();
      const r = JDR.summarize(j);
      notify(r.ok ? 'JD Remote: TeraBox 쿠키 자동 전송됨' : 'JD Remote: 전송했지만 JD 검증 실패', r.text);
    } catch (e) {
      notify('JD Remote: 자동 전송 실패', e.message);
    }
  }, 3000);   // 로그인 직후 쿠키가 여러 개 연달아 갱신되므로 3초 모아서 1회
});
function notify(title, message) {
  chrome.notifications.create({ type: 'basic', iconUrl: 'icons/icon-128.png', title, message });
}
