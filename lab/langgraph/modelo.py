"""`lab/langgraph/modelo.py` — DE DÓNDE SALE EL CEREBRO.

El grafo no sabe con qué modelo habla: le pasás un objeto que responde
`.invoke()` y listo. Por eso el proveedor se elige ACÁ y en un solo lugar — la
misma idea que `core/llm.py` en el repo grande.

Usamos **DeepSeek**, que ya tiene clave en el `.env` del Droplet
(`DEEPSEEK_API_KEY`). Habla dialecto OpenAI, así que se usa con el cliente de
OpenAI apuntándole a otra URL.
"""
from __future__ import annotations

import os

URL = "https://api.deepseek.com"
MODELO_DEFAULT = "deepseek-v4-flash"


def real(modelo: str = "", temperatura: float = 0.0):
    """El modelo de verdad. Falla FUERTE si no hay clave: un agente que se cae
    a un stub sin avisar es peor que uno que no arranca."""
    clave = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not clave:
        raise RuntimeError(
            "falta DEEPSEEK_API_KEY.\n"
            "  · en el Droplet ya está en /root/TradingAV/.env\n"
            "  · para cargarla:  set -a && source .env && set +a\n"
            "Para probar el cableado sin clave: `--guionado`.")
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=modelo or os.getenv("AI_MODEL_FLASH") or MODELO_DEFAULT,
        api_key=clave,
        base_url=os.getenv("DEEPSEEK_BASE_URL") or URL,
        temperature=temperatura)


def guionado(respuestas: list):
    """Un modelo de mentira que devuelve lo que le pusiste, en orden. Sirve para
    probar el GRAFO sin gastar un token: si una respuesta trae `tool_calls`, el
    grafo ejecuta esas herramientas de verdad."""
    from langchain_core.language_models import FakeMessagesListChatModel

    class _Guionado(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kw):
            return self

    return _Guionado(responses=respuestas)
