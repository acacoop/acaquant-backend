"""Backfill de VENCIMIENTO en Valuaciones.Assets desde Manager.PyRofexInstruments.

Para los assets que tienen VENCIMIENTO vacío ('', 'NO APLICA', null) y que
matchean un ticker de pyRofex, completa VENCIMIENTO con la `maturity` del
instrumento (formato YYYYMMDD → 'YYYY-MM-DD 00:00:00', el mismo que ya usa la
colección y que lee scripts/api_migrate.py). NO toca los que ya tienen valor.
Si no hay match (o el match no tiene maturity), lo deja como está.

Join: PyRofexInstruments[].ticker (ej. 'MERV - XMEV - PS34D - CI') contra un
campo de Assets — por defecto INSTRUMENTO (--match-field). El dry-run reporta
cuántos matches daría cada campo candidato (INSTRUMENTO / unidad / TICKER) para
poder confirmar el join contra datos reales antes de escribir.

DRY-RUN por defecto (no escribe). Para aplicar:
    python -m scripts.backfill_vencimiento_assets                  # dry-run
    python -m scripts.backfill_vencimiento_assets --apply          # escribe
    python -m scripts.backfill_vencimiento_assets --match-field unidad --apply

Idempotente: re-correr tras --apply no encuentra nada (filtra por VENCIMIENTO vacío).
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from datetime import UTC, datetime

from core.mongo import get_mongo_client, get_mongo_client_read

_EMPTY_VALUES: list[str | None] = ["", "NO APLICA", None]
_CANDIDATE_FIELDS = ("INSTRUMENTO", "unidad", "TICKER")
_OUT_FMT = "%Y-%m-%d 00:00:00"
_ACTOR = "backfill_vencimiento_pyrofex"


def _norm(s: str) -> str:
    """Normaliza para match laxo: upper + colapsa whitespace."""
    return re.sub(r"\s+", " ", s.strip()).upper()


def _parse_maturity(raw) -> datetime | None:
    """Convierte maturity de pyRofex (ej. '20271219' o 20271219) → datetime."""
    if raw is None:
        return None
    s = str(raw).strip()
    if len(s) < 8:
        return None
    try:
        return datetime.strptime(s[:8], "%Y%m%d")
    except (ValueError, TypeError):
        return None


def _build_maturity_map(read_db) -> tuple[dict[str, datetime], dict[str, datetime], int]:
    """Lee TODOS los docs de Manager.PyRofexInstruments y arma dos lookups
    ticker→maturity (exacto y normalizado). Devuelve (exacto, norm, n_tickers).
    """
    col = read_db["Manager"]["PyRofexInstruments"]
    exact: dict[str, datetime] = {}
    norm: dict[str, datetime] = {}
    conflictos = 0
    n = 0
    for doc in col.find({}, {"_id": 0, "instruments": 1}):
        for inst in doc.get("instruments", []) or []:
            ticker = (inst.get("ticker") or "").strip()
            mat = _parse_maturity(inst.get("maturity"))
            if not ticker or mat is None:
                continue
            n += 1
            if ticker in exact and exact[ticker] != mat:
                conflictos += 1
            exact.setdefault(ticker, mat)
            norm.setdefault(_norm(ticker), mat)
    if conflictos:
        print(f"  ⚠ {conflictos} tickers con maturity en conflicto (se usó el primero)")
    return exact, norm, n


def _report_formato_existente(read_db) -> None:
    """Muestra muestras y formato de los VENCIMIENTO ya cargados."""
    col = read_db["Valuaciones"]["Assets"]
    cur = col.find(
        {"VENCIMIENTO": {"$nin": _EMPTY_VALUES}},
        {"_id": 0, "VENCIMIENTO": 1},
    ).limit(2000)
    valores = [d.get("VENCIMIENTO", "") for d in cur]
    print(f"\n[formato VENCIMIENTO existente]  (muestra de {len(valores)} no-vacíos)")
    ok_iso = 0
    for v in valores:
        try:
            datetime.strptime(str(v).strip()[:10], "%Y-%m-%d")
            ok_iso += 1
        except (ValueError, TypeError):
            pass
    print(f"  parsean como 'YYYY-MM-DD...': {ok_iso}/{len(valores)}")
    print("  muestras crudas:")
    for v in valores[:8]:
        print(f"    {v!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Escribe (default: dry-run)")
    ap.add_argument("--match-field", default="INSTRUMENTO", choices=_CANDIDATE_FIELDS,
                    help="Campo de Assets a matchear contra el ticker de pyRofex")
    args = ap.parse_args()

    read_db = get_mongo_client_read()

    print("=== backfill VENCIMIENTO en Valuaciones.Assets ===")
    exact, norm, n_tickers = _build_maturity_map(read_db)
    print(f"PyRofexInstruments: {n_tickers} (ticker, maturity) válidos "
          f"→ {len(exact)} tickers únicos")

    _report_formato_existente(read_db)

    # Assets con VENCIMIENTO vacío.
    assets_col = read_db["Valuaciones"]["Assets"]
    proj = {"_id": 0, "unidad": 1, "INSTRUMENTO": 1, "TICKER": 1, "VENCIMIENTO": 1}
    vacios = list(assets_col.find({"VENCIMIENTO": {"$in": _EMPTY_VALUES}}, proj))
    print(f"\nAssets con VENCIMIENTO vacío: {len(vacios)}")

    # Diagnóstico: cuántos matchearían por cada campo candidato.
    print("\n[matches potenciales por campo candidato]")
    for field in _CANDIDATE_FIELDS:
        ex = sum(1 for a in vacios if (a.get(field) or "").strip() in exact)
        nm = sum(1 for a in vacios if _norm(a.get(field) or "") in norm)
        marca = "  <- elegido" if field == args.match_field else ""
        print(f"  {field:<12} exacto={ex:<5} normalizado={nm:<5}{marca}")

    # Propuestas con el campo elegido.
    field = args.match_field
    propuestas: list[tuple[str, str, datetime, str]] = []  # (unidad, valor, mat, modo)
    for a in vacios:
        unidad = a.get("unidad", "")
        valor = (a.get(field) or "").strip()
        if not valor:
            continue
        mat = exact.get(valor) or norm.get(_norm(valor))
        if mat is None:
            continue
        modo = "exacto" if valor in exact else "norm"
        propuestas.append((unidad, valor, mat, modo))

    print(f"\nPropuestas de relleno (match-field={field}): {len(propuestas)}")
    for unidad, valor, mat, modo in propuestas[:15]:
        print(f"  [{modo}] {unidad!r}  {field}={valor!r}  →  VENCIMIENTO={mat.strftime(_OUT_FMT)!r}")
    if len(propuestas) > 15:
        print(f"  ... y {len(propuestas) - 15} más")

    if not args.apply:
        print("\nDRY-RUN — no se escribió nada. Re-correr con --apply para aplicar.")
        return

    # --- APPLY ---
    write_col = get_mongo_client()["Valuaciones"]["Assets"]
    now = datetime.now(UTC)
    escritos = 0
    modos: Counter = Counter()
    for unidad, _valor, mat, modo in propuestas:
        # Re-chequea VENCIMIENTO vacío en el filtro → idempotente y seguro.
        res = write_col.update_one(
            {"unidad": unidad, "VENCIMIENTO": {"$in": _EMPTY_VALUES}},
            {"$set": {
                "VENCIMIENTO": mat.strftime(_OUT_FMT),
                "actualizado_por": _ACTOR,
                "actualizado_at": now,
            }},
        )
        if res.modified_count:
            escritos += 1
            modos[modo] += 1

    print(f"\n✓ APLICADO: {escritos}/{len(propuestas)} assets actualizados "
          f"(exacto={modos['exacto']}, norm={modos['norm']})")
    print("  Re-sincronizar la copia derivada con: python -m scripts.api_migrate assets")


if __name__ == "__main__":
    main()
