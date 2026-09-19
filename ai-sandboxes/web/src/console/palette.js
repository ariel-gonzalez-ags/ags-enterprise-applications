/* console/palette.js: command palette (search your tasks). Split from the
 * old console.js IIFE (rule 1), logic unchanged. Self-contained: registers
 * its own listeners; only select is imported from dataflow.
 */
import { S, dom, esc, on} from './state.js';
import { select } from './dataflow.js';

/* ---------- command palette (search your tasks) ---------- */

function paletteMatches() {
  var q = dom.paletteInput.value.trim().toLowerCase();
  if (!q) return S.tasks.slice();
  return S.tasks.filter(function (t) {
    return t.title.toLowerCase().indexOf(q) !== -1 ||
      t.id.toLowerCase().indexOf(q) !== -1 ||
      t.state.toLowerCase().indexOf(q) !== -1 ||
      (t.provider || '').toLowerCase().indexOf(q) !== -1;
  });
}

function renderPalette() {
  var rows = paletteMatches();
  if (S.palIdx >= rows.length) S.palIdx = Math.max(0, rows.length - 1);
  if (!rows.length) {
    dom.paletteList.innerHTML = '<p class="p-empty">No tasks match.</p>';
    return;
  }
  dom.paletteList.innerHTML = rows.map(function (t, i) {
    return '<button class="p-row' + (i === S.palIdx ? ' on' : '') + '" data-pal="' + esc(t.id) + '" type="button">' +
      '<span class="p-main"><span class="p-title">' + esc(t.title) + '</span>' +
      '<span class="p-sub mono">' + esc(t.id.slice(0, 8)) + ' · ' + esc(t.provider) + '</span></span>' +
      '<span class="p-state">' + esc(t.state) + '</span></button>';
  }).join('');
}

function openPalette() {
  dom.paletteEl.hidden = false;
  dom.paletteInput.value = '';
  S.palIdx = 0;
  renderPalette();
  dom.paletteInput.focus();
}
function closePalette() { dom.paletteEl.hidden = true; }
function paletteOpen() { return !dom.paletteEl.hidden; }
function paletteGo(id) { closePalette(); select(id); }

if (dom.navSearch) on(dom.navSearch, 'click', openPalette);
on(dom.paletteInput, 'input', function () { S.palIdx = 0; renderPalette(); });
on(dom.paletteList, 'click', function (e) {
  var row = e.target.closest('[data-pal]');
  if (row) paletteGo(row.getAttribute('data-pal'));
});
on(dom.paletteEl, 'click', function (e) { if (e.target === dom.paletteEl) closePalette(); });

document.addEventListener('keydown', function (e) {
  var mod = e.metaKey || e.ctrlKey;
  if (mod && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault();
    if (paletteOpen()) closePalette(); else openPalette();
    return;
  }
  if (!paletteOpen()) return;
  if (e.key === 'Escape') { closePalette(); return; }
  var rows = paletteMatches();
  if (e.key === 'ArrowDown') { e.preventDefault(); S.palIdx = Math.min(rows.length - 1, S.palIdx + 1); renderPalette(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); S.palIdx = Math.max(0, S.palIdx - 1); renderPalette(); }
  else if (e.key === 'Enter') { e.preventDefault(); if (rows[S.palIdx]) paletteGo(rows[S.palIdx].id); }
});
