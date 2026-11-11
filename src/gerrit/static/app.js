(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const state = { csrf: '', tasks: [], settings: {}, selected: null, busy: false };
  const terminal = new Set(['completed', 'failed', 'cancelled', 'reconciliation_required']);
  const form = $('#settings-form');
  const toast = (message) => { const el = $('#toast'); el.textContent = message; el.classList.add('visible'); window.setTimeout(() => el.classList.remove('visible'), 3200); };
  const safeText = (value, fallback = '—') => value === null || value === undefined || value === '' ? fallback : String(value);
  async function request(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body) headers.set('Content-Type', 'application/json');
    if (options.method && options.method !== 'GET') headers.set('X-CSRF-Token', state.csrf);
    const response = await fetch(url, { ...options, headers, cache: 'no-store' });
    let data;
    try { data = await response.json(); } catch { throw new Error(`Server returned an unreadable response (${response.status}).`); }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }
  function renderQueue() {
    const list = $('#task-list'); list.replaceChildren();
    $('#task-count').textContent = String(state.tasks.length);
    const active = Number.isInteger(state.executing) ? state.executing : state.tasks.filter(t => !terminal.has(t.state)).length;
    $('#parallel-count').textContent = `${active} / ${safeText(state.max_parallel, '—')} slots`;
    if (!state.tasks.length) {
      const empty = document.createElement('div'); empty.className = 'empty-queue';
      for (const [tag, text, cls] of [['span','◌','empty-glyph'],['strong','Nothing in motion',''],['span','Start a task to open an isolated workspace.','']]) { const el = document.createElement(tag); el.textContent = text; if (cls) el.className = cls; empty.append(el); }
      list.append(empty); return;
    }
    for (const task of state.tasks) {
      const button = document.createElement('button'); button.type = 'button'; button.className = `task-item${task.id === state.selected ? ' selected' : ''}`; button.setAttribute('aria-pressed', String(task.id === state.selected));
      const title = document.createElement('span'); title.className = 'task-item-title'; title.textContent = safeText(task.title, 'Untitled task');
      const meta = document.createElement('span'); meta.className = 'task-item-meta';
      const status = document.createElement('span'); const dot = document.createElement('i'); dot.className = `state-dot ${task.state || ''}`; status.append(dot, document.createTextNode(safeText(task.state, 'unknown')));
      const time = document.createElement('time'); time.textContent = task.created_at ? new Date(task.created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '';
      meta.append(status, time); button.append(title, meta); button.addEventListener('click', () => { state.selected = task.id; renderQueue(); renderDetail(task); }); list.append(button);
    }
    if (!state.tasks.some(t => t.id === state.selected)) { state.selected = state.tasks[0].id; renderQueue(); renderDetail(state.tasks[0]); }
  }
  function allowedLink(value) {
    if (typeof value !== 'string' || !value.trim()) return '';
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; } catch { return ''; }
  }
  function monitorItems(value) {
    if (!Array.isArray(value)) return { count: '—', lines: [] };
    const lines = value.slice(0, 5).map(item => {
      if (typeof item === 'string' || typeof item === 'number') return String(item).slice(0, 240);
      if (!item || typeof item !== 'object') return safeText(item, 'Item');
      const summary = item.summary || item.text || item.reason || item.status || item.subject || item.change_id || item.id;
      if (summary !== undefined) return String(summary).slice(0, 240);
      return Object.entries(item).slice(0, 4).map(([key, val]) => `${key}: ${typeof val === 'string' || typeof val === 'number' ? String(val).slice(0, 80) : '[details]'}`).join(' · ');
    });
    if (value.length > lines.length) lines.push(`+${value.length - lines.length} more`);
    return { count: String(value.length), lines };
  }
  function renderDetail(task) {
    $('#detail-empty').hidden = true; $('#task-detail').hidden = false;
    $('#detail-id').textContent = safeText(task.id, '').slice(0, 12);
    $('#detail-title').textContent = safeText(task.title, 'Untitled task');
    const badge = $('#detail-state'); badge.textContent = safeText(task.state, 'unknown'); badge.className = `state-badge ${task.state || ''}`;
    $('#detail-workspace').textContent = safeText(task.workspace, 'Workspace not assigned');
    $('#stop-task').hidden = terminal.has(task.state);
    const link = allowedLink(task.chain_url); const anchor = $('#chain-link'); anchor.hidden = !link;
    if (link) anchor.href = link; else anchor.removeAttribute('href');
    $('#chain-heading').textContent = link ? 'Review chain is ready' : 'Waiting for first confirmed upload';
    $('#chain-subtitle').textContent = link ? 'Open the Gerrit search for this task’s commit chain.' : 'The review link appears here as soon as Gerrit confirms the chain.';
    const changes = Array.isArray(task.changes) ? task.changes : []; $('#change-count').textContent = String(changes.length);
    const changesList = $('#changes-list'); changesList.replaceChildren();
    if (!changes.length) { const p = document.createElement('p'); p.className='muted'; p.textContent='No confirmed changes yet.'; changesList.append(p); }
    for (const change of changes) {
      const row = document.createElement('div'); row.className = 'change-item'; const main = document.createElement('strong');
      main.textContent = typeof change === 'string' ? change : safeText(change.subject || change.message || change.change_id || change.id, 'Confirmed change');
      row.append(main); if (change && typeof change === 'object') { const sub = document.createElement('span'); sub.textContent = safeText(change.revision || change.change_id || change.status, ''); row.append(sub); } changesList.append(row);
    }
    const monitor = task.monitor || {};
    const coverageActive = monitor.coverage === true;
    $('#coverage-fill').classList.toggle('active', coverageActive);
    $('#coverage-value').textContent = monitor.coverage === true ? 'Active' : monitor.coverage === false ? 'Paused' : '—';
    const pending = monitorItems(monitor.pending); const gaps = monitorItems(monitor.gaps);
    $('#pending-value').textContent = pending.count;
    $('#gaps-value').textContent = gaps.count;
    $('#monitor-state').textContent = safeText(monitor.state, coverageActive ? 'Monitoring' : monitor.coverage === false ? 'Paused' : 'Not started');
    const monitorDetails = [...pending.lines.map(line => `Pending · ${line}`), ...gaps.lines.map(line => `Poll gap · ${line}`)];
    const detail = $('#monitor-detail'); detail.textContent = monitorDetails.join('\n'); detail.hidden = !monitorDetails.length;
    const timing = [];
    if (monitor.last_poll) timing.push(`Last poll ${safeText(monitor.last_poll)}`);
    if (monitor.deadline) { const deadline = typeof monitor.deadline === 'number' && Number.isFinite(monitor.deadline) ? new Date(monitor.deadline * 1000).toISOString() : safeText(monitor.deadline); timing.push(`Deadline ${deadline}`); }
    $('#monitor-note').textContent = timing.length ? timing.join(' · ') : 'Coverage begins after the first confirmed upload.';
    const events = Array.isArray(task.events) ? task.events : []; const feed = $('#event-list'); feed.replaceChildren();
    if (!events.length) { const p=document.createElement('p'); p.className='muted'; p.style.padding='14px 8px'; p.textContent='Waiting for task events.'; feed.append(p); }
    for (const event of events) { const row=document.createElement('div'); row.className=`event-row${event.kind === 'error' ? ' error' : ''}`; for (const [cls, val] of [['event-time',event.time],['event-kind',event.kind],['event-text',event.text]]) { const span=document.createElement('span'); span.className=cls; span.textContent=safeText(val, ''); row.append(span); } feed.append(row); }
  }
  function setConnection(ok) { $('#connection-dot').className = `live-indicator ${ok ? 'online' : 'offline'}`; $('#connection-label').textContent = ok ? 'Connected' : 'Disconnected'; }
  async function poll() {
    try { const data = await request('/api/state'); state.csrf = data.csrf_token || state.csrf; state.tasks = Array.isArray(data.tasks) ? data.tasks : []; state.max_parallel = data.max_parallel; state.executing = Number.isInteger(data.executing) ? data.executing : undefined; if (data.settings) state.settings = data.settings; setConnection(true); renderQueue(); const selected=state.tasks.find(t=>t.id===state.selected); if (selected) renderDetail(selected); }
    catch (error) { setConnection(false); if ($('#task-detail').hidden) { $('#detail-empty').querySelector('h2').textContent = 'Dashboard unavailable'; $('#detail-empty').querySelector('p:last-child').textContent = `${error.message} Retrying shortly.`; } }
  }
  function fillSettings(settings) {
    const simple = ['repository','max_parallel','max_units','agent_timeout','http_url','ssh_host','ssh_port','username','project','base_branch','known_hosts','credential_mode','token_file','ssh_key_file','spec_file','profile','scope','token_env','ssh_key_env','poll_interval','monitor_hours'];
    for (const key of simple) { const input=form.elements.namedItem(key); if (input) input.value = settings[key] ?? ''; }
    form.elements.namedItem('agent_command').value = Array.isArray(settings.agent_command) ? settings.agent_command.join('\n') : '';
    form.elements.namedItem('field_env').value = settings.field_env && typeof settings.field_env === 'object' ? JSON.stringify(settings.field_env, null, 2) : '';
    updateCredentialFields();
  }
  function updateCredentialFields() { const mode=form.elements.namedItem('credential_mode').value; document.querySelectorAll('.credential-fields').forEach(el => { el.hidden=el.dataset.mode !== mode; }); }
  function showDrawer() { $('#drawer-backdrop').hidden=false; const drawer=$('#settings-drawer'); drawer.inert=false; drawer.setAttribute('aria-hidden','false'); requestAnimationFrame(()=>drawer.classList.add('open')); $('#close-settings').focus(); }
  function hideDrawer() { const drawer=$('#settings-drawer'); drawer.classList.remove('open'); drawer.inert=true; drawer.setAttribute('aria-hidden','true'); $('#drawer-backdrop').hidden=true; $('#open-settings').focus(); }
  $('#open-settings').addEventListener('click', async () => { try { state.settings=await request('/api/settings'); fillSettings(state.settings); } catch (e) { $('#settings-error').textContent=e.message; $('#settings-error').hidden=false; } showDrawer(); });
  $('#close-settings').addEventListener('click',hideDrawer); $('#cancel-settings').addEventListener('click',hideDrawer); $('#drawer-backdrop').addEventListener('click',hideDrawer);
  document.addEventListener('keydown', e => {
    const drawer = $('#settings-drawer');
    if (!drawer.classList.contains('open')) return;
    if (e.key === 'Escape') { e.preventDefault(); hideDrawer(); return; }
    if (e.key !== 'Tab') return;
    const focusable = [...drawer.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
      .filter(el => !el.hidden && el.getClientRects().length);
    if (!focusable.length) { e.preventDefault(); drawer.focus(); return; }
    const first = focusable[0]; const last = focusable[focusable.length - 1];
    if (e.shiftKey && (document.activeElement === first || !drawer.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (document.activeElement === last || !drawer.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
  });
  form.elements.namedItem('credential_mode').addEventListener('change',updateCredentialFields);
  form.addEventListener('submit',async e=>{ e.preventDefault(); const error=$('#settings-error'); error.hidden=true; const fd=new FormData(form); const payload={};
    for (const key of ['repository','http_url','ssh_host','username','project','base_branch','known_hosts','credential_mode','token_file','ssh_key_file','spec_file','profile','scope','token_env','ssh_key_env']) payload[key]=String(fd.get(key)||'').trim();
    const defaults={ssh_port:29418,max_parallel:3,max_units:20,agent_timeout:1800,poll_interval:300,monitor_hours:12};
    for (const key of Object.keys(defaults)) { const raw=String(fd.get(key)||'').trim(); payload[key]=raw ? Number(raw) : defaults[key]; }
    payload.agent_command=String(fd.get('agent_command')||'').split('\n').map(x=>x.trim()).filter(Boolean);
    if (!payload.agent_command.length) payload.agent_command=['opencode','run','--standalone','--format','json','{prompt}'];
    try { payload.field_env=String(fd.get('field_env')||'').trim() ? JSON.parse(String(fd.get('field_env'))) : {}; if (!payload.field_env || Array.isArray(payload.field_env) || typeof payload.field_env!=='object') throw new Error('Field variable map must be a JSON object.'); }
    catch (err) { error.textContent=err.message==='Field variable map must be a JSON object.' ? err.message : 'Field variable map must be valid JSON.'; error.hidden=false; return; }
    try { state.settings=await request('/api/settings',{method:'POST',body:JSON.stringify(payload)}); fillSettings(state.settings); await poll(); toast('Settings saved for future tasks.'); hideDrawer(); }
    catch(err) { error.textContent=err.message; error.hidden=false; }
  });
  $('#task-form').addEventListener('submit',async e=>{ e.preventDefault(); const error=$('#task-error'); error.hidden=true; const title=$('#task-title').value.trim(); if(!title) return; try { const task=await request('/api/tasks',{method:'POST',body:JSON.stringify({title})}); state.selected=task.id; $('#task-title').value=''; await poll(); toast('Task added to the queue.'); } catch(err) { error.textContent=err.message; error.hidden=false; } });
  $('#stop-task').addEventListener('click',async()=>{ const task=state.tasks.find(t=>t.id===state.selected); if(!task) return; const error=$('#detail-error'); error.hidden=true; try { await request(`/api/tasks/${encodeURIComponent(task.id)}/cancel`,{method:'POST',body:'{}'}); await poll(); toast('Stop requested.'); } catch(err) { error.textContent=err.message; error.hidden=false; } });
  poll(); window.setInterval(poll,1800);
})();
