"""diag_peaje_vs_servidor.py — de los ~500 ms de cada request, ¿cuánto es la query?

Read-only. Nace de medir la app desde el navegador (2026-08-11, `src/lib/perf.ts`
en acaquant-frontend). Ahí quedó claro que el costo NO está donde se pensaba:

  - el `JSON.parse` del browser tarda **0,5 ms** — el cálculo del cliente no pesa;
  - cada request tarda entre **300 y 2800 ms**, sin importar el tamaño;
  - endpoints que devuelven cuatro números igual tardan ~450 ms.

Eso deja UNA pregunta abierta, y es la que decide dónde trabajar: de esos
milisegundos, **¿cuántos son la query y cuántos son el viaje?**

  - Si manda la QUERY → hay que optimizar SQL / índices / precomputar. Trabajo
    de backend, endpoint por endpoint.
  - Si manda el VIAJE → no hay query que arreglar. Es la cadena
    browser → Next (Vercel) → Cloudflare → Droplet, y se ataca con menos
    requests o acortando la cadena. Optimizar SQL ahí no mueve la aguja.

Este diag mide el lado SERVIDOR de los mismos endpoints que se midieron en el
browser y hace la resta. La columna `browser` son los promedios REALES de esa
sesión (constante `MS_BROWSER`, con la fecha): no se re-miden acá, sirven de
referencia. Si volvés a medir el browser, actualizalos.

⚠️ Lo que la resta NO separa: dentro del "peaje" quedan juntos el viaje de red,
el proxy de Next, la serialización a JSON y el overhead de FastAPI. Es el
techo del ahorro posible si la query fuera instantánea, no un número de red puro.

Uso (Droplet, desde la raíz):
    python -m scripts.diag_peaje_vs_servidor
    python -m scripts.diag_peaje_vs_servidor --desde 2026-01-01
    python -m scripts.diag_peaje_vs_servidor --json peaje.json

Los parámetros por defecto imitan el uso típico de la vista (período desde
principio de año, moneda ARS, sin filtros). No son LOS mismos filtros exactos
que usaste en el browser: si un endpoint da muy distinto, probá con `--desde`.

⚠️ TRAMPA QUE YA HIZO MENTIR A ESTE DIAG (2026-08-11). Llamar al service directo
SALTEA los defaults del ROUTER, y el router es parte del contrato. En la primera
corrida, `informe-segmento-detalle` reportó 12,6 MB de payload y 817 ms — pero la
app nunca pide eso: el router manda `max_ops=1000` y el service, sin ese tope,
devuelve TODAS las operaciones. El diag estaba midiendo una llamada que no existe
en producción. **Cada candidato de acá tiene que pasar los defaults del ROUTER, no
los del service**; si tocás uno, chequeá primero la firma en `api/routers/`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

# ── ms promedio MEDIDOS EN EL BROWSER (sesión 2026-08-11, vistas Operadores /
# Operaciones / Agro / AUM). Incluyen TODO: viaje, proxy de Next, servidor y
# serialización. Son el total contra el que se compara el servidor.
MS_BROWSER: dict[str, float] = {
    "comercial/informe-segmento-detalle": 1527.2,   # n=15
    "comercial/operador": 1428.5,                   # n=15
    "comercial/informe": 997.9,                     # n=13
    "comercial/informe-segmento": 621.7,            # n=13
    "comercial/analisis": 2297.8,                   # n=1
    "ops/agro": 920.2,                              # n=8
    "ops/resumen": 520.5,                           # n=7
    "ops/serie": 448.2,                             # n=4
    "portfolio/total-serie": 950.0,                 # n=4 (browser: /api/aum-total/serie)
}


def _hoy() -> str:
    return datetime.now(UTC).date().isoformat()


def _año() -> str:
    return date(datetime.now(UTC).year, 1, 1).isoformat()


def _candidatos(desde: str, hasta: str) -> list[dict]:
    """Cada entrada llama al MISMO service que sirve el endpoint del browser.

    Los imports van adentro de cada lambda a propósito: si un service se rompe,
    cae solo ese candidato y el diag sigue con el resto.
    """
    def informe_segmento_detalle() -> Any:
        from api.services.comercial_sql import informe_segmento_detalle as f
        # max_ops=1000 y segmento="todos" NO son los defaults del SERVICE: son los
        # del ROUTER, que es lo que realmente recibe la app. Sin el tope, el
        # service devuelve TODAS las operaciones (12,6 MB medidos el 2026-08-11)
        # y el diag miente por exceso. Ver la nota sobre esta trampa arriba.
        return f(segmento="todos", moneda="ARS", desde=desde, max_ops=1000)

    def operador() -> Any:
        from api.services.comercial_sql import operador_comercial as f
        # El router manda operador=[] (lista vacía = todos), no el sentinel
        # "__todos__" — ese lo usa analisis_comercial y son caminos distintos.
        return f(operador=[], moneda="ARS", desde=desde)

    def informe() -> Any:
        from api.services.comercial_sql import informe_comercial as f
        return f(moneda="ARS", desde=desde)

    def informe_segmento() -> Any:
        from api.services.comercial_sql import informe_cuentas_por_segmento as f
        return f(desde=desde)

    def analisis() -> Any:
        from api.services.comercial_sql import analisis_comercial as f
        return f(operador=[], moneda="ARS")   # router: operador=[] = todos

    def agro() -> Any:
        from api.services.operaciones_sql import ops_agro as f
        return f(desde=desde, hasta=hasta, agg="MENSUAL")

    def resumen() -> Any:
        from api.services.operaciones_sql import ops_resumen as f
        return f(moneda="ARS", desde=desde, hasta=hasta)

    def serie() -> Any:
        from api.services.operaciones_sql import ops_serie as f
        return f(moneda="ARS")

    def total_serie() -> Any:
        from api.services.portfolio_sql import total_serie as f
        return f(desde=desde, hasta=hasta, moneda="ARS")

    return [
        {"id": "comercial/informe-segmento-detalle", "llamar": informe_segmento_detalle,
         "vista": "Operadores — el más caro y el más repetido (15 veces en 1 minuto)"},
        {"id": "comercial/operador", "llamar": operador,
         "vista": "Operadores — 15 llamadas en 1 minuto"},
        {"id": "comercial/informe", "llamar": informe,
         "vista": "Operadores — 13 llamadas"},
        {"id": "comercial/informe-segmento", "llamar": informe_segmento,
         "vista": "Operadores — 13 llamadas"},
        {"id": "comercial/analisis", "llamar": analisis,
         "vista": "Operadores — el pico absoluto medido (2,3 s)"},
        {"id": "ops/agro", "llamar": agro,
         "vista": "Operaciones/Agro — 22 llamadas, ~1 s cada cambio de filtro"},
        {"id": "ops/resumen", "llamar": resumen,
         "vista": "Operaciones — sale SIEMPRE junto con ops/serie"},
        {"id": "ops/serie", "llamar": serie,
         "vista": "Operaciones — el par de ops/resumen"},
        {"id": "portfolio/total-serie", "llamar": total_serie,
         "vista": "AUM — pico de 2858 ms"},
    ]


def _medir(fn: Callable[[], Any], veces: int = 3) -> tuple[float, float, Any]:
    """(ms_primera, ms_mejor_de_las_siguientes, payload).

    La primera incluye warm-up. De las siguientes se toma la MEJOR: es el piso
    real del cómputo — con la máquina ocupada por los motores, el promedio
    mide la carga del Droplet, no el costo del endpoint.
    """
    t0 = time.perf_counter()
    out = fn()
    primera = (time.perf_counter() - t0) * 1000
    mejor = float("inf")
    for _ in range(max(1, veces - 1)):
        t0 = time.perf_counter()
        fn()
        mejor = min(mejor, (time.perf_counter() - t0) * 1000)
    return primera, mejor, out


def _correr(cand: dict) -> dict:
    ident = cand["id"]
    browser = MS_BROWSER.get(ident)
    print("═" * 78)
    print(f"{ident}")
    print(f"   {cand['vista']}")
    res: dict = {"id": ident, "ms_browser": browser}
    try:
        primera, mejor, payload = _medir(cand["llamar"])
    except Exception as e:
        print(f"   ⚠ NO SE PUDO MEDIR: {type(e).__name__}: {e}\n")
        res["error"] = f"{type(e).__name__}: {e}"
        return res

    bytes_ = len(json.dumps(payload, default=str, separators=(",", ":")).encode())
    res |= {"ms_primera": round(primera, 1), "ms_servidor": round(mejor, 1),
            "bytes": bytes_}
    print(f"   servidor   : {mejor:8.1f} ms  (primera corrida {primera:.0f} ms)"
          f"  |  payload {bytes_ / 1024:,.1f} KB")

    if browser:
        peaje = browser - mejor
        pct_srv = 100 * mejor / browser
        res |= {"ms_peaje": round(peaje, 1), "pct_servidor": round(pct_srv, 1)}
        print(f"   browser    : {browser:8.1f} ms  (medido de este lado el 2026-08-11)")
        print(f"   PEAJE      : {peaje:8.1f} ms  ← viaje + proxy + serialización")
        print(f"   reparto    : servidor {pct_srv:.0f}%  |  peaje {100 - pct_srv:.0f}%")
        if pct_srv >= 60:
            print("   → manda la QUERY: acá sí conviene optimizar SQL/índices")
        elif pct_srv <= 25:
            print("   → manda el VIAJE: optimizar la query casi no se va a notar; "
                  "lo que paga es hacer MENOS requests")
        else:
            print("   → mitad y mitad: hay que atacar las dos cosas")
    print()
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde", default=_año(), help="inicio del período (YYYY-MM-DD)")
    ap.add_argument("--hasta", default=_hoy(), help="fin del período (YYYY-MM-DD)")
    ap.add_argument("--solo", help="correr un solo endpoint por id")
    ap.add_argument("--json", dest="salida", help="guardar el resultado")
    args = ap.parse_args()

    cands = _candidatos(args.desde, args.hasta)
    if args.solo:
        cands = [c for c in cands if c["id"] == args.solo]
        if not cands:
            print("ids: " + ", ".join(c["id"] for c in _candidatos("", "")))
            return 2

    print(f"\nPEAJE vs SERVIDOR — período {args.desde} → {args.hasta}, moneda ARS")
    print("El 'browser' son promedios reales medidos el 2026-08-11 en la app.\n")

    out = [_correr(c) for c in cands]

    print("═" * 78)
    print("RESUMEN — ¿dónde se va el tiempo?")
    print("═" * 78)
    print(f"{'endpoint':<38}{'browser':>9}{'servidor':>10}{'peaje':>9}{'%srv':>7}")
    for r in out:
        if r.get("error"):
            print(f"{r['id']:<38}{'ERROR':>9}")
            continue
        b = f"{r['ms_browser']:.0f}" if r.get("ms_browser") else "—"
        p = f"{r['ms_peaje']:.0f}" if r.get("ms_peaje") is not None else "—"
        pc = f"{r['pct_servidor']:.0f}%" if r.get("pct_servidor") is not None else "—"
        print(f"{r['id']:<38}{b:>9}{r['ms_servidor']:>10.0f}{p:>9}{pc:>7}")

    medidos = [r for r in out if r.get("ms_peaje") is not None]
    if medidos:
        peajes = sorted(r["ms_peaje"] for r in medidos)
        mediana = peajes[len(peajes) // 2]
        print(f"\nPeaje MEDIANO: {mediana:.0f} ms por request.")
        print("Ese es el piso que paga CUALQUIER request, haga lo que haga el "
              "servidor.\nMultiplicalo por la cantidad de requests que dispara una "
              "vista (Operadores:\n~160 en un minuto de uso) para dimensionar el "
              "problema real.")

    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nGuardado en {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
