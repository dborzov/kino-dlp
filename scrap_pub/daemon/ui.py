"""
ui.py — Single-file web UI served from GET /.

Vanilla JS + CSS, no build step, no framework dependencies.
Connects to the WebSocket server on ws_port (default 8766) for real-time updates.
"""

HTML_UI = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>scrap-pub</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:       #0f1117;
  --surface:  #1a1d27;
  --border:   #2d3044;
  --accent:   #5b8dee;
  --green:    #4caf77;
  --red:      #e05c5c;
  --yellow:   #d4a843;
  --muted:    #8b93aa;
  --text:     #d8dce8;
  --text-dim: #6b7391;
  font-size: 14px;
}

body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; }

/* ── Header ── */
header {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 18px; background: var(--surface);
  border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 10;
}
header h1 { font-size: 1rem; font-weight: 600; color: var(--accent); }
.stat { font-size: .8rem; color: var(--muted); }
.stat b { color: var(--text); }
.ws-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--red); display: inline-block; }
.ws-dot.ok { background: var(--green); }
.cookie-warn { background: var(--red); color: #fff; padding: 2px 8px; border-radius: 4px; font-size: .8rem; }
.spacer { flex: 1; }
button { cursor: pointer; border: none; border-radius: 5px; padding: 5px 12px;
         font-size: .8rem; font-family: inherit; }
.btn-primary { background: var(--accent); color: #fff; }
.btn-ghost   { background: transparent; color: var(--muted); border: 1px solid var(--border); }
.btn-ghost:hover { color: var(--text); border-color: var(--muted); }
.btn-sm { padding: 3px 8px; font-size: .75rem; }
.btn-danger { background: var(--red); color: #fff; }
.btn-success { background: var(--green); color: #fff; }

/* ── Tabs ── */
nav { display: flex; border-bottom: 1px solid var(--border); background: var(--surface); padding: 0 18px; }
nav button {
  background: transparent; color: var(--muted); border: none;
  padding: 10px 16px; font-size: .85rem; border-bottom: 2px solid transparent;
  cursor: pointer; transition: color .15s;
}
nav button.active { color: var(--accent); border-bottom-color: var(--accent); }
nav button:hover { color: var(--text); }

.tab-content { display: none; padding: 18px; }
.tab-content.active { display: block; }

/* ── Enqueue form ── */
.enqueue-form { display: flex; gap: 8px; margin-bottom: 16px; }
.enqueue-form input {
  flex: 1; background: var(--surface); border: 1px solid var(--border);
  color: var(--text); padding: 8px 12px; border-radius: 5px; font-size: .85rem;
  font-family: inherit;
}
.enqueue-form input:focus { outline: none; border-color: var(--accent); }

/* ── Task list ── */
.section-title { font-size: .75rem; color: var(--muted); text-transform: uppercase;
                 letter-spacing: .06em; margin-bottom: 8px; }
.task-list { display: flex; flex-direction: column; gap: 6px; }

.task-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 7px; padding: 10px 14px;
}
.task-card:hover { border-color: var(--accent); }
.task-header { display: flex; align-items: center; gap: 10px; }
.task-title { font-weight: 500; flex: 1; font-size: .88rem; overflow: hidden;
              white-space: nowrap; text-overflow: ellipsis; }
.task-meta { font-size: .75rem; color: var(--muted); }
.badge {
  font-size: .7rem; padding: 2px 7px; border-radius: 3px;
  font-weight: 600; text-transform: uppercase; white-space: nowrap;
}
.badge.pending    { background: #2d3044; color: var(--muted); }
.badge.active     { background: #1e3a5f; color: var(--accent); }
.badge.done       { background: #1a3328; color: var(--green); }
.badge.failed     { background: #3a1a1a; color: var(--red); }
.badge.skipped    { background: #2d2a1a; color: var(--yellow); }
.task-actions { display: flex; gap: 5px; }
.task-output-dir {
  margin-top: 4px; font-size: .72rem; color: var(--muted);
  font-family: ui-monospace, monospace; overflow: hidden;
  white-space: nowrap; text-overflow: ellipsis;
}

/* ── Streams ── */
.streams { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.stream-row { display: flex; align-items: center; gap: 10px; font-size: .78rem; }
.stream-type { width: 50px; color: var(--muted); }
.stream-label { flex: 1; color: var(--text-dim); overflow: hidden;
                white-space: nowrap; text-overflow: ellipsis; }
.stream-progress-wrap { width: 120px; position: relative; }
progress { width: 100%; height: 6px; border-radius: 3px; appearance: none; }
progress::-webkit-progress-bar  { background: var(--border); border-radius: 3px; }
progress::-webkit-progress-value { background: var(--accent); border-radius: 3px; transition: width .3s; }
progress.done::-webkit-progress-value { background: var(--green); }
.stream-speed { width: 55px; color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }
.stream-size  { width: 65px; color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }
.stream-status { width: 60px; text-align: right; }

/* ── Logs ── */
.log-toolbar { display: flex; gap: 8px; margin-bottom: 10px; align-items: center; }
.log-toolbar select {
  background: var(--surface); border: 1px solid var(--border); color: var(--text);
  padding: 5px 10px; border-radius: 5px; font-family: inherit; font-size: .8rem;
}
#log-output {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 7px; padding: 12px; height: 450px; overflow-y: auto;
  font-family: 'SFMono-Regular', 'Consolas', monospace; font-size: .78rem;
  line-height: 1.6;
}
.log-line { display: flex; gap: 10px; }
.log-ts    { color: var(--text-dim); white-space: nowrap; flex-shrink: 0; }
.log-level { width: 40px; flex-shrink: 0; }
.log-level.INFO  { color: var(--muted); }
.log-level.WARN  { color: var(--yellow); }
.log-level.ERROR { color: var(--red); }
.log-task { color: var(--accent); width: 52px; flex-shrink: 0; font-size: .72rem; }
.log-msg  { color: var(--text); }

/* ── Settings ── */
.settings-section { margin-bottom: 24px; }
.settings-section h3 { font-size: .8rem; color: var(--muted); text-transform: uppercase;
                        letter-spacing: .06em; margin-bottom: 12px; }
.field-row { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.field-label { width: 150px; color: var(--muted); font-size: .85rem; }
.field-row input, .field-row select, .field-row textarea {
  background: var(--surface); border: 1px solid var(--border); color: var(--text);
  padding: 6px 10px; border-radius: 5px; font-family: inherit; font-size: .85rem; flex: 1;
}
.field-row textarea { height: 90px; resize: vertical; font-size: .78rem; }
.field-row input:focus, .field-row textarea:focus {
  outline: none; border-color: var(--accent);
}
.cookie-status-ok   { color: var(--green); font-weight: 600; }
.cookie-status-fail { color: var(--red); font-weight: 600; }
.note { font-size: .75rem; color: var(--muted); margin-top: 4px; }

/* ── Empty state ── */
.empty { text-align: center; color: var(--muted); padding: 40px; font-size: .9rem; }

/* ── Filter bar ── */
.filter-bar { display: flex; gap: 6px; margin-bottom: 12px; align-items: center; flex-wrap: wrap; }
.filter-bar label { font-size: .78rem; color: var(--muted); }
.filter-bar select {
  background: var(--surface); border: 1px solid var(--border); color: var(--text);
  padding: 4px 8px; border-radius: 4px; font-family: inherit; font-size: .78rem;
}
.filter-bar button.chip {
  background: var(--surface); color: var(--muted);
  border: 1px solid var(--border); padding: 3px 10px;
  border-radius: 12px; font-size: .72rem;
}
.filter-bar button.chip.on {
  background: var(--accent); color: #fff; border-color: var(--accent);
}

/* ── Task row extras ── */
.task-size {
  font-size: .72rem; color: var(--muted); font-variant-numeric: tabular-nums;
  background: #22263a; padding: 2px 7px; border-radius: 3px;
}
.task-time { font-size: .72rem; color: var(--text-dim); font-variant-numeric: tabular-nums; }
.stream-pct { width: 38px; color: var(--text-dim); text-align: right;
              font-variant-numeric: tabular-nums; font-size: .72rem; }
.stream-eta { width: 44px; color: var(--muted); text-align: right;
              font-variant-numeric: tabular-nums; font-size: .72rem; }

/* ── Toast stack ── */
#toast-stack {
  position: fixed; top: 60px; right: 16px; z-index: 1000;
  display: flex; flex-direction: column; gap: 8px; max-width: 420px;
}
.toast {
  background: var(--surface); border: 1px solid var(--border);
  border-left: 3px solid var(--accent); color: var(--text);
  padding: 10px 14px; border-radius: 5px; font-size: .82rem;
  box-shadow: 0 4px 16px rgba(0,0,0,.4);
  display: flex; gap: 10px; align-items: flex-start;
  animation: toast-in .2s ease-out;
}
.toast.error { border-left-color: var(--red); }
.toast.warn  { border-left-color: var(--yellow); }
.toast.info  { border-left-color: var(--accent); }
.toast .toast-msg { flex: 1; word-break: break-word; }
.toast .toast-x {
  background: none; color: var(--muted); padding: 0 4px;
  font-size: 1rem; line-height: 1; cursor: pointer;
}
.toast .toast-x:hover { color: var(--text); }
@keyframes toast-in {
  from { opacity: 0; transform: translateX(16px); }
  to   { opacity: 1; transform: translateX(0); }
}
</style>
</head>
<body>

<div id="toast-stack"></div>

<header>
  <span class="ws-dot" id="ws-dot"></span>
  <h1>scrap-pub</h1>
  <span class="stat">Workers: <b id="hdr-workers">0</b></span>
  <span class="stat">Queued: <b id="hdr-queued">0</b></span>
  <span class="stat">Done: <b id="hdr-done">0</b></span>
  <span id="cookie-warn" class="cookie-warn" style="display:none">⚠ Cookies expired</span>
  <span class="spacer"></span>
  <button id="btn-pause" class="btn-ghost btn-sm">⏸ Pause</button>
</header>

<nav>
  <button class="active" onclick="showTab('queue')">Queue</button>
  <button onclick="showTab('active')">Active</button>
  <button onclick="showTab('done')">Done</button>
  <button onclick="showTab('logs')">Logs</button>
  <button onclick="showTab('settings')">Settings</button>
</nav>

<!-- ══ Queue ══════════════════════════════════════════════════════════ -->
<div id="tab-queue" class="tab-content active">
  <div class="enqueue-form">
    <input id="enqueue-url" type="text" placeholder="https://your-site.example/item/view/..." />
    <input id="enqueue-output-dir" type="text" list="output-dir-suggestions"
           placeholder="/mnt/plex/TV Shows (optional — leave empty for default)" />
    <datalist id="output-dir-suggestions"></datalist>
    <button class="btn-primary" onclick="doEnqueue()">+ Add</button>
  </div>
  <div class="section-title" id="pending-title">Pending (0)</div>
  <div class="task-list" id="pending-list"></div>
</div>

<!-- ══ Active ═════════════════════════════════════════════════════════ -->
<div id="tab-active" class="tab-content">
  <div class="task-list" id="active-list">
    <div class="empty">No active downloads</div>
  </div>
</div>

<!-- ══ Done ══════════════════════════════════════════════════════════ -->
<div id="tab-done" class="tab-content">
  <div class="filter-bar">
    <label>Since:</label>
    <button class="chip" data-since="today"     onclick="setSinceFilter('today')">Today</button>
    <button class="chip on" data-since="week"   onclick="setSinceFilter('week')">Week</button>
    <button class="chip" data-since="month"     onclick="setSinceFilter('month')">Month</button>
    <button class="chip" data-since=""          onclick="setSinceFilter('')">All</button>
    <span style="width:12px"></span>
    <label>Kind:</label>
    <select id="filter-kind" onchange="loadTasks()">
      <option value="">all</option>
      <option value="movie">movie</option>
      <option value="episode">episode</option>
    </select>
  </div>
  <div class="section-title">Done</div>
  <div class="task-list" id="done-list"></div>
  <div class="section-title" style="margin-top:16px">Failed</div>
  <div class="task-list" id="failed-list"></div>
</div>

<!-- ══ Logs ══════════════════════════════════════════════════════════ -->
<div id="tab-logs" class="tab-content">
  <div class="log-toolbar">
    <label style="color:var(--muted);font-size:.8rem">Task:</label>
    <select id="log-task-filter" onchange="filterLogs()">
      <option value="">all</option>
    </select>
    <label style="color:var(--muted);font-size:.8rem">Level:</label>
    <select id="log-level-filter" onchange="filterLogs()">
      <option value="">all</option>
      <option value="INFO">INFO</option>
      <option value="WARN">WARN</option>
      <option value="ERROR">ERROR</option>
    </select>
    <label style="font-size:.8rem;color:var(--muted)">
      <input type="checkbox" id="log-autoscroll" checked> Auto-scroll
    </label>
    <button class="btn-ghost btn-sm" onclick="clearLogs()">Clear</button>
  </div>
  <div id="log-output"></div>
</div>

<!-- ══ Settings ══════════════════════════════════════════════════════ -->
<div id="tab-settings" class="tab-content">
  <div class="settings-section">
    <h3>Session Cookies</h3>
    <div class="field-row">
      <span class="field-label">Status</span>
      <span id="cookie-status" class="cookie-status-ok">✓ OK</span>
    </div>
    <div class="field-row">
      <span class="field-label">Paste cookies.txt</span>
      <textarea id="cookie-input"
        placeholder="# Netscape HTTP Cookie File&#10;.example.com&#9;TRUE&#9;/&#9;TRUE&#9;0&#9;_identity&#9;..."
      ></textarea>
    </div>
    <div class="field-row">
      <span class="field-label"></span>
      <button class="btn-primary btn-sm" onclick="saveCookies()">Save Cookies</button>
    </div>
    <p class="note" style="margin-left:162px">
      Paste the full contents of a Netscape <code>cookies.txt</code> file
      (same format yt-dlp uses). Export it with the
      <em>Get cookies.txt LOCALLY</em> browser extension after logging
      into the target site. Required cookies: <code>_identity</code>,
      <code>token</code>, <code>_csrf</code>, <code>PHPSESSID</code>,
      <code>cf_clearance</code>.
    </p>
  </div>

  <div class="settings-section">
    <h3>Download Settings</h3>
    <div class="field-row">
      <span class="field-label">Concurrency</span>
      <input type="number" id="cfg-concurrency" min="1" max="10" style="max-width:80px">
      <button class="btn-ghost btn-sm" onclick="setCfg('concurrency', +id('cfg-concurrency').value)">Set</button>
      <span class="note">(requires restart to change worker count)</span>
    </div>
    <div class="field-row">
      <span class="field-label">Stall timeout (s)</span>
      <input type="number" id="cfg-stall" min="60" max="3600" style="max-width:80px">
      <button class="btn-ghost btn-sm" onclick="setCfg('stall_timeout_sec', +id('cfg-stall').value)">Set</button>
    </div>
    <div class="field-row">
      <span class="field-label">Video quality</span>
      <select id="cfg-quality" onchange="setCfg('video_quality', this.value)">
        <option value="lowest">Lowest (default)</option>
        <option value="highest">Highest</option>
        <option value="1080p">1080p</option>
        <option value="720p">720p</option>
      </select>
    </div>
    <div class="field-row">
      <span class="field-label">Output dir</span>
      <input type="text" id="cfg-output" disabled style="color:var(--muted)">
      <span class="note">(change in config file, requires restart)</span>
    </div>
    <div class="field-row">
      <span class="field-label">DB path</span>
      <input type="text" id="cfg-db" disabled style="color:var(--muted)">
    </div>
  </div>
</div>

<script>
// ── State ──────────────────────────────────────────────────────────────────────
const state = {
  tasks: {},       // id → task dict
  streams: {},     // stream_id → stream dict (keyed by task for active display)
  taskStreams: {}, // task_id → [stream_ids]
  logs: [],        // [{ts,level,msg,task_id}]
  paused: false,
  cookieOk: true,
  workers: 0,
  filter: { since: 'week', kind: '' },
  reconnectToast: null,
};

// ── Utilities ──────────────────────────────────────────────────────────────────
const id = x => document.getElementById(x);
function fmtBytes(b) {
  if (!b) return '';
  const kb = 1024, mb = kb*1024, gb = mb*1024;
  if (b < mb) return (b/kb).toFixed(0) + ' KB';
  if (b < gb) return (b/mb).toFixed(1) + ' MB';
  return (b/gb).toFixed(2) + ' GB';
}
function fmtTime(ts) {
  if (!ts) return '';
  return new Date(ts).toLocaleTimeString('en', {hour:'2-digit', minute:'2-digit'});
}
function fmtRelTime(iso) {
  if (!iso) return '';
  const t = new Date(iso);
  if (isNaN(t)) return '';
  const s = Math.round((Date.now() - t.getTime()) / 1000);
  if (s < 0)     return 'just now';
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.floor(s/60)}m ago`;
  if (s < 86400) return `${Math.floor(s/3600)}h ago`;
  return `${Math.floor(s/86400)}d ago`;
}
function fmtEta(sec) {
  if (sec == null) return '';
  const s = Math.max(0, Math.floor(sec));
  if (s < 60)   return '<1m';
  if (s < 3600) return `${Math.floor(s/60)}m`;
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60);
  return m ? `${h}h${m}m` : `${h}h`;
}
function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Toasts ─────────────────────────────────────────────────────────────────────
function showToast(msg, level = 'info', opts = {}) {
  const stack = id('toast-stack');
  if (!stack) return null;
  const el = document.createElement('div');
  el.className = `toast ${level}`;
  el.innerHTML = `<span class="toast-msg"></span><button class="toast-x">×</button>`;
  el.querySelector('.toast-msg').textContent = msg;
  el.querySelector('.toast-x').onclick = () => el.remove();
  stack.appendChild(el);
  if (!opts.sticky && level !== 'error') {
    setTimeout(() => el.remove(), 8000);
  }
  return el;
}

// ── Tab navigation ─────────────────────────────────────────────────────────────
function showTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('nav button').forEach((el, i) => {
    const names = ['queue','active','done','logs','settings'];
    el.classList.toggle('active', names[i] === name);
  });
  id(`tab-${name}`).classList.add('active');
  if (name === 'queue' || name === 'done') loadTasks();
}

// ── WebSocket ──────────────────────────────────────────────────────────────────
let ws = null;
let wsReconnectTimer = null;

function connect() {
  const port = 8766; // ws_port — must match server config
  ws = new WebSocket(`ws://localhost:${port}`);

  ws.onopen = () => {
    id('ws-dot').classList.add('ok');
    clearTimeout(wsReconnectTimer);
    if (state.reconnectToast) { state.reconnectToast.remove(); state.reconnectToast = null; }
    loadTasks();
    sendCmd({cmd: 'output_dir_history'});
  };

  ws.onclose = () => {
    id('ws-dot').classList.remove('ok');
    if (!state.reconnectToast) {
      state.reconnectToast = showToast('Disconnected — reconnecting…', 'warn', {sticky: true});
    }
    wsReconnectTimer = setTimeout(connect, 3000);
  };

  ws.onerror = () => ws.close();

  ws.onmessage = evt => {
    try { handleMsg(JSON.parse(evt.data)); }
    catch(e) { console.error('WS parse error', e); }
  };
}

function sendCmd(obj) {
  if (ws && ws.readyState === WebSocket.OPEN)
    ws.send(JSON.stringify(obj));
}

// ── Message handler ────────────────────────────────────────────────────────────
function handleMsg(msg) {
  switch(msg.type) {
    case 'daemon_status':   handleStatus(msg);   break;
    case 'stream_progress': handleProgress(msg); break;
    case 'task_update':     handleTaskUpdate(msg); break;
    case 'stream_update':   handleStreamUpdate(msg); break;
    case 'task_error':      handleTaskError(msg); break;
    case 'cookie_error':    handleCookieError(msg); break;
    case 'log':             appendLog(msg); break;
    case 'reply':           handleReply(msg); break;
  }
}

function handleStatus(msg) {
  state.paused    = msg.paused;
  state.cookieOk  = msg.cookie_ok;
  state.workers   = msg.active_workers ?? 0;
  id('hdr-workers').textContent = msg.active_workers ?? 0;
  id('hdr-queued').textContent  = msg.counts?.pending ?? msg.queue_depth ?? 0;
  id('hdr-done').textContent    = msg.counts?.done ?? 0;
  id('btn-pause').textContent   = msg.paused ? '▶ Resume' : '⏸ Pause';
  id('cookie-warn').style.display = msg.cookie_ok ? 'none' : '';
  id('cookie-status').textContent = msg.cookie_ok ? '✓ OK' : '⚠ EXPIRED';
  id('cookie-status').className   = msg.cookie_ok ? 'cookie-status-ok' : 'cookie-status-fail';
  if (msg.config) loadConfig(msg.config);
}

function handleProgress(msg) {
  const sid = msg.stream_id;
  if (!state.streams[sid]) state.streams[sid] = {};
  Object.assign(state.streams[sid], msg);
  renderActiveTask(msg.task_id);
}

function handleTaskUpdate(msg) {
  const t = state.tasks[msg.task_id];
  if (t) {
    t.status = msg.status;
    if (msg.mkv_path) t.mkv_path = msg.mkv_path;
  }
  // Refresh relevant lists
  renderPendingList();
  renderActiveList();
  renderDoneList();
}

function handleStreamUpdate(msg) {
  if (state.streams[msg.stream_id])
    Object.assign(state.streams[msg.stream_id], msg);
  renderActiveTask(msg.task_id);
}

function handleTaskError(msg) {
  const note = `Task ${msg.task_id} failed: ${msg.error}`;
  showToast(note, 'error');
  appendLog({
    level: 'ERROR',
    ts: new Date().toISOString(),
    task_id: msg.task_id,
    msg: `Error: ${msg.error} (attempt ${msg.attempt}, will_retry=${msg.will_retry})`,
  });
}

function handleCookieError(msg) {
  state.cookieOk = false;
  id('cookie-warn').style.display = '';
  id('cookie-status').textContent = '⚠ EXPIRED';
  id('cookie-status').className   = 'cookie-status-fail';
  showToast(msg.msg || 'Session cookies expired — workers paused', 'error');
  appendLog({level:'ERROR', ts: new Date().toISOString(), task_id: null, msg: msg.msg});
}

function handleReply(msg) {
  if (!msg.ok) {
    const label = msg.cmd ? `${msg.cmd}: ` : '';
    showToast(`${label}${msg.error || 'unknown error'}`, 'error');
    console.warn('Reply error:', msg);
    return;
  }
  if (msg.cmd === 'list' && msg.tasks) {
    // Merge streams_by_task into state.streams so cards can render live progress.
    const sbt = msg.streams_by_task || {};
    for (const [tid, arr] of Object.entries(sbt)) {
      (arr || []).forEach(s => {
        state.streams[s.id] = { ...(state.streams[s.id] || {}), ...s, task_id: +tid };
      });
    }
    msg.tasks.forEach(t => { state.tasks[t.id] = t; });
    renderPendingList();
    renderActiveList();
    renderDoneList();
  }
  if (msg.cmd === 'enqueue') {
    const n = msg.enqueued || 0;
    if (n === 0) {
      showToast('Already in queue — no new tasks added', 'warn');
    } else {
      id('enqueue-url').value = '';
      showToast(`Enqueued ${n} task(s)`, 'info');
      loadTasks();
    }
    if (msg.output_dir) sendCmd({cmd: 'output_dir_history'});
  }
  if (msg.cmd === 'output_dir_history' && Array.isArray(msg.paths)) {
    const dl = id('output-dir-suggestions');
    dl.innerHTML = msg.paths
      .map(p => `<option value="${escHtml(p)}"></option>`)
      .join('');
  }
}

// ── Render helpers ─────────────────────────────────────────────────────────────
function taskLabel(t) {
  const stem = t.plex_stem || '';
  const leaf = stem.split('/').pop() || `Task #${t.id}`;
  return escHtml(leaf);
}

function metaHTML(t) {
  const rel  = fmtRelTime(t.completed_at || t.started_at || t.enqueued_at);
  const ts   = t.completed_at || t.started_at || t.enqueued_at || '';
  const size = fmtBytes(t.output_size_bytes);
  return `
    ${rel ? `<span class="task-time" title="${escHtml(ts)}">${rel}</span>` : ''}
    ${size ? `<span class="task-size">${size}</span>` : ''}
  `;
}

function outputDirHTML(t) {
  if (!t.output_dir) return '';
  const path = escHtml(t.output_dir);
  return `<div class="task-output-dir" title="Custom output directory">→ ${path}</div>`;
}

function renderPendingList() {
  const pending = Object.values(state.tasks).filter(t => t.status === 'pending');
  id('pending-title').textContent = `Pending (${pending.length})`;
  id('pending-list').innerHTML = pending.length === 0
    ? '<div class="empty">Queue is empty</div>'
    : pending.map(t => `
      <div class="task-card" id="task-${t.id}">
        <div class="task-header">
          <span class="task-title">${taskLabel(t)}</span>
          ${metaHTML(t)}
          <span class="badge pending">pending</span>
          <div class="task-actions">
            <button class="btn-ghost btn-sm" onclick="skipTask(${t.id})">Skip</button>
          </div>
        </div>
        ${outputDirHTML(t)}
      </div>
    `).join('');
}

function renderActiveList() {
  const active = Object.values(state.tasks).filter(t => t.status === 'active');
  const el = id('active-list');
  if (!active.length) {
    el.innerHTML = '<div class="empty">No active downloads</div>';
    return;
  }
  el.innerHTML = active.map(t => taskCardHTML(t)).join('');
}

function renderActiveTask(task_id) {
  const t = state.tasks[task_id];
  if (!t || t.status !== 'active') return;
  const card = document.getElementById(`task-${task_id}`);
  if (!card) return;
  card.outerHTML = taskCardHTML(t);
}

function renderDoneList() {
  const done   = Object.values(state.tasks).filter(t => t.status === 'done');
  const failed = Object.values(state.tasks).filter(t => t.status === 'failed');
  id('done-list').innerHTML = done.length === 0
    ? '<div class="empty">Nothing yet</div>'
    : done.map(t => `
      <div class="task-card">
        <div class="task-header">
          <span class="task-title">${taskLabel(t)}</span>
          ${metaHTML(t)}
          <span class="badge done">done</span>
        </div>
        ${outputDirHTML(t)}
      </div>
    `).join('');
  id('failed-list').innerHTML = failed.length === 0
    ? '<div class="empty">No failures</div>'
    : failed.map(t => `
      <div class="task-card">
        <div class="task-header">
          <span class="task-title">${taskLabel(t)}</span>
          ${metaHTML(t)}
          <span class="badge failed">failed</span>
          <span class="task-meta" title="${escHtml(t.last_error || '')}">${escHtml((t.last_error || '').slice(0,60))}</span>
          <button class="btn-ghost btn-sm" onclick="retryTask(${t.id})">Retry</button>
        </div>
        ${outputDirHTML(t)}
      </div>
    `).join('');
}

function taskCardHTML(t) {
  // Collect streams for this task
  const streams = Object.values(state.streams).filter(s => s.task_id === t.id);
  const streamsHTML = streams.length === 0 ? '' : `
    <div class="streams">
      ${streams.map(s => streamRowHTML(s)).join('')}
    </div>
  `;
  return `
    <div class="task-card" id="task-${t.id}">
      <div class="task-header">
        <span class="task-title">${taskLabel(t)}</span>
        ${metaHTML(t)}
        <span class="badge ${t.status}">${t.status}</span>
        <div class="task-actions">
          <button class="btn-ghost btn-sm" onclick="viewLogs(${t.id})">Logs</button>
        </div>
      </div>
      ${outputDirHTML(t)}
      ${streamsHTML}
    </div>
  `;
}

function streamRowHTML(s) {
  const pct     = s.pct ?? s.progress_pct ?? 0;
  const isDone  = s.status === 'done';
  const speed   = s.speed  ? `${s.speed.toFixed(1)}x`  : '';
  const size    = s.size_bytes ? fmtBytes(s.size_bytes) : '';
  const eta     = isDone ? '✓' : fmtEta(s.eta_sec);
  const pctLbl  = (pct || pct === 0) ? `${Math.floor(pct)}%` : '';
  const typeLbl = (s.stream_type || '').slice(0, 5);
  const langLbl = s.lang ? `[${s.lang.toUpperCase()}]` : '';
  const label   = escHtml((s.label || '').replace(/^\d+\.\s*/, '').slice(0, 40));
  return `
    <div class="stream-row">
      <span class="stream-type">${typeLbl}</span>
      <span class="stream-label">${label} <small style="color:var(--text-dim)">${langLbl}</small></span>
      <div class="stream-progress-wrap">
        <progress class="${isDone?'done':''}" value="${pct}" max="100"></progress>
      </div>
      <span class="stream-pct">${pctLbl}</span>
      <span class="stream-eta">${eta}</span>
      <span class="stream-speed">${isDone ? '' : speed}</span>
      <span class="stream-size">${size}</span>
    </div>
  `;
}

// ── Load all tasks ─────────────────────────────────────────────────────────────
function loadTasks() {
  const payload = { cmd: 'list', limit: 200, verbose: true, include_unfinished: true };
  const kindSel = id('filter-kind');
  if (kindSel && kindSel.value) payload.kind = kindSel.value;
  else if (state.filter.kind)   payload.kind = state.filter.kind;
  if (state.filter.since)       payload.since = state.filter.since;
  sendCmd(payload);
}

function setSinceFilter(spec) {
  state.filter.since = spec;
  document.querySelectorAll('.filter-bar button.chip').forEach(b => {
    b.classList.toggle('on', b.dataset.since === spec);
  });
  // Drop tasks that may be filtered out by the new window so the UI doesn't
  // retain stale rows from the previous query.
  state.tasks = {};
  loadTasks();
}

// ── Log handling ──────────────────────────────────────────────────────────────
function appendLog(entry) {
  state.logs.push(entry);
  if (state.logs.length > 2000) state.logs.shift();

  const taskFilter  = id('log-task-filter').value;
  const levelFilter = id('log-level-filter').value;
  if (taskFilter  && String(entry.task_id) !== taskFilter)  return;
  if (levelFilter && entry.level !== levelFilter) return;

  const el  = id('log-output');
  const div = document.createElement('div');
  div.className = 'log-line';
  const ts    = entry.ts ? entry.ts.slice(11, 19) : '';
  const task  = entry.task_id ? `#${entry.task_id}` : '';
  div.innerHTML = `
    <span class="log-ts">${ts}</span>
    <span class="log-level ${entry.level}">${escHtml(entry.level)}</span>
    <span class="log-task">${escHtml(task)}</span>
    <span class="log-msg">${escHtml(entry.msg)}</span>
  `;
  el.appendChild(div);

  // Update task filter options
  if (entry.task_id) {
    const opt = id('log-task-filter');
    if (!opt.querySelector(`option[value="${entry.task_id}"]`)) {
      const o = document.createElement('option');
      o.value = entry.task_id; o.textContent = `#${entry.task_id}`;
      opt.appendChild(o);
    }
  }

  if (id('log-autoscroll').checked) el.scrollTop = el.scrollHeight;
}

function filterLogs() {
  const el          = id('log-output');
  const taskFilter  = id('log-task-filter').value;
  const levelFilter = id('log-level-filter').value;
  el.innerHTML = '';
  state.logs
    .filter(e => (!taskFilter || String(e.task_id) === taskFilter)
              && (!levelFilter || e.level === levelFilter))
    .forEach(appendLogDOM);
}

function appendLogDOM(entry) {
  // Used only by filterLogs — same as appendLog but without adding to state.logs
  const el = id('log-output');
  const div = document.createElement('div');
  div.className = 'log-line';
  const ts   = entry.ts ? entry.ts.slice(11, 19) : '';
  const task = entry.task_id ? `#${entry.task_id}` : '';
  div.innerHTML = `
    <span class="log-ts">${ts}</span>
    <span class="log-level ${entry.level}">${escHtml(entry.level)}</span>
    <span class="log-task">${escHtml(task)}</span>
    <span class="log-msg">${escHtml(entry.msg)}</span>
  `;
  el.appendChild(div);
}

function clearLogs() {
  state.logs = [];
  id('log-output').innerHTML = '';
}

function viewLogs(task_id) {
  showTab('logs');
  const sel = id('log-task-filter');
  sel.value = task_id;
  filterLogs();
}

// ── Actions ───────────────────────────────────────────────────────────────────
function doEnqueue() {
  const url = id('enqueue-url').value.trim();
  if (!url) return;
  const outputDir = id('enqueue-output-dir').value.trim();
  const payload = {cmd: 'enqueue', url};
  if (outputDir) payload.output_dir = outputDir;
  sendCmd(payload);
  const msg = `Enqueueing: ${url}${outputDir ? ` → ${outputDir}` : ''}`;
  appendLog({level:'INFO', ts: new Date().toISOString(), task_id:null, msg});
}

function skipTask(task_id) {
  sendCmd({cmd: 'skip', task_id});
  if (state.tasks[task_id]) state.tasks[task_id].status = 'skipped';
  renderPendingList();
}

function retryTask(task_id) {
  sendCmd({cmd: 'retry', task_id});
  if (state.tasks[task_id]) state.tasks[task_id].status = 'pending';
  renderPendingList();
  renderDoneList();
}

id('btn-pause').addEventListener('click', () => {
  sendCmd({cmd: state.paused ? 'resume' : 'pause'});
});

id('enqueue-url').addEventListener('keydown', e => {
  if (e.key === 'Enter') doEnqueue();
});
id('enqueue-output-dir').addEventListener('keydown', e => {
  if (e.key === 'Enter') doEnqueue();
});

// ── Cookie save ───────────────────────────────────────────────────────────────
function saveCookies() {
  const raw = id('cookie-input').value;
  if (!raw.trim()) { alert('Paste a Netscape cookies.txt file first.'); return; }
  sendCmd({cmd: 'cookies', cookies_txt: raw});
  id('cookie-input').value = '';
}

// ── Config ────────────────────────────────────────────────────────────────────
function loadConfig(cfg) {
  id('cfg-concurrency').value = cfg.concurrency ?? 2;
  id('cfg-stall').value       = cfg.stall_timeout_sec ?? 300;
  id('cfg-quality').value     = cfg.video_quality ?? 'lowest';
  id('cfg-output').value      = cfg.output_dir ?? '';
  id('cfg-db').value          = cfg.db_path ?? '';
}

function setCfg(key, value) {
  sendCmd({cmd: 'config_set', key, value});
}

// ── Init ──────────────────────────────────────────────────────────────────────
connect();
</script>
</body>
</html>
"""
