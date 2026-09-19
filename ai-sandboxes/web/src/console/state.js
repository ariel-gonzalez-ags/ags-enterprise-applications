/* console/state.js: the shared state + DOM handles + pure helpers every
 * console module uses. Split from the old console.js IIFE (rule 1). This is the
 * single source of truth for cross-module state: feature modules import S and
 * mutate S.selected / S.tasks / etc. (a module-level object, so all importers
 * share the same references). Server remains the source of truth for data; this
 * only mirrors it for rendering.
 *
 * Loading: native ES modules (no bundler). app.astro injects
 * /js/console/index.js as type="module"; the browser resolves these imports.
 */
'use strict';

// Shared mutable state. One object so every module sees the same live values.
export const S = {
tasks: [],
selected: null,      // full detail of the selected task
selectedId: null,
pollTimer: null,
eventSrc: null,      // EventSource for the selected task, when supported
busy: false,
typedKeys: {},       // message keys already fully typed
typeTimer: null,
palIdx: 0,
emberBal: null,      // from GET /api/embers
stopPending: null,   // task_id with an abort POST in flight (instant feedback)
availModels: [],     // from GET /api/models
pickedModel: localStorage.getItem('ags-model') || 'gemini-3.6-flash',
confirmCb: null,
};

// Not on /app? The entry guards on this before wiring anything.
export const list = document.querySelector('[data-console="task-list"]');

export const dom = {
thread: document.querySelector('[data-console="thread"]'),
input: document.querySelector('[data-console="input"]'),
sendBtn: document.querySelector('[data-console="send"]'),
newBtn: document.querySelector('[data-console="new-task"]'),
approveBtn: document.querySelector('[data-console="approve"]'),
eyebrow: document.querySelector('[data-console="run-eyebrow"]'),
title: document.querySelector('[data-console="run-title"]'),
provGrid: document.querySelector('[data-console="providers"]'),
tglIdem: document.querySelector('[data-console="idempotent"]'),
tglDestroy: document.querySelector('[data-console="destroy-after"]'),
budget: document.querySelector('[data-console="max-hours"]'),
artList: document.querySelector('[data-console="artifacts"]'),
confirmEl: document.querySelector('[data-console="confirm"]'),
confirmTitle: document.querySelector('[data-console="confirm-title"]'),
confirmBody: document.querySelector('[data-console="confirm-body"]'),
confirmGo: document.querySelector('[data-console="confirm-go"]'),
confirmCancel: document.querySelector('[data-console="confirm-cancel"]'),
provMenu: document.querySelector('[data-console="prov-menu"]'),
modelBtn: document.querySelector('[data-console="model-btn"]'),
modelCurrent: document.querySelector('[data-console="model-current"]'),
modelList: document.querySelector('[data-console="models"]'),
createBtn: document.querySelector('[data-console="create-task"]'),
stageSection: document.querySelector('[data-console="sandbox-section"]'),
stageList: document.querySelector('[data-console="stages"]'),
stopBtn: document.querySelector('[data-console="stop-run"]'),
emberSpent: document.querySelector('[data-console="ember-spent"]'),
emberDetail: document.querySelector('[data-console="ember-detail"]'),
emberBalance: document.querySelector('[data-console="ember-balance"]'),
viewer: document.querySelector('[data-console="artifact-viewer"]'),
viewerName: document.querySelector('[data-console="artifact-name"]'),
viewerBody: document.querySelector('[data-console="artifact-body"]'),
viewerDl: document.querySelector('[data-console="artifact-download"]'),
runfeed: document.querySelector('[data-console="runfeed"]'),
runfeedBody: document.querySelector('[data-console="runfeed-body"]'),
runfeedStatus: document.querySelector('[data-console="runfeed-status"]'),
paletteEl: document.querySelector('[data-console="palette"]'),
paletteInput: document.querySelector('[data-console="palette-input"]'),
paletteList: document.querySelector('[data-console="palette-list"]'),
navSearch: document.querySelector('[data-console="nav-search"]'),
};
// Derived: model dropdown lives inside the provider menu.
dom.modelDd = dom.provMenu ? dom.provMenu.querySelector('.model-dd') : null;

export const CHECK_SVG = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';
export const PLUS_SVG = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';

// Display sugar for the canonical catalog (mirrors content/console.js
// outputFormats). Unknown ids fall through: label = the raw id, kind =
// "custom". The catalog is a preference, not a constraint, so any id the
// planner or user supplies still renders.
export const FORMAT_LABELS = {
terraform: 'Terraform', ansible: 'Ansible', arm: 'ARM / Bicep',
helm: 'Helm chart', kubernetes: 'Kubernetes manifests', dockerfile: 'Dockerfile',
bash: 'Bash', powershell: 'PowerShell', python: 'Python',
json: 'JSON policy', yaml: 'YAML config', markdown: 'Markdown runbook',
};
export const FORMAT_KINDS = {
terraform: 'iac', ansible: 'iac', arm: 'iac', helm: 'iac', kubernetes: 'iac', dockerfile: 'iac',
bash: 'script', powershell: 'script', python: 'script',
json: 'config', yaml: 'config', markdown: 'doc',
};
export const formatLabel = function (id) { return FORMAT_LABELS[id] || id; };
export const formatKind = function (id) { return FORMAT_KINDS[id] || 'custom'; };

/* ---------- helpers ---------- */

export function api(path, opts) {
  opts = opts || {};
  opts.credentials = 'same-origin';
  if (opts.body) {
    opts.headers = { 'Content-Type': 'application/json' };
  }
  return fetch(path, opts).then(function (r) {
    if (!r.ok) {
      // Surface the server's reason (FastAPI puts it in `detail`) so a refused
      // action tells the user WHY (e.g. the rate-limit 429), not a bare status.
      return r.json().catch(function () { return {}; }).then(function (d) {
        var err = new Error((d && d.detail) ? d.detail : ('HTTP ' + r.status));
        err.status = r.status;
        throw err;
      });
    }
    // 204 No Content (e.g. DELETE) has no body; parsing it as JSON throws
    // "Unexpected end of JSON input". Return undefined for empty responses.
    if (r.status === 204) return undefined;
    return r.json();
  });
}

export function esc(s) {
  return String(s).replace(/[&<>"']/g, function (ch) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
  });
}

export function ago(ts) {
  var s = Math.max(1, Math.floor(Date.now() / 1000) - ts);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

export function provImg(id, cls) {
  var c = cls + (id === 'aws' ? ' ' + cls + '-aws' : '');
  if (id === 'aws') {
    return '<picture><source srcset="/assets/providers/aws-light.svg" media="(prefers-color-scheme: light)">' +
      '<img src="/assets/providers/aws.svg" alt="AWS" class="' + c + '"></picture>';
  }
  return '<img src="/assets/providers/' + esc(id) + '.svg" alt="' + esc(id) + '" class="' + c + '">';
}

export const TRASH_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';

// Null-safe listener registration: a module may be imported when its target is
// absent (defensive; the console only loads on /app, but a missing ref must not
// throw at import time and break every sibling module).
export function on(el, ev, fn) { if (el) el.addEventListener(ev, fn); }
