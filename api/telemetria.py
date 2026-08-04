"""api/telemetria.py — telemetría de LATENCIA por endpoint (endpoint × hora).

Reemplaza a la telemetría de USO (usuario × módulo, `manager.uso_modulos`) —
decomisada 2026-08-04 por pedido del user: nunca se usó. Lo que SÍ sirve para
operar es saber QUÉ endpoint está lento HOY y su tendencia — eso alimenta esta
tabla y mata el ciclo de diags one-shot que se desactualizan.

Alimenta `manager.latencia_endpoints` (agregado por endpoint × hora — NO un log
por request) para `GET /api/manager/latencia`.

Diseño (idéntico patrón probado del contador anterior — lo crítico es NO tocar
el hot path):
- El middleware llama `registrar_request(path, dur_ms, status)` en cada request:
  solo acumula en un dict EN MEMORIA bajo lock (nanosegundos, cero I/O).
- Cada ~60s un flush en THREAD aparte upsertea a SQL (ON CONFLICT acumula).
  Best-effort: si SQL falla, lo pendiente vuelve al buffer (con techo) y JAMÁS
  se rompe un request.
- El path se NORMALIZA (segmentos numéricos/UUID → {id}) para que /valuaciones/805
  y /valuaciones/912 agreguen en la misma serie.
- Sin identidad: es telemetría de operación, no de producto — no registra emails.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 60
_MAX_BUFFER = 5_000      # techo de claves si SQL está caído (se descarta lo demás)
UMBRAL_LENTA_MS = 1_000  # request "lenta" para el contador dedicado

_lock = threading.Lock()
# (endpoint, hora_iso) → [n, total_ms, max_ms, lentas, errores]
_acum: dict[tuple[str, str], list[int]] = {}
_ultimo_flush = {"t": 0.0}

_PATHS_IGNORADOS = {"/api/health"}
_PREFIJOS_IGNORADOS = ("/mcp", "/oauth", "/.well-known")

_RE_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _normalizar(path: str) -> str:
    """Colapsa los segmentos variables del path para agregar por ENDPOINT:
    /api/valuaciones/805/mensual → /api/valuaciones/{id}/mensual."""
    out = []
    for seg in path.split("?")[0].split("/"):
        if seg.isdigit() or _RE_UUID.match(seg) or len(seg) > 48:
            out.append("{id}")
        else:
            out.append(seg)
    return "/".join(out)[:200]


def _hora_actual_iso() -> str:
    return datetime.now(UTC).replace(minute=0, second=0, microsecond=0).isoformat()


def registrar_request(path: str, dur_ms: float, status: int) -> bool:
    """Acumula la latencia de un request. Solo memoria; nunca levanta.
    Devuelve True si registró (para tests)."""
    try:
        if path in _PATHS_IGNORADOS or path.startswith(_PREFIJOS_IGNORADOS):
            return False
        if not path.startswith("/api/"):
            return False
        clave = (_normalizar(path), _hora_actual_iso())
        ms = int(dur_ms)
        with _lock:
            fila = _acum.get(clave)
            if fila is None:
                if len(_acum) >= _MAX_BUFFER:
                    return False
                fila = _acum[clave] = [0, 0, 0, 0, 0]
            fila[0] += 1
            fila[1] += ms
            fila[2] = max(fila[2], ms)
            if ms > UMBRAL_LENTA_MS:
                fila[3] += 1
            if status >= 500:
                fila[4] += 1
        ahora = time.monotonic()
        if ahora - _ultimo_flush["t"] >= FLUSH_INTERVAL_S:
            _ultimo_flush["t"] = ahora
            threading.Thread(target=flush, daemon=True, name="telemetria-flush").start()
        return True
    except Exception as e:  # el hot path JAMÁS se rompe por telemetría
        logger.warning("telemetria: registrar falló (%s)", e)
        return False


def flush() -> int:
    """Upsertea lo acumulado a manager.latencia_endpoints. Best-effort: si SQL
    falla, lo pendiente vuelve al buffer (hasta _MAX_BUFFER). Retorna filas."""
    with _lock:
        if not _acum:
            return 0
        pendientes = dict(_acum)
        _acum.clear()
    try:
        from core.postgres import get_pool

        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO manager.latencia_endpoints "
                "(endpoint, hora, n, total_ms, max_ms, lentas, errores) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (endpoint, hora) DO UPDATE SET "
                "n = latencia_endpoints.n + EXCLUDED.n, "
                "total_ms = latencia_endpoints.total_ms + EXCLUDED.total_ms, "
                "max_ms = GREATEST(latencia_endpoints.max_ms, EXCLUDED.max_ms), "
                "lentas = latencia_endpoints.lentas + EXCLUDED.lentas, "
                "errores = latencia_endpoints.errores + EXCLUDED.errores",
                [(e, h, *v) for (e, h), v in pendientes.items()],
            )
        return len(pendientes)
    except Exception as e:
        logger.warning("telemetria: flush falló (%s) — devuelvo %s claves al buffer",
                       e, len(pendientes))
        with _lock:
            for k, v in pendientes.items():
                if len(_acum) >= _MAX_BUFFER:
                    break
                fila = _acum.get(k)
                if fila is None:
                    _acum[k] = list(v)
                else:
                    fila[0] += v[0]
                    fila[1] += v[1]
                    fila[2] = max(fila[2], v[2])
                    fila[3] += v[3]
                    fila[4] += v[4]
        return 0


def _reset_para_tests() -> None:
    with _lock:
        _acum.clear()
    _ultimo_flush["t"] = time.monotonic()  # evita que un test dispare el thread


def _snapshot_para_tests() -> dict[tuple[str, str], list[int]]:
    with _lock:
        return {k: list(v) for k, v in _acum.items()}
