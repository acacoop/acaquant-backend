"""api/telemetria.py — telemetría de uso por módulo (usuario × módulo × hora).

Doc: docs/OBSERVABILIDAD_ROBUSTEZ.md (commit 1). Alimenta `manager.uso_modulos`
(contador agregado por hora — NO un log por request) para el panel Manager → USO.

Diseño (lo crítico es NO tocar el hot path):
- El middleware llama `registrar_request(path, email)` en cada request: eso solo
  incrementa un dict EN MEMORIA bajo lock (nanosegundos, cero I/O).
- Cada ~60s un flush en THREAD aparte upsertea los contadores acumulados a SQL
  (ON CONFLICT ... hits = hits + EXCLUDED.hits). Best-effort: si SQL falla, se
  devuelven al buffer (con techo anti-crecimiento) y JAMÁS se rompe un request.
- Cada worker de uvicorn acumula y flushea lo suyo — los incrementos son
  aditivos, sin conflicto entre workers.
- El módulo del path se resuelve REUSANDO api.auth.get_module_for_path (el mapa
  ENDPOINT_MODULE_PREFIXES único — no hay mapa nuevo).

Identidad: se usan los headers saneados que setea el proxy (x-acaquant-user-email
/ cf-access-authenticated-user-email — los mismos de la rama 2a de get_user_email).
Es un CONTADOR de producto, no una superficie de seguridad: el gate real de cada
endpoint sigue siendo el RBAC. Se ignoran: emails vacíos, service tokens
(service:*), el portal invitado, health/me, MCP/OAuth y paths sin módulo mapeado.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 60
_MAX_BUFFER = 10_000     # techo del buffer si SQL está caído (se descarta lo demás)

_lock = threading.Lock()
_contadores: dict[tuple[str, str, str], int] = {}   # (email, modulo, hora_iso) → hits
_ultimo_flush = {"t": 0.0}

_PATHS_IGNORADOS = {"/api/health", "/api/me"}
_PREFIJOS_IGNORADOS = ("/mcp", "/oauth", "/.well-known")


def _hora_actual_iso() -> str:
    return datetime.now(UTC).replace(minute=0, second=0, microsecond=0).isoformat()


def registrar_request(path: str, email: str | None) -> bool:
    """Cuenta un request autenticado en el módulo del path. Solo memoria; nunca
    levanta. Devuelve True si contó (para tests)."""
    try:
        email = (email or "").lower().strip()
        if not email or email.startswith("service:"):
            return False
        if path in _PATHS_IGNORADOS or path.startswith(_PREFIJOS_IGNORADOS):
            return False
        from api.auth import get_module_for_path

        modulo = get_module_for_path(path)
        if not modulo:
            return False
        clave = (email, modulo, _hora_actual_iso())
        with _lock:
            _contadores[clave] = _contadores.get(clave, 0) + 1
        ahora = time.monotonic()
        if ahora - _ultimo_flush["t"] >= FLUSH_INTERVAL_S:
            _ultimo_flush["t"] = ahora
            threading.Thread(target=flush, daemon=True, name="telemetria-flush").start()
        return True
    except Exception as e:  # el hot path JAMÁS se rompe por telemetría
        logger.warning("telemetria: registrar falló (%s)", e)
        return False


def flush() -> int:
    """Upsertea lo acumulado a manager.uso_modulos. Best-effort: si SQL falla,
    devuelve los contadores al buffer (hasta _MAX_BUFFER). Retorna filas volcadas."""
    with _lock:
        if not _contadores:
            return 0
        pendientes = dict(_contadores)
        _contadores.clear()
    try:
        from core.postgres import get_pool

        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO manager.uso_modulos (email, modulo, hora, hits) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (email, modulo, hora) "
                "DO UPDATE SET hits = uso_modulos.hits + EXCLUDED.hits",
                [(e, m, h, n) for (e, m, h), n in pendientes.items()],
            )
        return len(pendientes)
    except Exception as e:
        logger.warning("telemetria: flush falló (%s) — devuelvo %s claves al buffer",
                       e, len(pendientes))
        with _lock:
            for k, n in pendientes.items():
                if len(_contadores) >= _MAX_BUFFER:
                    break
                _contadores[k] = _contadores.get(k, 0) + n
        return 0


def _reset_para_tests() -> None:
    with _lock:
        _contadores.clear()
    _ultimo_flush["t"] = time.monotonic()  # evita que un test dispare el thread


def _snapshot_para_tests() -> dict[tuple[str, str, str], int]:
    with _lock:
        return dict(_contadores)
