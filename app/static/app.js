/* jd-remote 프론트 — 단일 페이지, 빌드 없음. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  // ── 상태 ────────────────────────────────────────────────────────────
  const LS = {
    get(k, d) { try { const v = localStorage.getItem('jdr.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('jdr.' + k, JSON.stringify(v)); } catch (e) {} },
  };
  const state = {
    data: null, filter: LS.get('filter', 'all'), tab: LS.get('tab', 'downloads'), downloadRoot: '',
    pollMs: LS.get('pollMs', 3000),
    clipboard: LS.get('clipboard', true), lastClip: LS.get('lastClip', ''),
    speeds: new Array(60).fill(0), timer: null, inflight: false, expanded: new Set(), openPkg: null,
  };

  // ── 유틸 ────────────────────────────────────────────────────────────
  const fmtBytes = (n) => { if (!n && n !== 0) return '—'; const u = ['B','KB','MB','GB','TB']; let i = 0; let v = n; while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; } return (i === 0 ? v : v.toFixed(v >= 100 ? 0 : 1)) + ' ' + u[i]; };
  const fmtSpeed = (n) => n > 0 ? fmtBytes(n) + '/s' : '0 B/s';
  const fmtEta = (s) => { if (s == null || s < 0) return ''; if (s < 60) return s + '초'; if (s < 3600) return Math.round(s / 60) + '분'; return Math.floor(s / 3600) + '시간 ' + Math.round((s % 3600) / 60) + '분'; };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const URL_RE = /https?:\/\/[^\s<>"'()\[\]{}]+/gi;
  const extractUrls = (t) => [...new Set((t.match(URL_RE) || []).map((u) => u.replace(/[.,;:!?]+$/, '')))];

  let toastT;
  const toast = (msg, bad = false) => { const el = $('#toast'); el.textContent = msg; el.className = 'toast' + (bad ? ' bad' : ''); el.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => (el.hidden = true), 2600); };
  const vibrate = (ms = 10) => { try { navigator.vibrate && navigator.vibrate(ms); } catch (e) {} };

  async function api(path, opts = {}) {
    const r = await fetch('/api' + path, { headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search); throw new Error('401'); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    return j;
  }
  const act = async (label, fn) => { try { vibrate(); const r = await fn(); toast(label); refresh(); return r; } catch (e) { toast(label + ' 실패: ' + e.message, true); } };

  // ── 탭 ─────────────────────────────────────────────────────────────
  function showTab(name) {
    state.tab = name; LS.set('tab', name);
    $$('.tab').forEach((t) => t.classList.toggle('active', t.id === 'tab-' + name));
    $$('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
    window.scrollTo(0, 0);
    if (name === 'accounts') loadAccounts();
  }
  $$('.tab-btn').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));

  // ── 렌더: 상단 ─────────────────────────────────────────────────────
  function drawSpark() {
    const c = $('#spark'); const ctx = c.getContext('2d'); const w = c.width, h = c.height;
    ctx.clearRect(0, 0, w, h);
    const max = Math.max(...state.speeds, 1);
    ctx.beginPath();
    state.speeds.forEach((v, i) => { const x = (i / (state.speeds.length - 1)) * w; const y = h - 2 - (v / max) * (h - 4); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#4f8cff';
    ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.stroke();
  }
  function renderTop(d) {
    const dot = $('#connDot'); const jd = d.jd || {};
    dot.className = 'dot ' + (!jd.connected ? 'off' : jd.state === 'RUNNING' ? 'on' : jd.state && jd.state.includes('PAUSE') ? 'busy' : 'on');
    $('#speedText').textContent = jd.connected ? fmtSpeed(jd.speed || 0) : '연결 끊김';
    state.speeds.push(jd.speed || 0); state.speeds.shift(); drawSpark();
    const run = $('#btnToggleRun');
    if (!jd.connected) { run.textContent = '—'; run.dataset.state = ''; }
    else if (jd.state === 'RUNNING') { run.textContent = '⏸ 일시정지'; run.dataset.state = 'pause'; }
    else if ((jd.state || '').includes('PAUSE')) { run.textContent = '▶ 재개'; run.dataset.state = 'resume'; }
    else { run.textContent = '▶ 시작'; run.dataset.state = 'start'; }
    const sl = jd.speedlimit || {}; const slBtn = $('#btnSpeedLimit');
    $('#speedLimitText').textContent = sl.enabled ? (sl.limit / 1048576).toFixed(sl.limit % 1048576 ? 1 : 0) + ' MB/s' : '무제한';
    slBtn.classList.toggle('on', !!sl.enabled);
    // 배너
    const b = [];
    if (!jd.connected) b.push({ k: 'bad', t: 'JDownloader에 연결할 수 없습니다. ' + (jd.hint ? 'NAS에서: ' + jd.hint : ''), a: null });
    if (jd.captchas > 0) b.push({ k: 'warn', t: `캡차 ${jd.captchas}개가 입력을 기다립니다`, a: { href: jd.novncUrl, label: 'JD 화면 열기' } });
    if (jd.accountAlert) b.push({ k: 'bad', t: jd.accountAlert + ' — 계정 탭에서 쿠키를 갱신하세요', a: { onclick: () => showTab('accounts'), label: '계정 탭' } });
    if (d.linkgrabber && d.linkgrabber.collecting) b.push({ k: 'info', t: '링크를 확인하는 중…', a: null });
    const failed = (d.packages || []).filter((p) => p.kind === 'failed').length;
    if (failed) b.push({ k: 'warn', t: `실패한 패키지 ${failed}개`, a: { onclick: () => { setFilter('failed'); showTab('downloads'); }, label: '보기' } });
    $('#banners').innerHTML = b.map((x, i) => `<div class="banner ${x.k}"><span class="txt">${esc(x.t)}</span>${x.a ? (x.a.href ? `<a class="btn small" target="_blank" rel="noopener" href="${esc(x.a.href)}">${esc(x.a.label)}</a>` : `<button class="btn small" data-banner="${i}">${esc(x.a.label)}</button>`) : ''}</div>`).join('');
    $$('[data-banner]').forEach((el) => el.addEventListener('click', () => b[+el.dataset.banner].a.onclick()));
    $('#linkNovnc').hidden = !jd.novncUrl; if (jd.novncUrl) $('#linkNovnc').href = jd.novncUrl;
    $('#linkDsm').hidden = !jd.dsmUrl; if (jd.dsmUrl) $('#linkDsm').href = jd.dsmUrl;
    if (jd.downloadRoot) { $('#dlRootText').textContent = '다운로드 폴더: ' + jd.downloadRoot; setDownloadRoot(jd.downloadRoot); }
    if (jd.pollMs && !LS.get('pollMs', null)) state.pollMs = jd.pollMs;
  }

  // ── 렌더: 다운로드 목록 ───────────────────────────────────────────────
  function setFilter(f) { state.filter = f; LS.set('filter', f); $$('#filterChips .chip').forEach((c) => c.classList.toggle('active', c.dataset.f === f)); renderPackages(); }
  $$('#filterChips .chip').forEach((c) => c.addEventListener('click', () => setFilter(c.dataset.f)));

  function pkgCard(p) {
    const pct = Math.round((p.progress || 0) * 100);
    const meta = [];
    if (p.kind === 'running') meta.push(`<b>${fmtSpeed(p.speed)}</b>`, p.eta != null ? `남은 ${fmtEta(p.eta)}` : '');
    meta.push(`${fmtBytes(p.bytesLoaded)} / ${fmtBytes(p.bytesTotal)}`);
    if (p.childCount > 1) meta.push(`${p.childCount}개 파일`);
    if (p.hosts && p.hosts.length) meta.push(esc(p.hosts.slice(0, 2).join(', ')));
    const label = { running: '진행중', finished: '완료', failed: '실패', paused: '일시정지', waiting: '대기' }[p.kind] || p.kind;
    return `<li class="card-wrap" data-uuid="${p.uuid}">
      <div class="card-bg"><span class="l">${p.enabled ? '⏸ 일시정지' : '▶ 재개'}</span><span class="r">🗑 삭제</span></div>
      <div class="card ${p.kind}" data-uuid="${p.uuid}">
        <div class="title"><span class="name">${esc(p.name)}</span><span class="tag ${p.kind}">${label}${p.status && p.kind === 'failed' ? ' · ' + esc(p.status) : ''}</span></div>
        <div class="bar"><i style="width:${pct}%"></i></div>
        <div class="meta"><span>${pct}%</span>${meta.filter(Boolean).map((m) => `<span>${m}</span>`).join('')}</div>
      </div></li>`;
  }
  function renderPackages() {
    const d = state.data; if (!d) return;
    let pk = d.packages || [];
    $('#badgeDl').hidden = !pk.some((p) => p.kind === 'running');
    $('#badgeDl').textContent = pk.filter((p) => p.kind === 'running').length;
    if (state.filter !== 'all') pk = pk.filter((p) => p.kind === state.filter);
    const order = { running: 0, waiting: 1, failed: 2, paused: 3, finished: 4 };
    pk = [...pk].sort((a, b) => (order[a.kind] != null ? order[a.kind] : 9) - (order[b.kind] != null ? order[b.kind] : 9));
    $('#pkgList').innerHTML = pk.map(pkgCard).join('');
    $('#pkgEmpty').hidden = pk.length > 0;
    $$('#pkgList .card-wrap').forEach(attachSwipe);
  }

  // ── 스와이프 ─────────────────────────────────────────────────────────
  function attachSwipe(wrap) {
    const card = $('.card', wrap); let x0 = 0, y0 = 0, dx = 0, active = false, moved = false;
    card.addEventListener('pointerdown', (e) => { x0 = e.clientX; y0 = e.clientY; dx = 0; active = true; moved = false; card.classList.add('dragging'); });
    card.addEventListener('pointermove', (e) => {
      if (!active) return; const mx = e.clientX - x0, my = e.clientY - y0;
      if (!moved && Math.abs(my) > Math.abs(mx)) { active = false; card.classList.remove('dragging'); return; }
      if (Math.abs(mx) > 8) { moved = true; card.setPointerCapture(e.pointerId); }
      if (moved) { dx = Math.max(-140, Math.min(140, mx)); card.style.transform = `translateX(${dx}px)`; }
    });
    const end = () => {
      if (!active) return; active = false; card.classList.remove('dragging'); card.style.transform = '';
      const uuid = +card.dataset.uuid; const p = (state.data.packages || []).find((q) => q.uuid === uuid);
      if (!p) return;
      if (dx <= -80) confirmRemove(p);
      else if (dx >= 80) act(p.enabled ? '일시정지' : '재개', () => api(`/packages/${uuid}/${p.enabled ? 'pause' : 'resume'}`, { method: 'POST' }));
      else if (!moved) openPkgSheet(p);
      dx = 0;
    };
    card.addEventListener('pointerup', end); card.addEventListener('pointercancel', end);
  }

  function confirmRemove(p) {
    openPkgSheet(p, true);
  }

  // ── 패키지 시트 ───────────────────────────────────────────────────────
  async function openPkgSheet(p, removeMode = false) {
    state.openPkg = p.uuid;
    $('#pkgSheetTitle').textContent = p.name;
    $('#pkgSheetPath').textContent = p.path || '';
    const A = [];
    if (!removeMode) {
      A.push(p.enabled ? ['⏸ 일시정지', () => api(`/packages/${p.uuid}/pause`, { method: 'POST' })] : ['▶ 재개', () => api(`/packages/${p.uuid}/resume`, { method: 'POST' })]);
      if (p.kind === 'failed' || p.kind === 'paused') A.push(['🔁 재시도', () => api('/links/retry', { method: 'POST', body: { packageIds: [p.uuid] } })]);
      if (p.path) A.push(['📋 경로 복사', async () => { await navigator.clipboard.writeText(p.path); }]);
      if (state.data.jd.dsmUrl) A.push(['🗂 DSM 열기', async () => { window.open(state.data.jd.dsmUrl + '/?launchApp=SYNO.SDS.App.FileStation3.Instance', '_blank'); }]);
    }
    A.push(['🗑 목록에서 제거', () => api('/packages/remove', { method: 'POST', body: { packageIds: [p.uuid], deleteFiles: false } }), true]);
    A.push(['❌ 파일까지 삭제', async () => { if (!confirm(`"${p.name}"\n파일까지 완전히 삭제할까요?`)) throw new Error('취소'); return api('/packages/remove', { method: 'POST', body: { packageIds: [p.uuid], deleteFiles: true } }); }, true]);
    $('#pkgSheetActions').innerHTML = A.map(([l, , danger], i) => `<button class="btn ${danger ? 'danger' : ''}" data-i="${i}">${l}</button>`).join('');
    $$('#pkgSheetActions .btn').forEach((b) => b.addEventListener('click', async () => { const [l, fn] = A[+b.dataset.i]; closeSheets(); await act(l, fn); }));
    $('#pkgSheetLinks').innerHTML = '<li class="muted small">파일 목록 불러오는 중…</li>';
    $('#pkgSheet').hidden = false;
    try {
      const links = await api(`/packages/${p.uuid}/links`);
      if (state.openPkg !== p.uuid) return;
      $('#pkgSheetLinks').innerHTML = links.map((l) => `<li class="card ${l.finished ? 'finished' : ''}"><div class="title"><span class="name">${esc(l.name)}</span><span class="tag">${esc(l.status || (l.finished ? '완료' : ''))}</span></div>
        <div class="bar"><i style="width:${l.bytesTotal ? Math.round(l.bytesLoaded / l.bytesTotal * 100) : (l.finished ? 100 : 0)}%"></i></div>
        <div class="meta"><span>${fmtBytes(l.bytesLoaded)} / ${fmtBytes(l.bytesTotal)}</span><span>${esc(l.host || '')}</span>${l.speed ? `<span><b>${fmtSpeed(l.speed)}</b></span>` : ''}</div></li>`).join('') || '<li class="muted small">파일 없음</li>';
    } catch (e) { $('#pkgSheetLinks').innerHTML = `<li class="muted small">불러오기 실패: ${esc(e.message)}</li>`; }
  }

  // ── 렌더: 확인 대기(JD 링크그래버) ───────────────────────────────────
  function renderGrabber() {
    const g = (state.data || {}).linkgrabber; const ul = $('#grabList');
    if (!g) { ul.innerHTML = ''; $('#grabEmpty').hidden = false; $('#badgeGrab').hidden = true; return; }
    const links = g.links || [];
    $('#badgeGrab').hidden = links.length === 0; $('#badgeGrab').textContent = links.length;
    $('#grabberInfo').textContent = links.length ? `${links.length}개 링크 · ${fmtBytes(links.reduce((a, l) => a + (l.bytesTotal || 0), 0))}` : '확인 대기 중인 링크';
    const byPkg = new Map();
    links.forEach((l) => { const k = l.packageUUID; if (!byPkg.has(k)) byPkg.set(k, []); byPkg.get(k).push(l); });
    const pkgs = new Map((g.packages || []).map((p) => [p.uuid, p]));
    ul.innerHTML = [...byPkg.entries()].map(([pid, ls]) => {
      const p = pkgs.get(pid) || { name: '(패키지)', path: '' };
      const online = ls.filter((l) => (l.availability || '').toUpperCase() === 'ONLINE').length;
      return `<li class="card"><div class="title"><span class="name">${esc(p.name)}</span><span class="tag ${online === ls.length ? 'ONLINE' : online ? 'TEMP_UNKNOWN' : 'OFFLINE'}">${online}/${ls.length} 온라인</span></div>
        <div class="meta"><span>${fmtBytes(ls.reduce((a, l) => a + (l.bytesTotal || 0), 0))}</span><span>${esc(p.path || '')}</span></div>
        <ul class="list sub">${ls.map((l) => `<li class="meta"><span class="tag ${esc(l.availability || '')}">${esc(l.availability || '?')}</span><span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(l.name)}</span><span>${fmtBytes(l.bytesTotal)}</span></li>`).join('')}</ul>
        <div class="row-actions"><button class="btn small primary" data-gstart="${pid}">▶ 이 패키지 시작</button><button class="btn small danger" data-gremove="${pid}">삭제</button></div></li>`;
    }).join('');
    $('#grabEmpty').hidden = links.length > 0;
    $$('[data-gstart]').forEach((b) => b.addEventListener('click', () => act('다운로드 시작', () => api('/linkgrabber/start', { method: 'POST', body: { packageIds: [+b.dataset.gstart] } }))));
    $$('[data-gremove]').forEach((b) => b.addEventListener('click', () => act('삭제', () => api('/linkgrabber/remove', { method: 'POST', body: { packageIds: [+b.dataset.gremove] } }))));
  }
  $('#btnStartAll').addEventListener('click', () => act('전체 다운로드 시작', () => api('/linkgrabber/start', { method: 'POST', body: {} })));
  $('#btnClearOffline').addEventListener('click', () => act('오프라인 링크 제거', () => api('/linkgrabber/clear-offline', { method: 'POST' })));

  // ── 추가 탭 ───────────────────────────────────────────────────────────
  const addText = $('#addText');
  const updateCount = () => { const n = extractUrls(addText.value).length; $('#urlCount').textContent = n ? `URL ${n}개` : ''; $('#btnAdd').disabled = !n; };
  addText.addEventListener('input', updateCount);
  $('#btnClearText').addEventListener('click', () => { addText.value = ''; updateCount(); });
  $('#btnPaste').addEventListener('click', async () => { try { const t = await navigator.clipboard.readText(); if (!extractUrls(t).length) return toast('클립보드에 URL이 없습니다', true); addText.value = (addText.value ? addText.value + '\n' : '') + t; updateCount(); } catch (e) { toast('클립보드를 읽을 수 없습니다 (권한)', true); } });
  // 저장 위치: 설정에 보이는 다운로드 폴더를 기본값으로 채운다. 사용자가 고친 값은 건드리지 않는다.
  function setDownloadRoot(root) {
    const el = $('#addFolder'), prev = state.downloadRoot;
    state.downloadRoot = root;
    if (!el.value || el.value === prev) el.value = root;
  }
  $('#addAutostart').checked = LS.get('autostart', false);
  $('#addAutostart').addEventListener('change', (e) => LS.set('autostart', e.target.checked));
  $('#addForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = { text: addText.value, destFolder: $('#addFolder').value.trim() || null, autostart: $('#addAutostart').checked };
    const r = await act('링크 추가', () => api('/links', { method: 'POST', body }));
    if (r) { addText.value = ''; $('#addFolder').value = state.downloadRoot; updateCount(); showTab(body.autostart ? 'downloads' : 'grabber'); }
  });
  // share_target / ?add= 진입
  const params = new URLSearchParams(location.search);
  if (params.get('add')) { addText.value = params.get('add'); updateCount(); showTab('add'); history.replaceState(null, '', '/'); toast('공유된 링크를 확인 후 추가하세요'); }

  // 클립보드 감지(포커스 시). readText 는 사용자 제스처가 필요할 수 있어 실패하면 조용히 넘어간다.
  async function checkClipboard() {
    if (!state.clipboard || !navigator.clipboard || !navigator.clipboard.readText) return;
    try {
      const t = await navigator.clipboard.readText(); const urls = extractUrls(t);
      if (!urls.length || t === state.lastClip) return;
      state.lastClip = t; LS.set('lastClip', t);
      const el = document.createElement('div'); el.className = 'banner info';
      el.innerHTML = `<span class="txt">클립보드에 URL ${urls.length}개 — 추가할까요?</span><button class="btn small primary">추가 탭에 넣기</button><button class="icon-btn">✕</button>`;
      $('.btn', el).onclick = () => { addText.value = urls.join('\n'); updateCount(); showTab('add'); el.remove(); };
      $('.icon-btn', el).onclick = () => el.remove();
      $('#banners').prepend(el);
    } catch (e) {}
  }

  // ── 계정 탭 ───────────────────────────────────────────────────────────
  let accounts = [];
  const fmtDate = (ms) => (ms && ms > 0) ? new Date(ms).toLocaleDateString('ko-KR') : (ms === -1 ? '무기한' : '—');
  async function loadAccounts() {
    try { accounts = await api('/accounts'); } catch (e) { $('#acctList').innerHTML = `<li class="muted small">불러오기 실패: ${esc(e.message)}</li>`; return; }
    const bad = accounts.filter((a) => a.enabled && (a.valid === false || a.error)).length;
    $('#badgeAcct').hidden = !bad; $('#badgeAcct').textContent = bad;
    $('#acctInfo').textContent = accounts.length ? `계정 ${accounts.length}개` : '호스터 계정';
    $('#acctEmpty').hidden = accounts.length > 0;
    $('#acctList').innerHTML = accounts.map((a) => {
      const pending = a.enabled && a.valid == null && !a.error;   // JD가 아직 검증 안 함
      const st = !a.enabled ? ['paused', '사용 안 함'] : a.error ? ['failed', esc(a.error)] : a.valid === false ? ['failed', '오류'] : pending ? ['waiting', '확인 중'] : ['finished', '정상'];
      const meta = [];
      if (!pending && a.valid) {
        meta.push(`다음 확인 <b>${a.validUntil > 0 ? fmtDate(a.validUntil) : '정보 없음'}</b>`);
        if (a.trafficMax > 0) meta.push(`트래픽 ${fmtBytes(a.trafficLeft)} / ${fmtBytes(a.trafficMax)}`);
        else if (a.trafficLeft === -1) meta.push('트래픽 무제한');
      } else if (pending) meta.push('JD가 계정을 확인하는 중입니다 — ↻ 갱신을 눌러 재검증');
      const label = a.username ? esc(a.username) : '<i>라벨 없음</i>';
      return `<li class="card ${st[0]}" data-acct="${a.uuid}"><div class="title"><span class="name">${esc(a.hostname)}<br><span class="muted small">${label}</span></span><span class="tag ${st[0]}">${st[1]}</span></div>
        <div class="meta">${meta.map((m) => `<span>${m}</span>`).join('')}</div></li>`;
    }).join('');
    $$('#acctList .card').forEach((c) => c.addEventListener('click', () => openAcctSheet(accounts.find((a) => a.uuid === +c.dataset.acct))));
  }
  function openAcctSheet(a) {
    if (!a) return;
    $('#acctSheetTitle').textContent = a.hostname; $('#acctSheetInfo').textContent = `${a.username || ''} · 다음 확인 ${fmtDate(a.validUntil)}${a.error ? ' · ' + a.error : ''}`;
    $('#acctEditUser').value = a.username || ''; $('#acctEditPass').value = ''; $('#acctEditCookie').value = ''; setCookieMode(isCookieHoster(a.hostname), 'acctEdit', isCookieHoster(a.hostname));
    const A = [
      [a.enabled ? '⏸ 사용 안 함' : '▶ 사용', () => api(`/accounts/${a.uuid}/${a.enabled ? 'disable' : 'enable'}`, { method: 'POST' })],
      ['🗑 계정 삭제', async () => { if (!confirm(`${a.hostname} (${a.username}) 계정을 삭제할까요?`)) throw new Error('취소'); return api('/accounts/remove', { method: 'POST', body: { ids: [a.uuid] } }); }, true],
    ];
    $('#acctSheetActions').innerHTML = A.map(([l, , d], i) => `<button class="btn ${d ? 'danger' : ''}" data-i="${i}">${l}</button>`).join('');
    $$('#acctSheetActions .btn').forEach((b) => b.addEventListener('click', async () => { const [l, fn] = A[+b.dataset.i]; closeSheets(); await act(l, fn); loadAccounts(); }));
    $('#acctEditForm').onsubmit = async (e) => { e.preventDefault(); const u = $('#acctEditUser').value.trim(), pw = $('#acctEditCookieMode').checked ? $('#acctEditCookie').value.trim() : $('#acctEditPass').value; if (!u || !pw) return toast('라벨(아이디)과 함께 쿠키(또는 비밀번호)도 넣어야 합니다 — JD가 둘을 같이 받습니다', true); closeSheets(); await act('계정 변경', () => api(`/accounts/${a.uuid}`, { method: 'PUT', body: { username: u, password: pw } })); loadAccounts(); };
    $('#acctSheet').hidden = false;
  }
  // 쿠키 로그인 호스터(비밀번호 자리에 브라우저 쿠키를 넣는 JD 플러그인들) — 도메인 입력 시 자동으로 쿠키 모드
  const COOKIE_HOSTERS = ['terabox', '1024tera', 'nephobox', 'mirrobox', 'momerybox', 'teraboxapp', '4funbox', 'freeterabox'];
  const isCookieHoster = (h) => COOKIE_HOSTERS.some((k) => (h || '').toLowerCase().includes(k));
  // forced=true: 쿠키 전용 호스터 — 비밀번호 로그인은 JD 플러그인이 지원하지 않으므로 선택지 자체를 잠근다
  const setCookieMode = (on, prefix, forced = false) => {
    $(`#${prefix}PassField`).hidden = on; $(`#${prefix}CookieField`).hidden = !on;
    const cb = $(`#${prefix}CookieMode`); cb.checked = on; cb.disabled = forced;
    cb.closest('label').hidden = forced; // 쿠키 전용이면 선택지 자체를 숨김
    const note = $(`#${prefix}CookieNote`); if (note) note.hidden = !forced;
  };
  $('#acctCookieMode').addEventListener('change', (e) => setCookieMode(e.target.checked, 'acct'));
  $('#acctEditCookieMode').addEventListener('change', (e) => setCookieMode(e.target.checked, 'acctEdit'));
  const onHostInput = (e) => { const f = isCookieHoster(e.target.value); if (f) setCookieMode(true, 'acct', true); else if ($('#acctCookieMode').disabled) setCookieMode(false, 'acct', false); };
  $('#acctHost').addEventListener('change', onHostInput);
  onHostInput({ target: $('#acctHost') }); // 초기 선택(terabox)에 맞춰 쿠키 모드 잠금
  $('#acctForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const cookieMode = $('#acctCookieMode').checked;
    const secret = cookieMode ? $('#acctCookie').value.trim() : $('#acctPass').value;
    const body = { hostname: $('#acctHost').value.trim(), username: $('#acctUser').value.trim(), password: secret };
    if (!body.hostname || !body.username || !body.password) return toast(cookieMode ? '호스터·아이디·쿠키를 모두 입력하세요' : '호스터·아이디·비밀번호를 모두 입력하세요', true);
    const r = await act('계정 추가', () => api('/accounts', { method: 'POST', body }));
    if (r) { $('#acctUser').value = ''; $('#acctPass').value = ''; $('#acctCookie').value = ''; onHostInput({ target: $('#acctHost') }); setTimeout(loadAccounts, 1500); toast('JD가 계정을 확인하는 중 — 잠시 후 상태를 확인하세요'); }
  });

  // ── 상단 버튼 ─────────────────────────────────────────────────────────
  $('#btnToggleRun').addEventListener('click', (e) => { const s = e.currentTarget.dataset.state; if (!s) return; act({ pause: '일시정지', resume: '재개', start: '시작' }[s], () => api('/control/' + s, { method: 'POST' })); });
  $('#btnCleanup').addEventListener('click', () => { const n = ((state.data && state.data.packages) || []).filter((p) => p.kind === 'finished').length; if (!n) return toast('완료된 항목이 없습니다'); if (confirm(`완료된 ${n}개를 목록에서 정리할까요? (파일은 유지)`)) act('완료 정리', () => api('/cleanup-finished', { method: 'POST' })); });
  $('#btnSpeedLimit').addEventListener('click', () => { const sl = (state.data && state.data.jd && state.data.jd.speedlimit) || {}; $('#spEnabled').checked = !!sl.enabled; $('#spValue').value = sl.limit ? +(sl.limit / 1048576).toFixed(1) : 5; $('#speedSheet').hidden = false; });
  $$('#spPresets .chip').forEach((c) => c.addEventListener('click', () => { $('#spValue').value = c.dataset.mb; $('#spEnabled').checked = true; }));
  $('#spApply').addEventListener('click', () => { const mb = parseFloat($('#spValue').value); closeSheets(); act('속도 제한 적용', () => api('/speedlimit', { method: 'PUT', body: { enabled: $('#spEnabled').checked, limit: mb > 0 ? Math.round(mb * 1048576) : null } })); });

  // ── 설정 시트 ─────────────────────────────────────────────────────────
  $('#settingsBtn').addEventListener('click', () => { $('#setPoll').value = Math.round(state.pollMs / 1000); $('#setClipboard').checked = state.clipboard; $('#settingsSheet').hidden = false; });
  $('#setPoll').addEventListener('change', (e) => { state.pollMs = Math.max(1, Math.min(60, +e.target.value || 3)) * 1000; LS.set('pollMs', state.pollMs); schedule(); });
  $('#setClipboard').addEventListener('change', (e) => { state.clipboard = e.target.checked; LS.set('clipboard', state.clipboard); });
  function closeSheets() { $$('.sheet').forEach((s) => (s.hidden = true)); state.openPkg = null; }
  $$('.sheet').forEach((s) => { s.addEventListener('click', (e) => { if (e.target === s) closeSheets(); }); $$('[data-close]', s).forEach((b) => b.addEventListener('click', closeSheets)); });

  // ── 폴링 / 새로고침 ────────────────────────────────────────────────────
  async function refresh() {
    if (state.inflight) return; state.inflight = true;
    try { state.data = await api('/state'); renderTop(state.data); renderPackages(); renderGrabber(); }
    catch (e) { if (e.message !== '401') { $('#connDot').className = 'dot off'; $('#speedText').textContent = '오류'; } }
    finally { state.inflight = false; }
  }
  function schedule() { clearInterval(state.timer); state.timer = setInterval(() => { if (!document.hidden) refresh(); }, state.pollMs); }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { refresh(); checkClipboard(); } });
  window.addEventListener('focus', () => checkClipboard());

  // 당겨서 새로고침(스크롤 최상단에서 아래로 60px)
  let py0 = null; const hint = $('#pullHint');
  document.addEventListener('touchstart', (e) => { py0 = window.scrollY <= 0 ? e.touches[0].clientY : null; }, { passive: true });
  document.addEventListener('touchmove', (e) => { if (py0 == null) return; hint.classList.toggle('show', e.touches[0].clientY - py0 > 60); }, { passive: true });
  document.addEventListener('touchend', (e) => { if (py0 != null && hint.classList.contains('show')) { refresh(); toast('새로 고침'); } hint.classList.remove('show'); py0 = null; });

  // ── 시작 ─────────────────────────────────────────────────────────────
  updateCount(); showTab(state.tab); setFilter(state.filter); refresh(); schedule();
  setTimeout(checkClipboard, 400);
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
})();
