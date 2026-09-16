"""Agente CARTERA: el PATRIMONIO de una cuenta, lo que se mide en plata y
nominales: qué tiene, cuánto vale, cuánto rinde, qué cobra. Sus herramientas
y su agente, en un solo archivo. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal, get_args

from pydantic import Field

from asistente import estado as EST
from asistente import pantalla, permitido
from asistente.agente import COMUN, Agente
from asistente.agentes.renta_fija import (
    EmisorTipo,
    Fecha,
    clave_emisor,
    en_ventana,
    es_del_emisor,
    fecha_o_error,
    metricas_por_ticker,
    texto_ventana,
)
from core import cartera as CART
from core.postgres import get_pool

MAX_DIAS = 730
MAX_PAGOS = 300
MAX_POSICIONES = 200

# QUÉ ES CADA COSA, según la mesa. La fuente es `portafolio.assets.cartera`, que
# viaja en cada posición: un solo campo decide, no hay taxonomía que mantener.
#
# BONOS son TRES carteras y no una (HD · ARS · DL): por eso no alcanza con
# comparar `cartera` contra la palabra que dijo el usuario. El resto sí es
# directo. Fijado con el user: estas categorías agarran todo lo que hay.
#
# ⚠️ Ni un nombre de cartera se escribe acá: TODOS salen de `core/cartera.py`,
# que es donde se declaran una sola vez. Tipearlos sería una segunda copia de la
# misma verdad, y el día que se agregue una cartera de bonos esta lista no se
# enteraría — «qué bonos tengo» perdería títulos sin que falle nada (REGLA #9).
TIPOS: dict[str, tuple[str, ...]] = {
    "bonos": CART.BONOS,
    "acciones": (CART.RENTA_VARIABLE,),
    "fondos": CART.FCI,
    "derivados": (CART.DERIVADOS,),
    "caja": (CART.MONEDAS,),
}
Tipo = Literal["bonos", "acciones", "fondos", "derivados", "caja"]
# Hacia dónde se rota en el eje TIEMPO. Es una DIRECCIÓN, no una fecha: la
# fecha del título que sale la pone el código, y la otra punta la nombra el
# usuario. Sin dirección no se sabe de qué lado del bono propio hay que buscar.
HaciaPlazo = Literal["mas_largo", "mas_corto"]
# Cuántas alternativas se PRESENTAN. Fijado con el user: tres. No es un tope
# técnico — es cuántas opciones se le ponen adelante a un operador para que
# decida. Devolver 45 no es «más completo»: es no haber contestado.
ALTERNATIVAS = 3
MAX_ALTERNATIVAS = 20
HORIZONTES = ("t1", "t0")


def cuentas_disponibles() -> dict:
    """Las cuentas habilitadas con su nombre. No se le ofrece al modelo: va en
    la instrucción del agente cartera."""
    try:
        params = permitido.parametros()
    except permitido.SinPermiso:
        return permitido.como_error()
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id_cuenta, denominacion FROM clientes.cuentas "
                " WHERE id_cuenta = ANY(%(cuentas_permitidas)s) ORDER BY id_cuenta",
                params)
            filas = {r[0]: r[1] for r in cur.fetchall()}
    except Exception as e:
        return {"error": f"no pude leer las cuentas: {type(e).__name__}: {e}"}
    return {
        "cuentas": [{"id_cuenta": c, "nombre": filas.get(c) or "(sin denominación cargada)"}
                    for c in permitido.cuentas()],
        "cuantas": len(permitido.cuentas()),
    }


def _cuenta_habilitada(cuenta) -> tuple[str | None, dict | None]:
    pedida = str(cuenta or "").strip()
    if pedida not in permitido.cuentas():
        return None, {"error": f"la cuenta {pedida!r} no está habilitada para el asistente",
                      "cuentas_habilitadas": permitido.cuentas(),
                      "que_hacer": ("Preguntale al usuario cuál de las cuentas habilitadas "
                                    "quiere. NO contestes con otra cuenta.")}
    return pedida, None


def cobros_futuros(cuenta: str, dias: Annotated[int, Field(ge=1, le=MAX_DIAS)] = 90,
                   hasta: Fecha | None = None) -> dict:
    """Cuánta PLATA va a cobrar una cuenta de acá a una fecha, y en qué fechas.

    Contesta las dos preguntas que parecen distintas y no lo son:

      · «¿cuánto cobro?» / «¿qué me pagan este mes?» / «¿cuánta plata entra?»
      · «¿qué bono me vence?» — un vencimiento es el ÚLTIMO cobro de un bono.
        Cada título trae su fecha de vencimiento en `vence`.

    NO es lo que tiene la cuenta. Para «cuánto tengo», «qué títulos tengo» o
    «cuánto vale mi cartera» está `tenencia_actual`. Acá no hay nominales ni
    valuación: hay plata que entra.

    `cuenta` es OBLIGATORIA: el `id_cuenta` sale de la lista que tenés en las
    instrucciones.

    QUÉ DEVUELVE:
      · `total` — la plata del período, SEPARADA POR MONEDA: una cifra por
        moneda, tal cual vienen.
      · `por_mes` — el mismo total abierto mes a mes, ya hecho. Sirve para
        decir dónde cae el grueso.
      · `titulos` — cuánto paga cada bono en total, y cuándo vence.
      · `pagos` — el detalle, un renglón por fecha y bono.
      · `tenencia_del` — sobre qué foto de cartera se proyectó. Una compra o una
        venta posterior a esa fecha no está adentro.
      · `truncado` — true si hubo más pagos de los que entraron en `pagos`;
        `total` y `titulos` siguen siendo del período COMPLETO. Todo lo que hay
        que sumar YA VIENE SUMADO.

    Montos brutos contractuales en la moneda del bono; los CER ya ajustados.

    Args:
        cuenta: el `id_cuenta` a mirar.
        dias: cuántos días para adelante mirar. Si el usuario no dijo un
            plazo, son 90 y NO hace falta preguntárselo.
        hasta: la fecha límite, si el usuario nombró una («hasta fin de año»,
            «al 31 de diciembre»). Pisa a `dias`.
    """
    hoy = date.today()
    if hasta:
        try:
            limite = date.fromisoformat(str(hasta).strip()[:10])
        except ValueError:
            return {"error": f"`hasta` tiene que ser una fecha YYYY-MM-DD, llegó {hasta!r}"}
        n = (limite - hoy).days
        if n < 1 or n > MAX_DIAS:
            return {"error": f"`hasta` tiene que caer entre mañana y {MAX_DIAS} días "
                             f"(hoy es {hoy.isoformat()}), llegó {hasta!r}"}
    else:
        try:
            n = int(dias)
        except (TypeError, ValueError):
            return {"error": f"`dias` tiene que ser un número entero, llegó {dias!r}"}
        if n < 1 or n > MAX_DIAS:
            return {"error": f"`dias` tiene que estar entre 1 y {MAX_DIAS}, llegó {n}",
                    "que_hacer": "Volvé a llamar con un número dentro de ese rango."}
    try:
        params = permitido.parametros()
    except permitido.SinPermiso:
        return permitido.como_error()
    pedida, err = _cuenta_habilitada(cuenta)
    if err:
        return err
    params["cuentas_permitidas"] = [pedida]
    params["desde"] = hoy.isoformat()
    params["hasta"] = (hoy + timedelta(days=n)).isoformat()

    # Todo sale de `operaciones.acreencias` (precomputado por jobs/acreencias):
    # la tenencia no se vuelve a mirar. `fecha_pago` es TEXT ISO, así que la
    # comparación lexicográfica es cronológica. La moneda va siempre en el
    # GROUP BY. El total sale de su propia consulta, no de sumar el detalle.
    sql_pagos = f"""
        SELECT t.fecha_pago, t.ticker, t.moneda,
               sum(t.monto)           AS monto,
               max(t.data->>'emisor') AS emisor
          FROM operaciones.acreencias t
         WHERE {permitido.FILTRO_SQL}
           AND t.fecha_pago >= %(desde)s
           AND t.fecha_pago <= %(hasta)s
         GROUP BY t.fecha_pago, t.ticker, t.moneda
         ORDER BY t.fecha_pago, t.ticker
    """
    sql_titulos = f"""
        SELECT t.ticker, t.moneda,
               sum(t.monto)           AS total,
               max(t.data->>'emisor') AS emisor,
               max(c.fecha_vencimiento) AS vence
          FROM operaciones.acreencias t
          LEFT JOIN mercado.curvas c ON c.ticker = t.ticker
         WHERE {permitido.FILTRO_SQL}
           AND t.fecha_pago >= %(desde)s
           AND t.fecha_pago <= %(hasta)s
         GROUP BY t.ticker, t.moneda
         ORDER BY 3 DESC
    """
    sql_mes = f"""
        SELECT substr(t.fecha_pago, 1, 7) AS mes, t.moneda, sum(t.monto) AS monto
          FROM operaciones.acreencias t
         WHERE {permitido.FILTRO_SQL}
           AND t.fecha_pago >= %(desde)s
           AND t.fecha_pago <= %(hasta)s
         GROUP BY 1, t.moneda
         ORDER BY 1
    """
    sql_total = f"""
        SELECT t.moneda, sum(t.monto) AS monto
          FROM operaciones.acreencias t
         WHERE {permitido.FILTRO_SQL}
           AND t.fecha_pago >= %(desde)s
           AND t.fecha_pago <= %(hasta)s
         GROUP BY t.moneda
    """
    sql_ficha = f"""
        SELECT max(t.data->>'snapshot'), max(t.data->>'cliente')
          FROM operaciones.acreencias t
         WHERE {permitido.FILTRO_SQL}
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql_total, params)
            tot = cur.fetchall()
            cur.execute(sql_mes, params)
            meses = cur.fetchall()
            cur.execute(sql_titulos, params)
            tits = cur.fetchall()
            cur.execute(sql_pagos, params)
            filas = cur.fetchall()
            cur.execute(sql_ficha, params)
            snapshot, nombre = cur.fetchone()
    except Exception as e:
        return {"error": f"no pude leer los cobros: {type(e).__name__}: {e}"}

    def _mon(m) -> str:
        return (m or "SIN MONEDA").upper()

    return {
        "ventana": {"desde": params["desde"], "hasta": params["hasta"], "dias": n},
        "cuenta": {"id_cuenta": pedida, "nombre": nombre},
        "tenencia_del": snapshot,
        "total": {_mon(m): round(float(v or 0), 2) for m, v in tot},
        "por_mes": _por_mes(meses, _mon),
        "titulos": [{
            "ticker": t[0],
            "emisor": t[3],
            "vence": t[4].isoformat() if t[4] else None,
            "moneda": _mon(t[1]),
            "total": round(float(t[2] or 0), 2),
        } for t in tits],
        "pagos": [{
            "fecha": f[0],
            "dias": (date.fromisoformat(f[0]) - hoy).days,
            "ticker": f[1],
            "emisor": f[4],
            "moneda": _mon(f[2]),
            "monto": round(float(f[3] or 0), 2),
        } for f in filas[:MAX_PAGOS]],
        "cuantos_pagos": len(filas),
        "truncado": len(filas) > MAX_PAGOS,
    }


def _por_mes(filas, mon) -> list[dict]:
    out: dict[str, dict] = {}
    for mes, moneda, monto in filas:
        out.setdefault(mes, {"mes": mes})[mon(moneda)] = round(float(monto or 0), 2)
    return [out[k] for k in sorted(out)]


def _cartera_de(fila: dict) -> str:
    """La cartera de una posición, normalizada para comparar. Sin esto, «FCI» y
    «fci » son dos cosas distintas y el filtro pierde títulos en silencio."""
    return " ".join(str(fila.get("cartera") or "").split()).upper()


def _carteras_de(filas: list[dict]) -> list[dict]:
    """Qué carteras hay en la cuenta y cuántas posiciones tiene cada una. Va
    SIEMPRE: si un filtro no encuentra nada, esto dice qué sí hay, en vez de
    devolver una tabla vacía que parece «no tenés»."""
    cuenta: dict[str, int] = {}
    for f in filas:
        cuenta[_cartera_de(f) or "-"] = cuenta.get(_cartera_de(f) or "-", 0) + 1
    return [{"cartera": c, "posiciones": n}
            for c, n in sorted(cuenta.items(), key=lambda kv: (-kv[1], kv[0]))]


def tenencia_actual(cuenta: str, horizonte: Literal["t1", "t0"] = "t1",
                    tipo: Tipo | None = None) -> dict:
    """Qué TIENE hoy una cuenta: títulos, nominales, valuación y cuánto RINDEN
    a precios de mercado.

    Es la posición, la cartera, el patrimonio: lo que está en la cuenta AHORA.
    NO es lo que va a cobrar — para «cuánta plata entra», «qué me pagan» o «qué
    bono me vence» está `cobros_futuros`. Es la pantalla NEGOCIO → CARTERAS.

    **Si preguntan por un TIPO de título («qué bonos tengo», «tengo acciones?»)
    pasá `tipo`.** Sin eso viene la cuenta entera, saldos de caja incluidos, y
    la tabla los muestra.

    QUÉ DEVUELVE:
      · `posiciones` — por título: ticker, emisor, clase, cartera, `cantidad`
        (nominales), precio, valuación, `share` y, del mercado de hoy,
        `tea_pct`, `paridad_pct`, `duration`, `vencimiento`. Con trampa:
        `share` es sobre la cuenta COMPLETA, así que con `tipo` no suma 100;
        `precio_mercado` null = el título no está en el master de curvas.
      · `total` es de la cuenta ENTERA siempre; `total_tipo` es la suma de lo
        que pediste, ya hecha. Con `tipo`, el que corresponde es `total_tipo`.
      · `carteras` — qué hay en la cuenta y cuántas posiciones cada una.
      · `sin_mercado` — sin datos de mercado hoy: no les inventes TEA.
      · `cuantas` (lo devuelto) vs `cuantas_en_la_cuenta`. Todo lo que hay que
        sumar YA VIENE SUMADO.

    Args:
        cuenta: el `id_cuenta` a mirar.
        horizonte: `t1` (default) es la posición liquidada a MAÑANA, con lo
            concertado hoy adentro — «cuánto vale el cliente». `t0` es lo
            liquidado a HOY: lo que se puede entregar o caucionar. Si no lo
            dijo, `t1` y no preguntes.
        tipo: qué mirar — `bonos` · `acciones` · `fondos` · `derivados` ·
            `caja`. Sin esto viene todo junto.
    """
    h = str(horizonte or "t1").strip().lower()
    if h not in HORIZONTES:
        return {"error": f"`horizonte` tiene que ser {' o '.join(HORIZONTES)}, llegó {horizonte!r}"}
    try:
        permitido.parametros()
    except permitido.SinPermiso:
        return permitido.como_error()
    # `posiciones_actuales` no conoce el permiso: la pared es este chequeo.
    pedida, err = _cuenta_habilitada(cuenta)
    if err:
        return err

    from api.services import valuaciones_sql

    r = valuaciones_sql.posiciones_actuales(id_cuenta=pedida, asof=True,
                                            con_pnl=False, horizonte=h)
    filas = r.get("posiciones") or []
    nombres = {c["id_cuenta"]: c["nombre"] for c in cuentas_disponibles().get("cuentas") or []}
    # Lo que el mercado dice hoy de cada título, por código y no por modelo: es
    # el cruce cuenta × mercado que ningún proveedor tiene que hacer. Se cruza
    # por el MISMO ticker; una posición en la pata D no encuentra la pata en
    # pesos (queda en `sin_mercado`, no se adivina).
    try:
        mercado = metricas_por_ticker()
        mercado_error = None
    except Exception as e:
        mercado, mercado_error = {}, f"sin datos de mercado: {type(e).__name__}: {e}"
    posiciones, sin_mercado = [], []
    for p in filas[:MAX_POSICIONES]:
        m = mercado.get(p.get("ticker")) or {}
        if not m:
            sin_mercado.append(p.get("ticker"))
        posiciones.append({
            "ticker": p.get("ticker"),
            "emisor": p.get("emisor"),
            "clase": p.get("clase_activo"),
            "cartera": p.get("cartera") or None,
            "cantidad": p.get("cantidad"),
            "precio": p.get("precio"),
            "valuacion": p.get("valuacion"),
            "share": p.get("share"),
            "tea_pct": m.get("tea_pct"),
            "paridad_pct": m.get("paridad_pct"),
            "duration": m.get("duration"),
            "vencimiento": m.get("vencimiento"),
            "precio_mercado": m.get("precio"),
            "tasa_ruido": m.get("tasa_ruido", False),
        })
    carteras = _carteras_de(posiciones)
    pedido = str(tipo or "").strip().lower() or None
    mostradas, total_tipo = posiciones, None
    if pedido:
        if pedido not in TIPOS:
            return {"error": f"`tipo` tiene que ser uno de {sorted(TIPOS)}, llegó {tipo!r}"}
        mostradas = [p for p in posiciones if _cartera_de(p) in TIPOS[pedido]]
        if not mostradas:
            return {"error": f"la cuenta {pedida} no tiene {pedido} hoy",
                    "carteras_en_la_cuenta": carteras,
                    "que_hacer": "Decile qué SÍ tiene. No busques en otra cuenta por tu cuenta."}
        # El total de lo filtrado lo suma el código: el modelo no puede, y sin
        # esto la tabla mostraría 5 bonos con el total de TODA la cuenta abajo
        # —caja negativa incluida— y las dos cifras serían defendibles.
        total_tipo = round(sum(p.get("valuacion") or 0 for p in mostradas), 2)
    return {
        "cuenta": {"id_cuenta": pedida, "nombre": nombres.get(pedida, "")},
        "fecha": r.get("fecha"),
        "horizonte": h,
        "tipo": pedido,
        "moneda_valuacion": "ARS",
        "total": r.get("total"),
        "total_tipo": total_tipo,
        "posiciones": mostradas,
        "carteras": carteras,
        "cuantas": len(mostradas),
        "cuantas_en_la_cuenta": len(filas),
        "truncado": len(filas) > MAX_POSICIONES,
        "sin_mercado": [s for s in sin_mercado
                        if any(p["ticker"] == s for p in mostradas)] if pedido else sin_mercado,
        "mercado_error": mercado_error,
        "_tabla": pantalla.tabla(
            "posiciones",
            ["ticker", "emisor", "cantidad", "valuacion", "share", "tea_pct", "vencimiento"],
            f"{pedido.capitalize() if pedido else 'Tenencia'} de la {pedida} al {r.get('fecha')}",
            total="total_tipo" if pedido else "total", moneda="ARS"),
    }


def _mirable(f: dict) -> bool:
    """¿Este título sirve para comparar rendimientos? Necesita TEA y que no sea
    ruido (un bono que vence en días tiene una TEA anualizada que no compara
    con nada)."""
    return f.get("tea_pct") is not None and not f.get("tasa_ruido")


def _delta(a, b, dec: int = 2):
    return None if a is None or b is None else round(a - b, dec)


def opciones_para_rotar(cuenta: str, ticker: str | None = None,
                        desde_emisor: str | None = None, hacia_emisor: str | None = None,
                        hacia_tipo: EmisorTipo | None = None,
                        hacia_plazo: HaciaPlazo | None = None,
                        hacia_vencimiento: Fecha | None = None,
                        mostrar: Annotated[int, Field(ge=1, le=MAX_ALTERNATIVAS)]
                        | None = None) -> dict:
    """Qué opciones hay para cambiar un título de la cuenta por otro.

    Compara RENDIMIENTOS, no plata: acá no hay nominales ni valuación. «Rotar
    mi YFCOO a un corporativo», «vender el AO28 y comprar uno a 2029».

    Las alternativas salen de la MISMA CURVA que el título que sale: la TEA de
    un CER y la de un hard dollar no son el mismo número.

    Ya mira el mercado por vos: NO llames `instrumentos_de_la_curva` después
    para completar la lista —serían dos tablas de lo mismo— ni para mirar el
    que sale. Y NO es para comprar sin vender nada («qué bono me conviene»):
    para eso está esa otra. Acá siempre sale algo tuyo.

    QUÉ DEVUELVE:
      · `referencia` — el título tuyo que SALE, con su tasa y duration.
      · `alternativas` — las mejores por `delta_tea_pp` (cuánto MÁS rinde que
        la referencia, en puntos), con `delta_duration` (positivo = estirás el
        plazo). Ya restados.
      · `ventana` — entre qué fechas se buscó, si pediste plazo.
      · `tenes` — los tuyos que entraron; `sin_tasa`, los que no comparan.
      · Un delta positivo NO es una recomendación: mirá también la duration.

    Args:
        cuenta: el `id_cuenta` a mirar.
        ticker: QUÉ título sale. Si el usuario lo señaló sin nombrarlo
            («venderlo», «ese», «el que tengo»), es el ticker que ya trajo
            una herramienta en esta conversación: mandá ése.
        desde_emisor: si no nombró un título sino un emisor («mis bonos de
            YPF»); sale el que MENOS rinde de ésos. Hace falta uno de los dos.
        hacia_emisor: alternativas de este emisor, por pedazo del nombre.
        hacia_tipo: o de este tipo. Excluyente con `hacia_emisor`.
        hacia_plazo: cuando quiere estirar o acortar el vencimiento. NO le
            mandes la fecha del que sale: ésa la pone el sistema, que la tiene.
        hacia_vencimiento: el OTRO extremo, si el usuario lo nombró («hasta
            2029» es `2029-12-31` con `mas_largo`). Se busca en ese lapso.
        mostrar: cuántas querés; 3 si no pedís un número.
    """
    try:
        permitido.parametros()
    except permitido.SinPermiso:
        return permitido.como_error()
    pedida, err = _cuenta_habilitada(cuenta)
    if err:
        return err
    desde_e, hacia = clave_emisor(desde_emisor), clave_emisor(hacia_emisor)
    tk = (str(ticker or "").strip().upper()) or None
    tipo = (str(hacia_tipo or "").strip().lower()) or None
    plazo = (str(hacia_plazo or "").strip().lower()) or None
    if plazo and plazo not in get_args(HaciaPlazo):
        return {"error": f"`hacia_plazo` tiene que ser uno de {list(get_args(HaciaPlazo))}, "
                         f"llegó {hacia_plazo!r}"}
    if not hacia and not tipo and not plazo:
        return {"error": "hace falta decir hacia dónde: `hacia_emisor`, `hacia_tipo` o "
                         "`hacia_plazo`"}
    if hacia and tipo:
        return {"error": "`hacia_emisor` y `hacia_tipo` son excluyentes: mandá uno"}
    if tipo and tipo not in get_args(EmisorTipo):
        return {"error": f"`hacia_tipo` tiene que ser uno de {list(get_args(EmisorTipo))}, "
                         f"llegó {hacia_tipo!r}"}
    tope = None
    if hacia_vencimiento is not None:
        tope, err = fecha_o_error(hacia_vencimiento, "hacia_vencimiento")
        if err:
            return err
        if not plazo:
            return {"error": "`hacia_vencimiento` es UN extremo de la ventana: mandá también "
                             "`hacia_plazo`, porque el otro extremo lo pone el vencimiento del "
                             "título que sale"}
    # `mostrar` en None = nadie pidió un número. La diferencia importa para
    # `truncado`: mostrar las 3 mejores de 4 cuando nadie pidió un número ES la
    # respuesta, no un recorte. Decirle «truncado» ahí lo manda a buscar el
    # resto con otra herramienta — medido en el LAB, así salió la segunda tabla.
    pidio_numero = mostrar is not None
    try:
        n = ALTERNATIVAS if mostrar is None else int(mostrar)
    except (TypeError, ValueError):
        return {"error": f"`mostrar` tiene que ser un número entero, llegó {mostrar!r}"}
    n = max(1, min(n, MAX_ALTERNATIVAS))

    tenencia = tenencia_actual(pedida)
    if "error" in tenencia:
        return tenencia
    try:
        mercado = metricas_por_ticker()
    except Exception as e:
        return {"error": f"sin datos de mercado, no puedo comparar: {type(e).__name__}: {e}"}

    # Del lado de la cuenta el emisor puede venir escrito distinto que en el
    # master de curvas. Se acepta cualquiera de los dos: perder un título por
    # una diferencia de string sería contestar que no tenés algo que tenés.
    posiciones = tenencia.get("posiciones") or []
    if tk:
        # El usuario nombró el título: ese sale, y no se elige por él.
        mios = [p for p in posiciones if (p.get("ticker") or "").upper() == tk]
        if not mios:
            return {"error": f"la cuenta {pedida} no tiene {ticker!r}",
                    "tickers_en_la_cuenta": sorted(p["ticker"] for p in posiciones if p.get("ticker"))}
    elif desde_e:
        mios = [p for p in posiciones
                if es_del_emisor(p, desde_e) or es_del_emisor(mercado.get(p["ticker"]) or {}, desde_e)]
        if not mios:
            hay = sorted({e for p in posiciones if (e := clave_emisor(p.get("emisor")))})
            return {"error": f"la cuenta {pedida} no tiene títulos de un emisor que contenga "
                             f"{desde_emisor!r}",
                    "emisores_en_la_cuenta": hay}
    else:
        # Nadie dijo qué sale. No es motivo para cortar todavía: si la cuenta
        # tiene UN SOLO título comparable, «venderlo» señala con el dedo y el
        # dedo apunta al único que hay. Preguntar «¿cuál?» con uno solo le
        # quema un turno al operador. Con dos o más sí se pregunta (abajo).
        mios = posiciones

    tenes = [{"ticker": p["ticker"], "curva": (mercado.get(p["ticker"]) or {}).get("curva"),
              "tea_pct": p.get("tea_pct"), "duration": p.get("duration"),
              "paridad_pct": p.get("paridad_pct"), "vencimiento": p.get("vencimiento"),
              "tasa_ruido": p.get("tasa_ruido", False)} for p in mios]
    comparables = [f for f in tenes if _mirable(f) and f["curva"]]
    if not comparables:
        return {"error": f"tenés {ticker or desde_emisor or 'títulos'} pero sin tasa "
                         f"comparable hoy",
                "tenes": tenes,
                "que_hacer": "Decilo así: no es que no haya alternativas, es que no hay con "
                             "qué compararlas."}
    unico = not tk and not desde_e
    if unico:
        if len(comparables) > 1:
            return {"error": "hace falta decir QUÉ sale: el `ticker` que el usuario nombró "
                             "o señaló, o `desde_emisor`",
                    "titulos_comparables": [f["ticker"] for f in comparables]}
        # con uno solo, el resto de la cartera no es del tema
        tenes = comparables

    # El que menos rinde es el candidato natural a salir, y es contra ése que
    # los deltas significan algo. Se nombra: el modelo no lo tiene que deducir.
    ref = comparables[0] if (tk or unico) else min(comparables, key=lambda f: f["tea_pct"])

    # ⚠️ LA VENTANA LA ANCLA EL CÓDIGO, NO EL MODELO. El usuario nombra UNA
    # punta («hasta 2029»); la otra es el vencimiento del título que sale, que
    # acá ya lo tenemos. Si esa fecha la tuviera que mandar el modelo, la
    # estaría copiando del turno anterior de la conversación — y una fecha mal
    # copiada no falla: devuelve otra lista, igual de convincente (REGLA #9).
    # Sin ancla no se contesta: «más largo» que nada no quiere decir nada.
    desde = hasta = None
    if plazo:
        if not ref["vencimiento"]:
            return {"error": f"no sé cuándo vence {ref['ticker']}, así que no puedo decir qué "
                             f"es «{plazo.replace('_', ' ')}» que él",
                    "referencia": ref["ticker"]}
        if plazo == "mas_largo":
            desde, hasta = ref["vencimiento"], tope
        else:
            desde, hasta = tope or date.today().isoformat(), ref["vencimiento"]
        if desde and hasta and desde > hasta:
            return {"error": f"la ventana queda al revés: {ref['ticker']} vence "
                             f"{ref['vencimiento']} y pediste {plazo.replace('_', ' ')} "
                             f"hasta {tope}",
                    "referencia": ref["ticker"]}

    def _va(m: dict) -> bool:
        if m.get("curva") != ref["curva"] or not _mirable(m) or m["ticker"] == ref["ticker"]:
            return False
        if plazo:
            vto = m.get("vencimiento")
            # El mismo día no es ni más largo ni más corto: los bordes de la
            # ventana entran, el vencimiento de la referencia no.
            if vto == ref["vencimiento"] or not en_ventana(vto, desde, hasta):
                return False
        if hacia:
            return es_del_emisor(m, hacia)
        if tipo:
            return (m.get("emisor_tipo") or "").strip().lower() == tipo
        return True

    hacia_txt = " · ".join(x for x in (hacia_emisor, tipo, texto_ventana(desde, hasta)) if x)
    otros = [m for m in mercado.values() if _va(m)]
    if not otros:
        en_curva = sorted({e for m in mercado.values()
                           if m.get("curva") == ref["curva"] and (e := clave_emisor(m.get("emisor")))})
        return {"error": f"no hay títulos ({hacia_txt}) con tasa en la curva {ref['curva']}, "
                         f"que es donde está {ref['ticker']}",
                "tenes": tenes, "referencia": ref["ticker"], "emisores_en_esa_curva": en_curva}

    alternativas = sorted(
        ({"ticker": m["ticker"], "emisor": m.get("emisor"), "curva": m.get("curva"),
          "tea_pct": m.get("tea_pct"), "duration": m.get("duration"),
          "paridad_pct": m.get("paridad_pct"), "vencimiento": m.get("vencimiento"),
          "delta_tea_pp": _delta(m.get("tea_pct"), ref["tea_pct"]),
          "delta_duration": _delta(m.get("duration"), ref["duration"])} for m in otros),
        key=lambda f: -(f["delta_tea_pp"] or 0))
    salida = {
        "cuenta": pedida,
        "sale": ref["ticker"],
        "hacia": hacia_txt,
        "curva": ref["curva"],
        "tenes": tenes,
        "referencia": {"ticker": ref["ticker"], "tea_pct": ref["tea_pct"],
                       "duration": ref["duration"], "vencimiento": ref["vencimiento"],
                       "por_que": ("lo nombró el usuario" if tk else
                                   "es el único título comparable de la cuenta" if unico else
                                   "es el que menos rinde de los tuyos de ese emisor")},
        "alternativas": alternativas[:n],
        "cuantas": len(alternativas),
        "truncado": pidio_numero and len(alternativas) > n,
        "sin_tasa": [f["ticker"] for f in tenes if not _mirable(f)],
        "_tabla": pantalla.tabla(
            "alternativas",
            ["ticker", "vencimiento", "tea_pct", "delta_tea_pp", "duration", "delta_duration"],
            f"Alternativas ({hacia_txt}) para el {ref['ticker']}"),
    }
    if plazo:
        salida["ventana"] = {"desde": desde, "hasta": hasta}
    return salida


# ── el agente ───────────────────────────────────────────────────────────────

_INSTRUCCION = """
Si el usuario ya nombró una cuenta —en esta pregunta o antes: la que está en
foco—, usala. Si no hay ninguna nombrada ni en foco y hay más de una
habilitada, preguntale cuál quiere: no elijas vos.

Cuando muestres opciones para rotar, PRESENTÁS lo que sale de los números, no
aconsejás: quién decide es el operador. Decí siempre el trade-off completo —
más tasa con más duration es más plazo, no una ganancia gratis— y nombrá
contra qué título tuyo se está comparando.
"""


def _instruccion(foco: dict) -> str:
    cuentas = cuentas_disponibles().get("cuentas") or []
    partes = [COMUN, _INSTRUCCION]
    if cuentas:
        lista = "\n".join(f"  {c['id_cuenta']} — {c['nombre']}" for c in cuentas)
        partes.append("Cuentas habilitadas (son las ÚNICAS que podés consultar; el número "
                      f"es el `cuenta` que llevan las herramientas):\n{lista}\n")
    partes.append(EST.como_texto(foco))
    return "".join(partes)


AGENTE = Agente(
    nombre="cartera",
    tarea="asistente_cartera",
    describe="el PATRIMONIO de una cuenta: qué títulos tiene, cuántos nominales, cuánto "
             "valen, cuánto rinden, qué cobra y cuándo. Todo lo que se mide en plata y "
             "nominales. No sabe quién es el titular ni qué operó.",
    instruccion=_instruccion,
    herramientas=(tenencia_actual, cobros_futuros, opciones_para_rotar),
    senales=("tengo", "tenemos", "tenencia", "tenencias", "cartera", "carteras",
             "portafolio", "portafolios", "portfolio", "posicion", "posiciones",
             "rotar", "rotarlo", "rotarlos", "rotacion", "rotaciones",
             # «vender» en infinitivo es sobre algo que TENÉS: solo se vende lo
             # propio. «comprar» no —«qué me conviene comprar» es mercado— así
             # que no entra. El pasado («vendió», «compró») es de operaciones.
             "vender", "venderlo", "venderla", "venderlos", "vendo",
             "nominal", "nominales", "cobro", "cobros", "cobra", "cobrar", "cupon", "cupones",
             "valuacion", "patrimonio", "mio", "mia", "mis"),
    foco=("cuenta",),
)
