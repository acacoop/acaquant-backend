"""Detectores de MERCADO. Doc: `docs/AGENT_2.0.md` §5.

Todos devuelven `list[Hallazgo]` o levantan `SinDatos`. **Ninguno escribe.**
"""
from __future__ import annotations

import logging
from datetime import UTC, timedelta

from agente import fuentes, reloj
from agente.tipos import Hallazgo, SinDatos

logger = logging.getLogger(__name__)

# Qué emisores entran al censo de faltantes. `soberanos` incluye `bcra` porque
# los BOPREALes viven en la curva "BCRA USD" de 1816 y de nuestro lado están del
# lado soberano — el mismo criterio del discovery.
ALCANCE = frozenset({"soberano", "bcra"})
# 1816 publica 6 Globales en EUROS que no operamos. Se excluye por MONEDA y no
# anotando seis tickers: una regla estructural sigue valiendo cuando emitan el
# séptimo; una lista de excepciones, no.
MONEDAS_SEGUIDAS = frozenset({"ARS", "USD"})


def _tk(x) -> str:
    return (x or "").strip().upper()


def _es_pata_1816(ticker: str) -> bool:
    """1816 publica las patas de un dual como tickers aparte (`TXMD9 @TAMAR`).
    **No son instrumentos**: `/cashflow` les da 404 porque el cuadro lo tiene el
    ticker BASE, que ya se reporta por su cuenta."""
    return "@" in (ticker or "")


# ═══ soberanos_faltantes ═══════════════════════════════════════════════════
def soberanos_faltantes(u: dict) -> list[Hallazgo]:
    """Bonos que 1816 lista y no están en `mercado.curvas`.

    ⚠️ El cruce va por el TICKER DIRECTO. El agente viejo le sacaba la letra
    D/C final «por las dudas»: eso era una defensa contra un caso que el
    renombre de columnas de 2026-08-15 ya cerró — `curvas.ticker` ES la PK
    (`AL30`), el símbolo de mercado vive en `instrumento`.
    """
    from core import curvas_ejes

    univ = fuentes.universo_1816()
    if univ is None:
        raise SinDatos("ni 1816 ni el catálogo local contestaron: no puedo "
                       "afirmar que falte nada")
    docs = fuentes.master()
    if docs is None:
        raise SinDatos("no pude leer mercado.curvas")

    mios = {_tk(d.get("ticker_corto")) for d in docs} - {""}
    cartera = fuentes.en_cartera() or set()
    tickers_primary = fuentes.tickers_en_primary()

    out, sin_primary = [], []
    for ticker, inst in sorted((univ["instrumentos"] or {}).items()):
        if _es_pata_1816(ticker):
            continue
        tk = _tk(ticker)
        if not tk or tk in mios:
            continue
        curva = inst.get("_curva") or ""
        ejes = curvas_ejes.desde_1816(curva)
        if ejes is not None:
            if ejes.emisor_tipo not in ALCANCE:
                continue
            if ejes.moneda not in MONEDAS_SEGUIDAS:
                continue
        lo_tenemos = tk in cartera
        # ⚠️ **NO COTIZA EN PRIMARY → NO ES UN FALTANTE.** Si no se le puede
        # poner precio, el bono no vale nada y reportarlo cada corrida es ruido
        # permanente que empuja hacia abajo a los que sí importan.
        # La EXCEPCIÓN es la cartera: ahí el problema es más grave, no menor.
        if (not lo_tenemos and tickers_primary is not None
                and tk not in tickers_primary):
            sin_primary.append(tk)
            continue
        out.append(Hallazgo(
            sujeto=tk, regla="no_esta_en_curvas",
            severidad="alta" if lo_tenemos else "media",
            problema=(f"⚠ LO TENÉS EN CARTERA y no está en el master: hoy no "
                      f"valúa. 1816 lo publica en «{curva}»." if lo_tenemos else
                      f"1816 lo publica en «{curva}» y no está en el master."),
            que_hacer=("Darlo de alta en `mercado.curvas` con los ejes de su "
                       f"curva ({curva or 'sin clasificar'}) y su cronograma "
                       "desde 1816."),
            evidencia={
                "curva_1816": curva, "ticker_1816": ticker,
                "emisor": inst.get("emisorNombre") or inst.get("emisor"),
                "denominacion": inst.get("denominacion"),
                "moneda": inst.get("monedaDenom"),
                "isin": inst.get("isinCode"),
                "vencimiento_1816": inst.get("fechaVencimiento"),
                "en_cartera": lo_tenemos,
                "curva_desconocida": ejes is None,
                "fuente_universo": univ["fuente"]}))
    if sin_primary:
        logger.info("soberanos_faltantes: %d descartados por no cotizar en "
                    "Primary: %s", len(sin_primary), ", ".join(sorted(sin_primary)[:20]))
    return out


# ═══ bono_sin_flujo ════════════════════════════════════════════════════════
def bono_sin_flujo(u: dict) -> list[Hallazgo]:
    """Bonos del master sin cronograma de pagos: no valúan.

    ⚠️ El predicado es `tiene_flujo_def`, **no `bool(doc['flujos'])`**. Una
    LECAP es cupón cero: no tiene array y NO le falta nada — el motor la valúa
    con `flujo_vencimiento`. Mirar solo el array marcaba 11 letras que rinden
    perfecto.
    """
    from datetime import date

    from api.services.acreencias import tiene_flujo_def

    docs = fuentes.master()
    if docs is None:
        raise SinDatos("no pude leer mercado.curvas")
    univ = fuentes.universo_1816()
    en_1816 = {_tk(t) for t in ((univ or {}).get("instrumentos") or {})}

    hoy, out = date.today(), []
    for d in docs:
        tk = _tk(d.get("ticker_corto"))
        if not tk or tiene_flujo_def(d, hoy):
            continue
        # La diferencia entre «esto lo completa el agente» y «esto necesita el
        # prospecto». Un agente que no puede decir «no sé» empieza a rellenar.
        resoluble = bool(en_1816) and tk in en_1816
        out.append(Hallazgo(
            sujeto=tk, regla="sin_flujo",
            severidad="alta" if resoluble else "media",
            problema=("Sin cronograma de pagos: no tiene TEA, no entra al "
                      "gráfico y no aporta al fair value."),
            que_hacer=("Bajar el cuadro de 1816 y darlo de alta." if resoluble
                       else "1816 tampoco lo publica: hay que modelarlo a mano "
                            "en Manager → TÍTULOS."),
            evidencia={"resoluble_con_1816": resoluble, "curva": d.get("curva"),
                       "emisor": d.get("emisor"),
                       "moneda_flujo": d.get("moneda_flujo"),
                       "vencimiento": d.get("fecha_vencimiento")}))
    return out


# ═══ bono_sin_tasa ═════════════════════════════════════════════════════════
#
# ⚠️ **REEMPLAZA A `tasa_sospechosa`** (user: *«NO FUNCIONA HOY EN DÍA»*). Aquél
# metía SEIS reglas bajo un nombre, con umbrales a dedo (paridad 40-160, TEA
# -30%/+60%) que producían la mayor parte del ruido de la pantalla.
#
# Queda UNA regla, que es el cruce de dos condiciones:
#
#     precio NO + tasa NO  →  ilíquido. No cotiza a ninguna tasa. NO es hallazgo
#     precio SÍ + tasa SÍ  →  todo bien
#     precio SÍ + tasa NO  →  HALLAZGO, y el ticker entra a la lista de prioridad
def bono_sin_tasa(u: dict) -> list[Hallazgo]:
    """Bonos con precio y sin TEA. El ticker entra a la lista que se le pide a
    1816 cada 15 minutos (ver `agente/tasa_1816.py`)."""
    from datetime import date

    from api.services.acreencias import tiene_flujo_def

    docs, snap = fuentes.master(), fuentes.snapshot()
    if docs is None or snap is None:
        raise SinDatos("no pude leer el master o el snapshot")

    # Ajustes que el motor NO calcula por diseño: no tener tasa ahí no es un
    # problema del dato, es que nadie escribió ese cálculo.
    sin_calculo = set(u.get("ajustes_sin_calculo")
                      or ("tamar", "badlar", "tpm", "caucion"))
    hoy, out = date.today(), []
    for d in docs:
        tk, simbolo = _tk(d.get("ticker_corto")), (d.get("ticker") or "").strip()
        if not tk or not simbolo:
            continue
        if not tiene_flujo_def(d, hoy):
            continue                      # eso lo dice `bono_sin_flujo`
        if (d.get("ajuste") or "").strip().lower() in sin_calculo:
            continue
        m = snap.get(simbolo) or {}
        try:
            precio = float(m.get("last_price") or 0)
        except (TypeError, ValueError):
            precio = 0.0
        if not precio:
            continue                      # sin precio es ILIQUIDEZ, no un bug
        if m.get("tea") is not None:
            continue
        out.append(Hallazgo(
            sujeto=tk, regla="con_precio_sin_tea", severidad="alta",
            problema=(f"opera a {precio:,.2f} y el motor no le calcula la TEA: "
                      f"la fila sale en «--» con el papel cotizando · "
                      f"{reloj.hhmm()}"),
            que_hacer=("Entra a la lista de prioridad: se le piden TEA, "
                       "duration y precio a 1816 cada 15 min y la tasa se "
                       "muestra marcada como de 1816 hasta que el motor la "
                       "calcule."),
            evidencia={"simbolo": simbolo, "last_price": precio,
                       "curva": d.get("curva"), "ajuste": d.get("ajuste"),
                       "moneda_flujo": d.get("moneda_flujo"),
                       "n_flujos": len(d.get("flujos") or [])}))
    return out


# ═══ bono_sin_precio ═══════════════════════════════════════════════════════
def bono_sin_precio(u: dict) -> list[Hallazgo]:
    """Bonos del master a los que el motor NO les está dando precio, en rueda.

    Cuatro estados, y la diferencia importa porque el arreglo es OTRO en cada uno.
    """
    docs, snap = fuentes.master(), fuentes.snapshot()
    if docs is None or snap is None:
        raise SinDatos("no pude leer el master o el snapshot")

    ahora = reloj.ahora_utc()
    abierto = reloj.en_rueda(ahora)
    viejo = ahora - timedelta(minutes=int(u.get("precio_viejo_min", 20)))
    out = []
    for b in docs:
        tk, simbolo = _tk(b.get("ticker_corto")), (b.get("ticker") or "").strip()
        if not tk:
            continue
        ev = {"simbolo": simbolo or None, "curva": b.get("curva")}

        if not simbolo:
            # ⚠️ **No es «no aplica»: es el PEOR caso.** Un bono sin símbolo no
            # puede tener precio nunca y el motor ni lo intenta. El agente viejo
            # lo salteaba en silencio, así que el bono peor cargado del master
            # era justo el único invisible para el monitor.
            out.append(Hallazgo(
                sujeto=tk, regla="sin_simbolo", severidad="alta",
                problema=f"«{tk}» no tiene símbolo de mercado cargado: nadie le "
                         f"pidió nunca un precio · {reloj.hhmm(ahora)}",
                que_hacer="Cargarle el símbolo desde `mercado.especies` (la "
                          "pata del ticker) en el master.",
                evidencia=ev))
            continue

        d = snap.get(simbolo)
        if d is None:
            out.append(Hallazgo(
                sujeto=tk, regla="no_suscripto", severidad="alta",
                problema=f"el motor NO está pidiendo «{simbolo}»: está en el "
                         f"master y nadie lo suscribió · {reloj.hhmm(ahora)}",
                que_hacer="Pedirle el precio a Primary — el motor levanta la "
                          "suscripción en 5 s, sin reiniciar.",
                evidencia=ev))
            continue

        try:
            px = float(d.get("last_price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if not px:
            # `baja` y es del MERCADO: acá SÍ estamos escuchando, así que la
            # ausencia de punta es un dato sobre el papel (iliquidez) y no sobre
            # el sistema. Es la otra cara de la regla de «no se puede concluir
            # que no existe desde una tabla que solo tiene lo que pedimos».
            out.append(Hallazgo(
                sujeto=tk, regla="sin_punta", severidad="baja",
                problema=f"sin punta hoy · lo estamos pidiendo, así que es "
                         f"iliquidez · {reloj.hhmm(ahora)}",
                que_hacer="Nada que apretar: el papel no operó. Si la ausencia "
                          "dura toda la rueda, es un dato sobre el papel.",
                evidencia={**ev, "estado": "sin_punta"}))
            continue

        if not abierto:
            continue      # fuera de rueda «viejo» es lo normal, no un problema
        upd = d.get("updated_at")
        if upd is not None and upd.tzinfo is None:
            upd = upd.replace(tzinfo=UTC)
        if upd and upd < viejo:
            mins = int((ahora - upd).total_seconds() / 60)
            out.append(Hallazgo(
                sujeto=tk, regla="precio_viejo", severidad="media",
                problema=f"«{simbolo}» no se actualiza hace {mins} min (último "
                         f"{px:,.2f}) · {reloj.hhmm(ahora)}",
                que_hacer="Verificar si el papel dejó de operar o si se cayó el "
                          "feed del motor.",
                evidencia={**ev, "minutos": mins, "precio": px}))
    return out


# ═══ precio_moneda ═════════════════════════════════════════════════════════
def precio_moneda(u: dict) -> list[Hallazgo]:
    """Bonos de curva USD cuyo precio llega en pesos.

    ⚠️ La primera versión de esto marcaba `alta` a **46 de 230** y estaban
    SANOS: el motor ya divide por el MEP cuando el símbolo no termina en D/C, así
    que la TEA y la paridad salían bien. *Un detector que llama «alta» a 46 casos
    sanos no es estricto: enseña a ignorar la lista.*

    Lo que SÍ pasa es que la grilla muestra el precio CRUDO, y ahí conviven
    102.700 (pesos) y 74,19 (dólares) sin que nada lo diga. Quedan dos reglas con
    severidades distintas porque son problemas distintos.
    """
    docs, snap, m = fuentes.master(), fuentes.snapshot(), fuentes.mep()
    if docs is None or snap is None:
        raise SinDatos("no pude leer el master o el snapshot")
    if not m or m <= 0:
        # Sin MEP no se puede probar nada y **no se inventa**. Es `SinDatos` y
        # no `[]`: devolver vacío cerraría los que siguen estando mal.
        raise SinDatos("sin MEP no se puede decidir si el precio está en pesos")

    p_min = float(u.get("paridad_min", 40))
    p_max = float(u.get("paridad_max", 160))
    esp = fuentes.especies() or {}
    patas = esp.get("patas") or {}
    out = []
    for b in docs:
        if (b.get("moneda_eje") or "").upper() != "USD":
            continue
        # ⚠️ Un DÓLAR LINKED cotiza en pesos POR DEFINICIÓN: está denominado en
        # USD pero paga en pesos, no tiene pata en dólares y no la va a tener.
        # Medido: 8 de 44 casos eran esto — el 18% de la lista era ruido
        # estructural.
        if "dolar_linked" in {(b.get("ajuste") or "").strip().lower(),
                              (b.get("ajuste_alt") or "").strip().lower(),
                              (b.get("curva") or "").strip().lower()}:
            continue
        tk, simbolo = _tk(b.get("ticker_corto")), (b.get("ticker") or "").strip()
        try:
            px = float((snap.get(simbolo) or {}).get("last_price") or 0)
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue                      # eso lo dice `bono_sin_precio`
        try:
            residual = float(b.get("valor_nominal") or 100) or 100
        except (TypeError, ValueError):
            residual = 100.0

        par_cruda = px / residual * 100
        if p_min <= par_cruda <= p_max:
            continue                      # el precio ya viene en dólares
        par_mep = px / m / residual * 100
        if not (p_min <= par_mep <= p_max):
            continue                      # dividir no lo arregla → no es esto

        partes = simbolo.split(" - ")
        sym = partes[2] if len(partes) >= 3 else simbolo
        ev = {"simbolo": simbolo, "precio": px, "mep": m,
              "paridad_cruda": round(par_cruda, 2),
              "paridad_con_mep": round(par_mep, 2), "curva": b.get("curva")}

        if sym[-1:].upper() in ("D", "C"):
            # El símbolo YA es en dólares: el motor lo toma tal cual, no hay
            # conversión que explique la paridad. Ahí algo está realmente mal.
            out.append(Hallazgo(
                sujeto=tk, regla="precio_fuera_de_escala", severidad="alta",
                problema=f"precio en pesos con símbolo en dólares · paridad "
                         f"{par_cruda:,.0f}% (÷MEP daría {par_mep:.1f}%) · "
                         f"{reloj.hhmm()}",
                que_hacer="Revisar el símbolo del master: el motor lo trata "
                          "como dólares y le está llegando pesos.",
                evidencia=ev))
            continue

        # Cotiza por su pata en pesos. ¿Existe la pata en dólares?
        dolar = [p["simbolo"] for p in patas.get(tk, [])
                 if str(p.get("especie") or "").upper().endswith("D")]
        if dolar:
            out.append(Hallazgo(
                sujeto=tk, regla="pata_equivocada", severidad="media",
                problema=f"el master suscribe la pata en PESOS teniendo la pata "
                         f"en dólares «{dolar[0]}» · {reloj.hhmm()}",
                que_hacer=f"Apuntar el master a «{dolar[0]}» y pedirla: la "
                          f"columna pasa a mostrar dólares.",
                evidencia={**ev, "pata_dolar": dolar[0]}))
        else:
            out.append(Hallazgo(
                sujeto=tk, regla="cotiza_en_pesos", severidad="baja",
                problema=f"cotiza por su pata en pesos y la grilla muestra el "
                         f"crudo · el motor convierte bien · {reloj.hhmm()}",
                que_hacer="Es contexto, no un error: la TEA y la paridad están "
                          "bien calculadas. Si aparece una pata D, se pide.",
                evidencia=ev))
    return out


# ═══ hueco_de_curva ════════════════════════════════════════════════════════
def hueco_de_curva(u: dict) -> list[Hallazgo]:
    """Ajustes que existen en el master y que la app no sabe mostrar.

    Es de otra naturaleza que los demás: no es un dato mal cargado, es una
    **capacidad que le falta al sistema**. Por eso se reporta UNA vez por AJUSTE
    y no una por bono — el problema es el ajuste; los bonos son la evidencia de
    cuánto duele.
    """
    from core import curvas_ejes

    docs = fuentes.master()
    if docs is None:
        raise SinDatos("no pude leer mercado.curvas")

    por_ajuste: dict[str, list[str]] = {}
    monedas: dict[str, list[str]] = {}
    for d in docs:
        tk = _tk(d.get("ticker_corto"))
        if not tk:
            continue
        ejes = curvas_ejes.ejes_de_doc(d)
        if ejes is None:
            continue
        for aj in (ejes.ajuste, ejes.ajuste_alt):
            if curvas_ejes.ajuste_sin_curva(aj) and not curvas_ejes.pills(ejes):
                por_ajuste.setdefault(aj, []).append(tk)
                monedas.setdefault(aj, []).append(ejes.moneda)

    out = []
    for aj, tickers in sorted(por_ajuste.items()):
        tickers = sorted(set(tickers))
        ms = monedas.get(aj) or []
        # El LADO no se pregunta: lo dice la moneda de sus propios bonos.
        lado = "USD" if ms and ms.count("USD") > len(ms) / 2 else "ARS"
        out.append(Hallazgo(
            sujeto=aj.upper(), regla="ajuste_sin_curva", severidad="alta",
            nombre=f"ajuste {aj.upper()}",
            problema=f"{len(tickers)} bono(s) con ajuste «{aj}» no caen en "
                     f"NINGUNA curva: están cargados y no aparecen en la tabla, "
                     f"ni en los forwards, ni en el fair value — sin dar error.",
            que_hacer=f"Crear la curva {lado} para «{aj}»: hay que decidir de "
                      f"dónde sale su tasa. Es desarrollo, no dato.",
            evidencia={"ajuste": aj, "n": len(tickers), "tickers": tickers,
                       "lado": lado}))
    return out


# ═══ tasas_al_cierre ═══════════════════════════════════════════════════════
def tasas_al_cierre(u: dict) -> list[Hallazgo]:
    """**EL BARRIDO DEL CIERRE.** Doc: `AGENT_2.0.md` §5.

    User (2026-08-24): *«todo esto es lo que entra en horario de mercado, no
    debe seguir pidiéndose después de las 17. A las 17:30 debería haber una
    skill que busque todos los tickers de curva que no tienen tasa —si es que
    quedó alguno— y los rellene con 1816. Y listo.»*

    Dos cosas pasan acá, y en este orden:

      1. **RELLENA.** Le pide a 1816 la tasa de todo lo que quedó sin TEA. Es una
         sola llamada para toda la lista, con el día ya cerrado: los precios de
         1816 son los definitivos y no hay nada que esperar.
      2. **CANTA LO QUE NI ASÍ SE PUDO.** Un bono que operó, que el motor no
         calculó y que 1816 tampoco publica es un agujero REAL — y es el único
         que vale la pena mirar, porque los otros ya quedaron tapados.

    ⚠️ **Por qué acá y no en el detector de rueda.** `bono_sin_tasa` corre cada
    15 minutos MIENTRAS el mercado opera, porque un bono puede empezar a cotizar
    a las 14 y hay que enterarse en el momento. Este corre UNA vez, con el día
    cerrado, y su respuesta es final. Mezclarlos obligaría a elegir entre
    enterarse tarde o preguntarle a 1816 toda la noche.
    """
    from agente import tasa_1816

    docs = fuentes.master()
    if docs is None:
        raise SinDatos("no pude leer mercado.curvas")

    r = tasa_1816.refrescar()
    if not r.get("ok"):
        raise SinDatos(f"1816 no contestó el barrido del cierre: {r.get('error')}")

    tapadas = tasa_1816.tasas()
    out = []
    for tk in tasa_1816.pendientes():
        ta = tapadas.get(tk) or {}
        if ta.get("tea") is not None:
            continue                      # tapado: 1816 tenía la tasa
        out.append(Hallazgo(
            sujeto=tk, regla="sin_tasa_ni_en_1816", severidad="alta",
            problema=(f"«{tk}» operó hoy, el motor no le calculó la TEA y 1816 "
                      f"tampoco la publica: la fila queda en «--» con el papel "
                      f"cotizando · {reloj.hhmm()}"),
            que_hacer=("Es un agujero real, no un atraso: hay que ver por qué el "
                       "motor no lo calcula (ejes, moneda del flujo, cronograma) "
                       "— 1816 ya se descartó como salida."),
            evidencia={"pedido_a_1816": True,
                       "tickers_en_la_lista": r.get("tickers"),
                       "tapados_por_1816": r.get("escritos"),
                       "fecha_1816": r.get("fecha_1816")}))
    return out
