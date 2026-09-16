"""Backfill: completa `mesa_dinero.observacion_email` desde el NOMBRE ya cargado.

Herramienta: backfill one-shot · Idempotente · Scopeado · REGLA #4

QUÉ HACE
========

`operaciones.mesa_dinero.observacion` guarda el NOMBRE del operador. Desde el
2026-09-16 la fila guarda además su EMAIL (`observacion_email`), que es la PK del
operador y lo único con lo que se puede repartir plata sin riesgo (REGLA #9 — ver
el comentario de la columna en `sql/schema.sql`). Este script completa el email de
las filas que ya estaban cargadas.

POR QUÉ ES SEGURO (REGLA #4)
============================

· SCOPEADO   — solo filas con observación de operador y `observacion_email IS NULL`.
               Usa el índice parcial `ix_mesa_dinero_obs_email`. NO es un `--full`.
· MEDIDO     — `scripts/diag_produccion_operador` (2026-09-16) midió el universo
               ANTES: 651 filas totales, 92 con operador, y las 92 resuelven a un
               único email. No hay huérfanas ni ambiguas. Es una tabla de carga
               manual de ~650 filas, no un escaneo de prod.
· IDEMPOTENTE— el `WHERE observacion_email IS NULL` hace que la segunda corrida no
               toque nada. Se puede correr las veces que haga falta.
· AMBIGUOS   — si un nombre resolviera a MÁS de un operador, la fila se DEJA en
               NULL y se lista. Elegir uno «por defecto» le daría la plata a la
               persona equivocada con un total que igual cierra: preferimos que no
               le sume a nadie y que se vea.

Dry-run por defecto: sin `--apply` no escribe.

Uso:
    python -m scripts.backfill_mesa_observacion_email            # dry-run
    python -m scripts.backfill_mesa_observacion_email --apply
"""
from __future__ import annotations

import argparse

from api.services.mesa_dinero import OBSERVACION_MESA
from core.postgres import get_job_pool

# Filas a completar: tienen operador en `observacion` y todavía no tienen email.
_PENDIENTES = """
SELECT o.observacion,
       count(*) AS filas,
       (SELECT count(*) FROM clientes.operadores c
         WHERE upper(btrim(c.nombre)) = upper(btrim(o.observacion))) AS n_match,
       (SELECT min(c.email) FROM clientes.operadores c
         WHERE upper(btrim(c.nombre)) = upper(btrim(o.observacion))) AS email
FROM operaciones.mesa_dinero o
WHERE o.observacion_email IS NULL
  AND o.observacion IS NOT NULL
  AND btrim(o.observacion) <> ''
  AND btrim(o.observacion) <> %(mesa)s
GROUP BY o.observacion
ORDER BY 2 DESC
"""

# Un UPDATE por NOMBRE (7 nombres, no 92 filas) y solo donde el match es ÚNICO.
# El `n_match = 1` va dentro del propio WHERE: si mañana aparece un homónimo, la
# condición deja de cumplirse sola y el script no imputa nada — no hace falta que
# alguien se acuerde de revisarlo.
_UPDATE = """
UPDATE operaciones.mesa_dinero o
SET observacion_email = c.email
FROM clientes.operadores c
WHERE upper(btrim(c.nombre)) = upper(btrim(o.observacion))
  AND o.observacion_email IS NULL
  AND o.observacion IS NOT NULL
  AND btrim(o.observacion) <> %(mesa)s
  AND (SELECT count(*) FROM clientes.operadores c2
        WHERE upper(btrim(c2.nombre)) = upper(btrim(o.observacion))) = 1
"""


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="escribe (sin esto es dry-run)")
    args = ap.parse_args()

    filas = _q(_PENDIENTES, {"mesa": OBSERVACION_MESA})
    if not filas:
        print("✅ nada pendiente: todas las filas con operador ya tienen email.")
        return 0

    ok = [f for f in filas if f["n_match"] == 1]
    mal = [f for f in filas if f["n_match"] != 1]
    print(f"{'OBSERVACIÓN':<34}{'FILAS':>7}  EMAIL")
    for f in ok:
        print(f"{(f['observacion'] or '')[:33]:<34}{f['filas']:>7}  {f['email']}")
    for f in mal:
        motivo = "SIN OPERADOR" if f["n_match"] == 0 else f"AMBIGUO ({f['n_match']})"
        print(f"{(f['observacion'] or '')[:33]:<34}{f['filas']:>7}  ⚠️  {motivo} → queda NULL")

    n_ok = sum(f["filas"] for f in ok)
    print(f"\na completar: {n_ok} fila(s) en {len(ok)} nombre(s)")
    if mal:
        print(f"SIN completar: {sum(f['filas'] for f in mal)} fila(s) — no le suman a nadie")

    if not args.apply:
        print("\nDRY-RUN: no se escribió nada. Volvé a correr con --apply.")
        return 0

    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_UPDATE, {"mesa": OBSERVACION_MESA})
        n = cur.rowcount
        conn.commit()
    print(f"\n✅ {n} fila(s) actualizadas.")

    resto = _q(_PENDIENTES, {"mesa": OBSERVACION_MESA})
    pend = sum(f["filas"] for f in resto)
    print(f"pendientes tras el backfill: {pend}"
          f"{' (los ambiguos/sin operador, a propósito)' if pend else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
