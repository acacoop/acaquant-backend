"""Las herramientas del asistente: funciones puras de Python que el modelo pide
por nombre y el grafo ejecuta. El docstring de cada una es lo que el modelo lee
para elegirla; la firma (tipos, defaults, `Literal`) es el esquema que recibe.
Arquitectura y reglas: docs/AvAgentAI.md."""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Literal, get_args

from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from asistente import permitido
from core.postgres import get_pool

MAX_DIAS = 730
MAX_PAGOS = 300
MAX_POSICIONES = 200
MAX_INSTRUMENTOS = 50
MAX_FLUJOS = 24
# Techo del esquema que viaja al modelo por herramienta (chars de JSON).
MAX_FICHA_CHARS = 2_300

HORIZONTES = ("t1", "t0")
Curva = Literal["tasa_fija", "cer", "hard_dolar", "dolar_linked", "tamar"]
OrdenCurva = Literal["tea", "vencimiento", "duration", "volumen_dia"]


# ── cuenta ──────────────────────────────────────────────────────────────────


def cuentas_disponibles() -> dict:
    """Las cuentas habilitadas con su nombre. No se le ofrece al modelo: va en
    la instrucción del mundo cuenta."""
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


def cobros_futuros(cuenta: str, dias: int = 90) -> dict:
    """Cuánta PLATA va a cobrar una cuenta en los próximos N días, y en qué fechas.

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
      · `total` — la plata del período, SEPARADA POR MONEDA. NUNCA sumes pesos
        con dólares ni conviertas: dalos por separado, tal cual vienen.
      · `por_mes` — el mismo total abierto mes a mes. Usalo para decir dónde
        cae el grueso. NO lo calcules sumando `pagos`: ya viene hecho.
      · `titulos` — cuánto paga cada bono en total, y cuándo vence.
      · `pagos` — el detalle, un renglón por fecha y bono.
      · `tenencia_del` — sobre qué foto de cartera se proyectó. Una compra o una
        venta posterior a esa fecha no está adentro.
      · `truncado` — true si hubo más pagos de los que entraron en `pagos`.
        `total` y `titulos` siguen siendo del período COMPLETO.
      · Todo lo que hay que sumar YA VIENE SUMADO. No rehagas las cuentas.

    Los montos son el bruto contractual en la moneda del bono; los CER ya vienen
    ajustados por el último CER publicado. NO devuelve nominales ni valuación.

    Args:
        cuenta: el `id_cuenta` a mirar.
        dias: cuántos días para adelante mirar. Entre 1 y 730; si el usuario
            no dijo un plazo, son 90 y NO hace falta preguntárselo.
    """
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
    hoy = date.today()
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


def tenencia_actual(cuenta: str, horizonte: Literal["t1", "t0"] = "t1") -> dict:
    """Qué TIENE hoy una cuenta: los títulos, cuántos nominales y cuánto valen.

    Es la posición, la cartera, el patrimonio: lo que está en la cuenta AHORA.

    NO es lo que va a cobrar. Para «cuánta plata entra», «qué me pagan», «qué
    cupón viene» o «qué bono me vence» está `cobros_futuros`. Acá no hay fechas
    de pago ni cupones: hay tenencia.

    Devuelve exactamente lo mismo que la pantalla NEGOCIO → CARTERAS de la
    plataforma, porque corre el mismo código que esa pantalla.

    QUÉ DEVUELVE:
      · `total` — la valuación de toda la cuenta, en la moneda de `moneda_valuacion`.
      · `posiciones` — una fila por título: ticker, emisor, clase de activo,
        cartera, nominales (`cantidad`), precio, valuación y `share` (qué % de la
        cuenta es ese título, YA CALCULADO — no lo dividas vos).
      · `fecha` — de cuándo es la posición. Si no es de hoy, es la última foto
        conciliada y no incluye lo de después.
      · `truncado` — true si hay más títulos de los que entraron en la lista;
        `total` y `cuantas` siguen siendo de la cuenta COMPLETA.

    Todo lo que hay que sumar YA VIENE SUMADO. No rehagas las cuentas.

    Args:
        cuenta: el `id_cuenta` a mirar.
        horizonte: `t1` (default) es la posición liquidada a MAÑANA, con lo
            concertado hoy adentro — «cuánto vale el cliente». `t0` es lo
            liquidado a HOY: lo que está en custodia y se puede entregar o
            caucionar. Si el usuario no dijo nada, `t1` y no hace falta
            preguntárselo.
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
    return {
        "cuenta": {"id_cuenta": pedida, "nombre": nombres.get(pedida, "")},
        "fecha": r.get("fecha"),
        "horizonte": h,
        "moneda_valuacion": "ARS",
        "total": r.get("total"),
        # `vencimiento` no viaja: la fecha de verdad está en `mercado.curvas`,
        # que ya la da `cobros_futuros.vence`.
        "posiciones": [{
            "ticker": p.get("ticker"),
            "emisor": p.get("emisor"),
            "clase": p.get("clase_activo"),
            "cartera": p.get("cartera") or None,
            "cantidad": p.get("cantidad"),
            "precio": p.get("precio"),
            "valuacion": p.get("valuacion"),
            "share": p.get("share"),
        } for p in filas[:MAX_POSICIONES]],
        "cuantas": len(filas),
        "truncado": len(filas) > MAX_POSICIONES,
        "_tabla": {
            "campo": "posiciones",
            "columnas": ["ticker", "emisor", "cantidad", "valuacion", "share"],
            "total": "total",
            "moneda": "ARS",
        },
    }


# ── mercado ─────────────────────────────────────────────────────────────────


def _pct(x):
    """TEA y TEM salen del motor como decimal; al modelo van en porcentaje."""
    return round(float(x) * 100, 2) if x is not None else None


def _num(x, dec: int = 2):
    return round(float(x), dec) if x is not None else None


def _ordenar(filas: list[dict], por: str) -> list[dict]:
    if por == "tea":
        return sorted(filas, key=lambda f: (f["tasa_ruido"] or f["tea_pct"] is None,
                                            -(f["tea_pct"] or 0)))
    if por == "volumen_dia":
        return sorted(filas, key=lambda f: (f["volumen_dia"] is None, -(f["volumen_dia"] or 0)))
    return sorted(filas, key=lambda f: (f[por] is None, f[por] or 0))


def _instrumento(b: dict, hoy: date) -> dict:
    m = b.get("metrics") or {}
    vto = b.get("vencimiento")
    try:
        meses = round((date.fromisoformat(str(vto)[:10]) - hoy).days / 30.44, 1) if vto else None
    except ValueError:
        meses = None
    return {
        "ticker": b.get("ticker_corto"),
        "emisor": b.get("emisor"),
        "vencimiento": str(vto)[:10] if vto else None,
        "meses_al_vencimiento": meses,
        "precio": _num(m.get("last_price"), 4),
        "tea_pct": _pct(m.get("TEA")),
        "tem_pct": _pct(m.get("TEM")),
        "paridad_pct": _num(m.get("paridad")),
        "duration": _num(m.get("duration")),
        "volumen_dia": _num(m.get("total_nominals"), 0),
        "tasa_ruido": bool(b.get("tasa_ruido")),
    }


def curva(curva: Curva, ordenar_por: OrdenCurva = "tea", limit: int = 15) -> dict:
    """Qué instrumentos hay HOY en una curva de renta fija y cuánto rinden.

    Es el MERCADO, no una cuenta: acá no hay nominales de nadie ni plata que
    entra. Para «qué tengo» está `tenencia_actual`; para «qué cobro»,
    `cobros_futuros`. Esto contesta «qué hay», «qué rinde más», «qué vence en
    tal plazo», «cuál conviene».

    Curvas: `cer` (ajustan por inflación), `tasa_fija` (LECAP/BONCAP y tasa
    fija en pesos), `hard_dolar` (bonos en dólares: AL, GD, ONs), `dolar_linked`,
    `tamar`. Si el usuario dice «bonos CER» es `cer`; «en dólares», «soberanos»
    o «hard dollar» es `hard_dolar`; «letras» o «tasa fija» es `tasa_fija`.

    QUÉ DEVUELVE:
      · `instrumentos` — una fila por título: `ticker`, `emisor`, `vencimiento`,
        `meses_al_vencimiento`, `precio`, `tea_pct` y `tem_pct` (YA en
        porcentaje), `paridad_pct`, `duration` (años), `volumen_dia` y
        `tasa_ruido`. Si `tasa_ruido` es true, la tasa NO es comparable (el
        bono vence en días): no la uses para decir cuál rinde más.
      · `cuantos` — cuántos hay en la curva en total; `truncado` si entraron
        menos que eso en `instrumentos`.
      · Todo lo que hay que calcular YA VIENE CALCULADO. No conviertas tasas.

    Args:
        curva: cuál de las curvas mirar.
        ordenar_por: cómo ordenar; con `tea` los que más rinden van primero.
            Si el usuario no dijo, es `tea` y no hace falta preguntar.
        limit: cuántos instrumentos traer, de 1 a 50. Si no dijo, 15.
    """
    from api.services import curvas_vista as CV
    from core import curvas_ejes as ce

    if curva not in ce.pills_disponibles():
        return {"error": f"`curva` tiene que ser una de {list(ce.pills_disponibles())}, "
                         f"llegó {curva!r}"}
    if ordenar_por not in get_args(OrdenCurva):
        return {"error": f"`ordenar_por` tiene que ser uno de {list(get_args(OrdenCurva))}, "
                         f"llegó {ordenar_por!r}"}
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return {"error": f"`limit` tiene que ser un número entero, llegó {limit!r}"}
    n = max(1, min(n, MAX_INSTRUMENTOS))
    try:
        bonos = [b for b in CV.get_curvas_vista().get("bonos") or [] if b.get("pill") == curva]
    except Exception as e:
        return {"error": f"no pude leer la curva: {type(e).__name__}: {e}"}
    hoy = date.today()
    filas = _ordenar([_instrumento(b, hoy) for b in bonos], ordenar_por)
    return {
        "curva": curva,
        "ordenado_por": ordenar_por,
        "instrumentos": filas[:n],
        "cuantos": len(filas),
        "truncado": len(filas) > n,
        "_tabla": {
            "campo": "instrumentos",
            "columnas": ["ticker", "emisor", "vencimiento", "precio", "tea_pct", "duration"],
        },
    }


def ficha_bono(ticker: str) -> dict:
    """Qué ES un bono: quién lo emite, en qué moneda paga, cómo ajusta, cuándo
    vence, qué cupón tiene, qué paga en los próximos meses y cómo cotiza hoy.

    Es la ficha del INSTRUMENTO, sin importar quién lo tenga. NO dice cuánto
    cobra una cuenta de ese bono: eso depende de cuántos nominales tiene, y lo
    contesta `cobros_futuros`. Los montos acá son por 100 de valor nominal.

    QUÉ DEVUELVE:
      · `ficha` — emisor, tipo, curva, moneda, ajuste, ley, emisión,
        vencimiento, valor nominal, cupón anual.
      · `proximos_pagos` — un renglón por fecha futura: `fecha`, `interes`,
        `amortizacion`, `monto`, por 100 VN. `cuantos_pagos` y `truncado`
        dicen si entraron todos.
      · `hoy` — precio, `tea_pct` y `tem_pct` (YA en porcentaje), paridad y
        duration de la cotización de hoy, si el bono cotizó.
      · Un bono que no existe devuelve `error`: decilo, no adivines otro.

    Args:
        ticker: el ticker CORTO del bono, en mayúsculas: `AL30`, `TX26`,
            `S31O5`. Sin sufijo de moneda (`AL30D` es la misma especie que
            `AL30`; usá `AL30`).
    """
    from api.services import bono_detalle as BD

    tk = str(ticker or "").strip().upper()
    if not tk:
        return {"error": "`ticker` está vacío"}
    try:
        r = BD.get_bono(tk)
    except Exception as e:
        return {"error": f"no pude leer la ficha: {type(e).__name__}: {e}"}
    if r.get("error"):
        return {"error": r["error"],
                "que_hacer": "Decile al usuario que ese ticker no está en el master de "
                             "curvas. NO pruebes con otro ticker parecido: preguntale."}
    futuros = [f for f in r.get("flujos") or [] if f.get("futuro")]
    patas = r.get("patas") or []
    principal = next((p for p in patas if p.get("pata") == r.get("pata_principal")), None)
    m = (principal or {}).get("metrics") or {}
    return {
        "ticker": r.get("ticker"),
        "ficha": {k: v for k, v in (r.get("ficha") or {}).items()
                  if k in ("emisor", "emisor_tipo", "tipo", "curva", "moneda", "moneda_flujo",
                           "ajuste", "ley", "fecha_emision", "fecha_vencimiento",
                           "valor_nominal", "cupon_anual", "cer_fijado")},
        "unidad": r.get("unidad_flujo"),
        "nota": r.get("nota_flujo"),
        "proximos_pagos": [{
            "fecha": f.get("fecha"),
            "interes": f.get("interes"),
            "amortizacion": f.get("amortizacion"),
            "monto": f.get("monto"),
        } for f in futuros[:MAX_FLUJOS]],
        "cuantos_pagos": len(futuros),
        "truncado": len(futuros) > MAX_FLUJOS,
        "hoy": {
            "precio": _num(m.get("last_price"), 4),
            "tea_pct": _pct(m.get("TEA")),
            "tem_pct": _pct(m.get("TEM")),
            "paridad_pct": _num(m.get("paridad")),
            "duration": _num(m.get("duration")),
        } if m else None,
        "_tabla": {
            "campo": "proximos_pagos",
            "columnas": ["fecha", "interes", "amortizacion", "monto"],
        },
    }


# ── el registro ─────────────────────────────────────────────────────────────

# Las de la CUENTA reciben `cuenta` y pasan por el permiso. Las del MERCADO no
# la reciben. Un test exige las dos cosas.
DE_LA_CUENTA = (cobros_futuros, tenencia_actual)
DEL_MERCADO = (curva, ficha_bono)
TODAS = DE_LA_CUENTA + DEL_MERCADO
POR_NOMBRE = {f.__name__: f for f in TODAS}


def como_tool(fn) -> StructuredTool:
    """La herramienta como `tool` de LangChain: docstring = descripción, firma = esquema."""
    return StructuredTool.from_function(fn)


def ficha(fn) -> dict:
    """El esquema que ve el modelo, en el dialecto del proveedor."""
    return convert_to_openai_tool(como_tool(fn))


def para_el_modelo(resultado):
    """El resultado sin las claves que empiezan con `_` (instrucciones de pantalla)."""
    if not isinstance(resultado, dict):
        return resultado
    return {k: v for k, v in resultado.items() if not str(k).startswith("_")}


def peso_ficha(fn) -> int:
    return len(json.dumps(ficha(fn), ensure_ascii=False))
