"""Evaluación cuantitativa AGREGADA: latencia y tasa de éxito sobre N consultas.

Complementa eval_routes.py (3 casos vs Google) y eval_battery.py (12 invariantes)
con una corrida a gran escala: muestrea N pares origen-destino de paradas REALES
del feed (semilla fija → reproducible), consulta /plan y reporta la distribución
de tiempos de respuesta, la tasa de éxito y las medianas de transbordos/caminata.
Responde la crítica de que la evaluación se apoyaba solo en casos anecdóticos.

Requiere la API corriendo (Haversine; el ruteo OSM solo cambia la geometría de
las caminatas, no la selección de rutas ni el conteo de transbordos).

    AYATORI_API=http://127.0.0.1:8000 PYTHONUTF8=1 .venv/Scripts/python scripts/eval_aggregate.py
"""
import csv
import io
import json
import math
import os
import random
import statistics
import sys
import time
import zipfile

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.environ.get("AYATORI_API", "http://127.0.0.1:8000")
GTFS = os.environ.get("AYATORI_GTFS_PATH", "ayatori/data/GTFS/GTFS_20260425_v3.zip")
DEP = os.environ.get("AGG_DEP", "2026-06-25T08:00:00")  # jueves laboral, servicio "L"
N = int(os.environ.get("AGG_N", "300"))
SEED = int(os.environ.get("AGG_SEED", "42"))
PROFILE = os.environ.get("AGG_PROFILE", "fastest")
TAG = os.environ.get("AGG_TAG", "")
OUT_JSON = f"reports/eval_aggregate{TAG}.json"
OUT_MD = f"reports/eval_aggregate{TAG}.md"

client = httpx.Client(timeout=120)


def haversine_km(a, b):
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def load_stops():
    """Paradas abordables (location_type 0) con coordenadas válidas."""
    z = zipfile.ZipFile(GTFS)
    with z.open("stops.txt") as f:
        rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    out = []
    for x in rows:
        lt = (x.get("location_type") or "0").strip() or "0"
        if lt != "0":
            continue
        try:
            lat, lon = float(x["stop_lat"]), float(x["stop_lon"])
        except (ValueError, KeyError):
            continue
        # Recorte al área metropolitana (descarta paradas espurias fuera de RM)
        if -33.9 <= lat <= -33.0 and -71.2 <= lon <= -70.4:
            out.append([round(lat, 6), round(lon, 6)])
    return out


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


BUCKETS = [(0, 3, "<3 km"), (3, 8, "3–8 km"), (8, 15, "8–15 km"), (15, 999, ">15 km")]


def bucket(d):
    for lo, hi, label in BUCKETS:
        if lo <= d < hi:
            return label
    return ">15 km"


def main():
    stops = load_stops()
    print(f"Paradas abordables en RM: {len(stops)}")
    rng = random.Random(SEED)
    pairs = []
    while len(pairs) < N:
        o = rng.choice(stops)
        d = rng.choice(stops)
        if o == d:
            continue
        pairs.append((o, d))

    print(f"Corriendo {N} consultas /plan (perfil {PROFILE}, salida {DEP})…\n")
    recs = []
    t_start = time.perf_counter()
    for i, (o, d) in enumerate(pairs):
        body = {"origin": o, "destination": d, "departure": DEP,
                "num_alternatives": 3, "profile": PROFILE}
        t0 = time.perf_counter()
        try:
            r = client.post(f"{BASE}/plan", json=body)
            ms = (time.perf_counter() - t0) * 1000
            status = r.status_code
            js = r.json().get("journeys", []) if status == 200 else []
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            status, js = -1, []
            print(f"  [{i}] EXC {e}")
        best = js[0] if js else None
        direct = bool(best) and not any(s["type"] == "transit" for s in best["segments"])
        recs.append({
            "i": i, "straight_km": round(haversine_km(o, d), 2), "status": status,
            "ms": round(ms, 1), "n": len(js),
            "transfers": best["number_of_transfers"] if best else None,
            "walk_km": round(best["total_walking_distance_km"], 3) if best else None,
            "dur_min": round(best["total_duration_seconds"] / 60, 1) if best else None,
            "direct_walk": direct,
        })
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{N} …")
    wall = time.perf_counter() - t_start

    ok = [r for r in recs if r["status"] == 200]
    solved = [r for r in ok if r["n"] > 0]
    transit = [r for r in solved if not r["direct_walk"]]
    lat = [r["ms"] for r in ok]
    n_err = sum(1 for r in recs if r["status"] not in (200,))

    summary = {
        "n": N, "seed": SEED, "profile": PROFILE, "departure": DEP,
        "wall_seconds": round(wall, 1),
        "http_ok": len(ok), "errors": n_err,
        "solved": len(solved), "solved_pct": round(100 * len(solved) / N, 1),
        "direct_walk": len(solved) - len(transit),
        "transit_journeys": len(transit),
        "latency_ms": {
            "p50": round(pct(lat, 50), 1), "p90": round(pct(lat, 90), 1),
            "p95": round(pct(lat, 95), 1), "p99": round(pct(lat, 99), 1),
            "max": round(max(lat), 1) if lat else 0,
            "mean": round(statistics.mean(lat), 1) if lat else 0,
        },
        "transfers_median": statistics.median([r["transfers"] for r in transit]) if transit else None,
        "transfers_mean": round(statistics.mean([r["transfers"] for r in transit]), 2) if transit else None,
        "walk_km_median": round(statistics.median([r["walk_km"] for r in transit]), 2) if transit else None,
        "dur_min_median": round(statistics.median([r["dur_min"] for r in transit]), 1) if transit else None,
    }

    # Por bucket de distancia
    by_bucket = {}
    for lo, hi, label in BUCKETS:
        b = [r for r in ok if lo <= r["straight_km"] < hi]
        bs = [r for r in b if r["n"] > 0]
        blat = [r["ms"] for r in b]
        by_bucket[label] = {
            "n": len(b), "solved_pct": round(100 * len(bs) / len(b), 1) if b else 0,
            "p50_ms": round(pct(blat, 50), 1), "p90_ms": round(pct(blat, 90), 1),
        }

    print("\n=== RESUMEN AGREGADO ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nPor distancia:")
    print(json.dumps(by_bucket, ensure_ascii=False, indent=2))

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "by_bucket": by_bucket, "records": recs},
                  f, ensure_ascii=False, indent=2)

    L = summary["latency_ms"]
    lines = [
        f"# Evaluación agregada Ayatori (feed 2026, salida {DEP})", "",
        f"**{N} consultas** `/plan` sobre pares de paradas reales muestreados "
        f"(semilla {SEED}), perfil `{PROFILE}`, ruteo peatonal Haversine.", "",
        f"- Sin errores HTTP: **{len(ok)}/{N}** ({n_err} errores).",
        f"- Con itinerario: **{summary['solved']}/{N} ({summary['solved_pct']} %)** "
        f"({summary['transit_journeys']} con transporte, {summary['direct_walk']} caminata directa).",
        f"- Latencia (ms): p50 **{L['p50']}**, p90 **{L['p90']}**, p95 **{L['p95']}**, "
        f"p99 **{L['p99']}**, máx **{L['max']}**, media {L['mean']}.",
        f"- Mediana transbordos: **{summary['transfers_median']}**, "
        f"caminata **{summary['walk_km_median']} km**, duración **{summary['dur_min_median']} min** "
        "(solo viajes con transporte).", "",
        "## Por distancia en línea recta", "",
        "| Bucket | n | % resuelto | p50 ms | p90 ms |",
        "|---|---|---|---|---|",
    ]
    for _, _, label in BUCKETS:
        b = by_bucket[label]
        lines.append(f"| {label} | {b['n']} | {b['solved_pct']} | {b['p50_ms']} | {b['p90_ms']} |")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nWrote {OUT_JSON} and {OUT_MD}")


if __name__ == "__main__":
    main()
