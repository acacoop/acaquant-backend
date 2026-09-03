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
import pathlib

from dotenv import load_dotenv

# ⚠️ **EL .env SE LEE ACÁ, NO EN LA TERMINAL.** Es el mismo patrón que
# `core/postgres.py` y `core/llm.py`, y no es cosmético: un `source .env` de
# bash ROMPE los valores con `&` o `$` adentro — el `&` lo interpreta como
# "mandá esto al fondo" y corta la línea ahí. Un connection string
# (`...?sslmode=require&...`) llega mutilado y el error que ves después habla
# de una contraseña mal, no de un `&`. Costó una sesión entera de diagnóstico.
load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env")

URL = "https://api.deepseek.com"
# PRO, no flash: esto no redacta, INVESTIGA — encadena cinco o seis
# herramientas y tiene que sacar una conclusión de lo que leyó.
MODELO_DEFAULT = "deepseek-v4-pro"


def real(modelo: str = "", temperatura: float = 0.0):
    """El modelo de verdad. Falla FUERTE si no hay clave: un agente que se cae
    a un stub sin avisar es peor que uno que no arranca."""
    clave = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not clave:
        raise RuntimeError(
            "falta DEEPSEEK_API_KEY: no está en el .env de la raíz del repo.\n"
            "⚠️ NO uses `source .env` — bash rompe los valores con `&` adentro.\n"
            "Para probar el cableado sin clave: `--guionado`.")
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=modelo or os.getenv("AI_MODEL_PRO") or MODELO_DEFAULT,
        api_key=clave,
        base_url=os.getenv("DEEPSEEK_BASE_URL") or URL,
        temperature=temperatura)


def guionado(respuestas: list, veredicto=None):
    """Un modelo de mentira que devuelve lo que le pusiste, en orden. Sirve para
    probar el GRAFO sin gastar un token: si una respuesta trae `tool_calls`, el
    grafo ejecuta esas herramientas de verdad.

    ⚠️ `veredicto` es lo que devuelve cuando el grafo le pide una respuesta
    ESTRUCTURADA. Sin eso, el modo guionado dejaba de cubrir el último nodo — y
    un modo de prueba que cubre el 80% del camino es peor que ninguno: da
    tranquilidad sobre la parte que no probó.
    """
    from langchain_core.language_models import FakeMessagesListChatModel

    class _Fijo:
        def invoke(self, *_a, **_kw):
            if veredicto is None:
                raise RuntimeError("el modo guionado no trae veredicto de prueba")
            return veredicto

    class _Guionado(FakeMessagesListChatModel):
        # El grafo llama a las dos. Un modelo de mentira las ignora, pero tiene
        # que aceptarlas o el cableado no se puede probar.
        def bind_tools(self, tools, **kw):
            return self

        def with_structured_output(self, esquema, **kw):
            return _Fijo()

    return _Guionado(responses=respuestas)
