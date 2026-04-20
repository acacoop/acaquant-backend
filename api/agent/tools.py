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

    # Si la response viene vacía Y el args incluye algún ticker, probablemente
    # el modelo se equivocó de símbolo. Devolvemos did_you_mean para que se
    # auto-corrija en el siguiente turn sin molestar al usuario.
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

    return {
        "ok": True,
        "data": data,
        "_meta": compute_meta(data, source=endpoint),
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
