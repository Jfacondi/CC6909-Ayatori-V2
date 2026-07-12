// Punto de entrada: arranca módulos, carga estado del servidor y cablea eventos.

import { api } from "./api.js";
import { state } from "./state.js";
import { $ } from "./dom.js";
import { inputCoords } from "./coords.js";
import { PALETTE } from "./format.js";
import { setOrigin, setDestination, panTo, showLoading, hideLoading } from "./map.js";
import { initGeocoders } from "./geocoder.js";
import { renderSliders, renderProfiles, renderModes, resetSingleConfig } from "./config.js";
import { renderVariants, addVariant } from "./compare.js";
import { renderGroups } from "./results.js";
import { initTheme } from "./ui/theme.js";
import { initSheet } from "./ui/sheet.js";
import { toast } from "./ui/toast.js";
import { writePermalink, applyPermalink } from "./permalink.js";

// ── Fecha de salida ─────────────────────────────────────────────────────

const pad = (n) => String(n).padStart(2, "0");
const toLocalInput = (d) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

const fmtFeed = (iso) =>
  new Date(`${iso}T00:00`).toLocaleDateString("es-CL", { day: "numeric", month: "short", year: "numeric" });

// Aplica el rango del feed (state.feedStart/End) a los límites del selector y al
// aviso. Idempotente: se llama al arrancar y de nuevo cuando /health responde.
function applyFeedRange() {
  const dep = $("departure");
  dep.min = `${state.feedStart}T00:00`;
  dep.max = `${state.feedEnd}T23:59`;
  const note = $("feed-note");
  if (note) note.textContent = `Feed válido: ${fmtFeed(state.feedStart)} – ${fmtFeed(state.feedEnd)}`;
}

function setDeparture(force = false) {
  const dep = $("departure");
  if (!dep.value || force) {
    const now = new Date();
    const start = new Date(`${state.feedStart}T08:00`);
    const end = new Date(`${state.feedEnd}T23:59`);
    dep.value = toLocalInput(now < start || now > end ? start : now);
  }
  applyFeedRange();
}

function departureOutOfRange() {
  const v = $("departure").value;
  if (!v) return false;
  return v < `${state.feedStart}T00:00` || v > `${state.feedEnd}T23:59`;
}

// ── Cuerpo común de la petición ─────────────────────────────────────────

function commonBody() {
  const origin = inputCoords($("origin"));
  const destination = inputCoords($("destination"));
  const depRaw = $("departure").value;
  if (!origin || !destination || !depRaw) {
    toast(
      "Indica origen, destino y hora. Si escribiste una dirección, elige una opción del listado.",
      { type: "warning" }
    );
    return null;
  }
  if (departureOutOfRange()) {
    toast(`La fecha está fuera del feed (${fmtFeed(state.feedStart)} – ${fmtFeed(state.feedEnd)}).`, {
      type: "warning",
    });
  }
  return { origin, destination, departure: `${depRaw}:00` };
}

// ── Planificación ───────────────────────────────────────────────────────

async function runSinglePlan() {
  const body = commonBody();
  if (!body) return;
  body.num_alternatives = parseInt($("num_alternatives").value, 10);
  body.profile = state.profile;
  if (Object.keys(state.singleOverrides).length) body.config = state.singleOverrides;

  const btn = $("btn-plan");
  btn.dataset.loading = "true";
  showLoading();
  try {
    const data = await api.plan(body);
    renderGroups([{ label: "Resultados", color: PALETTE[0], journeys: data.journeys }]);
    writePermalink();
  } catch (e) {
    toast(`Error al planificar: ${e.message}`, { type: "error" });
  } finally {
    btn.dataset.loading = "false";
    hideLoading();
  }
}

async function runComparePlan() {
  const body = commonBody();
  if (!body) return;
  const n = parseInt($("num_alternatives").value, 10);
  body.variants = state.variants.map((v) => {
    const out = { label: v.label, num_alternatives: n, profile: state.profile };
    if (Object.keys(v.overrides).length) out.config = v.overrides;
    return out;
  });

  const btn = $("btn-compare");
  btn.dataset.loading = "true";
  showLoading();
  try {
    const data = await api.compare(body);
    const groups = data.results.map((res, i) => ({
      label: res.label,
      color: PALETTE[i % PALETTE.length],
      journeys: res.journeys,
    }));
    renderGroups(groups);
    writePermalink();
  } catch (e) {
    toast(`Error al comparar: ${e.message}`, { type: "error" });
  } finally {
    btn.dataset.loading = "false";
    hideLoading();
  }
}

// ── Acciones de UI ──────────────────────────────────────────────────────

function setMode(mode) {
  state.mode = mode;
  $("tab-single").setAttribute("aria-selected", String(mode === "single"));
  $("tab-compare").setAttribute("aria-selected", String(mode === "compare"));
  $("single-panel").style.display = mode === "single" ? "" : "none";
  $("compare-panel").style.display = mode === "compare" ? "" : "none";
}

function swapOD() {
  const o = $("origin");
  const d = $("destination");
  const oc = inputCoords(o);
  const dc = inputCoords(d);
  const ol = o.value;
  const dl = d.value;
  if (dc) setOrigin(dc[0], dc[1], dl);
  if (oc) setDestination(oc[0], oc[1], ol);
}

function useMyLocation() {
  if (!navigator.geolocation) {
    toast("Geolocalización no disponible en este navegador.", { type: "warning" });
    return;
  }
  const dismiss = toast("Obteniendo tu ubicación…", { type: "info", timeout: 0 });
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      dismiss();
      setOrigin(pos.coords.latitude, pos.coords.longitude, "Mi ubicación");
      panTo(pos.coords.latitude, pos.coords.longitude, 15);
    },
    (err) => {
      dismiss();
      toast(`No se pudo obtener la ubicación: ${err.message}`, { type: "error" });
    },
    { enableHighAccuracy: true, timeout: 10000 }
  );
}

async function shareTrip() {
  const url = writePermalink();
  try {
    if (navigator.share) {
      await navigator.share({ title: "Ayatori — viaje", url });
    } else {
      await navigator.clipboard.writeText(url);
      toast("Enlace copiado al portapapeles.", { type: "success" });
    }
  } catch {
    toast(`Copia este enlace:<br><small>${url}</small>`, { type: "info", timeout: 9000 });
  }
}

// ── Carga inicial ───────────────────────────────────────────────────────

async function loadState() {
  try {
    const [h, schema] = await Promise.all([api.health(), api.schema()]);
    state.schema = schema;
    // Rango real del feed cargado: reemplaza los defaults de respaldo.
    if (h.feed_start) state.feedStart = h.feed_start;
    if (h.feed_end) state.feedEnd = h.feed_end;
    applyFeedRange();
    const health = $("health");
    health.dataset.status = "ok";
    health.innerHTML =
      `<span class="health__item">${h.num_routes} rutas</span>` +
      `<span class="health__item">${h.num_stops} paradas</span>` +
      `<span class="health__item">${h.num_transfers} transferencias</span>`;
    renderSliders($("sliders"), state.singleOverrides);
    renderModes();
    renderVariants();
  } catch (e) {
    $("health").textContent = "error cargando estado del servidor";
    toast("No se pudo cargar el estado del servidor.", { type: "error" });
    console.error(e);
  }
}

function wireEvents() {
  $("tab-single").addEventListener("click", () => setMode("single"));
  $("tab-compare").addEventListener("click", () => setMode("compare"));
  $("btn-plan").addEventListener("click", runSinglePlan);
  $("btn-compare").addEventListener("click", runComparePlan);
  $("btn-reset").addEventListener("click", resetSingleConfig);
  $("btn-add-variant").addEventListener("click", addVariant);
  $("btn-swap").addEventListener("click", swapOD);
  $("btn-geoloc").addEventListener("click", useMyLocation);
  $("btn-now").addEventListener("click", () => setDeparture(true));
  $("btn-share").addEventListener("click", shareTrip);
  $("btn-print").addEventListener("click", () => window.print());
}

async function boot() {
  initTheme();
  initSheet();
  setDeparture();
  initGeocoders(setOrigin, setDestination);
  wireEvents();

  const { hasOD } = applyPermalink(setOrigin, setDestination);
  renderProfiles();

  await loadState();

  if (hasOD) runSinglePlan();
}

boot();
