/* ═════════════════════════════════════════════════════════
   NERMANA App v4.7.2
   ═════════════════════════════════════════════════════════ */

/* ═══ UTILITIES ═══ */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

async function api(url, opts = {}) {
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ error: r.statusText }));
    throw new Error(e.error || r.statusText);
  }
  return r.json();
}

/* ═══ TOAST ═══ */
let _toastTimer = null;
function toast(msg, type = 'ok') {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'show ' + type;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => (el.className = ''), 3000);
}

/* ═══ MODAL ═══ */
function showModal(title, body, confirmLabel, onConfirm) {
  const overlay = $('modal-overlay');
  $('modal-title').textContent = title;
  $('modal-body').innerHTML = body;
  $('modal-confirm').textContent = confirmLabel || 'Confirm';
  $('modal-confirm').className = 'btn btn-primary';
  $('modal-cancel').style.display = 'inline-flex';
  overlay.classList.add('open');
  const cleanup = () => overlay.classList.remove('open');
  const handler = () => {
    cleanup();
    onConfirm();
  };
  $('modal-confirm').onclick = handler;
  $('modal-cancel').onclick = cleanup;
  overlay.onclick = (e) => { if (e.target === overlay) cleanup(); };
}

function showAlert(title, body) {
  const overlay = $('modal-overlay');
  $('modal-title').textContent = title;
  $('modal-body').innerHTML = body;
  $('modal-confirm').textContent = 'OK';
  $('modal-confirm').className = 'btn btn-primary';
  $('modal-cancel').style.display = 'none';
  overlay.classList.add('open');
  const cleanup = () => overlay.classList.remove('open');
  $('modal-confirm').onclick = cleanup;
  overlay.onclick = (e) => { if (e.target === overlay) cleanup(); };
}

function toggleSwitch(el) { el.classList.toggle('on'); }
function toggleSet(id, v) { const el = $(id); if (el) el.classList.toggle('on', !!v); }

/* ═══ COLLAPSE ═══ */
function toggleCollapse(id) {
  const h = $('ch-' + id), b = $('cb-' + id);
  if (!h || !b) return;
  h.classList.toggle('open');
  b.classList.toggle('open');
  if (b.classList.contains('open')) {
    if (id === 'health' && !$('diagContent').dataset.loaded) loadDiagnostics();
    if (id === 'memory') { loadMemStats(); loadMood(); loadReminders(); }
    if (id === 'settings') loadSettings();
    if (id === 'activity' && !window._pipeInit) initPipeline();
  }
}

/* ═══ NAVIGATION ═══ */
function showPage(id, btn) {
  document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
  const page = $('page-' + id);
  if (page) page.classList.add('active');
  if (btn) btn.classList.add('active');
  const actions = {
    dashboard: () => { loadDashboard(); loadVersion(); },
    models: () => loadModels(),
    files: () => fmLoad(),
    bot: () => loadBotPage(),
  };
  if (actions[id]) actions[id]();
}

/* ═══ STATUS POLL ═══ */
async function pollStatus() {
  try {
    const s = await api('/api/status');
    const dl = $('dot-llm'), db = $('dot-bot');
    if (dl) dl.className = 'dot ' + (s.llm_server ? 'on' : s.sleeping ? 'warn' : 'off');
    if (db) db.className = 'dot ' + (s.bot ? 'on' : 'off');
  } catch (e) { /* ignore */ }
}
pollStatus();
setInterval(pollStatus, 8000);

/* ═════════════════════════════════════════════════════════
   CHAT
   ═════════════════════════════════════════════════════════ */
let _chatBusy = false;

function addMsg(who, text, id, extra) {
  const el = $('chatMsgs');
  const d = document.createElement('div');
  d.className = 'msg ' + who + (extra ? ' ' + extra : '');
  if (id) d.id = id;
  d.textContent = text;
  const es = $('chatEmpty');
  if (es && es.parentNode === el) es.remove();
  el.appendChild(d);
  el.scrollTop = el.scrollHeight;
  return d;
}

async function sendChat() {
  if (_chatBusy) return;
  const inp = $('chatInput');
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  _chatBusy = true;
  $('chatStatus').textContent = 'thinking…';
  $('sendBtn').classList.add('busy');
  addMsg('user', text);
  const botId = 'bot-' + Date.now();
  addMsg('bot', '…', botId, 'streaming');
  const ti = document.createElement('div');
  ti.className = 'typing';
  ti.id = 'typing-' + botId;
  ti.innerHTML = '<span></span><span></span><span></span>';
  $('chatMsgs').appendChild(ti);
  $('chatMsgs').scrollTop = $('chatMsgs').scrollHeight;
  let full = '';
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    if (!resp.ok) throw new Error((await resp.json()).error || resp.statusText);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    const typingEl = $('typing-' + botId);
    if (typingEl) typingEl.remove();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        if (!part.startsWith('data:')) continue;
        try {
          const d = JSON.parse(part.slice(5).trim());
          if (d.offline) { const el = $(botId); if (el) el.classList.add('offline'); }
          if (d.tool === 'search') addMsg('tool', '🔍 searching: ' + (d.query || '…'), 'tool-' + Date.now());
          if (d.tool === 'exec') addMsg('tool', '⚙ running: ' + (d.cmd || '…'), 'tool-' + Date.now());
          if (d.text) {
            full += d.text;
            const el = $(botId);
            if (el) { el.textContent = full; $('chatMsgs').scrollTop = $('chatMsgs').scrollHeight; }
          }
          if (d.done) { const el = $(botId); if (el) el.classList.remove('streaming'); }
        } catch (e) { /* ignore parse errors */ }
      }
    }
  } catch (e) {
    const typingEl = $('typing-' + botId);
    if (typingEl) typingEl.remove();
    const el = $(botId);
    if (el) el.textContent = 'Error: ' + (e.message || 'connection lost');
  } finally {
    _chatBusy = false;
    $('chatStatus').textContent = 'ready';
    $('sendBtn').classList.remove('busy');
    const el = $(botId);
    if (el && el.textContent === '…') el.textContent = '(no response)';
  }
}

async function clearChat() {
  showModal('Clear Chat', 'Delete all messages in the current conversation?', 'Clear', async () => {
    try {
      await fetch('/api/chat/clear', { method: 'POST' });
      $('chatMsgs').innerHTML = '<div class="msg bot">Chat cleared.</div>';
      toast('Chat cleared');
    } catch (e) { toast('Error: ' + e.message, 'err'); }
  });
}

/* ═════════════════════════════════════════════════════════
   PIPELINE SSE
   ═════════════════════════════════════════════════════════ */
let _pipeInit = false;
let _pipeFilter = 'all';
const _pipe = () => $('pipeLog');

function initPipeline() {
  if (_pipeInit) return;
  _pipeInit = true;
  const evt = new EventSource('/api/pipeline_stream');
  evt.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      if (_pipeFilter !== 'all' && d.stage !== _pipeFilter) return;
      const row = document.createElement('div');
      row.className = 'pipe-evt';
      const ds = typeof d.data === 'object'
        ? Object.values(d.data).join(' ').slice(0, 140)
        : String(d.data).slice(0, 140);
      row.innerHTML = '<div class="pipe-ts">' + (d.ts_human || '') + '</div>'
        + '<div class="pipe-stage ' + d.stage + '">' + d.stage + '</div>'
        + '<div class="pipe-data">' + esc(ds) + '</div>';
      const pl = _pipe();
      if (pl) {
        pl.prepend(row);
        while (pl.children.length > 100) pl.lastChild.remove();
        if (pl.textContent === 'Waiting for events…' || pl.querySelector('.pipe-empty')) pl.innerHTML = '';
      }
    } catch (e) { /* ignore */ }
  };
  evt.onerror = () => { /* reconnect */ };
}

function setPipeFilter(f, btn) {
  _pipeFilter = f;
  document.querySelectorAll('.pf-btn').forEach((b) => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  const pl = _pipe();
  if (!pl) return;
  Array.from(pl.children).forEach((row) => {
    if (f === 'all') { row.style.display = 'flex'; return; }
    const stage = row.querySelector('.pipe-stage');
    row.style.display = stage && stage.textContent === f ? 'flex' : 'none';
  });
}

/* ═════════════════════════════════════════════════════════
   DASHBOARD
   ═════════════════════════════════════════════════════════ */
async function loadDashboard() {
  try {
    const d = await api('/api/dashboard');
    const llm = $('dash-llm'), bot = $('dash-bot'), model = $('dash-model');
    if (llm) { llm.textContent = d.llm ? 'ON' : 'OFF'; llm.style.color = d.llm ? 'var(--accent3)' : 'var(--accent4)'; }
    if (bot) { bot.textContent = d.bot ? 'ON' : 'OFF'; bot.style.color = d.bot ? 'var(--accent)' : 'var(--accent4)'; }
    if (model) model.textContent = d.active_model || '—';
    const m = d.memory || {};
    const setStat = (id, val, cls) => {
      const el = $(id);
      if (el) { el.textContent = val || 0; if (cls) el.className = 'stat-num ' + cls; }
    };
    setStat('dash-lt', m.long_term, 'lt');
    setStat('dash-st', m.short_term, 'st');
    setStat('dash-buf', m.buffer, 'buf');
    setStat('dash-junk', m.junk, 'jk');
  } catch (e) { /* ignore */ }
}
loadDashboard();
setInterval(loadDashboard, 15000);

/* ═════════════════════════════════════════════════════════
   MEMORY (dashboard collapse)
   ═════════════════════════════════════════════════════════ */
async function loadMemStats() {
  try {
    const s = await api('/api/memory_stats');
    const set = (id, val, cls) => {
      const el = $(id);
      if (el) { el.textContent = val || 0; if (cls) el.className = 'stat-num ' + cls; }
    };
    set('statLT', s.long_term, 'lt');
    set('statST', s.short_term, 'st');
    set('statBuf', s.buffer, 'buf');
    set('statJunk', s.junk, 'jk');
  } catch (e) { /* ignore */ }
}

async function loadMood() {
  try {
    const m = await api('/api/mood');
    const icons = { happy: '😊', sad: '😢', curious: '🤔', neutral: '🧠', excited: '🤩', tired: '😴' };
    const icon = $('memMoodIcon');
    const text = $('memMoodText');
    if (icon) icon.textContent = icons[m.label] || '🧠';
    if (text) text.textContent = m.line || 'Neutral';
  } catch (e) { /* ignore */ }
}

async function loadReminders() {
  try {
    const r = await api('/api/reminders');
    const rems = r.reminders || [];
    const el = $('remindersList');
    if (!el) return;
    el.innerHTML = rems.length
      ? rems.map((rm) =>
          '<div class="mem-reminder" style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">'
          + '<span style="font-family:var(--mono);color:var(--accent);font-size:11px;min-width:48px">'
          + String(rm.hour).padStart(2, '0') + ':' + String(rm.minute).padStart(2, '0')
          + '</span>'
          + '<span style="flex:1;color:var(--text2);font-size:12px">' + esc(rm.text) + '</span>'
          + '<span style="font-size:9px;color:var(--dim)">' + (rm.daily ? 'daily' : 'once') + '</span>'
          + '</div>'
        ).join('')
      : '<span class="dim">No reminders set.</span>';
  } catch (e) { /* ignore */ }
}

/* ═════════════════════════════════════════════════════════
   DIAGNOSTICS
   ═════════════════════════════════════════════════════════ */
async function loadDiagnostics() {
  const dc = $('diagContent');
  if (dc) dc.dataset.loaded = '1';
  try {
    const d = await api('/api/diagnostics');
    renderDiagnostics(d);
  } catch (e) {
    if (dc) dc.innerHTML = '<div class="card"><div class="card-label">🏥 System Health</div><div class="dim">Click "Run Now" to diagnose.</div></div>';
  }
}

function renderDiagnostics(d) {
  const health = d || {};
  const cpu = health.cpu_percent || 0;
  const mem = health.memory_percent || 0;
  const temp = health.temperature || '—';
  const uptime = health.uptime_hours || '—';
  const llm = health.llm_latency_ms || '—';
  const cpuColor = cpu > 80 ? 'var(--accent4)' : cpu > 50 ? 'var(--yellow)' : 'var(--accent3)';
  const memColor = mem > 80 ? 'var(--accent4)' : mem > 50 ? 'var(--yellow)' : 'var(--accent3)';
  let html = '<div class="card"><div class="card-label">🏥 System Health</div><div class="diag-grid">'
    + '<div class="diag-card"><div class="dc-val" style="color:' + cpuColor + '">' + cpu + '%</div>'
    + '<div class="dc-lbl">CPU</div><div class="dc-bar"><div class="dc-bar-fill" style="width:' + Math.min(cpu, 100) + '%;background:' + cpuColor + '"></div></div></div>'
    + '<div class="diag-card"><div class="dc-val" style="color:' + memColor + '">' + mem + '%</div>'
    + '<div class="dc-lbl">Memory</div><div class="dc-bar"><div class="dc-bar-fill" style="width:' + Math.min(mem, 100) + '%;background:' + memColor + '"></div></div></div>'
    + '<div class="diag-card"><div class="dc-val" style="color:var(--accent)">' + temp + '</div><div class="dc-lbl">Temp (°C)</div></div>'
    + '<div class="diag-card"><div class="dc-val" style="color:var(--accent)">' + uptime + '</div><div class="dc-lbl">Uptime (h)</div></div>'
    + '<div class="diag-card"><div class="dc-val" style="color:var(--accent)">' + llm + '</div><div class="dc-lbl">LLM Latency (ms)</div></div>'
    + '</div></div>';
  if (health.quality_scores && health.quality_scores.length) {
    const avg = health.quality_scores.reduce((a, b) => a + b, 0) / health.quality_scores.length;
    html += '<div class="card"><div class="card-label">⭐ Recent Quality (avg ' + avg.toFixed(1) + ')</div><div class="flex-w">'
      + health.quality_scores.slice(-10).map((s) =>
          '<span class="badge ' + (s > 6 ? 'badge-green' : s > 4 ? 'badge-yellow' : 'badge-red') + '" style="font-size:13px">'
          + s.toFixed(1) + '</span>'
        ).join('')
      + '</div></div>';
  }
  if (health.corrections && health.corrections.length) {
    html += '<div class="card"><div class="card-label">🔧 Recent Corrections</div><div class="diag-list">'
      + health.corrections.slice(-5).map((c) =>
          '<div class="dl-item"><span class="dl-label">' + esc(c.reason || '') + '</span>'
          + '<span class="dl-val">' + esc(c.original || '').slice(0, 80) + ' → ' + esc(c.corrected || '').slice(0, 80) + '</span></div>'
        ).join('')
      + '</div></div>';
  }
  const dc = $('diagContent');
  if (dc) dc.innerHTML = html;
}

async function runDiagnostics() {
  const dc = $('diagContent');
  if (dc) dc.innerHTML = '<div class="skeleton" style="height:200px;margin-bottom:8px"></div>';
  try {
    const d = await api('/api/diagnose_now', { method: 'POST' });
    renderDiagnostics(d);
    toast('Diagnostics complete');
  } catch (e) {
    toast('Diagnostics error', 'err');
    loadDiagnostics();
  }
}

async function loadReflections() {
  try {
    const r = await api('/api/reflection_log?n=10');
    const items = r.reflections || [];
    let html = '<div class="card"><div class="card-label">🔄 Reflection Log</div><div class="diag-list">';
    if (!items.length) html += '<span class="dim">No reflections yet.</span>';
    else items.forEach((item) => {
      html += '<div class="dl-item"><span class="dl-label">' + (item.ts || '') + '</span>'
        + '<span class="dl-val">' + esc(item.summary || JSON.stringify(item)).slice(0, 200) + '</span></div>';
    });
    html += '</div></div>';
    const dc = $('diagContent');
    if (dc) dc.innerHTML = html;
  } catch (e) { toast('Error', 'err'); }
}

async function loadQuality() {
  try {
    const r = await api('/api/quality_scores?n=20');
    const scores = r.scores || [];
    let html = '<div class="card"><div class="card-label">⭐ Quality Scores</div><div class="flex-w">';
    if (!scores.length) html += '<span class="dim">No scores yet.</span>';
    else scores.forEach((s) => {
      html += '<span class="badge ' + (s.score > 6 ? 'badge-green' : s.score > 4 ? 'badge-yellow' : 'badge-red')
        + '" style="font-size:13px;padding:4px 10px">' + s.score.toFixed(1) + '</span>';
    });
    html += '</div></div>';
    const dc = $('diagContent');
    if (dc) dc.innerHTML = html;
  } catch (e) { toast('Error', 'err'); }
}

async function loadContradictions() {
  try {
    const r = await api('/api/contradictions?n=20');
    const items = r.contradictions || [];
    let html = '<div class="card"><div class="card-label">⚡ Contradictions</div><div class="diag-list">';
    if (!items.length) html += '<span class="dim">No contradictions found.</span>';
    else items.forEach((c) => {
      html += '<div class="dl-item"><span class="dl-label">' + esc(c.fact1 || '').slice(0, 80) + '</span>'
        + '<span class="dl-val">vs ' + esc(c.fact2 || '').slice(0, 80) + '</span></div>';
    });
    html += '</div></div>';
    const dc = $('diagContent');
    if (dc) dc.innerHTML = html;
  } catch (e) { toast('Error', 'err'); }
}

async function loadCuriosity() {
  try {
    const r = await api('/api/curiosity_queue');
    const items = r.queue || [];
    let html = '<div class="card"><div class="card-label">🔍 Curiosity Queue</div><div class="diag-list">';
    if (!items.length) html += '<span class="dim">Queue empty.</span>';
    else items.forEach((q) => {
      html += '<div class="dl-item"><span class="dl-label">' + esc(q.query || '').slice(0, 60) + '</span>'
        + '<span class="dl-val">priority: ' + (q.priority || '—') + '</span></div>';
    });
    html += '</div></div>';
    const dc = $('diagContent');
    if (dc) dc.innerHTML = html;
  } catch (e) { toast('Error', 'err'); }
}

async function loadAutoTune() {
  try {
    const r = await api('/api/auto_tune_log');
    const items = r.log || [];
    let html = '<div class="card"><div class="card-label">📊 Auto-Tune Log</div><div class="diag-list">';
    if (!items.length) html += '<span class="dim">No tuning events.</span>';
    else items.forEach((t) => {
      html += '<div class="dl-item"><span class="dl-label">' + (t.ts || '') + '</span>'
        + '<span class="dl-val">' + esc(t.action || JSON.stringify(t)).slice(0, 200) + '</span></div>';
    });
    html += '</div></div>';
    const dc = $('diagContent');
    if (dc) dc.innerHTML = html;
  } catch (e) { toast('Error', 'err'); }
}

/* ═════════════════════════════════════════════════════════
   MODELS
   ═════════════════════════════════════════════════════════ */
let _dlPoll = null;

async function loadModels() {
  const ml = $('modelList');
  const rl = $('recommendedList');
  if (ml) ml.innerHTML = '<div class="skeleton" style="height:80px;margin-bottom:8px"></div>';
  if (rl) rl.innerHTML = '<div class="skeleton" style="height:60px;margin-bottom:6px"></div>';
  try {
    const r = await api('/api/models');
    let installedHtml = '';
    let recHtml = '';
    const presets = [];
    for (const m of r.models) {
      if (m.custom) {
        const st = m.present
          ? (m.valid ? '<span class="badge badge-green">✓</span>' : '<span class="badge badge-red">⚠</span>')
          : '';
        const act = m.active ? '<span class="badge badge-accent">ACTIVE</span>' : '';
        const btns = [];
        if (m.present && m.valid && !m.active) btns.push('<button class="btn btn-sm btn-primary" onclick="switchModel(\'' + esc(m.file) + '\',\'' + esc(m.name) + '\')">⇄ Switch</button>');
        if (m.present) btns.push('<button class="btn btn-sm btn-danger" onclick="deleteModel(\'' + esc(m.file) + '\')">🗑 Delete</button>');
        if (m.present && !m.valid) btns.push('<button class="btn btn-sm" onclick="redownloadModel(\'' + esc(m.file) + '\')">↻ Re-download</button>');
        installedHtml += '<div class="mcard"><div class="mcard-top"><span class="mcard-name">' + esc(m.name) + ' ' + act + ' ' + st + '</span></div>'
          + '<div class="mcard-meta"><span>' + (m.size || '') + '</span></div>'
          + '<div class="mcard-actions">' + btns.join('') + '</div></div>';
        continue;
      }
      presets.push(m);
      const st = m.present
        ? (m.valid ? '<span class="badge badge-green">✓</span>' : '<span class="badge badge-red">⚠ corrupt</span>')
        : '<span class="badge badge-dim">not on disk</span>';
      const act = m.active ? '<span class="badge badge-accent">ACTIVE</span>' : '';
      const btns = [];
      if (m.present && m.valid && !m.active) btns.push('<button class="btn btn-sm btn-primary" onclick="switchModel(\'' + esc(m.file) + '\',\'' + esc(m.name) + '\')">⇄ Switch</button>');
      if (m.present) btns.push('<button class="btn btn-sm btn-danger" onclick="deleteModel(\'' + esc(m.file) + '\')">🗑 Delete</button>');
      if (m.present && !m.valid) btns.push('<button class="btn btn-sm" onclick="redownloadModel(\'' + esc(m.file) + '\')">↻ Re-download</button>');
      if (!m.present) btns.push('<button class="btn btn-sm btn-primary" onclick="startDownload(\'' + esc(m.file) + '\',\'' + esc(m.url) + '\',\'' + esc(m.name) + '\')">⬇ Download</button>');
      installedHtml += '<div class="mcard"><div class="mcard-top"><span class="mcard-name">' + esc(m.name) + ' ' + act + ' ' + st + '</span></div>'
        + '<div class="mcard-meta"><span>' + (m.size || '') + '</span></div>'
        + '<div class="mcard-actions">' + btns.join('') + '</div></div>';
    }
    const recs = presets.filter((mn) => !mn.present);
    if (recs.length) {
      recHtml = recs.map((mn) =>
        '<div class="mcard" style="background:var(--surface)"><div class="mcard-top"><span class="mcard-name">' + esc(mn.name) + '</span>'
        + '<span class="badge badge-dim">' + (mn.size || '') + '</span></div>'
        + '<div class="mcard-actions"><button class="btn btn-sm btn-primary" onclick="startDownload(\'' + esc(mn.file) + '\',\'' + esc(mn.url) + '\',\'' + esc(mn.name) + '\')">⬇ Download</button></div></div>'
      ).join('');
    } else {
      recHtml = '<div class="dim" style="padding:8px 0;font-size:12px">All recommended models are installed.</div>';
    }
    if (ml) ml.innerHTML = installedHtml || '<div class="empty-state"><div class="es-title">No models on disk</div><div class="es-desc">Download from Recommended below, or use Custom Download.</div></div>';
    if (rl) rl.innerHTML = recHtml;
  } catch (e) {
    if (ml) ml.innerHTML = '<div class="empty-state"><div class="es-title">Error loading models</div><div class="es-desc">' + esc(e.message) + '</div></div>';
  }
}

async function startDownload(file, url, name) {
  if (_dlPoll) { toast('Download in progress', 'info'); return; }
  try {
    const r = await fetch('/api/models/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file, url, name }),
    }).then((r) => r.json());
    if (r.error) { toast(r.error, 'err'); return; }
    toast('Downloading: ' + name, 'info');
    pollDownload();
  } catch (e) { toast(e.message, 'err'); }
}

async function redownloadModel(file) {
  if (_dlPoll) { toast('Download in progress', 'info'); return; }
  showModal('Re-download', 'Download ' + file + ' again? Existing file will be overwritten.', 'Re-download', async () => {
    try {
      const r = await fetch('/api/models/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file, url: '', force: true }),
      }).then((r) => r.json());
      if (r.error) { toast(r.error, 'err'); return; }
      toast('Re-downloading: ' + file, 'info');
      pollDownload();
    } catch (e) { toast(e.message, 'err'); }
  });
}

async function downloadCustom() {
  const url = $('customUrl').value.trim();
  const file = $('customFile').value.trim();
  if (!url || !file) { toast('URL and filename required', 'err'); return; }
  if (!file.endsWith('.gguf')) { toast('Filename must end in .gguf', 'err'); return; }
  await startDownload(file, url, file.replace('.gguf', ''));
}

function pollDownload() {
  const dp = $('dlProgress');
  if (dp) dp.style.display = 'block';
  const bar = $('dlBarFill');
  const txt = $('dlText');
  _dlPoll = setInterval(async () => {
    try {
      const s = await api('/api/models/download_progress');
      let pct = 0;
      if (s.progress && s.progress.includes('%')) {
        const m = s.progress.match(/(\d+\.?\d*)%/);
        if (m) pct = parseFloat(m[1]);
      }
      if (bar) bar.style.width = Math.min(pct, 99) + '%';
      if (txt) {
        if (s.error) { txt.textContent = '✗ ' + s.error; txt.style.color = 'var(--accent4)'; if (bar) bar.style.background = 'var(--accent4)'; }
        else if (s.progress === 'done') { txt.textContent = '✓ Done!'; txt.style.color = 'var(--accent3)'; if (bar) { bar.style.width = '100%'; bar.style.background = 'var(--accent3)'; } }
        else { txt.textContent = String(s.progress).slice(-80); txt.style.color = 'var(--yellow)'; }
      }
      if (!s.active) {
        clearInterval(_dlPoll);
        _dlPoll = null;
        setTimeout(() => { if (dp) dp.style.display = 'none'; loadModels(); }, 2500);
      }
    } catch (e) { /* ignore */ }
  }, 1500);
}

async function switchModel(file, name) {
  showModal('Switch Model', 'Switch to <b>' + esc(name) + '</b>?<br>The LLM server will restart.', 'Switch', async () => {
    try {
      const r = await api('/api/models/switch', { method: 'POST', body: JSON.stringify({ file, name }) });
      toast('Switched to ' + name);
      setTimeout(loadModels, 4000);
    } catch (e) { toast(e.message, 'err'); }
  });
}

async function deleteModel(file) {
  showModal('Delete Model', 'Delete <b>' + esc(file) + '</b>?<br>This cannot be undone.', 'Delete', async () => {
    try {
      const r = await api('/api/models/delete', { method: 'POST', body: JSON.stringify({ file }) });
      toast('Deleted: ' + file);
      loadModels();
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* ═════════════════════════════════════════════════════════
   SETTINGS
   ═════════════════════════════════════════════════════════ */
async function loadSettings() {
  try {
    const s = await api('/api/settings');
    const setVal = (id, val) => { const el = $(id); if (el) el.value = val; };
    setVal('s-temp', s.temperature);
    setVal('s-mtok', s.main_max_tokens);
    setVal('s-rep', s.repeat_penalty);
    setVal('s-meval', s.mem_eval_interval);
    setVal('s-bufw', s.buffer_window);
    setVal('s-lts', s.lt_score_min);
    setVal('s-mmtok', s.memory_max_tokens);
    setVal('s-city', s.default_city);
    setVal('s-idle', s.idle_sleep_minutes);
    setVal('s-pro', s.proactivity_level);
    setVal('s-sr', s.search_results);
    toggleSet('t-semantic', s.semantic_enabled);
  } catch (e) { toast('Error loading settings', 'err'); }
}

async function saveSettings() {
  const getVal = (id) => { const el = $(id); return el ? el.value : null; };
  const data = {
    temperature: parseFloat(getVal('s-temp')),
    main_max_tokens: parseInt(getVal('s-mtok')),
    repeat_penalty: parseFloat(getVal('s-rep')),
    mem_eval_interval: parseInt(getVal('s-meval')),
    buffer_window: parseInt(getVal('s-bufw')),
    lt_score_min: parseInt(getVal('s-lts')),
    memory_max_tokens: parseInt(getVal('s-mmtok')),
    default_city: getVal('s-city'),
    idle_sleep_minutes: parseInt(getVal('s-idle')),
    proactivity_level: parseInt(getVal('s-pro')),
    search_results: parseInt(getVal('s-sr')),
    semantic_enabled: ($('t-semantic') || {}).classList.contains('on'),
  };
  showModal('Save Settings', 'Apply changes and restart the bot?', 'Save & Restart', async () => {
    try {
      await api('/api/settings', { method: 'POST', body: JSON.stringify(data) });
      toast('Settings saved — bot restarting');
    } catch (e) { toast('Error: ' + e.message, 'err'); }
  });
}

async function botCtl(action) {
  try {
    const r = await api('/api/bot_control', { method: 'POST', body: JSON.stringify({ action }) });
    toast(action + ': ' + (r.output ? r.output.slice(-60) : 'done'));
    setTimeout(pollStatus, 2000);
    loadBotPage();
  } catch (e) { toast('Error', 'err'); }
}

/* ═════════════════════════════════════════════════════════
   BOT PAGE
   ═════════════════════════════════════════════════════════ */
async function loadBotPage() {
  try {
    const s = await api('/api/status');
    const statusEl = $('bot-status-text');
    const modeEl = $('bot-mode-text');
    if (statusEl) { statusEl.textContent = s.bot ? 'RUNNING' : 'STOPPED'; statusEl.style.color = s.bot ? 'var(--accent3)' : 'var(--accent4)'; }
    if (modeEl) { modeEl.textContent = s.llm_server ? 'Online' : 'Offline'; modeEl.style.color = s.llm_server ? 'var(--accent3)' : 'var(--text2)'; }
  } catch (e) { /* ignore */ }
  try {
    const cfg = await api('/api/settings');
    if (cfg.telegram_token) {
      $('bot-token').value = cfg.telegram_token;
      toggleSet('t-telegram', true);
    }
  } catch (e) { /* ignore */ }
}

async function saveTelegramToken() {
  const token = $('bot-token').value.trim();
  if (!token) { toast('Enter a token', 'err'); return; }
  try {
    await api('/api/settings', { method: 'POST', body: JSON.stringify({ telegram_token: token }) });
    toast('Token saved — restart bot to apply');
  } catch (e) { toast(e.message, 'err'); }
}

async function testTelegramToken() {
  const token = $('bot-token').value.trim();
  if (!token) { toast('Enter a token first', 'err'); return; }
  try {
    const r = await fetch('https://api.telegram.org/bot' + token + '/getMe').then((r) => r.json());
    if (r.ok) {
      const username = r.result ? r.result.username || '?' : '?';
      toast('✓ Connected as @' + username + ' — ID: ' + (r.result ? r.result.id : '?'));
    } else {
      toast('✗ Invalid: ' + (r.description || 'unknown error'), 'err');
    }
  } catch (e) {
    toast('Connection error: ' + e.message, 'err');
  }
}

let _tokenVis = false;
function toggleTokenVis() {
  _tokenVis = !_tokenVis;
  const el = $('bot-token');
  if (el) el.type = _tokenVis ? 'text' : 'password';
}

let _logVisible = false;
async function toggleLog() {
  const area = $('botLog');
  if (!area) return;
  _logVisible = !_logVisible;
  area.style.display = _logVisible ? 'block' : 'none';
  const btn = $('logToggleBtn');
  if (btn) btn.textContent = _logVisible ? 'Hide Log' : 'Show Log';
  if (!_logVisible) return;
  try {
    const r = await api('/api/bot_log');
    area.innerHTML = (r.lines || []).map((l) => {
      let cls = '';
      if (l.includes('ERROR') || l.includes('error')) cls = 'err';
      else if (l.includes('WARNING')) cls = 'warn';
      else if (l.includes('INFO')) cls = 'info';
      return '<span class="' + cls + '">' + esc(l) + '</span>';
    }).join('\n') || '(empty)';
    area.scrollTop = area.scrollHeight;
  } catch (e) { area.textContent = 'Error loading log'; }
}

/* ═════════════════════════════════════════════════════════
   VERSION & UPDATES
   ═════════════════════════════════════════════════════════ */
async function loadVersion() {
  try {
    const v = await api('/api/version');
    const cur = $('v-current');
    const com = $('v-commit');
    const beh = $('v-behind');
    if (cur) cur.textContent = v.current_version || '—';
    if (com) com.textContent = v.git_ok ? '#' + (v.commit || '—') : '—';
    if (beh) {
      if (!v.git_ok) {
        beh.textContent = 'no git repo';
        beh.className = 'badge badge-dim';
      } else {
        beh.textContent = 'tap Check';
        beh.className = 'badge badge-dim';
      }
    }
    const btn = $('btn-update');
    if (btn) btn.style.display = 'none';
  } catch (e) {
    const cur = $('v-current');
    if (cur) cur.textContent = 'error';
  }
}

async function checkUpdate() {
  const log = $('updateLog');
  if (!log) return;
  log.style.display = 'block';
  log.textContent = 'Fetching from GitHub…';
  const btn = $('btn-update');
  if (btn) btn.disabled = true;
  try {
    const v = await api('/api/update/check', { method: 'POST' });
    if (v.status === 'error') {
      log.textContent = '✗ ' + (v.error || 'check failed');
      return;
    }
    const history = (v.history || []).join('\n') || '(no history)';
    if (v.can_update) {
      log.textContent = history + '\n\n⬇ ' + v.behind + ' commit' + (v.behind > 1 ? 's' : '') + ' behind';
      if (btn) { btn.style.display = 'inline-flex'; btn.disabled = false; }
      const beh = $('v-behind');
      if (beh) { beh.textContent = v.behind + ' behind'; beh.className = 'badge badge-yellow'; }
    } else {
      log.textContent = history + '\n\n✓ Up to date (' + v.current_version + ')';
      if (btn) btn.style.display = 'none';
      const beh = $('v-behind');
      if (beh) { beh.textContent = 'up to date'; beh.className = 'badge badge-green'; }
    }
  } catch (e) { log.textContent = 'Error: ' + e.message; }
  if (btn) btn.disabled = false;
}

async function doUpdate() {
  showModal('Pull Update', 'Pull latest code from GitHub?<br>The web server will restart.', 'Pull', async () => {
    const log = $('updateLog');
    if (log) { log.style.display = 'block'; log.textContent = 'Pulling…'; }
    const btn = $('btn-update');
    if (btn) btn.disabled = true;
    try {
      const r = await api('/api/update/pull', { method: 'POST' });
      if (log) log.textContent = r.output || 'Done. Version: ' + (r.version || '?');
      setTimeout(loadVersion, 3000);
    } catch (e) { if (log) log.textContent = 'Error: ' + e.message; }
    if (btn) btn.disabled = false;
  });
}

async function doRollback() {
  showModal('Rollback', 'Rollback to the previous commit?<br>Local changes will be lost.', 'Rollback', async () => {
    const log = $('updateLog');
    if (log) { log.style.display = 'block'; log.textContent = 'Rolling back…'; }
    try {
      const r = await api('/api/update/rollback', { method: 'POST', body: JSON.stringify({ steps: 1 }) });
      if (log) log.textContent = (r.output || 'Done') + '\nVersion: ' + (r.version || '?');
      setTimeout(loadVersion, 3000);
    } catch (e) { if (log) log.textContent = 'Error: ' + e.message; }
  });
}

async function doReinstall() {
  showModal('Reinstall', 'Reinstall NERMANA?<br>Models preserved. Server terminal shows progress.', 'Reinstall', async () => {
    const log = $('updateLog');
    if (log) { log.style.display = 'block'; log.textContent = 'Reinstalling…'; }
    try {
      const r = await api('/api/reinstall', { method: 'POST' });
      if (log) log.textContent = (r.output || 'Done') + '\nVersion: ' + (r.version || '?');
    } catch (e) { if (log) log.textContent = 'Error: ' + e.message; }
  });
}

/* ═════════════════════════════════════════════════════════
   FILE MANAGER
   ═════════════════════════════════════════════════════════ */
let _fmPath = '';
let _fmFile = '';

function fmSize(b) {
  if (!b) return '';
  if (b < 1024) return b + 'B';
  if (b < 1048576) return (b / 1024).toFixed(1) + 'KB';
  return (b / 1048576).toFixed(1) + 'MB';
}

function fmTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fmGoHome() { _fmPath = ''; fmLoad(); }

function fmUp() {
  if (_fmPath) {
    const p = _fmPath.split('/');
    p.pop();
    _fmPath = p.join('/');
  }
  fmLoad();
}

function fmBreadClick(e) {
  const idx = e.target.dataset.idx;
  if (idx !== undefined) {
    const parts = _fmPath.split('/').filter(Boolean);
    _fmPath = parts.slice(0, parseInt(idx) + 1).join('/');
    fmLoad();
  }
}

async function fmLoad() {
  // close editors
  const editor = $('fmEditor');
  const upload = $('fmUpload');
  const toolbar = $('fmToolbar');
  if (editor) editor.classList.remove('open');
  if (upload) upload.classList.remove('open');
  if (toolbar) toolbar.style.display = 'none';
  // breadcrumb
  const parts = _fmPath.split('/').filter(Boolean);
  let bc = '<span data-idx="-1" onclick="fmGoHome()" style="color:var(--accent);cursor:pointer">~</span>';
  parts.forEach((p, i) => {
    bc += '<span style="color:var(--dim);margin:0 2px">/</span>'
      + '<span data-idx="' + i + '" style="color:' + (i === parts.length - 1 ? 'var(--text)' : 'var(--text2)') + ';cursor:pointer">' + esc(p) + '</span>';
  });
  const pathEl = $('fmPath');
  if (pathEl) {
    pathEl.innerHTML = '<span style="font-size:11px;color:var(--dim);margin-right:4px">nermana/</span>' + bc;
    pathEl.onclick = fmBreadClick;
  }
  // loading
  const listEl = $('fmList');
  if (listEl) listEl.innerHTML = '<div class="skeleton" style="height:60px;margin-bottom:5px"></div><div class="skeleton" style="height:60px;margin-bottom:5px"></div>';
  try {
    const r = await api('/api/files?path=' + encodeURIComponent(_fmPath));
    if (!r.is_dir) {
      const editArea = $('fmEditArea');
      const editPath = $('fmEditPath');
      if (editArea) editArea.value = r.content || '';
      _fmFile = r.path;
      if (editPath) editPath.textContent = r.path;
      if (editor) editor.classList.add('open');
      if (listEl) listEl.innerHTML = '';
      return;
    }
    let h = '';
    const dirs = (r.entries || []).filter((e) => e.is_dir).sort((a, b) => a.name.localeCompare(b.name));
    const files = (r.entries || []).filter((e) => !e.is_dir).sort((a, b) => a.name.localeCompare(b.name));
    for (const e of [...dirs, ...files]) {
      const isPy = e.name.endsWith('.py');
      const isMd = e.name.endsWith('.md');
      const isCfg = e.name === '.config';
      const icon = e.is_dir ? '📁' : isPy ? '🐍' : isMd ? '📝' : isCfg ? '⚙' : '📄';
      h += '<div class="fm-entry" onclick="fmOpen(\'' + esc(e.name) + '\',' + e.is_dir + ')">'
        + '<div class="fe-icon">' + icon + '</div>'
        + '<div class="fe-name">' + esc(e.name) + '</div>'
        + '<div class="fe-size">'
        + (e.size != null ? fmSize(e.size) : '')
        + (e.modified ? '<div style="font-size:9px;color:var(--border2)">' + fmTime(e.modified) + '</div>' : '')
        + '</div></div>';
    }
    if (listEl) {
      listEl.innerHTML = h || '<div class="fm-empty"><div class="es-title">Empty directory</div><div class="es-desc" style="font-size:12px">Create a file or directory</div></div>';
    }
  } catch (e) {
    if (listEl) listEl.innerHTML = '<div class="fm-empty"><div class="es-title">Error</div><div class="es-desc">' + esc(e.message) + '</div></div>';
  }
}

function fmOpen(name, isDir) {
  if (isDir) {
    _fmPath = _fmPath ? _fmPath + '/' + name : name;
    fmLoad();
  } else {
    _fmFile = _fmPath ? _fmPath + '/' + name : name;
    fmLoad();
  }
}

async function fmSave() {
  const content = $('fmEditArea');
  if (!content || !_fmFile) { toast('Nothing to save', 'err'); return; }
  try {
    await api('/api/files/save', { method: 'POST', body: JSON.stringify({ path: _fmFile, content: content.value }) });
    toast('Saved: ' + _fmFile);
  } catch (e) { toast(e.message, 'err'); }
}

function fmCancel() {
  const editor = $('fmEditor');
  if (editor) editor.classList.remove('open');
  _fmFile = '';
  fmLoad();
}

async function fmDelete() {
  const path = _fmFile || _fmPath;
  if (!path) { toast('Nothing selected', 'err'); return; }
  const name = path.split('/').pop() || 'this';
  const isDir = !!_fmPath && !_fmFile;
  showModal('Delete ' + (isDir ? 'Directory' : 'File'), 'Delete <b>' + esc(name) + '</b>?<br>This cannot be undone.', 'Delete', async () => {
    try {
      await api('/api/files/delete', { method: 'POST', body: JSON.stringify({ path }) });
      toast('Deleted: ' + name);
      _fmFile = '';
      const editor = $('fmEditor');
      if (editor) editor.classList.remove('open');
      fmUp();
    } catch (e) { toast(e.message, 'err'); }
  });
}

async function fmRename() {
  const path = _fmFile || _fmPath;
  if (!path) { toast('Nothing selected', 'err'); return; }
  const old = path.split('/').pop() || '';
  const neu = prompt('Rename to:', old);
  if (!neu || neu === old) return;
  try {
    const r = await api('/api/files/rename', { method: 'POST', body: JSON.stringify({ path, name: neu }) });
    toast('Renamed to ' + neu);
    if (_fmFile) _fmFile = r.path;
    fmLoad();
  } catch (e) { toast(e.message, 'err'); }
}

function fmNewFile() {
  const name = prompt('New file name:', 'untitled.txt');
  if (!name) return;
  const path = _fmPath ? (_fmPath + '/' + name) : name;
  api('/api/files/create', {
    method: 'POST',
    body: JSON.stringify({ path, kind: 'file', content: '' }),
  })
    .then(() => { toast('Created: ' + name); fmOpen(name, false); })
    .catch((e) => toast(e.message, 'err'));
}

function fmNewDir() {
  const name = prompt('New directory name:', 'new_folder');
  if (!name) return;
  const path = _fmPath ? (_fmPath + '/' + name) : name;
  api('/api/files/create', { method: 'POST', body: JSON.stringify({ path, kind: 'dir' }) })
    .then(() => { toast('Created: ' + name); fmLoad(); })
    .catch((e) => toast(e.message, 'err'));
}

function fmRefresh() { fmLoad(); }

function fmUploadToggle() {
  const u = $('fmUpload');
  if (u) u.classList.toggle('open');
}

async function fmUploadFile(input) {
  const file = input.files ? input.files[0] : null;
  if (!file) return;
  const dest = _fmPath ? (_fmPath + '/' + file.name) : file.name;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('path', dest);
  try {
    const r = await fetch('/api/files/upload', { method: 'POST', body: fd }).then((r) => r.json());
    if (r.error) { toast(r.error, 'err'); return; }
    toast('Uploaded: ' + file.name + ' (' + fmSize(r.size) + ')');
    const upload = $('fmUpload');
    if (upload) upload.classList.remove('open');
    fmLoad();
  } catch (e) { toast(e.message, 'err'); }
  input.value = '';
}

/* ═════════════════════════════════════════════════════════
   INIT
   ═════════════════════════════════════════════════════════ */
document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key === 'Enter') sendChat();
  if (e.ctrlKey && e.key === 's') {
    const editor = $('fmEditor');
    if (editor && editor.classList.contains('open')) { e.preventDefault(); fmSave(); }
  }
});
