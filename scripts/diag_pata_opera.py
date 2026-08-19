"""scripts/diag_pata_opera.py — ¿ESTA PATA OPERA, O ESTOY PIDIENDO UN PRECIO QUE NO EXISTE?

**Read-only.**

El AO29 ya suscribe la pata en dólares (`AO29D`) y el snapshot sigue vacío. Hay
DOS explicaciones y llevan a arreglos OPUESTOS:

    la pata D opera y algo falla     → hay que arreglar la suscripción
    la pata D NO opera nunca         → **no hay nada que arreglar ahí**: el precio
                                       que existe es el de la pata en PESOS, y lo
                                       correcto es mostrar ESE (el motor lo divide
                                       por el MEP y la paridad sale bien — es
                                       exactamente lo que hace GD46 hoy)

Elegir mal cuesta caro en las dos direcciones: apuntar a una pata que no cotiza
deja la fila muda para siempre, y volver a la de pesos cuando la D sí opera
perpetúa la columna con números de otra escala.

Se decide con historia, no con una foto: **una pata puede no haber operado TODAVÍA
hoy y operar a las 15**. Por eso se mira el tape y los cierres, no el snapshot.

    python -m scripts.diag_pata_opera AO29 CO32
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

DIAS = 30


def main() -> int:
    tickers = [t.strip().upper() for t in sys.argv[1:]] or ["AO29", "CO32"]
    with get_pool().connection() as conn, conn.cursor() as cur:
        for tk in tickers:
            cur.execute("SELECT simbolo, moneda, plazo, es_default "
                        "FROM mercado.especies WHERE upper(ticker) = %s "
                        "ORDER BY es_default DESC, simbolo", (tk,))
            patas = cur.fetchall()
            print(f"\n{tk}\n" + "=" * 78)
            if not patas:
                print("  sin patas en `mercado.especies`")
                continue
            print(f"  {'pata':<32} {'moneda':<7} {'trades ' + str(DIAS) + 'd':>12} "
                  f"{'último trade':>20}  {'cierres':>8}")
            print("  " + "-" * 76)
            for simbolo, moneda, plazo, es_def in patas:
                cur.execute(
                    "SELECT count(*), max(ts) FROM mercado.timesales "
                    "WHERE ticker = %s "
                    "  AND ts > now() - make_interval(days => %s)",
                    (simbolo, DIAS))
                n_trades, ult = cur.fetchone()
                cur.execute("SELECT count(*) FROM mercado.snapshots_cierre_hist "
                            "WHERE ticker = %s "
                            "AND fecha > current_date - %s", (simbolo, DIAS))
                n_cierres = cur.fetchone()[0]
                marca = " ←default" if es_def else ""
                print(f"  {simbolo.split(' - ')[2] + ' ' + plazo:<32} {moneda:<7} "
                      f"{n_trades:>12} {ult or '—'!s:>20}  {n_cierres:>8}"
                      f"{marca}")

            # EL VEREDICTO, que es lo único que se usa para decidir.
            cur.execute(
                "SELECT count(*) FROM mercado.timesales t "
                "JOIN mercado.especies e ON e.simbolo = t.ticker "
                "WHERE upper(e.ticker) = %s AND e.es_default "
                "  AND t.ts > now() - make_interval(days => %s)", (tk, DIAS))
            n_def = cur.fetchone()[0]
            print("  " + "-" * 76)
            if n_def:
                print(f"  → la pata DEFAULT operó {n_def} veces en {DIAS} días: "
                      f"apuntar el master ahí es correcto y hay que ver por qué "
                      f"no llega el precio.")
            else:
                print(f"  → **la pata DEFAULT no operó NI UNA VEZ en {DIAS} días.** "
                      f"Apuntar el master ahí deja la fila muda para siempre: lo "
                      f"correcto es la pata que SÍ cotiza (el motor divide por el "
                      f"MEP y la paridad sale bien, como en GD46).")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
