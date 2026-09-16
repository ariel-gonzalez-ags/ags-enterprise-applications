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

  var tasks = [];
  var selected = null;      // full detail of the selected task
  var selectedId = null;
  var pollTimer = null;
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
    if (id === 'aws') {
      return '<picture><source srcset="/assets/providers/aws-light.svg" media="(prefers-color-scheme: light)">' +
        '<img src="/assets/providers/aws.svg" alt="AWS" class="' + cls + '"></picture>';
    }
    return '<img src="/assets/providers/' + esc(id) + '.svg" alt="' + esc(id) + '" class="' + cls + '">';
  }

  /* ---------- rail ---------- */

  function renderRail() {
    if (!tasks.length) {
      list.innerHTML = '<p class="empty mono">No tasks yet. Start one.</p>';
      return;
    }
    list.innerHTML = tasks.map(function (t) {
      var pct = t.checks.total ? Math.round((t.checks.passed / t.checks.total) * 100) : 0;
      var meta = t.state === 'running' ? 'running · ' + t.checks.passed + '/' + t.checks.total : ago(t.updated);
      return '<button class="task' + (t.id === selectedId ? ' active' : '') + '" data-task-id="' + esc(t.id) + '">' +
        '<div class="t-top"><span class="state s-' + esc(t.state) + '">' + esc(t.state) + '</span>' +
        provImg(t.provider, 't-prov') + '</div>' +
        '<div class="t-title">' + esc(t.title) + '</div>' +
        '<div class="t-meta mono"><span>' + esc(t.id.slice(0, 8)) + '</span> · <span>' + meta + '</span></div>' +
        '<div class="t-checks"><span class="bar"><span class="fill" style="width:' + pct + '%"></span></span>' +
        '<span class="mono">' + t.checks.passed + '/' + t.checks.total + '</span></div>' +
        '</button>';
    }).join('');
  }

  list.addEventListener('click', function (e) {
    var card = e.target.closest('[data-task-id]');
    if (card) select(card.getAttribute('data-task-id'));
  });

  /* ---------- thread ---------- */

  function planCard(plan) {
    var opts = (plan.deliverables || []).map(function (d) {
      return '<div class="opt"><span class="opt-check">' + CHECK_SVG + '</span>' +
        '<span class="opt-name">' + esc(d.id) + '</span>' +
        '<span class="opt-why">' + esc(d.why || '') + '</span></div>';
    }).join('');
    return '<div class="choice-card">' +
      '<p class="choice-q">' + esc(plan.summary || 'Execution plan') + '</p>' +
      '<div class="choice-opts">' + opts + '</div>' +
      '<div class="choice-foot"><span class="choice-hint">Clouds: ' +
      esc((plan.clouds || []).join(', ') || '—') + '</span>' +
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
    thread.innerHTML = selected.messages.map(function (m) {
      var tag = m.role === 'agent'
        ? '<div class="msg-agent-tag mono">planner agent</div>' : '';
      var card = (m.role === 'agent' && m.plan) ? planCard(m.plan) : '';
      return '<div class="msg ' + m.role + '">' + tag +
        '<div class="msg-body">' + esc(m.text) + '</div>' + card + '</div>';
    }).join('');
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

  /* ---------- inspector ---------- */

  function renderInspector() {
    var provs = provGrid.querySelectorAll('.prov');
    for (var i = 0; i < provs.length; i++) {
      var on = !!selected && provs[i].getAttribute('data-prov') === selected.provider;
      provs[i].classList.toggle('on', on);
    }
    tglIdem.classList.toggle('on', !!(selected && selected.idempotent));
    tglDestroy.classList.toggle('on', !!(selected && selected.config && selected.config.destroyAfter));
    budget.textContent = selected && selected.config ? selected.config.maxHours + 'h' : '—';

    var arts = selected ? selected.artifacts : [];
    if (!arts.length) {
      artList.innerHTML = '<p class="hint">No artifacts yet. They appear when a run verifies.</p>';
    } else {
      artList.innerHTML = arts.map(function (a) {
        return '<div class="art"><div class="art-top"><span class="art-name mono">' + esc(a.id) +
          '</span><span class="art-size mono">' + esc(a.size) + '</span></div>' +
          '<div class="art-note">' + esc(a.note) + '</div></div>';
      }).join('');
    }
  }

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
      schedulePoll();
    });
  }

  function schedulePoll() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (selected && selected.state === 'running') {
      pollTimer = setTimeout(function () {
        select(selectedId).then(refreshList);
      }, 3000);
    }
  }

  newBtn.addEventListener('click', function () {
    if (busy) return;
    busy = true;
    api('/api/tasks', { method: 'POST', body: JSON.stringify({ title: '' }) })
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
    api('/api/tasks/' + encodeURIComponent(selectedId) + '/messages', {
      method: 'POST', body: JSON.stringify({ text: text }),
    }).then(function (d) {
      pending.remove();
      selected = d.task;
      renderAll();
      refreshList();
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

  /* ---------- boot ---------- */

  refreshList().then(function () {
    if (tasks.length) select(tasks[0].id);
    else renderAll();
  }).catch(function () {
    list.innerHTML = '<p class="empty mono">Sign in to see your tasks.</p>';
  });
})();
