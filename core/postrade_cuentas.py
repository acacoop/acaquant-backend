"""core/postrade_cuentas.py — cómo se LLAMA cada cuenta de la cámara.

La cámara identifica las cuentas por número y el reporte que la mesa manda por
mail habla de nombres. Ese nombre no salía de casa: **medido el 2026-08-24, 0 de
98 cuentas de `ap5` matchean contra `clientes.cuentas` ni `clientes.comitentes`**
— son cuentas de compensación de ACyRSA, no comitentes nuestros.

Pero la propia API lo publica. `PreTrade/AccountDetails` pide `accountCode` y
devuelve la denominación, el CUIT y la cuenta de neteo:

    149667 → ASOCIACION DE COOPERATIVAS ARGENTINAS COOP LTDA (CUIT 30500120882)
    155235 → ACA EXPORTACIÓN

**Es de a una cuenta por llamada** — no hay listado. `AccountList`,
`PartyDetails` y `DepositaryAccountList` contestan 404 (probado). Por eso hay
throttle y por eso el job lo hace UNA vez al día: 98 llamadas.

Traerlo de la fuente en vez de tipearlo importa porque un nombre tipeado se
desincroniza y nadie se entera. Lo que SÍ se escribe a mano es el `grupo`
(Cooperativas / MUNDO ACA), que la cámara no sabe y no se puede deducir del
nombre sin caer en la REGLA #9.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from core import postrade

logger = logging.getLogger(__name__)

METODO = "AccountDetails"

# Entre llamada y llamada. Son 98 pedidos de a uno contra un proveedor que no
# publica límite: ir despacio cuesta 15 segundos y evita que nos corten.
PAUSA_S = 0.15


def _texto(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def detalle(cuenta: str) -> dict | None:
    """Denominación, CUIT y cuenta de neteo de UNA cuenta. `None` si no resuelve.

    Devuelve `None` en vez de propagar para que una cuenta que la cámara no
    reconozca no voltee el refresco de las otras 97: no saber el nombre de una
    cuenta es un dato faltante, no un incidente.
    """
    try:
        crudo = postrade.leer(METODO, {"accountCode": cuenta})
    except Exception as e:  # se reporta y se sigue: una cuenta sin nombre no es un incidente
        logger.warning("AccountDetails(%s): %s: %s", cuenta, type(e).__name__, e)
        return None

    if isinstance(crudo, list):
        crudo = crudo[0] if crudo else {}
    if not isinstance(crudo, dict) or not crudo:
        return None

    return {
        "account": cuenta,
        "denominacion": _texto(crudo.get("Account")),
        "cuit": _texto(crudo.get("PartyId")),
        "netting": _texto(crudo.get("NettingAccountCode")),
    }


def traer(cuentas: list[str]) -> tuple[list[dict], list[str]]:
    """Resuelve varias cuentas. Devuelve `(resueltas, no_resueltas)`.

    Las que no resuelven se devuelven POR SEPARADO y no se descartan en
    silencio: un conteo de fallas es lo único que delata que la cámara dejó de
    reconocer una cuenta que sigue teniendo posición.
    """
    ok: list[dict] = []
    fallidas: list[str] = []
    for i, cuenta in enumerate(cuentas):
        d = detalle(cuenta)
        if d and d.get("denominacion"):
            ok.append(d)
        else:
            fallidas.append(cuenta)
        if i + 1 < len(cuentas):
            time.sleep(PAUSA_S)
    logger.info("AccountDetails: %d resueltas, %d sin nombre", len(ok), len(fallidas))
    return ok, fallidas
