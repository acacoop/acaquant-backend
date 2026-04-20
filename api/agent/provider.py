"""Clientes HTTP para LLM providers (Gemini y Claude).

Ambos implementan la misma interfaz `generate(...)` devolviendo un `LLMResponse`
neutro. El runner es provider-agnostic.

Canónico: el history usa formato "estilo Claude" — role + content (string
o lista de content blocks type=text|tool_use|tool_result). El GeminiProvider
convierte on-the-fly a Gemini shape.
"""
from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import requests

from api.agent.types import LLMResponse, ToolCallRequest

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Excepciones comunes
# ═══════════════════════════════════════════════════════════════════════════


class LLMError(RuntimeError):
    """Error del provider."""


class LLMRateLimitError(LLMError):
    """429 / quota excedida."""


class LLMTransportError(LLMError):
    """Fallo de red, DNS, TLS, timeout."""


class LLMBadResponseError(LLMError):
    """Respuesta no-200 no-429, o payload mal formado."""


# ═══════════════════════════════════════════════════════════════════════════
# Base
# ═══════════════════════════════════════════════════════════════════════════


class LLMProvider(ABC):
    name: str = "base"
    model: str = "base"

    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Invoca el modelo. messages + tools en formato canónico (estilo Claude).

        Devuelve `LLMResponse` con text, tool_calls, usage, stop_reason y
        assistant_message (el mensaje del modelo listo para apendear al history).
        """


# ═══════════════════════════════════════════════════════════════════════════
# Claude Provider
# ═══════════════════════════════════════════════════════════════════════════

CLAUDE_URL = "https://api.anthropic.com/v1/messages"

CLAUDE_MODELS = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-7",
}


def _claude_tool_declarations(
    tools: list[dict[str, Any]],
    cache_last: bool = True,
) -> list[dict[str, Any]]:
    """Convierte TOOLS (formato interno, tipos MAYÚSCULA estilo Gemini) a
    formato Claude (tipos lowercase, key `input_schema`).

    Si `cache_last` está activado, marca la última tool con `cache_control`
    para que Claude cachee todo el bloque tools (son ~3K tokens estables).
    """
    def _lower_schema(s: Any) -> Any:
        if isinstance(s, dict):
            out: dict = {}
            for k, v in s.items():
                if k == "type" and isinstance(v, str):
                    out[k] = v.lower()
                else:
                    out[k] = _lower_schema(v)
            return out
        if isinstance(s, list):
            return [_lower_schema(x) for x in s]
        return s

    claude_tools = []
    for t in tools:
        claude_tools.append({
            "name": t["name"],
            "description": t["description"],
            "input_schema": _lower_schema(t.get("parameters", {"type": "OBJECT", "properties": {}})),
        })

    if cache_last and claude_tools:
        claude_tools[-1]["cache_control"] = {"type": "ephemeral"}

    return claude_tools


def _parse_429_detail_claude(resp_text: str) -> tuple[str, float | None]:
    try:
        data = json.loads(resp_text)
        err = data.get("error", {})
        msg = err.get("message", "").lower()
        if "tokens per minute" in msg or "tpm" in msg:
            return "TPM (tokens/min)", None
        if "requests per minute" in msg or "rpm" in msg:
            return "RPM (requests/min)", None
        if "daily" in msg or "day" in msg:
            return "quota diaria", None
        return "rate limit", None
    except Exception:
        return "rate limit", None


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(
        self,
        api_key: str,
        model: str = "sonnet",
        timeout: int = 60,
        enable_cache: bool = True,
    ):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY vacía")
        self.api_key = api_key
        # Admite alias (haiku/sonnet/opus) o model ID directo
        self.model = CLAUDE_MODELS.get(model, model)
        self.alias = model if model in CLAUDE_MODELS else "custom"
        self.timeout = timeout
        self.enable_cache = enable_cache

    def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        temperature: float = 0.2,
    ) -> LLMResponse:
        # System con cache_control: el prefijo estático se cachea (5 min TTL).
        if self.enable_cache and system_prompt:
            system_blocks = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_blocks = [{"type": "text", "text": system_prompt}] if system_prompt else []

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": temperature,
            "messages": messages,
        }
        if system_blocks:
            body["system"] = system_blocks
        if tools:
            body["tools"] = _claude_tool_declarations(tools)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # Retry silencioso una vez ante 429 transitorio
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = requests.post(
                    CLAUDE_URL,
                    headers=headers,
                    data=json.dumps(body),
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                raise LLMTransportError(f"no pude contactar a Claude: {e}") from e

            if resp.status_code == 429:
                quota, _ = _parse_429_detail_claude(resp.text)
                if attempt < 2:
                    time.sleep(2.0)
                    continue
                raise LLMRateLimitError(
                    f"rate limit Claude — cuota: {quota}. Esperá ~1 min y reintentá."
                )
            if resp.status_code != 200:
                raise LLMBadResponseError(
                    f"Claude respondió {resp.status_code}: {resp.text[:300]}"
                )
            break

        data = resp.json()
        content_blocks = data.get("content", []) or []
        stop_reason = data.get("stop_reason", "end_turn")
        usage_raw = data.get("usage", {}) or {}

        # Normalizar usage con las claves que el resto del sistema espera
        usage = {
            "promptTokenCount":     usage_raw.get("input_tokens", 0) or 0,
            "candidatesTokenCount": usage_raw.get("output_tokens", 0) or 0,
            "totalTokenCount":      (usage_raw.get("input_tokens", 0) or 0)
                                    + (usage_raw.get("output_tokens", 0) or 0),
            "cache_read_input_tokens":     usage_raw.get("cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": usage_raw.get("cache_creation_input_tokens", 0) or 0,
            "model": self.model,
            "model_alias": self.alias,
        }

        text_parts = []
        tool_calls = []
        for block in content_blocks:
            t = block.get("type")
            if t == "text":
                text_parts.append(block.get("text", ""))
            elif t == "tool_use":
                tool_calls.append(ToolCallRequest(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    args=block.get("input", {}) or {},
                ))

        assistant_msg = {"role": "assistant", "content": content_blocks}

        return LLMResponse(
            text="\n".join([t for t in text_parts if t]).strip(),
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=stop_reason,
            assistant_message=assistant_msg,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Gemini Provider (legacy — mantiene compat cuando LLM_PROVIDER=gemini)
# ═══════════════════════════════════════════════════════════════════════════

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def _parse_429_detail_gemini(resp_text: str) -> tuple[str, float | None]:
    quota_short = "unknown"
    try:
        data = json.loads(resp_text)
        err = data.get("error", {})
        details = err.get("details", []) or []
        for d in details:
            for v in d.get("violations", []) or []:
                qid = v.get("quotaId", "")
                if "PerMinute" in qid:
                    quota_short = "TPM (tokens/min)" if "Tokens" in qid else "RPM (requests/min)"
                elif "PerDay" in qid:
                    quota_short = "input tokens/day" if "Tokens" in qid else "RPD (requests/day)"
                if quota_short != "unknown":
                    break
            if quota_short != "unknown":
                break
        retry_delay = None
        for d in details:
            rd = d.get("retryDelay")
            if rd and isinstance(rd, str):
                m = re.match(r"(\d+(?:\.\d+)?)s", rd)
                if m:
                    retry_delay = float(m.group(1))
                    break
        return quota_short, retry_delay
    except Exception:
        return "unknown", None


def _messages_to_gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte formato canónico (Claude) → Gemini contents."""
    contents = []
    # Necesitamos mapear tool_use_id ↔ name para functionResponse
    id_to_name: dict[str, str] = {}
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "user":
            if isinstance(content, str):
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif isinstance(content, list):
                parts = []
                for b in content:
                    t = b.get("type")
                    if t == "text":
                        parts.append({"text": b.get("text", "")})
                    elif t == "tool_result":
                        tid = b.get("tool_use_id", "")
                        name = id_to_name.get(tid, "unknown_tool")
                        # content de tool_result puede ser string o lista
                        raw = b.get("content", "")
                        try:
                            response_obj = json.loads(raw) if isinstance(raw, str) else raw
                        except Exception:
                            response_obj = {"content": raw}
                        parts.append({"functionResponse": {"name": name, "response": response_obj if isinstance(response_obj, dict) else {"result": response_obj}}})
                if parts:
                    contents.append({"role": "user", "parts": parts})
        elif role == "assistant":
            if isinstance(content, str):
                contents.append({"role": "model", "parts": [{"text": content}]})
            elif isinstance(content, list):
                parts = []
                for b in content:
                    t = b.get("type")
                    if t == "text":
                        parts.append({"text": b.get("text", "")})
                    elif t == "tool_use":
                        name = b.get("name", "")
                        id_to_name[b.get("id", "")] = name
                        parts.append({"functionCall": {"name": name, "args": b.get("input", {}) or {}}})
                if parts:
                    contents.append({"role": "model", "parts": parts})
    return contents


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL, timeout: int = 60):
        if not api_key:
            raise ValueError("GEMINI_API_KEY vacía")
        self.api_key = api_key
        self.model = model
        self.alias = model
        self.timeout = timeout

    def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        temperature: float = 0.2,
    ) -> LLMResponse:
        url = GEMINI_URL.format(model=self.model)
        contents = _messages_to_gemini_contents(messages)

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if tools:
            decls = [
                {"name": t["name"], "description": t["description"], "parameters": t.get("parameters", {"type": "OBJECT", "properties": {}})}
                for t in tools
            ]
            body["tools"] = [{"function_declarations": decls}]

        attempt = 0
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
                raise LLMTransportError(f"no pude contactar a Gemini: {e}") from e

            if resp.status_code == 429:
                quota, retry_delay = _parse_429_detail_gemini(resp.text)
                if attempt < 2 and retry_delay and retry_delay <= 6.0:
                    time.sleep(retry_delay + 0.5)
                    continue
                delay_hint = f" (esperar {int(retry_delay)}s)" if retry_delay else ""
                raise LLMRateLimitError(
                    f"rate limit Gemini — cuota: {quota}{delay_hint}. "
                    "En free tier pasa al encadenar tool-use. Considerá activar billing."
                )
            if resp.status_code != 200:
                raise LLMBadResponseError(
                    f"Gemini respondió {resp.status_code}: {resp.text[:300]}"
                )
            break

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMBadResponseError("Gemini sin candidates")

        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        usage_raw = data.get("usageMetadata", {}) or {}

        text_parts = []
        tool_calls = []
        claude_style_content = []
        for p in parts:
            if p.get("text"):
                text_parts.append(p["text"])
                claude_style_content.append({"type": "text", "text": p["text"]})
            elif "functionCall" in p:
                fc = p["functionCall"]
                # Gemini no devuelve id; sintetizamos uno
                tid = f"call_{len(tool_calls) + 1}_{fc.get('name', 'unknown')}"
                tool_calls.append(ToolCallRequest(
                    id=tid,
                    name=fc.get("name", ""),
                    args=fc.get("args", {}) or {},
                ))
                claude_style_content.append({
                    "type": "tool_use",
                    "id": tid,
                    "name": fc.get("name", ""),
                    "input": fc.get("args", {}) or {},
                })

        stop_reason = "tool_use" if tool_calls else "end_turn"
        usage = {
            "promptTokenCount":     usage_raw.get("promptTokenCount", 0) or 0,
            "candidatesTokenCount": usage_raw.get("candidatesTokenCount", 0) or 0,
            "totalTokenCount":      usage_raw.get("totalTokenCount", 0) or 0,
            "model": self.model,
            "model_alias": self.alias,
        }
        assistant_msg = {"role": "assistant", "content": claude_style_content}

        return LLMResponse(
            text="\n".join([t for t in text_parts if t]).strip(),
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=stop_reason,
            assistant_message=assistant_msg,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════════


def get_provider(model_hint: str = "haiku") -> LLMProvider:
    """Devuelve el provider configurado por LLM_PROVIDER + model_hint.

    - LLM_PROVIDER=claude (default): devuelve ClaudeProvider con model_hint
      (haiku | sonnet | opus).
    - LLM_PROVIDER=gemini: devuelve GeminiProvider Flash (ignora model_hint).
    """
    from config import ANTHROPIC_API_KEY, GEMINI_API_KEY, LLM_PROVIDER

    if LLM_PROVIDER == "gemini":
        return GeminiProvider(api_key=GEMINI_API_KEY)
    # Default: Claude
    return ClaudeProvider(api_key=ANTHROPIC_API_KEY, model=model_hint)
