'use strict';

// backend_bridge.js
// Gère le cycle de vie du backend Python et la communication JSON-lines.

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const readline = require('readline');
const { EventEmitter } = require('events');

/**
 * Détermine la commande et les arguments à lancer.
 * Stratégie :
 *   1. PYTHON_EXEC (env) pointant vers un exécutable backend PyInstaller.
 *   2. backend.exe/.sh packagé dans les ressources (mode distribué).
 *   3. interpréteur Python système + backend.py (dev ou ressources .py).
 */
function resolveLaunch() {
  const isWin = process.platform === 'win32';

  // 1. Variable d'environnement explicite.
  if (process.env.PYTHON_EXEC && fs.existsSync(process.env.PYTHON_EXEC)) {
    return {
      cmd: process.env.PYTHON_EXEC,
      args: [],
      backendDir: path.dirname(process.env.PYTHON_EXEC),
    };
  }

  // 2. Backend déjà compilé (PyInstaller) dans les ressources.
  const resPythonDir = process.resourcesPath
    ? path.join(process.resourcesPath, 'python')
    : null;
  if (resPythonDir) {
    const exeName = isWin ? 'backend.exe' : 'backend';
    const packagedExe = path.join(resPythonDir, exeName);
    if (fs.existsSync(packagedExe)) {
      return { cmd: packagedExe, args: [], backendDir: resPythonDir };
    }
  }

  // 3. Interpréteur Python système + backend.py.
  let backendScript;
  if (resPythonDir && fs.existsSync(path.join(resPythonDir, 'backend.py'))) {
    backendScript = path.join(resPythonDir, 'backend.py');
  } else {
    backendScript = path.join(__dirname, '..', 'python', 'backend.py');
  }
  const pythonExe = isWin ? 'python' : 'python3';
  return { cmd: pythonExe, args: [backendScript], backendDir: path.dirname(backendScript) };
}

class BackendBridge extends EventEmitter {
  constructor() {
    super();
    this.setMaxListeners(50);
    this.process = null;
    this.rl = null;
    this.pending = new Map();
    this.nextId = 1;
    this.isReady = false;
    this.isRunning = false;

    this.restartAttempts = 0;
    this.maxRestartAttempts = 5;
    this.autoRestart = true;

    this._restarting = false;
    this._exited = true;
    this._disposed = false;
    this._exitTimer = null;
  }

  // ---------- event emission ----------
  _emit(event, data) {
    try { this.emit(event, data); } catch (e) { /* listener errors */ }
  }

  // ---------- lifecycle ----------
  start() {
    if (this._disposed) return;
    if (this.process) return;
    this._exited = false;

    const { cmd, args, backendDir } = resolveLaunch();

    if (!args.length && !fs.existsSync(cmd)) {
      this._emit('error', { message: `Backend introuvable : ${cmd}` });
      return;
    }
    if (args.length && !fs.existsSync(args[0])) {
      this._emit('error', { message: `backend.py introuvable : ${args[0]}` });
      return;
    }

    let proc;
    try {
      proc = spawn(cmd, args, {
        cwd: backendDir,
        env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (e) {
      this._emit('error', { message: `Impossible de lancer Python : ${e.message}` });
      return;
    }

    this.process = proc;
    this.isRunning = true;

    // Erreur asynchrone de spawn (exécutable introuvable)
    proc.once('error', (err) => {
      this._emit('error', { message: `Erreur de processus Python : ${err.message}` });
      this._handleExit();
    });

    proc.on('exit', (code, signal) => {
      this._emit('closed', { code, signal });
      this._handleExit();
    });

    // Stderr : logs et traces Python (DEBUG dans l'UI).
    proc.stderr.on('data', (data) => {
      const text = data.toString().replace(/\r/g, '').trim();
      if (!text) return;
      // Multi-ligne possible : on émet chaque ligne.
      text.split('\n').forEach((line) => {
        if (line.trim()) {
          this._emit('log', {
            timestamp: new Date().toTimeString().slice(0, 8),
            level: 'DEBUG',
            message: line,
            raw: line,
            fromStderr: true,
          });
        }
      });
    });

    // Stdout : JSON-lines, une réponse ou notification par ligne.
    this.rl = readline.createInterface({ input: proc.stdout });
    this.rl.on('line', (line) => this._handleLine(line));
    this.rl.on('close', () => { this.rl = null; });
  }

  _handleExit() {
    if (this._exited) return;
    this._exited = true;

    const wasRunning = this.isRunning;
    this.isRunning = false;
    this.isReady = false;

    if (this.rl) {
      try { this.rl.close(); } catch (e) { /* ignore */ }
      this.rl = null;
    }
    this.process = null;

    // Rejette les requêtes en attente.
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      try { entry.reject(new Error('Backend arrêté avant réponse.')); } catch (e) { /* ignore */ }
    }
    this.pending.clear();

    if (this._disposed) return;
    if (this._restarting) return; // un restart() manuel est en cours

    if (wasRunning && this.autoRestart && this.restartAttempts < this.maxRestartAttempts) {
      this.restartAttempts++;
      const delay = Math.min(1000 * this.restartAttempts, 5000);
      this._emit('log', {
        timestamp: new Date().toTimeString().slice(0, 8),
        level: 'WARNING',
        message: `Backend arrêté. Reconnexion dans ${delay / 1000}s (tentative ${this.restartAttempts}/${this.maxRestartAttempts}).`,
        raw: '',
      });
      this._exitTimer = setTimeout(() => {
        this._exitTimer = null;
        this._exited = false;
        try { this.start(); } catch (e) { /* ignore */ }
      }, delay);
    } else if (wasRunning && this.restartAttempts >= this.maxRestartAttempts) {
      this._emit('error', { message: 'Backend injoignable après plusieurs tentatives.' });
    }
  }

  _handleLine(line) {
    if (!line) return;
    let obj;
    try {
      obj = JSON.parse(line);
    } catch (e) {
      // Ligne non JSON (ex: print accidentel du backend)
      this._emit('log', {
        timestamp: new Date().toTimeString().slice(0, 8),
        level: 'DEBUG',
        message: `[stdout non-JSON] ${line}`,
        raw: line,
      });
      return;
    }
    if (!obj || typeof obj !== 'object') return;

    // Réponse à une requête.
    if (obj.id !== undefined && obj.id !== null) {
      const entry = this.pending.get(String(obj.id));
      if (entry) {
        clearTimeout(entry.timer);
        this.pending.delete(String(obj.id));
        if (obj.error) {
          try { entry.reject(new Error(String(obj.error))); } catch (e) { /* ignore */ }
        } else {
          try { entry.resolve(obj.result); } catch (e) { /* ignore */ }
        }
      }
      return;
    }

    // Notification.
    if (obj.event) {
      if (obj.event === 'ready') {
        this.isReady = true;
        this.restartAttempts = 0;
      }
      this._emit(obj.event, obj.data);
    }
  }

  // ---------- requests ----------
  /**
   * Envoie une commande au backend et renvoie une Promise.
   * @param {string} method
   * @param {object} params
   * @param {number} timeout ms (défaut 15s)
   */
  send(method, params = {}, timeout = 15000) {
    return new Promise((resolve, reject) => {
      if (this._disposed) { reject(new Error('Bridge disposé.')); return; }
      if (!this.process || !this.isRunning) {
        reject(new Error('Backend non démarré.'));
        return;
      }
      const id = String(this.nextId++);
      const req = JSON.stringify({ id, method, params }) + '\n';
      const timer = setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`Délai dépassé pour '${method}'.`));
        }
      }, timeout);

      this.pending.set(id, { resolve, reject, timer, method });
      try {
        if (!this.process.stdin || this.process.stdin.destroyed) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(new Error('stdin fermé.'));
          return;
        }
        this.process.stdin.write(req, (err) => {
          if (err) {
            clearTimeout(timer);
            this.pending.delete(id);
            reject(new Error(`Écriture stdin impossible : ${err.message}`));
          }
        });
      } catch (e) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new Error(`Écriture stdin impossible : ${e.message}`));
      }
    });
  }

  // ---------- restart / dispose ----------
  /**
   * Relance proprement le backend.
   */
  async restart() {
    if (this._disposed) return false;
    if (this._restarting) return false;
    this._restarting = true;
    this.restartAttempts = 0;

    // Annule l'auto-restart en cours.
    if (this._exitTimer) {
      clearTimeout(this._exitTimer);
      this._exitTimer = null;
    }

    if (this.process) {
      // On évite que _handleExit déclenche un auto-restart en parallèle.
      this.autoRestart = false;
      try { this.process.kill(); } catch (e) { /* ignore */ }
      // Attend que le process se termine réellement (max 2s).
      const t0 = Date.now();
      while (this.process && Date.now() - t0 < 2000) {
        await new Promise((r) => setTimeout(r, 50));
      }
    }
    // Nettoyage forcé (sans déclencher de restart).
    this._forceCleanup();
    this.autoRestart = true;
    this._restarting = false;

    try { this.start(); } catch (e) { /* ignore */ }
    return true;
  }

  _forceCleanup() {
    if (this.rl) {
      try { this.rl.close(); } catch (e) { /* ignore */ }
      this.rl = null;
    }
    this.process = null;
    this.isRunning = false;
    this.isReady = false;
    this._exited = true;
    for (const [, entry] of this.pending) {
      clearTimeout(entry.timer);
      try { entry.reject(new Error('Backend réinitialisé.')); } catch (e) { /* ignore */ }
    }
    this.pending.clear();
  }

  dispose() {
    this._disposed = true;
    this.autoRestart = false;
    this._restarting = false;
    if (this._exitTimer) {
      clearTimeout(this._exitTimer);
      this._exitTimer = null;
    }
    if (this.process) {
      try { this.process.kill(); } catch (e) { /* ignore */ }
    }
    this._forceCleanup();
  }
}

// Raccourcis typés (compat ascendante).
BackendBridge.prototype.ping = function () { return this.send('ping'); };
BackendBridge.prototype.getStatus = function () { return this.send('get_status'); };
BackendBridge.prototype.getConfig = function () { return this.send('get_config'); };
BackendBridge.prototype.saveConfig = function (cfg) { return this.send('save_config', cfg); };
BackendBridge.prototype.startMonitoring = function () { return this.send('start_monitoring'); };
BackendBridge.prototype.stopMonitoring = function () { return this.send('stop_monitoring'); };
BackendBridge.prototype.setAutoRecord = function (enabled) { return this.send('set_auto_record', { enabled }); };
BackendBridge.prototype.startRecord = function () { return this.send('start_record'); };
BackendBridge.prototype.stopRecord = function () { return this.send('stop_record'); };
BackendBridge.prototype.testObs = function () { return this.send('test_obs'); };
BackendBridge.prototype.reconnectObs = function () { return this.send('reconnect_obs'); };
BackendBridge.prototype.launchObs = function () { return this.send('launch_obs'); };
BackendBridge.prototype.getHistory = function () { return this.send('get_history'); };
BackendBridge.prototype.getValoState = function () { return this.send('get_valo_state'); };

module.exports = { BackendBridge, resolveLaunch };
