"""Backfill del CÓDIGO DE CAJA (CVSA) en `portafolio.assets.codigo_cnv`.

Completa el código de los assets que NO lo tienen, cruzando por ticker contra el
maestro de especies de BYMA (`scripts/data/cvsa_especies.csv`).

⚠️ `codigo_cnv` se llama así por historia, pero lo que guarda es el código de
CVSA (Caja de Valores). Verificado por el user contra los que ya estaban
cargados: es el mismo número. No se renombra la columna — la usa el panel
Manager → ASSETS, `jobs/assets_autofill` y `api/services/assets_sql`, y un
renombre por prolijidad no vale el riesgo.

EL INVARIANTE, igual que `jobs/assets_autofill`:
    NUNCA pisa un valor cargado. Si el maestro dice algo distinto de lo que ya
    hay, NO escribe: lo reporta como CONFLICTO. Un código mal cargado tiene que
    verse en el run, no cambiar solo — la tenencia vieja lo referencia y el que
    lo puso a mano puede tener razón.

Lo que NO está en el maestro no se toca. Los FCI, en particular, quedan exacto
como están: el archivo de BYMA trae pocos fondos y este script no tiene opinión
sobre lo que no encuentra.

Idempotente: la segunda corrida no cambia nada.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.backfill_codigo_cvsa              # DRY-RUN: solo informa
    python -m scripts.backfill_codigo_cvsa --apply      # escribe
    python -m scripts.backfill_codigo_cvsa --conflictos # lista los que difieren
"""
from __future__ import annotations

import argparse
import csv
import pathlib

from core.postgres import get_pool

MAESTRO = pathlib.Path(__file__).parent / "data" / "cvsa_especies.csv"


def _normalizar(t: str | None) -> str:
    """La forma en que se compara un ticker. Un solo lugar, para que el pareo
    del dry-run y el del apply no puedan diferir."""
    return (t or "").strip().upper()


def cargar_maestro() -> dict[str, dict[str, str]]:
    """ticker → {cvsa_id, isin, tipo}.

    El archivo ya viene con una fila por ticker: en el export original cada uno
    se repite por plazo y segmento, siempre con el mismo código (verificado:
    0 tickers con dos códigos distintos sobre 17.125 filas).
    """
    with MAESTRO.open(encoding="utf-8") as f:
        return {_normalizar(r["ticker"]): r for r in csv.DictReader(f) if r.get("ticker")}


def leer_assets() -> list[tuple[str, str | None, str | None]]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT unidad, ticker, codigo_cnv FROM portafolio.assets ORDER BY unidad")
        return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description="Completa el código de CAJA en assets.")
    ap.add_argument("--apply", action="store_true", help="escribir (sin esto, dry-run)")
    ap.add_argument("--conflictos", action="store_true",
                    help="listar los assets cuyo código cargado difiere del maestro")
    args = ap.parse_args()

    maestro = cargar_maestro()
    assets = leer_assets()
    print(f"Maestro CVSA : {len(maestro)} tickers  ({MAESTRO})")
    print(f"Assets        : {len(assets)} filas")
    print()

    completar: list[tuple[str, str, str]] = []   # (unidad, ticker, cvsa_id)
    conflictos: list[tuple[str, str, str, str]] = []
    ya_ok = 0
    sin_ticker = 0
    sin_match: list[tuple[str, str]] = []

    for unidad, ticker, codigo in assets:
        tk = _normalizar(ticker)
        if not tk:
            sin_ticker += 1
            continue
        fila = maestro.get(tk)
        if not fila:
            if not (codigo or "").strip():
                sin_match.append((unidad, tk))
            continue

        nuevo = fila["cvsa_id"].strip()
        actual = (codigo or "").strip()
        if not actual:
            completar.append((unidad, tk, nuevo))
        elif actual == nuevo:
            ya_ok += 1
        else:
            conflictos.append((unidad, tk, actual, nuevo))

    print(f"  A COMPLETAR (estaban vacíos) : {len(completar)}")
    print(f"  Ya estaban y COINCIDEN       : {ya_ok}")
    print(f"  ⚠ CONFLICTO (difieren)       : {len(conflictos)}   ← NO se tocan")
    print(f"  Sin ticker en assets         : {sin_ticker}")
    print(f"  Sin código y sin match       : {len(sin_match)}   ← quedan sin código de CAJA")
    print()

    if completar:
        print("Ejemplos de lo que se completaría:")
        for unidad, tk, nuevo in completar[:10]:
            print(f"    {tk:12} → {nuevo:8}  {unidad[:52]}")
        print()

    if conflictos:
        print("⚠ CONFLICTOS — el código cargado no es el del maestro. Decisión de la mesa:")
        for unidad, tk, actual, nuevo in (conflictos if args.conflictos else conflictos[:10]):
            print(f"    {tk:12} cargado={actual:8} maestro={nuevo:8}  {unidad[:44]}")
        if not args.conflictos and len(conflictos) > 10:
            print(f"    … y {len(conflictos) - 10} más (correr con --conflictos para verlos todos)")
        print()

    if sin_match and not args.apply:
        print("Sin código de CAJA y sin match en el maestro (primeros 15):")
        for unidad, tk in sin_match[:15]:
            print(f"    {tk:12} {unidad[:60]}")
        print()

    if not args.apply:
        print("DRY-RUN: no se escribió nada. Para aplicar: "
              "python -m scripts.backfill_codigo_cvsa --apply")
        return 0

    if not completar:
        print("Nada para escribir.")
        return 0

    # Solo las filas que cambian, por PK, en una sola transacción. Son ~2.300
    # assets: no hace falta batchear, pero se escribe scopeado igual (REGLA #4).
    # La guarda `codigo_cnv IS NULL OR = ''` hace que dos corridas simultáneas no
    # puedan pisarse: la segunda no encuentra fila que actualizar.
    with get_pool().connection() as conn, conn.cursor() as cur:
        for unidad, _tk, nuevo in completar:
            cur.execute(
                "UPDATE portafolio.assets SET codigo_cnv = %s "
                "WHERE unidad = %s AND (codigo_cnv IS NULL OR btrim(codigo_cnv) = '')",
                (nuevo, unidad))
        conn.commit()

    print(f"✅ APLICADO: {len(completar)} assets completados.")
    print(f"   Quedan sin código de CAJA: {len(sin_match)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
