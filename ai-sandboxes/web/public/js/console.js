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

  // Display sugar for the canonical catalog (mirrors content/console.js
  // outputFormats). Unknown ids fall through: label = the raw id, kind =
  // "custom". The catalog is a preference, not a constraint, so any id the
  // planner or user supplies still renders.
  var FORMAT_LABELS = {
    terraform: 'Terraform', ansible: 'Ansible', arm: 'ARM / Bicep',
    helm: 'Helm chart', kubernetes: 'Kubernetes manifests', dockerfile: 'Dockerfile',
    bash: 'Bash', powershell: 'PowerShell', python: 'Python',
    json: 'JSON policy', yaml: 'YAML config', markdown: 'Markdown runbook',
  };
  var FORMAT_KINDS = {
    terraform: 'iac', ansible: 'iac', arm: 'iac', helm: 'iac', kubernetes: 'iac', dockerfile: 'iac',
    bash: 'script', powershell: 'script', python: 'script',
    json: 'config', yaml: 'config', markdown: 'doc',
  };
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
      // The newest agent reply types out (see below): render its body empty
      // and let the typewriter fill it, so SSE re-renders do not fight it.
      var isNewestAgent = m.role === 'agent' && i === selected.messages.length - 1;
      var body = (isNewestAgent && shouldType(m)) ? '' : esc(m.text);
      return '<div class="msg ' + m.role + '"' + (isNewestAgent ? ' data-console="latest-agent"' : '') + '>' + tag +
        '<div class="msg-body">' + body + '</div>' + card + '</div>';
    }).join('');
    // Planner is composing a reply that hasn't landed yet: show the bubble.
    if (selected.agent_pending) {
      appendPending('planning…');
    }
    thread.scrollTop = thread.scrollHeight;
    startTyping();
  }

  /* ---------- typing effect (model-agnostic) ----------
   * The planner returns a complete reply (reasoning models buffer, so true
   * token streaming is not available). We reveal the newest agent reply
   * progressively instead. The effect lives entirely client-side, so it
   * behaves identically no matter which model produced the text. The plan
   * card pops in once the text finishes (it is structured JSON, it cannot
   * render half-formed). */

  var typedKeys = {};      // message keys already fully typed
  var typeTimer = null;

  function msgKey(m) { return m.at + '|' + m.text.length; }
  function shouldType(m) { return !typedKeys[msgKey(m)] && m.text.length >= 24; }

  function startTyping() {
    var el = thread.querySelector('[data-console="latest-agent"] .msg-body');
    if (!el || !selected) return;
    var last = selected.messages[selected.messages.length - 1];
    if (!last || last.role !== 'agent' || !shouldType(last)) return;
    typedKeys[msgKey(last)] = true;  // mark before starting so re-renders show full text
    var full = last.text;
    if (typeTimer) { clearInterval(typeTimer); typeTimer = null; }
    var i = 0;
    var step = Math.max(2, Math.round(full.length / 50));  // ~50 frames, ~0.8s
    typeTimer = setInterval(function () {
      // If a re-render replaced the node, stop; the next render shows it full.
      if (!el.isConnected) { clearInterval(typeTimer); typeTimer = null; return; }
      i += step;
      if (i >= full.length) {
        el.textContent = full;
        clearInterval(typeTimer); typeTimer = null;
        return;
      }
      el.textContent = full.slice(0, i);
      thread.scrollTop = thread.scrollHeight;
    }, 16);
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

  /* ---------- guarantee toggles (idempotent / destroy-after / max-hours) ----------
   * These PATCH the task's run guarantees. Only editable while the task is
   * shapeable (drafting/planned); the server rejects otherwise and we resync. */
  function patchTask(fields) {
    if (!selectedId) return;
    api('/api/tasks/' + encodeURIComponent(selectedId), {
      method: 'PATCH', body: JSON.stringify(fields),
    }).then(function (t) {
      selected = t;
      renderAll();
    }).catch(function () {
      select(selectedId);  // locked or invalid: resync to server truth
    });
  }

  var shapeable = function () {
    return selected && (selected.state === 'drafting' || selected.state === 'planned');
  };

  if (tglIdem) tglIdem.addEventListener('click', function () {
    if (!shapeable()) return;
    patchTask({ idempotent: !selected.idempotent });
  });
  if (tglDestroy) tglDestroy.addEventListener('click', function () {
    if (!shapeable()) return;
    patchTask({ destroy_after: !(selected.config && selected.config.destroyAfter) });
  });
  if (budget) budget.addEventListener('click', function () {
    if (!shapeable()) return;
    // cycle a sensible sandbox TTL: 1h -> 2h -> 4h -> 8h -> 1h
    var cur = (selected.config && selected.config.maxHours) || 4;
    var next = { 1: 2, 2: 4, 4: 8, 8: 1 }[cur] || 4;
    patchTask({ max_hours: next });
  });

  /* ---------- target-cloud picker (rail) ---------- */

  /* ---------- provider + model picker (dropdown under "New task") ---------- */

  var provMenu = document.querySelector('[data-console="prov-menu"]');
  var modelDd = provMenu ? provMenu.querySelector('.model-dd') : null;
  var modelBtn = document.querySelector('[data-console="model-btn"]');
  var modelCurrent = document.querySelector('[data-console="model-current"]');
  var modelList = document.querySelector('[data-console="models"]');
  var createBtn = document.querySelector('[data-console="create-task"]');
  var availModels = [];   // from GET /api/models
  var pickedModel = localStorage.getItem('ags-model') || 'gemini-3.6-flash';

  function closeProvMenu() {
    if (provMenu) provMenu.hidden = true;
    newBtn.setAttribute('aria-expanded', 'false');
    closeModelList();
  }
  function toggleProvMenu() {
    var open = provMenu.hidden;
    provMenu.hidden = !open;
    newBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) { renderProviders(); renderModels(); syncModelBtn(); }
  }

  function renderProviders() {
    var provs = provGrid.querySelectorAll('.prov');
    // The picker is always driven by the stored pick, never by the selected
    // task: otherwise a click updates localStorage but the highlight snaps
    // back to the task's provider, looking like the click did nothing.
    var current = localStorage.getItem('ags-provider') || 'azure';
    for (var i = 0; i < provs.length; i++) {
      provs[i].classList.toggle('on', provs[i].getAttribute('data-prov') === current);
    }
  }

  function modelName(id) {
    var m = availModels.find(function (x) { return x.id === id; });
    return m ? m.name : id;
  }
  function syncModelBtn() { modelCurrent.textContent = modelName(pickedModel); }

  function closeModelList() {
    modelList.hidden = true;
    modelDd.classList.remove('open');
    modelBtn.setAttribute('aria-expanded', 'false');
  }
  function toggleModelList() {
    var open = modelList.hidden;
    modelList.hidden = !open;
    modelDd.classList.toggle('open', open);
    modelBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) renderModels();
  }

  function renderModels() {
    if (!availModels.length) { modelList.innerHTML = '<p class="p-empty">loading…</p>'; return; }
    modelList.innerHTML = availModels.map(function (m) {
      return '<button class="model-opt' + (m.id === pickedModel ? ' on' : '') + '" data-model="' + esc(m.id) + '" type="button">' +
        '<span class="model-name">' + esc(m.name) + '</span>' +
        '<span class="model-blurb">' + esc(m.blurb) + '</span></button>';
    }).join('');
  }

  // Load the selectable planner models once.
  api('/api/models').then(function (d) {
    availModels = d.models || [];
    if (!availModels.some(function (m) { return m.id === pickedModel; })) {
      pickedModel = availModels.length ? availModels[0].id : pickedModel;
    }
    syncModelBtn();
  }).catch(function () {});

  provGrid.addEventListener('click', function (e) {
    var btn = e.target.closest('.prov');
    if (!btn) return;
    localStorage.setItem('ags-provider', btn.getAttribute('data-prov'));
    renderProviders();
  });

  modelBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    toggleModelList();
  });

  modelList.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-model]');
    if (!btn) return;
    pickedModel = btn.getAttribute('data-model');
    localStorage.setItem('ags-model', pickedModel);
    syncModelBtn();
    closeModelList();  // collapse back to the single-line selector
  });

  createBtn.addEventListener('click', function () {
    var prov = localStorage.getItem('ags-provider') || 'azure';
    closeProvMenu();
    createTask(prov, pickedModel);
  });

  newBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    toggleProvMenu();
  });

  // Clicking anywhere outside the picker closes it.
  document.addEventListener('click', function (e) {
    if (!provMenu || provMenu.hidden) return;
    // Use the menu itself, not e.target.closest(): clicking a model option
    // re-renders the list (innerHTML), which detaches the clicked node, so
    // closest() on that stale element would wrongly report "outside".
    if (provMenu.contains(e.target) || e.target.closest('.new-btn')) return;
    closeProvMenu();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (modelList && !modelList.hidden) { closeModelList(); return; }
    if (provMenu && !provMenu.hidden) closeProvMenu();
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
  var stageSection = document.querySelector('[data-console="sandbox-section"]');
  var stageList = document.querySelector('[data-console="stages"]');

  var emberSpent = document.querySelector('[data-console="ember-spent"]');
  var emberDetail = document.querySelector('[data-console="ember-detail"]');
  var emberBalance = document.querySelector('[data-console="ember-balance"]');
  var emberBal = null;  // from GET /api/embers

  function renderCost() {
    if (!emberSpent) return;
    var cost = selected && selected.cost;
    if (cost && cost.embers > 0) {
      emberSpent.textContent = cost.embers;
      emberDetail.textContent = cost.sandboxSeconds + 's compute · ' +
        cost.llmTokens.toLocaleString() + ' tokens';
    } else {
      emberSpent.textContent = '—';
      emberDetail.textContent = selected && selected.state === 'running'
        ? 'metered when the run settles' : 'no run cost yet';
    }
    if (emberBalance) {
      emberBalance.textContent = 'Balance: ' +
        (emberBal === null ? '…' : emberBal + ' Embers');
    }
  }

  function loadEmbers() {
    api('/api/embers').then(function (d) {
      emberBal = d.balance;
      renderCost();
    }).catch(function () { /* leave balance placeholder */ });
  }

  function renderStages() {
    if (!stageSection || !stageList) return;
    var running = selected && selected.state === 'running';
    if (!running) { stageSection.hidden = true; return; }
    stageSection.hidden = false;
    var cur = selected.run_stage || 'provisioning';
    var curIdx = STAGES.findIndex(function (s) { return s[0] === cur; });
    if (curIdx < 0) curIdx = 0;
    stageList.innerHTML = STAGES.map(function (s, i) {
      var cls = i < curIdx ? 'done' : (i === curIdx ? 'active' : '');
      return '<li class="stage ' + cls + '"><span class="dot"></span>' + esc(s[1]) + '</li>';
    }).join('');
  }

  function renderInspector() {
    renderProviders();
    renderStages();
    tglIdem.classList.toggle('on', !!(selected && selected.idempotent));
    tglDestroy.classList.toggle('on', !!(selected && selected.config && selected.config.destroyAfter));
    budget.textContent = selected && selected.config ? selected.config.maxHours + 'h' : '—';
    renderCost();

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

  /* ---------- live sandbox activity feed ----------
   * While a run is in progress, the task carries run_log (the agent's live
   * transcript, appended per line server-side). We render it as a scrolling
   * feed so the user sees the agent working (provisioning, tool calls,
   * verification) instead of a frozen thread. Hidden when not running. */
  var runfeed = document.querySelector('[data-console="runfeed"]');
  var runfeedBody = document.querySelector('[data-console="runfeed-body"]');
  var runfeedStatus = document.querySelector('[data-console="runfeed-status"]');

  function renderRunfeed() {
    if (!runfeed || !runfeedBody) return;
    var running = selected && selected.state === 'running';
    if (!running) { runfeed.hidden = true; return; }
    runfeed.hidden = false;
    var stage = (selected.run_stage || 'provisioning');
    runfeedStatus.textContent = 'sandbox ' + stage;
    var lines = (selected.run_log || '').split('\n').filter(function (l) { return l.trim(); });
    // show the most recent lines; highlight tool calls and stage transitions
    var recent = lines.slice(-30);
    runfeedBody.innerHTML = recent.map(function (l) {
      var cls = l.indexOf('tool:') === 0 || l.indexOf('run_shell') >= 0 || l.indexOf('write_file') >= 0 ? 'rf-tool'
        : (l.indexOf('Provisioning') >= 0 || l.indexOf('Launching') >= 0 || l.indexOf('Verif') >= 0 || l.indexOf('torn down') >= 0) ? 'rf-step' : '';
      return '<div class="' + cls + '">' + esc(l) + '</div>';
    }).join('');
    runfeedBody.scrollTop = runfeedBody.scrollHeight;  // follow the tail
  }

  function renderAll() {
    renderRail();
    renderThread();
    renderInspector();
    renderHead();
    renderRunfeed();
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
        if (!(t.state === 'running' || t.agent_pending)) { closeStream(); loadEmbers(); }
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

  function createTask(prov, model) {
    if (busy) return;
    busy = true;
    api('/api/tasks', { method: 'POST', body: JSON.stringify({ title: '', provider: prov, model: model }) })
      .then(function (t) { return refreshList().then(function () { return select(t.id); }); })
      .catch(function () { /* surfaced by gate if auth broke */ })
      .then(function () { busy = false; input.focus(); });
  }

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

  loadEmbers();
  refreshList().then(function () {
    if (tasks.length) select(tasks[0].id);
    else renderAll();
  }).catch(function () {
    list.innerHTML = '<p class="empty mono">Sign in to see your tasks.</p>';
  });
})();
