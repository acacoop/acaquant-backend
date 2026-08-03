"""ops_tasa_mav.py — completa `operaciones.operaciones.tasa` para los boletos MAV.

QUÉ RESUELVE
------------
Los boletos de MERCADO MAV (pagarés / cheques) no tienen precio unitario: se
negocian A TASA. Esa tasa no está en ninguna columna — viaja embebida en el
texto de `operaciones.negocio_movimientos.informacion`:

    'Compra [#UAC140770002] 100.000,00@6% (ARS 24hs)'  → 6

Este job la extrae y la persiste en `operaciones.operaciones.tasa`, para que la
tasa quede al lado del boleto y no haya que cruzar tablas ni parsear texto cada
vez que alguien la necesita.

BACKFILL Y MANTENIMIENTO SON EL MISMO CÓDIGO
--------------------------------------------
El job siempre busca boletos MAV **con `tasa` todavía NULL** y les pone la que
corresponda. Correrlo una vez con `--todo` completa el histórico; en cada
corrida del cron sólo toca los boletos nuevos. Es idempotente por construcción:
correrlo dos veces seguidas no hace nada la segunda vez.

POR QUÉ VA ENCADENADO (y no dentro de la ingesta de operaciones)
----------------------------------------------------------------
`jobs.operaciones_informes` escribe el boleto y `jobs.negocio_movimientos`
escribe el texto con la tasa — son dos jobs independientes y no hay garantía de
orden. Calcular la tasa al ingerir la operación fallaría cada vez que el boleto
llega antes que su movimiento. Por eso este job va al final del `negocio_chain`
(`deploy/crontab.txt`), cuando el texto ya está, y por eso reintenta sobre una
ventana en vez de mirar sólo el día.

AMBIGÜEDAD: si un boleto tiene VARIOS movimientos con tasas DISTINTAS, no se
elige ninguna — se deja NULL y se cuenta en el stat `ambiguos`. Adivinar acá
sería meter un número inventado en la base.

Uso:
    python -m jobs.ops_tasa_mav                    # ventana default (7 días)
    python -m jobs.ops_tasa_mav --dias 30
    python -m jobs.ops_tasa_mav --todo             # BACKFILL histórico (una vez)
    python -m jobs.ops_tasa_mav --dry-run          # no escribe, sólo reporta
"""
from __future__ import annotations

import argparse
import time
from datetime import UTC, date, datetime, timedelta

from core.job_runs import JobRunLogger
from core.mav_tasa import parse_informacion
from core.postgres import get_job_pool

# Mismo criterio que `jobs.aranceles`: una ventana de días cubre liquidación
# tardía, reintentos de cuentas que fallaron y correcciones retroactivas, sin
# volver a mirar todo el histórico en cada corrida.
_DIAS_DEFAULT = 7
# Valor de `mercado` de los boletos que nos interesan. Si mañana hay que sumar
# otro mercado que también cotice a tasa, se agrega acá.
_MERCADOS = ("MAV",)
# Lotes del UPDATE + pausa entre lotes (REGLA #4): el backfill histórico no debe
# starvar a los motores ni al resto de la API, que comparten el mismo Postgres.
_BATCH = 500
_PAUSA_S = 0.2


def _pendientes(pool, desde: date | None, hasta: date | None) -> list[dict]:
    """Boletos MAV sin `tasa` + el/los textos de negocio que les corresponden.

    Trae una fila por boleto con el array de `informacion` de sus movimientos,
    para poder detectar el caso ambiguo (varias tasas distintas) sin una segunda
    query. `ix_ops_mercado_concert` cubre el filtro.
    """
    cond = ["o.mercado = ANY(%(mercados)s)", "o.tasa IS NULL"]
    p: dict = {"mercados": list(_MERCADOS)}
    if desde:
        cond.append("o.concertacion >= %(desde)s")
        p["desde"] = desde
    if hasta:
        cond.append("o.concertacion <= %(hasta)s")
        p["hasta"] = hasta
    sql = f"""
        SELECT o.boleto,
               array_agg(n.informacion) FILTER (WHERE n.informacion IS NOT NULL) AS infos
        FROM operaciones.operaciones o
        JOIN operaciones.negocio_movimientos n ON n.comprobante = o.boleto
        WHERE {' AND '.join(cond)}
        GROUP BY o.boleto
    """
    from psycopg.rows import dict_row
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, p)
        return cur.fetchall()


def _resolver(rows: list[dict]) -> tuple[list[tuple[float, str]], int, int]:
    """(actualizaciones, sin_tasa, ambiguos). Una tasa por boleto o ninguna."""
    updates: list[tuple[float, str]] = []
    sin_tasa = ambiguos = 0
    for r in rows:
        tasas = {
            t for info in (r["infos"] or [])
            if (t := parse_informacion(info)["tasa_pct"]) is not None
        }
        if len(tasas) == 1:
            updates.append((tasas.pop(), r["boleto"]))
        elif not tasas:
            sin_tasa += 1
        else:
            ambiguos += 1
    return updates, sin_tasa, ambiguos


def _aplicar(pool, updates: list[tuple[float, str]], run: JobRunLogger) -> int:
    escritos = 0
    for i in range(0, len(updates), _BATCH):
        lote = updates[i:i + _BATCH]
        with pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "UPDATE operaciones.operaciones SET tasa = %s WHERE boleto = %s", lote,
            )
            conn.commit()
        escritos += len(lote)
        if i + _BATCH < len(updates):
            time.sleep(_PAUSA_S)
        run.log(f"  {escritos}/{len(updates)} boletos actualizados")
    return escritos


def main() -> None:
    ap = argparse.ArgumentParser(description="Completa la tasa de los boletos MAV.")
    ap.add_argument("--dias", type=int, default=_DIAS_DEFAULT, help=f"ventana (default {_DIAS_DEFAULT})")
    ap.add_argument("--todo", action="store_true", help="TODO el histórico (backfill inicial)")
    ap.add_argument("--dry-run", action="store_true", help="no escribe; sólo reporta")
    args = ap.parse_args()

    hoy = datetime.now(UTC).date()
    desde, hasta = (None, None) if args.todo else (hoy - timedelta(days=args.dias), hoy)

    with JobRunLogger("ops_tasa_mav") as run:
        run.log(f"ventana: {'TODO el histórico' if args.todo else f'{desde} → {hasta}'}")
        pool = get_job_pool()

        rows = _pendientes(pool, desde, hasta)
        run.log(f"boletos MAV sin tasa (con movimiento): {len(rows)}")

        updates, sin_tasa, ambiguos = _resolver(rows)
        run.log(f"con tasa parseada: {len(updates)} · sin tasa en el texto: {sin_tasa} "
                f"· ambiguos (tasas distintas): {ambiguos}")

        escritos = 0
        if args.dry_run:
            run.log("DRY-RUN: no se escribe nada")
        elif updates:
            escritos = _aplicar(pool, updates, run)
        else:
            run.log("nada para actualizar")

        if sin_tasa:
            run.error(f"{sin_tasa} boleto(s) con movimiento pero sin tasa parseable "
                      f"en `informacion` — formato distinto al conocido")
        if ambiguos:
            run.error(f"{ambiguos} boleto(s) con VARIAS tasas distintas — se dejan NULL "
                      f"a propósito (no se adivina)")

        run.set_stat("pendientes", len(rows))
        run.set_stat("escritos", escritos)
        run.set_stat("sin_tasa", sin_tasa)
        run.set_stat("ambiguos", ambiguos)
        run.set_stat("todo", args.todo)
        run.set_stat("dry_run", args.dry_run)


if __name__ == "__main__":
    main()
