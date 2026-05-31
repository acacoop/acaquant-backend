"""Backfill de TICKER y VENCIMIENTO para assets de CARTERA FINANCIAMIENTO.

Estas unidades traen el dato en el propio string de `unidad`, ej:
    '[#MAV250950038] #MAV250950038 Nro. 103990 Vto. 25/09/2025'
  - TICKER       = lo que está entre corchetes  → '#MAV250950038'
  - VENCIMIENTO  = lo que viene tras 'Vto.'      → '25/09/2025' (DD/MM/YYYY)
                   se guarda como 'YYYY-MM-DD 00:00:00' (formato de Assets).

Solo rellena campos VACÍOS ('', 'NO APLICA', null) — no pisa valores ya cargados.
Tolera el valor de cartera nuevo ('FINANCIAMIENTO') y legacy ('CARTERA FINANCIAMIENTO').

DRY-RUN por defecto (no escribe). Para aplicar:
    python -m scripts.backfill_financiamiento_assets            # dry-run
    python -m scripts.backfill_financiamiento_assets --apply    # escribe

Tras aplicar, re-sincronizar la copia derivada:
    python -m scripts.api_migrate assets
"""
from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime

from core.mongo import get_mongo_client, get_mongo_client_read

_EMPTY_VALUES: list[str | None] = ["", "NO APLICA", None]
_ACTOR = "backfill_financiamiento_assets"
_OUT_FMT = "%Y-%m-%d 00:00:00"

_RE_TICKER = re.compile(r"\[([^\]]+)\]")
_RE_VTO = re.compile(r"Vto\.?\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE)


def _ticker_de(unidad: str) -> str | None:
    m = _RE_TICKER.search(unidad)
    return m.group(1).strip() if m else None


def _vencimiento_de(unidad: str) -> str | None:
    m = _RE_VTO.search(unidad)
    if not m:
        return None
    dd, mm, yyyy = (int(x) for x in m.groups())
    try:
        return datetime(yyyy, mm, dd).strftime(_OUT_FMT)
    except ValueError:
        return None


def _vacio(v) -> bool:
    return v in _EMPTY_VALUES


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Escribe (default: dry-run)")
    args = ap.parse_args()

    read_db = get_mongo_client_read()
    col = read_db["Valuaciones"]["Assets"]
    filtro = {"CARTERA": {"$in": ["FINANCIAMIENTO", "CARTERA FINANCIAMIENTO"]}}

    docs = list(col.find(filtro, {"_id": 0, "unidad": 1, "TICKER": 1, "VENCIMIENTO": 1}))
    print("=== backfill TICKER/VENCIMIENTO · CARTERA FINANCIAMIENTO ===")
    print(f"docs en la cartera: {len(docs)}\n")

    # (unidad, set_fields, ticker_parse_ok, vto_parse_ok)
    propuestas: list[tuple[str, dict]] = []
    sin_ticker = sin_vto = 0
    for d in docs:
        unidad = d.get("unidad", "")
        set_fields: dict[str, str] = {}

        if _vacio(d.get("TICKER")):
            t = _ticker_de(unidad)
            if t:
                set_fields["TICKER"] = t
            else:
                sin_ticker += 1
        if _vacio(d.get("VENCIMIENTO")):
            v = _vencimiento_de(unidad)
            if v:
                set_fields["VENCIMIENTO"] = v
            else:
                sin_vto += 1

        if set_fields:
            propuestas.append((unidad, set_fields))

    print(f"unidades a completar: {len(propuestas)}")
    print(f"  (sin TICKER parseable: {sin_ticker} · sin VENCIMIENTO parseable: {sin_vto})\n")
    for unidad, sf in propuestas[:15]:
        print(f"  {unidad!r}")
        print(f"      → {sf}")
    if len(propuestas) > 15:
        print(f"  ... y {len(propuestas) - 15} más")

    if not args.apply:
        print("\nDRY-RUN — no se escribió nada. Re-correr con --apply para aplicar.")
        return

    # --- APPLY ---
    write_col = get_mongo_client()["Valuaciones"]["Assets"]
    now = datetime.now(UTC)
    escritos = 0
    for unidad, sf in propuestas:
        # Re-chequea vacío en el filtro de cada campo → idempotente, no pisa.
        cond = {"unidad": unidad}
        for campo in sf:
            cond[campo] = {"$in": _EMPTY_VALUES}
        res = write_col.update_one(
            cond,
            {"$set": {**sf, "actualizado_por": _ACTOR, "actualizado_at": now}},
        )
        escritos += res.modified_count

    print(f"\n✓ APLICADO: {escritos}/{len(propuestas)} unidades actualizadas")
    print("  Re-sincronizar la copia derivada con: python -m scripts.api_migrate assets")


if __name__ == "__main__":
    main()
