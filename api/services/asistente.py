"""asistente — orquestador del ASISTENTE DE NEGOCIO (QuantAI P7, docs/QUANTAI.md).

Chatbot para los jefes: preguntan en lenguaje natural sobre el negocio
(operaciones / cartera / mercado) y responde en castellano. Admin-only
(módulo RBAC `asistente`), JAMÁS en el portal invitado (REGLA #8).

INVARIANTE DE SEGURIDAD (congelada por test): a core.llm no le llega NUNCA
texto sin pasar por la aduana (core/pii_gateway). El flujo completo:

    mensaje (real) ──tokenize──► mensaje limpio ──► core.ai.completar_con_tools
                                                        │  (loop de tools, máx 4
                                                        │   rondas — guarda de
                                                        │   costo del gateway)
    tools: reciben fichas, resuelven ids DENTRO del perímetro,
           devuelven resultados ya tokenizados (asistente_tools)
                                                        │
    respuesta (en fichas) ◄─────────────────────────────┘
    detokenize ──► respuesta con nombres reales ──► transcript SQL (perímetro)

- El mapping ficha↔identidad se persiste por chat_id (manager.asistente_mappings)
  y nunca viaja. El transcript (manager.asistente_chats) guarda los nombres
  REALES — vive en el perímetro, control de acceso por rol.
- FAIL-CLOSED: sin catálogo de clientes la aduana no puede garantizar el
  tachado → el asistente se niega a responder (mejor mudo que filtrando).
- READ-ONLY absoluto: las tools solo leen; acá no hay ninguna escritura de
  negocio (solo transcript/mapping propios).
- La traza en ia.trazas guarda el texto TOKENIZADO (detalle/respuesta): es el
  registro auditable de qué salió exactamente del perímetro.
"""
from __future__ import annotations

import logging
import uuid

from api.services import asistente_tools
from core import ai, llm, pii_gateway

logger = logging.getLogger(__name__)

_MAX_HISTORIAL_TURNOS = 8   # turnos previos que se re-inyectan (re-tokenizados)

_SYSTEM = """Sos el asistente de negocio de la mesa de ACA Valores, para los jefes.
Respondés en castellano rioplatense, claro y ejecutivo: la conclusión primero,
después el detalle. Audiencia: dirección — sin tecnicismos innecesarios.

DATOS: no sabés ningún número de memoria. Todo dato sale de tus herramientas;
si no lo tenés, decís que no lo tenés — jamás inventás ni estimás. Las
herramientas son TUYAS e INVISIBLES: jamás las ofrezcas ni las nombres —
usalas y respondé.

PRIVACIDAD: los clientes aparecen como referencias tipo CLIENTE_1, CTA_2.
Tratálas como nombres propios: usalas tal cual en tu respuesta (el sistema
las traduce después). Nunca intentes adivinar a quién corresponden.

ALCANCE: solo lectura y análisis. No ejecutás órdenes, no modificás nada,
no prometés acciones. Si piden algo fuera de tu alcance, lo decís derecho."""


def _cargar_historial(chat_id: str) -> list[tuple[str, str]]:
    """Últimos turnos del transcript (rol, contenido REAL) — se re-tokenizan
    antes de viajar. Best-effort: sin historial el chat arranca de cero."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT rol, contenido FROM ("
                "  SELECT rol, contenido, ts, id FROM manager.asistente_chats "
                "  WHERE chat_id = %s ORDER BY ts DESC, id DESC LIMIT %s"
                ") ult ORDER BY ts ASC, id ASC",
                (chat_id, _MAX_HISTORIAL_TURNOS))
            return [(r[0], r[1]) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("asistente: no pude leer el historial de %s (%s)", chat_id, e)
        return []


def _persistir(chat_id: str, email: str, turnos: list[tuple[str, str]]) -> None:
    """Transcript con nombres REALES — perímetro. Best-effort."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            for rol, contenido in turnos:
                cur.execute(
                    "INSERT INTO manager.asistente_chats (chat_id, email, rol, contenido) "
                    "VALUES (%s, %s, %s, %s)",
                    (chat_id, (email or "").lower(), rol, contenido))
    except Exception as e:
        logger.warning("asistente: no pude persistir el transcript de %s (%s)", chat_id, e)


def responder(*, mensaje: str, email: str, chat_id: str | None = None) -> dict:
    """Un turno del chat. Devuelve {ok, chat_id, respuesta} o
    {ok: False, motivo, mensaje} — nunca levanta."""
    mensaje = (mensaje or "").strip()
    if not mensaje:
        return {"ok": False, "motivo": "vacio", "mensaje": "escribí una pregunta"}
    if not llm.configurado():
        return {"ok": False, "motivo": "ia_apagada",
                "mensaje": "el asistente no está configurado en este entorno"}
    # FAIL-CLOSED: sin el catálogo de clientes la aduana no garantiza el
    # tachado por nombre → no se responde (seguridad antes que servicio).
    if not pii_gateway.catalogo_disponible():
        logger.error("asistente: catálogo de clientes NO disponible — me niego (fail-closed)")
        return {"ok": False, "motivo": "aduana",
                "mensaje": "el asistente no está disponible en este momento"}
    motivo = ai.motivo_presupuesto(email)
    if motivo:
        return {"ok": False, "motivo": "presupuesto",
                "mensaje": ("se agotó tu cupo diario de IA"
                            if motivo == "usuario" else
                            "se agotó el cupo diario de IA del sistema")}

    chat_id = (chat_id or "").strip() or uuid.uuid4().hex
    mapping = pii_gateway.cargar_mapping(chat_id, email)
    if mapping is None:  # el chat pertenece a otro usuario
        return {"ok": False, "motivo": "chat_ajeno",
                "mensaje": "esa conversación no te pertenece"}

    # ── LA ADUANA: de acá para abajo solo viaja texto tokenizado ─────────────
    historial_limpio: list[dict] = []
    for rol, contenido in _cargar_historial(chat_id):
        limpio, mapping = pii_gateway.tokenize(contenido, mapping)
        historial_limpio.append(
            {"role": "assistant" if rol == "assistant" else "user", "content": limpio})
    mensaje_limpio, mapping = pii_gateway.tokenize(mensaje, mapping)

    texto, traza_id, _ctx = ai.completar_con_tools(
        "asistente_negocio",
        system=_SYSTEM,
        user=mensaje_limpio,
        tools=asistente_tools.TOOLS,
        ejecutar=lambda nombre, args: asistente_tools.ejecutar(nombre, args, mapping=mapping),
        usuario=email,
        detalle=mensaje_limpio[:200],   # la traza guarda SOLO texto tokenizado
        historial=historial_limpio,
    )
    # el mapping pudo crecer (tools que tokenizaron resultados) → persistir
    pii_gateway.guardar_mapping(chat_id, email, mapping)

    if not texto:
        motivo = ai.motivo_presupuesto(email)
        return {"ok": False, "motivo": "llm", "chat_id": chat_id,
                "mensaje": ("se agotó el cupo diario de IA" if motivo else
                            "el asistente no pudo responder — probá de nuevo en un rato")}

    respuesta = pii_gateway.detokenize(texto, mapping)
    _persistir(chat_id, email, [("user", mensaje), ("assistant", respuesta)])
    return {"ok": True, "chat_id": chat_id, "respuesta": respuesta, "traza_id": traza_id}
