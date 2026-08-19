"""api/services/av_agent_evals.py — el EVAL SET del AV AGENT.

Doc madre: **`docs/AV_AGENT.md`**.

**La medición es la piedra angular** — y hasta hoy no existía. El agente ya
diagnostica y arregla, pero nadie puede decir con un número **cuánto acierta**, y
sin eso cada paso hacia la autonomía es un acto de fe. Es exactamente lo que dice
el propio doc del agente desde el primer día: *«un agente que diagnostica sin
poder medir si acertó no es un agente, es un generador de opiniones»*.

**El dataset ya lo teníamos y no lo estábamos guardando**: cada vez que el user
abre un diagnóstico y decide (aplicar, ignorar, corregir a mano), está emitiendo
un juicio sobre si la causa era la correcta. Ese juicio se perdía. Acá se guarda,
con la evidencia del caso, y se convierte en **precisión por causa**.

Para qué sirve, concretamente:

  · **decide qué se puede automatizar.** Una causa con 30 votos y 100% de acierto
    es candidata a lane automática; una con 60% no se toca. El número reemplaza a
    la corazonada, que es la única forma de ganar autonomía sin jugarse nada.
  · **muestra dónde miente el agente.** Si `escala_del_cuadro` acierta 9/10 y
    `sin_ejes` 3/10, la segunda regla está mal escrita — y eso hoy no se ve.
  · **es la línea de base.** El día que entre un LLM al diagnóstico, la pregunta
    «¿mejoró?» va a tener respuesta en vez de opinión.

**Un voto NO cambia nada del sistema.** No re-clasifica el hallazgo ni corrige el
dato: es una anotación sobre el AGENTE, no sobre el bono. Mezclarlas haría que
corregir el diagnóstico parezca arreglar el problema.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cuántos votos hacen falta para que un porcentaje signifique algo. Con menos, el
# número se muestra igual pero marcado: 2 de 2 no es "100% de acierto", es "casi
# no hay evidencia" — y presentarlo como lo primero es cómo se toman decisiones
# de autonomía con ruido.
MIN_VOTOS = 10


def votar(*, caso: str, dominio: str, causa: str, acierta: bool,
          nota: str = "", causa_correcta: str = "", por: str = "",
          origen: str = "humano", ref: str = "") -> dict:
    """Registra el juicio sobre UN diagnóstico.

    `caso` es el sujeto (el ticker de un bono o el id de un chequeo) y `causa` es
    lo que dijo el agente. **El mismo caso se puede votar varias veces** y todas
    quedan: si el agente cambia de opinión sobre LOC6O dentro de un mes, la
    historia de los dos juicios es justamente lo que dice si mejoró.

    `origen='derivado'` + `ref` es para los votos que NO son un click sino una
    deducción de una acción aprobada. Con `ref`, sembrar dos veces no duplica:
    el índice único lo rechaza y acá se devuelve `duplicado`.
    """
    caso, causa = (caso or "").strip(), (causa or "").strip()
    if not caso or not causa:
        return {"ok": False, "error": "falta el caso o la causa"}
    if not acierta and not (causa_correcta or nota).strip():
        # Un ✖ sin motivo no es un dato: no se puede aprender de «está mal».
        return {"ok": False,
                "error": "un voto negativo necesita la causa correcta o una nota "
                         "— sin eso no se puede aprender del error"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mercado.av_agent_evals "
                "(caso, dominio, causa, acierta, causa_correcta, nota, por, "
                " origen, ref) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                # Sembrar es idempotente: el mismo origen no vota dos veces.
                "ON CONFLICT (ref) WHERE ref IS NOT NULL DO NOTHING RETURNING id",
                (caso.upper(), dominio, causa, acierta,
                 (causa_correcta or "").strip() or None, (nota or "").strip() or None,
                 por or None, origen, (ref or "").strip() or None))
            fila = cur.fetchone()
            vid = fila[0] if fila else None
    except Exception as e:
        logger.warning("av_agent_evals: no se pudo guardar el voto", exc_info=True)
        return {"ok": False, "error": str(e)[:200]}
    if vid is None and ref:
        return {"ok": True, "duplicado": True, "caso": caso.upper(), "causa": causa}
    return {"ok": True, "id": vid, "caso": caso.upper(), "causa": causa,
            "acierta": acierta, "origen": origen}


def resumen() -> dict:
    """**La precisión por causa**, que es el tablero que decide qué se automatiza.

    `suficiente` separa un porcentaje con respaldo de uno con tres votos. Un
    número sin esa marca invita a leer 2/2 como «100%», que es exactamente el
    error que hace tomar decisiones de autonomía sobre ruido.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT dominio, causa, count(*), "
                "       count(*) FILTER (WHERE acierta), max(creado_at), "
                # **Los HUMANOS se cuentan aparte.** Son los que abren la
                # compuerta; los derivados solo dan contexto.
                "       count(*) FILTER (WHERE origen = 'humano'), "
                "       count(*) FILTER (WHERE origen = 'humano' AND acierta) "
                "FROM mercado.av_agent_evals GROUP BY dominio, causa "
                "ORDER BY count(*) DESC")
            filas = cur.fetchall()
            cur.execute(
                "SELECT caso, causa, causa_correcta, nota, por, creado_at "
                "FROM mercado.av_agent_evals WHERE NOT acierta "
                "ORDER BY creado_at DESC LIMIT 20")
            fallos = cur.fetchall()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "causas": [], "fallos": []}

    causas = []
    for d, c, n, ok, ultimo, nh, okh in filas:
        n, ok = int(n), int(ok or 0)
        nh, okh = int(nh or 0), int(okh or 0)
        causas.append({
            "dominio": d, "causa": c, "votos": n, "aciertos": ok,
            "precision": round(ok / n, 4) if n else None,
            # ⚠️ **LA COMPUERTA CUENTA SOLO LOS HUMANOS.** Un voto derivado sale
            # de una acción que alguien aprobó —una señal real, pero más débil— y
            # si contara para `candidata_a_auto`, el agente podría habilitarse
            # solo: propone, el humano aprueba por otra razón, y eso se lee como
            # «el diagnóstico acertó 10 de 10». Los derivados dan CONTEXTO.
            "humanos": nh, "aciertos_humanos": okh,
            "derivados": n - nh,
            "precision_humana": round(okh / nh, 4) if nh else None,
            "suficiente": nh >= MIN_VOTOS,
            "ultimo_at": ultimo.isoformat() if ultimo else None,
            # La recomendación NO es un permiso: es lo que el número habilita a
            # DISCUTIR. La lane automática se prende a mano, siempre.
            "candidata_a_auto": bool(nh >= MIN_VOTOS and okh == nh)})
    total = sum(c["votos"] for c in causas)
    aciertos = sum(c["aciertos"] for c in causas)
    humanos = sum(c["humanos"] for c in causas)
    return {
        "ok": True, "total": total, "aciertos": aciertos,
        "precision": round(aciertos / total, 4) if total else None,
        "humanos": humanos, "derivados": total - humanos,
        "min_votos": MIN_VOTOS,
        "causas": causas,
        "fallos": [{"caso": f[0], "causa_dicha": f[1], "causa_correcta": f[2],
                    "nota": f[3], "por": f[4],
                    "creado_at": f[5].isoformat() if f[5] else None}
                   for f in fallos]}
