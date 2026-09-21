// auth.js — shared login/session state for every frontend page. OI-6.
//
// There is no bundler and no shared header partial in this frontend (each
// HTML page has its own copy of the topbar markup) — see docs/PROGRESS.md,
// OI-6. Rather than duplicate token-handling logic per page, this file is
// included via a plain <script> tag before each page's own script, and
// every page that wants a login/logout affordance adds one element,
// <span id="auth-slot"></span>, to its nav for mountAuthNav() to render into.
//
// Requests go to the origin that served the page. This used to name
// http://127.0.0.1:8000 explicitly, because Express served the pages from
// :3000 and its /api proxy dropped the Authorization header — a logged-in
// request through it would have 401'd with no obvious cause. OI-5 retired
// Express; the backend serves these pages itself, so there is one origin
// and nothing to name.

const AUTH = (() => {
  // OI-5: same origin. The backend serves this page, so an absolute
  // origin here would be a second one to keep in step — and was what
  // forced CORS to allowlist localhost. Empty string keeps every
  // `${API_BASE}/api/...` template below working, as a relative URL.
  const API_BASE = '';
  const TOKEN_KEY = 'dk_auth_token';
  const USER_KEY = 'dk_auth_user';

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function _setSession(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }

  function clearSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }

  function getCachedUser() {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function isLoggedIn() {
    return Boolean(getToken() && getCachedUser());
  }

  // Every protected route (auth.py's CurrentUser/OptionalUser) fails the
  // same way — a stale or forged token is 401 with detail.code, never a
  // silent downgrade to anonymous (OI-10). A request that comes back with
  // token_expired or token_invalid means the session is over regardless of
  // which page or action triggered it, so clearing it here — the one place
  // every authenticated request passes through — is always correct. What
  // happens next (an inline "please log in" message vs. a redirect) is left
  // to the caller: this file does not decide that for every page.
  async function authFetch(path, options = {}) {
    const token = getToken();
    const headers = Object.assign({}, options.headers || {});
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });

    if (response.status === 401) {
      let code = null;
      try {
        code = (await response.clone().json()).detail?.code;
      } catch {
        // Non-JSON 401 body — still stale/invalid either way.
      }
      if (code === 'token_expired' || code === 'token_invalid' || code === 'account_inactive' || token) {
        clearSession();
      }
    }
    return response;
  }

  async function login(email, password) {
    try {
      const response = await fetch(`${API_BASE}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        return { ok: false, error: body.detail || 'Invalid email or password.' };
      }
      const data = await response.json();
      _setSession(data.access_token, data.user);
      return { ok: true, user: data.user };
    } catch {
      return { ok: false, error: 'Could not reach the server. Is the backend running?' };
    }
  }

  async function register(email, password, displayName) {
    try {
      const response = await fetch(`${API_BASE}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, display_name: displayName || null }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        return { ok: false, error: body.detail || 'Registration failed.' };
      }
      const data = await response.json();
      _setSession(data.access_token, data.user);
      return { ok: true, user: data.user };
    } catch {
      return { ok: false, error: 'Could not reach the server. Is the backend running?' };
    }
  }

  function logout() {
    clearSession();
    mountAuthNav();
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/[&<>"']/g, (m) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[m]));
  }

  // Renders into <span id="auth-slot"></span> wherever a page has one.
  // Pages without that element (this frontend has no shared nav include,
  // so not every page carries one — chatbot.html has no nav at all) are
  // silently skipped rather than treated as an error.
  function mountAuthNav() {
    const slot = document.getElementById('auth-slot');
    if (!slot) return;

    const user = getCachedUser();
    if (user) {
      slot.innerHTML = `
        <span class="auth-user" title="${escapeHtml(user.email)}">${escapeHtml(user.display_name || user.email)}</span>
        <a href="#" id="auth-logout-link">Log out</a>
      `;
      document.getElementById('auth-logout-link')?.addEventListener('click', (e) => {
        e.preventDefault();
        logout();
      });
    } else {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      slot.innerHTML = `<a href="./login.html?next=${next}">Log in</a>`;
    }
  }

  document.addEventListener('DOMContentLoaded', mountAuthNav);

  return {
    API_BASE,
    getToken,
    getCachedUser,
    isLoggedIn,
    authFetch,
    login,
    register,
    logout,
    mountAuthNav,
  };
})();
