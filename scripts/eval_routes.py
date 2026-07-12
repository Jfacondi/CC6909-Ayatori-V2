"""Evaluación funcional de Ayatori: casos reales de Santiago vía la API.

Geocodifica direcciones con /geocode (o usa coords directas), llama a /plan,
mide el tiempo de respuesta y escribe un resumen + JSON crudo en reports/.

Requiere la API corriendo (ver reports/EVALUACION.md §7).

    PYTHONUTF8=1 .venv/Scripts/python scripts/eval_routes.py
"""
import json
import os
import sys
import time

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")  # evita crash cp1252 en Windows
except Exception:
    pass

BASE = os.environ.get("AYATORI_API", "http://127.0.0.1:8000")
DEP = "2026-06-25T08:00:00"  # jueves, servicio "L" en el feed 2026
OUT_JSON = "reports/eval_results.json"
OUT_MD = "reports/eval_results.md"

client = httpx.Client(timeout=90)


def geocode(q):
    r = client.get(f"{BASE}/geocode", params={"q": q})
    if r.status_code == 200 and r.json():
        d = r.json()[0]
        return [round(d["lat"], 6), round(d["lon"], 6)]
    return None


def resolve(loc):
    """loc puede ser [lat, lon] o una dirección (str)."""
    if isinstance(loc, (list, tuple)):
        return list(loc)
    coords = geocode(loc)
    time.sleep(1.1)  # cortesía con Nominatim
    return coords


def fmt_min(sec):
    return f"{sec / 60:.0f}min"


def summarize(j):
    segs = []
    for s in j["segments"]:
        t = s["type"]
        if t == "walk":
            segs.append(f"a pie {fmt_min(s['duration_seconds'])}")
        elif t == "transit":
            mode = (s.get("mode") or "?").upper()
            name = s.get("route_short_name") or s.get("route_id") or "?"
            opts = s.get("route_options")
            segs.append(f"{mode} {name}" + (f"(+{len(opts)})" if opts else ""))
        else:
            segs.append("↹")
    collapsed = []
    for x in segs:  # colapsa hops repetidos del mismo tramo
        if not collapsed or collapsed[-1] != x:
            collapsed.append(x)
    return (
        f"{fmt_min(j['total_duration_seconds'])} | {j['number_of_transfers']}T | "
        f"{j['total_walking_distance_km']:.2f}km | " + " → ".join(collapsed)
    )


# name, origin, destination, profile, num_alternatives
# Coords directas evitan ambigüedad del geocoder (p.ej. "Pudahuel" -> aeropuerto).
CASES = [
    ("Beauchef→Pudahuel (canónico)", [-33.4580, -70.6617], [-33.4424, -70.7607], "balanced", 3),
    ("Plaza Armas→Estación Central", [-33.4376, -70.6504], [-33.4520, -70.6790], "fastest", 3),
    ("Beauchef→Mall Vespucio (largo)", [-33.4580, -70.6617], [-33.5166, -70.5980], "fewer_transfers", 3),
]


def run_case(name, o, d, profile, n):
    o, d = resolve(o), resolve(d)
    if not o or not d:
        return {"name": name, "error": "geocode failed"}
    body = {"origin": o, "destination": d, "departure": DEP,
            "num_alternatives": n, "profile": profile}
    t0 = time.perf_counter()
    r = client.post(f"{BASE}/plan", json=body)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    rec = {"name": name, "profile": profile, "origin": o, "dest": d,
           "status": r.status_code, "ms": ms}
    if r.status_code != 200:
        rec["error"] = r.text[:300]
        return rec
    js = r.json().get("journeys", [])
    rec["n_journeys"] = len(js)
    rec["journeys"] = [summarize(j) for j in js]
    rec["raw"] = js
    return rec


def main():
    results = []
    for c in CASES:
        print(f"\n### {c[0]}")
        rec = run_case(*c)
        for k in ("status", "ms", "n_journeys", "error"):
            if k in rec:
                print(f"   {k}: {rec[k]}")
        for s in rec.get("journeys", []):
            print(f"   - {s}")
        results.append(rec)

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    lines = [f"# Resultados Ayatori (feed 2026, salida {DEP})", "",
             "| Caso | Perfil | ms | #alt | Mejor viaje |", "|---|---|---|---|---|"]
    for r in results:
        best = (r.get("journeys") or [r.get("error", "—")])[0]
        lines.append(f"| {r['name']} | {r.get('profile','')} | {r.get('ms','')} | "
                     f"{r.get('n_journeys','')} | {best} |")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nWrote {OUT_JSON} and {OUT_MD}")


if __name__ == "__main__":
    main()
