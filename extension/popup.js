/* JD Remote 쿠키 도우미 — terabox 쿠키를 읽어 JD Remote 의 /api/accounts/terabox/cookies 로 보낸다. */
(() => {
  const $ = (s) => document.querySelector(s);
  const status = (msg, cls = '') => { const el = $('#status'); el.textContent = msg; el.className = cls; };
  const TERABOX_DOMAINS = ['terabox.com', '1024terabox.com', 'terabox.app', 'teraboxapp.com'];

  chrome.storage.sync.get(['server'], ({ server }) => { if (server) $('#server').value = server; });

  const normServer = () => { let v = $('#server').value.trim().replace(/\/+$/, ''); if (v && !/^https?:\/\//.test(v)) v = 'https://' + v; return v; };

  $('#save').addEventListener('click', async () => {
    const server = normServer(); if (!server) return status('주소를 입력하세요', 'bad');
    const origin = new URL(server).origin + '/*';
    const granted = await chrome.permissions.request({ origins: [origin] });
    if (!granted) return status('권한이 거부되었습니다', 'bad');
    await chrome.storage.sync.set({ server }); $('#server').value = server; status('저장됨: ' + server, 'ok');
  });

  // EditThisCookie/Cookie-Editor 와 같은 JSON 배열 형식으로 변환 (JD 가 파싱하는 형식)
  const toExport = (c) => ({
    domain: c.domain, expirationDate: c.expirationDate, hostOnly: c.hostOnly, httpOnly: c.httpOnly,
    name: c.name, path: c.path, sameSite: c.sameSite === 'no_restriction' ? 'no_restriction' : (c.sameSite || 'unspecified'),
    secure: c.secure, session: c.session, storeId: c.storeId || '0', value: c.value,
  });

  $('#send').addEventListener('click', async () => {
    const server = normServer(); if (!server) return status('먼저 JD Remote 주소를 저장하세요', 'bad');
    $('#send').disabled = true; status('쿠키 읽는 중…');
    try {
      const all = [];
      for (const d of TERABOX_DOMAINS) all.push(...await chrome.cookies.getAll({ domain: d }));
      const seen = new Set(); const cookies = all.filter((c) => { const k = c.domain + '|' + c.path + '|' + c.name; if (seen.has(k)) return false; seen.add(k); return true; }).map(toExport);
      if (!cookies.some((c) => ['ndus', 'BDUSS', 'STOKEN'].includes(c.name))) throw new Error('terabox 로그인 쿠키(ndus)가 없습니다. 이 브라우저에서 terabox.com에 먼저 로그인하세요.');
      // JD Remote 세션: 쿠키를 읽어 헤더로 전달 (확장→서버 요청은 SameSite 때문에 쿠키가 안 붙을 수 있음)
      const sess = await chrome.cookies.get({ url: server + '/', name: 'jdr_session' });
      if (!sess) throw new Error('JD Remote에 로그인되어 있지 않습니다. ' + server + ' 에 먼저 로그인하세요.');
      status(`쿠키 ${cookies.length}개 전송 중…`);
      const r = await fetch(server + '/api/accounts/terabox/cookies', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-JDR-Session': sess.value },
        body: JSON.stringify({ cookies }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail?.reason || j.detail || (r.status + ' ' + r.statusText));
      const a = j.account || {};
      status(`${j.action === 'added' ? '계정 추가' : '쿠키 갱신'} 완료 ✓\n${a.hostname || 'terabox.com'} · ${a.username || ''}\n상태: ${a.valid === false || a.error ? '오류 — ' + (a.error || 'invalid') : '정상'}${j.locked ? '\n(JD Remote 잠금이 아직 안 풀렸습니다. 몇 초 후 다시 확인)' : '\nJD Remote 잠금 해제됨'}`, a.valid === false ? 'bad' : 'ok');
    } catch (e) { status('실패: ' + e.message, 'bad'); }
    finally { $('#send').disabled = false; }
  });
})();
