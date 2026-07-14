"""Ingesta (escritura) — datos que ENTRAN desde fuera del Droplet.

`POST /api/ingest/dolar-oficial` — la PC de oficina (mae_forex) pollea MAE y, en
vez de escribir Mongo directo, manda el/los instrumento(s) acá; el Droplet (cuya
IP SÍ está whitelisteada en Atlas) los persiste en Valuaciones.DolarOficialLive.
Así la oficina NO necesita acceso directo a Mongo y Atlas se puede cerrar a la IP
del Droplet (adiós 0.0.0.0/0). Ver docs/SECURITY.md.

Auth en 2 capas:
  - CF Access (como todo api.acaquant.com): la oficina manda un service token.
  - X-Ingest-Token == config.DOLAR_INGEST_TOKEN (token DEDICADO; si se filtra,
    solo permite escribir el dólar, no da acceso a Mongo).
"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from config import DOLAR_INGEST_TOKEN
from core.dolar_oficial import upsert_oficial

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
