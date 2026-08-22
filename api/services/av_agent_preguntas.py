"""api/services/av_agent_preguntas.py — el AV AGENT le PREGUNTA al humano (E1.c).

Doc madre: **`docs/AV_AGENT.md`**.

**El problema que resuelve.** Un agente que necesita una decisión y no tiene cómo
pedirla se bloquea, y bloquea al proyecto: cada duda había que resolverla en un
chat, a mano, y la respuesta se perdía ahí. Acá el agente **convierte la duda en
una pregunta, la deja anotada y sigue con lo que sí puede hacer**. El user
contesta cuando puede — no cuando el agente corre.

**Tres propiedades que la hacen funcionar, y ninguna es opcional:**

1. **No repregunta.** La `clave` es única y estable (`falta:TZXD8`). Una
   herramienta que vuelve a preguntar lo mismo todas las noches se deja de leer,
   igual que una lista que repite lo descartado.
2. **Responder DISPARA un efecto**, no anota una opinión: `ignorar` escribe en
   `agente.av_agent_ignorados` y ese ticker no vuelve a salir nunca. Por eso
   `aplicada_at` es distinto de `respondida_at` — una respuesta cuyo efecto falló
   no puede quedar como si hubiera surtido.
3. **La pregunta se entiende sola.** Se lee semanas después de escrita, sin el
   hilo de chat que la originó: por eso lleva el contexto congelado adentro.

**Sin IA todavía, a propósito.** Las preguntas se arman deterministas desde los
hallazgos. Cuando llegue E4, el modelo va a redactarlas mejor y a agrupar las que
son la misma pregunta — pero el MECANISMO tiene que existir antes, porque es el
que convierte las respuestas en datos y no en mensajes.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Qué puede contestarse a una pregunta de hallazgo, y qué hace cada respuesta.
# `despues` existe para que "no sé todavía" sea una respuesta legítima: sin ella
# la única forma de no decidir es no contestar, y entonces no se distingue "lo
# pensé y lo dejo para después" de "no lo vi".
RESPUESTAS_FALTA = ("alta", "ignorar", "despues")

# Crear una curva: lo único que falta decidir es DE DÓNDE SALE SU TASA.
#   · `1816`  → se trae, igual que los TAMAR. Es configuración: el job la pide y
#               la vista la muestra. **Sin código.**
#   · `motor` → la calcula `engines/curvas.py` con el cash flow. La curva se crea
#               igual (los bonos ya aparecen con precio y duration), pero la TEA
#               llega cuando alguien escriba la rama de cálculo. Eso es
#               matemática, y la matemática no la escribe un agente.
RESPUESTAS_CURVA = ("1816", "motor", "despues")


def _row(r, cols: list[str]) -> dict:
    return dict(zip(cols, r, strict=False))


# ── Generación ───────────────────────────────────────────────────────────────


def preguntas_de_hallazgos(hallazgos: list[dict]) -> list[dict]:
    """Hallazgos → preguntas candidatas (PURA: sin base, testeable sin Postgres).

    Pregunta por DOS cosas, y las dos son decisiones que ninguna regla puede
    tomar sola:

    · **faltantes** — *"¿este bono nuevo nos interesa?"* depende de si la mesa lo
      opera, no de un dato.
    · **huecos de curva** — *"¿la tasa de esta curva la traigo de 1816 o la
      calcula el motor?"*. Es la única decisión que separa una curva que existe de
      una que no, y **responderla la CREA**.

    En cambio *"¿por qué este bono tiene paridad 150.000%?"* NO se pregunta: es
    trabajo del agente (E4), y mandársela sería delegarle al user el laburo que el
    agente vino a hacer.
    """
    out: list[dict] = []
    for h in hallazgos:
        tipo, ev = h.get("tipo"), (h.get("evidencia") or {})
        if tipo == "falta_en_base":
            out.append({
                "clave": f"falta:{h['ticker']}",
                "sujeto": h["ticker"], "causa": "falta_en_base",
                "tipo": "hallazgo",
                "pregunta": _texto_falta(h["ticker"], ev),
                "opciones": list(RESPUESTAS_FALTA),
                "contexto": ev,
            })
        elif tipo == "hueco_de_curva":
            aj = (ev.get("ajuste") or "").lower()
            tks = ev.get("tickers") or []
            muestra = ", ".join(tks[:5]) + (f" (+{len(tks) - 5})" if len(tks) > 5 else "")
            out.append({
                "clave": f"curva:{aj}",
                "sujeto": aj, "causa": "curva_sin_ajuste",
                "tipo": "hallazgo",
                # La pregunta NO es "¿creo la curva?" — eso ya está decidido por el
                # hecho de que hay bonos invisibles. Lo único que falta es de dónde
                # sale la tasa, así que se pregunta ESO y la curva se crea con la
                # respuesta. Una pregunta de sí/no seguida de otra de cómo son dos
                # clicks para una sola decisión.
                "pregunta": (
                    f"Hay {len(tks)} bono(s) con ajuste {aj.upper()} que no caen en "
                    f"ninguna curva y hoy no se ven en ningún lado ({muestra}). "
                    f"Creo la curva {aj.upper()} del lado {ev.get('lado', 'ARS')}: "
                    "¿su tasa la TRAIGO de 1816 (como los TAMAR) o la calcula "
                    "nuestro motor con el cash flow?"),
                "opciones": list(RESPUESTAS_CURVA),
                "contexto": ev,
            })
    return out


def _texto_falta(tk: str, ev: dict) -> str:
    """La pregunta de un faltante, con el contexto para poder contestarla.

    **Un ticker solo no es una pregunta contestable.** `M31G6` no le dice nada a
    nadie: hace falta QUIÉN emite y QUÉ es. El emisor y la denominación vienen
    del catálogo de 1816 (`emisorNombre` / `denominacion`), que el censo ya trae
    en el mismo crédito — no cuesta una llamada más.

    Y lo primero de todo es **si la casa ya lo tiene**: un bono en la tenencia
    que no está en `mercado.curvas` no valúa, así que ahí la respuesta deja de
    ser una preferencia y pasa a ser un arreglo pendiente."""
    partes: list[str] = []
    if ev.get("en_cartera"):
        partes.append("⚠ LO TENÉS EN CARTERA (hoy no valúa)")
    # Darlo de alta NO alcanza para verlo: sin pill, el bono queda cargado y no
    # aparece en ninguna pantalla. Decirlo ACÁ —y no después— es la diferencia
    # entre una decisión informada y cargar diez bonos que no se van a poder
    # mirar.
    if ev.get("ajuste_sin_curva"):
        aj = ((ev.get("ejes_sugeridos") or {}).get("ajuste") or "").upper()
        partes.append(f"⚠ el ajuste {aj} TODAVÍA NO TIENE CURVA en la app: si lo "
                      "das de alta no va a aparecer en ninguna tabla")
    emisor = (ev.get("emisor") or "").strip()
    if emisor:
        partes.append(emisor)
    den = (ev.get("denominacion") or "").strip()
    # La denominación repite el ticker en muchos casos ("AL30 - BONAR 2030"): se
    # muestra solo si agrega algo, si no es ruido en una lista de 24.
    if den and den.upper() != tk.upper():
        partes.append(den)
    curva = ev.get("curva_1816") or "?"
    partes.append(f"1816: «{curva}»")
    if ev.get("moneda"):
        partes.append(str(ev["moneda"]))
    vto = ev.get("vencimiento_1816")
    if vto:
        partes.append(f"vence {str(vto)[:10]}")
    return f"{tk} — " + " · ".join(partes) + ". ¿Lo damos de alta o no nos interesa?"


# ── LA PREGUNTA TAMBIÉN ES UN OBJETO (§0.bk) ────────────────────────────────
#
# Una pregunta abierta es un pendiente con la misma forma que todo lo demás:
# algo (un bono, una curva) sobre lo que falta una decisión. Espejarla en
# `agente.av_agent_items` le da lo que su tabla no tiene — **desde cuándo**
# está sin responder y cuántas veces volvió a hacerse necesaria.
#
# ⚠️ **La identidad sale de campos declarados, NO de partir la `clave`.** La
# clave es `falta:AL30`, o sea causa y sujeto pegados con dos puntos, y sería
# facilísimo separarla con un `split`. Pero ahí la identidad pasaría a depender
# de una convención de texto —justo lo que REGLA #9 prohíbe— y el día que una
# pregunta traiga un sujeto con `:` adentro partiría mal, en silencio. Una
# pregunta sin `sujeto`/`causa` **no se espeja**: prefiero que le falte la
# memoria a que la tenga mal.


def _espejar_pregunta(q: dict) -> None:
    """La pregunta, como objeto. Nunca levanta: es memoria, no es la lista."""
    sujeto, causa = (q.get("sujeto") or "").strip(), (q.get("causa") or "").strip()
    if not sujeto or not causa:
        return
    try:
        from api.services import av_agent_items
        av_agent_items.ver(
            tipo="pregunta", origen="pregunta", sujeto=sujeto, regla=causa,
            titulo=(q.get("pregunta") or "")[:300],
            afecta="hace falta una decisión", severidad="media")
    except Exception as e:                  # pragma: no cover - defensivo
        logger.warning("av_agent: no pude espejar la pregunta %s (%s)",
                       q.get("clave"), e)


def _cerrar_pregunta(q: dict, *, por: str = "") -> None:
    """Respondida = el pendiente se cierra. Lo cerró una PERSONA, así que va
    `resuelto` y arranca el seguimiento escalonado: si el mismo caso vuelve a
    aparecer, `ver()` lo pasa a `volvio` y eso es información."""
    sujeto, causa = (q.get("sujeto") or "").strip(), (q.get("causa") or "").strip()
    if not sujeto or not causa:
        return
    try:
        from api.services import av_agent_items
        from core import ciclo
        k = av_agent_items.clave_de_problema(sujeto, causa, "pregunta")
        av_agent_items.marcar(k, ciclo.RESUELTO, por=por or "persona")
    except Exception as e:                  # pragma: no cover - defensivo
        logger.warning("av_agent: no pude cerrar la pregunta %s (%s)",
                       q.get("clave"), e)


def registrar(preguntas: list[dict]) -> int:
    """Inserta las nuevas y REFRESCA el texto de las que siguen abiertas.
    → cuántas filas se tocaron.

    Dos reglas que conviven y no se contradicen:

    · **No se repregunta**: la `clave` es única, así que una pregunta ya hecha no
      genera otra fila.
    · **Pero sí se mejora el enunciado**: si el agente aprende a decirlo mejor
      (el 2026-08-16 se le sumaron emisor, denominación y «lo tenés en cartera»),
      las que están ABIERTAS tienen que reflejarlo. Con `DO NOTHING` las 21 que ya
      estaban se quedaban con el texto viejo para siempre y había que borrarlas a
      mano para verlas bien.

    **`WHERE estado = 'abierta'` es la parte que no se puede saltear**: una
    pregunta ya respondida se congela con el texto y el contexto que tenía cuando
    se contestó. Reescribirla haría que el historial diga que se decidió sobre
    una evidencia que en ese momento no existía."""
    if not preguntas:
        return 0
    filas = [(p["clave"], p["tipo"], p["pregunta"],
              json.dumps(p["opciones"], ensure_ascii=False),
              json.dumps(p.get("contexto") or {}, ensure_ascii=False, default=str),
              (p.get("sujeto") or "").strip() or None,
              (p.get("causa") or "").strip() or None)
             for p in preguntas]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO agente.av_agent_preguntas "
            "(clave, tipo, pregunta, opciones, contexto, sujeto, causa) "
            "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s) "
            "ON CONFLICT (clave) DO UPDATE SET "
            "  pregunta = EXCLUDED.pregunta, opciones = EXCLUDED.opciones, "
            "  contexto = EXCLUDED.contexto, "
            # Las viejas nacieron sin identidad (la columna es nueva): se
            # completa cuando vuelven a registrarse, pero NUNCA se borra con un
            # NULL de una pregunta que no la trae.
            "  sujeto = COALESCE(EXCLUDED.sujeto, agente.av_agent_preguntas.sujeto), "
            "  causa  = COALESCE(EXCLUDED.causa,  agente.av_agent_preguntas.causa) "
            "WHERE agente.av_agent_preguntas.estado = 'abierta'",
            filas)
        n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    for q in preguntas:
        _espejar_pregunta(q)
    return n


def registrar_decisiones(decisiones: list[dict]) -> int:
    """Las decisiones de DISEÑO del propio agente (alcance, política de conflicto).

    Su efecto no es automático: lo aplica el desarrollo siguiente. Se guardan
    igual para que la decisión quede asentada donde el agente la va a buscar, y
    no perdida en un chat."""
    return registrar([{**d, "tipo": "decision"} for d in decisiones])


# ── Lectura ──────────────────────────────────────────────────────────────────


_COLS = ["id", "clave", "tipo", "pregunta", "opciones", "contexto", "estado",
         "respuesta", "nota", "respondida_por", "respondida_at", "aplicada_at",
         # Para poder cerrar el objeto al responder: sin esto `responder()` no
         # sabría de qué problema habla la pregunta que acaba de contestar.
         "sujeto", "causa"]


def abiertas(limite: int = 200) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_COLS)} FROM agente.av_agent_preguntas "
                    "WHERE estado = 'abierta' ORDER BY tipo DESC, id LIMIT %s", (limite,))
        return [_row(r, _COLS) for r in cur.fetchall()]


def pendientes_de_aplicar() -> list[dict]:
    """Respuestas GUARDADAS cuyo efecto todavía no surtió (`aplicada_at IS NULL`).

    Es la cola de trabajo del agente: hoy son las `alta` esperando a E2 (dar de
    alta necesita bajar el cuadro de flujos y simular la TEA). Existe porque
    **una decisión tomada que no se ve en ningún lado se siente como una decisión
    perdida** — el user contestó 12 altas y no tenía dónde mirar qué pasó con
    ellas.

    Se excluye `despues`: eso no es una decisión pendiente de aplicar, es una
    decisión pospuesta a propósito."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, clave, respuesta, nota, respondida_por, respondida_at "
            "FROM agente.av_agent_preguntas "
            "WHERE estado = 'respondida' AND aplicada_at IS NULL "
            "  AND respuesta IS DISTINCT FROM 'despues' "
            "ORDER BY respondida_at", ())
        cols = ["id", "clave", "respuesta", "nota", "respondida_por", "respondida_at"]
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r, strict=False))
            d["respondida_at"] = d["respondida_at"].isoformat() if d["respondida_at"] else None
            d["ticker"] = d["clave"].split(":", 1)[1] if ":" in d["clave"] else d["clave"]
            out.append(d)
        return out


def resumen() -> dict:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT estado, count(*) FROM agente.av_agent_preguntas "
                    "GROUP BY estado")
        d = dict(cur.fetchall())
    return {"abiertas": d.get("abierta", 0), "respondidas": d.get("respondida", 0)}


# ── Respuesta + EFECTO ───────────────────────────────────────────────────────


class RespuestaInvalida(ValueError):
    """La respuesta no está entre las opciones de esa pregunta."""


def responder(id_pregunta: int, respuesta: str, *, por: str = "",
              nota: str = "") -> dict:
    """Guarda la respuesta y **aplica su efecto**. → la pregunta actualizada.

    El orden importa: primero el efecto, después el sello. Si el efecto falla, la
    pregunta queda respondida pero con `aplicada_at` en NULL y eso se ve — es la
    misma distinción que hace `saldo_cargado` en Tesorería entre "cargado en cero"
    y "sin cargar". Marcar como aplicada algo que no surtió es la clase de mentira
    que después nadie encuentra.
    """
    resp = (respuesta or "").strip().lower()
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_COLS)} FROM agente.av_agent_preguntas "
                    "WHERE id = %s", (id_pregunta,))
        fila = cur.fetchone()
    if not fila:
        raise RespuestaInvalida(f"no existe la pregunta {id_pregunta}")
    p = _row(fila, _COLS)
    opciones = p.get("opciones") or []
    if resp not in opciones:
        raise RespuestaInvalida(
            f"«{respuesta}» no es una opción de la pregunta {id_pregunta} "
            f"({', '.join(opciones)})")

    aplicada = _aplicar_efecto(p, resp, por=por, nota=nota)

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agente.av_agent_preguntas SET estado = 'respondida', "
            "respuesta = %s, nota = %s, respondida_por = %s, respondida_at = now(), "
            "aplicada_at = CASE WHEN %s THEN now() ELSE NULL END WHERE id = %s",
            (resp, nota or None, por or None, aplicada, id_pregunta))
    _cerrar_pregunta(p, por=por)
    return {**p, "estado": "respondida", "respuesta": resp, "aplicada": aplicada}


def _aplicar_efecto(p: dict, resp: str, *, por: str, nota: str) -> bool:
    """→ True si la respuesta surtió un efecto CONCRETO en la base.

    `alta` y `despues` devuelven False HOY y no es un bug: dar de alta un bono
    necesita bajar su cuadro de flujos y simular su TEA, que es E2. La respuesta
    queda guardada y el día que exista E2, esas son exactamente las que procesa.
    Decir que se aplicó algo que todavía no se puede hacer sería peor que decir
    que no."""
    clave = p.get("clave") or ""
    if p.get("tipo") != "hallazgo":
        return False

    from api.services import av_agent_acciones as acc

    if clave.startswith("falta:"):
        if resp != "ignorar":
            return False
        ticker = clave.split(":", 1)[1]
        motivo = nota or "el user lo marcó como «no nos interesa» desde el AV Agent"
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agente.av_agent_ignorados (ticker, motivo, por) "
                "VALUES (%s, %s, %s) ON CONFLICT (ticker) DO NOTHING",
                (ticker.upper(), motivo, por or None))
        acc.registrar(accion="ignorar_ticker", objetivo=ticker.upper(),
                      detalle={"motivo": motivo}, pregunta_id=p.get("id"), por=por)
        _ignorar_objetos(ticker.upper(), por=por)
        return True

    if clave.startswith("curva:"):
        # **Acá el agente CREA la curva.** Es la primera escritura suya que cambia
        # lo que la app muestra, y es segura por construcción: el catálogo SUMA
        # curvas y no puede redefinir una existente (`curvas_catalogo.crear` lo
        # rechaza), así que ningún bono que hoy se ve puede cambiar de tabla.
        if resp not in ("1816", "motor"):
            return False
        # LA PARADA cubre esta puerta porque crear una curva **cambia lo que la
        # app muestra**. No cubre `ignorar_ticker` ni los AVISOS, que son la
        # anotación de una decisión humana y no un dato de mercado: frenar al
        # agente tiene que dejarte seguir triando, o el primer reflejo ante una
        # duda sería quedarse sin la herramienta. El modal dice exactamente esto.
        from api.services import av_agent_control
        if av_agent_control.guardia("crear_curva"):
            return False
        from core import curvas_catalogo
        ctx = p.get("contexto") or {}
        aj = clave.split(":", 1)[1]
        lado = str(ctx.get("lado") or "ARS")
        try:
            r = curvas_catalogo.crear(
                ajuste=aj, lado=lado, fuente_valuacion=resp,
                orden=90,          # las nuevas van al final de su lado
                nota=nota or f"creada desde el AV Agent (valuación: {resp})",
                por=por)
        except Exception as e:
            logger.warning("av_agent: no se pudo crear la curva %s: %s", clave, e)
            # El intento FALLIDO también se anota: un libro que solo registra los
            # éxitos esconde justo el caso que uno va a querer investigar.
            acc.registrar(accion="crear_curva", objetivo=aj, ok=False,
                          error=str(e)[:300], detalle={"lado": lado, "fuente": resp},
                          pregunta_id=p.get("id"), por=por)
            return False
        acc.registrar(accion="crear_curva", objetivo=aj,
                      detalle={"lado": lado, "fuente_valuacion": resp,
                               "bonos_afectados": ctx.get("tickers") or [],
                               "ya_existia": not r.get("creada")},
                      pregunta_id=p.get("id"), por=por)
        return True

    return False


def _ignorar_objetos(ticker: str, *, por: str = "",
                     desandar: bool = False) -> None:
    """El «no me interesa», también en los objetos (§0.bl).

    Sin esto, `av_agent_ignorados` filtraba los hallazgos de la pantalla pero
    los objetos seguían abiertos, acumulando `veces` y antigüedad de algo que
    el user ya dijo que no le importa. Dos verdades sobre lo mismo — REGLA #9
    otra vez, y la que peor se ve: la pantalla dice «no hay nada» y el
    contador dice «lleva 20 días».
    """
    try:
        from api.services import av_agent_items
        av_agent_items.ignorar_sujeto(ticker, por=por, desandar=desandar)
    except Exception as e:                  # pragma: no cover - defensivo
        logger.warning("av_agent: no pude ignorar los objetos de %s (%s)",
                       ticker, e)


def ignorar(ticker: str, *, motivo: str = "", por: str = "") -> dict:
    """**«Sacalo de la lista por HOY» — un snooze, NO una blacklist** (§0.cv).

    ⚠️⚠️ **CAMBIO DE SEMÁNTICA (2026-08-22).** La primera versión escribía
    `av_agent_ignorados` (permanente, por ticker, todas las causas) y el user
    lo usó creyendo que descartaba EL AVISO del día: *«si yo lo ignoro quiero
    que salga de ENCONTRAR — NO que entre en una blacklist de cosas que nunca
    más me van a interesar. Si el agente funciona bien y algo se rompe hoy,
    mañana lo va a volver a detectar, y TIENE que volver a aparecer»*.

    Ahora el botón IGNORAR de un hallazgo:

    - **NO escribe `av_agent_ignorados`** (esa tabla queda SOLO para la
      respuesta «no nos interesa» de las preguntas de alta — ahí sí es un
      juicio durable sobre el PAPEL, y sigue siendo reversible en DECIDIDO).
    - Mueve los OBJETOS del sujeto a `ignorado` → la fila sale de LA LISTA ya.
    - Y el snooze **vence solo**: cuando un detector vuelve a ver el problema
      en un día ART POSTERIOR, `av_agent_items.ver()` lo reabre como `nuevo`.
      Si el detector no lo ve más, no vuelve — que es exactamente lo que
      distingue «lo ignoré y se arregló» de «lo ignoré y sigue roto».

    Sigue siendo por SUJETO (todas sus causas): con vencimiento diario el
    alcance amplio dejó de ser peligroso, y el gesto típico es sacar el bono
    entero de la vista de hoy.
    """
    from api.services import av_agent_acciones as acc

    tk = (ticker or "").strip().upper()
    if not tk:
        raise ValueError("ticker vacío")
    motivo = (motivo or "").strip() or "lo sacó de la lista por hoy"
    acc.registrar(accion="ignorar_hoy", objetivo=tk, por=por,
                  detalle={"motivo": motivo, "desde": "hallazgo"})
    _ignorar_objetos(tk, por=por)
    return {"ok": True, "ticker": tk, "ignorado": True, "hasta": "hoy"}


def designorar(ticker: str, *, por: str = "") -> dict:
    """Deshace un «no me interesa»: saca el ticker de la lista y **reabre su
    pregunta**, para que el agente vuelva a proponerlo.

    Reabrir la pregunta es la mitad que importa: sin eso el ticker volvería a
    salir como hallazgo pero sin nada que contestar, y la decisión quedaría
    colgada entre "ya la contesté" y "no la contesté".

    **La reversibilidad es lo que hace barata la decisión.** Si `ignorar` fuera
    irreversible desde la app, la respuesta segura pasaría a ser no contestar
    nada — y el canal de preguntas dejaría de usarse."""
    from api.services import av_agent_acciones as acc

    tk = (ticker or "").strip().upper()
    if not tk:
        raise ValueError("ticker vacío")
    with get_pool().connection() as conn, conn.cursor() as cur:
        # El motivo se lee ANTES de borrar: es el `antes` del libro, y es lo único
        # que permite reconstruir por qué se había ignorado.
        cur.execute("SELECT motivo, por FROM agente.av_agent_ignorados "
                    "WHERE ticker = %s", (tk,))
        prev = cur.fetchone()
        cur.execute("DELETE FROM agente.av_agent_ignorados WHERE ticker = %s", (tk,))
        borrado = cur.rowcount or 0
        cur.execute(
            "UPDATE agente.av_agent_preguntas SET estado = 'abierta', "
            "respuesta = NULL, respondida_at = NULL, aplicada_at = NULL, "
            "nota = NULL, respondida_por = %s WHERE clave = %s",
            (por or None, f"falta:{tk}"))
        reabierta = (cur.rowcount or 0) > 0
    acc.registrar(accion="designorar", objetivo=tk, por=por,
                  detalle={"pregunta_reabierta": reabierta},
                  antes=({"motivo": prev[0], "por": prev[1]} if prev else None))
    _ignorar_objetos(tk, por=por, desandar=True)
    return {"ok": True, "ticker": tk, "borrado": borrado > 0, "reabierta": reabierta}


# ── Parseo del comando del user ──────────────────────────────────────────────


def parsear_respuestas(texto: str) -> list[tuple[int, str]]:
    """`"3=alta,5-9=ignorar"` → `[(3,'alta'), (5,'ignorar'), …]`. PURA.

    Acepta RANGOS porque la forma real de contestar 24 preguntas de faltantes es
    "estas cinco sí, el resto no", y obligar a tipear 24 asignaciones en la
    consola web del Droplet garantiza que no se conteste nunca (REGLA #0: lo que
    es doloroso de tipear ahí, no se usa).
    """
    out: list[tuple[int, str]] = []
    for parte in (texto or "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "=" not in parte:
            raise ValueError(f"«{parte}» no tiene la forma ID=respuesta")
        izq, resp = parte.split("=", 1)
        izq, resp = izq.strip(), resp.strip().lower()
        if not resp:
            raise ValueError(f"«{parte}» no tiene respuesta")
        if "-" in izq:
            a, b = izq.split("-", 1)
            try:
                desde, hasta = int(a), int(b)
            except ValueError as e:
                raise ValueError(f"rango inválido en «{parte}»") from e
            if hasta < desde:
                raise ValueError(f"rango al revés en «{parte}»")
            out += [(i, resp) for i in range(desde, hasta + 1)]
        else:
            try:
                out.append((int(izq), resp))
            except ValueError as e:
                raise ValueError(f"«{izq}» no es un id") from e
    return out


def responder_lote(texto: str, *, por: str = "", nota: str = "") -> dict:
    """Aplica un lote y **nunca corta a la mitad**: una respuesta inválida no
    puede impedir que las otras 23 se guarden. Devuelve qué entró y qué no —
    con el motivo de cada rechazo, porque un lote que dice "falló" sin decir cuál
    obliga a repetirlo entero.

    Un id que no existe (típico de un rango que se pasa) se cuenta como `omitido`,
    no como error: es la forma natural de decir "de la 5 a la 30, todas ignorar"."""
    resultados: list[dict] = []
    ok = omitidos = fallidos = 0
    for id_p, resp in parsear_respuestas(texto):
        try:
            r = responder(id_p, resp, por=por, nota=nota)
            ok += 1
            resultados.append({"id": id_p, "respuesta": resp, "ok": True,
                               "aplicada": r.get("aplicada", False)})
        except RespuestaInvalida as e:
            if "no existe" in str(e):
                omitidos += 1
                resultados.append({"id": id_p, "ok": False, "omitido": True})
            else:
                fallidos += 1
                resultados.append({"id": id_p, "ok": False, "error": str(e)})
        except Exception as e:  # la base falló: se reporta, no se traga
            fallidos += 1
            resultados.append({"id": id_p, "ok": False, "error": f"{type(e).__name__}: {e}"})
    return {"ok": ok, "omitidos": omitidos, "fallidos": fallidos,
            "detalle": resultados}


# ── Las decisiones de DISEÑO abiertas (docs/AV_AGENT.md §4) ──────────────────
#
# Viven acá y no solo en el doc para que el agente pueda PREGUNTARLAS: una
# decisión que solo existe en un markdown depende de que alguien lo lea.
DECISIONES_ABIERTAS: list[dict[str, Any]] = [
    {"clave": "decision:alcance",
     "pregunta": ("¿Qué universo querés que mire el agente? «soberanos» son 104 "
                  "en 1816; «todo» son 887 (667 corporativos, donde la escala del "
                  "flujo varía mucho más)."),
     "opciones": ["soberanos", "no_corporativos", "todo"]},
    {"clave": "decision:conflicto",
     "pregunta": ("Cuando nuestro cuadro de flujos y el de 1816 difieren (caso "
                  "AER9O: 48.215,20 contra 49.710,88, un 3,1%), ¿1816 PISA "
                  "nuestro dato o solo lo reporta para que decidas vos?"),
     "opciones": ["pisa", "reporta"]},
    {"clave": "decision:diagnostico",
     "pregunta": ("El diagnóstico con IA (E4): ¿barre las 221 curvas todas las "
                  "noches buscando tasas raras, o es una herramienta que le "
                  "apuntás a un bono cuando ves algo?"),
     "opciones": ["barrido", "apuntado"]},
]
