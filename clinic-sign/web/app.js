(function () {
  const IDLE_MS = 60_000; // no touch for this long -> back to a clean home screen
  const EXIT_HOLD_MS = 5_000;

  const $ = (id) => document.getElementById(id);
  const home = $('home');
  const sign = $('sign');
  const search = $('search');
  const searchClear = $('search-clear');
  const grid = $('grid');
  const count = $('count');
  const empty = $('empty');
  const kbEl = $('keyboard');
  const signImg = $('sign-img');
  const signName = $('sign-name');
  const padHint = $('pad-hint');

  let logos = [];
  let tiles = [];
  let dir = '';

  // ---------- home: logo grid + search ----------
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
      const label = document.createElement('span');
      label.textContent = item.name;
      b.append(img, label);
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
    count.textContent = q.trim() ? `พบ ${shown} จาก ${logos.length} คลินิก` : `${logos.length} คลินิก`;
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
  });

  function openSign(item) {
    hideKeyboard();
    signImg.src = item.url;
    signName.textContent = item.name;
    home.hidden = true;
    sign.hidden = false;
    pad.clear();
  }

  function goHome() {
    pad.clear();
    sign.hidden = true;
    home.hidden = false;
  }

  $('btn-clear').addEventListener('click', () => pad.clear());
  $('btn-back').addEventListener('click', goHome);

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
      ctx.strokeStyle = ctx.fillStyle = getComputedStyle(canvas).color;
      base = Math.min(9, Math.max(3, w / 180));
      active = null;
      setInked(false);
    }

    const lineWidth = (e) => (e.pointerType === 'pen' && e.pressure > 0 ? base * (0.5 + e.pressure) : base);

    canvas.addEventListener('pointerdown', (e) => {
      // Only the first finger draws; a resting palm or second finger is ignored.
      if (active !== null || (e.pointerType === 'mouse' && e.button !== 0)) return;
      e.preventDefault();
      active = e.pointerId;
      canvas.setPointerCapture(e.pointerId);
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
  function resetAll() {
    goHome();
    hideKeyboard();
    keyboard.reset();
    setQuery('');
  }
  function poke() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(resetAll, IDLE_MS);
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
  async function loadLogos() {
    if (window.kiosk) {
      const res = await window.kiosk.listLogos();
      dir = res.dir;
      logos = res.items;
    } else {
      dir = '(เปิดผ่านโปรแกรม ClinicSign เท่านั้น)';
    }
    buildTiles();
  }

  if (window.kiosk) window.kiosk.onLogosChanged(loadLogos);
  loadLogos();
})();
