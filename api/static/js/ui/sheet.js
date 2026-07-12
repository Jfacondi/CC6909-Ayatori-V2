// Bottom sheet arrastrable para móvil (patrón Citymapper/Google Maps).
// En escritorio el grip está oculto y estas interacciones no aplican.

import { $ } from "../dom.js";

const STATES = ["peek", "half", "full"];
// Altura por estado, en sync con las reglas [data-state] de layout.css.
const STATE_HEIGHTS = { peek: "168px", half: "52vh", full: "92dvh" };
// Publica la altura del sheet a CSS para que la leyenda del mapa la siga
// (la usa `.legend { bottom: calc(var(--sheet-height) + 12px) }`).
const publishHeight = (h) => document.documentElement.style.setProperty("--sheet-height", h);

export function initSheet() {
  const panel = $("panel");
  const grip = $("sheet-grip");
  if (!panel || !grip) return;

  let idx = 1; // half
  const isMobile = () => matchMedia("(max-width: 767px)").matches;

  const setState = (i) => {
    idx = Math.max(0, Math.min(STATES.length - 1, i));
    panel.dataset.state = STATES[idx];
    panel.style.removeProperty("height"); // las reglas por data-state controlan
    publishHeight(STATE_HEIGHTS[STATES[idx]]);
  };

  if (isMobile()) setState(1);

  // Tap en el grip: cicla half → full → peek → …
  const cycle = () => setState(idx >= STATES.length - 1 ? 0 : idx + 1);
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
    if (!isMobile()) return;
    startY = e.clientY;
    startH = panel.getBoundingClientRect().height;
    dragging = true;
    panel.style.transition = "none";
    grip.setPointerCapture?.(e.pointerId);
  });
  grip.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dy = startY - e.clientY;
    const h = Math.max(120, Math.min(window.innerHeight * 0.92, startH + dy));
    panel.style.height = `${h}px`;
    publishHeight(`${h}px`); // la leyenda sigue al sheet en vivo durante el arrastre
  });
  const end = () => {
    if (!dragging) return;
    const h = panel.getBoundingClientRect().height;
    const vh = window.innerHeight;
    panel.style.transition = "";
    if (h < vh * 0.28) setState(0);
    else if (h < vh * 0.7) setState(1);
    else setState(2);
    // Pequeño delay para que el click posterior no vuelva a ciclar.
    setTimeout(() => (dragging = false), 0);
  };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
}
