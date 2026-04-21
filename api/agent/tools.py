"""Tools expuestas al LLM (read-only, SOLO data de mercado).

===============================================================================
POLÍTICA DE DATOS — LEER ANTES DE AGREGAR UNA TOOL
===============================================================================

El modelo usado actualmente (Gemini Flash en FREE TIER) **usa los prompts y
respuestas para entrenar** sus próximos modelos. Por eso acá SOLO exponemos
endpoints con data pública de mercado (cotizaciones, historicos, metadata
de títulos). NUNCA data de clientes.

PROHIBIDO exponer al modelo (blocked en dispatch() más abajo):
    /api/portfolio/*   → carteras, AuM (data de clientes)
    /api/operaciones/* → operaciones de mesa (clientes + montos)
    /api/cuentas/*     → accionistas, contrapartes (partes)
    /api/manager/*     → operaciones internas

Cuando se active billing en Google Cloud (Gemini deja de entrenar), se levanta
el bloqueo editando BLOCKED_PATH_PREFIXES abajo y agregando tools nuevas.

===============================================================================
"""
from __future__ import annotations

import json
from typing import Any

import requests

from api.agent.invariants import run_invariants
from api.agent.service_registry import get_service_handler
from api.agent.ticker_catalog import did_you_mean
from api.agent.tool_metadata import compute_meta
from config import API_KEY

# Keys que típicamente contienen un ticker en los args del modelo.
_TICKER_ARG_KEYS = ("instrumento", "ticker", "ticker_corto", "symbol")

API_BASE = "http://127.0.0.1:8000"
TOOL_TIMEOUT = 15

# Prefijos de rutas BLOQUEADAS para el LLM. dispatch() se niega a llamarlas
# aunque figuren en TOOLS. Safety belt por si alguien agrega una tool sin pensar.
BLOCKED_PATH_PREFIXES = (
    "/api/portfolio",
    "/api/operaciones",
    "/api/cuentas",
    "/api/manager",
)

# Gemini usa tipos en MAYÚSCULA (Schema type enum: STRING, INTEGER, NUMBER,
# BOOLEAN, ARRAY, OBJECT).

TOOLS: list[dict[str, Any]] = [
    # ─── GRUPO 1: cotizaciones y mercado (data pública) ──────────────────────
    {
        "name": "cotizacion_renta_fija",
        "description": "Cotización de un bono/letra: book, VWAP, último, min/max/cierre. Acepta ticker corto o full ROFEX.",
        "endpoint": "/api/cotizaciones/renta-fija",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instrumento": {"type": "STRING", "description": "Ticker (ej 'TX26')."},
            },
            "required": ["instrumento"],
        },
    },
    {
        "name": "cotizacion_opciones",
        "description": "Opciones GGAL: bid/offer, greeks, IV, strike, vence.",
        "endpoint": "/api/cotizaciones/opciones",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instrumento": {"type": "STRING", "description": "Ticker opción."},
                "tipo": {"type": "STRING", "description": "CALL o PUT."},
            },
        },
    },
    {
        "name": "forwards_por_curva",
        "description": "Matriz NxN de forwards implícitos entre bonos de una curva.",
        "endpoint": "/api/cotizaciones/forwards",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "curva": {"type": "STRING", "description": "tasa_fija o cer."},
            },
            "required": ["curva"],
        },
    },
    {
        "name": "breakevens_actuales",
        "description": "Breakevens inflación mensual implícita por pareo Lecap↔CER.",
        "endpoint": "/api/cotizaciones/breakevens",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "serie_cer",
        "description": "Serie CER (BCRA).",
        "endpoint": "/api/cotizaciones/cer",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "desde": {"type": "STRING"},
                "hasta": {"type": "STRING"},
            },
        },
    },
    {
        "name": "serie_badlar",
        "description": "Serie BADLAR privados (BCRA).",
        "endpoint": "/api/cotizaciones/badlar",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "desde": {"type": "STRING"},
                "hasta": {"type": "STRING"},
            },
        },
    },
    {
        "name": "serie_dolar_a3500",
        "description": "Serie dólar A3500 oficial BCRA.",
        "endpoint": "/api/cotizaciones/dolar",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "desde": {"type": "STRING"},
                "hasta": {"type": "STRING"},
            },
        },
    },
    {
        "name": "mep_actual",
        "description": "Último snapshot dólar MEP.",
        "endpoint": "/api/cotizaciones/mep",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "caucion_actual",
        "description": (
            "Snapshot live de la caución corta (típicamente 1 día, viernes 3 días). "
            "Devuelve la TNA last/bid/offer/closing por moneda. Sin filtro trae "
            "ambas monedas. La 'caución' es la tasa libre de riesgo del peso a "
            "1 día — todo el carry trade y la valuación de Lecaps cortas se "
            "prices off de acá."
        ),
        "endpoint": "/api/cotizaciones/caucion",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "moneda": {"type": "STRING", "description": "ARS o USD; vacío = ambas."},
            },
        },
    },
    {
        "name": "caucion_historica",
        "description": "Cierre diario histórico de la caución por moneda.",
        "endpoint": "/api/cotizaciones/historico/caucion",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "moneda": {"type": "STRING"},
                "desde":  {"type": "STRING"},
                "hasta":  {"type": "STRING"},
            },
        },
    },
    {
        "name": "futuros_dlr",
        "description": (
            "Curva entera de futuros del Dólar A3500 (outrights DLR/MMMYY). "
            "Devuelve para cada vencimiento: bid/offer/last + tasa implícita "
            "anualizada (TNA) calculada vs MEP spot. Útil para 'cuánta "
            "devaluación pricea el mercado a 3/6/12 meses', 'la curva está "
            "más empinada hoy?', arbitraje vs caución/lecaps."
        ),
        "endpoint": "/api/cotizaciones/futuros-dlr",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "futuros_dlr_historico",
        "description": "Cierre histórico de futuros DLR. Filtrable por ticker y rango.",
        "endpoint": "/api/cotizaciones/historico/futuros-dlr",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ticker": {"type": "STRING", "description": "Ej: DLR/JUN26"},
                "desde":  {"type": "STRING"},
                "hasta":  {"type": "STRING"},
            },
        },
    },
    {
        "name": "historico_trades",
        "description": "Trades de un instrumento, últimos 15 días (con TEA/TEM/duration/paridad).",
        "endpoint": "/api/cotizaciones/historico/trades",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instrumento": {"type": "STRING"},
            },
            "required": ["instrumento"],
        },
    },
    {
        "name": "historico_forwards",
        "description": "Evolución diaria forwards por curva.",
        "endpoint": "/api/cotizaciones/historico/forwards",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "curva": {"type": "STRING"},
                "desde": {"type": "STRING"},
                "hasta": {"type": "STRING"},
            },
            "required": ["curva"],
        },
    },
    {
        "name": "historico_breakevens",
        "description": "Evolución diaria breakevens Lecap↔CER.",
        "endpoint": "/api/cotizaciones/historico/breakevens",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "desde": {"type": "STRING"},
                "hasta": {"type": "STRING"},
            },
        },
    },
    {
        "name": "historico_mep",
        "description": "Serie histórica MEP.",
        "endpoint": "/api/cotizaciones/historico/mep",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "desde": {"type": "STRING"},
                "hasta": {"type": "STRING"},
            },
        },
    },
    {
        "name": "historico_curva",
        "description": "Serie diaria de TODOS los bonos de UNA curva (para gráficos de curva completa).",
        "endpoint": "/api/cotizaciones/historico/curva",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "curva": {"type": "STRING"},
            },
            "required": ["curva"],
        },
    },

    # ─── GRUPO 2: metadata de títulos ─────────────────────────────────────
    {
        "name": "metadata_activos",
        "description": "Metadata bonos: emisor, clase, calificación, vencimiento.",
        "endpoint": "/api/titulos/assets",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ticker":        {"type": "STRING"},
                "emisor":        {"type": "STRING"},
                "clase_activo":  {"type": "STRING"},
            },
        },
    },
    {
        "name": "flujos_titulo",
        "description": "Cronograma de flujos de un bono (cupones y amortizaciones).",
        "endpoint": "/api/titulos/flujos",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ticker":        {"type": "STRING"},
                "curva":         {"type": "STRING"},
                "moneda_flujo":  {"type": "STRING"},
            },
        },
    },
    {
        "name": "cotizacion_equity",
        "description": "Cotización live de acciones/ETFs/índices (ADRs AR: GGAL, YPF, BMA, etc; ETFs: SPY, QQQ, GLD, USO). Fuente: Finnhub, último snapshot cacheado. NO para bonos AR — usar cotizacion_renta_fija.",
        "endpoint": "/api/market/quotes",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "symbols": {"type": "STRING", "description": "CSV de tickers, ej 'GGAL,YPF,SPY'."},
            },
        },
    },
    {
        "name": "calendario_economico",
        "description": "Eventos macro próximos (FOMC, CPI US, jobs, etc). USAR para 'qué datos macro salen esta semana', 'cuándo es la próxima Fed'. Cubre todo el mundo (US, EU, AR si publican).",
        "endpoint": "/api/market/calendar/economic",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "desde":       {"type": "STRING", "description": "YYYY-MM-DD; default hoy."},
                "hasta":       {"type": "STRING", "description": "YYYY-MM-DD; default +30 días."},
                "importancia": {"type": "INTEGER", "description": "0 todas, 1 low, 2 medium, 3 high (solo importantes)."},
                "country":     {"type": "STRING", "description": "Código ISO: US, EU, AR, BR, MX."},
            },
        },
    },

    # ─── Tier 1: analítica core (framework de benchmarks dinámicos) ───────
    {
        "name": "listar_curva",
        "description": (
            "Lista TODOS los bonos de una curva con metadata enriquecida "
            "(precio, TEA, TEM, paridad, duration, volumen del día, vencimiento). "
            "USAR siempre que necesites la curva entera o filtrar por horizonte "
            "(ej: 'Boncer ≤ 6m', 'Lecap largas'). Evita múltiples calls a "
            "cotizacion_renta_fija uno por uno."
        ),
        "endpoint": "/api/analitica/listar-curva",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "curva": {
                    "type": "STRING",
                    "description": "Una de: cer, tasa_fija, tamar, soberanos, dolar_linked.",
                },
                "ordenar_por": {
                    "type": "STRING",
                    "description": "vencimiento (default) | volumen_dia | tea | duration.",
                },
                "vencimiento_min_meses": {
                    "type": "NUMBER",
                    "description": "Filtrar a bonos con >= N meses al vencimiento.",
                },
                "vencimiento_max_meses": {
                    "type": "NUMBER",
                    "description": "Filtrar a bonos con <= N meses al vencimiento.",
                },
                "limit": {"type": "INTEGER", "description": "Top N después de ordenar."},
            },
            "required": ["curva"],
        },
    },
    {
        "name": "obtener_serie_macro",
        "description": (
            "Devuelve valor actual + serie histórica + stats (percentil, z-score, "
            "clasificación) de una variable macro o una serie por ticker. "
            "Variables macro: tamar, cer, dolar, badlar, mep, ccl, canje, ipc, "
            "ipim, riesgo_pais, repo, rem_inflacion. "
            "Series por ticker: '<TICKER>.<CAMPO>' donde CAMPO ∈ {TEA, TEM, "
            "paridad, duration, price} — ej 'TX26.TEM', 'GD30.paridad'. "
            "USAR para análisis 'está alto o bajo vs historia' — reemplaza a "
            "obtener_xxx_actual + obtener_xxx_historia."
        ),
        "endpoint": "/api/analitica/serie-macro",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "variable": {"type": "STRING", "description": "Ver descripción para valores válidos."},
                "ventana_dias": {"type": "INTEGER", "description": "Default 90."},
            },
            "required": ["variable"],
        },
    },
    {
        "name": "clasificar_nivel",
        "description": (
            "Wrapper compacto de obtener_serie_macro: devuelve SOLO clasificación "
            "+ percentil + z-score, sin la serie entera. USAR cuando alcanza con "
            "la etiqueta ('TAMAR en mínimos', 'canje en percentil 75') para no "
            "gastar tokens."
        ),
        "endpoint": "/api/analitica/clasificar-nivel",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "variable": {"type": "STRING"},
                "ventana_dias": {"type": "INTEGER", "description": "Default 90."},
            },
            "required": ["variable"],
        },
    },

    # ─── Contexto analítico on-demand (local, sin HTTP) ───────────────────
    {
        "name": "consultar_framework_analitico",
        "description": "Framework de 4 capas + house view + señales + rotaciones. USAR para preguntas estratégicas (view de mercado, CER vs Lecap, qué rotar, HD vs DL). NO para lookup.",
        "endpoint": "__local__:framework",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "consultar_catalogo_estrategias",
        "description": "Fórmulas y construcción de estrategias (barbell, butterfly, covered call, carry, etc). USAR cuando piden armar una estructura específica. NO para lookup.",
        "endpoint": "__local__:catalogo",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tema": {
                    "type": "STRING",
                    "description": "Temas: mapa-datos, fi-curva, fi-butterfly, fi-carry, inflation, fx-carry, options-bullish, options-bearish, options-neutral, options-vol, options-notas, macro, no-aplican, reglas-asistente.",
                },
            },
            "required": ["tema"],
        },
    },
]

TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


def _is_blocked(endpoint: str) -> bool:
    # Tools locales (prefijo __local__:) nunca están bloqueadas.
    if endpoint.startswith("__local__:"):
        return False
    return any(endpoint.startswith(p) for p in BLOCKED_PATH_PREFIXES)


def gemini_tool_declarations() -> list[dict[str, Any]]:
    """Formato que espera Gemini: lista con una entrada `function_declarations`.

    Filtra automáticamente cualquier tool que apunte a ruta bloqueada, para que
    ni siquiera aparezca en el menú que ve el modelo.
    """
    decls = [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in TOOLS
        if not _is_blocked(t["endpoint"])
    ]
    return [{"function_declarations": decls}]


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta la tool pedida por el LLM.

    Double-check: aunque el modelo solo ve las tools no-bloqueadas (por
    gemini_tool_declarations), acá verificamos de nuevo antes de pegarle al
    endpoint. Belt-and-suspenders.
    """
    tool = TOOL_BY_NAME.get(name)
    if tool is None:
        return {"ok": False, "error": f"tool '{name}' no existe"}

    endpoint = tool["endpoint"]

    # Tools locales (no HTTP). Se identifican por prefijo "__local__:".
    if endpoint == "__local__:catalogo":
        from api.agent.estrategias import get_seccion
        tema = (args or {}).get("tema", "")
        content = get_seccion(tema)
        return {"ok": True, "data": {"tema": tema, "content": content}}

    if endpoint == "__local__:framework":
        from api.agent.estrategia import load_estrategia
        content = load_estrategia() or "(framework no disponible — archivo estrategia.md faltante)"
        return {"ok": True, "data": {"content": content}}

    if _is_blocked(endpoint):
        return {
            "ok": False,
            "error": (
                "ruta bloqueada por política de datos (free tier del modelo). "
                "No tengo acceso a información de clientes, carteras, AuM, "
                "operaciones, accionistas ni contrapartes."
            ),
        }

    # Service registry primero: si el endpoint tiene una función Python
    # registrada, la llamamos directo (sin loopback HTTP). Ver
    # api/agent/service_registry.py. Ganancia ~100-300ms por call.
    handler = get_service_handler(endpoint)
    if handler is not None:
        params = {k: v for k, v in (args or {}).items() if v not in (None, "")}
        try:
            data = handler(**params)
        except TypeError as e:
            # Arg mismatch: el LLM pasó un kwarg que la función no acepta
            return {"ok": False, "error": f"args inválidos para {name}: {e}"}
        except Exception as e:
            return {"ok": False, "error": f"error del service: {e}"}
        return _process_service_output(data, endpoint, args)

    # Fallback HTTP — para endpoints aún no migrados al service registry
    url = API_BASE + endpoint
    params = {k: v for k, v in (args or {}).items() if v not in (None, "")}

    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=TOOL_TIMEOUT)
    except requests.RequestException as e:
        return {"ok": False, "error": f"HTTP error: {e}"}

    if resp.status_code != 200:
        return {
            "ok": False,
            "error": f"endpoint {tool['endpoint']} devolvió {resp.status_code}",
            "detail": resp.text[:400],
        }

    try:
        data = resp.json()
    except json.JSONDecodeError:
        return {"ok": False, "error": "respuesta no es JSON", "raw": resp.text[:400]}

    return _process_service_output(data, endpoint, args)


def _process_service_output(
    data: Any,
    endpoint: str,
    args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Post-procesa el output de una tool (service o HTTP) uniformemente.

    - Si data viene vacía Y hay ticker en args → devuelve did_you_mean.
    - Calcula `_meta` con staleness.
    - Corre invariantes del dominio y agrega warnings si corresponde.
    """
    if _is_empty_response(data):
        ticker = _extract_ticker_from_args(args or {})
        if ticker:
            suggestions = did_you_mean(ticker)
            return {
                "ok": False,
                "error": f"no se encontraron datos para '{ticker}'",
                "did_you_mean": suggestions,
                "hint": (
                    f"Probá con uno de estos tickers: {', '.join(suggestions)}"
                    if suggestions
                    else "Revisá el ticker, puede que no exista o no esté cargado."
                ),
            }

    meta = compute_meta(data, source=endpoint)
    warnings = run_invariants(data)
    if warnings:
        meta["warnings"] = warnings

    return {
        "ok": True,
        "data": data,
        "_meta": meta,
    }


def _extract_ticker_from_args(args: dict[str, Any]) -> str | None:
    """Busca el primer valor no-vacío en args cuya key sea un ticker."""
    for k in _TICKER_ARG_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _is_empty_response(data: Any) -> bool:
    """Detecta responses 'vacías' típicas de nuestra API (ticker no encontrado)."""
    if data is None:
        return True
    if isinstance(data, list):
        return len(data) == 0
    if isinstance(data, dict):
        # Patrones comunes: {"resultados": []}, {"items": []}, {"data": []}, {}
        if not data:
            return True
        for key in ("resultados", "items", "data", "values", "rows"):
            inner = data.get(key)
            if isinstance(inner, list) and len(inner) == 0:
                return True
    return False
