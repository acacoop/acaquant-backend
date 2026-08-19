"""api/services/av_agent.py — los detectores del AV AGENT (etapa E1).

Doc madre: **`docs/AV_AGENT.md`** (leerlo antes de tocar esto).

**Qué es esta etapa.** El espejo: contesta las tres preguntas del agente SIN
escribir en `mercado.curvas` y **sin una sola llamada a un LLM**. Es deliberado —
un agente que diagnostica sin poder medir si acertó no es un agente, es un
generador de opiniones. Primero se construye el piso medible (E1), se calibra
contra la realidad, y recién ahí se le enchufa el modelo (E4).

Las tres preguntas:

  1. **¿Qué hay en 1816 que no tengo?**  → `detectar_faltantes`
  2. **¿Qué bono mío está sin flujo?**   → `detectar_sin_flujo`
  3. **¿Qué tasa está dando mal?**       → `detectar_tasas_sospechosas`

**Módulo PURO** (regla de capas): las tres `detectar_*` reciben los datos ya
leídos y no tocan la base ni la red, así se testean sin Postgres y sin créditos.
`relevar()` es el único que lee, y es el que llama el job.

⚠️ **El enemigo de esta etapa es el FALSO POSITIVO, no el falso negativo.** Si la
lista trae ruido, a las tres semanas no la mira nadie y el agente muere aunque
funcione. Por eso cada regla excluye explícitamente los casos que YA sabemos que
no son problemas (ver `_MOTIVOS_SIN_TASA_LEGITIMOS` y el reuso de
`es_tasa_ruido`), y por eso E3 va a castigar el falso positivo igual que el
falso negativo.
"""
from __future__ import annotations

import logging
from typing import Any

from core import curvas_ejes, mercado_1816

logger = logging.getLogger(__name__)

# ── Rangos de sanidad (docs/SALUD_CURVAS.md §7.1) ────────────────────────────
#
# ⚠️ ESCALAS, que no son obvias y equivocarlas invierte el resultado:
#   · `tea` de `mercado.market_snapshot` viene en **FRACCIONES** (0.0973 = 9,73%),
#     igual que el `spread` de 1816 — verificado en jobs/tamar_1816.
#   · `paridad` viene en **PORCENTAJE** (100 = a la par) — el motor la calcula
#     como `precio_calc / Σ amortizaciones futuras × 100`.
#   · `duration` en años.
PARIDAD_MIN, PARIDAD_MAX = 40.0, 160.0
TEA_MIN, TEA_MAX = -0.30, 0.60

# Ajustes cuya TEA el motor NO calcula por diseño: caen en el `else` de
# `engines/curvas.py`, que solo computa duration. Reportarlos como "sin tasa"
# sería denunciar todas las noches una decisión de arquitectura — 18 bonos TAMAR
# de ruido fijo, que es exactamente cómo se entrena a la gente a ignorar la
# lista. Su tasa llega por otro riel (`mercado.tamar_1816`, desde 1816).
_MOTIVOS_SIN_TASA_LEGITIMOS = {"tamar", "badlar", "tpm", "caucion"}

# Qué `emisor_tipo` entran según el alcance elegido (decisión D1 del doc, ABIERTA:
# por eso es un parámetro y no una constante). `soberanos` incluye **bcra** porque
# los BOPREALes viven en la curva "BCRA USD" de 1816 y en nuestra base están del
# lado soberano — es el mismo criterio que ya usa `jobs/mercado_1816_discovery`.
ALCANCES: dict[str, frozenset[str] | None] = {
    "soberanos": frozenset({"soberano", "bcra"}),
    "no_corporativos": frozenset({"soberano", "bcra", "provincial"}),
    "todo": None,          # None = sin filtro
}

# Monedas que la mesa SIGUE. 1816 publica 6 Globales en EUROS (GE29/GE30/GE35/
# GE38/GE41/GE46) que no operamos: no son un faltante, son un mercado en el que
# no estamos. Calibrado con la primera corrida (2026-08-16) — eran 6 de los 33.
#
# Se excluye por MONEDA y no anotando los seis tickers a mano a propósito: una
# regla estructural sigue valiendo cuando el Tesoro emita el séptimo, una lista
# de excepciones no. Para lo que sí es caso por caso está `IGNORADOS`.
MONEDAS_SEGUIDAS = frozenset({"ARS", "USD"})

_norm = mercado_1816.normalizar_ticker


def _es_pata(ticker: str) -> bool:
    """¿Es una VISTA DE VALUACIÓN por componente y no un instrumento?

    1816 publica las patas de un dual y otras variantes como tickers aparte con
    un sufijo `@`: `TXMD9 @TAMAR`, `BPOA8 @AFIP`, `TY30P @PUT`, `TTS26 @TASA
    FIJA`. **No son instrumentos**: `/cashflow` les da 404 (medido) porque el
    cuadro lo tiene el ticker BASE, que ya está —o ya se reporta— por su cuenta.

    Era el riesgo #3 del doc y se materializó en la primera corrida: 3 de los 33
    faltantes eran patas, y `BPOA8` salía DOS VECES (base y `@AFIP`)."""
    return "@" in (ticker or "")


# ── DOS CICLOS CONVIVEN EN `mercado.av_agent_hallazgos` ──────────────────────
#
# La RELEVADA nocturna deja una CORRIDA: una foto con su `corrida_at`, y la
# vigente es la última. Los monitores (rueda y sistema) no tienen corrida que
# elegir: cada pasada **REEMPLAZA** lo suyo, porque contestan «¿qué está mal
# AHORA?» y acumularlos dejaría 84 avisos del mismo problema al final del día.
#
# ⚠️ **Y por eso hay que excluirlos del `max(corrida_at)`**: se reescriben cada
# pocos minutos, así que un máximo a secas devuelve siempre el del monitor y la
# relevada entera desaparece de la pantalla **en silencio**. Ya pasó con `live`;
# la constante existe para que el próximo alcance de reemplazo no lo repita.
ALCANCES_VIVOS = ("live", "sistema")

# ⚠️ **UN HALLAZGO DE RUEDA TIENE FECHA DE VENCIMIENTO** (2026-08-19).
#
# *«En AVISOS tenía un montón de avisos de precios sin precio, pero eso era
# porque el MERCADO ESTABA CERRADO. Es obvio que no va a actualizar: si el precio
# cierra a las 17 y abre a las 10:30.»* (user)
#
# El monitor corre 10:30-17 ART y **reemplaza** lo suyo en cada pasada. Pero al
# cerrar el mercado deja de correr, y su última foto —la de las 16:55— se quedaba
# en la pantalla toda la noche y todo el fin de semana. O sea que lo que el user
# veía no era un detector equivocado: era un detector **CORRECTO mostrando una
# foto vieja**, que para el que mira es lo mismo.
#
# Vence en vez de borrarse con un cron al cierre, y eso es a propósito: si el
# monitor se muere a las 11, sus hallazgos también desaparecen — y está bien,
# porque ya **no sabemos** si siguen pasando. Un dato que nadie está refrescando
# no puede seguir afirmándose. Se cura solo y no depende de ningún job.
VENCEN_EN_S: dict[str, int] = {"live": 15 * 60}      # el monitor pasa cada 5'



def reemplazar_hallazgos(alcance: str, hallazgos: list[dict]) -> int:
    """Reescribe TODOS los hallazgos de un alcance de reemplazo, en UNA transacción.

    El DELETE y el INSERT van juntos a propósito: en dos pasos, entre uno y otro
    la pantalla mostraría cero hallazgos y alguien podría leer «está todo bien»
    justo cuando no lo está.

    Vive acá y no en cada job porque los dos monitores escriben lo mismo de la
    misma forma, y dos copias del mismo INSERT se separan el día que una cambia.
    """
    import json

    from core.postgres import get_pool

    if alcance not in ALCANCES_VIVOS:
        raise ValueError(f"{alcance!r} no es un alcance de reemplazo: borrar una "
                         f"CORRIDA entera no es lo que esta función hace")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mercado.av_agent_hallazgos WHERE alcance = %s",
                    (alcance,))
        for h in hallazgos:
            cur.execute(
                "INSERT INTO mercado.av_agent_hallazgos "
                "(alcance, tipo, ticker, regla, severidad, motivo, evidencia) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                (alcance, h["tipo"], h["ticker"], h["regla"], h["severidad"],
                 h["motivo"],
                 json.dumps(h.get("evidencia") or {}, ensure_ascii=False,
                            default=str)))
        conn.commit()
    return len(hallazgos)


# QUÉ PUEDE HACER EL AGENTE con cada tipo de hallazgo. **Vive acá y no en el
# front**: el que sabe si un hallazgo es accionable es el que sabe resolverlo.
#
# El front tenía la condición escrita a mano (`h.tipo === "flujos_vacios"`) y
# `flujos_vacios` no es el TIPO sino la REGLA — el botón simplemente no aparecía,
# sin error y sin nada que mirar. Un string mágico copiado a mano en la otra punta
# del sistema falla exactamente así: en silencio.
#
# **NO se persiste, se deriva en la lectura**: el día que un tipo se vuelva
# accionable, los hallazgos ya guardados lo heredan solos.
ACCION_POR_TIPO = {
    "falta_en_base": "alta",    # el bono no existe → se crea entero
    "sin_flujo": "flujos",      # el bono existe → se completa el cronograma
    # El bono existe Y tiene cuadro, pero un INSUMO está mal (los ejes o la
    # escala). Es la única acción que PISA un dato, así que su cadena exige las
    # dos mitades: que la propuesta coincida con 1816 y que lo de hoy NO.
    "tasa_sospechosa": "arreglo",
    # **`None` EXPLÍCITO, no ausencia.** Un `hueco_de_curva` sí es accionable,
    # pero por OTRA vía: el agente lo pregunta en ME PREGUNTA y ahí se crea la
    # curva (`av_agent_preguntas.crear_curva`). Ponerle botón de fila sería un
    # segundo camino para lo mismo — y dos caminos a la misma escritura terminan
    # con criterios distintos, que es el patrón que ya nos costó tres bugs.
    #
    # Escribirlo igual, con `None`, es lo que distingue **«se decidió que no»** de
    # **«nadie lo pensó»**. Un tipo que falta por olvido sale en la pantalla como
    # un comentario que nadie puede accionar y no da ningún error — exactamente lo
    # que le pasó a `tasa_sospechosa` durante 38 filas.
    "hueco_de_curva": None,
    # ── EL SISTEMA, no el mercado (2026-08-19) ───────────────────────────────
    # `None` EXPLÍCITO: son diagnósticos, no cosas que se arreglen tocando
    # `mercado.curvas`. Una tabla que creció de golpe se resuelve en el job que
    # la escribe; un endpoint degradado, en su propio código. El agente los VE y
    # los canta con la evidencia — que es todo lo que se le pidió.
    "db_cambio": None,
    "latencia": None,
    # Una tabla que dejó de escribir y un motor caído tampoco se arreglan desde
    # `mercado.curvas` — se arreglan en el job o el servicio que los produce. El
    # agente los VE y los canta con la evidencia; **poder relanzarlos es el paso
    # siguiente** (el user: «y a futuro que pueda hacer algo»).
    "tabla_quieta": None,
    "motor_caido": None,
    # Un permiso flojo se arregla en el router o en el borde, no en la base.
    "permiso_flojo": None,
    # ── EN RUEDA (2026-08-18) ────────────────────────────────────────────────
    # `None` EXPLÍCITO, y por un motivo distinto al resto: no es que falte
    # construirlo, es que **no se arreglan tocando `mercado.curvas`**. Un símbolo
    # sin suscribir se resuelve en el universo del motor, y un precio que llega
    # en pesos se resuelve en la pata o en el feed. El agente los VE y los canta
    # en el momento — que es todo lo que se le pidió.
    # **Deja de ser `None`** (2026-08-18): un bono sin precio SÍ tiene un
    # diagnóstico local que distingue las cinco causas —sin símbolo, fuera de
    # Primary, pata equivocada, nunca operó, sin actividad hoy— y esas se
    # arreglan distinto. El user lo pidió mirando AO29: «no hay diagnóstico, no
    # hay aviso». Sigue sin ARREGLAR nada: la puerta es de solo lectura.
    "sin_precio": "sin_precio",
    "precio_moneda": None,
    # ── SALUD entra al agente (2026-08-17) ──────────────────────────────────
    #
    # **`salud` abre una puerta de SOLO LECTURA**, y eso es una decisión, no una
    # limitación temporal mal resuelta: el agente ya razona un chequeo con ocho
    # lentes —lee el log, reconoce firmas de error conocidas, propone el comando
    # exacto— pero **no escribe nada del lado de SALUD**. Relanzar un job tiene
    # efectos afuera de `mercado.curvas` y se habilita cuando el eval set diga que
    # el diagnóstico acierta. Primero ver, después simular, después escribir — el
    # mismo camino que hizo la puerta de bonos.
    #
    # El valor NO es `None` porque el front usa este campo para elegir qué puerta
    # abrir, y sin él la fila queda muda: un chequeo en rojo que no se puede ni
    # mirar es peor que no tenerlo en la lista. Lo que impide escribir es que esa
    # puerta no tiene botón de aplicar, no que la fila sea inaccionable.
    "salud": "salud",
}


def _hallazgo(tipo: str, ticker: str, regla: str, severidad: str,
              motivo: str, evidencia: dict[str, Any]) -> dict:
    """Un hallazgo es siempre la MISMA forma, venga del detector que venga: así la
    tabla, la bandeja (E5) y el diagnóstico (E4) leen una sola estructura.

    `evidencia` es lo que sostiene la afirmación — se congela junto al hallazgo
    porque para cuando alguien lo mire, el motivo puede haber dejado de existir
    (mismo criterio que `manager.salud_eventos`)."""
    return {"tipo": tipo, "ticker": ticker, "regla": regla, "severidad": severidad,
            "motivo": motivo, "evidencia": evidencia}


# ── 1) ¿Qué hay en 1816 que no tengo? ────────────────────────────────────────


def _cotiza_en_primary(ticker: str, simbolos: set[str]) -> bool:
    """¿Primary lista ALGUNA pata de este ticker?

    **Por qué existe** (user, 2026-08-17): *«si no está en Primary ni me
    interesa, ya que si no le puedo meter el last price no tiene valor. Pero
    ¿cómo hacemos para que no aparezca constantemente?»*.

    Es el filtro más duro de todos, y tiene razón: un bono que no cotiza **no se
    puede valuar nunca**, así que no es un hallazgo — es ruido permanente que
    empuja hacia abajo a los que sí importan.

    Se prueban las DOS patas (`24hs` y `CI`) antes de descartar: el símbolo se
    arma por convención, y quedarse solo con `24hs` descartaría un bono que
    cotiza únicamente en contado inmediato. Descartar de más acá es INVISIBLE
    —el bono simplemente deja de proponerse— y por eso el criterio es generoso.
    """
    return any(f"MERV - XMEV - {ticker} - {plazo}" in simbolos
               for plazo in ("24hs", "CI"))


def descartar_por_primary(ticker: str, en_cartera: bool,
                          simbolos: set[str] | None) -> bool:
    """¿Este faltante se descarta por no cotizar? **EL predicado, uno solo.**

    Lo usan el DETECTOR (al relevar) y la LECTURA de la vista (al mostrar la
    foto). Tenerlo en un solo lugar no es prolijidad: la primera versión vivía
    solo en el detector, así que el filtro no tenía efecto hasta la próxima
    relevada —que cuesta ~29 créditos y no se corre por pantalla— y los bonos
    seguían apareciendo igual. Es la MISMA lección que TZXM8 y BADLAR: la foto se
    muestra, pero nunca sin cotejarla.
    """
    if not simbolos:          # sin criterio → no se filtra
        return False
    if en_cartera:            # lo tenemos: no valuar es MÁS grave, no menos
        return False
    return not _cotiza_en_primary(ticker, simbolos)


def simbolos_primary() -> set[str] | None:
    """El universo REAL de Primary. `None` = no se pudo leer → **no se filtra**.

    Misma degradación elegida que `core/instrumentos_validos`, y se reusa ESA
    función en vez de escribir otra query: filtrar de más esconde bonos reales,
    no filtrar deja el ruido de siempre. Ante la duda, lo segundo.
    """
    try:
        from core import instrumentos_validos
        return instrumentos_validos.validos()
    except Exception as e:
        logger.debug("av_agent: sin universo de Primary (%s) — no se filtra", e)
        return None


def universo_local() -> tuple[dict[str, dict], str]:
    """El censo de 1816 desde **la copia que ya tenemos en casa**
    (`research.mkt_1816_instrumentos`, 887 instrumentos que llena
    `jobs/mercado_1816_discovery --apply --catalogo`).

    **Por qué existe** (2026-08-17): `relevar()` arrancaba con `censar()`, o sea
    ~29 llamadas a 1816, y si el proveedor contestaba 429 —como pasó— **la
    relevada entera moría antes de empezar**. Pero de los cuatro detectores, el
    único que necesita el universo de 1816 es `detectar_faltantes` («¿qué hay allá
    que no tengo?»); los otros tres miran NUESTRA base y usan el universo, como
    mucho, para enriquecer un mensaje.

    Es el mismo error de diseño que la cadena del arreglo, en otro archivo: una
    dependencia externa colgando de algo que casi no la necesita. Y la solución es
    la misma — agotar lo local antes de salir a preguntar.

    El catálogo local es un poco más viejo que la API, así que devuelve TAMBIÉN su
    fecha: un faltante detectado contra una foto de hace una semana sigue siendo
    un faltante, pero quien lo lee tiene que saber con qué se comparó.
    """
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, denominacion, curva, curva_id, isin, fecha_emision, "
                "fecha_vencimiento, moneda_denom, emisor, "
                "to_char(max(actualizado_en) OVER (), 'YYYY-MM-DD') "
                "FROM research.mkt_1816_instrumentos WHERE activo IS NOT FALSE")
            filas = cur.fetchall()
    except Exception:
        logger.warning("av_agent: tampoco se pudo leer el catálogo local de 1816",
                       exc_info=True)
        return {}, ""
    # Las claves son las de 1816 (`emisorNombre`, `monedaDenom`, …) y no las de la
    # tabla: `detectar_faltantes` no puede saber de dónde salió el universo, o
    # habría que escribir el detector dos veces.
    univ = {r[0]: {"_curva": r[2] or "", "_curva_id": r[3],
                   "denominacion": r[1], "isinCode": r[4],
                   "fechaEmision": r[5].isoformat() if r[5] else None,
                   "fechaVencimiento": r[6].isoformat() if r[6] else None,
                   "monedaDenom": r[7], "emisorNombre": r[8]}
            for r in filas if r[0]}
    return univ, (filas[0][9] if filas else "")


def tickers_ignorados() -> set[str]:
    """Los «no me interesa». **Un solo lector, y lo llaman relevar Y leer.**

    Degradación elegida: si la consulta falla se devuelve el conjunto VACÍO, o sea
    se reporta de MÁS. Al revés —asumir que todo está ignorado— escondería
    hallazgos reales, que es el único error que este agente no puede permitirse.
    """
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT upper(btrim(ticker)) FROM mercado.av_agent_ignorados")
            return {r[0] for r in cur.fetchall() if r[0]}
    except Exception:
        logger.warning("av_agent: no se pudo leer la lista de ignorados", exc_info=True)
        return set()


def detectar_faltantes(universo_1816: dict[str, dict], docs: list[dict], *,
                       alcance: str = "soberanos",
                       ignorados: set[str] | None = None,
                       en_cartera: set[str] | None = None,
                       simbolos_primary: set[str] | None = None) -> list[dict]:
    """Tickers vigentes en 1816 que NO están en `mercado.curvas`.

    El cruce se hace sobre el ticker NORMALIZADO (sin la especie D/C final): 1816
    publica `AL30` y nuestro master puede tener `AL30D`. La normalización vive en
    el cliente porque es una convención DEL PROVEEDOR — tenerla duplicada hacía
    que dos cruces dieran universos distintos sin que nadie se entere.

    El **alcance** filtra por los ejes que `core.curvas_ejes` deriva del NOMBRE de
    la curva de 1816 (tabla explícita de 28 nombres, no un parser que adivina).
    Una curva que la tabla no conoce se reporta con `curva_desconocida` en vez de
    clasificarse mal en silencio: un nombre nuevo del proveedor tiene que ser
    visible, no invisible.
    """
    permitidos = ALCANCES.get(alcance, ALCANCES["soberanos"])
    ignorados = {_norm(t) for t in (ignorados or set())}
    mios = {_norm(d.get("ticker_corto")) for d in docs if d.get("ticker_corto")}
    mios.discard("")

    out: list[dict] = []
    # Los descartados por no cotizar. NO se tiran en silencio: se cuentan y se
    # logean — «no reporté 37 porque no cotizan» es información; «no aparecen» es
    # un agujero.
    sin_primary: list[str] = []
    for ticker, inst in sorted(universo_1816.items()):
        if _es_pata(ticker):          # vista de valuación, no instrumento
            continue
        tk = _norm(ticker)
        if not tk or tk in mios or tk in ignorados:
            continue
        curva_1816 = inst.get("_curva") or ""
        ejes = curvas_ejes.desde_1816(curva_1816)
        if permitidos is not None and (ejes is None or ejes.emisor_tipo not in permitidos):
            continue
        # Moneda que no seguimos → no es un faltante (los Globales en EUR).
        if ejes is not None and ejes.moneda not in MONEDAS_SEGUIDAS:
            continue
        # ¿La casa lo TIENE? Es el dato que convierte la pregunta en una obviedad:
        # un bono en la tenencia que no está en `mercado.curvas` NO VALÚA — no
        # tiene TEA, no entra al gráfico y su posición se muestra sin precio
        # modelado. Ahí "¿te interesa?" ya no es una opinión.
        # ⚠️ **NO COTIZA EN PRIMARY → NO ES UN HALLAZGO.** Es el filtro más duro
        # y el user lo pidió explícito: si no se le puede meter el last price, el
        # bono no tiene valor. Reportarlo cada corrida es ruido permanente que
        # empuja hacia abajo a los que sí importan.
        #
        # **La excepción es la CARTERA**: si la casa lo TIENE, se reporta igual
        # aunque no cotice — ahí el problema es más grave, no menor (una posición
        # que no valúa), y esconderlo sería justo lo contrario de lo que hay que
        # hacer.
        lo_tenemos = bool(en_cartera and tk in en_cartera)
        if descartar_por_primary(tk, lo_tenemos, simbolos_primary):
            sin_primary.append(tk)
            continue
        out.append(_hallazgo(
            "falta_en_base", tk, "no_esta_en_curvas",
            "alta" if lo_tenemos else "media",
            ("⚠ LO TENÉS EN CARTERA y no está en mercado.curvas: hoy no valúa. "
             f"1816 lo publica en «{curva_1816}»."
             if lo_tenemos else
             f"1816 lo publica en «{curva_1816}» y no está en mercado.curvas."),
            {"curva_1816": curva_1816, "curva_id": inst.get("_curva_id"),
             "ticker_1816": ticker,
             # Los nombres de campo son los de 1816, verificados contra
             # `jobs/mercado_1816_discovery` (que persiste este mismo catálogo):
             # emisorNombre · denominacion · monedaDenom · fechaEmision · isinCode.
             # Sin el emisor y la denominación, un ticker como M31G6 no le dice
             # nada a nadie y la pregunta es incontestable.
             "emisor": inst.get("emisorNombre") or inst.get("emisor"),
             "denominacion": inst.get("denominacion"),
             "moneda": inst.get("monedaDenom"),
             "emision_1816": inst.get("fechaEmision") or None,
             "isin": inst.get("isinCode"),
             "vencimiento_1816": inst.get("fechaVencimiento") or inst.get("vencimiento"),
             "en_cartera": lo_tenemos,
             "ejes_sugeridos": (
                 {"emisor_tipo": ejes.emisor_tipo, "moneda_eje": ejes.moneda,
                  "ajuste": ejes.ajuste, "ajuste_alt": ejes.ajuste_alt, "ley": ejes.ley}
                 if ejes else None),
             # ⚠ Darlo de alta NO alcanza para verlo: si su ajuste no tiene pill
             # (badlar/tpm/caución), el bono queda cargado y NO aparece en
             # ninguna pantalla. Avisarlo ANTES es la diferencia entre una
             # decisión informada y cargar diez bonos que no se van a poder
             # mirar — que es exactamente lo que le pasó al user con los BADLAR.
             "ajuste_sin_curva": bool(ejes and curvas_ejes.ajuste_sin_curva(ejes.ajuste)),
             "curva_desconocida": ejes is None}))
    if sin_primary:
        logger.info("av_agent: %d faltantes DESCARTADOS por no cotizar en Primary "
                    "(no se les puede poner precio): %s", len(sin_primary),
                    ", ".join(sorted(sin_primary)[:20]))
    return out


# ── 2) ¿Qué bono mío está sin flujo? ─────────────────────────────────────────


def detectar_sin_flujo(docs: list[dict],
                       universo_1816: dict[str, dict] | None = None) -> list[dict]:
    """Bonos de `mercado.curvas` sin cronograma de pagos.

    "Sin flujo" es ESTRUCTURAL (no hay definición de flujo), no de valuación — un
    CER futuro tiene flujo aunque todavía no se pueda valuar. Sin flujo no hay
    XIRR: el bono no tiene TEA, no entra al gráfico y no aporta al fair value.

    ⚠️ **El predicado es `acreencias.tiene_flujo_def`, no `bool(doc['flujos'])`.**
    Una LECAP/BONCAP es zero-coupon: no tiene array y NO le falta nada — el motor
    la valúa con `flujo_vencimiento` (`engines/curvas.py` rama `tasa_fija`). La
    primera corrida (2026-08-16) marcó 11 letras que rinden perfecto porque esta
    función miraba solo el array; el criterio correcto ya existía en el
    conciliador de Manager y había que USARLO, no reescribirlo peor.

    Marca `resoluble` cuando 1816 tiene ese ticker, que es la diferencia entre
    *"esto lo completa el agente"* y *"esto necesita el prospecto"*. **Un agente
    que no puede decir "no sé" empieza a rellenar**, así que la distinción viaja
    en el hallazgo y no se resuelve a dedo.
    """
    from datetime import date

    from api.services.acreencias import tiene_flujo_def

    hoy = date.today()
    univ = {_norm(t) for t in (universo_1816 or {})}
    out: list[dict] = []
    for d in docs:
        tc = (d.get("ticker_corto") or "").strip().upper()
        if not tc:
            continue
        if tiene_flujo_def(d, hoy):
            continue
        resoluble = _norm(tc) in univ
        out.append(_hallazgo(
            "sin_flujo", tc, "flujos_vacios", "alta" if resoluble else "media",
            ("Sin cronograma de pagos; 1816 lo tiene y se puede completar."
             if resoluble else
             "Sin cronograma de pagos, y 1816 tampoco lo publica: necesita carga manual."),
            {"resoluble_con_1816": resoluble, "curva": d.get("curva"),
             "emisor": d.get("emisor"), "moneda_flujo": d.get("moneda_flujo"),
             "vencimiento": d.get("fecha_vencimiento")}))
    return out


# ── 3) ¿Qué tasa está dando mal? ─────────────────────────────────────────────


def detectar_tasas_sospechosas(docs: list[dict], metricas: dict[str, dict],
                               tickers_en_assets: set[str] | None = None,
                               en_cartera: set[str] | None = None,
                               universo_1816: dict[str, dict] | None = None) -> list[dict]:
    """Las reglas de sanidad de `docs/SALUD_CURVAS.md` §6-§7 sobre el cierre.

    `metricas` = `{simbolo_de_mercado: {tea, paridad, duration, last_price}}` tal
    como lo devuelve `core.market_snapshot.cols_map`. El join va por el SÍMBOLO
    (`doc['ticker']`, que tras el renombre de columnas es el de Primary), no por
    el ticker corto.

    **Cada regla dice qué falla del catálogo sospecha**, porque ese mapeo es el
    que E4 va a usar como few-shot: el modelo no arranca de cero, arranca de las
    7 fallas que ya conocemos.

    Tres exclusiones deliberadas, que son lo que separa una lista útil de una
    lista que nadie mira:
      · los ajustes que el motor NO calcula por diseño (TAMAR y compañía);
      · las tasas que ya están marcadas como RUIDO por duration — mismo predicado
        que usa la vista (`es_tasa_ruido`), no una copia que pueda divergir;
      · los bonos sin flujo, que ya los reporta el detector 2 (un bono sin flujo
        no tiene tasa por definición: contarlo dos veces infla la lista y hace
        parecer que hay dos problemas donde hay uno).

    `en_cartera` acota `sin_espejo_en_assets` a lo que la casa TIENE: un bono que
    no está en la tenencia no necesita fila en `portafolio.assets` — no le falta
    nada al AuM porque no aporta al AuM. Es el mismo recorte que hace el
    conciliador de Manager (`titulos_sin_flujo` parte del último AuM). `None`
    apaga la regla en vez de marcar todo, igual que `tickers_en_assets`.
    """
    from datetime import date

    from api.services.acreencias import tiene_flujo_def
    from api.services.curvas_vista import es_tasa_ruido
    from engines.curvas import moneda_flujo_esperada, rama_calculo

    hoy = date.today()
    out: list[dict] = []
    for d in docs:
        tc = (d.get("ticker_corto") or "").strip().upper()
        simbolo = (d.get("ticker") or "").strip()
        if not tc:
            continue

        # sin flujo → es el hallazgo del detector 2, no una tasa rota. MISMO
        # predicado que allá: si acá se mirara solo el array, una LECAP entraría
        # a las reglas de tasa por una puerta y saldría por la otra.
        if not tiene_flujo_def(d, hoy):
            continue

        ejes = curvas_ejes.ejes_de_doc(d)
        emisor_tipo = ejes.emisor_tipo if ejes else d.get("emisor_tipo")
        ajuste = (d.get("ajuste") or "").strip().lower()
        m = metricas.get(simbolo) or {}
        tea, paridad = m.get("tea"), m.get("paridad")
        precio, duration = m.get("last_price"), m.get("duration")
        base = {"simbolo": simbolo, "tea": tea, "paridad": paridad,
                "last_price": precio, "duration": duration,
                "moneda_flujo": d.get("moneda_flujo"), "emisor": d.get("emisor"),
                "ajuste": ajuste or None, "n_flujos": len(d.get("flujos") or [])}

        # Un bono sin ejes desaparece de todo lo que llame a `por_curva` — y no
        # da error, que es lo que lo hace peligroso (paso 14 de RENTA_FIJA).
        if ejes is None:
            out.append(_hallazgo(
                "tasa_sospechosa", tc, "sin_ejes", "alta",
                "Sin ejes: no cae en ninguna curva y desaparece de la vista, "
                "los forwards y el fair value, sin dar error.",
                base))
            continue

        # ── EL DEFECTO, NO EL SÍNTOMA ────────────────────────────────────────
        #
        # Las demás reglas de acá miran una MÉTRICA que se salió de un rango, así
        # que solo ven el error cuando es lo bastante grande y cuando ese día hubo
        # precio. Medido el 2026-08-17: **30 de 140 bonos** de la rama ON tienen
        # `moneda_flujo` contradiciendo a sus ejes, y solo **8** habían disparado
        # algún hallazgo. Los otros 22 están igual de mal valuados y no aparecían
        # en ninguna pantalla.
        #
        # Esta regla mira el DEFECTO directamente: dos campos del mismo doc que se
        # contradicen. Por eso **no necesita precio, ni snapshot, ni 1816** — es
        # cierta un domingo y con la API caída, y no puede dispararse por un valor
        # viejo pegado en `market_snapshot`.
        #
        # No afirma CUÁL de los dos está mal: afirma que no pueden ser los dos. La
        # evidencia lleva los dos valores y lo que dice 1816, y quien decide es la
        # cadena del arreglo.
        if rama_calculo(d) == "on":
            esperada = moneda_flujo_esperada(d)
            actual = (d.get("moneda_flujo") or "").strip().upper()
            if esperada and actual != esperada:
                out.append(_hallazgo(
                    "tasa_sospechosa", tc, "moneda_flujo_contradice", "alta",
                    f"`moneda_flujo`={actual or '(vacío)'} pero los ejes dicen "
                    f"{ejes.moneda}/{ejes.ajuste} → debería ser {esperada}. **El "
                    "motor despacha por `moneda_flujo`**, así que el precio entra "
                    "sin convertir y la TEA y la paridad salen de otra escala.",
                    {**base, "moneda_flujo": actual or None,
                     "moneda_flujo_esperada": esperada,
                     "curva_1816": (universo_1816 or {}).get(_norm(tc), {}).get("_curva")}))

        if (tickers_en_assets is not None and tc not in tickers_en_assets
                and en_cartera is not None and tc in en_cartera):
            out.append(_hallazgo(
                "tasa_sospechosa", tc, "sin_espejo_en_assets", "alta",
                "La casa TIENE este bono y no está en portafolio.assets: no entra "
                "al AuM ni a Portfolios (falla del join de valuación).",
                base))

        ruidosa = es_tasa_ruido(m, emisor_tipo)

        if tea is None and precio and ajuste not in _MOTIVOS_SIN_TASA_LEGITIMOS:
            out.append(_hallazgo(
                "tasa_sospechosa", tc, "sin_tea_con_precio", "alta",
                "Tiene precio y flujo pero el motor no persiste TEA: el XIRR no "
                "converge. Sospecha: pata equivocada o escala del flujo distinta "
                "de la del precio (fallas #2 y #4 del catálogo).",
                base))

        if paridad is not None and not (PARIDAD_MIN <= paridad <= PARIDAD_MAX):
            out.append(_hallazgo(
                "tasa_sospechosa", tc, "paridad_fuera_de_rango",
                "alta" if paridad > 300 or paridad < 10 else "media",
                f"Paridad {paridad:,.1f}% fuera de [{PARIDAD_MIN:.0f}, "
                f"{PARIDAD_MAX:.0f}]. Sospecha: escala del flujo o pata "
                "equivocada (fallas #2, #3 y #4).",
                base))

        if tea is not None and not (TEA_MIN <= tea <= TEA_MAX) and not ruidosa:
            out.append(_hallazgo(
                "tasa_sospechosa", tc, "tea_fuera_de_rango", "media",
                f"TEA {tea:.2%} fuera de [{TEA_MIN:.0%}, {TEA_MAX:.0%}] y la "
                "duration no la explica. Sospecha: precio stale/ilíquido o dato "
                "del bono mal cargado (fallas #4 y #5).",
                base))

    return out


# ── 4) ¿Hay bonos que no caen en ninguna curva? (hueco ESTRUCTURAL) ──────────


def detectar_huecos_de_curva(docs: list[dict]) -> list[dict]:
    """Ajustes que existen en `mercado.curvas` pero que la app no sabe mostrar.

    **El caso real que lo motivó (user, 2026-08-16):** 1816 publica «Soberanos
    ARS Badlar» y nuestros ejes aceptan `ajuste='badlar'`, pero
    `_pill_de_ajuste` devuelve `None` para badlar/tpm/caucion → esos bonos no
    caen en ninguna pill, y por lo tanto en ninguna curva. Resultado: **están
    cargados y no aparecen en ninguna pantalla**, sin dar un solo error.

    Es un hallazgo de otra naturaleza que los tres anteriores: no es un dato mal
    cargado, es una **capacidad que le falta al sistema**. Por eso se reporta
    UNA vez por AJUSTE y no una por bono — el problema es el ajuste; los bonos
    son la evidencia de cuánto duele.

    Y por eso el AV Agent NO puede arreglarlo solo: darle una pill a `badlar` es
    tocar `curvas_ejes` + `sql_universo` + la vista del front. Es desarrollo, no
    dato. Lo que sí puede —y es la mitad que faltaba— es **verlo y decirlo antes
    de que alguien cargue diez bonos que no va a poder mirar.**
    """
    por_ajuste: dict[str, list[str]] = {}
    monedas: dict[str, list[str]] = {}
    for d in docs:
        tc = (d.get("ticker_corto") or "").strip().upper()
        if not tc:
            continue
        ejes = curvas_ejes.ejes_de_doc(d)
        if ejes is None:
            continue          # sin ejes → lo reporta `sin_ejes`, no es lo mismo
        for aj in (ejes.ajuste, ejes.ajuste_alt):
            if curvas_ejes.ajuste_sin_curva(aj) and not curvas_ejes.pills(ejes):
                por_ajuste.setdefault(aj, []).append(tc)
                monedas.setdefault(aj, []).append(ejes.moneda)

    out: list[dict] = []
    for aj, tickers in sorted(por_ajuste.items()):
        tickers = sorted(set(tickers))
        # El LADO de la curva no se pregunta: lo dice la moneda de sus propios
        # bonos. Preguntar algo que el dato ya contesta es hacerle perder tiempo
        # al usuario y abrir la puerta a que se conteste distinto de la realidad.
        ms = monedas.get(aj) or []
        lado = "USD" if ms and ms.count("USD") > len(ms) / 2 else "ARS"
        out.append(_hallazgo(
            "hueco_de_curva", aj.upper(), "ajuste_sin_curva", "alta",
            f"{len(tickers)} bono(s) con ajuste «{aj}» no caen en NINGUNA curva: "
            "están cargados y no aparecen en la tabla, ni en los forwards, ni en "
            "el fair value — sin dar error. La curva se puede CREAR desde acá; lo "
            "único que hay que decidir es de dónde sale su tasa.",
            {"ajuste": aj, "n": len(tickers), "tickers": tickers, "lado": lado}))
    return out



# ── 5) SALUD: el sistema mirándose a sí mismo ────────────────────────────────


def detectar_salud(chequeos: list[dict]) -> list[dict]:
    """Los chequeos de SALUD que NO están en verde, como hallazgos del agente.

    **Por qué SALUD entra acá** (user, 2026-08-17): *«quiero que el agente abarque
    tareas de SALUD y que así como simulamos y hacemos cosas de bonos, también
    aprenda a resolver»*.

    Y encajan sin forzar nada, porque **un chequeo y un hallazgo son el mismo
    objeto**: algo que se evalúa, tiene estado, guarda la evidencia congelada y le
    pide una decisión a alguien. Lo único que cambia es el sujeto — un bono o un
    job. Tenerlos en dos pantallas separadas obligaba a mirar dos lugares para
    contestar UNA pregunta («¿está sano el sistema?»), y ninguna de las dos la
    contestaba entera.

    Los dos módulos se complementan justo donde el otro es débil:

      · el AV Agent razona de forma DETERMINISTA (las lentes) y sabe arreglar;
      · SALUD tiene el HISTORIAL de cada chequeo y un diagnóstico con IA.

    Función PURA: recibe los chequeos ya evaluados. `relevar()` es quien llama a
    `salud.evaluar()`, igual que con el resto — así esto se testea sin base.

    ⚠️ **No duplica el estado de SALUD ni lo reemplaza.** SALUD sigue siendo el
    dueño de la evaluación; acá se la lee. Si un chequeo se arregla solo, deja de
    venir en la lista y el hallazgo caduca en la lectura, como el resto.
    """
    sev = {"error": "alta", "warn": "media"}
    out: list[dict] = []
    for c in chequeos or []:
        estado = (c.get("estado") or "").lower()
        if estado not in sev:            # verde → no es un hallazgo
            continue
        familia = c.get("familia") or "chequeo"
        out.append(_hallazgo(
            # El `ticker` es EL SUJETO del hallazgo — para un bono es el ticker y
            # para un chequeo es su id. El nombre del campo quedó del primer
            # detector; renombrarlo tocaría la tabla, el front y los tres
            # detectores que ya andan, y no cambia lo que significa.
            "salud", c.get("id") or "?", f"salud_{familia}", sev[estado],
            f"{c.get('titulo')}: {c.get('motivo')}",
            {"chequeo_id": c.get("id"), "familia": familia,
             "titulo": c.get("titulo"), "estado": estado,
             "evidencia_salud": c.get("evidencia"),
             "schedule": c.get("schedule"), "tabla": c.get("tabla"),
             "ultimo_at": c.get("ultimo_at"), "esperada_at": c.get("esperada_at"),
             "modulos": c.get("modulos")}))
    return out


# ── 5) EN RUEDA: lo que solo se puede ver con el mercado abierto ─────────────
#
# Pedido del user (2026-08-18, EN rueda): *«necesito que esté prendido el agente
# … al menos de 10:30 a 17. Porque por ej. el AO29 no está con precio, o sea no
# se suscribió, y quisiera saberlo en rueda. El GD46 está con el precio de ARS en
# la curva USD»*.
#
# **Estos dos hallazgos NO EXISTEN de noche.** Que un símbolo no tenga precio a
# las 11 de la mañana es un problema; a las 3 de la madrugada es lo normal. Por
# eso son detectores aparte y no una variante de los otros: su verdad depende de
# la hora, y mezclarlos con los que valen siempre daría falsos positivos todas
# las noches.
#
# **Cero red.** Los dos leen `mercado.curvas` y `mercado.market_snapshot`, que ya
# están en la base. Un monitor que corre cada 5 minutos y pega a 1816 quemaría la
# cuota del día antes del mediodía.

# Cuántos minutos sin actualizarse para considerar que un precio quedó viejo. El
# motor reescribe el snapshot cada pocos segundos; 20 minutos es un papel que
# dejó de operar o una suscripción caída, no un instante sin trades.
PRECIO_VIEJO_MIN = 20

# La banda donde una paridad es creíble. Es la MISMA de `detectar_tasas_
# sospechosas` — importarla en vez de copiarla es lo que evita que dos pantallas
# discutan sobre qué es una paridad sana.


# La rueda, en UTC. Los motores corren 13-20 UTC (10-17 ART) por cron, así que
# fuera de esa ventana el snapshot está viejo POR DISEÑO y no por un problema.
RUEDA_UTC = (13, 20)


def en_rueda(ahora=None) -> bool:
    """¿El mercado está abierto? **De esto depende que la mitad de lo que mira el
    centinela signifique algo.** Un precio sin actualizar hace 282 minutos es un
    problema a las 11 de la mañana y es lo normal a las 18 — la primera corrida
    real marcó los 230 bonos del universo justo después del cierre, que es la
    prueba de que sin esta pregunta el detector no dice nada."""
    from datetime import UTC, datetime
    ahora = ahora or datetime.now(UTC)
    return ahora.weekday() < 5 and RUEDA_UTC[0] <= ahora.hour < RUEDA_UTC[1]


def detectar_sin_precio(bonos: list[dict], snap: dict[str, dict],
                        ahora=None) -> list[dict]:
    """Bonos del master a los que el motor NO les está dando precio, en rueda.

    Cuatro estados distintos, y la diferencia importa porque el arreglo es otro:

      · el bono **no tiene símbolo de mercado cargado**: no hay nada que pedir.
        ⚠️ **Este caso se saltaba en silencio** (un `continue` en la primera
        línea del loop) hasta el 2026-08-19 — o sea que el bono peor cargado del
        master era justo el único que el detector no podía ver. Es además el más
        accionable de los cuatro: el símbolo sale de `mercado.especies`.
      · el símbolo **no está en el snapshot**: nadie lo suscribió. El bono existe
        en `mercado.curvas` y el motor nunca pidió su símbolo.
      · está pero **sin precio** (`last_price` nulo o 0): se suscribió y el
        mercado no le puso una punta. **Un 0 no es un precio.**
      · está con precio pero **viejo**: operó y dejó de hacerlo, o se cayó el
        feed.

    Función PURA: recibe el master y el snapshot ya leídos.
    """
    from datetime import UTC, datetime, timedelta
    ahora = ahora or datetime.now(UTC)
    viejo = ahora - timedelta(minutes=PRECIO_VIEJO_MIN)
    # `sin_punta` y `no_suscripto` valen siempre; `precio_viejo` SOLO en rueda.
    abierto = en_rueda(ahora)
    out: list[dict] = []
    for b in bonos:
        simbolo = (b.get("ticker") or "").strip()      # el símbolo de mercado
        tk = (b.get("ticker_corto") or "").strip().upper()
        if not tk:
            continue        # sin ticker no hay a quién adjudicarle el hallazgo
        if not simbolo:
            # **No es «no aplica»: es el peor caso.** Un bono del master sin
            # símbolo de mercado no puede tener precio nunca, y el motor no
            # falla — ni siquiera lo intenta. Saltearlo dejaba al bono peor
            # cargado como el único invisible para el monitor.
            out.append(_hallazgo(
                "sin_precio", tk, "sin_simbolo", "alta",
                f"«{tk}» no tiene símbolo de mercado cargado: el motor no puede "
                f"pedir un precio que nadie nombró. No es que no opere — es que "
                f"nunca se lo pidió.",
                {"simbolo": None, "curva": b.get("curva"),
                 "estado": "sin_simbolo",
                 "de_donde": "el símbolo sale de `mercado.especies` (la pata del "
                             "ticker); en el master vive en `curvas.instrumento`"}))
            continue
        d = snap.get(simbolo)
        ev = {"simbolo": simbolo, "curva": b.get("curva")}
        if d is None:
            out.append(_hallazgo(
                "sin_precio", tk, "no_suscripto", "alta",
                f"el motor NO está pidiendo «{simbolo}»: el símbolo no aparece en "
                f"el snapshot. El bono está en el master y nadie lo suscribió.",
                {**ev, "estado": "no_suscripto"}))
            continue
        px = d.get("last_price")
        try:
            px = float(px) if px is not None else None
        except (TypeError, ValueError):
            px = None
        if not px:
            out.append(_hallazgo(
                "sin_precio", tk, "sin_punta", "media",
                f"«{simbolo}» está suscripto pero sin precio: el mercado todavía "
                f"no le puso una punta hoy.",
                {**ev, "estado": "sin_punta"}))
            continue
        if not abierto:
            continue     # fuera de rueda, «viejo» es lo normal — ver `en_rueda`
        upd = d.get("updated_at")
        # `updated_at` es `timestamptz` → viene con tz. Si alguna vez llegara
        # naive, compararlo contra uno aware LEVANTA — y un monitor que se cae
        # por un detalle de tipos deja de avisar justo cuando hace falta.
        if upd is not None and upd.tzinfo is None:
            upd = upd.replace(tzinfo=UTC)
        if upd and upd < viejo:
            mins = int((ahora - upd).total_seconds() / 60)
            out.append(_hallazgo(
                "sin_precio", tk, "precio_viejo", "media",
                f"«{simbolo}» no se actualiza hace {mins} min (último {px:,.2f}). "
                f"O dejó de operar, o se cayó el feed.",
                {**ev, "estado": "precio_viejo", "minutos": mins, "precio": px}))
    return out


def detectar_precio_fuera_de_moneda(bonos: list[dict], snap: dict[str, dict],
                                    mep: float | None,
                                    simbolos: set[str] | None = None,
                                    defaults: dict[str, str] | None = None) -> list[dict]:
    """Bonos de curva USD cuyo PRECIO llega en pesos — el caso GD46.

    ⚠️ **CORREGIDO 2026-08-18, y la corrección es la parte que importa.** La
    primera versión llamaba a esto «el precio llega en la moneda equivocada» y
    lo marcaba `alta`. Contra prod dio **46 de 230**, y esa proporción fue la que
    obligó a mirar el motor en vez de creerle al detector:
    `engines/curvas.py::precio_soberano_a_usd` **YA divide por el MEP** cuando el
    símbolo no termina en D/C. O sea que en esos 46 la TEA y la paridad están
    BIEN calculadas — que es exactamente lo que dijo el user de GD46: *«por más
    que la tasa y eso esté bien»*.

    Un detector que llama «alta» a 46 casos sanos no es un detector estricto: es
    uno que enseña a ignorar la lista.

    Lo que SÍ pasa, y es real: **la grilla muestra el precio crudo**, así que en
    la misma columna conviven 102.700 (pesos) y 74,19 (dólares) sin que nada lo
    diga. No es un dato mal cargado — es que el bono cotiza por su pata en pesos.
    Cuando existe la pata D, nombrarla es la información accionable: suscribir
    ESA es lo que haría que la columna muestre dólares.

    Quedan DOS reglas, con severidades distintas porque son problemas distintos:

      · `cotiza_en_pesos` (**baja**): el símbolo no tiene sufijo D/C, el motor
        convierte bien, la grilla muestra pesos. Es contexto, no un error.
      · `precio_fuera_de_escala` (**alta**): el símbolo SÍ es D/C —o sea que el
        motor lo toma como dólares tal cual— y aun así la paridad se va de rango.
        Ahí no hay conversión que lo explique y algo está realmente mal.

    Sin MEP no se puede probar nada y **no se inventa**: devuelve vacío.
    """
    if not mep or mep <= 0:
        return []
    simbolos = simbolos or set()
    out: list[dict] = []
    for b in bonos:
        if (b.get("moneda_eje") or "").upper() != "USD":
            continue
        simbolo = (b.get("ticker") or "").strip()
        tk = (b.get("ticker_corto") or "").strip().upper()
        d = snap.get(simbolo) or {}
        try:
            px = float(d.get("last_price") or 0)
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue                      # eso lo dice el otro detector
        try:
            residual = float(b.get("valor_nominal") or 100) or 100
        except (TypeError, ValueError):
            residual = 100.0
        par_cruda = px / residual * 100
        if PARIDAD_MIN <= par_cruda <= PARIDAD_MAX:
            continue                      # el precio ya viene en dólares
        par_mep = px / mep / residual * 100
        if not (PARIDAD_MIN <= par_mep <= PARIDAD_MAX):
            continue                      # dividir no lo arregla → no es esto

        # El SUFIJO decide qué hace el motor, y por lo tanto si esto es un
        # problema o solo contexto. Es el mismo criterio de
        # `precio_soberano_a_usd`: mira el símbolo, no el ticker corto.
        partes = simbolo.split(" - ")
        sym = partes[2] if len(partes) >= 3 else simbolo
        es_dolar = sym[-1:].upper() in ("D", "C")
        ev = {"simbolo": simbolo, "precio": px, "mep": mep,
              "paridad_cruda": round(par_cruda, 2),
              "paridad_con_mep": round(par_mep, 2), "curva": b.get("curva")}

        if es_dolar:
            out.append(_hallazgo(
                "precio_moneda", tk, "precio_fuera_de_escala", "alta",
                f"«{sym}» termina en {sym[-1].upper()}, así que el motor lo toma "
                f"como dólares TAL CUAL — y aun así la paridad da {par_cruda:,.0f}%. "
                f"Dividido por el MEP daría {par_mep:.1f}%, o sea que el precio "
                f"viene en pesos con un símbolo que dice dólares.",
                {**ev, "sufijo": sym[-1].upper()}))
            continue

        # ⚠️ **¿O ES QUE EL MASTER SUSCRIBE LA PATA EQUIVOCADA?** (2026-08-19)
        #
        # Hay DOS fuentes de símbolos y nadie las cruzaba: `curvas.instrumento`
        # —lo que el motor suscribe— se carga **a mano**, y `mercado.especies`
        # sabe cuál es la pata correcta (`es_default`), derivada de Primary.
        #
        # Medido en prod: de 229 bonos, **3** suscriben una pata distinta de la
        # default (AO29, GD46, CO32) y los tres son justo los que muestran pesos
        # en una curva en dólares. O sea que esto NO era «el bono cotiza así y no
        # hay nada que hacer»: es un dato mal cargado, con la pata correcta ya
        # existente y validada, y con un arreglo de un campo.
        #
        # Por eso deja de ser `baja` (contexto) y pasa a `media` (accionable). No
        # `alta`: la valuación está bien, no hay plata mal contada.
        default = (defaults or {}).get(tk, "")
        if default and default != simbolo:
            out.append(_hallazgo(
                "precio_moneda", tk, "pata_equivocada", "media",
                f"el master suscribe «{sym}» (pesos) pero la pata correcta es "
                f"«{default.split(' - ')[2] if ' - ' in default else default}»: "
                f"por eso la grilla muestra {px:,.2f} al lado de bonos en dólares. "
                f"La valuación está bien (paridad {par_mep:.1f}%), lo mal cargado "
                f"es el símbolo.",
                {**ev, "sugerido": default,
                 "arreglo": "cambiar `mercado.curvas.instrumento` por el símbolo "
                            "sugerido — es el que `mercado.especies` marca como "
                            "`es_default` para este ticker",
                 # **Esto NO se puede omitir**: el universo del motor se arma al
                 # arrancar, así que cambiar el campo no surte efecto hasta el
                 # próximo reinicio — y reiniciar en rueda corta el feed de la
                 # mesa. Una acción que se aplica y no se ve es peor que ninguna.
                 "ojo": "el motor arma su universo al arrancar: el cambio recién "
                        "se ve cuando se reinicia el motor FUERA DE RUEDA"}))
            continue

        # La pata en dólares, si existe. Es lo único accionable de este hallazgo.
        # Se devuelve el SÍMBOLO COMPLETO, no el corto: es lo que se suscribe, y
        # es lo que hay que poder copiar sin volver a armarlo a mano.
        pata_d = next((x for cand in (f"{sym}D", f"{sym}C")
                       for x in simbolos if f" - {cand} - " in x), "")
        out.append(_hallazgo(
            "precio_moneda", tk, "cotiza_en_pesos", "baja",
            f"«{sym}» es de curva USD y cotiza por su pata en PESOS: la grilla "
            f"muestra {px:,.2f} al lado de bonos en dólares. **La valuación está "
            f"bien** — el motor divide por el MEP ({mep:,.2f}) y la paridad real "
            f"es {par_mep:.1f}%. Lo que se ve raro es la columna de precio."
            + (f" La pata en dólares existe: «{pata_d.split(' - ')[2]}»." if pata_d
               else " No encontré una pata en dólares para este ticker."),
            {**ev, "pata_dolar": pata_d}))
    return out


def relevar_live(*, ahora=None) -> dict:
    """**EL MONITOR DE RUEDA.** Corre los dos detectores que solo tienen sentido
    con el mercado abierto. Cero red, cero créditos de 1816.

    Es una función aparte de `relevar()` a propósito: `relevar` cuesta ~29
    créditos (censa 1816) y corre una vez por noche; esto corre cada pocos
    minutos y **no puede pagar nada**. Meterlos juntos habría obligado a elegir
    entre monitorear seguido o no quemar la cuota.
    """
    from api.services.macro import get_ultimo_mep
    from core import curvas_sql, market_snapshot
    from core.postgres import get_pool

    bonos = curvas_sql.cargar_todos() or []
    simbolos = [(b.get("ticker") or "").strip() for b in bonos if b.get("ticker")]
    snap = market_snapshot.cols_map(simbolos, ["last_price", "updated_at"]) or {}

    # El MEP puede fallar sin que eso invalide el resto: sin él, el detector de
    # moneda devuelve vacío (no inventa) y el de precios sigue igual.
    try:
        mep = float((get_ultimo_mep() or {}).get("mep") or 0) or None
    except Exception as e:
        logger.warning("av_agent live: sin MEP (%s)", e)
        mep = None

    # Los símbolos que EXISTEN + **cuál es la pata DEFAULT de cada ticker**.
    # `mercado.especies` es la fuente única de las patas, y la default es la que
    # el master debería estar suscribiendo: cruzar las dos es lo que destapa la
    # pata equivocada (AO29/GD46/CO32). Una sola query para las dos cosas.
    defaults: dict[str, str] = {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT simbolo, ticker, es_default FROM mercado.especies")
            filas = cur.fetchall()
        simbolos = {r[0] for r in filas if r[0]}
        defaults = {(r[1] or "").strip().upper(): r[0]
                    for r in filas if r[2] and r[0] and r[1]}
    except Exception as e:
        logger.warning("av_agent live: sin catálogo de especies (%s)", e)
        simbolos = set()

    hallazgos: list[dict] = []
    # LATENCIA entra acá y no al job nocturno: un endpoint degradado importa
    # MIENTRAS pasa, y cuesta una query sobre un agregado que ya existe.
    from api.services.av_agent_latencia import detectar_latencia
    from api.services.av_agent_motores import detectar_motores

    # LATENCIA y MOTORES entran al monitor de rueda: los dos importan MIENTRAS
    # pasan. Las TABLAS no — barrer 200 tablas cada 5 minutos sería absurdo, y su
    # atraso se mide en horas: van en el job nocturno.
    for nombre, fn in (("sin_precio", lambda: detectar_sin_precio(bonos, snap, ahora)),
                       ("precio_moneda",
                        lambda: detectar_precio_fuera_de_moneda(bonos, snap, mep,
                                                                simbolos, defaults)),
                       ("latencia", detectar_latencia),
                       ("motores", detectar_motores)):
        try:
            hallazgos.extend(fn())
        except Exception as e:      # un detector roto no puede tapar al otro
            logger.exception("av_agent live: detector %s falló: %s", nombre, e)

    return {"alcance": "live", "hallazgos": hallazgos, "mep": mep,
            "bonos": len(bonos), "con_snapshot": len(snap)}


# ── Orquestación (el único que lee de la base / la red) ──────────────────────


def relevar(*, alcance: str = "soberanos",
            universo_1816: dict[str, dict] | None = None) -> dict:
    """Corre los tres detectores y devuelve `{alcance, universo, hallazgos, resumen}`.

    **READ-ONLY**: no escribe una sola fila en `mercado.curvas`. Persistir el
    resultado es responsabilidad del job (`jobs/av_agent.py`), y solo en la tabla
    propia del agente.

    `universo_1816` se puede inyectar (un censo ya pagado) para no repetir los ~29
    créditos — así el job, los tests y un diag comparten la misma foto.
    """
    from core import curvas_sql, market_snapshot
    from core.postgres import get_pool

    # ⚠️ **1816 NO PUEDE FRENAR LA RELEVADA ENTERA** (2026-08-17). El 429 del
    # proveedor dejaba al job muerto antes del primer detector, y tres de los
    # cuatro no necesitan la red para nada. Se intenta la API, y si no contesta se
    # sigue con la copia local del catálogo; si tampoco está, se corre igual y se
    # dice qué quedó sin mirar. **Degradar es distinto de fallar**: lo que no se
    # pudo consultar se declara, no se disfraza de "no hay nada".
    fuente_univ, catalogo_at, error_univ = "1816", "", ""
    if universo_1816 is None:
        try:
            universo_1816 = (mercado_1816.censar() or {}).get("instrumentos") or {}
        except Exception as e:
            error_univ = str(e)[:200]
            universo_1816, catalogo_at = universo_local()
            fuente_univ = "catalogo_local" if universo_1816 else "sin_universo"
            logger.warning("av_agent: 1816 no contestó (%s) — se usa el catálogo "
                           "local (%d instrumentos, foto del %s)", error_univ,
                           len(universo_1816), catalogo_at or "?")

    docs = curvas_sql.cargar_todos()
    simbolos = [s for s in ((d.get("ticker") or "").strip() for d in docs) if s]
    metricas = market_snapshot.cols_map(
        simbolos, ["tea", "paridad", "duration", "last_price"])

    # Las tres lecturas de abajo comparten un contrato: si la query falla, el dato
    # queda en `None` y la regla que lo usa **no corre**. Marcar 222 bonos como
    # huérfanos porque se cayó una query sería el peor falso positivo posible —
    # "no pude mirar" jamás puede convertirse en "no está".
    en_assets: set[str] | None = None
    en_cartera: set[str] | None = None
    ignorados: set[str] = set()
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT upper(btrim(ticker)) FROM portafolio.assets "
                        "WHERE ticker IS NOT NULL AND ticker <> ''")
            en_assets = {r[0] for r in cur.fetchall()}
    except Exception:
        en_assets = None

    try:
        from api.services.acreencias import codigo_de_unidad
        with get_pool().connection() as conn, conn.cursor() as cur:
            # El código sale de la UNIDAD ('[57187] OLC3O' → 'OLC3O') y no de un
            # join con assets: el bono que nos interesa es justamente el que NO
            # tiene asset, así que joinear por ahí lo escondería.
            cur.execute("SELECT DISTINCT unidad FROM portafolio.tenencia "
                        "WHERE aum = 'si' AND fecha = ("
                        "  SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si')")
            en_cartera = {codigo_de_unidad(r[0]) for r in cur.fetchall() if r[0]}
            en_cartera.discard("")
    except Exception:
        en_cartera = None

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT upper(btrim(ticker)) FROM mercado.av_agent_ignorados")
            ignorados = {r[0] for r in cur.fetchall() if r[0]}
    except Exception:
        # Acá el default seguro es el CONTRARIO: sin la lista se reporta de más,
        # que es ruido; asumir que todo está ignorado escondería hallazgos reales.
        ignorados = set()

    # **Sin universo NO se buscan faltantes.** Un universo vacío haría que
    # `detectar_faltantes` no reporte ninguno, y "no pude mirar" jamás puede
    # convertirse en "no falta nada" — es el mismo contrato que ya rige `en_assets`
    # y `en_cartera` unas líneas más arriba.
    faltantes = (detectar_faltantes(universo_1816, docs, alcance=alcance,
                                    ignorados=ignorados, en_cartera=en_cartera,
                                    simbolos_primary=simbolos_primary())
                 if universo_1816 else [])
    try:
        from api.services import salud
        chequeos_salud = salud.evaluar()
    except Exception:
        logger.warning("av_agent: no se pudo evaluar SALUD", exc_info=True)
        chequeos_salud = []

    hallazgos = [
        *faltantes,
        *detectar_sin_flujo(docs, universo_1816),
        *detectar_tasas_sospechosas(docs, metricas, en_assets, en_cartera,
                                    universo_1816=universo_1816),
        *detectar_huecos_de_curva(docs),
        # SALUD entra como un detector más. Su lectura va en `try` propio: que la
        # observabilidad se caiga NO puede tumbar la relevada de bonos — el mismo
        # contrato que ya rige el universo de 1816.
        *detectar_salud(chequeos_salud),
    ]
    # **El «no me interesa» se aplica a los CUATRO tipos, en UN solo lugar.** Antes
    # solo lo respetaba `detectar_faltantes` (recibía `ignorados` por parámetro), y
    # los otros tres seguían reportando un ticker ya descartado. Filtrar al final
    # es lo que hace imposible que un detector NUEVO se olvide de mirarlo.
    hallazgos = [h for h in hallazgos
                 if (h.get("ticker") or "").strip().upper() not in ignorados]

    resumen: dict[str, int] = {}
    for h in hallazgos:
        resumen[h["tipo"]] = resumen.get(h["tipo"], 0) + 1
        resumen[f"regla:{h['regla']}"] = resumen.get(f"regla:{h['regla']}", 0) + 1

    return {
        "alcance": alcance,
        "universo": {"1816": len(universo_1816), "mio": len(docs),
                     # De dónde salió el universo y con qué foto se comparó. Sin
                     # esto, una corrida degradada se lee igual que una completa.
                     "fuente": fuente_univ, "catalogo_at": catalogo_at,
                     "error_1816": error_univ,
                     "faltantes_evaluados": bool(universo_1816),
                     "con_metricas": len(metricas),
                     "assets_leidos": en_assets is not None,
                     "cartera_leida": en_cartera is not None,
                     "en_cartera": len(en_cartera or ()),
                     "chequeos_salud": len(chequeos_salud),
                     "ignorados": len(ignorados)},
        "hallazgos": hallazgos,
        "resumen": resumen,
    }
