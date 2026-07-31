"""scripts/import_acavalores_retorno.py — carga del informe "ACA VALORES RETORNO TOTAL".

Carga el Excel "OP Aca Valores FCI - <mes>.xls" (informe de operaciones bursátiles
del fondo ACA R.TOTAL) en SQL `operaciones.acavalores_retorno`. Es la ÚNICA vía de
alta de estos datos — no hay motor ni API que los genere y NO es diario. Alimenta la
tab "ACA VALORES RETORNO TOTAL" de Mesa de Dinero.

Formato del archivo (fijo): título en la fila 0, header de 2 filas (Fondo/Operación…
+ Nombre/Descripción…), una fila en blanco, y a partir de ahí las operaciones. 21
columnas (ver mapeo en `_COLS`). El `.xls` es formato binario legacy → requiere
`xlrd` (además de pandas).

Idempotente por PERIODO ('YYYY-MM' de la fecha de concertación): re-importar un mes
BORRA y reinserta solo ese mes; los otros meses no se tocan. Cortarlo y re-correrlo
no duplica.

Uso (desde la raíz del repo, con .env que tenga POSTGRES_URI):
    python -m scripts.import_acavalores_retorno "C:/ruta/OP Aca Valores FCI - jul2026.xls"
    python -m scripts.import_acavalores_retorno <archivo.xls> --dry-run   # no escribe, solo resume
"""
from __future__ import annotations

import argparse
import math
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from core.postgres import get_pool

# Índice de columna en el Excel → nombre de columna en la tabla. El orden es fijo
# (informe estándar de Aunesa). Ver sql/schema.sql :: operaciones.acavalores_retorno.
_COLS: list[tuple[int, str]] = [
    (0, "fondo"),
    (1, "operacion"),
    (2, "fecha_concertacion"),
    (3, "plazo"),
    (4, "fecha_liquidacion"),
    (5, "papel_numero"),
    (6, "papel_descripcion"),
    (7, "depositario"),
    (8, "valor_nominal"),
    (9, "moneda_simbolo"),
    (10, "precio"),
    (11, "bruto"),
    (12, "gastos_total"),
    (13, "isin"),
    (14, "agente_descripcion"),
    (15, "liq_total"),
    (16, "papel_codigo"),
    (17, "liq_neto"),
    (18, "liq_precio"),
    (19, "fondo_neto"),
    (20, "tipo_especie"),
]
_DATE_COLS = {"fecha_concertacion", "fecha_liquidacion"}
_NUM_COLS = {"valor_nominal", "precio", "bruto", "gastos_total",
             "liq_total", "liq_neto", "liq_precio", "fondo_neto"}
_TEXT_COLS = {"fondo", "operacion", "papel_numero", "papel_descripcion",
              "depositario", "moneda_simbolo", "isin", "agente_descripcion",
              "papel_codigo", "tipo_especie"}


def _is_nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _to_text(v) -> str | None:
    if _is_nan(v):
        return None
    s = str(v).strip()
    return s or None


def _to_num(v) -> float | None:
    if _is_nan(v) or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> int | None:
    n = _to_num(v)
    return round(n) if n is not None else None


def _to_date(v) -> date | None:
    if _is_nan(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    # Formato del informe: dd/mm/yyyy. Fallback a parseo flexible de pandas.
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=True).date()
    except Exception:
        return None


def _find_data_start(raw: pd.DataFrame) -> int:
    """Primera fila de DATOS. El header arranca en la fila con 'Fondo' en la col 0
    (título, header, subheader, blanco, luego datos → +3). Fallback: fila 4."""
    for i in range(min(15, len(raw))):
        if _to_text(raw.iat[i, 0]) == "Fondo":
            return i + 3
    return 4


def _parse(path: Path) -> list[dict]:
    raw = pd.read_excel(path, header=None)
    if raw.shape[1] < 21:
        raise SystemExit(
            f"El archivo tiene {raw.shape[1]} columnas, se esperaban 21. "
            "¿Es el informe 'OP Aca Valores FCI'? Abortando sin tocar la base.")
    start = _find_data_start(raw)
    filas: list[dict] = []
    for _, r in raw.iloc[start:].iterrows():
        fondo = _to_text(r.iat[0])
        # Fila vacía / footer / total → se ignora (sin col 0 no es una operación).
        if fondo is None:
            continue
        row: dict = {}
        for idx, name in _COLS:
            v = r.iat[idx]
            if name in _DATE_COLS:
                row[name] = _to_date(v)
            elif name == "plazo":
                row[name] = _to_int(v)
            elif name in _NUM_COLS:
                row[name] = _to_num(v)
            else:
                row[name] = _to_text(v)
        fc = row.get("fecha_concertacion")
        row["periodo"] = f"{fc.year:04d}-{fc.month:02d}" if fc else None
        filas.append(row)
    return filas


_INSERT = """
INSERT INTO operaciones.acavalores_retorno
    (periodo, fondo, operacion, fecha_concertacion, plazo, fecha_liquidacion,
     papel_numero, papel_descripcion, depositario, valor_nominal, moneda_simbolo,
     precio, bruto, gastos_total, isin, agente_descripcion, liq_total, papel_codigo,
     liq_neto, liq_precio, fondo_neto, tipo_especie, archivo, importado_en)
VALUES
    (%(periodo)s, %(fondo)s, %(operacion)s, %(fecha_concertacion)s, %(plazo)s,
     %(fecha_liquidacion)s, %(papel_numero)s, %(papel_descripcion)s, %(depositario)s,
     %(valor_nominal)s, %(moneda_simbolo)s, %(precio)s, %(bruto)s, %(gastos_total)s,
     %(isin)s, %(agente_descripcion)s, %(liq_total)s, %(papel_codigo)s, %(liq_neto)s,
     %(liq_precio)s, %(fondo_neto)s, %(tipo_especie)s, %(archivo)s, %(importado_en)s)
"""


def _cargar(filas: list[dict], archivo: str, dry_run: bool) -> None:
    periodos = sorted({f["periodo"] for f in filas if f["periodo"]})
    if not periodos:
        raise SystemExit("No se pudo derivar ningún período (fechas vacías). Abortando.")

    ahora = datetime.now(UTC)
    for f in filas:
        f["archivo"] = archivo
        f["importado_en"] = ahora

    total_vn = sum(f["valor_nominal"] or 0 for f in filas)
    print(f"Archivo:  {archivo}")
    print(f"Períodos: {', '.join(periodos)}")
    print(f"Filas:    {len(filas)}  ·  Σ Valor Nominal = {total_vn:,.2f}")

    if dry_run:
        print("\n[dry-run] No se escribió nada. Muestra de las primeras 3 filas:")
        for f in filas[:3]:
            print(f"  {f['fecha_concertacion']} · {f['operacion']} · "
                  f"{f['papel_descripcion']} · {f['agente_descripcion']} · "
                  f"VN={f['valor_nominal']}")
        return

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM operaciones.acavalores_retorno WHERE periodo = ANY(%s)",
            (periodos,),
        )
        borradas = cur.rowcount
        cur.executemany(_INSERT, filas)
        conn.commit()
    print(f"\nOK — reemplazadas {borradas} fila(s) previa(s) de {periodos}; "
          f"insertadas {len(filas)}.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Carga el informe 'OP Aca Valores FCI' en operaciones.acavalores_retorno.")
    ap.add_argument("archivo", help="ruta al .xls del informe.")
    ap.add_argument("--dry-run", action="store_true",
                    help="parsea y resume, sin escribir en la base.")
    args = ap.parse_args()

    path = Path(args.archivo)
    if not path.exists():
        raise SystemExit(f"No existe el archivo: {path}")

    filas = _parse(path)
    if not filas:
        raise SystemExit("No se encontraron filas de operaciones en el archivo.")
    _cargar(filas, archivo=path.name, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
