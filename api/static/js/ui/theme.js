// Tema claro/oscuro: sigue prefers-color-scheme, se puede forzar y persiste.

import { $, svgIcon } from "../dom.js";

const KEY = "ayatori-theme";

function effective() {
  const set = document.documentElement.getAttribute("data-theme");
  if (set === "light" || set === "dark") return set;
  return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function apply(theme) {
  if (theme === "light" || theme === "dark") {
    document.documentElement.setAttribute("data-theme", theme);
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  // Mostrar el icono de la acción (a qué tema cambiará al pulsar).
  const goingTo = effective() === "light" ? "i-moon" : "i-sun";
  for (const id of ["btn-theme", "btn-theme-map"]) {
    const b = $(id);
    if (b) b.innerHTML = svgIcon(goingTo);
  }
}

export function initTheme() {
  apply(localStorage.getItem(KEY) || null);
  const toggle = () => {
    const next = effective() === "light" ? "dark" : "light";
    localStorage.setItem(KEY, next);
    apply(next);
  };
  $("btn-theme")?.addEventListener("click", toggle);
  $("btn-theme-map")?.addEventListener("click", toggle);
}
