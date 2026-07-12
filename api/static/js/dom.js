// Helpers DOM mínimos compartidos por todos los módulos.

export const $ = (id) => document.getElementById(id);

/** Devuelve el markup de un icono del sprite SVG inline. */
export function svgIcon(symId, cls = "icon") {
  return `<svg class="${cls}" aria-hidden="true"><use href="#${symId}"></use></svg>`;
}
