// Toasts no bloqueantes (reemplazan a window.alert).

import { $, svgIcon } from "../dom.js";

/**
 * @param {string} message  HTML breve
 * @param {{type?: "info"|"success"|"warning"|"error", timeout?: number}} opts
 */
export function toast(message, { type = "info", timeout = 4800 } = {}) {
  const stack = $("toasts");
  if (!stack) return () => {};

  const node = document.createElement("div");
  node.className = `toast toast--${type}`;
  node.setAttribute("role", type === "error" ? "alert" : "status");
  node.innerHTML = `
    <div class="toast__body">${message}</div>
    <button class="toast__close" aria-label="Cerrar">${svgIcon("i-close")}</button>`;

  let done = false;
  const close = () => {
    if (done) return;
    done = true;
    node.classList.add("is-leaving");
    setTimeout(() => node.remove(), 350);
  };

  node.querySelector(".toast__close").addEventListener("click", close);
  stack.appendChild(node);
  if (timeout) setTimeout(close, timeout);
  return close;
}
