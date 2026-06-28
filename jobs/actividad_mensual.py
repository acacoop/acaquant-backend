"""actividad_mensual.py — snapshot mensual de CUENTAS ACTIVAS.

"Cuenta activa en el mes M" = id_cuenta con ≥1 boleto operativo
(`_CATS_OPERACIONES`) con fecha dentro del mes calendario M. Es la métrica que
pidió el jefe comercial. Se precalcula offline acá y se persiste SQL-native en
`clientes.actividad_mensual` (1 fila por mes×cuenta) para que la serie histórica
sea instantánea — mismo patrón que `Valuaciones.ConsolidadoCuentas`.

Decomiso Mongo (2026-06-28): el writer dejó de escribir `Clientes.ActividadMensual`
(Mongo) y escribe directo a SQL (`core.pg_mirror.write_native`). El puente
`jobs/sync_postgres.py::sync_actividad_mensual` queda obsoleto → neutralizar.

Punto clave (point-in-time): la ACTIVIDAD sale de `NegocioMovimientos` (dato
inmutable). El operador/segmento se toma de `Clientes.Comitentes` al momento de
correr el job y se CONGELA en el doc. Los meses que se backfillean ahora usan la
asignación actual (es lo único reconstruible); de acá en más, cada corrida
estampa la asignación vigente → historia point-in-time real.

Uso:
    python -m jobs.actividad_mensual                 # mes corriente (ART)
    python -m jobs.actividad_mensual --mes 2026-04   # un mes puntual
    python -m jobs.actividad_mensual --backfill      # todos los meses en NM
    python -m jobs.actividad_mensual --backfill --dry-run
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from typing import Any

from api.services.comercial import _CATS_OPERACIONES
from core.job_runs import JobRunLogger
from core.pg_mirror import write_native


def _mes_actual_art() -> str:
    """Mes calendario actual en horario Argentina (UTC-3), 'YYYY-MM'."""
    return (datetime.now(UTC) - timedelta(hours=3)).date().isoformat()[:7]


def _agregar(cats: list[str], meses: list[str] | None) -> list[dict[str, Any]]:
    """Agrega SQL operaciones.negocio_movimientos por (year_month, id_cuenta).
    `meses=None` = todos."""
    from core.postgres import get_job_pool
    pesif = ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
             "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")
    conds = ["categoria = ANY(%(cats)s)", "id_cuenta IS NOT NULL"]
    p: dict[str, Any] = {"cats": cats}
    if meses:
        p["lo"] = min(meses) + "-01"
        p["hi"] = _mes_siguiente(max(meses)) + "-01"
        conds.append("fecha >= %(lo)s AND fecha < %(hi)s")
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT to_char(fecha, 'YYYY-MM') AS ym, id_cuenta, count(*) AS n_ops, "
            f"SUM({pesif}) AS volumen_ars FROM negocio_movimientos "
            f"WHERE {' AND '.join(conds)} GROUP BY ym, id_cuenta", p)
        return [{"_id": {"ym": ym, "id": idc}, "n_ops": n, "volumen_ars": float(v or 0.0)}
                for ym, idc, n, v in cur.fetchall()]


def _mes_siguiente(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Snapshot mensual de cuentas activas.")
    ap.add_argument("--mes", help="mes puntual YYYY-MM (default: mes corriente ART)")
    ap.add_argument("--backfill", action="store_true", help="todos los meses presentes en NM")
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo informa")
    args = ap.parse_args()

    cats = list(_CATS_OPERACIONES)

    with JobRunLogger("actividad_mensual") as run:
        if args.backfill:
            meses = None
            run.log("Backfill: agregando TODOS los meses presentes en NegocioMovimientos…")
        else:
            mes = args.mes or _mes_actual_art()
            meses = [mes]
            run.log(f"Procesando mes {mes}…")

        filas = _agregar(cats, meses)
        if not filas:
            run.log("⚠ 0 filas agregadas — la colección NO se toca.")
            return

        # Operador/segmento vigente (se congela en el doc) — SQL clientes.comitentes.
        from psycopg.rows import dict_row

        from core.postgres import get_job_pool
        with get_job_pool().connection() as _cn, _cn.cursor(row_factory=dict_row) as _cu:
            _cu.execute(
                "SELECT c.id_cuenta, c.operador_email, o.nombre AS operador_nombre, c.nivel_1 "
                "FROM comitentes c LEFT JOIN operadores o ON o.email = c.operador_email")
            info: dict[str, dict[str, Any]] = {str(c["id_cuenta"]): c for c in _cu.fetchall()}
        docs: list[dict[str, Any]] = []
        meses_tocados: set[str] = set()
        for f in filas:
            ym = f["_id"]["ym"]
            idc = str(f["_id"]["id"])
            meta = info.get(idc, {})
            meses_tocados.add(ym)
            # Columnas de clientes.actividad_mensual (sin `data` jsonb; sin computed_at,
            # que no existe como columna). Operador/segmento CONGELADOS al correr el job.
            docs.append({
                "year_month": ym,
                "id_cuenta": idc,
                "operador_email": meta.get("operador_email"),
                "operador_nombre": meta.get("operador_nombre"),
                "nivel_1": meta.get("nivel_1"),
                "n_ops": f["n_ops"],
                "volumen_ars": round(f.get("volumen_ars", 0.0), 2),
            })

        run.set_stat("meses", sorted(meses_tocados))
        run.set_stat("docs", len(docs))
        run.log(f"{len(docs)} cuentas-activas en {len(meses_tocados)} mes(es): "
                f"{', '.join(sorted(meses_tocados))}")

        if args.dry_run:
            run.log("DRY-RUN: no se escribió nada.")
            return

        # SQL-native idempotente: reemplaza por completo los meses (re)calculados.
        # DELETE de los meses tocados (purga cuentas que dejaron de operar) + upsert.
        from core.postgres import get_job_pool
        with get_job_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM actividad_mensual WHERE year_month = ANY(%s)",
                        (sorted(meses_tocados),))
            conn.commit()
        write_native("actividad_mensual", ["year_month", "id_cuenta"], docs)
        run.log(f"✅ {len(docs)} filas persistidas en SQL clientes.actividad_mensual.")


if __name__ == "__main__":
    main()
