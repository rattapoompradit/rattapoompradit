// End-to-end check driven with real input events: `ClinicSign.exe --selftest --logos=test\fixtures`.
// Writes PASS/FAIL lines to selftest.txt and exits non-zero on any failure.
const fs = require('node:fs');
const path = require('node:path');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

module.exports = async function selftest(win, { out, shots }) {
  const wc = win.webContents;
  const lines = [];
  let ok = true;
  const check = (name, pass, detail = '') => {
    ok = ok && !!pass;
    lines.push(`${pass ? 'PASS' : 'FAIL'} ${name}${detail ? ` (${detail})` : ''}`);
  };
  const js = (code) => wc.executeJavaScript(code, true);
  const q = (sel) => JSON.stringify(sel);

  async function waitFor(code, pred = Boolean, ms = 10000) {
    const until = Date.now() + ms;
    let v;
    while (Date.now() < until) {
      v = await js(code);
      if (pred(v)) break;
      await sleep(100);
    }
    return v;
  }
  // Page coordinates are CSS px; input events are window px, which differ by the zoom factor.
  const rect = async (sel) => {
    const z = wc.getZoomFactor();
    const r = await js(`(() => { const r = document.querySelector(${q(sel)}).getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height }; })()`);
    return { x: r.x * z, y: r.y * z, w: r.w * z, h: r.h * z };
  };
  const mouse = (type, x, y) =>
    wc.sendInputEvent({ type, x: Math.round(x), y: Math.round(y), button: 'left', clickCount: 1 });
  async function tap(sel) {
    const r = await rect(sel);
    mouse('mouseDown', r.x, r.y);
    await sleep(40);
    mouse('mouseUp', r.x, r.y);
    await sleep(150);
  }
  async function typeKeys(chars) {
    for (const ch of chars) await tap(`.key[data-ch=${q(ch)}]`);
  }
  async function draw(sel) {
    const r = await rect(sel);
    const x0 = r.x - r.w / 3;
    let x = x0;
    let y = r.y;
    mouse('mouseDown', x, y);
    for (let i = 1; i <= 24; i++) {
      x = x0 + ((r.w * 2) / 3) * (i / 24);
      y = r.y + Math.sin(i / 3) * (r.h / 6);
      mouse('mouseMove', x, y);
      await sleep(10);
    }
    mouse('mouseUp', x, y);
    await sleep(150);
  }
  const visible = (sel) => js(`!document.querySelector(${q(sel)}).hidden`);
  const shownTiles = () => js(`[...document.querySelectorAll('.tile')].filter(t => !t.hidden).length`);
  const inkPixels = () =>
    js(`(() => { const c = document.getElementById('pad');
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; })()`);
  async function shot(name) {
    if (!shots) return;
    fs.mkdirSync(shots, { recursive: true });
    await sleep(400); // let freshly decoded images paint
    fs.writeFileSync(path.join(shots, `${name}.png`), (await wc.capturePage()).toPNG());
  }

  try {
    const total = await waitFor(`document.querySelectorAll('.tile').length`, (n) => n > 0);
    check('logos listed', total > 0, `${total} tiles`);
    check(
      'logo images load',
      await waitFor(`[...document.querySelectorAll('.tile img')].every(i => i.complete && i.naturalWidth > 0)`),
    );
    const bg = await js(`document.body.classList.contains('has-bg') && getComputedStyle(document.body).backgroundImage`);
    check('background image applied (if _background.* exists)', bg && bg.includes('_background'), String(bg).slice(0, 80));
    await shot('1-home');

    await tap('#search');
    check('keyboard opens on search tap', await visible('#keyboard'));
    await typeKeys(['ฟ', 'ั', 'น']);
    check('thai keys type into search', (await js(`search.value`)) === 'ฟัน');
    check('search filters tiles', (await shownTiles()) === 1, `${await shownTiles()} shown`);
    await shot('2-search');
    await tap('#kb-back');
    check('backspace removes one char', (await js(`search.value`)) === 'ฟั');
    await tap('#kb-clear');
    check('clear key resets filter', (await shownTiles()) === total);
    await tap('#kb-lang');
    await typeKeys(['s', 'm', 'i']);
    check('english keys + search', (await shownTiles()) === 1);
    await typeKeys(['z', 'z']);
    check('no-match message', (await shownTiles()) === 0 && (await visible('#empty')));
    await tap('#search-clear');
    check('search ✕ button clears', (await shownTiles()) === total);
    await tap('#kb-hide');
    check('keyboard hides', !(await visible('#keyboard')));

    await tap('.tile:not([hidden])');
    check('tap logo opens sign page', (await visible('#sign')) && !(await visible('#home')));
    check('sign page shows logo', await waitFor(`document.getElementById('sign-img').naturalWidth > 0`));
    await draw('#pad');
    const inked = await inkPixels();
    check('finger stroke draws', inked > 0, `${inked} px`);
    check('hint hides while signing', !(await visible('#pad-hint')));
    await shot('3-sign');

    await tap('#btn-clear');
    check('ลบ clears signature and stays', (await inkPixels()) === 0 && (await visible('#sign')));
    check('hint returns after ลบ', await visible('#pad-hint'));

    await draw('#pad');
    await tap('#btn-back');
    check('กลับ returns to home', (await visible('#home')) && !(await visible('#sign')));
    await tap('.tile:not([hidden])');
    check('next signer starts with empty pad', (await inkPixels()) === 0);
    await tap('#btn-back');
  } catch (e) {
    check('selftest ran to the end', false, e && e.stack);
  }

  fs.writeFileSync(out, lines.join('\n') + '\n');
  return ok ? 0 : 1;
};
