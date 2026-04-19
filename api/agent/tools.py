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

from config import API_KEY

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
        "description": (
            "Snapshot de mercado de un bono/letra: book (top 5 bids/offers), "
            "VWAP, spread, último/máximo/mínimo/cierre, trades recientes. "
            "Usar para 'cómo está TX26', 'precio de AL30', 'spread de S31M6'."
        ),
        "endpoint": "/api/cotizaciones/renta-fija",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instrumento": {
                    "type": "STRING",
                    "description": "Ticker (ej: 'TX26', 'AL30', 'S31M6').",
                },
            },
            "required": ["instrumento"],
        },
    },
    {
        "name": "cotizacion_opciones",
        "description": (
            "Cotizaciones de opciones de GGAL con Greeks (delta, gamma, vega, theta) "
            "e IV. Usar para 'opciones de GGAL', 'call 3000 GGAL', 'IV de puts'."
        ),
        "endpoint": "/api/cotizaciones/opciones",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instrumento": {
                    "type": "STRING",
                    "description": "Símbolo de la opción (ej: 'GFGC3000JU').",
                },
                "tipo": {
                    "type": "STRING",
                    "description": "Filtrar por 'CALL' o 'PUT'.",
                },
            },
        },
    },
    {
        "name": "forwards_por_curva",
        "description": (
            "Matriz NxN de tasas forward implícitas entre todos los instrumentos "
            "de una curva. Usar para 'forwards de tasa fija', 'forward TX26-TZX26'."
        ),
        "endpoint": "/api/cotizaciones/forwards",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "curva": {
                    "type": "STRING",
                    "description": "Nombre de la curva: 'tasa_fija' o 'cer'.",
                },
            },
            "required": ["curva"],
        },
    },
    {
        "name": "breakevens_actuales",
        "description": (
            "Breakevens vigentes: inflación mensual implícita del pareo Lecap vs CER. "
            "Sin parámetros. Usar para 'cómo están los breakevens', "
            "'qué infla implícita el mercado'."
        ),
        "endpoint": "/api/cotizaciones/breakevens",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "serie_cer",
        "description": "Serie histórica del índice CER (BCRA). Rango opcional.",
        "endpoint": "/api/cotizaciones/cer",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "desde": {"type": "STRING", "description": "Fecha YYYY-MM-DD."},
                "hasta": {"type": "STRING", "description": "Fecha YYYY-MM-DD."},
            },
        },
    },
    {
        "name": "serie_badlar",
        "description": "Serie histórica de la tasa BADLAR (bancos privados, BCRA).",
        "endpoint": "/api/cotizaciones/badlar",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "desde": {"type": "STRING", "description": "Fecha YYYY-MM-DD."},
                "hasta": {"type": "STRING", "description": "Fecha YYYY-MM-DD."},
            },
        },
    },
    {
        "name": "serie_dolar_a3500",
        "description": "Serie histórica del dólar A3500 (referencia BCRA).",
        "endpoint": "/api/cotizaciones/dolar",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "desde": {"type": "STRING", "description": "Fecha YYYY-MM-DD."},
                "hasta": {"type": "STRING", "description": "Fecha YYYY-MM-DD."},
            },
        },
    },
    {
        "name": "mep_actual",
        "description": "Último snapshot intradía del dólar MEP. Sin parámetros.",
        "endpoint": "/api/cotizaciones/mep",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "historico_trades",
        "description": (
            "Trades de un instrumento en los últimos 15 días (serie intradía). "
            "Usar para 'evolución de TX26 en 15 días', 'trades recientes de AL30'."
        ),
        "endpoint": "/api/cotizaciones/historico/trades",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instrumento": {"type": "STRING", "description": "Ticker."},
            },
            "required": ["instrumento"],
        },
    },
    {
        "name": "historico_forwards",
        "description": "Evolución diaria de forwards por curva.",
        "endpoint": "/api/cotizaciones/historico/forwards",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "curva": {"type": "STRING", "description": "'tasa_fija' o 'cer'."},
                "desde": {"type": "STRING"},
                "hasta": {"type": "STRING"},
            },
            "required": ["curva"],
        },
    },
    {
        "name": "historico_breakevens",
        "description": "Evolución diaria de los breakevens Lecap vs CER.",
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
        "description": "Serie histórica del dólar MEP.",
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
        "description": (
            "Serie diaria (último precio + TEA/TEM/duration/paridad por día) de "
            "TODOS los instrumentos de UNA curva. Útil para ver evolución completa "
            "de una curva. Devuelve lista larga, preferí usar historico_trades si "
            "querés un solo instrumento."
        ),
        "endpoint": "/api/cotizaciones/historico/curva",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "curva": {"type": "STRING", "description": "'tasa_fija' o 'cer'."},
            },
            "required": ["curva"],
        },
    },

    # ─── GRUPO 2: metadata de títulos (info pública de CNV/prospectos) ──────
    {
        "name": "metadata_activos",
        "description": (
            "Metadata de instrumentos (emisor, clase de activo, calificación, "
            "vencimiento). Filtrar por ticker/emisor/clase_activo. "
            "Usar para 'emisor de TX26', 'ONs con calificación AAA', 'bonos que "
            "vencen en 2027'."
        ),
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
        "description": (
            "Cronograma de flujos de un bono (cupones, amortizaciones, moneda). "
            "Usar para 'cuándo paga TX26', 'próximo cupón de AL30', 'amortizaciones "
            "de TZX26'."
        ),
        "endpoint": "/api/titulos/flujos",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ticker":        {"type": "STRING"},
                "curva":         {"type": "STRING", "description": "'tasa_fija' o 'cer'."},
                "moneda_flujo":  {"type": "STRING", "description": "ARS o USD."},
            },
        },
    },
]

TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


def _is_blocked(endpoint: str) -> bool:
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

    if _is_blocked(tool["endpoint"]):
        return {
            "ok": False,
            "error": (
                "ruta bloqueada por política de datos (free tier del modelo). "
                "No tengo acceso a información de clientes, carteras, AuM, "
                "operaciones, accionistas ni contrapartes."
            ),
        }

    url = API_BASE + tool["endpoint"]
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
        return {"ok": True, "data": resp.json()}
    except json.JSONDecodeError:
        return {"ok": False, "error": "respuesta no es JSON", "raw": resp.text[:400]}
