"""core/mercado_1816.py — cliente de la API de Mercado de 1816 (vista RESEARCH).

Doc madre: docs/VISTA_RESEARCH.md. Cambiar de proveedor / URL = tocar solo acá.

Lo que resuelve (verificado con scripts/diag_1816.py el 2026-07-18):
- **Auth**: POST /v1/auth/token (apiKey + module=mercado) → JWT 24h. Cacheado en
  memoria del proceso; re-auth automático al vencer o ante 401.
- **RATE LIMIT DURO (429)**: la API tira "Demasiadas solicitudes" al encadenar
  llamadas. Por eso hay THROTTLE (intervalo mínimo entre requests) + BACKOFF
  exponencial en 429. NADA de hammering — este cliente es para jobs de fondo, la
  latencia no importa.
- **Créditos**: se controlan con balance() (100k/día, 3.1M/mes); el consumo por
  endpoint está en docs/VISTA_RESEARCH.md §4.2.

Env vars (.env del Droplet):
  MERCADO_1816_API_KEY   — sin ella el cliente está apagado (disponible() = False).
  MERCADO_1816_BASE_URL  — override, default https://api.1816.com.ar (verificado).
"""
from __future__ import annotations

import logging
import os
import threading
import time

from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

logger = logging.getLogger(__name__)

_BASE = os.getenv("MERCADO_1816_BASE_URL", "https://api.1816.com.ar").rstrip("/")
_MIN_INTERVALO_S = 2.5   # throttle mínimo entre llamadas (contra el 429)
_TIMEOUT = 45
_MAX_REINTENTOS = 5

_lock = threading.Lock()
_estado: dict = {"token": None, "exp": 0.0, "ultima": 0.0}


class Error1816(RuntimeError):
    """Fallo de la API de 1816 (auth, rate limit agotado, HTTP no-200)."""


def _api_key() -> str | None:
    return os.getenv("MERCADO_1816_API_KEY")


def disponible() -> bool:
    """True si hay API key configurada (sin ella el cliente no opera)."""
    return bool(_api_key())


def _auth() -> str:
    key = _api_key()
    if not key:
        raise Error1816("falta MERCADO_1816_API_KEY")
    import requests

    r = requests.post(f"{_BASE}/v1/auth/token",
                      json={"apiKey": key, "module": "mercado"}, timeout=_TIMEOUT)
    if r.status_code != 200:
        raise Error1816(f"auth HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    tok = d.get("token")
    if not tok:
        raise Error1816("auth sin token en la respuesta")
    _estado["token"] = tok
    _estado["exp"] = time.time() + int(d.get("expiresIn", 86400)) - 300  # 5 min margen
    logger.info("mercado_1816: token renovado (expira en %ss)", d.get("expiresIn"))
    return tok


def _token() -> str:
    with _lock:
        if not _estado["token"] or time.time() >= _estado["exp"]:
            _auth()
        return _estado["token"]


def _throttle() -> None:
    """Garantiza _MIN_INTERVALO_S entre llamadas (rate limit). Serializa con el lock
    para que dos hilos no disparen juntos."""
    with _lock:
        espera = _MIN_INTERVALO_S - (time.monotonic() - _estado["ultima"])
        if espera > 0:
            time.sleep(espera)
        _estado["ultima"] = time.monotonic()


def _get(path: str, params: dict | None = None) -> dict:
    """GET autenticado con throttle + backoff en 429 + re-auth en 401. Levanta
    Error1816 si se agotan los reintentos o ante un 4xx no recuperable."""
    import requests

    ultimo = ""
    for intento in range(_MAX_REINTENTOS):
        _throttle()
        try:
            r = requests.get(f"{_BASE}{path}",
                             headers={"Authorization": f"Bearer {_token()}"},
                             params=params, timeout=_TIMEOUT)
        except requests.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
            time.sleep(3 * (intento + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:                      # rate limit → backoff
            espera = min(5 * 2 ** intento, 60)
            logger.warning("mercado_1816 429 en %s — backoff %ss (intento %s)",
                           path, espera, intento + 1)
            time.sleep(espera)
            continue
        if r.status_code == 401:                      # token vencido → re-auth
            with _lock:
                _estado["token"] = None
            ultimo = "401 (re-auth)"
            continue
        if r.status_code >= 500:                      # server → retry
            ultimo = f"HTTP {r.status_code}"
            time.sleep(3 * (intento + 1))
            continue
        raise Error1816(f"{path} HTTP {r.status_code}: {r.text[:200]}")  # 4xx no-recup.
    raise Error1816(f"{path}: agotados {_MAX_REINTENTOS} reintentos ({ultimo})")


# ── Endpoints ────────────────────────────────────────────────────────────────


def balance() -> dict:
    """{daily:{used,limit}, monthly:{used,limit}} — control de créditos."""
    return _get("/v1/creditos/balance")


def curvas(texto: str | None = None) -> list[dict]:
    return _get("/v1/mercado/curvas", {"texto": texto} if texto else None)


def instrumentos(texto: str | None = None, curva_id: int | None = None) -> list[dict]:
    p: dict = {}
    if texto:
        p["texto"] = texto
    if curva_id:
        p["curvaId"] = curva_id
    return _get("/v1/mercado/instrumentos", p)


def indicadores(tickers: list[str], campos: list[str], fuente: str = "byma",
                plazo: int = 1, moneda: str = "ars",
                fecha_operacion: str | None = None) -> dict:
    """Indicadores a precio de mercado (≤50 tickers por llamada). Devuelve
    {fechaOperacion, fuente, plazo, moneda, instrumentos: {ticker: {campo: v}}}.
    Costo: tickers × campos. OJO: el default de la API es fechaOperacion=HOY —
    en fin de semana/feriado devuelve vacío (aprendido 2026-07-18, corrida de
    sábado con 0 datos): pasar `fecha_operacion` del último día hábil."""
    p: dict = {"tickers": list(tickers)[:50], "campos": list(campos),
               "fuente": fuente, "plazo": plazo, "moneda": moneda}
    if fecha_operacion:
        p["fechaOperacion"] = fecha_operacion
    return _get("/v1/mercado/indicadores", p)


def series(tickers: list[str], campos: list[str], desde: str, hasta: str,
           fuente: str = "byma", plazo: int = 1, moneda: str = "ars",
           convencion: str | None = None) -> dict:
    """Series históricas (≤10 tickers, ≤1 año). Devuelve el JSON crudo de la API;
    usar parse_series() para aplanarlo. Costo: tickers × campos × días."""
    p: dict = {"tickers": list(tickers), "campos": list(campos),
               "fechaInicial": desde, "fechaFinal": hasta,
               "fuente": fuente, "plazo": plazo, "moneda": moneda}
    if convencion:
        p["convencionTna"] = convencion
    return _get("/v1/mercado/series", p)


def parse_series(data: dict) -> list[dict]:
    """{instrumentos:{ticker:{campo:[[fecha,valor],…]}}} → filas tidy
    [{ticker, fecha, campo, valor, fuente, moneda, plazo, convencion_tna}]. Pura
    (testeable sin red). Saltea puntos con valor null o mal formados."""
    meta = {
        "fuente": data.get("fuente"), "moneda": data.get("moneda"),
        "plazo": data.get("plazo"), "convencion_tna": data.get("convencionTna"),
    }
    out: list[dict] = []
    for ticker, campos in (data.get("instrumentos") or {}).items():
        for campo, serie in (campos or {}).items():
            for punto in serie or []:
                if not isinstance(punto, (list, tuple)) or len(punto) < 2:
                    continue
                fecha, valor = punto[0], punto[1]
                if fecha is None or valor is None:
                    continue
                out.append({"ticker": ticker, "fecha": fecha, "campo": campo,
                            "valor": valor, **meta})
    return out
