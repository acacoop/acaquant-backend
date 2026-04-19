"""Tools expuestas al LLM (read-only).

Cada tool es un wrapper sobre un endpoint de la propia API. El LLM NO toca
Mongo directo: toca herramientas → ejecutamos un GET HTTP a localhost → devolvemos JSON.

Esto nos da:
- seguridad (mismo pipeline de auth/validación que el frontend)
- observabilidad (los logs del API quedan igual)
- desacoplamiento (si cambiamos la query interna, el tool sigue sirviendo el mismo contrato)
"""
from __future__ import annotations

import json
from typing import Any

import requests

from config import API_KEY

# Base URL del FastAPI. En producción corre en localhost:8000.
API_BASE = "http://127.0.0.1:8000"
TOOL_TIMEOUT = 15

# Gemini usa tipos en MAYÚSCULA en sus function_declarations (Schema type enum).
# Referencia: STRING, INTEGER, NUMBER, BOOLEAN, ARRAY, OBJECT.

TOOLS: list[dict[str, Any]] = [
    {
        "name": "listar_accionistas",
        "description": (
            "Devuelve la lista completa de accionistas de la firma (cuentas + nombre). "
            "Usar cuando el usuario pregunta quiénes son accionistas o cuántos hay."
        ),
        "endpoint": "/api/cuentas/accionistas",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "listar_contrapartes",
        "description": (
            "Devuelve la lista de contrapartes (fondos, bancos, ALYCs) con las que operamos. "
            "Cada una tiene un segmento: Fondos | ALYC | Bancos. Usar para preguntas tipo "
            "'qué fondos hay', 'listame las ALYCs', etc."
        ),
        "endpoint": "/api/cuentas/contrapartes",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "cartera_por_cuenta",
        "description": (
            "Devuelve las posiciones actuales de UNA cuenta específica (último snapshot). "
            "Requiere el id_cuenta numérico. Si el usuario da un nombre, primero hay que "
            "encontrar el id con listar_accionistas o pedir aclaración."
        ),
        "endpoint": "/api/portfolio/carteras",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "id_cuenta": {
                    "type": "STRING",
                    "description": "ID numérico de la cuenta (ej: '12345')",
                },
                "unidad": {
                    "type": "STRING",
                    "description": "Opcional. Filtra por unidad/activo específico.",
                },
            },
            "required": ["id_cuenta"],
        },
    },
    {
        "name": "aum_historico",
        "description": (
            "Devuelve AuM (Assets under Management) con filtros. Sin filtros devuelve "
            "la última foto agregada. Usar para consultas tipo 'AuM del fondo X', "
            "'serie histórica de AuM de cuenta Y', 'AuM entre fechas'."
        ),
        "endpoint": "/api/portfolio/aum",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "id_cuenta": {"type": "STRING", "description": "Filtra por id_cuenta."},
                "cuenta": {"type": "STRING", "description": "Filtra por nombre de cuenta."},
                "unidad": {"type": "STRING", "description": "Filtra por unidad/activo."},
                "desde": {
                    "type": "STRING",
                    "description": "Fecha desde en formato YYYY-MM-DD.",
                },
                "hasta": {
                    "type": "STRING",
                    "description": "Fecha hasta en formato YYYY-MM-DD.",
                },
                "ultimo": {
                    "type": "BOOLEAN",
                    "description": "Si true, devuelve solo el último snapshot.",
                },
            },
        },
    },
    {
        "name": "flujo_mesa",
        "description": (
            "Devuelve operaciones de mesa (libro de operaciones) filtradas. "
            "Usar para preguntas tipo 'qué operó contraparte X esta semana', "
            "'flujo en ARS de mayo', 'operaciones con fondos el último mes'."
        ),
        "endpoint": "/api/operaciones/flujo",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "contraparte": {"type": "STRING", "description": "Nombre de la contraparte."},
                "moneda": {"type": "STRING", "description": "ARS o USD."},
                "segmento": {"type": "STRING", "description": "Fondos | ALYC | Bancos."},
                "desde": {"type": "STRING", "description": "YYYY-MM-DD."},
                "hasta": {"type": "STRING", "description": "YYYY-MM-DD."},
            },
        },
    },
    {
        "name": "cotizacion_renta_fija",
        "description": (
            "Devuelve el snapshot de mercado de un bono/letra (book, métricas de microestructura, "
            "VWAP, spread, último precio, máximos/mínimos). Usar para 'cómo está cotizando X', "
            "'precio de TX26', 'spread de AL30'."
        ),
        "endpoint": "/api/cotizaciones/renta-fija",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instrumento": {
                    "type": "STRING",
                    "description": "Ticker del instrumento (ej: 'TX26', 'AL30', 'S31M6').",
                },
            },
            "required": ["instrumento"],
        },
    },
    {
        "name": "breakevens_actuales",
        "description": (
            "Devuelve los breakevens vigentes (inflación mensual implícita) del pareo Lecap vs CER. "
            "Sin parámetros. Usar para 'cómo están los breakevens', 'qué infla implícita el mercado'."
        ),
        "endpoint": "/api/cotizaciones/breakevens",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "forwards_por_curva",
        "description": (
            "Matriz NxN de tasas forward entre todos los instrumentos de UNA curva. "
            "Usar para 'forwards de la curva tasa fija', 'forward implícito TX26-TZX26'."
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
]

TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


def gemini_tool_declarations() -> list[dict[str, Any]]:
    """Formato que espera Gemini: lista con una entrada `function_declarations`."""
    decls = [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in TOOLS
    ]
    return [{"function_declarations": decls}]


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta la tool pedida por el LLM.

    Devuelve un dict con la forma `{"ok": bool, "data": ..., "error": ...}`
    para que el modelo distinga éxito de error explícitamente.
    """
    tool = TOOL_BY_NAME.get(name)
    if tool is None:
        return {"ok": False, "error": f"tool '{name}' no existe"}

    url = API_BASE + tool["endpoint"]
    # Limpiamos params None/vacíos para no ensuciar la URL.
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
