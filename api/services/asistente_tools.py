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
from collections.abc import Callable
from typing import NamedTuple

from api.cache import cached as _cached_vocab_factory
from core import pii_gateway
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_cached_vocab = _cached_vocab_factory(ttl=3600)

def _dimensiones() -> list[str]:
    """DERIVADAS del SQL (fuente única): agregar una dimensión es tocar UN
    archivo. Si la DB no responde al importar, el modelo igual arranca con el
    set base — pero jamás con uno que el SQL no acepte."""
    from api.services.operaciones_sql import dimensiones_consolidado
    return sorted(dimensiones_consolidado())


_DIMENSIONES = _dimensiones()

# Monedas en las que el asistente puede expresar plata. Un solo lugar: el enum
# que ve el modelo y la validación de entrada salen de acá, así no pueden
# divergir (el modelo pidiendo una que el código rebota a ARS en silencio).
_MONEDAS = ("ARS", "USD")


def _moneda_ok(v) -> str:
    m = str(v or "").upper()
    return m if m in _MONEDAS else _MONEDAS[0]


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
        "moneda": {"type": "string", "enum": list(_MONEDAS),
                   "description": "Moneda del resultado (default ARS). USD convierte "
                                  "CADA boleto con el tipo de cambio de SU día, no con "
                                  "una cotización de hoy — por eso sí se puede "
                                  "dolarizar un período largo."},
    },
    "required": ["desde", "hasta", "por"],
}


# ── Schemas para el function-calling (formato OpenAI) ────────────────────────

def _tool_serie() -> dict:
    """La serie histórica genérica también sirve al asistente de negocio (AuM
    por cartera, macro). Se importa del módulo dueño para no duplicar schema."""
    from api.services.copiloto.series import TOOL_SERIE
    return TOOL_SERIE


def _tools_comercial() -> list[dict]:
    """El bloque COMERCIAL vive en su propio módulo (`asistente_comercial`).
    Este archivo es el aggregator: sumar un dominio es un módulo nuevo + dos
    líneas acá, no 300 líneas más en un archivo de mil."""
    from api.services.asistente_comercial import TOOLS_COMERCIAL
    return TOOLS_COMERCIAL


TOOLS: list[dict] = [
    _tool_serie(),
    *_tools_comercial(),
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
                "eso lo da posiciones_cuenta, usá esa. "
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
            "name": "pulso_mesa",
            "description": (
                "Cómo viene el NEGOCIO por períodos (mes, año, semana…) CON la "
                "comparación contra el período anterior equivalente: volumen, "
                "comisiones y cuentas activas. Usala para '¿cómo venimos este "
                "mes?', '¿mejoramos contra el mes pasado?', 'el YTD'. Requiere "
                "un permiso especial: si el usuario no lo tiene, decíselo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "moneda": {"type": "string", "enum": list(_MONEDAS)},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flujo_de_fondos",
            "description": (
                "Plata que ENTRA y SALE de las cuentas de clientes (depósitos, "
                "extracciones, transferencias) en un período, agregada por moneda. "
                "Es la única forma de contestar si el patrimonio se movió por "
                "MERCADO o por PLATA NUEVA: el AuM sube tanto si el cliente "
                "deposita como si su bono valorizó, y esta herramienta separa "
                "una cosa de la otra. Usala para 'cuánta plata entró', 'hubo "
                "retiros', 'el AuM subió, ¿es aporte o mercado?'. No devuelve "
                "cuentas ni clientes: son totales."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "desde": {"type": "string",
                              "description": "Fecha inicial YYYY-MM-DD. Default: 1º del mes actual."},
                    "hasta": {"type": "string",
                              "description": "Fecha final YYYY-MM-DD. Default: hoy."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jobs_fallidos",
            "description": (
                "Salud de los procesos automáticos: cuáles vienen fallando y "
                "cuándo corrió cada uno. Usala para '¿está todo andando?', "
                "'¿corrió el proceso de X?', '¿por qué no se actualizó tal dato?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dias": {"type": "integer", "description": "Ventana (default 7)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "costo_ia",
            "description": (
                "Cuánto se está gastando en IA (por proveedor) y qué porcentaje "
                "del presupuesto diario va consumido. Usala para '¿cuánto nos "
                "cuesta la IA?', '¿cuánto del cupo llevamos?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dias": {"type": "integer", "description": "Ventana (default 14)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "controles_calidad_datos",
            "description": (
                "Qué está mal cargado HOY: las anomalías de datos vigentes "
                "(cuentas sin segmentar, títulos sin cartera, bonos sin tasa…). "
                "Usala cuando pregunten por qué un total no cuadra o qué hay que "
                "corregir. Devuelve el CONTEO por control, no los casos."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
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
            "name": "aum_variacion",
            "description": (
                "POR QUIÉN se movió el AuM entre dos cierres: qué cuentas "
                "sumaron, cuáles restaron, cuántas entraron y cuántas se "
                "fueron, y qué tan concentrado estuvo el movimiento. Es la "
                "pregunta que sigue a '¿cuánto subió el AuM?': si subió, ¿fue "
                "una cuenta grande o todo el mundo? Las cuentas vuelven como "
                "referencias CLIENTE_n. Ojo: mide el VALOR de la tenencia, así "
                "que mezcla efecto mercado con aportes — si quieren saber si "
                "entró plata nueva, eso es flujo_de_fondos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "desde": {"type": "string",
                              "description": "Cierre inicial (YYYY-MM-DD). Default: fin "
                                             "del mes anterior."},
                    "hasta": {"type": "string",
                              "description": "Cierre final (YYYY-MM-DD). Default: el último."},
                    "moneda": {"type": "string", "enum": list(_MONEDAS)},
                    "top": {"type": "integer",
                            "description": "Cuántas cuentas mostrar de cada lado (default 8)."},
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
                "máximo sobre los cierres diarios. Es la ÚNICA que acota a un "
                "CLIENTE (por su ficha). Usala para '¿cuál fue el AuM promedio de "
                "FCI en junio?', 'la mediana del mes', 'cómo evolucionó el AuM de "
                "CLIENTE_1'. Se puede acotar a una cartera (HD, DL, ARS, FCI…) y/o "
                "a un cliente. OJO: resumen_mesa da el AuM de HOY; esta da la "
                "HISTORIA con su distribución. Si preguntan si el AuM está ALTO o "
                "BAJO contra su historia (percentil/z), eso es serie_historica."
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
                "Aranceles facturados consolidados por una dimensión en un "
                "período: por mercado, tipo de operación, segmento o título. Se "
                "facturan en pesos, pero acepta moneda USD y convierte cada "
                "boleto con el TC de su día (igual que volumen_operado). Usala "
                "para 'lo facturado', consolidados de aranceles. Incluye el "
                "arancel de caución que vive en los cierres (regla de la mesa, "
                "ya aplicada)."
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
    # clave VERIFICADA (valuaciones_sql.posiciones_actuales): `posiciones`
    filas = [p for p in (r.get("posiciones") or [])
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


def aum_variacion(args: dict, *, mapping: dict) -> str:
    """POR QUIÉN se movió el AuM entre dos cierres: quién sumó, quién restó,
    cuántas cuentas entraron y cuántas se fueron. Es la pregunta que sigue a
    'el AuM subió 8%': ¿es una cuenta grande o es todo el mundo?

    Las cuentas salen FICHADAS (cada fila trae la denominación real)."""
    from datetime import date, timedelta

    from api.services import portfolio_sql

    hasta = str(args.get("hasta") or "").strip() or _fecha_snapshot()
    if not hasta:
        return "sin snapshots de tenencia todavía"
    desde = str(args.get("desde") or "").strip()
    if not desde:
        # default: contra el cierre del mes anterior — el corte natural del
        # negocio. total_diff resuelve el snapshot real más cercano.
        d = date.fromisoformat(hasta).replace(day=1) - timedelta(days=1)
        desde = d.isoformat()
    moneda = _moneda_ok(args.get("moneda"))
    r = portfolio_sql.total_diff(fecha_actual=hasta, fecha_anterior=desde,
                                 moneda=moneda) or {}
    filas = r.get("filas") or []
    if not filas:
        return f"no hay tenencia comparable entre {desde} y {hasta}"
    if r.get("mep_missing_actual") or r.get("mep_missing_anterior"):
        return ("falta el tipo de cambio de alguna de las dos fechas — pedí el "
                "dato en pesos o cambiá las fechas")
    total = float(r.get("total_diff") or 0)
    lineas = [f"[variación del AuM entre {r.get('fecha_anterior_resuelta')} y "
              f"{r.get('fecha_actual_resuelta')} — {moneda}] "
              f"NETO {_monto(total, moneda)} · {int(r.get('n_total') or 0)} cuentas "
              f"({int(r.get('n_nuevas') or 0)} nuevas, "
              f"{int(r.get('n_cerradas') or 0)} sin tenencia al final)"]
    top = max(1, min(int(args.get("top") or 8), 20))
    suben = [f for f in filas if f["diff"] > 0][:top]
    bajan = [f for f in reversed([f for f in filas if f["diff"] < 0])][:top]
    for etiqueta, grupo in (("SUMARON", suben), ("RESTARON", bajan)):
        if not grupo:
            continue
        lineas.append(f"{etiqueta}:")
        for f in grupo:
            ficha = pii_gateway.asignar_ficha(mapping, "CLIENTE", f.get("cuenta") or "?")
            marca = " (nueva)" if f.get("es_nueva") else (
                " (se fue)" if f.get("es_cerrada") else "")
            lineas.append(f"  - {ficha}{marca}: {_monto(f['diff'], moneda)}")
    concentracion = sum(abs(f["diff"]) for f in filas[:5])
    bruto = sum(abs(f["diff"]) for f in filas)
    if bruto:
        lineas.append(f"CONCENTRACIÓN: las 5 cuentas que más se movieron explican el "
                      f"{100 * concentracion / bruto:.1f}% del movimiento total.")
    return "\n".join(lineas)


def aum_composicion(args: dict, *, mapping: dict) -> str:
    """CÓMO está repartido el AuM de la mesa: por cartera (pesos / hard dollar
    / FCI…) en el último cierre. Agregado — no emite cuentas ni clientes."""
    from api.services import portfolio_sql

    fecha = str(args.get("fecha") or "").strip() or _fecha_snapshot()
    if not fecha:
        return "sin snapshots de tenencia todavía"
    r = portfolio_sql.total_snapshot(fecha=fecha, moneda="ARS") or {}
    # clave VERIFICADA (portfolio_sql.total_snapshot): `docs`. Antes decía
    # `rows or por_cartera` — ninguna de las dos existe, así que la tool
    # contestaba "no hay tenencia" SIEMPRE y el modelo improvisaba.
    acc: dict[str, float] = {}
    for f in r.get("docs") or []:
        # se agrega por cartera y se DESCARTA cuenta/id_cuenta (vienen por fila)
        cart = str(f.get("cartera") or "OTROS")
        acc[cart] = acc.get(cart, 0.0) + float(f.get("valuacion") or 0)
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


def pulso_mesa(args: dict, *, usuario: str | None) -> str:
    """Totales del negocio por períodos fijos (mes, YTD…) CON la variación
    contra el período anterior equivalente. ⚠ GATEADO: la web protege esto con
    Control Comercial, un permiso POR USUARIO — sin el chequeo, el chat sería
    una puerta trasera (hallazgo de la auditoría 2026-07-21)."""
    from core.roles import LABEL_CONTROL_COMERCIAL

    if not puede_control_comercial(usuario):
        return (f"ese dato requiere el permiso de {LABEL_CONTROL_COMERCIAL}, que "
                "este usuario no tiene — decíselo y no muestres ningún número")
    from api.services import control_comercial_sql

    moneda = _moneda_ok(args.get("moneda"))
    r = control_comercial_sql.datos_totales_alyc(moneda=moneda) or {}
    filas = r.get("filas") or []
    if not filas:
        return "sin datos de totales de la mesa"
    # Shape VERIFICADO en control_comercial_sql.datos_totales_alyc: cada fila
    # es {periodo, clientes_activos, volumen, comisiones, <campo>_pct}. Nada
    # de adivinar nombres de campos con fallbacks.
    campos = (("volumen", "volumen", True), ("comisiones", "comisiones", True),
              ("clientes_activos", "cuentas activas", False))
    lineas = [f"[pulso de la mesa — {moneda}, cada período contra el anterior "
              f"equivalente · ancla {r.get('ancla', 's/f')}]"]
    for f in filas:
        partes = []
        for clave, etiqueta, es_plata in campos:
            v = f.get(clave)
            if v is None:
                continue
            txt = _monto(float(v), moneda) if es_plata else f"{int(v)}"
            pct = f.get(f"{clave}_pct")
            if pct is not None:
                txt += f" ({float(pct):+.1f}%)"
            partes.append(f"{etiqueta} {txt}")
        if partes:
            lineas.append(f"  - {f.get('periodo', '?')}: " + " · ".join(partes))
    return "\n".join(lineas)


def flujo_de_fondos(args: dict, *, mapping: dict) -> str:
    """QUÉ PLATA ENTRA Y SALE (depósitos, extracciones, transferencias). Es la
    pregunta que el AuM no responde: "subió 8% — ¿es mercado o plata nueva?".
    Agregado por moneda; las cuentas NO se emiten."""
    from datetime import UTC, datetime, timedelta

    from api.services import cashflow_sql

    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()
    desde = str(args.get("desde") or "").strip() or hoy.replace(day=1).isoformat()
    hasta = str(args.get("hasta") or "").strip() or hoy.isoformat()
    r = cashflow_sql.flujos_resumen(desde=desde, hasta=hasta) or {}
    # clave VERIFICADA (cashflow_sql.flujos_resumen): siempre {"filas": [...]}.
    filas = r.get("filas") or []
    if not filas:
        return f"no hay movimientos de fondos entre {desde} y {hasta}"
    por_unidad: dict[str, dict] = {}
    for f in filas:
        e = por_unidad.setdefault(str(f.get("unidad") or "?"),
                                  {"entradas": 0.0, "salidas": 0.0, "n": 0})
        e["entradas"] += float(f.get("entradas") or 0)
        e["salidas"] += float(f.get("salidas") or 0)
        e["n"] += int(f.get("n") or 0)
    lineas = [f"[flujo de fondos de clientes — {desde} a {hasta}]"]
    for unidad, e in sorted(por_unidad.items(), key=lambda x: -abs(x[1]["entradas"])):
        neto = e["entradas"] + e["salidas"]      # salidas ya vienen negativas
        lineas.append(
            f"  - {unidad}: entraron {_monto(e['entradas'], unidad)} · salieron "
            f"{_monto(abs(e['salidas']), unidad)} · NETO {_monto(neto, unidad)} "
            f"({e['n']} movimientos)")
    lineas.append("El neto es plata NUEVA (o retirada): no confundir con la "
                  "variación del AuM, que además incluye el efecto del mercado.")
    return "\n".join(lineas)


def jobs_fallidos(args: dict) -> str:
    """Salud de los procesos automáticos: qué viene fallando y cuándo corrió
    cada uno por última vez."""
    from datetime import UTC, datetime, timedelta

    from api.services import manager_infra_sql

    dias = max(1, min(int(args.get("dias") or 7), 90))
    desde = datetime.now(UTC) - timedelta(days=dias)
    filas = manager_infra_sql.jobs_history_stats_sql(desde) or []
    if not filas:
        return f"no hay corridas de procesos en los últimos {dias} días"
    malos = [f for f in filas if int(f.get("error") or 0) > 0
             or f.get("last_status") == "error"]
    lineas = [f"[procesos automáticos — últimos {dias} días, {len(filas)} tipos]"]
    if not malos:
        lineas.append("  todo OK: ningún proceso con errores en la ventana")
    for f in sorted(malos, key=lambda x: -int(x.get("error") or 0)):
        lineas.append(
            f"  - {f.get('tipo')}: {f.get('error')} errores de {f.get('total')} corridas"
            f" · última {f.get('last_run')} ({f.get('last_status')})")
    return "\n".join(lineas)


def costo_ia(args: dict) -> str:
    """Cuánto se está gastando en IA y cuánto del presupuesto va consumido."""
    from api.services import ia_obs

    dias = max(1, min(int(args.get("dias") or 14), 90))
    r = ia_obs.observabilidad(dias=dias, limit=1) or {}
    hoy = r.get("hoy") or {}
    lineas = [
        f"[consumo de IA — hoy y últimos {dias} días]",
        f"  hoy: {int(hoy.get('tokens_total') or 0):,} tokens"
        + (f" ({hoy['presupuesto_pct']}% del presupuesto diario)"
           if hoy.get("presupuesto_pct") is not None else "")
        + f" · {int(hoy.get('llamadas') or 0)} consultas"
        + (f" · {int(hoy['errores'])} con error" if int(hoy.get("errores") or 0) else ""),
    ]
    for p in r.get("por_proveedor") or []:
        if not p.get("costo_estimable"):
            continue
        lineas.append(f"  - {p['proveedor']}: USD {p['costo_usd']:.4f} en la ventana "
                      f"(hoy USD {p['costo_usd_hoy']:.4f}) · "
                      f"{int(p['llamadas'])} llamadas"
                      + (" · no entrena con nuestros datos" if p.get("no_entrena") else ""))
    return "\n".join(lineas)


def controles_calidad_datos(args: dict) -> str:
    """Qué está mal cargado HOY: las anomalías de datos vigentes. Es lo que
    explica por qué un consolidado no cuadra."""
    from api.services import controles_sql

    r = controles_sql.listar_controles() or {}
    # shape VERIFICADO: {controles: {<id>: {activos[], resueltos[]}}, totales, …}
    # Antes se iteraba el dict de PRIMER nivel (cuyas claves son "controles" y
    # "totales", no controles) → la tool decía "todo limpio" siempre, incluso
    # con anomalías abiertas: el peor error posible en una tool de calidad.
    activos = {cid: g["activos"] for cid, g in (r.get("controles") or {}).items()
               if g.get("activos")}
    if not activos:
        return ("no hay anomalías de datos activas — todo limpio"
                + (f" (último chequeo {r['ultima_corrida']})"
                   if r.get("ultima_corrida") else ""))
    lineas = [f"[calidad de datos — {len(activos)} controles con anomalías activas]"]
    for control, items in sorted(activos.items(), key=lambda x: -len(x[1])):
        # `detalle` puede traer denominaciones → solo se emite el CONTEO.
        mas_viejo = min((i["desde"] for i in items if i.get("desde")), default=None)
        lineas.append(f"  - {control}: {len(items)} casos"
                      + (f" (el más viejo desde {mas_viejo})" if mas_viejo else ""))
    lineas.append("Detalle por caso: Manager → OBSERVABILIDAD → CONTROLES.")
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
    moneda = _moneda_ok(args.get("moneda"))
    r = operaciones_sql.ops_consolidado(
        metrica=metrica,
        desde=str(args.get("desde", "")), hasta=str(args.get("hasta", "")),
        por=str(args.get("por", "mercado")),
        mercado=(str(args["mercado"]) if args.get("mercado") else None),
        excluir_segmento=(str(args["excluir_segmento"])
                          if args.get("excluir_segmento") else None),
        operador_sel=operador_sel, denominacion=denominacion,
        cartera_filtro=(str(args["cartera"]) if args.get("cartera") else None),
        moneda=moneda,
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
    from core.roles import LABEL_CONTROL_COMERCIAL

    # Etiquetas del propio sistema (nombres de permisos, de vistas): no son
    # identidades y salen del módulo dueño, no escritas de nuevo acá.
    vals: list[str] = [LABEL_CONTROL_COMERCIAL]
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
#
# REGISTRO ÚNICO nombre → handler. Antes esto era una cadena de if/elif que
# podía DESINCRONIZARSE del schema en silencio: una tool declarada en TOOLS
# pero sin rama caía en "herramienta desconocida" y el modelo se quedaba sin
# entender por qué. Ahora hay un solo lugar y un test congela que el set de
# schemas y el de handlers sean idénticos.
#
# El handler recibe siempre (args, ctx) — ctx trae lo transversal (el mapping
# de la aduana y quién pregunta), así sumar una tool no cambia ninguna firma.

class _Ctx(NamedTuple):
    mapping: dict
    usuario: str | None
    pregunta: str | None = None    # lo que escribió el usuario (contexto del buzón)


def _serie_handler(args: dict, _ctx: _Ctx) -> str:
    from api.services.copiloto.series import ejecutar_serie
    return ejecutar_serie("serie_historica", args)


_HANDLERS: dict[str, Callable[[dict, _Ctx], str]] = {
    "resumen_mesa":            lambda a, c: resumen_mesa(),
    "quien_es":                lambda a, c: quien_es(str(a.get("ficha", "")),
                                                     mapping=c.mapping),
    "rendimiento_cuenta":      lambda a, c: rendimiento_cuenta(
                                   str(a.get("ficha_cuenta", "")), mapping=c.mapping),
    "posiciones_cuenta":       lambda a, c: posiciones_cuenta(a, mapping=c.mapping),
    "aum_composicion":         lambda a, c: aum_composicion(a, mapping=c.mapping),
    "aum_historico":           lambda a, c: aum_historico(a, mapping=c.mapping),
    "cobros_futuros":          lambda a, c: cobros_futuros(a, mapping=c.mapping),
    "flujo_de_fondos":         lambda a, c: flujo_de_fondos(a, mapping=c.mapping),
    "pulso_mesa":              lambda a, c: pulso_mesa(a, usuario=c.usuario),
    "jobs_fallidos":           lambda a, c: jobs_fallidos(a),
    "costo_ia":                lambda a, c: costo_ia(a),
    "controles_calidad_datos": lambda a, c: controles_calidad_datos(a),
    "serie_historica":         _serie_handler,
    "volumen_operado":         lambda a, c: _consolidado("bruto", a, mapping=c.mapping),
    "aranceles_consolidado":   lambda a, c: _consolidado("arancel", a, mapping=c.mapping),
    "aum_variacion":           lambda a, c: aum_variacion(a, mapping=c.mapping),
}


def _enganchar_dominios() -> None:
    """Engancha los handlers de los módulos de dominio. Firma común
    `(args, *, mapping, usuario)` → así un dominio nuevo se suma con una línea
    y sin que el aggregator sepa nada de lo que hace adentro."""
    from api.services.asistente_comercial import HANDLERS_COMERCIAL

    for nombre, fn in HANDLERS_COMERCIAL.items():
        _HANDLERS[nombre] = (
            lambda f: lambda a, c: f(a, mapping=c.mapping, usuario=c.usuario)
        )(fn)


_enganchar_dominios()


def herramientas_declaradas() -> set[str]:
    """Los nombres que el modelo ve en los schemas — para el test de contrato."""
    return {t["function"]["name"] for t in TOOLS}


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
             usuario: str | None = None, pregunta: str | None = None) -> str:
    """Ejecuta la tool pedida y devuelve el resultado YA pasado por la aduana
    (token-out): si algún dato colara una identidad, se tacha con las MISMAS
    fichas del chat antes de volver al LLM. Errores → mensaje corto, jamás
    excepción con datos adentro.

    `usuario` es QUIÉN pregunta: hace falta para los permisos que NO son por
    rol sino por usuario (ver puede_control_comercial). Sin él, una tool
    gateada no puede decidir y debe negarse."""
    handler = _HANDLERS.get(nombre)
    if handler is None:
        return f"herramienta desconocida: {nombre}"
    try:
        crudo = handler(args, _Ctx(mapping=mapping, usuario=usuario,
                                   pregunta=pregunta))
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
