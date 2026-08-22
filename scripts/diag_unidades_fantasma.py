"""scripts/diag_unidades_fantasma.py — el caso OTC: assets duplicados por ESPACIOS.

QUÉ MIDE (incidente 2026-08-22, REGLA #9): el control `assets_sin_cartera`
cantaba `'[OTC - MAI.ROS/ENE27] '` (unidad real, con espacio al final — el
doble espacio en la pantalla era la pista). El agente proponía sobre el sujeto
**stripeado**, y como `set_campos` era un UPSERT, el arreglo **creó una fila
FANTASMA** trimmeada con la cartera puesta: la verificación la releyó en verde,
el control siguió cantando la real, y la propuesta reaparecía infinita.

El código ya quedó arreglado (la identidad no se stripea más y el agente
escribe con `crear=False`); esto mide y limpia LO QUE YA QUEDÓ en la base.

Uso:
    python -m scripts.diag_unidades_fantasma            # solo mirar
    python -m scripts.diag_unidades_fantasma --reparar  # copiar cartera a la
                                                        # fila real y borrar la
                                                        # fantasma (con guardas)

Guardas del --reparar: la fantasma tiene que (1) ser el btrim exacto de otra
fila, (2) haberla escrito 'av-agent', (3) no estar referenciada por tenencia.
Si cualquiera falla, se reporta y NO se toca.
"""
from __future__ import annotations

import sys


def main() -> None:
    from core.postgres import get_pool
    from jobs.controles_datos import _chk_assets_sin_cartera

    reparar = "--reparar" in sys.argv

    print("═" * 70)
    print("1. LO QUE EL CONTROL VE AHORA (en vivo, mismo predicado)")
    print("═" * 70)
    for a in _chk_assets_sin_cartera():
        u = a["key"]
        print(f"  unidad={u!r}  len={len(u)}"
              + ("  ⚠ espacios en los bordes" if u != u.strip() else ""))

    print("\n" + "═" * 70)
    print("2. GRUPOS QUE COLISIONAN POR btrim (la misma cosa, dos filas)")
    print("═" * 70)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT btrim(unidad), array_agg(unidad ORDER BY unidad) "
            "FROM portafolio.assets GROUP BY btrim(unidad) "
            "HAVING count(*) > 1")
        grupos = cur.fetchall()
        if not grupos:
            print("  ninguno — no hay duplicados por espacios")
        pares: list[tuple[str, str]] = []   # (fantasma_trim, real_con_espacios)
        for base, unidades in grupos:
            print(f"\n  btrim = {base!r}")
            for u in unidades:
                cur.execute(
                    "SELECT cartera, actualizado_por, actualizado_at::date "
                    "FROM portafolio.assets WHERE unidad = %s", (u,))
                cart, por, ts = cur.fetchone()
                cur.execute("SELECT count(*) FROM portafolio.tenencia "
                            "WHERE unidad = %s", (u,))
                refs = cur.fetchone()[0]
                print(f"    unidad={u!r} len={len(u)} cartera={cart!r} "
                      f"escrita_por={por!r} el {ts} · tenencia={refs} filas")
                if (u == base and por == "av-agent" and refs == 0
                        and len(unidades) == 2):
                    otra = next(x for x in unidades if x != u)
                    pares.append((u, otra))

        print("\n" + "═" * 70)
        print(f"3. FANTASMAS REPARABLES: {len(pares)}")
        print("═" * 70)
        for fantasma, real in pares:
            cur.execute("SELECT cartera FROM portafolio.assets WHERE unidad = %s",
                        (fantasma,))
            cart = cur.fetchone()[0]
            print(f"  copiar cartera={cart!r} de {fantasma!r} → {real!r} "
                  "y borrar la fantasma")
            if reparar and cart:
                cur.execute(
                    "UPDATE portafolio.assets SET cartera = %s, "
                    "  actualizado_por = 'fix_unidades_fantasma' "
                    " WHERE unidad = %s AND (cartera IS NULL OR cartera = '' "
                    "   OR cartera = 'NO APLICA')",
                    (cart, real))
                movio = cur.rowcount or 0
                cur.execute("DELETE FROM portafolio.assets WHERE unidad = %s",
                            (fantasma,))
                conn.commit()
                print(f"    ✔ hecho (cartera escrita en {movio} fila/s real/es, "
                      "fantasma borrada)")
        if pares and not reparar:
            print("\n  (dry-run — nada se tocó; correr con --reparar para aplicar)")
        if reparar and pares:
            print("\nDespués de reparar: ↻ CHEQUEAR AHORA en la fila del control "
                  "tiene que bajar el conteo.")


if __name__ == "__main__":
    main()
