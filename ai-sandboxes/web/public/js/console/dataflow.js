/* console/dataflow.js: task data flow (list refresh, select, SSE stream with
 * polling fallback, create/send/approve). Split from the old console.js IIFE
 * (rule 1), logic unchanged. Renders are delegated to rail/thread/inspector;
 * the import cycles with those modules are safe because bindings are only
 * invoked at runtime, never during module evaluation.
 */
import { S, dom, api, on} from './state.js';
import { renderRail } from './rail.js';
import { renderThread, appendPending, showNotice } from './thread.js';
import { renderInspector, renderHead, renderRunfeed, loadEmbers } from './inspector.js';

export function renderAll() {
  renderRail();
  renderThread();
  renderInspector();
  renderHead();
  renderRunfeed();
}

/* ---------- data flow ---------- */

export function refreshList() {
  return api('/api/tasks').then(function (d) {
    S.tasks = d.tasks;
    renderRail();
  });
}

export function select(id) {
  S.selectedId = id;
  return api('/api/tasks/' + encodeURIComponent(id)).then(function (t) {
    S.selected = t;
    renderAll();
    // Live updates: stream state changes, fall back to polling if SSE is
    // unavailable. Tasks that are neither running nor awaiting a planner
    // reply have no live phase, so nothing to stream.
    if (t.state === 'running' || t.agent_pending) openStream();
    else closeStream();
  });
}

export function schedulePoll() {
  if (S.pollTimer) { clearTimeout(S.pollTimer); S.pollTimer = null; }
  // Fallback only: when SSE is unavailable or the stream dropped, poll
  // while the planner is replying or the sandbox is running. The stream
  // is the primary channel; this keeps the UI correct without it.
  if (S.selected && (S.selected.state === 'running' || S.selected.agent_pending)) {
    S.pollTimer = setTimeout(function () {
      select(S.selectedId).then(refreshList);
    }, 3000);
  }
}

/* ---------- live updates via SSE (with polling fallback) ---------- */

export function openStream() {
  closeStream();
  if (!S.selectedId || typeof EventSource === 'undefined') { schedulePoll(); return; }
  var es = new EventSource('/api/tasks/' + encodeURIComponent(S.selectedId) + '/events');
  S.eventSrc = es;
  es.onmessage = function () {
    if (!S.selectedId) return;
    // A nudge means state changed; re-fetch the task. Once the task is no
    // longer running or awaiting a planner reply, the live phase is over:
    // close the stream rather than hold a connection per task forever.
    api('/api/tasks/' + encodeURIComponent(S.selectedId)).then(function (t) {
      if (t.id !== S.selectedId) return;
      S.selected = t;
      renderAll();
      refreshList();
      if (!(t.state === 'running' || t.agent_pending)) { closeStream(); loadEmbers(); }
    }).catch(function () {});
  };
  es.onerror = function () {
    // Stream failed or was rejected: fall back to polling.
    closeStream();
    schedulePoll();
  };
}

export function closeStream() {
  if (S.eventSrc) { S.eventSrc.close(); S.eventSrc = null; }
  if (S.pollTimer) { clearTimeout(S.pollTimer); S.pollTimer = null; }
}

export function createTask(prov, model) {
  if (S.busy) return;
  S.busy = true;
  api('/api/tasks', { method: 'POST', body: JSON.stringify({ title: '', provider: prov, model: model }) })
    .then(function (t) { return refreshList().then(function () { return select(t.id); }); })
    .catch(function () { /* surfaced by gate if auth broke */ })
    .then(function () { S.busy = false; dom.input.focus(); });
}

export function send() {
  var text = dom.input.value.trim();
  if (!text || S.busy || !S.selectedId) return;
  S.busy = true;
  dom.sendBtn.disabled = true;
  dom.input.value = '';
  var pending = appendPending('planning…');
  // 202: the message is queued and the planner replies in the background.
  // The pending bubble stays until polling picks up the real agent reply.
  api('/api/tasks/' + encodeURIComponent(S.selectedId) + '/messages', {
    method: 'POST', body: JSON.stringify({ text: text }),
  }).then(function () {
    select(S.selectedId).then(refreshList);  // shows user msg + agent_pending; polling takes over
  }).catch(function (err) {
    pending.querySelector('.msg-body').textContent =
      err.status === 503 ? 'Planner is not configured yet (missing API key).' :
      err.status === 409 ? 'This task has moved on; chat is closed.' :
      'Something went wrong. Try again.';
  }).then(function () {
    S.busy = false;
    dom.sendBtn.disabled = false;
    dom.input.focus();
  });
}

on(dom.sendBtn, 'click', send);
on(dom.input, 'keydown', function (e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});

on(dom.approveBtn, 'click', function () {
  if (S.busy || !S.selectedId) return;
  S.busy = true;
  api('/api/tasks/' + encodeURIComponent(S.selectedId) + '/approve', { method: 'POST' })
    .then(function () { return select(S.selectedId).then(refreshList); })
    .catch(function (e) {
      // Refused (402 insufficient Embers, 429 rate limit, 409 state): tell the
      // user WHY in the thread instead of silently doing nothing.
      showNotice((e && e.message) ? e.message : 'Could not start the run.');
    })
    .then(function () { S.busy = false; });
});
