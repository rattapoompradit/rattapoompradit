const { app, BrowserWindow, ipcMain, powerSaveBlocker } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { parseLogoFile, isBackgroundFile, sortLogos } = require('./web/logic.js');

const args = process.argv.slice(1);
const flag = (name) => args.includes(`--${name}`);
const option = (name) => {
  const hit = args.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : null;
};

const selftest = flag('selftest');
const windowed = flag('windowed') || selftest;

app.commandLine.appendSwitch('disable-pinch');
app.commandLine.appendSwitch('overscroll-history-navigation', '0');

let win = null;

function logosDir() {
  const dir = option('logos') ? path.resolve(option('logos')) : path.join(app.getPath('documents'), 'ClinicLogos');
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

// mtime in the URL so a replaced file with the same name is not served from cache.
function fileUrl(full) {
  return `${pathToFileURL(full).href}?v=${Math.round(fs.statSync(full).mtimeMs)}`;
}

function listLogos(dir) {
  const items = [];
  let background = null;
  let entries = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return { dir, items, background };
  }
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const full = path.join(dir, entry.name);
    if (isBackgroundFile(entry.name)) {
      background = fileUrl(full);
      continue;
    }
    const parsed = parseLogoFile(entry.name);
    if (parsed) items.push({ ...parsed, url: fileUrl(full) });
  }
  return { dir, items: sortLogos(items), background };
}

function watchLogos(dir) {
  let timer;
  try {
    fs.watch(dir, () => {
      clearTimeout(timer);
      timer = setTimeout(() => win && win.webContents.send('logos:changed'), 800);
    });
  } catch {
    // No live reload; restarting the app picks up new logos.
  }
}

function createWindow() {
  const [width, height] = (option('size') || '540x960').split('x').map(Number);
  const w = new BrowserWindow({
    kiosk: !windowed,
    fullscreen: !windowed,
    width,
    height,
    useContentSize: true,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#eef3f4',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      sandbox: true,
      spellcheck: false,
      devTools: !app.isPackaged || flag('dev'),
    },
  });
  w.setMenu(null);

  const wc = w.webContents;
  wc.setVisualZoomLevelLimits(1, 1);
  wc.on('will-navigate', (e) => e.preventDefault());
  wc.setWindowOpenHandler(() => ({ action: 'deny' }));
  wc.on('render-process-gone', () => wc.reload());
  wc.on('before-input-event', (e, input) => {
    if (input.type !== 'keyDown' || !input.control) return;
    const key = input.key.toLowerCase();
    if (input.shift && key === 'q') {
      e.preventDefault();
      app.quit();
    } else if (['+', '-', '=', '0', 'p', 's'].includes(key)) {
      e.preventDefault();
    }
  });

  w.once('ready-to-show', () => w.show());
  w.loadFile(path.join(__dirname, 'web', 'index.html'));
  return w;
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) win.focus();
  });

  app.whenReady().then(() => {
    const dir = logosDir();
    ipcMain.handle('logos:list', () => listLogos(dir));
    ipcMain.on('app:quit', () => app.quit());

    if (!windowed) {
      if (app.isPackaged) app.setLoginItemSettings({ openAtLogin: true });
      powerSaveBlocker.start('prevent-display-sleep');
    }

    win = createWindow();
    watchLogos(dir);

    if (selftest) {
      require('./selftest.js')(win, {
        out: option('selftest-out') || path.join(process.cwd(), 'selftest.txt'),
        shots: option('shots'),
      }).then((code) => app.exit(code));
    }
  });

  app.on('window-all-closed', () => app.quit());
}
