"""Diag READ-ONLY: impacto de migrar los ARANCELES de operadores de
NegocioMovimientos (fuente actual, incompleta) a CashFlow.Operaciones (completa).

Compara, por operador, el Σ arancel total y del mes con ambas fuentes — para ver
el delta ANTES de migrar el código de comercial.py (los números los ve la mesa).

OLD (NegocioMovimientos): Σ arancel (arancel>0) — como informe_comercial hoy.
NEW (Operaciones):        Σ arancel (arancel>0, sin Cierre, sin etapa=solicitud).

NO escribe nada. Solo lectura.

Uso:
    python -m scripts.diag_aranceles_operadores_migracion
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.services._negocio_futuros import match_no_futuros
from core.mongo import get_mongo_client_read


def _mes_start() -> str:
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()
    return hoy.replace(day=1).isoformat()


def main() -> None:
    cli = get_mongo_client_read()
    cash = cli["CashFlow"]
    mes_start = _mes_start()

    # operador por cuenta (Comitentes activas)
    op_de_cuenta: dict[str, str] = {}
    nombre_op: dict[str, str] = {}
    for c in cli["Clientes"]["Comitentes"].find(
        {"estado": "Activa"}, {"_id": 0, "id_cuenta": 1, "operador_email": 1, "operador_nombre": 1}
    ):
        idc = str(c.get("id_cuenta") or "").strip()
        if not idc:
            continue
        em = (c.get("operador_email") or "(sin operador)").strip().lower() or "(sin operador)"
        op_de_cuenta[idc] = em
        nombre_op.setdefault(em, c.get("operador_nombre") or em)

    # OLD: NegocioMovimientos
    old: dict[str, dict] = {}
    for d in cash["NegocioMovimientos"].aggregate([
        {"$match": {**match_no_futuros(), "arancel": {"$gt": 0}}},
        {"$group": {"_id": "$id_cuenta",
                    "ar_total": {"$sum": "$arancel"},
                    "ar_mes": {"$sum": {"$cond": [{"$gte": ["$fecha", mes_start]}, "$arancel", 0]}}}},
    ]):
        old[str(d["_id"])] = d

    # NEW: Operaciones (sin Cierre, sin solicitudes FCI)
    new: dict[str, dict] = {}
    for d in cash["Operaciones"].aggregate([
        {"$match": {"arancel": {"$gt": 0},
                    "tipo_operacion": {"$not": {"$regex": "Cierre", "$options": "i"}},
                    "etapa": {"$ne": "solicitud"}}},
        {"$group": {"_id": "$cuenta",
                    "ar_total": {"$sum": "$arancel"},
                    "ar_mes": {"$sum": {"$cond": [{"$gte": ["$concertacion", mes_start]}, "$arancel", 0]}}}},
    ]):
        new[str(d["_id"])] = d

    # roll-up por operador
    agg: dict[str, dict] = {}
    for idc, em in op_de_cuenta.items():
        a = agg.setdefault(em, {"old": 0.0, "new": 0.0, "old_mes": 0.0, "new_mes": 0.0})
        a["old"] += float(old.get(idc, {}).get("ar_total", 0) or 0)
        a["new"] += float(new.get(idc, {}).get("ar_total", 0) or 0)
        a["old_mes"] += float(old.get(idc, {}).get("ar_mes", 0) or 0)
        a["new_mes"] += float(new.get(idc, {}).get("ar_mes", 0) or 0)

    def m(x: float) -> str:
        return f"{x/1e6:.1f}M" if abs(x) >= 1e6 else f"{x/1e3:.0f}k"

    filas = sorted(agg.items(), key=lambda kv: abs(kv[1]["new"] - kv[1]["old"]), reverse=True)
    print(f"Mes desde: {mes_start} | operadores: {len(filas)}\n")
    print(f"{'operador':<28}{'ar_total OLD':>14}{'ar_total NEW':>14}{'Δ':>12}")
    print("-" * 68)
    for em, a in filas[:25]:
        delta = a["new"] - a["old"]
        print(f"{nombre_op.get(em, em)[:27]:<28}{m(a['old']):>14}{m(a['new']):>14}{m(delta):>12}")
    print("-" * 68)
    tot_old = sum(a["old"] for _, a in filas)
    tot_new = sum(a["new"] for _, a in filas)
    tot_old_mes = sum(a["old_mes"] for _, a in filas)
    tot_new_mes = sum(a["new_mes"] for _, a in filas)
    print(f"{'TOTAL':<28}{m(tot_old):>14}{m(tot_new):>14}{m(tot_new - tot_old):>12}")
    print(f"{'TOTAL (mes en curso)':<28}{m(tot_old_mes):>14}{m(tot_new_mes):>14}{m(tot_new_mes - tot_old_mes):>12}")

    # cuentas con arancel en NEW que no estaban en OLD (lo que se ganaría)
    solo_new = [idc for idc in new if idc not in old]
    solo_old = [idc for idc in old if idc not in new]
    print(f"\nCuentas con arancel solo en Operaciones (nuevas): {len(solo_new)}")
    print(f"Cuentas con arancel solo en NegocioMov (se perderían): {len(solo_old)}")

    print("\n(read-only: no se escribió nada)")


if __name__ == "__main__":
    main()
