// Estado compartido de la aplicación (mutable, importado por referencia).

export const state = {
  schema: null,          // /config/schema
  mode: "single",        // "single" | "compare"
  profile: "balanced",
  singleOverrides: {},    // overrides de CSAConfig para plan único
  variants: [{ label: "A", overrides: {} }], // modo comparar
  availableModes: ["bus", "metro", "rail", "tram"],
  // Rango de validez del feed GTFS. Defaults de respaldo; /health los
  // sobreescribe con el rango real del feed cargado (ver loadState en main.js).
  feedStart: "2026-04-25",
  feedEnd: "2026-12-31",
};
