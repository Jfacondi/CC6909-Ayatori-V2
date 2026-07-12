// Controles de configuración: sliders avanzados (con etiquetas humanas),
// chips de modos y tarjetas de perfil.

import { state } from "./state.js";
import { $, svgIcon } from "./dom.js";
import { modeStyle } from "./format.js";

// Campos de CSAConfig expuestos como sliders, con etiqueta legible y unidad.
const FIELD_META = {
  max_walking_to_stop_km: { label: "Caminata máx. a la parada", unit: "km" },
  max_walking_transfer_km: { label: "Caminata máx. en transbordo", unit: "km" },
  max_total_walking_km: { label: "Caminata total máx.", unit: "km" },
  max_direct_walk_km: { label: "Caminata directa máx.", unit: "km" },
  walking_speed_kmh: { label: "Velocidad al caminar", unit: "km/h" },
  max_transfers: { label: "Transbordos máx.", unit: "" },
  transfer_buffer_seconds: { label: "Margen de transbordo", unit: "s" },
  transfer_cost_penalty_seconds: { label: "Penalización por transbordo", unit: "s" },
  time_horizon_hours: { label: "Horizonte temporal", unit: "h" },
  max_origin_stops: { label: "Paradas de origen", unit: "" },
  max_destination_stops: { label: "Paradas de destino", unit: "" },
};
const SLIDER_FIELDS = Object.keys(FIELD_META);

const PROFILES = [
  { id: "balanced", name: "Equilibrado", desc: "Buen balance general", sym: "i-balanced" },
  { id: "fastest", name: "Más rápido", desc: "Minimiza el tiempo total", sym: "i-bolt" },
  { id: "fewer_transfers", name: "Menos transbordos", desc: "Evita cambios de vehículo", sym: "i-route" },
  { id: "less_walking", name: "Menos caminata", desc: "Reduce el trecho a pie", sym: "i-walk" },
  { id: "prefer_rail", name: "Preferir riel", desc: "Prioriza metro/tren", sym: "i-train" },
];

const fmtVal = (v, unit) => `${v}${unit ? " " + unit : ""}`;

export function renderSliders(container, overrides) {
  container.innerHTML = "";
  if (!state.schema) return;
  for (const name of SLIDER_FIELDS) {
    const meta = state.schema[name];
    const info = FIELD_META[name];
    if (!meta || !info) continue;
    const value = overrides[name] ?? meta.default;
    const row = document.createElement("div");
    row.className = "slider-field";
    row.innerHTML = `
      <div class="slider-field__head">
        <span class="slider-field__label" title="${name}">${info.label}</span>
        <span class="slider-field__value" data-value="${name}">${fmtVal(value, info.unit)}</span>
      </div>
      <input type="range" data-field="${name}" aria-label="${info.label}"
             min="${meta.min}" max="${meta.max}" step="${meta.step}" value="${value}" />`;
    container.appendChild(row);
  }
  container.querySelectorAll("input[type=range]").forEach((inp) => {
    inp.addEventListener("input", (e) => {
      const f = e.target.dataset.field;
      const v = parseFloat(e.target.value);
      overrides[f] = v;
      container.querySelector(`span[data-value="${f}"]`).textContent = fmtVal(v, FIELD_META[f].unit);
    });
  });
}

export function renderProfiles() {
  const root = $("profiles");
  if (!root) return;
  root.innerHTML = "";
  for (const p of PROFILES) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "profile-card";
    btn.setAttribute("aria-pressed", String(p.id === state.profile));
    btn.dataset.profile = p.id;
    btn.innerHTML = `
      <span class="profile-card__name">${svgIcon(p.sym)} ${p.name}</span>
      <span class="profile-card__desc">${p.desc}</span>`;
    btn.addEventListener("click", () => {
      state.profile = p.id;
      root.querySelectorAll(".profile-card").forEach((c) =>
        c.setAttribute("aria-pressed", String(c.dataset.profile === p.id))
      );
    });
    root.appendChild(btn);
  }
}

export function renderModes() {
  const meta = state.schema && state.schema.allowed_modes;
  if (meta && Array.isArray(meta.options)) state.availableModes = meta.options;
  const root = $("modes");
  if (!root) return;
  root.innerHTML = "";
  for (const m of state.availableModes) {
    const st = modeStyle(m);
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.dataset.mode = m;
    chip.setAttribute("aria-pressed", "true");
    chip.innerHTML = `<span class="chip__dot" style="background:${st.color}"></span>${svgIcon(st.sym)} ${st.label}`;
    chip.addEventListener("click", () => {
      const on = chip.getAttribute("aria-pressed") === "true";
      chip.setAttribute("aria-pressed", String(!on));
      syncModes(root);
    });
    root.appendChild(chip);
  }
}

function syncModes(root) {
  const chips = [...root.querySelectorAll(".chip")];
  const checked = chips.filter((c) => c.getAttribute("aria-pressed") === "true").map((c) => c.dataset.mode);
  if (checked.length === state.availableModes.length || checked.length === 0) {
    delete state.singleOverrides.allowed_modes;
  } else {
    state.singleOverrides.allowed_modes = checked;
  }
}

export function resetSingleConfig() {
  state.singleOverrides = {};
  renderSliders($("sliders"), state.singleOverrides);
  renderModes();
}
