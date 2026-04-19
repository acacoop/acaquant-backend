"""Extracción estructurada de variables clave desde reportes de research.

Entrada: texto de un reporte (research interno o externo, comentario macro diario).
Salida: dict con las variables financieras argentinas más relevantes, tipadas.

Usa Gemini 2.5 Flash con responseSchema (JSON mode) para que la respuesta
venga validada. Si una variable no aparece en el reporte, devuelve null — NO
inventa.

Los valores confirmados se persisten en `Manager.IntelDocs` y después
`context.py` los lee e inyecta en el system prompt del asistente.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import requests

from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"


# Schema de extracción. Todas las variables numéricas son opcionales (nullable).
# El comentario macro es un resumen 2-4 oraciones del view del reporte.
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "reservas_netas_mkt": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Reservas netas a valor de mercado, en USD millones (ej: -2700).",
        },
        "reservas_netas_fmi": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Reservas netas según metodología FMI, en USD millones.",
        },
        "repo_stock_ars": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Stock total del REPO BCRA en ARS absolutos (ej: '$4.2B' = 4200000000000).",
        },
        "riesgo_pais_bps": {
            "type": "INTEGER",
            "nullable": True,
            "description": "Riesgo país EMBI+ en basis points. Si viene como % multiplicar por 100 (5.3% = 530).",
        },
        "compras_bcra_dia_usd_mm": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Compras del BCRA en el MLC del día, en USD millones. Negativo si vendió.",
        },
        "compras_bcra_ytd_usd_mm": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Compras netas del BCRA en el año corriente, en USD millones.",
        },
        "brecha_mep_a3500_pct": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Brecha entre MEP y A3500, en porcentaje (ej: 4.2 por 4.2%).",
        },
        "canje_ccl_mep_pct": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Canje entre CCL y MEP, en porcentaje.",
        },
        "rollover_lici_pct": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Rollover de la última licitación del Tesoro, en porcentaje (ej: 124).",
        },
        "bid_to_cover_lici": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Ratio demanda/adjudicado de la última licitación (ej: 2.3).",
        },
        "inflacion_proy_mens_pct": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Inflación proyectada mensual (o esperada por el research) en porcentaje (ej: 2.4).",
        },
        "caucion_prom_pct": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Tasa de caución promedio del día, en porcentaje TNA.",
        },
        "comentario_macro": {
            "type": "STRING",
            "nullable": True,
            "description": (
                "Resumen en 2-4 oraciones del view macro del reporte. Qué pasó, qué "
                "espera, qué tesis plantea. Español rioplatense, directo."
            ),
        },
    },
}

FIELD_LABELS: dict[str, str] = {
    "reservas_netas_mkt": "Reservas netas (VM)",
    "reservas_netas_fmi": "Reservas netas (FMI)",
    "repo_stock_ars": "REPO stock (ARS)",
    "riesgo_pais_bps": "Riesgo país (bps)",
    "compras_bcra_dia_usd_mm": "Compras BCRA día (USD MM)",
    "compras_bcra_ytd_usd_mm": "Compras BCRA YTD (USD MM)",
    "brecha_mep_a3500_pct": "Brecha MEP-A3500 (%)",
    "canje_ccl_mep_pct": "Canje CCL-MEP (%)",
    "rollover_lici_pct": "Rollover lici (%)",
    "bid_to_cover_lici": "Bid-to-cover lici",
    "inflacion_proy_mens_pct": "Inflación proy. mensual (%)",
    "caucion_prom_pct": "Caución promedio (%)",
    "comentario_macro": "Comentario macro",
}


SYSTEM_PROMPT_EXTRACTION = """Sos un extractor de datos financieros de reportes
de research del mercado argentino. Devolvé JSON conforme al schema dado.

REGLAS ESTRICTAS:
- Si una variable NO se menciona explícitamente en el texto, devolvé null. NO estimes, NO inferís.
- Montos en "USD millones" (ej: "USD 2.700 M" → 2700; "USD 13.841 MM" → 13841).
- Porcentajes en base 100 (ej: "4,2%" → 4.2; "124%" → 124).
- Riesgo país en bps (ej: "530 puntos básicos" → 530; si viene en % multiplicá por 100).
- REPO stock en pesos argentinos absolutos (ej: "$4,2 billones" → 4200000000000).
- Si el reporte habla de "compras del BCRA" sin especificar si es del día o YTD,
  usá la variable que parezca más apropiada por contexto.
- Comentario macro: 2-4 oraciones que resuman el view del reporte en español rioplatense.
  No repitas literal los números; contextualizalos.

Nunca inventes. Si no está, null."""


class ExtractionError(RuntimeError):
    pass


def extract_intel(text: str) -> dict[str, Any]:
    """Llama a Gemini con structured output y devuelve el dict extraído.

    Raises ExtractionError si falla el modelo o la respuesta no es parseable.
    """
    if not GEMINI_API_KEY:
        raise ExtractionError("GEMINI_API_KEY no configurada")
    if not text or not text.strip():
        raise ExtractionError("texto vacío")

    body = {
        "contents": [
            {"role": "user", "parts": [{"text": text.strip()[:50000]}]},
        ],
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT_EXTRACTION}]},
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": EXTRACTION_SCHEMA,
        },
    }

    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
            timeout=60,
        )
    except requests.RequestException as e:
        raise ExtractionError(f"no pude contactar a Gemini: {e}") from e

    if resp.status_code == 429:
        raise ExtractionError("rate limit de Gemini — esperá y reintentá")
    if resp.status_code != 200:
        raise ExtractionError(f"Gemini {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise ExtractionError("Gemini sin candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    raw_text = "".join(p.get("text", "") for p in parts).strip()
    if not raw_text:
        raise ExtractionError("Gemini devolvió texto vacío")

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"respuesta no es JSON válido: {e}; raw={raw_text[:300]}") from e

    # Normalizamos: todas las keys del schema existen (null si faltan).
    result: dict[str, Any] = {}
    for key in EXTRACTION_SCHEMA["properties"]:
        result[key] = parsed.get(key)

    return result
