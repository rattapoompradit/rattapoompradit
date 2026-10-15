const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('kiosk', {
  listLogos: () => ipcRenderer.invoke('logos:list'),
  onLogosChanged: (cb) => ipcRenderer.on('logos:changed', () => cb()),
  quit: () => ipcRenderer.send('app:quit'),
});
