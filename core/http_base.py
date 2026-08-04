"""core/http_base.py — piezas HTTP compartidas por los clientes externos de core/.

Antes cada cliente (finnhub, bcra_api, fred_api, argentina_datos) copiaba su
propio throttle / rate-limiter / loop de reintentos / manejo de status → un fix
(p.ej. respetar Retry-After, o el bug de dormir con el lock tomado) había que
aplicarlo N veces. Acá viven una sola vez; cada cliente queda como wrapper fino
de sus endpoints (que es lo único específico que tiene).

Regla de capas: core/ no importa nada del proyecto — este módulo solo usa stdlib
+ requests.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

logger = logging.getLogger("core.http_base")


class Throttle:
    """Intervalo mínimo entre requests (thread-safe). Patrón BCRA/FRED."""

    def __init__(self, min_interval_s: float):
        self.min_interval_s = float(min_interval_s)
        self._lock = threading.Lock()
        self._ultima = 0.0

    def wait(self) -> None:
        with self._lock:
            espera = self.min_interval_s - (time.monotonic() - self._ultima)
            if espera > 0:
                time.sleep(espera)
            self._ultima = time.monotonic()


class RateLimiter:
    """Sliding window de N calls por minuto (thread-safe). Patrón Finnhub.

    El sleep ocurre FUERA del lock (a diferencia de las copias viejas, que
    dormían con el lock tomado y serializaban todos los threads del proceso
    durante la espera); el re-chequeo en loop mantiene el límite."""

    def __init__(self, max_per_min: int):
        self.max_per_min = int(max_per_min)
        self._lock = threading.Lock()
        self._calls_ts: list[float] = []

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.time()
                self._calls_ts[:] = [t for t in self._calls_ts if now - t < 60]
                if len(self._calls_ts) < self.max_per_min:
                    self._calls_ts.append(now)
                    return
                sleep_s = 60 - (now - self._calls_ts[0]) + 0.2
            if sleep_s > 0:
                logger.debug("rate limit cerca; sleep %.2fs", sleep_s)
                time.sleep(sleep_s)


def get_json(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 15,
    exc: type[Exception] = RuntimeError,
    reintentos: int = 1,
    throttle: Throttle | RateLimiter | None = None,
    mensajes_status: dict[int, str] | None = None,
) -> Any:
    """GET + manejo de status + parse JSON, con throttle y reintentos opcionales.

    - `reintentos=1` → un solo intento (falla directo en red/status).
    - Con `reintentos>1`, reintenta errores de red (sleep 3·intento) y
      429/5xx (backoff exponencial min(5·2^i, 40)) — el loop exacto que
      compartían bcra_api y fred_api.
    - `mensajes_status`: mensaje custom por status (p.ej. 401 → 'key inválida').
    - Todo error sale como `exc` (la jerarquía propia de cada cliente).
    """
    ultimo = ""
    for intento in range(max(1, reintentos)):
        if throttle is not None:
            throttle.wait()
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
            if intento + 1 < reintentos:
                time.sleep(3 * (intento + 1))
                continue
            raise exc(f"red: {e}") from e
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError as e:
                raise exc(f"respuesta no-JSON: {r.text[:200]}") from e
        if (r.status_code == 429 or r.status_code >= 500) and intento + 1 < reintentos:
            ultimo = f"HTTP {r.status_code}"
            time.sleep(min(5 * 2 ** intento, 40))
            continue
        msg = (mensajes_status or {}).get(r.status_code)
        raise exc(msg or f"HTTP {r.status_code}: {r.text[:200]}")
    raise exc(f"agotados {reintentos} reintentos ({ultimo})")
