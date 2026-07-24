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
from core.eikon_chicago import universo_chicago, upsert_chicago
from core.eikon_live import set_rics, universo_rics, upsert_fundamentals, upsert_quotes

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
