const test = require('node:test');
const assert = require('node:assert/strict');
const { parseLogoFile, sortLogos, matches } = require('../web/logic.js');
const { TH } = require('../web/keyboard.js');

test('file name becomes clinic name; number prefix sets order', () => {
  assert.deepEqual(parseLogoFile('012 คลินิกหมอฟัน.png'), { name: 'คลินิกหมอฟัน', order: 12 });
  assert.deepEqual(parseLogoFile('5_Smile Dental.JPG'), { name: 'Smile Dental', order: 5 });
  assert.equal(parseLogoFile('คลินิก A.svg').name, 'คลินิก A');
  assert.equal(parseLogoFile('1234.png').name, '1234');
});

test('non-image and hidden files are skipped', () => {
  for (const f of ['readme.txt', 'Thumbs.db', '.DS_Store', '~$temp.png', 'noext']) {
    assert.equal(parseLogoFile(f), null, f);
  }
});

test('sort: numbered first by number, then Thai alphabetical', () => {
  const names = ['ฮ.png', '2 ข.png', 'ก.png', '10 ค.png']
    .map(parseLogoFile);
  assert.deepEqual(sortLogos(names).map((x) => x.name), ['ข', 'ค', 'ก', 'ฮ']);
});

test('search ignores case, spaces and Thai tone marks', () => {
  assert.ok(matches('Smile Dental', 'smiled'));
  assert.ok(matches('คลินิกบ้านหมอ', 'บานหมอ'));
  assert.ok(matches('คลินิกหมอฟัน', 'หมอฟั'));
  assert.ok(matches('คลินิกผิวหนัง', 'ผิว หนัง'));
  assert.ok(matches('คลินิกผิวหนัง', ''));
  assert.ok(!matches('คลินิกผิวหนัง', 'ฟัน'));
});

test('Thai keyboard: shifted row has the same key count; all 44 consonants reachable', () => {
  for (const [normal, shifted] of TH) {
    assert.equal(Array.from(normal).length, Array.from(shifted).length, normal);
  }
  const all = TH.flat().join('');
  for (let c = 0x0e01; c <= 0x0e2e; c++) {
    assert.ok(all.includes(String.fromCharCode(c)), String.fromCharCode(c));
  }
});
