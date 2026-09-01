'use strict';

// preload.js — Pont sécurisé (contextIsolation) entre le renderer et le main.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  /**
   * Appelle une méthode du backend Python.
   * @returns {Promise<{ok:boolean, data?:any, error?:string}>}
   */
  call: (method, params) => ipcRenderer.invoke('backend:call', { method, params }),

  // Dialogues natifs (choix de dossier / fichier).
  openDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
  openFile: (filters) => ipcRenderer.invoke('dialog:openFile', { filters }),

  // Ouvre un chemin dans l'explorateur (ou un fichier).
  // Renvoie { ok, error } pour permettre à l'UI de signaler un échec.
  openPath: (p) => ipcRenderer.invoke('shell:openPath', p),

  // Relance le backend Python.
  restartBackend: () => ipcRenderer.invoke('backend:restart'),

  // Version de l'app.
  getVersion: () => ipcRenderer.invoke('app:getVersion'),

  // Démarrage avec Windows.
  setAutoLaunch: (enabled) => ipcRenderer.invoke('app:setAutoLaunch', { enabled }),
  getAutoLaunch: () => ipcRenderer.invoke('app:getAutoLaunch'),

  // Contrôles fenêtre.
  showWindow: () => ipcRenderer.invoke('app:showWindow'),
  hideWindow: () => ipcRenderer.invoke('app:hideWindow'),
  quit: () => ipcRenderer.invoke('app:quit'),

  // Abonnements aux événements temps réel envoyés par le backend.
  on: (channel, cb) => {
    const allowed = [
      'backend:ready',
      'backend:status',
      'backend:log',
      'backend:config_changed',
      'backend:match_started',
      'backend:match_ended',
      'backend:error',
      'backend:closed',
    ];
    if (!allowed.includes(channel)) return () => {};
    const wrapped = (_evt, data) => cb(data);
    ipcRenderer.on(channel, wrapped);
    return () => ipcRenderer.removeListener(channel, wrapped);
  },
});
