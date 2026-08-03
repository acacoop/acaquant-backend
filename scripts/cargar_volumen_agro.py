"""scripts/cargar_volumen_agro.py — carga MANUAL mensual del volumen del MERCADO agro.

Es el DENOMINADOR del market share de NEGOCIO → OPERACIONES → AGRO → tab "SHARE DE
MERCADO": `share = toneladas nuestras (futuros) / toneladas del mercado`. El numerador
sale solo de `operaciones.operaciones` (automático); el denominador NO tiene feed → se
carga a mano, mes a mes, con este script. Si un mes no está cargado, ese mes NO aparece
en el gráfico de share (no se dibuja 0%).

Tabla: `mercado.volumen_mercado_agro` (PK `periodo` 'YYYY-MM' + `commodity`).
Commodities válidos: SOJA, TRIGO, MAIZ (en MAYÚSCULA, así joinea con las operaciones).
Idempotente: re-cargar el mismo periodo pisa el valor anterior (upsert).

Uso:
    # Ver qué está cargado (todo, ordenado por periodo)
    python -m scripts.cargar_volumen_agro --listar

    # Cargar/corregir un mes (podés pasar solo los commodities que tengas)
    python -m scripts.cargar_volumen_agro --periodo 2026-07 --soja 1250000 --trigo 480000 --maiz 990000

    # Carga masiva desde CSV con cabecera: periodo,commodity,toneladas
    python -m scripts.cargar_volumen_agro --csv volumen_agro.csv
"""
from __future__ import annotations

import argparse
import csv
import re
from datetime import UTC, datetime

from core.postgres import get_pool

COMMODITIES = ("SOJA", "TRIGO", "MAIZ")
_PERIODO_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_UPSERT = (
    "INSERT INTO volumen_mercado_agro (periodo, commodity, toneladas, data) "
    "VALUES (%s, %s, %s, %s) "
    "ON CONFLICT (periodo, commodity) DO UPDATE SET "
    "toneladas = EXCLUDED.toneladas, data = EXCLUDED.data"
)


def _upsert(rows: list[tuple[str, str, float]]) -> int:
    from psycopg.types.json import Jsonb

    ahora = datetime.now(UTC).isoformat()
    payload = [
        (p, c, t, Jsonb({"periodo": p, "commodity": c, "toneladas": t, "cargado_at": ahora}))
        for p, c, t in rows
    ]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(_UPSERT, payload)
    return len(payload)


def _listar() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT periodo, commodity, toneladas FROM volumen_mercado_agro "
            "ORDER BY periodo, commodity"
        )
        filas = cur.fetchall()
    if not filas:
        print("mercado.volumen_mercado_agro está VACÍA — el share de mercado no se puede calcular.")
        return
    por_periodo: dict[str, dict[str, float]] = {}
    for periodo, commodity, toneladas in filas:
        por_periodo.setdefault(periodo, {})[commodity] = float(toneladas or 0)
    print(f"{'PERIODO':<9} " + " ".join(f"{c:>14}" for c in COMMODITIES))
    for periodo in sorted(por_periodo):
        vals = por_periodo[periodo]
        print(
            f"{periodo:<9} "
            + " ".join(f"{vals.get(c, 0):>14,.0f}" if c in vals else f"{'—':>14}" for c in COMMODITIES)
        )
    print(f"\n{len(por_periodo)} periodos cargados.")


def _filas_csv(path: str) -> list[tuple[str, str, float]]:
    rows: list[tuple[str, str, float]] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            periodo = (r.get("periodo") or "").strip()
            commodity = (r.get("commodity") or "").strip().upper()
            crudo = (r.get("toneladas") or "").strip().replace(".", "").replace(",", ".")
            if not _PERIODO_RE.match(periodo):
                raise SystemExit(f"CSV línea {i}: periodo inválido {periodo!r} (esperado YYYY-MM).")
            if commodity not in COMMODITIES:
                raise SystemExit(f"CSV línea {i}: commodity inválido {commodity!r} ({COMMODITIES}).")
            try:
                rows.append((periodo, commodity, float(crudo)))
            except ValueError:
                raise SystemExit(f"CSV línea {i}: toneladas inválidas {crudo!r}.") from None
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Carga manual del volumen del mercado agro (share).")
    ap.add_argument("--listar", action="store_true", help="lista lo cargado y sale.")
    ap.add_argument("--periodo", help="mes 'YYYY-MM' a cargar.")
    ap.add_argument("--soja", type=float, help="toneladas de SOJA operadas por el MERCADO.")
    ap.add_argument("--trigo", type=float, help="toneladas de TRIGO operadas por el MERCADO.")
    ap.add_argument("--maiz", type=float, help="toneladas de MAIZ operadas por el MERCADO.")
    ap.add_argument("--csv", help="archivo CSV con cabecera periodo,commodity,toneladas.")
    args = ap.parse_args()

    if args.listar:
        _listar()
        return 0

    if args.csv:
        rows = _filas_csv(args.csv)
    else:
        if not args.periodo:
            ap.error("pasá --periodo YYYY-MM (o --csv, o --listar).")
        if not _PERIODO_RE.match(args.periodo):
            ap.error(f"periodo inválido {args.periodo!r}: se espera 'YYYY-MM'.")
        rows = [
            (args.periodo, c, v)
            for c, v in (("SOJA", args.soja), ("TRIGO", args.trigo), ("MAIZ", args.maiz))
            if v is not None
        ]
        if not rows:
            ap.error("pasá al menos uno de --soja / --trigo / --maiz.")
        negativas = [c for _, c, v in rows if v < 0]
        if negativas:
            ap.error(f"toneladas negativas en {negativas}.")

    n = _upsert(rows)
    for periodo, commodity, toneladas in rows:
        print(f"OK {periodo} {commodity:<5} {toneladas:,.0f} t")
    print(f"\n{n} filas upserteadas en mercado.volumen_mercado_agro.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
