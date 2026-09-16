"""READ-ONLY. Nuestras tenencias de FCI, para cruzar contra el export contable.

Herramienta: diag · Solo SELECT, cero escrituras.

EL PUNTO
========

El sistema contable exporta, día por día, la tenencia de cada cuenta en cada FCI
(columnas `Cuenta` / `Unidad` / `Cantidad` / `Importe`) y sobre eso calcula la
comisión. Antes de usar ese archivo para nada hay que contestar UNA pregunta:
**¿nuestra tenencia dice lo mismo?** Si los nominales no coinciden, cualquier
cálculo que hagamos después va a estar bien hecho sobre datos distintos.

Este diag vuelca NUESTRO lado con la misma ventana y el mismo recorte (solo
carteras FCI) para poder poner los dos números al lado.

LO QUE MIRA, Y POR QUÉ CADA COSA
================================

1. **COBERTURA DE FECHAS.** El export trae los 31 días de agosto — fines de semana
   incluidos, porque la comisión se devenga todos los días. Nuestra tenencia es una
   FOTO y puede tener solo días hábiles. Si faltan días, los totales del mes NO
   pueden coincidir y no es un error de nadie: es que miden cosas distintas. Se
   dice primero para no perseguir un descuadre que tiene explicación.

2. **`aum='si'` vs TODO.** Nuestra tenencia marca qué filas cuentan como AuM. No
   sabemos cuál de los dos criterios usa el contable, así que se muestran LOS DOS:
   si el archivo pega con uno, ahí queda aprendida la regla. Elegir uno de antemano
   sería decidir a ojo justo lo que este diag viene a averiguar.

3. **CANTIDAD antes que valuación.** El nominal no depende de precios ni de tipo de
   cambio: si la cantidad no cuadra, el problema es de QUÉ posiciones hay, no de
   cómo se valúan. La valuación va al lado, pero el juez es la cantidad.

Las carteras FCI salen de `core.cartera.FCI` (las dos grafías que conviven en la
base), no de un literal.

Uso:
    python -m scripts.diag_fci_tenencia --desde 2026-08-01 --hasta 2026-08-31
    python -m scripts.diag_fci_tenencia --desde 2026-08-01 --hasta 2026-08-31 --top 40
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from core.cartera import FCI
from core.postgres import get_job_pool


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _f(x) -> float:
    return float(x or 0)


def _m(x, dec: int = 2) -> str:
    return f"{_f(x):,.{dec}f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _t(titulo: str) -> None:
    print(f"\n{'=' * 82}\n{titulo}\n{'=' * 82}")


# `cartera` puede venir con espacios o en minúscula según quién cargó el asset.
_W_FCI = "upper(btrim(coalesce(cartera, ''))) = ANY(%(fci)s)"
_BASE = f"FROM portafolio.tenencia WHERE fecha >= %(d)s AND fecha <= %(h)s AND {_W_FCI}"


def cobertura(p: dict) -> None:
    _t("1. COBERTURA DE FECHAS — ¿tenemos foto de todos los días del rango?")
    rows = _q(f"SELECT DISTINCT fecha {_BASE} ORDER BY fecha", p)
    hay = {r["fecha"] for r in rows}
    d0, d1 = p["d"], p["h"]
    todos = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    faltan = [d for d in todos if d not in hay]
    print(f"  días del rango        : {len(todos)}")
    print(f"  días CON tenencia FCI : {len(hay)}")
    if not faltan:
        print("  ✅ están todos")
        return
    finde = [d for d in faltan if d.weekday() >= 5]
    habiles = [d for d in faltan if d.weekday() < 5]
    print(f"  días SIN foto         : {len(faltan)}  "
          f"({len(finde)} fin de semana, {len(habiles)} hábiles)")
    print("\n  ⚠️  El export contable devenga TODOS los días. Si acá faltan días, el")
    print("      total del mes NO puede dar igual — no es un descuadre, es que una")
    print("      serie tiene más días que la otra. Comparar DÍA A DÍA, no el total.")
    if habiles:
        print(f"\n  hábiles sin foto (revisar): {', '.join(d.isoformat() for d in habiles[:12])}"
              f"{' …' if len(habiles) > 12 else ''}")


def totales(p: dict) -> None:
    _t("2. TOTALES — con `aum='si'` y con TODO (no sabemos cuál usa el contable)")
    for etiqueta, extra in (("aum = 'si'", " AND aum = 'si'"), ("TODAS las filas", "")):
        rows = _q(
            f"SELECT coalesce(moneda, '(sin)') AS moneda, count(*) AS filas, "
            f"count(DISTINCT id_cuenta) AS ctas, count(DISTINCT unidad) AS fondos, "
            f"SUM(cantidad) AS cant, SUM(valuacion) AS val "
            f"{_BASE}{extra} GROUP BY 1 ORDER BY 1", p)
        print(f"\n  ── {etiqueta} ──")
        if not rows:
            print("     (sin filas)")
            continue
        print(f"  {'MONEDA':<10}{'FILAS':>9}{'CTAS':>7}{'FONDOS':>8}"
              f"{'Σ CANTIDAD':>22}{'Σ VALUACIÓN':>22}")
        for r in rows:
            print(f"  {r['moneda']:<10}{r['filas']:>9}{r['ctas']:>7}{r['fondos']:>8}"
                  f"{_m(r['cant'], 6):>22}{_m(r['val']):>22}")


def por_fecha(p: dict) -> None:
    _t("3. POR FECHA — la comparación que sí vale (misma ventana, día contra día)")
    rows = _q(
        f"SELECT fecha, count(*) AS filas, count(DISTINCT id_cuenta) AS ctas, "
        f"SUM(cantidad) AS cant, SUM(valuacion) AS val "
        f"{_BASE} AND aum = 'si' GROUP BY fecha ORDER BY fecha", p)
    if not rows:
        print("  (sin filas)")
        return
    print(f"  {'FECHA':<13}{'FILAS':>8}{'CTAS':>7}{'Σ CANTIDAD':>24}{'Σ VALUACIÓN':>22}")
    for r in rows:
        print(f"  {r['fecha'].isoformat():<13}{r['filas']:>8}{r['ctas']:>7}"
              f"{_m(r['cant'], 6):>24}{_m(r['val']):>22}")


def por_cuenta(p: dict, top: int) -> None:
    _t(f"4. POR CUENTA — top {top} por valuación (Σ del rango, aum='si')")
    rows = _q(
        f"SELECT id_cuenta, max(cuenta) AS cuenta, count(DISTINCT unidad) AS fondos, "
        f"SUM(cantidad) AS cant, SUM(valuacion) AS val "
        f"{_BASE} AND aum = 'si' GROUP BY id_cuenta "
        f"ORDER BY SUM(valuacion) DESC NULLS LAST LIMIT %(top)s", {**p, "top": top})
    if not rows:
        print("  (sin filas)")
        return
    print("  ⚠️  Σ CANTIDAD suma nominales de fondos DISTINTOS y de varios días: sirve")
    print("      para cruzar contra el mismo agregado del export, no como magnitud.\n")
    print(f"  {'CUENTA':<42}{'FONDOS':>7}{'Σ CANTIDAD':>22}{'Σ VALUACIÓN':>20}")
    for r in rows:
        nom = (r["cuenta"] or r["id_cuenta"] or "")[:41]
        print(f"  {nom:<42}{r['fondos']:>7}{_m(r['cant'], 4):>22}{_m(r['val']):>20}")


def por_fondo(p: dict, top: int) -> None:
    _t(f"5. POR FONDO — top {top} (para cruzar contra la columna `Unidad`)")
    rows = _q(
        f"SELECT unidad, count(DISTINCT id_cuenta) AS ctas, "
        f"SUM(cantidad) AS cant, SUM(valuacion) AS val "
        f"{_BASE} AND aum = 'si' GROUP BY unidad "
        f"ORDER BY SUM(valuacion) DESC NULLS LAST LIMIT %(top)s", {**p, "top": top})
    if not rows:
        print("  (sin filas)")
        return
    print(f"  {'UNIDAD':<54}{'CTAS':>6}{'Σ CANTIDAD':>20}")
    for r in rows:
        print(f"  {(r['unidad'] or '')[:53]:<54}{r['ctas']:>6}{_m(r['cant'], 4):>20}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde", required=True, help="YYYY-MM-DD (el del export)")
    ap.add_argument("--hasta", required=True, help="YYYY-MM-DD (el del export)")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    p = {"d": date.fromisoformat(a.desde), "h": date.fromisoformat(a.hasta),
         "fci": [x.upper() for x in FCI]}

    print(f"TENENCIAS FCI · {a.desde} → {a.hasta}")
    print(f"carteras consideradas: {', '.join(FCI)}  (core.cartera.FCI)")
    cobertura(p)
    totales(p)
    por_fecha(p)
    por_cuenta(p, a.top)
    por_fondo(p, a.top)
    print("\n" + "=" * 82)
    print("READ-ONLY: este script no escribió nada.")
    print("=" * 82)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
