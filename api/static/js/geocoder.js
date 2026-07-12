// Autocompletado de direcciones (Nominatim vía /geocode) con dropdown,
// navegación por teclado (↑/↓/Enter/Esc) y atributos ARIA de combobox.

import { api } from "./api.js";
import { $, svgIcon } from "./dom.js";
import { parseLatLon } from "./coords.js";
import { panTo } from "./map.js";

const DEBOUNCE_MS = 400;
const timers = {};

export function closeAllDropdowns() {
  document.querySelectorAll(".geo-dropdown").forEach((d) => {
    d.classList.remove("open");
    d.innerHTML = "";
    const input = d.previousElementSibling;
    if (input) {
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }
  });
}

function renderDropdown(dropdown, input, results, onPick) {
  dropdown.innerHTML = "";
  input.setAttribute("aria-expanded", "true");
  if (!results.length) {
    dropdown.innerHTML = `<div class="geo-empty">Sin resultados</div>`;
    dropdown.classList.add("open");
    return;
  }
  results.forEach((r, i) => {
    const opt = document.createElement("div");
    opt.className = "geo-option";
    opt.id = `${dropdown.id}-opt-${i}`;
    opt.setAttribute("role", "option");
    const type = r.type ? `<span class="geo-type">${r.type}</span>` : "";
    opt.innerHTML = `${svgIcon("i-pin")}<span>${r.display_name}</span>${type}`;
    opt.addEventListener("mousedown", (ev) => {
      ev.preventDefault(); // antes del blur del input
      onPick(r);
      closeAllDropdowns();
    });
    dropdown.appendChild(opt);
  });
  dropdown.classList.add("open");
}

function moveActive(dropdown, input, delta) {
  const opts = [...dropdown.querySelectorAll(".geo-option")];
  if (!opts.length) return;
  let idx = opts.findIndex((o) => o.classList.contains("active"));
  idx = (idx + delta + opts.length) % opts.length;
  opts.forEach((o) => o.classList.remove("active"));
  const active = opts[idx];
  active.classList.add("active");
  active.scrollIntoView({ block: "nearest" });
  input.setAttribute("aria-activedescendant", active.id);
}

export function bindGeocodeInput(inputId, dropdownId, setterFn) {
  const input = $(inputId);
  const dropdown = $(dropdownId);

  const doPick = (r) => {
    setterFn(r.lat, r.lon, r.display_name);
    panTo(r.lat, r.lon, 15);
  };

  input.addEventListener("input", () => {
    const q = input.value.trim();
    const ll = parseLatLon(q);
    if (ll) {
      setterFn(ll[0], ll[1]);
      closeAllDropdowns();
      return;
    }
    clearTimeout(timers[inputId]);
    if (q.length < 3) {
      dropdown.classList.remove("open");
      dropdown.innerHTML = "";
      input.classList.remove("geo-loading");
      input.setAttribute("aria-expanded", "false");
      return;
    }
    timers[inputId] = setTimeout(async () => {
      input.classList.add("geo-loading");
      try {
        const results = await api.geocode(q, 5);
        renderDropdown(dropdown, input, results, doPick);
      } catch (e) {
        dropdown.innerHTML = `<div class="geo-empty">Error: ${e.message}</div>`;
        dropdown.classList.add("open");
      } finally {
        input.classList.remove("geo-loading");
      }
    }, DEBOUNCE_MS);
  });

  input.addEventListener("keydown", (e) => {
    const open = dropdown.classList.contains("open");
    if (e.key === "ArrowDown") {
      if (open) {
        e.preventDefault();
        moveActive(dropdown, input, 1);
      }
    } else if (e.key === "ArrowUp") {
      if (open) {
        e.preventDefault();
        moveActive(dropdown, input, -1);
      }
    } else if (e.key === "Enter") {
      const active = dropdown.querySelector(".geo-option.active");
      if (active) {
        e.preventDefault();
        active.dispatchEvent(new MouseEvent("mousedown"));
      }
    } else if (e.key === "Escape") {
      closeAllDropdowns();
    }
  });

  input.addEventListener("focus", () => {
    if (dropdown.children.length) {
      dropdown.classList.add("open");
      input.setAttribute("aria-expanded", "true");
    }
  });
  input.addEventListener("blur", () => {
    setTimeout(() => {
      dropdown.classList.remove("open");
      input.setAttribute("aria-expanded", "false");
    }, 150);
  });
}

export function initGeocoders(setOrigin, setDestination) {
  bindGeocodeInput("origin", "origin-dropdown", setOrigin);
  bindGeocodeInput("destination", "destination-dropdown", setDestination);
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".field")) closeAllDropdowns();
  });
}
