"""api/services/av_agent_explicar.py — LO QUE EL AGENTE SABE EXPLICAR.

Doc madre: **`docs/AV_AGENT.md`** §0.m.

Pedido del user (2026-08-19), mirando Manager → VALIDACIONES:

    *«Cada una de las validaciones que hay acá tiene que ser funcionalidades que
    el agent domine a la perfección, porque son cosas que sabría hacer un trader
    o alguien de finanzas… sacándole la palabra DEBUG. Es decir: che, saber por
    qué esta TEA rinde tanto, por qué la TNA de futuros es tanto, saber las
    breakevens, saber tasa fija… quiero ir migrando funciones útiles al agent
    para que el día de mañana le hable y se lo pida.»*

EL HALLAZGO, QUE ES LO QUE VALE
================================

Esas ocho pantallas **nunca fueron herramientas de debug**: son las preguntas
que se hace alguien de finanzas todos los días, escritas con nombre de
programador. «Debug TEA Curvas» es *«¿por qué este bono rinde esto?»*. «Debug
TNA Futuros DLR» es *«¿por qué el futuro de dólar paga esa tasa?»*. Lo único que
las hacía parecer internas era el nombre y el lugar.

POR QUÉ ESTO ES EL PASO PREVIO A «QUE SE DÉ CUENTA SOLO»
========================================================

El user también dijo hacia dónde va: *«que en algún momento ya ni sea necesario,
que sea un sistema entero que se dé cuenta al toque que algo raro pasa… que ni
haga falta decirle que hay algo mal»*.

**El que sabe EXPLICAR un número sabe JUZGARLO.** Un explicador que reproduce el
cálculo paso a paso tiene, al final, el número recalculado al lado del
persistido; si no coinciden, eso no es un detalle de la explicación: **es un
hallazgo**. Por eso cada explicador devuelve, además de los pasos, un
`discrepancia` — y ese campo es el puente entre «me preguntaste» y «te aviso».

DÓNDE VA EL MODELO (y dónde NO)
================================

**El explicador determinista produce los NÚMEROS; el modelo produce la FRASE.**
Nunca al revés. El modelo no calcula una TEA, no infiere un flujo y no opina
sobre si un precio está bien: recibe los números que ya salieron del cálculo y
los convierte en la línea que un humano quería leer. Si no hay modelo
disponible, los pasos se muestran igual — la explicación no depende de él.

CÓMO SE AGREGA UNO NUEVO
=========================

Una clase con `pregunta` + `explicar()` y una línea en `EXPLICADORES`. **No se
reimplementa ningún cálculo**: se envuelve la función que YA existe y que ya usa
la pantalla (`debug_curva.debug_calculo_tea`, `debug_derivados.debug_soberano`,
`quant.pivot_points.debug_4_timeframes`). Es la misma regla que gobierna las
ACCIONES: una sola puerta, un solo criterio. Dos implementaciones del mismo
cálculo terminan dando dos respuestas distintas a la misma pregunta.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Los estados de un paso, iguales a los del resto del agente para que el modal
# los dibuje igual. Un explicador casi todo lo dice en INFO: está contando cómo
# se llegó a un número, no juzgando. REVISAR aparece solo cuando el propio
# cálculo se contradice — y ahí deja de ser una explicación y pasa a ser un aviso.
OK, INFO, REVISAR, NO_SE = "ok", "info", "revisar", "no_se_puede_saber"


def _paso(clave: str, titulo: str, estado: str, detalle: str,
          *, tabla: str = "") -> dict:
    return {"clave": clave, "titulo": titulo, "estado": estado,
            "detalle": detalle, "tabla": tabla, "accion": "", "aviso": "",
            "pide": None, "frena": False, "frena_auto": False}


def _n(v: Any, dec: int = 2, pct: bool = False) -> str:
    """Un número para leer. `None` es «—» y NO 0: no saber cuánto vale algo y
    que valga cero son cosas distintas, y confundirlas en una explicación es
    peor que en una tabla — acá el que lee se lleva una conclusión."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    # Formato argentino: punto para miles, coma para decimales. El swap por "X"
    # es porque Python emite al revés y reemplazar en dos pasos se pisa a sí mismo.
    crudo = f"{f * 100:,.{dec}f}" if pct else f"{f:,.{dec}f}"
    salida = crudo.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{salida}%" if pct else salida


class Explicador(Protocol):
    id: str
    pregunta: str          # cómo la haría una persona, no cómo se llama la función
    necesita: str          # "" | "ticker"
    de_donde: str          # de dónde salen los números

    def sugerencias(self) -> list[str]: ...
    def explicar(self, sujeto: str) -> dict: ...


# ── 1. ¿POR QUÉ ESTE BONO RINDE ESTO? ───────────────────────────────────────

class PorQueRinde:
    """Era «Debug TEA Curvas (renta fija — tasa_fija / cer / soberanos /
    dolar_linked)». Es la pregunta más frecuente de una mesa de renta fija."""

    id = "rinde"
    pregunta = "¿Por qué este bono rinde lo que rinde?"
    necesita = "ticker"
    de_donde = "mercado.curvas + mercado.market_snapshot"

    def sugerencias(self) -> list[str]:
        from core import curvas_sql
        return sorted({d["ticker_corto"] for d in curvas_sql.cargar_todos()
                       if d.get("ticker_corto")})

    def explicar(self, sujeto: str) -> dict:
        from api.services.debug_curva import debug_calculo_tea
        d = debug_calculo_tea(sujeto)
        if not d.get("ok"):
            return {"ok": False, "error": d.get("message") or "no lo encontré"}

        inst = d.get("instrumento") or {}
        trade = d.get("trade") or {}
        calc = d.get("calculado") or {}
        flujos = d.get("flujos_futuros") or []
        pasos = [
            _paso("que_es", "Qué es este papel", INFO,
                  f"curva {inst.get('curva') or '—'} · vence "
                  f"{inst.get('fecha_vencimiento') or '—'} · valor nominal "
                  f"{_n(inst.get('valor_nominal'))}",
                  tabla="mercado.curvas"),
            _paso("precio", "A qué precio se está pagando", INFO,
                  f"último precio {_n(trade.get('price'))}"
                  + (f" (del {str(trade.get('timestamp'))[:16]})"
                     if trade.get("timestamp") else ""),
                  tabla="mercado.market_snapshot"),
        ]
        # El AJUSTE es lo que casi siempre explica una tasa rara, y es el paso
        # que ninguna tabla muestra: un CER viejo mueve la TEA sin que se note.
        cer = d.get("cer_info") or {}
        if cer.get("cer_actual") or cer.get("cer_emision"):
            pasos.append(_paso(
                "ajuste", "El ajuste que lleva (CER)", INFO,
                f"CER de emisión {_n(cer.get('cer_emision'), 4)} → CER actual "
                f"{_n(cer.get('cer_actual'), 4)} · coeficiente "
                f"{_n(cer.get('coeficiente'), 4)}"
                + (f" (fecha {cer.get('fecha_cer')})" if cer.get("fecha_cer") else ""),
                tabla="macro.series_macro"))
        tc = d.get("tc_info") or {}
        if tc.get("tc"):
            pasos.append(_paso(
                "tc", "El tipo de cambio que usa", INFO,
                f"{_n(tc.get('tc'))} ({tc.get('fuente') or 'sin fuente'})",
                tabla="valuaciones.dolar"))

        if flujos:
            prox = flujos[0]
            pasos.append(_paso(
                "flujos", f"Lo que va a pagar · {len(flujos)} cobro/s por delante",
                INFO,
                f"el próximo es el {str(prox.get('fecha'))[:10]} por "
                f"{_n(prox.get('monto'))}; el último, el "
                f"{str(flujos[-1].get('fecha'))[:10]}",
                tabla="mercado.curvas"))
        else:
            pasos.append(_paso(
                "flujos", "Lo que va a pagar", REVISAR,
                "**no tiene ningún flujo futuro cargado** — sin cronograma no "
                "hay tasa que calcular, y por eso la celda queda vacía.",
                tabla="mercado.curvas"))

        tea = calc.get("TEA")
        pasos.append(_paso(
            "tasa", "La tasa que sale de todo eso", OK if tea is not None else NO_SE,
            f"TEA {_n(tea, 2, pct=True)} · duration {_n(calc.get('duration'))} · "
            f"paridad {_n(calc.get('paridad'), 1)}%"
            if tea is not None else
            (d.get("error_calc") or "el cálculo no llegó a un resultado")))

        # **EL PUENTE A DETECTAR.** Si lo recalculado no coincide con lo que
        # muestra la app, eso no es color de la explicación: es un problema.
        disc = _discrepancia_tea(d)
        if disc:
            pasos.append(_paso("ojo", "⚠ Ojo con esto", REVISAR, disc))
        return {"ok": True, "pasos": pasos, "discrepancia": disc,
                "numeros": {"tea": tea, "precio": trade.get("price"),
                            "paridad": calc.get("paridad")}}


def _discrepancia_tea(d: dict) -> str:
    """Lo recalculado contra lo persistido. Es lo que convierte a un explicador
    en un detector: si el motor guardó otra cosa, alguien lo tiene que saber."""
    diff = d.get("diff") or {}
    tea_d = diff.get("TEA")
    try:
        if tea_d is not None and abs(float(tea_d)) > 0.005:   # 50 bps
            return (f"recalculando da {_n((d.get('calculado') or {}).get('TEA'), 2, pct=True)} "
                    f"pero la app muestra "
                    f"{_n((d.get('persistido') or {}).get('TEA'), 2, pct=True)} — "
                    f"una diferencia de {_n(tea_d, 2, pct=True)}. O el motor "
                    f"guardó con insumos viejos, o el cronograma cambió después.")
    except (TypeError, ValueError):
        return ""
    return ""


# ── 2. ¿POR QUÉ EL FUTURO DE DÓLAR PAGA ESA TASA? ───────────────────────────

class PorQueEsaTNA:
    """Era «Debug TNA Futuros DLR (TNA lineal vs TEA compuesta)». La pregunta
    real es más simple, y la trampa también: **son dos convenciones distintas**
    y la mesa mira una sola."""

    id = "tna_futuros"
    pregunta = "¿Por qué el futuro de dólar paga esa tasa?"
    necesita = ""
    de_donde = "mercado.futuros_dlr_snapshot"

    def sugerencias(self) -> list[str]:
        return []

    def explicar(self, _sujeto: str = "") -> dict:
        from api.services import debug_derivados
        d = debug_derivados.debug_tna_futuros()
        filas = d.get("filas") or []
        spot = d.get("spot") or {}
        if not filas:
            return {"ok": False, "error": "no hay outrights en el snapshot — "
                                          "puede ser que el motor no esté corriendo"}
        pasos = [
            _paso("spot", "Contra qué dólar se compara", INFO,
                  f"spot {_n(spot.get('valor'))} (fuente: "
                  f"{spot.get('fuente') or 'sin declarar'}). **Todo el cálculo "
                  f"cuelga de este número**: un spot viejo desplaza todas las tasas.",
                  tabla="valuaciones.dolar_oficial_live"),
            _paso("cuenta", "La cuenta", INFO,
                  "tasa = (precio del futuro ÷ spot − 1) anualizada. Se puede "
                  "anualizar LINEAL (× 365/días, que es la convención de la "
                  "terminal de Rofex) o COMPUESTA (^365/días). **Dan distinto**, "
                  "y cuanto más corto el plazo, más se separan."),
        ]
        for f in filas[:12]:
            pasos.append(_paso(
                f"o_{f.get('ticker')}", f"{f.get('ticker')} · "
                f"{f.get('dias')} días", INFO,
                f"precio {_n(f.get('precio'))} → lineal "
                f"{_n(f.get('tna_lineal'), 2)}% · compuesta "
                f"{_n(f.get('tea_compuesta'), 2)}%"))
        if d.get("nota"):
            pasos.append(_paso("nota", "Lo que conviene tener presente", INFO,
                               str(d["nota"])))
        return {"ok": True, "pasos": pasos, "discrepancia": "",
                "numeros": {"spot": spot.get("valor"), "outrights": len(filas)}}


# ── 3. ¿DE DÓNDE SALE EL YTM DE UN SOBERANO? ────────────────────────────────

class ComoSeArmaElYTM:
    id = "ytm_soberano"
    pregunta = "¿Cómo se arma el rendimiento de un soberano?"
    necesita = "ticker"
    de_donde = "mercado.curvas + el MEP del momento"

    def sugerencias(self) -> list[str]:
        from core import curvas_sql
        return sorted({d["ticker_corto"] for d in curvas_sql.cargar_todos()
                       if d.get("ticker_corto")
                       and d.get("tipo") in ("globales", "bonares")})

    def explicar(self, sujeto: str) -> dict:
        from api.services import debug_derivados
        try:
            d = debug_derivados.debug_soberano(sujeto)
        except LookupError as e:
            return {"ok": False, "error": str(e)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        inst = d.get("instrumento") or {}
        precio = d.get("precio") or {}
        res = d.get("resultado") or d.get("calculado") or {}
        flujos = d.get("flujos") or d.get("flujos_futuros") or []
        pasos = [
            _paso("que_es", "Qué es", INFO,
                  f"{inst.get('tipo') or '—'} · vence "
                  f"{str(inst.get('fecha_vencimiento') or '')[:10] or '—'} · "
                  f"{inst.get('flujos_total') or 0} flujos en el cronograma",
                  tabla="mercado.curvas"),
            # El paso que la gente se saltea y es el que más confunde: el símbolo
            # sin D/C cotiza en PESOS y hay que pasarlo a dólares por MEP.
            _paso("moneda", "En qué moneda está el precio", INFO,
                  f"precio usado {_n(precio.get('usd') or precio.get('valor'))} USD"
                  + (f" (venía en pesos y se pasó por MEP {_n(precio.get('mep'))})"
                     if precio.get("mep") else " (el símbolo ya cotiza en dólares)"),
                  tabla="valuaciones.dolar"),
            _paso("cuenta", "La cuenta", INFO,
                  f"se descuentan los {len(flujos)} cobros futuros contra ese "
                  f"precio y se busca la tasa que iguala las dos puntas (XIRR)."),
            _paso("tasa", "El resultado",
                  OK if res.get("TEA") is not None else NO_SE,
                  f"TEA {_n(res.get('TEA'), 2, pct=True)} · duration "
                  f"{_n(res.get('duration'))} · paridad {_n(res.get('paridad'), 1)}%"
                  if res.get("TEA") is not None else "el XIRR no convergió"),
        ]
        return {"ok": True, "pasos": pasos, "discrepancia": "",
                "numeros": {"tea": res.get("TEA")}}


# ── 4. ¿QUÉ INFLACIÓN ESTÁ DESCONTANDO EL MERCADO? ──────────────────────────

class QueInflacionDescuenta:
    """Era «Debug Breakevens — comparar Buscar Objetivo vs Fisher». La pregunta
    de verdad no es cuál método usamos: es **qué inflación tiene que salir para
    que dé lo mismo comprar tasa fija o CER**."""

    id = "breakeven"
    pregunta = "¿Qué inflación está descontando el mercado?"
    necesita = ""
    de_donde = "pares Lecap/Boncap ↔ CER de mismo vencimiento"

    def sugerencias(self) -> list[str]:
        return []

    def explicar(self, _sujeto: str = "") -> dict:
        from api.services import debug_derivados
        d = debug_derivados.breakevens_debug()
        pares = d.get("pares") or []
        if not pares:
            return {"ok": False, "error": "no hay pares con datos suficientes hoy"}
        pasos = [
            _paso("idea", "La idea", INFO,
                  "se toma un papel a tasa fija y uno CER que vencen el MISMO "
                  "día, y se busca qué inflación mensual iguala los dos "
                  "rendimientos. Esa es la que el mercado está descontando: por "
                  "arriba conviene el CER, por abajo la tasa fija."),
            _paso("cer", "Con qué CER se calcula", INFO,
                  f"CER {_n(d.get('cer_actual'), 4)} al "
                  f"{d.get('fecha_cer_max') or '—'}. **El CER se publica con "
                  f"10 días hábiles de rezago**, así que este número no es el de "
                  f"hoy y no puede serlo.",
                  tabla="macro.series_macro"),
        ]
        for p in pares[:10]:
            pasos.append(_paso(
                f"par_{p.get('lecap') or p.get('tasa_fija')}",
                f"{p.get('lecap') or p.get('tasa_fija')} vs {p.get('cer')}",
                INFO,
                f"vencen {str(p.get('vencimiento') or '')[:10]} · breakeven "
                f"{_n(p.get('be_objetivo') or p.get('breakeven'), 2)}% mensual"
                + (f" (por Fisher daría {_n(p.get('be_fisher'), 2)}%)"
                   if p.get("be_fisher") is not None else "")))
        pasos.append(_paso(
            "metodo", "Por qué hay dos números", INFO,
            "**Buscar Objetivo** resuelve la igualdad exacta con los flujos "
            "reales; **Fisher** es la fórmula de manual y aproxima. Se muestran "
            "los dos porque cuando se separan mucho, la diferencia está avisando "
            "que a ese par le falta un dato."))
        return {"ok": True, "pasos": pasos, "discrepancia": "",
                "numeros": {"pares": len(pares)}}


# ── 5. ¿DE DÓNDE SALEN LOS NIVELES DEL GRÁFICO? ─────────────────────────────

class DeDondeSalenLosNiveles:
    id = "pivots"
    pregunta = "¿De dónde salen los niveles de soporte y resistencia?"
    necesita = "ticker"
    de_donde = "mercado.precios_acciones"

    def sugerencias(self) -> list[str]:
        return []

    def explicar(self, sujeto: str) -> dict:
        from quant.pivot_points import debug_4_timeframes
        d = debug_4_timeframes((sujeto or "").strip().upper())
        tfs = d.get("timeframes") or d.get("resultados") or []
        if not tfs:
            return {"ok": False, "error": f"no tengo velas de {sujeto}"}
        pasos = [_paso(
            "idea", "La idea", INFO,
            "los niveles NO se dibujan a ojo: salen de la vela ANTERIOR de cada "
            "marco temporal. Pivot = (máximo + mínimo + cierre) ÷ 3, y de ahí "
            "los soportes y resistencias por la fórmula Floor Trader.")]
        for t in (tfs if isinstance(tfs, list) else []):
            n = t.get("timeframe") or t.get("nombre") or "?"
            pasos.append(_paso(
                f"tf_{n}", f"Marco {n}", INFO,
                f"vela usada {str(t.get('vela_usada') or t.get('fecha') or '')[:10]} · "
                f"máx {_n(t.get('high'))} · mín {_n(t.get('low'))} · cierre "
                f"{_n(t.get('close'))} → pivot {_n(t.get('pivot'))}"))
        return {"ok": True, "pasos": pasos, "discrepancia": "",
                "numeros": {"marcos": len(tfs)}}


# ── 6. ¿CUÁNTO PESA LA BASE Y QUÉ CRECIÓ? ───────────────────────────────────

class CuantoPesaLaBase:
    id = "base"
    pregunta = "¿Cuánto pesa la base y qué creció desde ayer?"
    necesita = ""
    de_donde = "la foto diaria de manager.db_tamano"

    def sugerencias(self) -> list[str]:
        return []

    def explicar(self, _sujeto: str = "") -> dict:
        from api.services import av_agent_db as db
        c = db.comparar()
        if not c.get("ok"):
            return {"ok": False, "error": c.get("motivo") or "sin foto"}
        if c.get("primera"):
            return {"ok": True, "pasos": [_paso(
                "primera", "Es la primera foto", INFO,
                f"la base pesa {db.mb(c['bytes_total'])} en {c['n_tablas']} "
                f"tablas. **Mañana** te puedo decir qué cambió: el tamaño de hoy "
                f"solo no dice nada, la información es el delta.")],
                "discrepancia": "", "numeros": {"bytes": c["bytes_total"]}}

        d = c["delta_total"]
        pasos = [_paso(
            "total", "Cuánto pesa", INFO,
            f"{db.mb(c['bytes_total'])} en {c['n_tablas']} tablas · "
            f"{'+' if d >= 0 else ''}{db.mb(d)} desde el {c['fecha_previa']}",
            tabla="manager.db_tamano_dia")]

        if c["nuevas"]:
            pasos.append(_paso(
                "nuevas", f"Tablas NUEVAS · {len(c['nuevas'])}", REVISAR,
                "\n".join(f"  · {t['tabla']} — {db.mb(t['bytes'])}"
                           for t in c["nuevas"][:20]),
                tabla="manager.db_tamano"))
        if c["crecieron"]:
            pasos.append(_paso(
                "crecieron", f"Las que crecieron · {len(c['crecieron'])}", REVISAR,
                "\n".join(f"  · {t['tabla']} — +{db.mb(t['delta'])} "
                           f"(+{t['pct']}%)" for t in c["crecieron"][:20]),
                tabla="manager.db_tamano"))
        if c["desaparecidas"]:
            pasos.append(_paso(
                "fueron", f"Ya no están · {len(c['desaparecidas'])}", REVISAR,
                "\n".join(f"  · {t['tabla']} — pesaba {db.mb(t['bytes'])}"
                           for t in c["desaparecidas"][:20])))
        if not (c["nuevas"] or c["crecieron"] or c["desaparecidas"]):
            pasos.append(_paso("sin_cambios", "Qué cambió", OK,
                               "nada que amerite mirar desde ayer."))
        pasos.append(_paso(
            "corte", "A partir de cuánto aviso", INFO,
            f"un crecimiento entra si supera **los dos** filtros: +25% sobre su "
            f"propio tamaño Y {db.mb(c['corte_bytes'])} — que se calcula sobre "
            f"el tamaño real de la base, no es un número fijo que envejece."))
        return {"ok": True, "pasos": pasos, "discrepancia": "",
                "numeros": {"bytes": c["bytes_total"], "delta": d}}


# ── 7. ¿CÓMO VIENE LA APP DE VELOCIDAD? ─────────────────────────────────────

class ComoVieneDeVelocidad:
    """La pregunta que la pantalla de LATENCIA no contestaba: **no es "cuál es
    el más lento" —esa lista no cambia nunca— sino "hay algo peor que ayer"**."""

    id = "velocidad"
    pregunta = "¿Hay algún endpoint más lento que lo normal?"
    necesita = ""
    de_donde = "manager.latencia_endpoints"

    def sugerencias(self) -> list[str]:
        return []

    def explicar(self, _sujeto: str = "") -> dict:
        from api.services import av_agent_latencia as lat
        d = lat.como_viene()
        deg = d.get("degradados") or []
        eps = d.get("endpoints") or []
        if not eps:
            return {"ok": False, "error": "no hay tráfico registrado en la ventana"}

        pasos = []
        if deg:
            pasos.append(_paso(
                "degradados", f"Peor que su normal · {len(deg)}", REVISAR,
                "\n".join(
                    f"  · {c['endpoint']} — {c['avg_ms']} ms contra "
                    f"{c['base_ms']} ms habituales ({c['veces']}×)"
                    + (f" · {c['errores']} errores 5xx" if c["errores"] else "")
                    for c in deg[:15]),
                tabla="manager.latencia_endpoints"))
        else:
            pasos.append(_paso(
                "degradados", "Peor que su normal", OK,
                f"ninguno. Cada endpoint se compara contra la MEDIANA de sus "
                f"propias {lat.BASE_H} h previas, no contra los otros."))

        pasos.append(_paso(
            "consumen", "Los que más tiempo consumen", INFO,
            "\n".join(f"  · {e['endpoint']} — {e['avg_ms']} ms × {e['n']} req"
                       for e in eps[:10])
            + "\n\n**Esto NO es una lista de problemas.** Un endpoint puede ser "
              "lento porque hace algo caro (una llamada al modelo, un proveedor "
              "externo) y estar perfectamente bien. Es el ranking, y el ranking "
              "no cambia: por eso solo, no sirve.",
            tabla="manager.latencia_endpoints"))
        return {"ok": True, "pasos": pasos, "discrepancia": "",
                "numeros": {"degradados": len(deg),
                            "requests": d.get("total_requests")}}


# ── 8. ¿ESTÁ TODO FUNCIONANDO BIEN AHORA? ───────────────────────────────────

class EstaTodoBien:
    """**La pregunta que nadie contestaba de una.** El user: *«no quiero que me
    muestre todos los endpoints; yo quiero saber que en horario de mercado la
    aplicación funciona bien y no hay nada colapsando»*.

    Junta las TRES patas —los motores, las tablas y la velocidad— en una sola
    respuesta. Estaban las tres, en tres pantallas distintas, y por eso había que
    saber de antemano dónde mirar para poder preguntarse si el sistema andaba.
    """
    id = "todo_bien"
    pregunta = "¿Está todo funcionando bien ahora?"
    necesita = ""
    de_donde = "los motores, la frescura de las tablas y la latencia"

    def sugerencias(self) -> list[str]:
        return []

    def explicar(self, _sujeto: str = "") -> dict:
        from api.services import av_agent_contexto as ctx
        from api.services import av_agent_latencia as lat
        from api.services import av_agent_motores as mot

        pasos, problemas = [], 0

        # ── 1. LOS MOTORES ──
        m = mot.resumen()
        if not m.get("ok"):
            pasos.append(_paso("motores", "Los motores", NO_SE,
                               f"no pude leer el árbol: {m.get('error')}"))
        else:
            rotas = m["rotas"]
            problemas += rotas
            cuerpo = (f"{m['bien']} de {m['total']} piezas bien"
                      + (f" · {m['lentas']} lentas" if m["lentas"] else "")
                      + (f" · **{rotas} ROTAS**" if rotas else "")
                      + (f" · {m['apagadas']} apagadas (fuera de su ventana, "
                         f"que es lo correcto)" if m["apagadas"] else ""))
            if rotas:
                cuerpo += "\n" + "\n".join(
                    f"  · {d['label']} ({d['tipo']}) — {d['estado']}, "
                    f"último dato {d['hace']}" for d in m["detalle_rotas"][:12])
            pasos.append(_paso(
                "motores",
                "Los motores" + (" · EN RUEDA" if m["en_rueda"] else " · fuera de rueda"),
                REVISAR if rotas else OK, cuerpo,
                tabla="diagnostico_registry"))

        # ── 2. LAS TABLAS ──
        try:
            quietas = ctx.detectar_tablas()
        except Exception:
            quietas = []
        problemas += len(quietas)
        pasos.append(_paso(
            "tablas", f"Las tablas · {len(quietas)} sin escribir cuando deberían",
            REVISAR if quietas else OK,
            "\n".join(f"  · {q['ticker']} — {q['motivo']}" for q in quietas[:12])
            or "todas las que tienen un ritmo medido están al día.",
            tabla="manager.tabla_perfil"))

        # ── 3. LA VELOCIDAD ──
        try:
            deg = lat.comparar()
        except Exception:
            deg = []
        problemas += len(deg)
        pasos.append(_paso(
            "velocidad", f"La velocidad · {len(deg)} endpoint/s peor que su normal",
            REVISAR if deg else OK,
            "\n".join(f"  · {d['endpoint']} — {d['avg_ms']} ms contra "
                       f"{d['base_ms']} habituales" for d in deg[:8])
            or "ningún endpoint está por encima de su propia normalidad.",
            tabla="manager.latencia_endpoints"))

        veredicto = ("**Sí, está todo bien.**" if not problemas else
                     f"**No: hay {problemas} cosa/s para mirar.**")
        pasos.insert(0, _paso("veredicto", "En una línea",
                              OK if not problemas else REVISAR, veredicto))
        return {"ok": True, "pasos": pasos,
                "discrepancia": "" if not problemas else veredicto,
                "numeros": {"problemas": problemas}}


# ── 9. ¿ESTÁN BIEN PROTEGIDOS LOS ENDPOINTS? ────────────────────────────────

class EstanProtegidos:
    """*«Todos los endpoints debería ser capaz de controlar que solo los ves si
    tenés el permiso»* (user). Y separa las dos capas, que es lo que hace útil la
    respuesta: **el código puede estar impecable y el borde abierto.**"""

    id = "protegidos"
    pregunta = "¿Están bien protegidos los endpoints?"
    necesita = ""
    de_donde = "el árbol de rutas + una prueba real sin credenciales"

    def sugerencias(self) -> list[str]:
        return []

    def explicar(self, _sujeto: str = "") -> dict:
        from api.services import av_agent_seguridad as seg

        d = seg.declarado()
        pasos = [_paso(
            "declarado", f"Lo que dice el código · {d['total']} rutas",
            REVISAR if d["abiertas_inesperadas"] else OK,
            (f"{d['con_modulo']} con gate de módulo · {d['admin_only']} admin-only "
             f"· {d['escrituras']} escrituras\n"
             + ("sin ningún gate: "
                + ", ".join(d["abiertas_inesperadas"]) if d["abiertas_inesperadas"]
                else "todas las abiertas están declaradas: "
                     + ", ".join(d["abiertas_declaradas"]))),
            tabla="api/superficie.py")]

        p = seg.probar()
        if not p.get("ok"):
            pasos.append(_paso(
                "efectivo", "Lo que hace el borde", NO_SE, str(p.get("motivo"))))
        else:
            filtran = p["filtran"]
            pasos.append(_paso(
                "efectivo", f"Probado de verdad · {p['probadas']} endpoints",
                REVISAR if filtran else OK,
                (f"{p['rechazan']} rechazaron sin credencial"
                 + (f" · {p['mudos']} no dijeron nada (404/503)" if p["mudos"] else "")
                 + (f" · {p['errores']} no respondieron" if p["errores"] else ""))
                + ("\n\n**CONTESTARON SIN CREDENCIAL:**\n"
                   + "\n".join(f"  · {f['path']} → {f['status']} "
                                f"({f['bytes']} bytes)" for f in filtran[:15])
                   if filtran else ""),
                tabla=p["url"]))
            pasos.append(_paso(
                "alcance", "Qué se verificó de verdad", INFO, p["alcance"]))
        return {"ok": True, "pasos": pasos,
                "discrepancia": ("hay endpoints que contestan sin credencial"
                                 if (p.get("filtran") or d["abiertas_inesperadas"])
                                 else ""),
                "numeros": {"rutas": d["total"],
                            "filtran": len(p.get("filtran") or [])}}


EXPLICADORES: dict[str, Explicador] = {
    e.id: e for e in (PorQueRinde(), PorQueEsaTNA(), ComoSeArmaElYTM(),
                      QueInflacionDescuenta(), DeDondeSalenLosNiveles(),
                      CuantoPesaLaBase(), ComoVieneDeVelocidad(),
                      EstaTodoBien(), EstanProtegidos())
}


def catalogo() -> list[dict]:
    """Lo que el agente sabe contestar. Es lo que dibuja la pantalla — y el día
    que se le pueda HABLAR, esta misma lista es el menú de lo que entiende."""
    return [{"id": e.id, "pregunta": e.pregunta, "necesita": e.necesita,
             "de_donde": e.de_donde} for e in EXPLICADORES.values()]


def sugerencias(explicador_id: str) -> list[str]:
    e = EXPLICADORES.get(explicador_id)
    if e is None or not e.necesita:
        return []
    try:
        return e.sugerencias()[:2000]
    except Exception:
        logger.warning("av_agent_explicar: sin sugerencias para %s", explicador_id)
        return []


def explicar(explicador_id: str, sujeto: str = "", *, con_ia: bool = True) -> dict:
    """La respuesta: los pasos deterministas + UNA frase.

    Si el explicador detectó que el número no cierra, eso viaja en
    `discrepancia` y **no se pisa con la frase del modelo**: son dos cosas
    distintas y la segunda no puede tapar a la primera.
    """
    e = EXPLICADORES.get(explicador_id)
    if e is None:
        return {"ok": False, "error": f"no sé contestar «{explicador_id}»"}
    if e.necesita and not (sujeto or "").strip():
        return {"ok": False, "error": f"decime cuál ({e.necesita})"}
    try:
        r = e.explicar((sujeto or "").strip())
    except Exception as ex:
        logger.warning("av_agent_explicar: %s falló: %s", explicador_id, ex)
        return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or "no pude explicarlo"}

    pasos = r.get("pasos") or []
    for i, p in enumerate(pasos, 1):
        p["n"] = i
    frase = _frase(e, sujeto, pasos) if con_ia else ""
    return {"ok": True, "explicador": e.id, "pregunta": e.pregunta,
            "sujeto": sujeto, "frase": frase, "pasos": pasos,
            "discrepancia": r.get("discrepancia") or "",
            "numeros": r.get("numeros") or {}}


def _frase(e: Explicador, sujeto: str, pasos: list[dict]) -> str:
    """**El modelo NO calcula: redacta.** Recibe los pasos que ya salieron del
    cálculo determinista y devuelve la línea que la persona quería leer.

    Es deliberadamente lo último y lo más chico: si falla, si no hay credencial
    o si se acabó el presupuesto, la explicación se muestra igual. Una respuesta
    que depende del modelo para existir es una respuesta que un día no está.
    """
    from core import ai
    tarea = "av_agent_accion"
    if not ai.disponible(tarea):
        return ""
    cuerpo = "\n".join(f"{p['titulo']}: {p['detalle']}" for p in pasos)
    system = (
        "Sos un analista de una mesa de renta fija argentina explicándole un "
        "número a un colega. Te paso los pasos del cálculo YA HECHO.\n\n"
        "Escribí UNA sola frase (máximo 30 palabras) que conteste la pregunta "
        "con el dato que más la explica.\n\n"
        "REGLAS:\n"
        "- Usá SOLO los números que te paso. No calcules ni infieras otros.\n"
        "- Si los pasos no alcanzan para contestar, decí exactamente qué falta.\n"
        "- Sin markdown, sin viñetas, sin preámbulo. La frase sola.")
    txt = ai.completar(tarea, system=system,
                       user=f"PREGUNTA: {e.pregunta}\nSUJETO: {sujeto or '—'}\n\n{cuerpo}",
                       detalle=f"explicar {e.id} {sujeto}".strip())
    return (txt or "").strip()[:400]
