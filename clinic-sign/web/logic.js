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

  // "_background.jpg" in the logo folder is the page background, not a clinic.
  function isBackgroundFile(file) {
    const dot = file.lastIndexOf('.');
    return /^_background\./i.test(file) && IMAGE_EXT.includes(file.slice(dot).toLowerCase());
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

  const api = { IMAGE_EXT, parseLogoFile, isBackgroundFile, sortLogos, searchKey, matches };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof window !== 'undefined' ? window : globalThis);
