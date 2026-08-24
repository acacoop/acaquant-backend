"""Cierre de hallazgos por SUJETO, para la cadena de alta.

Existe por un solo caso: el diagnóstico de un bono prueba que está sano y hay
que cerrar lo que el agente tenía abierto sobre él. Es un cierre **POR ACCIÓN**
—el diagnóstico corrió y dio bien— así que si vuelve, reincide.
"""
from __future__ import annotations

import logging

from agente import tipos
from core.postgres import get_pool

logger = logging.getLogger(__name__)


def resolver_sujeto(sujeto: str, *, motivo: str = "", por: str = "") -> dict:
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE agente.hallazgos SET estado = %s, cerrado_at = now(), "
                "  cerrado_como = %s, cerrado_por = %s, arreglo_aplicado = %s "
                "WHERE upper(sujeto) = upper(%s) AND estado = ANY(%s)",
                (tipos.RESUELTO, tipos.POR_ACCION, por, motivo or "",
                 sujeto, list(tipos.ABIERTOS)))
            return {"ok": True, "cerrados": cur.rowcount or 0}
    except Exception as e:
        logger.warning("agente/items: no pude cerrar %s (%s)", sujeto, e)
        return {"ok": False, "error": str(e)}
