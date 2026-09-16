/* console.js: wires the /app shells to /api/tasks*. Vanilla IIFE, no
 * framework, same discipline as auth.js. Runs only on /app (the page
 * includes it); it no-ops anywhere else.
 *
 * Owns: task rail rendering, brainstorm thread + plan cards, run inspector
 * reflection, polling while a task is running. Server is the source of
 * truth; this file only renders state and posts user intent.
 */
(function () {
  'use strict';

  var list = document.querySelector('[data-console="task-list"]');
  if (!list) return; // not on /app

  var thread = document.querySelector('[data-console="thread"]');
  var input = document.querySelector('[data-console="input"]');
  var sendBtn = document.querySelector('[data-console="send"]');
  var newBtn = document.querySelector('[data-console="new-task"]');
  var approveBtn = document.querySelector('[data-console="approve"]');
  var eyebrow = document.querySelector('[data-console="run-eyebrow"]');
  var title = document.querySelector('[data-console="run-title"]');
  var provGrid = document.querySelector('[data-console="providers"]');
  var tglIdem = document.querySelector('[data-console="idempotent"]');
  var tglDestroy = document.querySelector('[data-console="destroy-after"]');
  var budget = document.querySelector('[data-console="max-hours"]');
  var artList = document.querySelector('[data-console="artifacts"]');

  var CHECK_SVG = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';
  var PLUS_SVG = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';

  var FORMAT_LABELS = {
    terraform: 'Terraform', ansible: 'Ansible', arm: 'ARM / Bicep',
    bash: 'Bash', powershell: 'PowerShell', markdown: 'Markdown runbook',
  };
  var FORMAT_KINDS = { terraform: 'iac', ansible: 'iac', arm: 'iac', bash: 'script', powershell: 'script', markdown: 'doc' };
  var formatLabel = function (id) { return FORMAT_LABELS[id] || id; };
  var formatKind = function (id) { return FORMAT_KINDS[id] || 'custom'; };

  var tasks = [];
  var selected = null;      // full detail of the selected task
  var selectedId = null;
  var pollTimer = null;
  var eventSrc = null;      // EventSource for the selected task, when supported
  var busy = false;

  /* ---------- helpers ---------- */

  function api(path, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    if (opts.body) {
      opts.headers = { 'Content-Type': 'application/json' };
    }
    return fetch(path, opts).then(function (r) {
      if (!r.ok) {
        var err = new Error('HTTP ' + r.status);
        err.status = r.status;
        throw err;
      }
      // 204 No Content (e.g. DELETE) has no body; parsing it as JSON throws
      // "Unexpected end of JSON input". Return undefined for empty responses.
      if (r.status === 204) return undefined;
      return r.json();
    });
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function ago(ts) {
    var s = Math.max(1, Math.floor(Date.now() / 1000) - ts);
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  function provImg(id, cls) {
    var c = cls + (id === 'aws' ? ' ' + cls + '-aws' : '');
    if (id === 'aws') {
      return '<picture><source srcset="/assets/providers/aws-light.svg" media="(prefers-color-scheme: light)">' +
        '<img src="/assets/providers/aws.svg" alt="AWS" class="' + c + '"></picture>';
    }
    return '<img src="/assets/providers/' + esc(id) + '.svg" alt="' + esc(id) + '" class="' + c + '">';
  }

  var TRASH_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';

  /* ---------- confirm modal (design-system, not window.confirm) ---------- */

  var confirmEl = document.querySelector('[data-console="confirm"]');
  var confirmBody = document.querySelector('[data-console="confirm-body"]');
  var confirmGo = document.querySelector('[data-console="confirm-go"]');
  var confirmCancel = document.querySelector('[data-console="confirm-cancel"]');
  var confirmCb = null;

  function askConfirm(body, onYes) {
    confirmBody.textContent = body;
    confirmCb = onYes;
    confirmEl.hidden = false;
  }
  function closeConfirm() { confirmEl.hidden = true; confirmCb = null; }
  confirmGo.addEventListener('click', function () { var cb = confirmCb; closeConfirm(); if (cb) cb(); });
  confirmCancel.addEventListener('click', closeConfirm);
  confirmEl.addEventListener('click', function (e) { if (e.target === confirmEl) closeConfirm(); });

  /* ---------- rail ---------- */

  function renderRail() {
    if (!tasks.length) {
      list.innerHTML = '<p class="empty mono">No tasks yet. Start one.</p>';
      return;
    }
    list.innerHTML = tasks.map(function (t) {
      var pct = t.checks.total ? Math.round((t.checks.passed / t.checks.total) * 100) : 0;
      var meta = t.state === 'running' ? 'running · ' + t.checks.passed + '/' + t.checks.total : ago(t.updated);
      var deletable = t.state !== 'running';
      return '<button class="task' + (t.id === selectedId ? ' active' : '') + '" data-task-id="' + esc(t.id) + '">' +
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

  list.addEventListener('click', function (e) {
    var del = e.target.closest('[data-del]');
    if (del) {
      e.stopPropagation();
      var id = del.getAttribute('data-del');
      var doDelete = function () {
        api('/api/tasks/' + encodeURIComponent(id), { method: 'DELETE' }).then(function () {
          if (selectedId === id) {
            // Clear the view FIRST so nothing below can leave a stale chat
            // on screen, then tear down the dead task's stream (defensive:
            // a stream error must never block the clear).
            selected = null; selectedId = null;
            try { closeStream(); } catch (e) { /* stream teardown is best-effort */ }
            renderAll();
          }
          refreshList();
        }).catch(function () { refreshList(); });
      };
      // Verified runs delivered artifacts: confirm first. Drafts/planned
      // (incl. untouched "Untitled task") delete immediately.
      if (del.getAttribute('data-state') === 'verified') {
        askConfirm('This verified task and its artifacts and evidence will be permanently removed.', doDelete);
      } else {
        doDelete();
      }
      return;
    }
    var card = e.target.closest('[data-task-id]');
    if (card) select(card.getAttribute('data-task-id'));
  });

  /* ---------- thread ---------- */

  function optRow(id, why, on, editable) {
    var tag = editable ? 'button' : 'div';
    return '<' + tag + ' class="opt' + (on ? ' on' : '') + '" data-format="' + esc(id) + '"' +
      (editable ? ' type="button"' : '') + '>' +
      '<span class="opt-check">' + (on ? CHECK_SVG : '') + '</span>' +
      '<span class="opt-name">' + esc(formatLabel(id)) + ' <span class="opt-kind mono">' + esc(formatKind(id)) + '</span></span>' +
      '<span class="opt-why">' + esc(why || '') + '</span></' + tag + '>';
  }

  function planCard(plan, editable) {
    var accepted = selected.formats;  // planCard is only called with a selected task
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

  function renderThread() {
    if (!selected) {
      thread.innerHTML = '<p class="empty mono">Select a task from the library to see the conversation.</p>';
      return;
    }
    if (!selected.messages.length) {
      thread.innerHTML = '<p class="empty mono">No messages yet. Describe the task below to start the brainstorm.</p>';
      return;
    }
    var editable = selected && (selected.state === 'drafting' || selected.state === 'planned');
    // latest agent message carrying a plan: that's the live card
    var latestPlanIdx = -1;
    selected.messages.forEach(function (m, i) {
      if (m.role === 'agent' && m.plan) latestPlanIdx = i;
    });
    thread.innerHTML = selected.messages.map(function (m, i) {
      var tag = m.role === 'agent'
        ? '<div class="msg-agent-tag mono">planner agent</div>' : '';
      var card = (m.role === 'agent' && m.plan)
        ? planCard(m.plan, editable && i === latestPlanIdx) : '';
      return '<div class="msg ' + m.role + '">' + tag +
        '<div class="msg-body">' + esc(m.text) + '</div>' + card + '</div>';
    }).join('');
    // Planner is composing a reply that hasn't landed yet: show the bubble.
    if (selected.agent_pending) {
      appendPending('planning…');
    }
    thread.scrollTop = thread.scrollHeight;
  }

  function appendPending(text) {
    var el = document.createElement('div');
    el.className = 'msg agent pending';
    el.innerHTML = '<div class="msg-agent-tag mono">planner agent</div>' +
      '<div class="msg-body">' + esc(text) + '</div>';
    thread.appendChild(el);
    thread.scrollTop = thread.scrollHeight;
    return el;
  }

  // Plan card interactions: toggling options / adding a custom deliverable
  // PATCHes the task's accepted format set. Optimistic paint, server truth.
  thread.addEventListener('click', function (e) {
    var opt = e.target.closest('.choice-card.editable .opt[data-format]');
    if (!opt || opt.tagName !== 'BUTTON') return;
    var on = opt.classList.toggle('on');
    opt.querySelector('.opt-check').innerHTML = on ? CHECK_SVG : '';
    var formats = [];
    thread.querySelectorAll('.choice-card.editable .opt.on').forEach(function (el) {
      formats.push(el.getAttribute('data-format'));
    });
    if (!formats.length) {  // never allow an empty deliverable set
      opt.classList.add('on');
      opt.querySelector('.opt-check').innerHTML = CHECK_SVG;
      return;
    }
    patchFormats(formats);
  });

  thread.addEventListener('submit', function (e) {
    var form = e.target.closest('.opt-add');
    if (!form) return;
    e.preventDefault();
    var inputEl = form.querySelector('input');
    // slug: lowercase, whitespace runs to dashes, strip the rest ("JSON policy format" -> "json-policy-format")
    var val = inputEl.value.trim().toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-_]/g, '').replace(/-{2,}/g, '-').replace(/^-|-$/g, '');
    if (!val) return;
    var formats = selected.formats.slice();
    if (formats.indexOf(val) === -1) formats.push(val);
    inputEl.value = '';
    patchFormats(formats);
  });

  function patchFormats(formats) {
    api('/api/tasks/' + encodeURIComponent(selectedId), {
      method: 'PATCH', body: JSON.stringify({ formats: formats }),
    }).then(function (t) {
      selected = t;
      renderAll();
      refreshList();
    }).catch(function () {
      select(selectedId);  // server rejected (e.g. locked): resync to truth
    });
  }

  /* ---------- target-cloud picker (rail) ---------- */

  // Reflect the selected task's provider; clicking while the task is
  // shapeable PATCHes it. The picker also sets the provider for New task.
  function renderProviders() {
    var provs = provGrid.querySelectorAll('.prov');
    var current = selected ? selected.provider : (localStorage.getItem('ags-provider') || 'azure');
    for (var i = 0; i < provs.length; i++) {
      provs[i].classList.toggle('on', provs[i].getAttribute('data-prov') === current);
    }
  }

  provGrid.addEventListener('click', function (e) {
    var btn = e.target.closest('.prov');
    if (!btn) return;
    var prov = btn.getAttribute('data-prov');
    localStorage.setItem('ags-provider', prov);
    if (selected && (selected.state === 'drafting' || selected.state === 'planned')) {
      api('/api/tasks/' + encodeURIComponent(selectedId), {
        method: 'PATCH', body: JSON.stringify({ provider: prov }),
      }).then(function (t) { selected = t; renderAll(); refreshList(); })
        .catch(function () { select(selectedId); });
    } else {
      renderProviders();
    }
  });

  /* ---------- inspector ---------- */

  function renderInspector() {
    renderProviders();
    tglIdem.classList.toggle('on', !!(selected && selected.idempotent));
    tglDestroy.classList.toggle('on', !!(selected && selected.config && selected.config.destroyAfter));
    budget.textContent = selected && selected.config ? selected.config.maxHours + 'h' : '—';

    var arts = selected ? selected.artifacts : [];
    if (!arts.length) {
      artList.innerHTML = '<p class="hint">No artifacts yet. They appear when a run verifies.</p>';
    } else {
      artList.innerHTML = '';
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
        artList.appendChild(card);
      });
    }
  }

  /* ---------- artifact viewer ---------- */

  var viewer = document.querySelector('[data-console="artifact-viewer"]');
  var viewerName = document.querySelector('[data-console="artifact-name"]');
  var viewerBody = document.querySelector('[data-console="artifact-body"]');
  var viewerDl = document.querySelector('[data-console="artifact-download"]');

  artList.addEventListener('click', function (e) {
    var card = e.target.closest('.art');
    if (!card || typeof card._artUrl !== 'string') return;
    // Trusted value from the API response (stored as a JS property above),
    // same-origin artifact path. Never parsed out of the DOM.
    var url = card._artUrl;
    viewerName.textContent = card._artName;
    viewerBody.textContent = 'loading…';
    viewerDl.setAttribute('href', url);
    viewer.hidden = false;
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
      .then(function (text) { viewerBody.textContent = text; })
      .catch(function () { viewerBody.textContent = 'could not load artifact.'; });
  });

  document.querySelector('[data-console="artifact-close"]').addEventListener('click', function () {
    viewer.hidden = true;
  });
  viewer.addEventListener('click', function (e) {
    if (e.target === viewer) viewer.hidden = true;  // click backdrop to close
  });

  /* ---------- header / actions ---------- */

  function renderHead() {
    if (!selected) {
      eyebrow.textContent = 'no task selected';
      title.textContent = 'Pick a task, or start a new one';
      approveBtn.hidden = true;
      return;
    }
    var live = selected.state === 'running' ? ' · <span class="live">sandbox running</span>' : '';
    eyebrow.innerHTML = esc(selected.id.slice(0, 8)) + ' · ' + esc(selected.provider) + live;
    title.textContent = selected.title;
    approveBtn.hidden = selected.state !== 'planned';
  }

  function renderAll() {
    renderRail();
    renderThread();
    renderInspector();
    renderHead();
  }

  /* ---------- data flow ---------- */

  function refreshList() {
    return api('/api/tasks').then(function (d) {
      tasks = d.tasks;
      renderRail();
    });
  }

  function select(id) {
    selectedId = id;
    return api('/api/tasks/' + encodeURIComponent(id)).then(function (t) {
      selected = t;
      renderAll();
      // Live updates: stream state changes, fall back to polling if SSE is
      // unavailable. Tasks that are neither running nor awaiting a planner
      // reply have no live phase, so nothing to stream.
      if (t.state === 'running' || t.agent_pending) openStream();
      else closeStream();
    });
  }

  function schedulePoll() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    // Fallback only: when SSE is unavailable or the stream dropped, poll
    // while the planner is replying or the sandbox is running. The stream
    // is the primary channel; this keeps the UI correct without it.
    if (selected && (selected.state === 'running' || selected.agent_pending)) {
      pollTimer = setTimeout(function () {
        select(selectedId).then(refreshList);
      }, 3000);
    }
  }

  /* ---------- live updates via SSE (with polling fallback) ---------- */

  function openStream() {
    closeStream();
    if (!selectedId || typeof EventSource === 'undefined') { schedulePoll(); return; }
    var es = new EventSource('/api/tasks/' + encodeURIComponent(selectedId) + '/events');
    eventSrc = es;
    es.onmessage = function () {
      if (!selectedId) return;
      // A nudge means state changed; re-fetch the task. Once the task is no
      // longer running or awaiting a planner reply, the live phase is over:
      // close the stream rather than hold a connection per task forever.
      api('/api/tasks/' + encodeURIComponent(selectedId)).then(function (t) {
        if (t.id !== selectedId) return;
        selected = t;
        renderAll();
        refreshList();
        if (!(t.state === 'running' || t.agent_pending)) closeStream();
      }).catch(function () {});
    };
    es.onerror = function () {
      // Stream failed or was rejected: fall back to polling.
      closeStream();
      schedulePoll();
    };
  }

  function closeStream() {
    if (eventSrc) { eventSrc.close(); eventSrc = null; }
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  }

  newBtn.addEventListener('click', function () {
    if (busy) return;
    busy = true;
    var prov = localStorage.getItem('ags-provider') || 'azure';
    api('/api/tasks', { method: 'POST', body: JSON.stringify({ title: '', provider: prov }) })
      .then(function (t) { return refreshList().then(function () { return select(t.id); }); })
      .catch(function () { /* surfaced by gate if auth broke */ })
      .then(function () { busy = false; input.focus(); });
  });

  function send() {
    var text = input.value.trim();
    if (!text || busy || !selectedId) return;
    busy = true;
    sendBtn.disabled = true;
    input.value = '';
    var pending = appendPending('planning…');
    // 202: the message is queued and the planner replies in the background.
    // The pending bubble stays until polling picks up the real agent reply.
    api('/api/tasks/' + encodeURIComponent(selectedId) + '/messages', {
      method: 'POST', body: JSON.stringify({ text: text }),
    }).then(function () {
      select(selectedId).then(refreshList);  // shows user msg + agent_pending; polling takes over
    }).catch(function (err) {
      pending.querySelector('.msg-body').textContent =
        err.status === 503 ? 'Planner is not configured yet (missing API key).' :
        err.status === 409 ? 'This task has moved on; chat is closed.' :
        'Something went wrong. Try again.';
    }).then(function () {
      busy = false;
      sendBtn.disabled = false;
      input.focus();
    });
  }

  sendBtn.addEventListener('click', send);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });

  approveBtn.addEventListener('click', function () {
    if (busy || !selectedId) return;
    busy = true;
    api('/api/tasks/' + encodeURIComponent(selectedId) + '/approve', { method: 'POST' })
      .then(function () { return select(selectedId).then(refreshList); })
      .catch(function () {})
      .then(function () { busy = false; });
  });

  /* ---------- command palette (search your tasks) ---------- */

  var paletteEl = document.querySelector('[data-console="palette"]');
  var paletteInput = document.querySelector('[data-console="palette-input"]');
  var paletteList = document.querySelector('[data-console="palette-list"]');
  var navSearch = document.querySelector('[data-console="nav-search"]');
  var palIdx = 0;

  function paletteMatches() {
    var q = paletteInput.value.trim().toLowerCase();
    if (!q) return tasks.slice();
    return tasks.filter(function (t) {
      return t.title.toLowerCase().indexOf(q) !== -1 ||
        t.id.toLowerCase().indexOf(q) !== -1 ||
        t.state.toLowerCase().indexOf(q) !== -1 ||
        (t.provider || '').toLowerCase().indexOf(q) !== -1;
    });
  }

  function renderPalette() {
    var rows = paletteMatches();
    if (palIdx >= rows.length) palIdx = Math.max(0, rows.length - 1);
    if (!rows.length) {
      paletteList.innerHTML = '<p class="p-empty">No tasks match.</p>';
      return;
    }
    paletteList.innerHTML = rows.map(function (t, i) {
      return '<button class="p-row' + (i === palIdx ? ' on' : '') + '" data-pal="' + esc(t.id) + '" type="button">' +
        '<span class="p-main"><span class="p-title">' + esc(t.title) + '</span>' +
        '<span class="p-sub mono">' + esc(t.id.slice(0, 8)) + ' · ' + esc(t.provider) + '</span></span>' +
        '<span class="p-state">' + esc(t.state) + '</span></button>';
    }).join('');
  }

  function openPalette() {
    paletteEl.hidden = false;
    paletteInput.value = '';
    palIdx = 0;
    renderPalette();
    paletteInput.focus();
  }
  function closePalette() { paletteEl.hidden = true; }
  function paletteOpen() { return !paletteEl.hidden; }
  function paletteGo(id) { closePalette(); select(id); }

  if (navSearch) navSearch.addEventListener('click', openPalette);
  paletteInput.addEventListener('input', function () { palIdx = 0; renderPalette(); });
  paletteList.addEventListener('click', function (e) {
    var row = e.target.closest('[data-pal]');
    if (row) paletteGo(row.getAttribute('data-pal'));
  });
  paletteEl.addEventListener('click', function (e) { if (e.target === paletteEl) closePalette(); });

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
    if (e.key === 'ArrowDown') { e.preventDefault(); palIdx = Math.min(rows.length - 1, palIdx + 1); renderPalette(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); palIdx = Math.max(0, palIdx - 1); renderPalette(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (rows[palIdx]) paletteGo(rows[palIdx].id); }
  });

  /* ---------- boot ---------- */

  refreshList().then(function () {
    if (tasks.length) select(tasks[0].id);
    else renderAll();
  }).catch(function () {
    list.innerHTML = '<p class="empty mono">Sign in to see your tasks.</p>';
  });
})();
