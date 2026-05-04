"""Capa de servicio — renta fija (MarketSnapshot + TimeSales + Curvas).

Funciones puras (sin FastAPI, sin HTTP) que consultan la data de renta fija
local: snapshot de libro/trades, histórico de trades por ticker, serie diaria
por curva, y el tool maestro `listar_curva` que enriquece la curva con TEA/
TEM/paridad/duration/convexity.

Helpers compartidos (`resolver_ticker_exacto`, `_ticker_filter`, constantes
`_CURVAS_VALIDAS`) viven acá porque la capa de renta fija es la que define
tickers y curvas; el resto de services los importa desde este módulo.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from api.cache import cached
from api.db import get_db_trading

logger = logging.getLogger(__name__)

_CURVAS_VALIDAS = ("cer", "tasa_fija", "tamar", "soberanos", "dolar_linked")
_ORDENES_VALIDOS = ("vencimiento", "volumen_dia", "tea", "duration")


def _ticker_filter(instrumento: str) -> dict:
    """Regex substring escape — compat con ticker corto o completo."""
    return {"$regex": re.escape(instrumento), "$options": "i"}


def resolver_ticker_exacto(instrumento: str) -> str | None:
    """Resuelve un ticker (corto o completo) al ticker completo de ROFEX.

    Evita regex table-scan cuando se puede usar match exacto (índice
    (ticker, timestamp) en TimeSales). Si el input ya trae ' - ', se asume
    completo. Si es corto, se busca en Trading.Curvas.ticker_corto.

    Devuelve el ticker completo o None si no se pudo resolver.
    """
    instr = (instrumento or "").strip()
    if not instr:
        return None
    if " - " in instr:
        return instr
    db = get_db_trading()
    doc = db["Curvas"].find_one(
        {"ticker_corto": instr}, {"ticker": 1, "_id": 0}
    )
    return doc.get("ticker") if doc else None


# ─────────────────────────────────────────────────────────────────────────────
# Renta Fija (MarketSnapshot)
# ─────────────────────────────────────────────────────────────────────────────


def _tc_breakeven(precio: float | None, flujo_vto: float | None,
                  mep: float | None) -> float | None:
    """TC al que el bono en pesos comprado hoy y mantenido a vto empata
    contra haber comprado dólar MEP hoy.

        TC_BE = MEP × (flujo_vencimiento / precio_actual)

    Solo aplica a tasa fija (incluye CER ya fijados, donde el flujo final
    está determinado). Devuelve None si falta cualquier input.
    """
    if not precio or not flujo_vto or not mep:
        return None
    if precio <= 0 or flujo_vto <= 0 or mep <= 0:
        return None
    return round(mep * (flujo_vto / precio), 2)


@cached(ttl=10)
def get_renta_fija(instrumento: str | None = None) -> list:
    """Snapshot de renta fija con métricas live + TC breakeven (tasa fija).

    TTL=10s (subido de 5s 2026-04-28): con el frontend poleando cada 5s
    en /snapshot-live + 8 users en mesa, el cache de 5s se vencía en
    cada poll y el backend recalculaba 12 veces/min. Con 10s, recalcula
    6/min — 50% menos trabajo. El delay máximo del dato visto por el
    user pasa de 5s a 10s, despreciable para los bonos de la mesa.

    El TC BE se calcula on-the-fly: requiere `flujo_vencimiento` (de
    Trading.Curvas) + last_price (del snapshot) + MEP live (macro).
    Sólo se popula para tickers cuya curva sea tasa_fija nativa o CER ya
    fijado (mismo set que `listar_curva` cuando curva='tasa_fija').
    """
    db = get_db_trading()
    filtro: dict = {}
    if instrumento:
        filtro["ticker"] = _ticker_filter(instrumento)

    pipeline = [
        {"$match": filtro},
        {"$project": {
            "_id": 0,
            "instrumento": "$ticker",
            "book": 1,
            "metrics.total_nominals": 1,
            "metrics.vwap": 1,
            "metrics.last_price": 1,
            "metrics.open_price": 1,
            "metrics.high_price": 1,
            "metrics.low_price": 1,
            "metrics.closing_price": 1,
            # Analíticos (escritos por engines/curvas.py en el mismo doc).
            "metrics.TEA": 1,
            "metrics.TEM": 1,
            "metrics.duration": 1,
            "metrics.mod_duration": 1,
            "metrics.convexity": 1,
            "metrics.paridad": 1,
        }},
    ]
    docs = list(db["MarketSnapshot"].aggregate(pipeline))

    # ── Enriquecimiento con TC breakeven para tasa fija ──
    # Build set de tickers tasa_fija (nativa + CER ya fijados → comportan tasa fija).
    fijados = _bonos_cer_fijados()
    flujo_por_ticker: dict[str, float] = {}
    for c in db["Curvas"].find(
        {"$or": [{"curva": "tasa_fija"}, {"ticker": {"$in": list(fijados)}}]}
        if fijados else {"curva": "tasa_fija"},
        {"_id": 0, "ticker": 1, "flujo_vencimiento": 1},
    ):
        fv = c.get("flujo_vencimiento")
        if fv and fv > 0:
            flujo_por_ticker[c["ticker"]] = float(fv)

    if flujo_por_ticker:
        from api.services.macro import get_ultimo_mep  # lazy: evita ciclo
        mep_doc = get_ultimo_mep()
        mep = mep_doc.get("mep") if mep_doc else None
        if mep:
            for d in docs:
                fv = flujo_por_ticker.get(d.get("instrumento") or "")
                if fv is None:
                    continue
                m = d.setdefault("metrics", {})
                last = m.get("last_price")
                m["tc_breakeven"] = _tc_breakeven(last, fv, mep)

    return docs


# ─────────────────────────────────────────────────────────────────────────────
# Histórico de trades + curvas (Trading.TimeSales)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=15)
def get_historico_trades(instrumento: str | None = None) -> list:
    """Trades de los últimos 15 días. Match EXACTO por ticker para usar índice."""
    db = get_db_trading()
    corte = datetime.now(UTC) - timedelta(days=15)
    filtro: dict = {"timestamp": {"$gte": corte}}
    if instrumento:
        exacto = resolver_ticker_exacto(instrumento)
        if exacto is None:
            return []
        filtro["ticker"] = exacto

    pipeline = [
        {"$match": filtro},
        {"$sort": {"timestamp": -1}},
        {"$limit": 10000},
        {"$project": {
            "_id": 0,
            "instrumento": "$ticker",
            "timestamp": 1,
            "price": 1,
            "size": 1,
            "side": 1,
            "money": 1,
            "duration": 1,
            "TEA": 1,
            "TEM": 1,
            "paridad": 1,
        }},
    ]
    return list(db["TimeSales"].aggregate(pipeline))


@cached(ttl=30)
def _bonos_cer_fijados() -> set[str]:
    """Tickers de bonos CER cuyo CER de liquidación del VTO ya fue publicado
    por el BCRA → efectivamente tasa fija desde ya. Se recalcula por request
    (barato: 1 query CER + 1 query DiasHabiles + loop chico).

    El `db` se resuelve adentro (no como arg) porque el decorador
    `@cached` solo acepta kwargs hashables — pasar el handle de Mongo
    como posicional dispara `takes 0 positional arguments but 1 was given`
    y rompe todo `listar_curva(curva ∈ {cer, tasa_fija})`.

    Fallback: si la función falla por cualquier motivo (Mongo down, dato
    faltante, comparación de tipos), devuelve set vacío y logea. Así
    `listar_curva` sigue devolviendo bonos aunque la reasignación CER↔
    tasa_fija se pierda. Sin esto, todo `listar_curva` con curva ∈ {cer,
    tasa_fija} se cae con un error opaco "error del service".
    """
    from engines.curvas import fecha_cer_liquidacion

    try:
        db = get_db_trading()
        cer_max_doc = db["CER"].find_one({}, sort=[("fecha", -1)], projection={"fecha": 1})
        if not cer_max_doc:
            return set()
        max_cer_publicado = cer_max_doc["fecha"]

        dias_habiles = sorted(
            d["fecha"] for d in db["DiasHabiles"].find({}, {"fecha": 1, "_id": 0})
        )

        fijados: set[str] = set()
        for inst in db["Curvas"].find(
            {"curva": "cer"},
            {"_id": 0, "ticker": 1, "fecha_vencimiento": 1},
        ):
            vto = str(inst.get("fecha_vencimiento") or "")[:10]
            if not vto:
                continue
            fecha_liq = fecha_cer_liquidacion(dias_habiles, vto, n=10)
            if fecha_liq and fecha_liq <= max_cer_publicado:
                fijados.add(inst["ticker"])
        return fijados
    except Exception:
        logger.exception("_bonos_cer_fijados falló — devuelvo set vacío (fallback)")
        return set()


def listar_curva(
    curva: str,
    ordenar_por: str = "vencimiento",
    vencimiento_min_meses: float | None = None,
    vencimiento_max_meses: float | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Lista los bonos de una curva con metadata enriquecida.

    Reasignación automática CER → tasa_fija: los bonos CER cuyo CER de
    liquidación del vencimiento ya está publicado por el BCRA se comportan
    como tasa fija (su flujo final está determinado). Por eso:
      - `curva='cer'`       → incluye solo los CER que TODAVÍA no están fijados.
      - `curva='tasa_fija'` → incluye tasa fija propia + CER ya fijados, con
                              un flag `cer_fijado=true` para el frontend.

    Devuelve para cada instrumento: ticker, ticker_corto, tipo, vencimiento,
    precio, TEA/TEM, paridad, duration, volumen del día. Ordenable por
    vencimiento (default), volumen, TEA o duration. Filtrable por horizonte
    (vencimiento_min/max_meses).
    """
    if curva not in _CURVAS_VALIDAS:
        return []
    if ordenar_por not in _ORDENES_VALIDOS:
        ordenar_por = "vencimiento"

    db = get_db_trading()

    # Reasignación CER ↔ tasa_fija.
    fijados_tickers = _bonos_cer_fijados() if curva in ("cer", "tasa_fija") else set()

    if curva == "cer":
        # Solo CER todavía variable (excluye los que ya quedaron fijados).
        filtro_curva = {"curva": "cer"}
        if fijados_tickers:
            filtro_curva["ticker"] = {"$nin": list(fijados_tickers)}
        # cupon_anual + cer_emision: para distinguir Lecers (zero coupon)
        # de Boncers cupón tipo TX26/TX28 (cupon_anual > 0). Lo necesita
        # la descomposición de retorno CER para filtrar la curva de
        # interpolación a los zero coupon (la spec lo pide explícitamente).
        curva_docs = list(db["Curvas"].find(
            filtro_curva,
            {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
             "fecha_vencimiento": 1, "fecha_emision": 1,
             "cupon_anual": 1, "cer_emision": 1},
        ))
    elif curva == "tasa_fija":
        # tasa_fija propia + CER fijados (se marcan como `cer_fijado=true`).
        # `flujo_vencimiento` se proyecta acá para calcular tc_breakeven.
        curva_docs = list(db["Curvas"].find(
            {
                "$or": [
                    {"curva": "tasa_fija"},
                    {"curva": "cer", "ticker": {"$in": list(fijados_tickers)}}
                    if fijados_tickers else {"curva": "tasa_fija"},
                ]
            },
            {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
             "curva": 1, "fecha_vencimiento": 1, "fecha_emision": 1,
             "flujo_vencimiento": 1},
        ))
    else:
        curva_docs = list(db["Curvas"].find(
            {"curva": curva},
            {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
             "fecha_vencimiento": 1, "fecha_emision": 1},
        ))
    if not curva_docs:
        return []

    ahora = datetime.now(UTC)
    filtrados: list[dict] = []
    for d in curva_docs:
        vto_raw = d.get("fecha_vencimiento")
        if not vto_raw:
            continue
        try:
            if isinstance(vto_raw, datetime):
                vto = vto_raw if vto_raw.tzinfo else vto_raw.replace(tzinfo=UTC)
            else:
                vto = datetime.fromisoformat(str(vto_raw)[:10]).replace(tzinfo=UTC)
        except Exception:
            continue
        meses = round((vto - ahora).days / 30.44, 1)
        if vencimiento_min_meses is not None and meses < vencimiento_min_meses:
            continue
        if vencimiento_max_meses is not None and meses > vencimiento_max_meses:
            continue
        d["_meses"] = meses
        filtrados.append(d)

    if not filtrados:
        return []

    tickers = [d["ticker"] for d in filtrados]

    enrich_map: dict[str, dict] = {}
    # Lee último estado por ticker desde MarketSnapshot (last_price +
    # analíticas TEA/TEM/duration/etc). Antes agregaba TimeSales con
    # $group/$first; este path lee 1 doc por ticker (find directo) y es
    # estrictamente más rápido. Los valores son idénticos: valores.py
    # escribe last_price y curvas.py escribe los analíticos.
    for r in db["MarketSnapshot"].find(
        {"ticker": {"$in": tickers},
         "metrics.last_price": {"$gt": 0}},
        {"_id": 0, "ticker": 1, "updated_at": 1,
         "metrics.last_price": 1, "metrics.TEA": 1, "metrics.TEM": 1,
         "metrics.paridad": 1, "metrics.duration": 1,
         "metrics.mod_duration": 1, "metrics.convexity": 1},
    ):
        m = r.get("metrics") or {}
        enrich_map[r["ticker"]] = {
            "price":        m.get("last_price"),
            "TEA":          m.get("TEA"),
            "TEM":          m.get("TEM"),
            "paridad":      m.get("paridad"),
            "duration":     m.get("duration"),
            "mod_duration": m.get("mod_duration"),
            "convexity":    m.get("convexity"),
            "ts":           r.get("updated_at"),
        }

    vol_map: dict[str, dict] = {}
    for r in db["MarketSnapshot"].find(
        {"ticker": {"$in": tickers}},
        {"_id": 0, "ticker": 1, "metrics.total_money": 1, "metrics.total_nominals": 1},
    ):
        m = r.get("metrics") or {}
        vol_map[r["ticker"]] = {
            "total_money": m.get("total_money") or 0,
            "total_nominals": m.get("total_nominals") or 0,
        }

    # MEP live para TC breakeven (sólo aplica a curva='tasa_fija' acá).
    mep_actual: float | None = None
    if curva == "tasa_fija":
        from api.services.macro import get_ultimo_mep  # lazy: evita ciclo
        mep_doc = get_ultimo_mep()
        mep_raw = mep_doc.get("mep") if mep_doc else None
        if mep_raw and mep_raw > 0:
            mep_actual = float(mep_raw)

    out: list[dict] = []
    for d in filtrados:
        enrich = enrich_map.get(d["ticker"], {})
        vol = vol_map.get(d["ticker"], {})
        ts_last = enrich.get("ts")
        entry = {
            "ticker": d["ticker"],
            "ticker_corto": d.get("ticker_corto"),
            "tipo": d.get("tipo"),
            "fecha_vencimiento": str(d.get("fecha_vencimiento"))[:10] if d.get("fecha_vencimiento") else None,
            "fecha_emision": str(d.get("fecha_emision"))[:10] if d.get("fecha_emision") else None,
            "meses_al_vto": d["_meses"],
            "ultimo_precio": enrich.get("price"),
            "tea": enrich.get("TEA"),
            "tem": enrich.get("TEM"),
            "paridad": enrich.get("paridad"),
            "duration": enrich.get("duration"),
            "mod_duration": enrich.get("mod_duration"),
            "convexity": enrich.get("convexity"),
            "total_money_dia": vol.get("total_money"),
            "total_nominals_dia": vol.get("total_nominals"),
            "ts_ultimo_trade": ts_last.isoformat() if isinstance(ts_last, datetime) else ts_last,
        }
        # Badge para el frontend: este bono cotiza en la tabla tasa_fija por
        # tener su CER de liquidación ya publicado, pero nativamente es CER.
        if d["ticker"] in fijados_tickers:
            entry["cer_fijado"] = True
        # TC breakeven: sólo tasa_fija (nativa o CER fijada).
        if curva == "tasa_fija":
            entry["tc_breakeven"] = _tc_breakeven(
                enrich.get("price"), d.get("flujo_vencimiento"), mep_actual,
            )
        # Metadatos extra para curva CER:
        #   - is_zero_coupon: distingue Lecers (cupon_anual=0) de Boncers cupón.
        #   - cer_emision: factor de emisión, para des-indexar precios sucios
        #     a paridad real cuando se descompone el retorno (carry/rolldown
        #     se calculan sobre paridad, el cer_accrual se separa después).
        if curva == "cer":
            cupon = d.get("cupon_anual")
            entry["is_zero_coupon"] = (cupon is None) or (float(cupon) == 0.0)
            cer_em = d.get("cer_emision")
            if cer_em:
                entry["cer_emision"] = float(cer_em)
        out.append(entry)

    if ordenar_por == "vencimiento":
        out.sort(key=lambda x: x.get("fecha_vencimiento") or "9999")
    elif ordenar_por == "volumen_dia":
        out.sort(key=lambda x: -(x.get("total_money_dia") or 0))
    elif ordenar_por == "tea":
        out.sort(key=lambda x: (x.get("tea") is None, x.get("tea") or 0))
    elif ordenar_por == "duration":
        out.sort(key=lambda x: (x.get("duration") is None, x.get("duration") or 0))

    if limit and limit > 0:
        out = out[:limit]

    return out


@cached(ttl=60)
def get_historico_curva(curva: str) -> list:
    """Serie diaria por ticker de una curva: último precio + enriquecimiento.

    Lee Trading.SnapshotsCierre (1 doc por (curva, fecha, ticker)). Antes
    agregaba TimeSales con $group (caro: 750k+ docs). Ahora el cierre ya
    está pre-agregado por jobs/snapshot_cierre y backfill_snapshots_cierre.

    Incluye `tipo` (globales / bonares / etc) para que el frontend pueda
    pintar curvas separadas dentro del mismo chart.
    """
    db = get_db_trading()
    out = []
    cur = db["SnapshotsCierre"].find(
        {"curva": curva},
        {"_id": 0,
         "ts_cierre": 1, "ticker_corto": 1, "ticker": 1, "tipo": 1,
         "ultimo_precio": 1, "tea": 1, "tem": 1,
         "duration": 1, "paridad": 1},
    ).sort([("ts_cierre", 1), ("ticker", 1)])
    for r in cur:
        out.append({
            "fecha":    r.get("ts_cierre"),
            "ticker":   r.get("ticker_corto") or r.get("ticker"),
            "tipo":     r.get("tipo"),
            "price":    r.get("ultimo_precio"),
            "TEA":      r.get("tea"),
            "TEM":      r.get("tem"),
            "duration": r.get("duration"),
            "paridad":  r.get("paridad"),
        })
    return out
