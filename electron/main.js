'use strict';

// main.js — Processus principal Electron
const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { BackendBridge } = require('./backend_bridge');

let mainWindow = null;
let backend = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 960,
    minHeight: 640,
    title: 'Valorant Auto Record',
    backgroundColor: '#0f1117',
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
    if (process.argv.includes('--dev')) {
      mainWindow.webContents.openDevTools({ mode: 'detach' });
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // Ouvre les liens externes dans le navigateur par défaut.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

function startBackend() {
  if (backend) return;
  backend = new BackendBridge();

  // Relais des événements backend vers le renderer.
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

// =====================================================================
// IPC : commandes du renderer vers le backend (via invoke/handle)
// =====================================================================
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
    if (p && fs.existsSync(p)) {
      shell.openPath(p);
      return true;
    }
    return false;
  });

  handle('backend:restart', async () => {
    if (backend) {
      await backend.restart();
      return true;
    }
    return false;
  });

  handle('app:getVersion', async () => app.getVersion());
}

// =====================================================================
// Cycle de vie
// =====================================================================
app.whenReady().then(() => {
  registerIpc();
  createWindow();
  startBackend();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (backend) {
    backend.dispose();
    backend = null;
  }
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (backend) {
    backend.dispose();
    backend = null;
  }
});
