"""scripts/diag_pata_opera.py — ¿ESTA PATA COTIZA? (y por qué la versión anterior MENTÍA)

**Read-only.**

⚠️ **LA PRIMERA VERSIÓN DE ESTE SCRIPT ERA CIRCULAR Y DIO UNA RESPUESTA FALSA.**

Preguntaba «¿cuántos trades tuvo `AO29D` en 30 días?» mirando `mercado.timesales`
— y esa tabla **la escribe `engines/valores.py` SOLO para los símbolos que el
motor suscribe**. Como a `AO29D` nunca lo suscribió nadie, tenía 0 filas **por
construcción**, no porque no opere. Medir liquidez con nuestras propias tablas es
preguntarle al que no estaba escuchando si sonó el teléfono.

Dos pistas que tendrían que haberlo delatado y no miré:

  · **TODAS** las patas no suscritas daban 0 — las seis menos la del master, en
    los dos bonos. Eso no es un patrón de mercado, es la firma de «solo tenemos
    lo que pedimos».
  · `mercado.timesales` **se purga a 7 días** (`prune_native(…, 7)`), así que una
    ventana de 30 días no podía existir. El número 0 era, en parte, la respuesta
    a una pregunta imposible.

Lo mismo vale para `snapshots_cierre_hist`: se arma desde `market_snapshot`, que
también contiene solo lo suscrito. **Las tres fuentes eran la misma fuente.**

QUÉ SÍ SIRVE PARA CONTESTARLO
==============================

1. **El catálogo de Primary** (`manager.pyrofex_instruments`) — es de ELLOS, no
   nuestro: dice qué símbolos existen y son operables. Independiente.
2. **Suscribirse y mirar** — que es lo que está pasando ahora: desde que el
   master apunta a la pata D, el motor la pide. Si en una rueda entera no llega
   nada, ahí sí se puede afirmar algo.

    python -m scripts.diag_pata_opera AO29 CO32
"""
from __future__ import annotations

import sys

from core.postgres import get_pool


def main() -> int:
    tickers = [t.strip().upper() for t in sys.argv[1:]] or ["AO29", "CO32"]

    # El catálogo de Primary, aplanado. Es la única fuente de este script que NO
    # depende de lo que nosotros hayamos suscrito.
    catalogo: dict[str, dict] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT cficode, instruments FROM manager.pyrofex_instruments")
        for cfi, instrumentos in cur.fetchall():
            for i in (instrumentos or []):
                sym = (i.get("ticker") or i.get("symbol") or "").strip()
                if sym:
                    catalogo[sym] = {**i, "cficode": cfi}

        print(f"\ncatálogo de Primary: {len(catalogo)} símbolos "
              f"(fuente independiente de lo que suscribimos)")

        for tk in tickers:
            cur.execute("SELECT simbolo, moneda, plazo, es_default "
                        "FROM mercado.especies WHERE upper(ticker) = %s "
                        "ORDER BY es_default DESC, simbolo", (tk,))
            patas = cur.fetchall()
            print(f"\n{tk}\n" + "=" * 84)
            if not patas:
                print("  sin patas en `mercado.especies`")
                continue
            simbolos = [p[0] for p in patas]

            # Lo ÚNICO nuestro que sigue valiendo: si HAY precio, es que cotiza.
            # Su ausencia no prueba nada — pero su presencia sí.
            cur.execute("SELECT ticker, last_price, updated_at "
                        "FROM mercado.market_snapshot WHERE ticker = ANY(%s)",
                        (simbolos,))
            snap = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

            print(f"  {'pata':<20} {'moneda':<7} {'en Primary':<12} "
                  f"{'moneda Primary':<15} {'precio AHORA':>14}")
            print("  " + "-" * 82)
            for simbolo, moneda, plazo, es_def in patas:
                c = catalogo.get(simbolo)
                px, upd = snap.get(simbolo, (None, None))
                corto = f"{simbolo.split(' - ')[2]} {plazo}"
                print(f"  {corto:<20} {moneda:<7} "
                      f"{('SÍ' if c else 'no figura'):<12} "
                      f"{(c or {}).get('currency', '—')!s:<15} "
                      f"{px if px is not None else '—'!s:>14}"
                      f"{'  ←default' if es_def else ''}")
                if upd:
                    print(f"  {'':<20} último update: {upd}")

            print("  " + "-" * 82)
            en_primary = [s for s in simbolos if s in catalogo]
            con_precio = [s for s in simbolos if snap.get(s, (None,))[0]]
            print(f"  → {len(en_primary)}/{len(simbolos)} patas existen en Primary; "
                  f"{len(con_precio)} tienen precio AHORA.")
            print("  → **La ausencia de precio NO prueba que no cotice**: prueba "
                  "que no la estamos\n     escuchando, o que todavía no operó hoy. "
                  "Solo la presencia prueba algo.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
