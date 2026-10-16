// Pure helpers shared by the main process (Node) and the page (browser global).
(function (root) {
  const IMAGE_EXT = ['.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif', '.bmp'];

  // "012 คลินิกหมอฟัน.png" -> { name: "คลินิกหมอฟัน", order: 12 }; the number prefix only sets the order.
  function parseLogoFile(file) {
    const dot = file.lastIndexOf('.');
    if (dot <= 0 || /^[.~_]/.test(file)) return null;
    if (!IMAGE_EXT.includes(file.slice(dot).toLowerCase())) return null;
    let name = file.slice(0, dot).normalize('NFC').trim();
    let order = Number.MAX_SAFE_INTEGER;
    const m = /^(\d+)[\s._-]+(.+)$/.exec(name);
    if (m) {
      order = Number(m[1]);
      name = m[2].trim();
    }
    return { name, order };
  }

  const VIDEO_EXT = ['.mp4', '.webm', '.m4v'];

  // "_background.jpg|.mp4" is the menu background and "_screensaver.mp4|.jpg" the screensaver;
  // neither is a clinic. Returns { role, video } or null.
  function specialFile(file) {
    const m = /^_(background|screensaver)(\.[^.]+)$/i.exec(file);
    if (!m) return null;
    const ext = m[2].toLowerCase();
    const video = VIDEO_EXT.includes(ext);
    return video || IMAGE_EXT.includes(ext) ? { role: m[1].toLowerCase(), video } : null;
  }

  function sortLogos(items) {
    return items.sort((a, b) => a.order - b.order || a.name.localeCompare(b.name, 'th'));
  }

  // Ignores case, spaces, punctuation and Thai tone marks so small typos still match.
  const LOOSE = /[\s่-์._\-()]/g;
  function searchKey(s) {
    return String(s).normalize('NFC').toLowerCase().replace(LOOSE, '');
  }

  function matches(name, query) {
    const q = searchKey(query);
    return !q || searchKey(name).includes(q);
  }

  const api = { IMAGE_EXT, VIDEO_EXT, parseLogoFile, specialFile, sortLogos, searchKey, matches };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof window !== 'undefined' ? window : globalThis);
