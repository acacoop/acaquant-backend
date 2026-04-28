"""Smoke real de envío + cancelación contra el broker LIVE.

PARANOIA: por default corre en dry-run. Para tocar el broker hay que
pasar `--live` explícito.

Flujo (con --live):
  1. send_order BUY 1 nominal AL30 24hs @ price (default 10 ARS, lejísimo
     del mercado: AL30 cotiza ~1400 ARS, así que NO matchea con nada).
  2. Espera 2s para que el broker registre.
  3. Lee Operaciones.OrdenesLive — debe haber 1 doc con clOrdId.
  4. cancel_order sobre ese clOrdId.
  5. Espera 2s.
  6. Lee Operaciones.OrdenesLive de nuevo.
  7. Imprime los últimos 6 events del audit log.

Sin motor_ordenes corriendo, el doc en OrdenesLive queda con
status=PENDING_NEW para siempre (el motor es el que actualiza con cada
ER del broker). Eso es esperable en esta fase. El smoke valida:
  - El broker acepta nuestro send_order y devuelve clOrdId.
  - El audit log se persiste correctamente (SEND_REQUEST + SEND_OK).
  - cancel_order ejecuta sin tirar excepción.

Correr:
    python -m scripts.smoke_ordenes_envio              # dry-run, no toca nada
    python -m scripts.smoke_ordenes_envio --live       # ENVÍA al broker
    python -m scripts.smoke_ordenes_envio --live --price 5    # custom price
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from api.services.ordenes import (  # noqa: E402
    cancel_order,
    get_order_status,
    send_order,
)
from core.mongo import get_mongo_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("smoke_ordenes_envio")

DEFAULT_TICKER = "MERV - XMEV - AL30 - 24hs"
DEFAULT_PRICE = 10.0  # ARS — AL30 cotiza ~1400, no matchea jamás
DEFAULT_SIZE = 1


def _print_audit(cl_ord_id: str, n: int = 6) -> None:
    db = get_mongo_client()["Operaciones"]
    docs = list(
        db["OrdenesAudit"]
        .find({"cl_ord_id": cl_ord_id}, {"_id": 0})
        .sort("ts", -1)
        .limit(n)
    )
    log.info("─── audit log para cl_ord_id=%s (últimos %d) ───", cl_ord_id, len(docs))
    for d in reversed(docs):
        log.info("  %s · %s · actor=%s", d.get("ts"), d.get("kind"), d.get("actor_email"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true",
                   help="Si no se pasa, hace dry-run (no envía al broker).")
    p.add_argument("--ticker", default=DEFAULT_TICKER)
    p.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
    p.add_argument("--size", type=int, default=DEFAULT_SIZE)
    p.add_argument("--price", type=float, default=DEFAULT_PRICE)
    p.add_argument("--tif", default="DAY", choices=["DAY", "IOC", "FOK", "GTC"])
    args = p.parse_args()

    log.info(
        "PARÁMETROS: ticker=%r side=%s size=%d price=%s tif=%s LIVE=%s",
        args.ticker, args.side, args.size, args.price, args.tif, args.live,
    )

    if not args.live:
        log.info("DRY-RUN — para enviar al broker pasá --live")
        return 0

    # ── 1. SEND
    log.info("→ enviando…")
    send_resp = send_order(
        ticker=args.ticker,
        side=args.side,
        size=args.size,
        order_type="LIMIT",
        price=args.price,
        tif=args.tif,
        actor_email="smoke@local",
    )
    log.info("send_order → %s", send_resp)
    if not send_resp.get("ok"):
        log.error("FALLO al enviar — abortando smoke. error=%s", send_resp.get("error"))
        return 1

    cl_ord_id = send_resp["cl_ord_id"]
    if not cl_ord_id:
        log.error("send_order OK pero sin cl_ord_id — broker_response=%s",
                  send_resp.get("broker_response"))
        return 1

    # ── 2. esperar y leer OrdenesLive
    time.sleep(2)
    doc = get_order_status(cl_ord_id)
    log.info("OrdenesLive (post-send) → %s", doc)
    if doc is None:
        log.error("send_order OK pero el doc no se persistió — bug en services/ordenes.py")
        return 1

    # ── 3. CANCEL
    log.info("→ cancelando %s…", cl_ord_id)
    cancel_resp = cancel_order(cl_ord_id, actor_email="smoke@local")
    log.info("cancel_order → %s", cancel_resp)

    # ── 4. esperar y leer de nuevo
    time.sleep(2)
    doc2 = get_order_status(cl_ord_id)
    log.info("OrdenesLive (post-cancel) → %s", doc2)

    # ── 5. audit
    _print_audit(cl_ord_id)

    if not cancel_resp.get("ok"):
        log.error("Cancel rechazado por broker — revisar manualmente que la orden no quede viva.")
        return 1

    log.info("OK · smoke completo. Si motor_ordenes NO está corriendo, "
             "el status puede quedar como PENDING_NEW (esperado).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
