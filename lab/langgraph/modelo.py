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


def real(modelo: str = "", temperatura: float = 0.0, *, usuario: str = "",
         detalle: str = "") -> tuple:
    """El modelo de verdad, **con el contador puesto**. Devuelve
    `(modelo, medidor)`.

    Falla FUERTE si no hay clave: un agente que se cae a un stub sin avisar
    es peor que uno que no arranca.

    ⚠️ El medidor viaja como CALLBACK de LangChain, así que el grafo y las
    herramientas no se enteran de que existe. Anotar el gasto no puede ser
    algo que cada nodo tenga que acordarse de hacer.
    """
    clave = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not clave:
        raise RuntimeError(
            "falta DEEPSEEK_API_KEY: no está en el .env de la raíz del repo.\n"
            "⚠️ NO uses `source .env` — bash rompe los valores con `&` adentro.\n"
            "Para probar el cableado sin clave: `--guionado`.")
    from langchain_openai import ChatOpenAI

    from lab.langgraph.medidor import Medidor
    modelo_id = modelo or os.getenv("AI_MODEL_PRO") or MODELO_DEFAULT
    medidor = Medidor(modelo_id, usuario=usuario, detalle=detalle)
    return ChatOpenAI(
        model=modelo_id,
        api_key=clave,
        callbacks=[medidor],
        base_url=os.getenv("DEEPSEEK_BASE_URL") or URL,
        temperature=temperatura,
        # ⚠️ **EL RAZONAMIENTO SE APAGA, Y NO ES POR AHORRAR.** Los v4 traen
        # `thinking` ENCENDIDO por default, y cuando está encendido DeepSeek
        # exige que le devuelvas su `reasoning_content` en cada llamada
        # siguiente. LangChain no lo hace —es un campo propio de DeepSeek, no
        # del dialecto OpenAI— así que la SEGUNDA vuelta del ciclo muere con
        # **HTTP 400 «The `reasoning_content` in the thinking mode must be
        # passed back to the API»**. O sea: un agente que encadena herramientas
        # no puede correr con thinking encendido por este camino.
        #
        # El shape sale de `core/llm.py::_armar_body`, que ya lo tenía
        # verificado contra la doc del proveedor: `{"thinking": {"type": ...}}`
        # y el default es enabled, por eso los callers lo mandan SIEMPRE
        # explícito. Es la segunda cosa en una hora que se resuelve mirando lo
        # que este repo ya sabía.
        extra_body={"thinking": {"type": "disabled"}}), medidor


def guionado(respuestas: list, veredicto=None) -> tuple:
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

    # Devuelve el MISMO par que `real()`: si el modo de prueba tuviera otra
    # forma, el llamador tendría dos caminos y sólo uno estaría probado.
    class _SinMedir:
        def resumen(self):
            return {"llamadas": 0, "tokens_in": 0, "tokens_out": 0, "tokens": 0}

    return _Guionado(responses=respuestas), _SinMedir()
