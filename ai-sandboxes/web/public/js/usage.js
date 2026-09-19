/* usage.js: fills the /usage page from /api/embers. Scoped exception to the
 * no-JS rule (AGENTS.md §8), same pattern as console.js: vanilla, IIFE, only
 * calls /api/*. Server-trusted JSON rendered as text, never innerHTML from
 * untrusted values. Month navigation filters the timestamped history and the
 * by-model breakdown client-side (the ledger rows carry epoch timestamps). */
(function () {
  'use strict';
  var $ = function (k) { return document.querySelector('[data-usage="' + k + '"]'); };
  var balEl = $('balance'), usdEl = $('usd'), barFill = $('bar-fill'),
      barLabel = $('bar-label'), runsEl = $('runs'), computeEl = $('compute'),
      tokensEl = $('tokens'), spentEl = $('spent'), histEl = $('history'),
      byModelEl = $('by-model'), monthLabel = $('month-label'),
      prevBtn = $('prev'), nextBtn = $('next');

  var DATA = null;
  var BILLING = null;   // from /api/billing/config
  var pickedUsd = null; // selected pack amount (custom input overrides)
  // monthOffset: null = all time; 0 = current month; -1 = last month; etc.
  var monthOffset = null;

  function monthRange(offset) {
    var now = new Date();
    var start = new Date(now.getFullYear(), now.getMonth() + offset, 1);
    var end = new Date(now.getFullYear(), now.getMonth() + offset + 1, 1);
    return { lo: start.getTime() / 1000, hi: end.getTime() / 1000 };
  }
  function monthName(offset) {
    var now = new Date();
    var d = new Date(now.getFullYear(), now.getMonth() + offset, 1);
    return d.toLocaleString('default', { month: 'long', year: 'numeric' });
  }
  function inMonth(ts, offset) {
    if (offset === null) return true;
    var r = monthRange(offset);
    return ts >= r.lo && ts < r.hi;
  }

  function fmtWhen(ts) {
    if (!ts) return '—';
    var d = new Date(ts * 1000), now = Date.now(), s = (now - d.getTime()) / 1000;
    if (s < 90) return 'just now';
    if (s < 3600) return Math.round(s / 60) + 'm ago';
    if (s < 86400) return Math.round(s / 3600) + 'h ago';
    return d.toLocaleDateString();
  }
  function fmtCompute(sec) {
    if (!sec) return '0s';
    if (sec < 60) return sec + 's';
    if (sec < 3600) return Math.round(sec / 60) + 'm';
    return (sec / 3600).toFixed(1) + 'h';
  }
  function fmtTokens(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
    return String(n || 0);
  }
  function reasonLabel(r) {
    return { trial_grant: 'Trial allowance', run_burn: 'Sandbox run',
             topup: 'Top-up' }[r] || r;
  }
  function empty(msg) {
    var p = document.createElement('p'); p.className = 'hint'; p.textContent = msg;
    return p;
  }
  function cell(cls, text) {
    var s = document.createElement('span'); s.className = cls; s.textContent = text; return s;
  }

  /* ---- billing: card gate + top-up (Stripe-hosted; we only redirect) ---- */
  var gateEl = $('card-gate'), gateGo = $('card-gate-go'),
      topupCard = $('topup-card'), topupGo = $('topup-go'),
      customUsd = $('custom-usd'), topupErr = $('topup-err');

  function renderBilling() {
    if (!BILLING || !BILLING.enabled) return;   // billing off: hide both panels
    // Card gate: only for a user with no card on file (trial not yet unlocked).
    if (gateEl) gateEl.hidden = !!BILLING.card_on_file;
    if (topupCard) topupCard.hidden = false;
  }

  function goCheckout(url) { window.location.assign(url); }

  function postForUrl(path, body, onErr) {
    return fetch(path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || 'error');
        return d;
      });
    }).then(function (d) { if (d.checkout_url) goCheckout(d.checkout_url); })
      .catch(function (e) { if (onErr) onErr(e); });
  }

  function showErr(msg) {
    if (!topupErr) return;
    topupErr.textContent = msg; topupErr.hidden = !msg;
  }

  var packs = document.querySelectorAll('[data-usage="pack"]');
  function clearPacks() { packs.forEach(function (b) { b.classList.remove('sel'); }); }
  // Pack buttons select an amount; typing a custom amount deselects them.
  packs.forEach(function (btn) {
    btn.addEventListener('click', function () {
      clearPacks(); btn.classList.add('sel');
      pickedUsd = parseFloat(btn.getAttribute('data-usd'));
      if (customUsd) customUsd.value = '';
      showErr('');
    });
  });
  if (customUsd) customUsd.addEventListener('input', function () {
    if (customUsd.value) { clearPacks(); pickedUsd = null; }
  });

  if (topupGo) topupGo.addEventListener('click', function () {
    var usd = pickedUsd || parseFloat(customUsd && customUsd.value);
    var min = (BILLING && BILLING.min_topup_usd) || 10;
    if (!usd || isNaN(usd)) { showErr('Pick an amount or enter one.'); return; }
    if (usd < min) { showErr('Minimum is $' + min + '.'); return; }
    showErr('');
    topupGo.disabled = true;
    postForUrl('/api/billing/topup', { usd: usd }, function (e) {
      topupGo.disabled = false; showErr(e.message || 'Could not start checkout.');
    });
  });

  if (gateGo) gateGo.addEventListener('click', function () {
    gateGo.disabled = true;
    postForUrl('/api/billing/card-setup', null, function () { gateGo.disabled = false; });
  });

  function render() {
    var data = DATA;
    var peg = data.peg_usd || 0.01;
    balEl.textContent = data.balance;
    usdEl.textContent = '≈ $' + (data.balance * peg).toFixed(2) + ' credit';

    // Filter the ledger rows to the selected month (null = all time).
    var rows = (data.recent || []).filter(function (r) { return inMonth(r.at, monthOffset); });
    var burns = rows.filter(function (r) { return r.delta < 0; });
    var granted = rows.filter(function (r) { return r.delta > 0; })
                      .reduce(function (a, r) { return a + r.delta; }, 0);
    var spent = burns.reduce(function (a, r) { return a - r.delta; }, 0);
    var seconds = burns.reduce(function (a, r) { return a + r.sandbox_seconds; }, 0);
    var tokens = burns.reduce(function (a, r) { return a + r.llm_tokens; }, 0);

    runsEl.textContent = burns.length;
    computeEl.textContent = fmtCompute(seconds);
    tokensEl.textContent = fmtTokens(tokens);
    spentEl.textContent = spent;

    var pct = granted > 0 ? Math.min(100, Math.round((spent / granted) * 100)) : 0;
    barFill.style.width = pct + '%';
    barLabel.textContent = granted > 0
      ? spent + ' of ' + granted + ' Embers used (' + pct + '%)'
      : (monthOffset === null ? 'No allowance yet' : 'No allowance in this period');

    // Month nav state
    monthLabel.textContent = monthOffset === null ? 'All time' : monthName(monthOffset);
    nextBtn.disabled = (monthOffset === null || monthOffset >= 0);

    // By-model breakdown, filtered to the same window.
    byModelEl.innerHTML = '';
    var byModel = {};
    burns.forEach(function (r) {
      var k = r.model || 'unknown';
      if (!byModel[k]) byModel[k] = { embers: 0, runs: 0, tokens: 0, seconds: 0 };
      byModel[k].embers += -r.delta;
      byModel[k].runs += 1;
      byModel[k].tokens += r.llm_tokens;
      byModel[k].seconds += r.sandbox_seconds;
    });
    var models = Object.keys(byModel);
    if (!models.length) {
      byModelEl.appendChild(empty('No model usage in this period.'));
    } else {
      models.sort(function (a, b) { return byModel[b].embers - byModel[a].embers; });
      models.forEach(function (m) {
        var d = byModel[m];
        var div = document.createElement('div'); div.className = 'hrow';
        div.appendChild(cell('h-delta mono neg', String(d.embers)));
        div.appendChild(cell('h-what', m));
        div.appendChild(cell('h-meta mono', d.runs + ' runs · ' + fmtTokens(d.tokens) + ' tok'));
        div.appendChild(cell('h-when mono', fmtCompute(d.seconds)));
        byModelEl.appendChild(div);
      });
    }

    // History
    histEl.innerHTML = '';
    if (!rows.length) {
      histEl.appendChild(empty(monthOffset === null
        ? 'No usage yet. Run a sandbox to see it here.'
        : 'No usage data for ' + monthName(monthOffset) + '.'));
      return;
    }
    rows.forEach(function (r) {
      var meta = r.reason === 'run_burn'
        ? fmtCompute(r.sandbox_seconds) + ' · ' + fmtTokens(r.llm_tokens) + ' tok'
        : '';
      var div = document.createElement('div'); div.className = 'hrow';
      div.appendChild(cell('h-delta mono ' + (r.delta < 0 ? 'neg' : 'pos'),
                           (r.delta > 0 ? '+' : '') + r.delta));
      div.appendChild(cell('h-what', reasonLabel(r.reason)));
      div.appendChild(cell('h-meta mono', meta));
      div.appendChild(cell('h-when mono', fmtWhen(r.at)));
      histEl.appendChild(div);
    });
  }

  if (prevBtn) prevBtn.addEventListener('click', function () {
    monthOffset = (monthOffset === null) ? -1 : monthOffset - 1;
    render();
  });
  if (nextBtn) nextBtn.addEventListener('click', function () {
    if (monthOffset === null) return;
    monthOffset += 1;
    if (monthOffset > 0) monthOffset = null;  // past current month -> all time
    render();
  });

  function loadBilling() {
    return fetch('/api/billing/config', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (cfg) { BILLING = cfg; renderBilling(); })
      .catch(function () { /* billing hidden */ });
  }
  function loadEmbers() {
    return fetch('/api/embers', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) { DATA = data; render(); })
      .catch(function () {
        if (histEl) { histEl.innerHTML = ''; histEl.appendChild(empty('Could not load usage. Try refreshing.')); }
      });
  }
  function loadAll() { loadEmbers(); loadBilling(); }

  // Returning from Stripe (card saved / top-up paid) lands us back here with a
  // query param. The webhook may still be in flight, so re-fetch a couple of
  // times to catch the credited balance / flipped card flag, then clean the URL.
  var ret = new URLSearchParams(window.location.search);
  loadAll();
  if (ret.has('card') || ret.has('topup')) {
    [1200, 3000].forEach(function (ms) { setTimeout(loadAll, ms); });
    window.history.replaceState({}, '', window.location.pathname);
  }
})();
