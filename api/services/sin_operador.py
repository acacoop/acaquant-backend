"""Cuentas sin operador asignado — el bucket "(sin operador)" del ranking comercial.

Replica el criterio de `informe_comercial`: cuentas CON volumen cuyo `operador_email`
está vacío en Clientes.Comitentes, o que no están en el master. Para las que NO
están en Comitentes las categoriza (propia / contraparte / FCI / OTC) con la lógica
de jobs/_aum_filters, así se distinguen los CLIENTES REALES sin operador (hay que
asignarles) de los no-clientes (ruido esperado del ranking).

Portado de scripts/diag_sin_operador.py para servir la tab MANAGER → CLIENTES →
SIN OPERADOR. Cacheado (TTL 5min): es un barrido agregado, no de cada request.
"""
from __future__ import annotations

from collections import Counter

from api.cache import cached
from api.services._negocio_futuros import match_no_futuros
from api.services.comercial import _CATS_VOLUMEN, _PESIF
from core.mongo import get_mongo_client_read
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)


def _vacio(email: object) -> bool:
    return not str(email or "").strip()


def _categoria(cuenta: str | None, unidad: str | None, idc: str, ids, names) -> str:
    c = (cuenta or "").upper()
    if c.startswith("[100]") or c.startswith("[101]"):
        return "propia [100]/[101]"
    if idc in ids:
        return "contraparte/FCI (id)"
    if "OTC" in c or "CDC" in c or (unidad or "").upper() == "USDL":
        return "OTC/CDC/USDL"
    if is_excluded(cuenta, unidad, idc, ids, names):
        return "contraparte (nombre)"
    return "SIN CLASIFICAR"


@cached(ttl=300)
def cuentas_sin_operador() -> dict:
    """{clientes_sin_operador, no_clientes, resumen_no_clientes, contadores}."""
    db = get_mongo_client_read()
    nm = db["CashFlow"]["NegocioMovimientos"]
    com = db["Clientes"]["Comitentes"]

    agg = {
        str(r["_id"]): {"vol": float(r["v"] or 0.0),
                        "cuenta": r.get("cuenta"), "unidad": r.get("unidad")}
        for r in nm.aggregate([
            {"$match": {"categoria": {"$in": list(_CATS_VOLUMEN)}, **match_no_futuros()}},
            {"$group": {"_id": "$id_cuenta", "v": {"$sum": _PESIF},
                        "cuenta": {"$first": "$cuenta"}, "unidad": {"$first": "$unidad"}}},
        ], allowDiskUse=True)
        if r.get("_id")
    }
    detalle = {
        str(d["id_cuenta"]): d
        for d in com.find({}, {"_id": 0, "id_cuenta": 1, "denominacion": 1,
                               "operador_email": 1, "estado": 1})
        if d.get("id_cuenta")
    }
    ids = load_contrapartes_id_cuentas()
    names = load_contrapartes_names()

    clientes: list[dict] = []
    no_clientes: list[dict] = []
    for idc, a in agg.items():
        info = detalle.get(idc)
        if info is not None and not _vacio(info.get("operador_email")):
            continue  # cliente con operador → fuera del bucket
        fila = {"id_cuenta": idc, "vol": round(a["vol"], 2), "cuenta": a.get("cuenta")}
        if info is not None:
            fila["denominacion"] = info.get("denominacion")
            fila["estado"] = info.get("estado")
            clientes.append(fila)
        else:
            fila["categoria"] = _categoria(a.get("cuenta"), a.get("unidad"), idc, ids, names)
            no_clientes.append(fila)

    clientes.sort(key=lambda f: f["vol"], reverse=True)
    no_clientes.sort(key=lambda f: f["vol"], reverse=True)

    cat_count: Counter = Counter(f["categoria"] for f in no_clientes)
    cat_vol: dict[str, float] = {}
    for f in no_clientes:
        cat_vol[f["categoria"]] = cat_vol.get(f["categoria"], 0.0) + f["vol"]
    resumen = [{"categoria": c, "n": n, "vol": round(cat_vol[c], 2)}
               for c, n in cat_count.most_common()]
    sin_clasif = sum(1 for f in no_clientes if f["categoria"] == "SIN CLASIFICAR")

    return {
        "clientes_sin_operador": clientes,
        "no_clientes": no_clientes,
        "resumen_no_clientes": resumen,
        "n_clientes_sin_op": len(clientes),
        "n_no_clientes": len(no_clientes),
        "sin_clasificar": sin_clasif,
    }
