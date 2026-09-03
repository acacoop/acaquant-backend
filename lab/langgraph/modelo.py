"""`lab/langgraph/modelo.py` — DE DÓNDE SALE EL CEREBRO.

Un grafo de LangGraph no sabe nada de proveedores: le pasás un objeto que
responde `.invoke(mensajes)` y listo. Por eso el modelo se elige ACÁ y en un
solo lugar — la misma idea que `core/llm.py` en el repo grande: **el que
orquesta no conoce al proveedor**.

Hay dos modos, y el segundo existe por una razón que no es comodidad:

  REAL     Gemini (Google AI Studio o Vertex). Necesita GOOGLE_API_KEY.
  GUIONADO Un modelo de mentira que devuelve una lista fija de respuestas.
           **Sirve para testear el GRAFO sin pagar tokens y sin azar.** Es la
           forma normal de escribir tests de un agente: separás "¿el cableado
           funciona?" de "¿el modelo eligió bien?", que son dos preguntas
           distintas y sólo la segunda es incierta.
"""
from __future__ import annotations

import os

MODELO_DEFAULT = "gemini-2.5-flash"


def real(modelo: str = "", temperatura: float = 0.0):
    """El modelo de verdad. Falla FUERTE si no hay clave: un agente que se cae
    a un stub sin avisar es peor que uno que no arranca."""
    clave = os.getenv("GOOGLE_API_KEY", "").strip()
    if not clave:
        raise RuntimeError(
            "falta GOOGLE_API_KEY.\n"
            "  · AI Studio (2 minutos, tier gratis): https://aistudio.google.com/apikey\n"
            "  · después:  export GOOGLE_API_KEY=...\n"
            "Para probar el cableado sin clave: `--guionado`.")
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=modelo or MODELO_DEFAULT,
                                  temperature=temperatura)


def guionado(respuestas: list):
    """Un modelo que devuelve exactamente lo que le pusiste, en orden.

    `respuestas` es una lista de `AIMessage`. Si una trae `tool_calls`, el grafo
    va a ejecutar esas herramientas de verdad — o sea que se prueba el camino
    completo (decisión → tool → vuelta al modelo) sin LLM en el medio.
    """
    from langchain_core.language_models import FakeMessagesListChatModel

    class _Guionado(FakeMessagesListChatModel):
        # El grafo llama `bind_tools`; un modelo de mentira las ignora, pero
        # tiene que aceptar la llamada o el cableado no se puede probar.
        def bind_tools(self, tools, **kw):
            return self

    return _Guionado(responses=respuestas)
