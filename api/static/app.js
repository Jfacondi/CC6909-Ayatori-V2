// Ayatori frontend — Leaflet + fetch + sliders sobre CSAConfig

const map = L.map("map").setView([-33.45, -70.66], 13);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap',
}).addTo(map);

let originMarker = null;
let destMarker = null;
let journeyLayers = []; // capas dibujadas; arrays paralelos a results

const PALETTE = [
  "#38bdf8", "#a78bfa", "#fbbf24", "#4ade80",
  "#f87171", "#22d3ee", "#fb923c", "#e879f9",
];

// ──────────────────────────────────────────────────────────────────────
// Estado
// ──────────────────────────────────────────────────────────────────────

let configSchema = null;
let configDefaults = null;
let currentMode = "single"; // "single" | "compare"
let variants = [{ label: "A", overrides: {} }]; // para modo compare

// ──────────────────────────────────────────────────────────────────────
// Helpers DOM
// ──────────────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

function parseLatLon(str) {
  const [lat, lon] = str.split(",").map((s) => parseFloat(s.trim()));
  if (Number.isFinite(lat) && Number.isFinite(lon)) return [lat, lon];
  return null;
}

function fmtSeg(s) {
  if (s.type === "walk") {
    const d = (s.distance_km ?? 0) * 1000;
    return `<span class="seg-walk">🚶 ${d.toFixed(0)}m · ${(s.duration_seconds/60).toFixed(1)}min</span>`;
  }
  if (s.type === "transit") {
    return `<span class="seg-transit">🚌 ${s.route_id} · ${(s.duration_seconds/60).toFixed(1)}min</span>`;
  }
  if (s.type === "transfer") {
    return `<span class="seg-transfer">↔ ${s.from_route}→${s.to_route}</span>`;
  }
  return s.type;
}

// ──────────────────────────────────────────────────────────────────────
// Markers en el mapa
// ──────────────────────────────────────────────────────────────────────

function setOrigin(lat, lon) {
  if (originMarker) map.removeLayer(originMarker);
  originMarker = L.marker([lat, lon], { draggable: true, title: "Origen" }).addTo(map);
  originMarker.bindTooltip("Origen", { permanent: true, direction: "top" });
  originMarker.on("drag", () => {
    const ll = originMarker.getLatLng();
    $("origin").value = `${ll.lat.toFixed(5)}, ${ll.lng.toFixed(5)}`;
  });
  $("origin").value = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

function setDestination(lat, lon) {
  if (destMarker) map.removeLayer(destMarker);
  destMarker = L.marker([lat, lon], { draggable: true, title: "Destino" }).addTo(map);
  destMarker.bindTooltip("Destino", { permanent: true, direction: "top" });
  destMarker.on("drag", () => {
    const ll = destMarker.getLatLng();
    $("destination").value = `${ll.lat.toFixed(5)}, ${ll.lng.toFixed(5)}`;
  });
  $("destination").value = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

map.on("click", (e) => setOrigin(e.latlng.lat, e.latlng.lng));
map.on("contextmenu", (e) => {
  e.originalEvent.preventDefault();
  setDestination(e.latlng.lat, e.latlng.lng);
});

// Inicializar markers con valores por defecto del form
{
  const o = parseLatLon($("origin").value);
  const d = parseLatLon($("destination").value);
  if (o) setOrigin(o[0], o[1]);
  if (d) setDestination(d[0], d[1]);
}

// ──────────────────────────────────────────────────────────────────────
// Sliders para CSAConfig
// ──────────────────────────────────────────────────────────────────────

const SLIDER_FIELDS = [
  "max_walking_to_stop_km",
  "max_walking_transfer_km",
  "max_total_walking_km",
  "max_direct_walk_km",
  "walking_speed_kmh",
  "max_transfers",
  "transfer_buffer_seconds",
  "transfer_cost_penalty_seconds",
  "time_horizon_hours",
  "max_origin_stops",
  "max_destination_stops",
];

function renderSliders(container, currentOverrides) {
  container.innerHTML = "";
  for (const name of SLIDER_FIELDS) {
    const meta = configSchema[name];
    if (!meta) continue;
    const value = currentOverrides[name] ?? meta.default;
    const row = document.createElement("div");
    row.innerHTML = `
      <label title="${name}">${name}</label>
      <div class="slider-row">
        <input type="range" data-field="${name}"
               min="${meta.min}" max="${meta.max}" step="${meta.step}" value="${value}" />
        <span data-value="${name}">${value}</span>
      </div>
    `;
    container.appendChild(row);
  }
  container.querySelectorAll("input[type=range]").forEach((inp) => {
    inp.addEventListener("input", (e) => {
      const f = e.target.dataset.field;
      const v = parseFloat(e.target.value);
      currentOverrides[f] = v;
      container.querySelector(`span[data-value="${f}"]`).textContent = v;
    });
  });
}

let singleOverrides = {};

// ──────────────────────────────────────────────────────────────────────
// Dibujar viajes
// ──────────────────────────────────────────────────────────────────────

function clearJourneys() {
  journeyLayers.forEach((g) => map.removeLayer(g));
  journeyLayers = [];
}

function styleForSegment(type) {
  if (type === "walk")    return { color: "#fbbf24", weight: 4, dashArray: "6 6" };
  if (type === "transit") return { color: "#38bdf8", weight: 5 };
  if (type === "transfer")return { color: "#a78bfa", weight: 4, dashArray: "2 6" };
  return { color: "#ffffff", weight: 3 };
}

function pointsForSegment(s, origin, destination) {
  const from = s.from_latlon || s.from_coords ||
               (s.from === "origin" ? origin : null);
  const to   = s.to_latlon   || s.to_coords   ||
               (s.to === "destination" ? destination : null);
  return [from, to].filter(Boolean);
}

function drawJourney(journey, origin, destination, color) {
  const group = L.layerGroup();
  for (const s of journey.segments) {
    const pts = pointsForSegment(s, origin, destination);
    if (pts.length === 2) {
      const style = styleForSegment(s.type);
      if (s.type === "transit") style.color = color;
      L.polyline(pts, style).addTo(group);
    }
  }
  return group;
}

// ──────────────────────────────────────────────────────────────────────
// Render resultados
// ──────────────────────────────────────────────────────────────────────

function renderResults(groups) {
  // groups: [{ label, color, journeys }]
  const root = $("results");
  root.innerHTML = "";
  clearJourneys();

  const origin = parseLatLon($("origin").value);
  const destination = parseLatLon($("destination").value);

  let idx = 0;
  groups.forEach((grp) => {
    if (groups.length > 1) {
      const h = document.createElement("h2");
      h.style.borderLeft = `4px solid ${grp.color}`;
      h.style.paddingLeft = "6px";
      h.textContent = grp.label;
      root.appendChild(h);
    }
    grp.journeys.forEach((j, i) => {
      const color = grp.color;
      const layer = drawJourney(j, origin, destination, color);
      layer.addTo(map);
      journeyLayers.push(layer);

      const card = document.createElement("div");
      card.className = "result-card";
      card.style.borderLeftColor = color;
      const dur = (j.total_duration_seconds / 60).toFixed(1);
      const walk = (j.total_walking_distance_km * 1000).toFixed(0);
      card.innerHTML = `
        <div class="title">${i + 1}. ${dur} min — ${j.number_of_transfers} transbordos</div>
        <div class="meta">${walk} m caminata · llega ${new Date(j.arrival_time).toLocaleTimeString()}</div>
        <div class="seg-list">${j.segments.map(fmtSeg).join(" → ")}</div>
      `;
      const layerIdx = journeyLayers.length - 1;
      card.addEventListener("click", () => {
        document.querySelectorAll(".result-card").forEach((c) => c.classList.remove("active"));
        card.classList.add("active");
        // bring this layer to front
        journeyLayers[layerIdx].eachLayer((l) => l.bringToFront && l.bringToFront());
      });
      root.appendChild(card);
      idx++;
    });
  });

  // Encuadrar
  if (journeyLayers.length) {
    const fg = L.featureGroup(journeyLayers.flatMap((g) => g.getLayers ? g.getLayers() : []));
    if (fg.getLayers().length) {
      map.fitBounds(fg.getBounds(), { padding: [40, 40] });
    }
  }
}

// ──────────────────────────────────────────────────────────────────────
// Llamadas a la API
// ──────────────────────────────────────────────────────────────────────

function getCommonRequestBody() {
  const origin = parseLatLon($("origin").value);
  const destination = parseLatLon($("destination").value);
  const depRaw = $("departure").value; // "YYYY-MM-DDTHH:mm"
  if (!origin || !destination || !depRaw) {
    alert("Origen, destino y hora son requeridos");
    return null;
  }
  return {
    origin,
    destination,
    departure: depRaw + ":00",
  };
}

async function runSinglePlan() {
  const body = getCommonRequestBody();
  if (!body) return;
  body.num_alternatives = parseInt($("num_alternatives").value, 10);
  body.profile = $("profile").value;
  if (Object.keys(singleOverrides).length) body.config = singleOverrides;

  $("btn-plan").disabled = true;
  $("btn-plan").textContent = "Planificando...";
  try {
    const r = await fetch("/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(JSON.stringify(err.detail || err));
    }
    const data = await r.json();
    renderResults([{ label: "Resultados", color: PALETTE[0], journeys: data.journeys }]);
  } catch (e) {
    alert("Error: " + e.message);
  } finally {
    $("btn-plan").disabled = false;
    $("btn-plan").textContent = "Planificar";
  }
}

async function runComparePlan() {
  const body = getCommonRequestBody();
  if (!body) return;
  body.variants = variants.map((v) => {
    const out = { label: v.label };
    if (Object.keys(v.overrides).length) out.config = v.overrides;
    out.num_alternatives = parseInt($("num_alternatives").value, 10);
    out.profile = $("profile").value;
    return out;
  });

  $("btn-compare").disabled = true;
  $("btn-compare").textContent = "Comparando...";
  try {
    const r = await fetch("/plan/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(JSON.stringify(err.detail || err));
    }
    const data = await r.json();
    const groups = data.results.map((res, i) => ({
      label: res.label,
      color: PALETTE[i % PALETTE.length],
      journeys: res.journeys,
    }));
    renderResults(groups);
  } catch (e) {
    alert("Error: " + e.message);
  } finally {
    $("btn-compare").disabled = false;
    $("btn-compare").textContent = "Comparar";
  }
}

// ──────────────────────────────────────────────────────────────────────
// Modo comparar
// ──────────────────────────────────────────────────────────────────────

function renderVariants() {
  const root = $("variants");
  root.innerHTML = "";
  variants.forEach((v, i) => {
    const card = document.createElement("div");
    card.className = "variant-card";
    card.style.borderLeftColor = PALETTE[i % PALETTE.length];
    card.innerHTML = `
      <input type="text" data-i="${i}" data-k="label" value="${v.label}" />
      <details>
        <summary>Overrides (${Object.keys(v.overrides).length})</summary>
        <div class="variant-sliders"></div>
      </details>
      ${variants.length > 1 ? `<button class="secondary" data-i="${i}" data-action="remove" style="margin-top:6px;font-size:11px;padding:4px;">Quitar</button>` : ""}
    `;
    root.appendChild(card);
    const slidersDiv = card.querySelector(".variant-sliders");
    renderSliders(slidersDiv, v.overrides);
  });
  root.querySelectorAll('input[data-k="label"]').forEach((inp) => {
    inp.addEventListener("input", (e) => {
      const i = parseInt(e.target.dataset.i, 10);
      variants[i].label = e.target.value;
    });
  });
  root.querySelectorAll('button[data-action="remove"]').forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const i = parseInt(e.target.dataset.i, 10);
      variants.splice(i, 1);
      renderVariants();
    });
  });
}

// ──────────────────────────────────────────────────────────────────────
// Bootstrap
// ──────────────────────────────────────────────────────────────────────

async function loadHealthAndConfig() {
  try {
    const [h, schema, defaults] = await Promise.all([
      fetch("/health").then((r) => r.json()),
      fetch("/config/schema").then((r) => r.json()),
      fetch("/config/defaults").then((r) => r.json()),
    ]);
    configSchema = schema;
    configDefaults = defaults;
    $("health").textContent = `${h.num_routes} rutas · ${h.num_stops} paradas · ${h.num_transfers} transferencias`;
    renderSliders($("sliders"), singleOverrides);
    renderVariants();
  } catch (e) {
    $("health").textContent = "error cargando estado";
    console.error(e);
  }
}

$("btn-plan").addEventListener("click", runSinglePlan);
$("btn-compare").addEventListener("click", runComparePlan);

$("btn-reset").addEventListener("click", () => {
  singleOverrides = {};
  renderSliders($("sliders"), singleOverrides);
});

$("btn-add-variant").addEventListener("click", () => {
  const nextLabel = String.fromCharCode(65 + variants.length); // A, B, C...
  variants.push({ label: nextLabel, overrides: {} });
  renderVariants();
});

$("mode-single").addEventListener("click", () => {
  currentMode = "single";
  $("mode-single").classList.add("active"); $("mode-single").classList.remove("inactive");
  $("mode-compare").classList.remove("active"); $("mode-compare").classList.add("inactive");
  $("single-panel").style.display = "";
  $("compare-panel").style.display = "none";
});
$("mode-compare").addEventListener("click", () => {
  currentMode = "compare";
  $("mode-compare").classList.add("active"); $("mode-compare").classList.remove("inactive");
  $("mode-single").classList.remove("active"); $("mode-single").classList.add("inactive");
  $("single-panel").style.display = "none";
  $("compare-panel").style.display = "";
});

loadHealthAndConfig();
