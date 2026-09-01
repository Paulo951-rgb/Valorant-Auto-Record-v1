'use strict';

// main.js — Processus principal Electron
const { app, BrowserWindow, ipcMain, dialog, shell, Tray, Menu,
        nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const { BackendBridge } = require('./backend_bridge');

let mainWindow = null;
let backend = null;
let tray = null;
let isQuitting = false;
let appReady = false;

const isDev = process.argv.includes('--dev');

// Single-instance lock : évite plusieurs fenêtres concurrentes.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      if (!mainWindow.isVisible()) mainWindow.show();
      mainWindow.focus();
    }
  });
}

function getAssetPath(name) {
  return path.join(__dirname, '..', 'assets', name);
}

function readConfigSync() {
  try {
    const p = path.join(__dirname, '..', 'python', 'config_local.json');
    if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, 'utf-8'));
  } catch (e) { /* ignore */ }
  return null;
}

function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
    return;
  }
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 720,
    minWidth: 880,
    minHeight: 600,
    title: 'Valorant Auto Record',
    backgroundColor: '#0b0d12',
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    if (isDev) mainWindow.webContents.openDevTools({ mode: 'detach' });
  });

  // Fermeture = minimize vers la zone de notification si configuré.
  // Important : le preventDefault doit être synchrone pour être pris en compte.
  mainWindow.on('close', (e) => {
    if (isQuitting) return;
    let cfg = null;
    try {
      const p = path.join(__dirname, '..', 'python', 'config_local.json');
      if (fs.existsSync(p)) cfg = JSON.parse(fs.readFileSync(p, 'utf-8'));
    } catch (err) { cfg = null; }
    if (cfg && cfg.minimize_to_tray) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.on('closed', () => { mainWindow = null; });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

function buildTray() {
  if (tray) return;
  const iconPath = getAssetPath('tray.png');
  const img = fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
  let monitoring = false;
  try {
    tray = new Tray(img);
  } catch (e) {
    tray = null;
    return;
  }
  tray.setToolTip('Valorant Auto Record');
  const rebuildMenu = () => {
    const menu = Menu.buildFromTemplate([
      { label: 'Ouvrir la fenêtre', click: () => createWindow() },
      { type: 'separator' },
      {
        label: 'Surveillance',
        type: 'checkbox',
        checked: monitoring,
        enabled: !!backend,
        click: () => {
          if (!backend) return;
          if (monitoring) {
            backend.send('stop_monitoring');
          } else {
            backend.send('start_monitoring');
          }
          setTimeout(rebuildMenu, 500);
        },
      },
      { type: 'separator' },
      { label: 'Quitter', click: () => { isQuitting = true; app.quit(); } },
    ]);
    tray.setContextMenu(menu);
  };
  rebuildMenu();
  tray.on('click', () => {
    if (mainWindow) {
      if (mainWindow.isVisible()) mainWindow.hide();
      else { mainWindow.show(); mainWindow.focus(); }
    } else { createWindow(); }
  });
  // Track monitoring state via backend notifications.
  if (backend) {
    backend.on('status', (data) => {
      if (data && typeof data.monitoring === 'boolean') {
        monitoring = data.monitoring;
        rebuildMenu();
      }
    });
  }
}

function startBackend() {
  if (backend) return;
  backend = new BackendBridge();

  backend.on('ready', (data) => sendToRenderer('backend:ready', data));
  backend.on('status', (data) => sendToRenderer('backend:status', data));
  backend.on('log', (data) => sendToRenderer('backend:log', data));
  backend.on('config_changed', (data) => sendToRenderer('backend:config_changed', data));
  backend.on('match_started', (data) => sendToRenderer('backend:match_started', data));
  backend.on('match_ended', (data) => sendToRenderer('backend:match_ended', data));
  backend.on('error', (data) => sendToRenderer('backend:error', data));
  backend.on('closed', (data) => sendToRenderer('backend:closed', data));

  backend.start();
}

function sendToRenderer(channel, data) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try { mainWindow.webContents.send(channel, data); } catch (e) { /* ignore */ }
  }
}

function handle(method, fn) {
  ipcMain.handle(method, async (_evt, ...args) => {
    try {
      const data = await fn(...args);
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: e && e.message ? e.message : String(e) };
    }
  });
}

function registerIpc() {
  handle('backend:call', async ({ method, params }) => {
    if (!backend) throw new Error('Backend non démarré.');
    return backend.send(method, params || {});
  });
  handle('dialog:openDirectory', async () => {
    if (!mainWindow) return null;
    const result = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });
  handle('dialog:openFile', async ({ filters }) => {
    if (!mainWindow) return null;
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openFile'],
      filters: filters || [{ name: 'Applications', extensions: ['exe'] }],
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });
  handle('shell:openPath', async (p) => {
    if (p && typeof p === 'string' && fs.existsSync(p)) {
      const err = await shell.openPath(p);
      return { ok: !err, error: err || null };
    }
    return { ok: false, error: 'Chemin introuvable' };
  });
  handle('backend:restart', async () => {
    if (backend) { await backend.restart(); return true; }
    return false;
  });
  handle('app:getVersion', async () => app.getVersion());
  handle('app:setAutoLaunch', async ({ enabled }) => {
    if (process.platform === 'win32' || process.platform === 'darwin') {
      try {
        app.setLoginItemSettings({
          openAtLogin: !!enabled,
          openAsHidden: true,
          args: process.platform === 'win32' ? ['--minimized'] : [],
        });
      } catch (e) { /* ignore */ }
    }
    return { enabled: !!enabled };
  });
  handle('app:getAutoLaunch', async () => {
    if (process.platform === 'win32' || process.platform === 'darwin') {
      try { return app.getLoginItemSettings().openAtLogin; } catch (e) { return false; }
    }
    return false;
  });
  handle('app:showWindow', async () => { createWindow(); return true; });
  handle('app:hideWindow', async () => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.hide();
    return true;
  });
  handle('app:quit', async () => { isQuitting = true; app.quit(); return true; });
}

function setupAutoLaunch() {
  try {
    const cfg = readConfigSync();
    if (cfg && cfg.start_with_windows) {
      app.setLoginItemSettings({
        openAtLogin: true,
        openAsHidden: true,
        args: process.platform === 'win32' ? ['--minimized'] : [],
      });
    }
  } catch (e) { /* ignore */ }
}

function killBackend() {
  if (backend) {
    try { backend.dispose(); } catch (e) { /* ignore */ }
    backend = null;
  }
}

app.whenReady().then(() => {
  appReady = true;
  registerIpc();
  setupAutoLaunch();
  createWindow();
  buildTray();
  startBackend();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    isQuitting = true;
    app.quit();
  }
});

app.on('before-quit', () => {
  isQuitting = true;
  killBackend();
});

// Protection : si le process est tué brutalement, on tente de tuer le backend.
process.on('SIGINT', () => { isQuitting = true; killBackend(); process.exit(0); });
process.on('SIGTERM', () => { isQuitting = true; killBackend(); process.exit(0); });

// Empêche le rendu silencieux d'erreurs non capturées.
process.on('uncaughtException', (err) => {
  try { console.error('Uncaught:', err); } catch (e) { /* ignore */ }
});
process.on('unhandledRejection', (err) => {
  try { console.error('Unhandled rejection:', err); } catch (e) { /* ignore */ }
});

module.exports = { isReady: () => appReady };
