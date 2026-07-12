"""Batería de casos funcionales: ¿Ayatori rutea bien en escenarios diversos?

A diferencia de eval_routes.py (3 casos para comparar con Google), aquí se
prueban ~12 casos cubriendo metro/bus/multimodal, distancias y zonas distintas,
y casos límite (origen=destino, caminata corta, océano). Cada caso verifica
INVARIANTES funcionales automáticamente; imprime PASS/FAIL y un veredicto.

Requiere la API corriendo. Escribe reports/eval_battery.md.

    AYATORI_API=http://127.0.0.1:8000 PYTHONUTF8=1 .venv/Scripts/python scripts/eval_battery.py
"""
import math
import os
import sys
import time

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.environ.get("AYATORI_API", "http://127.0.0.1:8000")
DEP = "2026-06-25T08:00:00"  # jueves laboral, servicio "L"
OUT_MD = "reports/eval_battery.md"
client = httpx.Client(timeout=120)

# name, origin[lat,lon], dest[lat,lon], profile, expect, allowed_modes
# expect ∈ {"route","walk","empty"}.  allowed_modes=None → todos.
CASES = [
    ("Metro L1 largo (Los Dominicos→Pajaritos)", [-33.4036, -70.5466], [-33.4668, -70.7320], "fastest", "route", None),
    ("Cross-city E-O (Las Condes→Maipú)", [-33.4010, -70.5780], [-33.5110, -70.7580], "balanced", "route", None),
    ("Norte-Sur (Quinta Normal→La Florida)", [-33.4400, -70.6840], [-33.5166, -70.5980], "balanced", "route", None),
    ("Sur lejano (Puente Alto→Centro)", [-33.6110, -70.5760], [-33.4430, -70.6500], "fastest", "route", None),
    ("Bus barrial (Cerrillos→Estación Central)", [-33.4950, -70.7150], [-33.4520, -70.6790], "balanced", "route", None),
    ("Corto cruce río (Providencia→Bellavista)", [-33.4260, -70.6100], [-33.4330, -70.6350], "fastest", "route", None),
    ("Cobertura aeropuerto (Costanera→SCL)", [-33.4178, -70.6063], [-33.3930, -70.7858], "fastest", "route", None),
    ("prefer_rail (Maipú→Tobalaba)", [-33.5110, -70.7580], [-33.4180, -70.6020], "prefer_rail", "route", None),
    ("Solo-metro filtrado (Baquedano→Tobalaba)", [-33.4370, -70.6340], [-33.4180, -70.6020], "fastest", "route", ["metro"]),
    ("Borde: origen==destino", [-33.4376, -70.6504], [-33.4376, -70.6504], "balanced", "walk", None),
    ("Borde: ~300 m a pie", [-33.4376, -70.6504], [-33.4400, -70.6520], "fastest", "walk", None),
    ("Borde: océano (inalcanzable)", [-33.0000, -71.9000], [-33.4000, -70.6000], "fastest", "empty", None),
]


def haversine_km(a, b):
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def modes_in(j):
    return {(s.get("mode") or "").lower() for s in j["segments"] if s["type"] == "transit" and s.get("mode")}


def dominates(x, y):
    """x domina a y si es <= en (tiempo, transbordos, caminata) y < en alguno."""
    xa = (x["total_duration_seconds"], x["number_of_transfers"], x["total_walking_distance_km"])
    ya = (y["total_duration_seconds"], y["number_of_transfers"], y["total_walking_distance_km"])
    return all(a <= b for a, b in zip(xa, ya)) and any(a < b for a, b in zip(xa, ya))


def check(name, o, d, profile, expect, allowed):
    body = {"origin": o, "destination": d, "departure": DEP,
            "num_alternatives": 4, "profile": profile}
    if allowed:
        body["config"] = {"allowed_modes": allowed}
    t0 = time.perf_counter()
    try:
        r = client.post(f"{BASE}/plan", json=body)
    except Exception as e:
        return {"name": name, "ok": False, "issues": [f"excepción de red: {e}"], "ms": 0, "summary": "—"}
    ms = round((time.perf_counter() - t0) * 1000)
    issues = []

    if r.status_code != 200:
        return {"name": name, "ok": False, "issues": [f"status {r.status_code}: {r.text[:120]}"], "ms": ms, "summary": "—"}

    js = r.json().get("journeys", [])

    if expect == "empty":
        ok = len(js) == 0
        if not ok:
            issues.append(f"esperaba 0 viajes, llegaron {len(js)}")
        return {"name": name, "ok": ok, "issues": issues, "ms": ms,
                "summary": "0 viajes (graceful)" if ok else f"{len(js)} viajes inesperados"}

    if not js:
        return {"name": name, "ok": False, "issues": ["0 viajes (esperaba ruta)"], "ms": ms, "summary": "vacío"}

    # --- invariantes por viaje ---
    for i, j in enumerate(js):
        dur = j["total_duration_seconds"]
        nt = j["number_of_transfers"]
        wk = j["total_walking_distance_km"]
        if not (isinstance(dur, (int, float)) and dur >= 0 and math.isfinite(dur)):
            issues.append(f"alt{i}: duración inválida {dur}")
        if nt < 0:
            issues.append(f"alt{i}: transbordos negativos {nt}")
        if not (math.isfinite(wk) and wk >= 0):
            issues.append(f"alt{i}: caminata inválida {wk}")
        if allowed:
            extra = modes_in(j) - {m.lower() for m in allowed}
            if extra:
                issues.append(f"alt{i}: modos no permitidos {extra}")
        # Invariante: un footpath (transbordo caminando) sólo sirve si después
        # se aborda algo. Un `transfer` seguido de la caminata de egreso —o que
        # cierra el viaje— es un transbordo colgante que infla el conteo y parte
        # el egreso (bug corregido en _reconstruct_journey; ver EVALUACION §fix).
        segs = j["segments"]
        for k, s in enumerate(segs):
            if s["type"] == "transfer":
                nxt = segs[k + 1] if k + 1 < len(segs) else None
                if nxt is None or nxt["type"] != "transit":
                    issues.append(f"alt{i}: transbordo colgante (footpath sin abordaje)")

    best = js[0]
    if expect == "walk":
        if best["number_of_transfers"] != 0:
            issues.append(f"caminata directa esperada pero {best['number_of_transfers']} transbordos")
        if any(s["type"] == "transit" for s in best["segments"]):
            issues.append("caminata directa esperada pero hay tramos de transporte")

    # --- Pareto: ningún alternativa domina a la primera; sin duplicados dominados ---
    for j in js[1:]:
        if dominates(j, best):
            issues.append("la 1ª alternativa está dominada por otra (orden Pareto roto)")
            break

    # --- cordura de cobertura: si hay ruta, la caminata total no debería ser absurda ---
    straight = haversine_km(o, d)
    if best["total_walking_distance_km"] > straight + 3.0:
        issues.append(f"caminata {best['total_walking_distance_km']:.1f}km >> distancia recta {straight:.1f}km")

    segs = []
    for s in best["segments"]:
        if s["type"] == "transit" and s.get("mode"):
            tag = (s.get("route_short_name") or s.get("mode"))
            if not segs or segs[-1] != tag:
                segs.append(str(tag))
    summary = (f"{dur_min(best)} · {best['number_of_transfers']}T · "
               f"{best['total_walking_distance_km']:.2f}km · "
               f"{' → '.join(segs) if segs else 'a pie directo'} · {len(js)} alt")

    return {"name": name, "ok": not issues, "issues": issues, "ms": ms, "summary": summary}


def dur_min(j):
    return f"{j['total_duration_seconds'] / 60:.0f}min"


def main():
    print(f"Batería funcional contra {BASE} (salida {DEP})\n")
    results = []
    for c in CASES:
        res = check(*c)
        results.append(res)
        mark = "PASS" if res["ok"] else "FAIL"
        print(f"[{mark}] {res['name']}  ({res['ms']} ms)")
        print(f"        {res['summary']}")
        for iss in res["issues"]:
            print(f"        WARN: {iss}")

    n_ok = sum(r["ok"] for r in results)
    n = len(results)
    print(f"\n=== {n_ok}/{n} casos PASS ===")

    lines = [f"# Batería funcional Ayatori (feed 2026, salida {DEP})", "",
             f"**{n_ok}/{n} casos PASS** · API `{BASE}` · ruteo peatonal Haversine "
             "(el ruteo OSM no altera la selección de rutas, solo la geometría de las caminatas).", "",
             "| OK | Caso | Perfil | ms | Mejor viaje | Observaciones |",
             "|---|---|---|---|---|---|"]
    for c, r in zip(CASES, results):
        mark = "OK" if r["ok"] else "FAIL"
        obs = "; ".join(r["issues"]) if r["issues"] else "—"
        lines.append(f"| {mark} | {r['name']} | {c[3]} | {r['ms']} | {r['summary']} | {obs} |")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nWrote {OUT_MD}")
    return 0 if n_ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
