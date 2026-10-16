const { app, BrowserWindow, ipcMain, powerSaveBlocker } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { parseLogoFile, specialFile, sortLogos } = require('./web/logic.js');

const args = process.argv.slice(1);
const flag = (name) => args.includes(`--${name}`);
const option = (name) => {
  const hit = args.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : null;
};

const selftest = flag('selftest');
const windowed = flag('windowed') || selftest;

app.commandLine.appendSwitch('disable-pinch');
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
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

// Default videos shipped with the app (resources/media in the installer, ./media when run from
// source). A _background / _screensaver file in the logo folder always wins.
const mediaDir = app.isPackaged ? path.join(process.resourcesPath, 'media') : path.join(__dirname, 'media');

function bundledMedia() {
  const found = {};
  let entries = [];
  try {
    entries = fs.readdirSync(mediaDir);
  } catch {
    return found;
  }
  for (const name of entries) {
    const role = specialFile(name);
    if (role && (!found[role.role] || role.video)) found[role.role] = { url: fileUrl(path.join(mediaDir, name)), video: role.video };
  }
  return found;
}

function listLogos(dir) {
  const items = [];
  const special = {};
  let entries = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    entries = [];
  }
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const full = path.join(dir, entry.name);
    const role = specialFile(entry.name);
    if (role) {
      // A video wins over an image for the same role.
      if (!special[role.role] || role.video) special[role.role] = { url: fileUrl(full), video: role.video };
      continue;
    }
    const parsed = parseLogoFile(entry.name);
    if (parsed) items.push({ ...parsed, url: fileUrl(full) });
  }
  const bundled = flag('no-bundled-media') ? {} : bundledMedia();
  return {
    dir,
    items: sortLogos(items),
    background: special.background || bundled.background || null,
    screensaver: special.screensaver || bundled.screensaver || null,
  };
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

  // Lay out as if the short side were 1080 px: a 4K screen at 100% Windows scaling then looks
  // like Full HD (only sharper) instead of tiny text and buttons.
  const fit = () => {
    const [cw, ch] = w.getContentSize();
    wc.setZoomFactor(Math.min(4, Math.max(0.5, Math.min(cw, ch) / 1080)));
  };
  wc.on('did-finish-load', fit);
  w.on('resize', fit);

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
