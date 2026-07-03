"""Sesión pyRofex dedicada a envío/seguimiento de órdenes.

Separa la sesión de "operaciones" de `core.rofex_session` (que usan los
motores de market data). Razones:
  - pyRofex es singleton por proceso. El motor de órdenes vive en su
    propio systemd unit, distinto al `motor_rofex` de market data, así
    que cada uno tiene su propia sesión sin pisarse.
  - El API también necesita levantar una sesión liviana para REST
    `send_order` / `cancel_order` (sin WS) — usa `inicializar_para_envio()`.
  - El motor de órdenes usa `inicializar_para_motor()` que sí abre el WS
    y registra el handler de order reports.

Modos:
  - REMARKET: sandbox de Primary/Rofex 24/7. Credenciales en
    ROFEX_USER_REMARKET / ROFEX_PASSWORD_REMARKET / ROFEX_ACCOUNT_REMARKET.
    Default V1 hasta que el smoke esté validado.
  - LIVE: ACA Valores. Reusa ROFEX_USER / ROFEX_PASSWORD / ROFEX_ACCOUNT.

La elección se controla con la env var `ROFEX_ORDERS_ENV`
("remarket" | "live", default "remarket"). El motor y el API leen lo
mismo, así que viven siempre en el mismo entorno.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import pyRofex

logger = logging.getLogger("rofex_orders_session")


_MAX_RETRIES = 3
_RETRY_DELAY_S = 2  # backoff lineal

# Singleton del estado de inicialización REST. pyRofex es process-scoped:
# una sola sesión inicializada por proceso. Múltiples services del API
# (ordenes, risk, …) comparten esto vía `ensure_session_envio()` para
# evitar reinicializaciones redundantes.
_envio_ready: bool = False
_envio_lock = threading.Lock()


def _env_kind() -> str:
    return os.getenv("ROFEX_ORDERS_ENV", "remarket").strip().lower()


def _credenciales() -> tuple[str, str, str, pyRofex.Environment]:
    """Devuelve (user, password, account, env) según ROFEX_ORDERS_ENV.

    No valida que las creds estén — eso lo hace `inicializar_*` y falla
    con un mensaje claro si falta alguna.
    """
    kind = _env_kind()
    if kind == "live":
        return (
            os.getenv("ROFEX_USER", ""),
            os.getenv("ROFEX_PASSWORD", ""),
            os.getenv("ROFEX_ACCOUNT", ""),
            pyRofex.Environment.LIVE,
        )
    return (
        os.getenv("ROFEX_USER_REMARKET", ""),
        os.getenv("ROFEX_PASSWORD_REMARKET", ""),
        os.getenv("ROFEX_ACCOUNT_REMARKET", ""),
        pyRofex.Environment.REMARKET,
    )


def normalizar_cuenta(account: str | None) -> str:
    """Cuenta ROFEX = MÍNIMO 3 dígitos con ceros a la izquierda ('9' → '009').

    ROFEX no vincula la cuenta ni trae saldos si va sin padding. El `id_cuenta` de
    `clientes.cuentas` quedó guardado sin ceros a la izquierda (las de 1-2 dígitos), así
    que normalizamos SIEMPRE antes de hablar con el broker. Es un no-op para cuentas de
    3+ dígitos y para valores no numéricos (no rompe nada existente)."""
    s = (account or "").strip()
    return s.zfill(3) if s.isdigit() else s


def cuenta_default() -> str:
    """Cuenta a usar cuando el caller no especifica una.

    V1 es 1 cuenta = la del .env. Cuando llegue el caso multi-cuenta,
    el frontend va a poder pasar `account=` y el service lo respeta;
    este helper sigue valiendo como fallback.
    """
    _, _, account, _ = _credenciales()
    return normalizar_cuenta(account)


def _do_initialize() -> tuple[str, pyRofex.Environment]:
    """Setea env params + llama pyRofex.initialize. Devuelve (account, env).

    Si en LIVE están definidas ROFEX_API_URL / ROFEX_WS_URL en el .env,
    las propaga al environment LIVE (paridad con `core.rofex_session`).
    """
    user, password, account, env = _credenciales()
    if not (user and password and account):
        raise RuntimeError(
            f"Credenciales incompletas para entorno {_env_kind()}: "
            "verificá ROFEX_USER[_REMARKET]/PASSWORD/ACCOUNT en el .env"
        )

    if env == pyRofex.Environment.LIVE:
        url = os.getenv("ROFEX_API_URL")
        ws = os.getenv("ROFEX_WS_URL")
        if url:
            pyRofex._set_environment_parameter("url", url, env)
        if ws:
            pyRofex._set_environment_parameter("ws", ws, env)

    pyRofex.initialize(user=user, password=password, account=account, environment=env)
    return account, env


def _initialize_with_retry() -> tuple[str, pyRofex.Environment]:
    last_err: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            account, env = _do_initialize()
            logger.info(
                "Sesión pyRofex (órdenes) inicializada [%s] cuenta=%s intento %d/%d",
                env.name, account, attempt, _MAX_RETRIES,
            )
            return account, env
        except Exception as e:
            last_err = e
            logger.warning(
                "Fallo inicializando sesión pyRofex (órdenes) %d/%d: %s",
                attempt, _MAX_RETRIES, e,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY_S * attempt)
    raise RuntimeError(f"No se pudo inicializar sesión pyRofex (órdenes): {last_err}")


def inicializar_para_envio() -> tuple[str, pyRofex.Environment]:
    """Sesión REST-only para enviar/cancelar órdenes desde el API.

    No abre WS, no suscribe a nada. Devuelve (account, env) para que el
    caller persista el contexto en cada doc de Mongo.
    """
    return _initialize_with_retry()


def ensure_session_envio() -> str:
    """Idempotente: inicializa la sesión REST la primera vez y devuelve
    la cuenta default. Pensada para ser llamada desde cualquier service
    del API que necesite pegar a pyRofex (REST). Thread-safe.
    """
    global _envio_ready
    if _envio_ready:
        return cuenta_default()
    with _envio_lock:
        if not _envio_ready:
            inicializar_para_envio()
            _envio_ready = True
    return cuenta_default()


def inicializar_para_motor(order_report_handler) -> tuple[str, pyRofex.Environment]:
    """Sesión completa para el motor de órdenes: REST + WS + order reports.

    `order_report_handler` recibe un dict con la forma:
        {"orderReport": {clOrdId, wsClOrdId, status, lastPx, lastQty, ...}}
    El motor le hace upsert a `Operaciones.OrdenesLive` y append a
    `Operaciones.OrdenesAudit`.

    Si el broker corta el WS, pyRofex tira `_on_close`. Acá NO manejamos
    reconnect — el motor lo detecta vía heartbeat y reinicializa toda
    la sesión llamando a esta función de nuevo (más robusto que
    intentar parchar la sesión vieja).
    """
    account, env = _initialize_with_retry()

    def _error_handler(message):
        logger.error("WS error: %s", message)

    def _exception_handler(e):
        logger.error("WS exception: %s", e, exc_info=True)

    pyRofex.init_websocket_connection(
        order_report_handler=order_report_handler,
        error_handler=_error_handler,
        exception_handler=_exception_handler,
    )
    pyRofex.order_report_subscription(account=account)
    logger.info("WS suscripto a order_report (cuenta=%s, env=%s)", account, env.name)
    return account, env


def cerrar_ws() -> None:
    """Cierra el WS. Idempotente."""
    try:
        pyRofex.close_websocket_connection()
    except Exception as e:
        logger.warning("Error cerrando WS de órdenes: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    acc, env = inicializar_para_envio()
    print(f"OK · cuenta={acc} env={env.name}")
