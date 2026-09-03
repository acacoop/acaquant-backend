"""`lab/langgraph/servicio.py` — QUIEN ATIENDE LA COLA.

DÓNDE CORRE, Y POR QUÉ EN UN HILO
=================================

Corre **adentro del daemon del agente** (`agente.service`), que ya está
prendido y ya tiene un reloj. No hace falta un servicio más.

Pero **no en la pasada**: una investigación tarda uno o dos minutos y la pasada
del agente es de treinta segundos. Colgarla ahí haría que el agente deje de
detectar mientras alguien investiga — el monitoreo se apagaría justo cuando
alguien está mirando un problema. Así que va en un HILO aparte: la pasada sigue
su ritmo y la investigación avanza al lado.

**Uno por vez.** No es una limitación técnica: dos investigaciones en paralelo
duplican el gasto de tokens sin que nadie las haya pedido a la vez, y la
pantalla las mostraría compitiendo. Si hay dos pedidos, el segundo espera.

QUÉ PASA SI SE REINICIA EL DAEMON
=================================

El hilo muere con el proceso y el pedido queda `corriendo` para siempre — un
spinner eterno en la pantalla, que es la peor forma de fallar porque no se
distingue de «está tardando». Por eso `recuperar_colgados()` corre al arrancar
y los cierra con el motivo. **No los vuelve a encolar solos**: un reintento
automático sobre algo que ya se cortó una vez es cómo se hace un bucle que
gasta tokens toda la noche.
"""
from __future__ import annotations

import logging
import threading

from lab.langgraph import cola

logger = logging.getLogger(__name__)

# Cuánto puede estar «corriendo» un pedido antes de darlo por muerto. Generoso:
# el tope real de una investigación lo pone `grafo.MAX_PASOS`, esto sólo
# levanta los cadáveres de un reinicio.
MINUTOS_HASTA_MUERTO = 15

_hilo: threading.Thread | None = None


# ── LA CONVERSIÓN DE UN EVENTO A UN PASO ───────────────────────────────────
#
# ⚠️ Vive ACÁ y la usan las DOS pantallas —la terminal y el modal—. Si cada una
# tradujera los eventos por su cuenta, mostrarían cosas distintas del mismo
# hecho y no habría forma de saber cuál miente.
def pasos_de(nodo: str, salida: dict | None) -> list[dict]:
    """Los pasos legibles de UN evento del grafo."""
    salida = salida or {}
    fuera: list[dict] = []
    if salida.get("guardado"):
        return fuera
    if salida.get("veredicto") is not None:
        return [{"clase": "veredicto", "que": "", "detalle": ""}]
    for m in salida.get("messages", []):
        if getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                fuera.append({"clase": "pide", "que": tc["name"],
                              "detalle": ", ".join(f"{k}={v}" for k, v in
                                                   tc["args"].items() if v)[:160]})
        elif m.__class__.__name__ == "ToolMessage":
            cuerpo = " ".join(str(m.content).split())
            fuera.append({
                # Un «ya lo pediste» NO es un resultado: es el grafo frenándolo.
                # Verlo distinto es lo que deja notar que estaba dando vueltas.
                "clase": "repetido" if cuerpo.startswith("YA PEDISTE") else "trajo",
                "que": m.name, "detalle": cuerpo[:220]})
        elif nodo == "revisar_piso" and m.content:
            fuera.append({"clase": "freno", "que": "", "detalle": str(m.content)[:300]})
        elif nodo == "antecedentes" and m.content:
            fuera.append({"clase": "antecedentes", "que": "",
                          "detalle": " ".join(str(m.content).split())[:400]})
        elif nodo == "cortar":
            fuera.append({"clase": "corte", "que": "",
                          "detalle": "se agotó el presupuesto de pasos"})
        elif nodo == "redactar" and m.content:
            fuera.append({"clase": "error", "que": "", "detalle": str(m.content)[:300]})
    return fuera


def recuperar_colgados() -> int:
    """Cierra los pedidos que quedaron `corriendo` de una vida anterior."""
    from lab.langgraph.base import escribir
    try:
        escribir(
            "UPDATE lab.pedidos SET estado = %s, terminado_at = now(), "
            "  error = 'se cortó (probablemente un reinicio del daemon). "
            "Pedila de nuevo.' "
            " WHERE estado = %s AND arrancado_at < now() - interval '%s minutes'",
            (cola.ERROR, cola.CORRIENDO, MINUTOS_HASTA_MUERTO))
        return 1
    except Exception as e:
        logger.warning("investigador: no pude recuperar los colgados (%s)", e)
        return 0


def _correr(pedido: dict) -> None:
    """UNA investigación, de punta a punta. **Nunca levanta**: si algo explota,
    el pedido se cierra con el motivo — un pedido que queda abierto es un
    spinner eterno."""
    from langchain_core.messages import HumanMessage

    from lab.langgraph import grafo, medidor, modelo
    from lab.langgraph.investigaciones import INVESTIGACIONES

    pid = pedido["id"]
    try:
        inv = INVESTIGACIONES.get(pedido["tipo"]) or INVESTIGACIONES["libre"]
        pregunta = inv.pregunta.format(caso=pedido["caso"])

        puede, por_que = medidor.hay_presupuesto(pedido.get("por", ""))
        if not puede:
            cola.terminar(pid, error=f"no arranqué: {por_que}")
            return

        cerebro, _med = modelo.real(usuario=pedido.get("por", ""), detalle=pregunta)
        app = grafo.construir(cerebro)
        estado = {"messages": [HumanMessage(pregunta)], "investigacion": inv.nombre,
                  "caso": pedido["caso"], "intentos": [], "faltan_del_piso": [],
                  "vueltas_piso": 0, "vueltas": 0, "corto_por_presupuesto": False,
                  "veredicto": None, "guardado": {}, "por": pedido.get("por", "")}
        config = {"configurable": {"thread_id": f"pedido:{pid}"},
                  "recursion_limit": 80}

        guardado = {}
        for evento in app.stream(estado, config, stream_mode="updates"):
            for nodo, salida in evento.items():
                if (salida or {}).get("guardado"):
                    guardado = salida["guardado"]
                for paso in pasos_de(nodo, salida):
                    cola.anotar_paso(pid, paso)

        if guardado.get("ok"):
            cola.terminar(pid, investigacion_id=guardado.get("id"))
        else:
            cola.terminar(pid, error=guardado.get("error")
                          or "terminó sin dejar veredicto en el diario")
    except Exception as e:
        logger.exception("investigador: el pedido %s murió", pid)
        cola.terminar(pid, error=f"{type(e).__name__}: {e}"[:400])


def atender() -> dict:
    """Lo que llama el daemon en cada pasada. Barato: mira y vuelve."""
    global _hilo
    if _hilo is not None and _hilo.is_alive():
        return {"ocupado": True}
    pedido = cola.siguiente()
    if pedido is None:
        return {"ocupado": False}
    logger.info("investigador: arranco el pedido %s (%s %s)",
                pedido["id"], pedido["tipo"], pedido["caso"])
    _hilo = threading.Thread(target=_correr, args=(pedido,), daemon=True,
                             name=f"investigador-{pedido['id']}")
    _hilo.start()
    return {"arrancado": pedido["id"]}
