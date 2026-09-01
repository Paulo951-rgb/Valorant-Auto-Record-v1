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

const isDev = process.argv.includes('--dev');

// Si packaging Windows : single-instance lock pour éviter plusieurs fenêtres.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
  process.exit(0);
}
app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    if (!mainWindow.isVisible()) mainWindow.show();
    mainWindow.focus();
  }
});

function getAssetPath(name) {
  return path.join(__dirname, '..', 'assets', name);
}

function createWindow() {
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
  mainWindow.on('close', async (e) => {
    if (isQuitting) return;
    const cfg = readConfigSync();
    if (cfg && cfg.minimize_to_tray) {
      e.preventDefault();
      mainWindow.hide();
      return false;
    }
  });

  mainWindow.on('closed', () => { mainWindow = null; });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

function readConfigSync() {
  try {
    const p = path.join(__dirname, '..', 'python', 'config_local.json');
    if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, 'utf-8'));
  } catch (e) { /* ignore */ }
  return null;
}

function buildTray() {
  if (tray) return;
  let img = null;
  const iconPath = getAssetPath('tray.png');
  if (fs.existsSync(iconPath)) {
    img = nativeImage.createFromPath(iconPath);
  } else {
    img = nativeImage.createEmpty();
  }
  try {
    tray = new Tray(img);
  } catch (e) {
    tray = null;
    return;
  }
  tray.setToolTip('Valorant Auto Record');
  const menu = Menu.buildFromTemplate([
    { label: 'Ouvrir la fenêtre', click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } else { createWindow(); } } },
    { label: 'Quitter', click: () => { isQuitting = true; app.quit(); } },
  ]);
  tray.setContextMenu(menu);
  tray.on('click', () => {
    if (mainWindow) {
      if (mainWindow.isVisible()) mainWindow.hide();
      else { mainWindow.show(); mainWindow.focus(); }
    } else { createWindow(); }
  });
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
    mainWindow.webContents.send(channel, data);
  }
}

function handle(method, fn) {
  ipcMain.handle(method, async (_evt, ...args) => {
    try {
      return { ok: true, data: await fn(...args) };
    } catch (e) {
      return { ok: false, error: e.message };
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
    if (p && fs.existsSync(p)) { shell.openPath(p); return true; }
    return false;
  });
  handle('backend:restart', async () => {
    if (backend) { await backend.restart(); return true; }
    return false;
  });
  handle('app:getVersion', async () => app.getVersion());
  handle('app:setAutoLaunch', async ({ enabled }) => {
    if (process.platform === 'win32' || process.platform === 'darwin') {
      try { app.setLoginItemSettings({ openAtLogin: !!enabled, openAsHidden: true }); } catch (e) { /* ignore */ }
    }
    return { enabled: !!enabled };
  });
  handle('app:getAutoLaunch', async () => {
    if (process.platform === 'win32' || process.platform === 'darwin') {
      try { return app.getLoginItemSettings().openAtLogin; } catch (e) { return false; }
    }
    return false;
  });
}

app.whenReady().then(() => {
  registerIpc();
  createWindow();
  buildTray();
  startBackend();

  // Sync auto-launch (best effort, non bloquant)
  try {
    const cfg = readConfigSync();
    if (cfg && cfg.start_with_windows) {
      app.setLoginItemSettings({ openAtLogin: true, openAsHidden: true });
    }
  } catch (e) { /* ignore */ }

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
  if (backend) { backend.dispose(); backend = null; }
});
