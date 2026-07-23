"""backfill_fci_ticker.py — completa el `ticker` (nombre del fondo) de los assets
FCI que lo tienen vacío, derivándolo de la `unidad` (core.cafci.nombre_fci).

Problema que resuelve: en /aum → FCI la fila se rotula con el `ticker` del asset;
los FCI con `ticker` vacío salen SIN NOMBRE y, peor, los que comparten "vacío" se
FUSIONAN en un solo renglón (ej. las dos clases de "ACA Valores Retorno Total"
sumaban $90.527M en una fila muda). El nombre ya vive en la `unidad`
(`[1047] CAFCI518-1047 - Argenfunds Ahorro Pesos - Clase B`) → se completa solo.

Scopeado + idempotente (REGLA #4): toca SOLO assets con cartera FCI y ticker
vacío cuyo nombre se puede derivar; re-correrlo no cambia nada. Es un UPDATE sobre
una tabla chica (catálogo de cientos de filas), no un scan de datos de mercado.

El `emisor` NO se toca: no es determinístico desde la unidad y hay valores mal
cargados a mano (ej. ACA Valores con emisor='SCHRODER'). Los que quedan sin emisor
—o cuya unidad no matchea el formato FCI— se listan al final para revisión manual
(y los vigila el control `fci_incompletos` de jobs/controles_datos.py).

Uso:
    python -m scripts.backfill_fci_ticker            # DRY-RUN (no escribe)
    python -m scripts.backfill_fci_ticker --apply    # aplica los updates
"""
from __future__ import annotations

import argparse

from core.cafci import nombre_fci
from core.postgres import get_pool

_FCI_CARTERAS = ("FCI", "CARTERA FCI")


def _rows_fci_sin_ticker() -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT unidad, emisor FROM portafolio.assets "
            "WHERE cartera = ANY(%s) AND (ticker IS NULL OR trim(ticker) = '') "
            "ORDER BY unidad",
            (list(_FCI_CARTERAS),))
        return [{"unidad": u, "emisor": e} for u, e in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="aplica (default: dry-run)")
    args = ap.parse_args()

    rows = _rows_fci_sin_ticker()
    if not rows:
        print("Nada que hacer: no hay assets FCI con ticker vacío.")
        return 0

    a_completar: list[tuple[str, str]] = []   # (unidad, ticker_nuevo)
    no_parseables: list[str] = []
    for r in rows:
        nombre = nombre_fci(r["unidad"])
        if nombre:
            a_completar.append((r["unidad"], nombre))
        else:
            no_parseables.append(r["unidad"])

    print(f"== FCI con ticker vacío: {len(rows)} "
          f"· completables: {len(a_completar)} · no-parseables: {len(no_parseables)} ==\n")

    print("── Se completaría `ticker` ──")
    for unidad, nombre in a_completar:
        print(f"  {unidad}")
        print(f"      ticker → {nombre!r}")
    sin_emisor = [u for u, _ in a_completar
                  if not (next((r["emisor"] for r in rows if r["unidad"] == u), "") or "").strip()]
    if sin_emisor:
        print(f"\n── Ojo: {len(sin_emisor)} de esos ADEMÁS no tienen emisor "
              "(no se auto-completa — cargar a mano en Manager → Assets) ──")
        for u in sin_emisor:
            print(f"  {u}")
    if no_parseables:
        print(f"\n── {len(no_parseables)} con cartera FCI pero unidad sin formato CAFCI "
              "(revisar a mano) ──")
        for u in no_parseables:
            print(f"  {u}")

    if not args.apply:
        print(f"\nDRY-RUN. Con --apply se completaría el ticker de {len(a_completar)} asset(s).")
        return 0

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE portafolio.assets SET ticker = %s "
            "WHERE unidad = %s AND (ticker IS NULL OR trim(ticker) = '')",
            [(nombre, unidad) for unidad, nombre in a_completar])
        n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(a_completar)
        conn.commit()
    print(f"\n✅ Aplicado: ticker completado en {n} asset(s) FCI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
