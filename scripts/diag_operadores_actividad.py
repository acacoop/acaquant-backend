"""Diagnóstico read-only: actividad comercial (última op / estado) comparando
la fuente VIEJA (CashFlow.NegocioMovimientos) vs la NUEVA (CashFlow.Operaciones).

Sirve para ver el impacto de la migración de fuente en `comercial.py`
(analisis_comercial / resumen_por_operador) ANTES de confiar en el cambio:
cuántas cuentas pasan de DORMIDA/NUEVA → ACTIVA/ENFRIANDOSE al usar la fuente
más completa.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.diag_operadores_actividad                 # toda la mesa
    python -m scripts.diag_operadores_actividad --operador a@b.com
    python -m scripts.diag_operadores_actividad --dias-activa 45 --dias-dormida 90

NO escribe nada. Solo lee y reporta.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime, timedelta

from api.db import get_db_cashflow, get_db_clientes
from api.services._negocio_futuros import match_no_futuros
from api.services.comercial import TODOS, estado_comercial

_CATS = (
    "compra", "venta", "suscripcion_fci", "solicitud_suscripcion_fci",
    "caucion_tom_ap", "caucion_col_ap", "rescate_fci", "solicitud_rescate_fci",
)


def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _ids_operador(operador: str) -> list[str]:
    q = {"estado": "Activa"} if operador == TODOS else {
        "operador_email": operador, "estado": "Activa"}
    return sorted(
        str(d["id_cuenta"])
        for d in get_db_clientes()["Comitentes"].find(q, {"_id": 0, "id_cuenta": 1})
        if d.get("id_cuenta")
    )


def _ult_op_negocio(ids: list[str], todos: bool) -> dict[str, str]:
    """Última op por cuenta — fuente VIEJA (NegocioMovimientos, filtrada por categoría)."""
    match: dict = {"categoria": {"$in": list(_CATS)}, **match_no_futuros()}
    if not todos:
        match["id_cuenta"] = {"$in": ids}
    out: dict[str, str] = {}
    for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
        {"$match": match},
        {"$group": {"_id": "$id_cuenta", "ult": {"$max": "$fecha"}}},
    ]):
        if d.get("_id") and d.get("ult"):
            out[str(d["_id"])] = str(d["ult"])[:10]
    return out


def _ult_op_operaciones(ids: list[str], todos: bool) -> dict[str, str]:
    """Última op por cuenta — fuente NUEVA (Operaciones, cualquier boleto)."""
    match: dict = {}
    if not todos:
        match["cuenta"] = {"$in": ids}
    out: dict[str, str] = {}
    for d in get_db_cashflow()["Operaciones"].aggregate([
        {"$match": match},
        {"$group": {"_id": "$cuenta", "ult": {"$max": "$concertacion"}}},
    ]):
        if d.get("_id") and d.get("ult"):
            out[str(d["_id"])] = str(d["ult"])[:10]
    return out


def _estado_de(ult: str | None, hoy: date, dias_activa: int, dias_dormida: int) -> str:
    dias = (hoy - date.fromisoformat(ult)).days if ult else None
    dias_win = dias if (dias is not None and dias <= dias_dormida) else None
    return estado_comercial(dias_win, ult is not None, dias_activa, dias_dormida)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--operador", default=TODOS, help="operador_email o __todos__ (default)")
    ap.add_argument("--dias-activa", type=int, default=45)
    ap.add_argument("--dias-dormida", type=int, default=90)
    ap.add_argument("--ejemplos", type=int, default=15, help="cuántos cambios listar")
    args = ap.parse_args()

    hoy = _hoy_art()
    todos = args.operador == TODOS
    ids = _ids_operador(args.operador)
    print(f"Operador: {args.operador} | cuentas activas: {len(ids)} | "
          f"ventana activa≤{args.dias_activa} dormida>{args.dias_dormida} | hoy ART {hoy}")
    if not ids:
        print("Sin cuentas. Fin.")
        return

    viejo = _ult_op_negocio(ids, todos)
    nuevo = _ult_op_operaciones(ids, todos)

    c_viejo: Counter[str] = Counter()
    c_nuevo: Counter[str] = Counter()
    cambios: list[tuple[str, str, str, str | None, str | None]] = []
    for idc in ids:
        ev = _estado_de(viejo.get(idc), hoy, args.dias_activa, args.dias_dormida)
        en = _estado_de(nuevo.get(idc), hoy, args.dias_activa, args.dias_dormida)
        c_viejo[ev] += 1
        c_nuevo[en] += 1
        if ev != en:
            cambios.append((idc, ev, en, viejo.get(idc), nuevo.get(idc)))

    orden = ["ACTIVA", "ENFRIANDOSE", "DORMIDA", "NUEVA"]
    print("\nEstado            NegocioMov   Operaciones   Δ")
    print("-" * 50)
    for e in orden:
        v, n = c_viejo.get(e, 0), c_nuevo.get(e, 0)
        print(f"{e:<16}{v:>10}{n:>14}{n - v:>+6}")
    print("-" * 50)
    print(f"Cuentas que cambian de estado: {len(cambios)} / {len(ids)}")

    if cambios:
        print(f"\nPrimeros {min(args.ejemplos, len(cambios))} cambios "
              f"(id_cuenta: viejo→nuevo | últ.op negocio / últ.op operaciones):")
        for idc, ev, en, uv, un in cambios[: args.ejemplos]:
            print(f"  {idc:<12} {ev:>11} → {en:<11}  | {uv or '—'} / {un or '—'}")


if __name__ == "__main__":
    main()
