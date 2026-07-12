// Coordenadas efectivas de los inputs de origen/destino.
// La fuente de verdad son los data-lat/data-lon del input (los setean los
// markers y el geocoder); se cae a parsear el texto "lat, lon" si no hay dataset.

import { $ } from "./dom.js";

export function parseLatLon(str) {
  if (!str) return null;
  const [lat, lon] = str.split(",").map((s) => parseFloat(s.trim()));
  if (Number.isFinite(lat) && Number.isFinite(lon)) return [lat, lon];
  return null;
}

export function inputCoords(inputEl) {
  const dLat = parseFloat(inputEl.dataset.lat);
  const dLon = parseFloat(inputEl.dataset.lon);
  if (Number.isFinite(dLat) && Number.isFinite(dLon)) return [dLat, dLon];
  return parseLatLon(inputEl.value);
}

export function setInputCoords(inputEl, lat, lon, label) {
  inputEl.dataset.lat = String(lat);
  inputEl.dataset.lon = String(lon);
  if (label !== undefined) inputEl.value = label;
}
