/**
 * HomeCave SommAI A2UI Renderer
 * Zero-dependency, CSP-safe declarative UI block renderer for SommAI across
 * the Mobile PWA (index.html) and the Lovelace SommAI Card.
 */
(function (globalScope) {
  "use strict";

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function num(value, fallback = 0) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function safeImageUrl(rawUrl) {
    if (!rawUrl || typeof rawUrl !== "string") return "";
    const trimmed = rawUrl.trim();
    if (/^\/api\/homecave\/cover\/[A-Za-z0-9._-]+(?:\?authSig=[A-Za-z0-9._~+/=-]+)?$/.test(trimmed)) {
      return trimmed;
    }
    if (/^https:\/\/images\.vivino\.com\//i.test(trimmed)) {
      return trimmed;
    }
    return "";
  }

  function formatDrinkStatus(status) {
    const map = {
      ready: { label: "Ready", cls: "a2ui-pill-ready" },
      drink_soon: { label: "Soon", cls: "a2ui-pill-soon" },
      hold: { label: "Keep", cls: "a2ui-pill-keep" },
      keep: { label: "Keep", cls: "a2ui-pill-keep" },
      past_peak: { label: "Past Peak", cls: "a2ui-pill-past" },
    };
    return map[String(status || "").toLowerCase()] || null;
  }

  function renderHeader(block) {
    const badge = block.badge
      ? `<span class="a2ui-badge">${esc(block.badge)}</span>`
      : "";
    const subtitle = block.subtitle
      ? `<div class="a2ui-header-sub">${esc(block.subtitle)}</div>`
      : "";
    return `
      <div class="a2ui-header">
        <div class="a2ui-header-top">
          <div class="a2ui-header-title">${esc(block.title)}</div>
          ${badge}
        </div>
        ${subtitle}
      </div>
    `;
  }

  function renderCallout(block) {
    const tone = ["info", "pairing", "warning"].includes(block.tone)
      ? block.tone
      : "info";
    return `
      <div class="a2ui-callout a2ui-callout-${esc(tone)}">
        ${block.title ? `<div class="a2ui-callout-title">${esc(block.title)}</div>` : ""}
        ${block.body ? `<div class="a2ui-callout-body">${esc(block.body)}</div>` : ""}
      </div>
    `;
  }

  function renderBottleCard(block) {
    const wid = Number.isInteger(num(block.wine_id, -1)) && num(block.wine_id, -1) > 0
      ? Math.round(num(block.wine_id))
      : null;
    const imgUrl = safeImageUrl(block.image_url);
    const matchLower = String(block.match_type || "").toLowerCase();
    const matchCls = matchLower.includes("best")
      ? "a2ui-pill-best"
      : matchLower.includes("adventurous")
        ? "a2ui-pill-adv"
        : "a2ui-pill-alt";
    const statusInfo = formatDrinkStatus(block.drink_status);
    const decantMin = Math.round(num(block.decant_minutes, 0));
    const qty = block.quantity !== undefined && block.quantity !== null ? Math.round(num(block.quantity, 0)) : null;
    const originParts = [block.region, block.country].filter(Boolean).join(" • ");

    return `
      <div class="a2ui-bottle-card ${wid ? "a2ui-clickable" : ""}" ${wid ? `data-detail-id="${wid}"` : ""}>
        <div class="a2ui-bottle-top">
          ${
            imgUrl
              ? `<img class="a2ui-bottle-thumb" src="${esc(imgUrl)}" alt="${esc(block.wine_title || "Bottle")}" loading="lazy" />`
              : `<div class="a2ui-bottle-thumb a2ui-bottle-placeholder">🍷</div>`
          }
          <div class="a2ui-bottle-main">
            <div class="a2ui-bottle-pills">
              <span class="a2ui-pill ${matchCls}">${esc(block.match_type || "Cellar Pick")}</span>
              ${statusInfo ? `<span class="a2ui-pill ${statusInfo.cls}">${esc(statusInfo.label)}</span>` : ""}
              ${qty !== null ? `<span class="a2ui-pill a2ui-pill-meta">🍾 ${qty} in stock</span>` : ""}
            </div>
            <div class="a2ui-bottle-title">${esc(block.wine_title || "Cellar Selection")}</div>
            <div class="a2ui-bottle-meta">
              ${originParts ? `<span>🌍 ${esc(originParts)}</span>` : ""}
              ${block.storage_location ? `<span>📍 ${esc(block.storage_location)}</span>` : ""}
              ${block.vivino_rating ? `<span>★ ${esc(num(block.vivino_rating).toFixed(1))}</span>` : ""}
            </div>
          </div>
        </div>
        ${block.reason ? `<div class="a2ui-bottle-reason">${esc(block.reason)}</div>` : ""}
        <div class="a2ui-bottle-footer">
          <div class="a2ui-bottle-service">
            ${block.serving_temp_c ? `<span class="a2ui-pill a2ui-pill-meta">🌡️ ${esc(block.serving_temp_c)}</span>` : ""}
            ${decantMin > 0 ? `<span class="a2ui-pill a2ui-pill-meta">⏳ Decant ${decantMin}m</span>` : ""}
          </div>
          ${wid ? `<span class="a2ui-open-link">Open Bottle ›</span>` : ""}
        </div>
      </div>
    `;
  }

  function renderComparisonTable(block) {
    const cols = Array.isArray(block.columns) ? block.columns : [];
    const rows = Array.isArray(block.rows) ? block.rows : [];
    if (cols.length < 2 || rows.length === 0) return "";
    return `
      <div class="a2ui-table-card">
        ${block.title ? `<div class="a2ui-section-title">${esc(block.title)}</div>` : ""}
        <div class="a2ui-table-scroll">
          <table class="a2ui-table">
            <thead>
              <tr>
                ${cols.map((c) => `<th>${esc(c)}</th>`).join("")}
              </tr>
            </thead>
            <tbody>
              ${rows
                .map(
                  (r) => `
                  <tr>
                    <th>${esc(r.label)}</th>
                    ${(Array.isArray(r.values) ? r.values.slice(0, cols.length - 1) : [])
                      .map((v) => `<td>${esc(v)}</td>`)
                      .join("")}
                  </tr>
                `
                )
                .join("")}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  function renderTasteMatch(block) {
    const metrics = Array.isArray(block.metrics) ? block.metrics : [];
    if (metrics.length === 0) return "";
    return `
      <div class="a2ui-taste-card">
        <div class="a2ui-section-title">${esc(block.title || "Structural Taste Match")}</div>
        <div class="a2ui-taste-legend">
          <span><i class="a2ui-dot a2ui-dot-dish"></i> Dish</span>
          <span><i class="a2ui-dot a2ui-dot-wine"></i> Wine</span>
        </div>
        ${metrics
          .map((m) => {
            const dishPct = Math.max(4, Math.min(100, Math.round(num(m.dish_level, 50))));
            const winePct = Math.max(4, Math.min(100, Math.round(num(m.wine_level, 50))));
            return `
              <div class="a2ui-taste-row">
                <div class="a2ui-taste-head">
                  <strong>${esc(m.label)}</strong>
                  ${m.note ? `<span>${esc(m.note)}</span>` : ""}
                </div>
                <div class="a2ui-taste-bars">
                  <div class="a2ui-taste-track"><div class="a2ui-taste-fill a2ui-fill-dish" style="width:${dishPct}%"></div></div>
                  <div class="a2ui-taste-track"><div class="a2ui-taste-fill a2ui-fill-wine" style="width:${winePct}%"></div></div>
                </div>
              </div>
            `;
          })
          .join("")}
      </div>
    `;
  }

  function renderServingGuide(block) {
    const decantMin = Math.round(num(block.decant_minutes, 0));
    const steps = Array.isArray(block.steps) ? block.steps : [];
    return `
      <div class="a2ui-serving-card">
        <div class="a2ui-section-title">🥂 Serving &amp; Decanting${block.wine_title ? ` — ${esc(block.wine_title)}` : ""}</div>
        <div class="a2ui-serving-pills">
          ${block.serving_temp_c ? `<span class="a2ui-pill a2ui-pill-meta">🌡️ ${esc(block.serving_temp_c)}</span>` : ""}
          <span class="a2ui-pill a2ui-pill-meta">⏳ ${decantMin > 0 ? `Decant ${decantMin} min` : "Ready to pour"}</span>
          ${block.glassware ? `<span class="a2ui-pill a2ui-pill-meta">🍷 ${esc(block.glassware)}</span>` : ""}
        </div>
        ${
          steps.length > 0
            ? `<ol class="a2ui-serving-steps">${steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>`
            : ""
        }
      </div>
    `;
  }

  function renderFollowUpChips(block) {
    const chips = Array.isArray(block.chips) ? block.chips : [];
    if (chips.length === 0) return "";
    return `
      <div class="a2ui-chips-row">
        ${chips
          .map(
            (ch) => `
            <button type="button" class="a2ui-chip" data-a2ui-prompt="${esc(ch.prompt)}">
              ✨ ${esc(ch.label)}
            </button>
          `
          )
          .join("")}
      </div>
    `;
  }

  function renderBlock(block) {
    if (!block || typeof block !== "object") return "";
    switch (block.type) {
      case "header":
        return renderHeader(block);
      case "callout":
        return renderCallout(block);
      case "bottle_card":
        return renderBottleCard(block);
      case "comparison_table":
        return renderComparisonTable(block);
      case "taste_match":
        return renderTasteMatch(block);
      case "serving_guide":
        return renderServingGuide(block);
      case "follow_up_chips":
        return renderFollowUpChips(block);
      default:
        return "";
    }
  }

  function renderBlocks(blocks) {
    if (!Array.isArray(blocks) || blocks.length === 0) return "";
    const html = blocks.map(renderBlock).filter(Boolean).join("");
    return html ? `<div class="a2ui-stack">${html}</div>` : "";
  }

  function getStyles() {
    return `
      .a2ui-stack { display: flex; flex-direction: column; gap: 10px; margin-top: 4px; }
      .a2ui-header { display: flex; flex-direction: column; gap: 4px; padding-bottom: 6px; border-bottom: 1px solid var(--border, rgba(128,128,128,0.2)); }
      .a2ui-header-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
      .a2ui-header-title { font-family: var(--font-serif, Georgia, serif); font-size: 16px; font-weight: 700; color: var(--text, inherit); }
      .a2ui-header-sub { font-size: 12.5px; color: var(--text-muted, #6b7280); line-height: 1.4; }
      .a2ui-badge { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; padding: 3px 8px; border-radius: 999px; background: var(--brand-soft, rgba(99,29,52,0.12)); color: var(--champagne, var(--brand, #621322)); border: 1px solid rgba(212,175,55,0.28); }
      .a2ui-callout { padding: 10px 12px; border-radius: 12px; font-size: 13px; line-height: 1.45; border-left: 3px solid var(--brand, #621322); background: var(--surface-alt, rgba(128,128,128,0.08)); }
      .a2ui-callout-warning { border-left-color: #f59e0b; background: rgba(245,158,11,0.1); }
      .a2ui-callout-title { font-weight: 700; margin-bottom: 3px; color: var(--champagne, var(--brand, #621322)); font-size: 12px; text-transform: uppercase; letter-spacing: 0.3px; }
      .a2ui-callout-body { color: var(--text, inherit); }
      .a2ui-bottle-card { border: 1px solid var(--border, rgba(128,128,128,0.22)); border-radius: 14px; padding: 12px; background: var(--surface, rgba(255,255,255,0.04)); display: flex; flex-direction: column; gap: 8px; transition: transform 0.15s ease, border-color 0.15s ease; }
      .a2ui-bottle-card.a2ui-clickable { cursor: pointer; }
      .a2ui-bottle-card.a2ui-clickable:hover { border-color: var(--champagne, var(--brand, #621322)); }
      .a2ui-bottle-top { display: flex; gap: 10px; align-items: flex-start; }
      .a2ui-bottle-thumb { width: 52px; height: 68px; border-radius: 10px; object-fit: cover; flex-shrink: 0; background: var(--surface-alt, rgba(128,128,128,0.12)); }
      .a2ui-bottle-placeholder { display: flex; align-items: center; justify-content: center; font-size: 24px; }
      .a2ui-bottle-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
      .a2ui-bottle-pills { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
      .a2ui-bottle-title { font-family: var(--font-serif, Georgia, serif); font-size: 14.5px; font-weight: 700; color: var(--text, inherit); line-height: 1.25; }
      .a2ui-bottle-meta { display: flex; flex-wrap: wrap; gap: 8px; font-size: 11.5px; color: var(--text-muted, #6b7280); }
      .a2ui-bottle-reason { font-size: 12.5px; line-height: 1.45; color: var(--text, inherit); }
      .a2ui-bottle-footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; padding-top: 6px; border-top: 1px solid var(--border, rgba(128,128,128,0.14)); }
      .a2ui-bottle-service { display: flex; gap: 6px; flex-wrap: wrap; }
      .a2ui-open-link { font-size: 11.5px; font-weight: 700; color: var(--champagne, var(--brand, #621322)); }
      .a2ui-pill { font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 999px; display: inline-flex; align-items: center; gap: 3px; }
      .a2ui-pill-best { background: rgba(16,185,129,0.15); color: #10b981; border: 1px solid rgba(16,185,129,0.3); }
      .a2ui-pill-alt { background: rgba(99,29,52,0.14); color: var(--champagne, var(--brand, #621322)); border: 1px solid rgba(212,175,55,0.25); }
      .a2ui-pill-adv { background: rgba(217,119,6,0.14); color: #f59e0b; border: 1px solid rgba(245,158,11,0.3); }
      .a2ui-pill-ready { background: rgba(16,185,129,0.12); color: #10b981; }
      .a2ui-pill-soon { background: rgba(245,158,11,0.12); color: #f59e0b; }
      .a2ui-pill-keep { background: rgba(2,132,199,0.12); color: #38bdf8; }
      .a2ui-pill-past { background: rgba(239,68,68,0.12); color: #ef4444; }
      .a2ui-pill-meta { background: var(--surface-alt, rgba(128,128,128,0.12)); color: var(--text-muted, #6b7280); border: 1px solid var(--border, rgba(128,128,128,0.18)); }
      .a2ui-section-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; color: var(--champagne, var(--brand, #621322)); margin-bottom: 8px; }
      .a2ui-table-card, .a2ui-taste-card, .a2ui-serving-card { border: 1px solid var(--border, rgba(128,128,128,0.2)); border-radius: 14px; padding: 12px; background: var(--surface-alt, rgba(128,128,128,0.06)); }
      .a2ui-table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
      .a2ui-table { width: 100%; border-collapse: collapse; font-size: 12px; }
      .a2ui-table th, .a2ui-table td { padding: 7px 8px; text-align: left; border-bottom: 1px solid var(--border, rgba(128,128,128,0.15)); vertical-align: top; }
      .a2ui-table thead th { font-weight: 700; color: var(--champagne, var(--brand, #621322)); font-size: 11.5px; }
      .a2ui-table tbody th { font-weight: 600; color: var(--text-muted, #6b7280); white-space: nowrap; }
      .a2ui-taste-legend { display: flex; gap: 12px; font-size: 11px; color: var(--text-muted, #6b7280); margin-bottom: 8px; }
      .a2ui-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
      .a2ui-dot-dish, .a2ui-fill-dish { background: #d4af37; }
      .a2ui-dot-wine, .a2ui-fill-wine { background: var(--brand, #621322); }
      .a2ui-taste-row { margin-bottom: 8px; }
      .a2ui-taste-row:last-child { margin-bottom: 0; }
      .a2ui-taste-head { display: flex; justify-content: space-between; font-size: 11.5px; margin-bottom: 4px; gap: 8px; }
      .a2ui-taste-head span { color: var(--text-muted, #6b7280); font-size: 11px; }
      .a2ui-taste-bars { display: flex; flex-direction: column; gap: 3px; }
      .a2ui-taste-track { height: 5px; border-radius: 999px; background: rgba(128,128,128,0.18); overflow: hidden; }
      .a2ui-taste-fill { height: 100%; border-radius: 999px; }
      .a2ui-serving-pills { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
      .a2ui-serving-steps { margin: 0; padding-left: 18px; font-size: 12.5px; line-height: 1.5; color: var(--text, inherit); }
      .a2ui-chips-row { display: flex; flex-wrap: wrap; gap: 6px; padding-top: 2px; }
      .a2ui-chip { padding: 6px 11px; border-radius: 999px; border: 1px solid rgba(212,175,55,0.35); background: var(--brand-soft, rgba(99,29,52,0.1)); color: var(--champagne, var(--brand, #621322)); font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit; }
      .a2ui-chip:hover { filter: brightness(1.08); }
    `;
  }

  globalScope.HomeCaveSommAIA2UI = {
    renderBlocks: renderBlocks,
    renderBlock: renderBlock,
    getStyles: getStyles,
  };
})(typeof window !== "undefined" ? window : globalThis);
