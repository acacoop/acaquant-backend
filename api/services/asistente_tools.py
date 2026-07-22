"""asistente_tools — tools curadas READ-ONLY del asistente de negocio (QuantAI P7).

Regla de oro (token-in / token-out):
- Las tools reciben FICHAS (CLIENTE_7, CTA_2) y las resuelven a ids reales
  SOLO acá adentro (perímetro), vía core.pii_gateway.id_cuenta_de_ficha.
- Lo que devuelven al LLM ya está limpio: los resultados se refieren a la
  cuenta por su FICHA (jamás nombre/id real) y, cinturón y tirantes, TODO
  resultado pasa por pii_gateway.tokenize antes de volver al loop.
- El LLM nunca escribe SQL: elige tool + parámetros; el SQL vive acá, fijo.
- READ-ONLY absoluto: ninguna tool muta nada.

Las cifras (AuM, P&L) salen pseudonimizadas — atadas a la ficha, imposibles
de vincular a una persona desde afuera (decisión user 2026-07-21, QUANTAI P7).

Set inicial CHICO (se amplía con uso real, no por las dudas):
  resumen_mesa()            — agregados del día: AuM total, cuentas, top segmentos.
  rendimiento_cuenta(ficha) — AuM + PnL de UNA cuenta, referida por su ficha.
  volumen_operado(...)      — volumen bruto consolidado por mercado/operación/
                              segmento/título en un período (reglas de la vista).
  aranceles_consolidado(...) — lo facturado (aranceles) consolidado igual.
"""
from __future__ import annotations

import logging

from api.cache import cached as _cached_vocab_factory
from core import pii_gateway
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_cached_vocab = _cached_vocab_factory(ttl=3600)

_DIMENSIONES = ["mercado", "operacion", "segmento", "nivel_3", "instrumento",
                "operador", "cartera", "cliente", "mes"]
_PARAMS_CONSOLIDADO = {
    "type": "object",
    "properties": {
        "desde": {"type": "string", "description": "Fecha inicial ISO (YYYY-MM-DD)."},
        "hasta": {"type": "string", "description": "Fecha final ISO (YYYY-MM-DD)."},
        "por": {"type": "string", "enum": _DIMENSIONES,
                "description": "Dimensión de agrupado: mercado (BYMA/MAV/A3/MAE/FCI "
                               "Bilateral), operacion (tipo de operación), segmento "
                               "(nivel 1, ej. PRODUCTORES), nivel_3 (segmento fino "
                               "del boleto), instrumento (título), operador (el "
                               "comercial que atiende las cuentas — aparecen como "
                               "referencias OPERADOR_n), cartera (la del TÍTULO "
                               "operado: HD, DL, ARS, FCI…), **cliente** (el "
                               "RANKING de clientes: quiénes son los que más "
                               "operaron o más dejaron) o **mes** (la SERIE mes a "
                               "mes del período, para comparar contra el mes "
                               "anterior)."},
        "mercado": {"type": "string",
                    "description": "Filtrar a UN mercado puntual (opcional)."},
        "excluir_segmento": {"type": "string",
                             "description": "Excluir un segmento nivel 1 (opcional, "
                                            "ej. 'AGRO' para un consolidado sin agro)."},
        "ficha_operador": {"type": "string",
                           "description": "Filtrar a las cuentas de UN operador, por "
                                          "su referencia (OPERADOR_1) tal cual "
                                          "aparece en la conversación (opcional)."},
        "ficha_cuenta": {"type": "string",
                         "description": "Filtrar a UN cliente por su referencia "
                                        "(CLIENTE_1). Es lo que responde '¿cuánto "
                                        "operó tal cliente?' (opcional)."},
        "cartera": {"type": "string",
                    "description": "Filtrar a una cartera del título (HD, DL, ARS, "
                                   "FCI…) (opcional)."},
        "moneda": {"type": "string", "enum": ["ARS", "USD"],
                   "description": "Moneda del resultado (default ARS). USD convierte "
                                  "CADA boleto con el tipo de cambio de SU día, no con "
                                  "una cotización de hoy — por eso sí se puede "
                                  "dolarizar un período largo."},
    },
    "required": ["desde", "hasta", "por"],
}


# ── Schemas para el function-calling (formato OpenAI) ────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "resumen_mesa",
            "description": (
                "Agregados del día de la mesa: AuM total administrado, cantidad de "
                "cuentas con tenencia y los segmentos más grandes por AuM. Usala para "
                "preguntas generales del negocio ('¿cuál es el AuM total?', '¿cómo se "
                "reparte por segmento?'). No recibe parámetros y no trae clientes."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rendimiento_cuenta",
            "description": (
                "TOTALES de patrimonio de UNA cuenta: cuánto vale su tenencia "
                "(AuM) y su P&L acumulado (no realizado, pasivo, realizado del "
                "día). El parámetro es la FICHA con la que la cuenta aparece en "
                "la conversación (CLIENTE_1, CTA_2...). "
                "DEVUELVE SOLO TOTALES: no trae el detalle de qué títulos tiene "
                "ni cuánto pesa cada uno — si preguntan EN QUÉ está invertido, "
                "decí que tenés el total pero no la composición. "
                "Tampoco sirve para '¿cuánto operó?' (eso es volumen operado, "
                "va por volumen_operado con ficha_cuenta)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ficha_cuenta": {
                        "type": "string",
                        "description": "La ficha tal cual aparece (ej. CLIENTE_1 o CTA_1).",
                    },
                },
                "required": ["ficha_cuenta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "posiciones_cuenta",
            "description": (
                "EN QUÉ está invertido un cliente: sus principales posiciones "
                "con cuánto pesa cada una y qué tan concentrada está la cartera. "
                "Usala cuando pregunten 'en qué está', 'qué tiene', 'cómo está "
                "compuesta su cartera'. Complementa rendimiento_cuenta, que solo "
                "da los totales."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ficha_cuenta": {"type": "string",
                                     "description": "La referencia del cliente (CLIENTE_1)."},
                    "top": {"type": "integer",
                            "description": "Cuántas posiciones listar (default 10)."},
                },
                "required": ["ficha_cuenta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aum_composicion",
            "description": (
                "CÓMO está repartido el AuM de toda la mesa por cartera (pesos, "
                "hard dollar, FCI…) en el último cierre. Usala para '¿cómo está "
                "compuesto el AuM?', '¿cuánto está en dólares?'. Es agregado: no "
                "trae clientes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha": {"type": "string",
                              "description": "Cierre a mirar (YYYY-MM-DD). Sin esto, el último."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cobros_futuros",
            "description": (
                "QUÉ PLATA ENTRA: cupones, rentas y amortizaciones que van a "
                "cobrar los clientes, por fecha, con el día pico. Usala para "
                "'¿qué se cobra este mes?', '¿cuándo entra plata?', 'acreencias "
                "próximas'. Agregado por día y moneda, sin clientes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dias": {"type": "integer",
                             "description": "Ventana hacia adelante desde hoy (default 30)."},
                    "desde": {"type": "string", "description": "Alternativa: fecha inicial."},
                    "hasta": {"type": "string", "description": "Alternativa: fecha final."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aum_historico",
            "description": (
                "EVOLUCIÓN del AuM en un período, con promedio, mediana, mínimo y "
                "máximo sobre los cierres diarios. Usala para '¿cuál fue el AuM "
                "promedio de FCI en junio?', 'la mediana del mes', 'cómo evolucionó "
                "el AuM'. Se puede acotar a una cartera (HD, DL, ARS, FCI…) y/o a un "
                "cliente. OJO: resumen_mesa da el AuM de HOY; esta da la HISTORIA."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "desde": {"type": "string", "description": "Fecha inicial YYYY-MM-DD."},
                    "hasta": {"type": "string", "description": "Fecha final YYYY-MM-DD."},
                    "cartera": {"type": "string",
                                "description": "Acotar a una cartera del título "
                                               "(HD, DL, ARS, FCI…) (opcional)."},
                    "ficha_cuenta": {"type": "string",
                                     "description": "Acotar a un cliente por su "
                                                    "referencia (CLIENTE_1) (opcional)."},
                },
                "required": ["desde", "hasta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quien_es",
            "description": (
                "Averigua si la persona que nombró el usuario es un CLIENTE (una "
                "cuenta) o un OPERADOR comercial (empleado de la mesa). Usala "
                "SIEMPRE antes de responder sobre una persona: son cosas distintas "
                "y se consultan con herramientas distintas. Si devuelve ambiguo, "
                "preguntale al usuario cuál quiere — no elijas vos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ficha": {
                        "type": "string",
                        "description": "La referencia de la persona tal cual aparece "
                                       "en la conversación (CLIENTE_1, OPERADOR_2…).",
                    },
                },
                "required": ["ficha"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "volumen_operado",
            "description": (
                "Volumen bruto operado (ARS) en un período, consolidado por una "
                "dimensión y opcionalmente filtrado a UN cliente o UN operador. "
                "ESTA es la herramienta de '¿cuánto operó X?' y '¿cuánto se operó?' "
                "— lo OPERADO es volumen de compras/ventas, NO el patrimonio ni el "
                "resultado (para eso está rendimiento_cuenta, que es otra cosa). El "
                "volumen excluye los cierres de caución (regla de la mesa, aplicada)."
            ),
            "parameters": _PARAMS_CONSOLIDADO,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aranceles_consolidado",
            "description": (
                "Aranceles facturados (SIEMPRE en pesos) consolidados por una "
                "dimensión en un período: por mercado, tipo de operación, segmento "
                "o título. Usala para 'lo facturado', consolidados de aranceles. "
                "Incluye el arancel de caución que vive en los cierres (regla de "
                "la mesa, ya aplicada)."
            ),
            "parameters": _PARAMS_CONSOLIDADO,
        },
    },
]


# ── Queries (perímetro, read-only) ───────────────────────────────────────────

def _fecha_snapshot() -> str | None:
    """Último snapshot de tenencias (portafolio.tenencia, writer diario 11 UTC)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
    return str(f) if f else None


def _aum_totales(fecha: str) -> tuple[float, int]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(sum(valuacion), 0), count(DISTINCT id_cuenta) "
            "FROM portafolio.tenencia WHERE fecha = %s AND aum = 'si'", (fecha,))
        total, n = cur.fetchone()
    return float(total or 0), int(n or 0)


def _aum_por_segmento(fecha: str, top: int = 5) -> list[tuple[str, float]]:
    """AuM agrupado por segmento nivel_1 (clientes.comitentes) — agregado puro,
    sin nombres. Cuentas sin segmentación → 'SIN SEGMENTO'."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(nullif(trim(m.nivel_1), ''), 'SIN SEGMENTO') AS seg, "
            "       sum(t.valuacion) AS aum "
            "FROM portafolio.tenencia t "
            "LEFT JOIN clientes.comitentes m USING (id_cuenta) "
            "WHERE t.fecha = %s AND t.aum = 'si' "
            "GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT %s", (fecha, top))
        return [(r[0], float(r[1] or 0)) for r in cur.fetchall()]


def _aum_cuenta(fecha: str, id_cuenta: str) -> float | None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT sum(valuacion) FROM portafolio.tenencia "
            "WHERE fecha = %s AND aum = 'si' AND id_cuenta = %s", (fecha, id_cuenta))
        v = cur.fetchone()[0]
    return float(v) if v is not None else None


def _pnl_cuenta(id_cuenta: str) -> dict | None:
    """Totales del motor de PnL (valuaciones.pnl_totales_cache, precalculado
    por jobs.pnl_totales_precompute). None = la cuenta no está en el cache."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT totales, computed_at FROM valuaciones.pnl_totales_cache "
            "WHERE id_cuenta = %s", (id_cuenta,))
        fila = cur.fetchone()
    if not fila or not fila[0]:
        return None
    return {"totales": fila[0], "computed_at": str(fila[1] or "")}


# ── Formato (números legibles, sin identidades) ──────────────────────────────

def _monto(v: float | None, moneda: str = "ARS") -> str:
    if v is None:
        return "sin dato"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.2f} mil millones {moneda}"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f} millones {moneda}"
    return f"{v:.0f} {moneda}"


# ── Las tools ────────────────────────────────────────────────────────────────

def resumen_mesa() -> str:
    fecha = _fecha_snapshot()
    if not fecha:
        return "sin datos de tenencia disponibles (el snapshot diario no corrió todavía)"
    total, n_cuentas = _aum_totales(fecha)
    lineas = [
        f"[resumen de la mesa — snapshot {fecha}]",
        f"AuM total administrado: {_monto(total)}",
        f"cuentas con tenencia: {n_cuentas}",
        "AuM por segmento (top):",
    ]
    for seg, aum in _aum_por_segmento(fecha):
        pct = 100 * aum / total if total else 0
        lineas.append(f"  - {seg}: {_monto(aum)} ({pct:.1f}%)")
    return "\n".join(lineas)


def rendimiento_cuenta(ficha_cuenta: str, *, mapping: dict) -> str:
    """La ficha se resuelve al id real SOLO acá adentro; la respuesta vuelve
    a referirse a la cuenta por su ficha — el id jamás viaja al LLM."""
    ficha = (ficha_cuenta or "").strip()
    id_cuenta = pii_gateway.id_cuenta_de_ficha(ficha, mapping)
    if not id_cuenta:
        return (f"no pude identificar la cuenta {ficha or '(vacía)'} — pedile al usuario "
                "que aclare el nombre completo o el número de cuenta")
    fecha = _fecha_snapshot()
    lineas = [f"[rendimiento de {ficha}]"]
    if fecha:
        aum = _aum_cuenta(fecha, id_cuenta)
        lineas.append(f"AuM ({fecha}): {_monto(aum)}"
                      if aum is not None else f"sin tenencia con AuM al {fecha}")
    pnl = _pnl_cuenta(id_cuenta)
    if pnl:
        t = pnl["totales"]
        lineas += [
            f"PnL títulos ACUMULADO (no es del día; motor de valuaciones, "
            f"al {pnl['computed_at'][:16]}):",
            f"  - no realizado (acumulado): {_monto(t.get('pnl_no_realizado'))}",
            f"  - pasivo acumulado (cupones/divs/amorts): {_monto(t.get('pnl_pasivo'))}",
            f"  - realizado del día de hoy: {_monto(t.get('pnl_realizado_dia'))}",
            f"  - total acumulado: {_monto(t.get('pnl_total'))}",
        ]
    else:
        lineas.append("PnL: la cuenta no está en el cache del motor (sin posiciones con "
                      "cost-basis o el precompute no corrió)")
    return "\n".join(lineas)


def _aum_serie_diaria(desde: str, hasta: str, cartera: str | None,
                      id_cuenta: str | None) -> list[tuple[str, float]]:
    """AuM valorizado POR DÍA en [desde, hasta]. `portafolio.tenencia` es un
    snapshot diario (writer 11 UTC) con `cartera` propia → la evolución y sus
    estadísticas salen de ahí. Mismo filtro que el AuM: aum='si'."""
    cond = ["fecha BETWEEN %(d)s AND %(h)s", "aum = 'si'"]
    p: dict = {"d": desde, "h": hasta}
    if cartera:
        cond.append("cartera = %(c)s")
        p["c"] = cartera
    if id_cuenta:
        cond.append("id_cuenta = %(idc)s")
        p["idc"] = id_cuenta
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT fecha, coalesce(sum(valuacion), 0) AS v FROM portafolio.tenencia "
            f"WHERE {' AND '.join(cond)} GROUP BY fecha ORDER BY fecha", p)
        return [(str(f), float(v or 0)) for f, v in cur.fetchall()]


def aum_historico(args: dict, *, mapping: dict) -> str:
    """Evolución del AuM en un período + promedio, mediana, mínimo y máximo.
    Responde "¿cuál fue el AuM promedio de FCI en junio?" — que NO es el AuM
    de hoy (resumen_mesa) ni el volumen operado."""
    import statistics

    from api.services.copiloto.navegacion import clasificar_persona

    desde, hasta = str(args.get("desde", "")), str(args.get("hasta", ""))
    if not desde or not hasta:
        return "necesito el período (desde y hasta, en formato YYYY-MM-DD)"
    id_cuenta = None
    ficha = str(args.get("ficha_cuenta") or "").strip()
    if ficha:
        quien = clasificar_persona(ficha, mapping)
        if not quien["cuenta"]:
            return (f"no encontré la cuenta {ficha} — pedile el número de cuenta "
                    "o el nombre como figura")
        id_cuenta = pii_gateway.id_cuenta_de_ficha(ficha, mapping)
        if not id_cuenta:
            return f"no pude resolver la cuenta {ficha} a un número de cuenta"
    cartera = str(args.get("cartera") or "").strip() or None

    serie = _aum_serie_diaria(desde, hasta, cartera, id_cuenta)
    if not serie:
        return (f"no hay snapshots de tenencia entre {desde} y {hasta}"
                + (f" para la cartera {cartera}" if cartera else "")
                + " — puede que el período sea anterior al primer cierre guardado")
    valores = [v for _f, v in serie]
    etiqueta = f"AuM{f' de {cartera}' if cartera else ''}{' de ' + ficha if ficha else ''}"
    mn = min(serie, key=lambda x: x[1])
    mx = max(serie, key=lambda x: x[1])
    return "\n".join([
        f"[{etiqueta} — {desde} a {hasta}, {len(serie)} días con snapshot]",
        f"  promedio: {_monto(statistics.fmean(valores))}",
        f"  mediana:  {_monto(statistics.median(valores))}",
        f"  mínimo:   {_monto(mn[1])} ({mn[0]})",
        f"  máximo:   {_monto(mx[1])} ({mx[0]})",
        f"  primero:  {_monto(valores[0])} ({serie[0][0]})",
        f"  último:   {_monto(valores[-1])} ({serie[-1][0]})",
    ])


def posiciones_cuenta(args: dict, *, mapping: dict) -> str:
    """EN QUÉ está invertido un cliente: top posiciones y concentración. Es lo
    que `rendimiento_cuenta` NO da (esa devuelve solo totales)."""
    from api.services import valuaciones_sql
    from api.services.copiloto.navegacion import clasificar_persona

    ficha = str(args.get("ficha_cuenta") or "").strip()
    quien = clasificar_persona(ficha, mapping)
    if not quien["cuenta"]:
        return (f"no encontré la cuenta {ficha or '(vacía)'} — pedile el número "
                "de cuenta o el nombre como figura")
    id_cuenta = pii_gateway.id_cuenta_de_ficha(ficha, mapping)
    if not id_cuenta:
        return f"no pude resolver {ficha} a un número de cuenta"
    top = max(1, min(int(args.get("top") or 10), 25))
    r = valuaciones_sql.posiciones_actuales(id_cuenta=id_cuenta) or {}
    filas = [p for p in (r.get("posiciones") or r.get("rows") or [])
             if float(p.get("valuacion") or 0) != 0]
    if not filas:
        return f"{ficha} no tiene posiciones en el último cierre"
    filas.sort(key=lambda p: -float(p.get("valuacion") or 0))
    total = sum(float(p.get("valuacion") or 0) for p in filas)
    lineas = [f"[posiciones de {ficha} — cierre {r.get('fecha') or 's/f'}, "
              f"{len(filas)} títulos · total {_monto(total)}]"]
    for p in filas[:top]:
        val = float(p.get("valuacion") or 0)
        pct = 100 * val / total if total else 0
        etq = p.get("ticker") or p.get("unidad") or "?"
        cart = p.get("cartera") or "sin cartera"
        lineas.append(f"  - {etq} ({cart}): {_monto(val)} · {pct:.1f}%")
    if len(filas) > top:
        resto = sum(float(p.get("valuacion") or 0) for p in filas[top:])
        lineas.append(f"  - resto ({len(filas) - top} títulos): {_monto(resto)} "
                      f"· {100 * resto / total if total else 0:.1f}%")
    top5 = sum(float(p.get("valuacion") or 0) for p in filas[:5])
    lineas.append(f"CONCENTRACIÓN: el top 5 es el {100 * top5 / total if total else 0:.1f}% "
                  "de la cartera")
    return "\n".join(lineas)


def aum_composicion(args: dict, *, mapping: dict) -> str:
    """CÓMO está repartido el AuM de la mesa: por cartera (pesos / hard dollar
    / FCI…) en el último cierre. Agregado — no emite cuentas ni clientes."""
    from api.services import portfolio_sql

    fecha = str(args.get("fecha") or "").strip() or _fecha_snapshot()
    if not fecha:
        return "sin snapshots de tenencia todavía"
    r = portfolio_sql.total_snapshot(fecha=fecha, moneda="ARS") or {}
    filas = r.get("rows") or r.get("por_cartera") or []
    acc: dict[str, float] = {}
    for f in filas:
        # se agrega por cartera y se DESCARTA cuenta/id_cuenta (vienen por fila)
        acc[str(f.get("cartera") or "OTROS")] = acc.get(
            str(f.get("cartera") or "OTROS"), 0.0) + float(f.get("valuacion") or 0)
    if not acc:
        return f"no hay tenencia valorizada al {fecha}"
    total = sum(acc.values())
    lineas = [f"[composición del AuM al {fecha} — total {_monto(total)}]"]
    for cart, val in sorted(acc.items(), key=lambda x: -x[1]):
        lineas.append(f"  - {cart}: {_monto(val)} ({100 * val / total if total else 0:.1f}%)")
    return "\n".join(lineas)


def cobros_futuros(args: dict, *, mapping: dict) -> str:
    """QUÉ PLATA ENTRA: cupones, rentas y amortizaciones que cobran los
    clientes, por fecha. El dominio 'plata' que el asistente no veía."""
    from datetime import UTC, datetime, timedelta

    from api.services import cashflow_sql

    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()
    dias = max(1, min(int(args.get("dias") or 30), 365))
    desde = str(args.get("desde") or "").strip() or hoy.isoformat()
    hasta = str(args.get("hasta") or "").strip() or (hoy + timedelta(days=dias)).isoformat()
    filas = cashflow_sql.por_dia(desde=desde, hasta=hasta) or []
    if not filas:
        return f"no hay cobros agendados entre {desde} y {hasta}"
    por_moneda: dict[str, float] = {}
    for f in filas:
        for mon, monto in (f.get("por_moneda") or {}).items():
            por_moneda[mon] = por_moneda.get(mon, 0.0) + float(monto or 0)
    lineas = [f"[cobros de clientes — {desde} a {hasta}, {len(filas)} días con pagos]",
              "TOTAL: " + " · ".join(f"{_monto(v, m)}" for m, v in
                                     sorted(por_moneda.items(), key=lambda x: -x[1]))]
    pico = max(filas, key=lambda f: sum((f.get("por_moneda") or {}).values()))
    lineas.append("día pico: " + str(pico.get("fecha")) + " → " + " · ".join(
        f"{_monto(v, m)}" for m, v in (pico.get("por_moneda") or {}).items()))
    lineas.append("por fecha:")
    for f in filas[:20]:
        montos = " · ".join(f"{_monto(v, m)}"
                            for m, v in (f.get("por_moneda") or {}).items())
        lineas.append(f"  - {f.get('fecha')}: {montos} "
                      f"({f.get('n_clientes', '?')} clientes, {f.get('n_pagos', '?')} pagos)")
    if len(filas) > 20:
        lineas.append(f"  (+{len(filas) - 20} días más en el período)")
    return "\n".join(lineas)


def _consolidado(metrica: str, args: dict, *, mapping: dict) -> str:
    """volumen_operado / aranceles_consolidado — envuelven ops_consolidado
    (api/services/operaciones_sql.py): MISMO _ops_where que la vista, con las
    reglas del negocio adentro (es_cierre, FCI por solicitud/liquidación).
    Dimensión operador (decisión b, 2026-07-21): los EMPLEADOS tampoco salen
    — cada nombre de operador se ficha OPERADOR_n ANTES de volver al LLM."""
    from api.services import operaciones_sql
    from api.services.copiloto.navegacion import clasificar_persona

    ficha_op = str(args.get("ficha_operador") or "").strip()
    operador_sel = None
    if ficha_op:
        operador_sel = pii_gateway.operador_de_ficha(ficha_op, mapping)
        if not operador_sel:
            quien = clasificar_persona(ficha_op, mapping)
            if quien["cuenta"]:
                return (f"{ficha_op} NO es un operador, es una CUENTA de cliente — "
                        "volvé a llamarme con ficha_cuenta en vez de ficha_operador")
            return (f"no pude identificar al operador {ficha_op} — pedile al usuario "
                    "el nombre completo del operador")

    # cuenta de cliente: resuelto DENTRO del perímetro; la denominación real
    # va al SQL, nunca vuelve al modelo
    ficha_cta = str(args.get("ficha_cuenta") or "").strip()
    denominacion = None
    if ficha_cta:
        quien = clasificar_persona(ficha_cta, mapping)
        if quien["cuenta"] and quien["operador"]:
            return (f"{ficha_cta} figura como cuenta Y como operador — preguntale al "
                    "usuario cuál quiere antes de traer números")
        if not quien["cuenta"]:
            if quien["operador"]:
                return (f"{ficha_cta} NO es una cuenta de cliente, es un OPERADOR — "
                        "volvé a llamarme con ficha_operador")
            return (f"no encontré la cuenta {ficha_cta} — pedile al usuario el número "
                    "de cuenta o el nombre como figura")
        denominacion = quien["cuenta"]
    moneda = str(args.get("moneda") or "ARS").upper()
    r = operaciones_sql.ops_consolidado(
        metrica=metrica,
        desde=str(args.get("desde", "")), hasta=str(args.get("hasta", "")),
        por=str(args.get("por", "mercado")),
        mercado=(str(args["mercado"]) if args.get("mercado") else None),
        excluir_segmento=(str(args["excluir_segmento"])
                          if args.get("excluir_segmento") else None),
        operador_sel=operador_sel, denominacion=denominacion,
        cartera_filtro=(str(args["cartera"]) if args.get("cartera") else None),
        moneda=moneda if moneda in ("ARS", "USD") else "ARS",
    )
    if r.get("error"):
        return r["error"]
    if not r["filas"]:
        return (f"sin operaciones para ese corte ({r['desde']} a {r['hasta']}, "
                f"por {r['por']})")
    titulo = "volumen bruto" if metrica == "bruto" else "aranceles"
    if moneda == "USD":
        titulo += " (convertido a USD con el TC de cada boleto)"
    por_operador = r["por"] == "operador"
    # la dimensión CLIENTE devuelve NOMBRES → se fichan antes de volver al
    # modelo (es un ranking de clientes, el dato más sensible que emitimos)
    por_cliente = r["por"] == "cliente"
    lineas = [f"[{titulo} por {r['por']} — {r['desde']} a {r['hasta']}, {r['moneda']}]"]
    for f in r["filas"]:
        clave = f["clave"]
        if por_operador and clave != "(sin operador)":
            # identidad de EMPLEADO → ficha directa (el LLM jamás ve el nombre)
            clave = pii_gateway.asignar_ficha(mapping, "OPERADOR", clave)
        elif por_cliente and clave != "(sin)":
            # identidad de CLIENTE → ficha. El usuario ve el nombre real
            # porque la respuesta se destokeniza al final del turno.
            clave = pii_gateway.asignar_ficha(mapping, "CLIENTE", clave)
        pct = 100 * f["valor"] / r["total"] if r["total"] else 0
        lineas.append(f"  - {clave}: {_monto(f['valor'], r['moneda'])} "
                      f"({pct:.1f}%) · {f['n']} boletos")
    lineas.append(f"TOTAL: {_monto(r['total'], r['moneda'])}")
    return "\n".join(lineas)


@_cached_vocab
def _vocabulario_protegido() -> tuple[str, ...]:
    """Valores de NUESTROS catálogos (tipos de operación, mercados, segmentos,
    niveles 3). La aduana no los puede tachar: son vocabulario del negocio, no
    identidades — un tipo de operación que comparte una palabra con el nombre
    de algún cliente terminaba saliendo como CLIENTE_n en la tabla de
    resultados (incidente real 2026-07-21)."""
    from api.services import operaciones_sql as ops

    vals: list[str] = []
    for getter, clave in ((ops.ops_tipos_operacion, "tipos"),
                          (ops.ops_mercados, "mercados"),
                          (ops.ops_segmentos, "segmentos"),
                          (ops.ops_niveles3, "niveles3")):
        try:
            vals += [str(v) for v in (getter() or {}).get(clave) or [] if v]
        except Exception as e:
            logger.warning("asistente_tools: catálogo %s no disponible (%s)", clave, e)
    # los más largos primero: protege "Compras A3" antes que "Compras"
    return tuple(sorted(set(vals), key=len, reverse=True))


def quien_es(ficha: str, *, mapping: dict) -> str:
    """¿Cliente u operador? Se CONSULTA a los dos catálogos, no se adivina
    (caso real 2026-07-21: el asistente asumía 'operador' por el nombre y
    respondía cualquier cosa). La respuesta habla en fichas."""
    from api.services.copiloto.navegacion import clasificar_persona

    f = (ficha or "").strip()
    quien = clasificar_persona(f, mapping)
    es_cuenta, es_operador = bool(quien["cuenta"]), bool(quien["operador"])
    if es_cuenta and es_operador:
        return (f"AMBIGUO: {f} figura como CUENTA de cliente Y como OPERADOR "
                "comercial. Preguntale al usuario cuál de los dos quiere ver "
                "antes de traer ningún número.")
    if es_cuenta:
        return (f"{f} es una CUENTA de cliente → usá rendimiento_cuenta para su "
                "tenencia y resultado, o los consolidados si te piden lo operado.")
    if es_operador:
        return (f"{f} es un OPERADOR comercial (empleado) → usá los consolidados "
                "con por='operador' para ver su producción.")
    return (f"no encontré a {f} ni entre las cuentas ni entre los operadores — "
            "pedile al usuario el número de cuenta o el nombre como figura.")


# ── Dispatcher (lo invoca el loop de tools del gateway) ──────────────────────

def puede_control_comercial(usuario: str | None) -> bool:
    """¿Este usuario tiene el permiso de CONTROL COMERCIAL? Es un permiso POR
    USUARIO (`api/auth.py::require_control_comercial`) que NO mira la matriz de
    roles: tener el módulo `asistente` NO alcanza. Cualquier tool que exponga
    datos que la web gatea con ese permiso tiene que chequearlo acá, o el chat
    se vuelve una puerta trasera (hallazgo de la auditoría 2026-07-21).
    Fail-closed: sin usuario o ante error → False."""
    if not usuario:
        return False
    try:
        from core.roles import user_has_control_comercial
        return bool(user_has_control_comercial(usuario))
    except Exception as e:
        logger.warning("asistente_tools: no pude validar control comercial (%s)", e)
        return False


def ejecutar(nombre: str, args: dict, *, mapping: dict,
             usuario: str | None = None) -> str:
    """Ejecuta la tool pedida y devuelve el resultado YA pasado por la aduana
    (token-out): si algún dato colara una identidad, se tacha con las MISMAS
    fichas del chat antes de volver al LLM. Errores → mensaje corto, jamás
    excepción con datos adentro.

    `usuario` es QUIÉN pregunta: hace falta para los permisos que NO son por
    rol sino por usuario (ver puede_control_comercial). Sin él, una tool
    gateada no puede decidir y debe negarse."""
    try:
        if nombre == "resumen_mesa":
            crudo = resumen_mesa()
        elif nombre == "quien_es":
            crudo = quien_es(str(args.get("ficha", "")), mapping=mapping)
        elif nombre == "aum_historico":
            crudo = aum_historico(args, mapping=mapping)
        elif nombre == "posiciones_cuenta":
            crudo = posiciones_cuenta(args, mapping=mapping)
        elif nombre == "aum_composicion":
            crudo = aum_composicion(args, mapping=mapping)
        elif nombre == "cobros_futuros":
            crudo = cobros_futuros(args, mapping=mapping)
        elif nombre == "rendimiento_cuenta":
            crudo = rendimiento_cuenta(str(args.get("ficha_cuenta", "")), mapping=mapping)
        elif nombre == "volumen_operado":
            crudo = _consolidado("bruto", args, mapping=mapping)
        elif nombre == "aranceles_consolidado":
            crudo = _consolidado("arancel", args, mapping=mapping)
        else:
            return f"herramienta desconocida: {nombre}"
    except Exception as e:
        logger.warning("asistente_tools.%s falló: %s: %s", nombre, type(e).__name__, e)
        return "la herramienta falló — respondé con lo que tengas y avisá que faltó ese dato"
    # texto_generado: los números del resultado son agregados producidos por
    # el código (AuM, conteos, %) — se respetan; los NOMBRES se tachan igual.
    # `protegidos`: el vocabulario del negocio (tipos de operación, mercados,
    # segmentos) NO es identidad y no se puede tachar.
    limpio, _ = pii_gateway.tokenize(crudo, mapping, texto_generado=True,
                                     protegidos=_vocabulario_protegido())
    return limpio
