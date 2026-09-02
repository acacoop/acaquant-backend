"""`agente/pulso.py` — dónde se guarda el pulso del cliente. Doc: `docs/AGENT.md` §0.dg.

El router (`api/routers/pulso.py`) es plumbing; la escritura vive acá, junto
con la lectura (`agente/fuentes.pulsos`) y la regla que lo interpreta
(`sistema._vistas_ciegas`). Un pulso nunca levanta hacia el navegador: si la
base no está, la pantalla ya tiene bastante con estar ciega.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)


def registrar(*, email: str, vista: str, endpoint: str, motivo: str = "",
              desde_at: str | None = None) -> dict:
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agente.pulso_cliente (email, vista, endpoint, motivo, desde_at) "
                "VALUES (%s, %s, %s, %s, %s::timestamptz)",
                ((email or "")[:200], (vista or "")[:120], (endpoint or "")[:200],
                 (motivo or "")[:120], desde_at))
        return {"ok": True}
    except Exception as e:
        logger.warning("agente/pulso: no pude guardar el pulso de %s (%s)", vista, e)
        return {"ok": False, "error": type(e).__name__}
