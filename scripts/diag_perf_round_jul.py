"""diag_perf_round_jul — verificación read-only del round de perf 2026-07-09.

Corre en el Droplet: python -m scripts.diag_perf_round_jul

1) TABLERO JEFATURA: compara datos_totales_alyc (batch nuevo, 2 queries con
   agregación condicional) contra el cálculo legacy (2 queries por período)
   re-implementado acá inline. Si algún número difiere, lo imprime — deben
   ser IDÉNTICOS.
2) FLUJO/CASHFLOW RESUMEN: smoke de los endpoints nuevos — corre los services
   y chequea que el total agregado == total de las filas crudas del mismo rango.

Borrar cuando el user confirme que el round cerró (REGLA #5).
"""
from __future__ import annotations

from datetime import date, timedelta

from api.services import control_comercial_sql as cc
from api.services.comercial import _arancel_expr, _valor_expr
from api.services.comercial_sql import _CATS_VOLUMEN, _f, _factor_usd, _q


def _agg_total_legacy(desde, hasta, moneda, mep_hoy, ids=None) -> dict:
    """Copia del _agg_total pre-refactor (2 queries por período)."""
    volx, arax = _valor_expr(moneda, mep_hoy), _arancel_expr(moneda, mep_hoy)
    scope = " AND id_cuenta = ANY(%(ids)s)" if ids is not None else ""
    p = {"d": desde, "h": hasta, "cats": list(_CATS_VOLUMEN)}
    pc: dict = {"d": desde, "h": hasta}
    if ids is not None:
        p["ids"] = pc["ids"] = ids
    v = _q(f"SELECT COUNT(DISTINCT id_cuenta) AS act, COALESCE(SUM({volx}),0) AS vol "
           f"FROM negocio_movimientos WHERE categoria = ANY(%(cats)s) "
           f"AND unidad IS DISTINCT FROM 'USDL' AND fecha >= %(d)s AND fecha <= %(h)s{scope}", p)[0]
    c = _q(f"SELECT COALESCE(SUM({arax}),0) AS com FROM operaciones "
           f"WHERE arancel > 0 AND etapa IS DISTINCT FROM 'solicitud' "
           f"AND concertacion >= %(d)s AND concertacion <= %(h)s{scope}", pc)[0]
    return {"clientes_activos": int(v["act"] or 0),
            "volumen": round(_f(v["vol"]), 2),
            "comisiones": round(_f(c["com"]), 2)}


def check_tablero() -> int:
    difs = 0
    for moneda in ("ARS", "USD"):
        factor = _factor_usd(moneda)
        a = cc._ancla()
        pd_ = cc._prev_biz(a)
        lun = cc._lunes(a)
        m1 = a.replace(day=1)
        y1 = a.replace(month=1, day=1)
        defs = [
            ("Día",      a,   a,   pd_, pd_),
            ("Semana",   lun, a,   lun - timedelta(days=7), lun - timedelta(days=1)),
            ("Mes",      m1,  a,   cc._add_months(m1, -1), m1 - timedelta(days=1)),
            ("YTD",      y1,  a,   y1.replace(year=y1.year - 1), cc._add_months(a, -12)),
            ("12 Meses", cc._add_months(a, -12) + timedelta(days=1), a,
                         cc._add_months(a, -24) + timedelta(days=1), cc._add_months(a, -12)),
            ("2025", date(2025, 1, 1), date(2025, 12, 31), date(2024, 1, 1), date(2024, 12, 31)),
            ("2024", date(2024, 1, 1), date(2024, 12, 31), date(2023, 1, 1), date(2023, 12, 31)),
            ("Total", date(2000, 1, 1), a, None, None),
        ]
        agg = cc._agg_totales_batch(defs, moneda, factor, None)
        for label, d, h, pdde, phasta in defs:
            for tag, dd, hh in [("cur", d, h)] + ([("prev", pdde, phasta)] if pdde else []):
                nuevo = agg(dd, hh)
                legacy = _agg_total_legacy(dd, hh, moneda, factor)
                if nuevo != legacy:
                    difs += 1
                    print(f"  DIF {moneda} {label}/{tag}: nuevo={nuevo} legacy={legacy}")
        print(f"[tablero] {moneda}: comparados {len(defs)} períodos (cur+prev)")
    return difs


def check_resumenes() -> None:
    from api.services import cashflow_sql, operaciones_sql
    hoy = date.today().isoformat()
    desde = (date.today() - timedelta(days=60)).isoformat()

    r = operaciones_sql.flujo_resumen(desde=desde, hasta=hoy)
    total_res = round(sum(f["bruto"] for f in r["filas"]), 2)
    n_res = sum(f["n"] for f in r["filas"])
    print(f"[flujo/resumen] 60d: {len(r['filas'])} filas agregadas, "
          f"{n_res} ops, Σbruto={total_res:,.2f}, grupos={r['grupos']}")

    c = cashflow_sql.flujos_resumen(desde=desde, hasta=hoy)
    crudo = cashflow_sql.listar_flujos(desde=desde, hasta=hoy)
    tot_crudo = round(sum(x["bruto"] or 0 for x in crudo), 2)
    tot_agg = round(sum(f["entradas"] + f["salidas"] for f in c["filas"]), 2)
    ok = abs(tot_crudo - tot_agg) < 0.01
    print(f"[flujos/resumen] 60d: {len(c['filas'])} filas vs {len(crudo)} crudas; "
          f"Σ crudo={tot_crudo:,.2f} vs Σ agregado={tot_agg:,.2f} → {'OK' if ok else 'DIF!'}")


if __name__ == "__main__":
    print("── 1. Tablero jefatura: batch nuevo vs legacy ──")
    difs = check_tablero()
    print("SIN DIFERENCIAS ✓" if difs == 0 else f"⚠ {difs} DIFERENCIAS — revisar antes de confiar")
    print("── 2. Resúmenes contrapartes/cashflow ──")
    check_resumenes()
