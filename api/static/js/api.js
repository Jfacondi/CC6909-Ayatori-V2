// Wrappers de fetch a la API Ayatori. Lanzan Error con el detalle del backend.

async function handle(r) {
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    const detail = err.detail ?? err;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json();
}

const getJSON = (url) => fetch(url).then(handle);
const postJSON = (url, body) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(handle);

export const api = {
  health: () => getJSON("/health"),
  schema: () => getJSON("/config/schema"),
  geocode: (q, limit = 5) => getJSON(`/geocode?q=${encodeURIComponent(q)}&limit=${limit}`),
  plan: (body) => postJSON("/plan", body),
  compare: (body) => postJSON("/plan/compare", body),
};
