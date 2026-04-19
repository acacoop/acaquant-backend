"""Cliente HTTP hacia Gemini API.

Abstracción pensada para ser swappable. Si mañana queremos Claude u OpenAI,
creamos otra clase con la misma interfaz `generate(...)` y cambiamos una línea.
"""
from __future__ import annotations

import json
from typing import Any

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Modelo por defecto. Flash es free tier y suficiente para el 90% de las consultas.
DEFAULT_MODEL = "gemini-2.5-flash"


class LLMError(RuntimeError):
    """Error del provider (HTTP, rate limit, respuesta mal formada).

    Subclases permiten discriminar en el router (chat.py) para devolver
    status codes y mensajes adecuados al cliente.
    """


class LLMRateLimitError(LLMError):
    """Gemini respondió 429 (rate limit / quota excedida)."""


class LLMTransportError(LLMError):
    """Fallo de red, DNS, TLS, timeout."""


class LLMBadResponseError(LLMError):
    """Respuesta 4xx/5xx que no es 429, o payload mal formado."""


class GeminiProvider:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: int = 60):
        if not api_key:
            raise ValueError("api_key vacía — configurar GEMINI_API_KEY en .env")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate(
        self,
        contents: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Invoca Gemini.generateContent y devuelve el primer candidate.

        contents: lista de mensajes en formato Gemini (role + parts).
        tools: lista con shape `[{"function_declarations": [...]}]`.
        Retorna el dict del candidate (content.parts puede contener text o functionCall).
        """
        url = GEMINI_URL.format(model=self.model)
        body: dict[str, Any] = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {"temperature": temperature},
        }
        if tools:
            body["tools"] = tools

        try:
            resp = requests.post(
                url,
                params={"key": self.api_key},
                headers={"Content-Type": "application/json"},
                data=json.dumps(body),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise LLMTransportError(f"no pude contactar al modelo: {e}") from e

        if resp.status_code == 429:
            raise LLMRateLimitError(
                "el modelo está temporalmente saturado (rate limit). "
                "Esperá ~1 minuto y reintentá."
            )

        if resp.status_code != 200:
            raise LLMBadResponseError(
                f"el modelo respondió {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMBadResponseError("el modelo devolvió una respuesta vacía")

        # Pegamos usage del top-level al candidate por comodidad del runner.
        candidate = candidates[0]
        candidate["usageMetadata"] = data.get("usageMetadata", {})
        return candidate
