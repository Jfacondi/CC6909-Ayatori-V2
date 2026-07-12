// Formato y lógica de presentación de segmentos (puro, sin DOM ni mapa).

import { svgIcon } from "./dom.js";

// Paleta Okabe–Ito (segura para daltonismo) por modo de transporte.
// `color` es hex (lo usan tanto las polylines del mapa como los estilos inline).
export const MODE_STYLE = {
  bus: { sym: "i-bus", color: "#0072B2", label: "Bus" },
  metro: { sym: "i-train", color: "#D55E00", label: "Metro" },
  rail: { sym: "i-train", color: "#009E73", label: "Tren" },
  tram: { sym: "i-tram", color: "#CC79A7", label: "Tranvía" },
  ferry: { sym: "i-ferry", color: "#56B4E9", label: "Ferry" },
  cable: { sym: "i-cable", color: "#E69F00", label: "Cable" },
  gondola: { sym: "i-cable", color: "#F0E442", label: "Góndola" },
  funicular: { sym: "i-train", color: "#999999", label: "Funicular" },
};
export const WALK_COLOR = "#E69F00";
export const TRANSFER_COLOR = "#CC79A7";

// Colores por grupo en modo comparar (no por modo).
export const PALETTE = [
  "#0072B2", "#D55E00", "#009E73", "#CC79A7",
  "#56B4E9", "#E69F00", "#F0E442", "#999999",
];

export function modeStyle(mode) {
  return MODE_STYLE[mode] || MODE_STYLE.bus;
}
export function modeSvg(mode, cls = "icon") {
  return svgIcon(modeStyle(mode).sym, cls);
}
export function routeLabel(s) {
  return s.route_short_name || s.route_id || "?";
}

export function fmtTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

// "HH:MM" desde segundos del día (los devuelve el backend para next_departure_secs).
export function fmtSecsOfDay(secs) {
  if (secs == null) return "";
  const s = ((Math.round(secs) % 86400) + 86400) % 86400;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

// ──────────────────────────────────────────────────────────────────────
// Líneas comunes: otras líneas que cubren el mismo tramo (mismos paraderos de
// subida/bajada) que la elegida. El backend las adjunta como `route_options`
// en cada segmento transit (ver GTFSData.common_lines_for_segment).
// ──────────────────────────────────────────────────────────────────────

/** route_options del segmento, excluyendo la línea ya elegida. */
export function altOptions(s) {
  if (!Array.isArray(s.route_options)) return [];
  return s.route_options.filter((o) => o && o.route_id !== s.route_id);
}
/** Nombres cortos de las líneas alternativas del tramo. */
export function altNames(s) {
  return altOptions(s).map((o) => o.route_short_name || o.route_id);
}

export function _transitColor(s, routeColors) {
  if (routeColors && routeColors[s.route_id]) return routeColors[s.route_id];
  return modeStyle(s.mode).color;
}

// Texto humano de un segmento para la línea de tiempo.
export function segTitle(s, routeColors) {
  if (s.type === "walk") {
    const d = ((s.distance_km ?? 0) * 1000).toFixed(0);
    const a = s.from === "origin" ? "origen" : s.from_stop_name || s.from || "parada";
    const b = s.to === "destination" ? "destino" : s.to_stop_name || s.to || "parada";
    return `${svgIcon("i-walk")} Caminar ${d} m · ${a} → ${b}`;
  }
  if (s.type === "transit") {
    const st = modeStyle(s.mode);
    const c = _transitColor(s, routeColors);
    const name = [routeLabel(s), s.route_long_name].filter(Boolean).join(" · ");
    const ag = s.agency_name ? ` (${s.agency_name})` : "";
    const fr = s.from_stop_name || s.from_stop || "?";
    const to = s.to_stop_name || s.to_stop || "?";
    const hops =
      s._hops_count && s._hops_count > 1
        ? ` <span class="seg-hops">(${s._hops_count} paradas)</span>`
        : "";
    const alts = altNames(s);
    const altLine = alts.length
      ? `<br><span class="seg-alt">También sirven: ${alts.join(" · ")}</span>`
      : "";
    return (
      `<span style="color:${c}">${modeSvg(s.mode)} ${st.label} ${name}</span>${ag}${hops}${altLine}` +
      `<br><span class="tl-time">${fr} → ${to}</span>`
    );
  }
  if (s.type === "transfer") {
    if (s.from_stop_name && s.to_stop_name && s.from_stop !== s.to_stop) {
      const d = s.distance_km ? ` (${(s.distance_km * 1000).toFixed(0)} m a pie)` : "";
      return `${svgIcon("i-transfer")} Transbordo: ${s.from_stop_name} → ${s.to_stop_name}${d}`;
    }
    const at = s.at_stop_name || s.at_stop || "?";
    return `${svgIcon("i-transfer")} Transbordo en ${at}`;
  }
  return s.type;
}

// ──────────────────────────────────────────────────────────────────────
// Consolidación de hops: el CSA emite un segmento `transit` por cada par de
// paradas consecutivas. Agrupamos hops consecutivos del mismo route_id en un
// único segmento. No se mergea a través de `transfer`.
// ──────────────────────────────────────────────────────────────────────

function _concatPath(acc, next) {
  if (!Array.isArray(next) || !next.length) return acc;
  if (!acc.length) return next.slice();
  const last = acc[acc.length - 1];
  const first = next[0];
  const skipFirst =
    last && first && Math.abs(last[0] - first[0]) < 1e-9 && Math.abs(last[1] - first[1]) < 1e-9;
  return acc.concat(skipFirst ? next.slice(1) : next);
}

export function consolidateTransitSegments(segments) {
  const out = [];
  for (const s of segments) {
    const last = out[out.length - 1];
    if (s.type === "transit" && last && last.type === "transit" && last.route_id === s.route_id) {
      const merged = { ...last };
      merged.to_stop = s.to_stop;
      merged.to_stop_name = s.to_stop_name ?? s.to_stop;
      merged.to_coords = s.to_coords ?? merged.to_coords;
      merged.to_latlon = s.to_latlon ?? merged.to_latlon;
      merged.arrival_time = s.arrival_time ?? merged.arrival_time;
      merged.end_time = s.end_time ?? merged.end_time;
      merged.duration_seconds = (last.duration_seconds || 0) + (s.duration_seconds || 0);
      merged.path = _concatPath(last.path || [], s.path || []);
      const hopFrom = last._hops_meta ?? [
        { stop_id: last.from_stop, stop_name: last.from_stop_name ?? last.from_stop },
        { stop_id: last.to_stop, stop_name: last.to_stop_name ?? last.to_stop },
      ];
      merged._hops_meta = hopFrom.concat([
        { stop_id: s.to_stop, stop_name: s.to_stop_name ?? s.to_stop },
      ]);
      merged._hops_count = merged._hops_meta.length - 1;
      out[out.length - 1] = merged;
    } else {
      out.push({ ...s });
    }
  }
  return out;
}

// ──────────────────────────────────────────────────────────────────────
// Colores por route_id: respeta route_color GTFS si es válido y único; cae a
// la paleta cíclica para el resto, evitando colisiones.
// ──────────────────────────────────────────────────────────────────────

function _normHex(c) {
  if (!c || typeof c !== "string") return null;
  const t = c.trim().replace(/^#/, "");
  if (!/^[0-9a-fA-F]{6}$/.test(t)) return null;
  return "#" + t.toUpperCase();
}

export function assignRouteColors(segments) {
  const colors = {};
  const used = new Set();
  let palIdx = 0;
  const transitSegs = segments.filter((s) => s.type === "transit" && s.route_id);
  for (const s of transitSegs) {
    if (colors[s.route_id]) continue;
    const c = _normHex(s.route_color);
    if (c && !used.has(c)) {
      colors[s.route_id] = c;
      used.add(c);
    }
  }
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
