// Bottom sheet arrastrable para móvil (patrón Citymapper/Google Maps).
// En escritorio el grip está oculto y estas interacciones no aplican.

import { $ } from "../dom.js";

const STATES = ["peek", "half", "full"];
// Altura por estado, en sync con las reglas [data-state] de layout.css.
const STATE_HEIGHTS = { peek: "168px", half: "52vh", full: "92dvh" };
// Publica la altura del sheet a CSS para que la leyenda del mapa la siga
// (la usa `.legend { bottom: calc(var(--sheet-height) + 12px) }`).
const publishHeight = (h) => document.documentElement.style.setProperty("--sheet-height", h);

export const isMobileSheet = () => matchMedia("(max-width: 767px)").matches;

let panel = null;
let backBtn = null;
let idx = 1; // half
let returnState = null; // estado a restaurar con el botón "Resultados"

function apply() {
  idx = Math.max(0, Math.min(STATES.length - 1, idx));
  panel.dataset.state = STATES[idx];
  panel.style.removeProperty("height"); // las reglas por data-state controlan
  publishHeight(STATE_HEIGHTS[STATES[idx]]);
  // El botón "volver a resultados" solo aplica con el mapa a la vista (peek).
  if (backBtn && STATES[idx] !== "peek") backBtn.classList.remove("show");
}

export function setSheetState(name) {
  const i = STATES.indexOf(name);
  if (i >= 0) {
    idx = i;
    apply();
  }
}
export function getSheetState() {
  return STATES[idx];
}
export function hideMapBack() {
  if (backBtn) backBtn.classList.remove("show");
}

// Baja el panel para revelar el mapa (al elegir una ruta) y muestra un botón
// para volver al estado en que estaba.
export function revealMapForResult() {
  if (!isMobileSheet() || !panel) return;
  if (STATES[idx] !== "peek") returnState = STATES[idx];
  setSheetState("peek");
  if (backBtn) backBtn.classList.add("show");
}

export function initSheet() {
  panel = $("panel");
  const grip = $("sheet-grip");
  backBtn = $("btn-back-results");
  if (!panel || !grip) return;

  if (backBtn) backBtn.addEventListener("click", () => setSheetState(returnState || "half"));

  if (isMobileSheet()) setSheetState("half");

  // Tap en el grip: cicla half → full → peek → …
  const cycle = () => {
    idx = idx >= STATES.length - 1 ? 0 : idx + 1;
    apply();
  };
  grip.addEventListener("click", (e) => {
    if (dragging) {
      e.stopImmediatePropagation();
      return;
    }
    cycle();
  });
  grip.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      cycle();
    }
  });

  // Arrastre vertical.
  let startY = null;
  let startH = null;
  let dragging = false;

  grip.addEventListener("pointerdown", (e) => {
    if (!isMobileSheet()) return;
    startY = e.clientY;
    startH = panel.getBoundingClientRect().height;
    grip.setPointerCapture?.(e.pointerId);
  });
  // El arrastre solo empieza cuando el puntero se mueve de verdad; así un tap
  // limpio no marca `dragging` y el handler de click puede ciclar el estado.
  grip.addEventListener("pointermove", (e) => {
    if (startY === null) return;
    const dy = startY - e.clientY;
    if (!dragging) {
      if (Math.abs(dy) < 4) return;
      dragging = true;
      panel.style.transition = "none";
    }
    const h = Math.max(120, Math.min(window.innerHeight * 0.92, startH + dy));
    panel.style.height = `${h}px`;
    publishHeight(`${h}px`); // la leyenda sigue al sheet en vivo durante el arrastre
  });
  const end = () => {
    if (startY === null) return;
    startY = null;
    if (!dragging) return; // tap puro → deja que el click cicle el estado
    const h = panel.getBoundingClientRect().height;
    const vh = window.innerHeight;
    panel.style.transition = "";
    idx = h < vh * 0.28 ? 0 : h < vh * 0.7 ? 1 : 2;
    apply();
    // Pequeño delay para que el click posterior no vuelva a ciclar.
    setTimeout(() => (dragging = false), 0);
  };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
}
