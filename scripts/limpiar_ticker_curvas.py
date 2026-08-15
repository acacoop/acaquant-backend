"""scripts/limpiar_ticker_curvas.py — PASO 9: sacarle el sufijo D/C al TICKER.

## Qué arregla

La tabla HARD DOLAR muestra `AL30D`, `GD29D`, `AO27D`. Eso está mal: el TICKER
del bono es `AL30`. La `D` es la ESPECIE (la pata en dólares), no parte del
nombre del papel — y hoy está pegada al VALOR de `mercado.curvas.ticker`, que es
la PK del master.

Es lo último que quedó del renombre del 2026-08-15: ese cambió los NOMBRES de las
columnas, no los VALORES.

## Por qué recién ahora es seguro

Antes, sacarle la D al ticker PERDÍA información: no había dónde anotar de qué
pata venía el precio. Ahora `mercado.especies` guarda esa identidad
(`ticker_especie` = `AL30D`, `ticker` = `AL30`), así que la D vive donde
corresponde y el master puede quedarse con el nombre del bono.

## Lo que toca (y por qué es delicado)

`curvas.ticker` es **PK**, y el ticker viaja además en TRES lugares más:

  1. `mercado.curvas.ticker`      — la PK
  2. `mercado.curvas.data`        — el blob jsonb, clave `ticker_corto`. Es lo que
     leen ~500 lugares vía `core/curvas_sql` (`SELECT data`). Si se renombra la
     columna y no el blob, la app sigue viendo el valor viejo y NADA cambia en
     pantalla — el síntoma sería "lo corrí y no pasó nada".
  3. `portafolio.assets.ticker`   — el join con el catálogo. Si se mueve uno y no
     el otro, el bono se desconecta de su tenencia: el AuM lo pierde.

Por eso los tres se mueven en **UNA transacción**.

## Colisiones: el caso que NO se puede resolver solo

Si existen `AL30` y `AL30D` como filas SEPARADAS del master, renombrar la segunda
choca con la primera. Eso no es un error del script: son dos filas que
representan el mismo bono y hay que decidir cuál queda. El script las DETECTA,
las reporta y **no toca ninguna de las dos** — el resto se migra igual.

DRY-RUN por default (REGLA #4).

Uso:
    python -m scripts.limpiar_ticker_curvas             # qué haría
    python -m scripts.limpiar_ticker_curvas --aplicar   # lo hace
"""
from __future__ import annotations

import argparse
import re

from core.postgres import get_pool

# La especie es UNA letra final sobre un ticker que ya termina en dígito:
# `AL30D` → `AL30`, `GD29C` → `GD29`. La restricción del dígito es la que evita
# tocar las ONs, cuyo nombre TERMINA en O/D como parte del ticker (`AERBO` es el
# bono, no la pata de `AERB`). Es la misma convención que usa `sembrar_especies`.
_RE_ESPECIE = re.compile(r"^([A-Z]+\d+)([DC])$")


def limpiar(ticker: str) -> str | None:
    """`AL30D` → `AL30`. `None` si no hay nada que sacar. PURA."""
    m = _RE_ESPECIE.match((ticker or "").strip().upper())
    return m.group(1) if m else None


def planificar(tickers: list[str]) -> tuple[list[dict], list[dict]]:
    """`(a_migrar, colisiones)`. PURA — el universo entra por parámetro.

    Hay DOS formas de chocar y las dos terminan en el mismo error de PK:

      · el destino YA existe como fila propia (`AL30` y `AL30D` conviven), o
      · DOS orígenes apuntan al mismo destino (`AL30D` y `AL30C` van los dos a
        `AL30`) — este se ve sólo mirando el conjunto entero, no fila por fila.

    En ambos casos son varias filas para el mismo bono y elegir cuál sobrevive
    —con lo que eso implica para su tenencia— no es decisión de un script.
    """
    existentes = {(t or "").strip().upper() for t in tickers}
    candidatos = [{"de": t, "a": n} for t in sorted(existentes)
                  if (n := limpiar(t))]
    # Cuántos orígenes quiere cada destino: >1 es un choque entre ellos.
    demanda: dict[str, int] = {}
    for c in candidatos:
        demanda[c["a"]] = demanda.get(c["a"], 0) + 1

    migrar, choques = [], []
    for c in candidatos:
        choca = c["a"] in existentes or demanda[c["a"]] > 1
        (choques if choca else migrar).append(c)
    return migrar, choques


def _tickers() -> list[str]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM mercado.curvas WHERE ticker IS NOT NULL")
        return [t for (t,) in cur.fetchall()]


def aplicar(migrar: list[dict]) -> dict:
    """Los TRES lugares, en UNA transacción. Si algo falla, no queda a medias."""
    if not migrar:
        return {"curvas": 0, "blob": 0, "assets": 0}
    hechos = {"curvas": 0, "blob": 0, "assets": 0}
    with get_pool().connection() as conn, conn.cursor() as cur:
        for m in migrar:
            cur.execute("UPDATE mercado.curvas SET ticker = %(a)s, "
                        "data = jsonb_set(data, '{ticker_corto}', to_jsonb(%(a)s::text)) "
                        "WHERE ticker = %(de)s", m)
            hechos["curvas"] += cur.rowcount
            cur.execute("UPDATE portafolio.assets SET ticker = %(a)s "
                        "WHERE ticker = %(de)s", m)
            hechos["assets"] += cur.rowcount
        conn.commit()
    hechos["blob"] = hechos["curvas"]      # se mueven juntos, en el mismo UPDATE
    return hechos


def main() -> None:
    ap = argparse.ArgumentParser(description="Sacar el sufijo D/C del ticker del master")
    ap.add_argument("--aplicar", action="store_true", help="escribe (default: dry-run)")
    args = ap.parse_args()

    migrar, choques = planificar(_tickers())

    print("=" * 90)
    print("PASO 9 — el TICKER del master deja de llevar la especie")
    print("=" * 90)
    print(f"  a migrar: {len(migrar)} · colisiones: {len(choques)}")

    if migrar:
        print(f"\n  {'DE':<12}{'A':<12}")
        print("  " + "-" * 24)
        for m in migrar:
            print(f"  {m['de']:<12}{m['a']:<12}")

    if choques:
        print(f"\n  ⚠ COLISIONES ({len(choques)}) — el destino YA existe como fila propia.")
        print("    Son DOS filas para el mismo bono. No se toca ninguna: elegir cuál")
        print("    sobrevive (y qué pasa con su tenencia) no lo puede decidir un script.")
        for c in choques:
            print(f"      {c['de']} → {c['a']}  (ya existe {c['a']})")

    if not args.aplicar:
        print("\n  [DRY-RUN] no se escribió nada. Con --aplicar se hace.")
        return

    h = aplicar(migrar)
    print(f"\n  ✅ {h['curvas']} fila(s) en mercado.curvas (columna + blob `data`) · "
          f"{h['assets']} en portafolio.assets")
    print("  La API relee el master por TTL; la vista muestra el ticker limpio en el "
          "próximo poll.")


if __name__ == "__main__":
    main()
