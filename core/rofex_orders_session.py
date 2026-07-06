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
    """Identidad (strip). Se mantiene por compat con los call sites de display/metadata
    (listado de cuentas, id que el front reenvía).

    OJO — historia: antes esto padeaba a 3 dígitos ('9' → '009') asumiendo que ROFEX
    exigía padding. FALSO: se midió contra el broker (scripts/diag_rofex_cuenta) y NO hay
    regla — algunas cuentas ROFEX las quiere crudas ('15'), otras con cero ('009'), sin
    patrón. La traducción al número que ROFEX acepta la hace `resolver_cuenta_rofex`
    (prueba contra el broker + cachea), NO un formateo."""
    return (account or "").strip()


def cuenta_default() -> str:
    """Cuenta a usar cuando el caller no especifica una.

    V1 es 1 cuenta = la del .env. Cuando llegue el caso multi-cuenta,
    el frontend va a poder pasar `account=` y el service lo respeta;
    este helper sigue valiendo como fallback.
    """
    _, _, account, _ = _credenciales()
    return normalizar_cuenta(account)


# ─────────────────────────────────────────────────────────────────────────────
# Resolución del número de cuenta que ROFEX ACEPTA (medido, no derivado)
# ─────────────────────────────────────────────────────────────────────────────
# `clientes.cuentas.id_cuenta` perdió los ceros a la izquierda de forma INCONSISTENTE:
# la cuenta real de ROFEX de una es '004' y de otra es '6' — no hay regla de formateo
# que las cubra a las dos (verificado contra el broker). Fuente de verdad = ROFEX: se
# prueba `get_account_report` con las formas candidatas y se cachea la que responde OK.
_cuenta_rofex_cache: dict[str, str] = {}


def _formas_cuenta(s: str) -> list[str]:
    """Formas candidatas a probar contra ROFEX, sin repetir: crudo, zfill(3), zfill(4)."""
    out: list[str] = []
    for f in (s, s.zfill(3), s.zfill(4)):
        if f not in out:
            out.append(f)
    return out


def resolver_cuenta_rofex(account: str | None) -> str:
    """Devuelve el número de cuenta que ROFEX ACEPTA para `account` (medido, cacheado).

    ROFEX no deriva la cuenta del `id_cuenta` por formato → se prueba
    `get_account_report` con [crudo, zfill(3), zfill(4)] y se devuelve la primera con
    status OK + saldos. Solo se cachean los ACIERTOS (un fallo transitorio del broker no
    queda pegado). Si ninguna forma anda (o el account no es numérico) se devuelve tal
    cual → falla visible aguas abajo (ej. cuenta '11', que no tiene nº ROFEX válido)."""
    s = (account or "").strip()
    if not s or not s.isdigit():
        return s
    cached = _cuenta_rofex_cache.get(s)
    if cached is not None:
        return cached
    try:
        ensure_session_envio()          # sin sesión no se puede resolver
    except Exception:
        return s                        # devolvemos crudo; se reintenta en la próxima
    for f in _formas_cuenta(s):
        try:
            resp = pyRofex.get_account_report(account=f)
        except Exception:
            continue
        if (isinstance(resp, dict) and resp.get("status") == "OK"
                and (resp.get("accountData") or {}).get("detailedAccountReports")):
            _cuenta_rofex_cache[s] = f
            if f != s:
                logger.info("cuenta ROFEX resuelta: id_cuenta %r → %r", s, f)
            return f
    logger.warning("cuenta %r no tiene forma ROFEX válida (probé %s) — se manda cruda y "
                   "va a fallar; revisar el nº real de ese comitente", s, _formas_cuenta(s))
    return s


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
