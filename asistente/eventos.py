"""Canal contextual para observar una ejecución mientras LangGraph avanza."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)


class InterrupcionRun(Exception):
    """Corte esperado entre dos pasos; nunca se degrada a fallo de telemetría."""


class CancelacionSolicitada(InterrupcionRun):
    """La ejecución fue cancelada entre dos pasos cooperativos."""


class TiempoAgotado(InterrupcionRun):
    """La ejecución superó su presupuesto entre dos pasos cooperativos."""


_sink: ContextVar[Callable[[dict], None] | None] = ContextVar("asistente_event_sink", default=None)


@contextmanager
def capturar(sink: Callable[[dict], None] | None) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def emitir(evento: dict) -> dict:
    """Entrega el evento al run activo y también lo devuelve para el estado."""
    sink = _sink.get()
    if sink is not None:
        try:
            sink(evento)
        except InterrupcionRun:
            raise
        except Exception as error:
            logger.warning("asistente: no pude persistir evento (%s)", error)
    return evento
