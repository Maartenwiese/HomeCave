/**
 * HomeCave — Local Wine Cellar, AI Label Scanner & SommAI Lovelace Cards (`custom:homecave-card`, v0.9.3)
 * Built using native Home Assistant Web Components (<ha-card>, <ha-icon>) and HA CSS variables.
 */

const WINE_TYPE_LABELS = {
  red: "Red",
  white: "White",
  rose: "Rosé",
  sparkling: "Sparkling",
  dessert: "Dessert",
  fortified: "Fortified",
};

const DRINK_STATUS_META = {
  ready: { label: "Ready", color: "var(--success-color, #2e7d32)" },
  drink_soon: { label: "Soon", color: "var(--warning-color, #ef6c00)" },
  hold: { label: "Keep", color: "var(--info-color, #0288d1)" },
  keep: { label: "Keep", color: "var(--info-color, #0288d1)" },
  past_peak: { label: "Past Peak", color: "var(--error-color, #c62828)" },
  unknown: { label: "No Window Set", color: "var(--secondary-text-color, #757575)" },
};

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Coerce a value to a finite number for safe interpolation into HTML templates.
const num = (v, d = 0) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
};

function safeExternalUrl(value) {
  if (!value) return "";
  const raw = String(value).trim();
  try {
    const parsed = new URL(raw);
    if (parsed.protocol === "https:") {
      return escapeHtml(parsed.href);
    }
  } catch (_) {}
  return "";
}

// Image sources are restricted to local cover files, Vivino's image CDN,
// in-memory blob: URLs and strict base64 raster data: URLs (no SVG).
function safeImageUrl(value) {
  if (!value) return "";
  const raw = String(value).trim();
  if (raw.startsWith("/local/homecave/covers/")) {
    if (raw.includes("..") || raw.includes("\\")) return "";
    return escapeHtml(raw);
  }
  if (/^data:image\/(png|jpeg|jpg|webp|gif);base64,[A-Za-z0-9+/=\s]+$/i.test(raw)) {
    return escapeHtml(raw);
  }
  try {
    const parsed = new URL(raw);
    if (parsed.protocol === "blob:") {
      return escapeHtml(parsed.href);
    }
    if (parsed.protocol === "https:" && parsed.hostname === "images.vivino.com") {
      return escapeHtml(parsed.href);
    }
  } catch (_) {}
  return "";
}

function nowLocalIsoMinutes() {
  const now = new Date();
  const offsetMs = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offsetMs).toISOString().slice(0, 16);
}

function formatDisplayDate(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  if (Number.isNaN(d.getTime())) return String(isoStr).slice(0, 16);
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

class WineInventoryCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._wines = [];
    this._statistics = {};
    this._cellarName = "HomeCave";
    this._currency = "EUR";
    this._tempEntity = "";
    this._humidityEntity = "";
    this._loading = false;
    this._error = "";

    // Filter & sort UI state
    this._filterTab = "all";
    this._searchQuery = "";
    this._sortBy = "recent";
    this._sortInverted = false;
    this._viewMode = "list";
    this._userToggledView = false;
    this._searchDebounce = null;
    this._fontScale = this._loadFontScale();

    // Active modal state: null | "scan" | "crop_rotate" | "quick_add" | "consume" | "detail" | "backup" | "image_preview"
    this._activeModal = null;
    this._modalWine = null;
    this._cropState = null;
    this._previewImageWine = null;
    this._previousModal = null;
    this._backupState = {
      busy: false,
      error: "",
      success: "",
      backups: [],
    };
    this._scanState = {
      busy: false,
      error: "",
      imageData: "",
      vivinoCandidates: [],
      vivinoStatus: "",
      existingWine: null,
      form: this._emptyForm(),
    };
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
      alcohol_pct: "",
      bottle_size_ml: 750,
      vivino_rating: "",
      vivino_ratings_count: null,
      vivino_url: "",
      vivino_price_estimate: "",
      rating_source: "",
      drink_from_year: "",
      drink_until_year: "",
      drink_window_note: "",
      food_pairings: "",
      general_notes: "",
      batch_notes: "",
      quantity: 1,
      as_tasted: false,
      personal_rating: "",
      added_at: nowLocalIsoMinutes(),
      purchase_location: "",
      purchase_price: "",
      storage_location: "",
    };
  }

  static get FONT_SCALES() {
    return [
      { id: "S", value: 0.9 },
      { id: "M", value: 1.0 },
      { id: "L", value: 1.15 },
      { id: "XL", value: 1.3 },
    ];
  }

  _loadFontScale() {
    try {
      const stored = parseFloat(window.localStorage.getItem("homecave_font_scale") || "");
      if (stored >= 0.8 && stored <= 1.5) return stored;
    } catch (_) {}
    return null;
  }

  _effectiveFontScale() {
    if (this._fontScale) return this._fontScale;
    const cfg = parseFloat(this._config?.font_scale);
    return cfg >= 0.8 && cfg <= 1.5 ? cfg : 1.0;
  }

  _fontScaleLabel() {
    const cur = this._effectiveFontScale();
    const scales = WineInventoryCard.FONT_SCALES;
    let best = scales[1];
    scales.forEach((s) => {
      if (Math.abs(s.value - cur) < Math.abs(best.value - cur)) best = s;
    });
    return best.id;
  }

  _cycleFontScale() {
    const scales = WineInventoryCard.FONT_SCALES;
    const idx = scales.findIndex((s) => s.id === this._fontScaleLabel());
    const next = scales[(idx + 1) % scales.length];
    this._fontScale = next.value;
    try {
      window.localStorage.setItem("homecave_font_scale", String(next.value));
    } catch (_) {}
    this._render();
  }

  _renderResults(wines) {
    if (wines.length === 0) {
      return `<div class="empty-state">
          <ha-icon icon="mdi:glass-wine" style="--mdc-icon-size: 40px; color: #a61a2e;"></ha-icon>
          <p>${
            this._searchQuery
              ? `No wines match “${escapeHtml(this._searchQuery)}”.`
              : "No wines match the current filter. Tap <strong>Scan Bottle</strong> to add one."
          }</p>
        </div>`;
    }
    return this._viewMode === "list"
      ? this._renderWineListView(wines)
      : `<div class="wine-grid">${wines.map((w) => this._renderWineCard(w)).join("")}</div>`;
  }

  /** Update only the results area, so the search <input> keeps focus and the mobile keyboard. */
  _refreshResultsOnly() {
    const container = this.shadowRoot?.querySelector("#wine-results");
    if (!container) {
      this._render();
      return;
    }
    container.innerHTML = this._renderResults(this._getFilteredAndSortedWines());
    this._bindResultEvents(container);
    const clearBtn = this.shadowRoot.querySelector("#btn-clear-search");
    if (clearBtn) clearBtn.style.visibility = this._searchQuery ? "visible" : "hidden";
  }

  _bindResultEvents(scope) {
    scope.querySelectorAll("[data-action]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const wineId = Number(btn.dataset.id);
        const wine = this._wines.find((w) => Number(w.id) === wineId);
        if (!wine) return;
        const action = btn.dataset.action;
        if (action === "quick-add") this._openQuickAddModal(wine);
        else if (action === "consume") this._openConsumeModal(wine);
        else if (action === "detail") this._openDetailModal(wine);
      });
    });
    scope.querySelectorAll('[data-action="preview-image"]').forEach((el) => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const wineId = Number(el.dataset.id);
        const wine = this._wines.find((w) => Number(w.id) === wineId) || this._modalWine;
        if (wine) {
          this._openImagePreview(wine);
        }
      });
    });
  }

  static getStubConfig() {
    return {
      title: "HomeCave",
      default_view: "list",
      default_tab: "all",
      show_mobile_app_button: true,
    };
  }

  static getConfigElement() {
    return document.createElement("homecave-card-editor");
  }

  setConfig(config) {
    this._config = config || {};
    if (this._config.title) {
      this._cellarName = this._config.title;
    }
    if (!this._userToggledView) {
      this._viewMode = this._config.default_view === "grid" ? "grid" : "list";
    }
    if (this._config.default_tab && !this._userSelectedTab) {
      this._filterTab = this._config.default_tab === "archived" ? "tasted" : this._config.default_tab;
    }
    this._render();
  }

  set hass(hass) {
    const firstBind = !this._hass;
    this._hass = hass;
    if (firstBind) {
      this._fetchInventory();
    } else {
      this._updateEnvironmentBadges();
    }
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return {
      columns: "full",
      min_columns: 6,
      rows: "auto",
    };
  }

  async _fetchInventory() {
    if (!this._hass) return;
    this._loading = true;
    this._error = "";
    try {
      const res = await this._hass.callWS({
        type: "homecave/list",
        include_out_of_stock: true,
      });
      this._wines = res.wines || [];
      this._statistics = res.statistics || {};
      this._cellarName = this._config.title || res.cellar_name || "HomeCave";
      this._currency = res.currency || "EUR";
      this._tempEntity = res.temperature_entity || "";
      this._humidityEntity = res.humidity_entity || "";
    } catch (err) {
      this._error = err?.message || "Failed to load Wine Inventory from Home Assistant.";
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _updateEnvironmentBadges() {
    if (!this.shadowRoot || !this._hass) return;
    const tempEl = this.shadowRoot.querySelector("#env-temp-val");
    if (tempEl && this._tempEntity && this._hass.states[this._tempEntity]) {
      const st = this._hass.states[this._tempEntity];
      tempEl.textContent = `${st.state} ${st.attributes.unit_of_measurement || "°C"}`;
    }
    const humEl = this.shadowRoot.querySelector("#env-hum-val");
    if (humEl && this._humidityEntity && this._hass.states[this._humidityEntity]) {
      const st = this._hass.states[this._humidityEntity];
      humEl.textContent = `${st.state} ${st.attributes.unit_of_measurement || "%"}`;
    }
  }

  _isWineTasted(w) {
    if (!w) return false;
    if (w.is_tasted) return true;
    if (Number(w.tasted_count || 0) > 0 || Number(w.tasted_bottles || 0) > 0) return true;
    if (Number(w.quantity || 0) === 0) return true;
    return (w.timeline || []).some(
      (h) => h && (h.event_type === "consumed" || h.event_type === "tasted" || Number(h.delta || 0) < 0)
    );
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
      DRINK_STATUS_META[w.drink_status]?.label,
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

  _getFilteredAndSortedWines() {
    const q = (this._searchQuery || "").trim().toLowerCase();
    let list = this._wines.filter((w) => {
      const qty = Number(w.quantity || 0);
      if (this._filterTab === "tasted" || this._filterTab === "archived") {
        if (!this._isWineTasted(w)) return false;
      } else {
        if (qty <= 0) return false;
        if (this._filterTab === "ready" && w.drink_status !== "ready") {
          return false;
        }
        if (this._filterTab === "drink_soon" && w.drink_status !== "drink_soon") {
          return false;
        }
        if ((this._filterTab === "keep" || this._filterTab === "hold") && w.drink_status !== "hold" && w.drink_status !== "keep") {
          return false;
        }
        if (this._filterTab === "past_peak" && w.drink_status !== "past_peak") {
          return false;
        }
        if (["red", "white", "rose", "sparkling"].includes(this._filterTab) && w.wine_type !== this._filterTab) {
          return false;
        }
        if (this._filterTab === "dessert" && !["dessert", "fortified"].includes(w.wine_type)) {
          return false;
        }
      }

      if (q) {
        const terms = q.split(/\s+/).filter(Boolean);
        const haystack = this._getWineSearchText(w);
        if (!terms.every((t) => haystack.includes(t))) return false;
      }
      return true;
    });

    const inv = this._sortInverted ? -1 : 1;
    list.sort((a, b) => {
      if (this._sortBy === "rating") {
        return inv * (Number(b.vivino_rating || 0) - Number(a.vivino_rating || 0));
      }
      if (this._sortBy === "drink_window") {
        const ay = Number(a.drink_until_year || 9999);
        const by = Number(b.drink_until_year || 9999);
        return inv * (ay - by);
      }
      if (this._sortBy === "vintage") {
        if (!a.vintage && !b.vintage) return 0;
        if (!a.vintage) return 1;
        if (!b.vintage) return -1;
        return inv * (Number(a.vintage) - Number(b.vintage));
      }
      if (this._sortBy === "quantity") {
        return inv * (Number(b.quantity || 0) - Number(a.quantity || 0));
      }
      // default: recent
      return inv * String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    });

    return list;
  }

  _openScanModal(manualOnly = false) {
    this._activeModal = "scan";
    const defaultToTasted = this._filterTab === "tasted" || this._filterTab === "archived";
    const form = this._emptyForm();
    form.as_tasted = defaultToTasted;
    form.quantity = defaultToTasted ? 0 : 1;
    this._scanState = {
      busy: false,
      error: "",
      manualOnly,
      imageData: "",
      vivinoCandidates: [],
      vivinoStatus: "",
      existingWine: null,
      form,
    };
    this._render();
  }

  _openQuickAddModal(wine) {
    this._activeModal = "quick_add";
    this._modalWine = wine;
    this._quickAddForm = {
      wine_id: wine.id,
      quantity: 1,
      added_at: nowLocalIsoMinutes(),
      purchase_location: wine.last_purchase_location || "",
      purchase_price: wine.last_purchase_price ?? "",
      storage_location: wine.last_storage_location || "",
      drink_window_note: wine.drink_window_note || "",
      notes: "",
    };
    this._render();
  }

  _openConsumeModal(wine) {
    this._activeModal = "consume";
    this._modalWine = wine;
    const activeBatches = (wine.batches || []).filter((b) => Number(b.quantity_remaining) > 0);
    this._consumeForm = {
      wine_id: wine.id,
      delta: -1,
      batch_id: activeBatches.length > 0 ? activeBatches[activeBatches.length - 1].id : null,
      personal_rating: wine.personal_rating ?? "",
      notes: "",
    };
    this._render();
  }

  _openDetailModal(wine) {
    this._activeModal = "detail";
    this._modalWine = wine;
    this._researchingWineId = null;
    this._researchError = "";
    this._vivinoLinkError = "";
    this._linkingVivinoId = null;
    this._detailForm = {
      wine_id: wine.id,
      winery: wine.winery || "",
      name: wine.name || "",
      vintage: wine.vintage ?? "",
      varietal: wine.varietal || "",
      region: wine.region || "",
      country: wine.country || "",
      storage_location: wine.storage_location || wine.last_storage_location || "",
      purchase_location: wine.purchase_location || wine.last_purchase_location || "",
      purchase_price: wine.purchase_price !== null && wine.purchase_price !== undefined
        ? wine.purchase_price
        : (wine.last_purchase_price !== null && wine.last_purchase_price !== undefined ? wine.last_purchase_price : ""),
      general_notes: wine.general_notes || "",
      drink_from_year: wine.drink_from_year ?? "",
      drink_until_year: wine.drink_until_year ?? "",
      drink_window_note: wine.drink_window_note || "",
      vivino_rating: wine.vivino_rating ?? "",
      vivino_url: wine.vivino_url || "",
      personal_rating: wine.personal_rating ?? "",
    };
    this._render();
  }

  _getAvailableStorageAreas() {
    const areas = new Set();
    if (this._hass && this._hass.areas) {
      Object.values(this._hass.areas).forEach((a) => {
        if (a && a.name) areas.add(a.name.trim());
      });
    }
    if (this._statistics && Array.isArray(this._statistics.known_storage_locations)) {
      this._statistics.known_storage_locations.forEach((loc) => {
        if (loc) areas.add(loc.trim());
      });
    }
    (this._wines || []).forEach((w) => {
      if (w.storage_location) areas.add(w.storage_location.trim());
      if (w.last_storage_location) areas.add(w.last_storage_location.trim());
    });
    if (areas.size === 0) {
      ["HomeCave", "HomeCave Fridge", "Pantry", "Rack A", "Rack B", "Kitchen"].forEach((loc) => areas.add(loc));
    }
    return Array.from(areas).filter(Boolean).sort();
  }

  _closeModal() {
    this._activeModal = null;
    this._modalWine = null;
    this._render();
  }

  async _handleFileSelected(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    event.target.value = "";
    this._scanState.busy = true;
    this._scanState.error = "";
    this._render();

    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        this._cropState = {
          image: img,
          originalDataUrl: String(reader.result || ""),
          rotation: 0,
          crop: { x: 0.1, y: 0.1, width: 0.8, height: 0.8 },
          dragging: false,
          dragHandle: null,
          dragStart: { px: 0, py: 0 },
          initialCrop: null,
        };
        this._scanState.busy = false;
        this._activeModal = "crop_rotate";
        this._render();
      };
      img.onerror = () => {
        this._scanState.busy = false;
        this._scanState.error = "Failed to decode the selected image file. Please choose a valid JPG/PNG/WebP photo.";
        this._render();
      };
      img.src = String(reader.result || "");
    };
    reader.onerror = () => {
      this._scanState.busy = false;
      this._scanState.error = "Failed to read image file from your device.";
      this._render();
    };
    reader.readAsDataURL(file);
  }

  _rotateCrop(delta) {
    if (!this._cropState) return;
    this._cropState.rotation = (this._cropState.rotation + delta + 360) % 360;
    this._cropState.crop = { x: 0.1, y: 0.1, width: 0.8, height: 0.8 };
    this._render();
  }

  _resetCrop() {
    if (!this._cropState) return;
    this._cropState.crop = { x: 0.1, y: 0.1, width: 0.8, height: 0.8 };
    this._drawCropCanvas();
  }

  _cancelCrop() {
    this._activeModal = "scan";
    this._render();
  }

  _confirmCropAndScan() {
    if (!this._cropState || !this._cropState.image) return;
    const { image, rotation, crop } = this._cropState;
    const origW = image.naturalWidth || image.width;
    const origH = image.naturalHeight || image.height;
    const isSideways = rotation === 90 || rotation === 270;
    const rotW = isSideways ? origH : origW;
    const rotH = isSideways ? origW : origH;

    const fullCanvas = document.createElement("canvas");
    fullCanvas.width = rotW;
    fullCanvas.height = rotH;
    const fctx = fullCanvas.getContext("2d");
    if (!fctx) return;
    fctx.translate(rotW / 2, rotH / 2);
    fctx.rotate((rotation * Math.PI) / 180);
    fctx.drawImage(image, -origW / 2, -origH / 2);

    const pixelX = Math.max(0, Math.round(crop.x * rotW));
    const pixelY = Math.max(0, Math.round(crop.y * rotH));
    const pixelW = Math.min(rotW - pixelX, Math.max(32, Math.round(crop.width * rotW)));
    const pixelH = Math.min(rotH - pixelY, Math.max(32, Math.round(crop.height * rotH)));

    const cropCanvas = document.createElement("canvas");
    cropCanvas.width = pixelW;
    cropCanvas.height = pixelH;
    const cctx = cropCanvas.getContext("2d");
    if (!cctx) return;
    cctx.drawImage(fullCanvas, pixelX, pixelY, pixelW, pixelH, 0, 0, pixelW, pixelH);

    const croppedDataUrl = cropCanvas.toDataURL("image/jpeg", 0.92);
    this._scanState.imageData = croppedDataUrl;
    this._activeModal = "scan";
    this._render();
    this._runBottleScan({ image_data: croppedDataUrl });
  }

  _setupCropCanvas() {
    const canvas = this.shadowRoot.querySelector("#crop-canvas");
    const wrapper = this.shadowRoot.querySelector("#canvas-wrapper");
    if (!canvas || !wrapper || !this._cropState || !this._cropState.image) return;

    const { image, rotation } = this._cropState;
    const origW = image.naturalWidth || image.width;
    const origH = image.naturalHeight || image.height;
    const isSideways = rotation === 90 || rotation === 270;
    const effW = isSideways ? origH : origW;
    const effH = isSideways ? origW : origH;

    const wrapperRect = wrapper.getBoundingClientRect();
    const maxW = Math.max(260, Math.min(520, wrapperRect.width || 480));
    const maxH = 400;
    const scale = Math.min(maxW / effW, maxH / effH);
    const dispW = Math.max(180, Math.round(effW * scale));
    const dispH = Math.max(180, Math.round(effH * scale));

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(dispW * dpr);
    canvas.height = Math.round(dispH * dpr);
    canvas.style.width = `${dispW}px`;
    canvas.style.height = `${dispH}px`;

    this._cropState.dispW = dispW;
    this._cropState.dispH = dispH;
    this._cropState.dpr = dpr;

    this._drawCropCanvas();

    const getCanvasPos = (e) => {
      const rect = canvas.getBoundingClientRect();
      const clientX = e.clientX ?? (e.touches && e.touches[0]?.clientX) ?? 0;
      const clientY = e.clientY ?? (e.touches && e.touches[0]?.clientY) ?? 0;
      return {
        px: (clientX - rect.left) * (dispW / rect.width),
        py: (clientY - rect.top) * (dispH / rect.height),
      };
    };

    const getHitHandle = (pos) => {
      const c = this._cropState.crop;
      const bx = c.x * dispW;
      const by = c.y * dispH;
      const bw = c.width * dispW;
      const bh = c.height * dispH;
      const r = 24;

      if (Math.hypot(pos.px - bx, pos.py - by) <= r) return "nw";
      if (Math.hypot(pos.px - (bx + bw), pos.py - by) <= r) return "ne";
      if (Math.hypot(pos.px - (bx + bw), pos.py - (by + bh)) <= r) return "se";
      if (Math.hypot(pos.px - bx, pos.py - (by + bh)) <= r) return "sw";
      if (pos.px >= bx && pos.px <= bx + bw && pos.py >= by && pos.py <= by + bh) return "move";
      return null;
    };

    canvas.onpointerdown = (e) => {
      e.preventDefault();
      const pos = getCanvasPos(e);
      const handle = getHitHandle(pos);
      if (!handle) return;
      this._cropState.dragging = true;
      this._cropState.dragHandle = handle;
      this._cropState.dragStart = pos;
      this._cropState.initialCrop = { ...this._cropState.crop };
      try {
        canvas.setPointerCapture(e.pointerId);
      } catch (_) {}
    };

    canvas.onpointermove = (e) => {
      if (!this._cropState.dragging) {
        const pos = getCanvasPos(e);
        const handle = getHitHandle(pos);
        if (handle === "nw" || handle === "se") canvas.style.cursor = "nwse-resize";
        else if (handle === "ne" || handle === "sw") canvas.style.cursor = "nesw-resize";
        else if (handle === "move") canvas.style.cursor = "move";
        else canvas.style.cursor = "default";
        return;
      }

      e.preventDefault();
      const pos = getCanvasPos(e);
      const dx = (pos.px - this._cropState.dragStart.px) / dispW;
      const dy = (pos.py - this._cropState.dragStart.py) / dispH;
      const init = this._cropState.initialCrop;
      const minSize = 0.08;

      if (this._cropState.dragHandle === "move") {
        const nx = Math.max(0, Math.min(1 - init.width, init.x + dx));
        const ny = Math.max(0, Math.min(1 - init.height, init.y + dy));
        this._cropState.crop.x = nx;
        this._cropState.crop.y = ny;
      } else if (this._cropState.dragHandle === "nw") {
        const right = init.x + init.width;
        const bottom = init.y + init.height;
        const nx = Math.max(0, Math.min(right - minSize, init.x + dx));
        const ny = Math.max(0, Math.min(bottom - minSize, init.y + dy));
        this._cropState.crop.x = nx;
        this._cropState.crop.y = ny;
        this._cropState.crop.width = right - nx;
        this._cropState.crop.height = bottom - ny;
      } else if (this._cropState.dragHandle === "ne") {
        const left = init.x;
        const bottom = init.y + init.height;
        const nw = Math.max(minSize, Math.min(1 - left, init.width + dx));
        const ny = Math.max(0, Math.min(bottom - minSize, init.y + dy));
        this._cropState.crop.y = ny;
        this._cropState.crop.width = nw;
        this._cropState.crop.height = bottom - ny;
      } else if (this._cropState.dragHandle === "se") {
        const left = init.x;
        const top = init.y;
        this._cropState.crop.width = Math.max(minSize, Math.min(1 - left, init.width + dx));
        this._cropState.crop.height = Math.max(minSize, Math.min(1 - top, init.height + dy));
      } else if (this._cropState.dragHandle === "sw") {
        const top = init.y;
        const right = init.x + init.width;
        const nx = Math.max(0, Math.min(right - minSize, init.x + dx));
        this._cropState.crop.x = nx;
        this._cropState.crop.width = right - nx;
        this._cropState.crop.height = Math.max(minSize, Math.min(1 - top, init.height + dy));
      }
      this._drawCropCanvas();
    };

    const stopDrag = (e) => {
      if (this._cropState && this._cropState.dragging) {
        this._cropState.dragging = false;
        this._cropState.dragHandle = null;
        try {
          canvas.releasePointerCapture(e.pointerId);
        } catch (_) {}
      }
    };
    canvas.onpointerup = stopDrag;
    canvas.onpointercancel = stopDrag;
  }

  _drawCropCanvas() {
    const canvas = this.shadowRoot.querySelector("#crop-canvas");
    if (!canvas || !this._cropState || !this._cropState.image) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { image, rotation, crop, dispW, dispH, dpr } = this._cropState;
    if (!dispW || !dispH) return;

    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, dispW, dispH);

    ctx.save();
    ctx.translate(dispW / 2, dispH / 2);
    ctx.rotate((rotation * Math.PI) / 180);
    const origW = image.naturalWidth || image.width;
    const origH = image.naturalHeight || image.height;
    const isSideways = rotation === 90 || rotation === 270;
    const targetW = isSideways ? dispH : dispW;
    const targetH = isSideways ? dispW : dispH;
    ctx.drawImage(image, -targetW / 2, -targetH / 2, targetW, targetH);
    ctx.restore();

    const bx = Math.round(crop.x * dispW);
    const by = Math.round(crop.y * dispH);
    const bw = Math.round(crop.width * dispW);
    const bh = Math.round(crop.height * dispH);

    ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
    ctx.fillRect(0, 0, dispW, by);
    ctx.fillRect(0, by + bh, dispW, dispH - (by + bh));
    ctx.fillRect(0, by, bx, bh);
    ctx.fillRect(bx + bw, by, dispW - (bx + bw), bh);

    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.strokeRect(bx, by, bw, bh);

    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "#1a73e8";
    ctx.strokeRect(bx, by, bw, bh);
    ctx.setLineDash([]);

    const handleR = 7;
    const corners = [
      [bx, by],
      [bx + bw, by],
      [bx + bw, by + bh],
      [bx, by + bh],
    ];
    corners.forEach(([cx, cy]) => {
      ctx.beginPath();
      ctx.arc(cx, cy, handleR, 0, Math.PI * 2);
      ctx.fillStyle = "#1a73e8";
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.stroke();
    });

    ctx.restore();
  }

  async _runBottleScan(payload) {
    if (!this._hass) return;
    this._scanState.busy = true;
    this._scanState.error = "";
    this._render();

    try {
      const res = await this._hass.callWS({
        type: "homecave/scan_bottle",
        ...payload,
      });
      const ext = res.extracted || {};
      const existing = res.existing_wine || null;
      this._scanState.vivinoCandidates = res.vivino_candidates || [];
      this._scanState.vivinoStatus = res.vivino_status || "";
      this._scanState.vivinoConfidence = res.vivino_confidence || "none";
      this._scanState.vivinoSuggestedIndex =
        res.vivino_selected_index === null || res.vivino_selected_index === undefined ? -1 : Number(res.vivino_selected_index);
      this._scanState.vivinoJudgeReason = res.vivino_judge_reason || "";
      // Only a HIGH-confidence verified match is pre-selected; otherwise default to "Do not link".
      this._scanState.selectedVivinoIndex =
        this._scanState.vivinoConfidence === "high" ? this._scanState.vivinoSuggestedIndex : -1;
      this._scanState.existingWine = existing;

      this._scanState.form = {
        wine_id: existing ? existing.id : null,
        winery: ext.winery || "",
        name: ext.name || "",
        vintage: ext.vintage ?? "",
        wine_type: ext.wine_type || "red",
        varietal: ext.varietal || "",
        region: ext.region || "",
        country: ext.country || "",
        alcohol_pct: ext.alcohol_pct ?? "",
        bottle_size_ml: ext.bottle_size_ml || 750,
        vivino_rating: ext.vivino_rating ?? "",
        vivino_ratings_count: ext.vivino_ratings_count ?? null,
        vivino_url: ext.vivino_url || "",
        vivino_price_estimate: ext.vivino_price_estimate ?? "",
        rating_source: ext.rating_source || "",
        vivino_wine_id: ext.vivino_wine_id ?? null,
        vivino_match_confidence: ext.vivino_match_confidence || "none",
        drink_from_year: ext.drink_from_year ?? (existing?.drink_from_year ?? ""),
        drink_until_year: ext.drink_until_year ?? (existing?.drink_until_year ?? ""),
        drink_window_note: ext.drink_window_note || existing?.drink_window_note || "",
        food_pairings: Array.isArray(ext.food_pairings)
          ? ext.food_pairings.join(", ")
          : ext.food_pairings || "",
        general_notes: existing?.general_notes || ext.general_notes || "",
        batch_notes: "",
        image_url: ext.image_url || "",
        as_tasted: Boolean(this._scanState.form?.as_tasted),
        personal_rating: this._scanState.form?.personal_rating || existing?.personal_rating || "",
        quantity: this._scanState.form?.as_tasted ? 0 : 1,
        added_at: nowLocalIsoMinutes(),
        purchase_location: existing?.last_purchase_location || "",
        purchase_price: existing?.last_purchase_price ?? "",
        storage_location: existing?.last_storage_location || "",
      };
    } catch (err) {
      const errMsg = err?.message || (typeof err === "string" ? err : "Bottle scan failed. You can still fill details manually.");
      this._scanState.error = errMsg;
      this.dispatchEvent(
        new CustomEvent("hass-notification", {
          detail: { message: `HomeCave: ${errMsg}` },
          bubbles: true,
          composed: true,
        })
      );
    } finally {
      this._scanState.busy = false;
      this._render();
    }
  }

  _applyVivinoCandidate(index) {
    this._scanState.selectedVivinoIndex = index;
    const f = this._scanState.form;
    if (index === -1) {
      f.vivino_rating = "";
      f.vivino_ratings_count = null;
      f.vivino_url = "";
      f.vivino_price_estimate = "";
      f.rating_source = "";
      f.vivino_wine_id = null;
      f.vivino_match_confidence = "none";
      this._render();
      return;
    }
    const cand = this._scanState.vivinoCandidates[index];
    if (!cand) return;
    f.vivino_rating = cand.vivino_rating ?? "";
    f.vivino_ratings_count = cand.vivino_ratings_count ?? null;
    f.vivino_url = cand.vivino_url || "";
    f.vivino_price_estimate = cand.vivino_price_estimate ?? "";
    f.rating_source = "vivino";
    f.vivino_wine_id = cand.vivino_wine_id ?? null;
    // A verified auto-match keeps "high"; any manual pick is a user confirmation.
    f.vivino_match_confidence =
      this._scanState.vivinoConfidence === "high" && index === this._scanState.vivinoSuggestedIndex ? "high" : "confirmed";
    if (cand.region && !f.region) f.region = cand.region;
    if (cand.country && !f.country) f.country = cand.country;
    this._render();
  }

  async _submitScanOrAddForm(event) {
    event.preventDefault();
    if (!this._hass) return;
    const f = this._scanState.form;
    const toIntOrNull = (v) => (v !== "" && v !== null && v !== undefined ? parseInt(v, 10) : null);
    const toFloatOrNull = (v) => (v !== "" && v !== null && v !== undefined ? parseFloat(v) : null);

    this._scanState.busy = true;
    this._render();
    try {
      await this._hass.callWS({
        type: "homecave/add_wine",
        wine_id: f.wine_id,
        winery: f.winery,
        name: f.name,
        vintage: toIntOrNull(f.vintage),
        wine_type: f.wine_type,
        varietal: f.varietal,
        region: f.region,
        country: f.country,
        alcohol_pct: toFloatOrNull(f.alcohol_pct),
        bottle_size_ml: toIntOrNull(f.bottle_size_ml) || 750,
        vivino_rating: toFloatOrNull(f.vivino_rating),
        vivino_ratings_count: toIntOrNull(f.vivino_ratings_count),
        vivino_url: f.vivino_url,
        vivino_price_estimate: toFloatOrNull(f.vivino_price_estimate),
        rating_source: f.vivino_url ? (f.rating_source || "vivino") : "",
        vivino_wine_id: f.vivino_url ? (toIntOrNull(f.vivino_wine_id)) : null,
        vivino_match_confidence: f.vivino_url ? (f.vivino_match_confidence || "confirmed") : "none",
        drink_from_year: toIntOrNull(f.drink_from_year),
        drink_until_year: toIntOrNull(f.drink_until_year),
        drink_window_note: f.drink_window_note,
        food_pairings: f.food_pairings,
        general_notes: f.general_notes,
        image_url: f.image_url || "",
        as_tasted: Boolean(f.as_tasted),
        personal_rating: toFloatOrNull(f.personal_rating),
        quantity: f.as_tasted ? 0 : Math.max(1, toIntOrNull(f.quantity) || 1),
        added_at: f.added_at ? new Date(f.added_at).toISOString() : null,
        purchase_location: f.purchase_location,
        purchase_price: toFloatOrNull(f.purchase_price),
        storage_location: f.as_tasted ? "" : f.storage_location,
        batch_notes: f.batch_notes,
      });
      if (f.as_tasted) {
        this._filterTab = "tasted";
      }
      this._closeModal();
      await this._fetchInventory();
    } catch (err) {
      this._scanState.error = err?.message || "Failed to save wine.";
      this._scanState.busy = false;
      this._render();
    }
  }

  async _submitQuickAdd(event) {
    event.preventDefault();
    if (!this._hass || !this._quickAddForm) return;
    const qf = this._quickAddForm;
    await this._hass.callWS({
      type: "homecave/adjust_quantity",
      wine_id: qf.wine_id,
      delta: Math.max(1, parseInt(qf.quantity, 10) || 1),
      added_at: qf.added_at ? new Date(qf.added_at).toISOString() : null,
      purchase_location: qf.purchase_location,
      purchase_price: qf.purchase_price !== "" ? parseFloat(qf.purchase_price) : null,
      storage_location: qf.storage_location,
      drink_window_note: qf.drink_window_note,
      notes: qf.notes,
    });
    this._closeModal();
    await this._fetchInventory();
  }

  async _submitConsume(event) {
    event.preventDefault();
    if (!this._hass || !this._consumeForm) return;
    const cf = this._consumeForm;
    await this._hass.callWS({
      type: "homecave/adjust_quantity",
      wine_id: cf.wine_id,
      delta: -Math.abs(parseInt(cf.delta, 10) || 1),
      batch_id: cf.batch_id ? parseInt(cf.batch_id, 10) : null,
      personal_rating: cf.personal_rating !== "" ? parseFloat(cf.personal_rating) : null,
      notes: cf.notes,
    });
    this._closeModal();
    await this._fetchInventory();
  }

  async _submitDetailUpdate(event) {
    event.preventDefault();
    if (!this._hass || !this._detailForm) return;
    const df = this._detailForm;
    const toIntOrNull = (v) => (v !== "" && v !== null && v !== undefined ? parseInt(v, 10) : null);
    const toFloatOrNull = (v) => (v !== "" && v !== null && v !== undefined ? parseFloat(v) : null);

    await this._hass.callWS({
      type: "homecave/update_wine",
      wine_id: df.wine_id,
      updates: {
        winery: df.winery,
        name: df.name,
        vintage: toIntOrNull(df.vintage),
        varietal: df.varietal,
        region: df.region,
        country: df.country,
        storage_location: df.storage_location,
        purchase_location: df.purchase_location || "",
        purchase_price: toFloatOrNull(df.purchase_price),
        general_notes: df.general_notes,
        drink_from_year: toIntOrNull(df.drink_from_year),
        drink_until_year: toIntOrNull(df.drink_until_year),
        drink_window_note: df.drink_window_note,
        vivino_rating: toFloatOrNull(df.vivino_rating),
        vivino_url: df.vivino_url,
        personal_rating: toFloatOrNull(df.personal_rating),
      },
    });
    this._closeModal();
    await this._fetchInventory();
  }

  async _handleLinkVivinoUrl(wineId, url) {
    if (!this._hass) return;
    const clean = (url || "").trim();
    this._vivinoLinkError = "";
    if (!clean) {
      this._vivinoLinkError = "Paste a Vivino wine link first (it contains /w/<number>).";
      this._render();
      return;
    }
    this._linkingVivinoId = Number(wineId);
    this._render();
    try {
      const res = await this._hass.callWS({
        type: "homecave/link_vivino_url",
        wine_id: Number(wineId),
        url: clean,
      });
      if (this._modalWine && Number(this._modalWine.id) === Number(wineId) && res.wine) {
        this._modalWine = res.wine;
        if (this._detailForm) {
          this._detailForm.vivino_url = res.wine.vivino_url || "";
          this._detailForm.vivino_rating = res.wine.vivino_rating ?? "";
        }
      }
      await this._fetchInventory();
    } catch (err) {
      this._vivinoLinkError = `Could not link: ${err?.message || err}`;
    } finally {
      this._linkingVivinoId = null;
      this._render();
    }
  }

  async _handleUnlinkVivino(wineId) {
    if (!this._hass) return;
    const wine = this._wines.find((w) => Number(w.id) === Number(wineId));
    const label = wine ? `${wine.winery} – ${wine.name}` : `Wine #${wineId}`;
    const confirmed = window.confirm(
      `Remove the Vivino link and ratings from "${label}"?\n\nThis will clear the Vivino URL, star rating, review count, and price estimate.`
    );
    if (!confirmed) return;

    try {
      const res = await this._hass.callWS({
        type: "homecave/unlink_vivino",
        wine_id: Number(wineId),
      });
      if (this._modalWine && Number(this._modalWine.id) === Number(wineId)) {
        this._modalWine = res.wine || this._modalWine;
        this._modalWine.vivino_url = "";
        this._modalWine.vivino_rating = null;
        this._modalWine.vivino_ratings_count = null;
        this._modalWine.vivino_price_estimate = null;
        this._modalWine.rating_source = "";
        if (this._detailForm) {
          this._detailForm.vivino_url = "";
          this._detailForm.vivino_rating = "";
        }
      }
      await this._fetchInventory();
      this._render();
    } catch (err) {
      alert(`Failed to unlink Vivino: ${err.message || err}`);
    }
  }

  async _handleResearchWine(wineId) {
    if (!this._hass) return;
    this._researchingWineId = Number(wineId);
    this._researchError = "";
    this._render();
    try {
      const res = await this._hass.callWS({
        type: "homecave/research_wine",
        wine_id: Number(wineId),
      });
      this._researchingWineId = null;
      if (this._modalWine && Number(this._modalWine.id) === Number(wineId)) {
        this._modalWine.research_notes = JSON.stringify(res.research || {});
      }
      await this._fetchInventory();
      this._render();
    } catch (err) {
      this._researchingWineId = null;
      this._researchError = err?.message || (typeof err === "string" ? err : "AI wine research failed.");
      this._render();
    }
  }

  async _refreshAllVivino() {
    if (!this._hass) return;
    this._loading = true;
    this._render();
    try {
      await this._hass.callWS({ type: "homecave/refresh_vivino" });
      await this._fetchInventory();
    } catch (err) {
      this._error = err?.message || "Vivino refresh failed.";
      this._loading = false;
      this._render();
    }
  }

  _renderWineCard(wine) {
    const statusMeta = DRINK_STATUS_META[wine.drink_status] || DRINK_STATUS_META.unknown;
    const typeLabel = WINE_TYPE_LABELS[wine.wine_type] || "Wine";
    const vintageLabel = wine.vintage ? String(wine.vintage) : "NV";
    const ratingVal = wine.vivino_rating ? Number(wine.vivino_rating).toFixed(1) : null;
    const ratingCount = wine.vivino_ratings_count
      ? `(${Number(wine.vivino_ratings_count).toLocaleString()})`
      : "";
    const drinkYears =
      wine.drink_from_year || wine.drink_until_year
        ? `${wine.drink_from_year || "Now"}–${wine.drink_until_year || "…"}`
        : statusMeta.label;

    return `
      <div class="wine-item" data-wine-id="${num(wine.id)}">
        <div class="wine-item-top">
          <div class="wine-thumb-wrap">
            ${
              safeImageUrl(wine.image_url)
                ? `<img class="wine-thumb preview-image-clickable" src="${safeImageUrl(wine.image_url)}" alt="${escapeHtml(wine.name)}" data-action="preview-image" data-id="${num(wine.id)}" title="Click to enlarge image" style="cursor: pointer;" />`
                : `<div class="wine-thumb-placeholder type-${escapeHtml(wine.wine_type)}">
                    <ha-icon icon="mdi:glass-wine"></ha-icon>
                  </div>`
            }
          </div>
          <div class="wine-info">
            <div class="wine-badges-row">
              <span class="type-pill type-${escapeHtml(wine.wine_type)}">${escapeHtml(typeLabel)}</span>
              <span class="vintage-pill">${escapeHtml(vintageLabel)}</span>
              ${
                wine.is_imported
                  ? `<span class="imported-pill" title="Imported wine" style="font-size: 11px; padding: 2px 7px; border-radius: 999px; background: rgba(217,119,6,0.12); color: #b45309; border: 1px solid rgba(217,119,6,0.3); font-weight: 600; display: inline-flex; align-items: center; gap: 3px;">📥 Imported</span>`
                  : ""
              }
              <span class="drink-pill" style="border-color:${statusMeta.color}; color:${statusMeta.color};">
                ${escapeHtml(statusMeta.label)} (${escapeHtml(drinkYears)})
              </span>
              ${
                this._isWineTasted(wine)
                  ? `<span class="tasted-badge">🥂 Tasted${Number(wine.tasted_count || 0) > 1 ? ` ${Number(wine.tasted_count)}×` : ""}</span>`
                  : ""
              }
            </div>
            <div class="wine-winery">${escapeHtml(wine.winery)}</div>
            <div class="wine-title">${escapeHtml(wine.name)}</div>
            <div class="wine-sub">
              ${escapeHtml([wine.varietal, wine.region, wine.country].filter(Boolean).join(" • ") || "—")}
            </div>
            <div class="wine-ratings-row">
              ${
                ratingVal
                  ? safeExternalUrl(wine.vivino_url)
                    ? `<a class="vivino-badge" href="${safeExternalUrl(wine.vivino_url)}" target="_blank" rel="noopener noreferrer" title="Open on Vivino (opens in new tab)">
                        ★ ${escapeHtml(ratingVal)} <span class="vivino-sub">Vivino ${escapeHtml(ratingCount)}</span>
                        <ha-icon icon="mdi:open-in-new" style="--mdc-icon-size: 12px; margin-left: 2px;"></ha-icon>
                      </a>`
                    : `<span class="vivino-badge">★ ${escapeHtml(ratingVal)} <span class="vivino-sub">Vivino ${escapeHtml(ratingCount)}</span></span>`
                  : `<span class="vivino-badge muted">No Vivino rating</span>`
              }
              ${
                wine.personal_rating
                  ? `<span class="personal-badge">My Rating: ★ ${Number(wine.personal_rating).toFixed(1)}</span>`
                  : ""
              }
            </div>
          </div>
        </div>

        <div class="wine-meta-grid">
          <div>
            <ha-icon icon="mdi:storefront-outline"></ha-icon>
            <span><strong>Bought:</strong> ${escapeHtml(wine.last_purchase_location || "—")}</span>
          </div>
          <div>
            <ha-icon icon="mdi:fridge-outline"></ha-icon>
            <span><strong>Location:</strong> ${escapeHtml((wine.active_storage_locations || []).join(", ") || wine.last_storage_location || "—")}</span>
          </div>
          <div>
            <ha-icon icon="mdi:calendar-clock"></ha-icon>
            <span><strong>Added:</strong> ${escapeHtml(formatDisplayDate(wine.latest_added_at))}</span>
          </div>
          ${
            wine.drink_window_note
              ? `<div>
                  <ha-icon icon="mdi:glass-wine"></ha-icon>
                  <span><strong>Drink Note:</strong> ${escapeHtml(wine.drink_window_note)}</span>
                </div>`
              : ""
          }
        </div>

        ${
          wine.general_notes
            ? `<div class="wine-notes-preview">
                <strong>Notes Overview:</strong> ${escapeHtml(wine.general_notes)}
              </div>`
            : ""
        }

        <div class="wine-actions-bar">
          <div class="stepper-group">
            <button class="ha-btn icon-btn minus-btn" data-action="consume" data-id="${num(wine.id)}" title="Open / Consume Bottle (-1)">
              <ha-icon icon="mdi:minus"></ha-icon>
            </button>
            <span class="qty-badge">${Number(wine.quantity || 0)} ${Number(wine.quantity) === 1 ? "bottle" : "bottles"}</span>
            <button class="ha-btn icon-btn plus-btn" data-action="quick-add" data-id="${num(wine.id)}" title="Add Bottle(s) (+1 with timestamp & notes)">
              <ha-icon icon="mdi:plus"></ha-icon>
            </button>
          </div>
          <button class="ha-btn secondary-btn" data-action="detail" data-id="${num(wine.id)}">
            <ha-icon icon="mdi:notebook-edit-outline"></ha-icon>
            <span>Batches &amp; Notes (${(wine.timeline || []).length})</span>
          </button>
        </div>
      </div>
    `;
  }

  _renderWineListView(wines) {
    return `
      <div class="wine-list-table-container">
        <table class="wine-list-table">
          <thead>
            <tr>
              <th style="width: 44px;">Label</th>
              <th>Wine &amp; Winery</th>
              <th>Vintage</th>
              <th>Type</th>
              <th>Drink Window</th>
              <th>Rating</th>
              <th style="text-align: center;">Stock</th>
              <th style="text-align: right;">Actions</th>
            </tr>
          </thead>
          <tbody>
            ${wines
              .map((wine) => {
                const statusMeta = DRINK_STATUS_META[wine.drink_status] || DRINK_STATUS_META.unknown;
                const typeLabel = WINE_TYPE_LABELS[wine.wine_type] || "Wine";
                const vintageLabel = wine.vintage ? String(wine.vintage) : "NV";
                const ratingVal = wine.vivino_rating ? Number(wine.vivino_rating).toFixed(1) : null;
                const drinkYears =
                  wine.drink_from_year || wine.drink_until_year
                    ? `${wine.drink_from_year || "Now"}–${wine.drink_until_year || "…"}`
                    : statusMeta.label;
                const subMeta = [wine.varietal, wine.region, wine.country].filter(Boolean).join(" • ") || "—";

                return `
                  <tr data-wine-id="${num(wine.id)}">
                    <td>
                      ${
                        safeImageUrl(wine.image_url)
                          ? `<img class="wine-list-thumb preview-image-clickable" src="${safeImageUrl(wine.image_url)}" alt="${escapeHtml(wine.name)}" data-action="preview-image" data-id="${num(wine.id)}" title="Click to enlarge image" style="cursor: pointer;" />`
                          : `<div class="wine-list-thumb-placeholder type-${escapeHtml(wine.wine_type)}">
                              <ha-icon icon="mdi:glass-wine" style="--mdc-icon-size: 20px;"></ha-icon>
                            </div>`
                      }
                    </td>
                    <td>
                      <div class="wine-list-title-wrap wine-title-clickable" data-action="detail" data-id="${num(wine.id)}" title="Click to view wine details" style="cursor: pointer;">
                        <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                          <strong title="${escapeHtml(wine.name)}" style="cursor: pointer; color: var(--primary-color, #a61a2e);">${escapeHtml(wine.name)}</strong>
                          ${
                            wine.is_imported
                              ? `<span class="imported-pill" title="Imported wine" style="font-size: 10px; padding: 1px 6px; border-radius: 999px; background: rgba(217,119,6,0.12); color: #b45309; border: 1px solid rgba(217,119,6,0.3); font-weight: 600; display: inline-flex; align-items: center; gap: 2px;">📥 Imported</span>`
                              : ""
                          }
                        </div>
                        <span style="font-weight: 500;">${escapeHtml(wine.winery)}${this._isWineTasted(wine) ? ` • <span class="tasted-badge">🥂 Tasted</span>` : ""}</span>
                        <span>${escapeHtml(subMeta)}</span>
                      </div>
                    </td>
                    <td>
                      <span class="vintage-pill">${escapeHtml(vintageLabel)}</span>
                    </td>
                    <td>
                      <span class="type-pill type-${escapeHtml(wine.wine_type)}">${escapeHtml(typeLabel)}</span>
                    </td>
                    <td>
                      <span class="drink-pill" style="border-color:${statusMeta.color}; color:${statusMeta.color}; font-size: 11px;">
                        ${escapeHtml(drinkYears)}
                      </span>
                    </td>
                    <td>
                      ${
                        ratingVal
                          ? safeExternalUrl(wine.vivino_url)
                            ? `<a class="vivino-badge" href="${safeExternalUrl(wine.vivino_url)}" target="_blank" rel="noopener noreferrer" title="Open on Vivino (opens in new tab)" style="font-size: 11px; padding: 2px 6px; text-decoration: none;">
                                ★ ${escapeHtml(ratingVal)}
                                <ha-icon icon="mdi:open-in-new" style="--mdc-icon-size: 11px; margin-left: 2px;"></ha-icon>
                              </a>`
                            : `<span class="vivino-badge" style="font-size: 11px; padding: 2px 6px;">★ ${escapeHtml(ratingVal)}</span>`
                          : `<span style="font-size: 11px; color: var(--secondary-text-color);">—</span>`
                      }
                    </td>
                    <td style="text-align: center;">
                      <div class="stepper-group" style="justify-content: center;">
                        <button class="ha-btn icon-btn minus-btn" data-action="consume" data-id="${num(wine.id)}" title="Open / Consume Bottle (-1)" style="padding: 2px 6px; min-width: 26px; min-height: 26px;">
                          <ha-icon icon="mdi:minus" style="--mdc-icon-size: 14px;"></ha-icon>
                        </button>
                        <strong class="qty-badge" style="min-width: 32px; font-size: 13px;">${Number(wine.quantity || 0)}</strong>
                        <button class="ha-btn icon-btn plus-btn" data-action="quick-add" data-id="${num(wine.id)}" title="Add Bottle (+1)" style="padding: 2px 6px; min-width: 26px; min-height: 26px;">
                          <ha-icon icon="mdi:plus" style="--mdc-icon-size: 14px;"></ha-icon>
                        </button>
                      </div>
                    </td>
                    <td style="text-align: right;">
                      <div class="wine-list-actions" style="justify-content: flex-end;">
                        <button class="ha-btn secondary-btn" data-action="detail" data-id="${num(wine.id)}" style="padding: 4px 10px; font-size: 12px;">
                          <ha-icon icon="mdi:notebook-edit-outline" style="--mdc-icon-size: 16px;"></ha-icon>
                          <span>Details</span>
                        </button>
                      </div>
                    </td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  async _loadBackups() {
    if (!this._hass) return;
    try {
      const res = await this._hass.callWS({ type: "homecave/list_backups" });
      this._backupState.backups = res.backups || [];
      this._render();
    } catch (err) {
      console.warn("Failed to list backups:", err);
    }
  }

  async _handleCreateBackup() {
    if (!this._hass) return;
    this._backupState.busy = true;
    this._backupState.error = "";
    this._backupState.success = "";
    this._render();
    try {
      const res = await this._hass.callWS({ type: "homecave/create_backup" });
      this._backupState.busy = false;
      this._backupState.success = `Backup created successfully (${res.wine_count} wines, ${(res.size_bytes / 1024).toFixed(1)} KB): ${res.backup_filename}`;
      const a = document.createElement("a");
      if (res.archive_base64) {
        a.href = `data:application/zip;base64,${res.archive_base64}`;
      } else if (res.download_url) {
        a.href = res.download_url;
      }
      a.download = res.backup_filename || "homecave_backup.zip";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      await this._loadBackups();
    } catch (err) {
      this._backupState.busy = false;
      this._backupState.error = err?.message || "Failed to create backup.";
      this._render();
    }
  }

  async _handleRestoreFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    event.target.value = "";
    if (
      !confirm(
        `Are you sure you want to restore from "${file.name}"? This will overwrite your current cellar database and restore images.`
      )
    ) {
      return;
    }
    this._backupState.busy = true;
    this._backupState.error = "";
    this._backupState.success = "";
    this._render();

    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const dataUrl = String(reader.result || "");
        const base64 = dataUrl.includes(",") ? dataUrl.split(",")[1] : dataUrl;
        const res = await this._hass.callWS({
          type: "homecave/restore_backup",
          archive_base64: base64,
        });
        this._backupState.busy = false;
        this._backupState.success = `Database restored successfully (${res.wines_restored} wines, ${res.images_restored} images)!`;
        await this._fetchInventory();
        await this._loadBackups();
      } catch (err) {
        this._backupState.busy = false;
        this._backupState.error = err?.message || "Failed to restore backup.";
        this._render();
      }
    };
    reader.onerror = () => {
      this._backupState.busy = false;
      this._backupState.error = "Failed to read selected backup file.";
      this._render();
    };
    reader.readAsDataURL(file);
  }

  async _handleImportFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    event.target.value = "";
    this._backupState.busy = true;
    this._backupState.error = "";
    this._backupState.success = "";
    this._render();

    const fileNameLower = file.name.toLowerCase();
    const isZip = fileNameLower.endsWith(".zip");
    const isCsv = fileNameLower.endsWith(".csv");
    const formatType = isZip ? "zip" : isCsv ? "csv" : "json";

    const reader = new FileReader();
    if (isZip) {
      reader.onload = async () => {
        try {
          const dataUrl = String(reader.result || "");
          const base64 = dataUrl.includes(",") ? dataUrl.split(",")[1] : dataUrl;
          const res = await this._hass.callWS({
            type: "homecave/import_wines",
            payload: base64,
            format_type: "zip",
          });
          this._backupState.busy = false;
          this._backupState.success = `Imported ${res.imported_wines} wines (${res.bottles_added} bottles added)!`;
          await this._fetchInventory();
        } catch (err) {
          this._backupState.busy = false;
          this._backupState.error = err?.message || "Import failed.";
          this._render();
        }
      };
      reader.readAsDataURL(file);
    } else {
      reader.onload = async () => {
        try {
          const text = String(reader.result || "");
          const payload = formatType === "json" ? JSON.parse(text) : text;
          const res = await this._hass.callWS({
            type: "homecave/import_wines",
            payload: payload,
            format_type: formatType,
          });
          this._backupState.busy = false;
          this._backupState.success = `Imported ${res.imported_wines} wines (${res.bottles_added} bottles added)!`;
          await this._fetchInventory();
        } catch (err) {
          this._backupState.busy = false;
          this._backupState.error = err?.message || "Import failed.";
          this._render();
        }
      };
      reader.readAsText(file);
    }
  }

  async _handleRestoreServerBackup(filename) {
    if (!this._hass || !filename) return;
    if (
      !confirm(
        `Are you sure you want to restore from "${filename}"? This will overwrite your current cellar database and restore images.`
      )
    ) {
      return;
    }
    this._backupState.busy = true;
    this._backupState.error = "";
    this._backupState.success = "";
    this._render();
    try {
      const res = await this._hass.callWS({
        type: "homecave/restore_backup",
        backup_filename: filename,
      });
      this._backupState.busy = false;
      this._backupState.success = `Database restored successfully (${res.wines_restored} wines, ${res.images_restored} images)!`;
      await this._fetchInventory();
      await this._loadBackups();
    } catch (err) {
      this._backupState.busy = false;
      this._backupState.error = err?.message || "Failed to restore backup.";
      this._render();
    }
  }

  _renderBackupModal() {
    const bs = this._backupState;
    return `
      <div class="modal-backdrop" id="backup-modal-backdrop">
        <div class="modal-surface" style="max-width: 620px;">
          <div class="modal-header">
            <h2>
              <ha-icon icon="mdi:database-cog" style="color: var(--primary-color, #03a9f4);"></ha-icon>
              <span>Cellar Database Backup, Restore &amp; Import</span>
            </h2>
            <button class="ha-btn icon-btn" id="close-backup-btn" title="Close">
              <ha-icon icon="mdi:close"></ha-icon>
            </button>
          </div>

          <div class="modal-body">
            ${
              bs.error
                ? `<div class="card-alert-banner" role="alert" style="margin-bottom: 14px;">
                    <div class="card-alert-content">
                      <ha-icon icon="mdi:alert-circle" style="color: var(--error-color, #c62828);"></ha-icon>
                      <span>${escapeHtml(bs.error)}</span>
                    </div>
                    <button class="ha-btn icon-btn" id="dismiss-backup-error-btn">
                      <ha-icon icon="mdi:close"></ha-icon>
                    </button>
                  </div>`
                : ""
            }

            ${
              bs.success
                ? `<div class="scan-status-banner" style="background: rgba(46, 125, 50, 0.15); color: #2e7d32; padding: 10px 14px; border-radius: 8px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                      <ha-icon icon="mdi:check-circle" style="color: #2e7d32;"></ha-icon>
                      <span>${escapeHtml(bs.success)}</span>
                    </div>
                    <button class="ha-btn icon-btn" id="dismiss-backup-success-btn">
                      <ha-icon icon="mdi:close"></ha-icon>
                    </button>
                  </div>`
                : ""
            }

            <div class="backup-action-card" style="border: 1px solid var(--divider-color, #e0e0e0); border-radius: 10px; padding: 14px; margin-bottom: 12px; background: var(--secondary-background-color, rgba(127,127,127,0.04));">
              <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;">
                <div style="max-width: 360px;">
                  <strong style="font-size: 14px; display: flex; align-items: center; gap: 6px;">
                    <ha-icon icon="mdi:folder-zip-outline" style="color: var(--primary-color);"></ha-icon>
                    Full Cellar Backup (.zip)
                  </strong>
                  <div style="font-size: 12px; color: var(--secondary-text-color); margin-top: 4px;">
                    Creates a complete archive containing <code>homecave.db</code>, all bottle photos (<code>covers/</code>), and metadata.
                  </div>
                </div>
                <button class="ha-btn primary-btn" id="btn-create-backup" ${bs.busy ? "disabled" : ""}>
                  <ha-icon icon="mdi:download"></ha-icon>
                  <span>${bs.busy ? "Creating…" : "Download Backup (.zip)"}</span>
                </button>
              </div>
            </div>

            <div class="backup-action-card" style="border: 1px solid var(--divider-color, #e0e0e0); border-radius: 10px; padding: 14px; margin-bottom: 12px; background: var(--secondary-background-color, rgba(127,127,127,0.04));">
              <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;">
                <div style="max-width: 360px;">
                  <strong style="font-size: 14px; display: flex; align-items: center; gap: 6px;">
                    <ha-icon icon="mdi:database-refresh" style="color: var(--warning-color, #ef6c00);"></ha-icon>
                    Restore Database from Backup (.zip)
                  </strong>
                  <div style="font-size: 12px; color: var(--secondary-text-color); margin-top: 4px;">
                    Restores your database and cover images. <em>Warning: Replaces active cellar data.</em>
                  </div>
                </div>
                <div>
                  <input type="file" id="restore-file-input" accept=".zip" style="display: none;" />
                  <button class="ha-btn secondary-btn" id="btn-select-restore" ${bs.busy ? "disabled" : ""}>
                    <ha-icon icon="mdi:upload"></ha-icon>
                    <span>Restore .zip Backup</span>
                  </button>
                </div>
              </div>
            </div>

            <div class="backup-action-card" style="border: 1px solid var(--divider-color, #e0e0e0); border-radius: 10px; padding: 14px; margin-bottom: 16px; background: var(--secondary-background-color, rgba(127,127,127,0.04));">
              <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;">
                <div style="max-width: 360px;">
                  <strong style="font-size: 14px; display: flex; align-items: center; gap: 6px;">
                    <ha-icon icon="mdi:file-import-outline" style="color: #2e7d32;"></ha-icon>
                    Import Wines (JSON / CSV / ZIP)
                  </strong>
                  <div style="font-size: 12px; color: var(--secondary-text-color); margin-top: 4px;">
                    Batch imports bottles from JSON, CSV, or another wine backup file without wiping existing wines.
                  </div>
                </div>
                <div>
                  <input type="file" id="import-file-input" accept=".json,.csv,.zip" style="display: none;" />
                  <button class="ha-btn secondary-btn" id="btn-select-import" ${bs.busy ? "disabled" : ""}>
                    <ha-icon icon="mdi:file-upload-outline"></ha-icon>
                    <span>Import File</span>
                  </button>
                </div>
              </div>
            </div>

            ${
              (bs.backups || []).length > 0
                ? `
                <div class="section-divider" style="margin-bottom: 10px;">Available On-Disk Backups (${bs.backups.length})</div>
                <div style="display: flex; flex-direction: column; gap: 8px; max-height: 180px; overflow-y: auto;">
                  ${bs.backups
                    .map(
                      (b) => `
                    <div style="display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border: 1px solid var(--divider-color, #e0e0e0); border-radius: 8px; font-size: 12px;">
                      <div>
                        <strong>${escapeHtml(b.filename)}</strong>
                        <div style="color: var(--secondary-text-color); font-size: 11px;">
                          ${(b.size_bytes / 1024).toFixed(1)} KB • ${escapeHtml(formatDisplayDate(b.created_at))}
                        </div>
                      </div>
                      <div style="display: flex; gap: 6px;">
                        ${
                          b.download_url
                            ? `<a class="ha-btn secondary-btn" href="${escapeHtml(b.download_url)}" download="${escapeHtml(b.filename)}" style="padding: 4px 8px; font-size: 11px; text-decoration: none;">Download</a>`
                            : ""
                        }
                        <button class="ha-btn secondary-btn" data-restore-server-filename="${escapeHtml(b.filename)}" style="padding: 4px 8px; font-size: 11px;">Restore</button>
                      </div>
                    </div>
                  `
                    )
                    .join("")}
                </div>
              `
                : ""
            }
          </div>
        </div>
      </div>
    `;
  }

  _renderCropRotateModal() {
    return `
      <div class="modal-backdrop" id="modal-backdrop">
        <div class="modal-surface crop-modal-surface">
          <div class="modal-header">
            <h2>
              <ha-icon icon="mdi:crop-rotate"></ha-icon>
              <span>Crop &amp; Rotate Bottle Label</span>
            </h2>
            <button class="ha-btn icon-btn" id="close-crop-btn" title="Cancel">
              <ha-icon icon="mdi:close"></ha-icon>
            </button>
          </div>

          <div class="modal-body crop-modal-body">
            <div class="crop-instructions">
              <ha-icon icon="mdi:information-outline"></ha-icon>
              <span>Rotate so the wine label is upright, then drag the box &amp; corner handles to frame only the label for optimal recognition.</span>
            </div>

            <div class="canvas-wrapper" id="canvas-wrapper">
              <canvas id="crop-canvas"></canvas>
            </div>

            <div class="crop-controls-row">
              <button type="button" class="ha-btn secondary-btn" id="btn-rotate-left">
                <ha-icon icon="mdi:rotate-left"></ha-icon>
                <span>Rotate Left (90°)</span>
              </button>
              <button type="button" class="ha-btn secondary-btn" id="btn-rotate-right">
                <ha-icon icon="mdi:rotate-right"></ha-icon>
                <span>Rotate Right (90°)</span>
              </button>
              <button type="button" class="ha-btn secondary-btn" id="btn-reset-crop">
                <ha-icon icon="mdi:refresh"></ha-icon>
                <span>Reset Frame</span>
              </button>
            </div>
          </div>

          <div class="modal-actions full-width crop-footer">
            <button type="button" class="ha-btn secondary-btn" id="btn-cancel-crop">Cancel</button>
            <button type="button" class="ha-btn primary-btn" id="btn-confirm-crop">
              <ha-icon icon="mdi:check"></ha-icon>
              <span>Confirm &amp; Scan Label</span>
            </button>
          </div>
        </div>
      </div>
    `;
  }

  _renderScanModal() {
    const s = this._scanState;
    const f = s.form;
    const knownStores = this._statistics.known_purchase_locations || [];
    const knownLocations = this._getAvailableStorageAreas();

    return `
      <div class="modal-backdrop" id="modal-backdrop">
        <div class="modal-surface">
         <div class="modal-inner">
          <div class="modal-cq">
          <div class="modal-header">
            <h2>
              <ha-icon icon="${s.manualOnly ? "mdi:plus-circle-outline" : "mdi:camera-iris"}"></ha-icon>
              <span>${s.manualOnly ? "Add Wine to Inventory" : "Scan Wine Bottle with AI Model"}</span>
            </h2>
            <div class="modal-header-actions">
              <button type="button" class="ha-btn secondary-btn icon-btn btn-font-scale" title="Text size (tap to change)" aria-label="Text size ${this._fontScaleLabel()}">
                <span class="font-scale-label">Aa <small>${this._fontScaleLabel()}</small></span>
              </button>
              <button class="ha-btn icon-btn" id="close-modal-btn"><ha-icon icon="mdi:close"></ha-icon></button>
            </div>
          </div>

          <div class="modal-body">
            ${
              !s.manualOnly
                ? `<div class="scan-capture-box">
                    <div class="scan-buttons-row">
                      <label class="ha-btn primary-btn file-label">
                        <ha-icon icon="mdi:camera"></ha-icon>
                        <span>Take Photo (Camera)</span>
                        <input type="file" id="bottle-file-input" accept="image/*" capture="environment" hidden />
                      </label>
                      <label class="ha-btn secondary-btn file-label">
                        <ha-icon icon="mdi:image-multiple-outline"></ha-icon>
                        <span>Choose from Library</span>
                        <input type="file" id="bottle-library-input" accept="image/*" hidden />
                      </label>
                      ${
                        this._cropState?.image
                          ? `<button type="button" class="ha-btn secondary-btn" id="reopen-crop-btn">
                              <ha-icon icon="mdi:crop-rotate"></ha-icon>
                              <span>Adjust Crop &amp; Rotate</span>
                            </button>`
                          : ""
                      }
                    </div>
                    ${
                      s.busy
                        ? `<div class="scan-status-banner">
                            <ha-icon icon="mdi:loading"></ha-icon>
                            <span>Analyzing bottle label with AI Model &amp; looking up Vivino rating…</span>
                          </div>`
                        : ""
                    }
                    ${
                      s.error
                        ? `<div class="scan-error-card" role="alert">
                            <div class="scan-error-header">
                              <div class="scan-error-title-wrap">
                                <ha-icon icon="mdi:alert-circle" style="color: var(--error-color, #c62828);"></ha-icon>
                                <strong style="color: var(--error-color, #c62828);">Scan Recognition Error</strong>
                              </div>
                              <button type="button" class="ha-btn icon-btn" id="dismiss-scan-error-btn" title="Dismiss Error">
                                <ha-icon icon="mdi:close"></ha-icon>
                              </button>
                            </div>
                            <div class="scan-error-body">
                              <p class="scan-error-message">${escapeHtml(s.error)}</p>
                              <div class="scan-error-hint">
                                <strong>Troubleshooting tips:</strong>
                                <ul>
                                  <li>Check that your AI API key (Gemini or Claude) is valid in HomeCave integration options.</li>
                                  <li>Click <em>Adjust Crop &amp; Rotate</em> to orient the label upright and frame only the printed front label.</li>
                                  <li>Or type the winery, wine name, and vintage manually into the fields below.</li>
                                </ul>
                              </div>
                            </div>
                            <div class="scan-error-actions">
                              ${
                                this._cropState?.image
                                  ? `<button type="button" class="ha-btn primary-btn" id="retry-crop-btn">
                                      <ha-icon icon="mdi:crop-rotate"></ha-icon>
                                      <span>Adjust Crop &amp; Retry</span>
                                    </button>`
                                  : ""
                              }
                              <button type="button" class="ha-btn secondary-btn" id="clear-scan-error-btn">Dismiss</button>
                            </div>
                          </div>`
                        : ""
                    }
                    ${
                      s.existingWine
                        ? `<div class="scan-existing-banner">
                            <ha-icon icon="mdi:check-circle-outline"></ha-icon>
                            <div>
                              <strong>Already in your cellar!</strong> Currently <strong>${num(s.existingWine.quantity)}</strong> bottle(s) in stock
                              (Last bought at: <em>${escapeHtml(s.existingWine.last_purchase_location || "—")}</em>,
                              Location: <em>${escapeHtml(s.existingWine.last_storage_location || "—")}</em>).
                              Saving will increment the bottle count and log this new addition batch.
                            </div>
                          </div>`
                        : ""
                    }
                    ${
                      s.vivinoCandidates && s.vivinoCandidates.length > 0
                        ? `<div class="form-field full-width">
                            ${
                              s.vivinoConfidence === "high"
                                ? `<div class="vivino-tier-banner tier-high" style="margin-bottom:6px; padding:8px 12px; border-radius:8px; background:rgba(46,125,50,0.10); border:1px solid rgba(46,125,50,0.4); font-size:12px;">
                                     <strong>✓ Verified Vivino match</strong>: producer and cuvée match exactly and the AI check confirmed the label.
                                   </div>`
                                : `<div class="vivino-tier-banner tier-medium" style="margin-bottom:6px; padding:8px 12px; border-radius:8px; background:rgba(245,124,0,0.10); border:1px solid rgba(245,124,0,0.45); font-size:12px;">
                                     <strong>Please confirm</strong>: we found possible Vivino records but could not verify which one is your bottle. Nothing is linked unless you pick one.
                                     ${s.vivinoSuggestedIndex >= 0 ? `<br/>AI suggestion: <em>${escapeHtml(s.vivinoCandidates[s.vivinoSuggestedIndex]?.name || "")}</em>` : ""}
                                   </div>`
                            }
                            <label>Vivino Match Selection (select candidate or leave unlinked)</label>
                            <select id="vivino-candidate-select" class="ha-input">
                              <option value="-1" ${s.selectedVivinoIndex === -1 ? "selected" : ""}>
                                🚫 Do not link to Vivino (Leave unlinked)
                              </option>
                              ${s.vivinoCandidates
                                .map(
                                  (c, idx) =>
                                    `<option value="${idx}" ${s.selectedVivinoIndex === idx ? "selected" : ""}>
                                      ${c.prefilter_exact ? "✓ " : ""}${escapeHtml(c.winery)}: ${escapeHtml(c.name)} (${escapeHtml(c.vintage || "NV")}) – ★ ${escapeHtml(c.vivino_rating ?? "?")} [${num(c.vivino_ratings_count).toLocaleString()} ratings]
                                    </option>`
                                )
                                .join("")}
                            </select>
                          </div>`
                        : s.form.vivino_rating || s.form.vivino_url
                        ? `<div class="form-field full-width">
                            <label>Vivino Match</label>
                            <div style="display:flex; justify-content:space-between; align-items:center; padding: 6px 12px; background:var(--secondary-background-color, rgba(127,127,127,0.08)); border-radius:8px;">
                              <span style="font-size: 13px;">★ ${escapeHtml(s.form.vivino_rating)} (Vivino)</span>
                              <button type="button" class="ha-btn secondary-btn" id="btn-disconnect-vivino" style="padding: 4px 8px; font-size: 12px;">
                                <ha-icon icon="mdi:link-variant-off"></ha-icon>
                                <span>Do Not Link</span>
                              </button>
                            </div>
                          </div>`
                        : ""
                    }
                  </div>`
                : ""
            }

            <div class="ha-scan-dest-toggle" role="group" aria-label="Save destination">
              <button type="button" class="ha-scan-dest-btn ${!f.as_tasted ? "active" : ""}" data-ha-scan-dest="cellar">
                🍷 Add to Cellar
              </button>
              <button type="button" class="ha-scan-dest-btn ${f.as_tasted ? "active tasted" : ""}" data-ha-scan-dest="tasted">
                🥂 Add to Tasted
              </button>
            </div>
            <div style="font-size: 12px; color: var(--secondary-text-color); margin-bottom: 12px;">
              ${
                f.as_tasted
                  ? "Logs this bottle directly to your <strong>Tasted</strong> collection without adding bottles to cellar stock."
                  : "Adds bottle(s) to your active wine cellar inventory."
              }
            </div>

            <form id="scan-add-form" class="form-grid">
              <div class="form-field">
                <label>Winery / Producer *</label>
                <input class="ha-input" name="winery" required value="${escapeHtml(f.winery)}" placeholder="e.g. Château Margaux" />
              </div>
              <div class="form-field">
                <label>Wine Name / Cuvée *</label>
                <input class="ha-input" name="name" required value="${escapeHtml(f.name)}" placeholder="e.g. Pavillon Rouge" />
              </div>
              <div class="form-field">
                <label>Vintage Year (blank = NV)</label>
                <input class="ha-input" type="number" name="vintage" min="1900" max="2100" value="${escapeHtml(f.vintage)}" />
              </div>
              <div class="form-field">
                <label>Wine Type</label>
                <select class="ha-input" name="wine_type">
                  ${Object.entries(WINE_TYPE_LABELS)
                    .map(
                      ([k, lbl]) =>
                        `<option value="${k}" ${f.wine_type === k ? "selected" : ""}>${lbl}</option>`
                    )
                    .join("")}
                </select>
              </div>
              <div class="form-field">
                <label>Varietal / Grapes</label>
                <input class="ha-input" name="varietal" value="${escapeHtml(f.varietal)}" placeholder="e.g. Cabernet Sauvignon, Merlot" />
              </div>
              <div class="form-field">
                <label>Region &amp; Country</label>
                <div class="split-2">
                  <input class="ha-input" name="region" value="${escapeHtml(f.region)}" placeholder="Region" />
                  <input class="ha-input" name="country" value="${escapeHtml(f.country)}" placeholder="Country" />
                </div>
              </div>
              <div class="form-field">
                <label>Vivino Rating (1.0–5.0)</label>
                <input class="ha-input" type="number" step="0.1" min="1" max="5" name="vivino_rating" value="${escapeHtml(f.vivino_rating)}" />
              </div>
              <div class="form-field">
                <label>Vivino URL</label>
                <input class="ha-input" name="vivino_url" value="${escapeHtml(f.vivino_url)}" placeholder="https://www.vivino.com/..." />
              </div>

              <div class="section-divider full-width">${f.as_tasted ? "Tasting Metadata, Rating &amp; Notes" : "Addition Metadata, Drinking Window &amp; Notes"}</div>

              ${
                f.as_tasted
                  ? `<div class="form-field">
                      <label>My Personal Rating (1.0–5.0)</label>
                      <input class="ha-input" type="number" step="0.1" min="1" max="5" name="personal_rating" value="${escapeHtml(f.personal_rating)}" placeholder="e.g. 4.4" />
                    </div>`
                  : `<div class="form-field">
                      <label>Quantity Added *</label>
                      <input class="ha-input" type="number" name="quantity" min="1" max="120" required value="${escapeHtml(f.quantity)}" />
                    </div>`
              }
              <div class="form-field">
                <label>${f.as_tasted ? "Date / Time Tasted *" : "Date / Time Added *"}</label>
                <input class="ha-input" type="datetime-local" name="added_at" required value="${escapeHtml(f.added_at)}" />
              </div>
              <div class="form-field">
                <label>${f.as_tasted ? "Where Was It Tasted / Bought?" : "Where Was It Bought?"}</label>
                <input class="ha-input" name="purchase_location" list="known-stores-list" value="${escapeHtml(f.purchase_location)}" placeholder="Store, winery, restaurant, or gift" />
                <datalist id="known-stores-list">
                  ${knownStores.map((st) => `<option value="${escapeHtml(st)}"></option>`).join("")}
                </datalist>
              </div>
              ${
                f.as_tasted
                  ? `<div class="form-field">
                      <label>Price Paid (${escapeHtml(this._currency)})</label>
                      <input class="ha-input" type="number" step="0.01" name="purchase_price" value="${escapeHtml(f.purchase_price)}" placeholder="Price" />
                    </div>`
                  : `<div class="form-field">
                      <label>Purchase Price (${escapeHtml(this._currency)}) &amp; Storage Slot</label>
                      <div class="split-2">
                        <input class="ha-input" type="number" step="0.01" name="purchase_price" value="${escapeHtml(f.purchase_price)}" placeholder="Price" />
                        <input class="ha-input" name="storage_location" list="known-locations-list" value="${escapeHtml(f.storage_location)}" placeholder="Fridge / Rack / Row" />
                      </div>
                      <datalist id="known-locations-list">
                        ${knownLocations.map((loc) => `<option value="${escapeHtml(loc)}"></option>`).join("")}
                      </datalist>
                    </div>`
              }

              <div class="form-field">
                <label>When to Drink (From Year – Until Year)</label>
                <div class="split-2">
                  <input class="ha-input" type="number" name="drink_from_year" min="1950" max="2100" value="${escapeHtml(f.drink_from_year)}" placeholder="From (e.g. 2025)" />
                  <input class="ha-input" type="number" name="drink_until_year" min="1950" max="2100" value="${escapeHtml(f.drink_until_year)}" placeholder="Until (e.g. 2032)" />
                </div>
              </div>
              <div class="form-field">
                <label>When to Drink Guidance / Occasion</label>
                <input class="ha-input" name="drink_window_note" value="${escapeHtml(f.drink_window_note)}" placeholder="e.g. Decant 2h, drink with steak" />
              </div>

              <div class="form-field full-width">
                <label>Free-Form Notes Overview (Master Wine Overview)</label>
                <textarea class="ha-textarea" name="general_notes" rows="2" placeholder="Overall wine summary, aromas, pairings, or cellar notes...">${escapeHtml(f.general_notes)}</textarea>
              </div>
              <div class="form-field full-width">
                <label>${f.as_tasted ? "Tasting Impressions &amp; Occasion Notes" : "Batch / Addition Note (Specific to this bottle purchase)"}</label>
                <textarea class="ha-textarea" name="batch_notes" rows="2" placeholder="${f.as_tasted ? "How did it taste? Who did you share it with?" : "Optional note for this specific purchase batch..."}">${escapeHtml(f.batch_notes)}</textarea>
              </div>

              <div class="modal-actions full-width">
                <button type="button" class="ha-btn secondary-btn" id="cancel-modal-btn">Cancel</button>
                <button type="submit" class="ha-btn primary-btn" ${s.busy ? "disabled" : ""}>
                  <ha-icon icon="mdi:check"></ha-icon>
                  <span>${f.as_tasted ? "🥂 Save Wine to Tasted" : s.existingWine ? "Add Bottle Batch (+)" : "Save Wine to Cellar"}</span>
                </button>
              </div>
            </form>
          </div>
          </div>
         </div>
        </div>
      </div>
    `;
  }

  _renderQuickAddModal() {
    const w = this._modalWine;
    const qf = this._quickAddForm;
    if (!w || !qf) return "";
    const knownStores = this._statistics.known_purchase_locations || [];
    const knownLocations = this._getAvailableStorageAreas();

    return `
      <div class="modal-backdrop" id="modal-backdrop">
        <div class="modal-surface compact">
         <div class="modal-inner">
          <div class="modal-cq">
          <div class="modal-header">
            <h2><ha-icon icon="mdi:plus-circle"></ha-icon> <span>Add Bottle(s): ${escapeHtml(w.winery)} – ${escapeHtml(w.name)}</span></h2>
            <div class="modal-header-actions">
              <button type="button" class="ha-btn secondary-btn icon-btn btn-font-scale" title="Text size (tap to change)" aria-label="Text size ${this._fontScaleLabel()}">
                <span class="font-scale-label">Aa <small>${this._fontScaleLabel()}</small></span>
              </button>
              <button class="ha-btn icon-btn" id="close-modal-btn"><ha-icon icon="mdi:close"></ha-icon></button>
            </div>
          </div>
          <div class="modal-body">
            <form id="quick-add-form" class="form-grid">
              <div class="form-field">
                <label>Bottles to Add (+)</label>
                <input class="ha-input" type="number" name="quantity" min="1" max="120" required value="${escapeHtml(qf.quantity)}" />
              </div>
              <div class="form-field">
                <label>Date / Time Added</label>
                <input class="ha-input" type="datetime-local" name="added_at" required value="${escapeHtml(qf.added_at)}" />
              </div>
              <div class="form-field">
                <label>Where Was It Bought?</label>
                <input class="ha-input" name="purchase_location" list="qa-stores" value="${escapeHtml(qf.purchase_location)}" placeholder="Store or winery" />
                <datalist id="qa-stores">
                  ${knownStores.map((st) => `<option value="${escapeHtml(st)}"></option>`).join("")}
                </datalist>
              </div>
              <div class="form-field">
                <label>Storage Location &amp; Price</label>
                <div class="split-2">
                  <input class="ha-input" name="storage_location" list="qa-locations" value="${escapeHtml(qf.storage_location)}" placeholder="Fridge / Rack" />
                  <input class="ha-input" type="number" step="0.01" name="purchase_price" value="${escapeHtml(qf.purchase_price)}" placeholder="Price" />
                </div>
                <datalist id="qa-locations">
                  ${knownLocations.map((loc) => `<option value="${escapeHtml(loc)}"></option>`).join("")}
                </datalist>
              </div>
              <div class="form-field full-width">
                <label>When to Drink Guidance</label>
                <input class="ha-input" name="drink_window_note" value="${escapeHtml(qf.drink_window_note)}" placeholder="e.g. Ready autumn 2026" />
              </div>
              <div class="form-field full-width">
                <label>Addition Note (Optional)</label>
                <textarea class="ha-textarea" name="notes" rows="2" placeholder="Where/why you bought it, occasion, etc.">${escapeHtml(qf.notes)}</textarea>
              </div>
              <div class="modal-actions full-width">
                <button type="button" class="ha-btn secondary-btn" id="cancel-modal-btn">Cancel</button>
                <button type="submit" class="ha-btn primary-btn">
                  <ha-icon icon="mdi:plus"></ha-icon>
                  <span>Save Addition</span>
                </button>
              </div>
            </form>
          </div>
          </div>
         </div>
        </div>
      </div>
    `;
  }

  _renderConsumeModal() {
    const w = this._modalWine;
    const cf = this._consumeForm;
    if (!w || !cf) return "";
    const activeBatches = (w.batches || []).filter((b) => Number(b.quantity_remaining) > 0);

    return `
      <div class="modal-backdrop" id="modal-backdrop">
        <div class="modal-surface compact">
         <div class="modal-inner">
          <div class="modal-cq">
          <div class="modal-header">
            <h2><ha-icon icon="mdi:glass-wine"></ha-icon> <span>Open / Drink Bottle: ${escapeHtml(w.winery)} – ${escapeHtml(w.name)}</span></h2>
            <div class="modal-header-actions">
              <button type="button" class="ha-btn secondary-btn icon-btn btn-font-scale" title="Text size (tap to change)" aria-label="Text size ${this._fontScaleLabel()}">
                <span class="font-scale-label">Aa <small>${this._fontScaleLabel()}</small></span>
              </button>
              <button class="ha-btn icon-btn" id="close-modal-btn"><ha-icon icon="mdi:close"></ha-icon></button>
            </div>
          </div>
          <div class="modal-body">
            <form id="consume-form" class="form-grid">
              <div class="form-field">
                <label>Bottles Opened</label>
                <input class="ha-input" type="number" name="delta" min="1" max="${Math.max(1, num(w.quantity))}" required value="1" />
              </div>
              <div class="form-field">
                <label>Your Personal Rating (1.0–5.0)</label>
                <input class="ha-input" type="number" step="0.1" min="1" max="5" name="personal_rating" value="${escapeHtml(cf.personal_rating)}" placeholder="e.g. 4.5" />
              </div>
              ${
                activeBatches.length > 0
                  ? `<div class="form-field full-width">
                      <label>Pull from Batch / Storage Location</label>
                      <select class="ha-input" name="batch_id">
                        ${activeBatches
                          .map(
                            (b) =>
                              `<option value="${num(b.id)}" ${cf.batch_id === b.id ? "selected" : ""}>
                                ${escapeHtml(b.storage_location || "Unspecified Slot")} • Bought at ${escapeHtml(
                                b.purchase_location || "—"
                              )} (${formatDisplayDate(b.added_at)}) — ${num(b.quantity_remaining)} left
                              </option>`
                          )
                          .join("")}
                      </select>
                    </div>`
                  : ""
              }
              <div class="form-field full-width">
                <label>Tasting Notes (Optional)</label>
                <textarea class="ha-textarea" name="notes" rows="3" placeholder="How did it taste? What did you pair it with? Would you buy again?">${escapeHtml(cf.notes)}</textarea>
              </div>
              <div class="modal-actions full-width">
                <button type="button" class="ha-btn secondary-btn" id="cancel-modal-btn">Cancel</button>
                <button type="submit" class="ha-btn primary-btn">
                  <ha-icon icon="mdi:check"></ha-icon>
                  <span>Confirm &amp; Log Tasting</span>
                </button>
              </div>
            </form>
          </div>
          </div>
         </div>
        </div>
      </div>
    `;
  }

  _renderDetailModal() {
    const w = this._modalWine;
    const df = this._detailForm;
    if (!w || !df) return "";

    const statusMeta = DRINK_STATUS_META[w.drink_status] || DRINK_STATUS_META.unknown;
    const typeLabel = WINE_TYPE_LABELS[w.wine_type] || "Wine";
    const drinkYears =
      w.drink_from_year || w.drink_until_year
        ? `${w.drink_from_year || "Now"}–${w.drink_until_year || "…"}`
        : statusMeta.label;
    const availableAreas = this._getAvailableStorageAreas();

    let research = null;
    if (w.research_notes) {
      try {
        research = typeof w.research_notes === "string" ? JSON.parse(w.research_notes) : w.research_notes;
      } catch (e) {
        research = { summary: String(w.research_notes) };
      }
    }

    return `
      <div class="modal-backdrop" id="modal-backdrop">
        <div class="modal-surface detail-modal-surface">
         <div class="modal-inner">
          <div class="modal-cq">
          <div class="modal-header">
            <h2><ha-icon icon="mdi:notebook-edit-outline"></ha-icon> <span>${escapeHtml(w.winery)} – ${escapeHtml(w.name)} (${escapeHtml(w.vintage || "NV")})</span></h2>
            <div class="modal-header-actions">
              <button type="button" class="ha-btn secondary-btn icon-btn btn-font-scale" id="btn-detail-font-scale" title="Adjust text size (S / M / L / XL)" aria-label="Text size ${this._fontScaleLabel()}">
                <span class="font-scale-label">Aa <small>${this._fontScaleLabel()}</small></span>
              </button>
              <button class="ha-btn icon-btn" id="close-modal-btn" title="Close"><ha-icon icon="mdi:close"></ha-icon></button>
            </div>
          </div>
          <div class="modal-body detail-modal-body">
            <div class="detail-overview-header">
              <div class="detail-thumb-wrap" title="Click to view or change bottle photo">
                ${
                  safeImageUrl(w.image_url)
                    ? `<img class="detail-wine-thumb preview-image-clickable" src="${safeImageUrl(w.image_url)}" alt="${escapeHtml(w.name)}" data-action="preview-image" data-id="${num(w.id)}" />
                       <div class="thumb-zoom-overlay">
                         <ha-icon icon="mdi:camera" style="--mdc-icon-size: 14px;"></ha-icon>
                       </div>`
                    : `<div class="wine-thumb-placeholder detail-wine-thumb preview-image-clickable type-${escapeHtml(w.wine_type)}" data-action="preview-image" data-id="${num(w.id)}" style="cursor: pointer;" title="Click to add photo">
                         <ha-icon icon="mdi:camera-plus-outline"></ha-icon>
                         <span style="font-size: 10px; margin-top: 2px;">Add Photo</span>
                       </div>`
                }
              </div>
              <div class="detail-overview-main">
                <div class="detail-badges-row">
                  <span class="type-pill detail-pill type-${escapeHtml(w.wine_type)}">${escapeHtml(typeLabel)}</span>
                  <span class="vintage-pill detail-pill">📅 ${escapeHtml(w.vintage ? String(w.vintage) : "NV")}</span>
                  ${
                    Boolean(w.is_imported || df.is_imported)
                      ? `<span class="imported-pill detail-pill" title="Imported wine" style="background: rgba(217,119,6,0.12); color: #b45309; border: 1px solid rgba(217,119,6,0.3); font-weight: 600; display: inline-flex; align-items: center; gap: 4px;"><ha-icon icon="mdi:package-down" style="--mdc-icon-size: 14px;"></ha-icon> <span>📥 Imported</span></span>`
                      : ""
                  }
                  ${w.varietal ? `<span class="filter-chip detail-pill">🍇 ${escapeHtml(w.varietal)}</span>` : ""}
                  <span class="drink-pill detail-pill" style="border-color:${statusMeta.color}; color:${statusMeta.color};">
                    ${escapeHtml(statusMeta.label)} (${escapeHtml(drinkYears)})
                  </span>
                </div>
                <div class="detail-winery-text">${escapeHtml(w.winery)}</div>
                <div class="detail-wine-title">${escapeHtml(w.name)}</div>
                <div class="detail-origin-line">
                  🌍 ${escapeHtml([w.region, w.country].filter(Boolean).join(" • ") || "Origin not specified")}
                  ${(w.storage_location || w.last_storage_location) ? ` • <strong style="color: var(--primary-color);">📍 ${escapeHtml(w.storage_location || w.last_storage_location)}</strong>` : ""}
                  ${(df.purchase_location || w.purchase_location || w.last_purchase_location) ? ` • <span>🛒 ${escapeHtml(df.purchase_location || w.purchase_location || w.last_purchase_location)}</span>` : ""}
                  ${(df.purchase_price !== "" && df.purchase_price !== null && df.purchase_price !== undefined) ? ` • <span>💰 ${escapeHtml(String(df.purchase_price))} ${escapeHtml(this._currency)}</span>` : (w.purchase_price !== null && w.purchase_price !== undefined) ? ` • <span>💰 ${escapeHtml(String(w.purchase_price))} ${escapeHtml(this._currency)}</span>` : ""}
                </div>
                <div class="detail-ratings-bar">
                  ${
                    safeExternalUrl(w.vivino_url)
                      ? `<a class="vivino-badge detail-vivino-badge" href="${safeExternalUrl(w.vivino_url)}" target="_blank" rel="noopener noreferrer" title="Open on Vivino (opens in new tab)">
                           ★ ${escapeHtml(w.vivino_rating ? Number(w.vivino_rating).toFixed(1) : "Vivino")}
                           <ha-icon icon="mdi:open-in-new" style="--mdc-icon-size: 13px; margin-left: 2px;"></ha-icon>
                         </a>`
                      : w.vivino_rating
                      ? `<span class="vivino-badge detail-vivino-badge">★ ${escapeHtml(Number(w.vivino_rating).toFixed(1))}</span>`
                      : `<span class="vivino-badge muted detail-vivino-badge">No Vivino rating</span>`
                  }
                  ${
                    (w.vivino_url || w.vivino_rating || df.vivino_url || df.vivino_rating)
                      ? `<button type="button" class="ha-btn secondary-btn detail-small-btn" id="btn-detail-unlink-vivino" data-id="${num(w.id)}" style="color: var(--error-color, #c62828); border-color: rgba(198, 40, 40, 0.35);" title="Remove incorrect Vivino link and ratings">
                           <ha-icon icon="mdi:link-off" style="--mdc-icon-size: 14px;"></ha-icon>
                           <span>Remove Vivino Link</span>
                         </button>`
                      : ""
                  }
                  ${
                    w.vivino_url && (w.vivino_match_confidence === "high" || w.vivino_match_confidence === "confirmed")
                      ? `<span class="filter-chip detail-pill" style="border-color: rgba(46,125,50,0.5); color: #2e7d32;" title="${w.vivino_match_confidence === "high" ? "Automatically verified: exact producer + cuvée and AI label check" : "Confirmed by you"}">✓ ${w.vivino_match_confidence === "high" ? "Verified" : "Confirmed"}</span>`
                      : ""
                  }
                  ${w.personal_rating ? `<span class="personal-badge detail-personal-badge">My Rating: ★ ${num(w.personal_rating)}</span>` : ""}
                </div>

                <!-- Bottle Quantity Stepper (+ / -) with 0-Bottle Warning -->
                <div class="detail-stepper-wrap">
                  <span class="detail-stepper-label">In Cellar:</span>
                  <div class="stepper-controls">
                    <button type="button" class="ha-btn icon-btn detail-step-btn" id="btn-detail-qty-minus" data-id="${num(w.id)}" data-qty="${num(w.quantity)}" title="Decrease bottle count (-1)">
                      <ha-icon icon="mdi:minus"></ha-icon>
                    </button>
                    <span id="detail-qty-display" class="detail-qty-value">
                      ${Number(w.quantity || 0)}
                    </span>
                    <button type="button" class="ha-btn icon-btn detail-step-btn" id="btn-detail-qty-plus" data-id="${num(w.id)}" data-qty="${num(w.quantity)}" title="Increase bottle count (+1)">
                      <ha-icon icon="mdi:plus"></ha-icon>
                    </button>
                  </div>
                  <span class="detail-stepper-unit">${Number(w.quantity) === 1 ? "bottle" : "bottles"}</span>
                  <button type="button" class="ha-btn secondary-btn detail-research-btn" id="btn-detail-research-wine" data-id="${num(w.id)}" title="Deep AI research on producer, terroir & cuvée history" ${this._researchingWineId === w.id ? "disabled" : ""}>
                    <ha-icon icon="mdi:auto-fix" style="color: var(--primary-color);"></ha-icon>
                    <span>${this._researchingWineId === w.id ? "Researching..." : "Research Wine (AI)"}</span>
                  </button>
                </div>
              </div>
            </div>

            <form id="detail-form" class="form-grid">
              <div class="form-field">
                <label>Winery / Producer</label>
                <input class="ha-input" name="winery" value="${escapeHtml(df.winery || "")}" placeholder="Producer / Estate" />
              </div>
              <div class="form-field">
                <label>Wine / Cuvée Name</label>
                <input class="ha-input" name="name" value="${escapeHtml(df.name || "")}" placeholder="Wine Name" />
              </div>

              <!-- Grape variety, Year, Country of Origin fields -->
              <div class="form-field">
                <label>Grape Variety / Blend</label>
                <input class="ha-input" name="varietal" value="${escapeHtml(df.varietal || "")}" placeholder="e.g. Cabernet Sauvignon, Pinot Noir" />
              </div>
              <div class="form-field">
                <label>Harvest Year / Vintage</label>
                <input class="ha-input" type="number" name="vintage" value="${escapeHtml(df.vintage || "")}" placeholder="e.g. 2020 (leave blank for NV)" />
              </div>
              <div class="form-field">
                <label>Country of Origin</label>
                <input class="ha-input" name="country" value="${escapeHtml(df.country || "")}" placeholder="e.g. France, Italy, Spain, USA" />
              </div>
              <div class="form-field">
                <label>Region / Appellation</label>
                <input class="ha-input" name="region" value="${escapeHtml(df.region || "")}" placeholder="e.g. Bordeaux, Tuscany, Rioja" />
              </div>

              <!-- Storage Location / Area selection -->
              <div class="form-field">
                <label>Storage Area / Location</label>
                <input class="ha-input" name="storage_location" list="detail-storage-areas" value="${escapeHtml(df.storage_location || "")}" placeholder="Select or type area (e.g. Wine Fridge, Cellar)" />
                <datalist id="detail-storage-areas">
                  ${availableAreas.map((loc) => `<option value="${escapeHtml(loc)}"></option>`).join("")}
                </datalist>
              </div>

              <!-- Bought At and Purchase Price fields -->
              <div class="form-field">
                <label>Bought At (Store / Vendor)</label>
                <input class="ha-input" name="purchase_location" value="${escapeHtml(df.purchase_location || "")}" placeholder="e.g. Gall & Gall, Local Winery, Online" />
              </div>
              <div class="form-field">
                <label>Purchase Price (${escapeHtml(this._currency)})</label>
                <input class="ha-input" type="number" step="0.01" name="purchase_price" value="${escapeHtml(df.purchase_price !== '' && df.purchase_price !== null && df.purchase_price !== undefined ? String(df.purchase_price) : "")}" placeholder="e.g. 19.99" />
              </div>

              <div class="form-field full-width">
                <label>Free-Form Notes Overview</label>
                <textarea class="ha-textarea" name="general_notes" rows="3" placeholder="Master overview notes for this wine...">${escapeHtml(df.general_notes)}</textarea>
              </div>

              <div class="form-field">
                <label>Drink Window (From – Until Year)</label>
                <div class="split-2">
                  <input class="ha-input" type="number" name="drink_from_year" value="${escapeHtml(df.drink_from_year)}" placeholder="From Year" />
                  <input class="ha-input" type="number" name="drink_until_year" value="${escapeHtml(df.drink_until_year)}" placeholder="Until Year" />
                </div>
              </div>
              <div class="form-field">
                <label>When to Drink Guidance</label>
                <input class="ha-input" name="drink_window_note" value="${escapeHtml(df.drink_window_note)}" />
              </div>
              <div class="form-field">
                <label>Vivino Rating &amp; Personal Rating</label>
                <div class="split-2">
                  <input class="ha-input" type="number" step="0.1" name="vivino_rating" value="${escapeHtml(df.vivino_rating)}" placeholder="Vivino ★" />
                  <input class="ha-input" type="number" step="0.1" min="1" max="5" name="personal_rating" value="${escapeHtml(df.personal_rating)}" placeholder="My ★ (1.0–5.0)" />
                </div>
              </div>
              <div class="form-field full-width">
                <label style="display: flex; justify-content: space-between; align-items: center;">
                  <span>Vivino URL</span>
                  ${
                    safeExternalUrl(df.vivino_url)
                      ? `<a href="${safeExternalUrl(df.vivino_url)}" target="_blank" rel="noopener noreferrer" class="detail-vivino-open-link" style="font-size: 12px; color: var(--primary-color); display: inline-flex; align-items: center; gap: 3px; text-decoration: none;">
                           <span>Open in Vivino</span>
                           <ha-icon icon="mdi:open-in-new" style="--mdc-icon-size: 13px;"></ha-icon>
                         </a>`
                      : ""
                  }
                </label>
                <input class="ha-input" name="vivino_url" value="${escapeHtml(df.vivino_url)}" placeholder="https://www.vivino.com/en/.../w/1413868?year=2025" />
              </div>

              <!-- Sommelier AI Research below manual inputs -->
              <div class="full-width" style="grid-column: 1 / -1;">
                ${
                  this._researchingWineId === w.id
                    ? `<div class="research-loading-banner" style="margin: 12px 0;">
                         <ha-icon icon="mdi:loading" class="spin-icon" style="color: var(--primary-color); --mdc-icon-size: 24px; animation: spin 1s linear infinite;"></ha-icon>
                         <div>
                           <div class="research-loading-title">AI Model is researching this wine...</div>
                           <div class="research-loading-sub">Investigating winemaker heritage, cuvée history, terroir, tasting profile, and pairings.</div>
                         </div>
                       </div>`
                    : ""
                }

                ${
                  this._researchError
                    ? `<div class="scan-error-card" style="margin: 12px 0;">
                         <div class="scan-error-message"><strong>Research Failed:</strong> ${escapeHtml(this._researchError)}</div>
                       </div>`
                    : ""
                }

                ${
                  research
                    ? `<div class="research-results-card" style="margin: 12px 0;">
                         <div class="research-header-row">
                           <div class="research-header-title">
                             <ha-icon icon="mdi:auto-fix" style="color: var(--primary-color);"></ha-icon>
                             <strong>Sommelier &amp; Producer AI Research</strong>
                           </div>
                           <button type="button" class="ha-btn secondary-btn icon-btn" id="btn-re-research-wine" data-id="${num(w.id)}" title="Re-run AI Research">
                             <ha-icon icon="mdi:refresh"></ha-icon>
                           </button>
                         </div>
                         ${research.summary ? `<div class="research-summary-text">${escapeHtml(research.summary)}</div>` : ""}
                         ${
                           research.taste_profile
                             ? `
                             <div class="taste-sliders-card">
                               <div class="taste-sliders-heading">
                                 <ha-icon icon="mdi:tune-vertical-variant" style="color: var(--primary-color); --mdc-icon-size: 16px;"></ha-icon>
                                 <strong>Structural Taste Profile</strong>
                               </div>
                               <div class="taste-sliders-list">
                                 <!-- Soft vs Acidic -->
                                 <div class="taste-slider-item">
                                   <div class="taste-slider-labels">
                                     <span class="${Number(research.taste_profile.soft_vs_acidic) <= 40 ? 'taste-pole-active' : 'taste-pole-muted'}">Soft / Round</span>
                                     <span class="taste-badge">
                                       ${escapeHtml(research.taste_profile.soft_vs_acidic_label || (Number(research.taste_profile.soft_vs_acidic) < 40 ? 'Soft & Mellow' : Number(research.taste_profile.soft_vs_acidic) > 60 ? 'Crisp & Acidic' : 'Balanced Acidity'))} (${Math.round(Number(research.taste_profile.soft_vs_acidic || 50))}%)
                                     </span>
                                     <span class="${Number(research.taste_profile.soft_vs_acidic) >= 60 ? 'taste-pole-active' : 'taste-pole-muted'}">Acidic / Crisp</span>
                                   </div>
                                   <div class="taste-track">
                                     <div class="taste-fill" style="width: ${Math.max(4, Math.min(100, Number(research.taste_profile.soft_vs_acidic || 50)))}%; background: linear-gradient(90deg, #c084fc, #a61a2e);"></div>
                                     <div class="taste-thumb" style="left: ${Math.max(2, Math.min(98, Number(research.taste_profile.soft_vs_acidic || 50)))}%;"></div>
                                   </div>
                                 </div>

                                 <!-- Light vs Bold -->
                                 <div class="taste-slider-item">
                                   <div class="taste-slider-labels">
                                     <span class="${Number(research.taste_profile.light_vs_bold) <= 40 ? 'taste-pole-active' : 'taste-pole-muted'}">Light-Bodied</span>
                                     <span class="taste-badge">
                                       ${escapeHtml(research.taste_profile.light_vs_bold_label || (Number(research.taste_profile.light_vs_bold) < 40 ? 'Light & Elegant' : Number(research.taste_profile.light_vs_bold) > 60 ? 'Bold & Powerful' : 'Medium-Bodied'))} (${Math.round(Number(research.taste_profile.light_vs_bold || 50))}%)
                                     </span>
                                     <span class="${Number(research.taste_profile.light_vs_bold) >= 60 ? 'taste-pole-active' : 'taste-pole-muted'}">Bold / Full</span>
                                   </div>
                                   <div class="taste-track">
                                     <div class="taste-fill" style="width: ${Math.max(4, Math.min(100, Number(research.taste_profile.light_vs_bold || 50)))}%; background: linear-gradient(90deg, #fcd34d, #831843);"></div>
                                     <div class="taste-thumb" style="left: ${Math.max(2, Math.min(98, Number(research.taste_profile.light_vs_bold || 50)))}%;"></div>
                                   </div>
                                 </div>

                                 <!-- Dry vs Sweet -->
                                 <div class="taste-slider-item">
                                   <div class="taste-slider-labels">
                                     <span class="${Number(research.taste_profile.dry_vs_sweet) <= 40 ? 'taste-pole-active' : 'taste-pole-muted'}">Bone Dry</span>
                                     <span class="taste-badge">
                                       ${escapeHtml(research.taste_profile.dry_vs_sweet_label || (Number(research.taste_profile.dry_vs_sweet) < 25 ? 'Bone Dry' : Number(research.taste_profile.dry_vs_sweet) < 50 ? 'Dry' : Number(research.taste_profile.dry_vs_sweet) < 75 ? 'Off-Dry' : 'Sweet'))} (${Math.round(Number(research.taste_profile.dry_vs_sweet || 10))}%)
                                     </span>
                                     <span class="${Number(research.taste_profile.dry_vs_sweet) >= 60 ? 'taste-pole-active' : 'taste-pole-muted'}">Sweet / Luscious</span>
                                   </div>
                                   <div class="taste-track">
                                     <div class="taste-fill" style="width: ${Math.max(4, Math.min(100, Number(research.taste_profile.dry_vs_sweet || 10)))}%; background: linear-gradient(90deg, #93c5fd, #f59e0b);"></div>
                                     <div class="taste-thumb" style="left: ${Math.max(2, Math.min(98, Number(research.taste_profile.dry_vs_sweet || 10)))}%;"></div>
                                   </div>
                                 </div>
                               </div>
                             </div>
                             `
                             : ""
                         }
                         <div class="research-blocks-list">
                           ${
                             research.producer_history
                               ? `<div class="research-block">
                                    <strong class="research-block-title">🏰 Producer &amp; Winery Heritage</strong>
                                    <div class="research-block-body">${escapeHtml(research.producer_history)}</div>
                                  </div>`
                               : ""
                           }
                           ${
                             research.wine_and_terroir
                               ? `<div class="research-block">
                                    <strong class="research-block-title">🍇 The Wine, Vineyards &amp; Terroir</strong>
                                    <div class="research-block-body">${escapeHtml(research.wine_and_terroir)}</div>
                                  </div>`
                               : ""
                           }
                           ${
                             research.tasting_profile
                               ? `<div class="research-block">
                                    <strong class="research-block-title">🍷 Tasting Profile &amp; Cellar Longevity</strong>
                                    <div class="research-block-body">${escapeHtml(research.tasting_profile)}</div>
                                  </div>`
                               : ""
                           }
                           ${
                             research.serving_and_pairing
                               ? `<div class="research-block">
                                    <strong class="research-block-title">🍽️ Serving &amp; Culinary Pairings</strong>
                                    <div class="research-block-body">${escapeHtml(research.serving_and_pairing)}</div>
                                  </div>`
                               : ""
                           }
                           ${
                             research.fun_facts
                               ? `<div class="research-block">
                                    <strong class="research-block-title">✨ Fascinating Trivia &amp; Accolades</strong>
                                    <div class="research-block-body">${escapeHtml(research.fun_facts)}</div>
                                  </div>`
                               : ""
                           }
                         </div>
                       </div>`
                    : ""
                }
              </div>
              <div class="modal-actions full-width detail-form-actions">
                <button type="button" class="ha-btn danger-btn" id="btn-delete-wine" data-id="${num(w.id)}" style="background: rgba(198, 40, 40, 0.12); color: var(--error-color, #c62828); border-color: rgba(198, 40, 40, 0.35);">
                  <ha-icon icon="mdi:trash-can-outline"></ha-icon>
                  <span>Delete Wine</span>
                </button>
                <button type="submit" class="ha-btn primary-btn">
                  <ha-icon icon="mdi:content-save-outline"></ha-icon>
                  <span>Save Overview Changes</span>
                </button>
              </div>
            </form>

            <div class="section-divider">Purchase Batches &amp; Storage Locations</div>
            <div class="timeline-list">
              ${
                (w.batches || []).length === 0
                  ? `<div class="empty-sub">No purchase batches recorded yet.</div>`
                  : (w.batches || [])
                      .map(
                        (b) => `
                        <div class="timeline-item">
                          <div class="timeline-title">
                            <strong>+${num(b.quantity_added)} bottle(s)</strong> (${num(b.quantity_remaining)} left in stock)
                            • <span>${escapeHtml(formatDisplayDate(b.added_at))}</span>
                          </div>
                          <div class="timeline-meta">
                            Bought at: <strong>${escapeHtml(b.purchase_location || "—")}</strong>
                            • Stored in: <strong>${escapeHtml(b.storage_location || "—")}</strong>
                            ${b.purchase_price !== null ? `• Price: ${escapeHtml(b.purchase_price)} ${escapeHtml(this._currency)}` : ""}
                          </div>
                          ${b.notes ? `<div class="timeline-note">${escapeHtml(b.notes)}</div>` : ""}
                        </div>
                      `
                      )
                      .join("")
              }
            </div>

            <div class="section-divider">Chronological Addition &amp; Tasting Notes Timeline</div>
            <div class="timeline-list">
              ${
                (w.timeline || []).length === 0
                  ? `<div class="empty-sub">No history entries yet.</div>`
                  : (w.timeline || [])
                      .map(
                        (h) => `
                        <div class="timeline-item">
                          <div class="timeline-title">
                            <span class="event-tag ${num(h.delta) > 0 ? "added" : "consumed"}">
                              ${num(h.delta) > 0 ? `Added +${num(h.delta)}` : `Opened ${num(h.delta)}`}
                            </span>
                            <span>${escapeHtml(formatDisplayDate(h.timestamp))}</span>
                            ${h.personal_rating ? `<span class="personal-badge">★ ${num(h.personal_rating)}</span>` : ""}
                          </div>
                          ${
                            h.tasting_notes
                              ? `<div class="timeline-note">${escapeHtml(h.tasting_notes)}</div>`
                              : ""
                          }
                        </div>
                      `
                      )
                      .join("")
              }
            </div>
          </div>
          </div>
         </div>
        </div>
      </div>
    `;
  }

  _openImagePreview(wine) {
    if (!wine) return;
    this._previewImageWine = wine;
    this._uploadingImageWineId = null;
    this._imageUploadError = "";
    this._previousModal = this._activeModal;
    this._activeModal = "image_preview";
    this._render();
  }

  _closeImagePreview() {
    this._activeModal = this._previousModal || null;
    this._previewImageWine = null;
    this._uploadingImageWineId = null;
    this._imageUploadError = "";
    this._previousModal = null;
    this._render();
  }

  _renderImagePreviewModal() {
    const wine = this._previewImageWine;
    if (!wine) return "";
    const hasImage = Boolean(safeImageUrl(wine.image_url));
    const isUploading = this._uploadingImageWineId === wine.id;

    return `
      <div class="modal-backdrop" id="image-preview-backdrop" style="background: rgba(0, 0, 0, 0.85); z-index: 1100;">
        <div class="modal-surface" style="max-width: 540px; width: 92%; background: var(--card-background-color, #fff); border-radius: 14px; overflow: hidden; box-shadow: 0 16px 40px rgba(0,0,0,0.5);">
          <div class="modal-header" style="padding: 14px 18px; border-bottom: 1px solid var(--divider-color, #e0e0e0); display: flex; align-items: center; justify-content: space-between;">
            <div>
              <h2 style="font-size: 16px; margin: 0; display: flex; align-items: center; gap: 8px;">
                <ha-icon icon="mdi:image-outline" style="color: var(--primary-color);"></ha-icon>
                <span>${escapeHtml(wine.name)}</span>
              </h2>
              <div style="font-size: 12px; color: var(--secondary-text-color); margin-top: 2px;">
                ${escapeHtml(wine.winery)} (${escapeHtml(wine.vintage || "NV")})
              </div>
            </div>
            <button class="ha-btn icon-btn" id="close-image-preview-btn" title="Close">
              <ha-icon icon="mdi:close"></ha-icon>
            </button>
          </div>
          <div style="padding: 16px; background: rgba(0,0,0,0.04); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; max-height: 75vh; overflow-y: auto;">
            ${
              hasImage
                ? `<img src="${safeImageUrl(wine.image_url)}" alt="${escapeHtml(wine.name)}" style="max-height: 55vh; max-width: 100%; object-fit: contain; border-radius: 8px; display: block; box-shadow: 0 4px 12px rgba(0,0,0,0.25);" />`
                : `<div style="padding: 36px 16px; text-align: center; color: var(--secondary-text-color);">
                     <ha-icon icon="mdi:camera-plus-outline" style="--mdc-icon-size: 48px; color: var(--primary-color); opacity: 0.7;"></ha-icon>
                     <div style="margin-top: 8px; font-weight: 600; font-size: 14px;">No bottle photo uploaded yet</div>
                     <div style="font-size: 12px; margin-top: 4px;">Upload a label or bottle photo from your camera or photo library.</div>
                   </div>`
            }
            <div style="display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; align-items: center; width: 100%; padding-top: 10px; border-top: 1px solid var(--divider-color, #e0e0e0);">
              <label class="ha-btn primary-btn" style="cursor: pointer; display: inline-flex; align-items: center; gap: 6px; margin: 0;">
                <ha-icon icon="mdi:camera"></ha-icon>
                <span>${hasImage ? "Change / Upload New Photo" : "Upload Bottle Photo"}</span>
                <input type="file" id="preview-image-upload-input" accept="image/*" style="display: none;" ${isUploading ? "disabled" : ""} />
              </label>
              ${
                isUploading
                  ? `<span style="font-size: 13px; color: var(--primary-color); display: inline-flex; align-items: center; gap: 4px;">
                       <ha-icon icon="mdi:loading" style="animation: spin 1s linear infinite;"></ha-icon>
                       <span>Uploading photo…</span>
                     </span>`
                  : ""
              }
            </div>
            ${
              this._imageUploadError
                ? `<div class="scan-error-message" style="color: var(--error-color, #c62828); font-size: 12.5px; text-align: center;">${escapeHtml(this._imageUploadError)}</div>`
                : ""
            }
          </div>
        </div>
      </div>
    `;
  }

  async _handleImageUpload(wine, file) {
    if (!wine || !file || !this._hass) return;
    this._uploadingImageWineId = wine.id;
    this._imageUploadError = "";
    this._render();

    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });

      const res = await this._hass.callWS({
        type: "homecave/update_wine",
        wine_id: wine.id,
        updates: { image_data: dataUrl },
      });

      if (res && res.wine) {
        wine.image_url = res.wine.image_url;
        if (this._modalWine && this._modalWine.id === wine.id) {
          this._modalWine.image_url = res.wine.image_url;
        }
      }
      await this._fetchInventory();
    } catch (err) {
      this._imageUploadError = err?.message || "Failed to upload photo.";
    } finally {
      this._uploadingImageWineId = null;
      this._render();
    }
  }

  _bindEvents() {
    const root = this.shadowRoot;
    if (!root) return;

    root.querySelector("#btn-scan-bottle")?.addEventListener("click", () => this._openScanModal(false));
    root.querySelector("#btn-add-manual")?.addEventListener("click", () => this._openScanModal(true));
    root.querySelector("#btn-refresh-vivino")?.addEventListener("click", () => this._refreshAllVivino());
    root.querySelector("#btn-toggle-grid")?.addEventListener("click", () => {
      this._viewMode = "grid";
      this._userToggledView = true;
      this._render();
    });
    root.querySelector("#btn-toggle-list")?.addEventListener("click", () => {
      this._viewMode = "list";
      this._userToggledView = true;
      this._render();
    });

    const searchInput = root.querySelector("#wine-search-input");
    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        this._searchQuery = e.target.value;
        clearTimeout(this._searchDebounce);
        this._searchDebounce = setTimeout(() => this._refreshResultsOnly(), 150);
      });
      searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") searchInput.blur();
      });
    }

    root.querySelector("#btn-clear-search")?.addEventListener("click", () => {
      this._searchQuery = "";
      const input = root.querySelector("#wine-search-input");
      if (input) {
        input.value = "";
        input.focus();
      }
      this._refreshResultsOnly();
    });
    root.querySelectorAll(".btn-font-scale").forEach((b) => b.addEventListener("click", () => this._cycleFontScale()));
    root.querySelector("#btn-mobile-scan")?.addEventListener("click", () => this._openScanModal(false));
    root.querySelector("#btn-mobile-add")?.addEventListener("click", () => this._openScanModal(true));
    root.querySelector("#btn-mobile-view")?.addEventListener("click", () => {
      this._viewMode = this._viewMode === "list" ? "grid" : "list";
      this._userToggledView = true;
      this._render();
    });

    root.querySelector("#wine-sort-select")?.addEventListener("change", (e) => {
      this._sortBy = e.target.value;
      this._render();
    });

    root.querySelector("#btn-card-invert-sort")?.addEventListener("click", () => {
      this._sortInverted = !this._sortInverted;
      this._render();
    });

    root.querySelectorAll(".filter-chip").forEach((chip) => {
      if (!chip.dataset.tab) return;
      chip.addEventListener("click", () => {
        this._filterTab = chip.dataset.tab;
        this._userSelectedTab = true;
        this._render();
      });
    });

    this._bindResultEvents(root);

    // Modal handlers
    root.querySelector("#close-modal-btn")?.addEventListener("click", () => this._closeModal());
    root.querySelector("#cancel-modal-btn")?.addEventListener("click", () => this._closeModal());

    // Image preview modal handlers
    root.querySelector("#close-image-preview-btn")?.addEventListener("click", () => this._closeImagePreview());
    root.querySelector("#image-preview-backdrop")?.addEventListener("click", (e) => {
      if (e.target.id === "image-preview-backdrop") {
        this._closeImagePreview();
      }
    });
    root.querySelector("#preview-image-upload-input")?.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file || !this._previewImageWine) return;
      await this._handleImageUpload(this._previewImageWine, file);
    });

    // Details View: Bottle quantity stepper (+ / -) with 0-bottle warning
    root.querySelector("#btn-detail-qty-plus")?.addEventListener("click", async () => {
      const wine = this._modalWine;
      if (!wine || !this._hass) return;
      try {
        await this._hass.callWS({
          type: "homecave/adjust_quantity",
          wine_id: Number(wine.id),
          delta: 1,
        });
        wine.quantity = Number(wine.quantity || 0) + 1;
        const disp = root.querySelector("#detail-qty-display");
        if (disp) disp.textContent = String(wine.quantity);
        await this._fetchInventory();
        this._render();
      } catch (err) {
        alert(`Failed to add bottle: ${err?.message || err}`);
      }
    });

    root.querySelector("#btn-detail-qty-minus")?.addEventListener("click", async () => {
      const wine = this._modalWine;
      if (!wine || !this._hass) return;
      const currentQty = Number(wine.quantity || 0);
      if (currentQty <= 0) return;

      if (currentQty === 1) {
        const label = `${wine.winery ? wine.winery + " – " : ""}${wine.name} (${wine.vintage || "NV"})`;
        const confirmed = window.confirm(
          `⚠️ WARNING: Reducing the bottle count to 0 will mark "${label}" as consumed / out of stock in your cellar.\n\nDo you want to confirm reducing the quantity to 0?`
        );
        if (!confirmed) return;
      }

      try {
        await this._hass.callWS({
          type: "homecave/adjust_quantity",
          wine_id: Number(wine.id),
          delta: -1,
        });
        wine.quantity = Math.max(0, currentQty - 1);
        const disp = root.querySelector("#detail-qty-display");
        if (disp) disp.textContent = String(wine.quantity);
        await this._fetchInventory();
        this._render();
      } catch (err) {
        alert(`Failed to consume bottle: ${err?.message || err}`);
      }
    });

    // Deep AI Wine Research Scan
    root.querySelector("#btn-detail-research-wine")?.addEventListener("click", (e) => {
      const wineId = Number(e.currentTarget.dataset.id);
      this._handleResearchWine(wineId);
    });
    root.querySelector("#btn-re-research-wine")?.addEventListener("click", (e) => {
      const wineId = Number(e.currentTarget.dataset.id);
      this._handleResearchWine(wineId);
    });

    // Link exact Vivino URL handler
    root.querySelector("#btn-detail-link-vivino")?.addEventListener("click", (e) => {
      const wineId = Number(e.currentTarget.dataset.id);
      const input = root.querySelector("#detail-vivino-link-input");
      this._handleLinkVivinoUrl(wineId, input ? input.value : "");
    });

    // Remove Vivino link button handler
    root.querySelector("#btn-detail-unlink-vivino")?.addEventListener("click", (e) => {
      const wineId = Number(e.currentTarget.dataset.id);
      if (wineId) this._handleUnlinkVivino(wineId);
    });

    // Delete wine button handler
    root.querySelector("#btn-delete-wine")?.addEventListener("click", async () => {
      const wine = this._modalWine;
      if (!wine || !this._hass) return;
      const label = `${wine.winery ? wine.winery + " – " : ""}${wine.name} (${wine.vintage || "NV"})`;
      const confirmed = window.confirm(
        `⚠️ WARNING: Are you sure you want to permanently delete "${label}" from your cellar database?\n\nThis will completely remove the wine, all its purchase batches, and all tasting history. This action CANNOT be undone!`
      );
      if (!confirmed) return;

      try {
        await this._hass.callWS({
          type: "homecave/delete_wine",
          wine_id: Number(wine.id),
        });
        this._closeModal();
        await this._fetchInventory();
      } catch (err) {
        alert(`Failed to delete wine: ${err?.message || err}`);
      }
    });

    // Crop / Rotate modal handlers
    root.querySelector("#close-crop-btn")?.addEventListener("click", () => this._cancelCrop());
    root.querySelector("#btn-cancel-crop")?.addEventListener("click", () => this._cancelCrop());
    root.querySelector("#btn-confirm-crop")?.addEventListener("click", () => this._confirmCropAndScan());
    root.querySelector("#btn-rotate-left")?.addEventListener("click", () => this._rotateCrop(-90));
    root.querySelector("#btn-rotate-right")?.addEventListener("click", () => this._rotateCrop(90));
    root.querySelector("#btn-reset-crop")?.addEventListener("click", () => this._resetCrop());
    root.querySelector("#reopen-crop-btn")?.addEventListener("click", () => {
      this._activeModal = "crop_rotate";
      this._render();
    });
    root.querySelector("#retry-crop-btn")?.addEventListener("click", () => {
      this._activeModal = "crop_rotate";
      this._render();
    });
    root.querySelector("#dismiss-scan-error-btn")?.addEventListener("click", () => {
      this._scanState.error = "";
      this._render();
    });
    root.querySelector("#clear-scan-error-btn")?.addEventListener("click", () => {
      this._scanState.error = "";
      this._render();
    });
    root.querySelector("#dismiss-card-error-btn")?.addEventListener("click", () => {
      this._error = "";
      this._render();
    });

    root.querySelector("#bottle-file-input")?.addEventListener("change", (e) => this._handleFileSelected(e));
    root.querySelector("#bottle-library-input")?.addEventListener("change", (e) => this._handleFileSelected(e));
    root.querySelector("#vivino-candidate-select")?.addEventListener("change", (e) => {
      this._applyVivinoCandidate(Number(e.target.value));
    });
    root.querySelector("#btn-disconnect-vivino")?.addEventListener("click", () => {
      this._applyVivinoCandidate(-1);
    });

    if (this._activeModal === "crop_rotate") {
      this._setupCropCanvas();
    }

    root.querySelectorAll("[data-ha-scan-dest]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const dest = btn.dataset.haScanDest;
        if (!this._scanState?.form) return;
        this._scanState.form.as_tasted = dest === "tasted";
        this._scanState.form.quantity = dest === "tasted" ? 0 : Math.max(1, Number(this._scanState.form.quantity) || 1);
        this._render();
      });
    });

    const scanForm = root.querySelector("#scan-add-form");
    if (scanForm) {
      scanForm.querySelectorAll("input, select, textarea").forEach((inp) => {
        inp.addEventListener("change", (e) => {
          if (e.target.name) {
            this._scanState.form[e.target.name] = e.target.value;
          }
        });
      });
      scanForm.addEventListener("submit", (e) => this._submitScanOrAddForm(e));
    }

    const qaForm = root.querySelector("#quick-add-form");
    if (qaForm) {
      qaForm.querySelectorAll("input, select, textarea").forEach((inp) => {
        inp.addEventListener("change", (e) => {
          if (e.target.name) {
            this._quickAddForm[e.target.name] = e.target.value;
          }
        });
      });
      qaForm.addEventListener("submit", (e) => this._submitQuickAdd(e));
    }

    const consumeForm = root.querySelector("#consume-form");
    if (consumeForm) {
      consumeForm.querySelectorAll("input, select, textarea").forEach((inp) => {
        inp.addEventListener("change", (e) => {
          if (e.target.name) {
            this._consumeForm[e.target.name] = e.target.value;
          }
        });
      });
      consumeForm.addEventListener("submit", (e) => this._submitConsume(e));
    }

    const detailForm = root.querySelector("#detail-form");
    if (detailForm) {
      detailForm.querySelectorAll("input, select, textarea").forEach((inp) => {
        inp.addEventListener("change", (e) => {
          if (e.target.name) {
            this._detailForm[e.target.name] = e.target.value;
          }
        });
      });
      detailForm.addEventListener("submit", (e) => this._submitDetailUpdate(e));
    }
  }

  _render() {
    if (!this.shadowRoot) return;
    const stats = this._statistics || {};
    const wines = this._getFilteredAndSortedWines();

    const isTastedTab = this._filterTab === "tasted" || this._filterTab === "archived";
    const tastedCount =
      stats.tasted_wines !== undefined && stats.tasted_wines !== null
        ? stats.tasted_wines
        : this._wines.filter((w) => this._isWineTasted(w)).length;

    const tabs = [
      { id: "all", label: `In Stock (${num(stats.total_bottles)})` },
      { id: "ready", label: `Ready to Drink (${num(stats.ready_to_drink_bottles)})` },
      { id: "drink_soon", label: `Soon (${num(stats.drink_soon_bottles)})` },
      { id: "keep", label: `Keep (${num(stats.keep_bottles || stats.hold_bottles)})` },
      { id: "past_peak", label: `Past Peak (${num(stats.past_peak_bottles)})` },
      { id: "red", label: `Red (${num(stats.by_type?.red)})` },
      { id: "white", label: `White (${num(stats.by_type?.white)})` },
      { id: "rose", label: `Rosé (${num(stats.by_type?.rose)})` },
      { id: "sparkling", label: `Sparkling (${num(stats.by_type?.sparkling)})` },
      { id: "tasted", label: `🥂 Tasted (${num(tastedCount)})` },
    ];

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          --ls-scale: ${this._effectiveFontScale()};
          display: block;
          color: var(--primary-text-color);
          font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
        }
        ha-card {
          padding: 20px;
          border-radius: var(--ha-card-border-radius, 12px);
          background: var(--ha-card-background, var(--card-background-color, #fff));
          transition: background 0.2s ease, border-color 0.2s ease;
        }
        ha-card.tasted-card-theme {
          background: linear-gradient(180deg, rgba(107, 45, 92, 0.08) 0%, var(--ha-card-background, var(--card-background-color, #fff)) 260px);
          border: 1px solid rgba(107, 45, 92, 0.28);
          --primary-color: #6b2d5c;
        }
        .tasted-badge {
          display: inline-flex;
          align-items: center;
          gap: 3px;
          padding: 2px 8px;
          border-radius: 999px;
          font-size: 11px;
          font-weight: 700;
          background: rgba(107, 45, 92, 0.14);
          color: #6b2d5c;
          border: 1px solid rgba(107, 45, 92, 0.3);
        }
        .ha-scan-dest-toggle {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 8px;
          padding: 5px;
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.1));
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 10px;
          margin-bottom: 6px;
        }
        .ha-scan-dest-btn {
          min-height: 42px;
          border-radius: 8px;
          border: 1px solid transparent;
          background: transparent;
          color: var(--secondary-text-color);
          font-size: 13.5px;
          font-weight: 700;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
        }
        .ha-scan-dest-btn.active {
          background: #a61a2e;
          color: #fff;
          box-shadow: 0 2px 6px rgba(166, 26, 46, 0.28);
        }
        .ha-scan-dest-btn.active.tasted {
          background: #6b2d5c;
          color: #fff;
          box-shadow: 0 2px 6px rgba(107, 45, 92, 0.3);
        }
        .header-bar {
          display: flex;
          flex-wrap: wrap;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          margin-bottom: 16px;
        }
        .title-block h1 {
          margin: 0;
          font-size: 20px;
          font-weight: 600;
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .env-pills {
          display: flex;
          gap: 8px;
          margin-top: 4px;
          font-size: 12px;
          color: var(--secondary-text-color);
        }
        .env-pill {
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
          padding: 2px 8px;
          border-radius: 999px;
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }
        .header-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }
        .ha-btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 8px;
          padding: 8px 14px;
          font-size: 13px;
          font-weight: 500;
          cursor: pointer;
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color);
          transition: opacity 0.15s ease;
        }
        .ha-btn:hover {
          opacity: 0.9;
        }
        .primary-btn {
          background: var(--primary-color, #03a9f4);
          color: var(--text-primary-color, #fff);
          border-color: transparent;
        }
        .secondary-btn {
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.1));
        }
        .icon-btn {
          padding: 6px;
          min-width: 34px;
          min-height: 34px;
        }
        .controls-bar {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          margin-bottom: 12px;
        }
        .ha-input, .ha-textarea {
          width: 100%;
          box-sizing: border-box;
          padding: 8px 12px;
          border-radius: 8px;
          border: 1px solid var(--divider-color, #ccc);
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color);
          font-size: 13px;
        }
        .search-wrap {
          flex: 1 1 240px;
        }
        .filter-chips {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-bottom: 16px;
        }
        .filter-chip {
          padding: 5px 12px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          border: 1px solid var(--divider-color, #ddd);
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
          color: var(--primary-text-color);
        }
        .filter-chip.active {
          background: var(--primary-color, #03a9f4);
          color: var(--text-primary-color, #fff);
          border-color: transparent;
        }
        .wine-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 14px;
        }
        .wine-item {
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 12px;
          padding: 14px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          gap: 10px;
          background: var(--card-background-color, #fff);
        }
        .wine-item-top {
          display: flex;
          gap: 12px;
        }
        .wine-thumb, .wine-thumb-placeholder {
          width: 68px;
          height: 92px;
          border-radius: 8px;
          object-fit: cover;
          flex-shrink: 0;
        }
        .wine-thumb-placeholder {
          display: flex;
          align-items: center;
          justify-content: center;
          background: rgba(123, 31, 58, 0.14);
          color: #880e4f;
        }
        .wine-info {
          flex: 1;
          min-width: 0;
        }
        .wine-badges-row {
          display: flex;
          flex-wrap: wrap;
          gap: 4px;
          margin-bottom: 4px;
        }
        .type-pill, .vintage-pill, .drink-pill {
          font-size: 10px;
          font-weight: 600;
          padding: 2px 7px;
          border-radius: 999px;
          text-transform: uppercase;
        }
        .type-red { background: rgba(183, 28, 28, 0.15); color: #c62828; }
        .type-white { background: rgba(245, 127, 23, 0.15); color: #ef6c00; }
        .type-rose { background: rgba(233, 30, 99, 0.15); color: #ad1457; }
        .type-sparkling { background: rgba(2, 136, 209, 0.15); color: #0277bd; }
        .vintage-pill {
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.15));
        }
        .drink-pill {
          border: 1px solid currentColor;
        }
        .wine-winery {
          font-size: 12px;
          color: var(--secondary-text-color);
          font-weight: 500;
        }
        .wine-title {
          font-size: 15px;
          font-weight: 600;
          margin: 1px 0 3px;
        }
        .wine-sub {
          font-size: 12px;
          color: var(--secondary-text-color);
          margin-bottom: 6px;
        }
        .wine-ratings-row {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          align-items: center;
        }
        .vivino-badge {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          background: rgba(166, 26, 46, 0.12);
          color: #a61a2e;
          padding: 2px 8px;
          border-radius: 6px;
          font-size: 12px;
          font-weight: 600;
          text-decoration: none;
        }
        .vivino-badge.muted {
          background: var(--secondary-background-color);
          color: var(--secondary-text-color);
          font-weight: 400;
        }
        .vivino-sub {
          font-weight: 400;
          font-size: 11px;
        }
        .personal-badge {
          font-size: 11px;
          font-weight: 600;
          color: var(--primary-color);
        }
        .wine-meta-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 4px 10px;
          font-size: 12px;
          color: var(--secondary-text-color);
          padding-top: 8px;
          border-top: 1px dashed var(--divider-color, #e0e0e0);
        }
        .wine-meta-grid > div {
          display: flex;
          align-items: center;
          gap: 4px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .wine-meta-grid ha-icon {
          --mdc-icon-size: 15px;
          flex-shrink: 0;
        }
        .wine-notes-preview {
          font-size: 12px;
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.07));
          padding: 6px 10px;
          border-radius: 6px;
          color: var(--primary-text-color);
        }
        .wine-actions-bar {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding-top: 8px;
          border-top: 1px solid var(--divider-color, #e0e0e0);
        }
        .stepper-group {
          display: inline-flex;
          align-items: center;
          gap: 8px;
        }
        .qty-badge {
          font-size: 13px;
          font-weight: 600;
          min-width: 68px;
          text-align: center;
        }
        .empty-state {
          text-align: center;
          padding: 32px 16px;
          color: var(--secondary-text-color);
        }
        /* Modal Styles */
        .modal-backdrop {
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.55);
          z-index: 999;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 16px;
        }
        .modal-surface {
          background: var(--ha-card-background, var(--card-background-color, #fff));
          color: var(--primary-text-color);
          border-radius: 14px;
          max-width: 680px;
          width: 100%;
          max-height: 90vh;
          overflow-y: auto;
          box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35);
        }
        .modal-surface.compact {
          max-width: 480px;
        }
        .modal-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 16px 20px;
          border-bottom: 1px solid var(--divider-color, #e0e0e0);
        }
        .modal-header h2 {
          margin: 0;
          font-size: 17px;
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .modal-body {
          padding: 18px 20px;
        }
        .scan-capture-box {
          margin-bottom: 16px;
          padding: 12px;
          border-radius: 10px;
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
        }
        .scan-buttons-row {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          align-items: center;
        }
        .crop-modal-surface {
          max-width: 640px;
          width: 95%;
        }
        .crop-modal-body {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 14px;
        }
        .crop-instructions {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 13px;
          color: var(--secondary-text-color);
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
          padding: 8px 12px;
          border-radius: 8px;
          width: 100%;
          box-sizing: border-box;
        }
        .canvas-wrapper {
          display: flex;
          align-items: center;
          justify-content: center;
          background: #111;
          border-radius: 8px;
          overflow: hidden;
          padding: 10px;
          width: 100%;
          box-sizing: border-box;
          min-height: 240px;
        }
        #crop-canvas {
          touch-action: none;
          user-select: none;
          max-width: 100%;
          display: block;
          border-radius: 4px;
        }
        .crop-controls-row {
          display: flex;
          flex-wrap: wrap;
          justify-content: center;
          gap: 8px;
          width: 100%;
        }
        .crop-footer {
          margin-top: 8px;
        }
        .scan-status-banner, .scan-existing-banner, .scan-error-banner {
          margin-top: 10px;
          padding: 10px 12px;
          border-radius: 8px;
          font-size: 12px;
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .scan-error-card {
          margin-top: 12px;
          padding: 14px;
          border-radius: 10px;
          border: 1px solid var(--error-color, #c62828);
          background: rgba(198, 40, 40, 0.08);
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .scan-error-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
        }
        .scan-error-title-wrap {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .scan-error-message {
          margin: 0;
          font-size: 13px;
          color: var(--primary-text-color);
          font-weight: 500;
          line-height: 1.4;
        }
        .scan-error-hint {
          margin-top: 6px;
          font-size: 12px;
          color: var(--secondary-text-color);
          line-height: 1.4;
        }
        .scan-error-hint ul {
          margin: 4px 0 0 16px;
          padding: 0;
        }
        .scan-error-hint li {
          margin-bottom: 2px;
        }
        .scan-error-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 4px;
        }
        .card-alert-banner {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 10px 14px;
          border-radius: 8px;
          margin-bottom: 14px;
          font-size: 13px;
          gap: 10px;
          background: rgba(198, 40, 40, 0.12);
          border: 1px solid var(--error-color, #c62828);
          color: var(--primary-text-color);
        }
        .card-alert-content {
          display: flex;
          align-items: center;
          gap: 8px;
          flex: 1;
        }
        .scan-status-banner {
          background: rgba(2, 136, 209, 0.15);
          color: var(--primary-text-color);
        }
        .scan-existing-banner {
          background: rgba(46, 125, 50, 0.15);
          color: var(--primary-text-color);
        }
        .scan-error-banner {
          background: rgba(198, 40, 40, 0.15);
          color: #c62828;
        }
        .form-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 12px;
        }
        .form-field {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .form-field.full-width, .section-divider.full-width, .modal-actions.full-width {
          grid-column: 1 / -1;
        }
        .form-field label {
          font-size: 12px;
          font-weight: 500;
          color: var(--secondary-text-color);
        }
        .split-2 {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 8px;
        }
        .section-divider {
          font-size: 12px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: var(--primary-color);
          padding-top: 8px;
          margin-top: 4px;
          border-top: 1px solid var(--divider-color, #e0e0e0);
        }
        .modal-actions {
          display: flex;
          justify-content: flex-end;
          gap: 10px;
          padding-top: 10px;
        }
        .timeline-list {
          display: flex;
          flex-direction: column;
          gap: 8px;
          margin: 10px 0 16px;
        }
        .timeline-item {
          padding: 10px 12px;
          border-radius: 8px;
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
          font-size: 12px;
        }
        .timeline-title {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 4px;
        }
        .event-tag {
          padding: 2px 6px;
          border-radius: 4px;
          font-weight: 600;
          font-size: 11px;
        }
        .event-tag.added { background: rgba(46, 125, 50, 0.15); color: #2e7d32; }
        .event-tag.consumed { background: rgba(239, 108, 0, 0.15); color: #ef6c00; }
        .timeline-note {
          margin-top: 4px;
          color: var(--primary-text-color);
          white-space: pre-wrap;
        }
        @media (max-width: 600px) {
          ha-card {
            padding: 12px;
          }
          .header-bar {
            flex-direction: column;
            align-items: stretch;
            gap: 10px;
          }
          .title-block h1 {
            font-size: 18px;
          }
          .header-actions {
            justify-content: flex-start;
            flex-wrap: wrap;
            gap: 6px;
          }
          .controls-bar {
            flex-direction: column;
            gap: 8px;
          }
          .search-wrap {
            width: 100%;
            flex: 1 1 100%;
          }
          .filter-chips {
            overflow-x: auto;
            flex-wrap: nowrap;
            -webkit-overflow-scrolling: touch;
            padding-bottom: 4px;
          }
          .filter-chip {
            white-space: nowrap;
            flex-shrink: 0;
          }
          .wine-grid {
            grid-template-columns: 1fr;
            gap: 10px;
          }
          .wine-list-table th:nth-child(4),
          .wine-list-table td:nth-child(4),
          .wine-list-table th:nth-child(5),
          .wine-list-table td:nth-child(5) {
            display: none;
          }
          .wine-list-title-wrap {
            max-width: 130px;
          }
          .modal-backdrop {
            padding: 6px;
          }
          .modal-surface {
            max-height: 94vh;
            border-radius: 10px;
          }
          .modal-header {
            padding: 12px 14px;
          }
          .modal-body {
            padding: 12px 14px;
          }
          .detail-overview-header {
            flex-direction: column;
            align-items: flex-start !important;
            gap: 10px !important;
          }
          .split-2 {
            grid-template-columns: 1fr;
          }
          .form-grid {
            grid-template-columns: 1fr;
          }
          .modal-actions {
            flex-direction: column;
            align-items: stretch;
          }
          .modal-actions button {
            width: 100%;
          }
        }
        .active-view {
          background: var(--primary-color, #03a9f4) !important;
          color: var(--text-primary-color, #fff) !important;
          border-color: transparent !important;
        }
        .wine-list-table-container {
          overflow-x: auto;
          margin-top: 6px;
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 10px;
          background: var(--card-background-color, #fff);
        }
        .wine-list-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
          text-align: left;
        }
        .wine-list-table th {
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
          padding: 10px 12px;
          font-weight: 600;
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: var(--secondary-text-color);
          border-bottom: 1px solid var(--divider-color, #e0e0e0);
          white-space: nowrap;
        }
        .wine-list-table td {
          padding: 10px 12px;
          border-bottom: 1px solid var(--divider-color, #f0f0f0);
          vertical-align: middle;
        }
        .wine-list-table tr:last-child td {
          border-bottom: none;
        }
        .wine-list-table tr:hover {
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.04));
        }
        .wine-list-thumb {
          width: 36px;
          height: 48px;
          border-radius: 4px;
          object-fit: cover;
          display: block;
        }
        .wine-list-thumb-placeholder {
          width: 36px;
          height: 48px;
          border-radius: 4px;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .wine-list-title-wrap {
          display: flex;
          flex-direction: column;
          gap: 2px;
          max-width: 260px;
        }
        .wine-list-title-wrap strong {
          font-size: 13px;
          color: var(--primary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .wine-list-title-wrap span {
          font-size: 11px;
          color: var(--secondary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .wine-list-actions {
          display: flex;
          align-items: center;
          gap: 6px;
          white-space: nowrap;
        }

        /* ================= Mobile-first responsive layer (v0.2.9 / v0.3.0) ================= */
        /* The query container sits INSIDE the zoomed root, so larger text sizes
           automatically fall back to the narrower (less detailed) layout tiers. */
        .ls-root { zoom: var(--ls-scale, 1); }
        .ls-cq {
          container-type: inline-size;
          container-name: lscard;
        }
        /* Modal inner zoom & container query so Detailed view scales cleanly without overflowing viewport */
        .modal-inner {
          zoom: var(--ls-scale, 1);
        }
        .modal-cq {
          container-type: inline-size;
          container-name: lsmodal;
        }
        .modal-header-actions {
          display: flex;
          align-items: center;
          gap: 6px;
          flex-shrink: 0;
        }
        .modal-header h2 {
          min-width: 0;
          flex: 1;
          line-height: 1.3;
        }
        .modal-header h2 span {
          overflow: hidden;
          text-overflow: ellipsis;
        }
        /* Detailed view scalable styles */
        .detail-overview-header {
          display: flex;
          gap: 14px;
          margin-bottom: 16px;
          padding: 14px;
          border-radius: 12px;
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.06));
          align-items: flex-start;
          border: 1px solid var(--divider-color, #e0e0e0);
        }
        .detail-thumb-wrap {
          position: relative;
          cursor: pointer;
          flex-shrink: 0;
        }
        .detail-wine-thumb {
          width: 84px;
          height: 112px;
          border-radius: 8px;
          object-fit: cover;
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .thumb-zoom-overlay {
          position: absolute;
          bottom: 4px;
          right: 4px;
          background: rgba(0, 0, 0, 0.65);
          color: #fff;
          border-radius: 4px;
          padding: 2px 4px;
          font-size: 10px;
          display: flex;
          align-items: center;
          pointer-events: none;
        }
        .detail-overview-main {
          flex: 1;
          min-width: 0;
        }
        .detail-badges-row {
          display: flex;
          gap: 6px;
          flex-wrap: wrap;
          margin-bottom: 6px;
        }
        .detail-pill {
          font-size: 11px !important;
          padding: 3px 8px !important;
          cursor: default;
        }
        .detail-winery-text {
          font-size: 13px;
          font-weight: 500;
          color: var(--secondary-text-color);
        }
        .detail-wine-title {
          font-size: 18px;
          font-weight: 700;
          margin: 2px 0 6px;
          line-height: 1.3;
          color: var(--primary-text-color);
        }
        .detail-origin-line {
          font-size: 12.5px;
          color: var(--secondary-text-color);
          margin-bottom: 8px;
          line-height: 1.45;
        }
        .detail-ratings-bar {
          display: flex;
          gap: 8px;
          align-items: center;
          flex-wrap: wrap;
        }
        .detail-vivino-badge {
          text-decoration: none;
          font-size: 12.5px;
          padding: 3px 9px;
        }
        .detail-personal-badge {
          font-size: 12px;
        }
        .detail-small-btn {
          font-size: 12px;
          padding: 4px 10px;
          min-height: 34px;
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }
        .vivino-link-row {
          display: flex;
          gap: 6px;
          align-items: center;
          margin-top: 8px;
          flex-wrap: wrap;
        }
        .detail-vivino-input {
          flex: 1;
          min-width: 180px;
          font-size: 13px;
          padding: 7px 10px;
        }
        .detail-stepper-wrap {
          display: flex;
          align-items: center;
          gap: 10px;
          margin-top: 12px;
          flex-wrap: wrap;
        }
        .detail-stepper-label {
          font-size: 13px;
          font-weight: 600;
          color: var(--secondary-text-color);
        }
        .stepper-controls {
          display: inline-flex;
          align-items: center;
          background: var(--card-background-color, #fff);
          border: 1px solid var(--divider-color, #ccc);
          border-radius: 8px;
          overflow: hidden;
          box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
        }
        .detail-step-btn {
          width: 40px;
          height: 40px;
          border: none;
          border-radius: 0;
          background: transparent;
          cursor: pointer;
        }
        .detail-qty-value {
          padding: 0 12px;
          font-weight: 700;
          font-size: 16px;
          min-width: 32px;
          text-align: center;
          color: var(--primary-color);
        }
        .detail-stepper-unit {
          font-size: 12.5px;
          color: var(--secondary-text-color);
        }
        .detail-research-btn {
          margin-left: auto;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          font-weight: 600;
          min-height: 40px;
        }
        .research-loading-banner {
          margin: 14px 0;
          padding: 14px;
          border-radius: 10px;
          background: rgba(3, 169, 244, 0.08);
          border: 1px solid var(--primary-color);
          display: flex;
          align-items: center;
          gap: 12px;
        }
        .research-loading-title {
          font-weight: 600;
          font-size: 13.5px;
          color: var(--primary-color);
        }
        .research-loading-sub {
          font-size: 12px;
          color: var(--secondary-text-color);
        }
        .research-results-card {
          margin: 16px 0;
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 12px;
          padding: 16px;
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.04));
          box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
        }
        .research-header-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 12px;
        }
        .research-header-title {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 14.5px;
        }
        .research-summary-text {
          font-size: 13.5px;
          font-weight: 500;
          font-style: italic;
          margin-bottom: 12px;
          line-height: 1.5;
          color: var(--primary-text-color);
        }
        .taste-sliders-card {
          margin: 6px 0 12px;
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 10px;
          padding: 12px;
          background: var(--card-background-color, #fff);
        }
        .taste-sliders-heading {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-bottom: 12px;
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: var(--primary-color);
        }
        .taste-sliders-list {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }
        .taste-slider-labels {
          display: flex;
          justify-content: space-between;
          align-items: center;
          font-size: 11.5px;
          margin-bottom: 5px;
          gap: 4px;
          flex-wrap: wrap;
        }
        .taste-pole-active {
          font-weight: 700;
          color: var(--primary-color);
        }
        .taste-pole-muted {
          font-weight: 600;
          color: var(--secondary-text-color);
        }
        .taste-badge {
          font-size: 11.5px;
          font-weight: 700;
          color: var(--primary-color);
          background: rgba(166, 26, 46, 0.08);
          padding: 1px 8px;
          border-radius: 10px;
        }
        .taste-track {
          position: relative;
          height: 8px;
          background: var(--secondary-background-color, #eee);
          border-radius: 6px;
          border: 1px solid var(--divider-color, #ddd);
        }
        .taste-fill {
          position: absolute;
          left: 0;
          top: 0;
          bottom: 0;
          border-radius: 5px;
        }
        .taste-thumb {
          position: absolute;
          top: 50%;
          transform: translate(-50%, -50%);
          width: 14px;
          height: 14px;
          background: #fff;
          border: 3px solid var(--primary-color, #a61a2e);
          border-radius: 50%;
          box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
        }
        .research-blocks-list {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .research-block {
          padding: 10px 12px;
          background: var(--card-background-color, #fff);
          border-radius: 8px;
          border: 1px solid var(--divider-color, #eee);
        }
        .research-block-title {
          display: block;
          font-size: 12.5px;
          color: var(--primary-color);
          margin-bottom: 4px;
        }
        .research-block-body {
          font-size: 12.5px;
          line-height: 1.5;
          color: var(--primary-text-color);
        }
        .detail-vivino-open-link {
          font-size: 11.5px;
          color: var(--primary-color);
          display: inline-flex;
          align-items: center;
          gap: 2px;
          text-decoration: none;
        }
        .detail-form-actions {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        /* Container query inside modal so L / XL font scale or mobile stacks cleanly */
        @container lsmodal (max-width: 520px) {
          .detail-overview-header {
            flex-direction: column;
            align-items: flex-start;
            gap: 10px;
          }
          .form-grid, .split-2 {
            grid-template-columns: 1fr !important;
          }
          .detail-research-btn {
            margin-left: 0;
            width: 100%;
          }
          .detail-form-actions {
            flex-direction: column-reverse;
            align-items: stretch;
          }
          .detail-form-actions button {
            width: 100%;
          }
        }
        .search-wrap {
          position: relative;
          display: flex;
          align-items: center;
        }
        .search-icon {
          position: absolute;
          left: 12px;
          color: var(--secondary-text-color);
          --mdc-icon-size: 20px;
          pointer-events: none;
        }
        .search-input {
          padding-left: 40px !important;
          padding-right: 44px !important;
          min-height: 48px;
          font-size: 16px !important; /* >=16px prevents iOS zoom-on-focus */
          border-radius: 12px !important;
        }
        .search-clear {
          position: absolute;
          right: 2px;
          width: 44px;
          height: 44px;
          border: none;
          background: transparent;
          color: var(--secondary-text-color);
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .font-scale-label { font-weight: 700; letter-spacing: -0.5px; }
        .font-scale-label small { font-weight: 500; font-size: 10px; margin-left: 2px; }
        .stepper-group .ha-btn {
          min-width: 44px !important;
          min-height: 44px !important;
        }
        .wine-title-clickable { min-height: 44px; justify-content: center; }

        /* Mobile action bar (thumb zone) - hidden on wide cards */
        .mobile-action-bar { display: none; }

        /* ---------- BASE = narrow phone (< 600px card width) ---------- */
        @container lscard (max-width: 599px) {
          .ls-root { padding-bottom: 4px; }
          .desktop-actions { display: none !important; }
          .header-bar { margin-bottom: 10px; }
          .controls-bar { flex-direction: column; gap: 8px; }
          .search-wrap, .sort-wrap { width: 100%; flex: 1 1 100%; }
          .sort-wrap select { min-height: 48px; font-size: 15px; border-radius: 12px; }
          .filter-chips {
            flex-wrap: nowrap;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
            margin: 0 -4px 12px;
            padding: 0 4px 4px;
          }
          .filter-chip { min-height: 40px; padding: 8px 14px; font-size: 13px; white-space: nowrap; flex-shrink: 0; }
          .wine-grid { grid-template-columns: 1fr !important; gap: 10px; }
          .wine-meta-grid, .wine-notes-preview { display: none; }
          .wine-actions-bar .ha-btn { min-height: 44px; }

          /* List view becomes a clean single-column card list */
          .wine-list-table-container { border: none; background: transparent; }
          .wine-list-table thead { display: none; }
          .wine-list-table, .wine-list-table tbody { display: block; width: 100%; }
          .wine-list-table tr {
            display: grid;
            grid-template-columns: 44px minmax(0, 1fr) auto;
            align-items: center;
            gap: 10px;
            padding: 10px 12px;
            margin-bottom: 8px;
            border: 1px solid var(--divider-color, #e0e0e0);
            border-radius: 12px;
            background: var(--card-background-color, #fff);
          }
          .wine-list-table td { padding: 0 !important; border: none !important; }
          .wine-list-table td:nth-child(3),
          .wine-list-table td:nth-child(4),
          .wine-list-table td:nth-child(5),
          .wine-list-table td:nth-child(6),
          .wine-list-table td:nth-child(8) { display: none; }
          .wine-list-title-wrap { max-width: none !important; }
          .wine-list-title-wrap strong { font-size: 15px; white-space: normal; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
          .wine-list-title-wrap span { font-size: 13px; }
          .wine-list-title-wrap span:nth-of-type(2) { display: none; }
          .wine-list-thumb, .wine-list-thumb-placeholder { width: 44px; height: 58px; }
          .stepper-group .qty-badge { min-width: 28px !important; font-size: 15px !important; }

          /* Sticky bottom action bar in thumb reach */
          .mobile-action-bar {
            display: grid;
            grid-template-columns: 56px 56px 56px 1fr;
            gap: 8px;
            position: sticky;
            bottom: 0;
            z-index: 5;
            margin: 12px -4px -4px;
            padding: 10px 4px calc(10px + env(safe-area-inset-bottom, 0px));
            background: var(--ha-card-background, var(--card-background-color, #fff));
            border-top: 1px solid var(--divider-color, #e0e0e0);
          }
          .mab-btn {
            min-height: 56px;
            flex-direction: column;
            gap: 2px;
            padding: 6px 4px;
            border-radius: 14px;
            font-size: 11px;
          }
          .mab-btn small { font-size: 11px; }
          .mab-primary { flex-direction: row; gap: 8px; font-size: 16px; font-weight: 600; }
          .mab-primary ha-icon { --mdc-icon-size: 26px; }
        }

        /* ---------- >= 380px: show vintage + rating in list ---------- */
        @container lscard (min-width: 380px) and (max-width: 599px) {
          .wine-list-table tr { grid-template-columns: 44px minmax(0, 1fr) auto auto auto; }
          .wine-list-table td:nth-child(3),
          .wine-list-table td:nth-child(6) { display: block; }
        }

        /* ---------- < 380px: trim grid cards to essentials ---------- */
        @container lscard (max-width: 379px) {
          .wine-list-table tr { grid-template-columns: 44px minmax(0, 1fr); row-gap: 6px; }
          .wine-list-table td:nth-child(7) { grid-column: 2; justify-self: start; }
          .wine-sub, .wine-ratings-row .personal-badge, .drink-pill { display: none; }
          .env-pills .env-pill { display: none; }
          .mobile-action-bar { grid-template-columns: 48px 48px 48px 1fr; gap: 6px; }
        }

        /* ---------- 600-899px: tablet - hide sub-meta line in list ---------- */
        @container lscard (min-width: 600px) and (max-width: 899px) {
          .wine-list-title-wrap span:nth-of-type(2) { display: none; }
          .wine-list-table th:nth-child(8), .wine-list-table td:nth-child(8),
          .wine-list-table th:nth-child(4), .wine-list-table td:nth-child(4) { display: none; }
          .wine-list-table th, .wine-list-table td { padding: 8px !important; }
          .wine-grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
          .wine-notes-preview { display: none; }
        }
      </style>

      <ha-card class="${isTastedTab ? "tasted-card-theme" : ""}" style="--ls-scale: ${this._effectiveFontScale()};">
       <div class="ls-root">
       <div class="ls-cq">
        <div class="header-bar">
          <div class="title-block">
            <h1>
              <img src="/homecave/icon-192.png" alt="" style="width: 28px; height: 28px; border-radius: 7px; object-fit: contain; vertical-align: middle; flex-shrink: 0;" />
              <span>${escapeHtml(this._cellarName)}</span>
            </h1>
            <div class="env-pills">
              <span><strong>${num(stats.total_bottles)}</strong> bottles (${num(stats.unique_wines)} unique)</span>
              ${
                this._tempEntity
                  ? `<span class="env-pill"><ha-icon icon="mdi:thermometer"></ha-icon><span id="env-temp-val">—</span></span>`
                  : ""
              }
              ${
                this._humidityEntity
                  ? `<span class="env-pill"><ha-icon icon="mdi:water-percent"></ha-icon><span id="env-hum-val">—</span></span>`
                  : ""
              }
            </div>
          </div>

          <div class="header-actions desktop-actions">
            <button class="ha-btn secondary-btn icon-btn btn-font-scale" title="Text size (tap to change)" aria-label="Text size ${this._fontScaleLabel()}">
              <span class="font-scale-label">Aa <small>${this._fontScaleLabel()}</small></span>
            </button>
            <button class="ha-btn ${this._viewMode === "grid" ? "active-view" : "secondary-btn"} icon-btn" id="btn-toggle-grid" title="Grid View">
              <ha-icon icon="mdi:view-grid"></ha-icon>
            </button>
            <button class="ha-btn ${this._viewMode === "list" ? "active-view" : "secondary-btn"} icon-btn" id="btn-toggle-list" title="Line-Item List View">
              <ha-icon icon="mdi:view-list"></ha-icon>
            </button>
            ${
              this._config?.show_mobile_app_button !== false
                ? `<a class="ha-btn secondary-btn" href="/homecave/index.html" target="_blank" rel="noopener noreferrer" title="Open Standalone Mobile App (Installable PWA)" style="text-decoration: none;">
                    <ha-icon icon="mdi:cellphone-arrow-down"></ha-icon>
                    <span>Mobile App</span>
                  </a>`
                : ""
            }
            <button class="ha-btn primary-btn" id="btn-scan-bottle">
              <ha-icon icon="mdi:camera-iris"></ha-icon>
              <span>Scan Bottle</span>
            </button>
            <button class="ha-btn secondary-btn" id="btn-add-manual">
              <ha-icon icon="mdi:plus"></ha-icon>
              <span>Add Wine</span>
            </button>
            <button class="ha-btn icon-btn" id="btn-refresh-vivino" title="Refresh Vivino Ratings">
              <ha-icon icon="mdi:refresh"></ha-icon>
            </button>
          </div>
        </div>

        <div class="controls-bar">
          <div class="search-wrap">
            <ha-icon icon="mdi:magnify" class="search-icon"></ha-icon>
            <input
              id="wine-search-input"
              class="ha-input search-input"
              type="text"
              inputmode="search"
              enterkeyhint="search"
              autocomplete="off"
              autocorrect="off"
              autocapitalize="off"
              spellcheck="false"
              aria-label="Search wines"
              placeholder="Search wines…"
              value="${escapeHtml(this._searchQuery)}"
            />
            <button type="button" class="search-clear" id="btn-clear-search" aria-label="Clear search" style="visibility: ${this._searchQuery ? "visible" : "hidden"};">
              <ha-icon icon="mdi:close-circle"></ha-icon>
            </button>
          </div>
          <div class="sort-wrap" style="display:flex; gap:6px; align-items:center;">
            <select id="wine-sort-select" class="ha-input">
              <option value="recent" ${this._sortBy === "recent" ? "selected" : ""}>Sort: Recently Added / Updated</option>
              <option value="rating" ${this._sortBy === "rating" ? "selected" : ""}>Sort: Vivino Rating (High → Low)</option>
              <option value="drink_window" ${this._sortBy === "drink_window" ? "selected" : ""}>Sort: Drink Window (Urgent First)</option>
              <option value="vintage" ${this._sortBy === "vintage" ? "selected" : ""}>Sort: Vintage (Oldest First)</option>
              <option value="quantity" ${this._sortBy === "quantity" ? "selected" : ""}>Sort: Bottle Count</option>
            </select>
            <button
              type="button"
              id="btn-card-invert-sort"
              class="action-btn"
              style="min-height:36px; min-width:36px; padding:0 8px; font-weight:700; border-radius:8px; border:1px solid var(--divider-color, #e0e0e0); background:${this._sortInverted ? "var(--primary-color, #722f37)" : "transparent"}; color:${this._sortInverted ? "#fff" : "var(--primary-text-color)"}; cursor:pointer;"
              title="${this._sortInverted ? "Inverted: Newest / Lowest / Furthest" : "Default: Oldest / Highest / Urgent"}"
            >
              ${this._sortInverted ? "⇅ ▾" : "⇅ ▴"}
            </button>
          </div>
        </div>

        <div class="filter-chips">
          ${tabs
            .map(
              (t) => `
              <button class="filter-chip ${(this._filterTab === t.id || (t.id === "tasted" && this._filterTab === "archived")) ? "active" : ""}" data-tab="${t.id}">
                ${escapeHtml(t.label)}
              </button>
            `
            )
            .join("")}
        </div>

        ${
          this._error
            ? `<div class="card-alert-banner" role="alert">
                <div class="card-alert-content">
                  <ha-icon icon="mdi:alert-circle" style="color: var(--error-color, #c62828);"></ha-icon>
                  <span>${escapeHtml(this._error)}</span>
                </div>
                <button class="ha-btn icon-btn" id="dismiss-card-error-btn" title="Dismiss">
                  <ha-icon icon="mdi:close"></ha-icon>
                </button>
              </div>`
            : ""
        }

        <div id="wine-results">${this._renderResults(wines)}</div>

        <nav class="mobile-action-bar" aria-label="Cellar actions">
          <button class="ha-btn secondary-btn mab-btn btn-font-scale" aria-label="Text size ${this._fontScaleLabel()}">
            <span class="font-scale-label">Aa</span><small>${this._fontScaleLabel()}</small>
          </button>
          <button class="ha-btn secondary-btn mab-btn" id="btn-mobile-view" aria-label="Switch to ${this._viewMode === "list" ? "grid" : "list"} view">
            <ha-icon icon="${this._viewMode === "list" ? "mdi:view-grid" : "mdi:view-list"}"></ha-icon><small>${this._viewMode === "list" ? "Grid" : "List"}</small>
          </button>
          <button class="ha-btn secondary-btn mab-btn" id="btn-mobile-add" aria-label="Add wine manually">
            <ha-icon icon="mdi:plus"></ha-icon><small>Add</small>
          </button>
          <button class="ha-btn primary-btn mab-btn mab-primary" id="btn-mobile-scan" aria-label="Scan bottle">
            <ha-icon icon="mdi:camera-iris"></ha-icon><span>Scan Bottle</span>
          </button>
        </nav>
       </div>
       </div>
      </ha-card>

      ${this._activeModal === "scan" ? this._renderScanModal() : ""}
      ${this._activeModal === "crop_rotate" ? this._renderCropRotateModal() : ""}
      ${this._activeModal === "quick_add" ? this._renderQuickAddModal() : ""}
      ${this._activeModal === "consume" ? this._renderConsumeModal() : ""}
      ${this._activeModal === "detail" ? this._renderDetailModal() : ""}
      ${this._activeModal === "image_preview" ? this._renderImagePreviewModal() : ""}
    `;

    this._updateEnvironmentBadges();
    this._bindEvents();
  }
}

class WineInventoryCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    this._config = {
      title: "HomeCave",
      default_view: "list",
      default_tab: "all",
      font_scale: "",
      show_mobile_app_button: true,
      ...(config || {}),
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
  }

  _emitConfigChanged() {
    const cfg = { ...this._config };
    if (!cfg.font_scale) delete cfg.font_scale;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: cfg },
        bubbles: true,
        composed: true,
      })
    );
  }

  _render() {
    if (!this.shadowRoot) return;
    const c = this._config;
    this.shadowRoot.innerHTML = `
      <style>
        .editor-wrap {
          display: flex;
          flex-direction: column;
          gap: 14px;
          padding: 12px 4px;
          font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
          color: var(--primary-text-color);
        }
        .editor-row {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .editor-row label {
          font-size: 13px;
          font-weight: 600;
          color: var(--secondary-text-color);
        }
        .editor-input {
          padding: 9px 12px;
          border-radius: 8px;
          border: 1px solid var(--divider-color, #ccc);
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color);
          font-size: 14px;
        }
        .editor-checkbox {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 14px;
          cursor: pointer;
        }
        .editor-note {
          font-size: 12px;
          color: var(--secondary-text-color);
          background: var(--secondary-background-color, rgba(127,127,127,0.08));
          padding: 10px 12px;
          border-radius: 8px;
          line-height: 1.45;
        }
      </style>
      <div class="editor-wrap">
        <div class="editor-row">
          <label for="ed-title">Card Title</label>
          <input id="ed-title" class="editor-input" type="text" value="${escapeHtml(c.title || "HomeCave")}" placeholder="HomeCave" />
        </div>
        <div class="editor-row">
          <label for="ed-view">Default Layout View</label>
          <select id="ed-view" class="editor-input">
            <option value="list" ${c.default_view !== "grid" ? "selected" : ""}>Line-Item List View (Default)</option>
            <option value="grid" ${c.default_view === "grid" ? "selected" : ""}>Bottle Card Grid View</option>
          </select>
        </div>
        <div class="editor-row">
          <label for="ed-tab">Initial Filter Tab</label>
          <select id="ed-tab" class="editor-input">
            <option value="all" ${c.default_tab === "all" || !c.default_tab ? "selected" : ""}>In Stock (All Bottles)</option>
            <option value="ready" ${c.default_tab === "ready" ? "selected" : ""}>Ready to Drink</option>
            <option value="drink_soon" ${c.default_tab === "drink_soon" ? "selected" : ""}>Drink Soon</option>
            <option value="red" ${c.default_tab === "red" ? "selected" : ""}>Red Wines</option>
            <option value="white" ${c.default_tab === "white" ? "selected" : ""}>White Wines</option>
            <option value="rose" ${c.default_tab === "rose" ? "selected" : ""}>Rosé Wines</option>
            <option value="sparkling" ${c.default_tab === "sparkling" ? "selected" : ""}>Sparkling Wines</option>
            <option value="tasted" ${c.default_tab === "tasted" || c.default_tab === "archived" ? "selected" : ""}>🥂 Tasted (Drank / Consumed &amp; Scanned to Tasted)</option>
          </select>
        </div>
        <div class="editor-row">
          <label for="ed-scale">Default Text Size (S / M / L / XL)</label>
          <select id="ed-scale" class="editor-input">
            <option value="" ${!c.font_scale ? "selected" : ""}>Use Device Preference (Default M)</option>
            <option value="0.9" ${String(c.font_scale) === "0.9" ? "selected" : ""}>S (90%)</option>
            <option value="1" ${String(c.font_scale) === "1" || String(c.font_scale) === "1.0" ? "selected" : ""}>M (100%)</option>
            <option value="1.15" ${String(c.font_scale) === "1.15" ? "selected" : ""}>L (115%)</option>
            <option value="1.3" ${String(c.font_scale) === "1.3" ? "selected" : ""}>XL (130%)</option>
          </select>
        </div>
        <label class="editor-checkbox">
          <input id="ed-mobile-btn" type="checkbox" ${c.show_mobile_app_button !== false ? "checked" : ""} />
          <span>Show "Mobile App" launcher button in header (<code>/homecave</code>)</span>
        </label>
        <div class="editor-note">
          💡 <strong>Tip:</strong> You can also open the standalone installable mobile app at <code>/homecave/index.html</code> on your phone and add it to your Home Screen.
        </div>
      </div>
    `;

    this.shadowRoot.querySelector("#ed-title")?.addEventListener("input", (e) => {
      this._config = { ...this._config, title: e.target.value };
      this._emitConfigChanged();
    });
    this.shadowRoot.querySelector("#ed-view")?.addEventListener("change", (e) => {
      this._config = { ...this._config, default_view: e.target.value };
      this._emitConfigChanged();
    });
    this.shadowRoot.querySelector("#ed-tab")?.addEventListener("change", (e) => {
      this._config = { ...this._config, default_tab: e.target.value };
      this._emitConfigChanged();
    });
    this.shadowRoot.querySelector("#ed-scale")?.addEventListener("change", (e) => {
      const val = e.target.value ? parseFloat(e.target.value) : "";
      this._config = { ...this._config, font_scale: val };
      this._emitConfigChanged();
    });
    this.shadowRoot.querySelector("#ed-mobile-btn")?.addEventListener("change", (e) => {
      this._config = { ...this._config, show_mobile_app_button: e.target.checked };
      this._emitConfigChanged();
    });
  }
}

/**
 * Compact Addable HomeCave Dashboard Card (`custom:homecave-summary-card`)
 * Designed to fit neatly on any Home Assistant room/overview page alongside normal HA cards.
 * Includes KPI tiles, quick Scan/Add actions, standalone Mobile App launcher, and top/recent wines
 * with 1-tap Wine Detail modal & bottle stepper support.
 */
class WineCellarSummaryCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._wines = [];
    this._statistics = {};
    this._cellarName = "HomeCave";
    this._currency = "EUR";
    this._tempEntity = "";
    this._humidityEntity = "";
    this._fontScale = this._loadFontScale();
    this._helperCard = null;
  }

  static getStubConfig() {
    return {
      title: "HomeCave",
      max_items: 4,
      filter: "ready_or_recent",
      show_kpis: true,
      show_mobile_app_button: true,
    };
  }

  static getConfigElement() {
    return document.createElement("homecave-summary-card-editor");
  }

  setConfig(config) {
    this._config = {
      title: "HomeCave",
      max_items: 4,
      filter: "ready_or_recent",
      show_kpis: true,
      show_mobile_app_button: true,
      ...(config || {}),
    };
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._helperCard) {
      this._helperCard.hass = hass;
    }
    if (first) {
      this._fetchData();
    } else {
      this._updateEnv();
    }
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return {
      columns: 12,
      min_columns: 6,
      rows: "auto",
    };
  }

  _loadFontScale() {
    try {
      const stored = parseFloat(window.localStorage.getItem("homecave_font_scale") || "");
      if (stored >= 0.8 && stored <= 1.5) return stored;
    } catch (_) {}
    return 1.0;
  }

  _fontScaleLabel() {
    const scales = WineInventoryCard.FONT_SCALES;
    let best = scales[1];
    scales.forEach((s) => {
      if (Math.abs(s.value - this._fontScale) < Math.abs(best.value - this._fontScale)) best = s;
    });
    return best.id;
  }

  _cycleFontScale() {
    const scales = WineInventoryCard.FONT_SCALES;
    const idx = scales.findIndex((s) => s.id === this._fontScaleLabel());
    const next = scales[(idx + 1) % scales.length];
    this._fontScale = next.value;
    try {
      window.localStorage.setItem("homecave_font_scale", String(next.value));
    } catch (_) {}
    if (this._helperCard) {
      this._helperCard._fontScale = next.value;
      this._helperCard._render();
    }
    this._render();
  }

  async _fetchData() {
    if (!this._hass) return;
    try {
      const res = await this._hass.callWS({
        type: "homecave/list",
        include_out_of_stock: true,
      });
      this._wines = res.wines || [];
      this._statistics = res.statistics || {};
      this._cellarName = this._config.title || res.cellar_name || "HomeCave";
      this._currency = res.currency || "EUR";
      this._tempEntity = res.temperature_entity || "";
      this._humidityEntity = res.humidity_entity || "";
      this._render();
    } catch (err) {
      console.warn("WineCellarSummaryCard failed to load inventory:", err);
    }
  }

  _updateEnv() {
    if (!this.shadowRoot || !this._hass) return;
    const tEl = this.shadowRoot.querySelector("#sum-temp");
    if (tEl && this._tempEntity && this._hass.states[this._tempEntity]) {
      const st = this._hass.states[this._tempEntity];
      tEl.textContent = `${st.state}${st.attributes.unit_of_measurement || "°C"}`;
    }
    const hEl = this.shadowRoot.querySelector("#sum-hum");
    if (hEl && this._humidityEntity && this._hass.states[this._humidityEntity]) {
      const st = this._hass.states[this._humidityEntity];
      hEl.textContent = `${st.state}${st.attributes.unit_of_measurement || "%"}`;
    }
  }

  _ensureHelperCard() {
    if (!this.shadowRoot) return null;
    let host = this.shadowRoot.querySelector("#summary-modal-host");
    if (!host) return null;
    if (!this._helperCard) {
      this._helperCard = document.createElement("homecave-card");
      this._helperCard.setConfig({ title: this._cellarName });
      this._helperCard.hass = this._hass;
      const origFetch = this._helperCard._fetchInventory.bind(this._helperCard);
      this._helperCard._fetchInventory = async () => {
        await origFetch();
        await this._fetchData();
      };
    }
    this._helperCard._wines = this._wines;
    this._helperCard._statistics = this._statistics;
    this._helperCard._currency = this._currency;
    this._helperCard._fontScale = this._fontScale;
    if (!host.contains(this._helperCard)) {
      host.innerHTML = "";
      host.appendChild(this._helperCard);
      const style = document.createElement("style");
      style.textContent = `ha-card { display: none !important; }`;
      this._helperCard.shadowRoot?.appendChild(style);
    }
    return this._helperCard;
  }

  _getDisplayWines() {
    const inStock = this._wines.filter((w) => Number(w.quantity || 0) > 0);
    const mode = this._config.filter || "ready_or_recent";
    let list = [...inStock];
    if (mode === "ready") {
      list = inStock.filter((w) => w.drink_status === "ready" || w.drink_status === "drink_soon");
    } else if (mode === "top_rated") {
      list.sort((a, b) => Number(b.vivino_rating || 0) - Number(a.vivino_rating || 0));
    } else if (mode === "ready_or_recent") {
      list.sort((a, b) => {
        const aReady = a.drink_status === "ready" || a.drink_status === "drink_soon" ? 1 : 0;
        const bReady = b.drink_status === "ready" || b.drink_status === "drink_soon" ? 1 : 0;
        if (aReady !== bReady) return bReady - aReady;
        return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
      });
    }
    const maxItems = Math.max(1, Math.min(12, Number(this._config.max_items || 4)));
    return list.slice(0, maxItems);
  }

  _render() {
    if (!this.shadowRoot) return;
    const stats = this._statistics || {};
    const wines = this._getDisplayWines();

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          --ls-scale: ${this._fontScale || 1};
          display: block;
          font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
          color: var(--primary-text-color);
        }
        ha-card {
          padding: 16px;
          border-radius: var(--ha-card-border-radius, 14px);
          background: var(--ha-card-background, var(--card-background-color, #fff));
        }
        .sum-root {
          zoom: var(--ls-scale, 1);
        }
        .sum-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 8px;
          margin-bottom: 12px;
          flex-wrap: wrap;
        }
        .sum-title {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 17px;
          font-weight: 700;
          margin: 0;
        }
        .sum-brand-badge {
          width: 28px;
          height: 28px;
          border-radius: 7px;
          background: #ffffff;
          border: 1px solid #e6e0e2;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          color: #a61a2e;
          flex-shrink: 0;
        }
        .sum-actions {
          display: flex;
          align-items: center;
          gap: 6px;
          flex-wrap: wrap;
        }
        .sum-btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 5px;
          padding: 6px 11px;
          min-height: 36px;
          border-radius: 9px;
          font-size: 12.5px;
          font-weight: 600;
          border: 1px solid var(--divider-color, #e0e0e0);
          background: var(--secondary-background-color, rgba(127,127,127,0.08));
          color: var(--primary-text-color);
          cursor: pointer;
          text-decoration: none;
        }
        .sum-btn.primary {
          background: var(--primary-color, #a61a2e);
          color: var(--text-primary-color, #fff);
          border-color: transparent;
        }
        .sum-kpis {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 8px;
          margin-bottom: 12px;
        }
        .sum-kpi {
          padding: 8px 10px;
          border-radius: 10px;
          background: var(--secondary-background-color, rgba(127,127,127,0.06));
          border: 1px solid var(--divider-color, #ebebeb);
          text-align: center;
        }
        .sum-kpi strong {
          display: block;
          font-size: 16px;
          color: var(--primary-color, #a61a2e);
        }
        .sum-kpi span {
          font-size: 11px;
          color: var(--secondary-text-color);
        }
        .sum-list {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .sum-row {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 8px 10px;
          border-radius: 10px;
          border: 1px solid var(--divider-color, #e6e6e6);
          background: var(--card-background-color, #fff);
          cursor: pointer;
        }
        .sum-row:hover {
          background: var(--secondary-background-color, rgba(127,127,127,0.04));
        }
        .sum-thumb {
          width: 34px;
          height: 46px;
          border-radius: 5px;
          object-fit: cover;
          flex-shrink: 0;
          background: rgba(166,26,46,0.1);
          display: flex;
          align-items: center;
          justify-content: center;
          color: #a61a2e;
        }
        .sum-info {
          flex: 1;
          min-width: 0;
        }
        .sum-wine-name {
          font-size: 13.5px;
          font-weight: 600;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          color: var(--primary-text-color);
        }
        .sum-wine-sub {
          font-size: 11.5px;
          color: var(--secondary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .sum-qty-pill {
          padding: 3px 9px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 700;
          background: rgba(166, 26, 46, 0.12);
          color: #a61a2e;
          flex-shrink: 0;
        }
      </style>
      <ha-card>
        <div class="sum-root">
          <div class="sum-header">
            <h2 class="sum-title">
              <img src="/homecave/icon-192.png" alt="" style="width: 26px; height: 26px; border-radius: 6px; object-fit: contain; vertical-align: middle; flex-shrink: 0;" />
              <span>${escapeHtml(this._cellarName)}</span>
            </h2>
            <div class="sum-actions">
              <button class="sum-btn" id="sum-font-btn" title="Adjust text size">Aa ${this._fontScaleLabel()}</button>
              ${
                this._config.show_mobile_app_button !== false
                  ? `<a class="sum-btn" href="/homecave/index.html" target="_blank" rel="noopener noreferrer" title="Open Standalone Mobile App">
                      <ha-icon icon="mdi:cellphone" style="--mdc-icon-size: 16px;"></ha-icon>
                      <span>App</span>
                    </a>`
                  : ""
              }
              <button class="sum-btn primary" id="sum-scan-btn">
                <ha-icon icon="mdi:camera-iris" style="--mdc-icon-size: 16px;"></ha-icon>
                <span>Scan</span>
              </button>
            </div>
          </div>

          ${
            this._config.show_kpis !== false
              ? `<div class="sum-kpis">
                  <div class="sum-kpi">
                    <strong>${num(stats.total_bottles)}</strong>
                    <span>Bottles</span>
                  </div>
                  <div class="sum-kpi">
                    <strong>${num(stats.ready_to_drink_bottles)}</strong>
                    <span>Ready Now</span>
                  </div>
                  <div class="sum-kpi">
                    <strong>${stats.estimated_cellar_value ? `${Math.round(num(stats.estimated_cellar_value))} ${escapeHtml(this._currency)}` : num(stats.unique_wines)}</strong>
                    <span>${stats.estimated_cellar_value ? "Est. Value" : "Unique Wines"}</span>
                  </div>
                </div>`
              : ""
          }

          <div class="sum-list">
            ${
              wines.length === 0
                ? `<div style="text-align: center; padding: 14px; font-size: 12.5px; color: var(--secondary-text-color);">No bottles in stock yet. Tap <strong>Scan</strong> to add one.</div>`
                : wines
                    .map(
                      (w) => `
                      <div class="sum-row" data-wine-id="${num(w.id)}" title="Tap to open wine details">
                        ${
                          safeImageUrl(w.image_url)
                            ? `<img class="sum-thumb" src="${safeImageUrl(w.image_url)}" alt="${escapeHtml(w.name)}" />`
                            : `<div class="sum-thumb"><ha-icon icon="mdi:glass-wine" style="--mdc-icon-size: 18px;"></ha-icon></div>`
                        }
                        <div class="sum-info">
                          <div class="sum-wine-name">${escapeHtml(w.name)} (${escapeHtml(w.vintage || "NV")})</div>
                          <div class="sum-wine-sub">
                            ${escapeHtml(w.winery)}
                            ${w.vivino_rating ? ` • ★ ${Number(w.vivino_rating).toFixed(1)}` : ""}
                            ${w.last_storage_location ? ` • 📍 ${escapeHtml(w.last_storage_location)}` : ""}
                          </div>
                        </div>
                        <span class="sum-qty-pill">${Number(w.quantity || 0)}x</span>
                      </div>
                    `
                    )
                    .join("")
            }
          </div>
        </div>
      </ha-card>
      <div id="summary-modal-host"></div>
    `;

    this._updateEnv();
    this.shadowRoot.querySelector("#sum-font-btn")?.addEventListener("click", () => this._cycleFontScale());
    this.shadowRoot.querySelector("#sum-scan-btn")?.addEventListener("click", () => {
      const helper = this._ensureHelperCard();
      if (helper) helper._openScanModal(false);
    });
    this.shadowRoot.querySelectorAll(".sum-row").forEach((row) => {
      row.addEventListener("click", () => {
        const wineId = Number(row.dataset.wineId);
        const wine = this._wines.find((w) => Number(w.id) === wineId);
        if (!wine) return;
        const helper = this._ensureHelperCard();
        if (helper) helper._openDetailModal(wine);
      });
    });
  }
}

class WineCellarSummaryCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
  }

  setConfig(config) {
    this._config = {
      title: "HomeCave",
      max_items: 4,
      filter: "ready_or_recent",
      show_kpis: true,
      show_mobile_app_button: true,
      ...(config || {}),
    };
    this._render();
  }

  _emit() {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: { ...this._config } },
        bubbles: true,
        composed: true,
      })
    );
  }

  _render() {
    if (!this.shadowRoot) return;
    const c = this._config;
    this.shadowRoot.innerHTML = `
      <style>
        .ed-wrap { display: flex; flex-direction: column; gap: 12px; padding: 10px 4px; font-family: Roboto, sans-serif; }
        .ed-row { display: flex; flex-direction: column; gap: 5px; }
        .ed-row label { font-size: 13px; font-weight: 600; color: var(--secondary-text-color); }
        .ed-input { padding: 8px 10px; border-radius: 8px; border: 1px solid var(--divider-color, #ccc); font-size: 14px; }
        .ed-check { display: flex; align-items: center; gap: 8px; font-size: 14px; }
      </style>
      <div class="ed-wrap">
        <div class="ed-row">
          <label>Card Title</label>
          <input id="s-title" class="ed-input" type="text" value="${escapeHtml(c.title || "HomeCave")}" />
        </div>
        <div class="ed-row">
          <label>Wines to Display</label>
          <select id="s-filter" class="ed-input">
            <option value="ready_or_recent" ${c.filter === "ready_or_recent" ? "selected" : ""}>Ready to Drink &amp; Recent</option>
            <option value="ready" ${c.filter === "ready" ? "selected" : ""}>Ready to Drink Only</option>
            <option value="top_rated" ${c.filter === "top_rated" ? "selected" : ""}>Highest Vivino Rating</option>
          </select>
        </div>
        <div class="ed-row">
          <label>Max Bottles Shown (1–12)</label>
          <input id="s-max" class="ed-input" type="number" min="1" max="12" value="${Number(c.max_items || 4)}" />
        </div>
        <label class="ed-check">
          <input id="s-kpis" type="checkbox" ${c.show_kpis !== false ? "checked" : ""} />
          <span>Show Summary KPI Tiles</span>
        </label>
        <label class="ed-check">
          <input id="s-app" type="checkbox" ${c.show_mobile_app_button !== false ? "checked" : ""} />
          <span>Show Mobile App Button (<code>/homecave</code>)</span>
        </label>
      </div>
    `;
    this.shadowRoot.querySelector("#s-title")?.addEventListener("input", (e) => {
      this._config = { ...this._config, title: e.target.value };
      this._emit();
    });
    this.shadowRoot.querySelector("#s-filter")?.addEventListener("change", (e) => {
      this._config = { ...this._config, filter: e.target.value };
      this._emit();
    });
    this.shadowRoot.querySelector("#s-max")?.addEventListener("change", (e) => {
      this._config = { ...this._config, max_items: Number(e.target.value || 4) };
      this._emit();
    });
    this.shadowRoot.querySelector("#s-kpis")?.addEventListener("change", (e) => {
      this._config = { ...this._config, show_kpis: e.target.checked };
      this._emit();
    });
    this.shadowRoot.querySelector("#s-app")?.addEventListener("change", (e) => {
      this._config = { ...this._config, show_mobile_app_button: e.target.checked };
      this._emit();
    });
  }
}

if (!customElements.get("homecave-card-editor")) {
  customElements.define("homecave-card-editor", WineInventoryCardEditor);
}
if (!customElements.get("homecave-card")) {
  customElements.define("homecave-card", WineInventoryCard);
}
if (!customElements.get("homecave-summary-card-editor")) {
  customElements.define("homecave-summary-card-editor", WineCellarSummaryCardEditor);
}
if (!customElements.get("homecave-summary-card")) {
  customElements.define("homecave-summary-card", WineCellarSummaryCard);
}
// Backward-compatible element tag aliases for existing dashboard YAML
if (!customElements.get("wine-inventory-card-editor")) {
  customElements.define("wine-inventory-card-editor", class extends WineInventoryCardEditor {});
}
if (!customElements.get("wine-inventory-card")) {
  customElements.define("wine-inventory-card", class extends WineInventoryCard {});
}
if (!customElements.get("wine-cellar-summary-card-editor")) {
  customElements.define("wine-cellar-summary-card-editor", class extends WineCellarSummaryCardEditor {});
}
if (!customElements.get("wine-cellar-summary-card")) {
  customElements.define("wine-cellar-summary-card", class extends WineCellarSummaryCard {});
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "homecave-card")) {
  window.customCards.push({
    type: "homecave-card",
    name: "HomeCave Wine Cellar Card",
    description: "HomeCave - your local Wine Cellar: interactive list & grid with AI label scanner, Vivino ratings, taste sliders, and stock management.",
    preview: true,
    documentationURL: "https://github.com/Maartenwiese/HomeCave",
  });
}
if (!window.customCards.some((c) => c.type === "homecave-summary-card")) {
  window.customCards.push({
    type: "homecave-summary-card",
    name: "HomeCave Cellar Quick Card",
    description: "Compact HomeCave overview card for any Home Assistant page: KPIs, Quick Scan, Mobile App link, and top/ready bottles.",
    preview: true,
    documentationURL: "https://github.com/Maartenwiese/HomeCave",
  });
}

class WineSommAICard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._messages = [];
    this._pendingImage = null;
    this._loading = false;
    this._error = "";
  }

  static getConfigElement() {
    return document.createElement("homecave-sommai-card-editor");
  }

  static getStubConfig() {
    return {
      title: "SommAI Sommelier",
    };
  }

  setConfig(config) {
    this._config = {
      title: "SommAI Sommelier",
      ...(config || {}),
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
  }

  getCardSize() {
    return 6;
  }

  _render() {
    if (!this.shadowRoot) return;
    const c = this._config;
    const title = c.title || "SommAI Sommelier";

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          --somm-brand: var(--primary-color, #7a2858);
          --somm-surface: var(--card-background-color, #ffffff);
          --somm-border: var(--divider-color, rgba(0, 0, 0, 0.12));
          --somm-text: var(--primary-text-color, #202124);
          --somm-muted: var(--secondary-text-color, #5f6368);
        }
        ha-card {
          padding: 16px;
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, none);
          border: 1px solid var(--somm-border);
          background: var(--somm-surface);
          color: var(--somm-text);
          font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
        }
        .header {
          display: flex;
          align-items: center;
          gap: 12px;
          margin-bottom: 12px;
          padding-bottom: 10px;
          border-bottom: 1px solid var(--somm-border);
        }
        .avatar {
          font-size: 28px;
          line-height: 1;
        }
        .title-block {
          flex: 1;
        }
        .title {
          font-size: 18px;
          font-weight: 700;
          color: var(--somm-brand);
          margin: 0;
        }
        .subtitle {
          font-size: 12.5px;
          color: var(--somm-muted);
        }
        .chips {
          display: flex;
          gap: 6px;
          overflow-x: auto;
          padding: 4px 0 10px;
          scrollbar-width: none;
        }
        .chips::-webkit-scrollbar { display: none; }
        .chip {
          padding: 6px 12px;
          border-radius: 16px;
          border: 1px solid var(--somm-border);
          background: var(--card-background-color, rgba(0,0,0,0.03));
          color: var(--somm-text);
          font-size: 12px;
          font-weight: 600;
          cursor: pointer;
          white-space: nowrap;
          transition: all 0.15s ease;
        }
        .chip:hover {
          background: var(--somm-brand);
          color: #ffffff;
          border-color: var(--somm-brand);
        }
        .chat-stream {
          max-height: 420px;
          overflow-y: auto;
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding: 8px 0;
          margin-bottom: 12px;
        }
        .msg {
          display: flex;
          flex-direction: column;
          max-width: 92%;
        }
        .msg-user {
          align-self: flex-end;
        }
        .msg-user .bubble {
          background: var(--somm-brand);
          color: #ffffff;
          border-radius: 14px 14px 2px 14px;
          padding: 10px 14px;
          font-size: 13.5px;
          line-height: 1.4;
        }
        .user-img {
          max-width: 120px;
          max-height: 120px;
          border-radius: 8px;
          object-fit: cover;
          margin-bottom: 6px;
        }
        .msg-bot {
          align-self: flex-start;
          width: 100%;
        }
        .msg-bot .bubble {
          background: var(--card-background-color, rgba(0,0,0,0.02));
          border: 1px solid var(--somm-border);
          border-radius: 14px 14px 14px 2px;
          padding: 12px 14px;
          font-size: 13.5px;
          line-height: 1.45;
        }
        .dish-title {
          font-size: 15px;
          font-weight: 700;
          color: var(--somm-brand);
          margin-bottom: 6px;
        }
        .analysis-box {
          margin: 8px 0;
          padding: 8px 10px;
          border-radius: 8px;
          background: rgba(0,0,0,0.03);
          font-size: 12.5px;
          line-height: 1.4;
        }
        .recs-header {
          font-size: 12px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: var(--somm-brand);
          margin: 10px 0 4px;
        }
        .rec-card {
          padding: 10px;
          border-radius: 10px;
          border: 1px solid var(--somm-border);
          background: var(--somm-surface);
          margin-top: 6px;
        }
        .rec-top {
          display: flex;
          align-items: center;
          gap: 6px;
          flex-wrap: wrap;
          margin-bottom: 4px;
        }
        .pill {
          display: inline-block;
          padding: 2px 8px;
          border-radius: 10px;
          font-size: 11px;
          font-weight: 700;
        }
        .pill-best { background: #e6f4ea; color: #137333; }
        .pill-alt { background: #e8f0fe; color: #1a73e8; }
        .pill-meta { background: rgba(0,0,0,0.06); color: var(--somm-text); }
        .rec-name {
          font-size: 14px;
          font-weight: 700;
          margin-bottom: 4px;
        }
        .rec-notes {
          font-size: 12.5px;
          color: var(--somm-muted);
        }
        .input-dock {
          border-top: 1px solid var(--somm-border);
          padding-top: 10px;
        }
        .pending-bar {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 4px 8px;
          background: rgba(0,0,0,0.04);
          border-radius: 8px;
          margin-bottom: 8px;
          font-size: 12px;
        }
        .pending-thumb {
          width: 28px;
          height: 28px;
          border-radius: 4px;
          object-fit: cover;
        }
        .input-row {
          display: flex;
          gap: 8px;
          align-items: center;
        }
        .photo-btn {
          width: 40px;
          height: 40px;
          border-radius: 50%;
          border: 1px solid var(--somm-border);
          background: transparent;
          font-size: 18px;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .text-input {
          flex: 1;
          height: 40px;
          padding: 8px 12px;
          border-radius: 20px;
          border: 1px solid var(--somm-border);
          background: var(--somm-surface);
          color: var(--somm-text);
          font-size: 14px;
          outline: none;
        }
        .text-input:focus {
          border-color: var(--somm-brand);
        }
        .send-btn {
          height: 40px;
          padding: 0 16px;
          border-radius: 20px;
          border: none;
          background: var(--somm-brand);
          color: #ffffff;
          font-weight: 700;
          font-size: 13px;
          cursor: pointer;
        }
        .send-btn:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }
        .thinking {
          font-style: italic;
          color: var(--somm-muted);
          font-size: 13px;
        }
      </style>

      <ha-card>
        <div class="header">
          <span class="avatar">🍷🤖</span>
          <div class="title-block">
            <h2 class="title">${escapeHtml(title)}</h2>
            <div class="subtitle">AI Food &amp; Wine Pairing from your Cellar</div>
          </div>
        </div>

        <div class="chips">
          <button class="chip" data-dish="Grilled Ribeye with Herb Butter">🥩 Ribeye Steak</button>
          <button class="chip" data-dish="Truffle &amp; Porcini Tagliatelle">🍝 Truffle Pasta</button>
          <button class="chip" data-dish="Salmon &amp; Tuna Sashimi">🍣 Salmon Sashimi</button>
          <button class="chip" data-dish="Roast Duck Breast with Cherries">🍗 Duck Breast</button>
          <button class="chip" data-dish="Artisanal Aged Cheese Board">🧀 Artisanal Cheeses</button>
          <button class="chip" data-dish="Spicy Thai Green Curry">🌶️ Thai Green Curry</button>
        </div>

        <div class="chat-stream" id="chat-stream">
          ${
            this._messages.length === 0
              ? `<div style="text-align:center; padding:20px; color:var(--somm-muted); font-size:13px;">
                  Tell me what you are eating, paste a recipe, or take a picture of your dish to find the best matching bottle from your cellar!
                </div>`
              : this._messages.map((m) => this._renderMessage(m)).join("")
          }
          ${
            this._loading
              ? `<div class="msg msg-bot">
                  <div class="bubble thinking">🍷 Sommelier is analyzing cellar inventory &amp; flavor pairing…</div>
                </div>`
              : ""
          }
          ${
            this._error
              ? `<div style="padding:8px 12px; border-radius:8px; background:rgba(211,47,47,0.1); color:#d32f2f; font-size:12.5px;">
                  ${escapeHtml(this._error)}
                </div>`
              : ""
          }
        </div>

        <div class="input-dock">
          ${
            this._pendingImage
              ? `<div class="pending-bar">
                  <img src="${safeImageUrl(this._pendingImage)}" class="pending-thumb" />
                  <span style="flex:1;">Dish photo attached</span>
                  <button id="clear-img-btn" style="background:none; border:none; cursor:pointer; font-weight:700;">✕</button>
                </div>`
              : ""
          }
          <div class="input-row">
            <input type="file" id="dish-file-inp" accept="image/*" style="display:none;" />
            <button class="photo-btn" id="pick-photo-btn" title="Take photo or pick image">📷</button>
            <input class="text-input" id="msg-inp" placeholder="Describe your dish or ingredients…" autocomplete="off" />
            <button class="send-btn" id="send-btn" ${this._loading ? "disabled" : ""}>Pair 🍷</button>
          </div>
        </div>
      </ha-card>
    `;

    this._bindEvents();
  }

  _renderMessage(m) {
    if (m.role === "user") {
      return `
        <div class="msg msg-user">
          <div class="bubble">
            ${safeImageUrl(m.image_data) ? `<img src="${safeImageUrl(m.image_data)}" class="user-img" />` : ""}
            <div>${escapeHtml(m.text || "Pair this dish")}</div>
          </div>
        </div>
      `;
    }
    const recs = m.recommended_wines || [];
    return `
      <div class="msg msg-bot">
        <div class="bubble">
          ${m.dish_name ? `<div class="dish-title">🍽️ ${escapeHtml(m.dish_name)}</div>` : ""}
          <div style="line-height:1.45; margin-bottom:6px;">${escapeHtml(m.conversational_response || m.text || "")}</div>
          ${
            m.dish_analysis
              ? `<div class="analysis-box"><strong>Dish Analysis:</strong> ${escapeHtml(m.dish_analysis)}</div>`
              : ""
          }
          ${
            m.pairing_reasoning
              ? `<div class="analysis-box" style="border-left:3px solid var(--somm-brand);"><strong>Pairing Strategy:</strong> ${escapeHtml(m.pairing_reasoning)}</div>`
              : ""
          }
          ${
            recs.length > 0
              ? `<div class="recs-header">🍾 Recommended from Cellar</div>
                 ${recs
                   .map((rec) => {
                     const isBest = rec.match_type === "best";
                     return `
                       <div class="rec-card">
                         <div class="rec-top">
                           <span class="pill ${isBest ? "pill-best" : "pill-alt"}">${isBest ? "★ Best Match" : "Alternative Match"}</span>
                           ${rec.serving_temp_c ? `<span class="pill pill-meta">🌡️ ${escapeHtml(rec.serving_temp_c)}</span>` : ""}
                           ${Math.round(num(rec.decant_minutes)) > 0 ? `<span class="pill pill-meta">⏳ Decant ${Math.round(num(rec.decant_minutes))}m</span>` : ""}
                         </div>
                         <div class="rec-name">${escapeHtml(rec.wine_title)}</div>
                         <div class="rec-notes">${escapeHtml(rec.sommelier_notes)}</div>
                       </div>
                     `;
                   })
                   .join("")}`
              : ""
          }
        </div>
      </div>
    `;
  }

  _bindEvents() {
    const s = this.shadowRoot;
    if (!s) return;

    s.querySelectorAll(".chip[data-dish]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._sendMessage(btn.dataset.dish);
      });
    });

    s.querySelector("#pick-photo-btn")?.addEventListener("click", () => {
      s.querySelector("#dish-file-inp")?.click();
    });

    s.querySelector("#dish-file-inp")?.addEventListener("change", (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        this._pendingImage = String(reader.result || "");
        this._render();
      };
      reader.readAsDataURL(file);
      e.target.value = "";
    });

    s.querySelector("#clear-img-btn")?.addEventListener("click", () => {
      this._pendingImage = null;
      this._render();
    });

    const sendAction = () => {
      const inp = s.querySelector("#msg-inp");
      const text = (inp?.value || "").trim();
      this._sendMessage(text);
    };

    s.querySelector("#send-btn")?.addEventListener("click", sendAction);
    s.querySelector("#msg-inp")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") sendAction();
    });
  }

  async _sendMessage(textPrompt) {
    const text = (textPrompt || "").trim();
    const image = this._pendingImage;
    if (!text && !image) return;
    if (!this._hass) return;

    this._messages.push({
      role: "user",
      text: text || "What wine from my cellar pairs best with this dish?",
      image_data: image,
    });
    this._pendingImage = null;
    this._loading = true;
    this._error = "";
    this._render();

    const stream = this.shadowRoot?.querySelector("#chat-stream");
    if (stream) stream.scrollTop = stream.scrollHeight;

    try {
      const historyPayload = this._messages.slice(-6).map((m) => ({
        role: m.role,
        text: m.conversational_response || m.text,
      }));
      const wsPayload = {
        type: "homecave/sommelier_chat",
        message: text,
        history: historyPayload,
      };
      if (image) {
        wsPayload.image_data = image;
      }
      const res = await this._hass.callWS(wsPayload);
      const p = (res && (res.pairing || res)) || {};
      this._messages.push({
        role: "assistant",
        text: p.conversational_response || p.text || "Here are my pairing recommendations.",
        dish_name: p.dish_name,
        dish_analysis: p.dish_analysis,
        pairing_reasoning: p.pairing_reasoning,
        recommended_wines: p.recommended_wines || [],
        conversational_response: p.conversational_response,
      });
    } catch (err) {
      this._error = err?.message || "Failed to get sommelier recommendation.";
    } finally {
      this._loading = false;
      this._render();
      const streamEnd = this.shadowRoot?.querySelector("#chat-stream");
      if (streamEnd) streamEnd.scrollTop = streamEnd.scrollHeight;
    }
  }
}

class WineSommAICardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
  }

  setConfig(config) {
    this._config = {
      title: "SommAI Sommelier",
      ...(config || {}),
    };
    this._render();
  }

  _emit() {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: { ...this._config } },
        bubbles: true,
        composed: true,
      })
    );
  }

  _render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `
      <style>
        .ed-wrap { display: flex; flex-direction: column; gap: 10px; padding: 8px 4px; font-family: Roboto, sans-serif; }
        .ed-row { display: flex; flex-direction: column; gap: 4px; }
        .ed-row label { font-size: 13px; font-weight: 600; color: var(--secondary-text-color); }
        .ed-input { padding: 8px 10px; border-radius: 8px; border: 1px solid var(--divider-color, #ccc); font-size: 14px; }
      </style>
      <div class="ed-wrap">
        <div class="ed-row">
          <label>Card Title</label>
          <input id="somm-title" class="ed-input" type="text" value="${escapeHtml(this._config.title || "SommAI Sommelier")}" />
        </div>
      </div>
    `;
    this.shadowRoot.querySelector("#somm-title")?.addEventListener("input", (e) => {
      this._config = { ...this._config, title: e.target.value };
      this._emit();
    });
  }
}

if (!customElements.get("homecave-sommai-card-editor")) {
  customElements.define("homecave-sommai-card-editor", WineSommAICardEditor);
}
if (!customElements.get("homecave-sommai-card")) {
  customElements.define("homecave-sommai-card", WineSommAICard);
}
if (!customElements.get("wine-sommai-card-editor")) {
  customElements.define("wine-sommai-card-editor", class extends WineSommAICardEditor {});
}
if (!customElements.get("wine-sommai-card")) {
  customElements.define("wine-sommai-card", class extends WineSommAICard {});
}

if (!window.customCards.some((c) => c.type === "homecave-sommai-card")) {
  window.customCards.push({
    type: "homecave-sommai-card",
    name: "HomeCave SommAI Card",
    description: "Interactive AI Sommelier chat pairing meals & dishes with your active HomeCave wine cellar inventory.",
    preview: true,
    documentationURL: "https://github.com/Maartenwiese/HomeCave",
  });
}
