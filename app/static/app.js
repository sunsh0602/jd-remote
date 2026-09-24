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
    pollMs: LS.get('pollMs', 3000), reordering: false, browserUrl: LS.get('browserUrl', ''), novncUrl: LS.get('novncUrl', ''), server: { browserUrl: '', novncUrl: '' },
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
    // 컨트롤러가 꺼져 있는데 받을 것이 있으면: 무엇을 눌러야 하는지 바로 보여준다
    if ((jd.state || '').startsWith('STOPPED') && (d.packages || []).some((p) => p.kind === 'waiting' || p.kind === 'running')) b.push({ k: 'warn', t: '다운로드가 정지되어 있습니다 — 대기 항목이 시작되지 않습니다', a: { onclick: () => act('시작', () => api('/control/start', { method: 'POST' })), label: '▶ 시작' } });
    if (jd.captchas > 0) b.push({ k: 'warn', t: `캡차 ${jd.captchas}개가 입력을 기다립니다`, a: effectiveUrl('novncUrl') ? { href: effectiveUrl('novncUrl'), label: 'JDownloader2(noVNC) 접속' } : null });
    if (jd.accountAlert) b.push({ k: 'bad', t: jd.accountAlert + ' — 계정 탭에서 쿠키를 갱신하세요', a: { onclick: () => showTab('accounts'), label: '계정 탭' } });
    if (d.linkgrabber && d.linkgrabber.collecting) b.push({ k: 'info', t: '링크를 확인하는 중…', a: null });
    const failed = (d.packages || []).filter((p) => p.kind === 'failed').length;
    if (failed) b.push({ k: 'warn', t: `실패한 패키지 ${failed}개`, a: { onclick: () => { setFilter('failed'); showTab('downloads'); }, label: '보기' } });
    $('#banners').innerHTML = b.map((x, i) => `<div class="banner ${x.k}"><span class="txt">${esc(x.t)}</span>${x.a ? (x.a.href ? `<a class="btn small" target="_blank" rel="noopener" href="${esc(x.a.href)}">${esc(x.a.label)}</a>` : `<button class="btn small" data-banner="${i}">${esc(x.a.label)}</button>`) : ''}</div>`).join('');
    $$('[data-banner]').forEach((el) => el.addEventListener('click', () => b[+el.dataset.banner].a.onclick()));
    state.server.novncUrl = jd.novncUrl || ''; state.server.browserUrl = jd.browserUrl || ''; applyLinks();
    if (jd.downloadRoot) { $('#dlRootText').textContent = '기본 다운로드 폴더: ' + jd.downloadRoot; setDownloadRoot(jd.downloadRoot); }
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
    const label = { running: '진행중', finished: '완료', failed: '실패', action: '조치 필요', paused: '일시정지', waiting: '대기' }[p.kind] || p.kind;
    const detail = p.kind === 'action' && p.reason ? ' · ' + esc(p.reason) : (p.kind === 'failed' && p.status ? ' · ' + esc(p.status) : '');
    return `<li class="card-wrap" data-uuid="${p.uuid}">
      <div class="card-bg"><span class="l">${p.enabled ? '⏸ 일시정지' : '▶ 재개'}</span><span class="r">🗑 삭제</span></div>
      <div class="card ${p.kind}" data-uuid="${p.uuid}">
        <div class="title"><span class="handle" data-handle title="끌어서 순서 변경">☰</span><span class="name">${esc(p.name)}</span><span class="tag ${p.kind}">${label}${detail}</span><button type="button" class="more" data-more aria-label="상세">⋯</button></div>
        <div class="bar"><i style="width:${pct}%"></i></div>
        <div class="meta"><span>${pct}%</span>${meta.filter(Boolean).map((m) => `<span>${m}</span>`).join('')}</div>
      </div></li>`;
  }
  function renderPackages() {
    const d = state.data; if (!d) return;
    let pk = d.packages || [];
    $('#badgeDl').hidden = !pk.some((p) => p.kind === 'running');
    $('#badgeDl').textContent = pk.filter((p) => p.kind === 'running').length;
    if (state.filter === 'verifying') pk = [];                          // 검증중은 JD 다운로드 목록엔 없는 임시 항목
    else if (state.filter !== 'all') pk = pk.filter((p) => p.kind === state.filter);
    const order = { running: 0, waiting: 1, action: 2, failed: 3, paused: 4, finished: 5 };
    pk = [...pk].sort((a, b) => (order[a.kind] != null ? order[a.kind] : 9) - (order[b.kind] != null ? order[b.kind] : 9));
    // '링크 검증 없이 즉시 다운로드'로 넣은 링크: JD 가 검증을 마치면 자동으로 이 목록에 들어온다.
    // 그 사이에도 사용자가 "어디 갔지?" 하지 않도록 맨 위에 '검증 중' 카드로 보여준다(전체/진행중 필터에서).
    const verifying = (state.filter === 'all' || state.filter === 'verifying') ? verifyingCards() : '';
    if (state.reordering) return;   // 끌고 있는 동안은 목록을 다시 그리지 않는다
    $('#pkgList').innerHTML = verifying + pk.map(pkgCard).join('');
    const EMPTY = { all: '다운로드가 없습니다.', verifying: '검증 중인 링크가 없습니다.', waiting: '대기 중인 다운로드가 없습니다.', running: '진행 중인 다운로드가 없습니다.', finished: '완료된 다운로드가 없습니다.', failed: '실패한 다운로드가 없습니다.', action: '조치가 필요한 다운로드가 없습니다.', paused: '일시정지된 다운로드가 없습니다.' };
    $('#pkgEmpty').textContent = EMPTY[state.filter] || '해당 상태의 다운로드가 없습니다.';
    $('#pkgEmpty').hidden = pk.length > 0 || !!verifying;
    $$('#pkgList .card-wrap').forEach(attachSwipe);
    $$('#pkgList .card-wrap').forEach(attachReorder);
    $$('#pkgList [data-vmore]').forEach((b) => b.addEventListener('click', (e) => { e.stopPropagation(); const li = b.closest('.card'); openVerifySheet(+li.dataset.vpid, li.dataset.job ? +li.dataset.job : null); }));
    $$('#pkgList [data-more]').forEach((b) => {
      b.addEventListener('pointerdown', (e) => e.stopPropagation());   // 스와이프 시작 방지
      b.addEventListener('click', (e) => { e.stopPropagation(); const p = (state.data.packages || []).find((q) => q.uuid === +b.closest('.card').dataset.uuid); if (p) openPkgSheet(p); });
    });
  }

  // ── 순서 변경: ☰ 손잡이를 세로로 끌어 놓으면 JD 목록 순서도 그대로 바뀐다 ─────────
  function attachReorder(wrap) {
    const handle = $('[data-handle]', wrap); if (!handle) return;
    let active = false, wraps = [], ph = null;
    handle.addEventListener('pointerdown', (e) => {
      e.stopPropagation(); e.preventDefault();
      active = true; state.reordering = true; vibrate();
      handle.setPointerCapture(e.pointerId);
      wrap.classList.add('lifting');
    });
    handle.addEventListener('pointermove', (e) => {
      if (!active) return;
      wraps = $$('#pkgList .card-wrap').filter((w) => w !== wrap);
      const y = e.clientY;
      let target = null;
      for (const w of wraps) { const r = w.getBoundingClientRect(); if (y < r.top + r.height / 2) { target = w; break; } }
      const list = $('#pkgList');
      if (target) { if (target.previousElementSibling !== wrap) list.insertBefore(wrap, target); }
      else if (list.lastElementChild !== wrap) list.appendChild(wrap);
    });
    const end = async () => {
      if (!active) return; active = false; wrap.classList.remove('lifting');
      const uuid = +wrap.dataset.uuid;
      const prev = wrap.previousElementSibling;
      const after = prev && prev.classList.contains('card-wrap') ? +prev.dataset.uuid : null;
      state.reordering = false;
      await act('순서 변경', () => api(`/packages/${uuid}/move`, { method: 'POST', body: { after } }));
    };
    handle.addEventListener('pointerup', end); handle.addEventListener('pointercancel', end);
  }
  function verifyingCards() {
    const g = (state.data || {}).linkgrabber; if (!g) return '';
    const links = (g.links || []).filter((l) => l.autostart);
    if (!links.length) return '';
    const byPkg = new Map(); links.forEach((l) => { const k = l.packageUUID; if (!byPkg.has(k)) byPkg.set(k, []); byPkg.get(k).push(l); });
    const pkgs = new Map((g.packages || []).map((p) => [p.uuid, p]));
    return [...byPkg.entries()].map(([pid, ls]) => {
      const p = pkgs.get(pid) || { name: ls[0].name || '(패키지)' };
      const done = ls.filter((l) => (l.availability || '').toUpperCase() === 'ONLINE').length;
      return `<li class="card verifying" data-vpid="${pid}" data-job="${ls[0].jobId || ''}"><div class="title"><span class="name">${esc(p.name)}</span><span class="tag waiting"><i class="spin"></i>검증 중</span><button type="button" class="more" data-vmore aria-label="상세">⋯</button></div>
        <div class="bar"><i style="width:${Math.round(done / ls.length * 100)}%"></i></div>
        <div class="meta"><span>링크 ${ls.length}개 · ${fmtBytes(ls.reduce((a, l) => a + (l.bytesTotal || 0), 0))}</span><span>검증이 끝나면 자동으로 시작됩니다</span></div></li>`;
    }).join('');
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
  // 상태를 바꾸는 동작(일시정지/재개·재시도·맨 위로)은 시트를 유지하고 버튼만 갱신한다. 지우는 동작만 시트를 닫는다.
  function renderPkgActions(p, removeMode) {
    // 상태별로 의미 있는 동작만 보인다.
    //   진행중/대기 : 일시정지 · 맨 위로        일시정지 : 재개 · 맨 위로
    //   실패        : 재시도                    조치 필요 : 재시도 (원인을 푼 뒤)
    //   완료        : (상태 동작 없음 — 정리만)
    const A = [];
    if (!removeMode) {
      const pause = ['⏸ 일시정지', () => api(`/packages/${p.uuid}/pause`, { method: 'POST' })];
      const resume = ['▶ 재개', () => api(`/packages/${p.uuid}/resume`, { method: 'POST' })];
      const retry = ['🔁 재시도', () => api('/links/retry', { method: 'POST', body: { packageIds: [p.uuid] } })];
      const top = ['⤒ 맨 위로 (먼저 받기)', () => api(`/packages/${p.uuid}/move`, { method: 'POST', body: { after: null } })];
      if (p.kind === 'running' || p.kind === 'waiting') A.push(pause, top);
      else if (p.kind === 'paused') A.push(resume, top);
      else if (p.kind === 'failed' || p.kind === 'action') A.push(retry);
      // finished: 상태를 바꿀 동작이 없다
    }
    A.push(['🗑 목록에서 제거', () => api('/packages/remove', { method: 'POST', body: { packageIds: [p.uuid], deleteFiles: false } }), true]);
    A.push(['❌ 파일까지 삭제', async () => { if (!confirm(`"${p.name}"\n파일까지 완전히 삭제할까요?`)) throw new Error('취소'); return api('/packages/remove', { method: 'POST', body: { packageIds: [p.uuid], deleteFiles: true } }); }, true]);
    const btn = ([l, , danger], i) => `<button class="btn ${danger ? 'danger' : ''}${!danger && A.filter((x) => !x[2]).length === 1 ? ' span2' : ''}" data-i="${i}">${l}</button>`;
    $('#pkgSheetActions').innerHTML = A.map((a, i) => a[2] ? '' : btn(a, i)).join('');
    $('#pkgSheetDanger').innerHTML = A.map((a, i) => a[2] ? btn(a, i) : '').join('');
    $('#pkgSheetDanger').hidden = !A.some((a) => a[2]);
    $$('#pkgSheetActions .btn, #pkgSheetDanger .btn').forEach((b) => b.addEventListener('click', async () => {
      const [l, fn, danger] = A[+b.dataset.i];
      if (danger) { closeSheets(); await act(l, fn); return; }
      b.disabled = true;
      await act(l, fn);
      await refreshNow();                       // 폴링 진행 여부와 무관하게 최신 상태를 받아
      const np = (state.data.packages || []).find((q) => q.uuid === p.uuid);
      if (state.openPkg === p.uuid && np) renderPkgActions(np, removeMode);   // 시트는 그대로, 버튼만 새 상태로
    }));
  }
  // 검증 중(즉시 다운로드) 항목의 시트: 검증이 끝나지 않을 때 빠져나갈 길을 준다
  function openVerifySheet(pid, jobId) {
    const g = (state.data || {}).linkgrabber || {}; const ls = (g.links || []).filter((l) => l.packageUUID === pid);
    const p = (g.packages || []).find((x) => x.uuid === pid) || { name: (ls[0] || {}).name || '(패키지)' };
    state.openPkg = -pid;
    $('#pkgSheetTitle').textContent = p.name; $('#pkgSheetPath').textContent = '즉시 다운로드 · 링크 검증 중 — 검증이 끝나면 자동으로 다운로드 목록으로 옮겨집니다';
    const A = [];
    if (jobId != null) A.push(['🧺 장바구니로 보내기 (자동 시작 취소)', () => api('/autostart/cancel', { method: 'POST', body: { jobId } })]);
    A.push(['🗑 삭제 (링크 제거)', () => api('/linkgrabber/remove', { method: 'POST', body: { packageIds: [pid] } }), true]);
    const btn = ([l, , danger], i) => `<button class="btn ${danger ? 'danger' : ''}${!danger && A.filter((x) => !x[2]).length === 1 ? ' span2' : ''}" data-i="${i}">${l}</button>`;
    $('#pkgSheetActions').innerHTML = A.map((a, i) => a[2] ? '' : btn(a, i)).join('');
    $('#pkgSheetDanger').innerHTML = A.map((a, i) => a[2] ? btn(a, i) : '').join(''); $('#pkgSheetDanger').hidden = false;
    $$('#pkgSheetActions .btn, #pkgSheetDanger .btn').forEach((b) => b.addEventListener('click', async () => { const [l, fn] = A[+b.dataset.i]; closeSheets(); await act(l, fn); }));
    $('#pkgSheetLinks').innerHTML = ls.map((l) => `<li class="card"><div class="title"><span class="name">${esc(l.name)}</span><span class="tag ${esc(l.availability || '')}">${esc(l.availability || '검증 중')}</span></div>
      <div class="meta"><span>${fmtBytes(l.bytesTotal)}</span><span>${esc(l.host || '')}</span></div></li>`).join('') || '<li class="muted small">링크 없음</li>';
    $('#pkgSheet').hidden = false;
  }
  async function refreshNow() {
    try { state.data = await api('/state'); renderTop(state.data); renderPackages(); renderGrabber(); } catch (e) {}
  }
  async function openPkgSheet(p, removeMode = false) {
    state.openPkg = p.uuid;
    $('#pkgSheetTitle').textContent = p.name;
    $('#pkgSheetPath').textContent = p.path || '';
    renderPkgActions(p, removeMode);
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

  // ── 렌더: 장바구니(JD 링크그래버) ─────────────────────────────────────
  function renderGrabber() {
    const g = (state.data || {}).linkgrabber; const ul = $('#grabList');
    if (!g) { ul.innerHTML = ''; $('#grabEmpty').hidden = false; $('#badgeGrab').hidden = true; return; }
    // 즉시 다운로드로 들어와 검증 중인 링크는 다운로드 탭에 '검증 중' 카드로 보이므로 장바구니에서는 뺀다(한 항목은 한 곳에만).
    const links = (g.links || []).filter((l) => !l.autostart);
    $('#badgeGrab').hidden = links.length === 0; $('#badgeGrab').textContent = links.length;
    $('#grabberInfo').textContent = links.length ? `장바구니 ${links.length}개 · ${fmtBytes(links.reduce((a, l) => a + (l.bytesTotal || 0), 0))}` : '장바구니 · 검증 후 시작';
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
  const updateCount = () => { const n = extractUrls(addText.value).length; $('#urlCount').textContent = n ? `URL ${n}개` : ''; $('#btnAddCart').disabled = $('#btnAddNow').disabled = !n; };
  addText.addEventListener('input', updateCount);
  $('#btnClearText').addEventListener('click', () => { addText.value = ''; updateCount(); });
  $('#btnPaste').addEventListener('click', async () => { try { const t = await navigator.clipboard.readText(); if (!extractUrls(t).length) return toast('클립보드에 URL이 없습니다', true); addText.value = (addText.value ? addText.value + '\n' : '') + t; updateCount(); } catch (e) { toast('클립보드를 읽을 수 없습니다 (권한)', true); } });
  // 저장 위치: 설정에 보이는 다운로드 폴더를 기본값으로 채운다. 사용자가 고친 값은 건드리지 않는다.
  function setDownloadRoot(root) {
    const el = $('#addFolder'), prev = state.downloadRoot;
    state.downloadRoot = root;
    if (!el.value || el.value === prev) el.value = root;
  }
  // 장바구니에 담기 / 즉시 다운로드 — 버튼이 곧 선택. Enter(폼 submit)는 안전한 쪽(장바구니)으로.
  async function addLinks(autostart) {
    if (!extractUrls(addText.value).length) return;
    const body = { text: addText.value, destFolder: $('#addFolder').value.trim() || null, autostart };
    const r = await act('링크 추가', () => api('/links', { method: 'POST', body }));
    if (r) { addText.value = ''; $('#addFolder').value = state.downloadRoot; updateCount(); showTab(autostart ? 'downloads' : 'grabber'); }
  }
  $('#btnAddCart').addEventListener('click', () => addLinks(false));
  $('#btnAddNow').addEventListener('click', () => addLinks(true));
  $('#addForm').addEventListener('submit', (e) => { e.preventDefault(); addLinks(false); });
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
  const daysLeft = (sec) => Math.ceil((sec * 1000 - Date.now()) / 86400000);
  // 쿠키 로그인은 넣어 둔 쿠키가 만료되면 끝난다. 가장 먼저 만료되는 인증 쿠키가 기준.
  function cookieExpiryText(a) {
    const d = daysLeft(a.cookieExpiry), when = fmtDate(a.cookieExpiry * 1000);
    const name = a.cookieName ? ` (${esc(a.cookieName)} 기준)` : '';
    if (d < 0) return `쿠키 만료됨 <b>${when}</b>${name} — 확장으로 갱신하세요`;
    if (d <= 7) return `쿠키 만료 <b>${when}</b> · ${d}일 남음${name} — 곧 갱신 필요`;
    return `쿠키 만료 <b>${when}</b> · ${d}일 남음${name}`;
  }
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
      if (a.cookieExpiry) meta.push(cookieExpiryText(a));
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
  $('#btnSpeedLimit').addEventListener('click', () => { const sl = (state.data && state.data.jd && state.data.jd.speedlimit) || {}; $('#spEnabled').checked = !!sl.enabled; $('#spValue').value = sl.limit ? +(sl.limit / 1048576).toFixed(1) : 10; $$('#spPresets .chip').forEach((c) => c.classList.toggle('active', sl.enabled && Math.round(sl.limit / 1048576) === +c.dataset.mb)); $('#speedSheet').hidden = false; });
  $$('#spPresets .chip').forEach((c) => c.addEventListener('click', () => { $('#spValue').value = c.dataset.mb; $('#spEnabled').checked = true; $$('#spPresets .chip').forEach((x) => x.classList.toggle('active', x === c)); }));
  $('#spApply').addEventListener('click', () => { const mb = parseFloat($('#spValue').value); closeSheets(); act('속도 제한 적용', () => api('/speedlimit', { method: 'PUT', body: { enabled: $('#spEnabled').checked, limit: mb > 0 ? Math.round(mb * 1048576) : null } })); });

  // ── 설정 시트 ─────────────────────────────────────────────────────────
  // 바로가기 주소: 사용자가 고급에서 넣은 값 > 서버(.env) 기본값. 둘 다 없으면 버튼은 보이되 '미설정' 표시 + 누르면 빨간 안내.
  const LINKS = { novncUrl: '#linkNovnc', browserUrl: '#linkBrowser' };
  function effectiveUrl(k) { return state[k] || state.server[k] || ''; }
  function applyLinks() {
    for (const k in LINKS) {
      const a = $(LINKS[k]), u = effectiveUrl(k), st = $('.link-state', a);
      a.classList.toggle('unset', !u); a.classList.toggle('set', !!u); a.href = u || '#'; if (u) a.target = '_blank'; else a.removeAttribute('target');
      if (st) { st.textContent = u ? '설정됨' : '미설정'; }
    }
  }
  for (const k in LINKS) $(LINKS[k]).addEventListener('click', (e) => { if (!effectiveUrl(k)) { e.preventDefault(); toast(`${$(LINKS[k]).dataset.name} 주소가 지정되지 않았습니다. 고급(바로가기 주소 설정)에서 입력하세요.`, true); } });
  function validUrl(v) { try { const u = new URL(v); return /^https?:$/.test(u.protocol) ? u.href : ''; } catch (e) { return ''; } }
  $('#settingsBtn').addEventListener('click', () => {
    $$('#pollChips .chip').forEach((c) => c.classList.toggle('active', +c.dataset.s * 1000 === state.pollMs)); $('#setClipboard').checked = state.clipboard;
    $('#setNovncUrl').value = state.novncUrl; $('#setBrowserUrl').value = state.browserUrl;
    $('#settingsSheet').hidden = false;
  });
  $('#setLinksForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const fields = { novncUrl: $('#setNovncUrl'), browserUrl: $('#setBrowserUrl') };
    for (const k in fields) { const raw = fields[k].value.trim(); if (raw && !validUrl(raw)) { toast('http:// 또는 https:// 로 시작하는 주소를 넣어 주세요', true); fields[k].focus(); return; } }
    for (const k in fields) { const raw = fields[k].value.trim(); state[k] = raw ? validUrl(raw) : ''; LS.set(k, state[k]); fields[k].value = state[k]; }
    applyLinks(); toast('바로가기 주소 저장됨');
  });
  $$('#pollChips .chip').forEach((c) => c.addEventListener('click', () => { state.pollMs = +c.dataset.s * 1000; LS.set('pollMs', state.pollMs); schedule(); $$('#pollChips .chip').forEach((x) => x.classList.toggle('active', x === c)); }));
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
