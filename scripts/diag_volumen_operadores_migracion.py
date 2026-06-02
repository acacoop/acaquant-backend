"""Diag READ-ONLY — verifica los supuestos de la fase 2 (migrar VOLUMEN de
operadores de NegocioMovimientos a CashFlow.Operaciones).

Tres chequeos (REGLA #2, sin asumir):
  1. ¿El `mep` es consistente por fecha en NegocioMov? (un valor por día). Si varía,
     estampar uno por día es aproximación.
  2. ¿Qué signo tiene `bruto` en Operaciones? (define si hay que usar abs()).
  3. Delta del volumen OLD (NegocioMov, pesificado per-boleto) vs NEW (Operaciones,
     pesificando USD con el mapa {fecha: mep} de NegocioMov) por operador.

NEW: ARS = Σ abs(bruto) directo; USD = Σ abs(bruto) × mep_del_dia. Excluye Cierre
y etapa=solicitud. Esto PREVÉ exactamente lo que daría estampar el mep.

NO escribe nada. Solo lectura.

Uso:
    python -m scripts.diag_volumen_operadores_migracion
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read

_CATS_VOL = ["compra", "venta", "suscripcion_fci", "solicitud_suscripcion_fci",
             "caucion_tom_ap", "caucion_col_ap"]
# match_no_futuros: excluye los futuros DLR (unidad USDL) del volumen de NegocioMov.
_NO_FUT = {"unidad": {"$ne": "USDL"}}
_OPS_NO_CIERRE = {"$not": {"$regex": "Cierre", "$options": "i"}}
# pesify NegocioMov: ARS→abs(importe); USD→abs(importe)×mep del boleto.
_PESIF = {"$cond": [{"$eq": ["$moneda", "ARS"]}, {"$abs": "$importe"},
                    {"$multiply": [{"$abs": "$importe"}, {"$ifNull": ["$mep", 0]}]}]}


def _m(x: float) -> str:
    return f"{x/1e9:.2f}B" if abs(x) >= 1e9 else (f"{x/1e6:.1f}M" if abs(x) >= 1e6 else f"{x/1e3:.0f}k")


def main() -> None:
    cli = get_mongo_client_read()
    cash = cli["CashFlow"]
    mov = cash["NegocioMovimientos"]
    ops = cash["Operaciones"]

    # ── 1) MEP por fecha consistente? ──
    print("══ 1) Consistencia del mep por fecha (NegocioMovimientos) ══")
    mep_map: dict[str, float] = {}
    incons = 0
    ejemplos_incons = []
    for d in mov.aggregate([
        {"$match": {"mep": {"$gt": 0}}},
        {"$group": {"_id": "$fecha", "meps": {"$addToSet": {"$round": ["$mep", 2]}},
                    "uno": {"$first": "$mep"}}},
    ]):
        mep_map[d["_id"]] = float(d["uno"])
        if len(d["meps"]) > 1:
            incons += 1
            if len(ejemplos_incons) < 5:
                ejemplos_incons.append((d["_id"], sorted(d["meps"])[:4]))
    print(f"  fechas con mep: {len(mep_map)} | fechas con >1 mep distinto: {incons}")
    for f, ms in ejemplos_incons:
        print(f"    {f}: {ms}")

    # ── 2) Signo de bruto en Operaciones ──
    print("\n══ 2) Signo de `bruto` en Operaciones (sin Cierre, sin solicitud) ══")
    base = {"tipo_operacion": _OPS_NO_CIERRE, "etapa": {"$ne": "solicitud"}}
    print(f"  bruto > 0 : {ops.count_documents({**base, 'bruto': {'$gt': 0}})}")
    print(f"  bruto < 0 : {ops.count_documents({**base, 'bruto': {'$lt': 0}})}")
    print(f"  bruto == 0: {ops.count_documents({**base, 'bruto': 0})}")
    print(f"  bruto null: {ops.count_documents({**base, 'bruto': None})}")

    # ── 3) Delta de volumen por operador ──
    print("\n══ 3) Volumen por operador: OLD (NegocioMov) vs NEW (Operaciones) ══")
    op_de_cuenta: dict[str, str] = {}
    nombre_op: dict[str, str] = {}
    for c in cli["Clientes"]["Comitentes"].find(
        {"estado": "Activa"}, {"_id": 0, "id_cuenta": 1, "operador_email": 1, "operador_nombre": 1}
    ):
        idc = str(c.get("id_cuenta") or "").strip()
        if idc:
            em = (c.get("operador_email") or "(sin operador)").strip().lower() or "(sin operador)"
            op_de_cuenta[idc] = em
            nombre_op.setdefault(em, c.get("operador_nombre") or em)

    # OLD
    old: dict[str, float] = {}
    for d in mov.aggregate([
        {"$match": {"categoria": {"$in": _CATS_VOL}, **_NO_FUT}},
        {"$group": {"_id": "$id_cuenta", "v": {"$sum": _PESIF}}},
    ]):
        if d.get("_id"):
            old[str(d["_id"])] = float(d.get("v") or 0.0)

    # NEW · ARS (en DB)
    new: dict[str, float] = {}
    for d in ops.aggregate([
        {"$match": {**base, "moneda": "ARS"}},
        {"$group": {"_id": "$cuenta", "v": {"$sum": {"$abs": {"$ifNull": ["$bruto", 0]}}}}},
    ]):
        if d.get("_id"):
            new[str(d["_id"])] = float(d.get("v") or 0.0)

    # NEW · USD (pesificado en Python con mep del día)
    usd_sin_mep = 0
    for d in ops.find({**base, "moneda": "USD"},
                      {"_id": 0, "cuenta": 1, "concertacion": 1, "bruto": 1}):
        idc = str(d.get("cuenta") or "")
        mep = mep_map.get(d.get("concertacion"))
        if not idc or d.get("bruto") is None:
            continue
        if not mep:
            usd_sin_mep += 1
            continue
        new[idc] = new.get(idc, 0.0) + abs(float(d["bruto"])) * mep

    agg: dict[str, dict] = {}
    for idc, em in op_de_cuenta.items():
        a = agg.setdefault(em, {"old": 0.0, "new": 0.0})
        a["old"] += old.get(idc, 0.0)
        a["new"] += new.get(idc, 0.0)

    filas = sorted(agg.items(), key=lambda kv: abs(kv[1]["new"] - kv[1]["old"]), reverse=True)
    print(f"  operadores: {len(filas)} | docs USD sin mep del día: {usd_sin_mep}\n")
    print(f"  {'operador':<28}{'vol OLD':>12}{'vol NEW':>12}{'Δ':>12}")
    print("  " + "-" * 64)
    for em, a in filas[:25]:
        print(f"  {nombre_op.get(em, em)[:27]:<28}{_m(a['old']):>12}{_m(a['new']):>12}{_m(a['new']-a['old']):>12}")
    print("  " + "-" * 64)
    to, tn = sum(a["old"] for _, a in filas), sum(a["new"] for _, a in filas)
    print(f"  {'TOTAL':<28}{_m(to):>12}{_m(tn):>12}{_m(tn-to):>12}")

    print("\n(read-only: no se escribió nada)")


if __name__ == "__main__":
    main()
