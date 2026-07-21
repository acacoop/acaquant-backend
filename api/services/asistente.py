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
from core import ai, pii_gateway

logger = logging.getLogger(__name__)

_TAREA = "asistente_negocio"   # su proveedor lo declara core/ai.py::_TAREAS
_MAX_HISTORIAL_TURNOS = 8   # turnos previos que se re-inyectan (re-tokenizados)

_SYSTEM = """Sos el asistente de negocio de la mesa de ACA Valores, para los jefes.
Respondés en castellano rioplatense, claro y ejecutivo: la conclusión primero,
después el detalle. Audiencia: dirección — sin tecnicismos innecesarios.

LARGO PROPORCIONAL: una pregunta simple se contesta en UNA línea con el número
pedido. Nada de volcar todo lo que sabés. El detalle y los desgloses solo si
los piden, o si sin ellos la respuesta engaña.

CADA PREGUNTA TIENE SU DATO — no sustituyas uno por otro:
- "cuánto OPERÓ" = VOLUMEN operado (compras/ventas) → volumen_operado.
- "cuánto FACTURÓ / aranceles" → aranceles_consolidado.
- "cuánto TIENE / cartera / posición / AuM / resultado" = patrimonio →
  rendimiento_cuenta.
Si te piden lo operado, responder con el patrimonio (o al revés) es RESPONDER
OTRA COSA: preferible decir "no tengo ese dato" antes que sustituirlo.

DATOS: no sabés ningún número de memoria. Todo dato sale de tus herramientas;
si no lo tenés, decís que no lo tenés — jamás inventás ni estimás. Las
herramientas son TUYAS e INVISIBLES: jamás las ofrezcas ni las nombres —
usalas y respondé.

PRIVACIDAD: los clientes aparecen como referencias tipo CLIENTE_1, CTA_2, y
los operadores comerciales como OPERADOR_1. Tratálas como nombres propios:
usalas tal cual en tu respuesta (el sistema las traduce después). Nunca
intentes adivinar a quién corresponden.

PERSONAS — NO ADIVINES QUÉ ES QUIÉN: cuando te nombran a alguien puede ser un
CLIENTE (una cuenta) o un OPERADOR comercial (empleado de la mesa). Son cosas
distintas y se consultan distinto. Usá `quien_es` para averiguarlo antes de
elegir la herramienta; si te dice que es ambiguo, PREGUNTALE al usuario cuál
quiere. Jamás asumas "es un operador" ni "es un cliente" por el nombre.

ALCANCE: solo lectura y análisis. No ejecutás órdenes, no modificás nada,
no prometés acciones. Si piden algo fuera de tu alcance, lo decís derecho.

FECHAS: cuando pidan un período con palabras ("este mes", "el semestre",
"junio"), traducilo vos a fechas ISO exactas para las herramientas usando la
fecha de hoy del encabezado. "El semestre" = del 1 de enero (o julio) al
último día de junio (o diciembre) del año en curso.

REGLAS DEL NEGOCIO (ya aplicadas por las herramientas — no las recalcules):
el VOLUMEN excluye los cierres de caución; los ARANCELES incluyen el arancel
de caución que vive en el cierre y van siempre en pesos. Si te preguntan por
qué difieren, esa es la razón — y es LA ÚNICA causa que podés afirmar:
PROHIBIDO inventar explicaciones de negocio que no estén en estas reglas o
en los datos ("X se arancela de otra manera", "tal operatoria factura más
porque..."). Si no sabés el porqué, decís que no lo sabés.

NÚMEROS Y ETIQUETAS:
- Cada cifra se presenta con LA UNIDAD que la herramienta dio (millones /
  mil millones ARS). JAMÁS conviertas a "billones" — es ambiguo en castellano.
- Cada dato se cita con SU nombre y SU fecha exactos: si la herramienta dice
  "snapshot 2026-07-21", no lo re-etiquetes como "cierre de ayer"; el PnL es
  ACUMULADO (no "del día", salvo la línea que dice "del día").
- Cuentas simples (sumar/restar/comparar números que ESTÁN en los datos)
  podés, redondeadas y presentadas como aproximación ("~"). Nada más
  elaborado: si falta un derivado, decilo."""


def _vocabulario_negocio() -> str:
    """Los VALORES REALES vigentes de los catálogos de Operaciones (mercados,
    tipos de operación, segmentos) — el idioma del negocio, leído de las
    mismas queries que alimentan los filtros de la vista (jamás hardcodeado,
    nunca stale). Mismo patrón que el guía v1.64. Best-effort: sin DB, el
    bloque no aparece y el asistente sigue."""
    try:
        from api.services import operaciones_sql as ops
        mercados = ops.ops_mercados()["mercados"]
        tipos = ops.ops_tipos_operacion()["tipos"]
        segmentos = ops.ops_segmentos()["segmentos"]
        niveles3 = ops.ops_niveles3()["niveles3"]
        return (
            "\n\nVOCABULARIO DEL NEGOCIO (valores reales vigentes — usalos tal "
            "cual en las herramientas):\n"
            f"- mercados: {', '.join(mercados)}\n"
            f"- tipos de operación: {', '.join(tipos[:40])}\n"
            f"- segmentos (nivel 1): {', '.join(segmentos)}\n"
            f"- segmentos del boleto (nivel 3): {', '.join(niveles3[:40])}\n"
            "- EQUIVALENCIAS que la gente usa: 'Rofex'/'Matba Rofex' → mercado "
            "A3 · 'FCI operado' → tipos Suscripción y Rescate · 'lo facturado' "
            "→ aranceles. Si nombran un mercado/segmento que NO está en las "
            "listas, aclaralo y ofrecé el más parecido de la lista.\n"
            "- OJO: en la dimensión instrumento, 'ARS'/'USD' son patas de "
            "EFECTIVO (movimientos de moneda), no títulos — si aparecen en un "
            "ranking de títulos, aclaralo o dejalos afuera del relato."
        )
    except Exception as e:
        logger.warning("asistente: vocabulario no disponible (%s) — sigo sin él", e)
        return ""


_VOCAB_TTL_S = 1800
_vocab_cache: dict = {"ts": 0.0, "texto": ""}


def _system_completo() -> str:
    import time
    ahora = time.monotonic()
    if ahora - _vocab_cache["ts"] > _VOCAB_TTL_S:
        _vocab_cache.update(ts=ahora, texto=_vocabulario_negocio())
    from datetime import UTC, datetime, timedelta
    hoy_art = (datetime.now(UTC) - timedelta(hours=3)).date().isoformat()
    return f"{_SYSTEM}\n\nHOY es {hoy_art}." + _vocab_cache["texto"]


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
    # FAIL-CLOSED de RUTEO: la tarea del asistente va a un proveedor con
    # no-retención (ver core/llm.py). Si ESE proveedor no está configurado, el
    # asistente se apaga — jamás cae al proveedor barato, que es adonde los
    # datos del negocio no deben ir (decisión del user 2026-07-21).
    if not ai.disponible(_TAREA):
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
        return {"ok": False, "motivo": "presupuesto", "cual": motivo,
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
        # los turnos del ASISTENTE son texto generado (sus números son
        # agregados legítimos — no re-tacharlos); los del usuario van con la
        # aduana completa (un número suelto SÍ puede ser una cuenta)
        limpio, mapping = pii_gateway.tokenize(
            contenido, mapping, texto_generado=(rol == "assistant"))
        historial_limpio.append(
            {"role": "assistant" if rol == "assistant" else "user", "content": limpio})
    mensaje_limpio, mapping = pii_gateway.tokenize(mensaje, mapping)

    texto, traza_id, _ctx = ai.completar_con_tools(
        _TAREA,
        system=_system_completo(),
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
        if motivo:
            return {"ok": False, "motivo": "presupuesto", "cual": motivo,
                    "chat_id": chat_id, "mensaje": "se agotó el cupo diario de IA"}
        return {"ok": False, "motivo": "llm", "chat_id": chat_id,
                "mensaje": "el asistente no pudo responder — probá de nuevo en un rato"}

    respuesta = pii_gateway.detokenize(texto, mapping)
    _persistir(chat_id, email, [("user", mensaje), ("assistant", respuesta)])
    return {"ok": True, "chat_id": chat_id, "respuesta": respuesta, "traza_id": traza_id}
