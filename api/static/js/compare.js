// Modo comparar: tarjetas de variantes con overrides propios.

import { state } from "./state.js";
import { $ } from "./dom.js";
import { PALETTE } from "./format.js";
import { renderSliders } from "./config.js";

export function renderVariants() {
  const root = $("variants");
  if (!root) return;
  root.innerHTML = "";
  state.variants.forEach((v, i) => {
    const card = document.createElement("div");
    card.className = "variant-card";
    card.style.borderLeftColor = PALETTE[i % PALETTE.length];
    card.innerHTML = `
      <input class="input" type="text" data-i="${i}" data-k="label" value="${v.label}"
             aria-label="Nombre de la variante" />
      <details class="accordion" style="margin-top:var(--space-2)">
        <summary>Overrides (${Object.keys(v.overrides).length})
          <svg class="icon chevron" style="margin-left:auto"><use href="#i-chevron"></use></svg>
        </summary>
        <div class="accordion__body variant-sliders"></div>
      </details>
      ${
        state.variants.length > 1
          ? `<button class="btn btn--ghost btn--sm" data-i="${i}" data-action="remove" style="margin-top:var(--space-2)">Quitar</button>`
          : ""
      }`;
    root.appendChild(card);
    renderSliders(card.querySelector(".variant-sliders"), v.overrides);
  });

  root.querySelectorAll('input[data-k="label"]').forEach((inp) => {
    inp.addEventListener("input", (e) => {
      state.variants[parseInt(e.target.dataset.i, 10)].label = e.target.value;
    });
  });
  root.querySelectorAll('button[data-action="remove"]').forEach((btn) => {
    btn.addEventListener("click", (e) => {
      state.variants.splice(parseInt(e.currentTarget.dataset.i, 10), 1);
      renderVariants();
    });
  });
}

export function addVariant() {
  const nextLabel = String.fromCharCode(65 + state.variants.length); // A, B, C…
  state.variants.push({ label: nextLabel, overrides: {} });
  renderVariants();
}
