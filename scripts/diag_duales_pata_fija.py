"""READ-ONLY. ¿Por qué TTD26 y TTS26 salen en `--` en la tabla TASA FIJA?

Herramienta: diag · Sigue la cadena hasta el punto exacto donde se corta.

EL SÍNTOMA
==========

Los duales aparecen COMPLETOS en la tab TAMAR (TNA, TEA, MARGEN, TEM, DUR) y
VACÍOS en TASA FIJA. Los mismos bonos, el mismo día.

Y eso NO es un bug de la vista: es su comportamiento correcto a medias. Un dual
tiene DOS tasas que se diferencian en miles de puntos básicos (medido en TXMD9:
6,82% real por CER contra 38,62% nominal por TAMAR), y `mercado.market_snapshot`
tiene UNA fila por símbolo, o sea UNA sola TEA. Así que la vista **borra las
métricas del snapshot en la pata SECUNDARIA** —si no, TTD26 mostraría en TASA
FIJA la tasa de su pata TAMAR, que es el bug que esto vino a arreglar— y espera
que 1816 le dé la de esta pata. **Si 1816 no la trajo, la celda queda vacía.**

O sea: el `--` no dice «no se pudo calcular». Dice **«falta la fila de 1816 para
la pata fija»**. Esto busca dónde se cortó.

LA CADENA, EN ORDEN
===================

    1. CATÁLOGO   research.mkt_1816_instrumentos tiene la variante `@…`?
                  Sin la variante, el job NO la pide (la grafía la manda 1816,
                  no la armamos nosotros).
    2. UNIVERSO   ¿el job la incluye en sus pedidos, y con qué pata?
                  Acá vive el alias `@TASA FIJA` ↔ `@BONCAP`.
    3. TABLA      ¿quedó la fila `(ticker, 'fija')` en mercado.tamar_1816?
                  Si no está, 1816 devolvió la TEA en null y el job NO escribe
                  (a propósito: pisar con nulls es peor que dejar la vieja).
    4. VISTA      ¿la pata que pide la vista es la misma con la que se escribió?

Uso:
    python -m scripts.diag_duales_pata_fija                # los duales del master
    python -m scripts.diag_duales_pata_fija TTD26 TTS26    # los que le pases
"""
from __future__ import annotations

import sys

from core.postgres import get_job_pool as get_pool


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def main() -> int:
    pedidos_cli = [a.upper() for a in sys.argv[1:] if not a.startswith("-")]

    # Un DUAL es un bono con `ajuste_alt`: dos patas de verdad.
    duales = _q("""
        SELECT ticker, ajuste, ajuste_alt, emisor_tipo, moneda_eje
          FROM mercado.curvas
         WHERE ajuste_alt IS NOT NULL AND ajuste_alt <> ''
         ORDER BY ticker
    """)
    if pedidos_cli:
        duales = [d for d in duales if d["ticker"].upper() in pedidos_cli]

    print(f"\n{'=' * 78}\n LOS DUALES Y SU PATA FIJA — {len(duales)} bonos"
          f"\n{'=' * 78}\n")
    if not duales:
        print("  No hay bonos con `ajuste_alt` en mercado.curvas.\n")
        return 0

    tk_list = [d["ticker"] for d in duales]

    # (1) CATÁLOGO
    cat = _q("""
        SELECT ticker, denominacion FROM research.mkt_1816_instrumentos
         WHERE ticker ILIKE ANY(%s)
    """, ([f"{t}@%" for t in tk_list] + [f"{t} @%" for t in tk_list],))
    por_base: dict[str, list[dict]] = {}
    for c in cat:
        por_base.setdefault(str(c["ticker"]).split("@", 1)[0].strip().upper(),
                            []).append(c)

    # (2) UNIVERSO — se le pregunta AL JOB, no se reimplementa: si el criterio
    # cambia, este diag tiene que cambiar con él y no seguir diciendo que sí.
    from jobs.tamar_1816 import universo
    pedidos, desconocidos = universo()
    por_ticker: dict[str, list[tuple[str, str]]] = {}
    for tk1816, (nuestro, pata) in pedidos.items():
        por_ticker.setdefault(nuestro.upper(), []).append((tk1816, pata))

    # (3) TABLA
    filas = {(r["ticker"].upper(), r["pata"]): r for r in _q("""
        SELECT ticker, pata, tea, tna, spread, duration, fecha_operacion,
               actualizado_en FROM mercado.tamar_1816
    """)}

    for d in duales:
        tk = d["ticker"].upper()
        print(f"  ── {tk}   ajuste={d['ajuste']} · alt={d['ajuste_alt']} · "
              f"{d['emisor_tipo']} {d['moneda_eje']}")

        v = por_base.get(tk) or []
        print(f"     1. CATÁLOGO 1816 : {len(v)} variante(s)  "
              f"{[str(x['ticker']) for x in v] or '← NINGUNA: el job no puede pedirla'}")
        if v:
            print(f"        denominaciones: {[str(x['denominacion']) for x in v]}")

        ped = por_ticker.get(tk) or []
        print(f"     2. UNIVERSO      : {ped or '← NO ENTRA al job'}")

        # Las patas que el bono TIENE, según sus ejes.
        for pata in (d["ajuste"], d["ajuste_alt"]):
            f = filas.get((tk, pata))
            if f is None:
                print(f"     3. TABLA [{pata:>6}] : ← SIN FILA  "
                      f"(1816 no trajo TEA para esa pata, o no se pidió)")
            else:
                print(f"     3. TABLA [{pata:>6}] : tea={f['tea']} tna={f['tna']} "
                      f"margen={f['spread']} dur={f['duration']} "
                      f"· 1816 {f['fecha_operacion']} · escrito {f['actualizado_en']:%d/%m %H:%M}")
        print()

    if desconocidos:
        print(f"  ⚠️ SUFIJOS QUE EL JOB NO SABE LEER ({len(desconocidos)}) — se "
              f"reportan y no se clasifican mal:\n")
        for x in desconocidos:
            print(f"      {x}")
        print()

    print("  CÓMO LEERLO\n"
          "    · Sin variante en (1) → 1816 no publica esa pata por separado:\n"
          "      no hay de dónde traerla y la celda vacía es la verdad.\n"
          "    · Con variante y sin fila en (3) → se pidió y 1816 devolvió la TEA\n"
          "      en null. El job NO escribe nulls a propósito.\n"
          "    · Con fila en (3) y la celda igual vacía → el corte está en la\n"
          "      vista: la pata con la que busca no es la que se escribió.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
