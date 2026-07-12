// Render de resultados: tarjetas con ribbon de modos, badges automáticos,
// timeline colapsable y tabla comparativa. Orquesta el dibujo en el mapa.

import {
  consolidateTransitSegments, assignRouteColors, modeStyle, modeSvg, routeLabel,
  fmtTime, segTitle, _transitColor, altNames,
} from "./format.js";
import {
  clearJourneys, addJourneyLayer, setActiveJourney, fitToJourneys, renderLegend,
} from "./map.js";
import { inputCoords } from "./coords.js";
import { $, svgIcon } from "./dom.js";

// ── Helpers de presentación ─────────────────────────────────────────────

function segStart(s) {
  return s.type === "transit" ? s.departure_time : s.start_time;
}
function segEnd(s) {
  return s.type === "transit" ? s.arrival_time : s.end_time;
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
    items.push(`<li>${segTitle(s, routeColors)}<br><span class="tl-time">${tspan}${min} min</span></li>`);
    const next = segs[i + 1];
    if (next) {
      const a = segEnd(s);
      const b = segStart(next);
      if (a && b) {
        const w = (new Date(b) - new Date(a)) / 60000;
        if (w >= 1) items.push(`<li class="tl-wait">⏱ espera ${w.toFixed(0)} min</li>`);
      }
    }
  }
  return `<ul class="timeline">${items.join("")}</ul>`;
}

// Máximo de líneas visibles dentro de un mismo chip del ribbon antes de
// resumir el resto como "+N" (la lista completa va en el tooltip y el paso a paso).
const RIBBON_MAX_LINES = 3;

function buildRibbon(journey, routeColors) {
  const segs = journey.segments.filter((s) => s.type !== "transfer");
  const pills = segs.map((s) => {
    if (s.type === "walk") {
      const min = Math.max(1, Math.round(s.duration_seconds / 60));
      return `<span class="ribbon__seg ribbon__seg--walk">${svgIcon("i-walk")} ${min}'</span>`;
    }
    const c = _transitColor(s, routeColors);
    // Línea elegida primero, luego las que sirven el mismo tramo (líneas comunes).
    const all = [routeLabel(s), ...altNames(s)];
    const shown = all.slice(0, RIBBON_MAX_LINES);
    const extra = all.length - shown.length;
    let inner = shown.join(`<span class="ribbon__sep">/</span>`);
    if (extra > 0) inner += `<span class="ribbon__more">+${extra}</span>`;
    const title =
      all.length > 1 ? ` title="Líneas que sirven este tramo: ${all.join(", ")}"` : "";
    return `<span class="ribbon__seg"${title} style="background:${c}">${modeSvg(s.mode)} ${inner}</span>`;
  });
  return pills.join(`<span class="ribbon__arrow">${svgIcon("i-arrow")}</span>`);
}

// Conjunto de journeys que alcanzan el mínimo de una métrica.
function winnersBy(journeys, fn) {
  const min = Math.min(...journeys.map(fn));
  return new Set(journeys.filter((j) => fn(j) === min));
}

function badgesHtml(journey, w) {
  const b = [];
  if (w.fast.has(journey)) b.push(`<span class="badge badge--fast">${svgIcon("i-bolt")} Más rápido</span>`);
  if (w.transfers.has(journey)) b.push(`<span class="badge badge--transfers">${svgIcon("i-route")} Menos transbordos</span>`);
  if (w.walk.has(journey)) b.push(`<span class="badge badge--walk">${svgIcon("i-walk")} Menos caminata</span>`);
  return b.length ? `<div class="result-card__badges">${b.join("")}</div>` : "";
}

// ── Tabla comparativa (modo comparar) ───────────────────────────────────

function compareTable(groups) {
  const rows = groups
    .map((g) => {
      if (!g.journeys.length) return null;
      const best = g.journeys.reduce((a, b) => (b.total_duration_seconds < a.total_duration_seconds ? b : a));
      return { label: g.label, color: g.color, n: g.journeys.length, best };
    })
    .filter(Boolean);
  if (rows.length < 2) return "";

  const minDur = Math.min(...rows.map((r) => r.best.total_duration_seconds));
  const minTr = Math.min(...rows.map((r) => r.best.number_of_transfers));
  const minWalk = Math.min(...rows.map((r) => r.best.total_walking_distance_km));

  const body = rows
    .map((r) => {
      const dur = Math.round(r.best.total_duration_seconds / 60);
      const walk = Math.round(r.best.total_walking_distance_km * 1000);
      const cd = r.best.total_duration_seconds === minDur ? "best" : "";
      const ct = r.best.number_of_transfers === minTr ? "best" : "";
      const cw = r.best.total_walking_distance_km === minWalk ? "best" : "";
      return `<tr>
        <td><span class="swatch" style="background:${r.color}"></span>${r.label}</td>
        <td class="${cd}">${dur} min</td>
        <td class="${ct}">${r.best.number_of_transfers}</td>
        <td class="${cw}">${walk} m</td>
        <td>${fmtTime(r.best.arrival_time)}</td>
      </tr>`;
    })
    .join("");

  return `<table class="compare-table">
    <thead><tr><th>Variante</th><th>Mejor t.</th><th>Transb.</th><th>Caminata</th><th>Llega</th></tr></thead>
    <tbody>${body}</tbody></table>`;
}

// ── Render principal ────────────────────────────────────────────────────

/** @param {{label:string,color:string,journeys:object[]}[]} groups */
export function renderGroups(groups) {
  const root = $("results");
  root.innerHTML = "";
  clearJourneys();

  const total = groups.reduce((n, g) => n + g.journeys.length, 0);
  const onlyWalk =
    total > 0 &&
    groups.every((g) => g.journeys.every((j) => j.segments.every((s) => s.type === "walk")));

  toggleActions(total > 0);

  if (total === 0 || onlyWalk) {
    const msg =
      total === 0
        ? "Sin viajes para este origen/destino, fecha y hora."
        : "Solo caminata: no hay transporte público útil para esta fecha/hora.";
    root.innerHTML = `<div class="empty-msg">${svgIcon("i-walk")} <div>${msg}</div></div>`;
    if (!onlyWalk) return;
  }

  const origin = inputCoords($("origin"));
  const destination = inputCoords($("destination"));
  const useRouteColors = groups.length === 1;

  // Consolidar hops y asignar colores por ruta.
  groups.forEach((grp) => {
    grp.journeys = grp.journeys.map((j) => ({ ...j, segments: consolidateTransitSegments(j.segments) }));
    if (useRouteColors) {
      grp.journeys.forEach((j) => {
        j._routeColors = assignRouteColors(j.segments);
      });
    }
  });

  // Tabla comparativa arriba (solo en modo comparar).
  if (groups.length > 1) {
    const tbl = compareTable(groups);
    if (tbl) {
      const wrap = document.createElement("div");
      wrap.className = "card";
      wrap.style.padding = "var(--space-2)";
      wrap.innerHTML = tbl;
      root.appendChild(wrap);
    }
  }

  groups.forEach((grp) => {
    if (groups.length > 1) {
      const h = document.createElement("h3");
      h.className = "section__title";
      h.style.borderLeft = `4px solid ${grp.color}`;
      h.style.paddingLeft = "8px";
      h.style.marginTop = "var(--space-2)";
      h.textContent = grp.label;
      root.appendChild(h);
    }

    const showBadges = useRouteColors && grp.journeys.length > 1;
    const winners = showBadges
      ? {
          fast: winnersBy(grp.journeys, (j) => j.total_duration_seconds),
          transfers: winnersBy(grp.journeys, (j) => j.number_of_transfers),
          walk: winnersBy(grp.journeys, (j) => j.total_walking_distance_km),
        }
      : null;

    grp.journeys.forEach((j) => {
      const color = grp.color;
      const rc = j._routeColors || null;
      const layerIdx = addJourneyLayer(j, origin, destination, color, useRouteColors, rc);

      const dur = (j.total_duration_seconds / 60).toFixed(0);
      const walk = (j.total_walking_distance_km * 1000).toFixed(0);

      const card = document.createElement("div");
      card.className = "result-card";
      card.style.borderLeftColor = color;
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.innerHTML = `
        <div class="result-card__top">
          <div class="result-card__dur">${dur} <small>min</small></div>
          <div class="result-card__clock">${fmtTime(j.departure_time)} → ${fmtTime(j.arrival_time)}</div>
        </div>
        ${winners ? badgesHtml(j, winners) : ""}
        <div class="ribbon">${buildRibbon(j, rc)}</div>
        <div class="result-card__meta">
          <span>${svgIcon("i-route")} ${j.number_of_transfers} transbordos</span>
          <span>${svgIcon("i-walk")} ${walk} m a pie</span>
        </div>
        <details class="result-card__details">
          <summary>Ver paso a paso ${svgIcon("i-chevron", "icon chevron")}</summary>
          ${buildTimeline(j, rc)}
        </details>`;

      // El toggle de detalles no debe disparar el highlight del journey.
      card.querySelector(".result-card__details summary")
        .addEventListener("click", (e) => e.stopPropagation());

      const activate = () => {
        const wasActive = card.classList.contains("active");
        document.querySelectorAll(".result-card").forEach((c) => c.classList.remove("active"));
        if (wasActive) {
          setActiveJourney(null);
        } else {
          card.classList.add("active");
          setActiveJourney(layerIdx);
        }
      };
      card.addEventListener("click", activate);
      card.addEventListener("keydown", (e) => {
        // Solo cuando el foco está en la tarjeta misma (no en el <details> interno).
        if (e.target !== card) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          activate();
        }
      });
      root.appendChild(card);
    });
  });

  fitToJourneys();

  // Leyenda: rutas usadas (single) o leyenda de modos (comparar).
  if (useRouteColors) {
    const seen = new Map();
    groups.forEach((grp) =>
      grp.journeys.forEach((j) =>
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
      )
    );
    renderLegend({ routes: [...seen.values()] });
  } else {
    renderLegend();
  }
}

function toggleActions(show) {
  const share = $("btn-share");
  const print = $("btn-print");
  if (share) share.hidden = !show;
  if (print) print.hidden = !show;
}
