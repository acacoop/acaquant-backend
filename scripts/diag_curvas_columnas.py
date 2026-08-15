"""scripts/diag_curvas_columnas.py — qué hay REALMENTE en cada columna de curvas. READ-ONLY.

Volcado crudo para mirar con los ojos, no para resumir. La pregunta que responde
es si las columnas de CLASIFICACIÓN se están pisando entre ellas: `curva`, `tipo`,
`ajuste`, `emisor_tipo`, `moneda_eje`, `moneda_flujo`, `ley`, `sector`. Son OCHO,
muchas nacieron en momentos distintos, y la sospecha es que varias dicen lo mismo
con otro nombre. (Eran nueve: `tipo_instrumento` se eliminó por estar vacía en los
221 bonos — la primera respuesta que dio este mismo diag.)

Tres bloques:

  1) MUESTRA — un bono por combinación distinta, con todas sus columnas. La
     muestra NO es aleatoria: se toma una fila por cada `(curva, ajuste,
     emisor_tipo)` distinto, porque 15 bonos al azar caen casi todos en la misma
     familia y no muestran nada. Así cada fila aporta un caso diferente.

  2) CRUCE — `curva` × `ajuste` × `tipo` con el conteo. Acá se ve la duplicación
     de una: si cada `curva` tiene siempre el mismo `ajuste`, una de las dos
     sobra. Si `curva='on_energia'` convive con muchos `ajuste`, entonces `curva`
     NO es una curva ahí — es un SECTOR, y eso es un significado distinto metido
     en la misma columna.

  3) POR COLUMNA — todos los valores distintos de cada una, con cuántos bonos y
     cuántos vacíos. El vacío importa tanto como el valor: una columna llena a
     medias no sirve para agrupar aunque el dato que tenga sea correcto.

Uso:
    python -m scripts.diag_curvas_columnas
    python -m scripts.diag_curvas_columnas --n 30    # más filas en la muestra
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool

# Todo menos los dos jsonb gigantes (`flujos` y `data`), que se resumen a un
# conteo — pegarlos enteros haría ilegible el volcado, que es justo lo que se
# quiere leer.
COLS = ("ticker", "instrumento", "curva", "tipo", "emisor", "emisor_tipo",
        "sector", "moneda_eje", "moneda_flujo", "ajuste", "ley",
        "valor_nominal", "cupon_anual", "cer_emision",
        "flujo_vencimiento", "fecha_emision", "fecha_vencimiento")

# Las que CLASIFICAN. Son las candidatas a estar diciendo lo mismo.
CLASIFICACION = ("curva", "tipo", "emisor_tipo", "sector", "moneda_eje",
                 "moneda_flujo", "ajuste", "ley")

_SEP = "=" * 96


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _v(x) -> str:
    return "—" if x is None or str(x).strip() == "" else str(x)


def main() -> None:
    ap = argparse.ArgumentParser(description="Volcado de mercado.curvas")
    ap.add_argument("--n", type=int, default=15, help="filas en la muestra")
    args = ap.parse_args()

    total = _q("SELECT count(*) n FROM mercado.curvas")[0]["n"]
    print(f"{_SEP}\nmercado.curvas — {total} bonos · {len(COLS)} columnas "
          f"(+ flujos y data, que son jsonb)\n{_SEP}")

    # ── 1) MUESTRA ──────────────────────────────────────────────────────────
    print(f"\n{_SEP}\n1) MUESTRA — un bono por combinación distinta\n{_SEP}")
    filas = _q(f"""
        SELECT DISTINCT ON (curva, ajuste, emisor_tipo) {', '.join(COLS)},
               (SELECT count(*) FROM jsonb_array_elements(flujos)) AS n_flujos,
               (data IS NOT NULL) AS tiene_data
        FROM mercado.curvas
        ORDER BY curva, ajuste, emisor_tipo, ticker
        LIMIT {int(args.n)}
    """)
    for f in filas:
        print(f"\n  ── {f['ticker']} " + "─" * (88 - len(str(f["ticker"]))))
        for c in COLS:
            if c == "ticker":
                continue
            print(f"     {c:<20} {_v(f[c])}")
        print(f"     {'flujos':<20} {f['n_flujos']} cupones · "
              f"blob data: {'sí' if f['tiene_data'] else 'no'}")

    # ── 2) CRUCE ────────────────────────────────────────────────────────────
    print(f"\n{_SEP}\n2) CRUCE — ¿`curva`, `ajuste` y `tipo` dicen lo mismo?\n{_SEP}")
    print("  Si una CURVA siempre trae el MISMO ajuste, una de las dos sobra.")
    print("  Si una curva convive con VARIOS ajustes, esa columna no es una")
    print("  curva ahí: está guardando otra cosa (un sector, por ejemplo).\n")
    print(f"  {'CURVA':<16}{'AJUSTE':<16}{'TIPO':<14}{'EMISOR_TIPO':<14}{'BONOS':>7}")
    print("  " + "-" * 68)
    for r in _q("""
        SELECT COALESCE(curva,'∅') AS curva, COALESCE(ajuste,'∅') AS ajuste,
               COALESCE(tipo,'∅') AS tipo, COALESCE(emisor_tipo,'∅') AS emisor_tipo,
               count(*) AS n
        FROM mercado.curvas GROUP BY 1,2,3,4 ORDER BY 1,2,3
    """):
        print(f"  {r['curva'][:15]:<16}{r['ajuste'][:15]:<16}{r['tipo'][:13]:<14}"
              f"{r['emisor_tipo'][:13]:<14}{r['n']:>7}")

    # ── 3) POR COLUMNA ──────────────────────────────────────────────────────
    print(f"\n{_SEP}\n3) POR COLUMNA — qué valores tiene y cuántos vacíos\n{_SEP}")
    print("  Una columna llena a medias no sirve para agrupar aunque lo que")
    print("  tenga sea correcto: los vacíos se van todos a la misma bolsa.\n")
    for c in CLASIFICACION:
        vals = _q(f"""
            SELECT COALESCE(NULLIF(trim({c}::text), ''), '∅ VACÍO') AS v,
                   count(*) AS n
            FROM mercado.curvas GROUP BY 1 ORDER BY 2 DESC
        """)
        vacios = next((v["n"] for v in vals if v["v"] == "∅ VACÍO"), 0)
        print(f"  · {c}  —  {len(vals)} valor(es) distinto(s), "
              f"{vacios} bono(s) sin dato")
        print("      " + " · ".join(f"{v['v']}({v['n']})" for v in vals[:14]))
        if len(vals) > 14:
            print(f"      … y {len(vals) - 14} valor(es) más")
        print()


if __name__ == "__main__":
    main()
