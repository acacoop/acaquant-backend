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

from api.cache import cached, invalidate
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cuántos votos hacen falta para que un porcentaje signifique algo. Con menos, el
# número se muestra igual pero marcado: 2 de 2 no es "100% de acierto", es "casi
# no hay evidencia" — y presentarlo como lo primero es cómo se toman decisiones
# de autonomía con ruido.
MIN_VOTOS = 10


def clave_caso(s: str | None) -> str:
    """**LA identidad de un caso en el eval set — la única normalización.**

    ⚠️⚠️ Existe por el bug que hizo que los votos sobre filas de SISTEMA no se
    recordaran NUNCA (2026-08-22). `votar()` guardaba `caso.upper()` y los dos
    lectores (`ya_votados`, `es_ruido`) — y la vista al buscar — comparaban el
    caso SIN upper. Un bono (`BPOA7`) ya viene en mayúscula y matcheaba *de
    casualidad*; `manager.salud_eventos`, `motor_cedears` o `Finnhub news`
    quedaban guardados en mayúscula y buscados en minúscula: el voto se escribía
    (hasta 9 veces — el dedup leía sin upper y tampoco encontraba el previo) y
    los botones volvían intactos en cada recarga, para siempre.

    Es REGLA #9 en su forma exacta: la misma identidad escrita en dos lugares
    con dos criterios, cero errores, cero logs. Por eso la normalización vive
    UNA vez y la usan el que escribe Y todos los que leen — un test exige que
    ningún acceso por caso quede fuera.
    """
    return (s or "").strip().upper()


def _voto_previo(caso: str, causa: str, origen: str = "humano") -> bool | None:
    """El último voto de esa CLASE sobre ese par, o `None` si nunca se votó.

    ⚠️ **El `origen` es un parámetro y antes estaba clavado en `'humano'`** — el
    mismo bug que `ya_votados`, un nivel más abajo. `votar()` llama a esto para
    no guardar dos veces la misma respuesta, y como la observación se guarda
    como `utilidad`, el previo NUNCA aparecía: cada «✔ sirve» escribía una fila
    nueva. La dedup existía y no dedupeaba nada.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT acierta FROM mercado.av_agent_evals "
                " WHERE caso = %s AND causa = %s AND origen = %s "
                " ORDER BY creado_at DESC LIMIT 1", (caso, causa, origen))
            r = cur.fetchone()
        return bool(r[0]) if r else None
    except Exception as e:
        # Ante la duda NO se bloquea el voto: perder uno es peor que repetirlo.
        logger.warning("evals: no pude mirar el voto previo (%s)", e)
        return None


# ⚠️⚠️ **UN FILTRO NO PUEDE SERVIR A DOS PREGUNTAS.** Acá vivía el bug que hizo
# que el user votara «✔ sirve» decenas de veces sobre la misma fila y los
# botones volvieran intactos en cada recarga:
#
#     ya_votados()  →  WHERE origen = 'humano'
#     la observación se guarda con  origen = 'utilidad'
#
# El voto SE GUARDABA —`votar()` hasta lo deduplica— y **la pantalla no podía
# verlo nunca**. Cero errores, cero logs: la fila volvía igual, para siempre.
#
# La causa de fondo: `utilidad` se separó de `humano` para que las
# observaciones no inflen la compuerta de autonomía (§0.f), y eso está BIEN.
# Pero «¿esto cuenta para dar autonomía?» y «¿ya me contestaste?» son dos
# preguntas distintas, y quedaron compartiendo un `WHERE`. La compuerta tiene
# que ser estricta; la pantalla tiene que acordarse de TODO lo que contestaste.
VOTOS_DE_PERSONA = ("humano", "utilidad")


def ya_votados() -> dict[tuple[str, str], bool]:
    """`(caso, causa) → qué contestaste`, para **todo lo que contestó una
    persona** — el «¿acertó?» y el «¿te sirve verlo?».

    Lo usa la pantalla para **no volver a pedir un voto ya emitido**. Es UNA
    query para toda la lista: preguntarlo por hallazgo serían 100 viajes.

    ⚠️ Incluye `utilidad` a propósito. Para la COMPUERTA de autonomía sigue
    contando solo `humano` — eso no se toca y tiene su propio filtro.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (caso, causa) caso, causa, acierta, origen "
                "  FROM mercado.av_agent_evals WHERE origen = ANY(%s) "
                " ORDER BY caso, causa, creado_at DESC",
                (list(VOTOS_DE_PERSONA),))
            # clave_caso también al LEER: la base ya viene en mayúscula, pero
            # normalizar solo al escribir es tener el criterio en un lado y la
            # fe en el otro.
            return {(clave_caso(c), ca): bool(a) for c, ca, a, _ in cur.fetchall()}
    except Exception as e:
        logger.warning("evals: no pude leer los votados (%s)", e)
        return {}


def es_ruido() -> set[tuple[str, str]]:
    """Los `(caso, causa)` que una persona marcó **«✖ es ruido»**.

    ⚠️⚠️ **Estos votos se escribían y NO LOS LEÍA NADIE.** Medido: cero
    consultas en todo el repo. O sea que «es ruido» era un botón que guardaba
    una opinión en una tabla y dejaba la fila exactamente donde estaba — que es
    la peor versión posible de un botón, porque parece que hizo algo.

    Se lee del ÚLTIMO voto: destildar («cambiar» en la pantalla) lo devuelve.
    Que sea reversible no es un detalle — si esconder algo fuera para siempre,
    la respuesta segura pasaría a ser no marcar nada.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (caso, causa) caso, causa, acierta "
                "  FROM mercado.av_agent_evals WHERE origen = 'utilidad' "
                " ORDER BY caso, causa, creado_at DESC")
            return {(clave_caso(c), ca) for c, ca, a in cur.fetchall() if not a}
    except Exception as e:
        logger.warning("evals: no pude leer lo marcado como ruido (%s)", e)
        return set()


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
    # La identidad se fija ACÁ, antes de todo: el dedup de abajo y el INSERT
    # tienen que hablar del mismo caso (clave_caso — ver su docstring: el bug
    # de los votos de sistema que no se recordaban nunca).
    caso, causa = clave_caso(caso), (causa or "").strip()
    if not caso or not causa:
        return {"ok": False, "error": "falta el caso o la causa"}

    # ⚠️ **NO SE VOTA DOS VECES LO MISMO** (user, 2026-08-20: *«otra vez lo mismo,
    # ya lo completé 40 veces y sigue apareciendo»*). Los BOPREALes llegaron a
    # 17/17: el mismo bono, la misma causa, votado en cada rueda.
    #
    # El docstring de abajo decía que re-votar es información porque el agente
    # puede cambiar de opinión — y es cierto, pero solo si **cambia la CAUSA**.
    # Repetir el MISMO juicio sobre el MISMO par no agrega un dato: infla el
    # denominador, y sobre todo convierte la pantalla en un formulario que hay
    # que volver a llenar todos los días. Eso no mide al agente, mide la
    # paciencia del que vota (§0.ai).
    #
    # Cambiar de opinión SÍ entra: si el `acierta` es distinto, es una
    # corrección y se guarda.
    # `utilidad` es el voto de una OBSERVACIÓN («¿te sirve verla?»). Dedup igual
    # que el humano: repetir «no me sirve» todos los días es el mismo formulario.
    if origen in ("humano", "utilidad") and not ref:
        previo = _voto_previo(caso, causa, origen)
        if previo is not None and previo == bool(acierta):
            return {"ok": True, "duplicado": True,
                    "error": "ya votaste esta causa para este caso"}
    # ⚠️ **El «no» de una OBSERVACIÓN no necesita explicación**: «no me sirve
    # verla» ES la explicación entera. Exigirle una nota sería fricción sobre la
    # única respuesta que el usuario puede dar sin investigar nada — y esa
    # fricción es justo lo que hace que nadie conteste.
    if not acierta and origen != "utilidad" and not (causa_correcta or nota).strip():
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
                # `caso` ya está normalizado por clave_caso arriba — un .upper()
                # suelto acá es exactamente el bug que clave_caso vino a matar.
                (caso, dominio, causa, acierta,
                 (causa_correcta or "").strip() or None, (nota or "").strip() or None,
                 por or None, origen, (ref or "").strip() or None))
            fila = cur.fetchone()
            vid = fila[0] if fila else None
    except Exception as e:
        logger.warning("av_agent_evals: no se pudo guardar el voto", exc_info=True)
        return {"ok": False, "error": str(e)[:200]}
    # **Se invalida acá.** Sin esto, votar y no ver el número moverse hasta un
    # minuto después se lee como que el voto no se guardó — y es justo la duda
    # que hace que la gente deje de votar.
    invalidate("precision_por_causa")
    if vid is None and ref:
        return {"ok": True, "duplicado": True, "caso": caso, "causa": causa}
    return {"ok": True, "id": vid, "caso": caso, "causa": causa,
            "acierta": acierta, "origen": origen}


@cached(ttl=60)
def precision_por_causa() -> dict[str, tuple[int, int, int]] | None:
    """`causa → (votos_humanos, aciertos_humanos, votos_totales)`.

    Lo que la LISTA de hallazgos necesita para mostrar la confianza en cada fila,
    que es lo que hace que votar valga la pena: si el voto no cambia nada
    visible, nadie vota y la medición tarda un mes en servir.

    **Cacheado, y no metido en el SELECT de la vista**: hay un test que cuenta
    las queries de `_hallazgos_ultima_corrida` porque cada una paga ~8,5 ms de
    distancia aunque ejecute en 0,1 ms. Este dato **solo cambia cuando alguien
    vota**, así que un TTL corto + invalidación en `votar()` deja el costo en ~0
    y el número fresco justo cuando importa.

    `None` = **no se pudo medir**, que es distinto de un dict vacío (eso sí es un
    dato: nadie votó nunca). El front los muestra distinto.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT causa, "
                "       count(*) FILTER (WHERE origen IN ('humano','verificado')), "
                "       count(*) FILTER (WHERE origen IN ('humano','verificado') "
                "                        AND acierta), "
                "       count(*) "
                "FROM mercado.av_agent_evals GROUP BY causa")
            return {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0))
                    for r in cur.fetchall()}
    except Exception as e:
        logger.warning("av_agent_evals: no se pudo leer la precisión (%s)", e)
        return None


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
                # ⚠️ **`verificado` CUENTA COMO UN HUMANO.** Sale de
                # `av_agent_seguimiento`: el problema volvió o no volvió en los
                # días siguientes. No es la opinión de nadie y el agente no lo
                # controla — es al menos tan buena evidencia como un click, y
                # dejarla afuera de la compuerta sería tirar la mejor señal que
                # tenemos. El `derivado` (una aprobación) SÍ queda afuera: eso es
                # alguien diciendo «dale», no el mundo diciendo «funcionó».
                "       count(*) FILTER (WHERE origen IN ('humano','verificado')), "
                "       count(*) FILTER (WHERE origen IN ('humano','verificado') "
                "                        AND acierta), "
                "       count(*) FILTER (WHERE origen = 'verificado') "
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
    for d, c, n, ok, ultimo, nh, okh, nv in filas:
        n, ok = int(n), int(ok or 0)
        nh, okh, nv = int(nh or 0), int(okh or 0), int(nv or 0)
        causas.append({
            "dominio": d, "causa": c, "votos": n, "aciertos": ok,
            "precision": round(ok / n, 4) if n else None,
            # ⚠️ **LA COMPUERTA CUENTA SOLO LOS HUMANOS.** Un voto derivado sale
            # de una acción que alguien aprobó —una señal real, pero más débil— y
            # si contara para `candidata_a_auto`, el agente podría habilitarse
            # solo: propone, el humano aprueba por otra razón, y eso se lee como
            # «el diagnóstico acertó 10 de 10». Los derivados dan CONTEXTO.
            # `humanos` incluye los `verificado` porque los dos abren la
            # compuerta; `verificados` se muestra aparte para poder decir de
            # dónde viene el respaldo.
            "humanos": nh, "aciertos_humanos": okh, "verificados": nv,
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
