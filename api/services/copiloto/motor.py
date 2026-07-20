"""copiloto/motor.py — el orquestador: preguntar() (una pregunta → contexto →
LLM → verificación/reflexion → respuesta), el VIGÍA determinista, la memoria
persistente y el feedback. Ata registro + verificacion + derivacion + base."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from .base import (
    _MAX_CHARS_MENSAJE,
    _MAX_CHARS_PREGUNTA,
    _MAX_FILAS,
    _MAX_HISTORIAL,
    _SYSTEM_BASE,
    _TONO_POR_ROL,
    _tsv,
)
from .derivacion import _RE_VISTA_MARKER, _bloque_otras_vistas, _extraer_vista_sugerida
from .registro import VISTAS
from .trading import (
    _estado_mercado,
    _fetch_trading,
    _radar_candidatos,
    _sanear_params_trading,
)
from .verificacion import (
    _RE_DERRAME,
    _exceso_de_cifras,
    _jerga_en_respuesta,
    _numeros_sin_respaldo,
    _periodos_sin_respaldo,
)

logger = logging.getLogger(__name__)

def _marcar_conversacion(traza_id: int | None, conv_id: str | None) -> None:
    """Etiqueta la traza con la conversación (best-effort, sin tocar el
    gateway: el id ya lo tenemos de vuelta)."""
    if not traza_id or not conv_id:
        return
    try:
        from core.postgres import get_pool

        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE ia.trazas SET conv_id = %s WHERE id = %s",
                        (conv_id[:64], traza_id))
    except Exception as e:
        logger.warning("copiloto: no pude marcar la conversación (%s)", e)


def preguntar(
    vista: str,
    pregunta: str,
    historial: list[dict] | None = None,
    usuario: str | None = None,
    conv_id: str | None = None,
    params: dict | None = None,
) -> dict:
    """Una pregunta sobre la tabla de la vista. Devuelve ok=True con la
    respuesta + fuente + traza_id (para feedback), u ok=False con motivo —
    nunca levanta (el panel degrada, la vista no se rompe)."""
    cfg = VISTAS.get(vista)
    if cfg is None:
        return {"ok": False, "error": "vista_desconocida"}
    pregunta = (pregunta or "").strip()[:_MAX_CHARS_PREGUNTA]
    if not pregunta:
        return {"ok": False, "error": "pregunta_vacia"}

    # Presupuesto ANTES de armar nada: si el tope ya está agotado, el error
    # dice CUÁL ("tu límite" vs "el del sistema") — el gateway re-chequea igual.
    from core.ai import motivo_presupuesto

    motivo = motivo_presupuesto(usuario)
    if motivo:
        return {"ok": False, "error": f"presupuesto_{motivo}"}

    try:
        filas = cfg["fetch"](params)
    except Exception as e:
        logger.warning("copiloto %s: no pude leer los datos (%s)", vista, e)
        filas = None
    if not filas:
        return {"ok": False, "error": "datos_no_disponibles"}

    enriquecer = cfg.get("enriquecer")
    if enriquecer:
        try:
            filas = enriquecer(filas)
        except Exception as e:
            logger.warning("copiloto %s: enriquecer falló (%s) — sigo sin derivadas", vista, e)

    truncado = len(filas) > _MAX_FILAS
    tabla = _tsv(filas[:_MAX_FILAS], cfg["columnas"], cfg.get("celda_max", 60))
    # timespec MINUTES a propósito: el proveedor cachea el prefijo repetido del
    # prompt (~10x más barato). Con segundos, el encabezado cambiaba en CADA
    # pregunta y rompía el prefijo; al minuto, las preguntas seguidas sobre la
    # misma tabla comparten caché. La precisión al segundo no aportaba nada.
    generado = datetime.now(UTC).isoformat(timespec="minutes")

    partes = [
        f"TABLA: {cfg['titulo']} — {min(len(filas), _MAX_FILAS)} instrumentos"
        + (f" (recortada de {len(filas)})" if truncado else "")
        + f" — datos al {generado}",
        "<datos>",
        tabla,
        "</datos>",
    ]
    extras = cfg.get("extras")
    if extras:
        try:
            partes.extend(extras(filas[:_MAX_FILAS], pregunta, historial or [], params))
        except Exception as e:
            logger.warning("copiloto %s: extras fallaron (%s) — sigo sin detalle", vista, e)
    for h in (historial or [])[-_MAX_HISTORIAL:]:
        p, r = (h.get("pregunta") or "").strip(), (h.get("respuesta") or "").strip()
        if p and r:
            partes.append(f"[pregunta previa] {p[:_MAX_CHARS_MENSAJE]}")
            partes.append(f"[tu respuesta previa] {r[:_MAX_CHARS_MENSAJE]}")
    partes.append(f"PREGUNTA: {pregunta}")

    from core.ai import completar_con_traza

    # Tier de modelo: la vista puede fijarlo (trading → siempre PRO, ahí se
    # opera en vivo); si no, se decide POR PREGUNTA: análisis profundo /
    # conversación avanzada / consigna larga → pro, el resto → flash
    # (_es_profunda, determinista). Decisión user 2026-07-20.
    from .base import _es_profunda

    tarea = cfg.get("tarea") or (
        "copiloto_vista_pro" if _es_profunda(pregunta, historial) else "copiloto_vista"
    )

    # Audiencia por rol RBAC: el mismo dato, contado distinto según quién pregunta
    tono = ""
    if usuario:
        try:
            from core.roles import get_user_role

            tono = _TONO_POR_ROL.get(get_user_role(usuario), "")
        except Exception:  # roles caídos → tono neutro, jamás corta la pregunta
            tono = ""
    system = (_SYSTEM_BASE + (f"\n{tono}" if tono else "") + "\n" + cfg["reglas"]
              + _bloque_otras_vistas(usuario, vista))

    contexto = "\n".join(partes)
    texto, traza_id = completar_con_traza(
        tarea,
        system=system,
        user=contexto,
        usuario=usuario,
        detalle=pregunta,  # queda en la traza → panel OBSERVABILIDAD
    )
    if not texto:
        # Si el presupuesto se agotó DURANTE la llamada (el pre-chequeo de
        # arriba había pasado justo por debajo del tope), el genérico "IA no
        # disponible" confundía — se re-chequea para nombrar el motivo real.
        motivo = motivo_presupuesto(usuario)
        if motivo:
            return {"ok": False, "error": f"presupuesto_{motivo}"}
        return {"ok": False, "error": "ia_no_disponible"}

    # Reflexion (QUANTAI, nonparametric): números sin respaldo o jerga interna
    # NO se muestran — el modelo recibe su respuesta con el detalle exacto y
    # la reescribe. Solo si tras el reintento queda algo, sale la advertencia
    # de números (la jerga residual se loguea, no se le muestra al usuario).
    malos, chequeados = _numeros_sin_respaldo(texto, contexto)
    jerga = _jerga_en_respuesta(texto, cfg, pregunta)
    derrame = bool(_RE_DERRAME.search(texto))
    exceso = _exceso_de_cifras(texto, pregunta)
    fantasmas = _periodos_sin_respaldo(texto, contexto)
    if malos or jerga or derrame or exceso or fantasmas:
        logger.warning(
            "copiloto %s: %d/%d números sin respaldo %s · jerga %s · derrame=%s · "
            "exceso_cifras=%d · períodos fantasma %s — autocorrección",
            vista, len(malos), chequeados, malos, jerga, derrame, exceso, fantasmas,
        )
        problemas = []
        if malos:
            problemas.append(
                f"estos números NO aparecen en los datos: {', '.join(malos)} — usá solo "
                "números exactos de los datos (si abreviás un monto con M, redondeá el "
                "real; y si redondeás un %, redondeá AL MÁS CERCANO con un decimal: "
                "-83.56 se escribe -83.6, jamás -83)"
            )
        if jerga:
            problemas.append(
                "usaste jerga interna del sistema que el usuario JAMÁS debe ver: "
                f"{', '.join(jerga)} — traducila a lenguaje de mesa"
            )
        if derrame:
            problemas.append(
                "mostraste correcciones o razonamiento intermedio — entregá SOLO la "
                "respuesta final, limpia"
            )
        if exceso:
            problemas.append(
                f"usaste {exceso} cifras para una pregunta puntual — elegí MÁXIMO 3 "
                "números (los que sostienen la conclusión) y contá el resto en "
                "palabras (fuerte, apenas, casi plano); la respuesta tiene que "
                "leerse de un tirón"
            )
        if fantasmas:
            problemas.append(
                f"afirmaste algo sobre {', '.join(fantasmas)} pero tus datos NO tienen "
                "ese período — eliminá TODA referencia y juicio sobre períodos que no "
                "están en los datos (no los reemplaces por otra afirmación inventada)"
            )
        correccion = (
            f"{contexto}\n[tu respuesta previa]\n{texto}\n"
            f"[verificación automática] {'; '.join(problemas)}. Reescribí la respuesta "
            "COMPLETA corregida, mismo formato y largo, sin mencionar esta corrección."
        )
        texto2, traza_id2 = completar_con_traza(
            tarea,  # la autocorrección usa el MISMO tier que la respuesta original
            system=system,
            user=correccion,
            usuario=usuario,
            detalle=f"[autocorrección] {pregunta}",
        )
        if texto2:
            malos2, _ = _numeros_sin_respaldo(texto2, contexto)
            jerga2 = _jerga_en_respuesta(texto2, cfg, pregunta)
            derrame2 = bool(_RE_DERRAME.search(texto2))
            exceso2 = _exceso_de_cifras(texto2, pregunta)
            fant2 = _periodos_sin_respaldo(texto2, contexto)
            if (len(malos2) + len(jerga2) + int(derrame2) + int(bool(exceso2)) + len(fant2)
                    < len(malos) + len(jerga) + int(derrame) + int(bool(exceso))
                    + len(fantasmas)):
                texto, traza_id, malos = texto2, traza_id2, malos2

    # Política estricta (user 2026-07-11: "lo que se dice TIENE QUE SER, y si
    # no, no se dice"): si tras la auto-corrección siguen quedando números sin
    # respaldo, la respuesta NO se muestra. Información financiera verificada
    # o nada.
    if malos:
        logger.warning("copiloto %s: verificación fallida tras reintento (%s) — NO se muestra",
                       vista, malos)
        return {"ok": False, "error": "verificacion"}

    texto, vista_sugerida = _extraer_vista_sugerida(texto, vista, usuario)

    # HANDOFF TRANSPARENTE A LA GUÍA (pedido user 2026-07-20: "te tiene que
    # guiar DIRECTO"): si un copiloto de DATOS derivó a la guía ([[VISTA:ayuda]]),
    # mandarle al usuario "no lo tengo + botón" es un REBOTE. En su lugar, la
    # misma pregunta se le hace a la vista ayuda acá adentro y se devuelve SU
    # respuesta (la receta concreta de navegación). Profundidad 1: ayuda jamás
    # deriva a ayuda. Si el handoff falla, cae a la respuesta original.
    if (vista != "ayuda" and (vista_sugerida or {}).get("vista") == "ayuda"):
        try:
            guia = preguntar("ayuda", pregunta, historial=historial,
                             usuario=usuario, conv_id=conv_id)
            if guia.get("ok"):
                return guia
        except Exception as e:
            logger.warning("copiloto %s: handoff a la guía falló (%s)", vista, e)

    _marcar_conversacion(traza_id, conv_id)
    return {
        "ok": True,
        "respuesta": texto,
        "traza_id": traza_id,
        "vista_sugerida": vista_sugerida,
        "numeros_sin_respaldo": [],
        "fuente": {
            "vista": vista,
            "titulo": cfg["titulo"],
            "filas": min(len(filas), _MAX_FILAS),
            "generado": generado,
        },
    }


# ── El VIGÍA (vista trading) — reactividad SIN LLM ──────────────────────────
#
# Watchers deterministas elegidos por el user (2026-07-12):
#  T1: una de SUS tarjetas toca o está muy cerca de un nivel de pivots.
#  T2: un ticker que NO tiene, del TOP 15 por volumen y con ±4% en la rueda,
#      se acerca a algún nivel (candidato a estrategia 1/2 que no está mirando).
# El front lo pollea; cada alerta es template fijo (cero tokens). El botón
# "¿lo miramos?" del toast recién ahí abre el copiloto (una llamada).

_VIGIA_UMBRAL_CARDS = 0.20   # % al nivel para "tocó / muy cerca" en tus tarjetas
# (los umbrales del radar T2 viven en trading.py junto a _radar_candidatos)


def vigia(params: dict | None = None) -> dict:
    """Evalúa los disparadores del vigía. Determinista, sin IA. Devuelve
    {estado, alertas:[{id, tipo, ticker, mensaje, ...}]}. Fuera de rueda no
    dispara (no hay nada que operar); en zona muerta dispara PERO cada
    mensaje lleva la advertencia de disciplina."""
    from datetime import timedelta

    ahora_art = datetime.now(UTC) - timedelta(hours=3)
    estado, _lectura = _estado_mercado(ahora_art, ahora_art.weekday() < 5)
    if estado.startswith(("CERRADO", "PRE-APERTURA")):
        return {"estado": estado, "alertas": []}
    en_zona_muerta = estado.startswith("ZONA MUERTA")
    sufijo_zm = (" ⚠ zona muerta (13-16): el toque con este volumen vale poco."
                 if en_zona_muerta else "")
    hoy = ahora_art.date().isoformat()
    alertas: list[dict] = []

    # T1 — tarjetas del usuario en nivel (con SUS overrides re-aplicados)
    tickers_cards, _sel, _ov = _sanear_params_trading(params)
    try:
        for f in _fetch_trading(params):
            dist = f.get("_nivel_dist")
            if dist is None or abs(dist) > _VIGIA_UMBRAL_CARDS:
                continue
            tk, nivel = f["ticker"], f["_nivel_nombre"]
            alertas.append({
                "id": f"card:{tk}:{nivel}:{hoy}",
                "tipo": "nivel_card",
                "ticker": tk,
                "nivel": nivel,
                "mensaje": f"{tk} tocó {nivel} ({f['_nivel_precio']:.0f}) — "
                           f"está a {dist:+.2f}%.{sufijo_zm}",
                "pregunta": f"{tk} acaba de llegar a {nivel}: leeme libro y tape, "
                            "¿hay señal o dejo pasar?",
            })
    except Exception as e:
        logger.warning("vigía T1 falló (%s)", e)

    # T2 — radar: top volumen + mover ±4% + cerca de nivel, fuera de tus cards.
    # La lógica vive en trading._radar_candidatos (compartida con el bloque
    # [radar] del copiloto — una sola fuente de candidatos).
    try:
        alertas.extend(_radar_candidatos(tickers_cards, sufijo_zm, hoy))
    except Exception as e:
        logger.warning("vigía T2 falló (%s)", e)

    return {"estado": estado, "alertas": alertas}


def historial_persistido(usuario: str, limit: int = 8) -> dict:
    """Memoria persistente SIN tabla nueva: las trazas YA guardan cada
    intercambio. Devuelve SOLO la última CONVERSACIÓN del usuario (cada chat
    es su propio mundo — pedido del user 2026-07-12) + su conv_id para que el
    panel la continúe. Sin conversaciones etiquetadas → vacío (mundo nuevo)."""
    try:
        from core.postgres import get_pool

        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT conv_id FROM ia.trazas WHERE usuario = %s AND conv_id IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (usuario,),
            )
            fila = cur.fetchone()
            if not fila:
                return {"conv_id": None, "mensajes": []}
            conv_id = fila[0]
            cur.execute(
                """
                SELECT id, detalle, respuesta, feedback
                FROM ia.trazas
                WHERE usuario = %s AND conv_id = %s AND tarea = 'copiloto_vista' AND ok
                  AND respuesta IS NOT NULL AND detalle IS NOT NULL
                  AND detalle NOT LIKE '[autocorrección]%%'
                ORDER BY id DESC LIMIT %s
                """,
                (usuario, conv_id, max(1, min(int(limit), 20))),
            )
            filas = cur.fetchall()
        return {
            "conv_id": conv_id,
            "mensajes": [
                # la traza guarda la respuesta CRUDA del modelo (antes del
                # post-proceso) → el marcador [[VISTA:x]] se limpia acá también
                # o reaparece literal al restaurar el chat (bug del shadow)
                {"traza_id": r[0], "pregunta": r[1],
                 "respuesta": _RE_VISTA_MARKER.sub("\n", r[2]).strip(),
                 "feedback": r[3]}
                for r in reversed(filas)
            ],
        }
    except Exception as e:
        logger.warning("copiloto historial: no pude leer (%s) — panel arranca vacío", e)
        return {"conv_id": None, "mensajes": []}


def registrar_feedback(traza_id: int, valor: int, usuario: str) -> dict:
    """👍/👎 sobre una respuesta propia: valor 1 o -1 sobre ia.trazas.feedback.
    Solo trazas del mismo usuario (nadie califica llamadas ajenas)."""
    if valor not in (1, -1):
        return {"ok": False, "error": "valor_invalido"}
    try:
        from core.postgres import get_pool

        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE ia.trazas SET feedback = %s WHERE id = %s AND usuario = %s",
                (valor, traza_id, usuario),
            )
            return {"ok": cur.rowcount == 1}
    except Exception as e:
        logger.warning("copiloto feedback: no pude registrar (%s)", e)
        return {"ok": False, "error": "db_no_disponible"}
