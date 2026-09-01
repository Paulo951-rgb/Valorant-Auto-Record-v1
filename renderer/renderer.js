'use strict';

/* ============================================================
   renderer.js — UI refonte Valorant Auto Record
   ============================================================ */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const api = window.api;

const state = {
  status: null,
  config: null,
  monitoring: false,
  autoRecord: true,
  logFilter: 'ALL',
  logLines: [],
  backendAlive: false,
  matches: [],
  matchesView: { search: '', result: 'ALL', sort: 'date_desc' },
  showAdvanced: false,
};

const VIEW_TITLES = {
  home:        { title: 'Accueil',     sub: 'Surveillance automatique' },
  matches:     { title: 'Parties',     sub: 'Vos enregistrements' },
  settings:    { title: 'Paramètres',  sub: 'Configuration du logiciel' },
  diagnostics: { title: 'Diagnostics', sub: 'Logs et informations techniques' },
};

function switchView(view) {
  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + view));
  const meta = VIEW_TITLES[view] || VIEW_TITLES.home;
  $('#viewTitle').textContent = meta.title;
  $('#viewSub').textContent = meta.sub;
  if (view === 'matches') loadHistory();
  if (view === 'settings') loadSettingsIntoForm();
  if (view === 'diagnostics') refreshDiagnostics();
}
$$('.nav-item').forEach((b) => b.addEventListener('click', () => switchView(b.dataset.view)));

/* Toasts */
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

/* Helpers */
function fmtDuration(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  // "2026-09-01 20:14" -> "01/09/2026 — 20:14"
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (m) return `${m[3]}/${m[2]}/${m[1]} — ${m[4]}:${m[5]}`;
  return iso;
}

function setCard(cardId, klass, stateText, detail) {
  const card = $('#card-' + cardId);
  card.classList.remove('ok', 'warn', 'err', 'active');
  if (klass) card.classList.add(klass);
  $('#' + cardId + 'State').textContent = stateText;
  if (detail !== undefined) {
    const el = $('#' + cardId + 'Detail');
    if (el) el.textContent = detail || '—';
  }
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ============================================================
   Rendu du statut temps réel (Accueil)
   ============================================================ */
function renderStatus(data) {
  if (!data) return;
  state.status = data;
  state.monitoring = !!data.monitoring;
  state.autoRecord = data.auto_record !== false;
  state.backendAlive = true;

  updateBackendPill('ok', 'Connecté');

  const obs = data.obs || {};
  // VALORANT
  if (data.valorant_running) {
    setCard('valorant', 'ok', 'Connecté', 'Processus Valorant détecté');
  } else {
    setCard('valorant', 'warn', 'Non détecté', 'Lancez Valorant pour commencer');
  }
  // OBS
  if (!obs.running) {
    setCard('obs', 'warn', 'OBS fermé', 'Sera lancé automatiquement');
  } else if (!obs.connected) {
    setCard('obs', 'err', 'Déconnecté', 'Connexion au WebSocket impossible');
  } else {
    let detail = 'Prêt';
    if (obs.version) detail = `OBS ${obs.version}`;
    setCard('obs', 'ok', 'Prêt', detail);
  }
  // REC
  if (data.recording) {
    setCard('rec', 'active', 'Enregistrement', 'Durée : ' + fmtDuration(data.recording_duration));
  } else {
    setCard('rec', '', 'En attente', 'Aucune partie en cours');
  }
  // AUTO
  if (state.autoRecord) {
    setCard('auto', 'ok', 'Activé', 'Démarre et arrête tout seul');
  } else {
    setCard('auto', 'warn', 'Désactivé', 'Contrôle manuel uniquement');
  }

  // HERO
  const hero = $('#heroBanner');
  hero.classList.remove('rec', 'ok', 'warn');
  const heroState = $('#heroState');
  const heroSub = $('#heroSub');
  if (data.recording) {
    hero.classList.add('rec');
    heroState.textContent = 'ENREGISTREMENT EN COURS';
    heroSub.textContent = `Carte : ${data.map || 'Inconnu'} — Agent : ${data.agent || 'Inconnu'}`;
  } else if (state.monitoring && data.valorant_running && data.session_state !== 'Indisponible') {
    hero.classList.add('ok');
    heroState.textContent = data.session_state === 'MENUS' ? 'EN ATTENTE D\'UNE PARTIE' : 'SURVEILLANCE';
    heroSub.textContent = data.session_state === 'MENUS'
      ? 'Aucune partie en cours — l\'enregistrement se lancera automatiquement.'
      : `État : ${data.session_label || data.session_state}`;
  } else if (state.monitoring) {
    hero.classList.add('warn');
    heroState.textContent = 'EN ATTENTE DE VALORANT';
    heroSub.textContent = 'Lancez Valorant et le client Riot pour commencer.';
  } else {
    heroState.textContent = 'SURVEILLANCE INACTIVE';
    heroSub.textContent = 'Cliquez sur « Activer la surveillance » pour démarrer.';
  }
  $('#heroDuration').textContent = fmtDuration(data.recording_duration);

  // Boutons Accueil
  $('#btnToggleMonitor').textContent = state.monitoring ? 'Désactiver la surveillance' : 'Activer la surveillance';
  $('#btnToggleMonitor').classList.toggle('btn-primary', !state.monitoring);
  $('#btnAutoRecord').textContent = 'Automatique : ' + (state.autoRecord ? 'ON' : 'OFF');
  $('#btnAutoRecord').classList.toggle('btn-primary', state.autoRecord);

  // Global status
  const gs = $('#globalStatus');
  gs.classList.remove('ok', 'warn', 'err');
  let gText = 'Inactif';
  if (!state.backendAlive) { gs.classList.add('err'); gText = 'Backend déconnecté'; }
  else if (data.recording) { gs.classList.add('ok'); gText = 'Enregistrement en cours'; }
  else if (state.monitoring && data.valorant_running && obs.connected) { gs.classList.add('ok'); gText = 'Système opérationnel'; }
  else if (state.monitoring) { gs.classList.add('warn'); gText = 'En surveillance'; }
  gs.querySelector('.gs-text').textContent = gText;

  // Erreur
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

/* ============================================================
   Logs
   ============================================================ */
function appendLog(entry) {
  state.logLines.push(entry);
  if (state.logLines.length > 2000) state.logLines.splice(0, 500);
  if (matchesFilter(entry)) renderLogLine(entry);
  const lv = $('#logView');
  if (lv && lv.scrollHeight - lv.scrollTop - lv.clientHeight < 60) lv.scrollTop = lv.scrollHeight;
}
function matchesFilter(entry) {
  return state.logFilter === 'ALL' || entry.level === state.logFilter;
}
function renderLogLine(entry) {
  const lv = $('#logView');
  if (!lv) return;
  const line = document.createElement('div');
  line.className = 'log-line ' + (entry.level || 'INFO');
  const time = entry.timestamp || new Date().toTimeString().slice(0, 8);
  line.innerHTML =
    `<span class="log-time">${time}</span>` +
    `<span class="log-level">[${entry.level || 'INFO'}]</span>` +
    `<span class="log-msg"></span>`;
  line.querySelector('.log-msg').textContent = entry.message || '';
  lv.appendChild(line);
}
function rerenderLogs() {
  const lv = $('#logView');
  if (!lv) return;
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

/* ============================================================
   Historique
   ============================================================ */
async function loadHistory() {
  const list = $('#historyList');
  list.innerHTML = '<div class="empty-state">Chargement…</div>';
  const res = await api.call('get_history');
  if (!res || !res.ok) {
    list.innerHTML = '<div class="empty-state">Impossible de charger l\'historique.</div>';
    return;
  }
  state.matches = res.data || [];
  renderMatches();
}

function renderMatches() {
  const list = $('#historyList');
  const view = state.matchesView;
  let items = state.matches.slice();
  // Filtre résultat
  if (view.result !== 'ALL') {
    items = items.filter((m) => (m.result || 'Inconnu') === view.result);
  }
  // Recherche
  const q = view.search.trim().toLowerCase();
  if (q) {
    items = items.filter((m) =>
      (m.map_name || '').toLowerCase().includes(q) ||
      (m.agent || '').toLowerCase().includes(q) ||
      (m.mode || '').toLowerCase().includes(q) ||
      (m.score || '').toLowerCase().includes(q)
    );
  }
  // Tri
  items.sort((a, b) => {
    switch (view.sort) {
      case 'date_asc': return (a.date || '').localeCompare(b.date || '');
      case 'duration_desc': return (b.duration_seconds || 0) - (a.duration_seconds || 0);
      case 'map_asc': return (a.map_name || '').localeCompare(b.map_name || '');
      case 'date_desc':
      default: return (b.date || '').localeCompare(a.date || '');
    }
  });
  if (!items.length) {
    list.innerHTML = '<div class="empty-state">Aucun match à afficher.</div>';
    return;
  }
  list.innerHTML = '';
  items.forEach((m) => list.appendChild(buildMatchCard(m)));
}

function buildMatchCard(m) {
  const result = m.result || 'Inconnu';
  const cls = (result || 'inconnu').toLowerCase();
  const card = document.createElement('div');
  card.className = 'match-card ' + cls;
  const score = m.score || `${m.ally_score ?? 0}-${m.enemy_score ?? 0}`;
  const dur = m.duration_seconds ? fmtDuration(m.duration_seconds) : '—';
  const date = fmtDate(m.date);
  const mode = m.mode || 'Inconnu';
  const mapName = m.map_name || 'Inconnu';
  const agent = m.agent || 'Inconnu';
  card.innerHTML =
    `<div class="mc-head">
       <span class="mc-result ${cls}">${escapeHtml(result)}</span>
       <span class="mc-date">${escapeHtml(date)}</span>
     </div>
     <div class="mc-title">${escapeHtml(mapName)}</div>
     <div class="mc-agent">Agent : ${escapeHtml(agent)} · ${escapeHtml(mode)}</div>
     <div class="mc-stats">
       <span>Score : <b>${escapeHtml(score)}</b></span>
       <span>Durée : <b>${dur}</b></span>
     </div>
     <div class="mc-foot">
       <span class="mc-duration">${m.video_path ? 'Vidéo disponible' : 'Vidéo indisponible'}</span>
       <span class="mc-actions">
         <button class="btn btn-ghost" data-act="open" data-path="${escapeHtml(m.video_path || '')}">Ouvrir</button>
         <button class="btn btn-ghost" data-act="del" data-mid="${escapeHtml(m.match_id || '')}">Supprimer</button>
       </span>
     </div>`;
  card.querySelector('[data-act="open"]').addEventListener('click', (e) => {
    const p = e.currentTarget.dataset.path;
    if (p) api.openPath(p);
    else toast('Vidéo introuvable.', 'warn');
  });
  card.querySelector('[data-act="del"]').addEventListener('click', async (e) => {
    const mid = e.currentTarget.dataset.mid;
    if (!mid) return;
    if (!confirm('Supprimer cette entrée de l\'historique ? (le fichier vidéo n\'est pas effacé)')) return;
    await api.call('delete_match', { match_id: mid });
    loadHistory();
  });
  return card;
}

$('#matchSearch').addEventListener('input', (e) => {
  state.matchesView.search = e.target.value;
  renderMatches();
});
$('#matchFilterResult').addEventListener('change', (e) => {
  state.matchesView.result = e.target.value;
  renderMatches();
});
$('#matchSort').addEventListener('change', (e) => {
  state.matchesView.sort = e.target.value;
  renderMatches();
});
$('#btnRefreshHistory').addEventListener('click', loadHistory);
$('#btnOpenRecordings').addEventListener('click', async () => {
  const cfg = state.config || (await api.call('get_config')).data;
  const folder = cfg && cfg.obs_folder;
  if (folder) api.openPath(folder);
  else toast('Aucun dossier configuré.', 'warn');
});

/* ============================================================
   Paramètres
   ============================================================ */
const SETTING_FIELDS = [
  ['setAutoRecord', 'auto_record', 'bool'],
  ['setAutoLaunchObs', 'auto_launch_obs', 'bool'],
  ['setStartWithWindows', 'start_with_windows', 'bool'],
  ['setMinimizeToTray', 'minimize_to_tray', 'bool'],
  ['setObsFolder', 'obs_folder'],
  ['setRecordFormat', 'record_format'],
  ['setMaxSize', 'max_size_gb', Number],
  ['setFileNaming', 'file_naming'],
  ['setObsHost', 'obs_host'],
  ['setObsPort', 'obs_port', Number],
  ['setObsPassword', 'obs_password'],
  ['setPoll', 'poll_interval', Number],
  ['setLogLevel', 'log_level'],
];

async function loadSettingsIntoForm() {
  const res = await api.call('get_config');
  if (!res || !res.ok) { toast('Impossible de charger la configuration.', 'err'); return; }
  const cfg = res.data || {};
  state.config = cfg;
  SETTING_FIELDS.forEach(([id, key, cast]) => {
    const el = $('#' + id);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = !!cfg[key];
    else el.value = cfg[key] !== undefined ? cfg[key] : '';
  });
  // Affichage/masquage Avancé
  state.showAdvanced = !!cfg.show_advanced;
  $('#advancedGroup').hidden = !state.showAdvanced;
  $('#btnToggleAdvanced').textContent = state.showAdvanced
    ? 'Masquer les options avancées' : 'Afficher les options avancées';
  // OBS installations
  refreshObsExeOptions();
  // Astuce dossier
  const hint = $('#setObsFolderHint');
  if (hint) hint.textContent = cfg.output_dir_hint || (cfg.obs_folder ? `OBS utilise : ${cfg.obs_folder}` : '');
}

async function refreshObsExeOptions() {
  const sel = $('#setObsExe');
  if (!sel) return;
  sel.innerHTML = '<option value="">— Recherche… —</option>';
  const res = await api.call('discover_obs');
  const list = (res && res.ok ? res.data : {}).installations || [];
  sel.innerHTML = '';
  if (!list.length) {
    const opt = document.createElement('option');
    opt.value = ''; opt.textContent = 'Aucune installation détectée';
    sel.appendChild(opt);
    $('#setObsExeHint').textContent = 'Indiquez manuellement le chemin ou réinstallez OBS Studio 28+.';
    return;
  }
  list.forEach((i) => {
    const opt = document.createElement('option');
    opt.value = i.path;
    opt.textContent = `${i.path}${i.version ? ` (OBS ${i.version})` : ''}${i.running ? ' — en cours' : ''}`;
    sel.appendChild(opt);
  });
  // Sélection courante
  const cur = (state.config && state.config.obs_exe_path) || (list.find((i) => i.running) || list[0]).path;
  sel.value = list.find((i) => i.path === cur) ? cur : list[0].path;
  $('#setObsExeHint').textContent = `${list.length} installation(s) détectée(s).`;
}

$('#setObsExe').addEventListener('change', (e) => {
  // Met à jour la config locale sans recharger.
  if (state.config) state.config.obs_exe_path = e.target.value;
});

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
  // obs_exe_path vient du <select>
  const sel = $('#setObsExe');
  if (sel) cfg.obs_exe_path = sel.value;
  // on conserve l'état d'affichage Avancé
  cfg.show_advanced = state.showAdvanced;
  const res = await api.call('save_config', cfg);
  if (res && res.ok) {
    toast('Paramètres enregistrés.', 'ok');
    state.config = res.data;
    if (cfg.auto_record !== undefined) api.call('set_auto_record', { enabled: cfg.auto_record });
  } else {
    toast('Échec : ' + (res ? res.error : 'erreur'), 'err');
  }
});

$('#btnResetSettings').addEventListener('click', async () => {
  if (!confirm('Réinitialiser tous les paramètres aux valeurs par défaut ?')) return;
  const res = await api.call('reset_config');
  if (res && res.ok) { toast('Paramètres réinitialisés.', 'ok'); loadSettingsIntoForm(); }
});

$('#btnToggleAdvanced').addEventListener('click', () => {
  state.showAdvanced = !state.showAdvanced;
  $('#advancedGroup').hidden = !state.showAdvanced;
  $('#btnToggleAdvanced').textContent = state.showAdvanced
    ? 'Masquer les options avancées' : 'Afficher les options avancées';
});

$('#btnScanObs').addEventListener('click', refreshObsExeOptions);

$$('[data-pick]').forEach((b) => {
  b.addEventListener('click', async () => {
    if (b.dataset.pick === 'folder') {
      const dir = await api.openDirectory();
      if (dir) $('#setObsFolder').value = dir;
    } else {
      const file = await api.openFile();
      if (file) $('#setObsExe').value = file;
    }
  });
});

$('#btnTestObs').addEventListener('click', async () => {
  const r = await api.call('test_obs');
  if (r && r.ok) {
    const d = r.data;
    if (d.connected) toast('Connexion OBS OK.', 'ok');
    else toast('OBS détecté mais WebSocket injoignable.', 'warn');
  } else toast('Échec du test : ' + (r ? r.error : 'erreur'), 'err');
});
$('#btnReconnectObs').addEventListener('click', async () => {
  const r = await api.call('reconnect_obs');
  if (r && r.ok) toast('Reconnexion demandée.', 'ok');
});
$('#btnLaunchObs').addEventListener('click', async () => {
  const r = await api.call('launch_obs');
  if (r && r.ok) toast(r.data.launched ? 'OBS lancé.' : 'OBS introuvable.', r.data.launched ? 'ok' : 'warn');
});

/* ============================================================
   Diagnostics
   ============================================================ */
async function refreshDiagnostics() {
  const r = await api.call('app_diagnostics');
  if (!r || !r.ok) return;
  const d = r.data || {};
  $('#diagBackend').textContent = state.backendAlive ? 'OK' : 'Déconnecté';
  $('#diagMonitor').textContent = state.monitoring ? 'Active' : 'Inactive';
  $('#diagAuto').textContent = state.autoRecord ? 'ON' : 'OFF';
  $('#diagDb').textContent = d.db_path || '—';
  $('#diagLog').textContent = d.log_path || '—';
  const obs = d.obs_status || {};
  $('#diagObsRun').textContent = obs.running ? 'Oui' : 'Non';
  $('#diagObsConn').textContent = obs.connected ? 'Connecté' : 'Injoignable';
  $('#diagObsVer').textContent = obs.version || '—';
  $('#diagObsWs').textContent = obs.websocket_version ? `v${obs.websocket_version}` : '—';
  $('#diagObsDir').textContent = obs.output_dir || d.recording_path || '—';
}
$('#btnRefreshDiag').addEventListener('click', refreshDiagnostics);
$('#btnOpenLogDir').addEventListener('click', async () => {
  const r = await api.call('app_diagnostics');
  if (r && r.ok && r.data && r.data.log_path) api.openPath(r.data.log_path);
});
$('#btnClearLogs').addEventListener('click', () => {
  state.logLines = [];
  $('#logView').innerHTML = '';
});

/* ============================================================
   Contrôles Accueil
   ============================================================ */
async function callBackend(method, params, okMsg, errMsg) {
  const res = await api.call(method, params);
  if (res && res.ok) { if (okMsg) toast(okMsg, 'ok'); }
  else { toast((errMsg || 'Échec') + ' : ' + (res ? res.error : 'erreur'), 'err'); }
  return res;
}
$('#btnToggleMonitor').addEventListener('click', async () => {
  const method = state.monitoring ? 'stop_monitoring' : 'start_monitoring';
  await callBackend(method, {},
    state.monitoring ? 'Surveillance arrêtée.' : 'Surveillance activée.',
    'Surveillance');
});
$('#btnAutoRecord').addEventListener('click', async () => {
  const next = !state.autoRecord;
  await callBackend('set_auto_record', { enabled: next },
    'Enregistrement automatique ' + (next ? 'activé.' : 'désactivé.'), 'Auto record');
});
$('#btnOpenFolder').addEventListener('click', async () => {
  const cfg = state.config || (await api.call('get_config')).data;
  const folder = cfg && cfg.obs_folder;
  if (folder) api.openPath(folder);
  else toast('Aucun dossier configuré.', 'warn');
});
$('#btnRestartBackend').addEventListener('click', async () => {
  updateBackendPill('err', 'Redémarrage…');
  await api.restartBackend();
});

/* ============================================================
   Événements backend temps réel
   ============================================================ */
api.on('backend:ready', async () => {
  updateBackendPill('ok', 'Connecté');
  state.backendAlive = true;
  api.call('get_status');
  const r = await api.call('get_config');
  if (r && r.ok) state.config = r.data;
});

api.on('backend:status', (data) => renderStatus(data));
api.on('backend:log', (data) => appendLog(data));
api.on('backend:config_changed', (data) => {
  state.config = data;
  if (data && data.auto_record !== undefined) {
    state.autoRecord = data.auto_record;
    $('#btnAutoRecord').textContent = 'Automatique : ' + (state.autoRecord ? 'ON' : 'OFF');
    $('#btnAutoRecord').classList.toggle('btn-primary', state.autoRecord);
  }
});
api.on('backend:match_started', (data) => {
  toast(`Partie détectée : ${data.map} (${data.agent})`, 'info');
});
api.on('backend:match_ended', (data) => {
  toast(`Match terminé : ${data.map} ${data.score} (${data.result})`, 'ok');
  if ($('#view-matches').classList.contains('active')) loadHistory();
});
api.on('backend:error', (data) => {
  toast('Erreur backend : ' + (data && data.message ? data.message : 'inconnue'), 'err');
});
api.on('backend:closed', (data) => {
  state.backendAlive = false;
  updateBackendPill('err', 'Déconnecté');
  const gs = $('#globalStatus');
  gs.classList.remove('ok', 'warn');
  gs.classList.add('err');
  gs.querySelector('.gs-text').textContent = 'Backend déconnecté';
  if (data) toast('Backend arrêté (code ' + (data.code !== null ? data.code : data.signal) + ').', 'warn');
});

/* ============================================================
   Démarrage
   ============================================================ */
(async function init() {
  try {
    const v = await api.getVersion();
    if (v && v.ok) $('#appVersion').textContent = 'v' + v.data;
  } catch (e) { /* ignore */ }
  // Polling sécurité
  setInterval(() => { if (state.backendAlive) api.call('get_status'); }, 10000);
})();
