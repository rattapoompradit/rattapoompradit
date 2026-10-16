(function () {
  const IDLE_MS = 60_000; // home: no touch for this long -> clean home screen
  const SIGN_IDLE_MS = 5 * 60_000; // sign page waits longer: people pose for photos after signing
  const QUIET_MS = 3_000; // poster page: buttons fade after this long without a touch
  const EXIT_HOLD_MS = 5_000;

  const $ = (id) => document.getElementById(id);
  const saver = $('saver');
  const saverVideo = $('saver-video');
  const saverImg = $('saver-img');
  const home = $('home');
  const bgVideo = $('bg-video');
  const toSaver = $('to-saver');
  const sign = $('sign');
  const search = $('search');
  const searchClear = $('search-clear');
  const grid = $('grid');
  const empty = $('empty');
  const kbEl = $('keyboard');
  const signImg = $('sign-img');
  const padHint = $('pad-hint');

  let logos = [];
  let tiles = [];
  let dir = '';

  // ---------- home: logo grid + search ----------
  // A 9:16 image is a clinic poster (REVIVE template): the home button is its white circle and
  // the sign page shows the whole poster. Anything else is a plain logo shown as is.
  const isPoster = (img) => img.naturalWidth > 0 && img.naturalHeight / img.naturalWidth >= 1.5;

  function buildTiles() {
    tiles = logos.map((item) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'tile';
      const img = new Image();
      img.src = item.url;
      img.alt = '';
      img.loading = 'lazy';
      img.decoding = 'async';
      img.draggable = false;
      img.addEventListener('load', () => {
        item.poster = isPoster(img);
        b.classList.toggle('poster', item.poster);
      });
      const logo = document.createElement('span');
      logo.className = 'logo';
      logo.append(img);
      b.setAttribute('aria-label', item.name);
      b.append(logo);
      b.addEventListener('click', () => openSign(item));
      return { el: b, name: item.name };
    });
    grid.replaceChildren(...tiles.map((t) => t.el));
    applyFilter();
  }

  function applyFilter() {
    const q = search.value;
    let shown = 0;
    for (const t of tiles) {
      t.el.hidden = !matches(t.name, q);
      if (!t.el.hidden) shown++;
    }
    searchClear.hidden = !q;
    if (!logos.length) {
      empty.textContent = `ยังไม่มีโลโก้ — ใส่ไฟล์รูปในโฟลเดอร์ ${dir} (ชื่อไฟล์ = ชื่อคลินิก)`;
    } else {
      empty.textContent = 'ไม่พบคลินิกที่ค้นหา';
    }
    empty.hidden = shown > 0;
    grid.hidden = shown === 0;
  }

  function setQuery(value) {
    search.value = value;
    grid.scrollTop = 0;
    applyFilter();
  }

  const keyboard = createKeyboard(kbEl, {
    onChar: (ch) => setQuery(search.value + ch),
    onBackspace: () => setQuery(Array.from(search.value).slice(0, -1).join('')),
    onClear: () => setQuery(''),
    onHide: hideKeyboard,
  });

  function showKeyboard() {
    kbEl.hidden = false;
  }
  function hideKeyboard() {
    kbEl.hidden = true;
    search.blur();
  }

  search.addEventListener('focus', showKeyboard);
  search.addEventListener('pointerdown', showKeyboard);
  search.addEventListener('input', () => {
    grid.scrollTop = 0;
    applyFilter();
  });
  searchClear.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    setQuery('');
  });

  // ---------- sign page ----------
  const pad = createPad($('pad'), (inked) => {
    padHint.hidden = inked;
    sign.classList.toggle('inked', inked);
  });

  function setPosterMode(on) {
    sign.classList.toggle('poster', on);
    padHint.textContent = on ? 'เซ็นชื่อที่นี่' : 'เซ็นชื่อในกรอบนี้';
  }

  function openSign(item) {
    hideKeyboard();
    setPosterMode(!!item.poster);
    sign.style.setProperty('--poster', `url("${item.url}")`);
    signImg.onload = () => setPosterMode(isPoster(signImg));
    signImg.src = item.url;
    pad.clear();
    showPage(sign);
  }

  function goHome() {
    pad.clear();
    showPage(home);
  }

  // ---------- pages + screensaver ----------
  let saverMedia = null;

  // Only the visible page's video plays.
  function playIf(video, on) {
    if (on && !video.hidden) video.play().catch(() => {});
    else video.pause();
  }

  function showPage(page) {
    for (const p of [saver, home, sign]) p.hidden = p !== page;
    playIf(bgVideo, page === home);
    playIf(saverVideo, page === saver);
    poke();
  }

  function setVideo(video, media) {
    const url = media && media.video ? media.url : '';
    if (video.dataset.url === url) return;
    video.dataset.url = url;
    video.hidden = !url;
    if (url) video.src = url;
    else video.removeAttribute('src');
  }

  saver.addEventListener('click', goHome);
  toSaver.addEventListener('click', () => resetAll());

  // A palm or second finger landing on a button mid-signature must not clear or leave the page.
  $('btn-clear').addEventListener('click', () => pad.drawing() || pad.clear());
  $('btn-back').addEventListener('click', () => pad.drawing() || goHome());

  function createPad(canvas, onInk) {
    const ctx = canvas.getContext('2d');
    let active = null;
    let last = null;
    let mid = null;
    let base = 4;
    let inked = false;

    function setInked(v) {
      if (inked !== v) {
        inked = v;
        onInk(v);
      }
    }

    function resize() {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (!w || !h) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      base = Math.min(9, Math.max(3, w / 180));
      active = null;
      setInked(false);
    }

    let scale = 1;
    const lineWidth = (e) => scale * (e.pointerType === 'pen' && e.pressure > 0 ? base * (0.5 + e.pressure) : base);

    canvas.addEventListener('pointerdown', (e) => {
      // Only the first finger draws; a resting palm or second finger is ignored.
      if (active !== null || (e.pointerType === 'mouse' && e.button !== 0)) return;
      e.preventDefault();
      active = e.pointerId;
      canvas.setPointerCapture(e.pointerId);
      const style = getComputedStyle(canvas);
      ctx.strokeStyle = ctx.fillStyle = style.color;
      scale = parseFloat(style.getPropertyValue('--ink-scale')) || 1;
      const r = canvas.getBoundingClientRect();
      last = mid = { x: e.clientX - r.left, y: e.clientY - r.top };
      ctx.lineWidth = lineWidth(e);
      ctx.beginPath();
      ctx.arc(last.x, last.y, ctx.lineWidth / 2, 0, Math.PI * 2);
      ctx.fill();
      setInked(true);
    });

    canvas.addEventListener('pointermove', (e) => {
      if (e.pointerId !== active) return;
      const r = canvas.getBoundingClientRect();
      const events = (e.getCoalescedEvents && e.getCoalescedEvents()) || [];
      for (const ev of events.length ? events : [e]) {
        const p = { x: ev.clientX - r.left, y: ev.clientY - r.top };
        const m = { x: (last.x + p.x) / 2, y: (last.y + p.y) / 2 };
        ctx.lineWidth = lineWidth(ev);
        ctx.beginPath();
        ctx.moveTo(mid.x, mid.y);
        ctx.quadraticCurveTo(last.x, last.y, m.x, m.y);
        ctx.stroke();
        last = p;
        mid = m;
      }
    });

    function end(e) {
      if (e.pointerId !== active) return;
      ctx.beginPath();
      ctx.moveTo(mid.x, mid.y);
      ctx.lineTo(last.x, last.y);
      ctx.stroke();
      active = null;
    }
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', end);

    new ResizeObserver(resize).observe(canvas);

    return {
      drawing: () => active !== null,
      clear() {
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.restore();
        active = null;
        setInked(false);
      },
    };
  }

  // ---------- kiosk behaviour ----------
  let idleTimer;
  let quietTimer;
  // Clean slate; with a screensaver file present that is the screensaver, otherwise the menu.
  function resetAll() {
    hideKeyboard();
    keyboard.reset();
    setQuery('');
    pad.clear();
    showPage(saverMedia ? saver : home);
  }
  function poke() {
    clearTimeout(idleTimer);
    if (saver.hidden) idleTimer = setTimeout(resetAll, sign.hidden ? IDLE_MS : SIGN_IDLE_MS);
    sign.classList.remove('quiet');
    clearTimeout(quietTimer);
    if (!sign.hidden) quietTimer = setTimeout(() => sign.classList.add('quiet'), QUIET_MS);
  }
  document.addEventListener('pointerdown', poke, true);
  document.addEventListener('keydown', poke, true);
  poke();

  for (const type of ['contextmenu', 'dragstart', 'selectstart']) {
    document.addEventListener(type, (e) => {
      if (e.target !== search) e.preventDefault();
    });
  }
  document.addEventListener('wheel', (e) => e.ctrlKey && e.preventDefault(), { passive: false });

  let exitTimer;
  const corner = $('corner');
  corner.addEventListener('pointerdown', (e) => {
    // The corner overlaps the search icon: a normal tap there still opens search.
    e.preventDefault();
    if (!home.hidden) search.focus();
    exitTimer = setTimeout(() => window.kiosk && window.kiosk.quit(), EXIT_HOLD_MS);
  });
  for (const type of ['pointerup', 'pointerleave', 'pointercancel']) {
    corner.addEventListener(type, () => clearTimeout(exitTimer));
  }

  // ---------- logos ----------
  function applyBackground(bg) {
    const image = bg && !bg.video ? bg.url : null;
    document.body.classList.toggle('has-bg', !!bg);
    if (image) document.body.style.setProperty('--bg-image', `url("${image}")`);
    else document.body.style.removeProperty('--bg-image');
    setVideo(bgVideo, bg);
    playIf(bgVideo, !home.hidden);
  }

  function applySaver(media) {
    saverMedia = media;
    setVideo(saverVideo, media);
    saverImg.hidden = !media || media.video;
    if (media && !media.video) saverImg.src = media.url;
    toSaver.hidden = !media;
    if (!saver.hidden) {
      if (media) playIf(saverVideo, true);
      else showPage(home);
    }
  }

  let loaded = null;
  async function loadLogos() {
    if (window.kiosk) {
      const res = await window.kiosk.listLogos();
      // The folder watcher also fires when a file is only read (access time); skip if nothing changed.
      const signature = JSON.stringify(res);
      if (signature === loaded) return;
      const first = loaded === null;
      loaded = signature;
      dir = res.dir;
      logos = res.items;
      applyBackground(res.background);
      applySaver(res.screensaver);
      if (first && saverMedia) showPage(saver); // start on the screensaver
    } else {
      dir = '(เปิดผ่านโปรแกรม ClinicSign เท่านั้น)';
    }
    buildTiles();
  }

  if (window.kiosk) window.kiosk.onLogosChanged(loadLogos);
  loadLogos();
})();
