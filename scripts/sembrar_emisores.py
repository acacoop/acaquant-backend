"""scripts/sembrar_emisores.py — arranca el catálogo de emisores. DRY-RUN por default.

## Qué hace

Da de alta en `mercado.emisores` a cada emisor que aparece en `mercado.curvas`, y
propone una industria de arranque derivada de la curva actual:

    on_energia  → energia      on_finanzas → finanzas      resto → (sin clasificar)

## Lo que NO hace, y es lo importante

**No resuelve las contradicciones.** Medido: 8 de 51 emisores tienen hoy sectores
distintos entre sus propios bonos (Pampa Energía tiene tres: `''`, `energia` y
`otros`). Colapsarlos "por mayoría" sería inventar un criterio y dejarlo escrito
como si fuera un dato — y nadie recordaría después que fue una adivinanza. Esos
emisores se dan de alta **SIN industria** y se listan aparte para que la mesa
elija. `sin_clasificar` es un estado válido y VISIBLE, no un error.

`on_otros` tampoco se traduce a una industria llamada "otros": `otros` no es una
industria, es el cajón de lo que nadie clasificó. Traducirlo lo volvería
indistinguible de una decisión tomada.

## Por qué es seguro

  · **Nunca pisa**: `ON CONFLICT DO NOTHING`. Re-correrlo no toca lo ya cargado ni
    lo que se editó a mano en Manager.
  · Idempotente: cortarlo a la mitad y repetirlo da lo mismo.
  · Alcance acotado: 51 emisores, no hay scan de nada grande (REGLA #4).

Uso:
    python -m scripts.sembrar_emisores             # qué haría
    python -m scripts.sembrar_emisores --aplicar   # lo hace
"""
from __future__ import annotations

import argparse

from api.services import emisores as svc
from core.postgres import get_pool

_SEP = "=" * 92

# La curva vieja → una industria de ARRANQUE. Solo las dos que nombran algo real:
# `on_otros` es el cajón y traducirlo lo haría pasar por decisión tomada.
_CURVA_A_INDUSTRIA = {"on_energia": "energia", "on_finanzas": "finanzas"}

INDUSTRIAS_BASE = ("energia", "finanzas", "agro", "industria", "consumo",
                   "telecomunicaciones", "construccion", "transporte", "otros")


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def proponer(filas: list[dict], en_conflicto: set[str]) -> list[dict]:
    """`[{emisor, industria|None, motivo}]`. PURA — el universo entra por parámetro.

    Un emisor en conflicto queda SIN industria aunque alguna de sus curvas la
    sugiera: si sus bonos se contradicen, la sugerencia es justamente el dato que
    no se puede confiar.
    """
    out = []
    for f in filas:
        em = svc.normalizar(f.get("emisor"))
        if not em:
            continue
        # La industria aplica SOLO a corporativos. `Argentina` (60 bonos) y `BCRA`
        # (5) no tienen industria: preguntarles cuál es no tiene respuesta. Se dan
        # de alta igual —el catálogo es de EMISORES— pero no entran a la lista de
        # pendientes de nadie.
        if f.get("es_corporativo") is False:
            out.append({"emisor": em, "industria": None,
                        "motivo": "no es corporativo — la industria no aplica"})
            continue
        if em.upper() in en_conflicto:
            out.append({"emisor": em, "industria": None,
                        "motivo": "sus bonos se contradicen — decide la mesa"})
            continue
        ind = _CURVA_A_INDUSTRIA.get((f.get("curva") or "").strip())
        out.append({"emisor": em, "industria": ind,
                    "motivo": f"de curva={f.get('curva')}" if ind
                              else "la curva no nombra una industria"})
    return out


def _emisores_del_master() -> list[dict]:
    return _q("""
        SELECT emisor, min(curva) AS curva, count(*) AS bonos,
               bool_or(emisor_tipo = 'corporativo') AS es_corporativo
        FROM mercado.curvas
        WHERE emisor IS NOT NULL AND btrim(emisor) <> ''
        GROUP BY emisor ORDER BY count(*) DESC, emisor
    """)


def aplicar(props: list[dict]) -> dict:
    """Alta idempotente. `DO NOTHING` y no `DO UPDATE`: lo que ya está cargado —o
    lo que alguien corrigió a mano en Manager— no se toca nunca."""
    if not props:
        return {"industrias": 0, "emisores": 0}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany("INSERT INTO mercado.industrias (industria) VALUES (%s) "
                        "ON CONFLICT (industria) DO NOTHING",
                        [(i,) for i in INDUSTRIAS_BASE])
        cur.executemany("INSERT INTO mercado.emisores (emisor, industria) "
                        "VALUES (%(emisor)s, %(industria)s) "
                        "ON CONFLICT (emisor) DO NOTHING", props)
        conn.commit()
    return {"industrias": len(INDUSTRIAS_BASE), "emisores": len(props)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Sembrar el catálogo de emisores")
    ap.add_argument("--aplicar", action="store_true", help="escribe (default: dry-run)")
    args = ap.parse_args()

    print(_SEP)
    print("CATÁLOGO DE EMISORES — la industria se muda del bono al emisor")
    print(_SEP)

    choques = svc.contradicciones()
    en_conflicto = {svc.normalizar(c["emisor"]).upper() for c in choques}
    props = proponer(_emisores_del_master(), en_conflicto)
    con = [p for p in props if p["industria"]]
    no_aplica = [p for p in props if "no es corporativo" in p["motivo"]]
    sin = [p for p in props if not p["industria"] and p not in no_aplica]

    print(f"\n  emisores en el master: {len(props)}")
    print(f"  con industria de arranque: {len(con)} · sin clasificar: {len(sin)} · "
          f"no aplica (soberano/provincia/BCRA): {len(no_aplica)}")

    if choques:
        print(f"\n  🛑 CONTRADICCIONES ({len(choques)}) — estos NO se clasifican solos.")
        print("     Sus bonos tienen HOY sectores distintos entre sí. Ninguno está")
        print("     'mal': cada fila suma bien por separado, y por eso agrupar da")
        print("     distinto según de dónde se lea. Elegir por mayoría sería inventar")
        print("     un criterio y dejarlo escrito como si fuera un dato.\n")
        print(f"     {'EMISOR':<34}{'BONOS':>6}   SECTORES QUE CONVIVEN")
        print("     " + "-" * 78)
        for c in choques:
            print(f"     {c['emisor'][:33]:<34}{c['bonos']:>6}   {c['sectores'][:36]}")

    if con:
        print(f"\n  Con industria de arranque ({len(con)}):")
        for p in con[:12]:
            print(f"     {p['emisor'][:34]:<36}→ {p['industria']:<12}({p['motivo']})")
        if len(con) > 12:
            print(f"     … y {len(con) - 12} más")

    print(f"\n  Sin clasificar ({len(sin)}) — quedan visibles como pendientes en")
    print("  Manager → TÍTULOS → EMISORES. NO se reparten a dedo.")

    if not args.aplicar:
        print("\n  [DRY-RUN] no se escribió nada. Con --aplicar se hace.")
        return
    h = aplicar(props)
    print(f"\n  ✅ {h['emisores']} emisor(es) y {h['industrias']} industria(s) base.")
    print("  Nunca pisa lo ya cargado: re-correrlo no toca lo que se editó a mano.")


if __name__ == "__main__":
    main()
