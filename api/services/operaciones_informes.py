"""operaciones_informes.py — normalización + ingesta a CashFlow.Operaciones.

`CashFlow.Operaciones` es la FUENTE DE VERDAD de operaciones (desde la API
`informes` de Aunesa, que NO trae movimientos administrativos / FCI bilateral —
esos siguen en NegocioMovimientos). Schema mínimo, 10 campos:

  boleto (🔑 único), cuenta, concertacion, denominacion, tipo_operacion,
  instrumento, condiciones, cantidad, bruto, arancel

LEY #1: un boleto = un documento → índice ÚNICO sobre `boleto`.

Este service es puro (sin FastAPI). Lo usan:
  - api/routers/manager/operaciones.py (backfill por CSV desde la UI).
  - (futuro) el job de ingesta diaria desde la API informes — mismo normalizador.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from pymongo import UpdateOne
from pymongo.errors import BulkWriteError

# Header (normalizado: lower, sin acentos, sin separadores) → campo canónico.
# Cubre los nombres de la API informes y los del histórico (Excel en español).
_ALIASES: dict[str, str] = {
    "boleto": "boleto",
    "cuenta": "cuenta",
    "concertacion": "concertacion",
    "denominacion": "denominacion",
    "tipodeoperacion": "tipo_operacion",
    "tipooperacion": "tipo_operacion",
    "instrumento": "instrumento",
    "condiciones": "condiciones",
    "cantidad": "cantidad",
    "cantidadtotal": "cantidad",
    "bruto": "bruto",
    "aranceles": "arancel",
    "arancel": "arancel",
}


def _norm_header(h: str) -> str:
    s = unicodedata.normalize("NFKD", str(h)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _to_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_cuenta(v) -> str | None:
    """Cuenta siempre como string sin '.0' (el Excel a veces la trae numérica)."""
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s or None


def _to_iso(raw) -> str | None:
    """A 'YYYY-MM-DD'. Acepta ISO ya formado o DD/MM/YYYY."""
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if "/" in s:
        try:
            d, m, y = s[:10].split("/")
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except (ValueError, IndexError):
            return None
    return None


def _to_moneda(condiciones) -> str | None:
    """Moneda de la operación, derivada de `condiciones` ('ARS Inm', 'USD 24hs')."""
    if not condiciones:
        return None
    tok = str(condiciones).strip().upper().split()
    if not tok:
        return None
    m = tok[0]
    if m.startswith("USD"):
        return "USD"
    if m.startswith("ARS"):
        return "ARS"
    return m or None


def _to_float(raw) -> float | None:
    """Parsea número tolerando: prefijo de moneda ('ARS 248.90'), separadores
    AR ('82965,6' → 82965.6; '1.234.567,89' → 1234567.89) y punto decimal."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = re.sub(r"[^\d,.\-]", "", str(raw).strip())  # tira 'ARS', símbolos, espacios
    if not s or s in ("-", ".", ","):
        return None
    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        # el ÚLTIMO separador es el decimal.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")   # '.' miles, ',' decimal
        else:
            s = s.replace(",", "")                       # ',' miles, '.' decimal
    elif has_comma:
        s = s.replace(",", ".")                          # ',' decimal
    try:
        return float(s)
    except ValueError:
        return None


def normalizar_fila(row: dict) -> dict | None:
    """Fila cruda (header→valor) → doc canónico de 10 campos. None si no hay boleto."""
    canon: dict = {}
    for h, v in row.items():
        campo = _ALIASES.get(_norm_header(h))
        if campo and campo not in canon:
            canon[campo] = v

    boleto = _to_str(canon.get("boleto"))
    if not boleto:
        return None
    return {
        "boleto":         boleto,
        "cuenta":         _to_cuenta(canon.get("cuenta")),
        "concertacion":   _to_iso(canon.get("concertacion")),
        "denominacion":   _to_str(canon.get("denominacion")),
        "tipo_operacion": _to_str(canon.get("tipo_operacion")),
        "instrumento":    _to_str(canon.get("instrumento")),
        "condiciones":    _to_str(canon.get("condiciones")),
        "cantidad":       _to_float(canon.get("cantidad")),
        "bruto":          _to_float(canon.get("bruto")),
        "arancel":        _to_float(canon.get("arancel")),
        "moneda":         _to_moneda(canon.get("condiciones")),
    }


def ensure_indexes(coll) -> None:
    """LEY #1: índice único parcial sobre boleto + índices de consulta."""
    coll.create_index(
        [("boleto", 1)], name="uq_boleto", unique=True,
        partialFilterExpression={"boleto": {"$type": ["string", "int", "long", "double"]}},
    )
    coll.create_index([("cuenta", 1), ("concertacion", -1)], name="cuenta_concertacion")
    coll.create_index([("concertacion", -1)], name="concertacion")


def cargar_maps_enrich(db) -> tuple[dict, dict]:
    """Carga (catálogo tipo→{mercado,operacion}, id_cuenta→nivel_1) — para
    enriquecer inline en la ingesta diaria sin scan completo de la colección."""
    cat = {
        d["tipo_operacion"]: d
        for d in db["TiposOperacion"].find(
            {}, {"_id": 0, "tipo_operacion": 1, "mercado": 1, "operacion": 1}
        )
        if d.get("tipo_operacion")
    }
    nivel1 = {
        str(d.get("id_cuenta")).strip(): (d.get("nivel_1") or "")
        for d in db.client["Clientes"]["Comitentes"].find(
            {"id_cuenta": {"$exists": True, "$ne": ""}}, {"_id": 0, "id_cuenta": 1, "nivel_1": 1}
        )
        if d.get("id_cuenta") not in (None, "")
    }
    return cat, nivel1


def _aplicar_enrich(doc: dict, maps: tuple[dict, dict] | None) -> None:
    """Setea mercado/operacion/segmento en el doc (moneda ya viene del normalizador)."""
    if not maps:
        return
    cat, nivel1 = maps
    c = cat.get(doc.get("tipo_operacion") or "")
    doc["mercado"] = (c or {}).get("mercado", "")
    doc["operacion"] = (c or {}).get("operacion", "otro")
    doc["segmento"] = nivel1.get(str(doc.get("cuenta") or "").strip(), "")


def ingestar_filas(
    coll, rows: list[dict], crear_indice: bool = False,
    enrich_maps: tuple[dict, dict] | None = None,
) -> dict:
    """Normaliza filas crudas y las upsertea por boleto (idempotente).

    Si `enrich_maps` se pasa (catálogo, nivel1), enriquece inline
    (mercado/operacion/segmento) — así la ingesta diaria no necesita el scan
    completo de `enriquecer`.

    Devuelve un resumen: recibidas / sin_boleto / upsertadas / modificadas.
    """
    if crear_indice:
        ensure_indexes(coll)

    ahora = datetime.now(UTC)
    # Dedup por boleto DENTRO del lote (última fila gana): con el índice único,
    # dos filas del mismo boleto que aún no existe harían dos inserts → E11000.
    por_boleto: dict[str, dict] = {}
    sin_boleto = 0
    for row in rows:
        doc = normalizar_fila(row)
        if doc is None:
            sin_boleto += 1
            continue
        doc["ingestado_en"] = ahora
        _aplicar_enrich(doc, enrich_maps)
        por_boleto[doc["boleto"]] = doc

    if not por_boleto:
        return {"recibidas": len(rows), "sin_boleto": sin_boleto,
                "upsertadas": 0, "modificadas": 0}

    ops = [
        UpdateOne({"boleto": d["boleto"]}, {"$set": d}, upsert=True)
        for d in por_boleto.values()
    ]

    try:
        res = coll.bulk_write(ops, ordered=False)
        upserted, modified = res.upserted_count, res.modified_count
    except BulkWriteError as bwe:
        # ordered=False → las ops sin conflicto SÍ se aplican. Los errores de
        # clave duplicada (11000) son inofensivos (el boleto ya está). Solo se
        # re-lanza si hay errores que NO son duplicados. Nunca 500 por un dup.
        det = bwe.details or {}
        otros = [e for e in det.get("writeErrors", []) if e.get("code") != 11000]
        if otros:
            raise
        upserted = det.get("nUpserted", 0)
        modified = det.get("nModified", 0)
    return {
        "recibidas":   len(rows),
        "sin_boleto":  sin_boleto,
        "upsertadas":  upserted,
        "modificadas": modified,
    }


def enriquecer(db, batch: int = 2000) -> dict:
    """Denormaliza sobre cada doc de CashFlow.Operaciones: `moneda` (de
    condiciones), `mercado` y `operacion` (join a CashFlow.TiposOperacion por
    tipo_operacion), y `grupo` + `es_contraparte` (join a CashFlow.Contrapartes
    por cuenta — grupo = su `segmento`). Re-correr tras editar catálogo o
    contrapartes. Crea índices de la vista.
    """
    cat = {
        d["tipo_operacion"]: d
        for d in db["TiposOperacion"].find(
            {}, {"_id": 0, "tipo_operacion": 1, "mercado": 1, "operacion": 1}
        )
        if d.get("tipo_operacion")
    }
    # id_cuenta → nivel_1 (segmento comercial) de Clientes.Comitentes.
    comit = db.client["Clientes"]["Comitentes"]
    nivel1 = {
        str(d.get("id_cuenta")).strip(): (d.get("nivel_1") or "")
        for d in comit.find(
            {"id_cuenta": {"$exists": True, "$ne": ""}}, {"_id": 0, "id_cuenta": 1, "nivel_1": 1}
        )
        if d.get("id_cuenta") not in (None, "")
    }
    coll = db["Operaciones"]
    coll.create_index([("concertacion", -1), ("mercado", 1)], name="concertacion_mercado")
    coll.create_index([("concertacion", -1), ("operacion", 1)], name="concertacion_operacion")
    coll.create_index([("concertacion", -1), ("segmento", 1)], name="concertacion_segmento")

    ops, total, sin_cat = [], 0, 0
    for d in coll.find({}, {"_id": 1, "tipo_operacion": 1, "condiciones": 1, "cuenta": 1}):
        c = cat.get(d.get("tipo_operacion") or "")
        if c is None:
            sin_cat += 1
        cuenta = str(d.get("cuenta") or "").strip()
        ops.append(UpdateOne(
            {"_id": d["_id"]},
            {"$set": {
                "moneda":    _to_moneda(d.get("condiciones")),
                "mercado":   (c or {}).get("mercado", ""),
                "operacion": (c or {}).get("operacion", "otro"),
                "segmento":  nivel1.get(cuenta, ""),
            }},
        ))
        if len(ops) >= batch:
            coll.bulk_write(ops, ordered=False)
            total += len(ops)
            ops = []
    if ops:
        coll.bulk_write(ops, ordered=False)
        total += len(ops)
    return {"actualizados": total, "sin_catalogo": sin_cat, "tipos_catalogo": len(cat)}


def stats(coll) -> dict:
    """Resumen del estado de la colección para la UI."""
    n = coll.count_documents({})
    if not n:
        return {"n": 0, "n_cuentas": 0, "min_concertacion": None, "max_concertacion": None}
    rango = list(coll.aggregate([
        {"$group": {
            "_id": None,
            "min": {"$min": "$concertacion"},
            "max": {"$max": "$concertacion"},
            "cuentas": {"$addToSet": "$cuenta"},
        }},
    ]))
    r = rango[0] if rango else {}
    return {
        "n":                n,
        "n_cuentas":        len(r.get("cuentas") or []),
        "min_concertacion": r.get("min"),
        "max_concertacion": r.get("max"),
    }
