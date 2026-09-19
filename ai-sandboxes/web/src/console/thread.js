/* console/thread.js: brainstorm thread + plan cards + the typewriter effect.
 * Split from the old console.js IIFE (rule 1), logic unchanged. Shared state
 * via S, DOM handles via dom.*, helpers from state.js; dataflow functions are
 * imported (cycle-safe: only called at runtime, never during evaluation).
 */
import { S, dom, CHECK_SVG, PLUS_SVG, formatLabel, formatKind, esc, api, on} from './state.js';
import { renderAll, refreshList, select } from './dataflow.js';
import { renderMarkdown } from './markdown.js';

/* ---------- thread ---------- */

function optRow(id, why, on, editable) {
  var tag = editable ? 'button' : 'div';
  // The rationale stays collapsed behind an info toggle so the card reads as
  // compact chips; expanding shows the "why" under the name.
  var info = why
    ? '<span class="opt-info" data-info role="button" tabindex="-1" title="Why this deliverable">' +
      '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/></svg></span>'
    : '';
  return '<' + tag + ' class="opt' + (on ? ' on' : '') + '" data-format="' + esc(id) + '"' +
    (editable ? ' type="button"' : '') + '>' +
    '<span class="opt-check">' + (on ? CHECK_SVG : '') + '</span>' +
    '<span class="opt-name">' + esc(formatLabel(id)) + ' <span class="opt-kind mono">' + esc(formatKind(id)) + '</span></span>' +
    info +
    '<span class="opt-why" hidden>' + esc(why || '') + '</span></' + tag + '>';
}

function planCard(plan, editable) {
  var accepted = S.selected.formats;  // planCard is only called with a selected task
  var opts = (plan.deliverables || []).map(function (d) {
    return optRow(d.id, d.why, accepted.indexOf(d.id) !== -1, editable);
  }).join('');
  // Custom formats the user added that the planner didn't propose
  var custom = accepted.filter(function (f) {
    return !(plan.deliverables || []).some(function (d) { return d.id === f; });
  });
  opts += custom.map(function (f) { return optRow(f, 'added by you', true, editable); }).join('');
  var addRow = editable
    ? '<form class="opt-add"><input type="text" maxlength="32" placeholder="Add your own (e.g. helm, dockerfile)…">' +
      '<button type="submit" title="Add">' + PLUS_SVG + '</button></form>' : '';
  return '<div class="choice-card' + (editable ? ' editable' : '') + '">' +
    '<p class="choice-q">' + esc(plan.summary || 'Execution plan') + '</p>' +
    '<div class="choice-opts">' + opts + '</div>' + addRow +
    '<div class="choice-foot"><span class="choice-hint">' +
    (editable ? 'Toggle what you want delivered; the run follows your selection.' :
                'Delivered set, locked at approval.') + '</span>' +
    '<span class="choice-meta">~' + esc(plan.est_hours || '?') + 'h sandbox</span></div>' +
    '</div>';
}

export function renderThread() {
  if (!S.selected) {
    dom.thread.innerHTML = '<p class="empty mono">Select a task from the library to see the conversation.</p>';
    return;
  }
  if (!S.selected.messages.length) {
    dom.thread.innerHTML = '<p class="empty mono">No messages yet. Describe the task below to start the brainstorm.</p>';
    return;
  }
  var editable = S.selected && (S.selected.state === 'drafting' || S.selected.state === 'planned');
  // latest agent message carrying a plan: that's the live card
  var latestPlanIdx = -1;
  S.selected.messages.forEach(function (m, i) {
    if (m.role === 'agent' && m.plan) latestPlanIdx = i;
  });
  dom.thread.innerHTML = S.selected.messages.map(function (m, i) {
    var tag = m.role === 'agent'
      ? '<div class="msg-agent-tag mono">planner agent</div>' : '';
    var card = (m.role === 'agent' && m.plan)
      ? planCard(m.plan, editable && i === latestPlanIdx) : '';
    // The newest agent reply types out (see below): render its body empty
    // and let the typewriter fill it, so SSE re-renders do not fight it.
    var isNewestAgent = m.role === 'agent' && i === S.selected.messages.length - 1;
    // Agent replies are markdown: render to safe HTML (escaped first, then a
    // small markdown transform). User text stays escaped plain text.
    var body = (isNewestAgent && shouldType(m))
      ? ''
      : (m.role === 'agent' ? renderMarkdown(m.text) : esc(m.text));
    return '<div class="msg ' + m.role + '"' + (isNewestAgent ? ' data-console="latest-agent"' : '') + '>' + tag +
      '<div class="msg-body">' + body + '</div>' + card + '</div>';
  }).join('');
  // Planner is composing a reply that hasn't landed yet: show the bubble.
  if (S.selected.agent_pending) {
    appendPending('planning…');
  }
  dom.thread.scrollTop = dom.thread.scrollHeight;
  startTyping();
}

/* ---------- typing effect (model-agnostic) ----------
 * The planner returns a complete reply (reasoning models buffer, so true
 * token streaming is not available). We reveal the newest agent reply
 * progressively instead. The effect lives entirely client-side, so it
 * behaves identically no matter which model produced the text. The plan
 * card pops in once the text finishes (it is structured JSON, it cannot
 * render half-formed). */

function msgKey(m) { return m.at + '|' + m.text.length; }
function shouldType(m) { return !S.typedKeys[msgKey(m)] && m.text.length >= 24; }

function startTyping() {
  var el = dom.thread.querySelector('[data-console="latest-agent"] .msg-body');
  if (!el || !S.selected) return;
  var last = S.selected.messages[S.selected.messages.length - 1];
  if (!last || last.role !== 'agent' || !shouldType(last)) return;
  S.typedKeys[msgKey(last)] = true;  // mark before starting so re-renders show full text
  var full = last.text;
  if (S.typeTimer) { clearInterval(S.typeTimer); S.typeTimer = null; }
  var i = 0;
  var step = Math.max(2, Math.round(full.length / 50));  // ~50 frames, ~0.8s
  // Type as plain text (typing raw markdown mid-stream would show half-formed
  // `**`/```), then swap to the rendered markdown the moment it completes.
  el.classList.add('md-typing');
  S.typeTimer = setInterval(function () {
    // If a re-render replaced the node, stop; the next render shows it full.
    if (!el.isConnected) { clearInterval(S.typeTimer); S.typeTimer = null; return; }
    i += step;
    if (i >= full.length) {
      el.classList.remove('md-typing');
      el.innerHTML = renderMarkdown(full);
      clearInterval(S.typeTimer); S.typeTimer = null;
      dom.thread.scrollTop = dom.thread.scrollHeight;
      return;
    }
    el.textContent = full.slice(0, i);
    dom.thread.scrollTop = dom.thread.scrollHeight;
  }, 16);
}

export function appendPending(text) {
  var el = document.createElement('div');
  el.className = 'msg agent pending';
  el.innerHTML = '<div class="msg-agent-tag mono">planner agent</div>' +
    '<div class="msg-body">' + esc(text) + '</div>';
  dom.thread.appendChild(el);
  dom.thread.scrollTop = dom.thread.scrollHeight;
  return el;
}

/* A transient system notice in the thread (e.g. a refused approve). Distinct
 * from planner messages (not persisted, styled as a warning); auto-clears on
 * the next real re-render. This is the feedback for an action the server
 * refused, so it never looks like the app silently did nothing. */
export function showNotice(text) {
  var old = dom.thread.querySelector('.msg.notice');
  if (old) old.remove();
  var el = document.createElement('div');
  el.className = 'msg notice';
  el.innerHTML = '<div class="msg-body">' + esc(text) + '</div>';
  dom.thread.appendChild(el);
  dom.thread.scrollTop = dom.thread.scrollHeight;
}

// Plan card interactions: toggling options / adding a custom deliverable
// PATCHes the task's accepted format set. Optimistic paint, server truth.
on(dom.thread, 'click', function (e) {
  // The info toggle expands/collapses the rationale only; it must not flip
  // the deliverable checkbox or fire the PATCH.
  var info = e.target.closest('.opt-info');
  if (info) {
    var row = info.closest('.opt[data-format]');
    if (row) {
      var why = row.querySelector('.opt-why');
      if (why) why.hidden = !why.hidden;
      info.classList.toggle('open');
    }
    return;
  }
  var opt = e.target.closest('.choice-card.editable .opt[data-format]');
  if (!opt || opt.tagName !== 'BUTTON') return;
  var on = opt.classList.toggle('on');
  opt.querySelector('.opt-check').innerHTML = on ? CHECK_SVG : '';
  var formats = [];
  dom.thread.querySelectorAll('.choice-card.editable .opt.on').forEach(function (el) {
    formats.push(el.getAttribute('data-format'));
  });
  if (!formats.length) {  // never allow an empty deliverable set
    opt.classList.add('on');
    opt.querySelector('.opt-check').innerHTML = CHECK_SVG;
    return;
  }
  patchFormats(formats);
});

on(dom.thread, 'submit', function (e) {
  var form = e.target.closest('.opt-add');
  if (!form) return;
  e.preventDefault();
  var inputEl = form.querySelector('input');
  // slug: lowercase, whitespace runs to dashes, strip the rest ("JSON policy format" -> "json-policy-format")
  var val = inputEl.value.trim().toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-_]/g, '').replace(/-{2,}/g, '-').replace(/^-|-$/g, '');
  if (!val) return;
  var formats = S.selected.formats.slice();
  if (formats.indexOf(val) === -1) formats.push(val);
  inputEl.value = '';
  patchFormats(formats);
});

function patchFormats(formats) {
  api('/api/tasks/' + encodeURIComponent(S.selectedId), {
    method: 'PATCH', body: JSON.stringify({ formats: formats }),
  }).then(function (t) {
    S.selected = t;
    renderAll();
    refreshList();
  }).catch(function () {
    select(S.selectedId);  // server rejected (e.g. locked): resync to truth
  });
}
