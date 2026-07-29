"""Diag PERF del INFORME comercial (read-only) — mide las 4 queries pesadas.

Correr en el Droplet:  python -m scripts.diag_comercial_informe

Por qué existe: el Informe (Operaciones → Comercial → Informe) agrega EN VIVO sobre
`operaciones` (~377MB) y `negocio_movimientos` (~123MB) sin caché. La memoria de
perf (perf-db-audit.md) las dejó marcadas como "próximo tier, sin EXPLAIN". Esto
mide el plan real (seq scan vs índice, buffers, tiempo) para decidir con datos.

Todo es read-only (solo EXPLAIN ANALYZE de SELECTs). Compara el peor caso (sin
filtro, histórico completo = lo que dispara el load por defecto) contra un período
acotado [desde, hasta], para ver cuánto pesa NO tener piso de fecha.
"""
from __future__ import annotations

import time
from datetime import date

from api.services.comercial import _CATS_VOLUMEN
from api.services.comercial_sql import _PESIF
from core.postgres import get_pool

LINEA = "─" * 78


def _explain(cur, titulo: str, sql: str, params=None) -> None:
    print(f"\n{LINEA}\n▶ {titulo}\n{LINEA}")
    t0 = time.perf_counter()
    cur.execute(f"EXPLAIN (ANALYZE, BUFFERS) {sql}", params)
    plan = "\n".join(r[0] for r in cur.fetchall())
    ms = (time.perf_counter() - t0) * 1000
    print(plan)
    # Pistas rápidas de lectura para el que no lee planes de Postgres:
    low = plan.lower()
    flags = []
    if "seq scan" in low:
        flags.append("⚠ hay SEQ SCAN (lee la tabla entera)")
    if "external merge" in low or "disk" in low:
        flags.append("⚠ ORDER/GROUP fue a DISCO (work_mem chico)")
    if not flags:
        flags.append("✓ sin seq scan / sin disco en este plan")
    print("   " + " · ".join(flags))
    print(f"⏱  wall total (explain+exec): {ms:.0f}ms")


def main() -> None:
    cats = list(_CATS_VOLUMEN)
    hoy = date.today()
    mes_ini = hoy.replace(day=1).isoformat()
    # Ventana acotada de comparación: mes en curso.
    desde = mes_ini
    hasta = hoy.isoformat()

    # ── Q_ROLLUP: el corazón del ranking (informe_comercial → _rollup_por_cuenta).
    # Dos CTEs (vol sobre negocio_movimientos, ar sobre operaciones) + FULL OUTER JOIN.
    def rollup(lb_vol: str, lb_ar: str, ub_vol: str, ub_ar: str, lo_vol: str, lo_ar: str) -> str:
        w_vol = f"unidad IS DISTINCT FROM 'USDL' AND categoria = ANY(%(cats)s){ub_vol}{lo_vol}"
        w_ar = f"arancel > 0 AND etapa IS DISTINCT FROM 'solicitud'{ub_ar}{lo_ar}"
        return (
            f"WITH vol AS (SELECT id_cuenta, "
            f"  SUM(CASE WHEN {lb_vol} THEN {_PESIF} ELSE 0 END) AS vol_total, "
            f"  SUM(CASE WHEN fecha >= %(mes_ini)s THEN {_PESIF} ELSE 0 END) AS vol_mes, "
            f"  SUM(CASE WHEN {lb_vol} THEN 1 ELSE 0 END) AS n_ops, "
            f"  SUM(CASE WHEN fecha >= %(mes_ini)s THEN 1 ELSE 0 END) AS n_ops_mes "
            f"  FROM negocio_movimientos WHERE {w_vol} GROUP BY id_cuenta), "
            f"ar AS (SELECT id_cuenta, "
            f"  SUM(CASE WHEN {lb_ar} THEN arancel ELSE 0 END) AS ar_total, "
            f"  SUM(CASE WHEN concertacion >= %(mes_ini)s THEN arancel ELSE 0 END) AS ar_mes "
            f"  FROM operaciones WHERE {w_ar} GROUP BY id_cuenta) "
            f"SELECT COALESCE(v.id_cuenta, a.id_cuenta) AS id_cuenta, "
            f"  COALESCE(v.vol_total,0), COALESCE(v.vol_mes,0), COALESCE(v.n_ops,0), "
            f"  COALESCE(v.n_ops_mes,0), COALESCE(a.ar_total,0), COALESCE(a.ar_mes,0) "
            f"FROM vol v FULL OUTER JOIN ar a ON v.id_cuenta = a.id_cuenta"
        )

    with get_pool().connection() as conn, conn.cursor() as cur:
        # (1) PEOR CASO: sin filtro, sin desde/hasta → histórico completo (load por defecto).
        _explain(
            cur,
            "1) ROLLUP ranking — PEOR CASO (sin filtro, histórico completo)",
            rollup("TRUE", "TRUE", "", "", "", ""),
            {"cats": cats, "mes_ini": mes_ini},
        )
        # (2) Mismo rollup pero acotado al mes en curso [desde, hasta].
        _explain(
            cur,
            "2) ROLLUP ranking — ACOTADO al mes en curso [desde, hasta]",
            rollup(
                "fecha >= %(desde)s", "concertacion >= %(desde)s",
                " AND fecha <= %(hasta)s", " AND concertacion <= %(hasta)s",
                " AND fecha >= %(lo)s", " AND concertacion >= %(lo)s",
            ),
            {"cats": cats, "mes_ini": mes_ini, "desde": desde, "hasta": hasta,
             "lo": min(desde, mes_ini)},
        )
        # (3) Operativas por segmento (informe_cuentas_por_segmento): JOIN nm × comitentes,
        #     count(DISTINCT) acotado al mes [mes_ini, hasta].
        _explain(
            cur,
            "3) OPERATIVAS por segmento — JOIN nm × comitentes, count(DISTINCT), mes acotado",
            "SELECT COALESCE(c.nivel_1, '(sin segmentar)') AS segmento, "
            "count(DISTINCT nm.id_cuenta) AS n "
            "FROM negocio_movimientos nm JOIN comitentes c ON c.id_cuenta = nm.id_cuenta "
            "AND c.estado = 'Activa' "
            "WHERE nm.categoria = ANY(%(cats)s) AND nm.unidad IS DISTINCT FROM 'USDL' "
            "AND nm.fecha >= %(mes)s AND nm.fecha <= %(corte)s "
            "GROUP BY COALESCE(c.nivel_1, '(sin segmentar)')",
            {"cats": cats, "mes": mes_ini, "corte": hasta},
        )
        # (4) Detalle segmento (informe_segmento_detalle): agregación de arancel por cuenta,
        #     acotada [desde, hasta]. Usa el índice parcial ix_ops_arancel_*.
        _explain(
            cur,
            "4) DETALLE arancel por cuenta — operaciones, período acotado",
            "SELECT id_cuenta, "
            "SUM(CASE WHEN concertacion >= %(desde)s THEN arancel ELSE 0 END) AS ar_total, "
            "SUM(CASE WHEN concertacion >= %(mes_ini)s THEN arancel ELSE 0 END) AS ar_mes "
            "FROM operaciones WHERE arancel > 0 AND etapa IS DISTINCT FROM 'solicitud' "
            "AND concertacion <= %(corte)s AND concertacion >= %(desde)s GROUP BY id_cuenta",
            {"desde": desde, "mes_ini": mes_ini, "corte": hasta},
        )

    print(f"\n{LINEA}")
    print("LECTURA: comparar (1) vs (2). Si (1) tarda mucho más y muestra SEQ SCAN,")
    print("el costo es agregar el histórico completo sin piso de fecha en el load por")
    print("defecto. Opciones (a decidir con estos números): (a) cachear el endpoint,")
    print("(b) índice parcial que cubra el predicado de volumen, (c) acotar el default.")
    print(LINEA)


if __name__ == "__main__":
    main()
