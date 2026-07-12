// Permalinks: serializa origen/destino/hora/perfil en la URL para compartir y
// reproducir un viaje. Solo frontend (no toca la API).

import { state } from "./state.js";
import { $ } from "./dom.js";
import { inputCoords, parseLatLon } from "./coords.js";

/** Actualiza la URL con el viaje actual y devuelve el href completo. */
export function writePermalink() {
  const o = inputCoords($("origin"));
  const d = inputCoords($("destination"));
  const t = $("departure").value;
  const params = new URLSearchParams();
  if (o) params.set("o", `${o[0].toFixed(5)},${o[1].toFixed(5)}`);
  if (d) params.set("d", `${d[0].toFixed(5)},${d[1].toFixed(5)}`);
  if (t) params.set("t", t);
  if (state.profile) params.set("p", state.profile);
  history.replaceState(null, "", `${location.pathname}?${params.toString()}`);
  return location.href;
}

/** Lee la URL y aplica O/D/hora/perfil. Devuelve si trae origen y destino. */
export function applyPermalink(setOrigin, setDestination) {
  const p = new URLSearchParams(location.search);
  const o = parseLatLon(p.get("o"));
  const d = parseLatLon(p.get("d"));
  const t = p.get("t");
  const prof = p.get("p");
  if (o) setOrigin(o[0], o[1]);
  if (d) setDestination(d[0], d[1]);
  if (t && $("departure")) $("departure").value = t;
  if (prof) state.profile = prof;
  return { hasOD: !!(o && d) };
}
