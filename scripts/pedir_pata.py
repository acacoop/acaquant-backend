"""scripts/pedir_pata.py — PEDIRLE UNA PATA A PRIMARY **SIN REINICIAR NINGÚN MOTOR**.

*«Es imposible que no opere la pata en dólares, está mal algo.»* (user, y tenía
razón: nadie la estaba pidiendo.)

QUÉ SE APRENDIÓ Y POR QUÉ ESTE SCRIPT EXISTE
=============================================

**`motor_curvas` NO suscribe nada.** Su propio log lo dice —*«Escuchando N
tickers vía MarketSnapshot»*—: es un CONSUMIDOR que lee `mercado.market_snapshot`
y le calcula TEA, paridad y duration. Reiniciarlo no cambia ninguna suscripción.

El que le pide los precios a Primary es **`motor_rofex`** (`engines/valores.py`),
y arma su universo AL ARRANCAR desde `mercado.curvas`. O sea que un símbolo nuevo
en el master no se pide hasta el próximo arranque.

**Pero no hace falta reiniciarlo**: ese motor tiene un `adhoc_watcher` que cada 5
segundos lee `mercado.adhoc_subscriptions` y suscribe lo que no esté — *«las
suscripciones pyRofex son aditivas, no rompen las existentes»*. Es la vía para
pedir algo **en plena rueda, sin cortarle el feed a la mesa**.

Y contesta la pregunta que ninguna de nuestras tablas podía contestar: **¿esta
pata cotiza?** Hasta ahora «no tiene precio» solo significaba «nadie la escucha».
Pedirla es la única forma de convertir esa ausencia en un dato.

    python -m scripts.pedir_pata AO29D CO32D     # por el corto: resuelve el símbolo
    python -m scripts.pedir_pata --ver           # qué hay pedido y qué llegó
"""
from __future__ import annotations

import sys

from core import adhoc_subscriptions as adhoc
from core.postgres import get_pool


def _resolver(pedidos: list[str]) -> list[str]:
    """`AO29D` → el símbolo completo de `mercado.especies`. Nada de armarlo a
    mano: un string mal compuesto se suscribe igual y no llega nunca."""
    out: list[str] = []
    with get_pool().connection() as conn, conn.cursor() as cur:
        for p in pedidos:
            if " - " in p:                      # ya vino el símbolo entero
                out.append(p)
                continue
            cur.execute(
                "SELECT simbolo FROM mercado.especies "
                "WHERE split_part(simbolo, ' - ', 3) = %s "
                "ORDER BY (plazo = '24hs') DESC, simbolo LIMIT 1", (p.upper(),))
            r = cur.fetchone()
            if r:
                out.append(r[0])
            else:
                print(f"  ✖ «{p}» no existe en `mercado.especies` — no lo invento")
    return out


def _ver() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, created_at, expires_at "
                    "FROM mercado.adhoc_subscriptions WHERE expires_at > now() "
                    "ORDER BY created_at DESC")
        filas = cur.fetchall()
        if not filas:
            print("\n  no hay ninguna suscripción adhoc activa.\n")
            return 0
        simbolos = [f[0] for f in filas]
        cur.execute("SELECT ticker, last_price, updated_at "
                    "FROM mercado.market_snapshot WHERE ticker = ANY(%s)",
                    (simbolos,))
        snap = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    print(f"\n{len(filas)} suscripciones adhoc activas\n" + "=" * 78)
    for tk, creada, vence in filas:
        px, upd = snap.get(tk, (None, None))
        estado = f"px={px}  ({upd})" if px else "**todavía sin precio**"
        print(f"  {tk:<40} {estado}")
        print(f"  {'':<40} pedida {creada:%Y-%m-%d %H:%M} · vence {vence:%Y-%m-%d}")
    print("\n  Si pasa una rueda entera y sigue sin precio, ahí SÍ se puede decir\n"
          "  que esa pata no cotiza — antes no.\n")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--ver" in sys.argv or not args:
        return _ver()

    simbolos = _resolver(args)
    if not simbolos:
        return 1
    print()
    for s in simbolos:
        r = adhoc.subscribe(s)
        estado = "✔ pedida" if r.get("ok") else f"✖ {r.get('reason')}"
        print(f"  {estado}  {s}")
    print("\n  El `adhoc_watcher` de motor_rofex pollea cada 5s y suscribe sin\n"
          "  reiniciar nada. Mirá en un rato con: python -m scripts.pedir_pata --ver\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
