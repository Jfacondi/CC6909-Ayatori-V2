/* global L */
// Mapa Leaflet: marcadores origen/destino, dibujo de viajes, highlight,
// leyenda, overlay de carga. Leaflet se carga como script global antes que
// este módulo (ver index.html).

import {
  modeStyle, modeSvg, routeLabel, fmtTime, fmtSecsOfDay, altOptions,
  _transitColor, WALK_COLOR, TRANSFER_COLOR,
} from "./format.js";
import { setInputCoords } from "./coords.js";
import { state } from "./state.js";
import { $, svgIcon } from "./dom.js";

export const map = L.map("map", { zoomControl: true }).setView([-33.45, -70.66], 12);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap",
}).addTo(map);

let originMarker = null;
let destMarker = null;
let journeyLayers = []; // arrays paralelos a los results dibujados
let activeJourneyIdx = null;
const DIM_OPACITY = 0.2;

// ── Marcadores origen/destino ───────────────────────────────────────────

function pinIcon(letter, bg) {
  const html =
    `<div style="width:26px;height:26px;border-radius:50% 50% 50% 0;` +
    `transform:rotate(-45deg);background:${bg};border:2px solid #fff;` +
    `box-shadow:0 2px 6px rgba(0,0,0,.45);display:grid;place-items:center">` +
    `<span style="transform:rotate(45deg);color:#fff;font-weight:700;font-size:13px">${letter}</span></div>`;
  return L.divIcon({ className: "", html, iconSize: [30, 30], iconAnchor: [15, 28], popupAnchor: [0, -26] });
}

function maybeHideHint() {
  const o = $("origin");
  const d = $("destination");
  const ready =
    o && d && Number.isFinite(parseFloat(o.dataset.lat)) && Number.isFinite(parseFloat(d.dataset.lat));
  const hint = $("map-hint");
  if (hint && ready) hint.style.display = "none";
}

export function setOrigin(lat, lon, label) {
  if (originMarker) map.removeLayer(originMarker);
  originMarker = L.marker([lat, lon], {
    draggable: true,
    icon: pinIcon("A", "#22c55e"),
    title: "Origen",
  }).addTo(map);
  originMarker.bindTooltip("Origen", { direction: "top", offset: [0, -24] });
  originMarker.on("drag", () => {
    const ll = originMarker.getLatLng();
    setInputCoords($("origin"), ll.lat, ll.lng, `${ll.lat.toFixed(5)}, ${ll.lng.toFixed(5)}`);
  });
  setInputCoords($("origin"), lat, lon, label ?? `${lat.toFixed(5)}, ${lon.toFixed(5)}`);
  maybeHideHint();
}

export function setDestination(lat, lon, label) {
  if (destMarker) map.removeLayer(destMarker);
  destMarker = L.marker([lat, lon], {
    draggable: true,
    icon: pinIcon("B", "#ef4444"),
    title: "Destino",
  }).addTo(map);
  destMarker.bindTooltip("Destino", { direction: "top", offset: [0, -24] });
  destMarker.on("drag", () => {
    const ll = destMarker.getLatLng();
    setInputCoords($("destination"), ll.lat, ll.lng, `${ll.lat.toFixed(5)}, ${ll.lng.toFixed(5)}`);
  });
  setInputCoords($("destination"), lat, lon, label ?? `${lat.toFixed(5)}, ${lon.toFixed(5)}`);
  maybeHideHint();
}

export function panTo(lat, lon, zoom = 15) {
  map.setView([lat, lon], zoom);
}

// Click izquierdo = origen; click derecho / long-press (táctil) = destino.
map.on("click", (e) => setOrigin(e.latlng.lat, e.latlng.lng));
map.on("contextmenu", (e) => {
  if (e.originalEvent) e.originalEvent.preventDefault();
  setDestination(e.latlng.lat, e.latlng.lng);
});

// Long-press en táctil para fijar el destino (no hay click derecho en móvil).
{
  const container = map.getContainer();
  let lpTimer = null;
  let lpStart = null;
  container.addEventListener(
    "touchstart",
    (ev) => {
      if (ev.touches.length !== 1) return;
      const t = ev.touches[0];
      lpStart = { x: t.clientX, y: t.clientY, ev: t };
      lpTimer = setTimeout(() => {
        const ll = map.mouseEventToLatLng(lpStart.ev);
        setDestination(ll.lat, ll.lng);
        lpTimer = null;
      }, 550);
    },
    { passive: true }
  );
  const cancel = () => {
    if (lpTimer) {
      clearTimeout(lpTimer);
      lpTimer = null;
    }
  };
  container.addEventListener(
    "touchmove",
    (ev) => {
      if (!lpStart || !ev.touches.length) return;
      const t = ev.touches[0];
      if (Math.abs(t.clientX - lpStart.x) > 12 || Math.abs(t.clientY - lpStart.y) > 12) cancel();
    },
    { passive: true }
  );
  container.addEventListener("touchend", cancel);
  container.addEventListener("touchcancel", cancel);
}

// ── Dibujo de viajes ────────────────────────────────────────────────────

export function clearJourneys() {
  journeyLayers.forEach((g) => map.removeLayer(g));
  journeyLayers = [];
  activeJourneyIdx = null;
}

function styleForSegment(s) {
  const type = typeof s === "string" ? s : s.type;
  if (type === "walk") return { color: WALK_COLOR, weight: 4, dashArray: "6 6", opacity: 1 };
  if (type === "transit") return { color: modeStyle(s.mode).color, weight: 5, opacity: 1 };
  if (type === "transfer") return { color: TRANSFER_COLOR, weight: 4, dashArray: "2 6", opacity: 1 };
  return { color: "#ffffff", weight: 3, opacity: 1 };
}

function pointsForSegment(s, origin, destination) {
  if (Array.isArray(s.path) && s.path.length >= 2) return s.path;
  const from = s.from_latlon || s.from_coords || (s.from === "origin" ? origin : null);
  const to = s.to_latlon || s.to_coords || (s.to === "destination" ? destination : null);
  return [from, to].filter(Boolean);
}

function popupHtmlForSegment(s, routeColors) {
  const dur = (s.duration_seconds / 60).toFixed(1);
  if (s.type === "walk") {
    const d = ((s.distance_km ?? 0) * 1000).toFixed(0);
    return `<strong>${svgIcon("i-walk")} Caminata</strong><br>${d} m · ${dur} min`;
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
    let intermediates = "";
    if (s._hops_meta && s._hops_meta.length > 2) {
      const inner = s._hops_meta.slice(1, -1);
      const li = inner.map((h) => `<li>${h.stop_name || h.stop_id}</li>`).join("");
      intermediates =
        `<details style="margin-top:4px"><summary style="cursor:pointer;font-size:11px">` +
        `${s._hops_count} paradas — ver intermedias</summary>` +
        `<ol style="font-size:11px;margin:4px 0 0;padding-left:18px">${li}</ol></details>`;
    }
    // Líneas comunes: otras líneas que cubren este mismo tramo (mismos
    // paraderos). Cada una con su próxima salida estimada desde la subida.
    let alternatives = "";
    const opts = altOptions(s);
    if (opts.length) {
      const li = opts
        .map((o) => {
          const nm = o.route_short_name || o.route_id;
          const nd =
            o.next_departure_secs != null
              ? ` <span style="color:var(--text-muted)">~${fmtSecsOfDay(o.next_departure_secs)}</span>`
              : "";
          return `<li>${modeSvg(o.mode)} ${nm}${nd}</li>`;
        })
        .join("");
      alternatives =
        `<details style="margin-top:4px" open><summary style="cursor:pointer;font-size:11px">` +
        `Otras líneas en este tramo (${opts.length})</summary>` +
        `<ul style="font-size:11px;margin:4px 0 0;padding-left:18px;list-style:none">${li}</ul></details>`;
    }
    return (
      `<strong style="color:${c}">${modeSvg(s.mode)} ${st.label} ${name}</strong>${ag}<br>` +
      `${fr} → ${to}<br>${sched} (${dur} min)${intermediates}${alternatives}`
    );
  }
  if (s.type === "transfer") {
    const at = s.at_stop_name || s.at_stop;
    return (
      `<strong>${svgIcon("i-transfer")} Transbordo</strong><br>` +
      `${modeStyle(s.from_mode).label} → ${modeStyle(s.to_mode).label}<br>en ${at}`
    );
  }
  return "";
}

function addTransitMarkers(s, color, group) {
  if (s.from_coords) {
    const board = L.circleMarker(s.from_coords, {
      radius: 6, color: "#0f172a", weight: 2, fillColor: color, fillOpacity: 1, opacity: 1,
    });
    board._baseStyle = { radius: 6, fillColor: color };
    const fr = s.from_stop_name || s.from_stop;
    board.bindPopup(`<strong>↑ Subir</strong><br>${modeSvg(s.mode)} ${routeLabel(s)}<br>${fr}<br>${fmtTime(s.departure_time)}`);
    board.addTo(group);
  }
  if (s.to_coords) {
    const alight = L.circleMarker(s.to_coords, {
      radius: 6, color: "#0f172a", weight: 2, fillColor: "#ffffff", fillOpacity: 1, opacity: 1,
    });
    alight._baseStyle = { radius: 6, fillColor: "#ffffff" };
    const to = s.to_stop_name || s.to_stop;
    alight.bindPopup(`<strong>↓ Bajar</strong><br>${modeSvg(s.mode)} ${routeLabel(s)}<br>${to}<br>${fmtTime(s.arrival_time)}`);
    alight.addTo(group);
  }
}

function addTransferMarker(s, group) {
  if (!s.from_coords) return;
  const m = L.circleMarker(s.from_coords, {
    radius: 5, color: "#0f172a", weight: 2, fillColor: TRANSFER_COLOR, fillOpacity: 1, opacity: 1,
  });
  m._baseStyle = { radius: 5, fillColor: TRANSFER_COLOR };
  const at = s.at_stop_name || s.at_stop;
  m.bindPopup(`<strong>${svgIcon("i-transfer")} Transbordo</strong><br>${modeStyle(s.from_mode).label} → ${modeStyle(s.to_mode).label}<br>en ${at}`);
  m.addTo(group);
}

function drawJourney(journey, origin, destination, groupColor, useRouteColors, routeColors) {
  const group = L.layerGroup();
  for (const s of journey.segments) {
    const pts = pointsForSegment(s, origin, destination);
    let segColor;
    if (s.type === "transit") {
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

/** Dibuja un journey y devuelve su índice de capa. */
export function addJourneyLayer(journey, origin, destination, groupColor, useRouteColors, routeColors) {
  const layer = drawJourney(journey, origin, destination, groupColor, useRouteColors, routeColors);
  layer.addTo(map);
  journeyLayers.push(layer);
  return journeyLayers.length - 1;
}

export function fitToJourneys() {
  if (!journeyLayers.length) return;
  const fg = L.featureGroup(journeyLayers.flatMap((g) => (g.getLayers ? g.getLayers() : [])));
  if (fg.getLayers().length) map.fitBounds(fg.getBounds(), { padding: [40, 40] });
}

// ── Highlight selectivo del journey activo ──────────────────────────────

function applyEmphasis(layerGroup, mode) {
  layerGroup.eachLayer((l) => {
    if (!l.setStyle || !l._baseStyle) return;
    const base = l._baseStyle;
    if (mode === "dim") {
      l.setStyle({ opacity: DIM_OPACITY, fillOpacity: DIM_OPACITY });
    } else if (mode === "active") {
      l.setStyle({ opacity: 1, fillOpacity: 1, weight: (base.weight ?? 4) + 2 });
    } else {
      l.setStyle({ opacity: 1, fillOpacity: 1, weight: base.weight ?? 4 });
    }
  });
}

export function setActiveJourney(idx) {
  activeJourneyIdx = idx;
  journeyLayers.forEach((g, i) => {
    if (idx === null) applyEmphasis(g, "normal");
    else applyEmphasis(g, i === idx ? "active" : "dim");
  });
  if (idx !== null && journeyLayers[idx]) {
    journeyLayers[idx].eachLayer((l) => l.bringToFront && l.bringToFront());
  }
}

// ── Leyenda ─────────────────────────────────────────────────────────────

export function renderLegend(opts) {
  const el = $("legend");
  if (!el) return;
  const walkRow = `<div class="lg-row"><span class="sw" style="background:${WALK_COLOR};height:0;border-top:3px dashed ${WALK_COLOR}"></span>${svgIcon("i-walk")} Caminata</div>`;
  const transferRow = `<div class="lg-row"><span class="sw" style="background:${TRANSFER_COLOR};height:0;border-top:3px dashed ${TRANSFER_COLOR}"></span>${svgIcon("i-transfer")} Transbordo</div>`;

  if (opts && Array.isArray(opts.routes) && opts.routes.length) {
    const rows = opts.routes
      .map((r) => {
        const long = r.long
          ? ` <span style="color:var(--text-muted);font-size:10px">${r.long}</span>`
          : "";
        return `<div class="lg-row"><span class="sw" style="background:${r.color}"></span>${modeSvg(r.mode)} <strong>${r.label}</strong>${long}</div>`;
      })
      .join("");
    el.innerHTML = `<h3>Rutas usadas</h3>${rows}${walkRow}${transferRow}`;
  } else {
    const rows = state.availableModes
      .map((m) => {
        const st = modeStyle(m);
        return `<div class="lg-row"><span class="sw" style="background:${st.color}"></span>${modeSvg(m)} ${st.label}</div>`;
      })
      .join("");
    el.innerHTML = `<h3>Leyenda</h3>${rows}${walkRow}${transferRow}`;
  }
  el.classList.add("show");
}

// ── Overlay de carga ────────────────────────────────────────────────────

export function showLoading() {
  $("map-overlay")?.classList.add("show");
}
export function hideLoading() {
  $("map-overlay")?.classList.remove("show");
}
