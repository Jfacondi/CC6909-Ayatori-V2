// Ayatori frontend — Leaflet + fetch + sliders sobre CSAConfig

const map = L.map("map").setView([-33.45, -70.66], 13);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap',
}).addTo(map);

let originMarker = null;
let destMarker = null;
let journeyLayers = []; // capas dibujadas; arrays paralelos a results

// Paleta Okabe–Ito (segura para daltonismo) por modo de transporte.
const MODE_STYLE = {
  bus:       { icon: "🚌", color: "#0072B2", label: "Bus" },
  metro:     { icon: "Ⓜ",  color: "#D55E00", label: "Metro" },
  rail:      { icon: "🚆", color: "#009E73", label: "Tren" },
  tram:      { icon: "🚊", color: "#CC79A7", label: "Tranvía" },
  ferry:     { icon: "⛴", color: "#56B4E9", label: "Ferry" },
  cable:     { icon: "🚠", color: "#E69F00", label: "Cable" },
  gondola:   { icon: "🚡", color: "#F0E442", label: "Góndola" },
  funicular: { icon: "🚞", color: "#999999", label: "Funicular" },
};
const WALK_COLOR = "#E69F00";
const TRANSFER_COLOR = "#CC79A7";

function modeStyle(mode) {
  return MODE_STYLE[mode] || MODE_STYLE.bus;
}
function routeLabel(s) {
  return s.route_short_name || s.route_id || "?";
}

// Colores por grupo en modo comparar (no por modo).
const PALETTE = [
  "#0072B2", "#D55E00", "#009E73", "#CC79A7",
  "#56B4E9", "#E69F00", "#F0E442", "#999999",
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
  if (!str) return null;
  const [lat, lon] = str.split(",").map((s) => parseFloat(s.trim()));
  if (Number.isFinite(lat) && Number.isFinite(lon)) return [lat, lon];
  return null;
}

// Coordenadas efectivas de un input de origen/destino: prioriza dataset
// (lo que setean los markers y el geocoder) y cae a parsear el texto como
// "lat, lon" si el dataset está vacío.
function inputCoords(inputEl) {
  const dLat = parseFloat(inputEl.dataset.lat);
  const dLon = parseFloat(inputEl.dataset.lon);
  if (Number.isFinite(dLat) && Number.isFinite(dLon)) return [dLat, dLon];
  return parseLatLon(inputEl.value);
}

function setInputCoords(inputEl, lat, lon, label) {
  inputEl.dataset.lat = String(lat);
  inputEl.dataset.lon = String(lon);
  if (label !== undefined) inputEl.value = label;
}

function _transitColor(s, routeColors) {
  if (routeColors && routeColors[s.route_id]) return routeColors[s.route_id];
  return modeStyle(s.mode).color;
}

function fmtSeg(s, routeColors) {
  const min = (s.duration_seconds / 60).toFixed(0);
  if (s.type === "walk") {
    const d = (s.distance_km ?? 0) * 1000;
    return `<span class="seg-walk">🚶 ${d.toFixed(0)} m · ${min} min</span>`;
  }
  if (s.type === "transit") {
    const st = modeStyle(s.mode);
    const c = _transitColor(s, routeColors);
    const hops = s._hops_count && s._hops_count > 1 ? ` · ${s._hops_count} paradas` : "";
    return `<span class="seg-transit" style="color:${c}">${st.icon} ${routeLabel(s)} · ${min} min${hops}</span>`;
  }
  if (s.type === "transfer") {
    const w = s.distance_km ? ` ${(s.distance_km * 1000).toFixed(0)} m` : "";
    return `<span class="seg-transfer">↔ transbordo${w}</span>`;
  }
  return s.type;
}

// Texto humano de un segmento para la línea de tiempo.
function segTitle(s, routeColors) {
  if (s.type === "walk") {
    const d = ((s.distance_km ?? 0) * 1000).toFixed(0);
    const a = s.from === "origin" ? "origen" : (s.from_stop_name || s.from || "parada");
    const b = s.to === "destination" ? "destino" : (s.to_stop_name || s.to || "parada");
    return `🚶 Caminar ${d} m · ${a} → ${b}`;
  }
  if (s.type === "transit") {
    const st = modeStyle(s.mode);
    const c = _transitColor(s, routeColors);
    const name = [routeLabel(s), s.route_long_name].filter(Boolean).join(" · ");
    const ag = s.agency_name ? ` (${s.agency_name})` : "";
    const fr = s.from_stop_name || s.from_stop || "?";
    const to = s.to_stop_name || s.to_stop || "?";
    const hops = s._hops_count && s._hops_count > 1
      ? ` <span class="seg-hops">(${s._hops_count} paradas)</span>` : "";
    return `<span style="color:${c}">${st.icon} ${st.label} ${name}</span>${ag}${hops}` +
           `<br><span class="tl-time">${fr} → ${to}</span>`;
  }
  if (s.type === "transfer") {
    if (s.from_stop_name && s.to_stop_name && s.from_stop !== s.to_stop) {
      const d = s.distance_km ? ` (${(s.distance_km * 1000).toFixed(0)} m a pie)` : "";
      return `↔ Transbordo: ${s.from_stop_name} → ${s.to_stop_name}${d}`;
    }
    const at = s.at_stop_name || s.at_stop || "?";
    return `↔ Transbordo en ${at}`;
  }
  return s.type;
}

// ──────────────────────────────────────────────────────────────────────
// Markers en el mapa
// ──────────────────────────────────────────────────────────────────────

function setOrigin(lat, lon, label) {
  if (originMarker) map.removeLayer(originMarker);
  originMarker = L.marker([lat, lon], { draggable: true, title: "Origen" }).addTo(map);
  originMarker.bindTooltip("Origen", { permanent: true, direction: "top" });
  originMarker.on("drag", () => {
    const ll = originMarker.getLatLng();
    setInputCoords($("origin"), ll.lat, ll.lng, `${ll.lat.toFixed(5)}, ${ll.lng.toFixed(5)}`);
  });
  const text = label ?? `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  setInputCoords($("origin"), lat, lon, text);
}

function setDestination(lat, lon, label) {
  if (destMarker) map.removeLayer(destMarker);
  destMarker = L.marker([lat, lon], { draggable: true, title: "Destino" }).addTo(map);
  destMarker.bindTooltip("Destino", { permanent: true, direction: "top" });
  destMarker.on("drag", () => {
    const ll = destMarker.getLatLng();
    setInputCoords($("destination"), ll.lat, ll.lng, `${ll.lat.toFixed(5)}, ${ll.lng.toFixed(5)}`);
  });
  const text = label ?? `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  setInputCoords($("destination"), lat, lon, text);
}

map.on("click", (e) => setOrigin(e.latlng.lat, e.latlng.lng));
map.on("contextmenu", (e) => {
  e.originalEvent.preventDefault();
  setDestination(e.latlng.lat, e.latlng.lng);
});

// Inicializar markers con valores por defecto del form
{
  const o = inputCoords($("origin"));
  const d = inputCoords($("destination"));
  if (o) setOrigin(o[0], o[1]);
  if (d) setDestination(d[0], d[1]);
}

// ──────────────────────────────────────────────────────────────────────
// Geocoder: dropdown con resultados de Nominatim (vía /geocode)
// ──────────────────────────────────────────────────────────────────────

const GEOCODE_DEBOUNCE_MS = 400;
const _geocodeTimers = {}; // por inputId

function closeAllDropdowns() {
  document.querySelectorAll(".geo-dropdown").forEach((d) => {
    d.classList.remove("open");
    d.innerHTML = "";
  });
}

function renderDropdown(dropdown, results, onPick) {
  dropdown.innerHTML = "";
  if (!results.length) {
    dropdown.innerHTML = `<div class="geo-empty">Sin resultados</div>`;
    dropdown.classList.add("open");
    return;
  }
  results.forEach((r) => {
    const el = document.createElement("div");
    el.className = "geo-option";
    const type = r.type ? `<span class="geo-type">${r.type}</span>` : "";
    el.innerHTML = `${r.display_name}${type}`;
    el.addEventListener("mousedown", (ev) => {
      // mousedown (no click) para que dispare antes del blur del input
      ev.preventDefault();
      onPick(r);
      closeAllDropdowns();
    });
    dropdown.appendChild(el);
  });
  dropdown.classList.add("open");
}

async function geocodeQuery(q) {
  const url = `/geocode?q=${encodeURIComponent(q)}&limit=5`;
  const r = await fetch(url);
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

function bindGeocodeInput(inputId, dropdownId, setterFn) {
  const input = $(inputId);
  const dropdown = $(dropdownId);

  // Invalidar dataset si el usuario edita manualmente: lo que escriba ya no
  // refleja las coords guardadas, hasta que (a) reparse como lat/lon, o (b)
  // elija una opción del dropdown.
  input.addEventListener("input", () => {
    const q = input.value.trim();
    // Si parsea como lat/lon, lo aceptamos directo sin geocodificar.
    const ll = parseLatLon(q);
    if (ll) {
      setterFn(ll[0], ll[1]);
      closeAllDropdowns();
      return;
    }
    // No invalidar dataset todavía: hasta que llegue el resultado, el
    // botón "Planificar" sigue usando las coords previas válidas si las hay.
    clearTimeout(_geocodeTimers[inputId]);
    if (q.length < 3) {
      dropdown.classList.remove("open");
      dropdown.innerHTML = "";
      input.classList.remove("geo-loading");
      return;
    }
    _geocodeTimers[inputId] = setTimeout(async () => {
      input.classList.add("geo-loading");
      try {
        const results = await geocodeQuery(q);
        renderDropdown(dropdown, results, (r) => {
          setterFn(r.lat, r.lon, r.display_name);
          map.setView([r.lat, r.lon], 15);
        });
      } catch (e) {
        dropdown.innerHTML = `<div class="geo-empty">Error: ${e.message}</div>`;
        dropdown.classList.add("open");
      } finally {
        input.classList.remove("geo-loading");
      }
    }, GEOCODE_DEBOUNCE_MS);
  });

  input.addEventListener("focus", () => {
    if (dropdown.children.length) dropdown.classList.add("open");
  });
  input.addEventListener("blur", () => {
    // Pequeño delay para que el mousedown de la opción dispare primero.
    setTimeout(() => dropdown.classList.remove("open"), 150);
  });
}

bindGeocodeInput("origin", "origin-dropdown", setOrigin);
bindGeocodeInput("destination", "destination-dropdown", setDestination);

document.addEventListener("click", (e) => {
  if (!e.target.closest(".geo-field")) closeAllDropdowns();
});

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
// Consolidación: el CSA emite un segmento `transit` por cada par de
// paradas consecutivas. Cuando una ruta (ej: bus 407) cubre N paradas sin
// transbordo, eso son N entradas idénticas en el timeline. Aquí
// agrupamos los hops consecutivos del mismo route_id en un único segmento
// con `to_stop` del último hop y las paradas intermedias listadas aparte.
// No se mergea a través de `transfer` (cualquier transfer rompe la racha).
// ──────────────────────────────────────────────────────────────────────

function _concatPath(acc, next) {
  if (!Array.isArray(next) || !next.length) return acc;
  if (!acc.length) return next.slice();
  const last = acc[acc.length - 1];
  const first = next[0];
  // Evita duplicar el punto de unión cuando el shape del hop i termina
  // exactamente donde empieza el del hop i+1.
  const skipFirst = last && first &&
                    Math.abs(last[0] - first[0]) < 1e-9 &&
                    Math.abs(last[1] - first[1]) < 1e-9;
  return acc.concat(skipFirst ? next.slice(1) : next);
}

function consolidateTransitSegments(segments) {
  const out = [];
  for (const s of segments) {
    const last = out[out.length - 1];
    if (
      s.type === "transit" &&
      last &&
      last.type === "transit" &&
      last.route_id === s.route_id
    ) {
      // Merge: extender el último segmento hasta este hop.
      const merged = { ...last };
      merged.to_stop = s.to_stop;
      merged.to_stop_name = s.to_stop_name ?? s.to_stop;
      merged.to_coords = s.to_coords ?? merged.to_coords;
      merged.to_latlon = s.to_latlon ?? merged.to_latlon;
      merged.arrival_time = s.arrival_time ?? merged.arrival_time;
      merged.end_time = s.end_time ?? merged.end_time;
      merged.duration_seconds = (last.duration_seconds || 0) + (s.duration_seconds || 0);
      merged.path = _concatPath(last.path || [], s.path || []);
      // Primer merge: _hops_meta aún no existe, así que sembramos con los
      // dos extremos del segmento previo (from y to). Mergeos posteriores
      // ya tienen _hops_meta acumulado y sólo agregan el nuevo to_stop.
      const hopFrom = last._hops_meta ?? [
        { stop_id: last.from_stop, stop_name: last.from_stop_name ?? last.from_stop },
        { stop_id: last.to_stop,   stop_name: last.to_stop_name   ?? last.to_stop },
      ];
      merged._hops_meta = hopFrom.concat([{
        stop_id: s.to_stop,
        stop_name: s.to_stop_name ?? s.to_stop,
      }]);
      // Número de paradas en las que el vehículo se detuvo desde que subí
      // (incluye la de bajada, no la de subida).
      merged._hops_count = merged._hops_meta.length - 1;
      out[out.length - 1] = merged;
    } else {
      out.push({ ...s });
    }
  }
  return out;
}

// ──────────────────────────────────────────────────────────────────────
// Colores por route_id: si el feed GTFS trae `route_color` (ej: Metro
// L1=rojo, L5=verde), respetarlo. Para route_ids sin color o que
// colisionen con otro ya asignado, caer a la paleta cíclica Okabe-Ito.
// ──────────────────────────────────────────────────────────────────────

function _normHex(c) {
  if (!c || typeof c !== "string") return null;
  const t = c.trim().replace(/^#/, "");
  if (!/^[0-9a-fA-F]{6}$/.test(t)) return null;
  return "#" + t.toUpperCase();
}

function assignRouteColors(segments) {
  const colors = {};
  const used = new Set();
  let palIdx = 0;
  const transitSegs = segments.filter((s) => s.type === "transit" && s.route_id);
  // Primera pasada: respetar route_color GTFS si es válido y único.
  for (const s of transitSegs) {
    if (colors[s.route_id]) continue;
    const c = _normHex(s.route_color);
    if (c && !used.has(c)) {
      colors[s.route_id] = c;
      used.add(c);
    }
  }
  // Segunda pasada: para las rutas sin color asignado, paleta cíclica
  // evitando colisiones con los colores GTFS ya tomados.
  for (const s of transitSegs) {
    if (colors[s.route_id]) continue;
    let c;
    let attempts = 0;
    do {
      c = PALETTE[palIdx % PALETTE.length].toUpperCase();
      palIdx++;
      attempts++;
    } while (used.has(c) && attempts <= PALETTE.length);
    colors[s.route_id] = c;
    used.add(c);
  }
  return colors;
}

// ──────────────────────────────────────────────────────────────────────
// Dibujar viajes
// ──────────────────────────────────────────────────────────────────────

function clearJourneys() {
  journeyLayers.forEach((g) => map.removeLayer(g));
  journeyLayers = [];
  activeJourneyIdx = null;
}

function styleForSegment(s) {
  const type = typeof s === "string" ? s : s.type;
  if (type === "walk")    return { color: WALK_COLOR, weight: 4, dashArray: "6 6", opacity: 1 };
  if (type === "transit") return { color: modeStyle(s.mode).color, weight: 5, opacity: 1 };
  if (type === "transfer")return { color: TRANSFER_COLOR, weight: 4, dashArray: "2 6", opacity: 1 };
  return { color: "#ffffff", weight: 3, opacity: 1 };
}

function pointsForSegment(s, origin, destination) {
  // path real: shape GTFS (transit) o ruta peatonal OSM (walk)
  if (Array.isArray(s.path) && s.path.length >= 2) {
    return s.path;
  }
  const from = s.from_latlon || s.from_coords ||
               (s.from === "origin" ? origin : null);
  const to   = s.to_latlon   || s.to_coords   ||
               (s.to === "destination" ? destination : null);
  return [from, to].filter(Boolean);
}

function fmtTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch { return ""; }
}

function popupHtmlForSegment(s, routeColors) {
  const dur = (s.duration_seconds / 60).toFixed(1);
  if (s.type === "walk") {
    const d = ((s.distance_km ?? 0) * 1000).toFixed(0);
    return `<strong>🚶 Caminata</strong><br>${d} m · ${dur} min`;
  }
  if (s.type === "transit") {
    const dep = fmtTime(s.departure_time);
    const arr = fmtTime(s.arrival_time);
    const sched = dep && arr ? `${dep} → ${arr}` : `${dur} min`;
    const st = modeStyle(s.mode);
    const c = _transitColor(s, routeColors);
    const name = [routeLabel(s), s.route_long_name].filter(Boolean).join(" · ");
    const ag = s.agency_name ? `<br><small>${s.agency_name}</small>` : "";
    const fr = s.from_stop_name || s.from_stop;
    const to = s.to_stop_name || s.to_stop;
    // Lista de paradas intermedias (sin contar from y to) cuando se mergearon hops.
    let intermediates = "";
    if (s._hops_meta && s._hops_meta.length > 2) {
      const inner = s._hops_meta.slice(1, -1);
      const li = inner.map((h) => `<li>${h.stop_name || h.stop_id}</li>`).join("");
      intermediates =
        `<details style="margin-top:4px"><summary style="cursor:pointer;font-size:11px">` +
        `${s._hops_count} paradas — ver intermedias` +
        `</summary><ol style="font-size:11px;margin:4px 0 0;padding-left:18px">${li}</ol></details>`;
    }
    return `<strong style="color:${c}">${st.icon} ${st.label} ${name}</strong>${ag}<br>
            ${fr} → ${to}<br>
            ${sched} (${dur} min)${intermediates}`;
  }
  if (s.type === "transfer") {
    const at = s.at_stop_name || s.at_stop;
    return `<strong>↔ Transbordo</strong><br>
            ${modeStyle(s.from_mode).label} → ${modeStyle(s.to_mode).label}<br>
            en ${at}`;
  }
  return "";
}

function addTransitMarkers(s, color, group) {
  if (s.from_coords) {
    const board = L.circleMarker(s.from_coords, {
      radius: 6, color: "#0f172a", weight: 2,
      fillColor: color, fillOpacity: 1, opacity: 1,
    });
    board._baseStyle = { radius: 6, fillColor: color };
    const fr = s.from_stop_name || s.from_stop;
    board.bindPopup(`<strong>↑ Subir</strong><br>${modeStyle(s.mode).icon} ${routeLabel(s)}<br>${fr}<br>${fmtTime(s.departure_time)}`);
    board.addTo(group);
  }
  if (s.to_coords) {
    const alight = L.circleMarker(s.to_coords, {
      radius: 6, color: "#0f172a", weight: 2,
      fillColor: "#ffffff", fillOpacity: 1, opacity: 1,
    });
    alight._baseStyle = { radius: 6, fillColor: "#ffffff" };
    const to = s.to_stop_name || s.to_stop;
    alight.bindPopup(`<strong>↓ Bajar</strong><br>${modeStyle(s.mode).icon} ${routeLabel(s)}<br>${to}<br>${fmtTime(s.arrival_time)}`);
    alight.addTo(group);
  }
}

function addTransferMarker(s, group) {
  if (!s.from_coords) return;
  const m = L.circleMarker(s.from_coords, {
    radius: 5, color: "#0f172a", weight: 2,
    fillColor: TRANSFER_COLOR, fillOpacity: 1, opacity: 1,
  });
  m._baseStyle = { radius: 5, fillColor: TRANSFER_COLOR };
  const at = s.at_stop_name || s.at_stop;
  m.bindPopup(`<strong>↔ Transbordo</strong><br>${modeStyle(s.from_mode).label} → ${modeStyle(s.to_mode).label}<br>en ${at}`);
  m.addTo(group);
}

function drawJourney(journey, origin, destination, groupColor, useRouteColors, routeColors) {
  const group = L.layerGroup();
  // `journey.segments` ya viene consolidado (ver renderResults).
  for (const s of journey.segments) {
    const pts = pointsForSegment(s, origin, destination);
    let segColor;
    if (s.type === "transit") {
      // En single-mode (useRouteColors=true) priorizamos color por route_id
      // para diferenciar buses distintos del mismo modo. En compare-mode
      // (false) todos los segs transit van con el color del grupo.
      segColor = useRouteColors ? _transitColor(s, routeColors) : groupColor;
    } else if (s.type === "walk") {
      segColor = WALK_COLOR;
    } else if (s.type === "transfer") {
      segColor = TRANSFER_COLOR;
    } else {
      segColor = groupColor;
    }
    if (pts.length >= 2) {
      const base = styleForSegment(s);
      const style = { ...base, color: segColor };
      const line = L.polyline(pts, style);
      line._baseStyle = { ...style };
      line.bindPopup(popupHtmlForSegment(s, routeColors));
      line.addTo(group);
    }
    if (s.type === "transit") addTransitMarkers(s, segColor, group);
    if (s.type === "transfer") addTransferMarker(s, group);
  }
  return group;
}

// ──────────────────────────────────────────────────────────────────────
// Highlight selectivo del journey activo
// ──────────────────────────────────────────────────────────────────────

const DIM_OPACITY = 0.2;
let activeJourneyIdx = null;

function applyEmphasis(layerGroup, mode) {
  layerGroup.eachLayer((l) => {
    if (!l.setStyle || !l._baseStyle) return;
    const base = l._baseStyle;
    if (mode === "dim") {
      l.setStyle({ opacity: DIM_OPACITY, fillOpacity: DIM_OPACITY });
    } else if (mode === "active") {
      l.setStyle({
        opacity: 1,
        fillOpacity: 1,
        weight: (base.weight ?? 4) + 2,
      });
    } else {
      l.setStyle({
        opacity: 1,
        fillOpacity: 1,
        weight: base.weight ?? 4,
      });
    }
  });
}

function setActiveJourney(idx) {
  activeJourneyIdx = idx;
  journeyLayers.forEach((g, i) => {
    if (idx === null) applyEmphasis(g, "normal");
    else applyEmphasis(g, i === idx ? "active" : "dim");
  });
  if (idx !== null && journeyLayers[idx]) {
    journeyLayers[idx].eachLayer((l) => l.bringToFront && l.bringToFront());
  }
}

// ──────────────────────────────────────────────────────────────────────
// Render resultados
// ──────────────────────────────────────────────────────────────────────

function segStart(s) {
  if (s.type === "transit") return s.departure_time;
  return s.start_time;
}
function segEnd(s) {
  if (s.type === "transit") return s.arrival_time;
  return s.end_time;
}

function buildTimeline(journey, routeColors) {
  const items = [];
  const segs = journey.segments;
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i];
    const st = segStart(s);
    const en = segEnd(s);
    const min = (s.duration_seconds / 60).toFixed(0);
    const tspan = st && en ? `${fmtTime(st)} → ${fmtTime(en)} · ` : "";
    items.push(
      `<li>${segTitle(s, routeColors)}<br><span class="tl-time">${tspan}${min} min</span></li>`
    );
    // espera entre este segmento y el siguiente
    const next = segs[i + 1];
    if (next) {
      const a = segEnd(s);
      const b = segStart(next);
      if (a && b) {
        const w = (new Date(b) - new Date(a)) / 60000;
        if (w >= 1) {
          items.push(
            `<li class="tl-wait">⏱ espera ${w.toFixed(0)} min</li>`
          );
        }
      }
    }
  }
  return `<ul class="timeline">${items.join("")}</ul>`;
}

function renderResults(groups) {
  // groups: [{ label, color, journeys }]
  const root = $("results");
  root.innerHTML = "";
  clearJourneys();

  const total = groups.reduce((n, g) => n + g.journeys.length, 0);
  const onlyWalk =
    total > 0 &&
    groups.every((g) =>
      g.journeys.every((j) =>
        j.segments.every((s) => s.type === "walk")
      )
    );
  if (total === 0 || onlyWalk) {
    const msg =
      total === 0
        ? "Sin viajes para este origen/destino, fecha y hora."
        : "Solo caminata: no hay transporte público útil para esta fecha/hora.";
    root.innerHTML = `<div class="empty-msg">🚶 ${msg}</div>`;
    if (onlyWalk) {
      // igual dibujamos la caminata directa
    } else {
      return;
    }
  }

  const origin = inputCoords($("origin"));
  const destination = inputCoords($("destination"));

  const useRouteColors = groups.length === 1;
  // Pre-procesar: consolidar hops del mismo route_id y asignar colores por route.
  // En compare-mode también consolidamos (mejora la UI) pero ignoramos los colores
  // por route porque ahí mandan los colores de grupo.
  const allRouteColors = {};
  groups.forEach((grp) => {
    grp.journeys = grp.journeys.map((j) => {
      const segs = consolidateTransitSegments(j.segments);
      return { ...j, segments: segs };
    });
    if (useRouteColors) {
      grp.journeys.forEach((j) => {
        const rc = assignRouteColors(j.segments);
        Object.assign(allRouteColors, rc);
        j._routeColors = rc;
      });
    }
  });

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
      const rc = j._routeColors || null;
      const layer = drawJourney(j, origin, destination, color, useRouteColors, rc);
      layer.addTo(map);
      journeyLayers.push(layer);

      const card = document.createElement("div");
      card.className = "result-card";
      card.style.borderLeftColor = color;
      const dur = (j.total_duration_seconds / 60).toFixed(0);
      const walk = (j.total_walking_distance_km * 1000).toFixed(0);
      const summary = j.segments.map((s) => fmtSeg(s, rc)).join(" → ");
      card.innerHTML = `
        <div class="title">${i + 1}. ${dur} min — ${j.number_of_transfers} transbordos</div>
        <div class="meta">${walk} m caminata · llega ${fmtTime(j.arrival_time)}</div>
        <div class="seg-list">${summary}</div>
        ${buildTimeline(j, rc)}
      `;
      const layerIdx = journeyLayers.length - 1;
      card.addEventListener("click", () => {
        const wasActive = card.classList.contains("active");
        document.querySelectorAll(".result-card").forEach((c) => c.classList.remove("active"));
        if (wasActive) {
          setActiveJourney(null);
        } else {
          card.classList.add("active");
          setActiveJourney(layerIdx);
        }
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

  // Leyenda dinámica: en single-mode mostramos las rutas activas (un color
  // por route_id); en compare-mode dejamos la leyenda de modos porque los
  // colores ahí pertenecen a los grupos, no a las rutas.
  if (useRouteColors) {
    const seen = new Map();
    groups.forEach((grp) => grp.journeys.forEach((j) =>
      j.segments.forEach((s) => {
        if (s.type !== "transit" || !s.route_id || seen.has(s.route_id)) return;
        seen.set(s.route_id, {
          route_id: s.route_id,
          label: s.route_short_name || s.route_id,
          long: s.route_long_name || "",
          mode: s.mode,
          color: (j._routeColors && j._routeColors[s.route_id]) || modeStyle(s.mode).color,
        });
      })
    ));
    renderLegend({ routes: [...seen.values()] });
  } else {
    renderLegend();
  }
}

// ──────────────────────────────────────────────────────────────────────
// Llamadas a la API
// ──────────────────────────────────────────────────────────────────────

function getCommonRequestBody() {
  const origin = inputCoords($("origin"));
  const destination = inputCoords($("destination"));
  const depRaw = $("departure").value; // "YYYY-MM-DDTHH:mm"
  if (!origin || !destination || !depRaw) {
    alert(
      "Origen, destino y hora son requeridos. Si escribiste una dirección, " +
      "elige una de las opciones del menú."
    );
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
// Selector de modos + leyenda
// ──────────────────────────────────────────────────────────────────────

let availableModes = ["bus", "metro", "rail", "tram"];

function renderModes() {
  const meta = configSchema && configSchema.allowed_modes;
  if (meta && Array.isArray(meta.options)) availableModes = meta.options;
  const root = $("modes");
  root.innerHTML = "";
  for (const m of availableModes) {
    const st = modeStyle(m);
    const wrap = document.createElement("label");
    wrap.className = "mode-chip";
    wrap.innerHTML = `<input type="checkbox" data-mode="${m}" checked />
      <span>${st.icon} ${st.label}</span>`;
    root.appendChild(wrap);
  }
  root.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", () => {
      const checked = [...root.querySelectorAll("input:checked")].map(
        (i) => i.dataset.mode
      );
      root.querySelectorAll(".mode-chip").forEach((c) => {
        const on = c.querySelector("input").checked;
        c.classList.toggle("off", !on);
      });
      // Todos marcados → sin filtro; si no, allowed_modes = marcados.
      if (checked.length === availableModes.length || checked.length === 0) {
        delete singleOverrides.allowed_modes;
      } else {
        singleOverrides.allowed_modes = checked;
      }
    });
  });
}

function renderLegend(opts) {
  const el = $("legend");
  const walkRow =
    `<div class="lg-row"><span class="sw" style="background:${WALK_COLOR};height:0;border-top:3px dashed ${WALK_COLOR}"></span>🚶 Caminata</div>`;
  const transferRow =
    `<div class="lg-row"><span class="sw" style="background:${TRANSFER_COLOR};height:0;border-top:3px dashed ${TRANSFER_COLOR}"></span>↔ Transbordo</div>`;

  if (opts && Array.isArray(opts.routes) && opts.routes.length) {
    // Leyenda específica del viaje: una entrada por route_id usado.
    const rows = opts.routes.map((r) => {
      const st = modeStyle(r.mode);
      const long = r.long ? ` <span style="color:var(--muted);font-size:10px">${r.long}</span>` : "";
      return `<div class="lg-row"><span class="sw" style="background:${r.color}"></span>${st.icon} <strong>${r.label}</strong>${long}</div>`;
    }).join("");
    el.innerHTML = `<h3>Rutas usadas</h3>${rows}${walkRow}${transferRow}`;
  } else {
    const rows = availableModes
      .map((m) => {
        const st = modeStyle(m);
        return `<div class="lg-row"><span class="sw" style="background:${st.color}"></span>${st.icon} ${st.label}</div>`;
      })
      .join("");
    el.innerHTML = `<h3>Leyenda</h3>${rows}${walkRow}${transferRow}`;
  }
  el.style.display = "";
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
    renderModes();
    renderLegend();
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
  renderModes();
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
