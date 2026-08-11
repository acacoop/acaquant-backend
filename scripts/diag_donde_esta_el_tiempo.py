"""diag_donde_esta_el_tiempo.py — las dos preguntas que quedaron abiertas.

Read-only. Cierra los dos pendientes que dejó la medición del 2026-08-11:

**A) `ops/agro` — ¿CUÁL de sus queries se come el segundo?**
Es el único de los nueve endpoints medidos donde manda la query y no el viaje
(1026 ms de servidor). Pero `ops_agro` no hace UNA query: hace ocho contra
`operaciones`, y optimizar la equivocada es tiempo tirado. Esta parte las corre
una por una con los mismos filtros y las ordena por costo.

La sospecha a confirmar o descartar (hipótesis, sin medir): la query de
`nuestro_mensual` es la única SIN filtro de fecha — agrega TODO el histórico de
agro en cada llamada. Y su resultado no depende de `desde`/`hasta`/`commodity`/
`cuenta`, así que las 22 llamadas por minuto de la vista recalculan exactamente
lo mismo 22 veces. Si el desglose lo confirma, la solución es cachearla; si no,
hay que mirar otra.

**B) Los 749 ms de peaje — ¿cuánto es RED y cuánto es el propio backend?**
El "peaje" (browser − service) mete en la misma bolsa cuatro cosas: viaje de
red, el proxy de Next, la serialización a JSON y el overhead de FastAPI. Las dos
últimas pasan DENTRO del Droplet y ya se están midiendo: el middleware
`api/telemetria.py` viene registrando ms por endpoint × hora en
`manager.latencia_endpoints` desde antes de todo esto. Restando:

    browser − middleware = red + proxy de Next  (fuera del Droplet)
    middleware − service = serialización + FastAPI  (dentro)

Y eso decide qué se ataca: si el grueso está FUERA, el trabajo es de
infraestructura (acortar la cadena browser → Vercel → Cloudflare → Droplet);
si está ADENTRO, es payload y serialización.

Uso (Droplet, desde la raíz):
    python -m scripts.diag_donde_esta_el_tiempo
    python -m scripts.diag_donde_esta_el_tiempo --solo A
    python -m scripts.diag_donde_esta_el_tiempo --horas 48
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

# Los mismos promedios de browser que usa scripts/diag_peaje_vs_servidor.py
# (sesión 2026-08-11). Si volvés a medir, actualizá los DOS archivos.
MS_BROWSER: dict[str, float] = {
    "/api/operaciones/comercial/informe-segmento-detalle": 1527.2,
    "/api/operaciones/comercial/operador": 1428.5,
    "/api/operaciones/comercial/informe": 997.9,
    "/api/operaciones/comercial/informe-segmento": 621.7,
    "/api/operaciones/comercial/analisis": 2297.8,
    "/api/operaciones/ops/agro": 920.2,
    "/api/operaciones/ops/resumen": 520.5,
    "/api/operaciones/ops/serie": 448.2,
    "/api/portfolio/total-serie": 950.0,
}

# ms del SERVICE medidos por diag_peaje_vs_servidor.py (mejor de 3, corrida
# 2026-08-11 con los defaults del router ya corregidos).
MS_SERVICE: dict[str, float] = {
    "/api/operaciones/comercial/informe-segmento-detalle": 344.6,
    "/api/operaciones/comercial/operador": 261.1,
    "/api/operaciones/comercial/informe": 171.4,
    "/api/operaciones/comercial/informe-segmento": 94.8,
    "/api/operaciones/comercial/analisis": 340.2,
    "/api/operaciones/ops/agro": 1025.9,
    "/api/operaciones/ops/resumen": 349.5,
    "/api/operaciones/ops/serie": 63.2,
    "/api/portfolio/total-serie": 201.4,
}


def _ms(fn: Callable[[], Any]) -> tuple[float, Any]:
    t0 = time.perf_counter()
    out = fn()
    return (time.perf_counter() - t0) * 1000, out


# ── A) desglose de ops_agro ──────────────────────────────────────────────────


def parte_a(desde: str, hasta: str) -> list[dict]:
    """Corre una por una las queries que `ops_agro` hace en cada llamada.

    Se replican TAL CUAL están en `api/services/operaciones_sql.py::ops_agro`
    (mismo WHERE, mismo GROUP BY, mismos parámetros por defecto de la vista:
    sin cuenta, sin commodity, sin scope, agg MENSUAL). Si esa función cambia,
    este desglose miente — está para decidir qué optimizar, no para quedarse.
    """
    from api.services._sql import _q
    from api.services.operaciones_sql import _TON

    fmt = "YYYY-MM"
    base = "commodity IN ('SOJA', 'TRIGO', 'MAIZ') AND anulado_en IS NULL"
    date_w = f"{base} AND concertacion >= %(desde)s AND concertacion <= %(hasta)s"
    dp = {"desde": desde, "hasta": hasta, "fmt": fmt}

    queries: list[tuple[str, str, dict, str]] = [
        ("serie",
         f"SELECT to_char(concertacion, %(fmt)s) AS p, commodity AS c, SUM({_TON}) AS ton "
         f"FROM operaciones WHERE {date_w} GROUP BY p, commodity", dp,
         "con rango de fechas"),
        ("totales_commodity",
         f"SELECT commodity AS c, SUM({_TON}) AS ton FROM operaciones "
         f"WHERE {date_w} GROUP BY commodity", dp,
         "con rango de fechas"),
        ("por_cuenta",
         f"SELECT denominacion AS d, SUM({_TON}) AS ton, count(*) AS n "
         f"FROM operaciones WHERE {date_w} GROUP BY denominacion ORDER BY ton DESC", dp,
         "con rango de fechas"),
        ("por_instrumento",
         f"SELECT instrumento AS i, SUM({_TON}) AS ton, count(*) AS n "
         f"FROM operaciones WHERE {date_w} GROUP BY instrumento ORDER BY ton DESC", dp,
         "con rango de fechas"),
        ("totales_tipo",
         f"SELECT commodity AS c, COALESCE(tipo_agro, 'FUTURO') AS t, SUM({_TON}) AS ton "
         f"FROM operaciones WHERE {date_w} GROUP BY commodity, COALESCE(tipo_agro, 'FUTURO')",
         dp, "con rango de fechas"),
        ("serie_tipo",
         f"SELECT to_char(concertacion, %(fmt)s) AS p, COALESCE(tipo_agro, 'FUTURO') AS t, "
         f"SUM({_TON}) AS ton FROM operaciones WHERE {date_w} "
         f"GROUP BY p, COALESCE(tipo_agro, 'FUTURO')", dp,
         "con rango de fechas"),
        ("nuestro_mensual",
         f"SELECT to_char(concertacion, 'YYYY-MM') AS p, commodity AS c, SUM({_TON}) AS ton "
         f"FROM operaciones WHERE {base} AND tipo_agro IS DISTINCT FROM 'OPCION' "
         f"GROUP BY p, commodity", {},
         "⚠ SIN rango de fechas — TODO el histórico, en CADA llamada"),
    ]

    print("═" * 78)
    print("A) ops/agro — ¿cuál de sus queries se come el tiempo?")
    print(f"   período {desde} → {hasta}, MENSUAL, sin cuenta ni commodity")
    print("═" * 78)

    out: list[dict] = []
    for nombre, sql, params, nota in queries:
        try:
            ms, filas = _ms(lambda s=sql, p=params: _q(s, p))
            out.append({"query": nombre, "ms": round(ms, 1), "filas": len(filas), "nota": nota})
            print(f"   {nombre:<20}{ms:8.1f} ms   {len(filas):>6} filas   {nota}")
        except Exception as e:
            print(f"   {nombre:<20}   ERROR: {type(e).__name__}: {e}")
            out.append({"query": nombre, "error": f"{type(e).__name__}: {e}"})

    # También el otro insumo del share, que no pega a `operaciones`.
    try:
        from api.services import cashflow_sql as _cf
        ms, m = _ms(_cf.volumen_mercado_agro)
        out.append({"query": "volumen_mercado_agro", "ms": round(ms, 1), "filas": len(m)})
        print(f"   {'volumen_mercado_agro':<20}{ms:8.1f} ms   {len(m):>6} períodos   "
              "(tabla del mercado, no de operaciones)")
    except Exception as e:
        print(f"   volumen_mercado_agro   ERROR: {type(e).__name__}: {e}")

    medidas = [r for r in out if "ms" in r]
    total = sum(r["ms"] for r in medidas)
    print(f"\n   TOTAL de las queries: {total:.0f} ms")
    if medidas and total > 0:
        peor = max(medidas, key=lambda r: r["ms"])
        print(f"   La más cara: {peor['query']} ({peor['ms']:.0f} ms = "
              f"{100 * peor['ms'] / total:.0f}% del total)")
        if peor["query"] == "nuestro_mensual":
            print("   → CONFIRMADO: la que no filtra por fecha es la que pesa. Se puede\n"
                  "     cachear por (scope, nivel5) sin cambiar un solo número: su\n"
                  "     resultado no depende de los filtros que el usuario toca.")
        else:
            print("   → La sospecha NO se confirma: cachear nuestro_mensual no resolvería.\n"
                  "     El trabajo está en la query de arriba.")
    print()
    return out


# ── B) partir el peaje en RED vs BACKEND ─────────────────────────────────────


def parte_b(horas: int) -> list[dict]:
    """Lee `manager.latencia_endpoints` (la telemetría del middleware) y parte el
    peaje en lo que pasa FUERA del Droplet y lo que pasa ADENTRO."""
    from api.services._sql import _q

    print("═" * 78)
    print(f"B) ¿el peaje es RED o es el backend? — últimas {horas} h de telemetría")
    print("═" * 78)

    try:
        rows = _q(
            "SELECT endpoint, SUM(n) AS n, SUM(total_ms) AS total_ms, MAX(max_ms) AS max_ms "
            "FROM manager.latencia_endpoints "
            "WHERE hora >= now() - make_interval(hours => %(h)s) "
            "AND endpoint = ANY(%(eps)s) GROUP BY endpoint",
            {"h": horas, "eps": list(MS_BROWSER)},
        )
    except Exception as e:
        print(f"   ⚠ no pude leer la telemetría: {type(e).__name__}: {e}\n")
        return []

    if not rows:
        print("   Sin datos en esa ventana. La telemetría se llena sola con el uso —\n"
              "   probá con --horas 72, o usá la app un rato y volvé a correr.\n")
        return []

    por_ep = {r["endpoint"]: r for r in rows}
    print(f"{'endpoint':<44}{'browser':>8}{'midware':>8}{'service':>8}"
          f"{'AFUERA':>8}{'ADENTRO':>8}{'n':>6}")
    out: list[dict] = []
    for ep, browser in MS_BROWSER.items():
        r = por_ep.get(ep)
        if not r or not r["n"]:
            print(f"{ep:<44}{browser:>8.0f}{'—':>8}{'(sin tráfico registrado)':>24}")
            continue
        mid = float(r["total_ms"]) / float(r["n"])
        svc = MS_SERVICE.get(ep)
        # AFUERA = lo que el browser espera y el Droplet ni ve (red + proxy Next).
        afuera = browser - mid
        # ADENTRO = lo que el backend suma por encima de la query (serialización,
        # validación, FastAPI). Puede dar negativo si la telemetría promedia
        # llamadas más livianas que las que midió el diag — se muestra igual.
        adentro = (mid - svc) if svc is not None else None
        out.append({"endpoint": ep, "ms_browser": browser, "ms_middleware": round(mid, 1),
                    "ms_service": svc, "ms_afuera": round(afuera, 1),
                    "ms_adentro": round(adentro, 1) if adentro is not None else None,
                    "n": int(r["n"])})
        print(f"{ep:<44}{browser:>8.0f}{mid:>8.0f}"
              f"{(f'{svc:.0f}' if svc is not None else '—'):>8}"
              f"{afuera:>8.0f}{(f'{adentro:.0f}' if adentro is not None else '—'):>8}"
              f"{int(r['n']):>6}")

    if out:
        afueras = sorted(r["ms_afuera"] for r in out)
        med_afuera = afueras[len(afueras) // 2]
        print(f"\n   AFUERA del Droplet (mediana): {med_afuera:.0f} ms por request.")
        print("   Eso es red + proxy de Next. No hay query ni índice que lo toque.")
        adentros = [r["ms_adentro"] for r in out if r["ms_adentro"] is not None]
        if adentros:
            adentros.sort()
            print(f"   ADENTRO, por encima de la query (mediana): "
                  f"{adentros[len(adentros) // 2]:.0f} ms — serialización + FastAPI.")
        print("\n   OJO: el middleware promedia TODAS las llamadas reales del período,\n"
              "   con sus filtros; el service se midió con UN juego de parámetros. Si\n"
              "   una fila da rara, mirá la columna n antes de sacar conclusiones.")
    print()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    hoy = datetime.now(UTC).date()
    ap.add_argument("--desde", default=date(hoy.year, 1, 1).isoformat())
    ap.add_argument("--hasta", default=hoy.isoformat())
    ap.add_argument("--horas", type=int, default=24, help="ventana de telemetría (parte B)")
    ap.add_argument("--solo", choices=["A", "B"], help="correr una sola parte")
    ap.add_argument("--json", dest="salida")
    args = ap.parse_args()

    res: dict = {}
    if args.solo != "B":
        res["agro"] = parte_a(args.desde, args.hasta)
    if args.solo != "A":
        res["peaje"] = parte_b(args.horas)

    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False, default=str)
        print(f"Guardado en {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
