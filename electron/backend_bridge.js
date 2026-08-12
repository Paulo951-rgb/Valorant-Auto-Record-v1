'use strict';

// backend_bridge.js
// Gère le cycle de vie du backend Python et la communication JSON-lines.
// Lancé dans le main process Electron.

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const readline = require('readline');

/**
 * Détermine la commande et les arguments à lancer.
 * Stratégie :
 *   1. PYTHON_EXEC (env) pointant vers un exécutable backend PyInstaller.
 *   2. backend.exe/.sh packagé dans les ressources (mode distribué).
 *   3. interpréteur Python système + backend.py (dev ou ressources .py).
 * Retourne { cmd, args, backendDir }.
 */
function resolveLaunch() {
  const isWin = process.platform === 'win32';

  // 1. Variable d'environnement explicite.
  if (process.env.PYTHON_EXEC && fs.existsSync(process.env.PYTHON_EXEC)) {
    return { cmd: process.env.PYTHON_EXEC, args: [], backendDir: path.dirname(process.env.PYTHON_EXEC) };
  }

  // 2. Backend déjà compilé (PyInstaller) dans les ressources.
  const resPythonDir = process.resourcesPath ? path.join(process.resourcesPath, 'python') : null;
  if (resPythonDir) {
    const exeName = isWin ? 'backend.exe' : 'backend';
    const packagedExe = path.join(resPythonDir, exeName);
    if (fs.existsSync(packagedExe)) {
      return { cmd: packagedExe, args: [], backendDir: resPythonDir };
    }
  }

  // 3. Interpréteur Python système + backend.py.
  //    - En distribué : backend.py est dans resourcesPath/python/
  //    - En dev       : backend.py est dans <projet>/python/
  let backendScript;
  if (resPythonDir && fs.existsSync(path.join(resPythonDir, 'backend.py'))) {
    backendScript = path.join(resPythonDir, 'backend.py');
  } else {
    backendScript = path.join(__dirname, '..', 'python', 'backend.py');
  }
  const pythonExe = isWin ? 'python' : 'python3';
  return { cmd: pythonExe, args: [backendScript], backendDir: path.dirname(backendScript) };
}

class BackendBridge {
  constructor() {
    this.process = null;
    this.rl = null;
    this.pending = new Map(); // id -> {resolve, reject, timer}
    this.nextId = 1;
    this.listeners = {
      status: new Set(),
      log: new Set(),
      ready: new Set(),
      error: new Set(),
      closed: new Set(),
      config_changed: new Set(),
      match_started: new Set(),
      match_ended: new Set(),
    };
    this.isReady = false;
    this.isRunning = false;
    this.restartAttempts = 0;
    this.maxRestartAttempts = 5;
    this.autoRestart = true;
    this._restarting = false;
  }

  on(event, cb) {
    if (!this.listeners[event]) this.listeners[event] = new Set();
    this.listeners[event].add(cb);
    return () => this.listeners[event].delete(cb);
  }

  _emit(event, data) {
    (this.listeners[event] || []).forEach((cb) => {
      try {
        cb(data);
      } catch (e) {
        console.error('Erreur listener', event, e);
      }
    });
  }

  start() {
    if (this.process) return;
    const { cmd, args, backendDir } = resolveLaunch();

    if (!args.length && !fs.existsSync(cmd)) {
      this._emit('error', { message: `Backend introuvable : ${cmd}` });
      return;
    }
    if (args.length && !fs.existsSync(args[0])) {
      this._emit('error', { message: `backend.py introuvable : ${args[0]}` });
      return;
    }

    try {
      this.process = spawn(cmd, args, {
        cwd: backendDir,
        env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
        windowsHide: true,
      });
    } catch (e) {
      this._emit('error', { message: `Impossible de lancer Python : ${e.message}` });
      return;
    }

    this.isRunning = true;
    this.rl = readline.createInterface({ input: this.process.stdout });

    this.rl.on('line', (line) => this._handleLine(line));

    this.process.stderr.on('data', (data) => {
      // Les logs/traces Python vont sur stderr (redirigés depuis stdout côté Python).
      const text = data.toString().trim();
      if (text) {
        this._emit('log', {
          timestamp: new Date().toTimeString().slice(0, 8),
          level: 'DEBUG',
          message: text,
          raw: text,
          fromStderr: true,
        });
      }
    });

    this.process.on('error', (err) => {
      this._emit('error', { message: `Erreur de processus Python : ${err.message}` });
      this._handleExit();
    });

    this.process.on('exit', (code, signal) => {
      this._emit('closed', { code, signal });
      this._handleExit();
    });
  }

  _handleExit() {
    const wasRunning = this.isRunning;
    this.isRunning = false;
    this.isReady = false;
    this.process = null;
    if (this.rl) {
      this.rl.close();
      this.rl = null;
    }
    // Rejette les requêtes en attente.
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      entry.reject(new Error('Backend arrêté avant réponse.'));
    }
    this.pending.clear();

    if (wasRunning && this.autoRestart && this.restartAttempts < this.maxRestartAttempts) {
      this.restartAttempts++;
      const delay = Math.min(1000 * this.restartAttempts, 5000);
      this._emit('log', {
        timestamp: new Date().toTimeString().slice(0, 8),
        level: 'WARNING',
        message: `Backend arrêté. Reconnexion dans ${delay / 1000}s (tentative ${this.restartAttempts}/${this.maxRestartAttempts}).`,
        raw: '',
      });
      setTimeout(() => this.start(), delay);
    } else if (wasRunning && this.restartAttempts >= this.maxRestartAttempts) {
      this._emit('error', { message: 'Backend injoignable après plusieurs tentatives.' });
    }
  }

  _handleLine(line) {
    let obj;
    try {
      obj = JSON.parse(line);
    } catch (e) {
      return; // ligne non-JSON ignorée
    }

    if (obj.id !== undefined) {
      // Réponse à une requête.
      const entry = this.pending.get(obj.id);
      if (entry) {
        clearTimeout(entry.timer);
        this.pending.delete(obj.id);
        if (obj.error) {
          entry.reject(new Error(obj.error));
        } else {
          entry.resolve(obj.result);
        }
      }
      return;
    }

    if (obj.event) {
      if (obj.event === 'ready') {
        this.isReady = true;
        this.restartAttempts = 0;
        this._emit('ready', obj.data);
      } else if (obj.event === 'status') {
        this._emit('status', obj.data);
      } else if (obj.event === 'log') {
        this._emit('log', obj.data);
      } else if (obj.event === 'config_changed') {
        this._emit('config_changed', obj.data);
      } else if (obj.event === 'match_started') {
        this._emit('match_started', obj.data);
      } else if (obj.event === 'match_ended') {
        this._emit('match_ended', obj.data);
      } else if (obj.event === 'error') {
        this._emit('error', obj.data);
      }
    }
  }

  /**
   * Envoie une commande au backend et renvoie une Promise.
   * @param {string} method
   * @param {object} params
   * @param {number} timeout ms (défaut 15s)
   */
  send(method, params = {}, timeout = 15000) {
    return new Promise((resolve, reject) => {
      if (!this.process || !this.isRunning) {
        reject(new Error('Backend non démarré.'));
        return;
      }
      const id = String(this.nextId++);
      const req = JSON.stringify({ id, method, params }) + '\n';
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Délai dépassé pour '${method}'.`));
      }, timeout);

      this.pending.set(id, { resolve, reject, timer });
      try {
        this.process.stdin.write(req);
      } catch (e) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new Error(`Écriture stdin impossible : ${e.message}`));
      }
    });
  }

  // Raccourcis typés.
  ping() { return this.send('ping'); }
  getStatus() { return this.send('get_status'); }
  getConfig() { return this.send('get_config'); }
  saveConfig(cfg) { return this.send('save_config', cfg); }
  startMonitoring() { return this.send('start_monitoring'); }
  stopMonitoring() { return this.send('stop_monitoring'); }
  setAutoRecord(enabled) { return this.send('set_auto_record', { enabled }); }
  startRecord() { return this.send('start_record'); }
  stopRecord() { return this.send('stop_record'); }
  testObs() { return this.send('test_obs'); }
  reconnectObs() { return this.send('reconnect_obs'); }
  launchObs() { return this.send('launch_obs'); }
  getHistory() { return this.send('get_history'); }
  getValoState() { return this.send('get_valo_state'); }

  /**
   * Relance proprement le backend (bouton "Relancer le backend").
   */
  async restart() {
    if (this._restarting) return;
    this._restarting = true;
    this.restartAttempts = 0;
    // Désactive l'auto-restart le temps du redémarrage pour éviter un double spawn.
    this.autoRestart = false;
    if (this.process) {
      try { this.process.kill(); } catch (e) { /* ignore */ }
      // attend que le processus se termine effectivement
      await new Promise((r) => setTimeout(r, 500));
    }
    this._handleExitCleanup();
    this.autoRestart = true;
    this._restarting = false;
    this.start();
  }

  _handleExitCleanup() {
    if (this.rl) { try { this.rl.close(); } catch (e) {} this.rl = null; }
    this.process = null;
    this.isRunning = false;
    this.isReady = false;
  }

  dispose() {
    this.autoRestart = false;
    if (this.process) {
      try { this.process.kill(); } catch (e) { /* ignore */ }
    }
    this._handleExitCleanup();
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      entry.reject(new Error('Backend disposé.'));
    }
    this.pending.clear();
  }
}

module.exports = { BackendBridge, resolveLaunch };
