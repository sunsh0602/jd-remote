/* popup.js 와 background.js 가 함께 쓰는 로직 (SW 에서는 importScripts, popup 에서는 <script>) */
const JDR = (() => {
  const TERABOX_DOMAINS = ['terabox.com', '1024terabox.com', 'terabox.app', 'teraboxapp.com'];
  const LOGIN_COOKIES = ['ndus', 'BDUSS', 'STOKEN'];
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  const getSettings = () => new Promise((res) => chrome.storage.sync.get(['server', 'label', 'auto'], (v) => res({ server: v.server || '', label: v.label || '', auto: v.auto !== false })));
  const setSettings = (obj) => new Promise((res) => chrome.storage.sync.set(obj, res));

  const toExport = (c) => ({
    domain: c.domain, expirationDate: c.expirationDate, hostOnly: c.hostOnly, httpOnly: c.httpOnly,
    name: c.name, path: c.path, sameSite: c.sameSite || 'unspecified', secure: c.secure, session: c.session,
    storeId: c.storeId || '0', value: c.value,
  });

  async function teraboxCookies() {
    const all = [];
    for (const d of TERABOX_DOMAINS) all.push(...await chrome.cookies.getAll({ domain: d }));
    const seen = new Set();
    return all.filter((c) => { const k = c.domain + '|' + c.path + '|' + c.name; if (seen.has(k)) return false; seen.add(k); return true; }).map(toExport);
  }
  const isLoggedIn = (cookies) => cookies.some((c) => LOGIN_COOKIES.includes(c.name) && c.value);

  async function jdrSession(server) {
    if (!server) return null;
    try { return await chrome.cookies.get({ url: server + '/', name: 'jdr_session' }); } catch (e) { return null; }
  }

  /** 상태 점검: terabox 로그인 / jdr 로그인 / 설정 유효 */
  async function check() {
    const s = await getSettings();
    const cookies = await teraboxCookies();
    const sess = await jdrSession(s.server);
    return { settings: s, cookies, teraboxOk: isLoggedIn(cookies), jdrOk: !!sess, sess, labelOk: EMAIL_RE.test(s.label), serverOk: /^https:\/\/.+/.test(s.server) };
  }

  /** 전송. 성공 시 {action, account, locked} 반환, 실패 시 throw */
  async function send() {
    const st = await check();
    if (!st.serverOk) throw new Error('JD Remote 주소를 먼저 저장하세요.');
    if (!st.labelOk) throw new Error('TeraBox 계정 이메일을 입력하세요.');
    if (!st.teraboxOk) throw new Error('terabox.com에 로그인되어 있지 않습니다.');
    if (!st.jdrOk) throw new Error('JD Remote에 로그인되어 있지 않습니다.');
    const r = await fetch(st.settings.server + '/api/accounts/terabox/cookies', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-JDR-Session': st.sess.value },
      body: JSON.stringify({ cookies: st.cookies, username: st.settings.label }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((j.detail && j.detail.reason) || j.detail || (r.status + ' ' + r.statusText));
    return j;
  }

  const summarize = (j) => { const a = j.account || {}; const bad = a.valid === false || !!a.error; return { ok: !bad, text: `${j.action === 'added' ? '계정 추가' : '쿠키 갱신'} ${bad ? '— JD 검증 실패: ' + (a.error || 'invalid') : '완료'}${a.username ? ' · ' + a.username : ''}${j.locked ? ' (잠금 유지)' : ''}` }; };

  return { TERABOX_DOMAINS, LOGIN_COOKIES, EMAIL_RE, getSettings, setSettings, teraboxCookies, isLoggedIn, jdrSession, check, send, summarize };
})();
if (typeof self !== 'undefined') self.JDR = JDR;
