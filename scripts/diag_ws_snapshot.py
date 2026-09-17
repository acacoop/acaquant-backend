"""scripts/diag_ws_snapshot.py — mide qué manda el WS de ROFEX al suscribirse con
`snapshot=False`, ANTES de diseñar el motor de "Órdenes del día" (REGLA #2: no
asumir, medir).

Herramienta: diag · one-shot · READ-ONLY total (solo `order_report_subscription`,
que es un mensaje de "avisame de esta cuenta" — jamás `send_order`/`cancel_order`).

LA PREGUNTA QUE RESPONDE
========================

`pyRofex.order_report_subscription(account, snapshot)` tiene un flag:
  - `snapshot=True`  (default): solo reports NUEVOS desde que te suscribís.
  - `snapshot=False`: en teoría también reenvía reports VIEJOS.

Si `snapshot=False` reenvía las órdenes que ya pasaron HOY (antes de suscribirse),
el motor de "Órdenes del día" casi no necesita REST — se suscribe a todas las
cuentas por WS y el broker le manda el historial del día solo. Si NO reenvía
nada, hace falta un backfill REST (`get_all_orders_status`) al arrancar, como
ya hace `_recovery` en `engines/motor_ordenes.py`, pero para TODAS las cuentas.

CÓMO SE USA
===========

Se suscribe (WS, snapshot=False) a las cuentas pasadas por `--cuentas` — usar
cuentas que YA tuvieron reports hoy (confirmado con `scripts/diag_rofex_crudo.py`)
para que cualquier cosa que llegue sea evidencia de reenvío, no de actividad nueva.
Escucha `--segundos` (default 25) y punta todo lo que reciba, con cuenta y hora.

Uso:
    python -m scripts.diag_ws_snapshot --cuentas 238,1749,167 --segundos 25
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime

os.environ["ROFEX_ORDERS_ENV"] = "live"  # ver comentario equivalente en diag_rofex_crudo.py

import pyRofex

from core.rofex_orders_session import cerrar_ws, inicializar_para_motor, resolver_cuenta_rofex

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_lock = threading.Lock()
_recibidos: list[dict] = []


def _handler(msg: dict) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]
    rep = msg.get("orderReport", msg) if isinstance(msg, dict) else msg
    with _lock:
        _recibidos.append({"hora_recibido": ts, "raw": rep})
    acc = (rep.get("accountId") or {}).get("id") if isinstance(rep, dict) else "?"
    print(f"[{ts}] ← orderReport cuenta={acc} clOrdId={rep.get('clOrdId')} "
          f"status={rep.get('status')} transactTime={rep.get('transactTime')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mide el reenvío de snapshot=False en WS (READ-ONLY).")
    ap.add_argument("--cuentas", required=True, help="cuentas con reports HOY conocidos, separadas por coma")
    ap.add_argument("--segundos", type=float, default=25, help="cuánto escuchar antes de cerrar (default 25)")
    args = ap.parse_args()
    cuentas = [c.strip() for c in args.cuentas.split(",") if c.strip()]

    print("Entorno pyRofex: LIVE (forzado, no toca .env ni motores)")
    print(f"Cuentas a suscribir (snapshot=False): {cuentas}\n")

    try:
        account_default, env = inicializar_para_motor(_handler)
        print(f"WS abierto. Cuenta default ya suscripta: {account_default!r} (env={env.name})\n")
    except Exception as e:
        print(f"✗ No se pudo abrir el WS: {e}")
        raise SystemExit(2) from None

    resueltas = {}
    for c in cuentas:
        try:
            r = resolver_cuenta_rofex(c)
        except Exception as e:
            print(f"✗ resolver_cuenta_rofex({c!r}) falló: {e}")
            continue
        resueltas[c] = r
        try:
            pyRofex.order_report_subscription(account=r, snapshot=False)
            print(f"→ suscripto (snapshot=False) cuenta_pedida={c} cuenta_rofex={r!r}")
        except Exception as e:
            print(f"✗ order_report_subscription({r!r}) falló: {e}")
        time.sleep(0.3)  # no ráfaguear el broker con N subscribes seguidos

    print(f"\nEscuchando {args.segundos:.0f}s… (Ctrl+C corta antes)\n")
    try:
        time.sleep(args.segundos)
    except KeyboardInterrupt:
        pass

    cerrar_ws()

    print("\n" + "=" * 78)
    print(f"RESULTADO: {len(_recibidos)} orderReport(s) recibido(s) en la ventana.")
    print("=" * 78)
    if not _recibidos:
        print("Ninguno. → snapshot=False NO reenvió historial: hace falta REST de backfill.")
    else:
        por_cuenta: dict[str, int] = {}
        for r in _recibidos:
            acc = (r["raw"].get("accountId") or {}).get("id") if isinstance(r["raw"], dict) else "?"
            por_cuenta[str(acc)] = por_cuenta.get(str(acc), 0) + 1
        for acc, n in sorted(por_cuenta.items()):
            print(f"  cuenta {acc}: {n} report(s)")
        print("\n→ snapshot=False SÍ reenvía historial (o llegó actividad nueva real — "
              "revisar 'transactTime' de cada uno contra la hora actual para distinguir).")
        print("\nCrudo de lo recibido:")
        for r in _recibidos:
            print(json.dumps(r, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
