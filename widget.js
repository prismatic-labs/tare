/**
 * Tare Embeddable Widget
 * https://prismatic-labs.github.io/tare/
 *
 * Usage:
 *   <div data-tare-food="chicken" data-tare-country="GB"></div>
 *   <script src="https://prismatic-labs.github.io/tare/widget.js" async></script>
 *
 * Attributes:
 *   data-tare-food     Food ID (e.g. "chicken", "bread", "salmon")
 *   data-tare-country  ISO country code (default: IT) — supports all countries in foods.json
 *   data-tare-theme    "light" (default) | "dark"
 *
 * The widget is self-contained — CSS is injected inline.
 * Data is fetched once and shared across all instances on a page.
 */
(function () {
  'use strict';

  const BASE     = 'https://prismatic-labs.github.io/tare/';
  const DATA_URL = BASE + 'data/foods.json';

  const SEV_COLOR = { extreme: '#C0392B', high: '#D4680F', moderate: '#B08800', low: '#2A7A3A' };
  const SEV_BG    = { extreme: '#fdf2f1', high: '#fef5ec', moderate: '#fefbe8', low: '#f0f9f1' };

  // ── Shared data promise (fetch once per page) ─────────────────────────────
  let _dataPromise = null;
  function getData() {
    if (!_dataPromise) {
      _dataPromise = fetch(DATA_URL).then(r => {
        if (!r.ok) throw new Error('tare widget: failed to load data');
        return r.json();
      });
    }
    return _dataPromise;
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function countryExposure(food, country) {
    const localFloor = food.local_cost_floor_pct || 45;
    const mult = country.impact_multiplier || 1.0;
    const adjustedCap = Math.min(95, Math.round(100 - localFloor / mult));
    const raw = Math.min(adjustedCap, Math.round(food.crisis_exposure_pct * mult));
    if (country.data_confidence === 'low')    return Math.min(adjustedCap, Math.round(raw / 10) * 10) || 10;
    if (country.data_confidence === 'medium') return Math.min(adjustedCap, Math.round(raw / 5)  * 5)  || 5;
    return raw;
  }
  function severityFromPct(pct) {
    if (pct >= 60) return 'extreme';
    if (pct >= 40) return 'high';
    if (pct >= 20) return 'moderate';
    return 'low';
  }
  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── CSS (injected once) ───────────────────────────────────────────────────
  let _cssInjected = false;
  function injectCSS() {
    if (_cssInjected) return;
    _cssInjected = true;
    const style = document.createElement('style');
    style.textContent = `
      .tare-widget {
        display: inline-block;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
        border-radius: 10px;
        border: 1px solid #ddd6c8;
        overflow: hidden;
        max-width: 240px;
        width: 100%;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08);
        text-decoration: none;
        color: inherit;
        vertical-align: top;
      }
      .tare-widget[data-tare-theme="dark"] { border-color: rgba(255,255,255,0.15); }
      .tare-widget-inner { padding: 0.85rem 1rem; }
      .tare-widget-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.6rem; }
      .tare-widget-emoji { font-size: 1.5rem; line-height: 1; }
      .tare-widget-name { font-size: 0.88rem; font-weight: 700; }
      .tare-widget-cat  { font-size: 0.7rem; color: #888; margin-top: 1px; }
      .tare-widget-badge {
        display: inline-block;
        padding: 1px 7px;
        border-radius: 20px;
        font-size: 0.65rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #fff;
        margin-bottom: 0.5rem;
      }
      .tare-widget-pct-row { display: flex; align-items: baseline; gap: 0.4rem; margin-bottom: 0.4rem; }
      .tare-widget-pct { font-size: 1.8rem; font-weight: 900; line-height: 1; }
      .tare-widget-pct-label { font-size: 0.7rem; color: #888; line-height: 1.3; }
      .tare-widget-bar { height: 5px; background: #e8e3da; border-radius: 3px; overflow: hidden; margin-bottom: 0.55rem; }
      .tare-widget-bar-fill { height: 100%; border-radius: 3px; }
      .tare-widget-drivers { font-size: 0.68rem; color: #666; line-height: 1.5; }
      .tare-widget-footer {
        padding: 0.4rem 1rem;
        font-size: 0.62rem;
        text-align: right;
        border-top: 1px solid #eee;
        background: rgba(0,0,0,0.02);
      }
      .tare-widget-footer a { color: #2C3E2D; text-decoration: none; font-weight: 600; }
      .tare-widget-footer a:hover { text-decoration: underline; }
      .tare-widget-error {
        padding: 0.85rem 1rem;
        font-size: 0.75rem;
        color: #888;
        font-family: -apple-system, sans-serif;
      }
      .tare-widget[data-tare-theme="dark"] .tare-widget-inner { background: #2C3E2D; color: #F5F0E8; }
      .tare-widget[data-tare-theme="dark"] .tare-widget-bar { background: rgba(255,255,255,0.12); }
      .tare-widget[data-tare-theme="dark"] .tare-widget-footer { background: rgba(0,0,0,0.2); border-color: rgba(255,255,255,0.1); }
      .tare-widget[data-tare-theme="dark"] .tare-widget-footer a { color: #c8e6c9; }
      .tare-widget[data-tare-theme="dark"] .tare-widget-cat,
      .tare-widget[data-tare-theme="dark"] .tare-widget-pct-label,
      .tare-widget[data-tare-theme="dark"] .tare-widget-drivers { color: rgba(245,240,232,0.55); }
    `;
    document.head.appendChild(style);
  }

  // ── Render one element ────────────────────────────────────────────────────
  function renderWidget(el, data) {
    const foodId  = el.getAttribute('data-tare-food');
    const cc      = (el.getAttribute('data-tare-country') || 'IT').toUpperCase();
    const theme   = el.getAttribute('data-tare-theme') || 'light';

    const food    = data.foods.find(f => f.id === foodId);
    const country = data.countries.find(c => c.code === cc) || data.countries[0];

    if (!food) {
      el.innerHTML = `<div class="tare-widget-error">Food "${esc(foodId)}" not found. <a href="${BASE}" target="_blank" rel="noopener">Browse all</a></div>`;
      el.classList.add('tare-widget');
      return;
    }

    const pct   = countryExposure(food, country);
    const sev   = severityFromPct(pct);
    const color = SEV_COLOR[sev];
    const bg    = theme === 'dark' ? 'transparent' : SEV_BG[sev];
    const topDrivers = food.drivers.slice(0, 3).map(d => `${d.input} +${d.price_change_pct}%`).join(' · ');

    el.setAttribute('data-tare-theme', theme);
    el.classList.add('tare-widget');
    el.innerHTML = `
      <div class="tare-widget-inner" style="background:${bg}">
        <div class="tare-widget-header">
          <span class="tare-widget-emoji">${food.emoji}</span>
          <div>
            <div class="tare-widget-name">${esc(food.name)}</div>
            <div class="tare-widget-cat">${esc(food.category)} · ${esc(country.name)}</div>
          </div>
        </div>
        <span class="tare-widget-badge" style="background:${color}">${sev}</span>
        <div class="tare-widget-pct-row">
          <span class="tare-widget-pct" style="color:${color}">${pct}%</span>
          <span class="tare-widget-pct-label">crisis<br>exposure</span>
        </div>
        <div class="tare-widget-bar">
          <div class="tare-widget-bar-fill" style="width:${pct}%;background:${color}"></div>
        </div>
        <div class="tare-widget-drivers">${esc(topDrivers)}</div>
      </div>
      <div class="tare-widget-footer">
        <a href="${BASE}#${esc(foodId)}" target="_blank" rel="noopener">Full breakdown → tare</a>
      </div>
    `;
  }

  // ── Main: find all host elements and populate ─────────────────────────────
  function run() {
    const elements = document.querySelectorAll('[data-tare-food]');
    if (!elements.length) return;

    injectCSS();

    getData().then(data => {
      elements.forEach(el => renderWidget(el, data));
    }).catch(err => {
      console.warn(err);
      elements.forEach(el => {
        el.classList.add('tare-widget');
        el.innerHTML = '<div class="tare-widget-error">Failed to load Tare data.</div>';
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
})();
