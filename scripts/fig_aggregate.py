"""Genera la figura de distribución de latencia agregada para la memoria.

Lee reports/eval_aggregate.json y escribe
informe final/imagenes/figuras/05-latencia-agregada.png
"""
import json
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SRC = "reports/eval_aggregate.json"
OUT = "informe final/imagenes/figuras/05-latencia-agregada.png"

with open(SRC, encoding="utf-8") as f:
    data = json.load(f)

recs = [r for r in data["records"] if r["status"] == 200]
lat = sorted(r["ms"] for r in recs)
S = data["summary"]
L = S["latency_ms"]

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["CMU Serif", "DejaVu Serif", "Times New Roman"],
    "font.size": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": "#666666",
    "axes.linewidth": 0.8,
})

fig, ax = plt.subplots(figsize=(6.6, 3.1))

BAR = "#5B7FA6"   # azul acero apagado, seguro en escala de grises
LINE = "#333333"

bins = [i * 125 for i in range(0, int(max(lat) // 125) + 2)]
ax.hist(lat, bins=bins, color=BAR, edgecolor="white", linewidth=0.5)

ymax = ax.get_ylim()[1]
for key, label, style in [("p50", "p50", "solid"), ("p90", "p90", (0, (4, 2))),
                          ("p99", "p99", (0, (1, 1.5)))]:
    x = L[key]
    ax.axvline(x, color=LINE, linewidth=1.1, linestyle=style)
    ax.text(x, ymax * 0.97, f" {label}\n {x:.0f} ms", va="top", ha="left",
            fontsize=8.5, color=LINE)

ax.set_xlabel("Tiempo de respuesta (ms)")
ax.set_ylabel("Consultas")
ax.set_xlim(0, min(max(lat) * 1.02, 3200))
ax.grid(axis="y", color="#E3E3E3", linewidth=0.6)
ax.set_axisbelow(True)

sub = (f"n = {S['n']} consultas · {S['solved_pct']} % resueltas · "
       f"mediana {S['dur_min_median']:.0f} min, {S['transfers_median']:.0f} transbordos, "
       f"{S['walk_km_median']:.2f} km a pie")
ax.set_title(sub, fontsize=8.5, color="#555555", loc="left", pad=8)

fig.tight_layout()
fig.savefig(OUT, dpi=200, bbox_inches="tight")
print(f"Wrote {OUT}  (p50={L['p50']}, p90={L['p90']}, p99={L['p99']}, max={L['max']})")
