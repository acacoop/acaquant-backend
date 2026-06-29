"""segmentar_patrimonial.py — re-clasifica `nivel_3` de todas las Comitentes activas.

Lee `tipo_cliente` + `cupo_transaccional_ars` y aplica las reglas de
`api.services.segmentacion.clasificar_nivel_3` (ver
`docs/SEGMENTACION_PATRIMONIAL.md`). Escribe `nivel_3` en SQL `clientes.comitentes`.

SQL-native (decomiso 2026-06-29): antes leía/escribía `Clientes.Comitentes` (Mongo,
deprecado, nadie lo lee — la vista comercial lee SQL). `sync_comitentes` inicializa
`nivel_3` en null y nunca lo pisa; este motor es su ÚNICO escritor.

PJ (Empresa / FCI / Cía. seguros / etc.) requiere la serie UVA del BCRA que
**todavía NO está ingestada** en el repo → mientras tanto se les escribe
`nivel_3 = null`. Cuando se sume `Trading.UVA`, este job los empieza a
clasificar también sin cambios de código (solo se completa `uva` acá).

Idempotente: solo escribe cuando el label nuevo difiere del actual. Dry-run por
default (sin `--apply`) para ver la distribución antes de aplicar.

Uso:
    python -m jobs.segmentar_patrimonial                  # dry-run
    python -m jobs.segmentar_patrimonial --apply          # aplica
    python -m jobs.segmentar_patrimonial --apply --ids 805,820,1003  # solo esas cuentas
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime

from api.services.segmentacion import cargar_ids_contrapartes, clasificar_nivel_3
from core.postgres import get_job_pool


def _get_mep() -> float | None:
    """MEP del momento del run, vía `api.services.macro.get_ultimo_mep`."""
    from api.services.macro import get_ultimo_mep
    mep = get_ultimo_mep().get("mep")
    return float(mep) if mep else None


def _get_uva() -> float | None:
    """Último UVA cargado manualmente en `Trading.UVA` (ver
    `api.services.macro.get_ultimo_uva`)."""
    from api.services.macro import get_ultimo_uva
    return get_ultimo_uva()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ejecutar (default: dry-run)")
    ap.add_argument(
        "--ids",
        default=None,
        help="restringir a estos id_cuenta separados por coma (default: todas las activas)",
    )
    args = ap.parse_args()

    mep = _get_mep()
    uva = _get_uva()
    print(f"MEP = {mep}   UVA = {uva}")
    if not mep:
        print("⚠ Sin MEP → ninguna PH se va a poder clasificar.")
    if not uva:
        print("⚠ Sin UVA (Trading.UVA no ingestado todavía) → ninguna PJ se va a clasificar.")

    contrapartes = cargar_ids_contrapartes()
    print(f"Contrapartes (→ PJ GRANDE): {len(contrapartes)} ids")

    # Lectura SQL del master (clientes.comitentes). `cupo_transaccional_ars` es
    # columna flat (en Mongo era el subdoc cupo.transaccional_ars).
    sql = ("SELECT id_cuenta, tipo_cliente, nivel_3, cupo_transaccional_ars "
           "FROM comitentes WHERE estado = 'Activa'")
    params: list = []
    if args.ids:
        ids = [s.strip() for s in args.ids.split(",") if s.strip()]
        sql += " AND id_cuenta = ANY(%s)"
        params.append(ids)
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        filas = cur.fetchall()

    nuevos: dict[str, str | None] = {}
    actuales: dict[str, str | None] = {}
    n_total = 0
    con_cupo = 0
    tipos: Counter = Counter()
    for idc_raw, tipo_cliente, nivel_3_actual, cupo_ars in filas:
        n_total += 1
        idc = str(idc_raw or "")
        if not idc:
            continue
        if cupo_ars is not None and float(cupo_ars) > 0:
            con_cupo += 1
        tipos[tipo_cliente or "(null)"] += 1
        seg = clasificar_nivel_3(
            tipo_cliente,
            float(cupo_ars) if cupo_ars is not None else None,
            mep=mep,
            uva=uva,
            es_contraparte=idc in contrapartes,
        )
        nuevos[idc] = seg
        actuales[idc] = nivel_3_actual

    # Distribución nueva (para el dry-run y el log).
    dist = Counter(v or "(sin clasificar)" for v in nuevos.values())
    cambios = [(idc, actuales[idc], nuevos[idc]) for idc in nuevos if nuevos[idc] != actuales[idc]]

    print(f"\nCuentas activas evaluadas: {n_total}")
    print(f"  con cupo.transaccional_ars > 0: {con_cupo}")
    print(f"  tipo_cliente: {dict(tipos.most_common())}")
    print("Distribución resultante (nivel_3) — labels reales:")
    for k, v in sorted(dist.items(), key=lambda kv: (kv[0] == "(sin clasificar)", -kv[1])):
        print(f"  {k:<22s} {v:>5d}")
    print(f"\nCambios a aplicar: {len(cambios)}  (cuentas con label distinto al actual)")

    if not args.apply:
        # Mostrar hasta 20 ejemplos de cambios.
        if cambios:
            print("\nPrimeros cambios (id_cuenta: actual → nuevo):")
            for idc, vieja, nueva in cambios[:20]:
                print(f"  {idc:<8s}  {vieja!s:<20s} → {nueva}")
        print("\n(dry-run) — pasar --apply para ejecutar.")
        return

    if not cambios:
        print("Sin cambios. Nada que escribir.")
        return

    now = datetime.now(UTC)
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE comitentes SET nivel_3 = %s, updated_at = %s WHERE id_cuenta = %s",
            [(nueva, now, idc) for idc, _, nueva in cambios])
        conn.commit()
    print(f"\nOK. Actualizadas: {len(cambios)}")


if __name__ == "__main__":
    main()
