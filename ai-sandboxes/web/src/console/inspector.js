/* console/inspector.js: run inspector (guarantee toggles, sandbox lifecycle
 * stages, stop-run kill switch, ember cost meter, artifact list + viewer,
 * header, live runfeed). Split from the old console.js IIFE (rule 1), logic
 * unchanged. NOTE: the guarantee toggles (patchTask/shapeable and the
 * tglIdem/tglDestroy/budget listeners) live here because they operate on the
 * inspector's elements; the module map in the task brief did not assign them.
 */
import { S, dom, esc, api, on} from './state.js';
import { askConfirm } from './confirm.js';
import { select, refreshList, renderAll } from './dataflow.js';
import { renderProviders } from './picker.js';

/* ---------- guarantee toggles (idempotent / destroy-after / max-hours) ----------
 * These PATCH the task's run guarantees. Only editable while the task is
 * shapeable (drafting/planned); the server rejects otherwise and we resync. */
function patchTask(fields) {
  if (!S.selectedId) return;
  api('/api/tasks/' + encodeURIComponent(S.selectedId), {
    method: 'PATCH', body: JSON.stringify(fields),
  }).then(function (t) {
    S.selected = t;
    renderAll();
  }).catch(function () {
    select(S.selectedId);  // locked or invalid: resync to server truth
  });
}

var shapeable = function () {
  return S.selected && (S.selected.state === 'drafting' || S.selected.state === 'planned');
};

if (dom.tglIdem) on(dom.tglIdem, 'click', function () {
  if (!shapeable()) return;
  patchTask({ idempotent: !S.selected.idempotent });
});
if (dom.tglDestroy) on(dom.tglDestroy, 'click', function () {
  if (!shapeable()) return;
  patchTask({ destroy_after: !(S.selected.config && S.selected.config.destroyAfter) });
});
if (dom.budget) on(dom.budget, 'click', function () {
  if (!shapeable()) return;
  // cycle a sensible sandbox TTL: 1h -> 2h -> 4h -> 8h -> 1h
  var cur = (S.selected.config && S.selected.config.maxHours) || 4;
  var next = { 1: 2, 2: 4, 4: 8, 8: 1 }[cur] || 4;
  patchTask({ max_hours: next });
});

/* ---------- inspector ---------- */

/* ---------- sandbox lifecycle stages ----------
 * While a real run spins up, the API reports run_stage (provisioning ->
 * agent -> verifying -> teardown). We render it as a step list so the user
 * sees the environment come up instead of a static bar. Hidden when the
 * selected task is not running. */
var STAGES = [
  ['provisioning', 'Provisioning resource group'],
  ['agent', 'Starting the agent'],
  ['deploying', 'Deploying resources'],
  ['verifying', 'Verifying the result'],
  ['teardown', 'Tearing down the sandbox'],
];

export function renderCost() {
  if (!dom.emberSpent) return;
  var cost = S.selected && S.selected.cost;
  if (cost && cost.embers > 0) {
    dom.emberSpent.textContent = cost.embers;
    dom.emberDetail.textContent = cost.sandboxSeconds + 's compute · ' +
      cost.llmTokens.toLocaleString() + ' tokens';
  } else {
    dom.emberSpent.textContent = '—';
    dom.emberDetail.textContent = S.selected && S.selected.state === 'running'
      ? 'metered when the run settles' : 'no run cost yet';
  }
  if (dom.emberBalance) {
    dom.emberBalance.textContent = 'Balance: ' +
      (S.emberBal === null ? '…' : S.emberBal + ' Embers');
  }
}

export function loadEmbers() {
  api('/api/embers').then(function (d) {
    S.emberBal = d.balance;
    renderCost();
  }).catch(function () { /* leave balance placeholder */ });
}

export function renderStages() {
  if (!dom.stageSection || !dom.stageList) return;
  var running = S.selected && S.selected.state === 'running';
  if (!running) {
    dom.stageSection.hidden = true;
    if (S.selected && S.selected.id === S.stopPending) S.stopPending = null;  // kill landed
    return;
  }
  dom.stageSection.hidden = false;
  // The button reflects the SERVER's stopping flag (killswitch.is_aborted),
  // which clears the moment the run settles; a fresh run after re-approve has
  // it false, so a stale "Stopping…" is impossible. stopPending gives instant
  // feedback only until the first server refresh confirms (then it clears).
  var stopping = !!S.selected.stopping || S.selected.id === S.stopPending;
  // Once the server has spoken (fresh detail has the real flag), drop the
  // optimistic local flag so a re-approved run is not held in "Stopping…".
  if (S.selected.id === S.stopPending && S.selected.stopping) S.stopPending = null;
  var cur = S.selected.run_stage || 'provisioning';
  var curIdx = STAGES.findIndex(function (s) { return s[0] === cur; });
  if (curIdx < 0) curIdx = 0;
  dom.stageList.innerHTML = STAGES.map(function (s, i) {
    var cls = i < curIdx ? 'done' : (i === curIdx ? 'active' : '');
    return '<li class="stage ' + cls + '"><span class="dot"></span>' + esc(s[1]) + '</li>';
  }).join('');
  if (dom.stopBtn) {
    dom.stopBtn.disabled = stopping;
    dom.stopBtn.classList.toggle('stopping', stopping);
    dom.stopBtn.innerHTML = stopping
      ? '<span class="stop-spin"></span> Stopping…'
      : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="6" width="12" height="12" rx="2"/></svg> Stop run';
  }
}

/* Kill switch (TODO #7): confirm, then POST abort. stopPending gives instant
 * "Stopping…" feedback; the server's stopping flag (authoritative, clears on
 * settle) takes over on the next refresh, so a re-approve after a kill shows
 * a clean "Stop run" on the fresh run. On a failed abort the button re-arms. */
if (dom.stopBtn) {
  on(dom.stopBtn, 'click', function () {
    if (!S.selected || S.selected.state !== 'running' || S.selected.stopping || S.selected.id === S.stopPending) return;
    var id = S.selected.id;
    askConfirm('Stop this run and tear down its sandbox now? The Embers used up to this point still count.', function () {
      S.stopPending = id;         // instant feedback until the server confirms
      renderStages();
      api('/api/tasks/' + encodeURIComponent(id) + '/abort', { method: 'POST' })
        .then(function () { select(id).then(refreshList); })   // pull the real flag
        .catch(function () {
          if (S.stopPending === id) S.stopPending = null;  // the kill failed: re-arm
          renderStages();
          refreshList();
        });
    }, 'Stop run?', 'Stop run');
  });
}

export function renderInspector() {
  renderProviders();
  renderStages();
  dom.tglIdem.classList.toggle('on', !!(S.selected && S.selected.idempotent));
  dom.tglDestroy.classList.toggle('on', !!(S.selected && S.selected.config && S.selected.config.destroyAfter));
  dom.budget.textContent = S.selected && S.selected.config ? S.selected.config.maxHours + 'h' : '—';
  renderCost();

  var arts = S.selected ? S.selected.artifacts : [];
  if (!arts.length) {
    dom.artList.innerHTML = '<p class="hint">No artifacts yet. They appear when a run verifies.</p>';
  } else {
    dom.artList.innerHTML = '';
    arts.forEach(function (a) {
      var card = document.createElement('button');
      card.className = 'art';
      card.type = 'button';
      // Server-trusted JSON (from the API, never markup). The URL and name
      // live on the element as properties, not data-attributes, so they are
      // never read back out of the DOM as untrusted text.
      card._artUrl = a.url;
      card._artName = a.id;
      var top = document.createElement('div'); top.className = 'art-top';
      var nm = document.createElement('span'); nm.className = 'art-name mono'; nm.textContent = a.id;
      var sz = document.createElement('span'); sz.className = 'art-size mono'; sz.textContent = a.size;
      top.appendChild(nm); top.appendChild(sz);
      var note = document.createElement('div'); note.className = 'art-note'; note.textContent = a.note;
      card.appendChild(top); card.appendChild(note);
      dom.artList.appendChild(card);
    });
  }
}

/* ---------- artifact viewer ---------- */

on(dom.artList, 'click', function (e) {
  var card = e.target.closest('.art');
  if (!card || typeof card._artUrl !== 'string') return;
  // Trusted value from the API response (stored as a JS property above),
  // same-origin artifact path. Never parsed out of the DOM.
  var url = card._artUrl;
  dom.viewerName.textContent = card._artName;
  dom.viewerBody.textContent = 'loading…';
  dom.viewerDl.setAttribute('href', url);
  dom.viewer.hidden = false;
  fetch(url, { credentials: 'same-origin' })
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
    .then(function (text) { dom.viewerBody.textContent = text; })
    .catch(function () { dom.viewerBody.textContent = 'could not load artifact.'; });
});

document.querySelector('[data-console="artifact-close"]').addEventListener('click', function () {
  dom.viewer.hidden = true;
});
on(dom.viewer, 'click', function (e) {
  if (e.target === dom.viewer) dom.viewer.hidden = true;  // click backdrop to close
});

/* ---------- header / actions ---------- */

export function renderHead() {
  if (!S.selected) {
    dom.eyebrow.textContent = 'no task selected';
    dom.title.textContent = 'Pick a task, or start a new one';
    dom.approveBtn.hidden = true;
    return;
  }
  var live = S.selected.state === 'running' ? ' · <span class="live">sandbox running</span>' : '';
  dom.eyebrow.innerHTML = esc(S.selected.id.slice(0, 8)) + ' · ' + esc(S.selected.provider) + live;
  dom.title.textContent = S.selected.title;
  dom.approveBtn.hidden = S.selected.state !== 'planned';
}

/* ---------- live sandbox activity feed ----------
 * While a run is in progress, the task carries run_log (the agent's live
 * transcript, appended per line server-side). We render it as a scrolling
 * feed so the user sees the agent working (provisioning, tool calls,
 * verification) instead of a frozen thread. Hidden when not running. */

export function renderRunfeed() {
  if (!dom.runfeed || !dom.runfeedBody) return;
  var running = S.selected && S.selected.state === 'running';
  if (!running) { dom.runfeed.hidden = true; return; }
  dom.runfeed.hidden = false;
  var stage = (S.selected.run_stage || 'provisioning');
  dom.runfeedStatus.textContent = 'sandbox ' + stage;
  var lines = (S.selected.run_log || '').split('\n').filter(function (l) { return l.trim(); });
  // show the most recent lines; highlight tool calls and stage transitions
  var recent = lines.slice(-30);
  dom.runfeedBody.innerHTML = recent.map(function (l) {
    var cls = l.indexOf('tool:') === 0 || l.indexOf('run_shell') >= 0 || l.indexOf('write_file') >= 0 ? 'rf-tool'
      : (l.indexOf('Provisioning') >= 0 || l.indexOf('Launching') >= 0 || l.indexOf('Verif') >= 0 || l.indexOf('torn down') >= 0) ? 'rf-step' : '';
    return '<div class="' + cls + '">' + esc(l) + '</div>';
  }).join('');
  dom.runfeedBody.scrollTop = dom.runfeedBody.scrollHeight;  // follow the tail
}
