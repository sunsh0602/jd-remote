(() => {
  const $ = (s) => document.querySelector(s);
  const status = (msg, cls = '') => { const el = $('#status'); el.textContent = msg; el.className = cls; };
  const setStep = (id, ok, text, btn) => { const el = $(id); el.className = 'step ' + (ok ? 'ok' : 'bad'); el.querySelector('.ic').textContent = ok ? '✅' : '❌'; el.querySelector('span').textContent = text; if (btn) btn.hidden = ok; };
  const normServer = (v) => { v = (v || '').trim().replace(/\/+$/, ''); if (v && !/^https?:\/\//.test(v)) v = 'https://' + v; return v; };

  async function refresh() {
    const st = await JDR.check();
    $('#server').value = st.settings.server; $('#label').value = st.settings.label; $('#auto').checked = st.settings.auto;
    const needSetup = !st.serverOk || !st.labelOk;
    $('#settings').hidden = !needSetup && $('#settings').dataset.open !== '1';
    setStep('#stTerabox', st.teraboxOk, st.teraboxOk ? '로그인됨 — 쿠키 준비됨' : '로그인되어 있지 않음', $('#openTerabox'));
    setStep('#stJdr', st.jdrOk, st.jdrOk ? '로그인됨' : (st.serverOk ? '로그인되어 있지 않음' : '설정에서 주소를 먼저 저장하세요'), $('#openJdr'));
    $('#openJdr').hidden = st.jdrOk || !st.serverOk;
    $('#send').disabled = !(st.teraboxOk && st.jdrOk && !needSetup);
    if (needSetup) status('⚙ 아래 설정에서 JD Remote 주소와 TeraBox 이메일을 저장하세요', 'muted');
    else if ($('#send').disabled) status('');
    return st;
  }

  $('#gear').addEventListener('click', () => { const s = $('#settings'); s.dataset.open = s.hidden ? '1' : '0'; s.hidden = !s.hidden; });
  $('#openTerabox').addEventListener('click', () => chrome.tabs.create({ url: 'https://www.terabox.com/' }));
  $('#openJdr').addEventListener('click', async () => { const s = await JDR.getSettings(); chrome.tabs.create({ url: s.server + '/' }); });

  $('#save').addEventListener('click', async () => {
    const server = normServer($('#server').value); const label = $('#label').value.trim();
    if (!/^https:\/\/.+/.test(server)) return status('주소는 https:// 로 시작해야 합니다', 'bad');
    if (!JDR.EMAIL_RE.test(label)) return status('TeraBox 계정 이메일을 입력하세요', 'bad');
    const granted = await chrome.permissions.request({ origins: [new URL(server).origin + '/*'] });
    if (!granted) return status('JD Remote 주소 접근 권한이 거부되었습니다', 'bad');
    await JDR.setSettings({ server, label, auto: $('#auto').checked });
    $('#settings').dataset.open = '0'; status('저장됨', 'ok'); refresh();
  });
  $('#auto').addEventListener('change', (e) => JDR.setSettings({ auto: e.target.checked }));

  $('#send').addEventListener('click', async () => {
    $('#send').disabled = true; status('전송 중…');
    try { const r = JDR.summarize(await JDR.send()); status((r.ok ? '✓ ' : '⚠ ') + r.text, r.ok ? 'ok' : 'bad'); }
    catch (e) { status('실패: ' + e.message, 'bad'); }
    finally { $('#send').disabled = false; }
  });

  refresh();
  // 팝업이 열린 동안 다른 탭에서 로그인하면 상태 갱신
  chrome.cookies.onChanged.addListener(() => { clearTimeout(refresh._t); refresh._t = setTimeout(refresh, 800); });
})();
