"""Smoke de la sesión LIVE de órdenes — NO envía nada.

Valida 3 cosas, en orden:
  1. Las credenciales del .env (ROFEX_USER/PASSWORD/ACCOUNT + URLs)
     inicializan la sesión pyRofex LIVE.
  2. El broker autoriza a esa cuenta a consultar el endpoint de órdenes
     (`get_all_orders_status`). Algunos perfiles tienen market data pero
     no operaciones — si esto falla, hay que pedir habilitación al
     broker antes de seguir.
  3. La cuenta default que devuelve `cuenta_default()` matchea la del
     .env (sanity check, descarta typos).

Correr:
    python -m scripts.smoke_ordenes_login

Salida exitosa termina con "OK · listo para Fase B" y exit 0.
Cualquier fallo imprime el error y exit 1.
"""
from __future__ import annotations

import logging
import os
import sys

import pyRofex

from core.rofex_orders_session import (
    cuenta_default,
    inicializar_para_envio,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("smoke_ordenes_login")


def main() -> int:
    env_kind = os.getenv("ROFEX_ORDERS_ENV", "remarket")
    log.info("ROFEX_ORDERS_ENV=%s", env_kind)
    if env_kind != "live":
        log.error("Esperado ROFEX_ORDERS_ENV=live, encontrado %r. Abortando.", env_kind)
        return 1

    # 1) init
    try:
        account, env = inicializar_para_envio()
    except Exception as e:
        log.error("FALLO al inicializar sesión: %s", e)
        return 1
    log.info("Sesión inicializada — cuenta=%s env=%s", account, env.name)

    # 2) sanity: cuenta_default() == cuenta inicializada
    cd = cuenta_default()
    if cd != account:
        log.error("Mismatch cuenta_default()=%r vs init=%r", cd, account)
        return 1

    # 3) llamada read-only que requiere permiso de órdenes
    try:
        resp = pyRofex.get_all_orders_status(account=account)
    except Exception as e:
        log.error("FALLO get_all_orders_status: %s", e)
        return 1

    status = (resp or {}).get("status")
    if status != "OK":
        log.error("get_all_orders_status devolvió status=%r resp=%s", status, resp)
        return 1

    n_ords = len((resp or {}).get("orders", []) or [])
    log.info("get_all_orders_status OK — %d orden(es) en estado vivo en el broker", n_ords)

    log.info("OK · listo para Fase B (router HTTP + envío real)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
