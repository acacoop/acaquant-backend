"""recategorizar_acreencias.py — aplica el criterio AMPLIO de acreencia a los
boletos YA persistidos en `operaciones.negocio_movimientos`.

POR QUÉ HACE FALTA. El categorizador (`api/services/aunesa_negocio.py`) corre en
la INGESTA. El cron `jobs.negocio_movimientos` sólo re-ingiere hoy + 2 días
hábiles, así que ampliar el filtro arregla lo NUEVO pero deja lo histórico como
está: "Stock dividend (DVSE)" sigue en `categoria='otro'` con `op=NULL` para
siempre. Este script recorre lo persistido y lo reclasifica.

NO pega a Aunesa: recalcula desde el `informacion` que ya está guardado, con las
MISMAS funciones que la ingesta (`op_acreencia`) — no puede divergir del criterio
vivo, y re-correrlo después de ampliar `ACREENCIA_OPS` de nuevo alcanza.

REGLA #4 — scopeado + batcheado + idempotente:
  · Scopeado: sólo filas con `categoria` distinta de 'acreencia' cuyo
    `informacion` matchee el patrón (índice-friendly: descarta el 99% en el
    WHERE, no en Python).
  · Batcheado: lotes de `--lote` (default 500) con `--sleep` entre lotes.
  · Idempotente: cortarlo y re-correrlo no rompe nada (el WHERE deja de
    matchear lo ya migrado).
  · No toca importes, cantidades ni fechas — SOLO `categoria` y `op`.

Uso (Droplet):
    python -m scripts.recategorizar_acreencias            # DRY-RUN: qué cambiaría
    python -m scripts.recategorizar_acreencias --commit   # aplica
    python -m scripts.recategorizar_acreencias --commit --lote 200 --sleep 1.0

Después de correrlo conviene recalcular el PnL cacheado (los boletos que ahora
son acreencia entran al pnl_pasivo): `python -m jobs.pnl_totales_precompute`.
"""
from __future__ import annotations

import argparse
import time
from collections import Counter

from api.services.aunesa_negocio import op_acreencia
from core.postgres import get_pool

# Patrón SQL: las familias del criterio amplio. Es un pre-filtro barato — la
# decisión REAL la toma `op_acreencia` en Python, fuente única del criterio.
_PATRON = r"(?i)(dividend|redemption|interest payment)"


def _candidatos(limite: int, offset: int) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fecha, comprobante, categoria, op, informacion "
            "  FROM operaciones.negocio_movimientos "
            " WHERE informacion ~ %(pat)s "
            "   AND coalesce(categoria, '') <> 'acreencia' "
            " ORDER BY fecha, comprobante "
            " LIMIT %(lim)s OFFSET %(off)s",
            {"pat": _PATRON, "lim": limite, "off": offset})
        return cur.fetchall()


def _aplicar(cambios: list[dict]) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE operaciones.negocio_movimientos "
            "   SET categoria = 'acreencia', op = %(op)s "
            " WHERE fecha = %(fecha)s AND comprobante = %(comprobante)s",
            cambios)
        conn.commit()
    return len(cambios)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="aplica (default: dry-run)")
    ap.add_argument("--lote", type=int, default=500, help="filas por lote. Default 500.")
    ap.add_argument("--sleep", type=float, default=0.5, help="pausa entre lotes. Default 0.5s.")
    args = ap.parse_args()

    modo = "COMMIT" if args.commit else "DRY-RUN"
    print(f"\nRecategorización de acreencias — modo {modo}\n")

    por_op: Counter[str] = Counter()
    por_cat_previa: Counter[str] = Counter()
    muestra: list[tuple] = []
    total_cambios = 0
    # En dry-run nada se actualiza → hay que avanzar el offset para no releer
    # el mismo lote. En commit el WHERE deja de matchear lo migrado, así que el
    # offset se queda en 0 y siempre se lee la cabeza de la cola pendiente.
    offset = 0

    while True:
        filas = _candidatos(args.lote, offset)
        if not filas:
            break

        cambios = []
        for fecha, comprobante, categoria, _op_vieja, informacion in filas:
            op_nueva = op_acreencia(informacion or "")
            if not op_nueva:
                continue  # matcheó el pre-filtro SQL pero no el criterio real
            cambios.append({"fecha": fecha, "comprobante": comprobante, "op": op_nueva})
            por_op[op_nueva] += 1
            por_cat_previa[categoria or "(null)"] += 1
            if len(muestra) < 15:
                muestra.append((fecha, categoria, op_nueva, (informacion or "")[:58]))

        if cambios and args.commit:
            total_cambios += _aplicar(cambios)
        else:
            total_cambios += len(cambios)

        if not args.commit or not cambios:
            offset += len(filas)
        print(f"  lote de {len(filas)} filas → {len(cambios)} a reclasificar "
              f"(acumulado {total_cambios})")
        time.sleep(args.sleep)

    if not total_cambios:
        print("\nNada que reclasificar — el histórico ya cumple el criterio actual.")
        return 0

    print(f"\n{total_cambios} boletos {'reclasificados' if args.commit else 'a reclasificar'}")
    print("\n  Por op nueva:")
    for op, n in por_op.most_common():
        print(f"    {op:<22}{n:>8}")
    print("\n  Venían de categoria:")
    for cat, n in por_cat_previa.most_common():
        print(f"    {cat:<22}{n:>8}")

    print("\n  Muestra:")
    for fecha, cat, op, info in muestra:
        print(f"    {fecha!s:<12}{cat or '?':<8}→ {op:<20}{info}")

    if not args.commit:
        print("\n(DRY-RUN — no se escribió nada. Revisá los números y corré con --commit.)")
    else:
        print("\nOK. Recalculá el PnL cacheado: python -m jobs.pnl_totales_precompute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
