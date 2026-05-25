"""api/profiling.py — Middleware opt-in de profiling de requests (pyinstrument).

Activado SOLO si `config.API_PROFILING` está prendido (env `API_PROFILING=1`).
Cuando está activo, cualquier request con `?profile=...` se ejecuta bajo un
statistical profiler y, en vez de la respuesta normal, devuelve el perfil:

    GET /api/algo?profile=1            → HTML con el árbol de llamadas (% por nodo)
    GET /api/algo?profile=speedscope   → JSON para abrir en https://speedscope.app

Sin `?profile`, el request pasa derecho (overhead nulo). Si `API_PROFILING`
está OFF, el middleware NI SE MONTA (ver api/main.py) → cero costo en prod.

Por qué pyinstrument y no cProfile:
  - Es un *statistical* profiler (samplea el stack cada `interval`), no
    instrumenta cada llamada → overhead bajo y números realistas.
  - Entiende async: con `async_mode="enabled"` no le imputa a tu código el
    tiempo que el request pasa en `await` de Mongo/red — que es justo lo que
    queremos distinguir (CPU tuyo vs espera de I/O).

Lectura del output (HTML): cada fila es una función con su % del tiempo total.
Si arriba de todo ves `pymongo`/`socket`/`asyncio` → I/O-bound (la respuesta
es índices/shape de query/caching). Si ves una función tuya de `quant/` o un
loop en un service → ahí sí hay cómputo para vectorizar.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response


class PyinstrumentMiddleware(BaseHTTPMiddleware):
    """Perfila el request cuando viene `?profile=...`; si no, no hace nada."""

    async def dispatch(self, request: Request, call_next):
        modo = request.query_params.get("profile")
        if modo is None:
            return await call_next(request)

        # Import perezoso: solo si realmente se pide perfilar (y así el módulo
        # importa aunque pyinstrument no esté instalado, mientras no se use).
        from pyinstrument import Profiler

        profiler = Profiler(interval=0.001, async_mode="enabled")
        profiler.start()
        try:
            await call_next(request)
        finally:
            profiler.stop()

        if modo == "speedscope":
            from pyinstrument.renderers.speedscope import SpeedscopeRenderer

            return Response(
                profiler.output(renderer=SpeedscopeRenderer()),
                media_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="profile.speedscope.json"'},
            )
        return HTMLResponse(profiler.output_html())


def maybe_add_profiler(app) -> bool:
    """Monta el middleware si `API_PROFILING` está prendido. Devuelve si lo montó."""
    from config import API_PROFILING

    if not API_PROFILING:
        return False
    app.add_middleware(PyinstrumentMiddleware)
    return True
