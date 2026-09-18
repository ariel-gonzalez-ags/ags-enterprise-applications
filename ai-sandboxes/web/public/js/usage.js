/* usage.js: fills the /usage page from /api/embers. Scoped exception to the
 * no-JS rule (AGENTS.md §8), same pattern as console.js: vanilla, IIFE, only
 * calls /api/*. Server-trusted JSON rendered as text, never innerHTML from
 * untrusted values. */
(function () {
  'use strict';
  var $ = function (k) { return document.querySelector('[data-usage="' + k + '"]'); };
  var balEl = $('balance'), usdEl = $('usd'), barFill = $('bar-fill'),
      barLabel = $('bar-label'), runsEl = $('runs'), computeEl = $('compute'),
      tokensEl = $('tokens'), spentEl = $('spent'), histEl = $('history'),
      topupEl = $('topup');

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

  function row(delta, reason, meta, when) {
    var div = document.createElement('div'); div.className = 'hrow';
    var d = document.createElement('span');
    d.className = 'h-delta mono ' + (delta < 0 ? 'neg' : 'pos');
    d.textContent = (delta > 0 ? '+' : '') + delta;
    var what = document.createElement('span'); what.className = 'h-what';
    what.textContent = reason;
    var m = document.createElement('span'); m.className = 'h-meta mono';
    m.textContent = meta;
    var w = document.createElement('span'); w.className = 'h-when mono';
    w.textContent = when;
    div.appendChild(d); div.appendChild(what); div.appendChild(m); div.appendChild(w);
    return div;
  }

  function render(data) {
    var peg = data.peg_usd || 0.01;
    balEl.textContent = data.balance;
    usdEl.textContent = '≈ $' + (data.balance * peg).toFixed(2) + ' credit';
    runsEl.textContent = data.runs;
    computeEl.textContent = fmtCompute(data.total_sandbox_seconds);
    tokensEl.textContent = fmtTokens(data.total_llm_tokens);
    spentEl.textContent = data.total_spent;

    // Allowance bar: spent vs granted (the trial/paid pool). Guard divide-by-0.
    var granted = data.total_granted || 0;
    var pct = granted > 0 ? Math.min(100, Math.round((data.total_spent / granted) * 100)) : 0;
    barFill.style.width = pct + '%';
    barLabel.textContent = granted > 0
      ? data.total_spent + ' of ' + granted + ' Embers used (' + pct + '%)'
      : 'No allowance yet';

    var recent = data.recent || [];
    histEl.innerHTML = '';
    if (!recent.length) {
      histEl.innerHTML = '<p class="hint">No usage yet. Run a sandbox to see it here.</p>';
      return;
    }
    recent.forEach(function (r) {
      var meta = r.reason === 'run_burn'
        ? fmtCompute(r.sandbox_seconds) + ' · ' + fmtTokens(r.llm_tokens) + ' tok'
        : '';
      histEl.appendChild(row(r.delta, reasonLabel(r.reason), meta, fmtWhen(r.at)));
    });
  }

  if (topupEl) {
    topupEl.addEventListener('click', function (e) {
      e.preventDefault();  // billing (Stripe) is Phase 2b; nothing to do yet
    });
  }

  fetch('/api/embers', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
    .then(render)
    .catch(function () {
      if (histEl) histEl.innerHTML = '<p class="hint">Could not load usage. Try refreshing.</p>';
    });
})();
