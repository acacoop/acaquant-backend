"""Capa de servicio — simulaciones de cartera por usuario.

Cada usuario puede crear, guardar, editar y borrar carteras hipotéticas
(posiciones = lista de `{ticker, importe}`). La función `calcular` toma
una lista de posiciones (no necesariamente persistidas) y devuelve
analytics: cashflow timeline, composición por dimensión, métricas
ponderadas. El frontend la usa para preview live mientras el usuario
edita y al cargar una simulación guardada.

Persistencia en `Trading.Simulaciones`. Filtro por `user_email` en cada
query — un usuario nunca puede leer ni mutar simulaciones de otro.

Índice recomendado (manual en Mongo):
    db.Simulaciones.createIndex({user_email: 1, _id: 1})
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from api.db import get_db_trading, get_db_valuaciones
from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────


class Posicion(BaseModel):
    """Una línea de la cartera: ticker + importe en moneda del activo.

    El `importe` se interpreta en la moneda del instrumento (ARS para CER /
    tasa fija / TAMAR; USD para globales / bonares). El service de cálculo
    (PR2) deriva la moneda del `clase_activo` y proyecta cashflows en
    consecuencia.
    """

    ticker: str = Field(..., min_length=1, max_length=50)
    importe: float = Field(..., gt=0)


class SimulacionCreate(BaseModel):
    """Body de POST /api/simulaciones."""

    nombre: str = Field(..., min_length=1, max_length=200)
    posiciones: list[Posicion] = Field(default_factory=list)


class SimulacionUpdate(BaseModel):
    """Body de PUT /api/simulaciones/{id}. Campos opcionales — solo se
    actualizan los que vienen distintos de None (PATCH semantics)."""

    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    posiciones: list[Posicion] | None = None


class Simulacion(BaseModel):
    """Doc completo serializado a la API. `id` es el ObjectId como string."""

    id: str
    user_email: str
    nombre: str
    posiciones: list[Posicion]
    creado: datetime
    actualizado: datetime


# ─────────────────────────────────────────────────────────────────────────────
# Acceso a Mongo
# ─────────────────────────────────────────────────────────────────────────────


def _coll():
    """Colección rw — escribimos y leemos por la misma para evitar lag
    de replicación (el usuario crea y quiere ver el doc al instante)."""
    return get_mongo_client()["Trading"]["Simulaciones"]


def _doc_to_model(doc: dict[str, Any]) -> Simulacion:
    """Mongo doc → Simulacion. Mapea _id (ObjectId) → id (str)."""
    return Simulacion(
        id=str(doc["_id"]),
        user_email=doc["user_email"],
        nombre=doc["nombre"],
        posiciones=[Posicion(**p) for p in doc.get("posiciones", [])],
        creado=doc["creado"],
        actualizado=doc["actualizado"],
    )


def _parse_object_id(simulacion_id: str) -> ObjectId | None:
    """ObjectId() tira si el string no tiene 24 hex chars. Lo capturamos
    y devolvemos None — el caller responde 404 en lugar de 500."""
    try:
        return ObjectId(simulacion_id)
    except (InvalidId, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────


def listar_simulaciones(user_email: str) -> list[Simulacion]:
    """Todas las simulaciones del usuario, más recientes primero."""
    docs = _coll().find({"user_email": user_email}).sort("actualizado", -1)
    return [_doc_to_model(d) for d in docs]


def listar_tickers_disponibles() -> list[dict[str, Any]]:
    """Universo de tickers para el autocomplete del frontend.

    Une `Valuaciones.Assets` con `Trading.Curvas` (por TICKER == ticker_corto)
    para enriquecer cada ticker con curva + tipo cuando existen. Solo devuelve
    docs con TICKER no vacío. Sin filtro por usuario (el universo es público
    dentro de la mesa).
    """
    db_v = get_db_valuaciones()
    db_t = get_db_trading()

    # Curvas: ticker_corto → metadata
    curvas_map: dict[str, dict] = {}
    for c in db_t["Curvas"].find(
        {}, {"_id": 0, "ticker_corto": 1, "tipo": 1, "curva": 1, "fecha_vencimiento": 1},
    ):
        ck = c.get("ticker_corto")
        if ck:
            curvas_map[ck] = c

    out: list[dict[str, Any]] = []
    for a in db_v["Assets"].find(
        {"TICKER": {"$ne": ""}},
        {"_id": 0, "TICKER": 1, "clase_activo": 1, "emisor": 1,
         "vencimiento": 1, "calificacion": 1, "cartera": 1},
    ):
        ticker = a.get("TICKER")
        if not ticker:
            continue
        c = curvas_map.get(ticker, {})
        vto = a.get("vencimiento") or c.get("fecha_vencimiento")
        out.append({
            "ticker":       ticker,
            "clase_activo": a.get("clase_activo"),
            "emisor":       a.get("emisor"),
            "calificacion": a.get("calificacion"),
            "cartera":      a.get("cartera"),
            "tipo":         c.get("tipo"),
            "curva":        c.get("curva"),
            "vencimiento":  str(vto)[:10] if vto else None,
        })
    out.sort(key=lambda x: x["ticker"])
    return out


def crear_simulacion(user_email: str, data: SimulacionCreate) -> Simulacion:
    """Crea una nueva simulación. `creado` y `actualizado` se setean al ahora."""
    ahora = datetime.now(UTC)
    doc = {
        "user_email": user_email,
        "nombre": data.nombre,
        "posiciones": [p.model_dump() for p in data.posiciones],
        "creado": ahora,
        "actualizado": ahora,
    }
    res = _coll().insert_one(doc)
    doc["_id"] = res.inserted_id
    return _doc_to_model(doc)


def obtener_simulacion(simulacion_id: str, user_email: str) -> Simulacion | None:
    """Devuelve None si el id es inválido, no existe, o pertenece a otro usuario."""
    oid = _parse_object_id(simulacion_id)
    if oid is None:
        return None
    doc = _coll().find_one({"_id": oid, "user_email": user_email})
    return _doc_to_model(doc) if doc else None


def actualizar_simulacion(
    simulacion_id: str,
    user_email: str,
    data: SimulacionUpdate,
) -> Simulacion | None:
    """Actualiza nombre y/o posiciones. Devuelve None si no existe o es de otro
    usuario. Si `data` no trae cambios, devuelve el doc actual sin tocar Mongo."""
    oid = _parse_object_id(simulacion_id)
    if oid is None:
        return None

    update_fields: dict[str, Any] = {}
    if data.nombre is not None:
        update_fields["nombre"] = data.nombre
    if data.posiciones is not None:
        update_fields["posiciones"] = [p.model_dump() for p in data.posiciones]

    if not update_fields:
        # PATCH vacío — devolvemos el doc actual sin escribir.
        doc = _coll().find_one({"_id": oid, "user_email": user_email})
        return _doc_to_model(doc) if doc else None

    update_fields["actualizado"] = datetime.now(UTC)
    doc = _coll().find_one_and_update(
        {"_id": oid, "user_email": user_email},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    return _doc_to_model(doc) if doc else None


def eliminar_simulacion(simulacion_id: str, user_email: str) -> bool:
    """True si se eliminó algo, False si no existía o era de otro usuario."""
    oid = _parse_object_id(simulacion_id)
    if oid is None:
        return False
    res = _coll().delete_one({"_id": oid, "user_email": user_email})
    return res.deleted_count > 0


# ─────────────────────────────────────────────────────────────────────────────
# Cálculo (preview / analytics)
# ─────────────────────────────────────────────────────────────────────────────

# Cuáles clases de activo cotizan por VN (precio expresado por cada 100 VN)
# vs unitario (FCI / OTROS / acciones). Determina cómo se deriva la cantidad
# nominal a partir del importe ingresado por el usuario. Mismas categorías
# que jobs/aum.py para mantener coherencia con el cálculo de AuM.
_CLASES_RENTA_FIJA = {
    "Títulos Públicos",
    "Letras",
    "Obligaciones Negociables",
    "Fideicomisos",
    "CPD",
}

# Curvas que pagan en pesos vs en USD. Para la composición por moneda.
_CURVAS_USD = {"soberanos"}  # globales / bonares
_CURVAS_ARS = {"cer", "tasa_fija", "tamar"}
# dolar_linked se trata aparte porque paga en pesos al USD oficial — depende
# de cómo lo considere la mesa. Lo etiquetamos "ARS-linked" en composicion.


def _enriquecer_lote(tickers_cortos: list[str]) -> dict[str, dict]:
    """Lookup batch en Curvas + Assets + TimeSales para una lista de tickers.

    Devuelve dict ticker_corto → metadata. Si un ticker no existe en Curvas,
    no aparece en el dict (el caller marca esa posición como "ticker desconocido").
    Si existe en Curvas pero no tiene precio actual, el campo precio queda en None.
    """
    if not tickers_cortos:
        return {}

    db_t = get_db_trading()
    db_v = get_db_valuaciones()

    # 1. Curvas: lookup por ticker_corto. Trae shape del instrumento + flujos.
    curva_docs = list(db_t["Curvas"].find(
        {"ticker_corto": {"$in": tickers_cortos}},
        {
            "_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1, "curva": 1,
            "fecha_emision": 1, "fecha_vencimiento": 1, "valor_nominal": 1,
            "flujos": 1, "cer_emision": 1,
        },
    ))
    by_corto = {d["ticker_corto"]: d for d in curva_docs}
    tickers_largos = [d["ticker"] for d in curva_docs]

    # 2. TimeSales: último precio + analíticos por ticker (largo).
    enrich_map: dict[str, dict] = {}
    if tickers_largos:
        for r in db_t["TimeSales"].aggregate([
            {"$match": {"ticker": {"$in": tickers_largos}, "price": {"$gt": 0}}},
            {"$sort": {"timestamp": -1}},
            {"$group": {
                "_id":      "$ticker",
                "price":    {"$first": "$price"},
                "TEA":      {"$first": "$TEA"},
                "TEM":      {"$first": "$TEM"},
                "paridad":  {"$first": "$paridad"},
                "duration": {"$first": "$duration"},
                "ts":       {"$first": "$timestamp"},
            }},
        ]):
            enrich_map[r["_id"]] = r

    # 3. Assets: clasificación por ticker_corto (matchea con TICKER del doc).
    assets_map: dict[str, dict] = {}
    for d in db_v["Assets"].find(
        {"TICKER": {"$in": tickers_cortos}},
        {"_id": 0, "TICKER": 1, "clase_activo": 1, "calificacion": 1,
         "emisor": 1, "cartera": 1, "unidad": 1},
    ):
        assets_map[d["TICKER"]] = d

    # 4. Combinar en un solo dict por ticker_corto.
    out: dict[str, dict] = {}
    for corto, c in by_corto.items():
        precio_doc = enrich_map.get(c["ticker"], {}) or {}
        asset_doc = assets_map.get(corto, {}) or {}
        out[corto] = {
            "ticker_corto":     corto,
            "ticker_largo":     c["ticker"],
            "tipo":             c.get("tipo"),
            "curva":            c.get("curva"),
            "fecha_emision":    c.get("fecha_emision"),
            "fecha_vencimiento": c.get("fecha_vencimiento"),
            "valor_nominal":    c.get("valor_nominal") or 100,
            "flujos":           c.get("flujos") or [],
            "cer_emision":      c.get("cer_emision"),
            "precio":           precio_doc.get("price"),
            "tea":              precio_doc.get("TEA"),
            "tem":              precio_doc.get("TEM"),
            "paridad":          precio_doc.get("paridad"),
            "duration":         precio_doc.get("duration"),
            "ts_precio":        precio_doc.get("ts"),
            "clase_activo":     asset_doc.get("clase_activo"),
            "calificacion":     asset_doc.get("calificacion"),
            "emisor":           asset_doc.get("emisor"),
            "cartera":          asset_doc.get("cartera"),
            "unidad":           asset_doc.get("unidad"),
        }
    return out


def _cantidad_nominal(importe: float, precio: float | None, clase_activo: str | None) -> float | None:
    """Inversa de las fórmulas de AuM (jobs/aum.py).

    - Renta fija (Títulos Públicos, Letras, ONs, Fideicomisos, CPD):
      `cantidad = importe * 100 / precio`  (precio cotiza por 100 VN)
    - FCI / OTROS / acciones / cualquier otra clase:
      `cantidad = importe / precio`        (precio unitario)

    Si no hay precio o es 0/negativo, devuelve None — no se puede operar.
    """
    if not precio or precio <= 0:
        return None
    if clase_activo in _CLASES_RENTA_FIJA:
        return round(importe * 100.0 / precio, 4)
    return round(importe / precio, 4)


def _proyectar_cashflows(
    curva: str | None,
    flujos: list[dict],
    cantidad_nominal: float,
    fecha_corte: datetime,
) -> list[dict]:
    """Proyecta los pagos futuros del instrumento × cantidad nominal.

    Reglas de monto por curva (alineadas con la doc del repo, CLAUDE.md):
    - cer / soberanos: cada flujo tiene `amortizacion_pct` y
      `cupon_sobre_residual` ya resueltos en pct sobre VN. Monto del flujo
      por 100 VN = `amortizacion_pct + cupon_sobre_residual`. Para CER
      este monto es nominal a la fecha de emisión — el ajuste por CER
      futuro NO se aplica acá (no se conoce todavía el CER de cada vto).
    - tasa_fija: cada flujo tiene `amortizacion` y `interes` en valores
      absolutos por 100 VN. Monto = `amortizacion + interes`.

    Para cualquier otro tipo (FCI, etc.) sin flujos modelados, devuelve
    lista vacía. La cartera muestra solo el importe sin proyección de
    cashflows en esos casos.
    """
    if not flujos or cantidad_nominal <= 0:
        return []

    # Ratio para escalar los montos de "por 100 VN" a "por cantidad real".
    ratio = cantidad_nominal / 100.0
    out: list[dict] = []
    for f in flujos:
        fecha_raw = f.get("fecha")
        if not fecha_raw:
            continue
        try:
            if isinstance(fecha_raw, datetime):
                fecha_pago = fecha_raw if fecha_raw.tzinfo else fecha_raw.replace(tzinfo=UTC)
            else:
                fecha_pago = datetime.fromisoformat(str(fecha_raw)[:10]).replace(tzinfo=UTC)
        except Exception:
            continue
        if fecha_pago < fecha_corte:
            continue

        if curva == "tasa_fija":
            monto_por_100 = float(f.get("amortizacion") or 0) + float(f.get("interes") or 0)
        elif curva in ("cer", "soberanos"):
            monto_por_100 = (
                float(f.get("amortizacion_pct") or 0)
                + float(f.get("cupon_sobre_residual") or 0)
            )
        else:
            # Otros (dolar_linked, tamar) tienen shapes que aún no soportamos.
            continue

        monto = round(monto_por_100 * ratio, 2)
        if monto > 0:
            out.append({
                "fecha": fecha_pago.date().isoformat(),
                "monto": monto,
            })
    return out


def _moneda_de(curva: str | None, cartera: str | None) -> str:
    """Etiqueta de moneda para la composición. Prioriza `cartera` de Assets
    si está presente; cae a `curva` si no.
    """
    if cartera:
        c = cartera.upper()
        if "USD" in c:
            return "USD"
        if "ARS" in c:
            return "ARS"
    if curva in _CURVAS_USD:
        return "USD"
    if curva == "dolar_linked":
        return "ARS-linked-USD"
    if curva in _CURVAS_ARS:
        return "ARS"
    return "OTRA"


def _ponderar(items: list[dict], campo: str) -> float | None:
    """Promedio ponderado por `importe` del campo `campo`. Ignora posiciones
    sin valor para ese campo. Devuelve None si no hay data ponderable."""
    pesos_total = 0.0
    suma = 0.0
    for it in items:
        val = it.get(campo)
        peso = it.get("importe") or 0
        if val is None or peso <= 0:
            continue
        pesos_total += peso
        suma += val * peso
    if pesos_total <= 0:
        return None
    return round(suma / pesos_total, 4)


def _composicion_por(items: list[dict], campo: str, monto_total: float) -> list[dict]:
    """Agrupa items por `campo` y devuelve [{valor, importe, pct}, ...]
    ordenado por importe desc. Items sin valor se agrupan en "(sin clasificar)".
    """
    if monto_total <= 0:
        return []
    grouped: dict[str, float] = defaultdict(float)
    for it in items:
        clave = it.get(campo) or "(sin clasificar)"
        grouped[clave] += it.get("importe") or 0
    salida = [
        {"valor": k, "importe": round(v, 2), "pct": round(v / monto_total * 100, 2)}
        for k, v in grouped.items()
    ]
    salida.sort(key=lambda x: -x["importe"])
    return salida


def calcular(posiciones: list[Posicion]) -> dict[str, Any]:
    """Pipeline completo: enriquece, calcula cashflows + composición + métricas.

    Devuelve dict con:
      - posiciones_enriquecidas: cada posición con datos de mercado y derivados.
      - cashflows: timeline mes a mes [{mes: 'YYYY-MM', monto: X}].
      - composicion: dict con {por_curva, por_clase_activo, por_emisor, por_moneda}.
      - metricas: dict con duration / TEA / paridad ponderadas + monto_total.
      - alertas: lista de strings con problemas detectados (ticker desconocido,
        sin precio, etc) — la UI los muestra junto a la cartera.
    """
    if not posiciones:
        return {
            "posiciones_enriquecidas": [],
            "cashflows": [],
            "composicion": {"por_curva": [], "por_clase_activo": [], "por_emisor": [], "por_moneda": []},
            "metricas": {
                "monto_total": 0,
                "duration_ponderada": None,
                "tea_ponderada": None,
                "paridad_ponderada": None,
            },
            "alertas": [],
        }

    tickers = [p.ticker for p in posiciones]
    enriq = _enriquecer_lote(tickers)
    ahora = datetime.now(UTC)

    items: list[dict] = []
    cashflows_acumulados: dict[str, float] = defaultdict(float)
    alertas: list[str] = []

    for pos in posiciones:
        meta = enriq.get(pos.ticker)
        if meta is None:
            alertas.append(f"Ticker desconocido: {pos.ticker}")
            items.append({
                "ticker_corto": pos.ticker,
                "importe": pos.importe,
                "precio": None,
                "cantidad_nominal": None,
                "curva": None, "tipo": None, "clase_activo": None,
                "emisor": None, "calificacion": None, "moneda": None,
                "fecha_vencimiento": None,
                "tea": None, "duration": None, "paridad": None,
            })
            continue

        cantidad = _cantidad_nominal(pos.importe, meta["precio"], meta["clase_activo"])
        if cantidad is None:
            alertas.append(f"{pos.ticker}: sin precio actual (Atlas pausado o ticker sin trades hoy)")

        cashflows_pos = (
            _proyectar_cashflows(meta["curva"], meta["flujos"], cantidad, ahora)
            if cantidad is not None else []
        )
        for cf in cashflows_pos:
            mes = cf["fecha"][:7]  # YYYY-MM
            cashflows_acumulados[mes] += cf["monto"]

        items.append({
            "ticker_corto":     meta["ticker_corto"],
            "ticker_largo":     meta["ticker_largo"],
            "importe":          pos.importe,
            "precio":           meta["precio"],
            "cantidad_nominal": cantidad,
            "curva":            meta["curva"],
            "tipo":             meta["tipo"],
            "clase_activo":     meta["clase_activo"],
            "emisor":           meta["emisor"],
            "calificacion":     meta["calificacion"],
            "moneda":           _moneda_de(meta["curva"], meta["cartera"]),
            "fecha_vencimiento": (
                str(meta["fecha_vencimiento"])[:10] if meta["fecha_vencimiento"] else None
            ),
            "tea":              meta["tea"],
            "duration":         meta["duration"],
            "paridad":          meta["paridad"],
        })

    monto_total = sum(it["importe"] for it in items)

    cashflows_timeline = [
        {"mes": mes, "monto": round(monto, 2)}
        for mes, monto in sorted(cashflows_acumulados.items())
    ]

    composicion = {
        "por_curva":        _composicion_por(items, "curva", monto_total),
        "por_clase_activo": _composicion_por(items, "clase_activo", monto_total),
        "por_emisor":       _composicion_por(items, "emisor", monto_total),
        "por_moneda":       _composicion_por(items, "moneda", monto_total),
    }

    metricas = {
        "monto_total":         round(monto_total, 2),
        "duration_ponderada":  _ponderar(items, "duration"),
        "tea_ponderada":       _ponderar(items, "tea"),
        "paridad_ponderada":   _ponderar(items, "paridad"),
    }

    return {
        "posiciones_enriquecidas": items,
        "cashflows": cashflows_timeline,
        "composicion": composicion,
        "metricas": metricas,
        "alertas": alertas,
    }
