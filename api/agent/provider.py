"""Cliente HTTP hacia Gemini API.

Abstracción pensada para ser swappable. Si mañana queremos Claude u OpenAI,
creamos otra clase con la misma interfaz `generate(...)` y cambiamos una línea.
"""
from __future__ import annotations

import json
import re
import time
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


def _parse_429_detail(resp_text: str) -> tuple[str, float | None]:
    """Extrae de la respuesta 429 de Gemini:
    - nombre compacto de la cuota que se tocó (RPM / RPD / TPM / etc.)
    - retryDelay en segundos, si Google lo incluyó.
    """
    quota_short = "unknown"
    try:
        data = json.loads(resp_text)
        err = data.get("error", {})
        # Ejemplos de quotaMetric:
        # "...generate_content_free_tier_requests" → RPM/RPD
        # "...generate_content_free_tier_input_token_count" → TPM in
        msg = err.get("message", "")
        details = err.get("details", []) or []

        for d in details:
            for v in d.get("violations", []) or []:
                m = v.get("quotaMetric", "")
                qid = v.get("quotaId", "")
                if "PerMinute" in qid:
                    if "Tokens" in qid:
                        quota_short = "TPM (tokens/min)"
                    else:
                        quota_short = "RPM (requests/min)"
                elif "PerDay" in qid:
                    if "Tokens" in qid:
                        quota_short = "input tokens/day"
                    else:
                        quota_short = "RPD (requests/day)"
                if quota_short != "unknown":
                    break
            if quota_short != "unknown":
                break

        # retryDelay del bloque RetryInfo
        retry_delay = None
        for d in details:
            rd = d.get("retryDelay")
            if rd and isinstance(rd, str):
                m2 = re.match(r"(\d+(?:\.\d+)?)s", rd)
                if m2:
                    retry_delay = float(m2.group(1))
                    break

        # Fallback: parsear del message si no hubo estructura
        if quota_short == "unknown" and "Quota exceeded" in msg:
            if "token_count" in msg.lower():
                quota_short = "tokens/min" if "PerMinute" in msg else "tokens/day"
            elif "requests" in msg.lower():
                quota_short = "requests/min" if "PerMinute" in msg else "requests/day"

        return quota_short, retry_delay
    except Exception:
        return "unknown", None


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

        # Reintento silencioso ante 429 transitorio: si Google manda un retryDelay
        # chico (<= 6s), esperamos y reintentamos UNA vez antes de fallar.
        attempt = 0
        max_attempts = 2
        while True:
            attempt += 1
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
                quota_short, retry_delay = _parse_429_detail(resp.text)
                if attempt < max_attempts and retry_delay and retry_delay <= 6.0:
                    time.sleep(retry_delay + 0.5)
                    continue
                delay_hint = (
                    f" (el modelo pide esperar {int(retry_delay)}s)"
                    if retry_delay else ""
                )
                raise LLMRateLimitError(
                    f"rate limit Gemini — cuota: {quota_short}{delay_hint}. "
                    "En free tier esto pasa mucho al encadenar tool-use. "
                    "Esperá y reintentá, o activá billing en Google Cloud."
                )

            if resp.status_code != 200:
                raise LLMBadResponseError(
                    f"el modelo respondió {resp.status_code}: {resp.text[:300]}"
                )
            break

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMBadResponseError("el modelo devolvió una respuesta vacía")

        # Pegamos usage del top-level al candidate por comodidad del runner.
        candidate = candidates[0]
        candidate["usageMetadata"] = data.get("usageMetadata", {})
        return candidate
