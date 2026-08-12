'use strict';

/* ============================================================
   renderer.js — contrôleur principal de l'interface
   Communique avec le backend Python via window.api (preload).
   ============================================================ */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// window.api est injecté par le preload (contextBridge). On l'utilise directement
// sans le redéclarer (sinon "Identifier 'api' has already been declared").
const backendApi = window.api;

// État UI local.
const state = {
  status: null,
  monitoring: false,
  autoRecord: true,
  logFilter: 'ALL',
  logLines: [],
  backendAlive: false,
};

// ============================================================
// Navigation entre vues
// ============================================================
const VIEW_TITLES = {
  dashboard: { title: 'Tableau de bord', sub: 'Surveillance en temps réel' },
  history: { title: 'Historique', sub: 'Matchs enregistrés' },
  logs: { title: 'Logs', sub: 'Journal d\'exécution' },
  settings: { title: 'Paramètres', sub: 'Configuration du logiciel' },
};

function switchView(view) {
  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + view));
  const meta = VIEW_TITLES[view] || VIEW_TITLES.dashboard;
  $('#viewTitle').textContent = meta.title;
  $('#viewSub').textContent = meta.sub;
  if (view === 'history') loadHistory();
  if (view === 'settings') loadSettingsIntoForm();
}

$$('.nav-item').forEach((b) => b.addEventListener('click', () => switchView(b.dataset.view)));

// ============================================================
// Toasts
// ============================================================
function toast(message, type = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = message;
  $('#toastContainer').appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .3s, transform .3s';
    el.style.opacity = '0';
    el.style.transform = 'translateX(40px)';
    setTimeout(() => el.remove(), 300);
  }, 3500);
}

// ============================================================
// Helpers d'affichage
// ============================================================
function fmtDuration(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function setCard(cardId, klass, stateText, ...details) {
  const card = $('#card-' + cardId);
  card.classList.remove('ok', 'warn', 'err', 'active');
  if (klass) card.classList.add(klass);
  $('#' + cardId + 'State').textContent = stateText;
  const detailEls = details.filter(Boolean);
  // Met à jour les lignes .sc-detail dans l'ordre.
  const detNodes = card.querySelectorAll('.sc-detail');
  detailEls.forEach((d, i) => { if (detNodes[i]) detNodes[i].textContent = d; });
}

// ============================================================
// Rendu du statut temps réel
// ============================================================
function renderStatus(data) {
  if (!data) return;
  state.status = data;
  state.monitoring = !!data.monitoring;
  state.autoRecord = data.auto_record !== false;
  state.backendAlive = true;

  updateBackendPill('ok', 'Connecté');

  const obs = data.obs || {};
  // --- OBS ---
  if (!obs.running) {
    setCard('obs', 'warn', 'OBS fermé', "OBS n'est pas lancé", 'Scène : —');
  } else if (!obs.connected) {
    setCard('obs', 'err', 'Déconnecté', 'OBS détecté', 'Scène : —');
  } else {
    setCard('obs', 'ok', 'Connecté', 'OBS détecté', 'Scène : ' + (obs.scene || '—'));
  }

  // --- VALORANT ---
  if (data.valorant_running) {
    setCard('valorant', 'ok', 'Détecté', 'Processus Valorant actif');
  } else {
    setCard('valorant', 'warn', 'Non détecté', 'Processus Valorant absent');
  }

  // --- RIOT ---
  if (data.riot_connected) {
    setCard('riot', 'ok', 'Connecté', 'Client Riot détecté');
  } else {
    setCard('riot', 'warn', 'Non détecté', 'Client Riot fermé (Valorant éteint)');
  }

  // --- PARTIE ---
  const session = data.session_state;
  if (session === 'INGAME' || session === 'INGAME_inprogress') {
    setCard('match', 'active', 'En cours',
      'Carte : ' + (data.map || '—') + ' | Agent : ' + (data.agent || '—'),
      'Score : ' + (data.score || '—'));
  } else if (session && session !== 'Indisponible') {
    setCard('match', 'ok', session,
      'Carte : ' + (data.map || '—') + ' | Agent : ' + (data.agent || '—'),
      'Score : ' + (data.score || '—'));
  } else {
    setCard('match', '', 'Indisponible', 'Carte : — | Agent : —', 'Score : —');
  }

  // --- REC ---
  if (data.recording) {
    setCard('rec', 'active', 'Enregistrement', 'Durée : ' + fmtDuration(data.recording_duration));
    $('#btnStartRecord').disabled = true;
    $('#btnStopRecord').disabled = false;
  } else if (obs.connected && data.valorant_running) {
    setCard('rec', 'ok', 'En attente', 'Durée : ' + fmtDuration(data.recording_duration));
    $('#btnStartRecord').disabled = false;
    $('#btnStopRecord').disabled = true;
  } else {
    setCard('rec', '', 'Arrêté', 'Durée : ' + fmtDuration(data.recording_duration));
    $('#btnStartRecord').disabled = !obs.running;
    $('#btnStopRecord').disabled = true;
  }

  // --- Hero banner ---
  const hero = $('#heroBanner');
  hero.classList.remove('rec', 'ok', 'warn');
  const heroState = $('#heroState');
  if (data.recording) {
    hero.classList.add('rec');
    heroState.textContent = 'ENREGISTREMENT';
  } else if (state.monitoring && data.valorant_running) {
    hero.classList.add('ok');
    heroState.textContent = 'SURVEILLANCE ACTIVE';
  } else if (state.monitoring) {
    hero.classList.add('warn');
    heroState.textContent = 'EN ATTENTE DE VALORANT';
  } else {
    heroState.textContent = 'SYSTÈME INACTIF';
  }
  $('#heroDuration').textContent = fmtDuration(data.recording_duration);
  $('#heroFile').textContent = data.output_dir || '—';
  $('#heroFile').title = data.output_dir || '';

  // --- Global status ---
  const gs = $('#globalStatus');
  gs.classList.remove('ok', 'warn', 'err');
  let gText = 'Inactif';
  if (!state.backendAlive) { gs.classList.add('err'); gText = 'Backend déconnecté'; }
  else if (data.recording) { gs.classList.add('ok'); gText = 'Enregistrement'; }
  else if (state.monitoring && data.valorant_running && obs.connected) { gs.classList.add('ok'); gText = 'Système opérationnel'; }
  else if (state.monitoring) { gs.classList.add('warn'); gText = 'En surveillance'; }
  gs.querySelector('.gs-text').textContent = gText;

  // --- Contrôles ---
  $('#btnToggleMonitor').textContent = state.monitoring ? 'Arrêter la surveillance' : 'Démarrer la surveillance';
  $('#btnToggleMonitor').classList.toggle('btn-primary', !state.monitoring);
  $('#btnAutoRecord').textContent = 'Auto : ' + (state.autoRecord ? 'ON' : 'OFF');
  $('#btnAutoRecord').classList.toggle('btn-primary', state.autoRecord);

  // --- Erreur ---
  const errBox = $('#lastErrorBox');
  if (data.last_error) {
    errBox.hidden = false;
    $('#lastErrorText').textContent = data.last_error;
  } else {
    errBox.hidden = true;
  }
}

function updateBackendPill(klass, text) {
  const pill = $('#backendPill');
  pill.classList.remove('ok', 'err');
  if (klass) pill.classList.add(klass);
  $('#backendState').textContent = text;
}

// ============================================================
// Logs
// ============================================================
function appendLog(entry) {
  state.logLines.push(entry);
  // Garde la mémoire sous contrôle.
  if (state.logLines.length > 2000) state.logLines.splice(0, 500);
  if (matchesFilter(entry)) renderLogLine(entry);
  // auto-scroll
  const lv = $('#logView');
  if (lv.scrollHeight - lv.scrollTop - lv.clientHeight < 60) lv.scrollTop = lv.scrollHeight;
}

function matchesFilter(entry) {
  return state.logFilter === 'ALL' || entry.level === state.logFilter;
}

function renderLogLine(entry) {
  const line = document.createElement('div');
  line.className = 'log-line ' + (entry.level || 'INFO');
  const time = entry.timestamp || new Date().toTimeString().slice(0, 8);
  line.innerHTML =
    `<span class="log-time">${time}</span>` +
    `<span class="log-level">[${entry.level || 'INFO'}]</span>` +
    `<span class="log-msg"></span>`;
  line.querySelector('.log-msg').textContent = entry.message || '';
  $('#logView').appendChild(line);
}

function rerenderLogs() {
  const lv = $('#logView');
  lv.innerHTML = '';
  state.logLines.forEach((e) => { if (matchesFilter(e)) renderLogLine(e); });
  lv.scrollTop = lv.scrollHeight;
}

$('#logFilters').addEventListener('click', (e) => {
  const btn = e.target.closest('.chip');
  if (!btn) return;
  $$('#logFilters .chip').forEach((c) => c.classList.remove('active'));
  btn.classList.add('active');
  state.logFilter = btn.dataset.level;
  rerenderLogs();
});

$('#btnClearLogs').addEventListener('click', () => {
  state.logLines = [];
  $('#logView').innerHTML = '';
});

// ============================================================
// Historique
// ============================================================
async function loadHistory() {
  const list = $('#historyList');
  list.innerHTML = '<div class="empty-state">Chargement…</div>';
  const res = await backendApi.call('get_history');
  if (!res || !res.ok) { list.innerHTML = '<div class="empty-state">Erreur de chargement.</div>'; return; }
  const matches = res.data || [];
  if (!matches.length) { list.innerHTML = '<div class="empty-state">Aucun match enregistré pour le moment.</div>'; return; }
  list.innerHTML = '';
  matches.forEach((m) => {
    const item = document.createElement('div');
    item.className = 'history-item';
    const result = m.result || 'FinMatch';
    item.innerHTML =
      `<div class="hi-main">
        <div class="hi-meta">
          <span class="hi-date">${m.date || '—'}</span>
          <span class="hi-map">${escapeHtml(m.map || 'Inconnu')}</span>
          <span class="hi-agent">Agent : ${escapeHtml(m.agent || 'Inconnu')}</span>
          <span class="hi-score">${escapeHtml(m.score || '—')}</span>
          <span class="badge ${result}">${escapeHtml(result)}</span>
        </div>
        <div class="hi-path">${escapeHtml(m.path || '')}</div>
      </div>
      <button class="btn btn-ghost btn-sm" data-open="${escapeAttr(m.path || '')}">Ouvrir</button>`;
    list.appendChild(item);
  });
  list.querySelectorAll('[data-open]').forEach((b) => {
    b.addEventListener('click', () => backendApi.openPath(b.dataset.open));
  });
}

$('#btnRefreshHistory').addEventListener('click', loadHistory);
$('#btnOpenRecordings').addEventListener('click', async () => {
  const cfg = await backendApi.call('get_config');
  const folder = cfg && cfg.ok ? cfg.data.obs_folder : null;
  if (folder) backendApi.openPath(folder);
  else toast('Aucun dossier configuré.', 'warn');
});

// ============================================================
// Paramètres
// ============================================================
const SETTING_FIELDS = [
  ['setObsFolder', 'obs_folder'],
  ['setObsExe', 'obs_exe_path'],
  ['setRecordFormat', 'record_format'],
  ['setMaxSize', 'max_size_gb', Number],
  ['setMaxDuration', 'max_duration_minutes', Number],
  ['setFileNaming', 'file_naming'],
  ['setAutoRecord', 'auto_record', 'bool'],
  ['setObsHost', 'obs_host'],
  ['setObsPort', 'obs_port', Number],
  ['setObsPassword', 'obs_password'],
  ['setPoll', 'poll_interval', Number],
  ['setLogLevel', 'log_level'],
];

async function loadSettingsIntoForm() {
  const res = await backendApi.call('get_config');
  if (!res || !res.ok) { toast('Impossible de charger la configuration.', 'err'); return; }
  const cfg = res.data || {};
  SETTING_FIELDS.forEach(([id, key, cast]) => {
    const el = $('#' + id);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = !!cfg[key];
    else el.value = cfg[key] !== undefined ? cfg[key] : '';
  });
}

$('#settingsForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const cfg = {};
  SETTING_FIELDS.forEach(([id, key, cast]) => {
    const el = $('#' + id);
    if (!el) return;
    let v = el.type === 'checkbox' ? el.checked : el.value;
    if (cast === Number) v = v === '' ? 0 : Number(v);
    cfg[key] = v;
  });
  const res = await backendApi.call('save_config', cfg);
  if (res && res.ok) {
    toast('Paramètres enregistrés.', 'ok');
    if (cfg.auto_record !== undefined) backendApi.call('set_auto_record', { enabled: cfg.auto_record });
  } else {
    toast('Échec de l\'enregistrement : ' + (res ? res.error : 'erreur'), 'err');
  }
});

$('#btnResetSettings').addEventListener('click', async () => {
  if (!confirm('Réinitialiser les champs affichés aux valeurs chargées ? (ne supprime pas le fichier)')) return;
  loadSettingsIntoForm();
});

// Boutons "Parcourir".
$$('[data-pick]').forEach((b) => {
  b.addEventListener('click', async () => {
    if (b.dataset.pick === 'folder') {
      const dir = await backendApi.openDirectory();
      if (dir) $('#setObsFolder').value = dir;
    } else {
      const file = await backendApi.openFile();
      if (file) $('#setObsExe').value = file;
    }
  });
});

// ============================================================
// Contrôles du Dashboard
// ============================================================
async function callBackend(method, params, okMsg, errMsg) {
  const res = await backendApi.call(method, params);
  if (res && res.ok) { if (okMsg) toast(okMsg, 'ok'); }
  else { toast((errMsg || 'Échec') + ' : ' + (res ? res.error : 'erreur'), 'err'); }
  return res;
}

$('#btnToggleMonitor').addEventListener('click', async () => {
  const method = state.monitoring ? 'stop_monitoring' : 'start_monitoring';
  await callBackend(method, {},
    state.monitoring ? 'Surveillance arrêtée.' : 'Surveillance démarrée.',
    'Surveillance');
});

$('#btnAutoRecord').addEventListener('click', async () => {
  const next = !state.autoRecord;
  await callBackend('set_auto_record', { enabled: next },
    'Enregistrement auto ' + (next ? 'activé.' : 'désactivé.'), 'Auto record');
});

$('#btnStartRecord').addEventListener('click', () =>
  callBackend('start_record', {}, 'Enregistrement démarré.', 'Démarrage enregistrement'));
$('#btnStopRecord').addEventListener('click', () =>
  callBackend('stop_record', {}, 'Enregistrement arrêté.', 'Arrêt enregistrement'));
$('#btnReconnectObs').addEventListener('click', () =>
  callBackend('reconnect_obs', {}, 'Reconnexion OBS demandée.', 'Reconnexion OBS'));
$('#btnLaunchObs').addEventListener('click', () =>
  callBackend('launch_obs', {}, 'Lancement OBS demandé.', 'Lancement OBS'));
$('#btnRestartBackend').addEventListener('click', async () => {
  updateBackendPill('err', 'Relance…');
  await backendApi.restartBackend();
});

// ============================================================
// Événements backend temps réel
// ============================================================
backendApi.on('backend:ready', () => {
  updateBackendPill('ok', 'Connecté');
  state.backendAlive = true;
  // Demande un statut initial + config.
  backendApi.call('get_status');
});

backendApi.on('backend:status', (data) => renderStatus(data));

backendApi.on('backend:log', (data) => appendLog(data));

backendApi.on('backend:config_changed', (data) => {
  // Si l'auto_record change côté backend, on synchronise l'UI.
  if (data && data.auto_record !== undefined) {
    state.autoRecord = data.auto_record;
    $('#btnAutoRecord').textContent = 'Auto : ' + (state.autoRecord ? 'ON' : 'OFF');
    $('#btnAutoRecord').classList.toggle('btn-primary', state.autoRecord);
  }
});

backendApi.on('backend:match_started', (data) => {
  toast(`Partie détectée : ${data.map} (${data.agent})`, '');
});
backendApi.on('backend:match_ended', (data) => {
  toast(`Match terminé : ${data.map} ${data.score} (${data.result})`, 'ok');
  if ($('#view-history').classList.contains('active')) loadHistory();
});

backendApi.on('backend:error', (data) => {
  toast('Erreur backend : ' + (data && data.message ? data.message : 'inconnue'), 'err');
});

backendApi.on('backend:closed', (data) => {
  state.backendAlive = false;
  updateBackendPill('err', 'Déconnecté');
  const gs = $('#globalStatus');
  gs.classList.remove('ok', 'warn');
  gs.classList.add('err');
  gs.querySelector('.gs-text').textContent = 'Backend déconnecté';
  if (data) toast('Backend arrêté (code ' + (data.code !== null ? data.code : data.signal) + ').', 'warn');
});

// ============================================================
// Utilitaires HTML
// ============================================================
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

// ============================================================
// Démarrage
// ============================================================
(async function init() {
  try {
    const v = await backendApi.getVersion();
    if (v && v.ok) $('#appVersion').textContent = 'v' + v.data;
  } catch (e) { /* ignore */ }

  // Rafraîchit périodiquement le statut (filet de sécurité en plus des events).
  setInterval(() => { if (state.backendAlive) backendApi.call('get_status'); }, 4000);

  // Initialise l'état des boutons.
  $('#btnStartRecord').disabled = true;
  $('#btnStopRecord').disabled = true;
})();
