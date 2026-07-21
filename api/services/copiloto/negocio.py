"""copiloto/negocio.py — vista NEGOCIO: el asistente de negocio (QuantAI P7)
DENTRO del mismo panel "Consultale a la IA" de siempre (decisión del user
2026-07-21: UN solo asistente, ninguna pantalla nueva).

A diferencia de las vistas de mercado, acá no hay tabla TSV: el cerebro es
`api/services/asistente.py` (la ADUANA core/pii_gateway + tools token-in/
token-out + transcript propio por conversación). Esta vista es el ADAPTADOR:
traduce el contrato del copiloto (pregunta/conv_id) al del asistente y su
respuesta al shape del panel. El motor la despacha por `handler` (sin fetch,
sin verificación de números — el asistente tiene su propia garantía: la IA
solo narra lo que las tools devuelven, ya tokenizado).

Gate: módulo RBAC `asistente` (admin-only por default) + `solo_internos`
(JAMÁS el portal invitado — REGLA #8). La memoria es server-side: el
transcript vive en manager.asistente_chats por conv_id — el historial que
manda el panel se ignora (contiene nombres reales ya detokenizados; el
asistente re-tokeniza el suyo propio al cargarlo).
"""
from __future__ import annotations


def _handler_negocio(*, pregunta: str, usuario: str | None, conv_id: str | None,
                     historial: list[dict] | None, params: dict | None) -> dict:
    """Adapta asistente.responder al contrato del panel. El conv_id del panel
    ES el chat_id del asistente (misma conversación = mismas fichas).

    Si el asistente no puede responder (su proveedor caído o sin credencial),
    NO se devuelve un panel muerto: se cae al GUÍA, que sin ver un solo dato
    igual puede LLEVAR al usuario a la vista con los filtros puestos
    (navegación asistida, v1.82). Degradar con gracia, no romper."""
    import logging

    from api.services import asistente

    logger = logging.getLogger(__name__)

    r = asistente.responder(mensaje=pregunta, email=usuario or "", chat_id=conv_id)
    if not r.get("ok"):
        if r.get("motivo") == "presupuesto":
            return {"ok": False, "error": f"presupuesto_{r.get('cual') or 'usuario'}"}
        # El presupuesto es del usuario y no se arregla navegando; el resto
        # (proveedor caído/sin key/aduana) sí tiene un plan B útil.
        logger.warning("negocio: asistente no disponible (%s) — caigo al guía",
                       r.get("motivo"))
        try:
            from .motor import preguntar

            guia = preguntar("ayuda", pregunta, historial=historial,
                             usuario=usuario, conv_id=conv_id)
            if guia.get("ok"):
                return guia
        except Exception as e:
            logger.warning("negocio: el guía tampoco respondió (%s)", e)
        return {"ok": False, "error": "ia_no_disponible"}

    traza_id = r.get("traza_id")
    if traza_id and conv_id:
        from .motor import _marcar_conversacion
        _marcar_conversacion(traza_id, conv_id)
    return {
        "ok": True,
        "respuesta": r["respuesta"],
        "traza_id": traza_id,
        "vista_sugerida": None,
        "numeros_sin_respaldo": [],
    }
