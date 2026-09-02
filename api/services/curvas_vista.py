"""api/services/curvas_vista.py — la tab CURVAS del rediseño, en UN request.

Doc madre: `docs/RENTA_FIJA.md` §0 (paso 3a). Service PURO (sin FastAPI).

**Qué reemplaza.** Hoy la tab arma la tabla con 4 fetches y clasifica del lado
del navegador: `renta-fija` (los 398 instrumentos del snapshot), `titulos/flujos`
(240 KB — el cronograma COMPLETO de los 222 bonos, del que usa 5 campos para
armar el mapa ticker→curva) y dos `fair-value`. Con los ejes ya en la base
(paso 2) **el backend sabe qué es cada bono**, así que ese mapa deja de existir:
se sirve todo clasificado en una sola respuesta.

**Lo que NO cambia**: qué bono cae en qué pill. Eso lo decide `core.curvas_ejes`,
el MISMO módulo que usó el test de equivalencia — no hay una segunda copia de la
regla que pueda divergir de la del front.

**Lo que sí cambia** (y es lo que se buscaba): entran las ONs. `emisor_tipo` viaja
en cada bono para que el filtro de EMISOR sea client-side y gratis: cambiar de
emisor no vuelve a pegarle al backend.

**Frescura**: TTL 10s, igual que `get_renta_fija` — la tabla es live y el motor
de curvas refresca cada 5s.
"""
from __future__ import annotations

from api.cache import cached
from api.services._sql import _f, _q
from api.services.renta_fija_sql import _METRIC_COLS, _tc_breakeven
from core import curvas_ejes as ce

# Duration mínima para publicar TEA/TNA. Debajo de esto la tasa es RUIDO, no un
# rendimiento: anualizar 3 días amplifica una diferencia de centavos a tres
# dígitos. Medido en pantalla el 2026-08-16 — AFCHO (vence en 3 días) mostraba
# TEA 142,1%, CS450 −49,1%, HBCAO −25,0%, y de paso esos outliers estiraban el
# eje del gráfico hasta aplastar a los otros 120 bonos contra el cero.
#
# **La TEA no se borra: se marca.** El bono sigue en la tabla con su precio y su
# vencimiento, y viaja `tasa_ruido=True` para que el front la muestre apagada y
# la EXCLUYA del gráfico. Ocultar el número sería mentir por omisión; mostrarlo
# como si fuera comparable es peor.
DUR_MIN_TASA = 0.05          # ~18 días corridos


def es_tasa_ruido(metrics: dict, emisor_tipo: str | None) -> bool:
    """¿La tasa de este bono es un artefacto de plazo, no un rendimiento?

    Se decide por DURATION y no por días al vencimiento porque la duration ya
    pondera el flujo: un bullet a 10 días y un amortizante que paga casi todo la
    semana que viene tienen el mismo problema y la fecha de vencimiento no lo dice.

    ⚠️ **SOLO CORPORATIVOS** (corregido 2026-08-16 tras verlo en pantalla). Una
    Lecap a 15 días con TEA 26,6% NO es ruido: cotiza con volumen todos los días,
    su precio es real y su tasa es la que la mesa opera. Sacarla del gráfico
    borraba el tramo corto de la curva soberana, que es justo el que más se mira.
    Lo que explota es la ON ilíquida a 3 días, cuyo último precio puede ser viejo
    o desalineado — ahí anualizar amplifica el desvío a tres dígitos (AFCHO 142%,
    CS450 −49%).

    O sea: el problema nunca fue el plazo corto, fue **plazo corto SIN liquidez**.
    El emisor es el proxy que tenemos hoy; el día que haya una medida de liquidez
    por bono, ESA es la condición correcta y esta regla se reemplaza.

    Vive server-side para que la tabla y el gráfico no puedan contradecirse."""
    if emisor_tipo != "corporativo":
        return False
    dur = metrics.get("duration")
    return dur is not None and float(dur) < DUR_MIN_TASA

# El orden en que se muestran las pills dentro de cada lado.
_ORDEN = {"tasa_fija": 1, "cer": 2, "tamar": 3, "duales": 4,
          "hard_dolar": 1, "dolar_linked": 2}

_EMISOR_LABEL = {"soberano": "SOBERANO", "provincial": "PROVINCIAL",
                 "corporativo": "CORPORATIVO", "bcra": "BCRA"}


def _bonos_crudos() -> list[dict]:
    """`mercado.curvas` con sus ejes + el snapshot live, en UNA query.

    El join va del master (222 filas) al snapshot y no al revés: la vista muestra
    BONOS, no tickers de pantalla. `market_snapshot` tiene 398 filas porque
    incluye especies y plazos que no son instrumentos del master — traerlos para
    descartarlos en el navegador es justamente lo que se está sacando.
    """
    cols = ", ".join(f"s.{c}" for c, _ in _METRIC_COLS)
    return _q(
        f"SELECT c.ticker AS ticker_corto, c.instrumento AS ticker, c.curva, c.tipo, "
        f"c.fecha_vencimiento, c.emisor, c.emisor_tipo, c.moneda_eje, c.ajuste, "
        f"c.ajuste_alt, c.ley, c.flujo_vencimiento, e.industria, {cols} "
        f"FROM mercado.curvas c "
        f"LEFT JOIN mercado.market_snapshot s ON s.ticker = c.instrumento "
        # La INDUSTRIA se resuelve desde el EMISOR en la LECTURA, no se guarda en
        # el bono. Ese es todo el punto: el dato existe UNA vez y no puede
        # contradecirse consigo mismo. El LEFT JOIN devuelve NULL cuando el emisor
        # todavía no tiene industria — y NULL es "sin clasificar", que la vista
        # tiene que MOSTRAR como bucket propio, nunca mezclar con `otros`.
        f"LEFT JOIN mercado.emisores e "
        f"       ON upper(btrim(e.emisor)) = upper(btrim(c.emisor))",
    )


def _tasas_agente() -> dict[str, dict]:
    """`ticker → tasa de 1816`, para los bonos que **el motor no pudo calcular**.

    La llena `jobs/agente_tasa` a partir de lo que encuentra la habilidad
    `bono_sin_tasa`: papeles que OPERAN y a los que el motor no les saca la TEA,
    así que la fila sale en «--» con el bono cotizando.

    ⚠️ **Es el ÚLTIMO recurso y no le gana a nadie.** Se aplica solo si después
    de todo lo demás la TEA sigue vacía: donde el motor calcula, su número es
    LIVE y el de 1816 tiene atraso — reemplazarlo sería empeorar la vista para
    ganar consistencia con un proveedor.

    Mismo patrón que TAMAR: **NO se escribe en `market_snapshot`** (esa tabla es
    del motor) y las dos se juntan ACÁ, en la lectura, con cada fila diciendo de
    dónde salió su tasa.
    """
    import logging
    try:
        from agente import tasa_1816
        return tasa_1816.tasas()
    except Exception as e:
        # Sin esto la vista sigue: una tasa de respaldo que no se pudo leer deja
        # la celda como estaba, no rompe la tabla.
        logging.getLogger(__name__).warning(
            "curvas_vista: sin tasas del agente (%s)", e)
        return {}


def _tamar_1816() -> dict[tuple[str, str], dict]:
    """`(ticker, pata) → fila de 1816`. Lo llena `jobs/tamar_1816` cada 30'.

    Fuente SEPARADA de `market_snapshot` a propósito: eso es del motor (Primary,
    live, cada 5s) y esto es 1816 (BYMA, con delay). Juntarlas en la tabla habría
    dejado una TEA sin forma de saber de dónde salió; se juntan acá, en la
    lectura, y cada bono viaja con su `tea_fuente`.
    """
    return {(r["ticker"], r["pata"]): r for r in _q(
        "SELECT ticker, pata, tea, tna, spread, precio_clean, duration, paridad, "
        "fecha_operacion FROM mercado.tamar_1816")}


def _fijados_cortos() -> set[str]:
    """Tickers CER con el CER de liquidación ya publicado (se comportan como tasa
    fija). MISMA fuente que la vista de hoy — si esto se calculara distinto, un
    bono cambiaría de pill en silencio."""
    from api.services.renta_fija import _bonos_cer_fijados
    return {t.split(" - ")[2].strip() if " - " in t else t.strip()
            for t in (_bonos_cer_fijados() or [])}


def _armar(rows: list[dict], fijados: set[str], mep: float | None = None,
           tamar: dict[tuple[str, str], dict] | None = None,
           tasas_agente: dict[str, dict] | None = None) -> dict:
    """Puro: filas crudas → payload de la vista. Testeable sin base.

    `mep` y `tamar` entran COMO PARÁMETROS y no se leen acá adentro a propósito:
    esta función es la que decide qué ve el usuario y se testea sin base. Traer el
    MEP desde adentro la haría depender de la red y de un import cíclico con
    `macro`. Sin MEP el `tc_breakeven` sale None, que es "no se pudo calcular" — no 0.
    """
    tamar = tamar or {}
    tasas_agente = tasas_agente or {}
    bonos: list[dict] = []
    sin_clasificar: list[str] = []
    n_pill: dict[str, int] = {}
    n_emisor: dict[str, int] = {}

    for r in rows:
        tc = (r.get("ticker_corto") or "").strip()
        ejes = None
        if r.get("emisor_tipo") and r.get("moneda_eje") and r.get("ajuste"):
            ejes = ce.Ejes(r["emisor_tipo"], r["moneda_eje"], r["ajuste"],
                           r.get("ley"), r.get("ajuste_alt"))
        if ejes is None:
            sin_clasificar.append(tc)
            continue
        fijado = tc in fijados
        del_bono = ce.pills(ejes, fijado)
        if not del_bono:          # badlar/tpm/caución: sin pill acordada todavía
            sin_clasificar.append(tc)
            continue

        metrics = {}
        for col, key in _METRIC_COLS:
            v = _f(r.get(col))
            if v is not None:
                metrics[key] = v

        # UNA FILA POR PILL. Un dual CER+TAMAR sale dos veces, con la misma ficha
        # y distinto `pill`/`lado`, y así aparece en las dos tablas — que es donde
        # el trader lo busca. Se emite repetido en vez de mandar una lista de pills
        # a propósito: el front ya filtra por `b.pill === pill`, así que el
        # contrato NO cambia y no hace falta que los dos deploys sean simultáneos
        # (el front va a Vercel solo; el backend se sube a mano y siempre después).
        for pill in del_bono:
            # ── LA TASA DE **ESTA** PATA ────────────────────────────────────
            #
            # `metrics` viene de `market_snapshot`, que tiene UNA fila por símbolo
            # y por lo tanto UNA sola TEA — y esa TEA es siempre la de la pata
            # PRINCIPAL (`ajuste`): es la que el motor calcula, y la que
            # `jobs/tamar_1816` escribe cuando el motor no puede. De ahí sale todo:
            #
            #   · pata PRINCIPAL → el snapshot ya es de esta pata. Se usa tal cual.
            #     **1816 NO se mete acá aunque tenga el dato**: donde el motor
            #     calcula (un CER, un dólar linked, una tasa fija) su número es
            #     LIVE y el de 1816 tiene media hora de atraso. Reemplazarlo sería
            #     empeorar la vista para ganar consistencia con un proveedor.
            #   · pata SECUNDARIA → el snapshot tiene la tasa de la OTRA pata, así
            #     que NO sirve y hay que descartarla. Ahí sí manda 1816, que es el
            #     único que publica las patas por separado. Sin dato, la celda
            #     queda vacía — que es lo correcto: mostrar la tasa de la otra
            #     pata es exactamente el bug que esto viene a arreglar.
            pata = ce.pata_de_pill(ejes, pill, fijado)
            es_principal = pata is not None and pata == r.get("ajuste")
            t1816 = tamar.get((tc, pata)) if pata else None
            m_pill = dict(metrics)
            fuente, fecha_1816, margen = None, None, None

            # ¿Manda 1816? SOLO donde el motor no puede con esta pata:
            #   · pata SECUNDARIA → el snapshot tiene la tasa de la otra pata.
            #   · `ajuste='tamar'` y emisor NO corporativo → `rama_calculo`
            #     devuelve `otros` y el motor no calcula tasa. Lo que haya quedado
            #     en el snapshot es BASURA VIEJA: el anti-TEA-fantasma no limpia
            #     esa rama, así que sobrevive un valor de antes de la migración de
            #     ejes (medido: TMF27 con TEA −25,0% y TEM −2,37%).
            #
            # ⚠️ La excepción del CORPORATIVO no es un detalle: un TAMAR de un
            # emisor corporativo va a la rama `on`, o sea que el motor **sí** lo
            # calcula, y en vivo. Sin esta condición, ZPC1O —el único corporativo
            # con pata TAMAR que 1816 cubre— mostraría el número del proveedor acá
            # y el del motor en la tabla RENTA FIJA: dos tabs con dos tasas para
            # el mismo bono, y ninguna pista de cuál mirar.
            manda_1816 = t1816 is not None and (
                not es_principal
                or (r.get("ajuste") == "tamar"
                    and r.get("emisor_tipo") != "corporativo"))

            # Las métricas del snapshot son SIEMPRE de la pata PRINCIPAL. Se
            # descartan en dos casos, y el segundo NO depende de que haya con qué
            # reemplazarlas:
            #   · esta fila es la pata SECUNDARIA → esos números son de la otra
            #     pata y no le pertenecen. Sin dato de 1816 la celda queda VACÍA,
            #     que es la respuesta correcta (así TTD26 dejó de aparecer en TASA
            #     FIJA con la TEA de su pata TAMAR).
            #   · manda 1816 → lo que quedó ahí es basura de un cálculo viejo.
            # Se van TODAS las derivadas y no solo la TEA: dejar `mod_duration`
            # del motor al lado de una duration de 1816 mezcla dos cálculos en la
            # misma fila y nadie podría decir cuál de los dos está mal.
            #
            # ⚠️ **TODAS incluye `duration` y `paridad`** (2026-09-02). Estaban
            # fuera de esta lista, y no se notaba porque justo abajo 1816 las
            # repone — pero SOLO si las trae. Cuando no las trae (el job las
            # cuenta como `sin_dato`: 8 de los 9 corporativos con pata TAMAR),
            # la fila salía con la TEA de 1816 al lado de la DURATION y la
            # PARIDAD del cálculo que esta misma rama acababa de declarar
            # basura. Medido: TMF27 mostraba TNA 32,8% (1816) con DUR 0,53 y
            # PARIDAD 99,0 heredadas de su TEA vieja de −25,0%.
            #
            # No es solo una columna fea: `tasa_ruido` se decide POR DURATION,
            # así que una duration ajena podía apagar —o dejar prendida— una
            # tasa que no le corresponde. Sin dato la celda queda vacía, que es
            # la misma respuesta que ya se eligió para la TEA.
            if manda_1816 or not es_principal:
                for k in ("TEA", "TEM", "TNA", "mod_duration", "convexity",
                          "duration", "paridad"):
                    m_pill.pop(k, None)

            if manda_1816 and t1816.get("tea") is not None:
                m_pill["TEA"] = float(t1816["tea"])
                # La TNA VIENE de 1816, no se deriva. El front la derivaba de la
                # TEA (`TEM×12`) porque el snapshot no la publica; teniendo la del
                # proveedor, derivarla sería inventar una discrepancia.
                for col, key in (("tna", "TNA"), ("duration", "duration"),
                                 ("paridad", "paridad")):
                    if t1816.get(col) is not None:
                        m_pill[key] = float(t1816[col])
                fuente, fecha_1816 = "1816", t1816.get("fecha_operacion")

            # ── ÚLTIMO RECURSO: el bono opera y el motor no le saca la TEA ──
            #
            # Si después de todo lo de arriba la celda sigue vacía y el papel
            # TIENE precio, la fila sale en «--» con el bono cotizando. Ahí entra
            # lo que `jobs/agente_tasa` le pidió a 1816.
            #
            # Va al final y con `is None` a propósito: **no le puede ganar al
            # motor**. Donde el motor calcula, su número es live.
            if (m_pill.get("TEA") is None and metrics.get("last_price")
                    and (ta := tasas_agente.get(tc))
                    and ta.get("tea") is not None):
                m_pill["TEA"] = float(ta["tea"])
                if ta.get("duration") is not None:
                    m_pill["duration"] = float(ta["duration"])
                fuente, fecha_1816 = "1816", ta.get("tea_fecha")

            # El MARGEN sobre la TAMAR: lo que la mesa mira de un TAMAR. En
            # fracción (0.0973 = 9,73%), la MISMA escala que la TEA. Solo 1816 lo
            # publica, así que no depende de qué pata sea.
            if t1816 and t1816.get("spread") is not None:
                margen = float(t1816["spread"])

            bonos.append({
                "ticker_corto": tc, "instrumento": r.get("ticker"),
                "pill": pill, "lado": ce.lado_de(pill),
                "emisor_tipo": ejes.emisor_tipo, "emisor": r.get("emisor"),
                "moneda": ejes.moneda, "ajuste": ejes.ajuste,
                "ajuste_alt": ejes.ajuste_alt, "ley": ejes.ley,
                # Solo para corporativos: un soberano no tiene industria, y
                # mandarla en null para todos haría que el filtro del gráfico
                # muestre un bucket "sin clasificar" con 60 soberanos adentro.
                "industria": (r.get("industria")
                              if ejes.emisor_tipo == "corporativo" else None),
                "tipo": r.get("tipo"), "vencimiento": r.get("fecha_vencimiento"),
                "cer_fijado": fijado,
                "flujo_vencimiento": _f(r.get("flujo_vencimiento")),
                # TC al que el bono en pesos empata contra comprar MEP hoy. Solo
                # tiene sentido donde el flujo final está determinado: tasa fija
                # nativa o CER ya fijado. Faltaba en esta tabla (la vieja sí lo
                # tenía) — se calcula server-side, como todo lo derivable.
                "tc_breakeven": (
                    _tc_breakeven(metrics.get("last_price"),
                                  _f(r.get("flujo_vencimiento")), mep)
                    if pill == "tasa_fija" else None),
                # La tasa de este bono es ruido por duration ~0 (ver DUR_MIN_TASA).
                "tasa_ruido": es_tasa_ruido(m_pill, ejes.emisor_tipo),
                # De qué pata es esta fila y de dónde salió su tasa. `None` en
                # `tea_fuente` = el motor (live). Viaja SIEMPRE, aunque hoy solo
                # el TAMAR use la otra fuente: una tasa sin procedencia obliga a
                # adivinar de dónde vino, y ese es el bug que no se ve.
                "pata": pata,
                "tea_fuente": fuente,
                "tea_fecha": fecha_1816,
                "margen": margen,
                "metrics": m_pill,
            })
            n_pill[pill] = n_pill.get(pill, 0) + 1
        # El emisor cuenta BONOS, no filas: un dual que sale en dos pills sigue
        # siendo un bono. Sin este cuidado el filtro EMISOR diría 222 sobre 221 y
        # nadie lo notaría — el contador simplemente estaría un poco alto.
        n_emisor[ejes.emisor_tipo] = n_emisor.get(ejes.emisor_tipo, 0) + 1

    # `pills_disponibles` = las de código + las que agregó el catálogo, y
    # `display_de`/`lado_de` no pueden tirar KeyError: una curva creada desde el
    # AV Agent aparece en la barra sin tocar el front ni este archivo.
    pills = sorted(
        ({"codigo": p, "display": ce.display_de(p), "lado": ce.lado_de(p),
          "orden": _ORDEN.get(p, 99), "n": n_pill.get(p, 0)}
         for p in ce.pills_disponibles()),
        key=lambda x: (x["lado"], x["orden"], x["display"]),
    )
    emisores = sorted(
        ({"codigo": e, "label": _EMISOR_LABEL.get(e, e.upper()), "n": n}
         for e, n in n_emisor.items()),
        key=lambda x: -x["n"],
    )
    return {
        "pills": pills,
        "emisores": emisores,
        "bonos": bonos,
        # NO se ocultan: un bono sin clasificar tiene que ser visible como
        # pendiente, no desaparecer. Son los que 1816 no tiene + los ajustes sin
        # pill (badlar/tpm/caución).
        "sin_clasificar": sorted(t for t in sin_clasificar if t),
    }


@cached(ttl=10)
def get_curvas_vista() -> dict:
    """La tab CURVAS entera: catálogo de pills, filtro de emisores y los bonos ya
    clasificados con sus métricas live."""
    # El MEP se lee ACÁ (una vez por request) y se inyecta: `_armar` queda pura.
    # Import lazy — `macro` importa de vuelta a este módulo.
    from api.services.macro import get_ultimo_mep
    doc = get_ultimo_mep()
    raw = doc.get("mep") if doc else None
    mep = float(raw) if raw and raw > 0 else None
    return _armar(_bonos_crudos(), _fijados_cortos(), mep, _tamar_1816(),
                  _tasas_agente())
