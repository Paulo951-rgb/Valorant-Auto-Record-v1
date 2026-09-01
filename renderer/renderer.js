'use strict';

/* ============================================================
   renderer.js — UI Valorant Auto Record
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
  // Anti-double-clic sur les actions critiques.
  busy: new Set(),
  // Token de session pour loadHistory (évite les courses).
  historyToken: 0,
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
  el.textContent = String(message == null ? '' : message);
  const c = $('#toastContainer');
  if (!c) return;
  c.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .3s, transform .3s';
    el.style.opacity = '0';
    el.style.transform = 'translateX(40px)';
    setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
  }, 3500);
}

/* Helpers */
function fmtDuration(sec) {
  sec = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (m) return `${m[3]}/${m[2]}/${m[1]} — ${m[4]}:${m[5]}`;
  return String(iso);
}

function setCard(cardId, klass, stateText, detail) {
  const card = $('#card-' + cardId);
  if (!card) return;
  card.classList.remove('ok', 'warn', 'err', 'active');
  if (klass) card.classList.add(klass);
  const stateEl = $('#' + cardId + 'State');
  if (stateEl) stateEl.textContent = stateText;
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
  if (!data || typeof data !== 'object') return;
  state.status = data;
  state.monitoring = !!data.monitoring;
  state.autoRecord = data.auto_record !== false;
  state.backendAlive = true;

  updateBackendPill('ok', 'Connecté');

  const obs = data.obs || {};
  if (data.valorant_running) {
    setCard('valorant', 'ok', 'Connecté', 'Processus Valorant détecté');
  } else {
    setCard('valorant', 'warn', 'Non détecté', 'Lancez Valorant pour commencer');
  }
  if (!obs.running) {
    setCard('obs', 'warn', 'OBS fermé', 'Sera lancé automatiquement');
  } else if (!obs.connected) {
    setCard('obs', 'err', 'Déconnecté', 'Connexion au WebSocket impossible');
  } else {
    let detail = 'Prêt';
    if (obs.version) detail = `OBS ${obs.version}`;
    setCard('obs', 'ok', 'Prêt', detail);
  }
  if (data.recording) {
    setCard('rec', 'active', 'Enregistrement', 'Durée : ' + fmtDuration(data.recording_duration));
  } else {
    setCard('rec', '', 'En attente', 'Aucune partie en cours');
  }
  if (state.autoRecord) {
    setCard('auto', 'ok', 'Activé', 'Démarre et arrête tout seul');
  } else {
    setCard('auto', 'warn', 'Désactivé', 'Contrôle manuel uniquement');
  }

  // HERO
  const hero = $('#heroBanner');
  if (hero) {
    hero.classList.remove('rec', 'ok', 'warn');
    const heroState = $('#heroState');
    const heroSub = $('#heroSub');
    if (heroState) {
      if (data.recording) {
        hero.classList.add('rec');
        heroState.textContent = 'ENREGISTREMENT EN COURS';
        if (heroSub) heroSub.textContent = `Carte : ${data.map || 'Inconnu'} — Agent : ${data.agent || 'Inconnu'}`;
      } else if (state.monitoring && data.valorant_running && data.session_state !== 'Indisponible') {
        hero.classList.add('ok');
        heroState.textContent = data.session_state === 'MENUS' ? "EN ATTENTE D'UNE PARTIE" : 'SURVEILLANCE';
        if (heroSub) heroSub.textContent = data.session_state === 'MENUS'
          ? "Aucune partie en cours — l'enregistrement se lancera automatiquement."
          : `État : ${data.session_label || data.session_state}`;
      } else if (state.monitoring) {
        hero.classList.add('warn');
        heroState.textContent = 'EN ATTENTE DE VALORANT';
        if (heroSub) heroSub.textContent = 'Lancez Valorant et le client Riot pour commencer.';
      } else {
        heroState.textContent = 'SURVEILLANCE INACTIVE';
        if (heroSub) heroSub.textContent = 'Cliquez sur « Activer la surveillance » pour démarrer.';
      }
    }
  }
  const dur = $('#heroDuration'); if (dur) dur.textContent = fmtDuration(data.recording_duration);

  // Boutons Accueil
  const btnToggle = $('#btnToggleMonitor');
  if (btnToggle) {
    btnToggle.textContent = state.monitoring ? 'Désactiver la surveillance' : 'Activer la surveillance';
    btnToggle.classList.toggle('btn-primary', !state.monitoring);
  }
  const btnAuto = $('#btnAutoRecord');
  if (btnAuto) {
    btnAuto.textContent = 'Automatique : ' + (state.autoRecord ? 'ON' : 'OFF');
    btnAuto.classList.toggle('btn-primary', state.autoRecord);
  }

  // Global status
  const gs = $('#globalStatus');
  if (gs) {
    gs.classList.remove('ok', 'warn', 'err');
    let gText = 'Inactif';
    if (!state.backendAlive) { gs.classList.add('err'); gText = 'Backend déconnecté'; }
    else if (data.recording) { gs.classList.add('ok'); gText = 'Enregistrement en cours'; }
    else if (state.monitoring && data.valorant_running && obs.connected) { gs.classList.add('ok'); gText = 'Système opérationnel'; }
    else if (state.monitoring) { gs.classList.add('warn'); gText = 'En surveillance'; }
    const gt = gs.querySelector('.gs-text');
    if (gt) gt.textContent = gText;
  }

  // Erreur
  const errBox = $('#lastErrorBox');
  if (errBox) {
    if (data.last_error) {
      errBox.hidden = false;
      const t = $('#lastErrorText'); if (t) t.textContent = data.last_error;
    } else {
      errBox.hidden = true;
    }
  }
}

function updateBackendPill(klass, text) {
  const pill = $('#backendPill');
  if (!pill) return;
  pill.classList.remove('ok', 'err');
  if (klass) pill.classList.add(klass);
  const s = $('#backendState'); if (s) s.textContent = text;
}

/* ============================================================
   Logs
   ============================================================ */
function appendLog(entry) {
  if (!entry || typeof entry !== 'object') return;
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
  // Limite DOM.
  while (lv.childElementCount > 2000) lv.removeChild(lv.firstElementChild);
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
   Historique (avec protection contre courses)
   ============================================================ */
async function loadHistory() {
  const token = ++state.historyToken;
  const list = $('#historyList');
  if (!list) return;
  list.innerHTML = '<div class="empty-state">Chargement…</div>';
  const res = await api.call('get_history');
  if (token !== state.historyToken) return; // une autre demande a pris le dessus
  if (!res || !res.ok) {
    list.innerHTML = '<div class="empty-state">Impossible de charger l\'historique.</div>';
    return;
  }
  state.matches = Array.isArray(res.data) ? res.data : [];
  renderMatches();
}

function renderMatches() {
  const list = $('#historyList');
  if (!list) return;
  const view = state.matchesView;
  let items = state.matches.slice();
  if (view.result !== 'ALL') {
    items = items.filter((m) => (m.result || 'Inconnu') === view.result);
  }
  const q = (view.search || '').trim().toLowerCase();
  if (q) {
    items = items.filter((m) =>
      (m.map_name || '').toLowerCase().includes(q) ||
      (m.agent || '').toLowerCase().includes(q) ||
      (m.mode || '').toLowerCase().includes(q) ||
      (m.score || '').toLowerCase().includes(q)
    );
  }
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
  // Reset propre (les listeners des anciennes cartes sont gc'd avec le DOM).
  list.innerHTML = '';
  const frag = document.createDocumentFragment();
  items.forEach((m) => frag.appendChild(buildMatchCard(m)));
  list.appendChild(frag);
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
         <button class="btn btn-ghost" data-act="open">Ouvrir</button>
         <button class="btn btn-ghost" data-act="del">Supprimer</button>
       </span>
     </div>`;
  const openBtn = card.querySelector('[data-act="open"]');
  const delBtn = card.querySelector('[data-act="del"]');
  if (openBtn) {
    openBtn.addEventListener('click', () => {
      if (m.video_path) api.openPath(m.video_path);
      else toast('Vidéo introuvable.', 'warn');
    });
  }
  if (delBtn) {
    delBtn.addEventListener('click', async () => {
      if (!m.match_id) return;
      if (!confirm('Supprimer cette entrée de l\'historique ? (le fichier vidéo n\'est pas effacé)')) return;
      const r = await api.call('delete_match', { match_id: m.match_id });
      if (r && r.ok) {
        toast('Entrée supprimée.', 'ok');
        loadHistory();
      } else {
        toast('Échec : ' + (r ? r.error : 'erreur'), 'err');
      }
    });
  }
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
  const cfg = state.config || ((await api.call('get_config')).data);
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
  state.showAdvanced = !!cfg.show_advanced;
  $('#advancedGroup').hidden = !state.showAdvanced;
  $('#btnToggleAdvanced').textContent = state.showAdvanced
    ? 'Masquer les options avancées' : 'Afficher les options avancées';
  refreshObsExeOptions();
  const hint = $('#setObsFolderHint');
  if (hint) hint.textContent = cfg.obs_folder ? `OBS utilise : ${cfg.obs_folder}` : '—';
}

let _obsExeReqToken = 0;
async function refreshObsExeOptions() {
  const sel = $('#setObsExe');
  if (!sel) return;
  const myToken = ++_obsExeReqToken;
  sel.innerHTML = '<option value="">— Recherche… —</option>';
  const res = await api.call('discover_obs');
  if (myToken !== _obsExeReqToken) return; // réponse d'une ancienne requête
  const list = (res && res.ok ? res.data : {}).installations || [];
  sel.innerHTML = '';
  if (!list.length) {
    const opt = document.createElement('option');
    opt.value = ''; opt.textContent = 'Aucune installation détectée';
    sel.appendChild(opt);
    const h = $('#setObsExeHint');
    if (h) h.textContent = 'Indiquez manuellement le chemin ou réinstallez OBS Studio 28+.';
    return;
  }
  list.forEach((i) => {
    const opt = document.createElement('option');
    opt.value = i.path;
    opt.textContent = `${i.path}${i.version ? ` (OBS ${i.version})` : ''}${i.running ? ' — en cours' : ''}`;
    sel.appendChild(opt);
  });
  const cur = (state.config && state.config.obs_exe_path) || (list.find((i) => i.running) || list[0]).path;
  const found = list.find((i) => i.path === cur);
  sel.value = found ? cur : list[0].path;
  const h = $('#setObsExeHint');
  if (h) h.textContent = `${list.length} installation(s) détectée(s).`;
}

$('#setObsExe').addEventListener('change', (e) => {
  if (state.config) state.config.obs_exe_path = e.target.value;
});
$('#setObsFolder').addEventListener('input', (e) => {
  if (state.config) state.config.obs_folder = e.target.value;
});

$('#settingsForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (state.busy.has('settings')) return;
  state.busy.add('settings');
  const submit = e.target.querySelector('button[type="submit"]');
  if (submit) submit.disabled = true;
  try {
    const cfg = {};
    SETTING_FIELDS.forEach(([id, key, cast]) => {
      const el = $('#' + id);
      if (!el) return;
      let v = el.type === 'checkbox' ? el.checked : el.value;
      if (cast === Number) v = v === '' ? 0 : Number(v);
      cfg[key] = v;
    });
    const sel = $('#setObsExe');
    if (sel) cfg.obs_exe_path = sel.value;
    cfg.show_advanced = state.showAdvanced;
    const res = await api.call('save_config', cfg);
    if (res && res.ok) {
      toast('Paramètres enregistrés.', 'ok');
      state.config = res.data;
      if (cfg.auto_record !== undefined) api.call('set_auto_record', { enabled: cfg.auto_record });
      if (cfg.start_with_windows !== undefined) {
        api.call('app:setAutoLaunch', { enabled: !!cfg.start_with_windows });
      }
    } else {
      toast('Échec : ' + (res ? res.error : 'erreur'), 'err');
    }
  } finally {
    state.busy.delete('settings');
    if (submit) submit.disabled = false;
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
      if (dir) {
        $('#setObsFolder').value = dir;
        if (state.config) state.config.obs_folder = dir;
      }
    } else {
      const file = await api.openFile();
      if (file) {
        $('#setObsExe').value = file;
        if (state.config) state.config.obs_exe_path = file;
      }
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
  if (r && r.ok) {
    if (r.data.launched) toast('OBS lancé.', 'ok');
    else toast('OBS introuvable.', 'warn');
  }
});

/* ============================================================
   Diagnostics
   ============================================================ */
async function refreshDiagnostics() {
  const r = await api.call('app_diagnostics');
  if (!r || !r.ok) return;
  const d = r.data || {};
  const setText = (id, v) => { const el = $('#' + id); if (el) el.textContent = v; };
  setText('diagBackend', state.backendAlive ? 'OK' : 'Déconnecté');
  setText('diagMonitor', state.monitoring ? 'Active' : 'Inactive');
  setText('diagAuto', state.autoRecord ? 'ON' : 'OFF');
  setText('diagDb', d.db_path || '—');
  setText('diagLog', d.log_path || '—');
  const obs = d.obs_status || {};
  setText('diagObsRun', obs.running ? 'Oui' : 'Non');
  setText('diagObsConn', obs.connected ? 'Connecté' : 'Injoignable');
  setText('diagObsVer', obs.version || '—');
  setText('diagObsWs', obs.websocket_version ? `v${obs.websocket_version}` : '—');
  setText('diagObsDir', obs.output_dir || d.recording_path || '—');
}
$('#btnRefreshDiag').addEventListener('click', refreshDiagnostics);
$('#btnOpenLogDir').addEventListener('click', async () => {
  const r = await api.call('app_diagnostics');
  if (r && r.ok && r.data && r.data.log_path) {
    const res = await api.openPath(r.data.log_path);
    if (res && res.ok === false) toast('Impossible d\'ouvrir : ' + (res.error || ''), 'err');
  }
});
$('#btnClearLogs').addEventListener('click', () => {
  state.logLines = [];
  const lv = $('#logView'); if (lv) lv.innerHTML = '';
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

function busyKey(method) { return 'act:' + method; }

async function safeAct(method, fn) {
  if (state.busy.has(busyKey(method))) return null;
  state.busy.add(busyKey(method));
  try { return await fn(); }
  finally { state.busy.delete(busyKey(method)); }
}

$('#btnToggleMonitor').addEventListener('click', async () => {
  const method = state.monitoring ? 'stop_monitoring' : 'start_monitoring';
  await safeAct(method, () => callBackend(method, {},
    state.monitoring ? 'Surveillance arrêtée.' : 'Surveillance activée.',
    'Surveillance'));
});
$('#btnAutoRecord').addEventListener('click', async () => {
  const next = !state.autoRecord;
  await safeAct('set_auto_record', () => callBackend('set_auto_record', { enabled: next },
    'Enregistrement automatique ' + (next ? 'activé.' : 'désactivé.'), 'Auto record'));
});
$('#btnOpenFolder').addEventListener('click', async () => {
  const cfg = state.config || ((await api.call('get_config')).data);
  const folder = cfg && cfg.obs_folder;
  if (folder) {
    const res = await api.openPath(folder);
    if (res && res.ok === false) toast('Impossible d\'ouvrir : ' + (res.error || ''), 'err');
  } else toast('Aucun dossier configuré.', 'warn');
});
$('#btnRestartBackend').addEventListener('click', async () => {
  if (state.busy.has('restart')) return;
  state.busy.add('restart');
  updateBackendPill('err', 'Redémarrage…');
  try { await api.restartBackend(); }
  finally { state.busy.delete('restart'); }
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
  if (!data) return;
  state.config = data;
  if (data.auto_record !== undefined) {
    state.autoRecord = data.auto_record;
    const btn = $('#btnAutoRecord');
    if (btn) {
      btn.textContent = 'Automatique : ' + (state.autoRecord ? 'ON' : 'OFF');
      btn.classList.toggle('btn-primary', state.autoRecord);
    }
  }
  if (data.start_with_windows !== undefined) {
    api.call('app:setAutoLaunch', { enabled: !!data.start_with_windows }).catch(() => {});
  }
});
api.on('backend:match_started', (data) => {
  if (!data) return;
  toast(`Partie détectée : ${data.map || 'Inconnu'} (${data.agent || 'Inconnu'})`, 'info');
});
api.on('backend:match_ended', (data) => {
  if (!data) return;
  toast(`Match terminé : ${data.map || 'Inconnu'} ${data.score || ''} (${data.result || 'Inconnu'})`, 'ok');
  if ($('#view-matches') && $('#view-matches').classList.contains('active')) loadHistory();
});
api.on('backend:error', (data) => {
  toast('Erreur backend : ' + (data && data.message ? data.message : 'inconnue'), 'err');
});
api.on('backend:closed', (data) => {
  state.backendAlive = false;
  updateBackendPill('err', 'Déconnecté');
  const gs = $('#globalStatus');
  if (gs) {
    gs.classList.remove('ok', 'warn');
    gs.classList.add('err');
    const gt = gs.querySelector('.gs-text');
    if (gt) gt.textContent = 'Backend déconnecté';
  }
  if (data) {
    const code = (data.code !== null && data.code !== undefined) ? data.code : data.signal;
    toast('Backend arrêté (code ' + code + ').', 'warn');
  }
});

/* ============================================================
   Démarrage
   ============================================================ */
(async function init() {
  try {
    const v = await api.getVersion();
    if (v && v.ok) {
      const a = $('#appVersion'); if (a) a.textContent = 'v' + v.data;
    }
  } catch (e) { /* ignore */ }
  // Polling sécurité (désactivé si backend déconnecté).
  setInterval(() => { if (state.backendAlive) api.call('get_status'); }, 10000);
})();
