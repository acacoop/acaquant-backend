"""Mundo CARTERA: el PATRIMONIO de una cuenta, lo que se mide en plata y
nominales: qué tiene, cuánto vale, cuánto rinde, qué cobra. Sus herramientas
y su agente, en un solo archivo. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from asistente import estado as EST
from asistente import permitido
from asistente.agente import COMUN, Agente
from asistente.mundos.mercado import metricas_por_ticker
from core.postgres import get_pool

MAX_DIAS = 730
MAX_PAGOS = 300
MAX_POSICIONES = 200
HORIZONTES = ("t1", "t0")


def cuentas_disponibles() -> dict:
    """Las cuentas habilitadas con su nombre. No se le ofrece al modelo: va en
    la instrucción del mundo cartera."""
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


def cobros_futuros(cuenta: str, dias: int = 90, hasta: str | None = None) -> dict:
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
      · `total` — la plata del período, SEPARADA POR MONEDA. NUNCA sumes pesos
        con dólares ni conviertas: dalos por separado, tal cual vienen.
      · `por_mes` — el mismo total abierto mes a mes. Usalo para decir dónde
        cae el grueso. NO lo calcules sumando `pagos`: ya viene hecho.
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
        dias: cuántos días para adelante mirar. Entre 1 y 730; si el usuario
            no dijo un plazo, son 90 y NO hace falta preguntárselo.
        hasta: la fecha límite, `YYYY-MM-DD`, si el usuario nombró una («hasta
            fin de año», «al 31 de diciembre»). Pisa a `dias`; no calcules los
            días vos.
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


def tenencia_actual(cuenta: str, horizonte: Literal["t1", "t0"] = "t1") -> dict:
    """Qué TIENE hoy una cuenta: los títulos, cuántos nominales, cuánto valen y
    cuánto RINDEN hoy a precios de mercado (TEA, paridad, duration, vencimiento).

    Es la posición, la cartera, el patrimonio: lo que está en la cuenta AHORA.
    También contesta «¿cuánto rinden los bonos que tengo?»: cada título trae lo
    que el mercado dice hoy de él, cuando está en el master de curvas.

    NO es lo que va a cobrar. Para «cuánta plata entra», «qué me pagan», «qué
    cupón viene» o «qué bono me vence» está `cobros_futuros`. Acá no hay fechas
    de pago ni cupones: hay tenencia.

    Es lo mismo que la pantalla NEGOCIO → CARTERAS: corre el mismo código.

    QUÉ DEVUELVE:
      · `total` — la valuación de toda la cuenta, en la moneda de `moneda_valuacion`.
      · `posiciones` — una fila por título: ticker, emisor, clase de activo,
        cartera, nominales (`cantidad`), precio, valuación y `share` (qué % de la
        cuenta es ese título, YA CALCULADO — no lo dividas vos). Y del mercado
        de hoy: `tea_pct`, `paridad_pct`, `duration`, `vencimiento`,
        `precio_mercado` (null si el título no está en el master de curvas).
      · `sin_mercado` — los tickers sin datos de mercado hoy. No les inventes TEA.
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
    return {
        "cuenta": {"id_cuenta": pedida, "nombre": nombres.get(pedida, "")},
        "fecha": r.get("fecha"),
        "horizonte": h,
        "moneda_valuacion": "ARS",
        "total": r.get("total"),
        "posiciones": posiciones,
        "cuantas": len(filas),
        "truncado": len(filas) > MAX_POSICIONES,
        "sin_mercado": sin_mercado,
        "mercado_error": mercado_error,
        "_tabla": {
            "campo": "posiciones",
            "columnas": ["ticker", "emisor", "cantidad", "valuacion", "share", "tea_pct",
                         "vencimiento"],
            "total": "total",
            "moneda": "ARS",
        },
    }


# ── el agente ───────────────────────────────────────────────────────────────

_INSTRUCCION = """
Si el usuario ya nombró una cuenta —en esta pregunta o antes: la que está en
foco—, usala. Si no hay ninguna nombrada ni en foco y hay más de una
habilitada, preguntale cuál quiere: no elijas vos.
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
    herramientas=(tenencia_actual, cobros_futuros),
    senales=("tengo", "tenemos", "tenencia", "tenencias", "cartera", "posicion", "posiciones",
             "nominal", "nominales", "cobro", "cobros", "cobra", "cobrar", "cupon", "cupones",
             "valuacion", "patrimonio", "vale", "mio", "mia", "mis"),
    foco=("cuenta",),
)
