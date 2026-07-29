"""Backfill one-shot de MESA DE DINERO desde la planilla de la mesa (Libro2.xlsx).

Carga `scripts/mesa_dinero_backfill.csv` (186 ops, 2026-07-01 → 2026-07-29,
exportado de la planilla real) en `operaciones.mesa_dinero` + los 4 traders en
`operaciones.mesa_dinero_traders`.

Decisiones:
- Los MONTOS y el RESULTADO se insertan TAL CUAL vienen de la planilla (NO se
  recalculan con vn×px/100: en varias filas el PX está redondeado y el monto es
  el real de la operación — la planilla es la fuente de verdad del histórico).
- pct = resultado / monto_compra (derivado, igual que el service).
- Filas sin patas (ej. "Pase OPS"): vn/px/montos NULL, resultado manual.
- Idempotente por guarda: si ya existen filas con creado_por='backfill:libro2'
  NO inserta de nuevo (protege ediciones posteriores del user). Para re-correr
  desde cero: borrar esas filas a mano primero.
- Observaciones que no sean "Mesa" ni un operador de `clientes.operadores` se
  REPORTAN como warning pero se insertan igual (el user las corrige en la vista).

Uso (Droplet): python -m scripts.backfill_mesa_dinero
"""
from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from api.services.mesa_dinero import _audit
from core.postgres import get_pool

CSV_PATH = Path(__file__).with_name("mesa_dinero_backfill.csv")
ORIGEN = "backfill:libro2"


def _num(x: str):
    x = (x or "").strip()
    return float(x) if x else None


def main() -> None:
    filas = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    print(f"CSV: {len(filas)} operaciones ({filas[0]['fecha']} → {filas[-1]['fecha']})")

    with get_pool().connection() as conn, conn.cursor() as cur:
        # Guarda de idempotencia
        cur.execute(
            "SELECT count(*) FROM operaciones.mesa_dinero WHERE creado_por = %s", (ORIGEN,)
        )
        ya = cur.fetchone()[0]
        if ya:
            print(f"ABORT: el backfill ya corrió ({ya} filas con creado_por={ORIGEN!r}). "
                  "No se inserta nada.")
            return

        # Catálogo de traders
        traders = sorted({f["trader"] for f in filas if f["trader"]})
        for t in traders:
            cur.execute(
                "INSERT INTO operaciones.mesa_dinero_traders (nombre, creado_por, creado_at) "
                "VALUES (%s, %s, %s) ON CONFLICT (nombre) DO NOTHING",
                (t, ORIGEN, datetime.now(UTC)),
            )
        print(f"Traders asegurados en catálogo: {', '.join(traders)}")

        # Observaciones vs operadores comerciales
        cur.execute("SELECT DISTINCT nombre FROM clientes.operadores")
        operadores = {r[0] for r in cur.fetchall()}
        obs_planilla = {f["observacion"] for f in filas if f["observacion"]}
        sin_match = sorted(o for o in obs_planilla if o != "Mesa" and o not in operadores)
        if sin_match:
            print(f"WARNING: observaciones que NO matchean clientes.operadores "
                  f"(se insertan igual, corregir en la vista): {', '.join(sin_match)}")

        # Inserción
        now = datetime.now(UTC)
        for f in filas:
            monto_c = _num(f["monto_compra"])
            resultado = _num(f["resultado"]) or 0.0
            pct = resultado / monto_c if monto_c else None
            cur.execute(
                """
                INSERT INTO operaciones.mesa_dinero
                    (fecha, trader, activo, vn_compra, px_compra, monto_compra,
                     vn_venta, px_venta, monto_venta, resultado, pct,
                     cliente, observacion, creado_por, creado_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (f["fecha"], f["trader"], f["activo"] or None,
                 _num(f["vn_compra"]), _num(f["px_compra"]), monto_c,
                 _num(f["vn_venta"]), _num(f["px_venta"]), _num(f["monto_venta"]),
                 resultado, pct, f["cliente"] or None, f["observacion"] or None,
                 ORIGEN, now),
            )
        conn.commit()

        # Control: resultado total insertado vs suma del CSV
        cur.execute(
            "SELECT count(*), round(sum(resultado)::numeric, 2) "
            "FROM operaciones.mesa_dinero WHERE creado_por = %s", (ORIGEN,)
        )
        n, total = cur.fetchone()
        print(f"OK: {n} operaciones insertadas. Resultado acumulado: {total:,}")

    _audit(ORIGEN, "backfill", "mesa_dinero", {"filas": len(filas), "traders": traders})


if __name__ == "__main__":
    main()
