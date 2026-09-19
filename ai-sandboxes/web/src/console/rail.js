/* console/rail.js: task rail rendering + the rail click handler (delete flow
 * with confirm, and select-on-click). Split from the old console.js IIFE
 * (rule 1), logic unchanged.
 */
import { S, list, TRASH_SVG, esc, ago, provImg, api, on} from './state.js';
import { askConfirm } from './confirm.js';
import { select, refreshList, renderAll, closeStream } from './dataflow.js';

/* ---------- rail ---------- */

export function renderRail() {
  if (!S.tasks.length) {
    list.innerHTML = '<p class="empty mono">No tasks yet. Start one.</p>';
    return;
  }
  list.innerHTML = S.tasks.map(function (t) {
    var pct = t.checks.total ? Math.round((t.checks.passed / t.checks.total) * 100) : 0;
    var meta = t.state === 'running' ? 'running · ' + t.checks.passed + '/' + t.checks.total : ago(t.updated);
    var deletable = t.state !== 'running';
    return '<button class="task' + (t.id === S.selectedId ? ' active' : '') + '" data-task-id="' + esc(t.id) + '">' +
      '<div class="t-top"><span class="state s-' + esc(t.state) + '">' + esc(t.state) + '</span>' +
      '<span class="t-right">' +
      (deletable ? '<span class="t-del" data-del="' + esc(t.id) + '" data-state="' + esc(t.state) + '" title="Delete task">' + TRASH_SVG + '</span>' : '') +
      provImg(t.provider, 't-prov') + '</span></div>' +
      '<div class="t-title">' + esc(t.title) + '</div>' +
      '<div class="t-meta mono"><span>' + esc(t.id.slice(0, 8)) + '</span> · <span>' + meta + '</span></div>' +
      '<div class="t-checks"><span class="bar"><span class="fill" style="width:' + pct + '%"></span></span>' +
      '<span class="mono">' + t.checks.passed + '/' + t.checks.total + '</span></div>' +
      '</button>';
  }).join('');
}

on(list, 'click', function (e) {
  var del = e.target.closest('[data-del]');
  if (del) {
    e.stopPropagation();
    var id = del.getAttribute('data-del');
    var doDelete = function () {
      api('/api/tasks/' + encodeURIComponent(id), { method: 'DELETE' }).then(function () {
        if (S.selectedId === id) {
          // Clear the view FIRST so nothing below can leave a stale chat
          // on screen, then tear down the dead task's stream (defensive:
          // a stream error must never block the clear).
          S.selected = null; S.selectedId = null;
          try { closeStream(); } catch (e) { /* stream teardown is best-effort */ }
          renderAll();
        }
        refreshList();
      }).catch(function () { refreshList(); });
    };
    // Terminal runs that produced a record (verified, or an honest infeasible/
    // blocked verdict) carry artifacts/evidence: confirm first. Drafts/planned
    // (incl. untouched "Untitled task") delete immediately.
    var st = del.getAttribute('data-state');
    if (st === 'verified' || st === 'infeasible' || st === 'blocked') {
      askConfirm('This ' + st + ' task and its artifacts and evidence will be permanently removed.', doDelete);
    } else {
      doDelete();
    }
    return;
  }
  var card = e.target.closest('[data-task-id]');
  if (card) select(card.getAttribute('data-task-id'));
});
