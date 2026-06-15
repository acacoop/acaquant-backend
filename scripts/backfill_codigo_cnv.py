"""backfill_codigo_cnv.py — carga masiva del CÓDIGO CNV a Valuaciones.Assets.

Lee un Excel/CSV con 2 columnas: A) Código (CNV)  B) Nombre. Rellena el campo
`CODIGO_CNV` en Valuaciones.Assets matcheando por `TICKER` (UPPERCASE, la fuente
de verdad). OJO: el 'Nombre' del export suele ser una DESCRIPCIÓN que arranca
con el ticker (ej. 'S30S2-L.T.GOB.NAC...'), así que se prueba el string completo
y el prefijo antes del primer '-' (ver `_candidatos`). El DRY-RUN mide cuántos
matchean de verdad antes de escribir.

Detección de columnas: busca por nombre de header (codigo/cnv y nombre/ticker,
sin acentos, case-insensitive); si no las encuentra usa las 2 primeras columnas
(A y B) y avisa cuáles usó. Override con --col-codigo / --col-nombre.

Seguro (REGLA #4):
  * DRY-RUN por default — NO escribe; muestra matcheados / no encontrados / inválidos.
  * Idempotente: UPDATE por `unidad` (sin upsert) → re-correrlo no duplica ni crea.
  * Carga TODO el catálogo de Assets en memoria 1 vez y hace UN bulk_write
    (no una query por fila del Excel, aunque tenga miles).
  * El código se guarda como STRING (preserva ceros a la izquierda).

Uso:
    python -m scripts.backfill_codigo_cnv --file scripts/codigos_cnv.xlsx            # DRY-RUN
    python -m scripts.backfill_codigo_cnv --file scripts/codigos_cnv.xlsx --out rep.txt
    python -m scripts.backfill_codigo_cnv --file scripts/codigos_cnv.xlsx --apply    # escribe
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import UTC, datetime

import pandas as pd

from core.postgres import get_pool


def _norm(s: str) -> str:
    """minúsculas sin acentos, para comparar headers."""
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).strip().lower()


def _candidatos(nombre: str) -> list[str]:
    """Tickers candidatos a partir del 'Nombre' del Excel. El Nombre suele ser
    una descripción que ARRANCA con el ticker, ej. 'S30S2-L.T.GOB.NAC...' → el
    ticker es el prefijo antes del primer '-'. Probamos en orden: (1) string
    completo, (2) prefijo antes del primer '-'. Devuelve UPPERCASE."""
    n = nombre.strip().upper()
    cands = [n]
    if "-" in n:
        pref = n.split("-", 1)[0].strip()
        if pref and pref != n:
            cands.append(pref)
    return cands


def _pick_col(cols: list[str], claves: tuple[str, ...], fallback_idx: int) -> str:
    for c in cols:
        n = _norm(c)
        if any(k in n for k in claves):
            return c
    return cols[fallback_idx]


def _emit(lines: list[str], out: str | None) -> None:
    txt = "\n".join(lines)
    print(txt)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(txt + "\n")
        print(f"\n(reporte volcado a {out})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="Excel (.xlsx) o CSV con columnas Código | Nombre(=TICKER)")
    ap.add_argument("--sheet", default=0, help="hoja del Excel (nombre o índice, default 0)")
    ap.add_argument("--col-codigo", default=None, help="nombre exacto de la columna de código (override)")
    ap.add_argument("--col-nombre", default=None, help="nombre exacto de la columna de nombre/ticker (override)")
    ap.add_argument("--out", default=None, help="vuelca el reporte a un archivo")
    ap.add_argument("--apply", action="store_true", help="escribe en Mongo (sin esto, DRY-RUN)")
    args = ap.parse_args()

    # 1) Leer el archivo (todo como string → preserva ceros a la izquierda).
    if args.file.lower().endswith(".csv"):
        df = pd.read_csv(args.file, dtype=str)
    else:
        df = pd.read_excel(args.file, sheet_name=args.sheet, dtype=str)
    cols = list(df.columns)
    if len(cols) < 2:
        print(f"ERROR: el archivo tiene {len(cols)} columna(s); se esperaban ≥2 (Código y Nombre).")
        return 1

    col_cod = args.col_codigo or _pick_col(cols, ("codig", "cnv"), 0)
    col_nom = args.col_nombre or _pick_col(cols, ("nombre", "ticker"), 1)

    rep: list[str] = []
    modo = "APPLY (escribe)" if args.apply else "DRY-RUN (no escribe)"
    rep.append(f"== backfill CODIGO_CNV — {modo} ==")
    rep.append(f"archivo: {args.file}  ({len(df)} filas)")
    rep.append(f"columna CÓDIGO = {col_cod!r}   columna NOMBRE/TICKER = {col_nom!r}")

    # 2) Catálogo desde SQL portafolio.assets: TICKER(upper) → [unidad, ...].
    por_ticker: dict[str, list[str]] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT unidad, ticker FROM portafolio.assets "
                    "WHERE ticker IS NOT NULL AND ticker <> ''")
        for unidad, ticker in cur.fetchall():
            tk = str(ticker or "").strip().upper()
            if tk:
                por_ticker.setdefault(tk, []).append(unidad)

    # 3) Recorrer las filas y armar los updates (codigo, unidad).
    ops: list[tuple] = []
    n_match_assets = 0
    no_encontrados: list[str] = []
    invalidos = 0
    vistos: set[str] = set()
    ahora = datetime.now(UTC)

    for _, row in df.iterrows():
        codigo = (row.get(col_cod) or "").strip() if pd.notna(row.get(col_cod)) else ""
        nombre = (row.get(col_nom) or "").strip() if pd.notna(row.get(col_nom)) else ""
        if not codigo or not nombre:
            invalidos += 1
            continue
        cands = _candidatos(nombre)
        if cands[0] in vistos:
            continue  # duplicado en el Excel → ya procesado
        vistos.add(cands[0])
        unidades = None
        for c in cands:
            unidades = por_ticker.get(c)
            if unidades:
                break
        if not unidades:
            no_encontrados.append(f"{nombre}  (cod {codigo})")
            continue
        for unidad in unidades:
            ops.append((codigo, ahora, unidad))
            n_match_assets += 1

    rep.append("")
    rep.append(f"tickers únicos en el Excel:        {len(vistos)}")
    rep.append(f"filas inválidas (código/nombre vacío): {invalidos}")
    rep.append(f"tickers SIN asset (no se cargan):  {len(no_encontrados)}")
    rep.append(f"assets a actualizar:               {n_match_assets}")

    if no_encontrados:
        rep.append("\n-- tickers del Excel sin asset en Valuaciones.Assets (muestra 30) --")
        rep.extend("   " + x for x in no_encontrados[:30])
        if len(no_encontrados) > 30:
            rep.append(f"   … +{len(no_encontrados) - 30} más")

    if not args.apply:
        rep.append("\nDRY-RUN: no se escribió nada. Revisá y corré con --apply.")
        _emit(rep, args.out)
        return 0

    if ops:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "UPDATE portafolio.assets SET codigo_cnv=%s, "
                "actualizado_por='backfill_codigo_cnv', actualizado_at=%s WHERE unidad=%s", ops)
            n = cur.rowcount
            conn.commit()
        rep.append(f"\nAPLICADO: {n} assets actualizados ({len(ops)} updates).")
    else:
        rep.append("\nNADA que aplicar (0 matches).")
    _emit(rep, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
