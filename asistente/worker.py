"""Worker durable de ejecuciones del asistente.

Toma runs con SKIP LOCKED, ejecuta LangGraph con checkpoint PostgreSQL y deja
cada evento disponible para SSE. Se corre como ``asistente-worker.service``.
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo

from asistente import ejecuciones, grafo, sesiones
from asistente import eventos as EV
from core.postgres import get_postgres_uri

logger = logging.getLogger(__name__)
_POLL_S = 0.5
_TIMEOUT_DEFAULT_S = 180
_detener = threading.Event()


def _senal(_signum, _frame) -> None:
    _detener.set()


def _checkpoint_dsn() -> str:
    return make_conninfo(get_postgres_uri(), options="-c search_path=ia")


def _resultado_publico(resultado: dict) -> dict:
    return {k: v for k, v in resultado.items() if k not in {"mensajes", "eventos", "evidencias"}}


def _timeout_s() -> int:
    try:
        return max(30, min(int(os.getenv("ASISTENTE_RUN_TIMEOUT_S", _TIMEOUT_DEFAULT_S)), 1800))
    except ValueError:
        return _TIMEOUT_DEFAULT_S


def procesar(run: dict, grafo_compilado) -> None:
    run_id = run["run_id"]
    timeout_s = _timeout_s()
    vence = time.monotonic() + timeout_s

    def sink(evento):
        if time.monotonic() >= vence:
            raise EV.TiempoAgotado(run_id)
        return ejecuciones.emitir(run_id, evento)
    try:
        if run.get("tipo") == "diagnostico":
            # Un run del AV AGENT sobre un hallazgo: no es una conversación de
            # nadie, no toca ia.conversaciones. Mismo worker, misma cancelación,
            # mismo timeout, mismos eventos.
            from asistente import diagnostico

            with EV.capturar(sink):
                resultado = diagnostico.correr_run(run)
        else:
            resultado = sesiones.preguntar(
                run["pregunta"], usuario=run["usuario"], sesion=run["sesion"],
                rol=run["rol"], portal=run["portal"], run_id=run_id,
                grafo_compilado=grafo_compilado, event_sink=sink, forzar_sesion=True)
        actual = ejecuciones.obtener(run_id)
        if actual and actual["estado"] == "cancel_requested":
            ejecuciones.terminar(run_id, "cancelled", error="cancelada por el usuario")
            ejecuciones.emitir(run_id, {"tipo": "cancelled", "agente": "run"})
            return
        publico = _resultado_publico(resultado)
        estado = "failed" if resultado.get("error") and not resultado.get("respuesta") else "succeeded"
        ejecuciones.terminar(run_id, estado, resultado=publico, error=resultado.get("error"))
        ejecuciones.emitir(run_id, {"tipo": "final", "agente": "run", "resultado": publico})
    except EV.CancelacionSolicitada:
        ejecuciones.terminar(run_id, "cancelled", error="cancelada por el usuario")
        ejecuciones.emitir(run_id, {"tipo": "cancelled", "agente": "run"})
    except EV.TiempoAgotado:
        mensaje = f"superó el presupuesto de {timeout_s} segundos"
        ejecuciones.terminar(run_id, "timed_out", error=mensaje)
        ejecuciones.emitir(run_id, {"tipo": "timed_out", "agente": "run", "error": mensaje})
    except Exception as error:
        logger.exception("asistente worker: run %s falló", run_id)
        mensaje = f"{type(error).__name__}: {error}"
        ejecuciones.terminar(run_id, "failed", error=mensaje)
        ejecuciones.emitir(run_id, {"tipo": "run_error", "agente": "run", "error": mensaje})


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, _senal)
    signal.signal(signal.SIGINT, _senal)
    reencoladas = ejecuciones.reencolar_interrumpidas()
    if reencoladas:
        logger.warning("asistente worker: %s run(s) interrumpidos vuelven a queued", reencoladas)
    with PostgresSaver.from_conn_string(_checkpoint_dsn()) as checkpointer:
        checkpointer.setup()
        grafo_compilado = grafo._armar(checkpointer)
        logger.info("asistente worker listo")
        while not _detener.is_set():
            run = ejecuciones.reclamar()
            if run is None:
                _detener.wait(_POLL_S)
                continue
            procesar(run, grafo_compilado)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
