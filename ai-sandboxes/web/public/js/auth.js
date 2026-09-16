// Auth widget — the ONLY client-side JavaScript on the site.
// Reads /api/auth/me and swaps the nav "Sign in" button for the user's
// avatar + logout. Scoped exception to the no-JS rule (see AGENTS.md §8).
(function () {
  var ME_URL = '/api/auth/me';
  var LOGIN_URL = '/api/auth/login';
  var LOGOUT_URL = '/api/auth/logout';

  function loginUrl() {
    return LOGIN_URL + '?next=' + encodeURIComponent(location.pathname);
  }

  function wireSignIn(el) {
    el.setAttribute('href', loginUrl());
  }

  function renderUser(container, user) {
    container.innerHTML = '';
    var isConsole = container.hasAttribute('data-auth-menu');

    if (isConsole) {
      // Console: avatar opens a small dropdown (name/email, sign out).
      var wrap = document.createElement('div');
      wrap.className = 'usermenu';

      var btn = document.createElement('button');
      btn.className = 'usermenu-btn';
      btn.setAttribute('aria-haspopup', 'true');
      if (user.picture) {
        var img = document.createElement('img');
        img.src = user.picture;
        img.alt = user.name || user.email;
        img.className = 'nav-avatar';
        img.referrerPolicy = 'no-referrer';
        btn.appendChild(img);
      } else {
        btn.textContent = (user.name || user.email || '?').charAt(0).toUpperCase();
        btn.classList.add('usermenu-initial');
      }

      var menu = document.createElement('div');
      menu.className = 'usermenu-pop';
      menu.hidden = true;

      var who = document.createElement('div');
      who.className = 'usermenu-who';
      var nm = document.createElement('div');
      nm.className = 'usermenu-name';
      nm.textContent = user.name || user.email;
      var em = document.createElement('div');
      em.className = 'usermenu-email';
      em.textContent = user.name ? user.email : '';
      who.appendChild(nm);
      who.appendChild(em);

      var out = document.createElement('a');
      out.className = 'usermenu-item';
      out.href = LOGOUT_URL;
      out.textContent = 'Sign out';

      menu.appendChild(who);
      menu.appendChild(out);
      wrap.appendChild(btn);
      wrap.appendChild(menu);
      container.appendChild(wrap);

      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        menu.hidden = !menu.hidden;
      });
      document.addEventListener('click', function () { menu.hidden = true; });
      return;
    }

    if (user.picture) {
      var img = document.createElement('img');
      img.src = user.picture;
      img.alt = user.name;
      img.className = 'nav-avatar';
      img.referrerPolicy = 'no-referrer';
      container.appendChild(img);
    }
    var name = document.createElement('span');
    name.className = 'nav-user';
    name.textContent = user.name || user.email;
    container.appendChild(name);

    var out = document.createElement('a');
    out.className = 'btn ghost';
    out.href = LOGOUT_URL;
    out.textContent = 'Sign out';
    container.appendChild(out);
  }

  function init() {
    var signin = document.querySelector('[data-auth="signin"]');
    if (signin) wireSignIn(signin);

    var slot = document.querySelector('[data-auth="user"]');
    if (!slot) return;

    fetch(ME_URL, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.user) return;
        // Signed in: hide the sign-in button, show the user.
        if (signin) signin.style.display = 'none';
        renderUser(slot, data.user);
        document.querySelectorAll('[data-auth="protected-link"]').forEach(function (a) {
          a.style.display = '';
        });
      })
      .catch(function () { /* offline or API down: leave nav as-is */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
