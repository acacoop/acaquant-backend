"""scripts/diag_renta_fija_perf.py — POR QUÉ TARDA la vista /renta-fija.

Paso 1 del rediseño de RENTA FIJA (docs/RENTA_FIJA.md §Rediseño). Antes de
optimizar nada hay que saber QUÉ tarda: la vista pide **9 endpoints en un
`Promise.all`** y por lo tanto **no renderiza hasta que termina el más lento**.
Este diag mide cada uno por separado, del lado del Droplet, para separar tres
cosas que se confunden:

  · **el tiempo del service** (la query + el cálculo) — lo que se puede optimizar,
  · **el peso del payload** (bytes que viajan a Vercel) — lo que hace lenta la red,
  · **el efecto del `@cached`** — se mide FRÍO y CALIENTE; si la 2ª corrida vuela,
    el problema lo tapa el cache en uso normal pero lo paga el primero que entra.

Y contesta la pregunta del rediseño: **cuánto duraría la primera pantalla si la
vista se parte en tabs** — o sea, si solo se piden los 3 endpoints que la tab
CURVAS necesita en vez de los 9.

READ-ONLY: solo lee. No escribe una fila ni toca cache de prod (salvo el
`@cached` en memoria del propio proceso del script, que muere con él).

Uso:
    python -m scripts.diag_renta_fija_perf              # los 9, frío y caliente
    python -m scripts.diag_renta_fija_perf --repeticiones 3
    python -m scripts.diag_renta_fija_perf --solo renta-fija,flujos
"""
from __future__ import annotations

import argparse
import json
import time

# Los 9 fetches del `Promise.all` de src/app/renta-fija/page.tsx, en el mismo
# orden. `tab` dice a qué tab del rediseño va a pertenecer cada uno: es lo que
# permite calcular cuánto ahorra partir la vista.
_LLAMADAS = [
    ("renta-fija", "CURVAS", "api.services.renta_fija_sql", "get_renta_fija", {}),
    ("flujos", "CURVAS", "api.services.titulos_flujos", "flujos_instrumentos", {}),
    ("fair-value:tasa_fija", "CURVAS", "api.services.fair_value",
     "get_fair_value_live", {"curva": "tasa_fija"}),
    ("fair-value:cer", "CURVAS", "api.services.fair_value",
     "get_fair_value_live", {"curva": "cer"}),
    ("forwards", "FORWARDS", "api.services.mercado_hist_sql", "get_forwards", {}),
    ("forwards-zscore", "FORWARDS", "api.services.mercado_hist_sql",
     "get_forwards_zscore", {}),
    ("historico/forwards", "FORWARDS", "api.services.mercado_hist_sql",
     "get_historico_forwards", {}),
    ("breakevens", "BREAKEVENS", "api.services.mercado_hist_sql", "get_breakevens", {}),
    ("historico/breakevens", "BREAKEVENS", "api.services.mercado_hist_sql",
     "get_historico_breakevens", {}),
]


def _tamano(v) -> tuple[int, int]:
    """(bytes del JSON, filas). El peso es lo que viaja Droplet → Vercel: un
    service rápido con un payload gordo igual hace lenta la pantalla."""
    try:
        crudo = json.dumps(v, default=str)
    except Exception:
        return -1, -1
    n = len(v) if isinstance(v, (list, dict)) else 1
    return len(crudo.encode()), n


def _correr(mod: str, fn: str, kwargs: dict) -> tuple[float, int, int, str]:
    """→ (ms, bytes, filas, error)."""
    import importlib
    try:
        f = getattr(importlib.import_module(mod), fn)
    except Exception as e:
        return 0.0, 0, 0, f"import: {str(e)[:90]}"
    t0 = time.perf_counter()
    try:
        out = f(**kwargs)
    except Exception as e:
        return (time.perf_counter() - t0) * 1000, 0, 0, str(e)[:90]
    ms = (time.perf_counter() - t0) * 1000
    b, n = _tamano(out)
    return ms, b, n, ""


def main() -> None:
    ap = argparse.ArgumentParser(description="Perf de la vista /renta-fija")
    ap.add_argument("--repeticiones", type=int, default=2,
                    help="corridas por endpoint (la 1ª es FRÍA, default 2)")
    ap.add_argument("--solo", help="lista separada por comas de nombres a medir")
    args = ap.parse_args()

    llamadas = _LLAMADAS
    if args.solo:
        qs = {s.strip() for s in args.solo.split(",") if s.strip()}
        llamadas = [c for c in _LLAMADAS if c[0] in qs or c[0].split(":")[0] in qs]

    print("=" * 92)
    print("VISTA /renta-fija — QUÉ TARDA (medido en el Droplet, sin el viaje a Vercel)")
    print("=" * 92)
    print("La página hace estos 9 fetches en UN Promise.all → no renderiza hasta")
    print("que termina EL MÁS LENTO. Por eso lo que importa es el máximo, no la suma.\n")

    filas = []
    for nombre, tab, mod, fn, kw in llamadas:
        tiempos = []
        b = n = 0
        err = ""
        for _ in range(max(1, args.repeticiones)):
            ms, b_, n_, e = _correr(mod, fn, kw)
            tiempos.append(ms)
            b, n = b_ or b, n_ or n
            if e:
                err = e
                break
        filas.append({"nombre": nombre, "tab": tab, "frio": tiempos[0],
                      "caliente": tiempos[-1] if len(tiempos) > 1 else None,
                      "bytes": b, "filas": n, "error": err})
        estado = f"✘ {err}" if err else f"{tiempos[0]:8.0f} ms"
        print(f"  {nombre:<24} {estado}")

    print(f"\n{'ENDPOINT':<24}{'TAB':<11}{'FRÍO':>10}{'CALIENTE':>10}"
          f"{'PAYLOAD':>12}{'FILAS':>8}")
    print("─" * 92)
    for f in sorted(filas, key=lambda x: -x["frio"]):
        cal = f"{f['caliente']:.0f} ms" if f["caliente"] is not None else "—"
        kb = f"{f['bytes'] / 1024:,.0f} KB" if f["bytes"] > 0 else "—"
        marca = "  ⚠" if f["error"] else ""
        print(f"{f['nombre']:<24}{f['tab']:<11}{f['frio']:>7.0f} ms{cal:>10}"
              f"{kb:>12}{f['filas']:>8}{marca}")
    print("─" * 92)

    ok = [f for f in filas if not f["error"]]
    if not ok:
        print("\n✘ ninguna llamada corrió — ¿falta la conexión a la base?")
        return

    total_bytes = sum(f["bytes"] for f in ok)
    peor = max(ok, key=lambda x: x["frio"])
    print(f"{'TOTAL':<35}{sum(f['frio'] for f in ok):>7.0f} ms"
          f"{'':>10}{total_bytes / 1024:>9,.0f} KB")

    print(f"\n➡ EL CUELLO DE BOTELLA: **{peor['nombre']}** con {peor['frio']:.0f} ms.")
    print("   La página espera ESE tiempo aunque las otras 8 terminen antes "
          "(Promise.all).")

    gordo = max(ok, key=lambda x: x["bytes"])
    print(f"\n➡ EL PAYLOAD MÁS PESADO: **{gordo['nombre']}** con "
          f"{gordo['bytes'] / 1024:,.0f} KB en {gordo['filas']} filas "
          f"({gordo['bytes'] / max(gordo['filas'], 1) / 1024:.1f} KB por fila).")
    print("   Eso viaja entero Droplet → Vercel → navegador en CADA carga.")

    # el número que decide el rediseño: ¿cuánto ahorra partir en tabs?
    curvas = [f for f in ok if f["tab"] == "CURVAS"]
    if curvas:
        hoy = max(f["frio"] for f in ok)
        con_tabs = max(f["frio"] for f in curvas)
        kb_hoy = total_bytes / 1024
        kb_tabs = sum(f["bytes"] for f in curvas) / 1024
        print(f"\n➡ SI SE PARTE EN TABS (la primera pantalla pide solo los "
              f"{len(curvas)} de CURVAS):")
        print(f"     espera:  {hoy:.0f} ms  →  {con_tabs:.0f} ms")
        print(f"     payload: {kb_hoy:,.0f} KB  →  {kb_tabs:,.0f} KB")
        if hoy > 0:
            print(f"     ({100 * (1 - con_tabs / hoy):.0f}% menos de espera · "
                  f"{100 * (1 - kb_tabs / max(kb_hoy, 0.01)):.0f}% menos de bytes)")
        print("   OJO: esto es el piso. Lo que se saca son los 5 fetches de "
              "FORWARDS y BREAKEVENS, que hoy se pagan aunque no los mires.")

    frios = [f for f in ok if f["caliente"] is not None and f["caliente"] > 0]
    tapados = [f for f in frios if f["frio"] / max(f["caliente"], 0.01) > 5]
    if tapados:
        print("\n➡ TAPADOS POR EL @cached (frío ≫ caliente): "
              + ", ".join(f"{f['nombre']} ({f['frio']:.0f}→{f['caliente']:.0f} ms)"
                          for f in tapados))
        print("   El cache los esconde en uso normal, pero el PRIMERO que entra "
              "después de que expira paga el precio completo. Si la vista 'a veces "
              "tarda', es esto.")


if __name__ == "__main__":
    main()
