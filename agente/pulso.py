"""`agente/pulso.py` — dónde se guarda lo que reporta el NAVEGADOR. Doc: `docs/AGENT.md` §0.dg.

Dos cosas, MISMA tabla y mismo endpoint, separadas por `tipo` porque llegan como
la misma frase («se me colgó la app») y se arreglan en lugares opuestos:
**`ciega`** = los pedidos fallan y el navegador anda bien; **`tilde`** = el
navegador no responde (hilo principal bloqueado N ms) y nada falla.

El router (`api/routers/pulso.py`) es plumbing; la escritura vive acá, junto
con la lectura (`agente/fuentes.pulsos`) y la regla que lo interpreta
(`sistema._vistas_ciegas`). Un pulso nunca levanta hacia el navegador: si la
base no está, la pantalla ya tiene bastante con estar ciega.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)


# Los DOS tipos de «se me colgó la app», que se arreglan en lugares opuestos:
#   ciega → los pedidos fallan y el navegador anda bien (backend, proxy o red).
#   tilde → el navegador NO responde: el hilo principal quedó bloqueado N ms.
#           Nada falla, nadie tira una excepción, y sin este aviso el servidor
#           no se entera nunca de que la pantalla se clavó.
# Un tipo desconocido entra como `ciega`: es el caso viejo, y el default no
# puede inventar una categoría que ningún detector mira.
TIPOS = ("ciega", "tilde")


def _datos_json(datos: dict | None) -> str:
    """El `datos` del navegador, acotado ANTES de serializar.

    ⚠️ Cortar el STRING ya serializado (`json.dumps(...)[:4000]`) es la trampa
    obvia y rompe callado: deja un JSON inválido, el `::jsonb` falla, el except
    se lo come y el aviso se pierde justo cuando había algo que contar. Se acota
    la estructura y recién después se serializa.
    """
    import json

    if not isinstance(datos, dict):
        return "{}"
    recortado = {}
    for k, v in list(datos.items())[:20]:
        if isinstance(v, str):
            v = v[:200]
        elif not isinstance(v, (int, float, bool, type(None))):
            v = str(v)[:200]
        recortado[str(k)[:40]] = v
    texto = json.dumps(recortado)
    return texto if len(texto) <= 4000 else "{}"


def registrar(*, email: str, vista: str, endpoint: str, motivo: str = "",
              desde_at: str | None = None, tipo: str = "ciega",
              ms: int | None = None, datos: dict | None = None) -> dict:
    if tipo not in TIPOS:
        tipo = "ciega"
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agente.pulso_cliente "
                "(email, vista, endpoint, motivo, desde_at, tipo, ms, datos) "
                "VALUES (%s, %s, %s, %s, %s::timestamptz, %s, %s, %s::jsonb)",
                ((email or "")[:200], (vista or "")[:120], (endpoint or "")[:200],
                 (motivo or "")[:120], desde_at, tipo,
                 int(ms) if ms is not None else None, _datos_json(datos)))
        return {"ok": True}
    except Exception as e:
        logger.warning("agente/pulso: no pude guardar el pulso de %s (%s)", vista, e)
        return {"ok": False, "error": type(e).__name__}
