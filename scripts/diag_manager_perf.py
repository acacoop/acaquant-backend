"""diag_manager_perf.py — READ-ONLY. Cuánto tarda el endpoint que abre Manager.

La tab Diagnóstico (default al entrar a Manager) pega a /api/manager/diagnostico →
api.services.diagnostico.arbol(), y el front lo re-dispara CADA 10s (setInterval).
`arbol()` NO está cacheado y hace un find_one por cada "pieza" (motor/job/api) →
N queries secuenciales a Atlas en cada llamada. Este diag lo cronometra para confirmar
si ESE es el cuello de botella (o si hay que mirar el bundle del front).

Mide: nº de piezas, tiempo de arbol() en 3 corridas (cold + 2 warm) y el costo
promedio por pieza. READ-ONLY (arbol solo lee).

Uso:
    python -m scripts.diag_manager_perf
"""
from __future__ import annotations

import time

from api.services.diagnostico import arbol


def _try_count_piezas() -> int | None:
    for mod, name in (("api.services.diagnostico", "PIEZAS"),
                      ("api.services.diagnostico_registry", "PIEZAS")):
        try:
            m = __import__(mod, fromlist=[name])
            return len(getattr(m, name))
        except Exception:
            continue
    return None


def main() -> None:
    print("=== Diag perf — /api/manager/diagnostico (arbol) ===\n")
    n_piezas = _try_count_piezas()
    print(f"    piezas (find_one c/u, secuencial): {n_piezas if n_piezas is not None else '?'}")
    print("    cacheado: NO  ·  el front lo pollea cada 10s\n")

    tiempos = []
    for i in range(3):
        t0 = time.perf_counter()
        res = arbol()
        dt = time.perf_counter() - t0
        tiempos.append(dt)
        etiqueta = "COLD" if i == 0 else f"warm {i}"
        print(f"    arbol() #{i + 1} [{etiqueta}]: {dt * 1000:>8.0f} ms")
    print()

    cold = tiempos[0]
    warm = sum(tiempos[1:]) / max(len(tiempos) - 1, 1)
    print(f"    cold={cold * 1000:.0f} ms   ·   warm prom={warm * 1000:.0f} ms")
    if n_piezas:
        print(f"    ≈ {cold / n_piezas * 1000:.1f} ms por pieza (round-trip a Atlas)")
    # Tamaño aproximado del árbol devuelto (sanity).
    try:
        n_vistas = len(res.get("vistas") or res.get("arbol") or [])
        print(f"    vistas en el árbol: {n_vistas}")
    except Exception:
        pass
    print()

    print("=== Lectura ===")
    peor = cold * 1000
    if peor > 1500:
        print(f"  🔴 {peor:.0f} ms por llamada, SIN cache y polleado cada 10s → ESTE es el cuello.")
        print("     Fix de fondo (alto valor / bajo riesgo):")
        print("       1) @cached(ttl=30) en arbol()  → el poll de 10s pega al cache, no a Mongo.")
        print("       2) las N frescuras en UN pipeline (o $in por colección) en vez de N find_one.")
    elif peor > 500:
        print(f"  🟡 {peor:.0f} ms — medio pesado. Cachear arbol() (ttl 30s) ya lo haría imperceptible.")
    else:
        print(f"  ✅ {peor:.0f} ms — el backend NO es el cuello. La lentitud de Manager es FRONT:")
        print("     bundle pesado (todas las tabs importadas estáticas) → partir con next/dynamic.")
    print("  (Complemento front: en el navegador, F12 → Network, abrí Manager y mirá cuánto tarda")
    print("   'diagnostico' y el JS inicial — eso separa backend de bundle.)")


if __name__ == "__main__":
    main()
