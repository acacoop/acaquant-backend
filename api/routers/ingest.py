"""Ingesta (escritura) — datos que ENTRAN desde fuera del Droplet (PC de oficina).

`POST /api/ingest/dolar-oficial` — la PC de oficina (mae_forex) pollea MAE y manda
el/los instrumento(s) acá; la API los persiste en SQL `valuaciones.dolar_oficial_live`.
Así la oficina NO necesita acceso directo a la base. Ver docs/SECURITY.md.

`/api/ingest/eikon/*` — mismo patrón para el feed Eikon/Workspace
(`scripts/eikon_feed_simple.py`, PRUEBA): universo de underlyings+RICs (GET), RICs
resueltos por symbology (POST rics, solo llena vacíos) y quotes live del
subyacente US (POST quotes → SQL `mercado.eikon_snapshot`). Ver `core/eikon_live.py`.

Auth en 2 capas:
  - CF Access (como todo api.acaquant.com): la oficina manda un service token.
  - X-Ingest-Token == config.DOLAR_INGEST_TOKEN (token DEDICADO de la ingesta de
    la PC de oficina; si se filtra, solo permite escribir estas tablas de mercado).
"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from config import DOLAR_INGEST_TOKEN
from core.dolar_oficial import upsert_oficial
from core.eikon_bonos import universo_bonos_off, upsert_bonos_off, upsert_cierres_off
from core.eikon_chicago import universo_chicago, upsert_chicago
from core.eikon_live import set_rics, universo_rics, upsert_fundamentals, upsert_quotes
from core.eikon_news import universo_news, upsert_news
from core.eikon_segmentos import upsert_segmentos

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


def verify_ingest_token(x_ingest_token: str | None = Header(default=None)) -> None:
    """Valida el token dedicado de ingesta. 503 si no está configurado (fail-closed:
    sin token seteado, nadie escribe)."""
    if not DOLAR_INGEST_TOKEN:
        raise HTTPException(status_code=503, detail="ingesta deshabilitada (falta DOLAR_INGEST_TOKEN)")
    # compare_digest sobre bytes (mismo patrón que api/deps.py y api/mcp/auth.py):
    # tiempo constante — con `==` el token se puede extraer byte a byte midiendo
    # latencia. En bytes, un header con cualquier byte no-ASCII falla cerrado
    # (401) en vez de romper con TypeError.
    expected = DOLAR_INGEST_TOKEN.encode("utf-8")
    received = x_ingest_token.encode("utf-8", "ignore") if x_ingest_token else b""
    if not secrets.compare_digest(received, expected):
        raise HTTPException(status_code=401, detail="token de ingesta inválido")


class DolarOficialPayload(BaseModel):
    # Lista de instrumentos de MAE. Cada item: el instrumento crudo
    # ({ticker, codigoSegmento, codigoPlazo, precioUltimo, variacion, ...}) o
    # ya envuelto en {"data": {...}}. Acotado para no recibir cargas absurdas.
    docs: list[dict] = Field(..., min_length=1, max_length=200)


@router.post("/dolar-oficial")
def ingest_dolar_oficial(
    payload: DolarOficialPayload,
    _: None = Depends(verify_ingest_token),
) -> dict:
    escritos = upsert_oficial(payload.docs)
    if escritos == 0:
        raise HTTPException(status_code=422, detail="ningún doc válido (falta data.ticker)")
    return {"ok": True, "escritos": escritos}


# ── Feed Eikon/Workspace (PRUEBA) ────────────────────────────────────────────


@router.get("/eikon/universo")
def eikon_universo(_: None = Depends(verify_ingest_token)) -> dict:
    """Lista de suscripción del feed: underlyings US únicos de los CEDEARs activos
    + su RIC (None si falta → el feed lo resuelve por symbology)."""
    return {"universo": universo_rics()}


class EikonRicsPayload(BaseModel):
    # RICs resueltos por el feed: [{ticker: 'AAPL', ric: 'AAPL.O'}, ...]
    rics: list[dict] = Field(..., min_length=1, max_length=500)


@router.post("/eikon/rics")
def eikon_rics(
    payload: EikonRicsPayload,
    _: None = Depends(verify_ingest_token),
) -> dict:
    """Persiste RICs resueltos en mercado.cedears.ric — SOLO llena vacíos
    (lo cargado a mano desde Manager/set_ric no se pisa)."""
    return {"ok": True, "actualizados": set_rics(payload.rics)}


class EikonQuotesPayload(BaseModel):
    # Quotes del subyacente US: [{ticker, ric, last, bid, ask, ...}, ...]
    docs: list[dict] = Field(..., min_length=1, max_length=500)


@router.post("/eikon/quotes")
def eikon_quotes(
    payload: EikonQuotesPayload,
    _: None = Depends(verify_ingest_token),
) -> dict:
    escritos = upsert_quotes(payload.docs)
    if escritos == 0:
        raise HTTPException(status_code=422, detail="ningún doc válido (falta ticker)")
    return {"ok": True, "escritos": escritos}


@router.get("/eikon/chicago/universo")
def eikon_chicago_universo(_: None = Depends(verify_ingest_token)) -> dict:
    """Lista de suscripción de futuros CBOT del feed: [{ric, familia}]. Sale de
    la constante FAMILIAS (core/eikon_chicago.py) — no hay catálogo editable."""
    return {"universo": universo_chicago()}


class EikonChicagoPayload(BaseModel):
    # Quotes de futuros CBOT: [{ric, mes, last, var_neta}, ...] crudos (sin factor)
    docs: list[dict] = Field(..., min_length=1, max_length=100)


@router.post("/eikon/chicago/quotes")
def eikon_chicago_quotes(
    payload: EikonChicagoPayload,
    _: None = Depends(verify_ingest_token),
) -> dict:
    escritos = upsert_chicago(payload.docs)
    if escritos == 0:
        raise HTTPException(status_code=422, detail="ningún doc válido (RIC desconocido o falta ric)")
    return {"ok": True, "escritos": escritos}


@router.get("/eikon/bonos/universo")
def eikon_bonos_universo(_: None = Depends(verify_ingest_token)) -> dict:
    """Lista de suscripción de los soberanos OFFSHORE: [{ric, bono}]. Constante
    BONOS_OFF (core/eikon_bonos.py) — sin catálogo editable."""
    return {"universo": universo_bonos_off()}


class EikonBonosPayload(BaseModel):
    # Quotes offshore: [{ric, last, primact, bid, ask, var_pct, var_neta}, ...]
    docs: list[dict] = Field(..., min_length=1, max_length=50)


@router.post("/eikon/bonos/quotes")
def eikon_bonos_quotes(
    payload: EikonBonosPayload,
    _: None = Depends(verify_ingest_token),
) -> dict:
    escritos = upsert_bonos_off(payload.docs)
    if escritos == 0:
        raise HTTPException(status_code=422, detail="ningún doc válido (RIC desconocido o falta ric)")
    return {"ok": True, "escritos": escritos}


class EikonCierresPayload(BaseModel):
    # Cierres diarios HISTÓRICOS offshore para backfill: [{ric, fecha, valor}, ...]
    docs: list[dict] = Field(..., min_length=1, max_length=5000)


@router.post("/eikon/cierres")
def eikon_cierres(
    payload: EikonCierresPayload,
    _: None = Depends(verify_ingest_token),
) -> dict:
    """BACKFILL one-shot: persiste cierres diarios históricos de los bonos
    offshore en `mercado.eikon_cierres` (grupo='bonos_off') — llena las columnas
    7D/MTD/YTD de la watchlist mientras el cron acumula histórico. Idempotente."""
    escritos = upsert_cierres_off(payload.docs)
    if escritos == 0:
        raise HTTPException(status_code=422, detail="ningún cierre válido (RIC desconocido o falta fecha/valor)")
    return {"ok": True, "escritos": escritos}


@router.get("/eikon/news/universo")
def eikon_news_universo(_: None = Depends(verify_ingest_token)) -> dict:
    """RICs a los que el feed pide titulares: [{ric}]. Constante curada
    (core/eikon_news.py::RICS_NEWS_EQUITIES + bonos offshore)."""
    return {"universo": universo_news()}


class EikonNewsPayload(BaseModel):
    # Titulares: [{story_id, ric, fecha, titular, fuente}, ...]
    docs: list[dict] = Field(..., min_length=1, max_length=500)


@router.post("/eikon/news")
def eikon_news(
    payload: EikonNewsPayload,
    _: None = Depends(verify_ingest_token),
) -> dict:
    escritos = upsert_news(payload.docs)
    # 0 insertados es normal acá (dedup por story_id) — no es error.
    return {"ok": True, "insertados": escritos}


class EikonFundamentalsPayload(BaseModel):
    # Fundamentals curados por subyacente (1 doc por ticker, ~1 vez por día)
    docs: list[dict] = Field(..., min_length=1, max_length=500)


@router.post("/eikon/fundamentals")
def eikon_fundamentals(
    payload: EikonFundamentalsPayload,
    _: None = Depends(verify_ingest_token),
) -> dict:
    escritos = upsert_fundamentals(payload.docs)
    if escritos == 0:
        raise HTTPException(status_code=422, detail="ningún doc válido (falta ticker)")
    return {"ok": True, "escritos": escritos}


class EikonSegmentosPayload(BaseModel):
    # Desglose de ingresos por segmento: una fila por
    # (ticker, tipo, periodo, fecha, segmento). El universo entero son ~28
    # tickers × ~7 segmentos × 13 períodos × 2 tipos → el feed lo manda por
    # tandas; el tope cubre holgado la tanda más grande.
    docs: list[dict] = Field(..., min_length=1, max_length=5000)


@router.post("/eikon/segmentos")
def eikon_segmentos(
    payload: EikonSegmentosPayload,
    _: None = Depends(verify_ingest_token),
) -> dict:
    """Ingresos por SEGMENTO de negocio / región (TR.BGS.*), 1 vez por día.
    Las filas de TOTAL que manda Reuters se descartan server-side
    (`core.eikon_segmentos.es_total`) — ver el módulo para el porqué."""
    escritos = upsert_segmentos(payload.docs)
    if escritos == 0:
        raise HTTPException(
            status_code=422,
            detail="ningún doc válido (falta ticker/fecha/segmento/ingresos o eran todos totales)")
    return {"ok": True, "escritos": escritos}
