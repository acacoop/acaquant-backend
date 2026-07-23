"""diag_operar_book — ¿por qué la card de OPERAR no trae nada?

READ-ONLY. Recorre la MISMA cadena que el endpoint `/api/operar/order-book` y
dice en qué eslabón se corta, en vez de dejar al frontend reintentando a ciegas:

  1. ¿El motor de mercado está vivo?      (frescura global de market_snapshot)
  2. ¿pyRofex conoce el ticker?           (manager.pyrofex_instruments)
  3. ¿Está suscripto?                     (mercado.adhoc_subscriptions + cupo)
  4. ¿Hay fila en market_snapshot?        (y hace cuánto que no se refresca)
  5. ¿El libro tiene puntas?              (bids/offers)

El caso que confunde: fila FRESCA con libro VACÍO. No es una falla — es el
mercado cerrado o un papel sin oferta. Se distingue acá para no salir a buscar
un bug que no existe.

Uso (Droplet):
    python -m scripts.diag_operar_book AL30
    python -m scripts.diag_operar_book AL30 --plazo CI
    python -m scripts.diag_operar_book "MERV - XMEV - AL30 - 24hs"
    python -m scripts.diag_operar_book --liberar-ociosas   # vacía el cupo de basura
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from psycopg.rows import dict_row

from core.postgres import get_pool

_OK, _MAL, _OJO = "✓", "✗", "?"


def _edad(ts) -> tuple[float | None, str]:
    if ts is None:
        return None, "nunca"
    if getattr(ts, "tzinfo", None) is None:
        ts = ts.replace(tzinfo=UTC)
    s = (datetime.now(UTC) - ts).total_seconds()
    if s < 90:
        return s, f"hace {s:.0f}s"
    if s < 3600:
        return s, f"hace {s / 60:.0f} min"
    return s, f"hace {s / 3600:.1f} h"


def _liberar_ociosas() -> None:
    """Borra las suscripciones adhoc que nadie está mirando ahora (last_used_at
    viejo). Alivio inmediato para un cupo lleno de basura, sin esperar al TTL de
    7 días ni al desalojo perezoso del próximo pick. Las cards abiertas (que
    pollean) NO se tocan: se refrescan solas al segundo siguiente."""
    from core.adhoc_subscriptions import PROTEGIDA_S
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mercado.adhoc_subscriptions "
            "WHERE last_used_at < now() - make_interval(secs => %s)", (PROTEGIDA_S,))
        borradas = cur.rowcount or 0
        cur.execute("SELECT count(*) FROM mercado.adhoc_subscriptions")
        quedan = cur.fetchone()[0]
    print(f"{_OK} liberadas {borradas} suscripciones ociosas · quedan {quedan}/50")
    print("  (las cards abiertas se re-suscriben solas en el próximo poll)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", nargs="?",
                    help="corto (AL30) o full ('MERV - XMEV - AL30 - 24hs')")
    ap.add_argument("--plazo", default="24hs", help="CI | 24hs | 48hs (default 24hs)")
    ap.add_argument("--liberar-ociosas", action="store_true",
                    help="vaciar el cupo de suscripciones que nadie está usando")
    args = ap.parse_args()

    if args.liberar_ociosas:
        _liberar_ociosas()
        return
    if not args.ticker:
        ap.error("falta el ticker (o usá --liberar-ociosas)")

    corto = args.ticker.strip()
    full = corto if " - " in corto else f"MERV - XMEV - {corto} - {args.plazo}"
    print(f"ticker pedido : {corto}")
    print(f"ticker full   : {full}\n")

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # 1. ¿el motor escribe? — la fila más fresca de TODO market_snapshot
        cur.execute("SELECT max(updated_at) AS ult FROM mercado.market_snapshot")
        _s, txt = _edad((cur.fetchone() or {}).get("ult"))
        vivo = _s is not None and _s < 120
        print(f"{_OK if vivo else _MAL} 1. motor de mercado: última escritura {txt}")
        if not vivo:
            print("     → el motor NO está escribiendo. Fuera de rueda es normal")
            print("       (L-V 13:20-20:00 UTC); en rueda, revisá los servicios.")

        # 2. ¿pyRofex lo conoce?
        cur.execute("SELECT 1 FROM manager.pyrofex_instruments LIMIT 1")
        hay_catalogo = cur.fetchone() is not None
        if not hay_catalogo:
            print(f"{_OJO} 2. pyrofex_instruments VACÍA — la validación no aplica")
            print("     (correr scripts.discovery_pyrofex para poder validar símbolos)")
        else:
            cur.execute(
                "SELECT 1 FROM manager.pyrofex_instruments "
                "WHERE instruments @> %s::jsonb LIMIT 1",
                (json.dumps([{"ticker": full}]),))
            conocido = cur.fetchone() is not None
            print(f"{_OK if conocido else _MAL} 2. pyRofex conoce el ticker: "
                  f"{'sí' if conocido else 'NO'}")
            if not conocido:
                print("     → el endpoint devuelve 404. El símbolo está mal escrito,")
                print("       o el plazo no existe para ese papel.")
                cur.execute(
                    "SELECT inst->>'ticker' AS t FROM manager.pyrofex_instruments p, "
                    "jsonb_array_elements(p.instruments) inst "
                    "WHERE inst->>'ticker' ILIKE %s LIMIT 8", (f"% - {corto} - %",))
                alt = [r["t"] for r in cur.fetchall()]
                if alt:
                    print("     Plazos que SÍ existen para ese papel:")
                    for a in alt:
                        print(f"       {a}")
                return

        # 3. suscripción adhoc + cupo
        cur.execute(
            "SELECT ticker, last_used_at FROM mercado.adhoc_subscriptions WHERE ticker = %s",
            (full,))
        sub = cur.fetchone()
        cur.execute("SELECT count(*) AS n FROM mercado.adhoc_subscriptions")
        n_subs = int((cur.fetchone() or {}).get("n") or 0)
        if sub:
            _s, txt = _edad(sub.get("last_used_at"))
            print(f"{_OK} 3. suscripción adhoc: registrada, último uso {txt} "
                  f"({n_subs} activas)")
        else:
            print(f"{_OJO} 3. suscripción adhoc: NO registrada ({n_subs} activas)")
            print("     → se registra sola la primera vez que abrís la card.")
        if n_subs >= 50:
            print(f"{_MAL}    CUPO LLENO ({n_subs}/50): las nuevas dan 429.")

        # 4 y 5. la fila y el libro
        cur.execute(
            "SELECT ticker, updated_at, book, last_price FROM mercado.market_snapshot "
            "WHERE ticker = %s", (full,))
        fila = cur.fetchone()
        if not fila:
            print(f"{_MAL} 4. market_snapshot: SIN FILA para ese ticker")
            print("     → el endpoint devuelve 202 'suscribiendo'. Si el motor está")
            print("       vivo, aparece en ~5s; si no, no va a aparecer nunca.")
            return
        edad_s, txt = _edad(fila.get("updated_at"))
        fresca = edad_s is not None and edad_s <= 90
        print(f"{_OK if fresca else _OJO} 4. market_snapshot: fila refrescada {txt}"
              + ("" if fresca else "  ← ABANDONADA (el endpoint re-suscribe)"))

        book = fila.get("book") or {}
        bids, offers = book.get("bids") or [], book.get("offers") or []
        if bids or offers:
            print(f"{_OK} 5. libro: {len(bids)} bid(s) / {len(offers)} offer(s) "
                  "→ la card TIENE que mostrar datos")
        else:
            print(f"{_OJO} 5. libro: VACÍO (sin bids ni offers)")
            print(f"     último precio: {fila.get('last_price')}")
            if fresca:
                print("     → NO es un bug: el papel no tiene oferta ahora mismo")
                print("       (mercado cerrado o sin liquidez). La card muestra el")
                print("       último precio y avisa que no hay puntas.")
            else:
                print("     → fila vieja Y vacía: el motor la dejó de refrescar.")


if __name__ == "__main__":
    main()
