"""Control Automático (Clientes): concilia un Excel de CUITs contra nuestras cuentas.

Flujo: el Excel (cualquier columnas; el CUIT está en 'Nº ident.fis.1') se cruza
contra Clientes.Comitentes y se parte en LAS QUE TENEMOS (con su id_cuenta) y LAS
QUE NO. Sobre las que tenemos, un botón segmenta → nivel_1 = "PRODUCTORES".

Matcher (no inferible, verificado con scripts/diag_titular_cuit):
  Nuestras cuentas guardan en `tipo_doc`/`nro_doc` (derivados del `titular` de
  Aunesa) un DNI (físicas, 7-8 díg) o un CUIT/CUIL/CDI (jurídicas, 11 díg). El
  Excel trae CUIT (11). Un CUIT contiene el DNI de 8 en el medio (TT-DDDDDDDD-V),
  así que una cuenta matchea si coincide el CUIT completo O el DNI embebido.
  `claves_match` devuelve TODAS las representaciones de un número → se aplica
  igual a nuestras cuentas (para indexar) y al CUIT del Excel (para buscar).
"""
from __future__ import annotations

from datetime import UTC, datetime

from pymongo import UpdateOne

from core.doc_fiscal import NIVEL_1_PRODUCTORES, claves_match, solo_digitos
from core.mongo import get_mongo_client, get_mongo_client_read


def _index_base() -> dict[str, dict]:
    """clave_match → datos de la cuenta nuestra (de Clientes.Comitentes)."""
    cur = get_mongo_client_read()["Clientes"]["Comitentes"].find(
        {"nro_doc": {"$exists": True, "$nin": [None, ""]}},
        {"_id": 0, "id_cuenta": 1, "denominacion": 1, "tipo_doc": 1,
         "nro_doc": 1, "operador_nombre": 1, "nivel_1": 1},
    )
    index: dict[str, dict] = {}
    for c in cur:
        for k in claves_match(c.get("nro_doc")):
            index.setdefault(k, c)   # primera gana (colisión improbable)
    return index


def reconciliar(cuits: list[str]) -> dict:
    """Parte la lista de CUITs del Excel en tenemos / no_tenemos (cruce por doc)."""
    index = _index_base()
    tenemos: list[dict] = []
    no_tenemos: list[dict] = []
    vistos: set[str] = set()
    for raw in cuits:
        cuit = solo_digitos(raw)
        if not cuit:
            continue
        hit = next((index[k] for k in claves_match(cuit) if k in index), None)
        if hit:
            idc = hit.get("id_cuenta")
            if idc in vistos:
                continue
            vistos.add(idc)
            tenemos.append({
                "cuit":         cuit,
                "id_cuenta":    idc,
                "denominacion": hit.get("denominacion"),
                "operador":     hit.get("operador_nombre"),
                "nivel_1":      hit.get("nivel_1"),
                "ya_productor": (hit.get("nivel_1") or "").upper() == NIVEL_1_PRODUCTORES,
            })
        else:
            no_tenemos.append({"cuit": cuit})
    tenemos.sort(key=lambda x: (x["ya_productor"], (x.get("denominacion") or "").upper()))
    return {
        "tenemos":      tenemos,
        "no_tenemos":   no_tenemos,
        "n_excel":      len([c for c in cuits if solo_digitos(c)]),
        "n_tenemos":    len(tenemos),
        "n_no_tenemos": len(no_tenemos),
    }


def segmentar(id_cuentas: list[str], actor: str = "system") -> dict:
    """Setea nivel_1 = PRODUCTORES en las cuentas dadas (bulk, idempotente)."""
    ids = [str(i) for i in id_cuentas if i]
    if not ids:
        return {"modificadas": 0, "matched": 0}
    now = datetime.now(UTC)
    ops = [
        UpdateOne(
            {"id_cuenta": idc},
            {"$set": {"nivel_1": NIVEL_1_PRODUCTORES,
                      "actualizado_por": actor, "actualizado_at": now}},
        )
        for idc in ids
    ]
    res = get_mongo_client()["Clientes"]["Comitentes"].bulk_write(ops, ordered=False)
    return {"modificadas": res.modified_count, "matched": res.matched_count}
