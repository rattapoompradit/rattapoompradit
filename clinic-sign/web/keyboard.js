// On-screen Thai (Kedmanee) / English keyboard. Windows' own touch keyboard is unreliable in kiosk mode.
(function (root) {
  // Each row: [normal, shifted]; every character is one key.
  const TH = [
    ['ๅ/-ภถุึคตจขช', '+๑๒๓๔ู฿๕๖๗๘๙'],
    ['ๆไำพะัีรนยบลฃ', '๐"ฎฑธํ๊ณฯญฐ,ฅ'],
    ['ฟหกดเ้่าสวง', 'ฤฆฏโฌ็๋ษศซ.'],
    ['ผปแอิืทมใฝ', '()ฉฮฺ์?ฒฬฦ'],
  ];
  const EN = ['1234567890', 'qwertyuiop', 'asdfghjkl', 'zxcvbnm-.'];
  const COMBINING = /[ัิ-ฺ็-๎]/;

  function button(label, cls, onPress, id) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'key ' + cls;
    b.textContent = label;
    if (id) b.id = id;
    // pointerdown + preventDefault: instant on touch and keeps focus in the search box.
    b.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      onPress();
    });
    return b;
  }

  function createKeyboard(el, { onChar, onBackspace, onClear, onHide }) {
    let lang = 'th';
    let shift = false;

    function render() {
      const rows = lang === 'th' ? TH.map((r) => r[shift ? 1 : 0]) : EN;
      const frag = document.createDocumentFragment();
      for (const row of rows) {
        const div = document.createElement('div');
        div.className = 'kb-row';
        for (const ch of Array.from(row)) {
          const k = button(COMBINING.test(ch) ? '◌' + ch : ch, 'char', () => {
            onChar(ch);
            if (shift) {
              shift = false;
              render();
            }
          });
          k.dataset.ch = ch;
          div.append(k);
        }
        frag.append(div);
      }
      const ctl = document.createElement('div');
      ctl.className = 'kb-row ctl';
      ctl.append(
        button(lang === 'th' ? 'ABC' : 'ไทย', 'wide', () => {
          lang = lang === 'th' ? 'en' : 'th';
          shift = false;
          render();
        }, 'kb-lang'),
      );
      if (lang === 'th') {
        ctl.append(button('⇧', 'wide' + (shift ? ' on' : ''), () => {
          shift = !shift;
          render();
        }, 'kb-shift'));
      }
      ctl.append(
        button('เว้นวรรค', 'space', () => onChar(' '), 'kb-space'),
        button('⌫', 'wide', onBackspace, 'kb-back'),
        button('ล้าง', 'wide', onClear, 'kb-clear'),
        button('ซ่อน ▾', 'wide', onHide, 'kb-hide'),
      );
      frag.append(ctl);
      el.replaceChildren(frag);
    }

    render();
    return {
      reset() {
        lang = 'th';
        shift = false;
        render();
      },
    };
  }

  const api = { createKeyboard, TH, EN };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof window !== 'undefined' ? window : globalThis);
