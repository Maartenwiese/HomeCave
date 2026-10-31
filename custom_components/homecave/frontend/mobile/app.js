/**
 * HomeCave Standalone Mobile Web App (PWA) — /homecave/app.js (v0.9.1)
 * Runs parallel to the native Home Assistant UI, inherits Home Assistant OAuth2
 * authentication, defaults to Large (L) text scale, and supports Home Screen installation.
 */

const WINE_TYPES = {
  red: "Red",
  white: "White",
  rose: "Rosé",
  sparkling: "Sparkling",
  dessert: "Dessert",
  fortified: "Fortified",
};

const DRINK_META = {
  ready: { label: "Ready", color: "#2e7d32" },
  drink_soon: { label: "Soon", color: "#ef6c00" },
  hold: { label: "Keep", color: "#0277bd" },
  keep: { label: "Keep", color: "#0277bd" },
  past_peak: { label: "Past Peak", color: "#c62828" },
  unknown: { label: "No Window Set", color: "#6c5e63" },
};

const FONT_SCALES = [
  { id: "S", value: 0.9 },
  { id: "M", value: 1.0 },
  { id: "L", value: 1.15 },
  { id: "XL", value: 1.3 },
];

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function safeExternalUrl(url) {
  if (!url) return "";
  const raw = String(url).trim();
  try {
    const parsed = new URL(raw, window.location.origin);
    if (parsed.protocol === "https:") return esc(parsed.href);
  } catch (_) {}
  return "";
}

function safeImageUrl(url) {
  if (!url) return "";
  const raw = String(url).trim();
  if (
    (raw.startsWith("/local/") || raw.startsWith("/api/")) &&
    !raw.includes("..")
  ) {
    return esc(raw);
  }
  if (/^data:image\/(jpeg|jpg|png|webp);base64,[A-Za-z0-9+/=\s]+$/i.test(raw)) {
    return esc(raw);
  }
  try {
    const parsed = new URL(raw);
    if (parsed.protocol === "https:") return esc(parsed.href);
  } catch (_) {}
  return "";
}

function nowIsoMinutes() {
  const now = new Date();
  const offsetMs = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offsetMs).toISOString().slice(0, 16);
}

function fmtDate(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  if (Number.isNaN(d.getTime())) return String(isoStr).slice(0, 16);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function personalRatingMeta(rawVal) {
  if (rawVal === null || rawVal === undefined || rawVal === "" || (typeof rawVal === "string" && !rawVal.trim())) {
    return {
      rated: false,
      valueStr: "",
      sliderValue: "4.0",
      tier: "Not rated",
      badgeText: "Slide 1.0–5.0 to rate",
    };
  }
  const num = Number(rawVal);
  if (!Number.isFinite(num)) {
    return {
      rated: false,
      valueStr: "",
      sliderValue: "4.0",
      tier: "Not rated",
      badgeText: "Slide 1.0–5.0 to rate",
    };
  }
  const clamped = Math.min(5.0, Math.max(1.0, Math.round(num * 10) / 10));
  const valueStr = clamped.toFixed(1);
  let tier = "Good";
  if (clamped >= 4.95) tier = "Excellent!";
  else if (clamped >= 4.5) tier = "Outstanding";
  else if (clamped >= 4.0) tier = "Very Good";
  else if (clamped >= 3.5) tier = "Good";
  else if (clamped >= 3.0) tier = "Average";
  else if (clamped >= 2.0) tier = "Below Average";
  else tier = "Poor";
  return {
    rated: true,
    valueStr,
    sliderValue: valueStr,
    tier,
    badgeText: `★ ${valueStr} / 5.0 — ${tier}`,
  };
}

function renderPersonalRatingSlider(prefix, rawVal) {
  const meta = personalRatingMeta(rawVal);
  return `
    <div class="rating-slider-card" data-rating-slider-prefix="${esc(prefix)}">
      <div class="rating-slider-head">
        <label for="${esc(prefix)}-rating-range">My Personal Rating (1.0–5.0 ★)</label>
        <div class="rating-slider-status">
          <span class="rating-slider-badge ${meta.rated ? "active" : ""}" id="${esc(prefix)}-rating-badge">${esc(meta.badgeText)}</span>
          <button type="button" class="rating-clear-btn" id="${esc(prefix)}-rating-clear" style="${meta.rated ? "" : "display:none;"}">Clear</button>
        </div>
      </div>
      <input type="hidden" name="personal_rating" id="${esc(prefix)}-rating-hidden" value="${esc(meta.valueStr)}" />
      <input
        type="range"
        class="rating-range-slider"
        id="${esc(prefix)}-rating-range"
        min="1.0"
        max="5.0"
        step="0.1"
        value="${esc(meta.sliderValue)}"
        aria-label="My personal rating from 1.0 to 5.0 in 0.1 increments"
      />
      <div class="rating-slider-ticks">
        <span>1.0</span>
        <span>2.0</span>
        <span>3.0 Good</span>
        <span>4.0 Great</span>
        <span style="color:var(--brand); font-weight:800;">5.0 Excellent!</span>
      </div>
    </div>
  `;
}

class HomeCaveMobileApp {
  constructor(rootEl) {
    this.root = rootEl;
    this.state = {
      cellarName: "HomeCave",
      currency: "EUR",
      tempDisplay: null,
      humidityDisplay: null,
      areas: ["HomeCave", "HomeCave Fridge", "Kitchen Rack"],
      statistics: {},
      wines: [],
      loading: true,
      error: "",
      authRequired: false,
      authenticatedUser: null,
      isOnline: navigator.onLine,

      // Navigation: "cellar" | "sommai" | "tasted" | "config"
      activeNav: "cellar",
      filterTab: "all",
      areaFilter: "",
      searchQuery: "",
      sortBy: "recent",
      sortInverted: false,

      // SommAI Sommelier state
      sommAiMessages: [],
      sommAiInputText: "",
      sommAiPendingImage: null,
      sommAiLoading: false,
      sommAiError: "",

      // Vivino import progress & warning modal state
      importWarningModal: false,
      importPendingFile: null,
      importProgressModal: false,
      importProgressData: null,
      importEnrichAi: true,

      // Tasted page filter/search/sort state
      tastedFilterTab: "all",
      tastedSearchQuery: "",
      tastedSortBy: "recent_tasted",
      tastedSortInverted: false,

      // Scan destination: "cellar" | "tasted"
      scanDestination: "cellar",

      // Display preferences
      fontScale: this._loadFontScale(),
      theme: localStorage.getItem("homecave_theme") || "auto",

      // PWA install state
      deferredPrompt: null,
      isInstalled:
        window.matchMedia("(display-mode: standalone)").matches ||
        window.navigator.standalone === true,
      isIOS: /iphone|ipad|ipod/i.test(window.navigator.userAgent || ""),
      installBannerDismissed: false,

      // Active sheet: null | "detail" | "scan" | "crop" | "preview" | "install_guide"
      activeSheet: null,
      detailWine: null,
      detailForm: null,
      previewWine: null,
      previousSheet: null,
      researchingWineId: null,
      researchError: "",
      linkingVivinoId: null,
      vivinoLinkError: "",

      // Scan & Add form
      scanBusy: false,
      scanError: "",
      scanImageData: "",
      scanCandidates: [],
      scanConfidence: "none",
      scanSuggestedIndex: -1,
      selectedCandidateIdx: -1,
      existingWine: null,
      scanForm: this._emptyForm(),
      cropState: null,

      toastMessage: "",
    };

    this._searchDebounce = null;
    this._toastTimer = null;
    this._cropDrag = null;
    this._cropRaf = null;

    this._applyThemeAndScale();
    this._setupPWAEvents();
    this._setupTouchGestures();
    this._parseUrlParams();
    this.render();
    this._initAuthAndFetchState();
  }

  /* ================= Touch helpers: confirm sheet, toast, gestures ================= */

  /**
   * Touch-friendly replacement for the native confirm dialog: a full-screen page with large
   * buttons. Rendered in its own DOM node so it never disturbs app state.
   */
  confirmAction({ title, message, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false }) {
    return new Promise((resolve) => {
      const host = document.createElement("div");
      host.className = "confirm-backdrop";
      host.setAttribute("role", "dialog");
      host.setAttribute("aria-modal", "true");
      host.innerHTML = `
        <div class="confirm-sheet">
          <div class="sheet-header">
            <button type="button" class="btn btn-icon btn-back" data-confirm="no" aria-label="Back"><span aria-hidden="true">‹</span></button>
            <h2>${esc(title)}</h2>
          </div>
          <div class="confirm-body">
            <h3 class="confirm-title">${esc(title)}</h3>
            <p class="confirm-message">${esc(message)}</p>
            <div class="confirm-actions">
              <button type="button" class="btn btn-lg ${danger ? "btn-danger-solid" : "btn-primary"}" data-confirm="yes">${esc(confirmLabel)}</button>
              <button type="button" class="btn btn-lg" data-confirm="no">${esc(cancelLabel)}</button>
            </div>
          </div>
        </div>
      `;
      const finish = (val) => {
        host.remove();
        resolve(val);
      };
      host.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-confirm]");
        if (btn) finish(btn.dataset.confirm === "yes");
      });
      document.body.appendChild(host);
      host.querySelector('[data-confirm="no"]')?.focus({ preventScroll: true });
    });
  }

  /**
   * Normalize numeric fields in wine payloads so empty strings "" are always converted
   * to null before sending to the backend SQLite API.
   */
  _sanitizeWinePayload(raw) {
    const out = { ...raw };
    const numericFields = [
      "wine_id",
      "vintage",
      "alcohol_pct",
      "bottle_size_ml",
      "vivino_rating",
      "vivino_ratings_count",
      "vivino_price_estimate",
      "vivino_wine_id",
      "drink_from_year",
      "drink_until_year",
      "personal_rating",
      "purchase_price",
    ];
    numericFields.forEach((k) => {
      if (!(k in out)) return;
      const v = out[k];
      if (v === "" || v === null || v === undefined || (typeof v === "string" && !v.trim())) {
        out[k] = null;
      } else {
        const n = Number(v);
        out[k] = Number.isFinite(n) ? n : null;
      }
    });
    if ("quantity" in out) {
      const q = Number(out.quantity);
      if (out.as_tasted) {
        out.quantity = 0;
      } else {
        out.quantity = Number.isFinite(q) && q >= 1 ? Math.round(q) : 1;
      }
    }
    if ("as_tasted" in out) {
      out.as_tasted = Boolean(out.as_tasted);
    }
    return out;
  }

  /** Auto-scale a textarea's height to fit all of its text content without inner scrollbars. */
  _autoResizeTextarea(el) {
    if (!el) return;
    el.style.height = "0px";
    const scale = el.closest(".scaled-zone") ? Math.max(1, Number(this.state.fontScale) || 1) : 1;
    const rawScroll = el.scrollHeight || 0;
    const nextHeight = Math.max(84, Math.ceil(rawScroll * scale) + 12);
    el.style.height = `${nextHeight}px`;
  }

  _autoResizeTextareas() {
    if (!this.root) return;
    this.root.querySelectorAll("textarea.textarea").forEach((el) => this._autoResizeTextarea(el));
  }

  _setupTouchGestures() {
    // Pull-to-refresh on the Cellar list (native PTR is disabled by overscroll-behavior).
    let startY = null;
    let pulling = false;
    const indicator = () => document.getElementById("ptr-indicator");
      window.addEventListener(
      "touchstart",
      (e) => {
        if (
          this.state.activeSheet ||
          (this.state.activeNav !== "cellar" && this.state.activeNav !== "tasted") ||
          window.scrollY > 0
        ) {
          startY = null;
          return;
        }
        if (e.touches.length !== 1) return;
        startY = e.touches[0].clientY;
        pulling = false;
      },
      { passive: true }
    );
    window.addEventListener(
      "touchmove",
      (e) => {
        if (startY === null) return;
        const dy = e.touches[0].clientY - startY;
        const el = indicator();
        if (!el) return;
        if (dy > 8 && window.scrollY <= 0) {
          pulling = true;
          const pull = Math.min(dy, 110);
          el.style.transform = `translate(-50%, ${pull - 50}px) rotate(${pull * 3}deg)`;
          el.style.opacity = String(Math.min(1, pull / 70));
          el.classList.toggle("ready", pull >= 75);
        }
      },
      { passive: true }
    );
    window.addEventListener(
      "touchend",
      () => {
        const el = indicator();
        if (pulling && el) {
          const ready = el.classList.contains("ready");
          el.style.transform = "";
          el.style.opacity = "";
          el.classList.remove("ready");
          if (ready && this.state.isOnline) {
            el.classList.add("spinning");
            this.fetchState().finally(() => {
              const again = indicator();
              if (again) again.classList.remove("spinning");
              this.showToast("Cellar refreshed");
            });
          }
        }
        startY = null;
        pulling = false;
      },
      { passive: true }
    );

    window.addEventListener("popstate", () => this._onPopState());

    // Redraw the crop canvas after orientation / viewport changes.
    window.addEventListener("resize", () => {
      if (this.state.activeSheet === "crop") this._drawCropCanvas();
    });
  }

  /**
   * Back navigation: the on-screen ‹ Back button and the phone's
   * hardware back button / back gesture all go to the previous page.
   * Open sheets are mirrored as browser history entries (this._sheetStack).
   */
  goBack() {
    const stack = this._sheetStack || [];
    if (stack.length && stack[stack.length - 1] === this.state.activeSheet) {
      history.back(); // popstate handler performs the navigation
      return;
    }
    this.closeSheet();
  }

  _onPopState() {
    if (this._ignorePops > 0) {
      this._ignorePops -= 1;
      return;
    }
    const stack = this._sheetStack || [];
    if (!stack.length) {
      if (this.state.activeSheet) this.closeSheet();
      return;
    }
    stack.pop();
    const prev = stack.length ? stack[stack.length - 1] : null;
    if (this.state.activeSheet === "preview") this.state.previewWine = null;
    this._navigatingBack = true;
    this.state.activeSheet = prev;
    this.render();
    this._navigatingBack = false;
  }

  /** Keep browser history in sync with the open sheet after every render. */
  _syncSheetHistory() {
    if (this._navigatingBack) return;
    const stack = this._sheetStack || (this._sheetStack = []);
    const cur = this.state.activeSheet;
    const top = stack.length ? stack[stack.length - 1] : null;
    if (cur === top) return;
    const idx = cur ? stack.lastIndexOf(cur) : -1;
    if (!cur || idx >= 0) {
      // Closed programmatically (e.g. after saving) or returned to an earlier sheet.
      const steps = stack.length - (idx + 1);
      if (steps > 0) {
        this._ignorePops = (this._ignorePops || 0) + 1;
        stack.length = idx + 1;
        history.go(-steps);
      }
      return;
    }
    stack.push(cur);
    history.pushState({ lsSheet: cur, depth: stack.length }, "");
  }

  closeSheet() {
    if (this.state.activeSheet === "preview") {
      this.state.activeSheet = this.state.previousSheet || null;
      this.state.previewWine = null;
    } else if (this.state.activeSheet === "crop") {
      this.state.activeSheet = "scan";
    } else {
      this.state.activeSheet = null;
    }
    this.render();
  }

  /** Two-way bind form fields into state so re-renders never discard unsaved edits. */
  _bindFormState(form, target) {
    if (!form || !target) return;
    const sync = (e) => {
      const el = e.target;
      if (!el || !el.name) return;
      target[el.name] = el.value;
    };
    form.addEventListener("input", sync);
    form.addEventListener("change", sync);
  }

  async _initAuthAndFetchState() {
    await this._handleAuthCallback();
    await this.fetchState();
  }

  _readStoredTokens() {
    const keys = ["hassTokens", "homecave_ha_tokens"];
    for (const key of keys) {
      try {
        const raw = localStorage.getItem(key);
        if (!raw) continue;
        const parsed = JSON.parse(raw);
        if (parsed && (parsed.access_token || parsed.refresh_token)) {
          return parsed;
        }
      } catch (_) {}
    }
    return null;
  }

  _saveTokens(tokenData) {
    if (!tokenData || typeof tokenData !== "object") return;
    try {
      localStorage.setItem("homecave_ha_tokens", JSON.stringify(tokenData));
      if (!localStorage.getItem("hassTokens")) {
        localStorage.setItem("hassTokens", JSON.stringify(tokenData));
      }
    } catch (_) {}
  }

  async _handleAuthCallback() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    if (!code) return;
    const clientId = `${window.location.origin}/`;
    try {
      const body = new URLSearchParams({
        grant_type: "authorization_code",
        code,
        client_id: clientId,
      });
      const res = await fetch("/auth/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      if (res.ok) {
        const data = await res.json();
        const expiresInMs = Number(data.expires_in || 1800) * 1000;
        const tokenObj = {
          hassUrl: window.location.origin,
          clientId,
          access_token: data.access_token,
          refresh_token: data.refresh_token,
          token_type: data.token_type || "Bearer",
          expires_in: data.expires_in || 1800,
          expires: Date.now() + expiresInMs,
        };
        this._saveTokens(tokenObj);
        this.state.authRequired = false;
      }
    } catch (err) {
      console.warn("HA OAuth token exchange failed:", err);
    } finally {
      params.delete("code");
      params.delete("auth_callback");
      params.delete("state");
      const cleanQs = params.toString();
      const cleanUrl = `${window.location.pathname}${cleanQs ? `?${cleanQs}` : ""}`;
      window.history.replaceState({}, document.title, cleanUrl);
    }
  }

  async _refreshAccessToken(tokenData) {
    if (!tokenData || !tokenData.refresh_token) return null;
    const clientId = tokenData.clientId || `${window.location.origin}/`;
    try {
      const body = new URLSearchParams({
        grant_type: "refresh_token",
        refresh_token: tokenData.refresh_token,
        client_id: clientId,
      });
      const res = await fetch("/auth/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      if (!res.ok) return null;
      const refreshed = await res.json();
      if (!refreshed.access_token) return null;
      const expiresInMs = Number(refreshed.expires_in || 1800) * 1000;
      const updated = {
        ...tokenData,
        access_token: refreshed.access_token,
        expires_in: refreshed.expires_in || 1800,
        expires: Date.now() + expiresInMs,
      };
      this._saveTokens(updated);
      try {
        if (localStorage.getItem("hassTokens")) {
          localStorage.setItem("hassTokens", JSON.stringify(updated));
        }
      } catch (_) {}
      return updated.access_token;
    } catch (_) {
      return null;
    }
  }

  async _getValidAccessToken(forceRefresh = false) {
    const tokenData = this._readStoredTokens();
    if (!tokenData) return null;
    const now = Date.now();
    const isExpired =
      forceRefresh ||
      !tokenData.access_token ||
      (tokenData.expires && Number(tokenData.expires) - 60000 < now);
    if (isExpired && tokenData.refresh_token) {
      const fresh = await this._refreshAccessToken(tokenData);
      if (fresh) return fresh;
    }
    return tokenData.access_token || null;
  }

  redirectToHomeAssistantLogin() {
    const clientId = `${window.location.origin}/`;
    const redirectUri = `${window.location.origin}/homecave/?auth_callback=1`;
    const authUrl = `/auth/authorize?response_type=code&client_id=${encodeURIComponent(clientId)}&redirect_uri=${encodeURIComponent(redirectUri)}`;
    window.location.assign(authUrl);
  }

  _emptyForm() {
    return {
      wine_id: null,
      winery: "",
      name: "",
      vintage: "",
      wine_type: "red",
      varietal: "",
      region: "",
      country: "",
      vivino_rating: "",
      vivino_ratings_count: null,
      vivino_url: "",
      vivino_price_estimate: "",
      vivino_wine_id: null,
      vivino_match_confidence: "",
      rating_source: "",
      drink_from_year: "",
      drink_until_year: "",
      drink_window_note: "",
      general_notes: "",
      batch_notes: "",
      personal_rating: "",
      quantity: 1,
      added_at: nowIsoMinutes(),
      purchase_location: "",
      purchase_price: "",
      storage_location: "",
    };
  }

  _loadFontScale() {
    const val = parseFloat(
      localStorage.getItem("homecave_mobile_font_scale") || ""
    );
    return val >= 0.8 && val <= 1.5 ? val : 1.15;
  }

  _fontScaleLabel() {
    let best = FONT_SCALES[2];
    FONT_SCALES.forEach((s) => {
      if (Math.abs(s.value - this.state.fontScale) < Math.abs(best.value - this.state.fontScale)) {
        best = s;
      }
    });
    return best.id;
  }

  setFontScale(value) {
    this.state.fontScale = value;
    try {
      localStorage.setItem("homecave_mobile_font_scale", String(value));
    } catch (_) {}
    this._applyThemeAndScale();
    this.render();
  }

  cycleFontScale() {
    const curLabel = this._fontScaleLabel();
    const idx = FONT_SCALES.findIndex((s) => s.id === curLabel);
    const next = FONT_SCALES[(idx + 1) % FONT_SCALES.length];
    this.setFontScale(next.value);
    this.showToast(`Text size set to ${next.id} (${Math.round(next.value * 100)}%)`);
  }

  setTheme(theme) {
    this.state.theme = theme;
    try {
      localStorage.setItem("homecave_theme", theme);
    } catch (_) {}
    this._applyThemeAndScale();
    this.render();
  }

  _applyThemeAndScale() {
    document.documentElement.style.setProperty("--ls-app-scale", String(this.state.fontScale));
    const prefersDark =
      window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark =
      this.state.theme === "dark" || (this.state.theme === "auto" && prefersDark);
    document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
  }

  _setupPWAEvents() {
    if ("serviceWorker" in navigator) {
      window.addEventListener("load", () => {
        navigator.serviceWorker
          .register("/homecave/sw.js", { scope: "/homecave/" })
          .catch((err) => console.warn("SW registration skipped:", err));
      });
    }

    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      this.state.deferredPrompt = e;
      this.render();
    });

    window.addEventListener("appinstalled", () => {
      this.state.isInstalled = true;
      this.state.deferredPrompt = null;
      this.state.activeSheet = null;
      this.showToast("HomeCave installed as an app!");
      this.render();
    });

    window.addEventListener("online", () => {
      this.state.isOnline = true;
      this.fetchState();
    });
    window.addEventListener("offline", () => {
      this.state.isOnline = false;
      this.render();
    });
  }

  _parseUrlParams() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("action") === "scan") {
      this.state.activeSheet = "scan";
    }
    if (params.get("filter")) {
      this.state.filterTab = params.get("filter");
    }
  }

  /**
   * Show a toast in its own DOM node. Never calls render(): a full re-render
   * would steal focus, close the on-screen keyboard, and reset scroll position.
   */
  showToast(msg, kind = "info") {
    this.state.toastMessage = msg;
    let host = document.getElementById("ls-toast-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "ls-toast-host";
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    host.innerHTML = `<div class="toast ${kind === "error" ? "toast-error" : ""}">${esc(msg)}</div>`;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(
      () => {
        this.state.toastMessage = "";
        const h = document.getElementById("ls-toast-host");
        if (h) h.innerHTML = "";
      },
      kind === "error" ? 4200 : 2400
    );
  }

  showError(prefix, err) {
    this.showToast(`${prefix}: ${err?.message || err}`, "error");
  }

  _clearAuthCache() {
    try {
      if ("serviceWorker" in navigator && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ type: "CLEAR_AUTH_CACHE" });
      }
      if ("caches" in window) {
        caches.keys().then((keys) => {
          keys
            .filter((k) => k.startsWith("homecave-pwa-"))
            .forEach((k) => {
              caches.open(k).then((cache) =>
                cache.keys().then((reqs) =>
                  reqs
                    .filter((r) => new URL(r.url).pathname.startsWith("/api/"))
                    .forEach((r) => cache.delete(r))
                )
              );
            });
        });
      }
    } catch (_) {}
  }

  async apiGet(action, params = {}) {
    const qs = new URLSearchParams(params).toString();
    const url = `/api/homecave/pwa/${action}${qs ? `?${qs}` : ""}`;
    let token = await this._getValidAccessToken(false);
    const headers = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;

    let res = await fetch(url, { credentials: "same-origin", headers });
    if (res.status === 401) {
      token = await this._getValidAccessToken(true);
      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
        res = await fetch(url, { credentials: "same-origin", headers });
      }
    }
    if (res.status === 401) {
      this.state.authRequired = true;
      this._clearAuthCache();
      throw new Error("Home Assistant authentication required. Tap Sign In below.");
    }
    this.state.authRequired = false;
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  async apiPost(action, body = {}) {
    let token = await this._getValidAccessToken(false);
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;

    let res = await fetch(`/api/homecave/pwa/${action}`, {
      method: "POST",
      headers,
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
    if (res.status === 401) {
      token = await this._getValidAccessToken(true);
      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
        res = await fetch(`/api/homecave/pwa/${action}`, {
          method: "POST",
          headers,
          credentials: "same-origin",
          body: JSON.stringify(body),
        });
      }
    }
    if (res.status === 401) {
      this.state.authRequired = true;
      this._clearAuthCache();
      this.render();
      throw new Error("Home Assistant authentication required.");
    }
    this.state.authRequired = false;
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  async fetchState() {
    this.state.loading = true;
    try {
      const data = await this.apiGet("state", { include_out_of_stock: "true" });
      this.state.cellarName = data.cellar_name || "HomeCave";
      this.state.currency = data.currency || "EUR";
      this.state.tempDisplay = data.temperature_display || null;
      this.state.humidityDisplay = data.humidity_display || null;
      this.state.areas = data.areas || ["HomeCave", "HomeCave Fridge", "Kitchen Rack"];
      this.state.statistics = data.statistics || {};
      this.state.wines = data.wines || [];
      this.state.backups = data.backups || [];
      this.state.authenticatedUser = data.authenticated_user || null;
      this.state.error = "";
    } catch (err) {
      this.state.error = err?.message || "Could not connect to Home Assistant Wine Inventory API.";
    } finally {
      this.state.loading = false;
      this.render();
    }
  }

  _isWineTasted(w) {
    if (!w) return false;
    if (Boolean(w.is_tasted)) return true;
    if (Number(w.tasted_count || 0) > 0 || Number(w.tasted_bottles || 0) > 0) return true;
    if (Number(w.quantity || 0) === 0) return true;
    if (Array.isArray(w.timeline)) {
      return w.timeline.some(
        (h) => h && (h.event_type === "consumed" || h.event_type === "tasted" || Number(h.delta || 0) < 0)
      );
    }
    return false;
  }

  _getWineSearchText(w) {
    if (!w) return "";
    const parts = [
      w.winery,
      w.name,
      w.vintage,
      w.wine_type,
      w.varietal,
      w.region,
      w.country,
      w.alcohol_pct ? `${w.alcohol_pct}%` : "",
      w.alcohol_pct,
      w.bottle_size_ml ? `${w.bottle_size_ml}ml` : "",
      w.bottle_size_ml,
      w.drink_from_year,
      w.drink_until_year,
      w.drink_window_note,
      w.drink_status,
      DRINK_META[w.drink_status]?.label,
      w.general_notes,
      w.label_summary,
      w.label_text_verbatim,
      w.last_purchase_location,
      w.last_storage_location,
      w.purchase_location,
      w.storage_location,
      w.purchase_price,
      w.last_purchase_price,
      w.vivino_price_estimate,
      w.personal_rating ? `★ ${w.personal_rating}` : "",
      w.personal_rating,
      w.vivino_rating ? `★ ${w.vivino_rating}` : "",
      w.vivino_rating,
      w.vivino_url,
    ];

    if (w.is_imported) {
      parts.push("imported", "vivino import");
    }

    if (Array.isArray(w.food_pairings)) {
      parts.push(...w.food_pairings);
    } else if (w.food_pairings) {
      parts.push(String(w.food_pairings));
    }

    if (w.research_notes) {
      if (typeof w.research_notes === "object") {
        try {
          parts.push(JSON.stringify(w.research_notes));
        } catch (_) {}
      } else {
        parts.push(String(w.research_notes));
      }
    }

    if (Array.isArray(w.batches)) {
      for (const b of w.batches) {
        if (!b) continue;
        parts.push(b.purchase_location, b.storage_location, b.notes, b.drink_window_note);
      }
    }

    if (Array.isArray(w.timeline)) {
      for (const t of w.timeline) {
        if (!t) continue;
        parts.push(t.event_type, t.tasting_notes, t.storage_location, t.purchase_location, t.notes);
      }
    }

    return parts.filter(Boolean).join(" ").toLowerCase();
  }

  getFilteredWines() {
    const q = (this.state.searchQuery || "").trim().toLowerCase();
    const areaFilter = (this.state.areaFilter || "").trim().toLowerCase();

    const filtered = this.state.wines.filter((w) => {
      const qty = Number(w.quantity || 0);
      if (this.state.filterTab === "archived" || this.state.filterTab === "tasted") {
        if (!this._isWineTasted(w)) return false;
      } else {
        if (qty <= 0) return false;
        if (this.state.filterTab === "ready" && w.drink_status !== "ready") {
          return false;
        }
        if (this.state.filterTab === "drink_soon" && w.drink_status !== "drink_soon") {
          return false;
        }
        if (
          (this.state.filterTab === "keep" || this.state.filterTab === "hold") &&
          w.drink_status !== "hold" &&
          w.drink_status !== "keep"
        ) {
          return false;
        }
        if (this.state.filterTab === "past_peak" && w.drink_status !== "past_peak") {
          return false;
        }
        if (
          ["red", "white", "rose", "sparkling"].includes(this.state.filterTab) &&
          w.wine_type !== this.state.filterTab
        ) {
          return false;
        }
      }

      if (areaFilter) {
        const wineAreas = [
          w.storage_location,
          w.last_storage_location,
          ...(w.active_storage_locations || []),
        ]
          .filter(Boolean)
          .map((x) => String(x).toLowerCase());
        if (!wineAreas.includes(areaFilter)) return false;
      }

      if (q) {
        const terms = q.split(/\s+/).filter(Boolean);
        const hay = this._getWineSearchText(w);
        if (!terms.every((t) => hay.includes(t))) return false;
      }
      return true;
    });

    const inv = this.state.sortInverted ? -1 : 1;
    filtered.sort((a, b) => {
      if (this.state.sortBy === "rating") {
        return inv * (Number(b.vivino_rating || 0) - Number(a.vivino_rating || 0));
      }
      if (this.state.sortBy === "drink_window") {
        return inv * (Number(a.drink_until_year || 9999) - Number(b.drink_until_year || 9999));
      }
      if (this.state.sortBy === "vintage") {
        if (!a.vintage && !b.vintage) return 0;
        if (!a.vintage) return 1;
        if (!b.vintage) return -1;
        return inv * (Number(a.vintage) - Number(b.vintage));
      }
      if (this.state.sortBy === "quantity") {
        return inv * (Number(b.quantity || 0) - Number(a.quantity || 0));
      }
      return inv * String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    });

    return filtered;
  }

  getFilteredTastedWines() {
    const q = (this.state.tastedSearchQuery || "").trim().toLowerCase();
    const tab = this.state.tastedFilterTab || "all";

    const filtered = this.state.wines.filter((w) => {
      if (!this._isWineTasted(w)) return false;
      const qty = Number(w.quantity || 0);
      if (tab === "in_cellar" && qty <= 0) return false;
      if (tab === "out_of_stock" && qty > 0) return false;
      if (["red", "white", "rose", "sparkling"].includes(tab) && w.wine_type !== tab) {
        return false;
      }

      if (q) {
        const terms = q.split(/\s+/).filter(Boolean);
        const hay = this._getWineSearchText(w);
        if (!terms.every((t) => hay.includes(t))) return false;
      }
      return true;
    });

    const inv = this.state.tastedSortInverted ? -1 : 1;
    filtered.sort((a, b) => {
      if (this.state.tastedSortBy === "rating") {
        return inv * (Number(b.vivino_rating || 0) - Number(a.vivino_rating || 0));
      }
      if (this.state.tastedSortBy === "personal_rating") {
        return inv * (Number(b.personal_rating || 0) - Number(a.personal_rating || 0));
      }
      if (this.state.tastedSortBy === "vintage") {
        if (!a.vintage && !b.vintage) return 0;
        if (!a.vintage) return 1;
        if (!b.vintage) return -1;
        return inv * (Number(a.vintage) - Number(b.vintage));
      }
      if (this.state.tastedSortBy === "tasted_count") {
        const aCnt = Number(a.tasted_bottles || a.tasted_count || 1);
        const bCnt = Number(b.tasted_bottles || b.tasted_count || 1);
        return inv * (bCnt - aCnt);
      }
      const aDate = String(a.last_tasted_at || a.updated_at || "");
      const bDate = String(b.last_tasted_at || b.updated_at || "");
      return inv * bDate.localeCompare(aDate);
    });

    return filtered;
  }

  async openDetail(wineId) {
    const summaryWine = this.state.wines.find((w) => Number(w.id) === Number(wineId));
    if (!summaryWine) return;
    let fullWine = summaryWine;
    try {
      const res = await this.apiGet("detail", { wine_id: wineId });
      if (res.wine) fullWine = res.wine;
    } catch (_) {}

    this.state.detailWine = fullWine;
    this.state.detailForm = {
      winery: fullWine.winery || "",
      name: fullWine.name || "",
      varietal: fullWine.varietal || "",
      vintage: fullWine.vintage ?? "",
      country: fullWine.country || "",
      region: fullWine.region || "",
      storage_location:
        fullWine.storage_location ||
        fullWine.last_storage_location ||
        (fullWine.active_storage_locations || [])[0] ||
        "",
      purchase_location: fullWine.purchase_location || fullWine.last_purchase_location || "",
      purchase_price:
        fullWine.purchase_price !== null && fullWine.purchase_price !== undefined
          ? fullWine.purchase_price
          : fullWine.last_purchase_price ?? "",
      general_notes: fullWine.general_notes || "",
      drink_from_year: fullWine.drink_from_year ?? "",
      drink_until_year: fullWine.drink_until_year ?? "",
      drink_window_note: fullWine.drink_window_note || "",
      vivino_rating: fullWine.vivino_rating ?? "",
      vivino_url: fullWine.vivino_url || "",
      personal_rating: fullWine.personal_rating ?? "",
    };
    this.state.researchError = "";
    this.state.vivinoLinkError = "";
    this.state.activeSheet = "detail";
    this.render();
  }

  async adjustBottleStock(wine, delta) {
    const cur = Number(wine.quantity || 0);
    if (delta < 0 && cur <= 0) return;
    if (delta < 0 && cur === 1) {
      const ok = await this.confirmAction({
        title: "Last bottle",
        message: `Reducing to 0 marks "${wine.winery ? wine.winery + " – " : ""}${wine.name}" as finished. It stays in your Tasted section and can be restocked anytime.`,
        confirmLabel: "Yes, set to 0",
        danger: true,
      });
      if (!ok) return;
    }
    try {
      const res = await this.apiPost("adjust", { wine_id: Number(wine.id), delta });
      if (res.wine && this.state.detailWine && Number(this.state.detailWine.id) === Number(wine.id)) {
        this.state.detailWine = res.wine;
      }
      await this.fetchState();
      this.showToast(delta > 0 ? "Added +1 bottle" : "Opened 1 bottle (-1) • Added to Tasted");
    } catch (err) {
      this.showError("Failed to update stock", err);
    }
  }

  async _handlePhotoUpload(file, wine) {
    if (!file || !wine) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      const dataUrl = ev.target?.result;
      if (!dataUrl) return;
      try {
        this.showToast("Uploading bottle photo…");
        const res = await this.apiPost("update", {
          wine_id: wine.id,
          updates: { image_data: dataUrl },
        });
        if (res && res.wine) {
          const updated = res.wine;
          const idx = this.state.wines.findIndex((x) => x.id === wine.id);
          if (idx >= 0) this.state.wines[idx] = updated;
          if (this.state.detailWine && this.state.detailWine.id === wine.id) {
            this.state.detailWine = updated;
          }
          if (this.state.previewWine && this.state.previewWine.id === wine.id) {
            this.state.previewWine = updated;
          }
          this.showToast("Bottle photo updated!");
          this.render();
        }
      } catch (err) {
        this.showError("Failed to upload photo", err);
      }
    };
    reader.readAsDataURL(file);
  }

  async handleInstallClick() {
    if (this.state.deferredPrompt) {
      await this.state.deferredPrompt.prompt();
      const choice = await this.state.deferredPrompt.userChoice;
      if (choice?.outcome === "accepted") {
        this.state.isInstalled = true;
        this.state.deferredPrompt = null;
      }
      this.render();
      return;
    }
    this.state.activeSheet = "install_guide";
    this.render();
  }

  /* ================= Focus-safe search update ================= */
  refreshBottleListOnly() {
    const listContainer = this.root.querySelector("#mobile-bottle-list");
    if (!listContainer) {
      this.render();
      return;
    }
    listContainer.innerHTML = this._renderBottleListItems(this.getFilteredWines());
    this._bindBottleCardEvents(listContainer);
    const clearBtn = this.root.querySelector("#btn-mobile-clear-search");
    if (clearBtn) clearBtn.style.visibility = this.state.searchQuery ? "visible" : "hidden";
  }

  refreshTastedBottleListOnly() {
    const listContainer = this.root.querySelector("#mobile-tasted-bottle-list");
    if (!listContainer) {
      this.render();
      return;
    }
    listContainer.innerHTML = this._renderBottleListItems(this.getFilteredTastedWines(), { tastedMode: true });
    this._bindBottleCardEvents(listContainer);
    const clearBtn = this.root.querySelector("#btn-mobile-clear-tasted-search");
    if (clearBtn) clearBtn.style.visibility = this.state.tastedSearchQuery ? "visible" : "hidden";
  }

  /* ================= Render Views ================= */

  _renderBottleListItems(wines, options = {}) {
    const tastedMode = Boolean(options.tastedMode);
    if (this.state.loading && wines.length === 0) {
      return `<div class="panel-card" style="text-align:center; padding:28px;">Loading your ${tastedMode ? "tasted wines" : "wine cellar"}…</div>`;
    }
    if (wines.length === 0) {
      const activeQuery = tastedMode ? this.state.tastedSearchQuery : this.state.searchQuery;
      return `
        <div class="panel-card" style="text-align:center; padding:28px 16px;">
          <div style="font-size:32px; margin-bottom:6px;">${tastedMode ? "🥂" : "🍷"}</div>
          <strong>${tastedMode ? "No tasted wines yet" : "No matching bottles found"}</strong>
          <p style="font-size:13px; color:var(--text-muted); margin:6px 0 14px;">
            ${
              activeQuery
                ? `Nothing matched “${esc(activeQuery)}”.`
                : tastedMode
                  ? "Scan a wine you drank (without adding to cellar stock) or open a bottle from your cellar."
                  : "Tap Scan in the bottom bar to snap a wine label."
            }
          </p>
          <button class="btn btn-primary" data-open-scan="${tastedMode ? "tasted" : "1"}">
            📷 ${tastedMode ? "Scan Wine to Tasted" : "Scan Bottle Label"}
          </button>
        </div>
      `;
    }

    return wines
      .map((w) => {
        const typeLabel = WINE_TYPES[w.wine_type] || "Wine";
        const drinkMeta = DRINK_META[w.drink_status] || DRINK_META.unknown;
        const area =
          (w.active_storage_locations || [])[0] || w.storage_location || w.last_storage_location || "";
        const rating = w.vivino_rating ? Number(w.vivino_rating).toFixed(1) : null;
        const boughtDateRaw = w.latest_added_at || w.created_at || "";
        const boughtDateLabel = boughtDateRaw ? fmtDate(boughtDateRaw) : "";
        const tastedDateRaw = w.last_tasted_at || w.updated_at || boughtDateRaw;
        const tastedDateLabel = tastedDateRaw ? fmtDate(tastedDateRaw) : "";
        const tastedQty = Math.max(1, Number(w.tasted_bottles || w.tasted_count || 1));
        const inCellarQty = Number(w.quantity || 0);
        const store = w.purchase_location || w.last_purchase_location || "";
        const price =
          w.purchase_price !== null && w.purchase_price !== undefined && w.purchase_price !== ""
            ? w.purchase_price
            : w.last_purchase_price !== null && w.last_purchase_price !== undefined && w.last_purchase_price !== ""
              ? w.last_purchase_price
              : null;
        const originText = [w.country, w.region].filter(Boolean).join(" • ");
        const drinkWindowYears =
          w.drink_from_year || w.drink_until_year
            ? ` (${w.drink_from_year || "now"}–${w.drink_until_year || "…"})`
            : "";

        return `
          <div class="bottle-card ${tastedMode ? "bottle-card-tasted" : ""}" data-wine-id="${w.id}">
            <div class="bottle-thumb-wrap" data-detail-id="${w.id}" title="Tap to view wine details">
              ${
                safeImageUrl(w.image_url)
                  ? `<img src="${safeImageUrl(w.image_url)}" alt="${esc(w.name)}" />`
                  : `<span style="font-size:24px;">${tastedMode ? "🥂" : "🍷"}</span>`
              }
            </div>
            <div class="bottle-card-main" data-detail-id="${w.id}">
              <div class="bottle-meta-top">
                ${
                  tastedMode
                    ? `<span class="pill pill-tasted" title="Times tasted / consumed">🥂 Tasted ${tastedQty}×</span>
                       ${
                         inCellarQty > 0
                           ? `<span class="pill pill-qty" title="Bottles still in cellar">In Cellar: ${inCellarQty}×</span>`
                           : `<span class="pill pill-vintage" title="Not currently in cellar">0 in Cellar</span>`
                       }`
                    : `<span class="pill pill-qty" title="Bottles in cellar">${inCellarQty}×</span>
                       ${
                         this._isWineTasted(w)
                           ? `<span class="pill pill-tasted" title="Previously tasted">🥂 Tasted</span>`
                           : ""
                       }`
                }
                <span class="pill pill-${esc(w.wine_type)}">${esc(typeLabel)}</span>
                <span class="pill pill-vintage">📅 ${esc(w.vintage || "NV")}</span>
                ${w.is_imported ? `<span class="pill pill-imported" title="Imported wine" style="background:rgba(217,119,6,0.12); color:#b45309; border:1px solid rgba(217,119,6,0.3); font-weight:600;">📥 Imported</span>` : ""}
                ${area && (!tastedMode || inCellarQty > 0) ? `<span class="pill pill-area">📍 ${esc(area)}</span>` : ""}
                ${originText ? `<span class="pill pill-vintage sub-origin">🌍 ${esc(originText)}</span>` : ""}
              </div>
              <div class="bottle-winery">${esc(w.winery)}</div>
              <div class="bottle-name">${esc(w.name)}</div>
              <div class="bottle-detail-chips">
                ${w.varietal ? `<span class="meta-chip" title="Grape Variety">🍇 ${esc(w.varietal)}</span>` : ""}
                ${
                  tastedMode && tastedDateLabel
                    ? `<span class="meta-chip" title="Last Tasted Date">🥂 ${esc(tastedDateLabel)}</span>`
                    : boughtDateLabel
                      ? `<span class="meta-chip" title="Bought / Added Date">🗓️ ${esc(boughtDateLabel)}</span>`
                      : ""
                }
                ${store ? `<span class="meta-chip" title="Bought At">🛒 ${esc(store)}</span>` : ""}
                ${price !== null ? `<span class="meta-chip" title="Purchase Price">💰 ${esc(price)} ${esc(this.state.currency)}</span>` : ""}
              </div>
              <div class="bottle-subline">
                ${
                  rating
                    ? safeExternalUrl(w.vivino_url)
                      ? `<a class="vivino-tag" href="${safeExternalUrl(w.vivino_url)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation();">★ ${esc(rating)} ↗</a>`
                      : `<span class="vivino-tag">★ ${esc(rating)}</span>`
                    : ""
                }
                ${w.personal_rating ? `<span class="meta-chip" style="color:var(--brand);">★ ${esc(w.personal_rating)}/5</span>` : ""}
                <span style="color:${drinkMeta.color}; font-weight:600; font-size:11.5px;">● ${esc(drinkMeta.label)}${esc(drinkWindowYears)}</span>
              </div>
            </div>
          </div>
        `;
      })
      .join("");
  }

  _renderCellarView() {
    const stats = this.state.statistics || {};
    const wines = this.getFilteredWines();
    const tastedTotal =
      stats.tasted_wines ?? this.state.wines.filter((w) => this._isWineTasted(w)).length;
    const tabs = [
      { id: "all", label: `In Stock (${stats.total_bottles || 0})` },
      { id: "ready", label: `Ready (${stats.ready_to_drink_bottles || 0})` },
      { id: "drink_soon", label: `Soon (${stats.drink_soon_bottles || 0})` },
      { id: "keep", label: `Keep (${stats.keep_bottles || stats.hold_bottles || 0})` },
      { id: "past_peak", label: `Past Peak (${stats.past_peak_bottles || 0})` },
      { id: "red", label: `Red (${stats.by_type?.red || 0})` },
      { id: "white", label: `White (${stats.by_type?.white || 0})` },
      { id: "rose", label: `Rosé (${stats.by_type?.rose || 0})` },
      { id: "sparkling", label: `Sparkling (${stats.by_type?.sparkling || 0})` },
      { id: "tasted", label: `Tasted (${tastedTotal})` },
    ];

    return `
      ${
        !this.state.isInstalled && !this.state.installBannerDismissed
          ? `<div class="install-banner">
              <div class="install-banner-text">
                <strong>📲 Install HomeCave as an App</strong>
                <span>Launch full-screen from your phone’s Home Screen</span>
              </div>
              <div style="display:flex; gap:6px; align-items:center;">
                <button class="btn btn-primary" id="btn-banner-install" style="padding:8px 14px;">Install</button>
                <button class="btn btn-icon" id="btn-banner-dismiss" aria-label="Dismiss">✕</button>
              </div>
            </div>`
          : ""
      }

      <div class="search-filter-dock">
        <div class="search-row">
          <div class="search-box">
            <span class="search-box-icon">🔍</span>
            <input
              id="mobile-search-input"
              class="search-input"
              type="text"
              inputmode="search"
              enterkeyhint="search"
              autocomplete="off"
              autocorrect="off"
              autocapitalize="off"
              spellcheck="false"
              placeholder="Search wine, grape, region, area…"
              value="${esc(this.state.searchQuery)}"
            />
            <button
              type="button"
              id="btn-mobile-clear-search"
              class="search-clear-btn"
              style="visibility: ${this.state.searchQuery ? "visible" : "hidden"};"
              aria-label="Clear search"
            >✕</button>
          </div>
          <select id="mobile-sort-select" class="sort-select" aria-label="Sort wines">
            <option value="recent" ${this.state.sortBy === "recent" ? "selected" : ""}>Recent</option>
            <option value="rating" ${this.state.sortBy === "rating" ? "selected" : ""}>★ Rating</option>
            <option value="drink_window" ${this.state.sortBy === "drink_window" ? "selected" : ""}>Drink Soon</option>
            <option value="vintage" ${this.state.sortBy === "vintage" ? "selected" : ""}>Vintage</option>
            <option value="quantity" ${this.state.sortBy === "quantity" ? "selected" : ""}>Stock</option>
          </select>
          <button
            type="button"
            id="btn-mobile-invert-sort"
            class="sort-invert-btn ${this.state.sortInverted ? "active" : ""}"
            aria-label="Invert sort order"
            title="${this.state.sortInverted ? "Inverted: Newest / Lowest / Furthest" : "Default: Oldest / Highest / Urgent"}"
            style="min-height:44px; min-width:44px; padding:0 8px; border-radius:10px; border:1px solid var(--border); background:${this.state.sortInverted ? "var(--brand-soft)" : "var(--surface)"}; color:${this.state.sortInverted ? "var(--brand)" : "var(--text)"}; font-weight:700; display:flex; align-items:center; justify-content:center; font-size:15px; cursor:pointer;"
          >
            ${this.state.sortInverted ? "⇅ ▾" : "⇅ ▴"}
          </button>
        </div>

        ${
          this.state.areaFilter
            ? `<div style="margin-bottom:8px; display:flex; align-items:center; justify-content:space-between; padding:6px 12px; border-radius:10px; background:var(--brand-soft); color:var(--brand); font-size:12.5px; font-weight:700;">
                <span>📍 Filtered by Area: ${esc(this.state.areaFilter)}</span>
                <button class="btn" id="btn-clear-area-filter" style="min-height:40px; padding:4px 12px; font-size:13px;">Show All</button>
              </div>`
            : ""
        }

        <div class="filter-pills-scroll">
          ${tabs
            .map(
              (t) => `
              <button class="filter-pill ${this.state.filterTab === t.id ? "active" : ""}" data-tab="${t.id}">
                ${esc(t.label)}
              </button>
            `
            )
            .join("")}
        </div>
      </div>

      <div id="mobile-bottle-list" class="bottle-list">
        ${this._renderBottleListItems(wines, { tastedMode: this.state.filterTab === "tasted" || this.state.filterTab === "archived" })}
      </div>
    `;
  }

  _renderTastedView() {
    const allTasted = this.state.wines.filter((w) => this._isWineTasted(w));
    const wines = this.getFilteredTastedWines();
    const tastedByType = { red: 0, white: 0, rose: 0, sparkling: 0 };
    let alsoInCellar = 0;
    let notInCellar = 0;
    allTasted.forEach((w) => {
      const t = w.wine_type || "red";
      if (tastedByType[t] !== undefined) tastedByType[t] += 1;
      if (Number(w.quantity || 0) > 0) alsoInCellar += 1;
      else notInCellar += 1;
    });

    const tabs = [
      { id: "all", label: `All Tasted (${allTasted.length})` },
      { id: "out_of_stock", label: `Not in Cellar (${notInCellar})` },
      { id: "in_cellar", label: `Also in Cellar (${alsoInCellar})` },
      { id: "red", label: `Red (${tastedByType.red})` },
      { id: "white", label: `White (${tastedByType.white})` },
      { id: "rose", label: `Rosé (${tastedByType.rose})` },
      { id: "sparkling", label: `Sparkling (${tastedByType.sparkling})` },
    ];

    return `
      <div class="tasted-view-banner">
        <div class="tasted-banner-main">
          <strong>🥂 Tasted &amp; Consumed Wines</strong>
          <span>Wines you scanned as Tasted or drank from your cellar (${allTasted.length} total)</span>
        </div>
      </div>

      <div class="search-filter-dock">
        <div class="search-row">
          <div class="search-box">
            <span class="search-box-icon">🔍</span>
            <input
              id="mobile-tasted-search-input"
              class="search-input"
              type="text"
              inputmode="search"
              enterkeyhint="search"
              autocomplete="off"
              autocorrect="off"
              autocapitalize="off"
              spellcheck="false"
              placeholder="Search tasted wines, grape, region…"
              value="${esc(this.state.tastedSearchQuery)}"
            />
            <button
              type="button"
              id="btn-mobile-clear-tasted-search"
              class="search-clear-btn"
              style="visibility: ${this.state.tastedSearchQuery ? "visible" : "hidden"};"
              aria-label="Clear search"
            >✕</button>
          </div>
          <select id="mobile-tasted-sort-select" class="sort-select" aria-label="Sort tasted wines">
            <option value="recent_tasted" ${this.state.tastedSortBy === "recent_tasted" ? "selected" : ""}>Recent</option>
            <option value="rating" ${this.state.tastedSortBy === "rating" ? "selected" : ""}>★ Vivino</option>
            <option value="personal_rating" ${this.state.tastedSortBy === "personal_rating" ? "selected" : ""}>★ My Rating</option>
            <option value="vintage" ${this.state.tastedSortBy === "vintage" ? "selected" : ""}>Vintage</option>
            <option value="tasted_count" ${this.state.tastedSortBy === "tasted_count" ? "selected" : ""}>Times Tasted</option>
          </select>
          <button
            type="button"
            id="btn-mobile-tasted-invert-sort"
            class="sort-invert-btn ${this.state.tastedSortInverted ? "active" : ""}"
            aria-label="Invert tasted sort order"
            title="${this.state.tastedSortInverted ? "Inverted" : "Default"}"
            style="min-height:44px; min-width:44px; padding:0 8px; border-radius:10px; border:1px solid var(--border); background:${this.state.tastedSortInverted ? "var(--brand-soft)" : "var(--surface)"}; color:${this.state.tastedSortInverted ? "var(--brand)" : "var(--text)"}; font-weight:700; display:flex; align-items:center; justify-content:center; font-size:15px; cursor:pointer;"
          >
            ${this.state.tastedSortInverted ? "⇅ ▾" : "⇅ ▴"}
          </button>
        </div>

        <div class="filter-pills-scroll">
          ${tabs
            .map(
              (t) => `
              <button class="filter-pill ${this.state.tastedFilterTab === t.id ? "active" : ""}" data-tasted-tab="${t.id}">
                ${esc(t.label)}
              </button>
            `
            )
            .join("")}
        </div>
      </div>

      <div id="mobile-tasted-bottle-list" class="bottle-list">
        ${this._renderBottleListItems(wines, { tastedMode: true })}
      </div>
    `;
  }

  /** Count helper: returns top-N [label, bottles] entries for a key function. */
  _topCounts(wines, keyFn, limit = 5) {
    const counts = {};
    wines.forEach((w) => {
      const keys = keyFn(w);
      (Array.isArray(keys) ? keys : [keys]).forEach((k) => {
        const label = String(k || "").trim();
        if (!label) return;
        counts[label] = (counts[label] || 0) + Number(w.quantity || 0);
      });
    });
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit);
  }

  _renderBarList(entries, total, emptyText) {
    if (!entries.length) {
      return `<div class="stat-empty">${esc(emptyText)}</div>`;
    }
    return `<div class="bar-list">
      ${entries
        .map(([label, count]) => {
          const pct = total ? Math.round((count / total) * 100) : 0;
          return `
            <div class="bar-row">
              <div class="bar-row-head">
                <span class="bar-label">${esc(label)}</span>
                <span class="bar-value">${count} <small>(${pct}%)</small></span>
              </div>
              <div class="taste-bar"><div class="taste-bar-fill" style="width:${Math.max(pct, 2)}%; background:var(--brand);"></div></div>
            </div>`;
        })
        .join("")}
    </div>`;
  }

  _renderStatsView() {
    this.state.activeNav = "config";
    return this._renderConfigView();
  }

  _renderStatsSection() {
    const stats = this.state.statistics || {};
    const cur = esc(this.state.currency);
    const inStock = this.state.wines.filter((w) => Number(w.quantity || 0) > 0);
    const totalBottles = inStock.reduce((s, w) => s + Number(w.quantity || 0), 0);
    const nowYear = new Date().getFullYear();

    // Price & value (real data only; "—" when unknown)
    const priced = inStock
      .map((w) => ({ w, p: w.purchase_price ?? w.last_purchase_price }))
      .filter((x) => x.p !== null && x.p !== undefined && x.p !== "" && !Number.isNaN(Number(x.p)));
    const pricedBottles = priced.reduce((s, x) => s + Number(x.w.quantity || 0), 0);
    const avgPrice = pricedBottles
      ? priced.reduce((s, x) => s + Number(x.p) * Number(x.w.quantity || 0), 0) / pricedBottles
      : null;
    const value = stats.estimated_value ?? stats.estimated_cellar_value ?? null;

    // Ratings
    const rated = inStock.filter((w) => w.vivino_rating);
    const avgRating = rated.length
      ? rated.reduce((s, w) => s + Number(w.vivino_rating), 0) / rated.length
      : null;
    const topRated = rated.slice().sort((a, b) => Number(b.vivino_rating) - Number(a.vivino_rating))[0];

    // Vintages
    const vintages = inStock.map((w) => Number(w.vintage)).filter((v) => v > 1800);
    const oldest = vintages.length ? Math.min(...vintages) : null;
    const newest = vintages.length ? Math.max(...vintages) : null;
    const avgAge = vintages.length
      ? Math.round((vintages.reduce((s, v) => s + (nowYear - v), 0) / vintages.length) * 10) / 10
      : null;

    // Drinking window buckets
    const windowCounts = { ready: 0, drink_soon: 0, hold: 0, past_peak: 0, unknown: 0 };
    inStock.forEach((w) => {
      const k = windowCounts[w.drink_status] !== undefined ? w.drink_status : "unknown";
      windowCounts[k] += Number(w.quantity || 0);
    });
    const drinkFirst = inStock
      .filter((w) => w.drink_until_year)
      .sort((a, b) => Number(a.drink_until_year) - Number(b.drink_until_year))[0];

    // Activity
    const cutoff = Date.now() - 30 * 24 * 3600 * 1000;
    const recentlyAdded = this.state.wines
      .filter((w) => w.latest_added_at || w.created_at)
      .sort((a, b) =>
        String(b.latest_added_at || b.created_at).localeCompare(String(a.latest_added_at || a.created_at))
      );
    const addedLast30 = recentlyAdded
      .filter((w) => new Date(w.latest_added_at || w.created_at).getTime() >= cutoff)
      .reduce((s, w) => s + Math.max(Number(w.quantity || 0), 1), 0);

    const areaEntries = this._topCounts(
      inStock,
      (w) => (w.active_storage_locations || [])[0] || w.storage_location || w.last_storage_location || "Unassigned Area",
      50
    );
    const countryEntries = this._topCounts(inStock, (w) => w.country);
    const grapeEntries = this._topCounts(inStock, (w) =>
      String(w.varietal || "")
        .split(/[,/&+]| and /i)
        .map((g) => g.trim())
    );
    const storeEntries = this._topCounts(inStock, (w) => w.purchase_location || w.last_purchase_location);

    const kpi = (value, label, color = "") => `
      <div class="kpi-card">
        <div class="kpi-value" ${color ? `style="color:${color};"` : ""}>${value}</div>
        <div class="kpi-label">${esc(label)}</div>
      </div>`;

    const wineLink = (w, sub) =>
      w
        ? `<button class="highlight-row" data-detail-id="${w.id}">
            <span class="highlight-main">
              <strong>${esc(w.winery)} – ${esc(w.name)}</strong>
              <small>${esc(sub)}</small>
            </span>
            <span class="highlight-chevron">›</span>
          </button>`
        : `<div class="stat-empty">—</div>`;

    return `
        <div class="kpi-grid">
          ${kpi(stats.total_bottles ?? totalBottles, "Bottles in Cellar")}
          ${kpi(stats.unique_wines ?? inStock.length, "Unique Wines")}
          ${kpi(value ? `${Math.round(value)} ${cur}` : "—", "Estimated Value")}
          ${kpi(avgPrice !== null ? `${avgPrice.toFixed(2)} ${cur}` : "—", "Avg. Price / Bottle")}
          ${kpi(avgRating !== null ? `★ ${avgRating.toFixed(2)}` : "—", `Avg. Vivino (${rated.length} rated)`)}
          ${kpi(stats.tasted_wines ?? stats.archived_wines ?? 0, "Tasted Wines")}
        </div>

        <div class="panel-card">
          <h3>🍷 Drinking Window</h3>
          ${this._renderBarList(
            [
              ["Ready to Drink", windowCounts.ready],
              ["Drink Soon", windowCounts.drink_soon],
              ["Hold / Aging", windowCounts.hold],
              ["Past Peak", windowCounts.past_peak],
              ["No Window Set", windowCounts.unknown],
            ].filter((e) => e[1] > 0),
            totalBottles,
            "No bottles in stock yet."
          )}
        </div>

        <div class="panel-card">
          <h3>⭐ Highlights</h3>
          <div class="highlight-label">Highest rated</div>
          ${wineLink(topRated, topRated ? `★ ${Number(topRated.vivino_rating).toFixed(1)} on Vivino` : "")}
          <div class="highlight-label">Drink first</div>
          ${wineLink(drinkFirst, drinkFirst ? `Drink by ${drinkFirst.drink_until_year}` : "")}
        </div>

        <div class="panel-card">
          <h3>📅 Vintages</h3>
          <div class="kpi-grid kpi-grid-3">
            ${kpi(oldest ?? "—", "Oldest")}
            ${kpi(newest ?? "—", "Newest")}
            ${kpi(avgAge !== null ? `${avgAge} y` : "—", "Avg. Age")}
          </div>
        </div>

        <div class="panel-card">
          <h3>🍾 Style Breakdown</h3>
          ${this._renderBarList(
            Object.entries(WINE_TYPES)
              .map(([k, label]) => [label, stats.by_type?.[k] || 0])
              .filter((e) => e[1] > 0),
            stats.total_bottles || totalBottles,
            "No bottles in stock yet."
          )}
        </div>

        <div class="panel-card">
          <h3>🌍 Top Countries</h3>
          ${this._renderBarList(countryEntries, totalBottles, "No countries recorded yet.")}
        </div>

        <div class="panel-card">
          <h3>🍇 Top Grapes</h3>
          ${this._renderBarList(grapeEntries, totalBottles, "No grape varieties recorded yet.")}
        </div>

        <div class="panel-card">
          <h3>🛒 Top Stores</h3>
          ${this._renderBarList(storeEntries, totalBottles, "No purchase locations recorded yet.")}
        </div>

        <div class="panel-card">
          <h3>🕒 Activity</h3>
          <div class="kpi-grid">
            ${kpi(addedLast30, "Bottles added (30 days)")}
            ${kpi(windowCounts.past_peak, "Past peak", windowCounts.past_peak ? "var(--danger)" : "")}
          </div>
          <div class="highlight-label" style="margin-top:12px;">Recently added</div>
          ${
            recentlyAdded.length
              ? recentlyAdded
                  .slice(0, 3)
                  .map((w) => wineLink(w, fmtDate(w.latest_added_at || w.created_at)))
                  .join("")
              : `<div class="stat-empty">—</div>`
          }
        </div>

        <div class="panel-card">
          <h3>
            <span>📍 Storage Areas</span>
            <small class="panel-hint">Tap to view bottles</small>
          </h3>
          ${
            areaEntries.length === 0
              ? `<div class="stat-empty">No bottles assigned to storage areas yet.</div>`
              : `<div class="area-grid">
                  ${areaEntries
                    .map(
                      ([loc, count]) => `
                      <button class="area-tile" data-jump-area="${esc(loc === "Unassigned Area" ? "" : loc)}">
                        <strong>${esc(loc)}</strong>
                        <span>${count} ${count === 1 ? "bottle" : "bottles"} →</span>
                      </button>`
                    )
                    .join("")}
                </div>`
          }
        </div>
    `;
  }

  _renderManualAddView() {
    const f = this.state.scanForm;
    return `
      <div class="section-wrap">
        <div class="panel-card">
          <h3>➕ Add Wine to Cellar</h3>
          <form id="mobile-manual-add-form" class="form-stack">
            <div class="form-row-2">
              <div class="field">
                <label>Winery / Producer *</label>
                <input class="input" name="winery" required value="${esc(f.winery)}" placeholder="e.g. Château Margaux" />
              </div>
              <div class="field">
                <label>Wine / Cuvée Name *</label>
                <input class="input" name="name" required value="${esc(f.name)}" placeholder="e.g. Pavillon Rouge" />
              </div>
            </div>
            <div class="form-row-2">
              <div class="field">
                <label>Harvest Year / Vintage</label>
                <input class="input" type="number" inputmode="numeric" pattern="[0-9]*" name="vintage" value="${esc(f.vintage)}" placeholder="e.g. 2019 (blank = NV)" />
              </div>
              <div class="field">
                <label>Wine Style</label>
                <select class="select" name="wine_type">
                  ${Object.entries(WINE_TYPES)
                    .map(([k, lbl]) => `<option value="${k}" ${f.wine_type === k ? "selected" : ""}>${lbl}</option>`)
                    .join("")}
                </select>
              </div>
            </div>
            <div class="form-row-2">
              <div class="field">
                <label>Grape Variety</label>
                <input class="input" name="varietal" value="${esc(f.varietal)}" placeholder="e.g. Pinot Noir, Syrah" />
              </div>
              <div class="field">
                <label>Country &amp; Region</label>
                <div class="form-row-2">
                  <input class="input" name="country" value="${esc(f.country)}" placeholder="Country" />
                  <input class="input" name="region" value="${esc(f.region)}" placeholder="Region" />
                </div>
              </div>
            </div>
            <div class="form-row-2">
              <div class="field">
                <label>Storage Area / Location</label>
                <input class="input" name="storage_location" list="mobile-areas-dl" value="${esc(f.storage_location)}" placeholder="e.g. HomeCave / Wine Fridge" />
                <datalist id="mobile-areas-dl">
                  ${this.state.areas.map((a) => `<option value="${esc(a)}"></option>`).join("")}
                </datalist>
              </div>
              <div class="field">
                <label>Bottles Added &amp; Price (${esc(this.state.currency)})</label>
                <div class="form-row-2">
                  <input class="input" type="number" inputmode="numeric" pattern="[0-9]*" name="quantity" min="1" required value="${esc(f.quantity || 1)}" />
                  <input class="input" type="number" inputmode="decimal" step="0.01" name="purchase_price" value="${esc(f.purchase_price)}" placeholder="Price" />
                </div>
              </div>
            </div>
            <div class="form-row-2">
              <div class="field">
                <label>Bought At (Store / Winery)</label>
                <input class="input" name="purchase_location" value="${esc(f.purchase_location)}" placeholder="e.g. Gall & Gall, Domain" />
              </div>
              <div class="field">
                <label>Drink Window (From – Until)</label>
                <div class="form-row-2">
                  <input class="input" type="number" inputmode="numeric" pattern="[0-9]*" name="drink_from_year" value="${esc(f.drink_from_year)}" placeholder="From" />
                  <input class="input" type="number" inputmode="numeric" pattern="[0-9]*" name="drink_until_year" value="${esc(f.drink_until_year)}" placeholder="Until" />
                </div>
              </div>
            </div>
            <div class="field">
              <label>Notes</label>
              <textarea class="textarea" name="general_notes" placeholder="Aromas, food pairing ideas, gift note…">${esc(f.general_notes)}</textarea>
            </div>
            <button type="submit" class="btn btn-primary" style="width:100%;">Save Wine to Cellar</button>
          </form>
        </div>
      </div>
    `;
  }

  _renderConfigView() {
    const scaleLabel = this._fontScaleLabel();
    const backups = this.state.backups || [];
    return `
      <div class="section-wrap">
        <h2 class="view-title">⚙️ Config</h2>
        <div class="panel-card">
          <h3>📲 Install HomeCave App</h3>
          <p style="font-size:13px; color:var(--text-muted); margin:0 0 12px; line-height:1.45;">
            ${
              this.state.isInstalled
                ? "✅ You are running HomeCave as an installed standalone mobile app."
                : "Install this mobile page to your phone’s Home Screen so it launches full-screen like a native app alongside Home Assistant."
            }
          </p>
          ${
            this.state.isInstalled
              ? `<div class="pill pill-area" style="display:inline-block; padding:6px 12px; font-size:12.5px;">✓ Installed Standalone Mode Active</div>`
              : `<button class="btn btn-primary btn-lg" id="btn-settings-install" style="width:100%;">
                  📲 Install App to Home Screen
                </button>`
          }
        </div>

        <div class="panel-card">
          <h3>Aa Text Size (Applies to All Views &amp; Wine Details)</h3>
          <div class="segmented-row">
            ${FONT_SCALES.map(
              (s) => `
              <button class="seg-btn ${scaleLabel === s.id ? "active" : ""}" data-set-scale="${s.value}">
                ${s.id} <small style="font-weight:500; font-size:11px;">${Math.round(s.value * 100)}%</small>
              </button>
            `
            ).join("")}
          </div>
        </div>

        <div class="panel-card">
          <h3>🎨 Appearance</h3>
          <div class="segmented-row" style="grid-template-columns: repeat(3, 1fr);">
            <button class="seg-btn ${this.state.theme === "auto" ? "active" : ""}" data-set-theme="auto">Auto</button>
            <button class="seg-btn ${this.state.theme === "light" ? "active" : ""}" data-set-theme="light">Light</button>
            <button class="seg-btn ${this.state.theme === "dark" ? "active" : ""}" data-set-theme="dark">Dark</button>
          </div>
        </div>

        <div class="panel-card">
          <h3>📦 Backups &amp; Vivino History Import</h3>
          <div class="form-stack">
            <button type="button" class="btn btn-primary" id="btn-settings-create-backup" style="width:100%;">
              💾 Create &amp; Download Full Backup (.zip)
            </button>
            ${
              backups.length > 0
                ? `<div style="display:flex; gap:8px; align-items:center;">
                    <select id="select-settings-backup" class="select" style="flex:1; min-width:0;">
                      ${backups
                        .map(
                          (b) =>
                            `<option value="${esc(b.filename)}">${esc(b.filename)} (${esc(b.size_mb)} MB)</option>`
                        )
                        .join("")}
                    </select>
                    <button type="button" class="btn btn-soft" id="btn-settings-download-backup" style="flex-shrink:0;">
                      📥 Download
                    </button>
                  </div>`
                : `<div style="font-size:12.5px; color:var(--text-muted);">No backup archives created yet.</div>`
            }
            <label class="btn btn-soft" style="width:100%; cursor:pointer;">
              🍇 Upload Vivino Export (.zip / .csv)
              <input type="file" id="mobile-vivino-csv-file" accept=".zip,.csv,text/csv,application/zip" hidden />
            </label>
          </div>
        </div>

        <div class="panel-card">
          <h3>🍷 Cellar Actions</h3>
          <div class="form-stack">
            <button class="btn" id="btn-settings-refresh-vivino" style="width:100%;">
              🔄 Refresh Verified Vivino Ratings
            </button>
            <a class="btn" href="/" style="width:100%;">
              🏠 Return to Home Assistant UI
            </a>
          </div>
        </div>

        <div class="panel-card" style="margin-top:20px; border-left:4px solid var(--brand);">
          <h3 style="font-size:17px; margin:0 0 6px;">📊 Cellar Statistics &amp; Analytics</h3>
          <p style="font-size:13px; color:var(--text-muted); margin:0;">
            Comprehensive analytics of your active cellar stock, drinking windows, styles, and geography.
          </p>
        </div>
        ${this._renderStatsSection()}
      </div>
    `;
  }

  _renderSommAIView() {
    const inStock = this.state.wines.filter((w) => Number(w.quantity || 0) > 0);
    const inStockCount = inStock.reduce((s, w) => s + Number(w.quantity || 0), 0);
    const messages = this.state.sommAiMessages || [];

    return `
      <div class="section-wrap sommai-view">
        <div class="sommai-header-card">
          <div class="sommai-header-top">
            <div class="sommai-avatar">🍷🤖</div>
            <div class="sommai-title-wrap">
              <h2 class="sommai-title">SommAI Sommelier</h2>
              <div class="sommai-subtitle">AI Food &amp; Wine Pairing • Grounded in Your Cellar</div>
            </div>
          </div>
          <p class="sommai-description">
            Describe your meal or snap a photo of your plate. I will analyze the dish's flavor profile, sauces, and cooking method, and recommend the best pairings directly from your <strong>${inStockCount} cellar bottles in stock</strong>.
          </p>
          <div class="sommai-chips-scroll">
            <span style="font-size:11px; font-weight:700; color:var(--text-muted); text-transform:uppercase; margin-right:4px; align-self:center;">Pair with:</span>
            <button type="button" class="sommai-chip" data-sommai-prompt="Grilled Ribeye Steak with Garlic Herb Butter">🥩 Ribeye Steak</button>
            <button type="button" class="sommai-chip" data-sommai-prompt="Truffle and Porcini Mushroom Tagliatelle">🍝 Truffle Pasta</button>
            <button type="button" class="sommai-chip" data-sommai-prompt="Fresh Salmon and Tuna Sashimi">🍣 Salmon Sashimi</button>
            <button type="button" class="sommai-chip" data-sommai-prompt="Pan-Seared Duck Breast with Cherry Glaze">🍗 Duck Breast</button>
            <button type="button" class="sommai-chip" data-sommai-prompt="Artisanal Aged Hard &amp; Soft Cheese Board">🧀 Aged Cheeses</button>
            <button type="button" class="sommai-chip" data-sommai-prompt="Spicy Thai Green Curry with Coconut Milk">🌶️ Thai Green Curry</button>
          </div>
        </div>

        <div class="sommai-chat-thread" id="sommai-chat-thread">
          ${
            messages.length === 0
              ? `<div class="panel-card" style="text-align:center; padding:24px 16px; background:var(--surface-alt);">
                  <span style="font-size:36px; display:block; margin-bottom:8px;">🍽️🍷</span>
                  <strong style="font-size:15px; display:block; margin-bottom:4px;">No pairings requested yet</strong>
                  <span style="font-size:13px; color:var(--text-muted); line-height:1.45;">
                    Tap one of the quick dishes above, type a recipe or ingredients below, or take a picture of what you are eating!
                  </span>
                </div>`
              : messages.map((m) => this._renderSommAiMessage(m)).join("")
          }
          ${
            this.state.sommAiLoading
              ? `<div class="sommai-msg sommai-msg-bot">
                  <div class="sommai-msg-bubble sommai-thinking">
                    <span class="sommai-spin">🍷</span>
                    <span>Sommelier is analyzing cellar inventory &amp; flavor pairing…</span>
                  </div>
                </div>`
              : ""
          }
          ${
            this.state.sommAiError
              ? `<div style="padding:10px 12px; border-radius:10px; background:rgba(198,40,40,0.1); color:var(--danger); font-size:13px;">
                  <strong>Sommelier Error:</strong> ${esc(this.state.sommAiError)}
                </div>`
              : ""
          }
        </div>

        <div class="sommai-input-dock">
          ${
            this.state.sommAiPendingImage
              ? `<div class="sommai-pending-img-bar">
                  <img src="${this.state.sommAiPendingImage}" class="sommai-pending-thumb" alt="Dish photo" />
                  <span class="sommai-pending-label">Dish photo attached</span>
                  <button type="button" class="btn btn-icon btn-sm" id="btn-clear-sommai-img" title="Remove photo">✕</button>
                </div>`
              : ""
          }
          <form id="sommai-chat-form" class="sommai-input-form">
            <input type="file" id="sommai-dish-file" accept="image/*" style="display:none;" />
            <button type="button" class="btn btn-icon sommai-photo-btn" id="btn-sommai-pick-photo" title="Attach dish photo">
              📷
            </button>
            <input
              type="text"
              id="sommai-text-input"
              class="sommai-input-field"
              placeholder="Describe your dish or ingredients..."
              value="${esc(this.state.sommAiInputText)}"
              autocomplete="off"
            />
            <button type="submit" class="btn btn-primary sommai-send-btn" id="btn-sommai-send" ${this.state.sommAiLoading ? "disabled" : ""}>
              Pair 🍷
            </button>
          </form>
        </div>
      </div>
    `;
  }

  _renderSommAiMessage(m) {
    if (m.role === "user") {
      return `
        <div class="sommai-msg sommai-msg-user">
          <div class="sommai-msg-bubble">
            ${m.image_data ? `<img src="${m.image_data}" class="sommai-user-img" alt="Dish photo" />` : ""}
            <div>${esc(m.text || "Pair this dish with wine")}</div>
          </div>
        </div>
      `;
    }

    const recs = m.recommended_wines || [];
    return `
      <div class="sommai-msg sommai-msg-bot">
        <div class="sommai-msg-bubble">
          ${m.dish_name ? `<div class="sommai-dish-title">🍽️ ${esc(m.dish_name)}</div>` : ""}
          <div style="margin-bottom:8px; line-height:1.5;">${esc(m.conversational_response || m.text || "")}</div>
          ${
            m.dish_analysis
              ? `<div class="sommai-analysis-box">
                  <strong style="color:var(--brand); display:block; margin-bottom:2px;">Dish Analysis:</strong>
                  ${esc(m.dish_analysis)}
                </div>`
              : ""
          }
          ${
            m.pairing_reasoning
              ? `<div class="sommai-analysis-box" style="background:rgba(122,40,88,0.06); border-left:3px solid var(--brand);">
                  <strong style="color:var(--brand); display:block; margin-bottom:2px;">Pairing Strategy:</strong>
                  ${esc(m.pairing_reasoning)}
                </div>`
              : ""
          }
          ${
            recs.length > 0
              ? `<div class="sommai-recs-wrap">
                  <div class="sommai-recs-header">🍾 Recommended from Your Cellar</div>
                  ${recs
                    .map((rec) => {
                      const matchLabel =
                        rec.match_type === "best"
                          ? "★ Best Cellar Match"
                          : rec.match_type === "alternative"
                            ? "Alternative Match"
                            : "Cellar Compromise";
                      const matchClass =
                        rec.match_type === "best"
                          ? "ready"
                          : rec.match_type === "alternative"
                            ? "vintage"
                            : "hold";
                      return `
                        <div class="sommai-rec-card" ${rec.wine_id ? `data-detail-id="${rec.wine_id}"` : ""}>
                          <div class="sommai-rec-top">
                            <span class="pill pill-${matchClass}">${matchLabel}</span>
                            ${rec.serving_temp_c ? `<span class="pill pill-vintage">🌡️ ${esc(rec.serving_temp_c)}</span>` : ""}
                            ${rec.decant_minutes ? `<span class="pill pill-vintage">⏳ Decant ${rec.decant_minutes}m</span>` : ""}
                          </div>
                          <div class="sommai-rec-title">
                            <strong>${esc(rec.wine_title)}</strong>
                            <span class="highlight-chevron">›</span>
                          </div>
                          <div class="sommai-rec-notes">${esc(rec.sommelier_notes)}</div>
                        </div>
                      `;
                    })
                    .join("")}
                </div>`
              : ""
          }
        </div>
      </div>
    `;
  }

  /* ================= Slide-Up Sheets ================= */

  _renderImportWarningSheet() {
    const file = this.state.importPendingFile;
    if (!file) return "";

    return `
      <div class="sheet-backdrop" id="sheet-backdrop">
        <div class="sheet sheet-fullscreen">
          <div class="sheet-header">
            <button type="button" class="btn btn-icon btn-back" id="btn-close-import-warning" aria-label="Cancel"><span aria-hidden="true">‹</span></button>
            <h2>🍇 Import Vivino History</h2>
          </div>
          <div class="sheet-body">
            <div class="scaled-zone">
              <div style="text-align:center; padding:16px 0 12px;">
                <span style="font-size:48px;">🍇⏳</span>
                <h3 style="font-size:18px; margin:8px 0 4px;">Ready to Import Vivino Archive</h3>
                <p style="font-size:13px; color:var(--text-muted); margin:0;">
                  File: <strong>${esc(file.name)}</strong> (${(file.size / 1024 / 1024).toFixed(2)} MB)
                </p>
              </div>

              <div class="panel-card" style="background:rgba(245,158,11,0.08); border-left:4px solid #f59e0b; margin-bottom:14px;">
                <div style="font-weight:700; color:#b45309; font-size:13.5px; margin-bottom:4px;">
                  ⚠️ Please Note: Import Duration &amp; AI Analysis
                </div>
                <p style="font-size:13px; color:var(--text); margin:0; line-height:1.5;">
                  Depending on the number of bottles in your export, parsing records, downloading label photos, and retrieving missing sommelier details can take anywhere from <strong>30 seconds to a few minutes</strong>.
                </p>
              </div>

              <div class="panel-card" style="margin-bottom:16px;">
                <label style="display:flex; align-items:flex-start; gap:12px; cursor:pointer;">
                  <input type="checkbox" id="import-enrich-ai-check" ${this.state.importEnrichAi ? "checked" : ""} style="width:20px; height:20px; margin-top:2px; accent-color:var(--brand);" />
                  <div>
                    <strong style="font-size:14px; display:block; margin-bottom:2px;">✨ Enrich with AI</strong>
                    <span style="font-size:12.5px; color:var(--text-muted); line-height:1.4; display:block;">
                      Automatically fills missing grape varietal blends, drinking windows (from/until), and structural taste profile sliders (Soft vs Acidic, Light vs Bold, Dry vs Sweet) for each bottle.
                    </span>
                  </div>
                </label>
              </div>

              <div class="form-stack">
                <button type="button" class="btn btn-primary btn-lg" id="btn-start-vivino-import" style="width:100%;">
                  🚀 Start Import
                </button>
                <button type="button" class="btn btn-lg" id="btn-cancel-vivino-import" style="width:100%;">
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  _renderImportProgressSheet() {
    const p = this.state.importProgressData || {
      running: true,
      progress_pct: 0,
      stage: "parsing",
      status_message: "Preparing import…",
    };
    const pct = Math.max(0, Math.min(100, Math.round(p.progress_pct || 0)));
    const isDone = !p.running && pct >= 100;

    return `
      <div class="sheet-backdrop" id="sheet-backdrop">
        <div class="sheet sheet-fullscreen">
          <div class="sheet-header">
            <button type="button" class="btn btn-icon btn-back" id="${isDone ? "btn-close-import-progress" : "btn-return-cellar-progress"}" aria-label="${isDone ? "Done" : "Return to Cellar"}"><span aria-hidden="true">‹</span></button>
            <h2>${isDone ? "✅ Import Complete" : "🍇 Importing Vivino Data"}</h2>
          </div>
          <div class="sheet-body">
            <div class="scaled-zone">
              <div style="text-align:center; padding:20px 0 16px;">
                <span style="font-size:48px;" class="${isDone ? "" : "sommai-spin"}">${isDone ? "🎉" : "🍇"}</span>
                <h3 style="font-size:19px; margin:8px 0 4px;">${isDone ? "Import Finished Successfully!" : "Processing Your Wine Archive"}</h3>
                <p style="font-size:13px; color:var(--text-muted); margin:0;">
                  ${isDone ? "All wines, tasted records, and bottle photos have been processed." : "Import is running in the background. You can safely return to your cellar."}
                </p>
              </div>

              <div class="panel-card" style="margin-bottom:16px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; font-weight:700; font-size:14px;">
                  <span>${isDone ? "Completed" : "Progress"}</span>
                  <span class="import-progress-pct" style="color:var(--brand);">${pct}%</span>
                </div>
                <div class="import-progress-track">
                  <div class="import-progress-fill" style="width:${pct}%;"></div>
                </div>
                <div class="import-progress-status-text" style="font-size:12.5px; color:var(--text-muted); margin-top:10px; line-height:1.45;">
                  <strong>Status:</strong> ${esc(p.status_message || (isDone ? "Done!" : "Working…"))}
                  ${p.total_bottles ? `<div style="margin-top:2px;">Bottles: ${p.current_bottle || 0} / ${p.total_bottles}</div>` : ""}
                </div>
              </div>

              ${isDone ? `
                <button type="button" class="btn btn-primary btn-lg" id="btn-finish-import-progress" style="width:100%;">
                  🍾 View Cellar &amp; Tasted Wines
                </button>
              ` : `
                <button type="button" class="btn btn-secondary btn-lg" id="btn-return-cellar-import" style="width:100%; margin-top:8px;">
                  ‹ Return to Cellar (runs in background)
                </button>
                <div style="text-align:center; font-size:12.5px; color:var(--text-muted); margin-top:10px;">
                  ⏳ Import will continue processing in the background.
                </div>
              `}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  _renderDetailSheet() {
    const w = this.state.detailWine;
    const df = this.state.detailForm;
    if (!w || !df) return "";

    const typeLabel = WINE_TYPES[w.wine_type] || "Wine";
    const drinkMeta = DRINK_META[w.drink_status] || DRINK_META.unknown;
    const originText = [w.region, w.country].filter(Boolean).join(" • ") || "Origin not specified";
    let research = null;
    if (w.research_notes) {
      try {
        research = typeof w.research_notes === "string" ? JSON.parse(w.research_notes) : w.research_notes;
      } catch (_) {
        research = { summary: String(w.research_notes) };
      }
    }

    return `
      <div class="sheet-backdrop" id="sheet-backdrop">
        <div class="sheet-surface">
          <div class="sheet-header">
            <button class="btn btn-icon btn-back" id="btn-close-sheet" aria-label="Back"><span aria-hidden="true">‹</span></button>
            <h2>${esc(w.winery)} – ${esc(w.name)} (${esc(w.vintage || "NV")})</h2>
          </div>

          <div class="sheet-body" id="detail-sheet-body">
            <div class="scaled-zone">
              <div class="detail-hero">
                <input type="file" id="detail-bottle-photo-input" accept="image/*" style="display:none;" />
                <div class="detail-hero-top">
                  ${
                    safeImageUrl(w.image_url)
                      ? `<div class="detail-hero-img-wrap" style="position:relative; display:inline-block;">
                          <img class="detail-hero-img" src="${safeImageUrl(w.image_url)}" alt="${esc(w.name)}" id="btn-detail-zoom-img" title="Tap to view or change photo" style="cursor:pointer;" />
                          <button type="button" class="btn btn-icon" id="btn-detail-upload-photo" aria-label="Change photo" title="Upload new photo" style="position:absolute; bottom:6px; right:6px; min-height:44px; min-width:44px; width:44px; height:44px; padding:0; border-radius:50%; background:rgba(0,0,0,0.75); color:#fff; border:1px solid rgba(255,255,255,0.4); font-size:16px; display:flex; align-items:center; justify-content:center; cursor:pointer;">📷</button>
                        </div>`
                      : `<div class="detail-hero-img" id="btn-detail-zoom-img" role="button" tabindex="0" title="Tap to upload bottle photo" style="background:var(--brand-soft); display:flex; flex-direction:column; align-items:center; justify-content:center; font-size:26px; cursor:pointer; gap:4px; border:2px dashed var(--brand); border-radius:10px;">
                          <span aria-hidden="true">📷</span>
                          <span style="font-size:10px; font-weight:700; color:var(--brand); text-transform:uppercase; letter-spacing:0.5px;">Add Photo</span>
                        </div>`
                  }
                  <div class="detail-hero-summary">
                    <div class="detail-hero-winery">${esc(w.winery)}</div>
                    <div class="detail-hero-title">${esc(w.name)}</div>
                    <div class="detail-hero-badges">
                      <span class="pill pill-${esc(w.wine_type)}">${esc(typeLabel)}</span>
                      <span class="pill pill-vintage">📅 ${esc(w.vintage || "NV")}</span>
                      ${w.is_imported ? `<span class="pill pill-imported" title="Imported wine" style="background:rgba(217,119,6,0.12); color:#b45309; border:1px solid rgba(217,119,6,0.3); font-weight:600;">📥 Imported</span>` : ""}
                      ${w.varietal ? `<span class="pill pill-vintage">🍇 ${esc(w.varietal)}</span>` : ""}
                      ${w.latest_added_at || w.created_at ? `<span class="pill pill-vintage">🗓️ ${esc(fmtDate(w.latest_added_at || w.created_at))}</span>` : ""}
                      <span class="pill" style="border:1px solid ${drinkMeta.color}; color:${drinkMeta.color};">${esc(drinkMeta.label)}</span>
                      <span class="meta-chip">🌍 ${esc(originText)}</span>
                      ${df.storage_location ? `<span class="meta-chip" style="color:var(--brand); font-weight:700;">📍 ${esc(df.storage_location)}</span>` : ""}
                      ${df.purchase_location ? `<span class="meta-chip">🛒 ${esc(df.purchase_location)}</span>` : ""}
                      ${df.purchase_price !== "" && df.purchase_price !== null ? `<span class="meta-chip">💰 ${esc(df.purchase_price)} ${esc(this.state.currency)}</span>` : ""}
                      ${
                        safeExternalUrl(w.vivino_url)
                          ? `<a class="vivino-tag" href="${safeExternalUrl(w.vivino_url)}" target="_blank" rel="noopener noreferrer" style="padding:3px 8px; font-size:12px;">
                              ★ ${esc(w.vivino_rating ? Number(w.vivino_rating).toFixed(1) : "Vivino")} ↗
                            </a>`
                          : w.vivino_rating
                            ? `<span class="vivino-tag" style="padding:3px 8px; font-size:12px;">★ ${esc(Number(w.vivino_rating).toFixed(1))}</span>`
                            : ""
                      }
                      ${
                        df.personal_rating !== "" && df.personal_rating !== null && df.personal_rating !== undefined
                          ? `<span class="meta-chip" style="color:var(--brand); font-weight:800;">★ ${esc(Number(df.personal_rating).toFixed(1))}/5</span>`
                          : ""
                      }
                      ${
                        w.vivino_url || w.vivino_rating
                          ? `<button type="button" class="rating-clear-btn" id="btn-mobile-unlink-vivino" title="Remove Vivino link" style="color:var(--danger); border-color:rgba(198,40,40,0.3);">
                              ✕ Unlink
                            </button>`
                          : ""
                      }
                    </div>
                  </div>
                </div>

                <div class="detail-hero-vivino-row">
                  <input class="input" id="mobile-vivino-url-input" type="url" placeholder="Paste Vivino URL (…/w/1234)" />
                  <button type="button" class="btn" id="btn-mobile-link-vivino">
                    ${this.state.linkingVivinoId === w.id ? "Linking…" : "Link Vivino"}
                  </button>
                </div>
                ${this.state.vivinoLinkError ? `<div style="color:var(--danger); font-size:12px;">${esc(this.state.vivinoLinkError)}</div>` : ""}

                <div class="detail-hero-actions">
                  <div class="detail-stepper-group">
                    <span class="detail-stepper-label">🍾 Stock</span>
                    <div class="stepper-capsule stepper-compact">
                      <button type="button" class="step-btn" id="btn-detail-minus" aria-label="Remove bottle">−</button>
                      <span class="step-qty">${Number(w.quantity || 0)}</span>
                      <button type="button" class="step-btn" id="btn-detail-plus" aria-label="Add bottle">+</button>
                    </div>
                  </div>
                  <button type="button" class="btn btn-soft detail-research-btn" id="btn-detail-research" ${this.state.researchingWineId === w.id ? "disabled" : ""}>
                    ✨ ${this.state.researchingWineId === w.id ? "Researching…" : "Research Wine"}
                  </button>
                </div>
              </div>

              <form id="mobile-detail-form" class="form-stack">
                <div class="form-row-2">
                  <div class="field">
                    <label>Winery / Producer</label>
                    <input class="input" name="winery" value="${esc(df.winery)}" />
                  </div>
                  <div class="field">
                    <label>Wine / Cuvée Name</label>
                    <input class="input" name="name" value="${esc(df.name)}" />
                  </div>
                </div>
                <div class="form-row-2">
                  <div class="field">
                    <label>Grape Variety</label>
                    <input class="input" name="varietal" value="${esc(df.varietal)}" />
                  </div>
                  <div class="field">
                    <label>Harvest Year</label>
                    <input class="input" type="number" inputmode="numeric" pattern="[0-9]*" name="vintage" value="${esc(df.vintage)}" />
                  </div>
                </div>
                <div class="form-row-2">
                  <div class="field">
                    <label>Country of Origin</label>
                    <input class="input" name="country" value="${esc(df.country)}" />
                  </div>
                  <div class="field">
                    <label>Region / Appellation</label>
                    <input class="input" name="region" value="${esc(df.region)}" />
                  </div>
                </div>
                <div class="form-row-2">
                  <div class="field">
                    <label>Storage Area / Location</label>
                    <input class="input" name="storage_location" list="detail-areas-dl" value="${esc(df.storage_location)}" />
                    <datalist id="detail-areas-dl">
                      ${this.state.areas.map((a) => `<option value="${esc(a)}"></option>`).join("")}
                    </datalist>
                  </div>
                  <div class="field">
                    <label>Bought At &amp; Price (${esc(this.state.currency)})</label>
                    <div class="form-row-2">
                      <input class="input" name="purchase_location" value="${esc(df.purchase_location)}" placeholder="Store" />
                      <input class="input" type="number" inputmode="decimal" step="0.01" name="purchase_price" value="${esc(df.purchase_price)}" placeholder="Price" />
                    </div>
                  </div>
                </div>
                <div class="field">
                  <label>Drink Window (From – Until)</label>
                  <div class="form-row-2">
                    <input class="input" type="number" inputmode="numeric" pattern="[0-9]*" name="drink_from_year" value="${esc(df.drink_from_year)}" placeholder="From" />
                    <input class="input" type="number" inputmode="numeric" pattern="[0-9]*" name="drink_until_year" value="${esc(df.drink_until_year)}" placeholder="Until" />
                  </div>
                </div>
                <div class="field">
                  ${renderPersonalRatingSlider("detail", df.personal_rating)}
                </div>
                <div class="field">
                  <label>Master Wine Notes</label>
                  <textarea class="textarea" id="detail-master-notes" name="general_notes" rows="2">${esc(df.general_notes)}</textarea>
                </div>

                ${
                  this.state.researchError
                    ? `<div style="padding:10px 12px; border-radius:10px; background:rgba(198,40,40,0.1); color:var(--danger); font-size:12.5px;">
                        <strong>Research Error:</strong> ${esc(this.state.researchError)}
                      </div>`
                    : ""
                }

                ${
                  research
                    ? `<div class="panel-card detail-research-card" style="background:var(--surface-alt);">
                        <h3>
                          <span>✨ Sommelier &amp; Producer AI Research</span>
                          <button type="button" class="btn btn-icon" id="btn-detail-reresearch" aria-label="Refresh AI research" title="Refresh AI Research">🔄</button>
                        </h3>
                        ${research.summary ? `<p style="font-size:14.5px; font-style:italic; margin:0 0 10px; line-height:1.45;">${esc(research.summary)}</p>` : ""}
                        ${
                          research.taste_profile
                            ? `<div class="taste-card">
                                <div style="font-size:11.5px; font-weight:800; text-transform:uppercase; color:var(--brand); margin-bottom:10px;">
                                  Structural Taste Profile
                                </div>
                                <div class="taste-row">
                                  <div class="taste-labels">
                                    <span>Soft / Round</span>
                                    <span style="color:var(--brand);">${esc(research.taste_profile.soft_vs_acidic_label || "Balanced")} (${Math.round(Number(research.taste_profile.soft_vs_acidic || 50))}%)</span>
                                    <span>Acidic / Crisp</span>
                                  </div>
                                  <div class="taste-bar">
                                    <div class="taste-bar-fill" style="width:${Math.max(4, Math.min(100, Number(research.taste_profile.soft_vs_acidic || 50)))}%; background:linear-gradient(90deg, #c084fc, #a61a2e);"></div>
                                    <div class="taste-bar-dot" style="left:${Math.max(2, Math.min(98, Number(research.taste_profile.soft_vs_acidic || 50)))}%;"></div>
                                  </div>
                                </div>
                                <div class="taste-row">
                                  <div class="taste-labels">
                                    <span>Light-Bodied</span>
                                    <span style="color:var(--brand);">${esc(research.taste_profile.light_vs_bold_label || "Medium")} (${Math.round(Number(research.taste_profile.light_vs_bold || 50))}%)</span>
                                    <span>Bold / Full</span>
                                  </div>
                                  <div class="taste-bar">
                                    <div class="taste-bar-fill" style="width:${Math.max(4, Math.min(100, Number(research.taste_profile.light_vs_bold || 50)))}%; background:linear-gradient(90deg, #fcd34d, #831843);"></div>
                                    <div class="taste-bar-dot" style="left:${Math.max(2, Math.min(98, Number(research.taste_profile.light_vs_bold || 50)))}%;"></div>
                                  </div>
                                </div>
                                <div class="taste-row">
                                  <div class="taste-labels">
                                    <span>Bone Dry</span>
                                    <span style="color:var(--brand);">${esc(research.taste_profile.dry_vs_sweet_label || "Dry")} (${Math.round(Number(research.taste_profile.dry_vs_sweet || 10))}%)</span>
                                    <span>Sweet / Luscious</span>
                                  </div>
                                  <div class="taste-bar">
                                    <div class="taste-bar-fill" style="width:${Math.max(4, Math.min(100, Number(research.taste_profile.dry_vs_sweet || 10)))}%; background:linear-gradient(90deg, #93c5fd, #f59e0b);"></div>
                                    <div class="taste-bar-dot" style="left:${Math.max(2, Math.min(98, Number(research.taste_profile.dry_vs_sweet || 10)))}%;"></div>
                                  </div>
                                </div>
                              </div>`
                            : ""
                        }
                        <div class="form-stack" style="font-size:13.5px; line-height:1.5;">
                          ${research.producer_history ? `<div><strong style="color:var(--brand);">🏰 Producer Heritage:</strong> ${esc(research.producer_history)}</div>` : ""}
                          ${research.wine_and_terroir ? `<div><strong style="color:var(--brand);">🍇 Terroir &amp; Vineyards:</strong> ${esc(research.wine_and_terroir)}</div>` : ""}
                          ${research.tasting_profile ? `<div><strong style="color:var(--brand);">🍷 Tasting Profile:</strong> ${esc(research.tasting_profile)}</div>` : ""}
                          ${research.serving_and_pairing ? `<div><strong style="color:var(--brand);">🍽️ Pairings:</strong> ${esc(research.serving_and_pairing)}</div>` : ""}
                        </div>
                      </div>`
                    : ""
                }

                <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:4px;">
                  <button type="button" class="btn btn-danger" id="btn-mobile-delete-wine" style="flex:1;">
                    🗑️ Delete Wine
                  </button>
                  <button type="submit" class="btn btn-primary" style="flex:2;">
                    💾 Save Overview Changes
                  </button>
                </div>
              </form>

              <div style="margin-top:18px; padding-top:14px; border-top:1px solid var(--border);">
                <strong style="font-size:12.5px; text-transform:uppercase; color:var(--brand);">Purchase Batches &amp; History</strong>
                <div style="display:flex; flex-direction:column; gap:8px; margin-top:8px;">
                  ${
                    (w.batches || []).length === 0
                      ? `<div style="font-size:12.5px; color:var(--text-muted);">No batch records yet.</div>`
                      : (w.batches || [])
                          .map(
                            (b) => `
                            <div style="padding:9px 11px; border-radius:10px; background:var(--surface-alt); font-size:12.5px;">
                              <strong>+${b.quantity_added} bottle(s)</strong> (${b.quantity_remaining} left) • ${esc(fmtDate(b.added_at))}<br/>
                              Bought at: <strong>${esc(b.purchase_location || "—")}</strong> • Area: <strong>${esc(b.storage_location || "—")}</strong>
                            </div>
                          `
                          )
                          .join("")
                  }
                </div>
              </div>
              <div style="margin-top:6px; font-size:12.5px; color:var(--text-muted); margin-bottom:8px;">
                🗓️ Bought / Added: <strong>${esc(fmtDate(w.latest_added_at || w.created_at))}</strong>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  _renderScanSheet() {
    const f = this.state.scanForm;
    const isTastedDest = this.state.scanDestination === "tasted";
    return `
      <div class="sheet-backdrop ${isTastedDest ? "tasted-page-theme" : ""}" id="sheet-backdrop">
        <div class="sheet-surface">
          <div class="sheet-header">
            <button class="btn btn-icon btn-back" id="btn-close-sheet" aria-label="Back"><span aria-hidden="true">‹</span></button>
            <h2>📷 ${isTastedDest ? "Scan Wine to Tasted" : "Scan Wine Label"}</h2>
          </div>
          <div class="sheet-body" id="scan-sheet-body">
            <div class="scaled-zone">
              <div class="scan-dest-toggle" role="radiogroup" aria-label="Save destination">
                <button type="button" class="scan-dest-btn ${!isTastedDest ? "active" : ""}" data-scan-dest="cellar">
                  <span class="scan-dest-title">🍷 Add to Cellar</span>
                  <small>Add bottles to your cellar stock</small>
                </button>
                <button type="button" class="scan-dest-btn ${isTastedDest ? "active tasted-active" : ""}" data-scan-dest="tasted">
                  <span class="scan-dest-title">🥂 Add to Tasted</span>
                  <small>Drank before · 0 added to cellar</small>
                </button>
              </div>

              <div style="padding:14px; border-radius:14px; background:var(--surface-alt); border:1px solid var(--border); margin-bottom:14px;">
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                  <label class="btn btn-primary" style="flex:1; min-width:140px; cursor:pointer;">
                    📷 Take Photo (Camera)
                    <input type="file" id="mobile-scan-file" accept="image/*" capture="environment" hidden />
                  </label>
                  <label class="btn btn-soft" style="flex:1; min-width:140px; cursor:pointer;">
                    🖼️ Choose from Library
                    <input type="file" id="mobile-scan-library" accept="image/*" hidden />
                  </label>
                  ${
                    this.state.cropState?.image
                      ? `<button type="button" class="btn" id="btn-reopen-crop">✂️ Crop &amp; Rotate</button>`
                      : ""
                  }
                </div>
                ${
                  this.state.scanBusy
                    ? `<div style="margin-top:10px; font-size:13px; font-weight:600; color:var(--brand);">
                        ⏳ Reading the label with AI Model and checking Vivino… (this can take ~10 s)
                      </div>`
                    : ""
                }
                ${
                  this.state.scanError
                    ? `<div class="scan-note scan-note-error">
                        <strong>Scan failed:</strong> ${esc(this.state.scanError)}
                      </div>`
                    : ""
                }
                ${
                  !this.state.scanBusy && !this.state.scanError && this.state.scanResultNote
                    ? `<div class="scan-note">${esc(this.state.scanResultNote)}</div>`
                    : ""
                }
                ${
                  !this.state.scanBusy && this.state.existingWine
                    ? `<div class="scan-note scan-note-info">
                        Already in your records: <strong>${esc(this.state.existingWine.winery || "")} – ${esc(this.state.existingWine.name || "")}</strong>.
                        ${isTastedDest ? "Saving records a new tasting without changing cellar stock." : "Saving adds a new batch to this wine."}
                      </div>`
                    : ""
                }
                ${
                  this.state.scanCandidates.length > 0
                    ? `<div class="field" style="margin-top:10px;">
                        <label>
                          ${
                            this.state.scanConfidence === "high"
                              ? "✓ Verified Vivino Match"
                              : "⚠️ Possible Vivino Matches (Confirm or Leave Unlinked)"
                          }
                        </label>
                        <select id="mobile-vivino-candidate" class="select">
                          <option value="-1" ${this.state.selectedCandidateIdx === -1 ? "selected" : ""}>
                            🚫 Do not link to Vivino
                          </option>
                          ${this.state.scanCandidates
                            .map(
                              (c, idx) => `
                              <option value="${idx}" ${this.state.selectedCandidateIdx === idx ? "selected" : ""}>
                                ${c.prefilter_exact ? "✓ " : ""}${esc(c.winery)}: ${esc(c.name)} (${esc(c.vintage || "NV")}) – ★ ${esc(c.vivino_rating ?? "?")}
                              </option>
                            `
                            )
                            .join("")}
                        </select>
                      </div>`
                    : ""
                }
              </div>

              <form id="mobile-scan-save-form" class="form-stack">
                <div class="form-row-2">
                  <div class="field">
                    <label>Winery / Producer *</label>
                    <input class="input" name="winery" required value="${esc(f.winery)}" />
                  </div>
                  <div class="field">
                    <label>Wine / Cuvée Name *</label>
                    <input class="input" name="name" required value="${esc(f.name)}" />
                  </div>
                </div>
                <div class="form-row-2">
                  <div class="field">
                    <label>Vintage Year</label>
                    <input class="input" type="number" inputmode="numeric" pattern="[0-9]*" name="vintage" value="${esc(f.vintage)}" />
                  </div>
                  <div class="field">
                    <label>Wine Style</label>
                    <select class="select" name="wine_type">
                      ${Object.entries(WINE_TYPES)
                        .map(([k, lbl]) => `<option value="${k}" ${f.wine_type === k ? "selected" : ""}>${lbl}</option>`)
                        .join("")}
                    </select>
                  </div>
                </div>
                <div class="form-row-2">
                  <div class="field">
                    <label>Grape Variety</label>
                    <input class="input" name="varietal" value="${esc(f.varietal)}" />
                  </div>
                  <div class="field">
                    <label>Country &amp; Region</label>
                    <div class="form-row-2">
                      <input class="input" name="country" value="${esc(f.country)}" placeholder="Country" />
                      <input class="input" name="region" value="${esc(f.region)}" placeholder="Region" />
                    </div>
                  </div>
                </div>
                ${
                  isTastedDest
                    ? `<div class="field">
                        ${renderPersonalRatingSlider("scan", f.personal_rating)}
                      </div>
                      <div class="field">
                        <label>Where Tasted / Bought &amp; Price (${esc(this.state.currency)})</label>
                        <div class="form-row-2">
                          <input class="input" name="purchase_location" value="${esc(f.purchase_location)}" placeholder="Restaurant / Store" />
                          <input class="input" type="number" inputmode="decimal" step="0.01" name="purchase_price" value="${esc(f.purchase_price)}" placeholder="Price" />
                        </div>
                      </div>
                      <div class="field">
                        <label>Tasting Notes</label>
                        <textarea class="textarea" name="general_notes" rows="2" placeholder="What did you think of this wine?">${esc(f.general_notes)}</textarea>
                      </div>`
                    : `<div class="form-row-2">
                        <div class="field">
                          <label>Storage Area</label>
                          <input class="input" name="storage_location" list="scan-areas-dl" value="${esc(f.storage_location)}" placeholder="HomeCave / Wine Fridge" />
                          <datalist id="scan-areas-dl">
                            ${this.state.areas.map((a) => `<option value="${esc(a)}"></option>`).join("")}
                          </datalist>
                        </div>
                        <div class="field">
                          <label>Quantity &amp; Price (${esc(this.state.currency)})</label>
                          <div class="form-row-2">
                            <input class="input" type="number" inputmode="numeric" pattern="[0-9]*" name="quantity" min="1" required value="${esc(f.quantity || 1)}" />
                            <input class="input" type="number" inputmode="decimal" step="0.01" name="purchase_price" value="${esc(f.purchase_price)}" placeholder="Price" />
                          </div>
                        </div>
                      </div>
                      <div class="field">
                        <label>Bought At (Store / Winery)</label>
                        <input class="input" name="purchase_location" value="${esc(f.purchase_location)}" />
                      </div>`
                }
                <button type="submit" class="btn btn-primary btn-lg" style="width:100%;" ${this.state.scanBusy ? "disabled" : ""}>
                  ${isTastedDest ? "🥂 Save Scanned Wine to Tasted" : "✓ Save Scanned Wine to Cellar"}
                </button>
              </form>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  _renderCropSheet() {
    const fine = Number(this.state.cropState?.fine || 0);
    return `
      <div class="sheet-backdrop" id="sheet-backdrop">
        <div class="sheet-surface sheet-surface-tall">
          <div class="sheet-header">
            <button class="btn btn-icon btn-back" id="btn-cancel-crop" aria-label="Back"><span aria-hidden="true">‹</span></button>
            <h2>✂️ Crop &amp; Rotate</h2>
          </div>
          <div class="sheet-body crop-body">
            <p class="crop-hint">Drag the frame to move it. Drag a corner or edge to resize so it fits the label.</p>
            <div class="crop-stage">
              <canvas id="mobile-crop-canvas" aria-label="Crop area: drag to move, drag corners to resize"></canvas>
            </div>
            <div class="crop-fine">
              <div class="crop-fine-head">
                <span>Straighten</span>
                <strong id="crop-fine-value">${fine > 0 ? "+" : ""}${fine.toFixed(1)}°</strong>
              </div>
              <input type="range" id="crop-fine-slider" min="-20" max="20" step="0.5" value="${fine}" aria-label="Straighten angle" />
            </div>
            <div class="crop-tools">
              <button type="button" class="btn btn-lg" id="btn-crop-left" aria-label="Rotate left 90 degrees">↺ 90°</button>
              <button type="button" class="btn btn-lg" id="btn-crop-right" aria-label="Rotate right 90 degrees">↻ 90°</button>
              <button type="button" class="btn btn-lg" id="btn-crop-reset">Reset</button>
            </div>
            <button type="button" class="btn btn-primary btn-lg btn-block" id="btn-crop-confirm">
              ✓ Use Photo &amp; Scan Label
            </button>
          </div>
        </div>
      </div>
    `;
  }

  _renderPreviewSheet() {
    const w = this.state.previewWine;
    if (!w) return "";
    const imgUrl = safeImageUrl(w.image_url);
    return `
      <div class="sheet-backdrop" id="sheet-backdrop">
        <div class="sheet-surface">
          <div class="sheet-header">
            <button class="btn btn-icon btn-back" id="btn-close-preview" aria-label="Back"><span aria-hidden="true">‹</span></button>
            <h2>${esc(w.winery)} – ${esc(w.name)} (${esc(w.vintage || "NV")})</h2>
          </div>
          <div class="sheet-body" style="display:flex; flex-direction:column; align-items:center; justify-content:center; background:${imgUrl ? "#111" : "var(--surface)"}; padding:20px; min-height:320px;">
            <input type="file" id="preview-bottle-photo-input" accept="image/*" style="display:none;" />
            ${
              imgUrl
                ? `<img src="${imgUrl}" alt="${esc(w.name)}" style="max-height:72dvh; max-width:100%; object-fit:contain; border-radius:8px; margin-bottom:16px;" />
                   <div style="display:flex; gap:10px; width:100%; max-width:320px;">
                     <button type="button" class="btn btn-secondary" id="btn-preview-upload-photo" style="flex:1; min-height:46px; background:rgba(255,255,255,0.15); color:#fff; border-color:rgba(255,255,255,0.3); font-weight:700;">📷 Change Photo</button>
                   </div>`
                : `<div style="text-align:center; padding:32px 16px;">
                     <div style="font-size:52px; margin-bottom:14px;">🍷</div>
                     <h3 style="margin-bottom:8px; font-size:18px;">No Bottle Photo</h3>
                     <p style="color:var(--text-muted); font-size:13.5px; max-width:280px; margin:0 auto 24px auto; line-height:1.5;">Add a photo of the bottle or label to easily identify this wine in your cellar.</p>
                     <button type="button" class="btn btn-primary" id="btn-preview-upload-photo" style="min-height:50px; padding:0 24px; font-size:15px; font-weight:700;">📷 Upload Bottle Photo</button>
                   </div>`
            }
          </div>
        </div>
      </div>
    `;
  }

  _renderInstallGuideSheet() {
    return `
      <div class="sheet-backdrop" id="sheet-backdrop">
        <div class="sheet-surface">
          <div class="sheet-header">
            <button class="btn btn-icon btn-back" id="btn-close-sheet" aria-label="Back"><span aria-hidden="true">‹</span></button>
            <h2>📲 Install HomeCave as an App</h2>
          </div>
          <div class="sheet-body">
            <div class="scaled-zone">
              <div style="display:flex; align-items:center; gap:12px; margin-bottom:14px;">
                <img src="/homecave/logo-512.png" alt="HomeCave" class="brand-icon" style="width:56px; height:56px; object-fit:contain;" />
                <div>
                  <strong style="font-size:16px; display:block;">HomeCave - your local Wine Cellar</strong>
                  <span style="font-size:12.5px; color:var(--text-muted);">Standalone Mobile App • Parallel to Home Assistant</span>
                </div>
              </div>
              ${
                this.state.deferredPrompt
                  ? `<button class="btn btn-primary" id="btn-guide-native-install" style="width:100%; min-height:54px; margin-bottom:14px;">
                      📲 Tap Here to Install App Now
                    </button>`
                  : ""
              }
              <div class="panel-card" style="background:var(--surface-alt); margin-bottom:12px;">
                <h3 style="margin-bottom:8px;">🍏 iPhone / iPad (Safari)</h3>
                <ol style="margin:0; padding-left:18px; font-size:13.5px; line-height:1.6;">
                  <li>Tap the <strong>Share</strong> button (square with arrow pointing up) in Safari’s toolbar.</li>
                  <li>Scroll down and tap <strong>Add to Home Screen</strong>.</li>
                  <li>Tap <strong>Add</strong> in the top-right corner to launch HomeCave full-screen.</li>
                </ol>
              </div>
              <div class="panel-card" style="background:var(--surface-alt);">
                <h3 style="margin-bottom:8px;">🤖 Android (Chrome / Edge)</h3>
                <ol style="margin:0; padding-left:18px; font-size:13.5px; line-height:1.6;">
                  <li>Tap the browser menu <strong>⋮</strong> in the top-right corner.</li>
                  <li>Tap <strong>Install app</strong> or <strong>Add to Home screen</strong>.</li>
                </ol>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  /* ================= Main Render ================= */

  render() {
    if (!this.root) return;
    // Preserve scroll positions across full re-renders (touch users lose their
    // place otherwise when tapping +/−, saving, or researching inside a sheet).
    const prevSheet = this._lastRenderedSheet || null;
    const prevNav = this._lastRenderedNav || null;
    const prevSheetBody = this.root.querySelector(".sheet-body");
    const sheetScroll = prevSheetBody ? prevSheetBody.scrollTop : 0;
    const pageScroll = window.scrollY;
    document.body.style.overflow = this.state.activeSheet ? "hidden" : "";
    const stats = this.state.statistics || {};
    const scaleLabel = this._fontScaleLabel();

    let activeViewHtml = "";
    if (this.state.activeNav === "sommai") activeViewHtml = this._renderSommAIView();
    else if (this.state.activeNav === "tasted") activeViewHtml = this._renderTastedView();
    else if (this.state.activeNav === "add") activeViewHtml = this._renderManualAddView();
    else if (this.state.activeNav === "config" || this.state.activeNav === "stats") activeViewHtml = this._renderConfigView();
    else activeViewHtml = this._renderCellarView();

    const isTastedPage = this.state.activeNav === "tasted";
    document.body.classList.toggle("tasted-page-theme", isTastedPage);
    const tastedCount =
      stats.tasted_wines ?? this.state.wines.filter((w) => this._isWineTasted(w)).length;

    this.root.innerHTML = `
      ${
        !this.state.isOnline
          ? `<div class="offline-banner">⚡ Offline Mode — Showing cached cellar inventory</div>`
          : ""
      }
      <div id="ptr-indicator" class="ptr-indicator" aria-hidden="true">↻</div>
      <div class="app-shell ${isTastedPage ? "tasted-page-theme" : ""}">

        <main class="scaled-zone">
          ${
            this.state.authRequired
              ? `<div class="panel-card" style="margin:12px; border:1px solid var(--brand); background:var(--surface);">
                  <h3 style="margin-bottom:6px;">🔐 Home Assistant Sign-In Required</h3>
                  <p style="font-size:13px; color:var(--text-muted); margin:0 0 12px; line-height:1.45;">
                    HomeCave inherits your Home Assistant session. Sign in once with your Home Assistant account to unlock your cellar.
                  </p>
                  <button class="btn btn-primary btn-lg" id="btn-ha-login" style="width:100%;">
                    🔐 Sign in with Home Assistant
                  </button>
                </div>`
              : this.state.error
                ? `<div style="margin:12px; padding:12px; border-radius:12px; background:rgba(198,40,40,0.12); color:var(--danger); font-size:13px;">
                    ${esc(this.state.error)}
                  </div>`
                : ""
          }
          ${activeViewHtml}
        </main>
      </div>

      <nav class="bottom-nav ${isTastedPage ? "tasted-page-theme" : ""}" aria-label="Mobile Navigation">
        <div class="bottom-nav-inner">
          <button class="nav-tab ${this.state.activeNav === "cellar" ? "active" : ""}" data-nav="cellar" aria-label="Cellar">
            <span class="nav-ico">🍷</span>
            <span>Cellar</span>
          </button>
          <button class="nav-tab ${this.state.activeNav === "sommai" ? "active" : ""}" data-nav="sommai" aria-label="SommAI Sommelier">
            <span class="nav-ico">🤖</span>
            <span>SommAI</span>
          </button>
          <button class="nav-fab-scan" id="btn-nav-scan" aria-label="Scan Bottle">
            <span class="nav-ico">📷</span>
            <span>SCAN</span>
          </button>
          <button class="nav-tab ${this.state.activeNav === "tasted" ? "active" : ""}" data-nav="tasted" aria-label="Tasted wines">
            <span class="nav-ico">🥂</span>
            <span>Tasted</span>
          </button>
          <button class="nav-tab ${this.state.activeNav === "config" ? "active" : ""}" data-nav="config" aria-label="Config">
            <span class="nav-ico">⚙️</span>
            <span>Config</span>
          </button>
        </div>
      </nav>

      ${this.state.activeSheet === "detail" ? this._renderDetailSheet() : ""}
      ${this.state.activeSheet === "scan" ? this._renderScanSheet() : ""}
      ${this.state.activeSheet === "crop" ? this._renderCropSheet() : ""}
      ${this.state.activeSheet === "preview" ? this._renderPreviewSheet() : ""}
      ${this.state.activeSheet === "install_guide" ? this._renderInstallGuideSheet() : ""}
      ${this.state.importWarningModal ? this._renderImportWarningSheet() : ""}
      ${this.state.importProgressModal ? this._renderImportProgressSheet() : ""}
    `;

    this._bindEvents();
    this._autoResizeTextareas();
    requestAnimationFrame(() => this._autoResizeTextareas());

    // Restore scroll: same sheet keeps its position; same tab keeps page position.
    const newSheetBody = this.root.querySelector(".sheet-body");
    if (newSheetBody && prevSheet && prevSheet === this.state.activeSheet) {
      newSheetBody.scrollTop = sheetScroll;
    }
    if (prevNav === this.state.activeNav && !this.state.activeSheet) {
      window.scrollTo(0, pageScroll);
    } else if (prevNav !== this.state.activeNav) {
      window.scrollTo(0, 0);
    }
    this._lastRenderedSheet = this.state.activeSheet;
    this._lastRenderedNav = this.state.activeNav;
    this._syncSheetHistory();
  }

  _bindBottleCardEvents(scope) {
    scope.querySelectorAll("[data-detail-id]").forEach((el) => {
      el.addEventListener("click", () => this.openDetail(Number(el.dataset.detailId)));
    });
    scope.querySelectorAll("[data-preview-id]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const wine = this.state.wines.find((w) => Number(w.id) === Number(el.dataset.previewId));
        if (wine && wine.image_url) {
          this.state.previewWine = wine;
          this.state.previousSheet = this.state.activeSheet;
          this.state.activeSheet = "preview";
          this.render();
        } else if (wine) {
          this.openDetail(wine.id);
        }
      });
    });
    scope.querySelectorAll("[data-open-scan]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this.state.scanForm = this._emptyForm();
        this.state.scanDestination =
          btn.dataset.openScan === "tasted" || this.state.activeNav === "tasted"
            ? "tasted"
            : "cellar";
        this.state.activeSheet = "scan";
        this.render();
      });
    });
  }

  _bindRatingSlider(prefix, targetStateObj) {
    const r = this.root;
    if (!r) return;
    const range = r.querySelector(`#${prefix}-rating-range`);
    const hidden = r.querySelector(`#${prefix}-rating-hidden`);
    const badge = r.querySelector(`#${prefix}-rating-badge`);
    const clearBtn = r.querySelector(`#${prefix}-rating-clear`);
    if (!range || !hidden || !badge) return;

    const applyVal = (raw) => {
      const meta = personalRatingMeta(raw);
      hidden.value = meta.valueStr;
      badge.textContent = meta.badgeText;
      badge.classList.toggle("active", meta.rated);
      if (clearBtn) clearBtn.style.display = meta.rated ? "" : "none";
      if (targetStateObj) targetStateObj.personal_rating = meta.valueStr;
    };

    range.addEventListener("input", () => applyVal(range.value));
    range.addEventListener("change", () => applyVal(range.value));
    clearBtn?.addEventListener("click", () => {
      applyVal("");
      range.value = "4.0";
    });
  }

  async _downloadBackupArchive(filename) {
    if (!filename) return;
    try {
      this.showToast(`Downloading ${filename}…`);
      let token = await this._getValidAccessToken(false);
      const headers = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(`/api/homecave/download/backup/${encodeURIComponent(filename)}`, {
        credentials: "same-origin",
        headers,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 5000);
      this.showToast(`Downloaded ${filename}`);
    } catch (err) {
      this.showError("Backup download failed", err);
    }
  }

  _bindEvents() {
    const r = this.root;
    r.querySelector("#btn-ha-login")?.addEventListener("click", () => this.redirectToHomeAssistantLogin());
    r.querySelector("#btn-top-refresh")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.classList.add("spinning");
      await this.fetchState();
      this.showToast("Cellar refreshed");
    });
    this._bindFormState(r.querySelector("#mobile-detail-form"), this.state.detailForm);
    this._bindFormState(r.querySelector("#mobile-scan-save-form"), this.state.scanForm);
    this._bindFormState(r.querySelector("#mobile-manual-add-form"), this.state.scanForm);
    this._bindRatingSlider("detail", this.state.detailForm);
    this._bindRatingSlider("scan", this.state.scanForm);
    r.querySelectorAll("textarea.textarea").forEach((el) => {
      el.addEventListener("input", () => this._autoResizeTextarea(el));
    });
    r.querySelector("#btn-top-font-scale")?.addEventListener("click", () => this.cycleFontScale());
    r.querySelector("#btn-top-install")?.addEventListener("click", () => this.handleInstallClick());
    r.querySelector("#btn-banner-install")?.addEventListener("click", () => this.handleInstallClick());
    r.querySelector("#btn-settings-install")?.addEventListener("click", () => this.handleInstallClick());
    r.querySelector("#btn-guide-native-install")?.addEventListener("click", () => this.handleInstallClick());
    r.querySelector("#btn-banner-dismiss")?.addEventListener("click", () => {
      this.state.installBannerDismissed = true;
      this.render();
    });

    // Bottom navigation
    r.querySelectorAll("[data-nav]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this.state.activeNav = btn.dataset.nav;
        this.render();
      });
    });
    r.querySelector("#btn-nav-scan")?.addEventListener("click", () => {
      this.state.scanForm = this._emptyForm();
      this.state.scanDestination = this.state.activeNav === "tasted" ? "tasted" : "cellar";
      this.state.scanError = "";
      this.state.scanResultNote = "";
      this.state.existingWine = null;
      this.state.scanCandidates = [];
      this.state.selectedCandidateIdx = -1;
      this.state.activeSheet = "scan";
      this.render();
    });

    // Search & filter in Cellar view
    const searchInput = r.querySelector("#mobile-search-input");
    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        this.state.searchQuery = e.target.value;
        clearTimeout(this._searchDebounce);
        this._searchDebounce = setTimeout(() => this.refreshBottleListOnly(), 120);
      });
      searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") searchInput.blur();
      });
    }
    r.querySelector("#btn-mobile-clear-search")?.addEventListener("click", () => {
      this.state.searchQuery = "";
      const inp = r.querySelector("#mobile-search-input");
      if (inp) {
        inp.value = "";
        inp.focus();
      }
      this.refreshBottleListOnly();
    });
    r.querySelector("#mobile-sort-select")?.addEventListener("change", (e) => {
      this.state.sortBy = e.target.value;
      this.render();
    });
    r.querySelector("#btn-mobile-invert-sort")?.addEventListener("click", () => {
      this.state.sortInverted = !this.state.sortInverted;
      this.render();
    });
    r.querySelectorAll(".filter-pill[data-tab]").forEach((pill) => {
      pill.addEventListener("click", () => {
        this.state.filterTab = pill.dataset.tab;
        this.render();
      });
    });

    // Search & filter in Tasted view
    const tastedSearchInput = r.querySelector("#mobile-tasted-search-input");
    if (tastedSearchInput) {
      tastedSearchInput.addEventListener("input", (e) => {
        this.state.tastedSearchQuery = e.target.value;
        clearTimeout(this._searchDebounce);
        this._searchDebounce = setTimeout(() => this.refreshTastedBottleListOnly(), 120);
      });
      tastedSearchInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") tastedSearchInput.blur();
      });
    }
    r.querySelector("#btn-mobile-clear-tasted-search")?.addEventListener("click", () => {
      this.state.tastedSearchQuery = "";
      const inp = r.querySelector("#mobile-tasted-search-input");
      if (inp) {
        inp.value = "";
        inp.focus();
      }
      this.refreshTastedBottleListOnly();
    });
    r.querySelector("#mobile-tasted-sort-select")?.addEventListener("change", (e) => {
      this.state.tastedSortBy = e.target.value;
      this.render();
    });
    r.querySelector("#btn-mobile-tasted-invert-sort")?.addEventListener("click", () => {
      this.state.tastedSortInverted = !this.state.tastedSortInverted;
      this.render();
    });
    r.querySelectorAll(".filter-pill[data-tasted-tab]").forEach((pill) => {
      pill.addEventListener("click", () => {
        this.state.tastedFilterTab = pill.dataset.tastedTab;
        this.render();
      });
    });
    r.querySelector("#btn-clear-area-filter")?.addEventListener("click", () => {
      this.state.areaFilter = "";
      this.render();
    });

    this._bindBottleCardEvents(r);

    // Areas view jump
    r.querySelectorAll("[data-jump-area]").forEach((tile) => {
      tile.addEventListener("click", () => {
        this.state.areaFilter = tile.dataset.jumpArea || "";
        this.state.activeNav = "cellar";
        this.render();
      });
    });

    // Settings view controls
    r.querySelectorAll("[data-set-scale]").forEach((b) => {
      b.addEventListener("click", () => this.setFontScale(parseFloat(b.dataset.setScale)));
    });
    r.querySelectorAll("[data-set-theme]").forEach((b) => {
      b.addEventListener("click", () => this.setTheme(b.dataset.setTheme));
    });
    r.querySelector("#btn-settings-refresh-vivino")?.addEventListener("click", async () => {
      try {
        this.showToast("Refreshing Vivino ratings…");
        await this.apiPost("refresh_vivino", {});
        await this.fetchState();
        this.showToast("Vivino ratings refreshed!");
      } catch (err) {
        this.showError("Vivino refresh failed", err);
      }
    });
    r.querySelector("#btn-settings-create-backup")?.addEventListener("click", async () => {
      try {
        this.showToast("Creating full cellar backup…");
        const res = await this.apiPost("backup_create", {});
        const createdName = res?.backup?.filename;
        await this.fetchState();
        if (createdName) {
          await this._downloadBackupArchive(createdName);
        } else {
          this.showToast("Backup created!");
        }
      } catch (err) {
        this.showError("Backup creation failed", err);
      }
    });
    r.querySelector("#btn-settings-download-backup")?.addEventListener("click", async () => {
      const sel = r.querySelector("#select-settings-backup");
      const filename = sel?.value;
      if (filename) await this._downloadBackupArchive(filename);
    });
    r.querySelector("#mobile-vivino-csv-file")?.addEventListener("change", (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      this.state.importPendingFile = file;
      this.state.importWarningModal = true;
      this.state.importProgressModal = false;
      this.render();
      e.target.value = "";
    });

    r.querySelector("#btn-close-import-warning")?.addEventListener("click", () => {
      this.state.importWarningModal = false;
      this.state.importPendingFile = null;
      this.render();
    });
    r.querySelector("#btn-cancel-vivino-import")?.addEventListener("click", () => {
      this.state.importWarningModal = false;
      this.state.importPendingFile = null;
      this.render();
    });
    r.querySelector("#import-enrich-ai-check")?.addEventListener("change", (e) => {
      this.state.importEnrichAi = Boolean(e.target.checked);
    });

    r.querySelector("#btn-start-vivino-import")?.addEventListener("click", async () => {
      const file = this.state.importPendingFile;
      if (!file) return;
      const isZip = file.name.toLowerCase().endsWith(".zip");
      const enrichAi = Boolean(this.state.importEnrichAi);

      this.state.importWarningModal = false;
      this.state.importProgressModal = true;
      this.state.importProgressData = {
        running: true,
        progress_pct: 5,
        stage: "parsing",
        status_message: "Reading and uploading archive…",
      };
      this.render();

      let pollTimer = null;
      pollTimer = setInterval(async () => {
        try {
          const prog = await this.apiGet("import_progress");
          if (prog) {
            this.state.importProgressData = prog;
            this._updateProgressUi(prog);
            if (!prog.running && (prog.progress_pct >= 100 || prog.percent >= 100 || prog.status === "completed")) {
              clearInterval(pollTimer);
            }
          }
        } catch (_) {}
      }, 800);

      const reader = new FileReader();
      reader.onload = async () => {
        try {
          const payload = {
            format: isZip ? "zip" : "csv",
            content: String(reader.result || ""),
            enrich_ai: enrichAi,
          };
          const res = await this.apiPost("import", payload);
          clearInterval(pollTimer);
          const totalImported = (res?.cellar_imported ?? 0) + (res?.tasted_imported ?? res?.imported_count ?? 0);
          this.state.importProgressData = {
            running: false,
            progress_pct: 100,
            percent: 100,
            status: "completed",
            stage: "complete",
            status_message: isZip
              ? `Imported ${res?.cellar_imported ?? 0} cellar (${res?.cellar_bottles ?? 0} bottles), ${res?.tasted_imported ?? 0} tasted, ${res?.images_downloaded ?? 0} covers, ${res?.enriched_count ?? 0} AI enriched.`
              : `Imported ${res?.imported_count ?? 0} wines (${res?.enriched_count ?? 0} AI enriched).`,
            total_bottles: totalImported,
            current_bottle: totalImported,
          };
          this._updateProgressUi(this.state.importProgressData);
          await this.fetchState();
          if (!this.state.importProgressModal) {
            this.showToast("🍾 Vivino import complete! Cellar updated.");
          }
          this.render();
        } catch (err) {
          clearInterval(pollTimer);
          this.state.importProgressData = {
            running: false,
            progress_pct: 100,
            percent: 100,
            status: "error",
            stage: "error",
            status_message: `Import failed: ${err?.message || "Unknown error"}`,
          };
          this.render();
          this.showError("Vivino import failed", err);
        }
      };
      if (isZip) {
        reader.readAsDataURL(file);
      } else {
        reader.readAsText(file);
      }
    });

    r.querySelector("#btn-close-import-progress")?.addEventListener("click", () => {
      this.state.importProgressModal = false;
      this.state.importPendingFile = null;
      this.state.importProgressData = null;
      this.render();
    });
    r.querySelector("#btn-return-cellar-progress")?.addEventListener("click", () => {
      this.state.importProgressModal = false;
      this.state.activeNav = "cellar";
      this.render();
      this.showToast("Vivino import running in background…");
    });
    r.querySelector("#btn-return-cellar-import")?.addEventListener("click", () => {
      this.state.importProgressModal = false;
      this.state.activeNav = "cellar";
      this.render();
      this.showToast("Vivino import running in background…");
    });
    r.querySelector("#btn-finish-import-progress")?.addEventListener("click", () => {
      this.state.importProgressModal = false;
      this.state.importPendingFile = null;
      this.state.importProgressData = null;
      this.state.activeNav = "cellar";
      this.render();
    });

    // SommAI Sommelier event listeners
    r.querySelectorAll(".sommai-chip[data-sommai-prompt]").forEach((chip) => {
      chip.addEventListener("click", () => {
        const prompt = chip.dataset.sommaiPrompt;
        this._sendSommAiMessage(prompt);
      });
    });

    r.querySelector("#btn-sommai-pick-photo")?.addEventListener("click", () => {
      r.querySelector("#sommai-dish-file")?.click();
    });
    r.querySelector("#sommai-dish-file")?.addEventListener("change", (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        this.state.sommAiPendingImage = String(reader.result || "");
        this.render();
      };
      reader.readAsDataURL(file);
      e.target.value = "";
    });
    r.querySelector("#btn-clear-sommai-img")?.addEventListener("click", () => {
      this.state.sommAiPendingImage = null;
      this.render();
    });
    const sommForm = r.querySelector("#sommai-chat-form");
    if (sommForm) {
      sommForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const inp = r.querySelector("#sommai-text-input");
        const val = (inp?.value || "").trim();
        this._sendSommAiMessage(val);
      });
    }

    // Manual add form
    const manualForm = r.querySelector("#mobile-manual-add-form");
    if (manualForm) {
      manualForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(manualForm);
        const payload = this._sanitizeWinePayload(Object.fromEntries(fd.entries()));
        try {
          await this.apiPost("add", payload);
          this.state.scanForm = this._emptyForm();
          this.state.activeNav = "cellar";
          await this.fetchState();
          this.showToast("Wine added to cellar!");
        } catch (err) {
          this.showError("Failed to add wine", err);
        }
      });
    }

    // Full-screen page back buttons
    r.querySelector("#btn-close-sheet")?.addEventListener("click", () => this.goBack());
    r.querySelector("#btn-close-preview")?.addEventListener("click", () => this.goBack());

    // Preview sheet photo upload handlers
    r.querySelector("#btn-preview-upload-photo")?.addEventListener("click", () => {
      r.querySelector("#preview-bottle-photo-input")?.click();
    });
    r.querySelector("#preview-bottle-photo-input")?.addEventListener("change", (e) => {
      const file = e.target?.files?.[0];
      if (file) this._handlePhotoUpload(file, this.state.previewWine);
    });

    // Detail sheet handlers
    r.querySelector("#btn-detail-zoom-img")?.addEventListener("click", () => {
      this.state.previewWine = this.state.detailWine;
      this.state.previousSheet = "detail";
      this.state.activeSheet = "preview";
      this.render();
    });
    r.querySelector("#btn-detail-upload-photo")?.addEventListener("click", () => {
      r.querySelector("#detail-bottle-photo-input")?.click();
    });
    r.querySelector("#detail-bottle-photo-input")?.addEventListener("change", (e) => {
      const file = e.target?.files?.[0];
      if (file) this._handlePhotoUpload(file, this.state.detailWine);
    });
    r.querySelector("#btn-detail-plus")?.addEventListener("click", () => {
      if (this.state.detailWine) this.adjustBottleStock(this.state.detailWine, 1);
    });
    r.querySelector("#btn-detail-minus")?.addEventListener("click", () => {
      if (this.state.detailWine) this.adjustBottleStock(this.state.detailWine, -1);
    });
    r.querySelector("#btn-detail-research")?.addEventListener("click", () => this._runResearch());
    r.querySelector("#btn-detail-reresearch")?.addEventListener("click", () => this._runResearch());
    r.querySelector("#btn-mobile-unlink-vivino")?.addEventListener("click", async () => {
      const w = this.state.detailWine;
      if (!w) return;
      const okUnlink = await this.confirmAction({
        title: "Remove Vivino link?",
        message: `The Vivino link and rating will be removed from "${w.name}".`,
        confirmLabel: "Remove link",
        danger: true,
      });
      if (!okUnlink) return;
      try {
        const res = await this.apiPost("unlink_vivino", { wine_id: w.id });
        this.state.detailWine = res.wine;
        await this.fetchState();
        this.showToast("Vivino link removed");
      } catch (err) {
        this.showError("Failed to unlink Vivino", err);
      }
    });
    r.querySelector("#btn-mobile-link-vivino")?.addEventListener("click", async () => {
      const w = this.state.detailWine;
      const inp = r.querySelector("#mobile-vivino-url-input");
      const url = (inp?.value || "").trim();
      if (!w || !url) return;
      this.state.linkingVivinoId = w.id;
      this.state.vivinoLinkError = "";
      this.render();
      try {
        const res = await this.apiPost("link_vivino", { wine_id: w.id, url });
        this.state.linkingVivinoId = null;
        if (res.wine) this.state.detailWine = res.wine;
        await this.fetchState();
        this.showToast("Vivino link updated!");
      } catch (err) {
        this.state.linkingVivinoId = null;
        this.state.vivinoLinkError = err?.message || "Could not link Vivino URL";
        this.render();
      }
    });
    r.querySelector("#btn-mobile-delete-wine")?.addEventListener("click", async () => {
      const w = this.state.detailWine;
      if (!w) return;
      const ok = await this.confirmAction({
        title: "Delete wine permanently?",
        message: `"${w.winery} – ${w.name}" and all its batches and history will be deleted from the database. This cannot be undone.`,
        confirmLabel: "Delete permanently",
        danger: true,
      });
      if (!ok) return;
      try {
        await this.apiPost("delete", { wine_id: w.id });
        this.state.activeSheet = null;
        this.state.detailWine = null;
        await this.fetchState();
        this.showToast("Wine deleted");
      } catch (err) {
        this.showError("Delete failed", err);
      }
    });

    const detailForm = r.querySelector("#mobile-detail-form");
    if (detailForm) {
      detailForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const w = this.state.detailWine;
        if (!w) return;
        const fd = new FormData(detailForm);
        const updates = this._sanitizeWinePayload(Object.fromEntries(fd.entries()));
        try {
          const res = await this.apiPost("update", { wine_id: w.id, updates });
          if (res.wine) this.state.detailWine = res.wine;
          await this.fetchState();
          this.showToast("Wine details saved!");
        } catch (err) {
          this.showError("Save failed", err);
        }
      });
    }

    // Scanner & Crop handlers
    r.querySelectorAll("[data-scan-dest]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this.state.scanDestination = btn.dataset.scanDest === "tasted" ? "tasted" : "cellar";
        this.render();
      });
    });
    r.querySelector("#mobile-scan-file")?.addEventListener("change", (e) => this._onScanFilePicked(e));
    r.querySelector("#mobile-scan-library")?.addEventListener("change", (e) => this._onScanFilePicked(e));
    r.querySelector("#btn-reopen-crop")?.addEventListener("click", () => {
      this.state.activeSheet = "crop";
      this.render();
    });
    r.querySelector("#mobile-vivino-candidate")?.addEventListener("change", (e) => {
      this._selectScanCandidate(Number(e.target.value));
    });
    const scanSaveForm = r.querySelector("#mobile-scan-save-form");
    if (scanSaveForm) {
      scanSaveForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(scanSaveForm);
        const isTastedDest = this.state.scanDestination === "tasted";
        const payload = this._sanitizeWinePayload({
          ...this.state.scanForm,
          ...Object.fromEntries(fd.entries()),
          as_tasted: isTastedDest,
          quantity: isTastedDest ? 0 : fd.get("quantity") || this.state.scanForm.quantity || 1,
        });
        try {
          await this.apiPost("add", payload);
          this.state.activeSheet = null;
          if (isTastedDest) {
            this.state.activeNav = "tasted";
          }
          await this.fetchState();
          this.showToast(isTastedDest ? "Wine saved to Tasted!" : "Scanned bottle saved to Cellar!");
        } catch (err) {
          this.showError("Save failed", err);
        }
      });
    }

    if (this.state.activeSheet === "crop") {
      this._initCropCanvas();
      r.querySelector("#btn-cancel-crop")?.addEventListener("click", () => this.goBack());
      r.querySelector("#crop-fine-slider")?.addEventListener("input", (e) => this._setFineRotation(e.target.value));
      r.querySelector("#btn-crop-left")?.addEventListener("click", () => this._rotateCrop(-90));
      r.querySelector("#btn-crop-right")?.addEventListener("click", () => this._rotateCrop(90));
      r.querySelector("#btn-crop-reset")?.addEventListener("click", () => {
        if (this.state.cropState) {
          this.state.cropState.crop = { x: 0.08, y: 0.08, w: 0.84, h: 0.84 };
          this.state.cropState.fine = 0;
          const slider = this.root.querySelector("#crop-fine-slider");
          if (slider) slider.value = "0";
          this._setFineRotation(0);
        }
      });
      r.querySelector("#btn-crop-confirm")?.addEventListener("click", () => this._confirmCropAndRunScan());
    }
  }

  _updateProgressUi(prog) {
    const r = this.root;
    if (!r) return;
    const fill = r.querySelector(".import-progress-fill");
    const pctSpan = r.querySelector(".import-progress-pct");
    const statusDiv = r.querySelector(".import-progress-status-text");
    const pct = Math.max(0, Math.min(100, Math.round(prog.progress_pct ?? prog.percent ?? 0)));
    if (fill) fill.style.width = `${pct}%`;
    if (pctSpan) pctSpan.textContent = `${pct}%`;
    if (statusDiv) {
      const msg = prog.status_message || prog.message || "Working…";
      const total = prog.total_bottles || prog.total || 0;
      const current = prog.current_bottle || prog.current || 0;
      statusDiv.innerHTML = `<strong>Status:</strong> ${esc(msg)}${
        total ? `<div style="margin-top:4px; font-weight:600; color:var(--text);">Bottle ${current} / ${total}</div>` : ""
      }`;
    }
  }

  async _sendSommAiMessage(textPrompt) {
    const text = (textPrompt !== undefined ? textPrompt : this.state.sommAiInputText || "").trim();
    const image = this.state.sommAiPendingImage;
    if (!text && !image) return;

    const userMsg = {
      role: "user",
      text: text || "What wine from my cellar pairs best with this dish?",
      image_data: image,
      timestamp: new Date().toISOString(),
    };
    this.state.sommAiMessages.push(userMsg);
    this.state.sommAiInputText = "";
    this.state.sommAiPendingImage = null;
    this.state.sommAiLoading = true;
    this.state.sommAiError = "";
    this.render();

    const thread = this.root?.querySelector("#sommai-chat-thread");
    if (thread) thread.scrollTop = thread.scrollHeight;

    try {
      const historyPayload = this.state.sommAiMessages.slice(-6).map((m) => ({
        role: m.role,
        text: m.conversational_response || m.text,
      }));
      const res = await this.apiPost("sommai", {
        message: text,
        image_data: image,
        history: historyPayload,
      });
      const p = (res && (res.pairing || res)) || {};
      this.state.sommAiMessages.push({
        role: "assistant",
        text: p.conversational_response || p.text || "Here are my pairing recommendations.",
        dish_name: p.dish_name,
        dish_analysis: p.dish_analysis,
        pairing_reasoning: p.pairing_reasoning,
        recommended_wines: p.recommended_wines || [],
        conversational_response: p.conversational_response,
        timestamp: new Date().toISOString(),
      });
    } catch (err) {
      this.state.sommAiError = err?.message || "Failed to get sommelier recommendation.";
      this.showError("SommAI pairing failed", err);
    } finally {
      this.state.sommAiLoading = false;
      this.render();
      const threadEnd = this.root?.querySelector("#sommai-chat-thread");
      if (threadEnd) threadEnd.scrollTop = threadEnd.scrollHeight;
    }
  }

  async _runResearch() {
    const w = this.state.detailWine;
    if (!w) return;
    this.state.researchingWineId = w.id;
    this.state.researchError = "";
    this.render();
    try {
      const res = await this.apiPost("research", { wine_id: w.id });
      this.state.researchingWineId = null;
      if (res.wine) this.state.detailWine = res.wine;
      await this.fetchState();
      this.showToast("AI wine research complete!");
    } catch (err) {
      this.state.researchingWineId = null;
      this.state.researchError = err?.message || "Research failed.";
      this.render();
    }
  }

  _onScanFilePicked(e) {
    const input = e.target;
    const file = input.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        this.state.cropState = {
          image: img,
          rotation: 0, // quarter turns in degrees (0 / 90 / 180 / 270)
          fine: 0, // straighten angle in degrees (−20 … +20)
          crop: { x: 0.08, y: 0.08, w: 0.84, h: 0.84 },
        };
        this.state.activeSheet = "crop";
        this.render();
      };
      img.onerror = () => this.showToast("Could not read this photo. Try another image.", "error");
      img.src = String(reader.result);
    };
    reader.readAsDataURL(file);
    // Allow picking the same photo again later.
    input.value = "";
  }

  _rotateCrop(deg) {
    const cs = this.state.cropState;
    if (!cs) return;
    cs.rotation = (cs.rotation + deg + 360) % 360;
    cs.crop = { x: 0.08, y: 0.08, w: 0.84, h: 0.84 };
    this._drawCropCanvas();
  }

  _setFineRotation(deg) {
    const cs = this.state.cropState;
    if (!cs) return;
    cs.fine = Math.max(-20, Math.min(20, Number(deg) || 0));
    const label = this.root.querySelector("#crop-fine-value");
    if (label) label.textContent = `${cs.fine > 0 ? "+" : ""}${cs.fine.toFixed(1)}°`;
    this._scheduleCropDraw();
  }

  /** Geometry of the rotated image's bounding box (shared by preview and output). */
  _cropGeometry(cs) {
    const theta = (((cs.rotation || 0) + (cs.fine || 0)) * Math.PI) / 180;
    const w = cs.image.naturalWidth || cs.image.width;
    const h = cs.image.naturalHeight || cs.image.height;
    const cos = Math.abs(Math.cos(theta));
    const sin = Math.abs(Math.sin(theta));
    return { theta, w, h, bw: w * cos + h * sin, bh: w * sin + h * cos };
  }

  _drawRotatedImage(ctx, cs, geo, scale) {
    ctx.save();
    ctx.fillStyle = "#141012";
    ctx.fillRect(0, 0, geo.bw * scale, geo.bh * scale);
    ctx.translate((geo.bw * scale) / 2, (geo.bh * scale) / 2);
    ctx.rotate(geo.theta);
    ctx.drawImage(cs.image, (-geo.w * scale) / 2, (-geo.h * scale) / 2, geo.w * scale, geo.h * scale);
    ctx.restore();
  }

  _initCropCanvas() {
    const canvas = this.root.querySelector("#mobile-crop-canvas");
    if (!canvas) return;
    this._drawCropCanvas();

    const HANDLE = 36; // finger-sized hit zone (CSS px) around corners/edges
    const MIN = 0.12;

    const pointFromEvent = (ev) => {
      const rect = canvas.getBoundingClientRect();
      return {
        x: (ev.clientX - rect.left) / rect.width,
        y: (ev.clientY - rect.top) / rect.height,
        hx: HANDLE / 2 / rect.width,
        hy: HANDLE / 2 / rect.height,
      };
    };

    const hitTest = (p, c) => {
      const nearL = Math.abs(p.x - c.x) <= p.hx;
      const nearR = Math.abs(p.x - (c.x + c.w)) <= p.hx;
      const nearT = Math.abs(p.y - c.y) <= p.hy;
      const nearB = Math.abs(p.y - (c.y + c.h)) <= p.hy;
      const inX = p.x >= c.x - p.hx && p.x <= c.x + c.w + p.hx;
      const inY = p.y >= c.y - p.hy && p.y <= c.y + c.h + p.hy;
      if (nearT && nearL) return "nw";
      if (nearT && nearR) return "ne";
      if (nearB && nearL) return "sw";
      if (nearB && nearR) return "se";
      if (nearT && inX) return "n";
      if (nearB && inX) return "s";
      if (nearL && inY) return "w";
      if (nearR && inY) return "e";
      if (p.x > c.x && p.x < c.x + c.w && p.y > c.y && p.y < c.y + c.h) return "move";
      return null;
    };

    canvas.addEventListener("pointerdown", (ev) => {
      const cs = this.state.cropState;
      if (!cs || this._cropDrag) return; // ignore a second finger
      const p = pointFromEvent(ev);
      const mode = hitTest(p, cs.crop);
      if (!mode) return;
      ev.preventDefault();
      try {
        canvas.setPointerCapture(ev.pointerId);
      } catch (_) {
        /* capture unsupported for this pointer; move events still reach the canvas */
      }
      this._cropDrag = { id: ev.pointerId, mode, start: p, orig: { ...cs.crop } };
      canvas.classList.add("dragging");
    });

    canvas.addEventListener("pointermove", (ev) => {
      const drag = this._cropDrag;
      const cs = this.state.cropState;
      if (!drag || !cs || ev.pointerId !== drag.id) return;
      ev.preventDefault();
      const p = pointFromEvent(ev);
      const dx = p.x - drag.start.x;
      const dy = p.y - drag.start.y;
      const o = drag.orig;
      let { x, y, w, h } = o;
      const m = drag.mode;
      if (m === "move") {
        x = Math.min(Math.max(0, o.x + dx), 1 - o.w);
        y = Math.min(Math.max(0, o.y + dy), 1 - o.h);
      } else {
        if (m.includes("w")) {
          const nx = Math.min(Math.max(0, o.x + dx), o.x + o.w - MIN);
          w = o.w + (o.x - nx);
          x = nx;
        }
        if (m.includes("e")) w = Math.min(Math.max(MIN, o.w + dx), 1 - o.x);
        if (m.includes("n")) {
          const ny = Math.min(Math.max(0, o.y + dy), o.y + o.h - MIN);
          h = o.h + (o.y - ny);
          y = ny;
        }
        if (m.includes("s")) h = Math.min(Math.max(MIN, o.h + dy), 1 - o.y);
      }
      cs.crop = { x, y, w, h };
      this._scheduleCropDraw();
    });

    const end = (ev) => {
      if (!this._cropDrag || ev.pointerId !== this._cropDrag.id) return;
      this._cropDrag = null;
      canvas.classList.remove("dragging");
      this._drawCropCanvas();
    };
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);
    canvas.addEventListener("lostpointercapture", end);
  }

  _scheduleCropDraw() {
    if (this._cropRaf) return;
    this._cropRaf = requestAnimationFrame(() => {
      this._cropRaf = null;
      this._drawCropCanvas();
    });
  }

  _drawCropCanvas() {
    const canvas = this.root.querySelector("#mobile-crop-canvas");
    const cs = this.state.cropState;
    if (!canvas || !cs?.image) return;
    const stage = canvas.parentElement;
    const geo = this._cropGeometry(cs);

    // Fit the full sheet width and up to ~58% of the viewport height.
    const maxW = Math.max(200, (stage?.clientWidth || 320) - 16);
    const maxH = Math.max(220, Math.round(window.innerHeight * 0.58));
    const scale = Math.min(maxW / geo.bw, maxH / geo.bh);
    const cssW = Math.round(geo.bw * scale);
    const cssH = Math.round(geo.bh * scale);
    const dpr = Math.min(window.devicePixelRatio || 1, 3);

    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);

    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._drawRotatedImage(ctx, cs, geo, scale);

    const cx = cs.crop.x * cssW;
    const cy = cs.crop.y * cssH;
    const cw = cs.crop.w * cssW;
    const ch = cs.crop.h * cssH;

    // Dim outside the crop frame.
    ctx.save();
    ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
    ctx.beginPath();
    ctx.rect(0, 0, cssW, cssH);
    ctx.rect(cx, cy, cw, ch);
    ctx.fill("evenodd");
    ctx.restore();

    // Rule-of-thirds grid (helps straighten labels with the fine slider).
    ctx.strokeStyle = "rgba(255, 255, 255, 0.45)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 1; i < 3; i++) {
      ctx.moveTo(cx + (cw * i) / 3, cy);
      ctx.lineTo(cx + (cw * i) / 3, cy + ch);
      ctx.moveTo(cx, cy + (ch * i) / 3);
      ctx.lineTo(cx + cw, cy + (ch * i) / 3);
    }
    ctx.stroke();

    // Frame and thick L-shaped corner handles.
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.strokeRect(cx, cy, cw, ch);
    const L = Math.min(24, cw / 3, ch / 3);
    ctx.lineWidth = 5;
    ctx.lineCap = "round";
    ctx.beginPath();
    [
      [cx, cy, 1, 1],
      [cx + cw, cy, -1, 1],
      [cx, cy + ch, 1, -1],
      [cx + cw, cy + ch, -1, -1],
    ].forEach(([px, py, sx, sy]) => {
      ctx.moveTo(px + sx * L, py);
      ctx.lineTo(px, py);
      ctx.lineTo(px, py + sy * L);
    });
    ctx.stroke();
    // Mid-edge grips.
    ctx.lineWidth = 5;
    ctx.beginPath();
    const g = Math.min(16, cw / 4, ch / 4);
    ctx.moveTo(cx + cw / 2 - g, cy);
    ctx.lineTo(cx + cw / 2 + g, cy);
    ctx.moveTo(cx + cw / 2 - g, cy + ch);
    ctx.lineTo(cx + cw / 2 + g, cy + ch);
    ctx.moveTo(cx, cy + ch / 2 - g);
    ctx.lineTo(cx, cy + ch / 2 + g);
    ctx.moveTo(cx + cw, cy + ch / 2 - g);
    ctx.lineTo(cx + cw, cy + ch / 2 + g);
    ctx.stroke();
  }

  async _confirmCropAndRunScan() {
    const cs = this.state.cropState;
    if (!cs?.image) return;
    const geo = this._cropGeometry(cs);

    // Render the rotated image (same math as the preview), capped for memory on phones.
    const MAX_WORK = 2600;
    const workScale = Math.min(1, MAX_WORK / Math.max(geo.bw, geo.bh));
    const rotCanvas = document.createElement("canvas");
    rotCanvas.width = Math.max(1, Math.round(geo.bw * workScale));
    rotCanvas.height = Math.max(1, Math.round(geo.bh * workScale));
    this._drawRotatedImage(rotCanvas.getContext("2d"), cs, geo, workScale);

    const sx = Math.round(cs.crop.x * rotCanvas.width);
    const sy = Math.round(cs.crop.y * rotCanvas.height);
    const sw = Math.max(1, Math.round(cs.crop.w * rotCanvas.width));
    const sh = Math.max(1, Math.round(cs.crop.h * rotCanvas.height));
    const maxOut = 1280;
    const outScale = Math.min(maxOut / sw, maxOut / sh, 1);
    const outCanvas = document.createElement("canvas");
    outCanvas.width = Math.max(1, Math.round(sw * outScale));
    outCanvas.height = Math.max(1, Math.round(sh * outScale));
    outCanvas.getContext("2d").drawImage(rotCanvas, sx, sy, sw, sh, 0, 0, outCanvas.width, outCanvas.height);

    const dataUrl = outCanvas.toDataURL("image/jpeg", 0.88);
    this.state.scanImageData = dataUrl;
    this.state.activeSheet = "scan";
    this.state.scanBusy = true;
    this.state.scanError = "";
    this.state.scanResultNote = "";
    this.state.existingWine = null;
    this.render();

    try {
      const res = await this.apiPost("scan", { image_data: dataUrl });
      this._applyScanResult(res);
    } catch (err) {
      this.state.scanError = err?.message || "Bottle recognition failed.";
    } finally {
      this.state.scanBusy = false;
      this.render();
    }
  }

  /**
   * Map the server's scan response (coordinator.async_scan_bottle) into the scan form.
   * Response keys: extracted, vivino_confidence, vivino_selected_index,
   * vivino_candidates, vivino_judge_reason, existing_wine.
   */
  _applyScanResult(res) {
    const ext = res?.extracted || {};
    const confidence = res?.vivino_confidence || "none";
    const selected =
      res?.vivino_selected_index === null || res?.vivino_selected_index === undefined
        ? -1
        : Number(res.vivino_selected_index);
    this.state.scanCandidates = res?.vivino_candidates || [];
    this.state.scanConfidence = confidence;
    this.state.scanSuggestedIndex = selected;
    this.state.scanJudgeReason = res?.vivino_judge_reason || "";
    this.state.existingWine = res?.existing_wine || null;
    // Only a verified (high) match is pre-selected; otherwise "Do not link" stays selected.
    this.state.selectedCandidateIdx = confidence === "high" && selected >= 0 ? selected : -1;
    const keepHigh = confidence === "high" && selected >= 0;
    this.state.scanForm = {
      ...this._emptyForm(),
      storage_location: this.state.scanForm?.storage_location || "",
      purchase_location: this.state.scanForm?.purchase_location || "",
      purchase_price: this.state.scanForm?.purchase_price || "",
      quantity: this.state.scanForm?.quantity || 1,
      winery: ext.winery || "",
      name: ext.name || "",
      vintage: ext.vintage ?? "",
      wine_type: ext.wine_type || "red",
      varietal: ext.varietal || "",
      region: ext.region || "",
      country: ext.country || "",
      vivino_rating: keepHigh ? ext.vivino_rating ?? "" : "",
      vivino_ratings_count: keepHigh ? ext.vivino_ratings_count ?? null : null,
      vivino_url: keepHigh ? ext.vivino_url || "" : "",
      vivino_wine_id: keepHigh ? ext.vivino_wine_id ?? null : null,
      vivino_price_estimate: keepHigh ? ext.vivino_price_estimate ?? "" : "",
      vivino_match_confidence: keepHigh ? "high" : "",
      rating_source: keepHigh ? ext.rating_source || "vivino" : "",
      drink_from_year: ext.drink_from_year ?? "",
      drink_until_year: ext.drink_until_year ?? "",
      drink_window_note: ext.drink_window_note || "",
      general_notes: ext.general_notes || "",
      image_url: ext.image_url || "",
    };
    this.state.scanResultNote = ext.winery || ext.name
      ? `✓ Label read by AI (${ext.model_used || "model"})`
      : "The AI model could not read a producer or wine name. Fill in the fields manually.";
  }

  _selectScanCandidate(idx) {
    this.state.selectedCandidateIdx = idx;
    if (idx < 0 || !this.state.scanCandidates[idx]) {
      this.state.scanForm.vivino_rating = "";
      this.state.scanForm.vivino_ratings_count = null;
      this.state.scanForm.vivino_price_estimate = "";
      this.state.scanForm.vivino_url = "";
      this.state.scanForm.vivino_wine_id = null;
      this.state.scanForm.vivino_match_confidence = "";
    } else {
      const c = this.state.scanCandidates[idx];
      this.state.scanForm.vivino_rating = c.vivino_rating ?? "";
      this.state.scanForm.vivino_ratings_count = c.vivino_ratings_count ?? null;
      this.state.scanForm.vivino_price_estimate = c.vivino_price_estimate ?? "";
      this.state.scanForm.vivino_url = c.vivino_url || "";
      this.state.scanForm.vivino_wine_id = c.vivino_wine_id ?? null;
      this.state.scanForm.vivino_match_confidence = "confirmed";
    }
    this.render();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const root = document.getElementById("homecave-mobile-root");
  if (root) {
    window.homeCaveApp = new HomeCaveMobileApp(root);
  }
});
